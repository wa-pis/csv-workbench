"""Qt preview window."""

import json
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from csv_workbench.core import (
    PAGE_SIZE,
    Cancellation,
    Selection,
    count_rows,
    export_csv,
    prepare_result,
    query_csv,
    read_page,
    selection_sql,
)
from csv_workbench.session import load_session, save_session

ROOT_INDEX = QModelIndex()
BUFFER_ROWS = PAGE_SIZE * 3
JOB_NAMES = ("view_job", "count_job", "page_job", "export_job", "query_job")


class PageModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.headers: list[str] = []
        self.rows: list[tuple] = []
        self.offset = 0

    def replace(self, headers: list[str], rows: list[tuple], offset: int = 0) -> None:
        self.beginResetModel()
        self.headers, self.rows, self.offset = headers, rows, offset
        self.endResetModel()

    def rowCount(self, parent=ROOT_INDEX):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=ROOT_INDEX):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        value = self.rows[index.row()][index.column()]
        return "NULL" if value is None else str(value)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return str(self.offset + section + 1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Workbench")
        self.path: Path | None = None
        self.has_more = False
        self.result = None
        self.distinct = None
        self.adjusting = False
        self.file_size = 0
        self.total_rows = None
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.count_job = None
        self.page_job = None
        self.export_job = None
        self.view_job = None
        self.query_job = None
        self.sql_text = "SELECT * FROM csv_data"
        self.poll = QTimer(self, interval=30)
        self.poll.timeout.connect(self.collect_reads)
        self.source_headers: list[str] = []
        self.selection = Selection()
        self.model = PageModel()
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self.open_button = QPushButton("Open CSV")
        self.open_button.clicked.connect(self.open_file)
        layout.addWidget(self.open_button)
        session_menu = self.menuBar().addMenu("Session")
        self.open_session_action = session_menu.addAction("Open session…")
        self.open_session_action.triggered.connect(self.open_session_file)
        self.save_session_action = session_menu.addAction("Save session…")
        self.save_session_action.triggered.connect(self.save_session_file)
        self.save_session_action.setEnabled(False)
        self.selection_button = QPushButton("Filter, sort and columns…")
        self.selection_button.setEnabled(False)
        self.selection_button.clicked.connect(self.edit_selection)
        layout.addWidget(self.selection_button)
        self.sql_button = QPushButton("Preview SQL…")
        self.sql_button.setEnabled(False)
        self.sql_button.clicked.connect(self.preview_sql)
        layout.addWidget(self.sql_button)
        self.query_button = QPushButton("SQL editor…")
        self.query_button.setEnabled(False)
        self.query_button.clicked.connect(self.edit_sql)
        layout.addWidget(self.query_button)
        self.unique_button = QPushButton("Unique values…")
        self.unique_button.setEnabled(False)
        self.unique_button.clicked.connect(self.edit_unique)
        layout.addWidget(self.unique_button)
        self.export_button = QPushButton("Export CSV…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.save_selection)
        layout.addWidget(self.export_button)
        self.cancel_button = QPushButton("Cancel operations")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_operations)
        layout.addWidget(self.cancel_button)

        self.table = QTableView()
        self.table.setModel(self.model)
        layout.addWidget(self.table, 1)

        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalScrollBar().valueChanged.connect(self.load_at_edge)
        self.status = QLabel("Open a CSV file to begin")
        layout.addWidget(self.status)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV (*.csv);;All files (*)")
        if path:
            self.load_view(Path(path), Selection(), new_file=True)

    def open_session_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open session", "", "Session (*.csvsession)"
        )
        if filename:
            try:
                path, selection = load_session(Path(filename))
                self.load_view(path, selection, new_file=True)
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Open session", str(exc))

    def save_session_file(self):
        if self.path is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save session",
            str(self.path.with_suffix(".csvsession")),
            "Session (*.csvsession)",
        )
        if filename:
            destination = Path(filename)
            if not destination.suffix:
                destination = destination.with_suffix(".csvsession")
            try:
                save_session(destination, self.path, self.selection)
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Save session", str(exc))

    def submit(self, operation, *args):
        token = Cancellation()
        future = self.executor.submit(operation, *args, cancel=token)
        return token, future

    def cancel_operations(self):
        for job in self.jobs():
            if job is not None:
                token, future = job
                token.cancel()
                future.cancel()
        self.update_status()

    def save_selection(self):
        if self.path is None or self.export_job is not None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSV",
            str(self.path.with_name(f"{self.path.stem}-selection.csv")),
            "CSV (*.csv)",
        )
        if destination:
            self.export_button.setEnabled(False)
            self.export_job = self.submit(export_csv, self.path, Path(destination), self.selection)
            self.poll.start()
            self.update_status()

    def preview_sql(self):
        sql, values = selection_sql(self.source_headers, self.selection)
        dialog = QDialog(self)
        dialog.setWindowTitle("SQL for current CSV selection")
        dialog.resize(720, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("DuckDB SQL"))
        query = QPlainTextEdit(sql, dialog)
        query.setReadOnly(True)
        layout.addWidget(query)
        layout.addWidget(QLabel("Parameters in order (bind the path to a UTF-8 CSV first)"))
        parameters = QPlainTextEdit(
            json.dumps(["<UTF-8 CSV path>", *values], ensure_ascii=False, indent=2), dialog
        )
        parameters.setReadOnly(True)
        layout.addWidget(parameters)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def edit_sql(self):
        if self.path is None or self.query_job is not None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("SQL editor")
        dialog.resize(720, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Run one SELECT against csv_data. Use WITH for multiple steps."))
        editor = QPlainTextEdit(self.sql_text, dialog)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        run = buttons.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        run.clicked.connect(dialog.accept)
        open_sql = buttons.addButton("Open SQL…", QDialogButtonBox.ButtonRole.ActionRole)
        open_sql.clicked.connect(lambda: self.open_sql_file(editor))
        save_sql = buttons.addButton("Save SQL…", QDialogButtonBox.ButtonRole.ActionRole)
        save_sql.clicked.connect(lambda: self.save_sql_file(editor))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.sql_text = editor.toPlainText()
            self.query_job = self.submit(query_csv, self.path, self.sql_text)
            self.poll.start()
            self.update_status()

    def open_sql_file(self, editor):
        filename, _ = QFileDialog.getOpenFileName(editor, "Open SQL", "", "SQL (*.sql)")
        if filename:
            try:
                editor.setPlainText(Path(filename).read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                QMessageBox.critical(editor, "Open SQL", str(exc))

    def save_sql_file(self, editor):
        filename, _ = QFileDialog.getSaveFileName(editor, "Save SQL", "", "SQL (*.sql)")
        if not filename:
            return
        destination = Path(filename)
        if not destination.suffix:
            destination = destination.with_suffix(".sql")
        try:
            if destination.suffix.lower() != ".sql":
                raise ValueError("Choose a .sql file.")
            if destination.resolve() == self.path.resolve():
                raise ValueError("Choose a destination other than the source CSV.")
            with TemporaryDirectory(dir=destination.parent) as scratch:
                output = Path(scratch) / "pipeline.sql"
                output.write_text(editor.toPlainText(), encoding="utf-8")
                output.replace(destination)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(editor, "Save SQL", str(exc))

    def show_query_result(self, headers, rows, has_more):
        dialog = QDialog(self)
        dialog.setWindowTitle("SQL result")
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(f"{len(rows)} rows shown" + (" • More rows available" if has_more else ""))
        )
        table = QTableView(dialog)
        model = PageModel()
        model.setParent(table)
        model.replace(headers, rows)
        table.setModel(model)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def edit_selection(self):
        if self.path is None:
            return
        if self.distinct is not None:
            self.load_view(self.path, self.selection)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Filter, sort and columns")
        form = QFormLayout(dialog)
        current = self.selection

        mode = QComboBox()
        mode.addItems(["All conditions (AND)", "Any condition (OR)"])
        mode.setCurrentIndex(int(current.match_any))
        form.addRow("Match", mode)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        filter_widget = QWidget()
        filter_rows = QVBoxLayout(filter_widget)
        scroll.setWidget(filter_widget)
        form.addRow("Filters", scroll)
        conditions = []

        def add_condition(column=None, value=""):
            row = QWidget()
            widgets = QHBoxLayout(row)
            field = QComboBox()
            field.addItems(self.source_headers)
            if column is not None:
                field.setCurrentText(column)
            substring = QLineEdit(value)
            substring.setPlaceholderText("Contains")
            remove = QPushButton("Remove")
            widgets.addWidget(field)
            widgets.addWidget(substring)
            widgets.addWidget(remove)
            condition = field, substring
            conditions.append(condition)
            filter_rows.addWidget(row)

            def remove_condition():
                conditions.remove(condition)
                filter_rows.removeWidget(row)
                row.hide()
                row.deleteLater()

            remove.clicked.connect(remove_condition)

        for column, value in current.conditions or ((None, ""),):
            add_condition(column, value)
        add = QPushButton("Add condition")
        add.clicked.connect(lambda: add_condition())
        form.addRow(add)

        sort_column = QComboBox()
        sort_column.addItem("None", None)
        for header in self.source_headers:
            sort_column.addItem(header, header)
        if current.sort_column is not None:
            sort_column.setCurrentIndex(sort_column.findData(current.sort_column))

        direction = QComboBox()
        direction.addItems(["Ascending", "Descending"])
        direction.setCurrentIndex(int(current.descending))
        columns = QListWidget()
        for header in self.source_headers:
            item = QListWidgetItem(header, columns)
            visible = current.visible_columns is None or header in current.visible_columns
            item.setCheckState(Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)

        form.addRow("Sort column", sort_column)
        form.addRow("Direction", direction)
        form.addRow("Visible columns", columns)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Reset
            | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        def apply():
            visible = tuple(
                item.text()
                for i in range(columns.count())
                if (item := columns.item(i)).checkState() == Qt.CheckState.Checked
            )
            selection = Selection(
                tuple(
                    (field.currentText(), value.text())
                    for field, value in conditions
                    if value.text()
                ),
                sort_column.currentData(),
                direction.currentIndex() == 1,
                visible if len(visible) != columns.count() else None,
                match_any=mode.currentIndex() == 1,
            )
            self.load_view(self.path, selection)
            dialog.accept()

        def reset():
            self.load_view(self.path, Selection())
            dialog.accept()

        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(reset)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def edit_unique(self):
        if self.path is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Unique values")
        form = QFormLayout(dialog)
        column = QComboBox()
        column.addItems(self.source_headers)
        once = QCheckBox("Only values occurring once")
        form.addRow("Column", column)
        form.addRow(once)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.load_view(
                self.path,
                self.selection,
                distinct=column.currentText(),
                singletons=once.isChecked(),
            )

    def load_view(
        self, path: Path, selection: Selection, *, new_file=False, distinct=None, singletons=False
    ):
        if self.view_job is not None:
            return

        def read_view(*, cancel):
            size = path.stat().st_size
            source_headers = None
            if new_file and selection.visible_columns is not None:
                source_headers = read_page(path, 0, cancel=cancel)[0]
            if distinct is not None or selection.sort_column is not None:
                result = prepare_result(
                    path, selection, distinct=distinct, singletons=singletons, cancel=cancel
                )
                return size, result.page(0, cancel=cancel), result, source_headers
            return size, read_page(path, 0, selection, cancel=cancel), None, source_headers

        self.view_request = path, selection, new_file, distinct
        self.view_job = self.submit(read_view)
        self.poll.start()
        self.update_status()

    def apply_view(self, size, page, result, source_headers):
        path, selection, new_file, distinct = self.view_request
        headers, rows, has_more = page
        if self.page_job is not None:
            self.page_job[0].cancel()
            self.page_job[1].cancel()
            self.page_job = None
        if new_file:
            if self.count_job is not None:
                self.count_job[0].cancel()
                self.count_job[1].cancel()
            self.total_rows = None
            self.count_job = self.submit(count_rows, path)
            self.source_headers = source_headers if source_headers is not None else headers

        self.path, self.selection, self.file_size = path, selection, size
        self.result, self.distinct = result, distinct
        self.has_more = False
        self.model.replace(headers, rows)
        self.table.scrollToTop()
        self.has_more = has_more
        self.setWindowTitle(f"CSV Workbench — {path.name}")

    def load_at_edge(self, value: int):
        if self.adjusting or self.path is None or self.view_job or self.page_job:
            return
        bar = self.table.verticalScrollBar()
        start = self.model.offset
        if value == bar.maximum() and self.has_more:
            offset = start + len(self.model.rows)
        elif value == 0 and start:
            offset = start - PAGE_SIZE
        else:
            return
        self.page_offset = offset
        if self.result is None:
            self.page_job = self.submit(read_page, self.path, offset, self.selection)
        else:
            self.page_job = self.submit(self.result.page, offset)
        self.poll.start()
        self.update_status()

    def apply_page(self, headers, rows, has_more):
        if headers != self.model.headers or not rows:
            raise ValueError("CSV changed. Reopen the file to refresh it.")
        start = self.model.offset
        bar = self.table.verticalScrollBar()
        height = self.table.verticalHeader().defaultSectionSize()
        position = bar.value()
        if self.page_offset < start:
            combined = rows + self.model.rows
            trimmed = len(combined) > BUFFER_ROWS
            start = self.page_offset
            position += len(rows) * height
            self.has_more = self.has_more or trimmed
            combined = combined[:BUFFER_ROWS]
        else:
            combined = self.model.rows + rows
            removed = PAGE_SIZE if len(combined) > BUFFER_ROWS else 0
            start += removed
            position -= removed * height
            self.has_more = has_more
            combined = combined[removed:]
        self.adjusting = True
        self.model.replace(headers, combined, start)
        self.table.doItemsLayout()
        bar.setValue(position)
        self.adjusting = False

    def jobs(self):
        return tuple(getattr(self, name) for name in JOB_NAMES)

    def collect_reads(self):
        for name in JOB_NAMES:
            job = getattr(self, name)
            if job is None or not job[1].done():
                continue
            token, future = job
            setattr(self, name, None)
            try:
                result = future.result()
                if name != "export_job":
                    token.check()
                if name == "view_job":
                    self.apply_view(*result)
                elif name == "count_job":
                    self.total_rows = result
                elif name == "page_job":
                    self.apply_page(*result)
                elif name == "query_job":
                    self.show_query_result(*result)
                else:
                    QMessageBox.information(self, "Export CSV", f"Exported {result:,} rows.")
            except CancelledError:
                if name == "count_job":
                    self.total_rows = "cancelled"
            except (duckdb.Error, OSError, ValueError) as exc:
                if name == "count_job":
                    self.total_rows = "unavailable"
                else:
                    if name == "page_job":
                        self.has_more = False
                    QMessageBox.critical(self, "CSV Workbench", str(exc))
        self.update_status()
        if not self.cancel_button.isEnabled():
            self.poll.stop()

    def update_status(self):
        jobs = self.jobs()
        self.cancel_button.setEnabled(any(job is not None for job in jobs))
        self.open_button.setEnabled(self.view_job is None and self.query_job is None)
        self.open_session_action.setEnabled(self.open_button.isEnabled())
        self.save_session_action.setEnabled(self.path is not None and self.view_job is None)
        self.query_button.setEnabled(
            self.path is not None and self.view_job is None and self.query_job is None
        )
        self.selection_button.setEnabled(self.path is not None and self.view_job is None)
        self.unique_button.setEnabled(self.path is not None and self.view_job is None)
        self.sql_button.setEnabled(
            self.path is not None and self.view_job is None and self.distinct is None
        )
        self.selection_button.setText(
            "Back to CSV selection" if self.distinct is not None else "Filter, sort and columns…"
        )
        self.export_button.setEnabled(
            self.path is not None
            and self.view_job is None
            and self.export_job is None
            and self.distinct is None
        )
        total = self.total_rows
        if isinstance(total, int):
            total = f"{total:,}"
        progress = " • Loading more…" if self.page_job is not None else ""
        if self.view_job is not None:
            progress += " • Reading CSV…"
        if any(job is not None and job[0].cancelled for job in jobs):
            progress += " • Cancelling…"
        if self.export_job is not None:
            progress += " • Exporting CSV…"
        if self.query_job is not None:
            progress += " • Running SQL…"
        if self.distinct is not None:
            progress += f" • Unique: {self.distinct}"
        self.status.setText(
            f"File: {self.file_size:,} bytes • Total rows: {total if total is not None else 'counting…'}"
            f" • Buffered: {len(self.model.rows):,}{progress}"
        )

    def closeEvent(self, event):
        self.cancel_operations()
        self.poll.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.result = None
        self.view_job = None
        self.page_job = None
        super().closeEvent(event)
