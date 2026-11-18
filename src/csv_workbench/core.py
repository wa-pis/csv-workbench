"""Read pages from a local CSV without modifying the source."""

import codecs
import csv
import io
import re
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

import duckdb

from csv_workbench.text_formats import formatted_chunks

PAGE_SIZE = 200
NEW_ROW = 1 << 62
DELETED_COLUMN = -1
CSV_ENCODINGS = {
    "Auto": None,
    "UTF-8": "utf-8-sig",
    "UTF-16 with BOM": "utf-16",
    "UTF-16 LE (no BOM)": "utf-16-le",
    "UTF-16 BE (no BOM)": "utf-16-be",
    "UTF-32 with BOM": "utf-32",
    "Windows-1251": "cp1251",
    "Windows-1252": "cp1252",
    "KOI8-R": "koi8-r",
    "CP866": "cp866",
}


@dataclass(frozen=True)
class CsvFormat:
    delimiter: str = ","
    header: bool = True
    encoding: str | None = None
    quote: str = '"'

    def __post_init__(self):
        if (
            not isinstance(self.delimiter, str)
            or len(self.delimiter) != 1
            or (self.delimiter != "\t" and not (" " <= self.delimiter <= "~"))
            or self.delimiter == '"'
        ):
            raise ValueError("Enter one ASCII delimiter character other than a double quote.")
        if not isinstance(self.header, bool):
            raise ValueError("Invalid CSV header setting.")  # noqa: TRY004

        if self.encoding not in CSV_ENCODINGS.values():
            raise ValueError("Unsupported CSV encoding.")
        if (
            not isinstance(self.quote, str)
            or len(self.quote) != 1
            or not (" " <= self.quote <= "~")
            or self.quote == self.delimiter
        ):
            raise ValueError("Enter one printable ASCII quote character distinct from the delimiter.")

    @property
    def source(self):
        delimiter = self.delimiter.replace("'", "''")
        quote = self.quote.replace("'", "''")
        return (
            f"read_csv(?, header = {str(self.header).lower()}, delim = '{delimiter}', "
            f"quote = '{quote}', escape = '{quote}', all_varchar = true, hive_partitioning = false)"
        )


CSV_SOURCE = CsvFormat().source


def read_format_sample(path, encoding=None):
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    with path.open(encoding=encoding or detect_encoding(path), newline="") as handle:
        sample = handle.read(128 * 1024 + 1)
    return sample[: 128 * 1024], len(sample) > 128 * 1024


def format_records(sample, delimiter, limit=11, *, quote='"'):
    text, truncated = sample
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar=quote, strict=True)
    rows = []
    try:
        for row in reader:
            if row:
                rows.append(row)
            if len(rows) == limit:
                break
        else:
            if truncated and rows and not text.endswith(("\n", "\r")):
                rows.pop()
    except csv.Error:
        if not truncated or reader.line_num < len(text.splitlines()):
            raise
    return rows


def detect_delimiter(sample, fallback=",", *, quote='"'):
    best, score = (fallback if fallback != quote else ";"), 1
    for delimiter in dict.fromkeys((fallback, ",", ";", "\t")):
        if delimiter == quote:
            continue
        try:
            rows = format_records(sample, delimiter, quote=quote)
        except csv.Error:
            continue
        if rows and len(rows[0]) > score and all(len(row) == len(rows[0]) for row in rows):
            best, score = delimiter, len(rows[0])
    return best


def preview_format(sample, fmt):
    rows = format_records(sample, fmt.delimiter, limit=10 + fmt.header, quote=fmt.quote)
    if not rows:
        return [], []
    if fmt.header:
        return rows[0], rows[1:]
    width = max(map(len, rows))
    digits = len(str(width - 1))
    return [f"column{i:0{digits}d}" for i in range(width)], rows


