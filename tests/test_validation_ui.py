from concurrent.futures import CancelledError
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from csv_workbench.core import Selection
from csv_workbench.ui import MainWindow
from csv_workbench.validation import ValidationResult


def settle(grid):
    for _ in range(10):
        jobs = [job for job in grid.jobs() if job]
        if not jobs:
            return
        for _, future in jobs:
            try:
                future.result(timeout=5)
            except CancelledError:
                pass
        grid.collect_reads()
    raise AssertionError('jobs did not settle')


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication(['validation-test', '-platform', 'offscreen'])
    monkeypatch.setattr('csv_workbench.ui.QSettings', lambda *_: QSettings(
        str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(MainWindow, 'recovery_root', property(lambda _: tmp_path / 'recovery'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    source = tmp_path / 'input.csv'
    source.write_text('id,value\n' + ''.join(f'{i},{i}\n' for i in range(601)) + '601,bad\n')
    grid = MainWindow()
    grid.load_view(source, Selection(), new_file=True)
    settle(grid)
    yield grid
    grid.close()
    grid.executor.shutdown(wait=True)
    grid.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_full_background_check_and_immediate_highlight_undo(window):
    window.set_column_types((('value', 'integer', False),))
    window.start_validation()
    settle(window)
    assert window.validation_result == ValidationResult(602, (('value', 1),))
    index = window.model.index(0, 1)
    assert window.model.setData(index, 'oops')
    settle(window)
    assert window.validation_result is None
    assert index.data(Qt.ItemDataRole.BackgroundRole) is not None
    assert 'Expected integer' in index.data(Qt.ItemDataRole.ToolTipRole)
    window.start_validation()
    settle(window)
    assert window.validation_result.cells == 2
    window.move_history(-1)
    settle(window)
    assert index.data(Qt.ItemDataRole.BackgroundRole) is None
    assert window.validation_result is None


def test_null_empty_edits_and_undo(window):
    index = window.model.index(0, 1)
    window.table.setCurrentIndex(index)
    window.set_column_types((('value', 'text', False),))
    window.clear_cells()
    settle(window)
    assert window.model.cell_value(index) is None
    assert index.data() == 'NULL'
    assert 'missing' in index.data(Qt.ItemDataRole.ToolTipRole)
    assert index.data(Qt.ItemDataRole.BackgroundRole) is not None
    window.set_empty_cells()
    settle(window)
    assert window.model.cell_value(index) == ''
    assert index.data() == '""'
    assert index.data(Qt.ItemDataRole.BackgroundRole) is None
    window.move_history(-1)
    settle(window)
    assert window.model.cell_value(index) is None
    window.move_history(1)
    settle(window)
    assert window.model.cell_value(index) == ''


@pytest.mark.parametrize('invalidate', ['cancel', 'edit', 'rules', 'view'])
def test_cancel_or_invalidate_discards_late_result(window, monkeypatch, invalidate):
    entered, release = Event(), Event()

    def delayed(*args, cancel, **kwargs):
        entered.set()
        assert release.wait(5)
        # A worker that returns despite cancellation must never publish stale data.
        return ValidationResult(999, (('value', 999),))

    monkeypatch.setattr('csv_workbench.ui.validate_csv_values', delayed)
    window.set_column_types((('value', 'integer', False),))
    window.start_validation()
    token, future = window.validation_job
    assert entered.wait(5)
    try:
        if invalidate == 'cancel':
            window.cancel_button.click()
        elif invalidate == 'edit':
            window.model.setData(window.model.index(0, 1), 'bad')
        elif invalidate == 'rules':
            window.set_column_types((('value', 'number', True),))
        else:
            window.load_view(window.path, Selection(column_types=window.selection.column_types,
                                                   filters=(('id', '1'),)))
        with pytest.raises(CancelledError):
            token.check()
    finally:
        release.set()
    try:
        future.result(timeout=5)
    except CancelledError:
        pass
    settle(window)
    assert window.validation_result is None
    if invalidate == 'cancel':
        assert window.validation_note == 'Validation cancelled'
