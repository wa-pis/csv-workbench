"""Named CSV formats stored in the application's local settings."""

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QSettings

from csv_workbench.core import CsvFormat


class FormatPresets:
    def __init__(self, settings, *, exporting=False):
        self.settings = settings
        self.key = "formatPresets/export" if exporting else "formatPresets/open"

    def load(self):
        try:
            data = json.loads(self.settings.value(self.key, "{}", type=str))
        except ValueError:
            return {}
        presets = {}
        if isinstance(data, dict):
            for name, fields in data.items():
                try:
                    presets[name] = CsvFormat(**fields)
                except (TypeError, ValueError):
                    continue
        return presets

    def save(self, name, fmt):
        name = self.valid_name(name)
        presets = self.load()
        presets[name] = fmt
        self._write(presets, self.default_name())

    @staticmethod
    def valid_name(name):
        name = name.strip()
        if not name:
            raise ValueError("Enter a name for the preset.")
        return name

    def default_name(self):
        name = self.settings.value(f"{self.key}Default", "", type=str)
        return name or None

    def rename(self, old, new):
        new = self.valid_name(new)
        presets = self.load()
        if old not in presets:
            raise ValueError("The preset no longer exists.")
        if old == new:
            return new
        if new in presets:
            raise ValueError("A preset with that name already exists.")
        presets[new] = presets.pop(old)
        default = self.default_name()
        self._write(presets, new if default == old else default)
        return new

    def delete(self, name):
        presets = self.load()
        if name not in presets:
            raise ValueError("The preset no longer exists.")
        del presets[name]
        default = self.default_name()
        self._write(presets, None if default == name else default)

    def set_default(self, name):
        presets = self.load()
        if name is not None and name not in presets:
            raise ValueError("The preset no longer exists.")
        self._write(presets, name)

    def _write(self, presets, default):
        self._stage(presets, default)
        sync_settings(self.settings)

    def _stage(self, presets, default):
        self.settings.setValue(
            self.key, json.dumps({label: asdict(value) for label, value in presets.items()})
        )
        self.settings.setValue(f"{self.key}Default", default if default in presets else "")

def sync_settings(settings):
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        raise OSError("Could not save the format presets.")


def preset_stores(settings):
    return {"open": FormatPresets(settings), "export": FormatPresets(settings, exporting=True)}


def export_format_presets(settings, destination: Path):
    document = {"version": 1}
    for scope, store in preset_stores(settings).items():
        presets = store.load()
        default = store.default_name()
        document[scope] = {
            "presets": {name: asdict(fmt) for name, fmt in presets.items()},
            "default": default if default in presets else None,
        }
    with TemporaryDirectory(dir=destination.parent) as scratch:
        output = Path(scratch) / "presets.json"
        output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.replace(destination)


def unique_json_keys(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}.")
        result[key] = value
    return result


def import_format_presets(settings, source: Path, *, confirm_replace=None):
    document = json.loads(source.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_json_keys)
    if not isinstance(document, dict) or type(document.get("version")) is not int or document["version"] != 1:
        raise ValueError("Unsupported preset JSON version.")
    pending = []
    conflicts = {}
    for scope, store in preset_stores(settings).items():
        data = document.get(scope)
        if not isinstance(data, dict) or not isinstance(data.get("presets"), dict) or "default" not in data:
            raise ValueError(f"Invalid preset list: {scope}.")
        incoming = {}
        for name, fields in data["presets"].items():
            try:
                if (
                    not name or name != name.strip() or not isinstance(fields, dict)
                    or fields.keys() != {"delimiter", "header", "encoding", "quote"}
                ):
                    raise ValueError
                incoming[name] = CsvFormat(**fields)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid preset in {scope}: {name!r}.") from exc
        default = data["default"]
        if default is not None and (not isinstance(default, str) or default not in incoming):
            raise ValueError(f"Invalid default preset: {scope}.")
        existing = store.load()
        if names := sorted(existing.keys() & incoming.keys()):
            conflicts[scope] = names
        pending.append((store, existing | incoming, default))
    if conflicts and (confirm_replace is None or not confirm_replace(conflicts)):
        return False
    for store, presets, default in pending:
        store._stage(presets, default)
    sync_settings(settings)
    return True
