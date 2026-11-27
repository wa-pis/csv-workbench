"""Native Cocoa regression probe for QTBUG-149612, run in an isolated process."""

import csv
import ctypes
import json
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QLineEdit, QTableView

from csv_workbench import core
from csv_workbench.accessibility import MAX_CELLS
from csv_workbench.core import Cancellation, Selection, prepare_result
from csv_workbench.ui import CsvTable, PageModel

_started = monotonic()


def phase(name, **details):
    """Flush one JSON record so a stalled native call leaves its last phase visible."""
    print(json.dumps({'accessibility_phase': name,
                      'elapsed_seconds': monotonic() - _started, **details}), flush=True)


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

    def supported(obj, name):
        return send(obj, 'respondsToSelector:', ctypes.c_bool,
                    objc.sel_registerName(name.encode()))

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
        phase('legacy-start', pinned=pinned)
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
        phase('legacy-end', pinned=pinned)
    # Reproduce the actual wide, fully selected hierarchy through Cocoa. Python-only
    # childCount checks cannot detect native synthetic-table expansion.
    class CountingModel(PageModel):
        flags_calls = 0

        def flags(self, index):
            self.flags_calls += 1
            return super().flags(index)

    phase('wide-setup-start')
    model = CountingModel()
    model.editable = True
    model.replace([f'n{i}' for i in range(2000)], [('1',) * 2000] * 200)
    table = CsvTable(model)
    table.resize(900, 500)
    table.set_pins(('n0',))
    table.show()

    def selection_change(name, action):
        phase('selection-start', action=name)
        assert QAccessible.isActive(), 'selection regression requires active native accessibility'
        model.flags_calls = 0
        selection_started = monotonic()
        action()
        phase('selection-end', action=name,
              selection_seconds=monotonic() - selection_started,
              model_flags_calls=model.flags_calls)
        assert model.flags_calls < MAX_CELLS * 4, 'selection enumerated the full CSV buffer'

    selection_change('select-all', table.selectAll)
    assert sum(r.width() * r.height() for r in table.selectionModel().selection()) == 400000
    app.processEvents()
    phase('wide-setup-end')

    def walk(obj, depth=0, roles=None):
        assert depth < 20, 'accessible hierarchy cycle'
        for attribute in ('accessibilityFocusedUIElement', 'accessibilityTitle',
                          'accessibilityValue', 'accessibilityParent'):
            if supported(obj, attribute):
                send(obj, attribute)
        for attribute in ('isAccessibilityFocused', 'isAccessibilityEnabled'):
            if supported(obj, attribute):
                send(obj, attribute, ctypes.c_bool)
        if roles is not None:
            role = send(obj, 'accessibilityRole')
            roles.append(send(role, 'UTF8String', ctypes.c_char_p) if role else None)
        return 1 + sum(walk(child, depth + 1, roles)
                       for child in children(obj, 'accessibilityChildren'))

    walk_seconds = []

    def timed_walk(*, roles=None):
        window = int(table.winId())
        # Editor setup and event dispatch are separate from native hierarchy traversal.
        phase('native-walk-start', walk=len(walk_seconds) + 1)
        walk_started = monotonic()
        count = walk(window, roles=roles)
        elapsed = monotonic() - walk_started
        walk_seconds.append(elapsed)
        phase('native-walk-end', native_walk_seconds=elapsed, native_walk_nodes=count)
        assert elapsed < 10, 'native viewport traversal blocked UI'
        return count

    counts = []
    started = monotonic()
    # An external AX hierarchy query also asks for native focus/value/state while
    # Python is scanning a CSV. Keep a real bounded workload active until cancel.
    phase('fixture-start')
    with TemporaryDirectory(prefix='csv-accessibility-') as directory:
        source = Path(directory) / 'wide.csv'
        with source.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow([f'n{i}' for i in range(4000)])
            row = ['value' * 20] * 4000
            for index in range(128):
                writer.writerow([str(index), *row[1:]])
        phase('fixture-end')
        cancellation = Cancellation()
        worker_started = Event()
        operations = []
        original_rows = core.iter_csv_rows

        def observed_rows(*args, **kwargs):
            with closing(original_rows(*args, **kwargs)) as rows:
                for row in rows:
                    worker_started.set()
                    yield row

        def busy_worker():
            try:
                while True:
                    cancellation.check()
                    result = prepare_result(source, Selection(), distinct='n0', cancel=cancellation)
                    try:
                        assert result.page(0, limit=200)[1] == [(str(i), 1) for i in sorted(
                            range(128), key=str)]
                        operations.append(True)
                    finally:
                        result.scratch.cleanup()
            except CancelledError:
                return

        core.iter_csv_rows = observed_rows
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                phase('worker-start')
                worker = executor.submit(busy_worker)
                try:
                    assert worker_started.wait(10), 'CSV worker did not enter its real scan'
                    phase('worker-scan-ready')
                    table.activateWindow()
                    QApplication.setActiveWindow(table)
                    for step, (column, editing) in enumerate(((1, False), (0, False), (1, True))):
                        phase('busy-focus-events-start', column=column, editing=editing)
                        index = model.index(0, column)
                        if step == 0:
                            selection_change('busy-current-index',
                                             lambda index=index: table.setCurrentIndex(index))
                        else:
                            table.setCurrentIndex(index)
                        table.setFocus()
                        if editing:
                            table.edit(index)
                        app.processEvents()
                        if editing:
                            editor = QApplication.focusWidget()
                            assert isinstance(editor, QLineEdit) and table.isAncestorOf(editor)
                        else:
                            assert table.hasFocus(), 'native busy query needs real keyboard focus'
                        phase('busy-focus-events-end', busy_native_column=column,
                              busy_native_editor=editing)
                        assert not worker.done(), 'CSV workload ended before native query'
                        counts.append(timed_walk())
                        assert not worker.done(), 'CSV workload ended during native query'
                        assert 10 < counts[-1] < MAX_CELLS * 4
                    table.closePersistentEditor(model.index(0, 1))
                    table.setFocus()
                finally:
                    phase('worker-cancel-start')
                    cancellation.cancel()
                worker.result(timeout=10)
                phase('worker-cancel-end', busy_native_worker_operations=len(operations),
                      busy_native_cancelled=cancellation.cancelled)
        finally:
            core.iter_csv_rows = original_rows
        phase('fixture-cleanup-start')
    phase('fixture-cleanup-end')
    for position in (0, 90000, 180000, 0):
        table.horizontalScrollBar().setValue(position)
        model.setData(model.index(0, 0), 'edited')
        app.processEvents()
        counts.append(timed_walk())
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
        assert timed_walk(roles=roles) < MAX_CELLS * 4
        assert b'AXTextField' in roles, 'native cell editor missing'
        table.closePersistentEditor(model.index(0, 0))
        table.setFocus()
    model.edits.clear()
    model.replace(['a', 'b'], [('new', 'value')])
    app.processEvents()
    assert timed_walk() < MAX_CELLS * 4
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == 'new'
    phase('wide-end', wide_cocoa_seconds=monotonic() - started,
          native_max_walk_seconds=max(walk_seconds), native_node_counts=counts)
    phase('table-cleanup-start')
    table.close()
    table.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    phase('table-cleanup-end')
    return 0
