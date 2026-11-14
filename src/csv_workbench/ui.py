"""Qt preview window."""

import csv
import io
import json
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPoint,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
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
    QStyle,
    QStyleOptionButton,
    QTableView,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from csv_workbench.core import (
    DELETED_COLUMN,
    NEW_ROW,
    PAGE_SIZE,
    Cancellation,
    CsvFormat,
    Selection,
    count_rows,
    detect_delimiter,
    export_csv,
    export_edited_csv,
    file_revision,
    prepare_editable_result,
    prepare_result,
    preview_format,
    query_csv,
    read_editable_page,
    read_format_sample,
    read_page,
    save_csv,
    selection_sql,
)
from csv_workbench.session import load_session, save_session

MISSING = object()
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


class CsvHeader(QHeaderView):
    sortRequested = Signal(int, bool)
    filterRequested = Signal(int)
    button_width = 24

    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.sort_keys = ()
        self.filtered = set()
        self.controls_enabled = False
        self.pressed_control = None
        self.setMinimumSectionSize(90)

    def control_rects(self, rect):
        width = self.button_width
        return tuple(
            QRect(rect.right() - width * n + 1, rect.top() + 2, width, rect.height() - 4)
            for n in (2, 1)
        )

    def paintSection(self, painter, rect, logical_index):
        if not self.controls_enabled:
            super().paintSection(painter, rect, logical_index)
            return
        sort, filters = self.control_rects(rect)
        painter.save()
        painter.setClipRect(rect.adjusted(0, 0, -self.button_width * 2, 0))
        super().paintSection(painter, rect, logical_index)
        painter.restore()
        column = self.model().headers[logical_index]
        arrow = "↕"
        for index, (name, descending) in enumerate(self.sort_keys, 1):
            if name == column:
                arrow = ("↓" if descending else "↑") + (
                    str(index) if len(self.sort_keys) > 1 else ""
                )
                break
        for bounds, label in ((sort, arrow), (filters, "●" if column in self.filtered else "F")):
            option = QStyleOptionButton()
            option.initFrom(self)
            option.rect = bounds
            option.text = label
            self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)

    def control_at(self, point):
        section = self.logicalIndexAt(point)
        if not self.controls_enabled or section < 0:
            return None
        rect = QRect(
            self.sectionViewportPosition(section), 0, self.sectionSize(section), self.height()
        )
        for control, bounds in enumerate(self.control_rects(rect)):
            if bounds.contains(point):
                return section, control
        return None

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            hit = self.control_at(event.pos())
            if hit is not None:
                section, control = hit
                column = self.model().headers[section]
                tip = (
                    f"Sort {column} (Alt: keep other keys)" if control == 0 else f"Filter {column}"
                )
                QToolTip.showText(event.globalPos(), tip, self.viewport())
                return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed_control = self.control_at(event.position().toPoint())
            if self.pressed_control is not None:
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.pressed_control is None:
            super().mouseReleaseEvent(event)
            return
        pressed, self.pressed_control = self.pressed_control, None
        if pressed != self.control_at(event.position().toPoint()):
            return
        section, control = pressed
        if control == 0:
            self.sortRequested.emit(
                section, bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            )
        else:
            self.filterRequested.emit(section)


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
        self.csv_format = CsvFormat()
        self.model = PageModel()
        self.history = []
        self.history_pos = self.clean_pos = 0
        self.pending_history = None
        self.source_snapshot = None
        self.next_row_id = NEW_ROW
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
        self.copy_action = edit_menu.addAction("Copy range", self.copy_range)
        self.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.paste_action = edit_menu.addAction("Paste range", self.paste_range)
        self.paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self.clear_action = edit_menu.addAction("Clear cells", self.clear_cells)
        self.clear_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.add_row_action = edit_menu.addAction("Add row", lambda: self.add_rows())
        self.clone_action = edit_menu.addAction("Duplicate rows", lambda: self.add_rows(clone=True))
        self.delete_action = edit_menu.addAction("Delete rows", self.delete_rows)
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
        header = CsvHeader(self.table)
        self.table.setHorizontalHeader(header)
        header.setSectionsClickable(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(True)
        header.sortRequested.connect(self.cycle_column_sort)
        header.filterRequested.connect(self.open_column_menu)
        layout.addWidget(self.table, 1)

        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalScrollBar().valueChanged.connect(self.load_at_edge)
        self.status = QLabel("Open a CSV file to begin")
        self.statusBar().addWidget(self.status, 1)
        self.update_edit_actions()

    def cycle_column_sort(self, section, append):
        if self.path is not None and self.view_job is None and self.distinct is None:
            self.load_view(
                self.path, self.selection.cycle_sort(self.model.headers[section], append=append)
            )

    def open_column_menu(self, section):
        if self.path is None or self.view_job is not None or self.distinct is not None:
            return
        column = self.model.headers[section]
        current = self.selection
        menu = QMenu(self)
        add_filter = menu.addAction("Add contains filter…")
        clear_filter = menu.addAction("Clear filters for this column")
        remaining = tuple(pair for pair in current.filters if pair[0] != column)
        clear_filter.setEnabled(any(name == column for name, _ in current.conditions))
        header = self.table.horizontalHeader()
        action = menu.exec(
            header.mapToGlobal(QPoint(header.sectionViewportPosition(section), header.height()))
        )
        if action == add_filter:
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
        name, _ = QFileDialog.getOpenFileName(
            self, "Open CSV / TSV", "", "Delimited files (*.csv *.tsv);;All files (*)"
        )
        if name:
            path = Path(name)
            try:
                fmt = self.configure_format(path)
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Open CSV", str(exc))
                return
            if fmt is not None:
                self.load_view(path, Selection(), new_file=True, fmt=fmt)

    def configure_format(self, path):
        sample = read_format_sample(path)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"File format — {path.name}")
        dialog.resize(680, 350)
        form = QFormLayout(dialog)
        delimiter = QComboBox()
        for label, value in (("Comma (,)", ","), ("Semicolon (;)", ";"), ("Tab (TSV)", "\t")):
            delimiter.addItem(label, value)
        delimiter.addItem("Custom…")
        custom = QLineEdit()
        custom.setPlaceholderText("One ASCII character, e.g. |")
        detected = detect_delimiter(sample, "\t" if path.suffix.lower() == ".tsv" else ",")
        delimiter.setCurrentIndex(delimiter.findData(detected))
        header = QCheckBox("First record contains column names")
        header.setChecked(True)
        form.addRow("Delimiter", delimiter)
        form.addRow("Custom delimiter", custom)
        form.addRow(header)
        table = QTableView(dialog)
        model = PageModel()
        model.setParent(table)
        table.setModel(model)
        form.addRow(table)
        status = QLabel()
        status.setWordWrap(True)
        form.addRow(status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def current_format():
            value = delimiter.currentData()
            return CsvFormat(custom.text() if value is None else value, header.isChecked())

        def refresh():
            form.setRowVisible(custom, delimiter.currentData() is None)
            open_button = buttons.button(QDialogButtonBox.StandardButton.Open)
            try:
                fmt = current_format()
            except ValueError as exc:
                open_button.setEnabled(False)
                model.replace([], [])
                status.setText(str(exc))
                return
            open_button.setEnabled(True)
            try:
                headers, rows = preview_format(sample, fmt)
                width = max([len(headers), *(len(row) for row in rows)])
                headers += [""] * (width - len(headers))
                model.replace(headers, [tuple(row + [""] * (width - len(row))) for row in rows])
                status.setText(
                    "Preview: up to 10 records"
                    if headers
                    else "No complete records in the preview sample."
                )
            except (csv.Error, ValueError) as exc:
                model.replace([], [])
                status.setText(str(exc))

        delimiter.currentIndexChanged.connect(refresh)
        custom.textChanged.connect(refresh)
        header.toggled.connect(refresh)
        refresh()
        return current_format() if dialog.exec() else None

    def open_session_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open session", "", "Session (*.csvsession)"
        )
        if filename:
            try:
                path, selection, fmt = load_session(Path(filename))
                self.load_view(path, selection, new_file=True, fmt=fmt)
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
                save_session(destination, self.path, self.selection, self.csv_format)
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Save session", str(exc))

    def submit(self, operation, *args, **kwargs):
        token = Cancellation()
        future = self.executor.submit(operation, *args, cancel=token, **kwargs)
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
                str(self.path.with_name(f"{self.path.stem}-copy{self.path.suffix or '.csv'}")),
                "CSV (*.csv)",
            )
            if not name:
                return
            destination = Path(name)
            if not destination.suffix:
                destination = destination.with_suffix(
                    ".tsv" if self.csv_format.delimiter == "\t" else ".csv"
                )
        elif not self.dirty:
            return
        if self.page_job is not None:
            self.page_job[0].cancel()
            self.page_job[1].cancel()
            self.page_job = None
        path, revision, fmt = self.path, self.revision, self.csv_format
        edits = self.model.edits.copy()
        snapshot = self.source_snapshot
        keep_base = self.structural(edits) or any(
            row >= NEW_ROW or col == DELETED_COLUMN
            for entry in self.history
            for (row, col), _, _ in entry
        )

        def save(*, cancel):
            if snapshot is not None or not keep_base:
                base = Path(snapshot.name) / "source.csv" if snapshot else None
                return save_csv(
                    path, destination, edits, revision, source=base, cancel=cancel, fmt=fmt
                ), snapshot
            scratch = TemporaryDirectory(prefix="csv-history-")
            base = Path(scratch.name) / "source.csv"
            try:
                with path.open("rb") as source, base.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        cancel.check()
                        output.write(chunk)
                return save_csv(
                    path, destination, edits, revision, source=base, cancel=cancel, fmt=fmt
                ), scratch
            except BaseException:
                scratch.cleanup()
                raise

        self.save_job = self.submit(save)
        self.poll.start()
        self.update_status()

    @property
    def dirty(self):
        return self.history_pos != self.clean_pos

    def record_edit(self, key, before, after):
        self.refresh_after_edit(("edit", [(key, before, after)]))

    def move_history(self, step):
        target = self.history_pos + step
        if not self.model.editable or not 0 <= target <= len(self.history):
            return
        for key, before, after in self.history[min(target, self.history_pos)]:
            self.set_patch(key, before if step < 0 else after)
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

    @staticmethod
    def structural(edits):
        return any(row >= NEW_ROW or col == DELETED_COLUMN for row, col in edits)

    def set_patch(self, key, value):
        if value is MISSING:
            self.model.edits.pop(key, None)
        else:
            self.model.edits[key] = value

    def edit_batch(self, values):
        if not self.model.editable:
            return
        changes = [
            (key, self.model.edits.get(key, MISSING), value)
            for key, value in values.items()
            if self.model.edits.get(key, MISSING) != value
        ]
        if changes:
            for key, _, value in changes:
                self.set_patch(key, value)
            self.refresh_after_edit(("edit", changes))

    def copy_range(self):
        indexes = self.table.selectedIndexes()
        if not indexes:
            return
        values = {
            (i.row(), i.column()): self.model.data(i, Qt.ItemDataRole.EditRole) for i in indexes
        }
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t")
        columns = range(min(c for _, c in values), max(c for _, c in values) + 1)
        for row in range(min(r for r, _ in values), max(r for r, _ in values) + 1):
            writer.writerow(values.get((row, col), "") for col in columns)
        QApplication.clipboard().setText(output.getvalue())

    def paste_range(self):
        anchor = self.table.currentIndex()
        if not self.model.editable or not anchor.isValid():
            return
        try:
            rows = list(
                csv.reader(
                    io.StringIO(QApplication.clipboard().text(), newline=""),
                    delimiter="\t",
                    strict=True,
                )
            )
        except csv.Error as exc:
            QMessageBox.warning(self, "Paste range", str(exc))
            return
        if anchor.row() + len(rows) > self.model.rowCount() or any(
            anchor.column() + len(row) > self.model.columnCount() for row in rows
        ):
            QMessageBox.warning(self, "Paste range", "The block does not fit in the loaded cells.")
            return
        self.edit_batch(
            {
                self.model.cell_key(self.model.index(anchor.row() + r, anchor.column() + c)): value
                or None
                for r, row in enumerate(rows)
                for c, value in enumerate(row)
            }
        )

    def clear_cells(self):
        self.edit_batch(
            {self.model.cell_key(index): None for index in self.table.selectedIndexes()}
        )

    def selected_rows(self):
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def add_rows(self, *, clone=False):
        if not self.model.editable:
            return
        rows = self.selected_rows() if clone else [None]
        if clone and self.model.headers != self.source_headers:
            QMessageBox.warning(self, "Duplicate rows", "Show all columns before duplicating rows.")
            return
        values = {}
        for row in rows:
            for col in range(len(self.source_headers)):
                index = self.model.index(row, col) if row is not None else None
                values[self.next_row_id, col] = (
                    self.model.data(index, Qt.ItemDataRole.EditRole) or None
                    if index is not None
                    else None
                )
            self.next_row_id += 1
        self.edit_batch(values)

    def delete_rows(self):
        self.edit_batch(
            {
                (self.model.cell_key(self.model.index(row, 0))[0], DELETED_COLUMN): None
                for row in self.selected_rows()
            }
        )

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
            snapshot, path, revision, selection, edits = (
                self.source_snapshot,
                self.path,
                self.revision,
                self.selection,
                self.export_edits,
            )
            fmt = self.csv_format
            if edits or snapshot:

                def export(*, cancel):
                    base = Path(snapshot.name) / "source.csv" if snapshot else path
                    return export_edited_csv(
                        base,
                        Path(destination),
                        edits,
                        revision,
                        selection,
                        document=path,
                        fmt=fmt,
                        cancel=cancel,
                    )

                self.export_job = self.submit(export)
            else:
                self.export_job = self.submit(
                    export_csv, self.path, Path(destination), self.selection, fmt=fmt
                )
            self.poll.start()
            self.update_status()

    def preview_sql(self):
        sql, values = selection_sql(
            self.source_headers, self.selection, source=self.csv_format.source
        )
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
            self.query_job = self.submit(query_csv, self.path, self.sql_text, fmt=self.csv_format)
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
                extra_sort=current.extra_sort
                if (sort_column.currentData(), direction.currentIndex() == 1)
                == (current.sort_column, current.descending)
                else (),
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
        if self.selection != Selection() or self.identified or self.structural(self.model.edits):
            self.load_view(self.path, self.selection, refreshing_edit=True)
        else:
            self.applied_edits = self.model.edits.copy()
            self.commit_history()
            if self.model.rowCount():
                self.model.dataChanged.emit(
                    self.model.index(0, 0),
                    self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1),
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
        fmt=None,
    ):
        if (
            self.view_job is not None
            or self.save_job is not None
            or (self.export_job is not None and self.export_edits)
            or (new_file and not self.confirm_discard_edits())
        ):
            return

        fmt = fmt or (CsvFormat() if new_file else self.csv_format)
        edits = {} if new_file else self.model.edits.copy()
        snapshot = None if new_file else self.source_snapshot
        identified = distinct is None and (
            selection != Selection() or self.structural(edits) or snapshot is not None
        )

        def read_view(*, cancel):
            revision = file_revision(path)
            if not new_file and revision != self.revision:
                raise ValueError("The source CSV changed. Reopen it before editing.")
            base = Path(snapshot.name) / "source.csv" if snapshot else path
            source_headers = None
            if new_file and selection.visible_columns is not None:
                source_headers = read_page(path, 0, cancel=cancel, fmt=fmt)[0]
            if distinct is not None and (edits or snapshot):
                result = prepare_editable_result(
                    base,
                    replace(
                        selection, visible_columns=(distinct,), sort_column=None, extra_sort=()
                    ),
                    edits,
                    distinct=distinct,
                    singletons=singletons,
                    cancel=cancel,
                    fmt=fmt,
                )
                return revision, result.page(0, cancel=cancel), result, source_headers
            if distinct is not None:
                result = prepare_result(
                    path,
                    selection,
                    distinct=distinct,
                    singletons=singletons,
                    cancel=cancel,
                    fmt=fmt,
                )
                return revision, result.page(0, cancel=cancel), result, source_headers
            if selection.sort_column is not None:
                result = prepare_editable_result(base, selection, edits, cancel=cancel, fmt=fmt)
                page = result.page(0, cancel=cancel)
                if file_revision(path) != revision:
                    raise ValueError("The source CSV changed. Reopen it before editing.")
                return revision, page, result, source_headers
            if identified:
                page = read_editable_page(base, 0, selection, edits, cancel=cancel, fmt=fmt)
            else:
                page = read_page(path, 0, selection, cancel=cancel, fmt=fmt)
            if file_revision(path) != revision:
                raise ValueError("The source CSV changed. Reopen it before editing.")
            return revision, page, None, source_headers

        self.refreshing_edit = refreshing_edit
        self.view_request = path, selection, new_file, distinct, identified, fmt
        self.view_job = self.submit(read_view)
        self.poll.start()
        self.update_status()

    def apply_view(self, revision, page, result, source_headers):
        path, selection, new_file, distinct, identified, fmt = self.view_request
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
            self.count_job = self.submit(count_rows, path, fmt=fmt)
            self.source_headers = (
                source_headers
                if source_headers is not None
                else (headers[1:] if selection != Selection() else headers)
            )

        self.path, self.selection, self.file_size = path, selection, revision[2]
        self.revision = revision
        self.csv_format = fmt
        if new_file:
            self.source_snapshot = None
            self.next_row_id = NEW_ROW
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
        self.identified = identified
        if identified:
            headers, rows, ids = self.split_row_ids(headers, rows)
        self.model.replace(headers, rows, row_ids=ids)
        header = self.table.horizontalHeader()
        header.controls_enabled = distinct is None
        header.sort_keys = selection.sort_keys
        header.filtered = self.model.filtered_columns
        header.viewport().update()
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
            edits = self.model.edits.copy()
            snapshot, identified, fmt = self.source_snapshot, self.identified, self.csv_format

            def read_current_page(*, cancel):
                if file_revision(path) != revision:
                    raise ValueError("The source CSV changed. Reopen it before editing.")
                page = (
                    read_page(path, offset, selection, cancel=cancel, fmt=fmt)
                    if not identified
                    else read_editable_page(
                        Path(snapshot.name) / "source.csv" if snapshot else path,
                        offset,
                        selection,
                        edits,
                        cancel=cancel,
                        fmt=fmt,
                    )
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
        if self.identified:
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
                    (self.path, self.revision), self.source_snapshot = result
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

    def update_edit_actions(self):
        self.copy_action.setEnabled(self.path is not None)
        for action in (
            self.paste_action,
            self.clear_action,
            self.add_row_action,
            self.clone_action,
            self.delete_action,
        ):
            action.setEnabled(self.model.editable)

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
        self.update_edit_actions()
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
