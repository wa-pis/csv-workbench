"""Local history for successfully opened CSV and TSV documents."""

import json
from dataclasses import asdict
from pathlib import Path

from csv_workbench.core import CsvFormat

RECENT_LIMIT = 10
RECENT_KEY = "recentCsvFiles"


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
            except (KeyError, TypeError, ValueError):
                continue
            if path.is_absolute():
                result.setdefault(path, fmt)
                if len(result) == RECENT_LIMIT:
                    break
    return list(result.items())


def remember_file(settings, path, fmt):
    path = path.absolute()
    entries = [(path, fmt), *((old, value) for old, value in recent_files(settings) if old != path)]
    settings.setValue(
        RECENT_KEY,
        json.dumps(
            [{"path": str(file), "format": asdict(value)} for file, value in entries[:RECENT_LIMIT]]
        ),
    )
    settings.sync()
