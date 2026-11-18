import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from csv_workbench.core import CsvFormat, Selection
from csv_workbench.ui import MainWindow, WorkspaceWindow


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication(["csv-workbench-test", "-platform", "offscreen"])
    monkeypatch.setattr(
        "csv_workbench.ui.QSettings",
        lambda *_: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
    )
    window = WorkspaceWindow()
    yield window
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    documents = window.documents()
    window.close()
    window.heavy_executor.shutdown(wait=True)
    for document in documents:
        document.executor.shutdown(wait=True)
    window.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def finish_view(document):
    document.view_job[1].result(timeout=5)
    document.collect_reads()


def test_workspace_keyboard_shortcuts(monkeypatch, workspace):
    opened = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (opened.append(True) or "", ""))
    workspace.show()
    workspace.activateWindow()
    QApplication.processEvents()

    def press(key):
        table = workspace.tabs.currentWidget().table
        table.setFocus()
        QTest.keySequence(table, QKeySequence(key))

    keys = QKeySequence.StandardKey
    first = workspace.tabs.currentWidget()
    press(keys.AddTab)
    second = workspace.tabs.currentWidget()
    assert second is not first
    press(keys.NextChild)
    assert workspace.tabs.currentWidget() is first
    press(keys.PreviousChild)
    assert workspace.tabs.currentWidget() is second
    press(keys.Open)
    assert opened == [True]
    press(keys.Close)
    assert workspace.documents() == [first]


def test_tabs_keep_selection_and_unsaved_edits(tmp_path, monkeypatch, workspace):
    first = tmp_path / "first.csv"
    first.write_text("id,label\n1,keep a\n2,keep z\n3,hide\n", encoding="utf-8")
    second = tmp_path / "second.tsv"
    second.write_text("id\tlabel\n9\tother\n", encoding="utf-8")
    monkeypatch.setattr(
        MainWindow,
        "configure_format",
        lambda _, path: CsvFormat("\t") if path == second else CsvFormat(),
    )
    active = workspace.tabs.currentWidget()
    active.open_with_format(first)
    finish_view(active)
    selection = Selection((("label", "keep"),), "label", True)
    active.load_view(first, selection)
    finish_view(active)
    assert active.model.rows == [("2", "keep z"), ("1", "keep a")]
    assert active.model.setData(active.model.index(0, 1), "keep b")
    finish_view(active)

    active.open_with_format(second)
    other = workspace.tabs.currentWidget()
    assert other is not active and workspace.tabs.count() == 2
    finish_view(other)
    assert other.selection == Selection()
    assert other.csv_format == CsvFormat("\t")
    assert other.model.rows == [("9", "other")]
    assert not other.dirty

    other.open_with_format(first)
    assert workspace.tabs.currentWidget() is active and workspace.tabs.count() == 2
    assert active.selection == selection
    assert active.model.rows == [("2", "keep b"), ("1", "keep a")]
    assert active.dirty and workspace.tabs.tabText(0).endswith("*")
    active.undo_action.trigger()
    finish_view(active)
    assert active.model.rows == [("2", "keep z"), ("1", "keep a")]
    assert other.model.rows == [("9", "other")]


def test_close_and_save_as_protect_another_tab(tmp_path, monkeypatch, workspace):
    first, second = (tmp_path / name for name in ("first.csv", "second.csv"))
    for path in (first, second):
        path.write_text("id\n1\n", encoding="utf-8")
        workspace.open_document(path, Selection(), CsvFormat())
        finish_view(workspace.tabs.currentWidget())
    active, other = workspace.documents()
    assert active.model.setData(active.model.index(0, 0), "2")
    if active.view_job is not None:
        finish_view(active)
    assert active.dirty

    warnings = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(second), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    active.save_document(as_copy=True)
    assert warnings and active.save_job is None
    assert second.read_text(encoding="utf-8") == "id\n1\n"

    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    workspace.close_tab(0)
    assert workspace.tabs.count() == 2 and active.dirty
    assert not workspace.close()
    assert workspace.documents() == [active, other]
    assert other.model.rows == [("1",)]
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    workspace.close_tab(0)
    assert workspace.documents() == [other]
    assert other.model.rows == [("1",)]


def test_failed_open_removes_only_the_new_tab(tmp_path, monkeypatch, workspace):
    from concurrent.futures import wait

    first = tmp_path / "first.csv"
    first.write_text("id\n1\n", encoding="utf-8")
    workspace.open_document(first, Selection(), CsvFormat())
    active = workspace.tabs.currentWidget()
    finish_view(active)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    workspace.open_document(tmp_path / "missing.csv", Selection(), CsvFormat())
    pending = workspace.tabs.currentWidget()
    wait([pending.view_job[1]], timeout=5)
    pending.collect_reads()
    assert errors and workspace.documents() == [active]
    assert active.model.rows == [("1",)]


