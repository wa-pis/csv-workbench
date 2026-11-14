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
        headers = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
    return headers, rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


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
