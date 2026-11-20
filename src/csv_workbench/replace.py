"""Prepare replacements on disk without changing the document."""

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from csv_workbench.core import (
    Cancellation,
    CsvFormat,
    csv_columns,
    csv_pattern,
    editable_query,
    quote_identifier,
    search_lower,
    utf8_source,
)


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

    def changes(self, *, cancel):
        # shortcut: patches and Undo scale with changed cells; move history to disk for massive replacements.
        changes = []
        with (Path(self.scratch.name) / "changes.jsonl").open(encoding="utf-8") as source:
            for line in source:
                cancel.check()
                row, column, before, after = json.loads(line)
                changes.append(((row, column), before, after))
        cancel.check()
        return changes


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
        with utf8_source(path, cancel, fmt) as source, cancel.connection() as connection:
            columns = csv_columns(connection, csv_pattern(source), fmt)
            visible = [name for name in selection.ordered_columns(columns)
                       if selection.visible_columns is None or name in selection.visible_columns]
            sql, params = editable_query(connection, source, selection, edits, fmt)
            position_name = "__csv_replace_position"
            while position_name.casefold() in {name.casefold() for name in columns}:
                position_name += "_"
            identifier = quote_identifier(position_name)
            conditions = " OR ".join(
                f"contains(lower({quote_identifier(name)}), lower(?))" for name in visible
            )
            cursor = connection.execute(
                f"WITH numbered AS (SELECT row_number() OVER () - 1 AS {identifier}, * FROM ({sql})) "
                f"SELECT * FROM numbered WHERE {conditions} ORDER BY {identifier}",
                [*params, *([needle] * len(visible))],
            )
            with (Path(scratch.name) / "changes.jsonl").open("w", encoding="utf-8") as output:
                done = False
                while not done and (batch := cursor.fetchmany(1024)):
                    cancel.check()
                    for position, row_id, *values in batch:
                        cancel.check()
                        for name, before in zip(visible, values, strict=True):
                            if before is None:
                                continue
                            after, count = replace_literal(before, needle, replacement, first=first)
                            if not count:
                                continue
                            occurrences += count
                            if after != before:
                                output.write(json.dumps(
                                    [row_id, columns.index(name), before, after or None],
                                    ensure_ascii=False,
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
