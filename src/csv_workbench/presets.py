"""Named CSV formats stored in the application's local settings."""

import json
from dataclasses import asdict

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
        self.settings.setValue(
            self.key, json.dumps({label: asdict(value) for label, value in presets.items()})
        )
        self.settings.setValue(f"{self.key}Default", default if default in presets else "")
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("Could not save the format preset.")
