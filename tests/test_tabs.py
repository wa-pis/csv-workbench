import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
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
    for document in documents:
        document.executor.shutdown(wait=True)
    window.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def finish_view(document):
    document.view_job[1].result(timeout=5)
    document.collect_reads()


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
