"""Small integrated editor and image preview."""
from pathlib import Path
from PySide6.QtCore import Signal, QRegularExpression
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
                               QPushButton, QLabel, QMessageBox, QScrollArea)


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


class Editor(QDialog):
    saved = Signal()

    def __init__(self, path: str, title: str, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(title)
        self.resize(900, 640)
        self.dirty = False
        layout = QVBoxLayout(self)
        self.message = QLabel(self.tr("Enregistrez pour envoyer les modifications au serveur."))
        layout.addWidget(self.message)
        self.text = QPlainTextEdit()
        self.text.setFont(QFont("Consolas", 11))
        self.text.setPlainText(self.path.read_text(encoding="utf-8"))
        self.highlighter = CodeHighlighter(self.text.document())
        self.text.textChanged.connect(self.mark_dirty)
        layout.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch()
        self.save_button = QPushButton(self.tr("Enregistrer et envoyer"))
        self.save_button.setObjectName("primary")
        self.save_button.setShortcut("Ctrl+S")
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.save_button)
        layout.addLayout(row)

    def mark_dirty(self):
        self.dirty = True

    def save(self):
        try:
            self.path.write_text(self.text.toPlainText(), encoding="utf-8")
            self.dirty = False
            self.saved.emit()
        except OSError:
            QMessageBox.warning(self, self.tr("Enregistrement"), self.tr("Impossible d’enregistrer la copie locale."))

    def closeEvent(self, event):
        if self.dirty and QMessageBox.question(self, self.tr("Modifications non enregistrées"), self.tr("Fermer et abandonner les modifications ?"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            event.ignore()
        else:
            event.accept()


class ImagePreview(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        area = QScrollArea()
        label = QLabel()
        label.setPixmap(QPixmap(path))
        area.setWidget(label)
        layout.addWidget(area)
