"""Local history for successfully opened CSV and TSV documents."""

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from csv_workbench.core import CsvFormat

RECENT_LIMIT = 10
RECENT_KEY = "recentCsvFiles"


@dataclass(frozen=True)
class RecentFile:
    path: Path
    format: CsvFormat
    pinned: bool = False


def _ordered(entries):
    pinned = [entry for entry in entries if entry.pinned]
    recent = [entry for entry in entries if not entry.pinned]
    return pinned + recent[:RECENT_LIMIT]


def recent_files(settings):
    try:
        entries = json.loads(settings.value(RECENT_KEY, "[]", type=str))
    except ValueError:
        return []
    result = {}
    if isinstance(entries, list):
        for entry in entries:
            try:
                path = Path(entry["path"])
                fmt = CsvFormat(**entry["format"])
                pinned = entry.get("pinned", False)
            except (KeyError, TypeError, ValueError):
                continue
            if path.is_absolute() and isinstance(pinned, bool):
                result.setdefault(path, RecentFile(path, fmt, pinned))
    return _ordered(result.values())


def _save(settings, entries):
    settings.setValue(
        RECENT_KEY,
        json.dumps(
            [
                {"path": str(entry.path), "format": asdict(entry.format), "pinned": entry.pinned}
                for entry in _ordered(entries)
            ]
        ),
    )
    settings.sync()


def remember_file(settings, path, fmt):
    path = path.absolute()
    entries = recent_files(settings)
    for index, entry in enumerate(entries):
        if entry.path == path:
            if entry.pinned:
                entries[index] = replace(entry, format=fmt)
                break
            entries.pop(index)
            entries.insert(0, RecentFile(path, fmt))
            break
    else:
        entries.insert(0, RecentFile(path, fmt))
    _save(settings, entries)


def set_file_pinned(settings, path, pinned):
    entries = recent_files(settings)
    for index, entry in enumerate(entries):
        if entry.path == path and entry.pinned != pinned:
            entries.pop(index)
            _save(settings, [replace(entry, pinned=pinned), *entries])
            return


def remove_recent_file(settings, path):
    _save(settings, [entry for entry in recent_files(settings) if entry.path != path])


def clear_recent_files(settings):
    _save(settings, [entry for entry in recent_files(settings) if entry.pinned])
