"""Connection editor with optional operating-system credential storage."""
from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox, QSpinBox,
                               QCheckBox, QDialogButtonBox, QLabel, QWidget, QHBoxLayout, QPushButton, QFileDialog)
from smftp.models.entries import Connection, PROTOCOL_LABELS, DEFAULT_PORTS


class ConnectionDialog(QDialog):
    def __init__(self, parent=None, site=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Nouvelle connexion"))
        self.setMinimumWidth(440)
        self.save_only = False
        form = QFormLayout(self)
        form.addRow(QLabel(self.tr("Connectez votre espace distant")))
        self.name = QLineEdit()
        self.name.setPlaceholderText(self.tr("Ex. Site de production"))
        self.protocol = QComboBox()
        for value, label in PROTOCOL_LABELS.items():
            self.protocol.addItem(label, value)
        self.host = QLineEdit()
        self.host.setPlaceholderText("sftp.exemple.fr")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QPushButton(self.tr("Afficher"))
        self.show_password.setCheckable(True)
        self.show_password.setToolTip(self.tr("Afficher ou masquer le mot de passe saisi ou stocké dans le coffre-fort"))
        self.show_password.toggled.connect(self.toggle_password)
        password_row = QWidget()
        password_layout = QHBoxLayout(password_row)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.addWidget(self.password)
        password_layout.addWidget(self.show_password)
        self.key = QLineEdit()
        self.key.setPlaceholderText(self.tr("Chemin facultatif d’une clé SSH"))
        self.key_picker = QPushButton(self.tr("Parcourir…"))
        self.key_picker.clicked.connect(self.choose_key)
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key)
        key_layout.addWidget(self.key_picker)
        self.group = QLineEdit()
        self.local_directory = QLineEdit()
        self.remote_directory = QLineEdit()
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 300)
        self.timeout.setValue(20)
        self.timeout.setSuffix(self.tr(" s"))
        self.remember = QCheckBox(self.tr("Mémoriser dans le coffre-fort système"))
        for label, widget in [("Nom du profil", self.name), ("Protocole", self.protocol), ("Hôte", self.host), ("Port", self.port),
                              ("Utilisateur", self.user), ("Mot de passe / phrase secrète", password_row),
                              ("Clé privée", key_row), ("", self.remember),
                              ("Groupe de serveurs", self.group), ("Dossier local de départ", self.local_directory),
                              ("Dossier distant de départ", self.remote_directory), ("Délai réseau", self.timeout)]:
            form.addRow(self.tr(label), widget)
        self.warning = QLabel(self.tr("SFTP et FTPS chiffrent votre connexion. FTP transmet les données en clair."))
        self.warning.setWordWrap(True)
        form.addRow(self.warning)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        form.addRow(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Enregistrer et connecter"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Annuler"))
        self.save_button = buttons.addButton(self.tr("Enregistrer seulement"), QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.accept_save_only)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        def validate():
            from smftp.utils.profiles import profile_for
            try:
                profile_for(self.connection())
                valid = True
                self.validation.clear()
            except (ValueError, TypeError) as error:
                valid = False
                self.validation.setText(str(error))
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)
            self.save_button.setEnabled(valid)
        self.host.textChanged.connect(validate)
        self.user.textChanged.connect(validate)
        self.protocol.currentIndexChanged.connect(lambda _: self.port.setValue(DEFAULT_PORTS[self.protocol_value()]))
        self.protocol.currentIndexChanged.connect(lambda _: (self.key.setEnabled(self.protocol_value() == "SFTP"), self.key_picker.setEnabled(self.protocol_value() == "SFTP")))
        if site:
            self.name.setText(site.get("name", site.get("host", "")))
            self.set_protocol(site.get("protocol", "SFTP"))
            self.host.setText(site.get("host", ""))
            self.user.setText(site.get("user", ""))
            self.port.setValue(site.get("port", 22))
            self.key.setText(site.get("key_file", ""))
            self.group.setText(site.get("group", ""))
            self.local_directory.setText(site.get("local_directory", ""))
            self.remote_directory.setText(site.get("remote_directory", ""))
            self.timeout.setValue(site.get("timeout", 20))
            try:
                import keyring
                stored = keyring.get_password("SMFTP", self.connection().identity)
                self.password.setText(stored or "")
                self.remember.setChecked(stored is not None)
            except Exception:
                self.warning.setText(self.tr("Le coffre-fort système est indisponible. Saisissez le secret pour cette connexion."))

    def toggle_password(self, visible: bool):
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_password.setText(self.tr("Masquer") if visible else self.tr("Afficher"))

    def done(self, result):
        self.show_password.setChecked(False)
        super().done(result)

    def protocol_value(self) -> str:
        return self.protocol.currentData() or "SFTP"

    def set_protocol(self, protocol: str) -> None:
        index = self.protocol.findData(protocol)
        self.protocol.setCurrentIndex(index if index >= 0 else 0)

    def accept_save_only(self):
        self.save_only = True
        self.accept()

    def choose_key(self):
        filename, _ = QFileDialog.getOpenFileName(self, self.tr("Sélectionner une clé privée SSH"),
            self.key.text(), self.tr("Clés SSH (*.pem *.key id_rsa id_ed25519 id_ecdsa);;Tous les fichiers (*)"))
        if filename:
            self.key.setText(filename)

    def connection(self) -> Connection:
        protocol = self.protocol_value()
        return Connection(self.host.text().strip(), self.user.text().strip(), protocol,
                          self.port.value(), self.password.text(), self.key.text().strip() if protocol == "SFTP" else "", self.remember.isChecked(), self.name.text().strip(),
                          self.group.text().strip(), self.local_directory.text().strip(), self.remote_directory.text().strip(), self.timeout.value())