def test_workspace_session_restores_tab_order_active_and_views(tmp_path, monkeypatch, workspace):
    first = tmp_path / "first.csv"
    first.write_text("id;label\n1;keep a\n2;keep z\n3;hide\n", encoding="utf-8")
    second = tmp_path / "second.tsv"
    second.write_text("id\tlabel\n9\tother\n", encoding="utf-8")
    first_fmt, second_fmt = CsvFormat(";"), CsvFormat("\t")
    selection = Selection((("label", "keep"),), "label", True, ("id", "label"))
    for path, view, fmt in ((first, selection, first_fmt), (second, Selection(), second_fmt)):
        workspace.open_document(path, view, fmt)
        finish_view(workspace.tabs.currentWidget())
    workspace.tabs.tabBar().moveTab(1, 0)
    workspace.tabs.setCurrentIndex(1)
    session = tmp_path / "workspace.csvsession"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(session), ""))
    workspace.save_workspace_file()

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(session), ""))
    old = workspace.documents()
    workspace.restore_workspace_file()
    assert workspace.documents() == old
    while workspace.restore_pending is not None:
        finish_view(workspace.restore_pending)
    second_doc, first_doc = workspace.documents()
    assert (second_doc.path, first_doc.path) == (second.resolve(), first.resolve())
    assert workspace.tabs.currentWidget() is first_doc
    assert first_doc.selection == selection
    assert first_doc.csv_format == first_fmt and second_doc.csv_format == second_fmt
    assert first_doc.model.rows == [("2", "keep z"), ("1", "keep a")]
    assert first_doc.model.editable
    assert second_doc.model.rows == [("9", "other")]


def test_failed_workspace_restore_keeps_current_tabs_and_edits(tmp_path, monkeypatch, workspace):
    from concurrent.futures import wait

    path = tmp_path / "existing.csv"
    path.write_text("id\n1\n", encoding="utf-8")
    workspace.open_document(path, Selection(), CsvFormat())
    current = workspace.tabs.currentWidget()
    finish_view(current)
    assert current.model.setData(current.model.index(0, 0), "2")
    assert current.dirty
    tabs = [(path, Selection(), CsvFormat()), (tmp_path / "missing.csv", Selection(), CsvFormat())]
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    workspace.restore_workspace(tabs, 0)
    assert workspace.restore_pending is None

    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    workspace.restore_workspace(tabs, 0)
    finish_view(workspace.restore_pending)
    pending = workspace.restore_pending
    wait([pending.view_job[1]], timeout=5)
    pending.collect_reads()
    assert workspace.restore_pending is None and errors and "missing.csv" in errors[0]
    assert workspace.documents() == [current]
    assert current.model.data(current.model.index(0, 0)) == "2" and current.dirty


def test_heavy_jobs_queue_across_tabs_and_cached_reads_bypass_queue(tmp_path, workspace):
    from concurrent.futures import CancelledError
    from tempfile import TemporaryDirectory
    from threading import Event

    from PySide6.QtCore import QTimer

    for name in ("first.csv", "second.csv"):
        path = tmp_path / name
        path.write_text("id\n1\n", encoding="utf-8")
        workspace.open_document(path, Selection(), CsvFormat())
        document = workspace.tabs.currentWidget()
        finish_view(document)
        document.count_job[1].result(timeout=5)
        document.collect_reads()
    first, second = workspace.documents()
    workspace.tabs.setCurrentWidget(first)
    started, release, queued_started = Event(), Event(), Event()
    ticks = []

    def sorting(*, cancel):
        with TemporaryDirectory(dir=tmp_path):
            started.set()
            while not release.wait(0.01):
                cancel.check()
            cancel.check()

    def unique(*, cancel):
        queued_started.set()

    first.query_job = first.submit(sorting)
    try:
        assert started.wait(5)
        workspace.tabs.setCurrentWidget(second)
        second.query_job = second.submit(unique)
        second.update_status()
        assert "Queued: Running SQL" in second.status.text()
        assert not second.query_job[1].running()
        QTimer.singleShot(0, lambda: ticks.append(workspace.tabs.currentWidget()))
        QApplication.processEvents()
        assert ticks == [second]

        _, cached = second.submit(lambda *, cancel: "cached page", heavy=False)
        assert cached.result(timeout=5) == "cached page"
        second.cancel_operations()
        with pytest.raises(CancelledError):
            second.query_job[1].result(timeout=5)
        second.collect_reads()
        assert not queued_started.is_set() and not first.query_job[0].cancelled
        assert any(path.is_dir() for path in tmp_path.iterdir())

        _, next_job = second.submit(lambda *, cancel: "next")
        first.cancel_operations()
        with pytest.raises(CancelledError):
            first.query_job[1].result(timeout=5)
        assert next_job.result(timeout=5) == "next"
        first.collect_reads()
        assert not any(path.is_dir() for path in tmp_path.iterdir())
        assert workspace.documents() == [first, second]
    finally:
        release.set()
