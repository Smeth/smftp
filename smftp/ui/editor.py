"""Small integrated editor and image preview."""
from pathlib import Path
from PySide6.QtCore import Signal, QRegularExpression, Qt, QRect, QSize
from PySide6.QtGui import (QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QPixmap, QPainter,
                           QTextCursor, QTextDocument, QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPlainTextEdit,
                               QPushButton, QLabel, QMessageBox, QScrollArea, QWidget, QLineEdit,
                               QCheckBox, QInputDialog)


class CodeHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        rules = [
            (r'\b(def|class|return|import|from|if|else|elif|for|while|try|except|with|as|async|await|function|const|let|var|export|true|false|null|None|True|False)\b', "#b995ef"),
            (r'\b[0-9]+(?:\.[0-9]+)?\b', "#e6b36a"),
            (r'"(?:[^"\\]|\\.)*"|\x27(?:[^\x27\\]|\\.)*\x27', "#75bb9b"),
            (r'#[^\n]*|//[^\n]*', "#8191a6"),
            (r'</?[\w:-]+|[\w-]+(?=\s*:)', "#78aff5"),
        ]
        for pattern, color in rules:
            matches = QRegularExpression(pattern).globalMatch(text)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            while matches.hasNext():
                match = matches.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class LineNumbers(QWidget):
    """Gutter painted by the editor."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class CodeEdit(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.gutter = LineNumbers(self)
        self.blockCountChanged.connect(self.update_margin)
        self.updateRequest.connect(self.update_gutter)
        self.update_margin()

    def gutter_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def update_margin(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        area = self.contentsRect()
        self.gutter.setGeometry(QRect(area.left(), area.top(), self.gutter_width(), area.height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), self.palette().alternateBase())
        painter.setPen(self.palette().placeholderText().color())
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                painter.drawText(0, top, self.gutter.width() - 6, height,
                                 Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            top += round(self.blockBoundingRect(block).height())
            block = block.next()


def detect_format(raw: bytes):
    """Return (text, newline, bom) so that saving keeps the original file format."""
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig" if bom else "utf-8")
    crlf = text.count("\r\n")
    bare_lf = text.count("\n") - crlf
    bare_cr = text.count("\r") - crlf
    if crlf and crlf >= bare_lf:
        newline = "\r\n"
    elif bare_cr and not bare_lf and not crlf:
        newline = "\r"
    else:
        newline = "\n"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, newline, bom


class Editor(QDialog):
    saved = Signal()
    NEWLINES = {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}

    def __init__(self, path: str, title: str, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(title)
        self.resize(900, 640)
        self.dirty = False
        text, self.newline, self.bom = detect_format(self.path.read_bytes())
        layout = QVBoxLayout(self)
        self.message = QLabel(self.tr("Enregistrez pour envoyer les modifications au serveur."))
        layout.addWidget(self.message)
        self.text = CodeEdit()
        self.text.setFont(QFont("Consolas", 11))
        self.text.setPlainText(text)
        self.text.setAccessibleName(self.tr("Contenu du fichier"))
        self.highlighter = CodeHighlighter(self.text.document())
        # Only real content edits change the document's modification flag;
        # formatting applied by the highlighter does not.
        self.text.document().setModified(False)
        self.text.document().modificationChanged.connect(self.mark_dirty)
        layout.addWidget(self.text)
        layout.addWidget(self.build_find_bar())
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("muted")
        row.addWidget(self.status)
        row.addStretch()
        self.save_button = QPushButton(self.tr("Enregistrer et envoyer"))
        self.save_button.setObjectName("primary")
        self.save_button.setShortcut("Ctrl+S")
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        for keys, callback in [("Ctrl+F", lambda: self.show_find(False)), ("Ctrl+H", lambda: self.show_find(True)),
                               ("F3", lambda: self.find(False)), ("Shift+F3", lambda: self.find(True)),
                               ("Ctrl+G", self.go_to_line)]:
            QShortcut(QKeySequence(keys), self, activated=callback)
        self.text.cursorPositionChanged.connect(self.update_status)
        self.update_status()

    # ---------- find / replace ----------
    def build_find_bar(self):
        self.find_bar = QWidget()
        grid = QGridLayout(self.find_bar)
        grid.setContentsMargins(0, 0, 0, 0)
        self.find_field = QLineEdit()
        self.find_field.setPlaceholderText(self.tr("Rechercher"))
        self.find_field.setAccessibleName(self.tr("Texte à rechercher"))
        self.find_field.returnPressed.connect(lambda: self.find(False))
        self.replace_field = QLineEdit()
        self.replace_field.setPlaceholderText(self.tr("Remplacer par"))
        self.replace_field.setAccessibleName(self.tr("Texte de remplacement"))
        self.case = QCheckBox(self.tr("Respecter la casse"))
        self.find_result = QLabel()
        grid.addWidget(self.find_field, 0, 0)
        for column, (label, callback) in enumerate([("Précédent", lambda: self.find(True)), ("Suivant", lambda: self.find(False))], 1):
            button = QPushButton(self.tr(label))
            button.clicked.connect(callback)
            grid.addWidget(button, 0, column)
        grid.addWidget(self.case, 0, 3)
        grid.addWidget(self.find_result, 0, 4)
        grid.addWidget(self.replace_field, 1, 0)
        self.replace_buttons = []
        for column, (label, callback) in enumerate([("Remplacer", self.replace_one), ("Tout remplacer", self.replace_all)], 1):
            button = QPushButton(self.tr(label))
            button.clicked.connect(callback)
            grid.addWidget(button, 1, column)
            self.replace_buttons.append(button)
        self.find_bar.hide()
        return self.find_bar

    def show_find(self, replace):
        self.find_bar.show()
        for widget in [self.replace_field, *self.replace_buttons]:
            widget.setVisible(replace)
        selected = self.text.textCursor().selectedText()
        if selected and " " not in selected:
            self.find_field.setText(selected)
        self.find_field.setFocus()
        self.find_field.selectAll()

    def flags(self, backward=False):
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        if self.case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        return flags

    def find(self, backward=False):
        needle = self.find_field.text()
        if not needle:
            self.show_find(self.replace_field.isVisible())
            return False
        if self.text.find(needle, self.flags(backward)):
            self.find_result.setText("")
            return True
        # Wrap around once.
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
        self.text.setTextCursor(cursor)
        found = self.text.find(needle, self.flags(backward))
        self.find_result.setText("" if found else self.tr("Introuvable"))
        return found

    def selection_matches(self):
        selected = self.text.textCursor().selectedText()
        needle = self.find_field.text()
        return bool(needle) and (selected == needle if self.case.isChecked() else selected.casefold() == needle.casefold())

    def replace_one(self):
        if self.selection_matches():
            self.text.textCursor().insertText(self.replace_field.text())
        self.find(False)

    def replace_all(self):
        needle = self.find_field.text()
        if not needle:
            return 0
        document = self.text.document()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()  # One undo step.
        count = 0
        found = document.find(needle, 0, self.flags())
        while not found.isNull():
            found.insertText(self.replace_field.text())
            count += 1
            found = document.find(needle, found, self.flags())
        cursor.endEditBlock()
        self.find_result.setText(self.tr("%n remplacement(s)", "", count))
        return count

    def go_to_line(self):
        line, ok = QInputDialog.getInt(self, self.tr("Aller à la ligne"), self.tr("Ligne :"),
                                       self.text.textCursor().blockNumber() + 1, 1, self.text.blockCount())
        if ok:
            block = self.text.document().findBlockByNumber(line - 1)
            self.text.setTextCursor(QTextCursor(block))
            self.text.centerCursor()

    def update_status(self):
        cursor = self.text.textCursor()
        self.status.setText(self.tr("Ligne %s, colonne %s") % (cursor.blockNumber() + 1, cursor.positionInBlock() + 1)
                            + f"    UTF-8{' avec BOM' if self.bom else ''} · {self.NEWLINES[self.newline]}")

    # ---------- state ----------
    def mark_dirty(self, modified=True):
        self.dirty = bool(modified)

    def save(self):
        try:
            content = self.text.toPlainText().replace("\n", self.newline).encode("utf-8")
            self.path.write_bytes((b"\xef\xbb\xbf" if self.bom else b"") + content)
            self.text.document().setModified(False)
            self.dirty = False
            self.saved.emit()
        except OSError:
            QMessageBox.warning(self, self.tr("Enregistrement"), self.tr("Impossible d’enregistrer la copie locale."))

    def confirm_discard(self):
        """Single guard for every closing path: X button, Escape, parent close, reject()."""
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, self.tr("Modifications non enregistrées"), self.tr("Fermer et abandonner les modifications ?"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.dirty = False  # Discarded edits are no longer pending for the main window.
        return True

    def reject(self):
        # Escape first closes the find bar when it has the focus.
        if self.find_bar.isVisible() and self.find_bar.isAncestorOf(QApplication.focusWidget()):
            self.find_bar.hide()
            self.text.setFocus()
            return
        # QDialog maps Escape to reject(), which would otherwise bypass closeEvent.
        if self.confirm_discard():
            super().reject()

    def closeEvent(self, event):
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()


class ImagePreview(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.resize(800, 600)
        self.pixmap = QPixmap(path)
        self.fit = True
        self.zoom = 1.0
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.info = QLabel()
        self.info.setObjectName("muted")
        bar.addWidget(self.info)
        bar.addStretch()
        self.controls = []
        for label, name, callback in [("−", "Zoom arrière", lambda: self.set_zoom(self.current_scale() / 1.25)),
                                      ("+", "Zoom avant", lambda: self.set_zoom(self.current_scale() * 1.25)),
                                      ("100 %", "Taille réelle", lambda: self.set_zoom(1.0)),
                                      ("Ajuster", "Ajuster à la fenêtre", self.fit_window)]:
            button = QPushButton(self.tr(label))
            button.setToolTip(self.tr(name))
            button.setAccessibleName(self.tr(name))
            button.clicked.connect(callback)
            bar.addWidget(button)
            self.controls.append(button)
        layout.addLayout(bar)
        self.area = QScrollArea()
        self.area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.area.setWidgetResizable(False)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.area.setWidget(self.label)
        layout.addWidget(self.area)
        for keys, callback in [("Ctrl++", self.controls[1].click), ("Ctrl+=", self.controls[1].click),
                               ("Ctrl+-", self.controls[0].click), ("Ctrl+0", self.controls[2].click)]:
            QShortcut(QKeySequence(keys), self, activated=callback)
        if self.pixmap.isNull():
            for button in self.controls:
                button.setEnabled(False)
            self.label.setText(self.tr("Impossible d’afficher cette image : fichier illisible ou format non pris en charge."))
            self.label.setWordWrap(True)
            self.area.setWidgetResizable(True)
            self.info.setText("")
        else:
            size = Path(path).stat().st_size
            from smftp.ui.explorer import size_label
            self.info.setText(f"{self.pixmap.width()} × {self.pixmap.height()} px • {size_label(size)}")
            self.render()

    def current_scale(self):
        if not self.fit:
            return self.zoom
        viewport = self.area.viewport().size()
        ratio = min(viewport.width() / max(1, self.pixmap.width()), viewport.height() / max(1, self.pixmap.height()))
        return min(1.0, ratio)  # Small images are not enlarged when fitting.

    def set_zoom(self, value):
        self.fit = False
        self.zoom = max(0.05, min(16.0, value))
        self.render()

    def fit_window(self):
        self.fit = True
        self.render()

    def render(self):
        if self.pixmap.isNull():
            return
        scale = self.current_scale()
        size = self.pixmap.size() * scale
        self.label.setPixmap(self.pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
        self.label.resize(size)
        self.setWindowTitle(f"{self.windowTitle().split(' — ')[0]} — {round(scale * 100)} %")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit:
            self.render()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and not self.pixmap.isNull():
            self.set_zoom(self.current_scale() * (1.25 if event.angleDelta().y() > 0 else 0.8))
            event.accept()
        else:
            super().wheelEvent(event)
