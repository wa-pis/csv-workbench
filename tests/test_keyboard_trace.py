import json

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcutEvent
from PySide6.QtWidgets import QApplication, QLineEdit

from csv_workbench.keyboard_trace import KeyboardTrace, trace_keyboard


@pytest.fixture
def trace(tmp_path):
    app = QApplication.instance() or QApplication(['trace-test', '-platform', 'offscreen'])
    path = tmp_path / 'keyboard.jsonl'
    trace = KeyboardTrace(app, path)
    app.keyboard_trace = trace
    yield app, trace, path
    del app.keyboard_trace
    trace.close()


def test_key_focus_and_shortcut_trace_never_records_text(trace):
    _app, recorder, path = trace
    editor = QLineEdit()
    editor.setText('private CSV value')
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F10,
                      Qt.KeyboardModifier.ShiftModifier, 'private typed text')
    assert recorder.eventFilter(editor, event) is False
    recorder.eventFilter(editor, QShortcutEvent(QKeySequence('Shift+F10'), 7, True))
    recorder.focus_changed(None, editor)
    trace_keyboard('cell_menu_requested', enabled=True)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    key = next(row for row in rows if row['event'] == 'KeyPress')
    assert key['key'] == Qt.Key.Key_F10
    assert key['modifiers'] == Qt.KeyboardModifier.ShiftModifier.value
    assert key['receiver'] == 'QLineEdit'
    assert next(row for row in rows if row['event'] == 'Shortcut')['ambiguous'] is True
    assert any(row['event'] == 'focus' and row['current'] == 'QLineEdit' for row in rows)
    assert rows[-1]['event'] == 'cell_menu_requested'
    assert 'private' not in path.read_text()


def test_trace_rotates_and_keeps_only_one_backup(trace):
    _, recorder, path = trace
    recorder.handler.maxBytes = 300
    for _ in range(30):
        recorder.record('cell_menu_opening')
    assert path.stat().st_size <= 300
    assert path.with_suffix('.jsonl.1').stat().st_size <= 300
    assert not path.with_suffix('.jsonl.2').exists()


def test_disabled_trace_creates_no_file(tmp_path):
    QApplication.instance() or QApplication(['trace-test', '-platform', 'offscreen'])
    trace_keyboard('cell_menu_opening')
    assert list(tmp_path.iterdir()) == []


def test_invalid_trace_path_fails_before_installing_filter(tmp_path):
    app = QApplication.instance() or QApplication(['trace-test', '-platform', 'offscreen'])
    with pytest.raises(OSError):
        KeyboardTrace(app, tmp_path / 'missing' / 'keyboard.jsonl')
    assert getattr(app, 'keyboard_trace', None) is None
