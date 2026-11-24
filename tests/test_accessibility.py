"""Viewport bounds and real cell semantics, independently of Cocoa permissions."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QLineEdit

from csv_workbench.accessibility import (
    MAX_CELLS,
    CellAccessible,
    ViewAccessible,
    factory,
    visible_cells,
)
from csv_workbench.ui import CsvTable, PageModel


@pytest.fixture
def grid():
    app = QApplication.instance() or QApplication([])
    # Explicit installation also exercises the implementation on Linux/Windows CI.
    QAccessible.installFactory(factory)
    model = PageModel()
    model.editable = True
    model.replace([f'n{i}' for i in range(2000)], [('1',) * 2000] * 200)
    view = CsvTable(model)
    view.resize(900, 500)
    view.show()
    app.processEvents()
    yield app, view, model
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    # Factory is intentionally kept for the process, just as on macOS.


def test_wide_selection_does_not_enumerate_selected_or_offscreen_cells(grid, monkeypatch):
    _, view, model = grid
    iface = QAccessible.queryAccessibleInterface(view)
    assert isinstance(iface, ViewAccessible)
    monkeypatch.setattr(view.selectionModel(), 'selectedIndexes',
                        lambda: pytest.fail('expanded the entire selected buffer'))
    view.selectAll()
    assert '400000 selected cells' in iface.text(QAccessible.Text.Name)
    keys = visible_cells(view)
    assert 0 < len(keys) < MAX_CELLS
    cells = [iface.child(i) for i in range(iface.childCount())]
    assert len(iface.cells) == len(keys)
    assert all(cell.state().selected for cell in cells if isinstance(cell, CellAccessible))
    assert iface.child(-1) is None and iface.child(iface.childCount()) is None
    assert iface.child(2002) == iface.child(0)  # Qt's legacy first-cell notification
    assert model.rowCount() * model.columnCount() == 400000


def test_scroll_hidden_and_reordered_columns_bound_cache(grid):
    app, view, _ = grid
    iface = QAccessible.queryAccessibleInterface(view)
    view.horizontalHeader().moveSection(1999, 0)
    view.setColumnHidden(0, True)
    first = visible_cells(view)
    assert first[0] == (0, 1999)
    assert all(col != 0 for _, col in first)
    iface.childCount()
    old_tokens = list(iface.cells.values())
    for position in range(0, view.horizontalScrollBar().maximum(), 20000):
        view.horizontalScrollBar().setValue(position)
        view.verticalScrollBar().setValue(1000)
        app.processEvents()
        iface.childCount()
        assert set(iface.cells) == set(visible_cells(view))
        assert len(iface.cells) <= MAX_CELLS
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any(token in iface.cells.values() for token in old_tokens)


def test_cell_actions_edited_values_null_empty_and_model_reset(grid):
    app, view, model = grid
    iface = QAccessible.queryAccessibleInterface(view)
    cell = iface.child(0)
    assert cell.text(QAccessible.Text.Name) == 'Row 1, n0: 1'
    cell.doAction(cell.pressAction())
    assert view.currentIndex() == model.index(0, 0)
    assert cell.state().selected and cell.state().focused
    assert iface.focusChild() == cell
    assert iface.indexOfChild(cell) == 0
    assert iface.childAt(cell.rect().center().x(), cell.rect().center().y()) == cell
    model.setData(model.index(0, 0), '42')
    assert cell.text(QAccessible.Text.Value) == '42'
    model.setData(model.index(0, 0), None)
    assert cell.text(QAccessible.Text.Value) == 'NULL'
    model.setData(model.index(0, 0), '')
    assert cell.text(QAccessible.Text.Value) == '""'
    model.edits.clear()
    model.replace(['new'], [('replacement',)])
    assert not cell.isValid()
    cell.doAction(cell.pressAction())  # stale AX reference cannot edit a different cell
    assert cell.text(QAccessible.Text.Value) == ''
    app.processEvents()
    assert iface.child(0).text(QAccessible.Text.Name) == 'Row 1, new: replacement'


def test_pinned_shared_selection_and_native_editor_remain_reachable(grid):
    app, view, model = grid
    view.set_pins(('n0',))
    app.processEvents()
    root = QAccessible.queryAccessibleInterface(view)
    pinned = QAccessible.queryAccessibleInterface(view.pinned)
    assert pinned in root.children()
    cell = pinned.child(0)
    cell.doAction(cell.pressAction())
    assert view.currentIndex() == view.pinned.currentIndex() == model.index(0, 0)
    assert view.selectionModel().isSelected(model.index(0, 0))
    assert root.focusChild() == cell
    assert not any(col == 0 for _, col in root.cells)
    view.pinned.edit(model.index(0, 0))
    app.processEvents()
    editor = view.pinned.findChild(QLineEdit)
    assert editor is not None and editor.isVisible()
    assert QAccessible.queryAccessibleInterface(editor) in pinned.children()
    editor.setText('7.5')
    view.pinned.commitData(editor)
    assert cell.text(QAccessible.Text.Value) == '7.5'
    view.pinned.setColumnHidden(0, True)
    app.processEvents()
    assert visible_cells(view.pinned) == []


@pytest.mark.parametrize('pinned', [False, True])
def test_cell_focus_follows_keyboard_owner_and_native_editor(grid, pinned):
    app, view, model = grid
    view.set_pins(('n0',) if pinned else ())
    view.activateWindow()
    app.processEvents()
    root = QAccessible.queryAccessibleInterface(view)
    target = view.pinned if pinned else view
    cell = QAccessible.queryAccessibleInterface(target).child(0)
    cell.doAction(cell.pressAction())
    app.processEvents()
    assert view.hasFocus() and cell.state().focused
    assert root.focusChild() == cell

    target.edit(model.index(0, 0))
    app.processEvents()
    editor = target.findChild(QLineEdit)
    assert editor is not None and editor.hasFocus()
    assert not cell.state().focused
    assert root.focusChild() == QAccessible.queryAccessibleInterface(editor)

    toolbar = QLineEdit()
    try:
        toolbar.show()
        toolbar.activateWindow()
        toolbar.setFocus()
        app.processEvents()
        assert toolbar.hasFocus() and not view.hasFocus()
        assert not cell.state().focused
        assert root.focusChild() is None
    finally:
        toolbar.close()
        toolbar.deleteLater()
