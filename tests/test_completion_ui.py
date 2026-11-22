from concurrent.futures import CancelledError
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from csv_workbench.core import Selection
from csv_workbench.ui import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication(['completion-test', '-platform', 'offscreen'])
    monkeypatch.setattr('csv_workbench.ui.QSettings', lambda *_: QSettings(
        str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(MainWindow, 'recovery_root', property(lambda _: tmp_path / 'recovery'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    source = tmp_path / 'input.csv'
    source.write_text('id,note\n' + ''.join(f'{i:04},value{i:04}\n' for i in range(601)))
    grid = MainWindow()
    grid.load_view(source, Selection(), new_file=True)
    settle(grid)
    grid.show()
    grid.activateWindow()
    grid.table.setFocus()
    app.processEvents()
    yield grid
    grid.close()
    grid.executor.shutdown(wait=True)
    grid.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def settle(grid):
    for _ in range(10):
        jobs = [job for job in grid.jobs() if job]
        if not jobs:
            return
        for token, future in jobs:
            try:
                future.result(timeout=5)
            except CancelledError:
                pass
        grid.collect_reads()
    raise AssertionError('jobs did not settle')


def request(grid, prefix):
    grid.edit_current_cell()
    editor = grid.completion_delegate.editor
    assert editor is not None
    editor.setText(prefix)
    grid.complete_action.trigger()
    settle(grid)
    return editor


@pytest.mark.parametrize('pinned', [False, True])
def test_real_completion_popup_accept_confirm_undo_redo(window, pinned):
    if pinned:
        window.load_view(window.path, Selection(pinned_columns=('note',)))
        settle(window)
    window.table.setCurrentIndex(window.model.index(0, 1))
    editor = request(window, 'value06')
    popup = editor.completer().popup()
    assert popup.isVisible() and popup.model().rowCount() == 1
    assert popup.model().index(0, 0).data() == 'value0600'
    assert not window.dirty  # Opening suggestions never edits the document.
    QTest.keyClick(popup, Qt.Key.Key_Down)
    QTest.keyClick(popup, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert editor.text() == 'value0600'
    assert not window.dirty  # Choosing a value is still only editor text.
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    settle(window)
    assert window.model.index(0, 1).data() == 'value0600'
    assert window.history_pos == 1
    window.move_history(-1)
    settle(window)
    assert window.model.index(0, 1).data() == 'value0000'
    window.move_history(1)
    settle(window)
    assert window.model.index(0, 1).data() == 'value0600'


def test_shortcut_and_escape_do_not_commit_and_limit_is_visible(window):
    window.table.setCurrentIndex(window.model.index(0, 1))
    window.edit_current_cell()
    editor = window.completion_delegate.editor
    editor.setText('')
    QTest.keyClick(editor, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
    settle(window)
    popup = editor.completer().popup()
    assert popup.isVisible() and popup.model().rowCount() == 100
    assert 'First 100' in window.statusBar().currentMessage()
    QTest.keyClick(popup, Qt.Key.Key_Escape)
    assert not popup.isVisible() and not window.completion_delegate.active
    assert not window.dirty
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert not window.dirty and window.history_pos == 0


def test_background_request_cancel_prefix_and_stale_response(window, monkeypatch):
    started, release = Event(), Event()

    def delayed(*args, cancel, **kwargs):
        started.set()
        assert release.wait(5)
        cancel.check()
        return ['stale'], False

    monkeypatch.setattr('csv_workbench.ui.complete_values', delayed)
    window.table.setCurrentIndex(window.model.index(0, 1))
    window.edit_current_cell()
    editor = window.completion_delegate.editor
    editor.setText('old')
    window.complete_action.trigger()
    token, future = window.completion_job
    try:
        assert started.wait(5)
        # GUI events continue while the worker is blocked.
        QTest.keyClicks(editor, 'new')
        QApplication.processEvents()
        assert token.cancelled and window.completion_job is None
        assert window.completion_delegate.timer.isActive()
        window.completion_delegate.timer.stop()
        window.completion_delegate.dismiss()
    finally:
        release.set()
    with pytest.raises(CancelledError):
        future.result(timeout=5)
    window.collect_reads()
    assert not editor.completer().popup().isVisible()
    assert editor.text() == 'oldnew' and not window.dirty


def test_close_editor_cancel_global_and_changed_selection_reject_results(window):
    window.table.setCurrentIndex(window.model.index(0, 1))
    editor = request(window, 'value06')
    request_state = (editor, editor.text(), window.completion_state(), window.completion_delegate.cell)
    window.completion_delegate.dismiss()
    window.completion_delegate.active = True
    window.completion_request = request_state
    window.selection = Selection(filters=(('note', 'other'),))
    window.apply_completion((['stale'], False))
    assert not editor.completer().popup().isVisible()
    window.cancel_operations()
    assert not window.completion_delegate.active
    assert window.completion_job is None
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert window.completion_delegate.editor is None


def test_debounce_restarts_search_and_closed_editor_cannot_receive_old_results(window):
    window.table.setCurrentIndex(window.model.index(0, 1))
    editor = request(window, 'value0')
    old_request = (editor, editor.text(), window.completion_state(), window.completion_delegate.cell)
    editor.selectAll()
    QTest.keyClicks(editor, 'value06')
    QTest.qWait(220)
    settle(window)
    assert editor.completer().popup().model().rowCount() == 1
    assert editor.completer().popup().model().index(0, 0).data() == 'value0600'
    window.completion_delegate.dismiss()
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    QApplication.processEvents()
    window.table.setCurrentIndex(window.model.index(0, 0))
    window.edit_current_cell()
    other = window.completion_delegate.editor
    window.completion_delegate.active = True
    window.completion_request = old_request
    window.apply_completion((['stale'], False))
    assert other is not editor and other.text() == '0000'
    assert not other.completer().popup().isVisible()


def test_queued_completion_can_be_cancelled_without_running(window):
    release = Event()
    blockers = [window.executor.submit(release.wait, 5) for _ in range(2)]
    try:
        window.table.setCurrentIndex(window.model.index(0, 1))
        window.edit_current_cell()
        window.complete_action.trigger()
        token, future = window.completion_job
        assert not future.running()
        window.cancel_operations()
        assert token.cancelled and future.cancelled()
        assert window.completion_job is None
    finally:
        release.set()
        for future in blockers:
            future.result(timeout=5)


@pytest.mark.parametrize('pinned', [False, True])
@pytest.mark.parametrize('cancel_from', ['escape', 'button'])
def test_active_search_cancellation_keeps_editor_and_rejects_late_result(
        window, monkeypatch, pinned, cancel_from):
    started, release = Event(), Event()

    def delayed(*args, cancel, **kwargs):
        started.set()
        assert release.wait(5)
        # Simulate a response which races with cancellation rather than cooperates.
        return ['late-value'], False

    monkeypatch.setattr('csv_workbench.ui.complete_values', delayed)
    if pinned:
        window.table.set_pins(('note',))
    window.table.setCurrentIndex(window.model.index(0, 1))
    window.edit_current_cell()
    editor = window.completion_delegate.editor
    editor.setText('value')
    window.complete_action.trigger()
    token, future = window.completion_job
    try:
        assert started.wait(5)
        if cancel_from == 'escape':
            QTest.keyClick(editor, Qt.Key.Key_Escape)
        else:
            window.cancel_button.click()
        assert token.cancelled and window.completion_job is None
        assert not window.completion_delegate.active
        assert editor.text() == 'value' and not window.dirty
    finally:
        release.set()
    future.result(timeout=5)
    window.collect_reads()
    QApplication.processEvents()
    assert not editor.completer().popup().isVisible()
    assert window.history_pos == 0
