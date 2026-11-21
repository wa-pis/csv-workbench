"""Opt-in keyboard routing trace; never records text or document values."""

import json
import logging
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication


class KeyboardTrace(QObject):
    def __init__(self, app, path):
        super().__init__(app)
        self.app = app
        self.log = logging.getLogger('csv-workbench-keyboard')
        self.log.propagate = False
        self.handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=1,
                                          encoding='utf-8')
        self.log.addHandler(self.handler)
        app.installEventFilter(self)
        app.focusChanged.connect(self.focus_changed)
        self.record('started', platform=app.platformName())

    def record(self, event, **fields):
        focus = self.app.focusWidget()
        self.log.warning(json.dumps(dict(time_ns=time.monotonic_ns(), event=event,
                                             focus=type(focus).__name__, **fields)))

    def focus_changed(self, old, new):
        self.record('focus', previous=type(old).__name__, current=type(new).__name__)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            self.record(kind.name, receiver=type(watched).__name__, key=event.key(),
                        modifiers=event.modifiers().value,
                        native_virtual_key=event.nativeVirtualKey(),
                        native_scan_code=event.nativeScanCode(),
                        native_modifiers=event.nativeModifiers(), repeat=event.isAutoRepeat())
        elif kind == QEvent.Type.Shortcut:
            self.record('Shortcut', receiver=type(watched).__name__,
                        shortcut=event.key().toString(), ambiguous=event.isAmbiguous())
        elif kind == QEvent.Type.ApplicationStateChange:
            self.record('application_state', state=self.app.applicationState().name)
        return False

    def close(self):
        if getattr(self.app, 'keyboard_trace', None) is self:
            self.app.keyboard_trace = None
        self.app.removeEventFilter(self)
        self.app.focusChanged.disconnect(self.focus_changed)
        self.handler.close()
        self.log.removeHandler(self.handler)


def trace_keyboard(event, **fields):
    trace = getattr(QApplication.instance(), 'keyboard_trace', None)
    if trace is not None:
        trace.record(event, **fields)