@dataclass(frozen=True)
class Selection:
    filters: tuple[tuple[str, str], ...] = ()
    sort_column: str | None = None
    descending: bool = False
    visible_columns: tuple[str, ...] | None = None
    match_any: bool = False
    extra_sort: tuple[tuple[str, bool], ...] = ()
    column_order: tuple[str, ...] = ()
    exact_filters: tuple[tuple[str, str, str | None], ...] = ()

    # View metadata must not switch the CSV query or paging path.
    pinned_columns: tuple[str, ...] = field(default=(), compare=False)

    def ordered_columns(self, columns):
        if len(set(self.column_order)) != len(self.column_order):
            raise ValueError("Duplicate column in layout.")
        if missing := set(self.column_order).difference(columns):
            raise ValueError(f"Unknown CSV column: {min(missing)}")
        return list(dict.fromkeys((*self.column_order, *columns)))

    @property
    def sort_keys(self):
        return (
            ((self.sort_column, self.descending), *self.extra_sort)
            if self.sort_column is not None
            else ()
        )

    def cycle_sort(self, column, *, append=False):
        keys = dict(self.sort_keys)
        previous = keys.get(column)
        if not append:
            keys.clear()
        if previous is True:
            keys.pop(column, None)
        else:
            keys[column] = previous is False
        (first, descending), *extra = list(keys.items()) or [(None, False)]
        return replace(self, sort_column=first, descending=descending, extra_sort=tuple(extra))

    @property
    def conditions(self):
        return tuple((column, value) for column, value in self.filters if value)

    @property
    def filter_columns(self):
        return {column for column, _ in self.conditions} | {
            column for column, _, _ in self.exact_filters
        }


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
def utf8_source(path: Path, cancel: Cancellation, fmt=CsvFormat()):
    cancel.check()
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    encoding = fmt.encoding or detect_encoding(path)
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


def count_rows(path: Path, *, fmt=CsvFormat(), cancel: Cancellation | None = None) -> int:
    cancel = cancel or Cancellation()
    with utf8_source(path, cancel, fmt) as source, cancel.connection() as connection:
        return connection.execute(
            f"SELECT count(*) FROM {fmt.source}", [csv_pattern(source)]
        ).fetchone()[0]


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def filter_clause(selection, identifier):
    predicates = [
        f"contains(lower({identifier(column)}), lower(?))" for column, _ in selection.conditions
    ]
    values = [value for _, value in selection.conditions]
    operators = {"equals": "IS NOT DISTINCT FROM", "not_equals": "IS DISTINCT FROM"}
    for column, operator, value in selection.exact_filters:
        if operator not in operators:
            raise ValueError(f"Unknown filter operation: {operator}")
        predicates.append(f"{identifier(column)} {operators[operator]} ?")
        values.append(value)
    joiner = " OR " if selection.match_any else " AND "
    return (
        " WHERE " + joiner.join(predicates) if predicates else "",
        values,
    )


def csv_columns(connection, pattern, fmt):
    # A parameterized relation may materialize the full CSV just to get its schema.
    cursor = connection.execute(f"SELECT * FROM {fmt.source} LIMIT 0", [pattern])
    return [column[0] for column in cursor.description]


def selection_query(
    connection, path: Path, selection: Selection, fmt=CsvFormat()
) -> tuple[str, list[str | None]]:
    pattern = csv_pattern(path)
    columns = csv_columns(connection, pattern, fmt) if selection != Selection() else []
    sql, values = selection_sql(columns, selection, source=fmt.source)
    return sql, [pattern, *values]


def sort_clause(selection, identifier, tie_breaker="row_number() OVER ()"):
    keys = [
        f"{identifier(column)} {'DESC' if descending else 'ASC'} NULLS LAST"
        for column, descending in selection.sort_keys
    ]
    return " ORDER BY " + ", ".join([*keys, tie_breaker]) if keys else ""


def selection_sql(
    columns: list[str], selection: Selection, *, source=CSV_SOURCE, row_id=None
) -> tuple[str, list[str | None]]:
    projection, where, order = "*", "", ""
    values = []
    if selection != Selection():

        def identifier(name: str) -> str:
            if name not in columns:
                raise ValueError(f"Unknown CSV column: {name}")
            return quote_identifier(name)

        if selection.visible_columns is not None:
            if not selection.visible_columns:
                raise ValueError("Select at least one visible column.")
            for name in selection.visible_columns:
                identifier(name)
        if selection.visible_columns is not None or selection.column_order:
            projection = ", ".join(
                identifier(name) for name in selection.ordered_columns(columns)
                if selection.visible_columns is None or name in selection.visible_columns
            )
        where, values = filter_clause(selection, identifier)
        order = sort_clause(selection, identifier, row_id or "row_number() OVER ()")
    if row_id is not None:
        projection = f"{row_id}, {projection if projection != '*' else ', '.join(map(quote_identifier, columns))}"
    return f"SELECT {projection} FROM {source}{where}{order}", values


