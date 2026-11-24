"""Native Cocoa regression probe for QTBUG-149612, run in an isolated process."""

import ctypes
import json
from time import monotonic

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QLineEdit, QTableView

from csv_workbench.accessibility import MAX_CELLS
from csv_workbench.ui import CsvTable, PageModel


def check_tables():
    # Call Cocoa on our own test widgets; no external app or AX permission is needed.
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p

    def send(obj, name, result=ctypes.c_void_p, *args):
        signature = ctypes.CFUNCTYPE(
            result, ctypes.c_void_p, ctypes.c_void_p, *([ctypes.c_void_p] * len(args)))
        return signature(('objc_msgSend', objc))(
            obj, objc.sel_registerName(name.encode()), *args)

    def children(obj, name):
        array = send(obj, name)
        return [send(array, 'objectAtIndex:', ctypes.c_void_p, i)
                for i in range(send(array, 'count', ctypes.c_ulong))] if array else []

    def tables(obj, depth=0):
        if depth > 12:
            return []
        role = send(obj, 'accessibilityRole')
        if role and send(role, 'UTF8String', ctypes.c_char_p) == b'AXTable':
            return [obj]
        return [table for child in children(obj, 'accessibilityChildren')
                for table in tables(child, depth + 1)]

    app = QApplication.instance()
    if app.platformName() != 'cocoa':
        raise RuntimeError('Accessibility regression requires native Cocoa, not offscreen')
    for pinned in (False, True):
        model = PageModel()
        model.replace(['a', 'b'], [('one', 'two')] * 4)
        views = [QTableView() for _ in range(2 if pinned else 1)]
        for view in views:
            view.setModel(model)
            view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
            view.show()
        app.processEvents()
        ids = [QAccessible.uniqueId(QAccessible.queryAccessibleInterface(view)) for view in views]
        for count in (2, 3, 2) * 5:
            model.replace(['a', 'b', 'c'][:count], [tuple('abc'[:count])] * 4)
            app.processEvents()
            for view, ident in zip(views, ids, strict=True):
                native = tables(int(view.winId()))
                assert native, 'native table missing'
                for element in native:
                    # Materialize placeholder cells before querying selected real cells.
                    for row in children(element, 'accessibilityRows'):
                        children(row, 'accessibilityChildren')
                    for row in range(4):
                        view.selectRow(row)
                        app.processEvents()
                        selected = children(element, 'accessibilitySelectedChildren')
                        assert QAccessible.accessibleInterface(ident) is not None, (
                            'QTBUG-149612: parent table interface deleted')
                        assert selected, 'native selected cells missing'
        for view in views:
            view.close()
            view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    # Reproduce the actual wide, fully selected hierarchy through Cocoa. Python-only
    # childCount checks cannot detect native synthetic-table expansion.
    model = PageModel()
    model.editable = True
    model.replace([f'n{i}' for i in range(2000)], [('1',) * 2000] * 200)
    table = CsvTable(model)
    table.resize(900, 500)
    table.set_pins(('n0',))
    table.show()
    table.selectAll()
    app.processEvents()

    def walk(obj, depth=0, roles=None):
        assert depth < 20, 'accessible hierarchy cycle'
        if roles is not None:
            role = send(obj, 'accessibilityRole')
            roles.append(send(role, 'UTF8String', ctypes.c_char_p) if role else None)
        return 1 + sum(walk(child, depth + 1, roles)
                       for child in children(obj, 'accessibilityChildren'))

    counts = []
    started = monotonic()
    for position in (0, 90000, 180000, 0):
        table.horizontalScrollBar().setValue(position)
        model.setData(model.index(0, 0), 'edited')
        app.processEvents()
        counts.append(walk(int(table.winId())))
        assert 10 < counts[-1] < MAX_CELLS * 4, 'unbounded or missing native hierarchy'
        for view in (table, table.pinned):
            iface = QAccessible.queryAccessibleInterface(view)
            assert iface.role() == QAccessible.Role.Grouping
            assert iface.childCount() <= MAX_CELLS + 5
        table.setCurrentIndex(model.index(0, 0))
        table.edit(model.index(0, 0))
        app.processEvents()
        assert any(editor.isVisible() for editor in table.findChildren(QLineEdit))
        roles = []
        assert walk(int(table.winId()), roles=roles) < MAX_CELLS * 4
        assert b'AXTextField' in roles, 'native cell editor missing'
        table.closePersistentEditor(model.index(0, 0))
        table.setFocus()
    elapsed = monotonic() - started
    assert elapsed < 10, 'native viewport traversal blocked UI'
    print(json.dumps({'wide_cocoa_seconds': elapsed, 'native_node_counts': counts}))
    model.edits.clear()
    model.replace(['a', 'b'], [('new', 'value')])
    app.processEvents()
    assert walk(int(table.winId())) < MAX_CELLS * 4
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == 'new'
    table.close()
    table.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    return 0
