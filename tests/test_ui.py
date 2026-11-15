from concurrent.futures import wait

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from csv_workbench.ui import MainWindow


@pytest.fixture
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
