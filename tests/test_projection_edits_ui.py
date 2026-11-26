"""Derived results must never reinterpret source patch coordinates."""

from concurrent.futures import CancelledError
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from csv_workbench import core, ui
from csv_workbench.core import Selection
from csv_workbench.ui import MainWindow, PageModel


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(['projection-test', '-platform', 'offscreen'])


@pytest.mark.parametrize(('headers', 'row'), [
    (['Value', 'Frequency'], ('000002', 1)),
    (['n0', 'calculated'], ('sql-result', '')),
    (['Status', 'Before', 'After'], ('changed', None, 'comparison-result')),
], ids=['unique', 'sql', 'comparison'])
def test_read_only_projection_uses_result_values_despite_retained_source_edits(app, headers, row):
    model = PageModel()
    model.edits[(0, 0)] = 'zz-ёж'
    model.edits[(0, 1)] = None
    snapshot = model.edits.copy()
    model.replace(headers, [row])
    model.find_text = 'zz'

    for column, value in enumerate(row):
        index = model.index(0, column)
        assert model.cell_value(index) == value
        assert index.data() == ('NULL' if value is None else ('""' if value == '' else str(value)))
        assert index.data(Qt.ItemDataRole.EditRole) == ('' if value is None else str(value))
        assert 'zz-ёж' not in index.data(Qt.ItemDataRole.ToolTipRole)
        assert index.data(Qt.ItemDataRole.BackgroundRole) is None
        assert not model.setData(index, 'blocked')
    assert model.edits == snapshot


def test_source_overlay_remains_visible_when_editing_is_temporarily_disabled(app):
    model = PageModel()
    model.source_view = True
    model.replace(['n0'], [('saved',)])
    model.edits[(0, 0)] = 'unsaved'
    model.editable = False

    assert model.index(0, 0).data() == 'unsaved'
    assert not model.setData(model.index(0, 0), 'blocked')


def settle(window):
    for _ in range(12):
        jobs = [job for job in window.jobs() if job]
        if not jobs:
            return
        for _, future in jobs:
            try:
                future.result(timeout=5)
            except CancelledError:
                pass
        window.collect_reads()
    raise AssertionError('jobs did not settle')


@pytest.mark.parametrize('wide', [False, True], ids=['narrow', 'wide'])
def test_unique_after_reordered_edit_and_save_as_preserves_source_history(app, tmp_path, monkeypatch, wide):
    if wide:
        monkeypatch.setattr(core, 'WIDE_CSV_COLUMNS', 3)
        monkeypatch.setattr(ui, 'WIDE_CSV_COLUMNS', 3)
    monkeypatch.setattr(ui, 'QSettings', lambda *_: QSettings(
        str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(MainWindow, 'recovery_root', property(lambda _: tmp_path / 'recovery'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    source = tmp_path / 'source.csv'
    source.write_text('n0,n1,n2,n3\n' + ''.join(f'{row:06},x,x,x\n' for row in range(1, 7)))
    saved = tmp_path / 'saved.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(saved), ''))
    window = MainWindow()
    try:
        window.load_view(source, Selection(), new_file=True)
        settle(window)
        assert window.model.setData(window.model.index(0, 0), 'a-ёж')
        settle(window)
        assert window.model.setData(window.model.index(5, 0), 'z-ёж')
        settle(window)
        selection = Selection(filters=(('n0', 'ЁЖ'),), sort_column='n0', descending=True)
        window.load_view(source, selection)
        settle(window)
        assert window.model.row_ids == [5, 0]
        assert window.model.setData(window.model.index(1, 0), 'zz-ёж')
        settle(window)
        assert window.model.row_ids == [0, 5]
        assert [window.model.index(row, 0).data() for row in range(2)] == ['zz-ёж', 'z-ёж']
        window.load_view(source, replace(selection, visible_columns=('n0', 'n2', 'n3')))
        settle(window)
        window.save_document(as_copy=True)
        settle(window)
        assert window.path == saved and not window.dirty
        assert window.history_pos == window.clean_pos == 3
        window.load_view(saved, Selection())
        settle(window)
        assert window.model.row_ids == list(range(6))
        snapshot = window.model.edits.copy()
        source_snapshot = window.source_snapshot

        window.load_view(saved, Selection(), distinct='n0')
        settle(window)
        assert window.model.rows[0] == ('000002', 1)
        assert window.model.index(0, 0).data() == '000002'
        assert window.model.index(0, 1).data() == '1'
        assert window.model.edits == snapshot
        assert window.source_snapshot is source_snapshot
        assert window.history_pos == window.clean_pos == len(window.history) == 3
        assert not window.model.editable

        window.load_view(saved, Selection())
        settle(window)
        assert window.model.index(0, 0).data() == 'zz-ёж'
        window.move_history(-1)
        settle(window)
        assert window.model.index(0, 0).data() == 'a-ёж' and window.dirty
        window.move_history(1)
        settle(window)
        assert window.model.index(0, 0).data() == 'zz-ёж' and not window.dirty
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
