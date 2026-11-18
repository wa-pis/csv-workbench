"""Qt preview window."""

import csv
import io
import json
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QSettings,
    QSignalBlocker,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
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
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyleOptionButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from csv_workbench.core import (
    CSV_ENCODINGS,
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
    find_csv_cell,
    prepare_editable_result,
    prepare_result,
    preview_format,
    query_csv,
    read_editable_page,
    read_format_sample,
    read_page,
    save_csv,
    search_lower,
    selection_sql,
)
from csv_workbench.presets import FormatPresets, export_format_presets, import_format_presets
from csv_workbench.recent import (
    clear_recent_files,
    recent_files,
    remember_file,
    remove_recent_file,
    set_file_pinned,
)
from csv_workbench.session import (
    load_session,
    load_workspace_session,
    save_session,
    save_workspace_session,
)

MISSING = object()
ROOT_INDEX = QModelIndex()
BUFFER_ROWS = PAGE_SIZE * 3
JOB_NAMES = ("view_job", "count_job", "page_job", "find_job", "export_job", "query_job", "save_job")
FIND_COLORS = {
    Qt.ItemDataRole.BackgroundRole: QBrush(QColor("#ffe38a")),
    Qt.ItemDataRole.ForegroundRole: QBrush(QColor("#242424")),
}


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
        self.find_text = ""

    def replace(self, headers: list[str], rows: list[tuple], offset: int = 0, row_ids=()) -> None:
        self.beginResetModel()
        self.headers, self.rows, self.offset = headers, rows, offset
        self.row_ids = list(row_ids)
        self.endResetModel()

    def insert_buffer_rows(self, index, rows, ids, offset):
        self.beginInsertRows(ROOT_INDEX, index, index + len(rows) - 1)
        self.rows[index:index] = rows
        if ids:
            self.row_ids[index:index] = ids
        self.offset = offset
        self.endInsertRows()

    def remove_buffer_rows(self, index, count, offset):
        self.beginRemoveRows(ROOT_INDEX, index, index + count - 1)
        del self.rows[index:index + count]
        del self.row_ids[index:index + count]
        self.offset = offset
        self.endRemoveRows()

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
        if not index.isValid():
            return None
        highlight = self.find_text and role in FIND_COLORS
        if not highlight and role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        value = self.cell_value(index)
        if highlight:
            return FIND_COLORS[role] if value is not None and self.find_text in search_lower(str(value)) else None
        if role == Qt.ItemDataRole.EditRole:
            return "" if value is None else str(value)
        return "NULL" if value is None else str(value)

    def cell_value(self, index):
        return self.edits.get(self.cell_key(index), self.rows[index.row()][index.column()])

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


class CsvTable(QTableView):
    def __init__(self, model):
        super().__init__()
        self.setModel(model)
        self.pinned = QTableView(self)
        self.pinned.setModel(model)
        self.pinned.setSelectionModel(self.selectionModel())
        self.pinned.setHorizontalHeader(CsvHeader(self.pinned))
        self.pinned.verticalHeader().hide()
        self.pinned.setFrameShape(QTableView.Shape.NoFrame)
        self.pinned.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pinned.clicked.connect(lambda _: self.setFocus())
        self.pinned.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pinned.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        for view in (self, self.pinned):
            view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            view.verticalHeader().setDefaultSectionSize(23)
            view.setAlternatingRowColors(True)
            view.setShowGrid(False)
        self.pinned.hide()
        self.pinned_sections = ()
        self.verticalScrollBar().valueChanged.connect(self.pinned.verticalScrollBar().setValue)
        self.pinned.verticalScrollBar().valueChanged.connect(self.sync_pinned_scroll)
        self.verticalHeader().sectionResized.connect(
            lambda row, _, size: self.pinned.setRowHeight(row, size)
        )
        self.pinned.horizontalHeader().sectionResized.connect(
            lambda col, _, size: self.setColumnWidth(col, size)
        )

    def sync_pinned_scroll(self, value):
        if self.pinned.isVisible():
            self.verticalScrollBar().setValue(value)

    def set_pins(self, names):
        header = self.horizontalHeader()
        pinned_header = self.pinned.horizontalHeader()
        sections = tuple(i for i, name in enumerate(self.model().headers) if name in names)
        self.pinned_sections = sections
        order = (*sections, *(i for i in range(header.count()) if i not in sections))
        with QSignalBlocker(header), QSignalBlocker(pinned_header):
            for visual, logical in enumerate(order):
                header.moveSection(header.visualIndex(logical), visual)
                self.pinned.setColumnHidden(logical, logical not in sections)
                if logical in sections:
                    self.pinned.setColumnWidth(logical, header.sectionSize(logical))
        self.pinned.setVisible(bool(sections))
        self.place_pins()

    def place_pins(self):
        if not self.pinned_sections:
            return
        header = self.horizontalHeader()
        viewport = self.viewport().geometry()
        reserve = 80 if len(self.pinned_sections) < header.count() else 0
        width = min(
            self.pinned.horizontalHeader().length(), max(0, viewport.width() - reserve)
        )
        self.pinned.setGeometry(viewport.x(), header.y(), width, viewport.height() + header.height())
        self.pinned.raise_()
        with QSignalBlocker(self.pinned.verticalScrollBar()):
            self.pinned.verticalScrollBar().setValue(self.verticalScrollBar().value())

    def updateGeometries(self):
        super().updateGeometries()
        if hasattr(self, "pinned_sections"):
            self.place_pins()

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        bar = self.horizontalScrollBar()
        offset = bar.value()
        super().scrollTo(index, hint)
        if not index.isValid() or not getattr(self, "pinned_sections", ()):
            return
        if index.column() in self.pinned_sections:
            bar.setValue(offset)
        elif (left := self.visualRect(index).left()) < self.pinned.width():
            bar.setValue(bar.value() + left - self.pinned.width())


class CsvFormatFields:
    def __init__(self, form, *, exporting=False):
        delimiter = QComboBox()
        for label, value in (("Comma (,)", ","), ("Semicolon (;)", ";"), ("Tab (TSV)", "\t")):
            delimiter.addItem(label, value)
        delimiter.addItem("Custom…")
        custom = QLineEdit()
        custom.setPlaceholderText("One ASCII character, e.g. |")
        encoding = QComboBox()
        for label, codec in CSV_ENCODINGS.items():
            if exporting:
                label = {"Auto": "UTF-8", "UTF-8": "UTF-8 with BOM"}.get(label, label)
            encoding.addItem(label, codec)
        quote = QLineEdit('"')
        quote.setPlaceholderText("One ASCII character")
        header = QCheckBox("Include column names" if exporting else "First record contains column names")
        header.setChecked(True)
        form.addRow("Delimiter", delimiter)
        form.addRow("Custom delimiter", custom)
        form.addRow("Encoding", encoding)
        form.addRow("Quote character", quote)
        form.addRow(header)
        self.delimiter = delimiter
        self.custom = custom
        self.encoding = encoding
        self.quote = quote
        self.header = header

        def show_custom():
            form.setRowVisible(custom, delimiter.currentData() is None)

        self.show_custom = show_custom
        delimiter.currentIndexChanged.connect(show_custom)
        show_custom()

    def apply(self, fmt):
        index = self.delimiter.findData(fmt.delimiter)
        with (
            QSignalBlocker(self.delimiter),
            QSignalBlocker(self.custom),
            QSignalBlocker(self.encoding),
            QSignalBlocker(self.quote),
            QSignalBlocker(self.header),
        ):
            self.custom.setText(fmt.delimiter if index < 0 else "")
            self.delimiter.setCurrentIndex(index if index >= 0 else self.delimiter.findData(None))
            self.encoding.setCurrentIndex(self.encoding.findData(fmt.encoding))
            self.quote.setText(fmt.quote)
            self.header.setChecked(fmt.header)
        self.show_custom()

    def value(self):
        value = self.delimiter.currentData()
        return CsvFormat(
            self.custom.text() if value is None else value,
            self.header.isChecked(),
            self.encoding.currentData(),
            self.quote.text(),
        )


