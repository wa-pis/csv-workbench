"""Read pages from a local CSV without modifying the source."""

import re
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

import duckdb

PAGE_SIZE = 200
CSV_SOURCE = """read_csv(?, header = true, delim = ',', quote = '"', escape = '"',
                         all_varchar = true, hive_partitioning = false)"""


@dataclass(frozen=True)
class Selection:
    filter_column: str | None = None
    substring: str = ""
    sort_column: str | None = None
    descending: bool = False
    visible_columns: tuple[str, ...] | None = None


class Cancellation:
    def __init__(self):
        self.lock = Lock()
        self.cancelled = False
        self.active = None

    def cancel(self):
        with self.lock:
            self.cancelled = True
            if self.active is not None:
                self.active.interrupt()

    def check(self):
        if self.cancelled:
            raise CancelledError()

    @contextmanager
    def connection(self):
        self.check()
        with (
            TemporaryDirectory(prefix="csv-workbench-") as scratch,
            duckdb.connect(
                config={
                    "preserve_insertion_order": True,
                    "memory_limit": "512MiB",
                    "threads": 2,
                    "temp_directory": scratch,
                }
            ) as connection,
        ):
            with self.lock:
                self.check()
                self.active = connection
            try:
                yield connection
                self.check()
            except duckdb.Error:
                self.check()
                raise
            finally:
                with self.lock:
                    self.active = None

    def publish(self, output: Path, destination: Path):
        with self.lock:
            self.check()
            output.replace(destination)


def csv_pattern(path: Path) -> str:
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")

    # DuckDB treats path arguments as globs, including in bound parameters.
    return re.sub(r"[*?\[]", lambda match: f"[{match[0]}]", path.as_posix())


def count_rows(path: Path, *, cancel: Cancellation | None = None) -> int:
    with (cancel or Cancellation()).connection() as connection:
        return connection.execute(
            f"SELECT count(*) FROM {CSV_SOURCE}", [csv_pattern(path)]
        ).fetchone()[0]


def selection_query(connection, path: Path, selection: Selection) -> tuple[str, list[str]]:
    pattern = csv_pattern(path)
    projection, where, order = "*", "", ""
    params = [pattern]
    if selection != Selection():
        columns = connection.sql(f"SELECT * FROM {CSV_SOURCE}", params=[pattern]).columns

        def identifier(name: str) -> str:
            if name not in columns:
                raise ValueError(f"Unknown CSV column: {name}")
            return '"' + name.replace('"', '""') + '"'

        if selection.visible_columns is not None:
            if not selection.visible_columns:
                raise ValueError("Select at least one visible column.")
            for name in selection.visible_columns:
                identifier(name)
            projection = ", ".join(
                identifier(name) for name in columns if name in selection.visible_columns
            )
        if selection.filter_column is not None and selection.substring:
            column = identifier(selection.filter_column)
            where = f" WHERE contains(lower({column}), lower(?))"
            params.append(selection.substring)
        if selection.sort_column is not None:
            column = identifier(selection.sort_column)
            direction = "DESC" if selection.descending else "ASC"
            order = f" ORDER BY {column} {direction} NULLS LAST, row_number() OVER ()"

    return f"SELECT {projection} FROM {CSV_SOURCE}{where}{order}", params


def read_page(
    path: Path,
    offset: int,
    selection: Selection = Selection(),
    *,
    cancel: Cancellation | None = None,
) -> tuple[list[str], list[tuple], bool]:
    cancel = cancel or Cancellation()
    with cancel.connection() as connection:
        sql, params = selection_query(connection, path, selection)
        cancel.check()
        cursor = connection.execute(
            sql + " LIMIT ? OFFSET ?",
            [*params, PAGE_SIZE + 1, offset],
        )
        return page_result(cursor)


def export_csv(
    path: Path, destination: Path, selection: Selection, *, cancel: Cancellation | None = None
) -> int:
    cancel = cancel or Cancellation()
    cancel.check()
    source = path.resolve(strict=True)
    destination = destination.absolute()
    if destination.resolve() == source or (destination.exists() and destination.samefile(source)):
        raise ValueError("Choose a destination other than the source CSV.")

    with TemporaryDirectory(dir=destination.parent) as scratch:
        output = Path(scratch) / "export.csv"
        with cancel.connection() as connection:
            sql, params = selection_query(connection, source, selection)
            cancel.check()
            # DuckDB binds the COPY destination before the inner query parameters.
            count = connection.execute(
                f"COPY ({sql}) TO ? (FORMAT CSV, HEADER true, COMPRESSION none)",
                [str(output), *params],
            ).fetchone()[0]
        cancel.publish(output, destination)
    return count


