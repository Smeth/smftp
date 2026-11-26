"""Connection editor with optional operating-system credential storage."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox, QSpinBox, QCompleter,
                               QCheckBox, QDialogButtonBox, QLabel, QWidget, QHBoxLayout, QFileDialog)
from smftp.models.entries import (Connection, PROTOCOL_LABELS, DEFAULT_PORTS, DEFAULT_PROXY_PORTS, ENCODINGS,
                                  PROXY_TYPES, format_offset, normalize_encoding, normalize_offset, uses_ssh)
from smftp.ui.icons import icon_button, make_icon, theme_of, recolor

DEFAULT_PROTOCOL = "SFTP"  # Encrypted by default; plain FTP stays one choice away.
# URL scheme -> stored protocol. ftps:// is implicit TLS by convention, ftpes:// explicit.
SCHEMES = {"sftp": "SFTP", "ssh": "SSH", "ftp": "FTP", "ftps": "FTPSI", "ftpes": "FTPS"}


def parse_address(text: str) -> dict:
    """Split what people paste into the host field: ``ftp://user@host:2121/path``,
    ``user@host:22`` or ``host:21``. Returns only the parts that were present."""
    text = text.strip()
    if not text:
        return {}
    parts = {}
    if "://" in text:
        scheme = text.split("://", 1)[0].lower()
        if scheme not in SCHEMES:
            return {}
        parts["protocol"] = SCHEMES[scheme]
    else:
        text = "//" + text
    try:
        url = urlsplit(text)
        port = url.port
    except ValueError:
        return {}
    if not url.hostname:
        return {}
    parts["host"] = url.hostname
    if url.username:
        parts["user"] = unquote(url.username)
    if url.password:
        parts["password"] = unquote(url.password)
    if port:
        parts["port"] = port
    if url.path and url.path != "/":
        parts["remote_directory"] = unquote(url.path)
    return parts


class ConnectionDialog(QDialog):
    def __init__(self, parent=None, site=None, sites=None):
        super().__init__(parent)
        self.editing = bool(site)
        self.original = site
        # Saved profiles, to refuse a second profile for the same server or name.
        self.sites = list(sites if sites is not None else getattr(parent, "sites", []) or [])
        self.setWindowTitle(self.tr("Modifier la connexion") if site else self.tr("Nouvelle connexion"))
        # Wide enough for the four buttons (the primary one is bold), whatever rows are shown.
        self.setMinimumWidth(720)
        self.save_only = False
        self.touched = False
        dark = theme_of(parent)
        # The fields scroll when the screen is short (1080p at 150 %); the buttons stay visible.
        from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget, QFrame, QAbstractScrollArea
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("formScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        content = QWidget()
        content.setObjectName("formContent")
        self.scroll.setWidget(content)
        self.scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        outer.addWidget(self.scroll)
        form = QFormLayout(content)
        form.setContentsMargins(0, 0, 8, 0)
        self.form = form
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heading = QLabel(self.tr("Connectez votre espace distant"))
        heading.setObjectName("dialogTitle")
        form.addRow(heading)
        legend = QLabel(self.tr("Les champs marqués d’un * sont obligatoires."))
        legend.setObjectName("muted")
        form.addRow(legend)
        self.name = QLineEdit()
        self.name.setPlaceholderText(self.tr("Ex. Site de production (par défaut : l’hôte)"))
        self.protocol = QComboBox()
        for value, label in PROTOCOL_LABELS.items():
            self.protocol.addItem(self.tr(label), value)
        self.host = QLineEdit()
        self.host.setPlaceholderText(self.tr("ftp.exemple.fr ou ftp://utilisateur@hôte:21"))
        self.host_help = self.tr("Adresse du serveur. Vous pouvez coller une adresse complète "
                                 "(sftp://, ftp://, ftps://) : protocole, port et utilisateur seront remplis.")
        self.host.setToolTip(self.host_help)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.user = QLineEdit()
        self.user.setPlaceholderText(self.tr("Ex. anonymous"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(self.tr("Facultatif si vous utilisez une clé SSH"))
        self.password.setToolTip(self.tr("Sans clé SSH : mot de passe du compte.\n"
                                         "Avec une clé SSH : phrase secrète de la clé (jamais envoyée au serveur)."))
        # Show / hide sits inside the field, like in a browser.
        self.show_password = QAction(self.password)
        self.show_password.setCheckable(True)
        self.show_password.setProperty("iconName", "eye")
        self.show_password.setProperty("iconDark", dark)
        self.show_password.toggled.connect(self.toggle_password)
        self.password.addAction(self.show_password, QLineEdit.ActionPosition.TrailingPosition)
        self.toggle_password(False)
        self.key = QLineEdit()
        self.key.setPlaceholderText(self.tr("Chemin facultatif d’une clé SSH (SFTP et SSH)"))
        self.key_picker = icon_button("folder", self.tr("Choisir une clé privée SSH…"), self.choose_key)
        self.remember = QCheckBox(self.tr("Mémoriser dans le coffre-fort système"))
        self.group = QLineEdit()
        self.group.setPlaceholderText(self.tr("Ex. Clients, Production…"))
        groups = sorted({s.get("group", "") for s in self.sites if s.get("group")})
        if groups:
            completer = QCompleter(groups, self.group)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.group.setCompleter(completer)
        self.local_directory = QLineEdit()
        self.local_directory.setPlaceholderText(self.tr("Dossier ouvert à gauche après connexion"))
        self.local_picker = icon_button("folder", self.tr("Choisir le dossier local…"), self.choose_local_directory)
        self.remote_directory = QLineEdit()
        self.remote_directory.setPlaceholderText(self.tr("Ex. /var/www (par défaut : dossier d’accueil)"))
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 300)
        self.timeout.setValue(20)
        self.timeout.setSuffix(self.tr(" s"))
        self.encoding = QComboBox()
        for value, label in ENCODINGS.items():
            self.encoding.addItem(self.tr(label), value)
        self.encoding.setToolTip(self.tr(
            "Encodage des noms de fichiers sur le serveur FTP. Automatique convient presque toujours ; "
            "choisissez l’encodage exact si des accents s’affichent mal sur un ancien serveur."))
        self.transfer_mode = QComboBox()
        self.transfer_mode.addItem(self.tr("Passif (recommandé)"), True)
        self.transfer_mode.addItem(self.tr("Actif"), False)
        self.transfer_mode.setToolTip(self.tr(
            "Passif : votre poste ouvre la connexion de données ; fonctionne derrière la plupart des box et pare-feu.\n"
            "Actif : le serveur se connecte à votre poste ; à choisir seulement si le serveur refuse le mode passif."))
        self.use_agent = QCheckBox(self.tr("Utiliser l’agent SSH (Pageant ou OpenSSH)"))
        self.use_agent.setToolTip(self.tr("Les clés chargées dans Pageant ou dans l’agent OpenSSH de Windows "
                                          "sont aussi proposées au serveur (SFTP et SSH)."))
        self.proxy_type = QComboBox()
        for value, label in PROXY_TYPES.items():
            self.proxy_type.addItem(self.tr(label) if value == "none" else label, value)
        self.proxy_type.setToolTip(self.tr("Passer par un proxy SOCKS5 ou HTTP (méthode CONNECT) pour joindre le serveur. "
                                           "En FTP, seul le mode passif fonctionne à travers un proxy."))
        self.proxy_host = QLineEdit()
        self.proxy_host.setPlaceholderText(self.tr("Ex. proxy.entreprise.fr"))
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(DEFAULT_PROXY_PORTS["socks5"])
        self.proxy_port.setAccessibleName(self.tr("Port du proxy"))
        self.proxy_user = QLineEdit()
        self.proxy_user.setPlaceholderText(self.tr("Facultatif"))
        self.proxy_password = QLineEdit()
        self.proxy_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy_password.setPlaceholderText(self.tr("Conservé dans le coffre-fort système"))
        self.current_proxy = "none"
        self.time_offset = QComboBox()
        self.time_offset.addItem(self.tr("Automatique (détection par MDTM)"), None)
        for minutes in range(-12 * 60, 14 * 60 + 1, 15):
            self.time_offset.addItem(self.tr("Heure du serveur = {offset}").format(offset=format_offset(minutes)), minutes)
        self.time_offset.setToolTip(self.tr(
            "Sans MLSD, la liste FTP donne l’heure locale du serveur. Automatique compare cette heure à celle "
            "donnée par MDTM (en UTC) sur un fichier ; choisissez le fuseau du serveur si la détection échoue."))
        self.labels = {}
        rows = [("Nom du profil", self.name, False), ("Protocole", self.protocol, True),
                ("Hôte", self.host, True), ("Port", self.port, True), ("Utilisateur", self.user, True),
                ("Mot de passe / phrase secrète", self.password, False),
                ("Clé privée", self.with_button(self.key, self.key_picker), False), ("", self.remember, False)]
        advanced = [("Groupe de serveurs", self.group, False),
                    ("Dossier local de départ", self.with_button(self.local_directory, self.local_picker), False),
                    ("Dossier distant de départ", self.remote_directory, False), ("Délai réseau", self.timeout, False),
                    ("Mode de transfert", self.transfer_mode, False),
                    ("Encodage des noms", self.encoding, False),
                    ("Décalage horaire du serveur", self.time_offset, False),
                    ("Agent SSH", self.use_agent, False),
                    ("Proxy", self.proxy_type, False),
                    ("Hôte et port du proxy", self.with_button(self.proxy_host, self.proxy_port), False),
                    ("Utilisateur du proxy", self.proxy_user, False),
                    ("Mot de passe du proxy", self.proxy_password, False)]
        for label, widget, required in rows:
            self.add_row(form, label, widget, required)
        section = QLabel(self.tr("OPTIONS AVANCÉES"))
        section.setObjectName("eyebrow")
        form.addRow(section)
        for label, widget, required in advanced:
            self.add_row(form, label, widget, required)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        form.addRow(self.warning)
        self.validation = QLabel()
        self.validation.setObjectName("error")
        self.validation.setWordWrap(True)
        form.addRow(self.validation)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        self.test_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.test_result.hide()
        form.addRow(self.test_result)
        self.probe = None
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(self.tr("Enregistrer et connecter"))
        self.ok_button.setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Annuler"))
        self.save_button = buttons.addButton(self.tr("Enregistrer seulement"), QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button.clicked.connect(self.accept_save_only)
        self.test_button = buttons.addButton(self.tr("Tester la connexion"), QDialogButtonBox.ButtonRole.ActionRole)
        self.test_button.setToolTip(self.tr("Se connecter, s’authentifier et lire le dossier de départ, sans rien enregistrer."))
        self.test_button.clicked.connect(self.test_connection)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.current_protocol = DEFAULT_PROTOCOL
        self.protocol.currentIndexChanged.connect(self.protocol_changed)
        self.name.textChanged.connect(self.validate)
        self.port.valueChanged.connect(self.validate)
        for field in (self.host, self.user, self.key):
            field.textChanged.connect(self.validate)
            field.editingFinished.connect(self.mark_touched)
        self.host.editingFinished.connect(self.expand_address)
        self.transfer_mode.currentIndexChanged.connect(self.update_warning)
        self.proxy_type.currentIndexChanged.connect(self.proxy_changed)
        self.proxy_host.textChanged.connect(self.validate)
        self.proxy_port.valueChanged.connect(self.validate)
        self.set_protocol(DEFAULT_PROTOCOL)
        self.port.setValue(DEFAULT_PORTS[DEFAULT_PROTOCOL])
        if site:
            self.name.setText(site.get("name", site.get("host", "")))
            self.set_protocol(site.get("protocol", "SFTP"))
            self.host.setText(site.get("host", ""))
            self.user.setText(site.get("user", ""))
            self.port.setValue(site.get("port", DEFAULT_PORTS.get(self.protocol_value(), 21)))
            self.key.setText(site.get("key_file", ""))
            self.group.setText(site.get("group", ""))
            self.local_directory.setText(site.get("local_directory", ""))
            self.remote_directory.setText(site.get("remote_directory", ""))
            self.timeout.setValue(site.get("timeout", 20))
            self.transfer_mode.setCurrentIndex(0 if site.get("passive", True) else 1)
            encoding = normalize_encoding(site.get("encoding", "auto"))
            if self.encoding.findData(encoding) < 0:
                self.encoding.addItem(encoding, encoding)
            self.encoding.setCurrentIndex(self.encoding.findData(encoding))
            offset = normalize_offset(site.get("time_offset"))
            index = self.time_offset.findData(offset) if offset is not None else 0
            if index < 0:
                self.time_offset.addItem(self.tr("Heure du serveur = {offset}").format(offset=format_offset(offset)), offset)
                index = self.time_offset.count() - 1
            self.time_offset.setCurrentIndex(index)
            self.use_agent.setChecked(bool(site.get("use_agent", False)))
            kind = site.get("proxy_type", "none")
            self.proxy_type.setCurrentIndex(max(0, self.proxy_type.findData(kind)))
            self.proxy_host.setText(site.get("proxy_host", ""))
            self.proxy_port.setValue(int(site.get("proxy_port") or DEFAULT_PROXY_PORTS.get(kind, 1080)))
            self.proxy_user.setText(site.get("proxy_user", ""))
            self.touched = True
        self.protocol_changed()
        self.proxy_changed()
        if site:
            try:
                import keyring
                from smftp.utils.profiles import proxy_key
                identity = self.connection().identity
                stored = keyring.get_password("SMFTP", identity)
                self.password.setText(stored or "")
                self.remember.setChecked(stored is not None)
                if self.proxy_user.text():
                    self.proxy_password.setText(keyring.get_password("SMFTP", proxy_key(identity)) or "")
            except Exception:
                self.warning.setText(self.tr("Le coffre-fort système est indisponible. Saisissez le secret pour cette connexion."))
        recolor(self, dark)
        self.validate()
        (self.password if site else self.host).setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        # Never taller than the screen: the form scrolls instead.
        screen = self.screen() or (self.parentWidget().screen() if self.parentWidget() else None)
        if screen is not None:
            available = screen.availableGeometry()
            limit = int(available.height() * 0.92)
            self.setMaximumHeight(limit)
            if self.height() > limit:
                self.resize(self.width(), limit)
            frame = self.frameGeometry()
            if frame.bottom() > available.bottom() or frame.top() < available.top():
                self.move(self.x(), max(available.top(), available.top() + (available.height() - frame.height()) // 2))

    def add_row(self, form, label, widget, required):
        text = self.tr(label) + (" *" if required else "")
        form.addRow(text, widget)
        if label:
            caption = form.labelForField(widget)
            if required and caption is not None:
                caption.setToolTip(self.tr("Champ obligatoire"))
            widget.setAccessibleName(self.tr(label) + (self.tr(" (obligatoire)") if required else ""))
            self.labels[label] = caption

    @staticmethod
    def with_button(field, button):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(field)
        layout.addWidget(button)
        return row

    def protocol_changed(self, *_):
        protocol = self.protocol_value()
        # Follow the protocol's port unless the user typed a custom one.
        if self.port.value() == DEFAULT_PORTS.get(self.current_protocol):
            self.port.setValue(DEFAULT_PORTS[protocol])
        self.current_protocol = protocol
        sftp = uses_ssh(protocol)
        terminal = protocol == "SSH"
        self.key.setEnabled(sftp)
        self.key_picker.setEnabled(sftp)
        self.transfer_mode.setEnabled(not sftp)
        self.encoding.setEnabled(not sftp)
        self.time_offset.setEnabled(not sftp)
        self.use_agent.setEnabled(sftp)
        self.local_directory.setEnabled(not terminal)
        self.local_picker.setEnabled(not terminal)
        # SSH terminal: no local folder, transfer mode, file-name encoding or listing time to set.
        if hasattr(self, "form"):
            for widget in (self.local_directory.parentWidget(), self.transfer_mode, self.encoding, self.time_offset):
                if self.form.indexOf(widget) >= 0:
                    self.form.setRowVisible(widget, not terminal)
            # Only the height follows the hidden rows: the width keeps the buttons readable.
            self.resize(max(self.width(), self.sizeHint().width()), self.sizeHint().height())
        self.ok_button.setText(self.tr("Enregistrer et ouvrir le terminal") if terminal
                               else self.tr("Enregistrer et connecter"))
        self.update_warning()

    def proxy_changed(self, *_):
        kind = self.proxy_type.currentData() or "none"
        if self.proxy_port.value() == DEFAULT_PROXY_PORTS.get(self.current_proxy):
            self.proxy_port.setValue(DEFAULT_PROXY_PORTS[kind])
        self.current_proxy = kind
        for widget in (self.proxy_host, self.proxy_port, self.proxy_user, self.proxy_password):
            widget.setEnabled(kind != "none")
        self.validate()

    def update_warning(self, *_):
        protocol = self.protocol_value()
        terminal = protocol == "SSH"
        active = not uses_ssh(protocol) and not self.transfer_mode.currentData()
        if terminal:
            self.warning.setObjectName("muted")
            self.warning.setText(self.tr("Connexion chiffrée : SSH. Ce profil ouvre une console sur le serveur "
                                         "à la place de l’explorateur de fichiers."))
        elif protocol == "FTP":
            self.warning.setObjectName("warning")
            text = self.tr("⚠ FTP transmet le mot de passe et les fichiers en clair. "
                           "Préférez FTPS ou SFTP si le serveur les accepte.")
            if active:
                text += "\n" + self.tr("⚠ Mode actif sans chiffrement : le serveur se connecte à votre poste, "
                                       "ce qui demande d’ouvrir un port entrant dans le pare-feu, et les données "
                                       "circulent en clair. Préférez le mode passif.")
            self.warning.setText(text)
        else:
            self.warning.setObjectName("muted")
            self.warning.setText(self.tr("Connexion chiffrée : %s.") % self.tr(PROTOCOL_LABELS[protocol]))
        self.warning.style().unpolish(self.warning)
        self.warning.style().polish(self.warning)
        self.validate()

    def mark_touched(self):
        self.touched = True
        self.validate()

    def expand_address(self):
        """Fill protocol, port, user and folder from a pasted URL in the host field."""
        text = self.host.text().strip()
        if not any(marker in text for marker in ("://", "@", ":", "/")):
            return
        parts = parse_address(text)
        if not parts:
            return
        if "protocol" in parts:
            self.set_protocol(parts["protocol"])
        self.host.setText(parts["host"])
        if "port" in parts:
            self.port.setValue(parts["port"])
        if parts.get("user") and not self.user.text().strip():
            self.user.setText(parts["user"])
        if parts.get("password") and not self.password.text():
            self.password.setText(parts["password"])
        if parts.get("remote_directory") and not self.remote_directory.text().strip():
            self.remote_directory.setText(parts["remote_directory"])

    def problems(self):
        """Per-field messages; empty when the profile can be saved."""
        from smftp.utils.profiles import profile_for
        found = {}
        host = self.host.text().strip()
        if not host:
            found["Hôte"] = self.tr("Indiquez l’hôte du serveur.")
        if not self.user.text().strip():
            found["Utilisateur"] = self.tr("Indiquez le nom d’utilisateur (« anonymous » pour un FTP public).")
        key = self.key.text().strip()
        if key and uses_ssh(self.protocol_value()) and not Path(key).is_file():
            found["Clé privée"] = self.tr("Clé privée introuvable : %s") % key
        if (self.proxy_type.currentData() or "none") != "none":
            if not self.proxy_host.text().strip():
                found["Proxy"] = self.tr("Indiquez l’hôte du proxy.")
            elif not uses_ssh(self.protocol_value()) and not self.transfer_mode.currentData():
                found["Proxy"] = self.tr("Le mode actif est impossible à travers un proxy : "
                                         "choisissez le mode passif dans le profil.")
        if not found:
            try:
                profile_for(self.connection())
            except (ValueError, TypeError) as error:
                message = str(error)
                # The message starts with the field name, in French or translated.
                field = next((f for f in ("Hôte", "Utilisateur", "Proxy")
                              if message.startswith(f) or message.startswith(self.tr(f))), "Port")
                found[field] = message
        return found

    def duplicates(self):
        """Another saved profile with the same server account or the same name."""
        from smftp.utils.profiles import identity
        found = {}
        try:
            mine = self.connection()
        except (ValueError, TypeError):
            return found
        name = (mine.name or mine.host).casefold()
        original = None
        if self.original:
            try:
                original = identity(self.original)
            except (ValueError, TypeError):
                pass
        for site in self.sites:
            try:
                other = identity(site)
            except (ValueError, TypeError):
                continue
            if other == original:
                continue  # The profile being edited.
            label = site.get("name") or site.get("host", "")
            if other == mine.identity and "Hôte" not in found:
                found["Hôte"] = self.tr("Le profil « %s » utilise déjà ce serveur, ce port et cet utilisateur. "
                                       "Modifiez-le plutôt que d’en créer un second.") % label
            elif label.casefold() == name and "Nom du profil" not in found:
                found["Nom du profil"] = self.tr("Un autre profil s’appelle déjà « %s ». "
                                                "Donnez un nom distinct à celui-ci.") % label
        return found

    def validate(self, *_):
        if not hasattr(self, "save_button"):
            return
        found = self.problems()
        blocking = found or self.duplicates()
        self.ok_button.setEnabled(not blocking)
        self.save_button.setEnabled(not blocking)
        # A duplicate can still be tested: only incomplete fields prevent it.
        self.test_button.setEnabled(not found and not (self.probe and self.probe.isRunning()))
        # Do not greet an empty form with errors: wait until a field was left or filled.
        shown = blocking if self.touched or self.host.text() or self.user.text() or self.name.text() else {}
        self.validation.setText("\n".join(shown.values()))
        self.validation.setVisible(bool(shown))
        for label, field in (("Hôte", self.host), ("Utilisateur", self.user), ("Clé privée", self.key),
                             ("Nom du profil", self.name), ("Proxy", self.proxy_host)):
            field.setProperty("invalid", label in shown)
            field.style().unpolish(field)
            field.style().polish(field)
            field.setToolTip(shown.get(label, self.host_help if field is self.host else ""))

    def toggle_password(self, visible: bool):
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        name = "eye_off" if visible else "eye"
        self.show_password.setProperty("iconName", name)
        dark = self.show_password.property("iconDark")
        self.show_password.setIcon(make_icon(name, True if dark is None else bool(dark)))
        text = self.tr("Masquer le mot de passe") if visible else self.tr("Afficher le mot de passe")
        self.show_password.setText(text)
        self.show_password.setToolTip(text)

    def test_connection(self):
        from smftp.workers.probe import ConnectionProbe
        if self.problems() or (self.probe and self.probe.isRunning()):
            return
        self.show_test_result(self.tr("Test en cours…"), "muted")
        self.test_button.setEnabled(False)
        self.test_button.setText(self.tr("Test en cours…"))
        self.probe = ConnectionProbe(self.connection())
        self.probe.step.connect(lambda text: self.show_test_result(self.without_status(text), "muted"))
        self.probe.succeeded.connect(lambda text: self.show_test_result("✓ " + text, "success"))
        self.probe.failed.connect(self.test_failed)
        self.probe.host_unknown.connect(self.test_host_unknown)
        self.probe.finished.connect(self.test_finished)
        self.probe.start()

    def without_status(self, text):
        """Drop the "Statut : " prefix of a progress step, in French or translated ("Status: ")."""
        for prefix in ("Statut : ", self.tr("Statut") + " : ", self.tr("Statut") + ": "):
            if text.startswith(prefix):
                return text[len(prefix):]
        return text

    def test_finished(self):
        self.test_button.setText(self.tr("Tester la connexion"))
        self.validate()

    def test_failed(self, info):
        self.show_test_result("✕ " + info.summary, "error")
        self.test_result.setToolTip(info.details)
        if info.details:
            self.test_result.setText(self.test_result.text() + "\n" + info.details.split("\n\n")[0])

    def test_host_unknown(self, error):
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(self, self.tr("Vérifier l’identité du serveur"),
            self.tr("Première connexion à %s\n\nEmpreinte %s :\n%s\n\nComparez cette empreinte avec celle fournie par votre administrateur. Faire confiance à ce serveur ?") % (error.host, error.key_type, error.fingerprint),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            self.show_test_result(self.tr("✕ Clé du serveur non approuvée : test interrompu."), "error")
            return
        try:
            from smftp.core.clients import trust_host_key
            from smftp.utils.storage import data_dir
            trust_host_key(error, str(data_dir() / "known_hosts"))
        except Exception:
            self.show_test_result(self.tr("✕ Impossible d’enregistrer la clé du serveur."), "error")
            return
        # Retry once the current probe has fully ended.
        if self.probe.isFinished():
            self.test_connection()
        else:
            self.probe.finished.connect(self.test_connection)

    def show_test_result(self, text, kind):
        self.test_result.setObjectName(kind)
        self.test_result.setToolTip("")
        self.test_result.setText(text)
        self.test_result.style().unpolish(self.test_result)
        self.test_result.style().polish(self.test_result)
        self.test_result.show()

    def done(self, result):
        self.show_password.setChecked(False)
        if self.probe is not None:
            self.probe.abandon()
        super().done(result)

    def protocol_value(self) -> str:
        return self.protocol.currentData() or DEFAULT_PROTOCOL

    def set_protocol(self, protocol: str) -> None:
        index = self.protocol.findData(protocol)
        self.protocol.setCurrentIndex(index if index >= 0 else self.protocol.findData(DEFAULT_PROTOCOL))

    def accept_save_only(self):
        self.save_only = True
        self.accept()

    def choose_key(self):
        start = self.key.text() or str(Path.home() / ".ssh")
        filename, _ = QFileDialog.getOpenFileName(self, self.tr("Sélectionner une clé privée SSH"),
            start, self.tr("Clés SSH (*.pem *.key *.ppk id_rsa id_ed25519 id_ecdsa);;Clés PuTTY (*.ppk);;Tous les fichiers (*)"))
        if filename:
            self.key.setText(filename)

    def choose_local_directory(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Dossier local de départ"),
                                                  self.local_directory.text() or str(Path.home()))
        if folder:
            self.local_directory.setText(folder)

    def connection(self) -> Connection:
        protocol = self.protocol_value()
        proxy = self.proxy_type.currentData() or "none"
        return Connection(self.host.text().strip(), self.user.text().strip(), protocol,
                          self.port.value(), self.password.text(), self.key.text().strip() if uses_ssh(protocol) else "", self.remember.isChecked(), self.name.text().strip(),
                          self.group.text().strip(), self.local_directory.text().strip(), self.remote_directory.text().strip(), self.timeout.value(),
                          uses_ssh(protocol) or bool(self.transfer_mode.currentData()),
                          "auto" if uses_ssh(protocol) else (self.encoding.currentData() or "auto"),
                          proxy_type=proxy, proxy_host=self.proxy_host.text().strip() if proxy != "none" else "",
                          proxy_port=self.proxy_port.value(),
                          proxy_user=self.proxy_user.text().strip() if proxy != "none" else "",
                          proxy_password=self.proxy_password.text() if proxy != "none" else "",
                          use_agent=uses_ssh(protocol) and self.use_agent.isChecked(),
                          time_offset=None if uses_ssh(protocol) else self.time_offset.currentData())