def editable_query(connection, source, selection, edits, fmt=CsvFormat()):
    pattern = csv_pattern(source)
    columns = csv_columns(connection, pattern, fmt)
    name = "__csv_source_row"
    folded = {column.casefold() for column in columns}
    while name.casefold() in folded:
        name += "_"
    row_id = quote_identifier(name)
    edited_columns = {col for _, col in edits}
    scan = f"(SELECT row_number() OVER () - 1 AS {row_id}, * FROM {fmt.source})"
    if edits:
        connection.execute("CREATE TEMP TABLE cell_edits (rid BIGINT, col INTEGER, val VARCHAR)")
        connection.executemany(
            "INSERT INTO cell_edits VALUES (?, ?, ?)",
            [(row, col, value) for (row, col), value in edits.items()],
        )
        projection = ", ".join(
            f"CASE WHEN map_contains(p.cells, {index}) THEN map_extract_value(p.cells, {index}) ELSE s.{quoted} END AS {quoted}"
            if index in edited_columns
            else f"s.{quoted}"
            for index, quoted in enumerate(map(quote_identifier, columns))
        )
        patches = "(SELECT rid, map(list(col), list(val)) AS cells FROM cell_edits GROUP BY rid)"
        if any(row >= NEW_ROW for row, _ in edits):
            blanks = ", ".join(f"NULL::VARCHAR AS {quote_identifier(col)}" for col in columns)
            scan = f"({scan} UNION ALL SELECT rid, {blanks} FROM {patches} WHERE rid >= {NEW_ROW})"
        scan = (
            f"(SELECT s.{row_id}, {projection} FROM {scan} s LEFT JOIN "
            f"{patches} p ON s.{row_id} = p.rid "
            f"WHERE NOT coalesce(map_contains(p.cells, {DELETED_COLUMN}), false))"
        )
    sql, values = selection_sql(columns, selection, source=scan, row_id=row_id)
    if edits and selection.sort_column is None:
        sql += f" ORDER BY {row_id}"
    return sql, [pattern, *values]


def read_editable_page(path, offset, selection, edits, *, cancel, fmt=CsvFormat(), limit=PAGE_SIZE):
    with utf8_source(path, cancel, fmt) as source, cancel.connection() as connection:
        sql, params = editable_query(connection, source, selection, edits, fmt)
        return query_page(connection, sql, params, offset, limit)


def prepare_editable_result(
    path, selection, edits, *, cancel, distinct=None, singletons=False, fmt=CsvFormat()
):
    scratch = TemporaryDirectory(prefix="csv-result-")
    output = Path(scratch.name) / "result.parquet"
    try:
        with utf8_source(path, cancel, fmt) as source, cancel.connection() as connection:
            sql, params = editable_query(connection, source, selection, edits, fmt)
            if distinct is not None:
                having = " HAVING count(*) = 1" if singletons else ""
                sql = (
                    f'SELECT {quote_identifier(distinct)} AS "Value", count(*) AS "Frequency"'
                    f" FROM ({sql}) GROUP BY 1{having} ORDER BY 1 NULLS LAST"
                )
            connection.execute(f"COPY ({sql}) TO ? (FORMAT PARQUET)", [str(output), *params])
    except BaseException:
        scratch.cleanup()
        raise
    return DiskResult(scratch, output)


def read_page(
    path: Path,
    offset: int | None,
    selection: Selection = Selection(),
    *,
    cancel: Cancellation | None = None,
    fmt=CsvFormat(),
    limit=PAGE_SIZE,
) -> tuple[list[str], list[tuple], bool]:
    cancel = cancel or Cancellation()
    with utf8_source(path, cancel, fmt) as source, cancel.connection() as connection:
        sql, params = selection_query(connection, source, selection, fmt)
        cancel.check()
        return query_page(connection, sql, params, offset, limit)


def query_csv(path: Path, sql: str, *, fmt=CsvFormat(), cancel: Cancellation | None = None):
    cancel = cancel or Cancellation()
    with cancel.connection() as connection:
        with utf8_source(path, cancel, fmt) as source:
            connection.execute(
                f"CREATE TABLE csv_data AS SELECT * FROM {fmt.source}", [csv_pattern(source)]
            )
        cancel.check()
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
        statements = connection.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise ValueError("Enter one SELECT statement.")
        cursor = connection.sql(statements[0].query).limit(PAGE_SIZE + 1)
        return page_result(cursor)


