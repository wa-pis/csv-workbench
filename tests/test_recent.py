import json

from PySide6.QtCore import QSettings

from csv_workbench.core import CsvFormat
from csv_workbench.recent import RECENT_KEY, RECENT_LIMIT, recent_files, remember_file


def test_recent_history_is_bounded_and_reopen_updates_format(tmp_path):
    settings_path = str(tmp_path / "settings.ini")
    settings = QSettings(settings_path, QSettings.Format.IniFormat)
    paths = [tmp_path / f"report-{i}.tsv" for i in range(RECENT_LIMIT + 1)]
    for path in paths:
        remember_file(settings, path, CsvFormat("\t"))
    assert [path for path, _ in recent_files(settings)] == paths[:0:-1]

    fmt = CsvFormat("|", False, "cp1251", "'")
    remember_file(settings, paths[1], fmt)
    reopened = QSettings(settings_path, QSettings.Format.IniFormat)
    assert recent_files(reopened) == [
        (paths[1], fmt),
        *((path, CsvFormat("\t")) for path in paths[:1:-1]),
    ]


def test_recent_history_skips_invalid_entries(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    path = tmp_path / "good.csv"
    settings.setValue(
        RECENT_KEY,
        json.dumps(
            [
                {"path": "relative.csv", "format": {}},
                {"path": str(path), "format": {"delimiter": "||"}},
                {"path": str(path), "format": {"delimiter": ";"}},
            ]
        ),
    )
    assert recent_files(settings) == [(path, CsvFormat(";"))]
