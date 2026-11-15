import json

import pytest
from PySide6.QtCore import QSettings

from csv_workbench.core import CsvFormat
from csv_workbench.presets import FormatPresets


def test_presets_round_trip_and_keep_open_and_export_separate(tmp_path):
    path = str(tmp_path / "settings.ini")
    opened = CsvFormat("|", False, "cp1251", "'")
    exported = CsvFormat("\t", True, "utf-16", "^")
    settings = QSettings(path, QSettings.Format.IniFormat)
    FormatPresets(settings).save("  Отчёт / Азия  ", opened)
    FormatPresets(settings, exporting=True).save("Отчёт / Азия", exported)

    reopened = QSettings(path, QSettings.Format.IniFormat)
    assert FormatPresets(reopened).load() == {"Отчёт / Азия": opened}
    assert FormatPresets(reopened, exporting=True).load() == {"Отчёт / Азия": exported}
    FormatPresets(reopened).save("Отчёт / Азия", CsvFormat())
    assert FormatPresets(settings).load() == {"Отчёт / Азия": CsvFormat()}
    assert FormatPresets(settings, exporting=True).load() == {"Отчёт / Азия": exported}


def test_presets_skip_invalid_entries_and_reject_blank_names(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store = FormatPresets(settings)
    settings.setValue(
        store.key, json.dumps({"OK": {"delimiter": ";"}, "wrong": {"quote": ","}, "bad": 42})
    )
    with pytest.raises(ValueError, match="name"):
        store.save("  ", CsvFormat())
    assert store.load() == {"OK": CsvFormat(";")}
    settings.setValue(store.key, "{")
    assert store.load() == {}


def test_defaults_follow_rename_and_delete_only_in_their_scope(tmp_path):
    path = str(tmp_path / "settings.ini")
    settings = QSettings(path, QSettings.Format.IniFormat)
    opening = FormatPresets(settings)
    exporting = FormatPresets(settings, exporting=True)
    opening.save("Report", CsvFormat("|", False))
    opening.save("Other", CsvFormat(";"))
    exporting.save("Report", CsvFormat("\t", False))
    assert opening.default_name() is None
    opening.set_default("Report")
    exporting.set_default("Report")

    with pytest.raises(ValueError, match="already exists"):
        opening.rename("Report", "Other")
    with pytest.raises(ValueError, match="name"):
        opening.rename("Report", "  ")
    assert opening.rename("Report", "  Renamed  ") == "Renamed"
    opening.delete("Other")

    reopened = QSettings(path, QSettings.Format.IniFormat)
    opening = FormatPresets(reopened)
    exporting = FormatPresets(reopened, exporting=True)
    assert opening.load() == {"Renamed": CsvFormat("|", False)}
    assert opening.default_name() == "Renamed"
    assert exporting.default_name() == "Report"
    with pytest.raises(ValueError, match="no longer exists"):
        opening.set_default("Report")
    assert opening.default_name() == "Renamed"
    opening.delete("Renamed")
    assert opening.load() == {}
    assert opening.default_name() is None
    assert exporting.default_name() == "Report"
    exporting.set_default(None)
    assert exporting.default_name() is None
    assert exporting.load() == {"Report": CsvFormat("\t", False)}
