from concurrent.futures import wait
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from csv_workbench.ui import MainWindow


@pytest.fixture(autouse=True)
def preset_settings(tmp_path, monkeypatch):
    path = str(tmp_path / "format-settings.ini")

    def settings(*_):
        return QSettings(path, QSettings.Format.IniFormat)

    monkeypatch.setattr("csv_workbench.ui.QSettings", settings)
    return settings()


def test_open_and_load_more(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "preview.csv"
    source.write_text("id\n" + "".join(f"{i:05}\n" for i in range(1, 202)), encoding="utf-8")
    selected = iter([source, tmp_path / "missing.csv"])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(next(selected)), ""))
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[-1]))

    from csv_workbench.core import CsvFormat

    monkeypatch.setattr(MainWindow, "configure_format", lambda *_: CsvFormat())
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        QTest.mouseClick(window.open_button, Qt.MouseButton.LeftButton)
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.rowCount() == 200
        if window.count_job is not None:
            window.count_job[1].result(timeout=5)
            window.collect_reads()
        assert f"File: {source.stat().st_size:,} bytes" in window.status.text()
        assert "Total rows: 201" in window.status.text()
        assert "Buffered: 200" in window.status.text()

        bar = window.table.verticalScrollBar()
        bar.setValue(bar.maximum())
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.rowCount() == 201
        assert window.model.rows[-1] == ("00201",)
        assert not window.has_more
        assert "Buffered: 201" in window.status.text()

        QTest.mouseClick(window.open_button, Qt.MouseButton.LeftButton)
        wait([window.view_job[1]], timeout=5)
        window.collect_reads()
        assert len(errors) == 1
        assert window.model.rowCount() == 201
        assert window.path == source

    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_cancel_running_selection_keeps_view(tmp_path, monkeypatch):
    from threading import Event

    from PySide6.QtCore import QTimer

    from csv_workbench.core import Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    window = MainWindow()
    source = tmp_path / "data.csv"
    source.write_text("id\n001\n", encoding="utf-8")
    window.load_view(source, Selection(), new_file=True)
    window.view_job[1].result(timeout=5)
    window.collect_reads()
    if window.count_job is not None:
        window.count_job[1].result(timeout=5)
        window.collect_reads()
    started, release = Event(), Event()

    def slow_read(*args, cancel, fmt):
        started.set()
        assert release.wait(5)
        return ["id"], [("stale",)], False

    monkeypatch.setattr("csv_workbench.ui.read_editable_page", slow_read)
    try:
        window.load_view(source, Selection(filters=(("id", "x"),)))
        assert started.wait(5)
        token, future = window.view_job
        assert not future.done()
        QTimer.singleShot(0, window.cancel_button.click)
        app.processEvents()
        assert token.cancelled
        release.set()
        future.result(timeout=5)
        window.collect_reads()
        assert window.model.rows == [("001",)]
        assert window.selection == Selection()
        assert window.open_button.isEnabled()
    finally:
        release.set()
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_scroll_buffer_can_restore_evicted_rows(tmp_path):
    from csv_workbench.core import Selection
    from csv_workbench.ui import BUFFER_ROWS

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "many.csv"
    source.write_text("id\n" + "".join(f"{i:04}\n" for i in range(601)), encoding="utf-8")
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.load_view(source, Selection(), new_file=True)
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        bar = window.table.verticalScrollBar()
        for _ in range(3):
            bar.setValue(bar.maximum())
            last = window.model.index(window.model.rowCount() - 1, 0)
            window.table.setCurrentIndex(last)
            current_value = last.data()
            top_row = window.model.offset + window.table.rowAt(0)
            window.page_job[1].result(timeout=5)
            window.collect_reads()
            app.processEvents()
            assert len(window.model.rows) <= BUFFER_ROWS
            assert window.table.currentIndex().data() == current_value
            assert window.model.offset + window.table.rowAt(0) == top_row
        assert window.model.offset == 200
        assert window.model.rows[-1] == ("0600",)
        QTest.keyClick(window.table, Qt.Key.Key_PageDown)
        assert window.table.currentIndex().data() == "0600"
        bar.setValue(0)
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.offset == 0
        assert window.model.rows[0] == ("0000",)
        assert len(window.model.rows) <= BUFFER_ROWS
        assert window.has_more
        window.activateWindow()
        window.table.setFocus()
        app.processEvents()
        QTest.keyClick(window.table, Qt.Key.Key_End)
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.table.currentIndex().data() == "0600"
        assert bar.value() == bar.maximum()
        assert not window.has_more
        assert window.model.offset == 400
        bar.setValue(0)
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.offset == 200
        assert window.model.rows[-1] == ("0600",)
        QTest.keyClick(window.table, Qt.Key.Key_Home)
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.offset == 0
        assert window.table.currentIndex().data() == "0000"
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("sorted_view", [False, True])
def test_go_to_row_in_current_view(tmp_path, monkeypatch, sorted_view):
    from csv_workbench.core import Selection
    from csv_workbench.ui import BUFFER_ROWS

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "rows.csv"
    source.write_text(
        "id,label\n" + "".join(f"{i:04},{'keep' if i % 2 == 0 else 'skip'}\n" for i in range(2000)),
        encoding="utf-8",
    )
    selection = Selection((("label", "keep"),), "id", True) if sorted_view else Selection()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    answers = iter([("401", True), ("abc", True), ("10001", True), ("1", False)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: next(answers))
    window = MainWindow()
    window.show()
    try:
        window.load_view(source, selection, new_file=True)
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        assert window.go_to_row_action.isEnabled()
        window.go_to_row_action.trigger()
        window.page_job[1].result(timeout=5)
        window.collect_reads()

        expected = "1198" if sorted_view else "0400"
        assert window.table.currentIndex().data() == expected
        assert window.model.offset + window.table.currentIndex().row() == 400
        assert window.model.rowCount() <= BUFFER_ROWS

        window.go_to_row_action.trigger()  # Invalid text.
        window.go_to_row_action.trigger()  # Valid integer, but past the end.
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        window.go_to_row_action.trigger()  # Cancel the dialog.
        assert len(warnings) == 2
        assert window.table.currentIndex().data() == expected
        assert window.model.offset == 200
        assert window.has_more

        bar = window.table.verticalScrollBar()
        bar.setValue(0)
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.offset == 0
        assert window.table.currentIndex().data() == expected
        bar.setValue(bar.maximum())
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.offset == 200
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_find_selects_and_highlights_unbuffered_cells_and_cancels(tmp_path, monkeypatch):
    from csv_workbench.core import Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "find.csv"
    source.write_text(
        "id,note\n" + "".join(f"{i},{'hit far' if i == 710 else 'plain'}\n" for i in range(1000)),
        encoding="utf-8",
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    window.show()
    window.activateWindow()
    app.processEvents()
    release = Event()
    try:
        window.load_view(source, Selection(), new_file=True)
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        window.model.setData(window.model.index(0, 1), "HIT edit")
        window.table.setFocus()
        QTest.keySequence(window.table, QKeySequence(QKeySequence.StandardKey.Find))
        assert not window.find_bar.isHidden()
        window.find_input.setText("hit")

        def navigate(button):
            button.click()
            window.find_job[1].result(timeout=5)
            window.collect_reads()
            if window.page_job is not None:
                window.page_job[1].result(timeout=5)
                window.collect_reads()
            current = window.table.currentIndex()
            assert current.column() == 1
            assert current.data(Qt.ItemDataRole.BackgroundRole) is not None
            return current.data()

        assert navigate(window.find_next) == "HIT edit"
        assert navigate(window.find_next) == "hit far"
        assert window.model.offset + window.table.currentIndex().row() == 710
        assert navigate(window.find_previous) == "HIT edit"
        assert window.selection == Selection()

        started = Event()

        def late_search(*_, **__):
            started.set()
            assert release.wait(timeout=5)
            return 710, 1

        monkeypatch.setattr("csv_workbench.ui.find_csv_cell", late_search)
        window.find_next.click()
        assert started.wait(timeout=5)
        window.find_stop.click()
        assert window.find_job[0].cancelled
        release.set()
        window.find_job[1].result(timeout=5)
        window.collect_reads()
        assert window.find_message.text() == "Cancelled"
        assert window.table.currentIndex().data() == "HIT edit"
        window.close_find()
        assert window.table.currentIndex().data(Qt.ItemDataRole.BackgroundRole) is None
    finally:
        release.set()
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_open_session_restores_selection_and_keeps_all_source_headers(tmp_path, monkeypatch):
    from csv_workbench.core import Selection
    from csv_workbench.session import save_session

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "preview.csv"
    source.write_text("id,label\n001,keep\n002,skip\n003,keep\n", encoding="utf-8")
    session = tmp_path / "saved.csvsession"
    selection = Selection((("label", "keep"),), "id", True, ("id",))
    save_session(session, source, selection)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(session), ""))
    window = MainWindow()
    try:
        window.open_session_action.trigger()
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        assert window.selection == selection
        assert window.source_headers == ["id", "label"]
        assert window.model.headers == ["id"]
        assert window.model.rows == [("003",), ("001",)]
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_column_header_actions_filter_and_sort_full_csv(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMenu

    from csv_workbench.core import Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    window = MainWindow()
    source = tmp_path / "data.csv"
    source.write_text("id,label\n" + "001,skip\n" * 200 + "202,keep\n203,keep\n", encoding="utf-8")
    try:

        def finish_view():
            window.view_job[1].result(timeout=5)
            window.collect_reads()

        window.load_view(source, Selection(), new_file=True)
        finish_view()
        monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("keep", True))
        from PySide6.QtCore import QRect

        class TestMenu(QMenu):
            def exec(self, position):
                return self.actions()[0]

        monkeypatch.setattr("csv_workbench.ui.QMenu", TestMenu)
        header = window.table.horizontalHeader()
        window.show()
        app.processEvents()

        def click(section, control, modifier=Qt.KeyboardModifier.NoModifier):
            rect = QRect(
                header.sectionViewportPosition(section),
                0,
                header.sectionSize(section),
                header.height(),
            )
            QTest.mouseClick(
                header.viewport(),
                Qt.MouseButton.LeftButton,
                modifier,
                header.control_rects(rect)[control].center(),
            )
            finish_view()

        click(1, 1)
        assert window.model.headerData(1, Qt.Orientation.Horizontal) == "label  •"
        click(0, 0)
        assert window.model.rows == [("202", "keep"), ("203", "keep")]
        click(1, 0, Qt.KeyboardModifier.AltModifier)
        assert header.sort_keys == (("id", False), ("label", False))
        click(0, 0)
        assert header.sort_keys == (("id", True),)
        assert window.model.rows == [("203", "keep"), ("202", "keep")]
        click(0, 0)
        assert not header.sort_keys
        assert window.model.rows == [("202", "keep"), ("203", "keep")]
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_edit_sorted_filtered_cell_updates_view_and_export(tmp_path, monkeypatch, preset_settings):
    from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QLineEdit

    from csv_workbench.core import CsvFormat, Selection, read_page

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "data.csv"
    original = "id,label,group\n1,b,yes\n2,a,yes\n3,c,no\n4,d,yes\n"
    source.write_text(original, encoding="utf-8")
    output = tmp_path / "edited.tsv"
    selection = Selection((("group", "yes"),), "label", False, ("label", "group"))
    window = MainWindow()

    def finish(name="view_job"):
        getattr(window, name)[1].result(timeout=5)
        window.collect_reads()

    try:
        window.load_view(source, selection, new_file=True)
        finish()
        assert window.model.rows == [("a", "yes"), ("b", "yes"), ("d", "yes")]
        assert window.model.setData(window.model.index(0, 0), "z")
        finish()
        assert window.model.rows == [("b", "yes"), ("d", "yes"), ("z", "yes")]
        assert window.model.setData(window.model.index(0, 1), "no")
        finish()
        assert window.model.rows == [("d", "yes"), ("z", "yes")]

        def configure_export(dialog):
            delimiter, encoding = dialog.findChildren(QComboBox)[:2]
            _, quote = dialog.findChildren(QLineEdit)
            include_header = dialog.findChild(QCheckBox)
            save = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
            delimiter.setCurrentIndex(delimiter.findText("Custom…"))
            assert not save.isEnabled()
            delimiter.setCurrentIndex(delimiter.findData("\t"))
            encoding.setCurrentIndex(encoding.findData("utf-16"))
            quote.setText("'")
            include_header.setChecked(False)
            assert save.isEnabled()
            return QDialog.DialogCode.Accepted

        def choose_destination(_, title, suggestion, file_filter):
            assert suggestion.endswith("-selection.tsv")
            assert file_filter == "TSV (*.tsv)"
            return str(output), file_filter

        monkeypatch.setattr(QDialog, "exec", configure_export)
        monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_destination)
        monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
        window.save_selection()
        finish("export_job")
        fmt = CsvFormat("\t", False, "utf-16", "'")
        assert read_page(output, 0, fmt=fmt)[1] == [("d", "yes"), ("z", "yes")]
        assert output.read_text(encoding="utf-16").splitlines() == ["'d'\t'yes'", "'z'\t'yes'"]
        assert window.dirty
        window.undo_action.trigger()
        finish()
        assert window.model.rows == [("b", "yes"), ("d", "yes"), ("z", "yes")]
        window.redo_action.trigger()
        finish()
        assert window.model.rows == [("d", "yes"), ("z", "yes")]

        saved = tmp_path / "whole.csv"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(saved), ""))
        window.save_as_action.trigger()
        finish("save_job")
        assert window.path == saved.resolve()
        assert not window.dirty
        assert read_page(saved, 0)[1] == [
            ("1", "b", "no"),
            ("2", "z", "yes"),
            ("3", "c", "no"),
            ("4", "d", "yes"),
        ]
        window.undo_action.trigger()
        finish()
        assert window.dirty
        assert window.model.rows == [("b", "yes"), ("d", "yes"), ("z", "yes")]
        window.save_action.trigger()
        finish("save_job")
        assert not window.dirty
        assert read_page(saved, 0)[1][0] == ("1", "b", "yes")
        window.redo_action.trigger()
        finish()

        window.load_view(window.path, Selection())
        finish()
        assert window.model.data(window.model.index(0, 2)) == "no"
        assert window.model.data(window.model.index(1, 1)) == "z"
        assert source.read_text(encoding="utf-8") == original
    finally:
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_cell_menu_filters_empty_value_and_removes_rule_individually(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialogButtonBox, QLabel, QMenu, QPushButton

    from csv_workbench.core import Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "values.csv"
    source.write_text(
        "id,value,tag\n1,,keep\n"
        + "".join(f"{i},NULL,keep\n" for i in range(2, 205))
        + "205,,keep\n206,,skip\n",
        encoding="utf-8",
    )

    class CellMenu(QMenu):
        def exec(self, point):
            return self.actions()[0].menu().actions()[0]

    monkeypatch.setattr("csv_workbench.ui.QMenu", CellMenu)
    window = MainWindow()
    window.show()
    app.processEvents()

    def finish():
        window.view_job[1].result(timeout=5)
        window.collect_reads()

    try:
        selection = Selection(filters=(("tag", "keep"),))
        window.load_view(source, selection, new_file=True)
        finish()
        index = window.model.index(0, 1)
        window.cell_filter_menu(window.table.visualRect(index).center())
        finish()
        assert window.selection.exact_filters == (("value", "equals", None),)
        assert window.model.rows == [("1", None, "keep"), ("205", None, "keep")]

        removed = []

        def remove_exact():
            dialog = app.activeModalWidget()
            label = next((v for v in dialog.findChildren(QLabel) if v.text() == "value = NULL"), None)
            if label is not None:
                label.parentWidget().findChild(QPushButton).click()
                removed.append(True)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Apply).click()

        QTimer.singleShot(0, remove_exact)
        window.edit_selection()
        finish()
        assert removed == [True]
        assert window.selection == selection
        assert window.model.rowCount() == 200
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_column_layout_survives_session_and_preserves_source_order(tmp_path, monkeypatch):
    from csv_workbench.core import CsvFormat, Selection, read_page
    from csv_workbench.session import load_session, save_session

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "layout.csv"
    source.write_text("id,label,note\n1,a,b\n", encoding="utf-8")
    output = tmp_path / "export.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    monkeypatch.setattr(MainWindow, "configure_export_format", lambda _: CsvFormat())
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    window = MainWindow()

    def finish(name="view_job"):
        getattr(window, name)[1].result(timeout=5)
        window.collect_reads()

    try:
        window.load_view(source, Selection(), new_file=True)
        finish()
        window.table.horizontalHeader().moveSection(2, 0)
        app.processEvents()
        finish()
        assert window.model.headers == ["note", "id", "label"]
        window.set_visible_columns(("note", "id"))
        finish()
        assert window.model.headers == ["note", "id"]

        session = tmp_path / "layout.csvsession"
        save_session(session, source, window.selection)
        path, selection, fmt = load_session(session)
        window.load_view(path, selection, new_file=True, fmt=fmt)
        finish()
        assert window.model.headers == ["note", "id"]
        window.set_visible_columns(("note", "id", "label"))
        finish()
        assert window.model.headers == ["note", "id", "label"]

        window.model.setData(window.model.index(0, 0), "edited")
        finish()
        window.save_selection()
        finish("export_job")
        assert read_page(output, 0)[:2] == (["note", "id", "label"], [("edited", "1", "a")])
        window.save_action.trigger()
        finish("save_job")
        assert read_page(source, 0)[:2] == (["id", "label", "note"], [("1", "a", "edited")])
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_value_editor_preserves_multiline_text_in_save_and_export(tmp_path, monkeypatch):
    from csv_workbench.core import CsvFormat, Selection, read_page

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "value.csv"
    source.write_text("id,note\n1,old\n2,keep\n", encoding="utf-8")
    output = tmp_path / "export.csv"
    value = 'first\nsecond, "quoted"'
    answers = iter([(value, False), (value, True)])

    def open_editor(*args):
        assert args[-1] == "old"
        return next(answers)

    monkeypatch.setattr(QInputDialog, "getMultiLineText", open_editor)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    monkeypatch.setattr(MainWindow, "configure_export_format", lambda _: CsvFormat())
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    window.show()
    window.activateWindow()
    app.processEvents()

    def finish(name):
        getattr(window, name)[1].result(timeout=5)
        window.collect_reads()

    try:
        window.load_view(source, Selection(), new_file=True)
        finish("view_job")
        index = window.model.index(0, 1)
        window.table.setCurrentIndex(index)
        window.table.setFocus()
        QTest.keyClick(window.table, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert index.data() == "old"
        assert not window.history

        window.value_action.trigger()
        assert index.data() == value
        assert window.history_pos == len(window.history) == 1
        window.undo_action.trigger()
        assert index.data() == "old"
        window.redo_action.trigger()
        assert index.data() == value

        window.save_selection()
        finish("export_job")
        assert read_page(output, 0)[1] == [("1", value), ("2", "keep")]
        assert read_page(source, 0)[1][0] == ("1", "old")
        window.save_action.trigger()
        finish("save_job")
        assert read_page(source, 0)[1] == [("1", value), ("2", "keep")]
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_range_and_row_history_survives_save(tmp_path, monkeypatch):
    from PySide6.QtCore import QItemSelection, QItemSelectionModel

    from csv_workbench.core import Selection, read_page

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "data.csv"
    source.write_text("id,label\n1,a\n2,b\n3,c\n", encoding="utf-8")
    window = MainWindow()

    def finish(name):
        getattr(window, name)[1].result(timeout=5)
        window.collect_reads()

    def select(row, last_row, col, last_col):
        window.table.setCurrentIndex(window.model.index(row, col))
        window.table.selectionModel().select(
            QItemSelection(window.model.index(row, col), window.model.index(last_row, last_col)),
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )

    try:
        window.load_view(source, Selection(), new_file=True)
        finish("view_job")
        select(0, 1, 0, 1)
        window.copy_action.trigger()
        assert app.clipboard().text().splitlines() == ["1\ta", "2\tb"]
        select(1, 1, 0, 0)
        window.paste_action.trigger()
        assert window.model.data(window.model.index(2, 1)) == "b"
        assert window.history_pos == 1
        window.undo_action.trigger()
        assert window.model.data(window.model.index(2, 1)) == "c"
        window.redo_action.trigger()
        select(1, 2, 1, 1)
        window.clear_action.trigger()
        assert [window.model.data(window.model.index(i, 1)) for i in (1, 2)] == ["NULL"] * 2
        window.undo_action.trigger()
        select(0, 0, 0, 1)
        window.clone_action.trigger()
        finish("view_job")
        window.add_row_action.trigger()
        finish("view_job")
        select(1, 2, 0, 1)
        window.delete_action.trigger()
        finish("view_job")
        assert window.model.rows == [("1", "a"), ("1", "a"), (None, None)]
        window.save_action.trigger()
        finish("save_job")
        assert read_page(source, 0)[1] == window.model.rows
        assert not window.dirty
        window.undo_action.trigger()
        finish("view_job")
        assert window.model.rows == [("1", "a"), ("1", "a"), ("2", "b"), ("1", "a"), (None, None)]
        window.redo_action.trigger()
        finish("view_job")
        assert window.model.rows == [("1", "a"), ("1", "a"), (None, None)]
        assert not window.dirty
    finally:
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_open_format_dialog_updates_for_manual_encoding_quote_and_delimiter(
    tmp_path, monkeypatch, preset_settings
):
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QLineEdit,
        QTableView,
    )

    from csv_workbench.core import CsvFormat

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "input.csv"
    source.write_bytes("один;два|'2|3'\nтри;четыре|4\n".encode("utf-16-le"))
    window = MainWindow()

    def configure(dialog):
        delimiter, encoding = dialog.findChildren(QComboBox)[:2]
        custom, quote = dialog.findChildren(QLineEdit)
        header = dialog.findChild(QCheckBox)
        model = dialog.findChild(QTableView).model()
        open_button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Open)
        assert not open_button.isEnabled()
        encoding.setCurrentIndex(encoding.findData("utf-16-le"))
        assert open_button.isEnabled()
        assert delimiter.currentData() == ";"
        assert model.headers == ["один", "два|'2|3'"]
        header.setChecked(False)
        delimiter.setCurrentIndex(delimiter.findText("Custom…"))
        assert not open_button.isEnabled()
        custom.setText("|")
        assert model.rows[0] == ("один;два", "'2", "3'")
        quote.setText("|")
        assert not open_button.isEnabled()
        quote.setText("'")
        assert open_button.isEnabled()
        assert model.rows[0] == ("один;два", "2|3")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", configure)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(source), ""))
    try:
        window.open_file()
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        assert window.csv_format == CsvFormat("|", False, "utf-16-le", "'")
        assert window.model.rows == [("один;два", "2|3"), ("три;четыре", "4")]
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_format_dialogs_save_and_reapply_presets(tmp_path, monkeypatch, preset_settings):
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QInputDialog,
        QLineEdit,
        QPushButton,
        QTableView,
    )

    from csv_workbench.core import CsvFormat
    from csv_workbench.presets import FormatPresets

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "report.txt"
    source.write_bytes("001|'данные|здесь'\n".encode("cp1251"))
    fmt = CsvFormat("|", False, "cp1251", "'")
    opening = FormatPresets(preset_settings)
    opening.save("Report", fmt)
    opening.set_default("Report")

    def use_open_preset(dialog):
        combo = dialog.findChild(QComboBox, "formatPresets")
        assert combo.currentText() == "Report"
        model = dialog.findChild(QTableView).model()
        assert model.rows == [("001", "данные|здесь")]
        return QDialog.DialogCode.Accepted

    export_format = CsvFormat("\t", False, "utf-16", "^")

    def save_and_load_export_preset(dialog):
        delimiter, encoding, combo = dialog.findChildren(QComboBox)
        _, quote = dialog.findChildren(QLineEdit)
        header = dialog.findChild(QCheckBox)
        assert combo.findText("Report") < 0
        delimiter.setCurrentIndex(delimiter.findData("\t"))
        encoding.setCurrentIndex(encoding.findData("utf-16"))
        quote.setText("^")
        header.setChecked(False)
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Save Preset…").click()
        delimiter.setCurrentIndex(delimiter.findData(","))
        encoding.setCurrentIndex(0)
        quote.setText('"')
        header.setChecked(True)
        combo.activated.emit(combo.currentIndex())
        manage = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Manage…")
        next(a for a in manage.menu().actions() if a.text() == "Set as Default").trigger()
        return QDialog.DialogCode.Accepted

    def rename_export_default(dialog):
        combo = dialog.findChild(QComboBox, "formatPresets")
        assert combo.currentText() == "TSV report"
        manage = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Manage…")
        next(a for a in manage.menu().actions() if a.text() == "Rename…").trigger()
        assert combo.currentText() == "Renamed TSV"
        return QDialog.DialogCode.Accepted

    def delete_export_default(dialog):
        combo = dialog.findChild(QComboBox, "formatPresets")
        assert combo.currentText() == "Renamed TSV"
        manage = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Manage…")
        next(a for a in manage.menu().actions() if a.text() == "Delete…").trigger()
        assert combo.currentIndex() == 0
        return QDialog.DialogCode.Rejected

    window = MainWindow()
    try:
        monkeypatch.setattr(QDialog, "exec", use_open_preset)
        assert window.configure_format(source) == fmt
        monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("TSV report", True))
        monkeypatch.setattr(QDialog, "exec", save_and_load_export_preset)
        assert window.configure_export_format() == export_format
        exporting = FormatPresets(preset_settings, exporting=True)
        assert exporting.default_name() == "TSV report"
        monkeypatch.setattr(QInputDialog, "getText", lambda *_, **__: ("Renamed TSV", True))
        monkeypatch.setattr(QDialog, "exec", rename_export_default)
        assert window.configure_export_format() == export_format
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QDialog, "exec", delete_export_default)
        assert window.configure_export_format() is None
        assert exporting.load() == {}
        assert exporting.default_name() is None
        assert opening.default_name() == "Report"
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_open_recent_restores_saved_format_and_handles_missing_files(tmp_path, monkeypatch, preset_settings):
    from csv_workbench.core import CsvFormat, Selection
    from csv_workbench.recent import RecentFile, recent_files

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "R&D.tsv"
    source.write_bytes("001\t'чай\tкофе'\n".encode("cp1251"))
    other = tmp_path / "other.csv"
    other.write_text("id\n002\n", encoding="utf-8")
    fmt = CsvFormat("\t", False, "cp1251", "'")
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    window = MainWindow()

    def finish():
        wait([window.view_job[1]], timeout=5)
        window.collect_reads()
        assert window.view_job is None

    def open_recent(path):
        window.populate_recent_files()
        label = str(path).replace("&", "&&")
        next(action for action in window.recent_menu.actions() if action.text() == label).trigger()
        finish()

    try:
        window.load_view(source, Selection(), new_file=True, fmt=fmt)
        finish()
        window.load_view(other, Selection(), new_file=True)
        finish()
        open_recent(source)
        assert window.csv_format == fmt
        assert window.model.rows == [("001", "чай\tкофе")]
        assert recent_files(preset_settings)[0] == RecentFile(source, fmt)
        if window.count_job:
            window.count_job[1].result(timeout=5)
            window.collect_reads()
        source.unlink()
        open_recent(other)
        open_recent(source)
        assert window.path == other
        assert window.model.rows == [("002",)]
        assert errors and recent_files(preset_settings)[0].path == other
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_manage_recent_files_controls_update_menu(tmp_path, monkeypatch, preset_settings):
    from PySide6.QtWidgets import QDialog, QListWidget, QPushButton

    from csv_workbench.core import CsvFormat
    from csv_workbench.recent import remember_file

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    first, second, third = (tmp_path / name for name in ("first.csv", "second.csv", "third.tsv"))
    for path in (first, second, third):
        remember_file(preset_settings, path, CsvFormat())
    window = MainWindow()

    def manage(dialog):
        files = dialog.findChild(QListWidget)
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        files.setCurrentRow(2)
        buttons["Pin"].click()
        assert files.item(0).text() == f"★ {first}"
        assert buttons["Pin"].text() == "Unpin"
        files.setCurrentRow(1)
        buttons["Remove"].click()
        assert files.count() == 2
        assert files.item(1).text() == str(second)
        buttons["Clear Unpinned"].click()
        assert files.count() == 1
        assert not buttons["Clear Unpinned"].isEnabled()
        return QDialog.DialogCode.Accepted

    try:
        monkeypatch.setattr(QDialog, "exec", manage)
        window.populate_recent_files()
        window.recent_menu.actions()[-1].trigger()
        window.populate_recent_files()
        assert window.recent_menu.actions()[0].text() == f"★ {first}"
        assert not any(str(path) in action.text() for path in (second, third) for action in window.recent_menu.actions())
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("accept", [True, False])
def test_drop_tsv_opens_format_preview_before_loading(tmp_path, monkeypatch, accept):
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
    from PySide6.QtWidgets import QDialog, QTableView

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    path = tmp_path / "report.TSV"
    path.write_text("id\tname\n001\tчай\n", encoding="utf-8")
    window = MainWindow()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    actions = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
    start = QDragEnterEvent(QPointF(), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    move = QDragMoveEvent(QPointF(), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    drop = QDropEvent(QPointF(), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    def preview(dialog):
        model = dialog.findChild(QTableView).model()
        assert window.path is None
        assert model.headers == ["id", "name"]
        assert model.rows == [("001", "чай")]
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    try:
        monkeypatch.setattr(QDialog, "exec", preview)
        assert window.acceptDrops()
        QApplication.sendEvent(window.table.viewport(), start)
        QApplication.sendEvent(window.table.viewport(), move)
        QApplication.sendEvent(window.table.viewport(), drop)
        assert start.isAccepted() and move.isAccepted() and drop.isAccepted()
        assert drop.dropAction() == Qt.DropAction.CopyAction
        app.processEvents()
        if not accept:
            assert window.path is None and window.view_job is None
            return
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        assert window.path == path
        assert window.csv_format.delimiter == "\t"
        assert window.model.rows == [("001", "чай")]
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("kind", ["multiple", "remote", "directory", "unsupported", "move_only"])
def test_drop_ignores_unsupported_payloads(tmp_path, kind):
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDropEvent

    path = tmp_path / ("report.txt" if kind == "unsupported" else "report.csv")
    if kind == "directory":
        path.mkdir()
    else:
        path.touch()
    urls = [QUrl.fromLocalFile(str(path))]
    if kind == "multiple":
        urls *= 2
    if kind == "remote":
        urls = [QUrl("https://example.com/report.csv")]
    mime = QMimeData()
    mime.setUrls(urls)
    actions = Qt.DropAction.MoveAction if kind == "move_only" else Qt.DropAction.CopyAction
    drop = QDropEvent(QPointF(), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    assert MainWindow.dropped_csv(drop) is None


def test_pinned_columns_stay_left_and_restore_from_session(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu

    from csv_workbench.core import CsvFormat, Selection, read_page
    from csv_workbench.session import load_session, save_session

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "wide.csv"
    columns = [f"col{i}" for i in range(12)]
    source.write_text(",".join(columns) + "\n" + ("1," * 11 + "1\n") * 80, encoding="utf-8")
    output = tmp_path / "export.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    monkeypatch.setattr(MainWindow, "configure_export_format", lambda _: CsvFormat())
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    window = MainWindow()
    window.resize(640, 360)
    window.show()

    def finish():
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        app.processEvents()

    def choose(label, header, index):
        class ChoosingMenu(QMenu):
            def exec(self, *_):
                return next(item for item in self.actions() if item.text() == label)

        monkeypatch.setattr("csv_workbench.ui.QMenu", ChoosingMenu)
        window.column_layout_menu(QPoint(header.sectionViewportPosition(index) + 5, 5), header)
        app.processEvents()

    try:
        window.load_view(source, Selection(), new_file=True)
        finish()
        table = window.table
        frozen = table.pinned
        choose("Pin Column Left", table.horizontalHeader(), 2)
        choose("Pin Column Left", table.horizontalHeader(), 3)
        assert frozen.isVisible()
        assert frozen.model() is table.model()
        assert frozen.selectionModel() is table.selectionModel()
        assert window.model.headers == columns
        assert not frozen.isColumnHidden(2) and not frozen.isColumnHidden(3)
        assert frozen.isColumnHidden(0)
        start = frozen.visualRect(window.model.index(0, 2)).left()
        before = table.visualRect(window.model.index(0, 4)).left()
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        table.verticalScrollBar().setValue(100)
        assert table.visualRect(window.model.index(0, 4)).left() < before
        assert frozen.visualRect(window.model.index(0, 2)).left() == start
        assert frozen.rowAt(0) == table.rowAt(0) > 0
        frozen.verticalScrollBar().setValue(200)
        assert table.verticalScrollBar().value() == 200
        table.scrollTo(window.model.index(10, 0))
        assert table.visualRect(window.model.index(10, 0)).left() >= frozen.width()

        window.save_selection()
        window.export_job[1].result(timeout=5)
        window.collect_reads()
        assert read_page(output, 0)[0] == columns

        session = tmp_path / "pins.csvsession"
        save_session(session, source, window.selection)
        path, selection, fmt = load_session(session)
        assert selection.pinned_columns == ("col2", "col3")
        window.load_view(path, selection, new_file=True, fmt=fmt)
        finish()
        assert table.pinned_sections == (2, 3)
        choose("Unpin Column", frozen.horizontalHeader(), 2)
        assert table.pinned_sections == (3,)
        choose("Unpin Column", frozen.horizontalHeader(), 3)
        assert not frozen.isVisible()
        assert window.model.headers == columns
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_record_view_uses_current_row_and_live_visible_values(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QTableWidget

    from csv_workbench.core import Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "records.csv"
    source.write_text("id,note,hidden\n1,first,a\n2,second,b\n", encoding="utf-8")
    window = MainWindow()

    opened = []

    class InspectDialog(QDialog):
        def exec(self):
            opened.append(True)
            assert self.windowTitle() == "Record View — Row 2"
            table = self.findChild(QTableWidget)
            assert not table.editTriggers()
            assert table.rowCount() == 2
            assert [
                (table.item(row, 0).text(), table.item(row, 1).text())
                for row in range(table.rowCount())
            ] == [("note", "unsaved\nsecond line"), ("id", "2")]

    monkeypatch.setattr("csv_workbench.ui.QDialog", InspectDialog)
    try:
        assert not window.record_action.isEnabled()
        window.load_view(
            source, Selection(visible_columns=("id", "note"), pinned_columns=("note",)),
            new_file=True,
        )
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        cell = window.model.index(1, 1)
        window.model.edits[window.model.cell_key(cell)] = "unsaved\nsecond line"
        window.table.setCurrentIndex(cell)
        assert window.record_action.isEnabled()
        window.record_action.trigger()
        assert opened == [True]
        assert window.table.currentIndex() == cell
        assert window.view_job is window.page_job is None
    finally:
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_transpose_selection_is_bounded_and_uses_live_values(tmp_path, monkeypatch):
    from PySide6.QtCore import QItemSelection, QItemSelectionModel
    from PySide6.QtWidgets import QDialog, QTableWidget

    from csv_workbench.core import Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "transpose.csv"
    columns = [f"c{i}" for i in range(51)]
    source.write_text(",".join(columns) + "\n" + (",".join(columns) + "\n") * 200)
    window = MainWindow()
    opened, warnings = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    class InspectDialog(QDialog):
        def exec(self):
            table = self.findChild(QTableWidget)
            opened.append(True)
            assert table.rowCount() == table.columnCount() == 2
            assert not table.editTriggers()
            assert [table.verticalHeaderItem(i).text() for i in range(2)] == ["c1", "c0"]
            assert [table.horizontalHeaderItem(i).text() for i in range(2)] == ["101", "102"]
            assert table.item(0, 0).text() == "edited\nvalue"
            assert table.item(1, 1).text() == "c0"

    monkeypatch.setattr("csv_workbench.ui.QDialog", InspectDialog)
    try:
        window.load_view(source, Selection(pinned_columns=("c1",)), new_file=True)
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        window.model.offset = 100
        cell = window.model.index(0, 1)
        window.model.edits[window.model.cell_key(cell)] = "edited\nvalue"
        selection = window.table.selectionModel()
        flags = QItemSelectionModel.SelectionFlag
        selection.select(QItemSelection(window.model.index(0, 0), window.model.index(1, 1)),
                         flags.ClearAndSelect)
        window.transpose_action.trigger()
        assert opened == [True]
        assert window.page_job is None
        selection.select(QItemSelection(window.model.index(0, 0), window.model.index(199, 50)),
                         flags.ClearAndSelect)
        window.transpose_action.trigger()
        selection.select(QItemSelection(window.model.index(0, 0), window.model.index(0, 0)),
                         flags.ClearAndSelect)
        selection.select(QItemSelection(window.model.index(2, 0), window.model.index(2, 0)),
                         flags.Select)
        window.transpose_action.trigger()
        assert len(warnings) == 2
        assert opened == [True]
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_text_preview_reads_saved_encoded_source_and_preserves_table(tmp_path):
    from csv_workbench.core import CsvFormat, Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "source.csv"
    raw = 'id;note\r\n1;"Привет; мир"\r\n'
    source.write_bytes(raw.encode("cp1251"))
    window = MainWindow()
    try:
        window.load_view(source, Selection(), new_file=True,
                         fmt=CsvFormat(";", encoding="cp1251"))
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        cell = window.model.index(0, 1)
        key = window.model.cell_key(cell)
        window.model.edits[key] = "unsaved"
        window.table.setCurrentIndex(cell)
        window.view_mode.setCurrentIndex(1)
        assert window.source_text.isReadOnly()
        window.text_job[1].result(timeout=5)
        window.collect_reads()
        # QPlainTextEdit normalizes line endings for display.
        assert window.source_text.toPlainText() == raw.replace("\r\n", "\n")
        assert "unsaved table edits" in window.text_notice.text()
        assert "Complete source text" in window.text_notice.text()
        assert window.views.currentIndex() == 1
        window.view_mode.setCurrentIndex(0)
        assert window.table.currentIndex() == cell
        assert window.model.cell_value(cell) == "unsaved"
        assert window.views.currentIndex() == 0

        source.write_bytes(("x" * (128 * 1024 + 1)).encode("cp1251"))
        window.view_mode.setCurrentIndex(1)
        window.text_job[1].result(timeout=5)
        window.collect_reads()
        assert len(window.source_text.toPlainText()) == 128 * 1024
        assert "limited" in window.text_notice.text()

        window.view_mode.setCurrentIndex(0)
        window.view_mode.setCurrentIndex(1)
        token, _future = window.text_job
        window.view_mode.setCurrentIndex(0)
        assert token.cancelled
        assert window.text_job is None
        window.collect_reads()
        assert window.views.currentIndex() == 0
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_copy_as_uses_visible_order_edits_and_preserves_clipboard_on_limit(tmp_path, monkeypatch):
    import json

    from PySide6.QtCore import QItemSelection, QItemSelectionModel

    from csv_workbench.core import Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "copy.csv"
    source.write_text("id,note\n1,original\n")
    window = MainWindow()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    try:
        window.load_view(source, Selection(pinned_columns=("note",)), new_file=True)
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        cell = window.model.index(0, 1)
        window.model.edits[window.model.cell_key(cell)] = "edited"
        selection = window.table.selectionModel()
        selection.select(QItemSelection(window.model.index(0, 0), cell),
                         QItemSelectionModel.SelectionFlag.ClearAndSelect)
        next(action for action in window.copy_as_menu.actions()
             if action.text() == "JSON").trigger()
        assert app.clipboard().text().index("note") < app.clipboard().text().index("id")
        assert json.loads(app.clipboard().text()) == [{"note": "edited", "id": "1"}]
        window.copy_action.trigger()
        assert app.clipboard().text() == "edited\t1\r\n"
        app.clipboard().setText("keep")
        window.model.edits[window.model.cell_key(cell)] = "я" * (2 * 1024 * 1024)
        window.copy_range()
        assert app.clipboard().text() == "keep"
        assert len(warnings) == 1
        window.model.replace([str(i) for i in range(101)], [tuple(["x"] * 101)] * 100)
        selection.select(QItemSelection(window.model.index(0, 0), window.model.index(99, 100)),
                         QItemSelectionModel.SelectionFlag.ClearAndSelect)
        window.copy_range()
        assert app.clipboard().text() == "keep"
        assert len(warnings) == 2
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_json_export_action_writes_unloaded_rows(tmp_path, monkeypatch):
    import json

    from csv_workbench.core import Selection

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "source.csv"
    source.write_text("id\n" + "".join(f"{i:04}\n" for i in range(1201)))
    output = tmp_path / "output.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    window = MainWindow()
    try:
        window.load_view(source, Selection(sort_column="id", descending=True), new_file=True)
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.rowCount() == 200
        window.text_export_actions[0].trigger()
        window.export_job[1].result(timeout=5)
        window.collect_reads()
        rows = json.loads(output.read_text())
        assert len(rows) == 1201
        assert rows[0] == {"id": "1200"}
        assert rows[-1] == {"id": "0000"}
        assert window.model.rowCount() == 200
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
