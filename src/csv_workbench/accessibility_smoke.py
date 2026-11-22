"""Native Cocoa regression probe for QTBUG-149612, run in an isolated process."""

import ctypes

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QTableView

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
        table = CsvTable(model)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.show()
        app.processEvents()
        views = (table, table.pinned) if pinned else (table,)
        if pinned:
            table.set_pins(('a',))
        ids = [QAccessible.uniqueId(QAccessible.queryAccessibleInterface(view)) for view in views]
        for count in (2, 3, 2) * 5:
            model.replace(['a', 'b', 'c'][:count], [tuple('abc'[:count])] * 4)
            table.set_pins(('a',) if pinned else ())
            app.processEvents()
            for view, ident in zip(views, ids, strict=True):
                native = tables(int(view.winId()))
                assert native, 'native table missing'
                for element in native:
                    # Materialize placeholder cells before querying selected real cells.
                    for row in children(element, 'accessibilityRows'):
                        children(row, 'accessibilityChildren')
                    for row in range(4):
                        table.selectRow(row)
                        app.processEvents()
                        selected = children(element, 'accessibilitySelectedChildren')
                        assert QAccessible.accessibleInterface(ident) is not None, (
                            'QTBUG-149612: parent table interface deleted')
                        assert selected, 'native selected cells missing'
        table.close()
        table.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    return 0