def write_csv_rows(cursor, output, fmt, cancel, *, skip=0):
    count = 0
    with output.open("w", encoding=fmt.encoding or "utf-8", newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter=fmt.delimiter,
            quotechar=fmt.quote,
            quoting=csv.QUOTE_NOTNULL,
            lineterminator="\r\n",
        )
        if fmt.header:
            writer.writerow([column[0] for column in cursor.description[skip:]])
        while batch := cursor.fetchmany(1024):
            cancel.check()
            for row in batch:
                values = row[skip:]
                writer.writerow(("",) if values == (None,) else values)
            count += len(batch)
    return count


def export_csv(
    path: Path,
    destination: Path,
    selection: Selection,
    *,
    fmt=CsvFormat(),
    output_format=CsvFormat(),
    cancel: Cancellation | None = None,
) -> int:
    cancel = cancel or Cancellation()
    cancel.check()
    source = path.resolve(strict=True)
    destination = destination.absolute()
    if destination.resolve() == source or (destination.exists() and destination.samefile(source)):
        raise ValueError("Choose a destination other than the source CSV.")

    with TemporaryDirectory(dir=destination.parent) as scratch:
        output = Path(scratch) / "export.csv"
        with utf8_source(source, cancel, fmt) as decoded, cancel.connection() as connection:
            sql, params = selection_query(connection, decoded, selection, fmt)
            cancel.check()
            if output_format == CsvFormat():
                # Keep the native streaming writer for the default format.
                count = connection.execute(
                    f"COPY ({sql}) TO ? (FORMAT CSV, HEADER true, COMPRESSION none)",
                    [str(output), *params],
                ).fetchone()[0]
            else:
                count = write_csv_rows(
                    connection.execute(sql, params), output, output_format, cancel
                )
        cancel.publish(output, destination)
    return count


def export_text(
    path, destination, selection, text_format, *, edits=None, document=None,
    revision=None, fmt=CsvFormat(), cancel=None,
):
    cancel = cancel or Cancellation()
    cancel.check()
    if text_format not in ("JSON", "Markdown"):
        raise ValueError("Choose JSON or Markdown.")
    source = path.resolve(strict=True)
    document = document or source
    revision = revision or file_revision(document)
    destination = destination.absolute()
    if destination.resolve() == document.resolve() or (
        destination.exists() and destination.samefile(document)
    ):
        raise ValueError("Choose a destination other than the source CSV.")

    def check_source():
        if file_revision(document) != revision:
            raise ValueError("The source CSV changed. Reopen it before exporting.")

    check_source()
    count = 0
    with TemporaryDirectory(dir=destination.parent) as scratch:
        output = Path(scratch) / "export"
        with utf8_source(source, cancel, fmt) as decoded, cancel.connection() as connection:
            if edits or source != document.resolve():
                sql, params = editable_query(connection, decoded, selection, edits or {}, fmt)
                skip = 1
            else:
                sql, params = selection_query(connection, decoded, selection, fmt)
                skip = 0
            cursor = connection.execute(sql, params)
            headers = [column[0] for column in cursor.description[skip:]]

            def rows():
                nonlocal count
                while batch := cursor.fetchmany(1024):
                    cancel.check()
                    for row in batch:
                        count += 1
                        yield row[skip:]

            with output.open("w", encoding="utf-8", newline="") as handle:
                for chunk in formatted_chunks(headers, rows(), text_format):
                    cancel.check()
                    handle.write(chunk)
        check_source()
        cancel.publish(output, destination)
    return count


