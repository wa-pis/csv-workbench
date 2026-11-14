import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication

from csv_workbench.core import Selection, export_csv, read_page
from csv_workbench.ui import MainWindow


def main() -> int:
    smoke = sys.argv[1:] == ["--smoke-test"]
    app = QApplication([sys.argv[0], "-platform", "offscreen"] if smoke else sys.argv)
    app.setApplicationName("CSV Workbench")
    window = MainWindow()
    window.resize(1100, 720)
    window.show()
    if smoke:
        app.processEvents()
        with TemporaryDirectory() as scratch:
            source = Path(scratch) / "input.csv"
            target = source.with_name("export.csv")
            source.write_text("id,label\n001,skip\n002,keep\n", encoding="utf-8")
            selection = Selection("label", "keep", "id", visible_columns=("id",))
            if read_page(source, 0, selection) != (["id"], [("002",)], False):
                return 1
            if (
                export_csv(source, target, selection) != 1
                or target.read_text(encoding="utf-8") != "id\n002\n"
            ):
                return 1
        window.close()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