class MainWindow(QMainWindow):
    def __init__(self, workspace=None):
        super().__init__()
        self.workspace = workspace
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
        self.find_job = None
        self.find_match = None
        self.export_job = None
        self.save_job = None
        self.write_destination = None
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
        self.setAcceptDrops(True)
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
        open_action = file_menu.addAction("Open CSV…", self.open_file)
        open_action.setShortcuts(QKeySequence.StandardKey.Open)
        self.settings = QSettings("CSV Workbench", "CSV Workbench")
        self.recent_menu = file_menu.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self.populate_recent_files)
        file_menu.addSeparator()
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
        self.redo_action.setShortcuts(QKeySequence.StandardKey.Redo)
        self.go_to_row_action = edit_menu.addAction("Go to Row…", self.prompt_for_row)
        self.go_to_row_action.setShortcut(QKeySequence("Ctrl+G"))
        self.go_to_row_action.setEnabled(False)
        self.find_action = edit_menu.addAction("Find…", self.open_find)
        self.find_action.setShortcuts(QKeySequence.StandardKey.Find)
        self.find_action.setEnabled(False)
        self.transpose_action = edit_menu.addAction("Transpose Selection…", self.transpose_selection)
        self.record_action = edit_menu.addAction("Record View…", self.show_record)
        self.value_action = edit_menu.addAction("Open in Value Editor…", self.edit_cell_value)
        self.value_action.setShortcuts(["Shift+Return", "Shift+Enter"])
        self.value_action.setEnabled(False)
        self.columns_action = edit_menu.addAction("Manage Columns…", self.manage_columns)
        self.columns_action.setEnabled(False)
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
        if workspace is not None:
            session_menu.addSeparator()
            session_menu.addAction("Save workspace…", workspace.save_workspace_file)
            session_menu.addAction("Restore workspace…", workspace.restore_workspace_file)
            self.menuBar().setNativeMenuBar(False)
            for action in (
                open_action, self.save_action, self.save_as_action, self.undo_action, self.redo_action,
                self.copy_action, self.paste_action, self.clear_action, self.go_to_row_action,
                self.find_action, self.value_action,
            ):
                self.addAction(action)
                action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
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

        self.find_bar = QWidget()
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(6, 2, 6, 2)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in current view")
        self.find_input.setAccessibleName("Find in current view")
        find_layout.addWidget(self.find_input, 1)
        self.find_previous = QPushButton("Previous")
        self.find_next = QPushButton("Next")
        self.find_stop = QPushButton("Stop")
        self.find_message = QLabel()
        find_close = QPushButton("Close")
        for widget in (self.find_previous, self.find_next, self.find_stop, self.find_message, find_close):
            find_layout.addWidget(widget)
        self.find_input.textChanged.connect(self.reset_find)
        self.find_input.returnPressed.connect(lambda: self.start_find(True))
        self.find_previous.clicked.connect(lambda: self.start_find(False))
        self.find_next.clicked.connect(lambda: self.start_find(True))
        self.find_stop.clicked.connect(self.stop_find)
        find_close.clicked.connect(self.close_find)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.find_bar)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.close_find)
        self.find_bar.hide()
        layout.addWidget(self.find_bar)

        self.table = CsvTable(self.model)
        self.table.selectionModel().currentChanged.connect(self.update_edit_actions)
        self.table.selectionModel().selectionChanged.connect(self.update_edit_actions)
        header = CsvHeader(self.table)
        self.table.setHorizontalHeader(header)
        header.setSectionsClickable(True)
        header.setSectionsMovable(True)
        header.sectionMoved.connect(lambda *_: QTimer.singleShot(0, self.apply_column_drag))
        header.sectionResized.connect(self.sync_pin_width)
        header.setStretchLastSection(True)
        for view in (self.table, self.table.pinned):
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda point, view=view: self.cell_filter_menu(point, view)
            )
            view_header = view.horizontalHeader()
            view_header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view_header.customContextMenuRequested.connect(
                lambda point, h=view_header: self.column_layout_menu(point, h)
            )
            view_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            view_header.sortRequested.connect(self.cycle_column_sort)
            view_header.filterRequested.connect(
                lambda section, h=view_header: self.open_column_menu(section, h)
            )
        layout.addWidget(self.table, 1)

        self.table.verticalScrollBar().valueChanged.connect(self.load_at_edge)
        for key, standard, last in (
            (Qt.Key.Key_Home, QKeySequence.StandardKey.MoveToStartOfDocument, False),
            (Qt.Key.Key_End, QKeySequence.StandardKey.MoveToEndOfDocument, True),
        ):
            shortcut = QShortcut(self.table)
            keys = QKeySequence.keyBindings(standard)
            plain = QKeySequence(key)
            shortcut.setKeys(keys if plain in keys else [plain, *keys])
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda last=last: self.go_to_edge(last))
        self.status = QLabel("Open a CSV file to begin")
        self.statusBar().addWidget(self.status, 1)
        self.update_edit_actions()

    def cycle_column_sort(self, section, append):
        if self.path is not None and self.view_job is None and self.distinct is None:
            self.load_view(
                self.path, self.selection.cycle_sort(self.model.headers[section], append=append)
            )

    def open_column_menu(self, section, header=None):
        if self.path is None or self.view_job is not None or self.distinct is not None:
            return
        column = self.model.headers[section]
        current = self.selection
        menu = QMenu(self)
        add_filter = menu.addAction("Add contains filter…")
        clear_filter = menu.addAction("Clear filters for this column")
        remaining = tuple(pair for pair in current.filters if pair[0] != column)
        clear_filter.setEnabled(column in current.filter_columns)
        header = header or self.table.horizontalHeader()
        action = menu.exec(
            header.mapToGlobal(QPoint(header.sectionViewportPosition(section), header.height()))
        )
        if action == add_filter:
            value, accepted = QInputDialog.getText(self, "Column filter", f"{column} contains:")
            if not accepted or not value:
                return
            selection = replace(current, filters=(*current.filters, (column, value)))
        elif action == clear_filter:
            selection = replace(
                current, filters=remaining,
                exact_filters=tuple(rule for rule in current.exact_filters if rule[0] != column),
            )
        else:
            return
        self.load_view(self.path, selection)

    def cell_filter_menu(self, point, table=None):
        table = table or self.table
        index = table.indexAt(point)
        if not index.isValid() or not self.columns_action.isEnabled():
            return
        menu = QMenu(self)
        filters = menu.addMenu("Filter by")
        equals = filters.addAction("Equals this value")
        not_equals = filters.addAction("Does not equal this value")
        cell = QPersistentModelIndex(index)
        action = menu.exec(table.viewport().mapToGlobal(point))
        if action in (equals, not_equals) and cell.isValid():
            self.quick_filter(QModelIndex(cell), "equals" if action == equals else "not_equals")

    def quick_filter(self, index, operator):
        rule = (self.model.headers[index.column()], operator, self.model.cell_value(index))
        if rule not in self.selection.exact_filters:
            self.load_view(self.path, replace(
                self.selection, exact_filters=(*self.selection.exact_filters, rule),
            ))

    def sync_pin_width(self, section, _, width):
        if section in self.table.pinned_sections and width:
            header = self.table.pinned.horizontalHeader()
            with QSignalBlocker(header):
                self.table.pinned.setColumnWidth(section, width)
            self.table.place_pins()

    def column_layout_menu(self, point, header=None):
        if not self.columns_action.isEnabled():
            return
        header = header or self.table.horizontalHeader()
        section = header.logicalIndexAt(point)
        menu = QMenu(self)
        pin = None
        if section >= 0:
            name = self.model.headers[section]
            pinned = name in self.selection.pinned_columns
            pin = menu.addAction("Unpin Column" if pinned else "Pin Column Left")
        hide = menu.addAction("Hide Column")
        hide.setEnabled(section >= 0 and self.model.columnCount() > 1)
        menu.addAction(self.columns_action)
        action = menu.exec(header.viewport().mapToGlobal(point))
        if pin is not None and action == pin:
            pins = self.selection.pinned_columns
            pins = tuple(column for column in pins if column != name) if pinned else (*pins, name)
            self.selection = replace(self.selection, pinned_columns=pins)
            self.table.set_pins(pins)
        elif action == hide:
            self.set_visible_columns(name for i, name in enumerate(self.model.headers) if i != section)

    def set_visible_columns(self, columns):
        visible = tuple(columns)
        if visible:
            self.load_view(self.path, replace(
                self.selection,
                visible_columns=visible if len(visible) != len(self.source_headers) else None,
            ))

    def manage_columns(self):
        if not self.columns_action.isEnabled():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage Columns")
        layout = QVBoxLayout(dialog)
        search = QLineEdit(placeholderText="Find columns")
        layout.addWidget(search)
        fields = QListWidget()
        layout.addWidget(fields)
        for name in self.selection.ordered_columns(self.source_headers):
            item = QListWidgetItem(name, fields)
            item.setCheckState(Qt.CheckState.Checked if name in self.model.headers else Qt.CheckState.Unchecked)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        def selected():
            return [fields.item(i).text() for i in range(fields.count())
                    if fields.item(i).checkState() == Qt.CheckState.Checked]

        def filter_columns(value):
            for i in range(fields.count()):
                item = fields.item(i)
                item.setHidden(value.casefold() not in item.text().casefold())

        search.textChanged.connect(filter_columns)
        fields.itemChanged.connect(lambda: buttons.button(QDialogButtonBox.StandardButton.Ok)
                                   .setEnabled(bool(selected())))
        search.setFocus()
        if dialog.exec():
            self.set_visible_columns(selected())

    def apply_column_drag(self):
        header = self.table.horizontalHeader()
        visible = self.model.headers
        if header.count() != len(visible):
            return
        pins = set(self.selection.pinned_columns)
        logical = [header.logicalIndex(i) for i in range(header.count())]
        prefix = self.table.pinned_sections
        if logical == [*prefix, *(i for i in range(header.count()) if i not in prefix)]:
            return
        order = [visible[i] for i in logical if visible[i] not in pins]
        self.table.set_pins(self.selection.pinned_columns if self.distinct is None else ())
        if self.columns_action.isEnabled():
            reordered = iter(order)
            layout = tuple(next(reordered) if name in visible and name not in pins else name
                           for name in self.selection.ordered_columns(self.source_headers))
            if layout != tuple(self.selection.ordered_columns(self.source_headers)):
                self.load_view(self.path, replace(self.selection, column_order=layout))

    def populate_recent_files(self):
        self.recent_menu.clear()
        entries = recent_files(self.settings)
        if not entries:
            self.recent_menu.addAction("No recent files").setEnabled(False)
        for entry in entries:
            label = str(entry.path).replace("&", "&&").replace("\n", " ").replace("\t", " ")
            if entry.pinned:
                label = f"★ {label}"
            self.recent_menu.addAction(
                label,
                lambda checked=False, entry=entry: self.open_document(
                    entry.path, Selection(), entry.format
                ),
            )

        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Manage Recent Files…", self.manage_recent_files)

    def manage_recent_files(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage recent files")
        dialog.resize(720, 350)
        layout = QVBoxLayout(dialog)
        files = QListWidget()
        layout.addWidget(files)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        pin = QPushButton("Pin")
        remove = QPushButton("Remove")
        clear = QPushButton("Clear Unpinned")
        close = QPushButton("Close")
        for button in (pin, remove, clear, close):
            controls.addWidget(button)

        entries = []

        def selected():
            index = files.currentRow()
            return entries[index] if 0 <= index < len(entries) else None

        def update_controls():
            entry = selected()
            pin.setText("Unpin" if entry and entry.pinned else "Pin")
            pin.setEnabled(entry is not None)
            remove.setEnabled(entry is not None)
            clear.setEnabled(any(not item.pinned for item in entries))

        def reload(path=None):
            entries[:] = recent_files(self.settings)
            with QSignalBlocker(files):
                files.clear()
                for entry in entries:
                    label = f"★ {entry.path}" if entry.pinned else str(entry.path)
                    files.addItem(label)
                index = next((i for i, entry in enumerate(entries) if entry.path == path), 0)
                if entries:
                    files.setCurrentRow(index)
            update_controls()

        def toggle_pin():
            if entry := selected():
                set_file_pinned(self.settings, entry.path, not entry.pinned)
                reload(entry.path)

        def remove_file():
            if entry := selected():
                remove_recent_file(self.settings, entry.path)
                reload()

        def clear_history():
            entry = selected()
            clear_recent_files(self.settings)
            reload(entry.path if entry else None)

        files.currentRowChanged.connect(update_controls)
        pin.clicked.connect(toggle_pin)
        remove.clicked.connect(remove_file)
        clear.clicked.connect(clear_history)
        close.clicked.connect(dialog.accept)
        reload()
        dialog.exec()

    def open_file(self):
        name, _ = QFileDialog.getOpenFileName(
            self, "Open CSV / TSV", "", "Delimited files (*.csv *.tsv);;All files (*)"
        )
        if name:
            self.open_with_format(Path(name))

    def open_with_format(self, path):
        try:
            fmt = self.configure_format(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Open CSV", str(exc))
            return
        if fmt is not None:
            self.open_document(path, Selection(), fmt)

    def open_document(self, path, selection, fmt):
        if self.workspace is not None:
            self.workspace.open_document(path, selection, fmt)
        else:
            self.load_view(path, selection, new_file=True, fmt=fmt)

    @staticmethod
    def dropped_csv(event):
        urls = event.mimeData().urls()
        if (
            len(urls) == 1
            and urls[0].isLocalFile()
            and event.possibleActions() & Qt.DropAction.CopyAction
        ):
            path = Path(urls[0].toLocalFile())
            if path.suffix.lower() in {".csv", ".tsv"} and path.is_file():
                return path
        return None

    def dragEnterEvent(self, event):
        if self.dropped_csv(event) is None:
            event.ignore()
        else:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if (path := self.dropped_csv(event)) is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        # Open the modal dialog after Qt finishes handling the drop.
        QTimer.singleShot(0, self, lambda: self.open_with_format(path))

    def add_format_presets(self, form, fields, refresh, *, exporting=False):
        dialog = form.parentWidget()
        settings = self.settings
        store = FormatPresets(settings, exporting=exporting)
        combo = QComboBox()
        combo.setObjectName("formatPresets")
        save = QPushButton("Save Preset…")
        manage = QPushButton("Manage…")
        menu = QMenu(manage)
        rename = menu.addAction("Rename…")
        delete = menu.addAction("Delete…")
        make_default = menu.addAction("Set as Default")
        clear_default = menu.addAction("Clear Default")
        menu.addSeparator()
        import_json = menu.addAction("Import Presets from JSON…")
        export_json = menu.addAction("Export Presets to JSON…")
        manage.setMenu(menu)
        row = QHBoxLayout()
        row.addWidget(combo, 1)
        row.addWidget(save)
        row.addWidget(manage)
        form.insertRow(0, "Preset", row)

        def populate(selected=None):
            combo.clear()
            combo.addItem("Choose a preset…")
            for name, fmt in sorted(store.load().items(), key=lambda item: item[0].casefold()):
                combo.addItem(name, fmt)
                if name == selected:
                    combo.setCurrentIndex(combo.count() - 1)

        def apply_preset(index):
            if fmt := combo.itemData(index):
                fields.apply(fmt)
                refresh()

        def save_preset():
            try:
                fmt = fields.value()
                name, accepted = QInputDialog.getText(dialog, "Save format preset", "Preset name:")
                if not accepted:
                    return
                name = name.strip()
                if name in store.load() and QMessageBox.question(
                    dialog, "Replace preset", f"Replace the preset “{name}”?"
                ) != QMessageBox.StandardButton.Yes:
                    return
                store.save(name, fmt)
                populate(name)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(dialog, "Format preset", str(exc))

        def selected_name():
            return combo.currentText() if combo.currentIndex() > 0 else None

        def update_menu():
            name = selected_name()
            default = store.default_name()
            rename.setEnabled(name is not None)
            delete.setEnabled(name is not None)
            make_default.setEnabled(name is not None and name != default)
            clear_default.setEnabled(default is not None)
            manage.setToolTip(f"Default preset: {default}" if default else "No default preset")

        def rename_preset():
            old = selected_name()
            name, accepted = QInputDialog.getText(
                dialog, "Rename preset", "New name:", text=old
            )
            if accepted:
                try:
                    populate(store.rename(old, name))
                except (OSError, ValueError) as exc:
                    QMessageBox.warning(dialog, "Format preset", str(exc))

        def delete_preset():
            name = selected_name()
            if QMessageBox.question(
                dialog, "Delete preset", f"Delete the preset “{name}”?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes:
                try:
                    store.delete(name)
                    populate()
                except (OSError, ValueError) as exc:
                    QMessageBox.warning(dialog, "Format preset", str(exc))

        def set_default(name):
            try:
                store.set_default(name)
                update_menu()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(dialog, "Format preset", str(exc))

        def confirm_replace(conflicts):
            details = "\n".join(
                f"{scope.title()}: {', '.join(names)}" for scope, names in conflicts.items()
            )
            message = QMessageBox(dialog)
            message.setWindowTitle("Import format presets")
            message.setIcon(QMessageBox.Icon.Question)
            message.setTextFormat(Qt.TextFormat.PlainText)
            message.setText(f"Replace these presets and import both lists?\n\n{details}")
            message.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            message.setDefaultButton(QMessageBox.StandardButton.No)
            return message.exec() == QMessageBox.StandardButton.Yes

        def import_presets():
            filename, _ = QFileDialog.getOpenFileName(
                dialog, "Import format presets", "", "JSON (*.json)"
            )
            if filename:
                try:
                    if import_format_presets(settings, Path(filename), confirm_replace=confirm_replace):
                        populate()
                        update_menu()
                except (OSError, ValueError) as exc:
                    QMessageBox.warning(dialog, "Import format presets", str(exc))

        def export_presets():
            filename, _ = QFileDialog.getSaveFileName(
                dialog, "Export format presets", "csv-format-presets.json", "JSON (*.json)"
            )
            if filename:
                try:
                    export_format_presets(settings, Path(filename))
                except OSError as exc:
                    QMessageBox.warning(dialog, "Export format presets", str(exc))

        default = store.default_name()
        populate(default)
        update_menu()
        combo.currentIndexChanged.connect(update_menu)
        combo.activated.connect(apply_preset)
        save.clicked.connect(save_preset)
        rename.triggered.connect(rename_preset)
        delete.triggered.connect(delete_preset)
        make_default.triggered.connect(lambda: set_default(selected_name()))
        clear_default.triggered.connect(lambda: set_default(None))
        import_json.triggered.connect(import_presets)
        export_json.triggered.connect(export_presets)
        apply_preset(combo.currentIndex())

    def configure_format(self, path):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"File format — {path.name}")
        dialog.resize(680, 350)
        form = QFormLayout(dialog)
        fields = CsvFormatFields(form)
        sample = None
        sample_encoding = object()
        manual_delimiter = False
        fallback = "\t" if path.suffix.lower() == ".tsv" else ","
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

        def choose_delimiter():
            nonlocal manual_delimiter
            manual_delimiter = True
            refresh()

        def refresh():
            nonlocal sample, sample_encoding
            open_button = buttons.button(QDialogButtonBox.StandardButton.Open)
            open_button.setEnabled(False)
            try:
                codec = fields.encoding.currentData()
                if codec != sample_encoding:
                    sample = read_format_sample(path, codec)
                    sample_encoding = codec
                quote_char = fields.quote.text()
                if not manual_delimiter and len(quote_char) == 1 and " " <= quote_char <= "~":
                    suggested = detect_delimiter(sample, fallback, quote=quote_char)
                    with QSignalBlocker(fields.delimiter):
                        fields.delimiter.setCurrentIndex(fields.delimiter.findData(suggested))
                fmt = fields.value()
                headers, rows = preview_format(sample, fmt)
                open_button.setEnabled(True)
                width = max([len(headers), *(len(row) for row in rows)])
                headers += [""] * (width - len(headers))
                model.replace(headers, [tuple(row + [""] * (width - len(row))) for row in rows])
                status.setText(
                    "Preview: up to 10 records"
                    if headers
                    else "No complete records in the preview sample."
                )
            except (csv.Error, OSError, ValueError) as exc:
                model.replace([], [])
                status.setText(str(exc))

        fields.delimiter.currentIndexChanged.connect(choose_delimiter)
        fields.encoding.currentIndexChanged.connect(refresh)
        fields.quote.textChanged.connect(refresh)
        fields.custom.textChanged.connect(refresh)
        fields.header.toggled.connect(refresh)
        self.add_format_presets(form, fields, choose_delimiter)
        refresh()
        return fields.value() if dialog.exec() else None

    def open_session_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open session", "", "Session (*.csvsession)"
        )
        if filename:
            try:
                path, selection, fmt = load_session(Path(filename))
                self.open_document(path, selection, fmt)
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

    def submit(self, operation, *args, heavy=True, **kwargs):
        token = Cancellation()
        executor = self.workspace.heavy_executor if heavy and self.workspace else self.executor
        future = executor.submit(operation, *args, cancel=token, **kwargs)
        return token, future

    def cancel_operations(self):
        for job in self.jobs():
            if job is not None:
                token, future = job
                future.cancel()
                token.cancel()
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
        if self.workspace and self.workspace.destination_busy(destination, except_document=self):
            QMessageBox.warning(self, "Save CSV", "The destination is open or being written in another tab.")
            return
        self.reset_find()
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

        self.write_destination = destination
        self.save_job = self.submit(save)
        self.poll.start()
        self.update_status()

    @property
    def dirty(self):
        return self.history_pos != self.clean_pos

    def record_edit(self, key, before, after):
        self.refresh_after_edit(("edit", [(key, before, after)]))

    def transpose_selection(self):
        if not self.transpose_action.isEnabled():
            return
        ranges = self.table.selectionModel().selection()
        if len(ranges) != 1 or ranges[0].width() * ranges[0].height() > 10_000:
            QMessageBox.warning(
                self, "Transpose Selection",
                "Select one rectangular range containing at most 10,000 cells.",
            )
            return
        selected = ranges[0]
        header = self.table.horizontalHeader()
        columns = [header.logicalIndex(i) for i in range(header.count())
                   if selected.left() <= header.logicalIndex(i) <= selected.right()]
        dialog = QDialog(self)
        dialog.setWindowTitle("Transpose Selection — buffered rows only")
        dialog.resize(800, 480)
        layout = QVBoxLayout(dialog)
        fields = QTableWidget(len(columns), selected.height(), dialog)
        fields.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        fields.setVerticalHeaderLabels([self.model.headers[col] for col in columns])
        fields.setHorizontalHeaderLabels([
            str(self.model.offset + row + 1)
            for row in range(selected.top(), selected.bottom() + 1)
        ])
        for i, col in enumerate(columns):
            for j, row in enumerate(range(selected.top(), selected.bottom() + 1)):
                fields.setItem(i, j, QTableWidgetItem(self.model.index(row, col).data()))
        layout.addWidget(fields)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def show_record(self):
        current = self.table.currentIndex()
        if not self.record_action.isEnabled() or not current.isValid():
            return
        row = current.row()
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Record View — Row {self.model.offset + row + 1:,}")
        dialog.resize(700, 460)
        layout = QVBoxLayout(dialog)
        header = self.table.horizontalHeader()
        fields = QTableWidget(header.count(), 2, dialog)
        fields.setHorizontalHeaderLabels(["Field", "Value"])
        fields.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        fields.verticalHeader().hide()
        fields.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        fields.horizontalHeader().setStretchLastSection(True)
        fields.setColumnWidth(0, 200)
        for i in range(header.count()):
            column = header.logicalIndex(i)
            value = self.model.index(row, column).data()
            fields.setItem(i, 0, QTableWidgetItem(self.model.headers[column]))
            fields.setItem(i, 1, QTableWidgetItem(value))
        layout.addWidget(fields)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def edit_cell_value(self):
        current = self.table.currentIndex()
        if not self.model.editable or not current.isValid():
            return
        cell = QPersistentModelIndex(current)
        value = current.data(Qt.ItemDataRole.EditRole)
        edited, accepted = QInputDialog.getMultiLineText(self, "Value Editor", "Cell value:", value)
        if accepted and cell.isValid():
            self.model.setData(QModelIndex(cell), edited)

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

    def configure_export_format(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("CSV / TSV export format")
        form = QFormLayout(dialog)
        fields = CsvFormatFields(form, exporting=True)
        status = QLabel()
        status.setWordWrap(True)
        form.addRow(status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)

        def validate():
            try:
                fields.value()
            except ValueError as exc:
                status.setText(str(exc))
                save.setEnabled(False)
            else:
                status.clear()
                save.setEnabled(True)

        fields.delimiter.currentIndexChanged.connect(validate)
        fields.custom.textChanged.connect(validate)
        fields.quote.textChanged.connect(validate)
        self.add_format_presets(form, fields, validate, exporting=True)
        validate()
        return fields.value() if dialog.exec() else None

    def save_selection(self):
        if self.path is None or self.export_job is not None or self.save_job is not None:
            return
        output_format = self.configure_export_format()
        if output_format is None:
            return
        tsv = output_format.delimiter == "\t"
        extension = ".tsv" if tsv else ".csv"
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSV",
            str(self.path.with_name(f"{self.path.stem}-selection{extension}")),
            "TSV (*.tsv)" if tsv else "CSV (*.csv)",
        )
        if destination:
            destination = Path(destination)
            if self.workspace and self.workspace.destination_busy(destination, except_document=self):
                QMessageBox.warning(self, "Export CSV", "The destination is open or being written in another tab.")
                return
            self.write_destination = destination
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
                        destination,
                        edits,
                        revision,
                        selection,
                        document=path,
                        fmt=fmt,
                        output_format=output_format,
                        cancel=cancel,
                    )

                self.export_job = self.submit(export)
            else:
                self.export_job = self.submit(
                    export_csv, path, destination, selection, fmt=fmt, output_format=output_format
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

        exact_rules = list(current.exact_filters)

        def add_exact_row(rule):
            column, operator, value = rule
            row = QWidget()
            widgets = QHBoxLayout(row)
            comparison = "=" if operator == "equals" else "≠"
            description = f"{column} {comparison} {repr(value) if value is not None else 'NULL'}"
            label = QLabel(description[:120])
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setToolTip(description)
            widgets.addWidget(label, 1)
            remove = QPushButton("Remove")
            widgets.addWidget(remove)
            filter_rows.addWidget(row)

            def remove_rule():
                exact_rules.remove(rule)
                filter_rows.removeWidget(row)
                row.hide()
                row.deleteLater()

            remove.clicked.connect(remove_rule)

        for rule in exact_rules:
            add_exact_row(rule)
        for column, value in current.conditions or (() if exact_rules else ((None, ""),)):
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
                column_order=current.column_order,
                exact_filters=tuple(exact_rules),
                pinned_columns=current.pinned_columns,
            )
            self.load_view(self.path, selection)
            dialog.accept()

        def reset():
            self.load_view(self.path, Selection(pinned_columns=current.pinned_columns))
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
        self.reset_find()
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

        self.reset_find()
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
            if new_file and (selection.visible_columns is not None or selection.column_order):
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
            self.count_job = (
                None if self.restoring_workspace else self.submit(count_rows, path, fmt=fmt)
            )
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
            selection.filter_columns if distinct is None else set()
        )
        ids = []
        self.identified = identified
        if identified:
            headers, rows, ids = self.split_row_ids(headers, rows)
        self.model.replace(headers, rows, row_ids=ids)
        self.table.set_pins(selection.pinned_columns if distinct is None else ())
        for view in (self.table, self.table.pinned):
            header = view.horizontalHeader()
            header.controls_enabled = distinct is None
            header.sort_keys = selection.sort_keys
            header.filtered = self.model.filtered_columns
            header.viewport().update()
        self.table.scrollToTop()
        self.has_more = has_more
        self.setWindowTitle(f"CSV Workbench — {path.name}")
        if new_file and not self.restoring_workspace:
            remember_file(self.settings, path, fmt)

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
        self.load_page(offset)

    def go_to_edge(self, last):
        if self.path is None or self.view_job or self.page_job or self.save_job:
            return
        needs_page = self.has_more if last else self.model.offset > 0
        if needs_page:
            self.load_page(None if last else 0, target_row=None if last else 0)
        else:
            self.show_edge_row(last)

    def prompt_for_row(self):
        value, accepted = QInputDialog.getText(self, "Go to Row", "Row number in the current view:")
        if not accepted:
            return
        try:
            number = int(value)
        except ValueError:
            number = 0
        self.go_to_row(number)

    def open_find(self):
        if self.find_bar.isHidden():
            self.find_bar.show()
            self.reset_find()
        self.find_input.setFocus()
        self.find_input.selectAll()
        self.update_status()

    def close_find(self):
        self.find_bar.hide()
        self.reset_find()
        self.table.setFocus()

    def cancel_find(self):
        jobs = [self.find_job]
        if self.page_job is not None and self.page_target_column is not None:
            jobs.append(self.page_job)
        for job in jobs:
            if job is not None:
                token, future = job
                future.cancel()
                token.cancel()

    def stop_find(self):
        self.cancel_find()
        self.find_match = None
        self.find_message.setText("Cancelled")
        self.update_status()

    def reset_find(self):
        self.cancel_find()
        self.find_match = None
        self.find_message.clear()
        self.model.find_text = (
            "" if self.find_bar.isHidden() else search_lower(self.find_input.text())
        )
        self.table.viewport().update()
        self.update_status()

    def start_find(self, forward):
        if not self.find_next.isEnabled():
            return
        anchor = self.find_match
        if anchor is None:
            anchor = (-1, -1) if forward else (2**63 - 1, -1)
        path, revision = self.path, self.revision
        snapshot, result = self.source_snapshot, self.result
        selection, fmt = self.selection, self.csv_format
        edits, columns = self.model.edits.copy(), tuple(self.model.headers)
        needle = self.find_input.text()

        def search(*, cancel):
            if file_revision(path) != revision:
                raise ValueError("The source CSV changed. Reopen it before searching.")
            match = find_csv_cell(
                Path(snapshot.name) / "source.csv" if snapshot else path,
                selection, edits, columns, needle, anchor, forward,
                cancel=cancel, fmt=fmt, result=result,
            )
            if file_revision(path) != revision:
                raise ValueError("The source CSV changed. Reopen it before searching.")
            return match

        self.find_job = self.submit(search)
        self.find_message.clear()
        self.poll.start()
        self.update_status()

    def apply_find(self, match):
        if match is None:
            self.find_message.setText("No more matches" if self.find_match else "No matches")
            return
        self.find_match = match
        row, column = match
        self.go_to_row(row + 1, column=column)

    def go_to_row(self, number, *, column=None):
        if self.path is None or self.view_job or self.page_job or self.save_job:
            return
        target = number - 1
        start = self.model.offset
        end = start + self.model.rowCount()
        if target < 0 or (not self.has_more and target >= end) or target >= 2**63 - 1:
            self.report_missing_row()
        elif start <= target < end:
            self.show_row(target - start, QAbstractItemView.ScrollHint.PositionAtCenter, column)
        else:
            # Leave room to scroll on either side of the target.
            offset = max(0, target // PAGE_SIZE - 1) * PAGE_SIZE
            self.load_page(offset, target_row=target, target_column=column, limit=BUFFER_ROWS)

    def report_missing_row(self):
        QMessageBox.warning(self, "Go to Row", "Enter an existing row number starting at 1.")

    def show_edge_row(self, last):
        if self.model.rowCount():
            row = self.model.rowCount() - 1 if last else 0
            hint = QAbstractItemView.ScrollHint
            self.show_row(row, hint.PositionAtBottom if last else hint.PositionAtTop)

    def show_row(self, row, hint, column=None):
        if column is None:
            column = max(0, self.table.currentIndex().column())
        index = self.model.index(row, column)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index, hint)

    def load_page(self, offset, *, target_row=None, target_column=None, limit=PAGE_SIZE):
        self.page_offset = offset
        self.page_target_row = target_row
        self.page_target_column = target_column
        if self.result is None:
            path, revision, selection = self.path, self.revision, self.selection
            edits = self.model.edits.copy()
            snapshot, identified, fmt = self.source_snapshot, self.identified, self.csv_format

            def read_current_page(*, cancel):
                if file_revision(path) != revision:
                    raise ValueError("The source CSV changed. Reopen it before editing.")
                page = (
                    read_page(path, offset, selection, cancel=cancel, fmt=fmt, limit=limit)
                    if not identified
                    else read_editable_page(
                        Path(snapshot.name) / "source.csv" if snapshot else path,
                        offset,
                        selection,
                        edits,
                        cancel=cancel,
                        fmt=fmt,
                        limit=limit,
                    )
                )
                if file_revision(path) != revision:
                    raise ValueError("The source CSV changed. Reopen it before editing.")
                return page

            self.page_job = self.submit(read_current_page)
        else:
            # Cached pages only read the existing result and can bypass the scan queue.
            self.page_job = self.submit(self.result.page, offset, limit=limit, heavy=False)
        self.poll.start()
        self.update_status()

    @staticmethod
    def split_row_ids(headers, rows):
        return headers[1:], [row[1:] for row in rows], [row[0] for row in rows]

    def apply_page(self, headers, rows, has_more, tail_offset=None):
        ids = []
        if self.identified:
            headers, rows, ids = self.split_row_ids(headers, rows)
        if headers != self.model.headers:
            raise ValueError("CSV changed. Reopen the file to refresh it.")
        target = self.page_target_row
        if target is not None and target - self.page_offset >= len(rows):
            self.report_missing_row()
            return
        if not rows:
            raise ValueError("CSV changed. Reopen the file to refresh it.")
        if tail_offset is not None or target is not None:
            self.adjusting = True
            self.has_more = has_more
            if tail_offset is not None:
                self.model.replace(headers, rows, tail_offset, ids)
                self.show_edge_row(True)
            else:
                self.model.replace(headers, rows, self.page_offset, ids)
                self.show_row(
                    target - self.page_offset, QAbstractItemView.ScrollHint.PositionAtCenter,
                    self.page_target_column,
                )
            self.adjusting = False
            return
        start = self.model.offset
        bar = self.table.verticalScrollBar()
        height = self.table.verticalHeader().defaultSectionSize()
        position = bar.value()
        size = len(self.model.rows)
        overflow = max(0, size + len(rows) - BUFFER_ROWS)
        self.adjusting = True
        if self.page_offset < start:
            if overflow:
                self.model.remove_buffer_rows(size - overflow, overflow, start)
            self.model.insert_buffer_rows(0, rows, ids, self.page_offset)
            position += len(rows) * height
            self.has_more = self.has_more or bool(overflow)
        else:
            # Keep the buffer aligned to page boundaries for backward reads.
            removed = PAGE_SIZE if overflow else 0
            if removed:
                self.model.remove_buffer_rows(0, removed, start + removed)
            self.model.insert_buffer_rows(size - removed, rows, ids, start + removed)
            position -= removed * height
            self.has_more = has_more
        self.table.doItemsLayout()
        bar.setValue(position)
        self.adjusting = False

    def jobs(self):
        return tuple(getattr(self, name) for name in JOB_NAMES)

    @property
    def restoring_workspace(self):
        return self.workspace is not None and self.workspace.restore_pending is self

    def collect_reads(self):
        for name in JOB_NAMES:
            job = getattr(self, name)
            if job is None or not job[1].done() or (name == "find_job" and self.page_job):
                continue
            token, future = job
            setattr(self, name, None)
            if name in ("export_job", "save_job"):
                self.write_destination = None
            restoring = name == "view_job" and self.restoring_workspace
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
                elif name == "find_job":
                    self.apply_find(result)
                elif name == "query_job":
                    self.show_query_result(*result)
                elif name == "save_job":
                    old_path = self.path
                    (self.path, self.revision), self.source_snapshot = result
                    if self.path != old_path:
                        remember_file(self.settings, self.path, self.csv_format)
                    self.file_size = self.revision[2]
                    self.clean_pos = self.history_pos
                    self.setWindowTitle(f"CSV Workbench — {self.path.name}")
                else:
                    QMessageBox.information(self, "Export CSV", f"Exported {result:,} rows.")
            except CancelledError:
                if restoring:
                    self.workspace.finish_restore("The workspace restore was cancelled.")
                    return
                if name == "view_job" and self.refreshing_edit:
                    self.rollback_edit()
                if name == "count_job":
                    self.total_rows = "cancelled"
                if name == "page_job" and self.page_target_column is not None:
                    self.find_match = None
            except (duckdb.Error, OSError, ValueError) as exc:
                if restoring:
                    self.workspace.finish_restore(f"{self.view_request[0]}: {exc}")
                    return
                if name == "view_job" and self.refreshing_edit:
                    self.rollback_edit()
                if name == "count_job":
                    self.total_rows = "unavailable"
                else:
                    if name == "page_job":
                        self.has_more = False
                    QMessageBox.critical(self, "CSV Workbench", str(exc))
            if restoring:
                self.poll.stop()
                self.workspace.restore_next_tab()
                return
        self.update_status()
        if not self.cancel_button.isEnabled():
            self.poll.stop()
        if self.workspace is not None and self.path is None and self.view_job is None:
            self.workspace.discard_empty_tab(self)

    def update_edit_actions(self):
        self.copy_action.setEnabled(self.path is not None)
        self.transpose_action.setEnabled(
            self.path is not None and self.distinct is None and self.view_job is None
            and self.table.selectionModel().hasSelection()
        )
        self.record_action.setEnabled(
            self.path is not None and self.distinct is None and self.view_job is None
            and self.table.currentIndex().isValid()
        )
        for action in (
            self.value_action,
            self.paste_action,
            self.clear_action,
            self.add_row_action,
            self.clone_action,
            self.delete_action,
        ):
            action.setEnabled(self.model.editable)

    def update_status(self):
        def progress_for(job, label):
            if job is None:
                return ""
            token, future = job
            if not token.cancelled and not future.running() and not future.done():
                return f" • Queued: {label}"
            return f" • {label}…"

        jobs = self.jobs()
        available = self.path is not None and not (
            self.view_job or self.save_job or self.export_job
        )
        self.save_action.setEnabled(available and self.dirty)
        self.save_as_action.setEnabled(available)
        self.go_to_row_action.setEnabled(available and self.page_job is None and self.model.rowCount() > 0)
        self.find_action.setEnabled(self.path is not None)
        can_manage_columns = available and self.distinct is None
        self.columns_action.setEnabled(can_manage_columns)
        self.table.horizontalHeader().setSectionsMovable(can_manage_columns)
        can_find = (
            available and not self.find_bar.isHidden() and bool(self.model.rowCount())
            and bool(self.find_input.text()) and self.find_job is None and self.page_job is None
        )
        self.find_next.setEnabled(can_find)
        self.find_previous.setEnabled(can_find)
        self.find_stop.setEnabled(self.find_job is not None or (
            self.page_job is not None and self.page_target_column is not None
        ))
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
        progress = "".join(
            progress_for(job, label)
            for job, label in (
                (self.page_job, "Loading more"),
                (self.find_job, "Searching"),
                (self.view_job, "Reading CSV"),
                (self.count_job, "Counting rows"),
                (self.export_job, "Exporting CSV"),
                (self.query_job, "Running SQL"),
                (self.save_job, "Saving CSV"),
            )
        )
        if any(job is not None and job[0].cancelled for job in jobs):
            progress += " • Cancelling…"
        if self.distinct is not None:
            progress += f" • Unique: {self.distinct}"
        if self.dirty:
            progress += " • Unsaved edits"
        self.status.setText(
            f"File: {self.file_size:,} bytes • Total rows: {total if total is not None else 'counting…'}"
            f" • Buffered: {len(self.model.rows):,}{progress}"
        )

        if self.workspace is not None:
            self.workspace.update_document_tab(self)

    def confirm_discard_edits(self):
        if not self.dirty and self.pending_history is None:
            return True
        return (
            QMessageBox.question(
                self, "Unsaved edits", f"Discard unsaved changes to {self.path.name if self.path else 'the CSV'}?"
            )
            == QMessageBox.StandardButton.Yes
        )

    def closeEvent(self, event):
        if not self.confirm_discard_edits():
            event.ignore()
            return
        self.shutdown_document()
        super().closeEvent(event)

    def shutdown_document(self):
        self.cancel_operations()
        self.poll.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.result = None
        self.view_job = None
        self.page_job = None