class DiskResult:
    """A reusable query result; active readers keep its temporary directory alive."""

    def __init__(self, scratch: TemporaryDirectory, path: Path):
        self.scratch = scratch
        self.path = path

    def page(self, offset: int, *, cancel: Cancellation | None = None):
        with (cancel or Cancellation()).connection() as connection:
            cursor = connection.execute(
                "SELECT * FROM read_parquet(?) LIMIT ? OFFSET ?",
                [str(self.path), PAGE_SIZE + 1, offset],
            )
            return page_result(cursor)


def page_result(cursor):
    headers = [column[0] for column in cursor.description]
    rows = cursor.fetchall()
    return headers, rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


CSV_BLOCK_BYTES = 8 * 2**20


def csv_blocks(source, cancel, block_bytes=CSV_BLOCK_BYTES):
    """Split on record boundaries, including CSV fields containing newlines."""
    pending = b""
    while chunk := source.read(block_bytes):
        cancel.check()
        pending += chunk
        end = pending.rfind(b"\n") + 1
        if not end:
            continue
        if pending.count(b'"', 0, end) % 2:
            # An escaped quote contributes two quotes, so it cannot change parity.
            while end:
                previous = pending.rfind(b"\n", 0, end - 1) + 1
                if pending.count(b'"', previous, end) % 2:
                    end = previous
                    break
                end = previous
        if end:
            yield pending[:end]
            pending = pending[end:]
    if pending:
        yield pending


def stage_selection(path, selection, distinct, directory, cancel):
    """Stream just the required columns and matching rows into Parquet batches."""
    import polars as pl

    visible = (distinct,) if distinct is not None else selection.visible_columns
    if visible is not None and not visible:
        raise ValueError("Select at least one visible column.")
    sort = selection.sort_column if distinct is None else None
    files = []
    with path.open("rb") as source:
        blocks = csv_blocks(source, cancel)
        first = next(blocks, None)
        if first is None:
            raise ValueError("CSV has no header.")
        first_frame = pl.read_csv(first, infer_schema=False, n_threads=2, null_values="")
        names = first_frame.columns
        required = set(names if visible is None else visible)
        required.update(name for name in (sort, selection.filter_column) if name is not None)
        if missing := required.difference(names):
            raise ValueError(f"Unknown CSV column: {min(missing)}")
        output_columns = [name for name in names if visible is None or name in visible]
        staged_columns = list(dict.fromkeys(output_columns + ([sort] if sort else [])))

        def write_batch(frame, index):
            frame = frame.lazy()
            if selection.filter_column is not None and selection.substring:
                frame = frame.filter(
                    pl.col(selection.filter_column)
                    .str.to_lowercase()
                    .str.contains(pl.lit(selection.substring).str.to_lowercase(), literal=True)
                )
            batch = frame.select(staged_columns).collect()
            cancel.check()
            if batch.height:
                file = directory / f"batch-{index:08}.parquet"
                batch.write_parquet(file)
                files.append(str(file))

        write_batch(first_frame, 0)
        for index, block in enumerate(blocks, 1):
            write_batch(
                pl.read_csv(
                    block,
                    has_header=False,
                    infer_schema=False,
                    n_threads=2,
                    new_columns=names,
                    null_values="",
                    raise_if_empty=False,
                ),
                index,
            )
    if not files:
        empty = directory / "empty.parquet"
        pl.DataFrame(schema={name: pl.String for name in staged_columns}).write_parquet(empty)
        files.append(str(empty))
    return output_columns, files


def prepare_result(
    path: Path,
    selection: Selection,
    *,
    distinct: str | None = None,
    singletons: bool = False,
    cancel: Cancellation | None = None,
) -> DiskResult:
    import polars as pl

    cancel = cancel or Cancellation()
    scratch = TemporaryDirectory(prefix="csv-result-")
    output = Path(scratch.name) / "result.parquet"
    try:
        with TemporaryDirectory(prefix="csv-stage-") as staging:
            columns, files = stage_selection(path, selection, distinct, Path(staging), cancel)

            def quote(name):
                return '"' + name.replace('"', '""') + '"'

            projection = ", ".join(map(quote, columns))
            order = ""
            if distinct is not None:
                having = " HAVING count(*) = 1" if singletons else ""
                sql = (
                    f'SELECT {projection} AS "Value", count(*) AS "Frequency"'
                    f" FROM read_parquet(?) GROUP BY 1{having} ORDER BY 1 NULLS LAST"
                )
            else:
                if selection.sort_column is not None:
                    direction = "DESC" if selection.descending else "ASC"
                    order = f" ORDER BY {quote(selection.sort_column)} {direction} NULLS LAST, row_number() OVER ()"
                sql = f"SELECT {projection} FROM read_parquet(?){order}"
            with cancel.connection() as connection:
                connection.execute(f"COPY ({sql}) TO ? (FORMAT PARQUET)", [str(output), files])
    except pl.exceptions.PolarsError as exc:
        scratch.cleanup()
        cancel.check()
        raise ValueError(str(exc)) from exc
    except BaseException:
        scratch.cleanup()
        raise
    return DiskResult(scratch, output)
