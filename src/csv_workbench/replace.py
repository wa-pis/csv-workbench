"""Prepare replacements on disk without changing the document."""

import json
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from csv_workbench import core
from csv_workbench.core import (
    Cancellation,
    CsvFormat,
    editable_query,
    quote_identifier,
    search_lower,
    utf8_source,
)
from csv_workbench.edits import DiskBatch, DiskEdits


def replace_literal(value, needle, replacement, *, first=False):
    lowered, needle = search_lower(value), search_lower(needle)
    if not needle:
        raise ValueError("Enter nonempty search text.")
    pieces = []
    start = count = 0
    while (position := lowered.find(needle, start)) >= 0:
        pieces.extend((value[start:position], replacement))
        start = position + len(needle)
        count += 1
        if first:
            break
    pieces.append(value[start:])
    return "".join(pieces), count


@dataclass
class ReplacementPlan:
    scratch: TemporaryDirectory
    preview: list
    cells: int
    occurrences: int

    def iter_changes(self, *, cancel):
        with (Path(self.scratch.name) / "changes.jsonl").open(encoding="utf-8") as source:
            for line in source:
                cancel.check()
                row, column, before, after = json.loads(line)
                yield ((row, column), before, after)
        cancel.check()

    def prepare(self, edits, *, cancel):
        before = edits.copy() if isinstance(edits, DiskEdits) else DiskEdits()
        if not isinstance(edits, DiskEdits) and edits:
            before = before.updated(edits.items(), cancel=cancel)
        after = before.updated(((key, value) for key, _, value in self.iter_changes(cancel=cancel)), cancel=cancel)
        return DiskBatch(before, after, self.cells)


@contextmanager
def replacement_rows(source, columns, selection, edits, visible, needle, *, cancel, fmt):
    if len(columns) > core.WIDE_CSV_COLUMNS:
        from csv_workbench.wide import prepare_wide_result

        result = prepare_wide_result(source, columns, selection, edits, cancel=cancel, fmt=fmt)
        with result.scratch, closing(result.iter_rows(cancel=cancel)) as rows:
            yield rows
        return
    with cancel.connection() as connection:
        sql, params = editable_query(
            connection, source, selection, edits, fmt, cancel=cancel, columns=columns,
        )
        position_name = '__csv_replace_position'
        while position_name.casefold() in {name.casefold() for name in columns}:
            position_name += '_'
        identifier = quote_identifier(position_name)
        conditions = ' OR '.join(
            f'contains(lower({quote_identifier(name)}), lower(?))' for name in visible
        )
        cursor = connection.execute(
            f'WITH numbered AS (SELECT row_number() OVER () - 1 AS {identifier}, * FROM ({sql})) '
            f'SELECT * FROM numbered WHERE {conditions} ORDER BY {identifier}',
            [*params, *([needle] * len(visible))],
        )

        def rows():
            while batch := cursor.fetchmany(1024):
                cancel.check()
                for position, rid, *values in batch:
                    yield position, rid, values

        with closing(rows()) as stream:
            yield stream


def preview_replacement(path, selection, edits, needle, replacement, *, first=False, fmt=None, cancel=None):
    cancel = cancel or Cancellation()
    cancel.check()
    if not needle:
        raise ValueError("Enter nonempty search text.")
    fmt = fmt or CsvFormat()
    scratch = TemporaryDirectory(prefix="csv-replace-")
    preview = []
    cells = occurrences = 0
    try:
        with utf8_source(path, cancel, fmt) as source:
            columns = core.source_columns(source, cancel, fmt)
            visible = [name for name in selection.ordered_columns(columns)
                       if selection.visible_columns is None or name in selection.visible_columns]
            indices = {name: column for column, name in enumerate(columns)}
            with replacement_rows(source, columns, selection, edits, visible, needle,
                                  cancel=cancel, fmt=fmt) as rows, (
                Path(scratch.name) / "changes.jsonl"
            ).open("w", encoding="utf-8") as output:
                for position, row_id, values in rows:
                    cancel.check()
                    done = False
                    for name, before in zip(visible, values, strict=True):
                        if before is None:
                            continue
                        after, count = replace_literal(before, needle, replacement, first=first)
                        if not count:
                            continue
                        occurrences += count
                        if after != before:
                            output.write(json.dumps(
                                [row_id, indices[name], before, after], ensure_ascii=False,
                            ) + "\n")
                            cells += 1
                            if len(preview) < 100:
                                preview.append((position + 1, name, before, after))
                        if first:
                            done = True
                            break
                    if done:
                        break
        cancel.check()
        return ReplacementPlan(scratch, preview, cells, occurrences)
    except BaseException:
        scratch.cleanup()
        raise
