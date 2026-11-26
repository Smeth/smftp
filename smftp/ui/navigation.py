"""Folder bookmarks, quick connect bar and synchronized browsing of the two panels."""
import os
import posixpath
from pathlib import Path
from urllib.parse import quote
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QPushButton, QToolButton,
                               QCompleter, QDialog, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QInputDialog,
                               QMessageBox, QHeaderView)
from smftp.models.entries import Connection, DEFAULT_PORTS, is_terminal
from smftp.utils.storage import load_bookmarks, save_bookmarks

QUICK_HISTORY = 10
# Stored protocol -> URL scheme of the quick connect history (see connection_dialog.SCHEMES).
SCHEME_OF = {"SFTP": "sftp", "SSH": "ssh", "FTP": "ftp", "FTPSI": "ftps", "FTPS": "ftpes"}


def history_address(connection) -> str:
    """scheme://user@host:port/path, never the password."""
    address = f"{SCHEME_OF.get(connection.protocol, 'sftp')}://"
    if connection.user:
        address += quote(connection.user, safe="") + "@"
    host = f"[{connection.host}]" if ":" in connection.host else connection.host
    address += f"{host}:{connection.port}"
    if connection.remote_directory:
        address += quote(connection.remote_directory, safe="/")
    return address


class QuickConnectBar(QWidget):
    """Host, user, password, port and a Connect button, under the menu bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("quickConnect")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(6)
        self.host = QLineEdit()
        self.host.setPlaceholderText(self.tr("hôte ou sftp://utilisateur@hôte:22/dossier"))
        self.host.setAccessibleName(self.tr("Hôte"))
        self.host.setToolTip(self.tr("Nom d’hôte, adresse IP ou adresse complète (sftp://, ftp://, ftps://, ftpes://). "
                                     "SFTP par défaut."))
        self.completer = QCompleter([], self.host)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.host.setCompleter(self.completer)
        self.user = QLineEdit()
        self.user.setPlaceholderText(self.tr("utilisateur"))
        self.user.setAccessibleName(self.tr("Utilisateur"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(self.tr("mot de passe"))
        self.password.setAccessibleName(self.tr("Mot de passe"))
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText(self.tr("auto"))
        self.port.setAccessibleName(self.tr("Port"))
        self.port.setToolTip(self.tr("« auto » : port de l’adresse, sinon port habituel du protocole (22 en SFTP)."))
        self.button = QPushButton(self.tr("Connexion"))
        self.button.setObjectName("compact")
        for label, field, stretch in ((self.tr("Hôte"), self.host, 3), (self.tr("Utilisateur"), self.user, 2),
                                      (self.tr("Mot de passe"), self.password, 2), (self.tr("Port"), self.port, 0)):
            caption = QLabel(label)
            caption.setObjectName("muted")
            caption.setBuddy(field)
            row.addWidget(caption)
            row.addWidget(field, stretch)
        row.addWidget(self.button)
        for field in (self.host, self.user, self.password):
            field.returnPressed.connect(self.button.click)

    def set_history(self, addresses):
        self.completer.model().setStringList(list(addresses))


class NavigationFeatures:
    """Mixin of MainWindow: bookmarks menu, quick connect and synchronized browsing."""

    def initialize_navigation(self):
        # ----- bookmarks -----
        problems = []
        self.bookmarks = load_bookmarks(problems)
        for problem in problems:
            self.log(problem)
        self.site_problems.extend(problems)
        self.bookmarks_menu = self.menuBar().addMenu(self.tr("Signets"))
        self.add_bookmark_action = QAction(self.tr("Ajouter un signet…"), self)
        self.add_bookmark_action.setShortcut(QKeySequence("Ctrl+D"))
        self.add_bookmark_action.triggered.connect(lambda checked=False: self.add_bookmark())
        self.manage_bookmarks_action = QAction(self.tr("Gérer les signets…"), self)
        self.manage_bookmarks_action.triggered.connect(lambda checked=False: self.manage_bookmarks())
        self.bookmarks_menu.aboutToShow.connect(self.update_bookmarks_menu)
        self.update_bookmarks_menu()
        # ----- quick connect -----
        self.quick_bar = QuickConnectBar(self)
        self.centralWidget().layout().insertWidget(0, self.quick_bar)
        self.quick_history = [item for item in self.settings.value("quick_connect/history", [], type=list)
                              if isinstance(item, str)][:QUICK_HISTORY]
        self.quick_bar.set_history(self.quick_history)
        self.quick_bar.button.clicked.connect(self.quick_connect)
        display = self.display_menu
        display.addSeparator()
        self.quick_bar_action = self.add_command(display, "Barre de connexion rapide", self.show_quick_bar, checkable=True)
        self.quick_bar_action.setChecked(self.settings.value("layout/quick_connect", True, type=bool))
        self.quick_bar.setVisible(self.quick_bar_action.isChecked())
        # ----- synchronized browsing -----
        self.sync_base = None
        self.sync_pending_remote = None
        self.sync_navigation_action = self.add_command(display, "Navigation synchronisée",
                                                       self.set_synchronized_navigation, checkable=True)
        self.sync_navigation_button = QToolButton()
        self.sync_navigation_button.setObjectName("compact")
        self.sync_navigation_button.setText("⇄")
        self.sync_navigation_button.setCheckable(True)
        self.sync_navigation_button.setFixedHeight(24)
        self.sync_navigation_button.setToolTip(self.tr("Navigation synchronisée : ouvrir un dossier d’un côté ouvre "
                                                       "le dossier correspondant de l’autre côté"))
        self.sync_navigation_button.setAccessibleName(self.tr("Navigation synchronisée"))
        # The menu action holds the state; the button only mirrors it (a refusal unchecks both).
        self.sync_navigation_button.clicked.connect(lambda checked=False: self.sync_navigation_action.trigger())
        self.sync_navigation_action.toggled.connect(
            lambda checked: self.sync_navigation_button.setChecked(self.sync_navigation_action.isChecked()))
        self.remote.title_row.insertWidget(self.remote.title_row.count() - 1, self.sync_navigation_button)
        self.local.path_changed.connect(lambda path: self.follow_navigation(path, False))
        self.remote.path_changed.connect(lambda path: self.follow_navigation(path, True))
        self.session.failed.connect(self.synchronized_listing_failed)
        self.session.disconnected.connect(self.stop_synchronized_navigation)

    # ----- bookmarks -----
    def current_bookmarks(self):
        if not (self.online and self.connection):
            return []
        return self.bookmarks.get(self.connection.identity, [])

    def update_bookmarks_menu(self):
        menu = self.bookmarks_menu
        menu.clear()
        menu.addAction(self.add_bookmark_action)
        self.add_bookmark_action.setEnabled(bool(self.online and self.connection))
        menu.addSeparator()
        if not (self.online and self.connection):
            menu.addAction(self.tr("Connectez un serveur pour voir ses signets")).setEnabled(False)
        elif not self.current_bookmarks():
            menu.addAction(self.tr("Aucun signet pour ce serveur")).setEnabled(False)
        else:
            for bookmark in self.current_bookmarks():
                action = menu.addAction(bookmark["name"].replace("&", "&&"))
                action.setToolTip(f"{bookmark['local']}  ↔  {bookmark['remote']}")
                action.setStatusTip(action.toolTip())
                action.triggered.connect(lambda checked=False, b=dict(bookmark): self.open_bookmark(b))
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        menu.addAction(self.manage_bookmarks_action)

    def save_bookmark_changes(self):
        try:
            save_bookmarks(self.bookmarks)
            return True
        except (OSError, ValueError, TypeError):
            self.on_error(self.tr("Impossible d’enregistrer les signets (bookmarks.json)."))
            return False

    def add_bookmark(self, name=None):
        if not (self.online and self.connection):
            self.statusBar().showMessage(self.tr("Signets : connectez d’abord un serveur."), 5000)
            return None
        if name is None:
            default = posixpath.basename(self.remote.path.rstrip("/")) or "/"
            name, accepted = QInputDialog.getText(
                self, self.tr("Ajouter un signet"),
                self.tr("Nom du signet pour :\n{local}\n{remote}").format(local=self.local.path, remote=self.remote.path),
                QLineEdit.EchoMode.Normal, default)
            if not accepted:
                return None
        name = (name or "").strip()
        if not name:
            return None
        items = self.bookmarks.setdefault(self.connection.identity, [])
        bookmark = {"name": name, "local": self.local.path, "remote": self.remote.path}
        existing = next((i for i, item in enumerate(items) if item["name"] == name), None)
        if existing is None:
            items.append(bookmark)
        else:
            items[existing] = bookmark  # Same name: the folders are updated.
        if self.save_bookmark_changes():
            self.statusBar().showMessage(self.tr("Signet « {name} » enregistré.").format(name=name), 4000)
        return bookmark

    def open_bookmark(self, bookmark):
        if self.sync_base is not None:
            self.sync_base = (bookmark["local"], bookmark["remote"])  # The bookmark is the new pair.
        if Path(bookmark["local"]).is_dir():
            self.local.navigate(bookmark["local"])
        else:
            self.statusBar().showMessage(self.tr("Dossier local du signet introuvable : {path}").format(
                path=bookmark["local"]), 6000)
        if self.online and posixpath.normpath(self.remote.path) != posixpath.normpath(bookmark["remote"]):
            self.remote.navigate(bookmark["remote"])

    def forget_bookmarks(self, profile_identity):
        """A deleted profile takes its bookmarks with it."""
        if self.bookmarks.pop(profile_identity, None) is not None:
            self.save_bookmark_changes()

    def move_bookmarks(self, old_identity, new_identity):
        """A profile edited to another server identity keeps its bookmarks."""
        if old_identity == new_identity or old_identity not in self.bookmarks:
            return
        moved = self.bookmarks.pop(old_identity)
        target = self.bookmarks.setdefault(new_identity, [])
        names = {item["name"] for item in target}
        target.extend(item for item in moved if item["name"] not in names)
        self.save_bookmark_changes()

    def bookmark_owner_label(self, key):
        from smftp.utils.profiles import identity
        site = next((s for s in self.sites if identity(s) == key), None)
        return (site.get("name") or site["host"]) if site else key.split("://", 1)[-1]

    def manage_bookmarks(self):
        from smftp.ui.icons import resize_within_screen
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Gérer les signets"))
        resize_within_screen(dialog, 760, 420)
        layout = QVBoxLayout(dialog)
        tree = QTreeWidget()
        tree.setHeaderLabels([self.tr("Nom"), self.tr("Dossier local"), self.tr("Dossier distant")])
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(tree)
        empty = QLabel(self.tr("Aucun signet. Connectez un serveur, ouvrez deux dossiers puis « Signets → Ajouter un "
                               "signet… » (Ctrl+D)."))
        empty.setObjectName("muted")
        empty.setWordWrap(True)
        layout.addWidget(empty)

        def fill():
            tree.clear()
            for key, items in self.bookmarks.items():
                if not items:
                    continue
                parent = QTreeWidgetItem([self.bookmark_owner_label(key)])
                tree.addTopLevelItem(parent)
                for index, bookmark in enumerate(items):
                    child = QTreeWidgetItem([bookmark["name"], bookmark["local"], bookmark["remote"]])
                    child.setData(0, Qt.ItemDataRole.UserRole, (key, index))
                    parent.addChild(child)
            tree.expandAll()
            empty.setVisible(tree.topLevelItemCount() == 0)
            follow()

        def chosen():
            item = tree.currentItem()
            return item.data(0, Qt.ItemDataRole.UserRole) if item else None

        def rename():
            target = chosen()
            if not target:
                return
            key, index = target
            current = self.bookmarks[key][index]["name"]
            name, accepted = QInputDialog.getText(dialog, self.tr("Renommer le signet"), self.tr("Nom :"),
                                                  QLineEdit.EchoMode.Normal, current)
            name = name.strip()
            if accepted and name and name != current:
                self.bookmarks[key][index]["name"] = name
                self.save_bookmark_changes()
                fill()

        def remove():
            target = chosen()
            if not target:
                return
            key, index = target
            del self.bookmarks[key][index]
            if not self.bookmarks[key]:
                del self.bookmarks[key]
            self.save_bookmark_changes()
            fill()

        row = QHBoxLayout()
        rename_button = QPushButton(self.tr("Renommer…"))
        rename_button.clicked.connect(rename)
        remove_button = QPushButton(self.tr("Supprimer"))
        remove_button.setToolTip(self.tr("Supprime le signet seulement : les dossiers ne sont pas touchés."))
        remove_button.clicked.connect(remove)
        close = QPushButton(self.tr("Fermer"))
        close.clicked.connect(dialog.accept)
        row.addWidget(rename_button)
        row.addWidget(remove_button)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)

        def follow(*_):
            selected = chosen() is not None
            rename_button.setEnabled(selected)
            remove_button.setEnabled(selected)
        tree.currentItemChanged.connect(follow)
        tree.itemDoubleClicked.connect(lambda *_: rename())
        fill()
        self.bookmarks_dialog = dialog  # Reached by the tests.
        self.bookmarks_dialog_actions = {"rename": rename, "remove": remove, "tree": tree}
        dialog.exec()

    # ----- quick connect -----
    def show_quick_bar(self, visible):
        self.quick_bar.setVisible(visible)
        self.settings.setValue("layout/quick_connect", bool(visible))
        if visible:
            self.quick_bar.host.setFocus()

    def quick_connection(self):
        """The Connection described by the bar, or an error message."""
        from smftp.ui.connection_dialog import parse_address, DEFAULT_PROTOCOL
        bar = self.quick_bar
        parts = parse_address(bar.host.text())
        if not parts:
            return None, self.tr("Connexion rapide : indiquez un hôte valide (ex. exemple.fr ou sftp://moi@exemple.fr).")
        protocol = parts.get("protocol", DEFAULT_PROTOCOL)
        user = bar.user.text().strip() or parts.get("user", "")
        if not user:
            if protocol in ("FTP", "FTPS", "FTPSI"):
                user = "anonymous"
            else:
                return None, self.tr("Connexion rapide : indiquez l’utilisateur.")
        port = parts.get("port") or bar.port.value() or DEFAULT_PORTS.get(protocol, 22)
        connection = Connection(host=parts["host"], user=user, protocol=protocol, port=port,
                                password=bar.password.text() or parts.get("password", ""),
                                name=parts["host"], remote_directory=parts.get("remote_directory", ""))
        return connection, ""

    def quick_connect(self):
        if self.closing:
            return
        if self.busy:
            self.statusBar().showMessage(self.tr("Une connexion est déjà en cours : attendez ou cliquez sur « Annuler »."), 5000)
            return
        connection, problem = self.quick_connection()
        if connection is None:
            self.statusBar().showMessage(problem, 6000)
            return
        self.remember_quick_address(connection)
        self.last_secret = connection.password
        if is_terminal(connection.protocol):
            from smftp.utils.profiles import profile_for
            self.start_terminal(profile_for(connection), connection)
            return
        # No profile is created: the connection lives until it is closed.
        self.retry_timer.stop()
        self.retry_count = 0
        self.connection = connection
        self.start_connection()

    def remember_quick_address(self, connection):
        address = history_address(connection)
        self.quick_history = [address] + [item for item in self.quick_history if item != address]
        self.quick_history = self.quick_history[:QUICK_HISTORY]
        self.settings.setValue("quick_connect/history", self.quick_history)
        self.quick_bar.set_history(self.quick_history)

    # ----- synchronized browsing -----
    def set_synchronized_navigation(self, enabled):
        if not enabled:
            self.sync_base = None
            self.sync_pending_remote = None
            return
        if not (self.online and self.remote.position >= 0):
            self.statusBar().showMessage(self.tr("Navigation synchronisée : connectez d’abord un serveur."), 5000)
            self.sync_navigation_action.setChecked(False)
            return
        self.sync_base = (self.local.path, self.remote.path)
        self.statusBar().showMessage(self.tr("Navigation synchronisée : {local} ↔ {remote}").format(
            local=self.local.path, remote=self.remote.path), 5000)

    def stop_synchronized_navigation(self, reason=""):
        if self.sync_base is None and not self.sync_navigation_action.isChecked():
            return
        self.sync_navigation_action.setChecked(False)
        if reason:
            self.statusBar().showMessage(reason, 8000)
            self.log(reason)

    def synchronized_target(self, path, from_remote):
        """The folder of the other side matching ``path``, relative to the activation pair."""
        local_base, remote_base = self.sync_base
        if from_remote:
            relative = posixpath.relpath(posixpath.normpath(path), posixpath.normpath(remote_base))
            target = Path(local_base)
            for part in relative.split("/"):
                if part == "..":
                    target = target.parent
                elif part not in ("", "."):
                    target = target / part
            return os.path.normpath(str(target))
        try:
            relative = os.path.relpath(path, local_base)
        except ValueError:
            return None  # Another drive.
        relative = relative.replace(os.sep, "/")
        return posixpath.normpath(posixpath.join(remote_base, relative))

    def follow_navigation(self, path, from_remote):
        if self.sync_base is None or not self.online:
            return
        if from_remote:
            if self.sync_pending_remote and posixpath.normpath(path) == self.sync_pending_remote:
                self.sync_pending_remote = None
            target = self.synchronized_target(path, True)
            if os.path.normcase(target) == os.path.normcase(os.path.normpath(self.local.path)):
                return
            if not Path(target).is_dir():
                self.stop_synchronized_navigation(self.tr(
                    "Navigation synchronisée désactivée : le dossier local {path} n’existe pas.").format(path=target))
                return
            self.local.navigate(target)
            return
        target = self.synchronized_target(path, False)
        if target is None:
            self.stop_synchronized_navigation(self.tr(
                "Navigation synchronisée désactivée : {path} n’est pas sur le même disque.").format(path=path))
            return
        current = posixpath.normpath(self.remote.path)
        if target == current:
            return
        if posixpath.dirname(target) == current:
            # A sub-folder of the listing on screen: known at once whether it exists.
            name = posixpath.basename(target)
            if not any(entry.is_dir and entry.name == name for entry in self.remote.last_entries):
                self.stop_synchronized_navigation(self.tr(
                    "Navigation synchronisée désactivée : le dossier distant {path} n’existe pas.").format(path=target))
                return
        if self.busy:
            self.stop_synchronized_navigation(self.tr(
                "Navigation synchronisée désactivée : le serveur est occupé, {path} n’a pas pu être ouvert.").format(
                path=target))
            return
        self.sync_pending_remote = target
        self.remote.navigate(target)

    def synchronized_listing_failed(self, error):
        if self.sync_pending_remote is None:
            return
        target, self.sync_pending_remote = self.sync_pending_remote, None
        self.stop_synchronized_navigation(self.tr(
            "Navigation synchronisée désactivée : le dossier distant {path} n’a pas pu être ouvert.").format(path=target))
