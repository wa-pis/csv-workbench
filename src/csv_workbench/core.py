"""Read pages from a local CSV without modifying the source."""

import codecs
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
    filters: tuple[tuple[str, str], ...] = ()
    sort_column: str | None = None
    descending: bool = False
    visible_columns: tuple[str, ...] | None = None
    match_any: bool = False

    @property
    def conditions(self):
        return tuple((column, value) for column, value in self.filters if value)


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


def detect_encoding(path: Path) -> str:
    with path.open("rb") as source:
        sample = source.read(128 * 1024)
        complete = not source.read(1)
    for bom, codec in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
        (codecs.BOM_UTF8, "utf-8-sig"),
    ):
        if sample.startswith(bom):
            return codec
    if b"\x00" in sample:
        raise ValueError("Unknown CSV encoding; save the file as UTF-8 or UTF-16 with BOM.")
    try:
        codecs.getincrementaldecoder("utf-8")().decode(sample, final=complete)
    except UnicodeDecodeError:
        from charset_normalizer import from_bytes

        match = from_bytes(sample, cp_isolation=["cp1251", "cp1252", "koi8_r", "cp866"]).best()
        if match is None:
            raise ValueError("Could not detect the CSV encoding; save the file as UTF-8.") from None
        return match.encoding
    return "utf-8"


@contextmanager
def utf8_source(path: Path, cancel: Cancellation):
    cancel.check()
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    encoding = detect_encoding(path)
    if encoding in {"utf-8", "utf-8-sig"}:
        yield path
        return

    # shortcut: legacy encodings use a temporary copy; cache it if repeated scans become costly.
    with TemporaryDirectory(prefix="csv-encoding-") as scratch:
        converted = Path(scratch) / "source.csv"
        with (
            path.open(encoding=encoding, newline="") as source,
            converted.open("w", encoding="utf-8", newline="") as output,
        ):
            while chunk := source.read(1024 * 1024):
                cancel.check()
                output.write(chunk)
        cancel.check()
        yield converted


def csv_pattern(path: Path) -> str:
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")

    # DuckDB treats path arguments as globs, including in bound parameters.
    return re.sub(r"[*?\[]", lambda match: f"[{match[0]}]", path.as_posix())


def count_rows(path: Path, *, cancel: Cancellation | None = None) -> int:
    cancel = cancel or Cancellation()
    with utf8_source(path, cancel) as source, cancel.connection() as connection:
        return connection.execute(
            f"SELECT count(*) FROM {CSV_SOURCE}", [csv_pattern(source)]
        ).fetchone()[0]


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def filter_clause(selection, identifier):
    predicates = [
        f"contains(lower({identifier(column)}), lower(?))" for column, _ in selection.conditions
    ]
    joiner = " OR " if selection.match_any else " AND "
    return (
        " WHERE " + joiner.join(predicates) if predicates else "",
        [value for _, value in selection.conditions],
    )


def selection_query(connection, path: Path, selection: Selection) -> tuple[str, list[str]]:
    pattern = csv_pattern(path)
    projection, where, order = "*", "", ""
    params = [pattern]
    if selection != Selection():
        columns = connection.sql(f"SELECT * FROM {CSV_SOURCE}", params=[pattern]).columns

        def identifier(name: str) -> str:
            if name not in columns:
                raise ValueError(f"Unknown CSV column: {name}")
            return quote_identifier(name)

        if selection.visible_columns is not None:
            if not selection.visible_columns:
                raise ValueError("Select at least one visible column.")
            for name in selection.visible_columns:
                identifier(name)
            projection = ", ".join(
                identifier(name) for name in columns if name in selection.visible_columns
            )
        where, values = filter_clause(selection, identifier)
        params.extend(values)
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
    with utf8_source(path, cancel) as source, cancel.connection() as connection:
        sql, params = selection_query(connection, source, selection)
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
        with utf8_source(source, cancel) as decoded, cancel.connection() as connection:
            sql, params = selection_query(connection, decoded, selection)
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
    """Stream just the required columns into Parquet batches."""
    import polars as pl

    visible = (distinct,) if distinct is not None else selection.visible_columns
    if visible is not None and not visible:
        raise ValueError("Select at least one visible column.")
    sort = selection.sort_column if distinct is None else None
    files = []
    with path.open("rb") as source, cancel.connection() as connection:
        blocks = csv_blocks(source, cancel)
        first = next(blocks, None)
        if first is None:
            raise ValueError("CSV has no header.")
        first_frame = pl.read_csv(first, infer_schema=False, n_threads=2, null_values="")
        names = first_frame.columns
        required = set(names if visible is None else visible)
        required.update(column for column, _ in selection.conditions)
        if sort is not None:
            required.add(sort)
        if missing := required.difference(names):
            raise ValueError(f"Unknown CSV column: {min(missing)}")
        output_columns = [name for name in names if visible is None or name in visible]
        staged_columns = [name for name in names if name in required]

        result_columns = list(dict.fromkeys(output_columns + ([sort] if sort is not None else [])))
        projection = ", ".join(map(quote_identifier, result_columns))
        where, values = filter_clause(selection, quote_identifier)
        raw = directory / "input-block.parquet"

        def write_batch(frame, index):
            cancel.check()
            if not frame.height:
                return
            file = directory / f"batch-{index:08}.parquet"
            if where:
                frame.select(staged_columns).write_parquet(raw)
                cancel.check()
                count = connection.execute(
                    f"COPY (SELECT {projection} FROM read_parquet(?){where}) TO ? (FORMAT PARQUET)",
                    [str(file), str(raw), *values],
                ).fetchone()[0]
                raw.unlink()
                if not count:
                    file.unlink()
                    return
            else:
                frame.select(result_columns).write_parquet(file)
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
        pl.DataFrame(schema={name: pl.String for name in result_columns}).write_parquet(empty)
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
            with utf8_source(path, cancel) as source:
                columns, files = stage_selection(source, selection, distinct, Path(staging), cancel)

            projection = ", ".join(map(quote_identifier, columns))
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
                    order = f" ORDER BY {quote_identifier(selection.sort_column)} {direction} NULLS LAST, row_number() OVER ()"
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
