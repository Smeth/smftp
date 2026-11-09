"""Editable POSIX permissions with an octal representation."""
from PySide6.QtWidgets import QDialog, QGridLayout, QLabel, QCheckBox, QLineEdit, QDialogButtonBox


class PermissionsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Permissions du fichier"))
        grid = QGridLayout(self)
        self.boxes = []
        for col, title in enumerate(["Lire", "Écrire", "Exécuter"], 1):
            grid.addWidget(QLabel(self.tr(title)), 0, col)
        for row, title in enumerate(["Propriétaire", "Groupe", "Autres"], 1):
            grid.addWidget(QLabel(self.tr(title)), row, 0)
            for col in range(1, 4):
                box = QCheckBox()
                grid.addWidget(box, row, col)
                self.boxes.append(box)
                box.toggled.connect(self.update_octal)
        self.octal = QLineEdit("644")
        self.octal.setMaxLength(3)
        grid.addWidget(QLabel(self.tr("Octal")), 4, 0)
        grid.addWidget(self.octal, 4, 1, 1, 3)
        self.octal.textEdited.connect(self.update_boxes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_valid)
        buttons.rejected.connect(self.reject)
        grid.addWidget(buttons, 5, 0, 1, 4)
        self.update_boxes("644")

    def update_octal(self):
        value = sum((1 << (8 - i)) for i, box in enumerate(self.boxes) if box.isChecked())
        self.octal.setText(f"{value:03o}")

    def update_boxes(self, text):
        if len(text) != 3 or any(c not in "01234567" for c in text):
            return
        value = int(text, 8)
        for i, box in enumerate(self.boxes):
            box.blockSignals(True)
            box.setChecked(bool(value & (1 << (8 - i))))
            box.blockSignals(False)

    def accept_valid(self):
        value = self.octal.text()
        if len(value) == 3 and all(c in "01234567" for c in value):
            self.accept()

    def mode(self):
        return int(self.octal.text(), 8)
