"""Save and restore visual selection settings."""

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from csv_workbench.core import CsvFormat, Selection


def save_session(destination: Path, source: Path, selection: Selection, fmt=None) -> None:
    source = source.resolve()
    if destination.suffix.lower() != ".csvsession":
        raise ValueError("Choose a .csvsession file.")
    if destination.resolve() == source or (destination.exists() and destination.samefile(source)):
        raise ValueError("Choose a destination other than the source CSV.")
    data = {
        "version": 1,
        "source": str(source),
        "selection": asdict(selection),
        "format": asdict(fmt or CsvFormat()),
    }
    with TemporaryDirectory(dir=destination.parent) as scratch:
        output = Path(scratch) / "session"
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        output.replace(destination)


def load_session(path: Path) -> tuple[Path, Selection, CsvFormat]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Unsupported session format.")
    source = data.get("source")
    if not isinstance(source, str) or not Path(source).is_absolute():
        raise ValueError("Session requires an absolute CSV path.")
    selection = data.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("Invalid session selection in file.")  # noqa: TRY004
    filters = selection.get("filters")
    columns = selection.get("visible_columns")
    sort = selection.get("sort_column")
    extra = selection.get("extra_sort", [])
    descending = selection.get("descending")
    match_any = selection.get("match_any")
    if (
        not isinstance(filters, list)
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(item, str) for item in pair)
            for pair in filters
        )
        or (
            columns is not None
            and (
                not isinstance(columns, list)
                or not columns
                or not all(isinstance(column, str) for column in columns)
            )
        )
        or (sort is not None and not isinstance(sort, str))
        or not isinstance(extra, list)
        or any(
            not isinstance(key, list)
            or len(key) != 2
            or not isinstance(key[0], str)
            or not isinstance(key[1], bool)
            for key in extra
        )
        or (extra and sort is None)
        or len({key[0] for key in extra} | {sort}) != len(extra) + 1
        or not isinstance(descending, bool)
        or not isinstance(match_any, bool)
    ):
        raise ValueError("Invalid session selection.")
    settings = data.get("format", {})
    if not isinstance(settings, dict):
        raise ValueError("Invalid format in session file.")  # noqa: TRY004
    fmt = CsvFormat(settings.get("delimiter", ","), settings.get("header", True))
    return (
        Path(source),
        Selection(
            tuple((column, value) for column, value in filters),
            sort,
            descending,
            tuple(columns) if columns is not None else None,
            match_any,
            tuple((column, direction) for column, direction in extra),
        ),
        fmt,
    )
