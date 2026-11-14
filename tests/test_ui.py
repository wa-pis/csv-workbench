from concurrent.futures import wait

from PySide6.QtCore import Qt
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

    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        QTest.mouseClick(window.open_button, Qt.MouseButton.LeftButton)
        window.view_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.rowCount() == 200
        window.count_job[1].result(timeout=5)
        window.collect_reads()
        assert f"File: {source.stat().st_size:,} bytes" in window.status.text()
        assert "Total rows: 201" in window.status.text()
        assert "Loaded: 200" in window.status.text()

        bar = window.table.verticalScrollBar()
        bar.setValue(bar.maximum())
        window.page_job[1].result(timeout=5)
        window.collect_reads()
        assert window.model.rowCount() == 201
        assert window.model.rows[-1] == ("00201",)
        assert not window.has_more
        assert "Loaded: 201" in window.status.text()

        QTest.mouseClick(window.open_button, Qt.MouseButton.LeftButton)
        wait([window.view_job[1]], timeout=5)
        window.collect_reads()
        assert len(errors) == 1
        assert window.model.rowCount() == 201
        assert window.path == source

    finally:
        window.close()


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
    window.count_job[1].result(timeout=5)
    window.collect_reads()
    started, release = Event(), Event()

    def slow_read(*args, cancel):
        started.set()
        assert release.wait(5)
        return ["id"], [("stale",)], False

    monkeypatch.setattr("csv_workbench.ui.read_page", slow_read)
    try:
        window.load_view(source, Selection(sort_column="id"))
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
