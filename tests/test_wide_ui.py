"""Filtered wide views retain a disk snapshot for subsequent UI pages."""

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from csv_workbench import core, ui
from csv_workbench.wide import WideResult


def test_filtered_wide_view_caches_pages_and_preserves_source_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "WIDE_CSV_COLUMNS", 3)
    monkeypatch.setattr(ui, "WIDE_CSV_COLUMNS", 3)
    monkeypatch.setattr(ui.MainWindow, "recovery_root", property(lambda _: tmp_path / "recovery"))
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat,
    ))
    app = QApplication.instance() or QApplication(["wide-test", "-platform", "offscreen"])
    source = tmp_path / "wide.csv"
    source.write_text("id,text,group,payload\n" + "".join(
        f"{i:05},value{i},keep,x\n" for i in range(405)
    ))
    window = ui.MainWindow()
    try:
        window.load_view(source, core.Selection(filters=(("group", "keep"),)), new_file=True)
        window.view_job[1].result(timeout=10)
        window.collect_reads()
        if window.count_job is not None:
            window.count_job[1].result(timeout=10)
            window.collect_reads()
        assert isinstance(window.result, WideResult)
        assert window.source_headers == ["id", "text", "group", "payload"]
        assert window.model.row_ids == list(range(200))
        assert window.model.rows[0] == ("00000", "value0", "keep", "x")
        snapshot = window.result

        # A cached view can page its captured values even if the source disappears.
        source.unlink()
        window.load_page(200)
        window.page_job[1].result(timeout=10)
        window.collect_reads()
        assert window.result is snapshot
        assert window.model.row_ids == list(range(400))
        assert window.model.rows[-1] == ("00399", "value399", "keep", "x")
    finally:
        window.close()
        window.executor.shutdown(wait=True)
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
