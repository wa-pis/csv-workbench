"""Bounded value completion over the effective CSV selection."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from csv_workbench.core import (
    CsvFormat,
    editable_query,
    quote_identifier,
    utf8_source,
)

COMPLETION_LIMIT = 100


def complete_values(path, selection, edits, column, prefix, *, cancel, fmt=None, result=None):
    """Return (values, truncated), keeping only 101 candidates."""
    with cancel.connection() as connection:
        if result is not None:
            return query_values(connection, 'SELECT * FROM read_parquet(?)',
                                [str(result.path)], column, prefix, cancel)
        fmt = fmt or CsvFormat()
        with utf8_source(path, cancel, fmt) as source:
            unordered = replace(selection, sort_column=None, descending=False, extra_sort=())
            sql, params = editable_query(
                connection, source, unordered, edits, fmt, cancel=cancel, ordered=False,
            )
            return query_values(connection, sql, params, column, prefix, cancel)


def query_values(connection, sql, params, column, prefix, cancel):
    name = quote_identifier(column)
    # Spool only matching values: DuckDB 1.x fetchmany can retain the entire result.
    with TemporaryDirectory(prefix='csv-completion-') as scratch:
        output = Path(scratch) / 'values.parquet'
        cancel.check()
        connection.execute(
            f'COPY (SELECT {name} FROM ({sql}) WHERE starts_with({name}, ?)) '
            'TO ? (FORMAT PARQUET)', [str(output), *params, prefix],
        )
        cancel.check()
        candidates = set()

        def reduce_batch(batch):
            nonlocal candidates
            if cancel.cancelled:
                return True
            candidates.update(batch.to_series().to_list())
            candidates = set(sorted(candidates)[:COMPLETION_LIMIT + 1])
            return cancel.cancelled

        try:
            pl.scan_parquet(output).sink_batches(
                reduce_batch, chunk_size=8192, engine='streaming',
            )
        except pl.exceptions.PolarsError as exc:
            cancel.check()
            raise ValueError(f'Cannot read completion values: {exc}') from exc
        cancel.check()
        values = sorted(candidates)
        return values[:COMPLETION_LIMIT], len(values) > COMPLETION_LIMIT
