"""Qt preview window."""

import json
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
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
    export_edited_csv,
    file_revision,
    prepare_editable_result,
    prepare_result,
    query_csv,
    read_editable_page,
    read_page,
    save_csv,
    selection_sql,
)
from csv_workbench.session import load_session, save_session

ROOT_INDEX = QModelIndex()
BUFFER_ROWS = PAGE_SIZE * 3
JOB_NAMES = ("view_job", "count_job", "page_job", "export_job", "query_job", "save_job")


class PageModel(QAbstractTableModel):
    cellEdited = Signal(tuple, object, object)

    def __init__(self):
        super().__init__()
        self.headers: list[str] = []
        self.rows: list[tuple] = []
        self.offset = 0
        self.filtered_columns: set[str] = set()
        self.edits: dict[tuple[int, int], str | None] = {}
        self.editable = False
        self.row_ids = []
        self.source_columns = []

    def replace(self, headers: list[str], rows: list[tuple], offset: int = 0, row_ids=()) -> None:
        self.beginResetModel()
        self.headers, self.rows, self.offset = headers, rows, offset
        self.row_ids = list(row_ids)
        self.endResetModel()

    def cell_key(self, index):
        row, col = index.row(), index.column()
        if self.row_ids:
            return self.row_ids[row], self.source_columns.index(self.headers[col])
        return self.offset + row, col

    def rowCount(self, parent=ROOT_INDEX):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=ROOT_INDEX):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.EditRole,
        ):
            return None
        value = self.edits.get(self.cell_key(index), self.rows[index.row()][index.column()])
        if role == Qt.ItemDataRole.EditRole:
            return "" if value is None else str(value)
        return "NULL" if value is None else str(value)

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsEditable if self.editable and index.isValid() else flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not self.editable or not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        if value != self.data(index, role):
            key = self.cell_key(index)
            before = self.edits.get(key, self.rows[index.row()][index.column()])
            after = value or None
            self.edits[key] = after
            self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
            self.cellEdited.emit(key, before, after)
        return True

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            name = self.headers[section]
            return f"{name}  •" if name in self.filtered_columns else name
        return str(self.offset + section + 1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Workbench")
        self.resize(960, 640)
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
        self.save_job = None
        self.view_job = None
        self.query_job = None
        self.sql_text = "SELECT * FROM csv_data"
        self.poll = QTimer(self, interval=30)
        self.poll.timeout.connect(self.collect_reads)
        self.source_headers: list[str] = []
        self.selection = Selection()
        self.model = PageModel()
        self.history = []
        self.history_pos = self.clean_pos = 0
        self.pending_history = None
        self.model.cellEdited.connect(self.record_edit)
        self.applied_edits = {}
        self.refreshing_edit = False
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 3, 6, 3)
        toolbar.setSpacing(4)
        layout.addLayout(toolbar)

        self.open_button = QPushButton("Open CSV")
        self.open_button.clicked.connect(self.open_file)
        toolbar.addWidget(self.open_button)
        file_menu = self.menuBar().addMenu("File")
        self.save_action = file_menu.addAction("Save", self.save_document)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_as_action = file_menu.addAction(
            "Save As…", lambda: self.save_document(as_copy=True)
        )
        self.save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        edit_menu = self.menuBar().addMenu("Edit")
        self.undo_action = edit_menu.addAction("Undo", lambda: self.move_history(-1))
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = edit_menu.addAction("Redo", lambda: self.move_history(1))
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        for action in (self.save_action, self.save_as_action, self.undo_action, self.redo_action):
            action.setEnabled(False)
        session_menu = self.menuBar().addMenu("Session")
        self.open_session_action = session_menu.addAction("Open session…")
        self.open_session_action.triggered.connect(self.open_session_file)
        self.save_session_action = session_menu.addAction("Save session…")
        self.save_session_action.triggered.connect(self.save_session_file)
        self.save_session_action.setEnabled(False)
        self.selection_button = QPushButton("Selection…")
        self.selection_button.setEnabled(False)
        self.selection_button.clicked.connect(self.edit_selection)
        toolbar.addWidget(self.selection_button)
        self.sql_button = QPushButton("Preview SQL…")
        self.sql_button.setEnabled(False)
        self.sql_button.clicked.connect(self.preview_sql)
        toolbar.addWidget(self.sql_button)
        self.query_button = QPushButton("SQL editor…")
        self.query_button.setEnabled(False)
        self.query_button.clicked.connect(self.edit_sql)
        toolbar.addWidget(self.query_button)
        self.unique_button = QPushButton("Unique values…")
        self.unique_button.setEnabled(False)
        self.unique_button.clicked.connect(self.edit_unique)
        toolbar.addWidget(self.unique_button)
        toolbar.addStretch()
        self.export_button = QPushButton("Export CSV…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.save_selection)
        toolbar.addWidget(self.export_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Cancel running operations")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_operations)
        toolbar.addWidget(self.cancel_button)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(23)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(True)
        header.sectionClicked.connect(self.open_column_menu)
        layout.addWidget(self.table, 1)

        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalScrollBar().valueChanged.connect(self.load_at_edge)
        self.status = QLabel("Open a CSV file to begin")
        self.statusBar().addWidget(self.status, 1)

    def open_column_menu(self, section):
        if self.path is None or self.view_job is not None or self.distinct is not None:
            return
        column = self.model.headers[section]
        current = self.selection
        menu = QMenu(self)
        asc = menu.addAction("Sort ascending")
        desc = menu.addAction("Sort descending")
        unsort = menu.addAction("Clear sort")
        unsort.setEnabled(current.sort_column == column)
        menu.addSeparator()
        add_filter = menu.addAction("Add contains filter…")
        clear_filter = menu.addAction("Clear filters for this column")
        remaining = tuple(pair for pair in current.filters if pair[0] != column)
        clear_filter.setEnabled(any(name == column for name, _ in current.conditions))
        header = self.table.horizontalHeader()
        action = menu.exec(
            header.mapToGlobal(QPoint(header.sectionViewportPosition(section), header.height()))
        )
        if action in (asc, desc):
            selection = replace(current, sort_column=column, descending=action == desc)
        elif action == unsort:
            selection = replace(current, sort_column=None, descending=False)
        elif action == add_filter:
            value, accepted = QInputDialog.getText(self, "Column filter", f"{column} contains:")
            if not accepted or not value:
                return
            selection = replace(current, filters=(*current.filters, (column, value)))
        elif action == clear_filter:
            selection = replace(current, filters=remaining)
        else:
            return
        self.load_view(self.path, selection)

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

    def save_document(self, *, as_copy=False):
        if self.path is None or self.view_job or self.save_job or self.export_job:
            return
        destination = self.path
        if as_copy:
            name, _ = QFileDialog.getSaveFileName(
                self,
                "Save As",
                str(self.path.with_name(f"{self.path.stem}-copy.csv")),
                "CSV (*.csv)",
            )
            if not name:
                return
            destination = Path(name)
            if not destination.suffix:
                destination = destination.with_suffix(".csv")
        elif not self.dirty:
            return
        if self.page_job is not None:
            self.page_job[0].cancel()
            self.page_job[1].cancel()
            self.page_job = None
        self.save_job = self.submit(
            save_csv, self.path, destination, self.model.edits.copy(), self.revision
        )
        self.poll.start()
        self.update_status()

    @property
    def dirty(self):
        return self.history_pos != self.clean_pos

    def record_edit(self, key, before, after):
        self.refresh_after_edit(("edit", (key, before, after)))

    def move_history(self, step):
        target = self.history_pos + step
        if not self.model.editable or not 0 <= target <= len(self.history):
            return
        key, before, after = self.history[min(target, self.history_pos)]
        self.model.edits[key] = before if step < 0 else after
        self.refresh_after_edit(("move", target))

    def commit_history(self):
        if self.pending_history is None:
            return
        operation, value = self.pending_history
        self.pending_history = None
        if operation == "edit":
            if self.clean_pos > self.history_pos:
                self.clean_pos = -1
            del self.history[self.history_pos :]
            self.history.append(value)
            self.history_pos += 1
        else:
            self.history_pos = value

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
            self.export_edits = self.model.edits.copy()
            if self.export_edits:
                self.export_job = self.submit(
                    export_edited_csv,
                    self.path,
                    Path(destination),
                    self.export_edits,
                    self.revision,
                    self.selection,
                )
            else:
                self.export_job = self.submit(
                    export_csv, self.path, Path(destination), self.selection
                )
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

    def refresh_after_edit(self, history):
        self.pending_history = history
        if self.selection != Selection():
            self.load_view(self.path, self.selection, refreshing_edit=True)
        else:
            self.applied_edits = self.model.edits.copy()
            self.commit_history()
            if history[0] == "move":
                self.model.replace(
                    self.model.headers, self.model.rows, self.model.offset, self.model.row_ids
                )
            self.update_status()

    def rollback_edit(self):
        self.pending_history = None
        self.model.edits = self.applied_edits.copy()
        self.model.replace(
            self.model.headers, self.model.rows, self.model.offset, self.model.row_ids
        )

    def load_view(
        self,
        path: Path,
        selection: Selection,
        *,
        new_file=False,
        distinct=None,
        singletons=False,
        refreshing_edit=False,
    ):
        if (
            self.view_job is not None
            or self.save_job is not None
            or (self.export_job is not None and self.export_edits)
            or (new_file and not self.confirm_discard_edits())
        ):
            return

        edits = {} if new_file else self.model.edits.copy()

        def read_view(*, cancel):
            revision = file_revision(path)
            if not new_file and revision != self.revision:
                raise ValueError("The source CSV changed. Reopen it before editing.")
            source_headers = None
            if new_file and selection.visible_columns is not None:
                source_headers = read_page(path, 0, cancel=cancel)[0]
            if distinct is not None and edits:
                result = prepare_editable_result(
                    path,
                    replace(selection, visible_columns=(distinct,), sort_column=None),
                    edits,
                    distinct=distinct,
                    singletons=singletons,
                    cancel=cancel,
                )
                return revision, result.page(0, cancel=cancel), result, source_headers
            if distinct is not None:
                result = prepare_result(
                    path, selection, distinct=distinct, singletons=singletons, cancel=cancel
                )
                return revision, result.page(0, cancel=cancel), result, source_headers
            if selection.sort_column is not None:
                result = prepare_editable_result(path, selection, edits, cancel=cancel)
                page = result.page(0, cancel=cancel)
                if file_revision(path) != revision:
                    raise ValueError("The source CSV changed. Reopen it before editing.")
                return revision, page, result, source_headers
            if selection != Selection():
                page = read_editable_page(path, 0, selection, edits, cancel=cancel)
            else:
                page = read_page(path, 0, selection, cancel=cancel)
            if file_revision(path) != revision:
                raise ValueError("The source CSV changed. Reopen it before editing.")
            return revision, page, None, source_headers

        self.refreshing_edit = refreshing_edit
        self.view_request = path, selection, new_file, distinct
        self.view_job = self.submit(read_view)
        self.poll.start()
        self.update_status()

    def apply_view(self, revision, page, result, source_headers):
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
            self.source_headers = (
                source_headers
                if source_headers is not None
                else (headers[1:] if selection != Selection() else headers)
            )

        self.path, self.selection, self.file_size = path, selection, revision[2]
        self.revision = revision
        if new_file:
            self.model.edits.clear()
            self.history.clear()
            self.history_pos = self.clean_pos = 0
        self.commit_history()
        self.applied_edits = self.model.edits.copy()
        self.model.source_columns = self.source_headers
        self.result, self.distinct = result, distinct
        self.has_more = False
        self.model.filtered_columns = (
            {name for name, _ in selection.conditions} if distinct is None else set()
        )
        ids = []
        if distinct is None and selection != Selection():
            headers, rows, ids = self.split_row_ids(headers, rows)
        self.model.replace(headers, rows, row_ids=ids)
        header = self.table.horizontalHeader()
        if distinct is None and selection.sort_column in headers:
            header.setSortIndicator(
                headers.index(selection.sort_column),
                Qt.SortOrder.DescendingOrder
                if selection.descending
                else Qt.SortOrder.AscendingOrder,
            )
            header.setSortIndicatorShown(True)
        else:
            header.setSortIndicatorShown(False)
        self.table.scrollToTop()
        self.has_more = has_more
        self.setWindowTitle(f"CSV Workbench — {path.name}")

    def load_at_edge(self, value: int):
        if self.adjusting or self.path is None or self.view_job or self.page_job or self.save_job:
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
            path, revision, selection = self.path, self.revision, self.selection
            edits = self.model.edits.copy() if selection != Selection() else {}

            def read_current_page(*, cancel):
                if file_revision(path) != revision:
                    raise ValueError("The source CSV changed. Reopen it before editing.")
                page = (
                    read_page(path, offset, selection, cancel=cancel)
                    if selection == Selection()
                    else read_editable_page(path, offset, selection, edits, cancel=cancel)
                )
                if file_revision(path) != revision:
                    raise ValueError("The source CSV changed. Reopen it before editing.")
                return page

            self.page_job = self.submit(read_current_page)
        else:
            self.page_job = self.submit(self.result.page, offset)
        self.poll.start()
        self.update_status()

    @staticmethod
    def split_row_ids(headers, rows):
        return headers[1:], [row[1:] for row in rows], [row[0] for row in rows]

    def apply_page(self, headers, rows, has_more):
        ids = []
        if self.distinct is None and self.selection != Selection():
            headers, rows, ids = self.split_row_ids(headers, rows)
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
            ids = (ids + self.model.row_ids)[:BUFFER_ROWS]
        else:
            combined = self.model.rows + rows
            removed = PAGE_SIZE if len(combined) > BUFFER_ROWS else 0
            start += removed
            position -= removed * height
            self.has_more = has_more
            combined = combined[removed:]
            ids = (self.model.row_ids + ids)[removed:]
        self.adjusting = True
        self.model.replace(headers, combined, start, ids)
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
                if name not in ("export_job", "save_job"):
                    token.check()
                if name == "view_job":
                    self.apply_view(*result)
                elif name == "count_job":
                    self.total_rows = result
                elif name == "page_job":
                    self.apply_page(*result)
                elif name == "query_job":
                    self.show_query_result(*result)
                elif name == "save_job":
                    self.path, self.revision = result
                    self.file_size = self.revision[2]
                    self.clean_pos = self.history_pos
                    self.setWindowTitle(f"CSV Workbench — {self.path.name}")
                else:
                    QMessageBox.information(self, "Export CSV", f"Exported {result:,} rows.")
            except CancelledError:
                if name == "view_job" and self.refreshing_edit:
                    self.rollback_edit()
                if name == "count_job":
                    self.total_rows = "cancelled"
            except (duckdb.Error, OSError, ValueError) as exc:
                if name == "view_job" and self.refreshing_edit:
                    self.rollback_edit()
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
        available = self.path is not None and not (
            self.view_job or self.save_job or self.export_job
        )
        self.save_action.setEnabled(available and self.dirty)
        self.save_as_action.setEnabled(available)
        self.undo_action.setEnabled(available and self.distinct is None and self.history_pos > 0)
        self.redo_action.setEnabled(
            available and self.distinct is None and self.history_pos < len(self.history)
        )
        self.model.editable = (
            self.path is not None
            and self.distinct is None
            and self.view_job is None
            and self.export_job is None
            and self.save_job is None
        )
        self.cancel_button.setEnabled(any(job is not None for job in jobs))
        self.open_button.setEnabled(
            self.view_job is None and self.query_job is None and self.save_job is None
        )
        self.open_session_action.setEnabled(self.open_button.isEnabled())
        self.save_session_action.setEnabled(self.path is not None and self.view_job is None)
        self.query_button.setEnabled(
            self.path is not None
            and self.view_job is None
            and self.query_job is None
            and self.save_job is None
            and not self.dirty
        )
        self.selection_button.setEnabled(self.path is not None and self.view_job is None)
        self.unique_button.setEnabled(self.path is not None and self.view_job is None)
        self.sql_button.setEnabled(
            self.path is not None and self.view_job is None and self.distinct is None
        )
        self.selection_button.setText("Back to CSV" if self.distinct is not None else "Selection…")
        self.export_button.setEnabled(
            self.path is not None
            and self.view_job is None
            and self.export_job is None
            and self.save_job is None
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
        if self.save_job is not None:
            progress += " • Saving CSV…"
        if self.dirty:
            progress += " • Unsaved edits"
        self.status.setText(
            f"File: {self.file_size:,} bytes • Total rows: {total if total is not None else 'counting…'}"
            f" • Buffered: {len(self.model.rows):,}{progress}"
        )

    def confirm_discard_edits(self):
        if not self.dirty and self.pending_history is None:
            return True
        return (
            QMessageBox.question(self, "Unsaved edits", "Discard unsaved changes to the CSV?")
            == QMessageBox.StandardButton.Yes
        )

    def closeEvent(self, event):
        if not self.confirm_discard_edits():
            event.ignore()
            return
        self.cancel_operations()
        self.poll.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.result = None
        self.view_job = None
        self.page_job = None
        super().closeEvent(event)