def file_revision(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def export_edited_csv(
    path,
    destination,
    edits,
    revision,
    selection=Selection(),
    *,
    cancel=None,
    save=False,
    document=None,
    fmt=CsvFormat(),
    output_format=CsvFormat(),
):
    cancel = cancel or Cancellation()
    source = path.resolve(strict=True)
    destination = destination.absolute()
    document = document or source
    if save:
        destination = destination.resolve()
    elif destination.resolve() == document.resolve() or (
        destination.exists() and destination.samefile(document)
    ):
        raise ValueError("Choose a destination other than the source CSV.")

    def check_source():
        if file_revision(document) != revision:
            raise ValueError("The source CSV changed. Reopen it before editing.")

    check_source()
    with TemporaryDirectory(dir=destination.parent) as scratch:
        output = Path(scratch) / "edited.csv"
        with utf8_source(source, cancel, fmt) as decoded, cancel.connection() as connection:
            sql, params = editable_query(connection, decoded, selection, edits, fmt)
            count = write_csv_rows(
                connection.execute(sql, params), output, fmt if save else output_format, cancel, skip=1
            )
        if save and destination.exists():
            output.chmod(destination.stat().st_mode & 0o777)
        check_source()
        cancel.publish(output, destination)
    return count


def save_csv(path, destination, edits, revision, *, cancel=None, source=None, fmt=CsvFormat()):
    destination = destination.resolve()
    export_edited_csv(
        source or path,
        destination,
        edits,
        revision,
        cancel=cancel,
        save=True,
        document=path,
        fmt=fmt,
    )
    return destination, file_revision(destination)


class DiskResult:
    """A reusable query result; active readers keep its temporary directory alive."""

    def __init__(self, scratch: TemporaryDirectory, path: Path):
        self.scratch = scratch
        self.path = path

    def page(self, offset: int | None, *, cancel: Cancellation | None = None, limit=PAGE_SIZE):
        with (cancel or Cancellation()).connection() as connection:
            return query_page(
                connection, "SELECT * FROM read_parquet(?)", [str(self.path)], offset, limit
            )


def search_lower(value):
    # Match DuckDB's simple Unicode lowercase mapping, including Greek sigma.
    return value.lower() if value.isascii() else "".join(c.lower()[0] for c in value)


def find_csv_cell(
    path, selection, edits, columns, needle, anchor, forward, *,
    cancel, fmt=CsvFormat(), result=None,
):
    """Return a zero-based (row, column) in the existing view, without storing matches."""
    with cancel.connection() as connection:
        if result is not None:
            return find_cell_in_query(
                connection, "SELECT * FROM read_parquet(?)", [str(result.path)],
                columns, needle, anchor, forward,
            )
        with utf8_source(path, cancel, fmt) as source:
            if edits or selection != Selection():
                sql, params = editable_query(connection, source, selection, edits, fmt)
            else:
                sql, params = selection_query(connection, source, selection, fmt)
            cancel.check()
            return find_cell_in_query(connection, sql, params, columns, needle, anchor, forward)


def find_cell_in_query(connection, sql, params, columns, needle, anchor, forward):
    aliases = [f"c{i}" for i in range(len(columns))]
    if not aliases or not needle:
        return None
    comparison, direction = (">", "ASC") if forward else ("<", "DESC")
    indices = range(len(columns)) if forward else range(len(columns) - 1, -1, -1)
    checks = ", ".join(
        f"(rn <> anchor_row OR {i} {comparison} anchor_col) "
        f"AND contains(lower(CAST(c{i} AS VARCHAR)), needle)"
        for i in indices
    )
    projected = ", ".join(map(quote_identifier, columns))
    row, column = anchor
    found = connection.execute(
        f"""
        WITH numbered(rn, {", ".join(aliases)}) AS (
            SELECT row_number() OVER () - 1, {projected} FROM ({sql})
        ), options AS (
            SELECT lower(?) AS needle, ?::BIGINT AS anchor_row, ?::INTEGER AS anchor_col
        )
        SELECT rn, list_position([{checks}], true) AS pos
        FROM numbered, options
        WHERE rn {comparison}= anchor_row AND pos IS NOT NULL
        ORDER BY rn {direction}
        LIMIT 1
        """,
        [*params, needle, row, column],
    ).fetchone()
    if found is None:
        return None
    row, pos = found
    return row, pos - 1 if forward else len(columns) - pos


def query_page(connection, sql, params, offset, limit):
    tail = offset is None
    if tail:
        count = connection.execute(f"SELECT count(*) FROM ({sql})", params).fetchone()[0]
        # Include the preceding page so the user can scroll up even with a short final page.
        limit = 2 * PAGE_SIZE
        offset = max(0, ((count - 1) // PAGE_SIZE - 1) * PAGE_SIZE)
    cursor = connection.execute(sql + " LIMIT ? OFFSET ?", [*params, limit + 1, offset])
    page = page_result(cursor, limit)
    return (*page, offset) if tail else page


def page_result(cursor, limit=PAGE_SIZE):
    headers = [column[0] for column in cursor.description]
    rows = cursor.fetchall()
    return headers, rows[:limit], len(rows) > limit


CSV_BLOCK_BYTES = 8 * 2**20


def custom_quote_boundary(data, opening, closing):
    end = pos = 0
    while match := opening.search(data, pos):
        end = max(end, data.rfind(b"\n", pos, match.end() - 1) + 1)
        match = closing.match(data, match.end())
        if match is None:
            return end
        pos = match.end()
    return max(end, data.rfind(b"\n", pos) + 1)


def csv_blocks(source, cancel, block_bytes=CSV_BLOCK_BYTES, *, quote=b'"', delimiter=b","):
    """Split on record boundaries, including CSV fields containing newlines."""
    opening = closing = None
    if quote != b'"':
        escaped = re.escape(quote)
        opening = re.compile(rb"(?:\A|[" + re.escape(delimiter) + rb"\n])" + escaped)
        closing = re.compile(rb"(?:[^" + escaped + rb"]++|" + escaped * 2 + rb")*+" + escaped)
    pending = b""
    while chunk := source.read(block_bytes):
        cancel.check()
        pending += chunk
        end = pending.rfind(b"\n") + 1
        if not end:
            continue
        if opening is not None:
            end = custom_quote_boundary(pending, opening, closing)
        elif pending.count(quote, 0, end) % 2:
            # An escaped quote contributes two quotes, so it cannot change parity.
            while end:
                previous = pending.rfind(b"\n", 0, end - 1) + 1
                if pending.count(quote, previous, end) % 2:
                    end = previous
                    break
                end = previous
        if end:
            yield pending[:end]
            pending = pending[end:]
    if pending:
        yield pending


def stage_selection(path, selection, distinct, directory, cancel, fmt=CsvFormat()):
    """Stream just the required columns into Parquet batches."""
    import polars as pl

    visible = (distinct,) if distinct is not None else selection.visible_columns
    if visible is not None and not visible:
        raise ValueError("Select at least one visible column.")
    sort = [column for column, _ in selection.sort_keys] if distinct is None else []
    files = []
    with path.open("rb") as source, cancel.connection() as connection:
        blocks = csv_blocks(
            source, cancel, quote=fmt.quote.encode("ascii"), delimiter=fmt.delimiter.encode("ascii")
        )
        first = next(blocks, None)
        if first is None:
            raise ValueError("CSV has no header.")
        first_frame = pl.read_csv(
            first,
            has_header=fmt.header,
            separator=fmt.delimiter,
            quote_char=fmt.quote,
            infer_schema=False,
            n_threads=2,
            null_values="",
        )
        if not fmt.header:
            digits = len(str(first_frame.width - 1))
            first_frame.columns = [f"column{i:0{digits}d}" for i in range(first_frame.width)]
        names = first_frame.columns
        required = set(names if visible is None else visible)
        required.update(selection.filter_columns)
        required.update(sort)
        if missing := required.difference(names):
            raise ValueError(f"Unknown CSV column: {min(missing)}")
        ordered = selection.ordered_columns(names) if distinct is None else names
        output_columns = [name for name in ordered if visible is None or name in visible]
        staged_columns = [name for name in names if name in required]

        result_columns = list(dict.fromkeys(output_columns + sort))
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
                    separator=fmt.delimiter,
                    quote_char=fmt.quote,
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
    fmt=CsvFormat(),
) -> DiskResult:
    import polars as pl

    cancel = cancel or Cancellation()
    scratch = TemporaryDirectory(prefix="csv-result-")
    output = Path(scratch.name) / "result.parquet"
    try:
        with TemporaryDirectory(prefix="csv-stage-") as staging:
            with utf8_source(path, cancel, fmt) as source:
                columns, files = stage_selection(
                    source, selection, distinct, Path(staging), cancel, fmt
                )

            projection = ", ".join(map(quote_identifier, columns))
            if distinct is not None:
                having = " HAVING count(*) = 1" if singletons else ""
                sql = (
                    f'SELECT {projection} AS "Value", count(*) AS "Frequency"'
                    f" FROM read_parquet(?) GROUP BY 1{having} ORDER BY 1 NULLS LAST"
                )
            else:
                order = sort_clause(selection, quote_identifier)
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
