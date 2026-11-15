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
        name = name.strip()
        if not name:
            raise ValueError("Enter a name for the preset.")
        presets = self.load()
        presets[name] = fmt
        self.settings.setValue(
            self.key, json.dumps({label: asdict(value) for label, value in presets.items()})
        )
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("Could not save the format preset.")
