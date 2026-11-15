import json

from PySide6.QtCore import QSettings

from csv_workbench.core import CsvFormat
from csv_workbench.recent import (
    RECENT_KEY,
    RECENT_LIMIT,
    RecentFile,
    clear_recent_files,
    recent_files,
    remember_file,
    remove_recent_file,
    set_file_pinned,
)


def test_recent_history_is_bounded_and_reopen_updates_format(tmp_path):
    settings_path = str(tmp_path / "settings.ini")
    settings = QSettings(settings_path, QSettings.Format.IniFormat)
    paths = [tmp_path / f"report-{i}.tsv" for i in range(RECENT_LIMIT + 1)]
    for path in paths:
        remember_file(settings, path, CsvFormat("\t"))
    assert [entry.path for entry in recent_files(settings)] == paths[:0:-1]

    fmt = CsvFormat("|", False, "cp1251", "'")
    remember_file(settings, paths[1], fmt)
    reopened = QSettings(settings_path, QSettings.Format.IniFormat)
    assert recent_files(reopened) == [
        RecentFile(paths[1], fmt),
        *(RecentFile(path, CsvFormat("\t")) for path in paths[:1:-1]),
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
    assert recent_files(settings) == [RecentFile(path, CsvFormat(";"))]


def test_pins_survive_history_limit_and_format_updates(tmp_path):
    settings_path = str(tmp_path / "settings.ini")
    settings = QSettings(settings_path, QSettings.Format.IniFormat)
    first, second = tmp_path / "first.csv", tmp_path / "second.tsv"
    remember_file(settings, first, CsvFormat())
    set_file_pinned(settings, first, True)
    remember_file(settings, second, CsvFormat("\t"))
    set_file_pinned(settings, second, True)
    for i in range(RECENT_LIMIT + 1):
        remember_file(settings, tmp_path / f"{i}.csv", CsvFormat())
    updated = CsvFormat(";", False, "cp1251", "'")
    remember_file(settings, first, updated)
    reopened = QSettings(settings_path, QSettings.Format.IniFormat)
    entries = recent_files(reopened)
    assert entries[:2] == [
        RecentFile(second, CsvFormat("\t"), True),
        RecentFile(first, updated, True),
    ]
    assert len(entries) == RECENT_LIMIT + 2

    set_file_pinned(reopened, second, False)
    assert recent_files(reopened)[:2] == [
        RecentFile(first, updated, True),
        RecentFile(second, CsvFormat("\t")),
    ]
    assert len(recent_files(reopened)) == RECENT_LIMIT + 1


def test_clear_and_remove_change_only_history(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    pinned, removed, ordinary = (tmp_path / name for name in ("pin.csv", "remove.csv", "other.csv"))
    for path in (pinned, removed, ordinary):
        path.write_text("id\n1\n", encoding="utf-8")
        remember_file(settings, path, CsvFormat())
    set_file_pinned(settings, pinned, True)
    set_file_pinned(settings, removed, True)
    remove_recent_file(settings, removed)
    assert [entry.path for entry in recent_files(settings)] == [pinned, ordinary]
    clear_recent_files(settings)
    assert recent_files(settings) == [RecentFile(pinned, CsvFormat(), True)]
    assert all(path.is_file() for path in (pinned, removed, ordinary))
