from concurrent.futures import wait

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from csv_workbench.ui import MainWindow


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
            window.page_job[1].result(timeout=5)
            window.collect_reads()
            assert len(window.model.rows) <= BUFFER_ROWS
        assert window.model.offset == 200
        assert window.model.rows[-1] == ("0600",)
        bar.setValue(0)
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.offset == 0
        assert window.model.rows[0] == ("0000",)
        assert len(window.model.rows) <= BUFFER_ROWS
        assert window.has_more
    finally:
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


def test_edit_sorted_filtered_cell_updates_view_and_export(tmp_path, monkeypatch):
    from csv_workbench.core import Selection, read_page

    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    source = tmp_path / "data.csv"
    original = "id,label,group\n1,b,yes\n2,a,yes\n3,c,no\n4,d,yes\n"
    source.write_text(original, encoding="utf-8")
    output = tmp_path / "edited.csv"
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

        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
        monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
        window.save_selection()
        finish("export_job")
        assert read_page(output, 0) == (["label", "group"], [("d", "yes"), ("z", "yes")], False)
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


def test_open_format_dialog_uses_manual_delimiter_and_header(tmp_path, monkeypatch):
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
    source.write_text("one;two|2\nthree;four|4\n", encoding="utf-8")
    window = MainWindow()

    def configure(dialog):
        delimiter = dialog.findChild(QComboBox)
        header = dialog.findChild(QCheckBox)
        model = dialog.findChild(QTableView).model()
        assert delimiter.currentData() == ";"
        assert model.headers == ["one", "two|2"]
        header.setChecked(False)
        assert model.rows[0] == ("one", "two|2")
        delimiter.setCurrentIndex(delimiter.findText("Custom…"))
        open_button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Open)
        assert not open_button.isEnabled()
        dialog.findChild(QLineEdit).setText("|")
        assert open_button.isEnabled()
        assert model.rows[0] == ("one;two", "2")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", configure)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(source), ""))
    try:
        window.open_file()
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        assert window.csv_format == CsvFormat("|", False)
        assert window.model.rows == [("one;two", "2"), ("three;four", "4")]
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
