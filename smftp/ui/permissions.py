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
    def __init__(self, parent=None, mode=None, directory=False, name="", recursive=False):
        super().__init__(parent)
        title = self.tr("Permissions du dossier") if directory else self.tr("Permissions du fichier")
        self.setWindowTitle(f"{title} — {name}" if name else title)
        self.setMinimumWidth(380)
        # Special bits (setuid, setgid, sticky) are not editable here but are preserved.
        self.special = (mode or 0) & 0o7000
        grid = QGridLayout(self)
        self.boxes = []
        for col, title in enumerate(["Lire", "Écrire", "Exécuter"], 1):
            grid.addWidget(QLabel(self.tr(title)), 0, col)
        grid.setColumnMinimumWidth(0, 110)
        rights = ["Lire", "Écrire", "Traverser" if directory else "Exécuter"]
        if directory:
            grid.itemAtPosition(0, 3).widget().setText(self.tr("Traverser"))
        for row, title in enumerate(["Propriétaire", "Groupe", "Autres"], 1):
            grid.addWidget(QLabel(self.tr(title)), row, 0)
            for col in range(1, 4):
                box = QCheckBox()
                box.setAccessibleName(f"{self.tr(title)} — {self.tr(rights[col - 1])}")
                grid.addWidget(box, row, col)
                self.boxes.append(box)
                box.toggled.connect(self.update_octal)
        self.octal = QLineEdit()
        self.octal.setMaxLength(3)
        from PySide6.QtCore import QRegularExpression
        from PySide6.QtGui import QRegularExpressionValidator
        # Only octal digits can be typed (e.g. 644, 755).
        self.octal.setValidator(QRegularExpressionValidator(QRegularExpression("[0-7]{0,3}"), self.octal))
        self.octal.setPlaceholderText("644")
        self.octal.setAccessibleName(self.tr("Permissions en octal"))
        grid.addWidget(QLabel(self.tr("Octal")), 4, 0)
        grid.addWidget(self.octal, 4, 1, 1, 3)
        self.octal.textEdited.connect(self.update_boxes)
        self.notice = QLabel(self.tr("Le serveur n’indique pas les droits actuels : saisissez explicitement la valeur souhaitée."))
        self.notice.setWordWrap(True)
        self.notice.setVisible(mode is None)
        grid.addWidget(self.notice, 5, 0, 1, 4)
        # Remote folder: the same change can be applied to everything it contains.
        self.recursive_box = QCheckBox(self.tr("Appliquer aussi au contenu"))
        self.recursive_box.setToolTip(self.tr("Sous-dossiers et fichiers reçoivent chacun leur mode ; "
                                              "les liens symboliques ne sont pas suivis."))
        self.folder_mode = self.mode_field("755", self.tr("Mode des sous-dossiers"))
        self.file_mode = self.mode_field("644", self.tr("Mode des fichiers"))
        self.folder_label = QLabel(self.tr("Sous-dossiers"))
        self.file_label = QLabel(self.tr("Fichiers"))
        grid.addWidget(self.recursive_box, 6, 0, 1, 4)
        grid.addWidget(self.folder_label, 7, 0)
        grid.addWidget(self.folder_mode, 7, 1, 1, 3)
        grid.addWidget(self.file_label, 8, 0)
        grid.addWidget(self.file_mode, 8, 1, 1, 3)
        for widget in (self.recursive_box, self.folder_label, self.folder_mode, self.file_label, self.file_mode):
            widget.setVisible(recursive)
        for widget in (self.folder_mode, self.file_mode):
            widget.setEnabled(False)
        self.recursive_box.toggled.connect(self.folder_mode.setEnabled)
        self.recursive_box.toggled.connect(self.file_mode.setEnabled)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_valid)
        buttons.rejected.connect(self.reject)
        grid.addWidget(buttons, 9, 0, 1, 4)
        if mode is not None:
            text = f"{mode & 0o777:03o}"
            self.octal.setText(text)
            self.update_boxes(text)

    def mode_field(self, value, name):
        from PySide6.QtCore import QRegularExpression
        from PySide6.QtGui import QRegularExpressionValidator
        field = QLineEdit(value)
        field.setMaxLength(3)
        field.setValidator(QRegularExpressionValidator(QRegularExpression("[0-7]{0,3}"), field))
        field.setAccessibleName(name)
        return field

    def recursive(self):
        """(sub-folder mode, file mode) when the content is changed too, else None."""
        if not self.recursive_box.isChecked():
            return None
        return int(self.folder_mode.text(), 8), int(self.file_mode.text(), 8)

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
        values = [self.octal.text()]
        if self.recursive_box.isChecked():
            values += [self.folder_mode.text(), self.file_mode.text()]
        if all(len(value) == 3 and all(c in "01234567" for c in value) for value in values):
            self.accept()
            return
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, self.tr("Permissions"),
                            self.tr("Indiquez trois chiffres de 0 à 7, par exemple 644 (fichier) ou 755 (dossier)."))
        self.octal.setFocus()

    def mode(self):
        return self.special | int(self.octal.text(), 8)
