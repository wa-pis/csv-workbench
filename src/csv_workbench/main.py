import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from csv_workbench.core import Selection, export_csv, prepare_result, read_page
from csv_workbench.keyboard_trace import KeyboardTrace
from csv_workbench.ui import WorkspaceWindow


def main() -> int:
    smoke = sys.argv[1:] == ["--smoke-test"]
    app = QApplication([sys.argv[0], "-platform", "offscreen"] if smoke else sys.argv)
    app.setApplicationName("CSV Workbench")
    if path := os.environ.get("CSV_WORKBENCH_KEY_LOG"):
        try:
            app.keyboard_trace = KeyboardTrace(app, path)
        except OSError as exc:
            print(f"Cannot open keyboard trace: {exc}", file=sys.stderr)
    window = WorkspaceWindow()
    window.resize(1100, 720)
    window.show()
    if smoke:
        app.processEvents()
        with TemporaryDirectory() as scratch:
            source = Path(scratch) / "input.csv"
            target = source.with_name("export.csv")
            label = "Привет, мир! Это проверка русского текста для таблицы"
            source.write_bytes(f'id,label\n001,skip\n002,"{label}"\n'.encode("cp1251"))
            selection = Selection(
                (("label", "ПРИВЕТ"), ("id", "002")), "id", visible_columns=("id",)
            )
            if read_page(source, 0, selection) != (["id"], [("002",)], False):
                return 1
            result = prepare_result(source, selection, distinct="label")
            if result.page(0) != (["Value", "Frequency"], [(label, 1)], False):
                return 1
            if (
                export_csv(source, target, selection) != 1
                or target.read_text(encoding="utf-8") != "id\n002\n"
            ):
                return 1
        window.close()
        return 0
    QTimer.singleShot(0, window.offer_recovery)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
