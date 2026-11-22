"""Save and restore visual selection settings."""

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from csv_workbench.core import CsvFormat, Selection
from csv_workbench.validation import parse_rules


def session_tab(source, selection, fmt):
    return {"source": str(source.resolve()), "selection": asdict(selection), "format": asdict(fmt)}


def write_session(destination, data, sources):
    if destination.suffix.lower() != ".csvsession":
        raise ValueError("Choose a .csvsession file.")
    resolved = destination.resolve()
    for source in sources:
        if resolved == source.resolve() or (destination.exists() and destination.samefile(source)):
            raise ValueError("Choose a destination other than the source CSV.")
    with TemporaryDirectory(dir=destination.parent) as scratch:
        output = Path(scratch) / "session"
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        output.replace(destination)


def save_session(destination: Path, source: Path, selection: Selection, fmt=None) -> None:
    write_session(
        destination, {"version": 1, **session_tab(source, selection, fmt or CsvFormat())}, [source]
    )


def save_workspace_session(destination, tabs, active):
    if type(active) is not int or not 0 <= active < len(tabs):
        raise ValueError("Invalid active tab in workspace session.")
    write_session(
        destination,
        {"version": 2, "active": active, "tabs": [session_tab(*tab) for tab in tabs]},
        [tab[0] for tab in tabs],
    )


def session_data(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        raise ValueError("Unsupported session format.")
    return data


def load_session(path: Path) -> tuple[Path, Selection, CsvFormat]:
    data = session_data(path)
    if data["version"] != 1:
        raise ValueError("Unsupported session format.")
    return parse_session_tab(data)


def load_workspace_session(path):
    data = session_data(path)
    if data["version"] == 1:
        return [parse_session_tab(data)], 0
    if data["version"] != 2:
        raise ValueError("Unsupported session format.")
    tabs, active = data.get("tabs"), data.get("active")
    if not isinstance(tabs, list) or type(active) is not int or not 0 <= active < len(tabs):
        raise ValueError("Invalid tabs or active tab in workspace session.")
    parsed = [parse_session_tab(tab) for tab in tabs]
    if len({tab[0].resolve() for tab in parsed}) != len(parsed):
        raise ValueError("Workspace session contains duplicate files.")
    return parsed, active


def parse_session_tab(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid tab in workspace session.")  # noqa: TRY004
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
    column_order = selection.get("column_order", [])
    pinned = selection.get("pinned_columns", [])
    exact_filters = selection.get("exact_filters", [])
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
        or not isinstance(column_order, list)
        or not all(isinstance(name, str) for name in column_order)
        or len(set(column_order)) != len(column_order)
        or not isinstance(pinned, list)
        or not all(isinstance(name, str) for name in pinned)
        or len(set(pinned)) != len(pinned)
        or not isinstance(exact_filters, list)
        or any(
            not isinstance(rule, list)
            or len(rule) != 3
            or not isinstance(rule[0], str)
            or rule[1] not in ("equals", "not_equals")
            or (rule[2] is not None and not isinstance(rule[2], str))
            for rule in exact_filters
        )
    ):
        raise ValueError("Invalid session selection.")
    settings = data.get("format", {})
    if not isinstance(settings, dict):
        raise ValueError("Invalid format in session file.")  # noqa: TRY004
    fmt = CsvFormat(
        settings.get("delimiter", ","),
        settings.get("header", True),
        settings.get("encoding"),
        settings.get("quote", '"'),
    )
    return (
        Path(source),
        Selection(
            tuple((column, value) for column, value in filters),
            sort,
            descending,
            tuple(columns) if columns is not None else None,
            match_any,
            tuple((column, direction) for column, direction in extra),
            tuple(column_order),
            tuple(tuple(rule) for rule in exact_filters),
            tuple(pinned),
            parse_rules(selection.get("column_types", [])),
        ),
        fmt,
    )
