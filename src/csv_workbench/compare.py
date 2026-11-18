"""Disk-backed, exact comparison of two saved CSVs."""

from pathlib import Path
from tempfile import TemporaryDirectory

from csv_workbench.core import (
    Cancellation,
    CsvFormat,
    DiskResult,
    csv_columns,
    csv_pattern,
    file_revision,
    quote_identifier,
    utf8_source,
)


def compare_csv(left, right, key, *, left_format=None, right_format=None, cancel=None):
    cancel = cancel or Cancellation()
    cancel.check()
    left_format, right_format = left_format or CsvFormat(), right_format or CsvFormat()
    revisions = file_revision(left), file_revision(right)
    scratch = TemporaryDirectory(prefix="csv-compare-")
    root = Path(scratch.name)
    try:
        with cancel.connection() as connection:
            schemas = []
            for index, (path, fmt) in enumerate(((left, left_format), (right, right_format))):
                with utf8_source(path, cancel, fmt) as decoded:
                    pattern = csv_pattern(decoded)
                    columns = csv_columns(connection, pattern, fmt)
                    schemas.append(columns)
                    if key not in columns:
                        raise ValueError(f"Key column {key!r} is missing in {path.name}.")
                    stage = root / f"{index}.parquet"
                    connection.execute(
                        f"COPY (SELECT * FROM {fmt.source}) TO ? (FORMAT PARQUET)",
                        [str(stage), pattern],
                    )
                identifier = quote_identifier(key)
                invalid = connection.execute(
                    f"SELECT {identifier} FROM read_parquet(?) GROUP BY {identifier} "
                    f"HAVING {identifier} IS NULL OR {identifier} = '' OR count(*) > 1 LIMIT 1",
                    [str(stage)],
                ).fetchone()
                if invalid is not None:
                    raise ValueError(f"Duplicate or empty key {invalid[0]!r} in {path.name}.")
            columns = schemas[0]
            if set(columns) != set(schemas[1]):
                raise ValueError("CSV column names must match; column order may differ.")
            identifier = quote_identifier(key)
            values = [column for column in columns if column != key]
            differences = [
                f"l.{quote_identifier(column)} IS DISTINCT FROM r.{quote_identifier(column)}"
                for column in values
            ]
            projection = [
                (f"CASE WHEN l.{identifier} IS NULL THEN 'Added' "
                f"WHEN r.{identifier} IS NULL THEN 'Removed' ELSE 'Changed' END AS Status"),
                f"coalesce(l.{identifier}, r.{identifier}) AS Key",
            ]
            for column in values:
                for alias, label in (("l", "Before"), ("r", "After")):
                    projection.append(
                        f"{alias}.{quote_identifier(column)} AS {quote_identifier(label + ': ' + column)}"
                    )
            sql = (
                f"SELECT {', '.join(projection)} FROM read_parquet(?) l "
                f"FULL OUTER JOIN read_parquet(?) r ON l.{identifier} = r.{identifier} "
                f"WHERE l.{identifier} IS NULL OR r.{identifier} IS NULL"
                + (" OR " + " OR ".join(differences) if differences else "")
                + " ORDER BY 2"
            )
            output = root / "result.parquet"
            connection.execute(
                f"COPY ({sql}) TO ? (FORMAT PARQUET)",
                [str(output), str(root / "0.parquet"), str(root / "1.parquet")],
            )
            counts = dict(connection.execute(
                "SELECT Status, count(*) FROM read_parquet(?) GROUP BY Status", [str(output)]
            ).fetchall())
        cancel.check()
        if revisions != (file_revision(left), file_revision(right)):
            raise ValueError("A source CSV changed during comparison. Try again.")
        (root / "0.parquet").unlink()
        (root / "1.parquet").unlink()
        result = DiskResult(scratch, output)
        return result, counts, result.page(0, cancel=cancel)
    except BaseException:
        scratch.cleanup()
        raise