class WorkspaceWindow(QWidget):
    """Keep each CSV's view, jobs and edit history alive in its own tab."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Workbench")
        self.setAcceptDrops(True)
        self.heavy_executor = ThreadPoolExecutor(max_workers=1)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabWidget::tab-bar { alignment: left; }")
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.restore_pending = None
        self.restore_progress = None
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.activate_document)
        for key, callback in (
            (QKeySequence.StandardKey.AddTab, self.add_document_tab),
            (QKeySequence.StandardKey.Close, lambda: self.close_tab(self.tabs.currentIndex())),
            (QKeySequence.StandardKey.NextChild, lambda: self.switch_tab(1)),
            (QKeySequence.StandardKey.PreviousChild, lambda: self.switch_tab(-1)),
        ):
            shortcut = QShortcut(self)
            keys = QKeySequence.keyBindings(key)
            if sys.platform == "darwin":
                # Qt's Meta modifier is the physical Control key on macOS.
                if key == QKeySequence.StandardKey.NextChild:
                    keys.append(QKeySequence("Meta+Tab"))
                elif key == QKeySequence.StandardKey.PreviousChild:
                    keys.extend((QKeySequence("Meta+Shift+Tab"), QKeySequence("Meta+Shift+Backtab")))
            shortcut.setKeys(keys)
            shortcut.activated.connect(callback)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.add_document_tab()

    def switch_tab(self, direction):
        if self.tabs.count():
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + direction) % self.tabs.count())

    def add_document_tab(self):
        document = MainWindow(workspace=self)
        self.tabs.addTab(document, "New tab")
        self.tabs.setCurrentWidget(document)
        return document

    def documents(self):
        return [self.tabs.widget(index) for index in range(self.tabs.count())]

    @staticmethod
    def path_identity(path):
        try:
            return path.resolve()
        except (OSError, RuntimeError):
            return path.absolute()

    def document_for_path(self, path):
        path = self.path_identity(path)
        for document in self.documents():
            candidate = document.path
            if candidate is None and document.view_job is not None:
                candidate = document.view_request[0]
            if candidate is not None and self.path_identity(candidate) == path:
                return document
        return None

    def writing_document(self, path):
        path = self.path_identity(path)
        for document in self.documents():
            if document.write_destination is not None and self.path_identity(document.write_destination) == path:
                return document
        return None

    def destination_busy(self, path, *, except_document):
        return any(
            document is not None and document is not except_document
            for document in (self.document_for_path(path), self.writing_document(path))
        )

    def open_document(self, path, selection, fmt):
        document = self.document_for_path(path)
        if document is not None:
            self.tabs.setCurrentWidget(document)
            return
        if self.writing_document(path) is not None:
            QMessageBox.warning(self, "Open CSV", "Wait for the write to finish before opening this file.")
            return
        document = self.tabs.currentWidget()
        if document.path is not None or document.view_job is not None:
            document = self.add_document_tab()
        document.load_view(path, selection, new_file=True, fmt=fmt)

    def save_workspace_file(self):
        documents = self.documents()
        if any(document.view_job or document.save_job for document in documents):
            QMessageBox.warning(self, "Save workspace", "Wait for the tabs to finish loading or saving.")
            return
        documents = [document for document in documents if document.path is not None]
        if not documents:
            return
        active = self.tabs.currentWidget()
        if active not in documents:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save workspace", "workspace.csvsession", "Session (*.csvsession)"
        )
        if filename:
            destination = Path(filename)
            if not destination.suffix:
                destination = destination.with_suffix(".csvsession")
            try:
                tabs = [(doc.path, doc.selection, doc.csv_format) for doc in documents]
                save_workspace_session(destination, tabs, documents.index(active))
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Save workspace", str(exc))

    def restore_workspace_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Restore workspace", "", "Session (*.csvsession)"
        )
        if filename:
            try:
                self.restore_workspace(*load_workspace_session(Path(filename)))
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Restore workspace", str(exc))

    def restore_workspace(self, tabs, active):
        if self.restore_progress is not None:
            return
        documents = self.documents()
        if any(document.write_destination is not None for document in documents):
            QMessageBox.warning(self, "Restore workspace", "Wait for saves and exports to finish.")
            return
        if not all(document.confirm_discard_edits() for document in documents):
            return
        self.restore_progress = QProgressDialog("Opening tabs…", "Cancel", 0, 0, self)
        self.restore_progress.setWindowTitle("Restore workspace")
        self.restore_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.restore_progress.canceled.connect(lambda: self.finish_restore(cancelled=True))
        self.restore_progress.show()
        self.restore_active = active
        self.restore_queue = iter(tabs)
        self.restored_tabs = []
        self.restore_next_tab()

    def restore_next_tab(self):
        if self.restore_pending is not None:
            self.restored_tabs.append(self.restore_pending)
            self.restore_pending = None
        entry = next(self.restore_queue, None)
        if entry is None:
            self.finish_restore()
            return
        path, selection, fmt = entry
        self.restore_progress.setLabelText(f"Opening {path.name}…")
        document = MainWindow(workspace=self)
        document.setParent(self)
        self.restore_pending = document
        document.load_view(path, selection, new_file=True, fmt=fmt)

    def finish_restore(self, error=None, *, cancelled=False):
        if self.restore_progress is None:
            return
        progress, self.restore_progress = self.restore_progress, None
        progress.hide()
        progress.deleteLater()
        if error or cancelled:
            if self.restore_pending is not None:
                self.restored_tabs.append(self.restore_pending)
            self.restore_pending = None
            for document in self.restored_tabs:
                document.shutdown_document()
                document.deleteLater()
            self.restored_tabs = []
            if error:
                QMessageBox.critical(self, "Restore workspace", error)
            return
        old_documents = self.documents()
        with QSignalBlocker(self.tabs):
            self.tabs.clear()
            for document in self.restored_tabs:
                self.tabs.addTab(document, "")
            self.tabs.setCurrentIndex(self.restore_active)
        for document in old_documents:
            document.shutdown_document()
            document.deleteLater()
        for document in reversed(self.restored_tabs):
            remember_file(document.settings, document.path, document.csv_format)
            document.update_status()
        self.restored_tabs = []
        self.activate_document()

    def update_document_tab(self, document):
        index = self.tabs.indexOf(document)
        if index < 0:
            return
        path = document.path
        if path is None and document.view_job is not None:
            path = document.view_request[0]
        label = path.name if path is not None else "New tab"
        if document.dirty or document.pending_history is not None:
            label += " *"
        self.tabs.setTabText(index, label.replace("&", "&&"))
        self.tabs.setTabToolTip(index, str(path) if path is not None else "")
        if self.tabs.currentWidget() is document:
            self.update_title()

    def activate_document(self):
        document = self.tabs.currentWidget()
        if (
            document is not None and document.path is not None
            and document.total_rows is None and document.count_job is None
        ):
            document.count_job = document.submit(count_rows, document.path, fmt=document.csv_format)
            document.poll.start()
            document.update_status()
        self.update_title()

    def update_title(self):
        document = self.tabs.currentWidget()
        path = document.path if document is not None else None
        self.setWindowTitle(f"CSV Workbench — {path.name}" if path else "CSV Workbench")

    def discard_empty_tab(self, document):
        if self.tabs.count() > 1 and self.tabs.indexOf(document) >= 0:
            self.close_tab(self.tabs.indexOf(document))

    def close_tab(self, index):
        document = self.tabs.widget(index)
        was_current = index == self.tabs.currentIndex()
        if document is not None and document.close():
            self.tabs.removeTab(index)
            document.deleteLater()
            if not self.tabs.count():
                self.add_document_tab()
            if was_current:
                active = self.tabs.currentWidget()
                (active.table if active.path is not None else active.open_button).setFocus()

    def dragEnterEvent(self, event):
        self.tabs.currentWidget().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.tabs.currentWidget().dragMoveEvent(event)

    def dropEvent(self, event):
        self.tabs.currentWidget().dropEvent(event)

    def closeEvent(self, event):
        self.finish_restore(cancelled=True)
        documents = self.documents()
        if not all(document.confirm_discard_edits() for document in documents):
            event.ignore()
            return
        for document in documents:
            document.shutdown_document()
        self.heavy_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)
