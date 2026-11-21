"""Cell editor lifecycle and bounded completion popup."""

from PySide6.QtCore import QEvent, QPersistentModelIndex, QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import QCompleter, QLineEdit, QStyledItemDelegate
from shiboken6 import isValid


class CompletionDelegate(QStyledItemDelegate):
    editorOpened = Signal()
    requested = Signal(object, str)
    cancelled = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.editor = None
        self.cell = QPersistentModelIndex()
        self.active = False
        self.dismissing = False
        self.timer = QTimer(self, interval=180, singleShot=True)
        self.timer.timeout.connect(self.request)
        self.closeEditor.connect(self.closed)

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            self.dismiss()
            self.editor, self.cell = editor, QPersistentModelIndex(index)
            completer = QCompleter(editor)
            completer.setModel(QStringListModel([], completer))
            completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)
            completer.popup().setAccessibleName('Cell value suggestions (maximum 100)')
            completer.popup().installEventFilter(self)
            editor.setCompleter(completer)
            completer.activated.connect(self.accepted)
            editor.textEdited.connect(self.changed)
            editor.destroyed.connect(lambda: self.closed(editor))
            self.editorOpened.emit()
        return editor

    def closed(self, editor, *_):
        if editor is self.editor:
            self.dismiss()
            self.editor = None
            self.cell = QPersistentModelIndex()

    def dismiss(self):
        if self.dismissing:
            return
        self.active = False
        self.timer.stop()
        self.cancelled.emit()
        editor = self.editor
        if editor is not None and isValid(editor):
            completer = editor.completer()
            self.dismissing = True
            try:
                completer.popup().hide()
                if isValid(completer):
                    completer.model().setStringList([])
            finally:
                self.dismissing = False

    def accepted(self, *_):
        self.active = False
        self.timer.stop()
        self.cancelled.emit()

    def begin(self):
        if self.editor is not None:
            self.active = True
            self.request()

    def changed(self, *_):
        if self.active:
            editor = self.editor
            self.dismiss()
            if self.editor is editor:
                self.active = True
                self.timer.start()

    def request(self):
        if self.active and self.editor is not None and self.cell.isValid():
            self.requested.emit(self.editor, self.editor.text())

    def show_values(self, values, truncated):
        if not self.active or self.editor is None:
            return
        completer = self.editor.completer()
        completer.model().setStringList(values)
        completer.setCompletionPrefix(self.editor.text())
        notice = ('First 100 suggestions; narrow the prefix' if truncated
                  else f'{len(values)} suggestions')
        self.editor.setToolTip(notice)
        completer.popup().setAccessibleDescription(notice)
        if values:
            completer.complete()

    def eventFilter(self, watched, event):
        if self.dismissing and event.type() == QEvent.Type.FocusOut:
            return False
        if (self.active and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape):
            self.dismiss()
            return True
        if watched is self.editor and event.type() == QEvent.Type.Hide:
            self.dismiss()
        # The standard delegate owns commit/cancel and Tab traversal.
        if watched is self.editor:
            return super().eventFilter(watched, event)
        return False
