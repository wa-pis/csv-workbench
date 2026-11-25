"""Wide CSV payloads on disk; only filter and sort keys enter query vectors."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from csv_workbench.edits import NEW_ROW, DiskEdits

BATCH_ROWS = 8192
BATCH_BYTES = 8 * 2**20


@contextmanager
def _connection(path, cancel, *, readonly=False):
    cancel.check()
    address = f"{path.as_uri()}?mode=ro" if readonly else str(path)
    try:
        with closing(sqlite3.connect(address, uri=readonly, check_same_thread=False)) as db:
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA mmap_size=0")
            db.execute("PRAGMA temp_store=FILE")
            db.set_progress_handler(lambda: int(cancel.cancelled), 10000)
            yield db
            cancel.check()
    except sqlite3.Error as exc:
        cancel.check()
        raise OSError(f"Wide CSV database: {exc}") from exc


def _patch_rows(edits, cancel):
    if isinstance(edits, DiskEdits):
        yield from edits.iter_rows(cancel=cancel)
        return
    grouped = {}
    for (rid, column), value in edits.items():
        cancel.check()
        grouped.setdefault(rid, {})[column] = value
    for rid in sorted(grouped):
        cancel.check()
        yield rid, grouped[rid]


def effective_rows(source, columns, edits, *, cancel, fmt):
    """Yield stable IDs and effective rows without retaining the source or patches."""
    from csv_workbench.core import iter_csv_rows

    cancel.check()
    with closing(_patch_rows(edits, cancel)) as patches, closing(
        iter_csv_rows(source, columns, cancel, fmt)
    ) as rows:
        patch = next(patches, None)
        for rid, values in enumerate(rows):
            cancel.check()
            while patch is not None and patch[0] < rid:
                patch = next(patches, None)
            if patch is not None and patch[0] == rid:
                changes = patch[1]
                patch = next(patches, None)
                if -1 in changes:
                    continue
                values = list(values)
                for column, value in changes.items():
                    if 0 <= column < len(columns):
                        values[column] = value
            yield rid, values
        while patch is not None:
            cancel.check()
            rid, changes = patch
            if rid >= NEW_ROW and -1 not in changes:
                values = [None] * len(columns)
                for column, value in changes.items():
                    if 0 <= column < len(columns):
                        values[column] = value
                yield rid, values
            patch = next(patches, None)
    cancel.check()


def selection_matcher(columns, selection):
    """Compile the same literal Unicode and NULL-safe predicates as the SQL path."""
    from csv_workbench.core import search_lower

    indices = {name: index for index, name in enumerate(columns)}

    def index(name):
        if name not in indices:
            raise ValueError(f"Unknown CSV column: {name}")
        return indices[name]

    conditions = [(index(name), search_lower(value)) for name, value in selection.conditions]
    exact = []
    for name, operator, value in selection.exact_filters:
        if operator not in {"equals", "not_equals"}:
            raise ValueError(f"Unknown filter operation: {operator}")
        exact.append((index(name), operator == "equals", value))

    def matches(row):
        checks = [row[column] is not None and needle in search_lower(row[column])
                  for column, needle in conditions]
        checks.extend((row[column] == value) == equals for column, equals, value in exact)
        return not checks or (any(checks) if selection.match_any else all(checks))

    return matches


class WideResult:
    """Immutable ordered view with row payloads outside DuckDB."""

    def __init__(self, scratch, path, headers, identified, rowidname):
        self.scratch, self.path = scratch, path
        self.headers = list(headers)
        self.identified, self.rowidname = identified, rowidname

    def page(self, offset, *, cancel=None, limit=200):
        from csv_workbench.core import PAGE_SIZE, Cancellation

        cancel = cancel or Cancellation()
        tail = offset is None
        with _connection(self.path, cancel, readonly=True) as db:
            if tail:
                count = db.execute("SELECT count(*) FROM positions").fetchone()[0]
                limit = 2 * PAGE_SIZE
                offset = max(0, ((count - 1) // PAGE_SIZE - 1) * PAGE_SIZE)
            if offset < 0 or limit < 0:
                raise ValueError("Page offset and limit must be nonnegative.")
            cursor = db.execute(
                "SELECT p.rid,r.cells FROM positions p JOIN rows r USING(rid) "
                "WHERE p.pos>=? ORDER BY p.pos LIMIT ?", (offset, limit + 1),
            )
            rows = []
            for rid, cells in cursor:
                cancel.check()
                values = tuple(json.loads(cells))
                rows.append((rid, *values) if self.identified else values)
        headers = [self.rowidname, *self.headers] if self.identified else list(self.headers)
        page = headers, rows[:limit], len(rows) > limit
        return (*page, offset) if tail else page

    def iter_rows(self, *, cancel, start=0, reverse=False):
        """Yield inclusive view positions from start in the requested direction."""
        operator, direction = ("<=", "DESC") if reverse else (">=", "ASC")
        with _connection(self.path, cancel, readonly=True) as db:
            cursor = db.execute(
                "SELECT p.pos,p.rid,r.cells FROM positions p JOIN rows r USING(rid) "
                f"WHERE p.pos{operator}? ORDER BY p.pos {direction}", (start,),
            )
            for position, rid, cells in cursor:
                cancel.check()
                yield position, rid, tuple(json.loads(cells))
        cancel.check()

    def values(self, column, *, cancel):
        if column not in self.headers:
            raise ValueError(f"Unknown CSV column: {column}")
        index = self.headers.index(column)
        with _connection(self.path, cancel, readonly=True) as db:
            cursor = db.execute(
                "SELECT json_extract(r.cells,?) FROM positions p JOIN rows r USING(rid) "
                "ORDER BY p.pos", (f"$[{index}]",),
            )
            for (value,) in cursor:
                cancel.check()
                yield value
        cancel.check()


def prepare_wide_result(
    source, columns, selection, edits, *, cancel, fmt, identified=False,
    distinct=None, singletons=False,
):
    import polars as pl

    from csv_workbench.core import DiskResult, quote_identifier

    cancel.check()
    indices = {name: index for index, name in enumerate(columns)}

    def index(name):
        if name not in indices:
            raise ValueError(f"Unknown CSV column: {name}")
        return indices[name]

    ordered = selection.ordered_columns(columns)
    if selection.visible_columns is not None:
        if not selection.visible_columns:
            raise ValueError("Select at least one visible column.")
        for name in selection.visible_columns:
            index(name)
    visible = [name for name in ordered
               if selection.visible_columns is None or name in selection.visible_columns]
    projected = [index(name) for name in visible]
    matches = selection_matcher(columns, selection)
    keys = [(index(name), descending) for name, descending in selection.sort_keys]
    distinct_index = index(distinct) if distinct is not None else None
    rowidname = "__csv_source_row"
    folded = {name.casefold() for name in columns}
    while rowidname.casefold() in folded:
        rowidname += "_"

    scratch = TemporaryDirectory(prefix="csv-wide-")
    root = Path(scratch.name)
    path = root / "result.sqlite"
    stage, stage_bytes, files = [], 0, []
    schema = {"value": pl.String} if distinct is not None else {
        "rid": pl.Int64, **{f"k{i}": pl.String for i in range(len(keys))},
    }

    def flush_stage():
        nonlocal stage_bytes
        if not stage:
            return
        cancel.check()
        file = root / f"keys-{len(files):08}.parquet"
        pl.DataFrame(stage, orient="row", schema=schema).write_parquet(file)
        files.append(str(file))
        stage.clear()
        stage_bytes = 0

    def stage_row(values):
        nonlocal stage_bytes
        size = sum(len(value.encode("utf-8")) if isinstance(value, str) else 8 for value in values)
        if stage and (len(stage) >= BATCH_ROWS or stage_bytes + size > BATCH_BYTES):
            flush_stage()
        stage.append(values)
        stage_bytes += size

    try:
        with _connection(path, cancel) as db, db:
            if distinct is None:
                db.executescript(
                    "CREATE TABLE rows(rid INTEGER PRIMARY KEY,cells TEXT NOT NULL);"
                    "CREATE TABLE positions(pos INTEGER PRIMARY KEY,rid INTEGER NOT NULL);"
                )
            payload, positions, payload_bytes = [], [], 0

            def flush_payload():
                nonlocal payload_bytes
                cancel.check()
                db.executemany("INSERT INTO rows VALUES (?,?)", payload)
                if positions:
                    db.executemany("INSERT INTO positions VALUES (?,?)", positions)
                payload.clear()
                positions.clear()
                payload_bytes = 0

            position = 0
            with closing(effective_rows(source, columns, edits, cancel=cancel, fmt=fmt)) as rows:
                for rid, row in rows:
                    cancel.check()
                    if not matches(row):
                        continue
                    if distinct is not None:
                        stage_row((row[distinct_index],))
                        continue
                    cells = json.dumps([row[column] for column in projected],
                                       ensure_ascii=False, separators=(",", ":"))
                    size = len(cells.encode("utf-8"))
                    if payload and (len(payload) >= BATCH_ROWS or payload_bytes + size > BATCH_BYTES):
                        flush_payload()
                    payload.append((rid, cells))
                    payload_bytes += size
                    if keys:
                        stage_row((rid, *(row[column] for column, _ in keys)))
                    else:
                        positions.append((position, rid))
                    position += 1
            if distinct is None:
                flush_payload()
            if keys or distinct is not None:
                flush_stage()
                if not files:
                    empty = root / "keys-empty.parquet"
                    pl.DataFrame(schema=schema).write_parquet(empty)
                    files.append(str(empty))
                output = root / "result.parquet"
                with cancel.connection() as connection:
                    if distinct is not None:
                        having = " HAVING count(*)=1" if singletons else ""
                        sql = (f'SELECT value AS "Value",count(*) AS "Frequency" FROM read_parquet(?) '
                               f"GROUP BY value{having} ORDER BY value NULLS LAST")
                    else:
                        order = ",".join(
                            f'{quote_identifier(f"k{i}")} {"DESC" if descending else "ASC"} NULLS LAST'
                            for i, (_, descending) in enumerate(keys)
                        )
                        sql = f"SELECT rid FROM read_parquet(?) ORDER BY {order},rid"
                    connection.execute(f"COPY ({sql}) TO ? (FORMAT PARQUET)", [str(output), files])
                cancel.check()
                if distinct is None:
                    position = 0

                    def insert_positions(batch):
                        nonlocal position
                        if cancel.cancelled:
                            return True
                        ids = batch.get_column("rid").to_list()
                        db.executemany("INSERT INTO positions VALUES (?,?)",
                                       ((position + i, rid) for i, rid in enumerate(ids)))
                        position += len(ids)
                        return cancel.cancelled

                    pl.scan_parquet(output).sink_batches(
                        insert_positions, chunk_size=BATCH_ROWS, maintain_order=True, engine="streaming",
                    )
                    cancel.check()
                    output.unlink()
                for file in files:
                    cancel.check()
                    Path(file).unlink()
        cancel.check()
        if distinct is not None:
            path.unlink()
            return DiskResult(scratch, root / "result.parquet")
        return WideResult(scratch, path, visible, identified, rowidname)
    except pl.exceptions.PolarsError as exc:
        scratch.cleanup()
        cancel.check()
        raise ValueError(str(exc)) from exc
    except BaseException:
        scratch.cleanup()
        raise
