import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from csv_workbench.core import Selection
from csv_workbench.ui import BUFFER_ROWS, MainWindow


@pytest.fixture
def grid(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication(['grid-test', '-platform', 'offscreen'])
    monkeypatch.setattr('csv_workbench.ui.QSettings', lambda *_: QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(MainWindow, 'recovery_root', property(lambda _: tmp_path / 'recovery'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    source = tmp_path / 'input.csv'
    source.write_text('id,note,tag\n' + ''.join(f'{i:04},note{i},yes\n' for i in range(601)))
    window = MainWindow()
    window.resize(800, 500)
    window.load_view(source, Selection(), new_file=True)
    finish(window)
    window.show()
    window.activateWindow()
    window.table.setFocus()
    app.processEvents()
    yield window
    window.close()
    window.executor.shutdown(wait=True)
    window.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def finish(window):
    for _ in range(10):
        jobs = [job for job in (window.view_job, window.page_job) if job]
        if not jobs:
            return
        for _, future in jobs:
            future.result(timeout=5)
        window.collect_reads()
    raise AssertionError('navigation did not settle')


def test_keyboard_navigation_crosses_buffer_and_respects_visual_order(grid):
    table = grid.table
    # Direct cursor calls test the same path Qt uses for keys and delegate Tab traversal.
    grid.adjusting = True
    table.setCurrentIndex(grid.model.index(199, 2))
    grid.adjusting = False
    table.moveCursor(table.CursorAction.MoveNext, Qt.KeyboardModifier.NoModifier)
    finish(grid)
    assert grid.model.offset + table.currentIndex().row() == 200
    assert table.currentIndex().column() == 0
    grid.adjusting = True
    table.setCurrentIndex(grid.model.index(0, 0))
    grid.adjusting = False
    if grid.model.offset:
        table.moveCursor(table.CursorAction.MoveUp, Qt.KeyboardModifier.NoModifier)
        finish(grid)
    grid.go_to_edge(True)
    finish(grid)
    assert grid.model.offset + table.currentIndex().row() == 600
    table.moveCursor(table.CursorAction.MoveNext, Qt.KeyboardModifier.NoModifier)
    finish(grid)
    assert grid.model.offset + table.currentIndex().row() == 600
    assert len(grid.model.rows) <= BUFFER_ROWS


def test_keyboard_up_and_page_down_clamp_at_file_boundaries(grid):
    grid.go_to_row(401, column=1)
    finish(grid)
    grid.adjusting = True
    grid.table.setCurrentIndex(grid.model.index(0, 1))
    first = grid.model.offset
    grid.adjusting = False
    grid.table.moveCursor(grid.table.CursorAction.MoveUp, Qt.KeyboardModifier.NoModifier)
    finish(grid)
    assert grid.model.offset + grid.table.currentIndex().row() == first - 1
    assert grid.table.currentIndex().column() == 1
    grid.go_to_edge(True)
    finish(grid)
    QTest.keyClick(grid.table, Qt.Key.Key_PageDown)
    finish(grid)
    assert grid.model.offset + grid.table.currentIndex().row() == 600


def test_go_to_visible_column_and_row_rejects_bad_addresses(grid, monkeypatch):
    grid.table.horizontalHeader().moveSection(0, 2)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_: ('2:501', True))
    grid.prompt_for_row()
    finish(grid)
    assert grid.model.offset + grid.table.currentIndex().row() == 500
    assert grid.table.currentIndex().column() == 2
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: errors.append(True))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_: ('4:10', True))
    grid.prompt_for_row()
    assert errors == [True]


def test_extend_shrink_selection_restores_original_range(grid):
    from PySide6.QtCore import QItemSelection, QItemSelectionModel

    table = grid.table
    table.setCurrentIndex(grid.model.index(3, 1))
    original = QItemSelection(grid.model.index(2, 0), grid.model.index(3, 1))
    table.selectionModel().select(original, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    table.extend_selection()
    assert table.selection_summary() == (grid.model.rowCount(), grid.model.rowCount(), 1)
    table.extend_selection()
    assert table.selection_summary() == (3, 1, 3)
    table.extend_selection()
    assert table.selection_summary() == (grid.model.rowCount() * 3, grid.model.rowCount(), 3)
    assert 'buffered' in grid.selection_status.text()
    for _ in range(3):
        table.shrink_selection()
    assert table.selection_summary() == (4, 2, 2)
    assert table.selectionModel().selection() == original
    table.select_scope('row')
    assert table.selection_summary() == (3, 1, 3)
    assert not table.selection_steps


def test_selection_summary_handles_disjoint_ranges_without_materializing_cells(grid, monkeypatch):
    from PySide6.QtCore import QItemSelection, QItemSelectionModel

    flags = QItemSelectionModel.SelectionFlag
    selection = grid.table.selectionModel()
    selection.select(QItemSelection(grid.model.index(0, 0), grid.model.index(2, 0)), flags.ClearAndSelect)
    selection.select(QItemSelection(grid.model.index(1, 2), grid.model.index(3, 2)), flags.Select)
    monkeypatch.setattr(grid.table, 'selectedIndexes', lambda: pytest.fail('materialized cells'))
    assert grid.table.selection_summary() == (6, 4, 2)
    grid.update_edit_actions()
    assert '6 cells; 4 rows; 2 columns' in grid.selection_status.text()


def test_shift_navigation_does_not_discard_selection_at_buffer_edge(grid):
    grid.adjusting = True
    grid.table.setCurrentIndex(grid.model.index(199, 1))
    grid.adjusting = False
    before = grid.table.selectionModel().selection()
    grid.table.moveCursor(grid.table.CursorAction.MoveDown, Qt.KeyboardModifier.ShiftModifier)
    assert grid.table.selectionModel().selection() == before
    assert grid.keyboard_target is None


@pytest.mark.parametrize('key', [Qt.Key.Key_Return, Qt.Key.Key_F2])
def test_enter_and_f2_edit_confirm_once_and_escape_cancels(grid, key):
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QLineEdit

    grid.table.setCurrentIndex(grid.model.index(0, 1))
    QTest.keyClick(grid.table, key)
    editor = grid.table.findChild(QLineEdit)
    assert editor is not None and editor.isVisible()
    editor.setText('changed')
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert grid.history_pos == 0 and grid.model.index(0, 1).data() == 'note0'
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QTest.keyClick(grid.table, key)
    editor = next(w for w in grid.table.findChildren(QLineEdit) if w.isVisible())
    editor.setText('changed')
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    finish(grid)
    assert grid.history_pos == 1 and grid.model.index(0, 1).data() == 'changed'
    grid.undo_action.trigger()
    finish(grid)
    assert grid.model.index(0, 1).data() == 'note0'
    grid.table.setFocus()
    QTest.keyClick(grid.table, key)
    editor = next(w for w in grid.table.findChildren(QLineEdit) if w.isVisible())
    editor.setFocus()
    QTest.keySequence(editor, QKeySequence(QKeySequence.StandardKey.SelectAll))
    assert editor.selectedText() == 'note0'
    QTest.keyClick(editor, Qt.Key.Key_Escape)


def test_cell_editor_is_disabled_for_read_only_and_busy_views(grid):
    from PySide6.QtWidgets import QLineEdit

    grid.table.setCurrentIndex(grid.model.index(0, 1))
    grid.distinct = 'note'
    grid.update_status()
    assert not grid.edit_action.isEnabled() and not grid.value_action.isEnabled()
    grid.edit_current_cell()
    assert not any(w.isVisible() for w in grid.table.findChildren(QLineEdit))
    grid.distinct = None
    grid.load_view(grid.path, Selection(sort_column='id', descending=True))
    assert not grid.edit_action.isEnabled()
    finish(grid)
    grid.table.setCurrentIndex(grid.model.index(0, 1))
    assert grid.edit_action.isEnabled()


def test_context_menu_preserves_range_inside_and_selects_outside(grid, monkeypatch):
    from PySide6.QtCore import QItemSelection, QItemSelectionModel
    from PySide6.QtWidgets import QMenu

    original = QItemSelection(grid.model.index(0, 0), grid.model.index(1, 1))
    grid.table.selectionModel().select(original, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    observed = []

    class InspectMenu(QMenu):
        def exec(self, point):
            actions = self.actions()
            assert grid.copy_action in actions and grid.edit_action in actions
            assert grid.value_action in actions and grid.undo_action in actions
            observed.append(grid.table.selection_summary())
            grid.copy_action.trigger()
            return grid.copy_action

    monkeypatch.setattr('csv_workbench.ui.QMenu', InspectMenu)
    grid.cell_filter_menu(grid.table.visualRect(grid.model.index(1, 1)).center())
    assert grid.table.selectionModel().selection() == original
    assert QApplication.clipboard().text().splitlines() == ['0000\tnote0', '0001\tnote1']
    grid.cell_filter_menu(grid.table.visualRect(grid.model.index(3, 1)).center())
    assert observed == [(4, 2, 2), (1, 1, 1)]
    assert QApplication.clipboard().text().strip() == 'note3'


@pytest.mark.parametrize('key,modifiers', [(Qt.Key.Key_Menu, Qt.KeyboardModifier.NoModifier),
                                         (Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)])
def test_keyboard_context_menu_and_read_only_actions(grid, monkeypatch, key, modifiers):
    from PySide6.QtWidgets import QMenu

    calls = []
    grid.table.setCurrentIndex(grid.model.index(2, 1))
    grid.distinct = 'note'
    grid.update_status()

    class InspectMenu(QMenu):
        def exec(self, point):
            assert not grid.edit_action.isEnabled()
            assert not grid.paste_action.isEnabled()
            assert grid.copy_action.isEnabled()
            assert grid.table.currentIndex().data() == 'note2'
            calls.append(True)

    monkeypatch.setattr('csv_workbench.ui.QMenu', InspectMenu)
    QTest.keyClick(grid.table, key, modifiers)
    assert calls == [True]


def test_pinned_cell_edits_use_visible_editor_and_one_undo(grid):
    from PySide6.QtWidgets import QLineEdit

    grid.table.set_pins(('note',))
    grid.table.setCurrentIndex(grid.model.index(0, 1))
    grid.edit_current_cell()
    editor = next(w for w in grid.table.pinned.findChildren(QLineEdit) if w.isVisible())
    editor.setText('pinned edit')
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    finish(grid)
    assert grid.history_pos == 1 and grid.model.edits[(0, 1)] == 'pinned edit'


def test_inline_edit_in_filtered_sorted_view_has_one_undo(grid):
    from PySide6.QtWidgets import QLineEdit

    grid.load_view(grid.path, Selection((('tag', 'yes'),), 'id', True))
    finish(grid)
    grid.table.setCurrentIndex(grid.model.index(0, 2))
    grid.edit_current_cell()
    editor = next(w for w in grid.table.findChildren(QLineEdit) if w.isVisible())
    editor.setText('no')
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    finish(grid)
    assert grid.history_pos == 1 and grid.model.edits[(600, 2)] == 'no'
    assert grid.model.rows[0][0] == '0599'
    grid.undo_action.trigger()
    finish(grid)
    assert grid.model.rows[0][0] == '0600'
