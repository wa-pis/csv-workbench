import json

import pytest
from PySide6.QtCore import QSettings

from csv_workbench.core import CsvFormat
from csv_workbench.presets import FormatPresets, export_format_presets, import_format_presets


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


def test_json_transfer_both_lists_and_defaults(tmp_path):
    source = QSettings(str(tmp_path / "source.ini"), QSettings.Format.IniFormat)
    target = QSettings(str(tmp_path / "target.ini"), QSettings.Format.IniFormat)
    name = "Отчёт / Азия"
    opened = CsvFormat("|", False, "cp1251", "'")
    exported = CsvFormat("\t", True, "utf-16", "^")
    for store, fmt in (
        (FormatPresets(source), opened),
        (FormatPresets(source, exporting=True), exported),
    ):
        store.save(name, fmt)
        store.set_default(name)
    target_open = FormatPresets(target)
    target_export = FormatPresets(target, exporting=True)
    target_open.save("Local only", CsvFormat(";"))
    output = tmp_path / "presets.json"
    export_format_presets(source, output)
    assert name in output.read_text(encoding="utf-8")
    assert import_format_presets(target, output)
    assert target_open.load() == {name: opened, "Local only": CsvFormat(";")}
    assert target_export.load() == {name: exported}
    assert target_open.default_name() == target_export.default_name() == name


def test_json_conflicts_require_confirmation_before_any_change(tmp_path):
    source = QSettings(str(tmp_path / "source.ini"), QSettings.Format.IniFormat)
    target = QSettings(str(tmp_path / "target.ini"), QSettings.Format.IniFormat)
    target_open = FormatPresets(target)
    target_export = FormatPresets(target, exporting=True)
    target_open.save("Report", CsvFormat())
    target_open.set_default("Report")
    FormatPresets(source).save("Report", CsvFormat("|"))
    FormatPresets(source, exporting=True).save("Report", CsvFormat("\t"))
    output = tmp_path / "presets.json"
    export_format_presets(source, output)

    assert import_format_presets(target, output) is False
    asked = []

    def decline(conflicts):
        asked.append(conflicts)
        return False

    assert import_format_presets(target, output, confirm_replace=decline) is False
    assert asked == [{"open": ["Report"]}]
    assert target_open.load() == {"Report": CsvFormat()}
    assert target_open.default_name() == "Report"
    assert target_export.load() == {}

    assert import_format_presets(target, output, confirm_replace=lambda _: True)
    assert target_open.load() == {"Report": CsvFormat("|")}
    assert target_export.load() == {"Report": CsvFormat("\t")}
    assert target_open.default_name() is None


@pytest.mark.parametrize("invalid", ["version", "default", "format", "duplicate"])
def test_invalid_json_never_changes_either_list(tmp_path, invalid):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    local = FormatPresets(settings)
    local.save("Local", CsvFormat())
    local.set_default("Local")
    output = tmp_path / "presets.json"
    section = {
        "presets": {"New": {"delimiter": "|", "header": False, "encoding": None, "quote": "'"}},
        "default": "New",
    }
    document = {"version": 1, "open": section, "export": {"presets": {}, "default": None}}
    if invalid == "version":
        document["version"] = True
    if invalid == "default":
        document["export"]["default"] = "Missing"
    if invalid == "format":
        document["export"]["presets"]["Invalid"] = {**section["presets"]["New"], "header": 1}
    payload = json.dumps(document)
    if invalid == "duplicate":
        payload = payload.replace('"version": 1', '"version": 1, "version": 1')
    output.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        import_format_presets(settings, output)
    assert local.load() == {"Local": CsvFormat()}
    assert local.default_name() == "Local"
    assert FormatPresets(settings, exporting=True).load() == {}
