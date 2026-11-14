"""Editable POSIX permissions with an octal representation."""
from PySide6.QtWidgets import QDialog, QGridLayout, QLabel, QCheckBox, QLineEdit, QDialogButtonBox


def parse_mode(text):
    """Return the POSIX mode described by a listing, or None when it is unknown.

    Accepts symbolic forms (``drwxr-xr-x``, ``rwsr-x--T``) and octal forms
    (``755``, ``0755``, ``4755``). MLSD ``perm`` facts such as ``elradfmw`` are
    not POSIX permissions and return None.
    """
    text = (text or "").strip()
    if text.isdigit() and 3 <= len(text) <= 4 and all(c in "01234567" for c in text):
        return int(text, 8) & 0o7777
    symbolic = text[-9:]
    if len(text) not in (9, 10) or len(symbolic) != 9:
        return None
    mode = 0
    specials = (0o4000, 0o2000, 0o1000)
    for index, char in enumerate(symbolic):
        position, bit = divmod(index, 3)
        expected = "rwx"[bit]
        value = 1 << (8 - index)
        if char == expected:
            mode |= value
        elif bit == 2 and char in ("s", "t"):
            mode |= value | specials[position]
        elif bit == 2 and char in ("S", "T"):
            mode |= specials[position]
        elif char != "-":
            return None
    return mode


class PermissionsDialog(QDialog):
    def __init__(self, parent=None, mode=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Permissions du fichier"))
        # Special bits (setuid, setgid, sticky) are not editable here but are preserved.
        self.special = (mode or 0) & 0o7000
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
        self.octal = QLineEdit()
        self.octal.setMaxLength(3)
        grid.addWidget(QLabel(self.tr("Octal")), 4, 0)
        grid.addWidget(self.octal, 4, 1, 1, 3)
        self.octal.textEdited.connect(self.update_boxes)
        self.notice = QLabel(self.tr("Le serveur n’indique pas les droits actuels : saisissez explicitement la valeur souhaitée."))
        self.notice.setWordWrap(True)
        self.notice.setVisible(mode is None)
        grid.addWidget(self.notice, 5, 0, 1, 4)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_valid)
        buttons.rejected.connect(self.reject)
        grid.addWidget(buttons, 6, 0, 1, 4)
        if mode is not None:
            text = f"{mode & 0o777:03o}"
            self.octal.setText(text)
            self.update_boxes(text)

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
        return self.special | int(self.octal.text(), 8)
