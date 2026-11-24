"""Bound Cocoa hierarchy enumeration to the CSV viewport, never the whole buffer."""

import sys
from itertools import islice

from PySide6.QtCore import QPersistentModelIndex, QPoint, QRect, Qt
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import QAccessibleWidget, QLineEdit, QTableView, QWidget

MAX_CELLS = 512
_installed = False


def visible_sections(header, extent, start=0):
    result = []
    point = start
    while point < extent:
        section = header.logicalIndexAt(point)
        if section < 0:
            break
        result.append(section)
        point = max(point + 1, header.sectionViewportPosition(section) + header.sectionSize(section))
    return result


def visible_cells(view):
    if not view.isVisible():
        return []
    pinned = getattr(view, 'pinned', None)
    start = pinned.width() if pinned is not None and pinned.isVisible() else 0
    rows = visible_sections(view.verticalHeader(), view.viewport().height())
    columns = visible_sections(view.horizontalHeader(), view.viewport().width(), start)
    return list(islice(((row, col) for row in rows for col in columns), MAX_CELLS))


class CellWidget(QWidget):
    """Invisible ownership token; its accessible geometry comes from the real cell."""

    def __init__(self, view, index):
        super().__init__(view)
        self.view = view
        self.index = QPersistentModelIndex(index)
        self.hide()


class CellAccessible(QAccessibleWidget):
    def __init__(self, widget):
        super().__init__(widget, QAccessible.Role.Cell)

    def isValid(self):
        return super().isValid() and self.widget().index.isValid()

    def parent(self):
        return QAccessible.queryAccessibleInterface(self.widget().view)

    def childCount(self):
        return 0

    def child(self, index):
        return None

    def rect(self):
        if not self.isValid():
            return QRect()
        cell = self.widget()
        bounds = cell.view.visualRect(cell.index).intersected(cell.view.viewport().rect())
        return QRect(cell.view.viewport().mapToGlobal(bounds.topLeft()), bounds.size())

    def state(self):
        state = QAccessible.State()
        state.invalid = not self.isValid()
        if not state.invalid:
            cell = self.widget()
            state.selectable = state.focusable = True
            state.selected = cell.view.selectionModel().isSelected(cell.index)
            owner = cell.view.parentWidget()
            focus_view = owner if isinstance(owner, AccessibleTableView) else cell.view
            state.focused = focus_view.hasFocus() and cell.view.currentIndex() == cell.index
            state.editable = bool(cell.index.flags() & Qt.ItemFlag.ItemIsEditable)
            state.invisible = not cell.view.isVisible() or self.rect().isEmpty()
        return state

    def text(self, kind):
        if not self.isValid():
            return ''
        cell = self.widget()
        if kind == QAccessible.Text.Value:
            return str(cell.index.data(Qt.ItemDataRole.DisplayRole) or '')
        if kind == QAccessible.Text.Name:
            model = cell.view.model()
            row = getattr(model, 'offset', 0) + cell.index.row() + 1
            column = model.headerData(cell.index.column(), Qt.Orientation.Horizontal)
            return f'Row {row}, {column}: {self.text(QAccessible.Text.Value)}'
        return ''

    def actionNames(self):
        return [self.pressAction(), self.setFocusAction()]

    def doAction(self, action):
        if not self.isValid() or action not in self.actionNames():
            return
        cell = self.widget()
        cell.view.setCurrentIndex(cell.index)
        # The pinned overlay intentionally has NoFocus; the main view owns keyboard navigation.
        owner = cell.view.parentWidget()
        (owner if isinstance(owner, AccessibleTableView) else cell.view).setFocus()


class ViewAccessible(QAccessibleWidget):
    def __init__(self, view):
        super().__init__(view, QAccessible.Role.Grouping)
        self.cells = {}

    def children(self):
        view = self.widget()
        keys = visible_cells(view)
        wanted = set(keys)
        for key, token in list(self.cells.items()):
            if key not in wanted or not token.index.isValid():
                token.deleteLater()
                del self.cells[key]
        for key in keys:
            if key not in self.cells:
                self.cells[key] = CellWidget(view, view.model().index(*key))
        children = [QAccessible.queryAccessibleInterface(self.cells[key]) for key in keys]
        # Do not recurse into the standard Qt viewport/header table hierarchy.
        widgets = [editor for editor in view.findChildren(QLineEdit) if editor.isVisible()]
        widgets += [bar for bar in (view.horizontalScrollBar(), view.verticalScrollBar())
                    if bar.isVisible()]
        pinned = getattr(view, 'pinned', None)
        if pinned is not None and pinned.isVisible():
            widgets.append(pinned)
        return children + [QAccessible.queryAccessibleInterface(widget) for widget in widgets]

    def childCount(self):
        return len(self.children())

    def child(self, index):
        children = self.children()
        if 0 <= index < len(children):
            return children[index]
        # Qt item-view notifications still use its flattened table child numbering.
        # Only resolve an out-of-viewport-range notification if its cell is visible.
        width = self.widget().model().columnCount() + 1
        row, column = divmod(index, width)
        key = (row - 1, column - 1)
        token = self.cells.get(key) if index >= 0 else None
        pinned = getattr(self.widget(), 'pinned', None)
        if token is None and pinned is not None and pinned.isVisible():
            iface = QAccessible.queryAccessibleInterface(pinned)
            iface.children()
            token = iface.cells.get(key)
        return QAccessible.queryAccessibleInterface(token) if token is not None else None

    def indexOfChild(self, child):
        return next((i for i, item in enumerate(self.children()) if item == child), -1)

    def childAt(self, x, y):
        return next((child for child in reversed(self.children())
                     if child.rect().contains(QPoint(x, y))), None)

    def focusChild(self):
        children = self.children()
        pinned = getattr(self.widget(), 'pinned', None)
        if pinned is not None and pinned.isVisible():
            child = QAccessible.queryAccessibleInterface(pinned).focusChild()
            if child is not None:
                return child
        return next((child for child in reversed(children) if child.state().focused), None)

    def text(self, kind):
        if kind in (QAccessible.Text.Name, QAccessible.Text.Description):
            view = self.widget()
            model = view.model()
            selected = sum(r.width() * r.height() for r in view.selectionModel().selection())
            return (f'CSV visible cells; {model.rowCount()} buffered rows, '
                    f'{model.columnCount()} columns; {selected} selected cells. '
                    'Use arrow keys to navigate and Enter to edit.')
        return super().text(kind)


def factory(key, obj):
    if isinstance(obj, CellWidget):
        return CellAccessible(obj)
    if isinstance(obj, AccessibleTableView):
        return ViewAccessible(obj)
    return None


class AccessibleTableView(QTableView):
    def __init__(self, *args):
        global _installed
        if sys.platform == 'darwin' and not _installed:
            QAccessible.installFactory(factory)
            _installed = True
        super().__init__(*args)
        self.horizontalScrollBar().valueChanged.connect(self.notify_viewport)
        self.verticalScrollBar().valueChanged.connect(self.notify_viewport)

    def setSelectionModel(self, selection):
        super().setSelectionModel(selection)
        selection.currentChanged.connect(self.notify_focus)
        selection.selectionChanged.connect(self.notify_viewport)

    def notify_focus(self, *_):
        if sys.platform == 'darwin':
            iface = QAccessible.queryAccessibleInterface(self)
            child = iface.focusChild()
            if child is not None:
                QAccessible.updateAccessibility(QAccessibleEvent(child.object(), QAccessible.Event.Focus))

    def notify_viewport(self, *_):
        if sys.platform == 'darwin':
            QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.ObjectReorder))
