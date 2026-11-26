"""Main desktop workspace."""
from pathlib import Path
import posixpath
from PySide6.QtCore import Qt, Signal, QThread, QSettings, QTimer, QFileSystemWatcher, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
                               QPushButton, QListWidget, QSplitter, QTabWidget,
                               QPlainTextEdit, QMessageBox, QFrame, QMenu, QInputDialog, QFileDialog, QApplication)
from smftp.ui.explorer import Explorer
from smftp.ui.connection_dialog import ConnectionDialog
from smftp.workers.session import Session
from smftp.utils.storage import data_dir, load_sites, save_sites
from smftp.ui.transfers import Transfers
from smftp.workers.transfer import Transfer
from smftp.core.errors import ErrorInfo, describe_error, redact
from smftp.utils.profiles import profile_for, merge_profiles, normalize_profile, store_credential, identity, read_profiles, XMLCorrectionRequired
from smftp.ui.workspace_features import WorkspaceFeatures
from smftp.models.entries import protocol_label, is_terminal, uses_ssh


class MainWindow(WorkspaceFeatures, QMainWindow):
    connect_requested = Signal(object)
    list_requested = Signal(str)
    disconnect_requested = Signal()
    operation_requested = Signal(str, object)
    inspect_requested = Signal(str, str, str)  # token, remote path, connection identity
    command_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SMFTP — Votre espace de transfert")
        self.resize(1380, 880)
        self.setMinimumSize(940, 620)
        self.connection = None
        self.online = False
        self.busy = False
        self.closing = False
        self.settings = QSettings()
        self.edit_downloads = {}
        self.edit_uploads = {}
        self.editors = []
        self.edit_records = {}
        self.pending_saves = {}  # inspection token -> edit record
        self.parked = {}  # id(failed task) -> (task, follow-up actions) kept for "Relancer"
        self.inspection_counter = 0
        self.temp_dirs = []
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.external_changed)
        self.site_problems = []
        self.sites = load_sites(self.site_problems)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # No banner: the brand sits in the sidebar, global actions in the menu bar corner.
        workspace = QSplitter()
        self.workspace_splitter = workspace
        outer.addWidget(workspace, 1)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(195)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 16, 18, 18)
        brand = QLabel("◈  SMFTP")
        brand.setObjectName("brand")
        brand.setToolTip(self.tr("Un lien direct avec vos fichiers."))
        side.addWidget(brand)
        side.addSpacing(14)
        label = QLabel(self.tr("MES SERVEURS"))
        label.setObjectName("eyebrow")
        side.addWidget(label)
        self.site_list = QListWidget()
        self.site_list.setObjectName("sites")
        self.site_list.addItems([s.get('name') or s['host'] for s in self.sites])
        for row, site in enumerate(self.sites):
            self.site_list.item(row).setData(Qt.ItemDataRole.UserRole, identity(site))
        self.site_list.itemDoubleClicked.connect(lambda item: self.connect_profile(self.sites[self.site_list.row(item)]))
        self.site_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.site_list.customContextMenuRequested.connect(self.site_context_menu)
        side.addWidget(self.site_list)
        self.connect_button = QPushButton(self.tr("＋  Nouvelle connexion"))
        self.connect_button.setAccessibleName(self.tr("Nouvelle connexion"))
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(lambda: self.new_connection())
        side.addWidget(self.connect_button)
        note = QLabel(self.tr("SFTP · FTPS · FTP · SSH • identifiants dans le coffre-fort système"))
        note.setObjectName("muted")
        note.setWordWrap(True)
        side.addWidget(note)
        workspace.addWidget(sidebar)
        main = QSplitter(Qt.Orientation.Vertical)
        panels = QSplitter()
        self.panel_splitter = panels
        self.local = Explorer()
        local_default = self.settings.value("local_directory", "")
        if local_default:
            self.local.navigate(local_default)
        self.remote = Explorer(remote=True)
        self.remote.requested.connect(self.request_listing)
        self.remote.dropped.connect(lambda paths, target, source: self.handle_drop(paths, target, source, True))
        self.local.dropped.connect(lambda paths, target, source: self.handle_drop(paths, target, source, False))
        self.local.context_requested.connect(lambda point: self.context_menu(self.local, point))
        self.remote.context_requested.connect(lambda point: self.context_menu(self.remote, point))
        # Disconnecting belongs to the server panel, next to its status.
        self.disconnect_button = QPushButton(self.tr("Déconnecter"))
        self.disconnect_button.setObjectName("compact")
        self.disconnect_button.setFixedHeight(24)
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect_session)
        self.remote.title_row.addWidget(self.disconnect_button)
        panels.addWidget(self.local)
        panels.addWidget(self.remote)
        panels.setSizes([530, 530])
        main.addWidget(panels)
        tabs = QTabWidget()
        self.activity_tabs = tabs
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(1000)
        self.log_panel = self.build_log_panel()
        tabs.addTab(self.log_panel, self.tr("Journal de connexion"))
        self.transfers = Transfers(self)
        self.transfers.concurrency.setValue(self.settings.value("concurrency", 2, type=int))
        self.transfers.verify = self.settings.value("verify_checksum", True, type=bool)
        self.transfers.completed.connect(self.transfer_completed)
        self.transfers.changed.connect(self.refresh)
        self.transfers.outcome.connect(self.transfer_outcome)
        self.transfers.retried.connect(self.transfer_retried)
        self.transfers.discarded.connect(self.transfers_discarded)
        self.transfers.diagnostic.connect(lambda error: self.log(error.summary + "\n" + error.details))
        self.transfers.activity.connect(self.log)
        self.transfers.speed_changed.connect(self.update_speed)
        tabs.addTab(self.transfers, self.tr("Transferts"))
        tabs.setCurrentIndex(0)
        main.addWidget(tabs)
        main.setSizes([600, 190])
        # Centre: file panels for FTP / FTPS / SFTP, terminal tabs for SSH profiles.
        from PySide6.QtWidgets import QStackedWidget
        from smftp.ui.terminal import TerminalArea
        self.center = QStackedWidget()
        self.files_view = main
        self.center.addWidget(main)
        self.terminal = TerminalArea(self.settings.value("dark", True, type=bool))
        self.terminal.status.connect(self.log)
        self.terminal.failed.connect(self.terminal_failed)
        self.terminal.host_unknown.connect(self.terminal_host_unknown)
        self.terminal.new_session_requested.connect(lambda site: self.open_terminal(site, new=True))
        self.terminal.emptied.connect(lambda: self.set_view("files"))
        self.terminal.current_changed.connect(self.terminal_changed)
        self.center.addWidget(self.terminal)
        workspace.addWidget(self.center)
        # The assistant is a full-height side pane, shown on demand.
        from smftp.ui.assistant import AssistantPanel
        self.assistant_panel = AssistantPanel(self)
        workspace.addWidget(self.assistant_panel)
        workspace.setCollapsible(2, False)
        workspace.setSizes([220, 1160, 400])
        self.assistant_panel.setVisible(self.settings.value("assistant_visible", False, type=bool))
        self.build_corner()
        self.statusBar().showMessage(self.tr("●  Déconnecté    •    Prêt à explorer"))
        from smftp.workers.updates import CURRENT_VERSION
        self.statusBar().addPermanentWidget(QLabel(f"SMFTP  /  {CURRENT_VERSION}"))
        self.speed_label = QLabel("0 o/s")
        self.statusBar().addPermanentWidget(self.speed_label)
        self.thread = QThread(self)
        self.session = Session()
        self.session.moveToThread(self.thread)
        self.connect_requested.connect(self.session.connect_to)
        self.list_requested.connect(self.session.list_path)
        self.disconnect_requested.connect(self.session.close)
        self.operation_requested.connect(self.session.operate)
        self.session.operated.connect(self.operation_completed)
        self.inspect_requested.connect(self.session.inspect_path)
        self.command_requested.connect(self.session.command)
        self.session.inspected.connect(self.inspected_file)
        self.session.inspect_failed.connect(self.inspection_failed)
        self.session.move_conflict.connect(self.move_conflict)
        self.session.connected.connect(self.on_connected)
        self.session.progress.connect(self.log)
        self.session.listed.connect(self.on_listed)
        self.session.failed.connect(self.on_session_error)
        self.session.host_unknown.connect(self.confirm_host)
        self.session.disconnected.connect(self.on_disconnected)
        self.thread.finished.connect(self.session.deleteLater)
        self.thread.finished.connect(self.finish_close)
        self.file_shortcuts = []
        for text, key, callback in [("Nouvelle connexion", "Ctrl+K", lambda: self.new_connection()),
                                     ("Actualiser", "F5", self.refresh),
                                     ("Rechercher", "Ctrl+F", self.focus_search),
                                     ("Nouveau dossier", "Ctrl+N", lambda: self.file_action("mkdir")),
                                     # Suppr and F2 act only on the file panel that has the
                                     # keyboard focus, never on a selection left elsewhere.
                                     ("Renommer", "F2", lambda: self.focused_file_action("rename")),
                                     ("Supprimer", "Delete", lambda: self.focused_file_action("remove"))]:
            action = QAction(self.tr(text), self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            self.addAction(action)
            if key != "Ctrl+K":
                self.file_shortcuts.append(action)
        hidden = QAction(self.tr("Afficher les fichiers locaux cachés"), self)
        hidden.setCheckable(True)
        hidden.triggered.connect(self.local.show_hidden)
        self.display_menu = self.menuBar().addMenu(self.tr("Affichage"))
        self.display_menu.addAction(hidden)
        sites_menu = self.menuBar().addMenu(self.tr("Serveurs"))
        for title, callback in [("Importer JSON / FileZilla…", self.import_sites), ("Exporter les signets…", self.export_sites)]:
            sites_menu.addAction(self.tr(title)).triggered.connect(callback)
        tools_menu = self.menuBar().addMenu(self.tr("Outils"))
        self.tools_menu = tools_menu
        tools_menu.addAction(self.tr("Préférences…")).triggered.connect(self.preferences)
        tools_menu.addAction(self.tr("Synchroniser les dossiers…")).triggered.connect(self.synchronize)
        self.dark = self.settings.value("dark", True, type=bool)
        self.apply_theme()
        self.initialize_features()
        from smftp.ui.scheduler import Scheduler
        self.scheduler = Scheduler(self)
        self.thread.start()
        self.log(self.tr("Bienvenue. Ouvrez une connexion ou parcourez vos fichiers locaux."))

    def focus_search(self):
        (self.remote if self.remote.isAncestorOf(self.focusWidget()) else self.local).search.setFocus()

    def update_speed(self, speed):
        from smftp.ui.explorer import size_label
        self.speed_label.setText(f"{size_label(int(speed))}/s")

    def refresh(self):
        self.local.refresh()
        if self.online:
            self.remote.refresh()

    def build_corner(self):
        """Global toggles in the menu bar corner: no vertical space taken."""
        from PySide6.QtWidgets import QToolButton
        from smftp.ui.icons import make_icon
        corner = QWidget(self.menuBar())  # Parent set at creation: Qt owns it, freed once.
        row = QHBoxLayout(corner)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(4)
        self.assistant_toggle = QToolButton()
        self.assistant_toggle.setObjectName("cornerAction")
        self.assistant_toggle.setProperty("iconName", "spark")
        self.assistant_toggle.setIcon(make_icon("spark", self.dark if hasattr(self, "dark") else True))
        self.assistant_toggle.setText(self.tr("Assistant"))
        self.assistant_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.assistant_toggle.setCheckable(True)
        self.assistant_toggle.setChecked(self.assistant_panel.isVisible())
        self.assistant_toggle.setToolTip(self.tr("Afficher ou masquer l’assistant IA (Ctrl+Maj+I)"))
        self.assistant_toggle.toggled.connect(self.set_assistant_visible)
        row.addWidget(self.assistant_toggle)
        theme = QToolButton()
        theme.setObjectName("cornerAction")
        theme.setProperty("iconName", "theme")
        theme.setIcon(make_icon("theme"))
        theme.setToolTip(self.tr("Basculer le thème clair ou sombre"))
        theme.setAccessibleName(self.tr("Apparence : basculer le thème clair ou sombre"))
        theme.clicked.connect(self.toggle_theme)
        row.addWidget(theme)
        self.menuBar().setCornerWidget(corner, Qt.Corner.TopRightCorner)

    def set_assistant_visible(self, visible):
        self.assistant_panel.setVisible(visible)
        self.settings.setValue("assistant_visible", bool(visible))
        if self.assistant_toggle.isChecked() != visible:
            self.assistant_toggle.setChecked(visible)
        if visible:
            self.assistant_panel.focus_input()

    def build_log_panel(self):
        from smftp.ui.icons import icon_button
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.log_copy_button = icon_button("copy", self.tr("Copier le journal"), self.copy_log)
        self.log_save_button = icon_button("save", self.tr("Enregistrer le journal…"), self.save_log)
        self.log_clear_button = icon_button("trash", self.tr("Effacer le journal"), self.clear_log)
        for button in (self.log_copy_button, self.log_save_button, self.log_clear_button):
            bar.addWidget(button)
        bar.addStretch()
        layout.addLayout(bar)
        layout.addWidget(self.logs)
        self.logs.document().contentsChanged.connect(self.update_log_actions)
        self.update_log_actions()
        return panel

    def update_log_actions(self):
        has_text = not self.logs.document().isEmpty()
        for button in (self.log_copy_button, self.log_save_button, self.log_clear_button):
            button.setEnabled(has_text)

    def copy_log(self):
        selected = self.logs.textCursor().selectedText().replace(chr(0x2029), chr(10))
        QApplication.clipboard().setText(selected or self.logs.toPlainText())
        self.statusBar().showMessage(self.tr("Journal copié dans le presse-papiers."), 3000)

    def save_log(self):
        from datetime import datetime
        filename, _ = QFileDialog.getSaveFileName(self, self.tr("Enregistrer le journal"),
                                                  f"smftp-journal-{datetime.now():%Y%m%d-%H%M%S}.txt", "Texte (*.txt)")
        if filename:
            try:
                Path(filename).write_text(self.logs.toPlainText() + "\n", encoding="utf-8")
            except OSError:
                self.on_error(self.tr("Impossible d’enregistrer ce fichier."))

    def clear_log(self):
        self.logs.clear()

    def log(self, message):
        import logging
        from datetime import datetime
        secrets = tuple(filter(None, [self.connection.password if self.connection else "",
                                      getattr(self, "last_secret", "")]))
        message = redact(message, secrets)
        self.logs.appendPlainText(f"{datetime.now():%H:%M:%S}  {message}")
        self.logs.ensureCursorVisible()
        logging.getLogger("smftp").info(message)

    def new_connection(self, site=None):
        if self.busy:
            return
        dialog = ConnectionDialog(self, site)
        if dialog.exec():
            connection = dialog.connection()
            if not self.persist_profile(connection, site):
                return
            if not dialog.save_only:
                if is_terminal(connection.protocol):
                    self.start_terminal(profile_for(connection), connection)
                    return
                self.connection = connection
                self.start_connection()

    def refresh_sites(self):
        self.site_list.clear()
        for site in self.sites:
            group = site.get("group", "")
            label = (group + " / " if group else "") + (site.get("name") or site["host"])
            self.site_list.addItem(label)
            self.site_list.item(self.site_list.count() - 1).setData(Qt.ItemDataRole.UserRole, identity(site))
        self.update_site_states()

    def persist_profile(self, connection, original=None):
        self.last_secret = connection.password
        try:
            profile = profile_for(connection)
            remaining = [s for s in self.sites if original is None or identity(s) != identity(original)]
            updated = merge_profiles(remaining, [profile])
            # An explicit unchecked box also clears an existing remembered secret.
            for item in updated:
                if identity(item) == connection.identity:
                    item["remember"] = connection.remember
            save_sites(updated)
            self.sites = updated
            self.refresh_sites()
        except (OSError, ValueError, TypeError) as error:
            self.on_error(describe_error(error, connection, "Enregistrement du profil"))
            return False
        try:
            store_credential(connection)
            if original and identity(original) != connection.identity:
                from smftp.models.entries import Connection
                previous = Connection(**normalize_profile(original))
                previous.remember = False
                store_credential(previous)
        except Exception:
            if connection.remember:
                # The profile must not claim a remembered secret that the vault never received.
                for item in self.sites:
                    if identity(item) == connection.identity:
                        item["remember"] = False
                try:
                    save_sites(self.sites)
                except (OSError, ValueError, TypeError):
                    pass
                self.refresh_sites()
            self.on_error(self.tr("Le profil est enregistré, mais le coffre-fort système est indisponible. Le secret n’a pas pu être mémorisé ou supprimé : le profil est marqué « secret non mémorisé ». Il n’est jamais écrit dans le fichier JSON."))
        self.log(self.tr("Profil enregistré : %s") % profile["name"])
        return True

    def site_context_menu(self, point):
        item = self.site_list.itemAt(point)
        if not item:
            return
        site = self.sites[self.site_list.row(item)]
        menu = QMenu(self)
        protocol = site.get("protocol", "")
        if is_terminal(protocol):
            menu.addAction(self.tr("Ouvrir le terminal")).triggered.connect(lambda: self.open_terminal(site))
            menu.addAction(self.tr("Nouvelle session de terminal")).triggered.connect(
                lambda: self.open_terminal(site, new=True))
        else:
            menu.addAction(self.tr("Connecter")).triggered.connect(lambda: self.connect_profile(site))
            if uses_ssh(protocol):
                menu.addAction(self.tr("Ouvrir un terminal")).triggered.connect(
                    lambda: self.open_terminal(site, new=True))
        menu.addSeparator()
        menu.addAction(self.tr("Modifier…")).triggered.connect(lambda: self.new_connection(site))
        menu.addAction(self.tr("Supprimer le profil…")).triggered.connect(lambda: self.delete_profile(site))
        menu.exec(self.site_list.viewport().mapToGlobal(point))

    def delete_profile(self, site):
        if QMessageBox.question(self, self.tr("Supprimer le profil"), self.tr("Supprimer ce profil et son secret mémorisé ?"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            from smftp.models.entries import Connection
            connection = Connection(**normalize_profile(site))
            connection.remember = False
            store_credential(connection)
            remaining = [s for s in self.sites if identity(s) != connection.identity]
            save_sites(remaining)
            self.sites = remaining
            self.refresh_sites()
        except Exception:
            self.on_error(self.tr("Suppression incomplète : vérifiez l’accès au coffre-fort et au fichier des profils. Le profil est conservé pour pouvoir réessayer."))

    def start_connection(self):
        self.set_view("files")
        self.activity_tabs.setCurrentWidget(self.log_panel)
        self.busy = True
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        self.statusBar().showMessage(self.tr("Connexion en cours…"))
        self.log(self.tr("Ouverture d’une connexion %s.") % protocol_label(self.connection.protocol))
        self.connect_requested.emit(self.connection)

    def on_connected(self, path):
        self.online = True
        self.update_site_states()
        self.disconnect_button.setEnabled(True)
        self.statusBar().showMessage(f"●  Connecté    •    {self.connection.host}    •    {protocol_label(self.connection.protocol)}")
        self.log(self.tr("Connexion établie. Lecture du dossier distant…"))
        self.record_recent_connection()

    def request_listing(self, path):
        if self.online and not self.busy:
            self.busy = True
            self.list_requested.emit(path)

    def on_listed(self, path, entries):
        self.busy = False
        self.retry_count = 0
        self.connect_button.setEnabled(True)
        self.remote.set_entries(path, entries)
        self.log(self.tr("Dossier distant chargé : %n élément(s).", "", len(entries)))

    def move_conflict(self, source, target):
        """The session refused to move onto an existing destination: ask explicitly."""
        import posixpath
        self.busy = False
        answer = QMessageBox.question(self, self.tr("Destination existante"),
            self.tr("« %s » existe déjà dans %s.\n\nRemplacer ce fichier par « %s » ? Le fichier remplacé sera perdu.")
            % (posixpath.basename(target), posixpath.dirname(target) or "/", posixpath.basename(source)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.busy = True
            self.operation_requested.emit("replace", [source, target])
        else:
            self.log(self.tr("Déplacement annulé : %s existe déjà.") % target)

    def operation_completed(self):
        self.busy = False
        self.refresh()

    def transfer_completed(self, task):
        if self.closing:
            return
        self.log(self.tr("Transfert terminé : %s") % Path(task.local).name)
        self.notify(self.tr("Transfert terminé"), Path(task.local).name)
        if id(task) in self.pending_remote_copies:
            destination, directory = self.pending_remote_copies.pop(id(task))
            self.transfers.add(Transfer(task.connection, task.local, destination, True, directory), transient=True)
        if id(task) in self.drag_downloads:
            connection_id, remote, local, metadata = self.drag_downloads.pop(id(task))
            if self.connection and self.connection.identity == connection_id:
                self.remote.external_cache[remote] = local
                self.remote.external_metadata[remote] = metadata
                self.log(self.tr("Copie prête : vous pouvez maintenant glisser %s vers l’Explorateur Windows.") % Path(local).name)
        if id(task) in self.edit_downloads:
            record = self.edit_downloads.pop(id(task))
            self.open_downloaded(record)
        if id(task) in self.edit_uploads:
            record = self.edit_uploads.pop(id(task))
            if self.online and self.connection and self.connection.identity == record["connection"].identity:
                record["refresh_baseline"] = True
                self.request_inspection(record)
            else:
                # The browsing session now targets another server: never inspect the
                # same path there. The next save will ask before overwriting.
                record["baseline_unknown"] = True
                self.edit_sent(record)
        self.refresh()

    def transfer_outcome(self, task, status):
        if status != "Terminé":
            # Keep follow-up actions so that "Relancer" completes them too.
            actions = {}
            for name in self.CONTINUATIONS:
                value = getattr(self, name).pop(id(task), None)
                if value is not None:
                    actions[name] = value
            if actions:
                self.parked[id(task)] = (task, actions)
            record = actions.get("edit_uploads")
            if record:
                record.pop("save_again", None)
            if record and record.get("editor"):
                record["editor"].message.setText(self.tr("Envoi interrompu. La copie locale est conservée ; relancez le transfert ou enregistrez à nouveau."))
            self.log(self.tr("Transfert interrompu : %s. Vérifiez la connexion, l’espace disque et les permissions.") % status)

    CONTINUATIONS = ("pending_remote_copies", "drag_downloads", "edit_downloads", "edit_uploads")

    def transfer_retried(self, old, new):
        task, actions = self.parked.pop(id(old), (None, {}))
        if task is not old:
            return
        for name, value in actions.items():
            getattr(self, name)[id(new)] = value

    def transfers_discarded(self, tasks):
        for task in tasks:
            if self.parked.get(id(task), (None,))[0] is task:
                self.parked.pop(id(task))

    def handle_drop(self, paths, target, source_remote, target_remote):
        import posixpath
        if source_remote == target_remote:
            if QMessageBox.question(self, self.tr("Déplacer"), self.tr("Déplacer les éléments sélectionnés dans ce dossier ?")) != QMessageBox.StandardButton.Yes:
                return
            for item in paths:
                source = item["path"]
                if target_remote:
                    destination = posixpath.join(target, posixpath.basename(source))
                    if source != destination:
                        self.operation_requested.emit("rename", [source, destination])
                else:
                    # Native local moves are handled separately from network I/O.
                    destination = str(Path(target) / Path(source).name)
                    if Path(source) == Path(destination):
                        continue
                    resolved = self.local_destination(source, destination)
                    if resolved:
                        self.start_local("move", [source, *resolved])
            return
        if not self.online:
            self.on_error(self.tr("Connectez un serveur avant de transférer des fichiers."))
            return
        for item in paths:
            source = item["path"]
            try:
                from smftp.utils.paths import local_child
                local = str(local_child(target, posixpath.basename(source))) if source_remote else source
            except ValueError as error:
                self.on_error(str(error))
                return
            remote = source if source_remote else posixpath.join(target, Path(source).name)
            self.transfers.add(Transfer(self.connection, local, remote, not source_remote, item["directory"], conflict=self.settings.value("conflict", "ask")))

    def context_menu(self, panel, point):
        menu = QMenu(self)
        for title, operation in [("Télécharger" if panel.remote else "Téléverser", "transfer"),
                                 ("Nouveau dossier", "mkdir"), ("Renommer", "rename"),
                                 ("Supprimer", "remove"), ("Propriétés", "properties")]:
            action = menu.addAction(self.tr(title))
            action.triggered.connect(lambda checked=False, op=operation: self.file_action(op, panel))
        if panel.remote:
            menu.addAction(self.tr("Permissions…")).triggered.connect(lambda: self.file_action("chmod", panel))
            menu.addAction(self.tr("Modifier / Aperçu…")).triggered.connect(lambda: self.edit_remote(False))
            menu.addAction(self.tr("Ouvrir dans l’éditeur système…")).triggered.connect(lambda: self.edit_remote(True))
            menu.addAction(self.tr("Préparer le glisser-déposer externe")).triggered.connect(self.prepare_external_drag)
        menu.exec(point)

    def focused_file_action(self, operation):
        focused = QApplication.focusWidget()
        for panel in (self.local, self.remote):
            if focused is not None and (panel is focused or panel.isAncestorOf(focused)):
                self.last_panel = panel
                self.file_action(operation, panel)
                return

    def file_action(self, operation, panel=None):
        import posixpath
        panel = panel or self.active_panel()
        selected = panel.selected()
        if panel.remote and (not self.online or self.busy):
            return
        if operation == "transfer":
            target = self.local.path if panel.remote else self.remote.path
            self.handle_drop([{"path": p, "directory": d} for p, d in selected], target, panel.remote, not panel.remote)
            return
        if operation == "properties" and selected:
            from smftp.ui.explorer import size_label
            from datetime import datetime
            details = []
            for path, directory in selected:
                if panel.remote:
                    entry = next((e for e in panel.last_entries if e.name == posixpath.basename(path)), None)
                    if entry:
                        date = datetime.fromtimestamp(entry.modified).isoformat(" ", timespec="seconds") if entry.modified else "Inconnue"
                        details.append(f"{path}\nType : {'Dossier' if directory else 'Fichier'}\nTaille : {size_label(entry.size)}\nModifié : {date}\nPermissions : {entry.permissions or 'Inconnues'}")
                else:
                    try:
                        info = Path(path).lstat()
                        details.append(f"{path}\nType : {'Dossier' if directory else 'Fichier'}\nTaille : {size_label(info.st_size)}\nModifié : {datetime.fromtimestamp(info.st_mtime)}\nPermissions : {info.st_mode & 0o777:o}")
                    except OSError as error:
                        details.append(str(error))
            QMessageBox.information(self, self.tr("Propriétés"), "\n\n".join(details))
            return
        if operation in ("mkdir", "rename"):
            if operation == "rename" and len(selected) != 1:
                return
            value, ok = QInputDialog.getText(self, self.tr("Nouveau dossier" if operation == "mkdir" else "Renommer"), self.tr("Nom :"))
            if not ok or not value or value in (".", "..") or any(char in value for char in "/\\:\r\n"):
                return
            join = posixpath.join if panel.remote else lambda a, b: str(Path(a) / b)
            arguments = [join(panel.path, value)] if operation == "mkdir" else [selected[0][0], join(panel.path, value)]
        elif operation == "remove":
            if not selected:
                return
            names = [(posixpath.basename(path.rstrip("/")) if panel.remote else Path(path).name) + ("/" if directory else "")
                     for path, directory in selected]
            listed = "\n".join(f"• {name}" for name in names[:10])
            if len(names) > 10:
                listed += "\n" + self.tr("… et %1 autre(s)").replace("%1", str(len(names) - 10))
            where = (self.tr("sur le serveur %1").replace("%1", self.connection.host) if panel.remote and self.connection
                     else self.tr("sur ce PC"))
            question = (self.tr("Supprimer définitivement ces %1 élément(s) %2, avec tout leur contenu ?")
                        .replace("%1", str(len(names))).replace("%2", where)
                        + "\n\n" + listed + "\n\n" + self.tr("Cette action est irréversible."))
            if QMessageBox.question(self, self.tr("Suppression définitive"), question,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            for path, directory in selected:
                if panel.remote:
                    self.operation_requested.emit("remove", [path, directory])
                else:
                    self.start_local("remove", [path, directory])
            return
        elif operation == "chmod" and len(selected) == 1:
            from smftp.ui.permissions import PermissionsDialog, parse_mode
            if panel.remote:
                entry = next((e for e in panel.last_entries if e.name == posixpath.basename(selected[0][0])), None)
                current = parse_mode(entry.permissions) if entry else None
            else:
                try:
                    current = Path(selected[0][0]).lstat().st_mode & 0o7777
                except OSError:
                    current = None
            dialog = PermissionsDialog(self, current, selected[0][1], posixpath.basename(selected[0][0]) if panel.remote else Path(selected[0][0]).name)
            if not dialog.exec():
                return
            arguments = [selected[0][0], dialog.mode()]
        else:
            return
        if panel.remote:
            self.busy = True
            self.operation_requested.emit(operation, arguments)
        else:
            self.start_local(operation, arguments)

    def local_destination(self, source, destination):
        """Same conflict choices as transfers. Returns [destination, overwrite] or None."""
        from smftp.utils.paths import unique_copy_name
        target = Path(destination)
        if not (target.exists() or target.is_symlink()):
            return [destination, False]
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Fichier existant"))
        box.setText(self.tr("%s existe déjà. Quelle action souhaitez-vous ?") % target.name)
        replace = None
        if Path(source).is_file() and target.is_file():
            replace = box.addButton(self.tr("Écraser"), QMessageBox.ButtonRole.DestructiveRole)
        else:
            box.setInformativeText(self.tr("Les dossiers ne sont jamais fusionnés ni remplacés."))
        rename = box.addButton(self.tr("Renommer"), QMessageBox.ButtonRole.ActionRole)
        skip = box.addButton(self.tr("Ignorer"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(skip)
        box.exec()
        clicked = box.clickedButton()
        if replace is not None and clicked is replace:
            return [destination, True]
        if clicked is rename:
            return [str(unique_copy_name(target, lambda p: p.exists() or p.is_symlink())), False]
        self.log(self.tr("Ignoré : %s existe déjà.") % destination)
        return None

    def start_local(self, operation, arguments):
        from smftp.workers.local import LocalOperation
        if not hasattr(self, "local_workers"):
            self.local_workers = []
        worker = LocalOperation(operation, arguments, self)
        worker.failed.connect(self.on_error)
        worker.finished.connect(self.local.refresh)
        worker.finished.connect(lambda: self.local_workers.remove(worker))
        self.local_workers.append(worker)
        worker.start()

    def import_sites(self):
        filename, _ = QFileDialog.getOpenFileName(self, self.tr("Importer les serveurs"), "", "Serveurs (*.json *.xml)")
        if not filename:
            return
        try:
            try:
                result = read_profiles(filename)
            except XMLCorrectionRequired as error:
                answer = QMessageBox.question(self, self.tr("Corriger l’en-tête XML pour l’import ?"),
                    str(error) + self.tr("\n\nAppliquer cette correction uniquement en mémoire et poursuivre l’import ? Le fichier d’origine restera inchangé."),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
                result = read_profiles(filename, repair_header=True)
            if not result.profiles:
                info = ErrorInfo(self.tr("Aucun profil compatible à importer."), "\n".join(result.warnings))
                self.on_error(info)
                return
            import_passwords = False
            if result.passwords:
                import_passwords = QMessageBox.question(self, self.tr("Importer les identifiants"),
                    self.tr("Ce XML contient %n mot(s) de passe lisible(s). Les enregistrer dans le coffre-fort système ? Ils ne seront jamais copiés dans les profils JSON.", "", len(result.passwords)),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
            imported = merge_profiles([], result.profiles)
            # Profiles first: if they cannot be written, no secret reaches the vault.
            updated = merge_profiles(self.sites, imported)
            save_sites(updated)
            self.sites = updated
            self.refresh_sites()
            if import_passwords:
                self.import_secrets(imported, updated, result)
            message = self.tr("%n profil(s) importé(s) ou mis à jour. Les doublons ont été fusionnés.", "", len(imported))
            self.log(message)
            box = QMessageBox(self)
            box.setWindowTitle(self.tr("Import terminé"))
            box.setText(message)
            box.setTextFormat(Qt.TextFormat.PlainText)
            if result.warnings:
                box.setInformativeText(self.tr("Certains éléments nécessitent votre attention. Consultez les détails."))
                box.setDetailedText(redact("\n".join(result.warnings), tuple(result.passwords.values())))
            box.exec()
        except Exception as error:
            self.on_error(describe_error(error, operation="Import de configuration"))

    def import_secrets(self, imported, updated, result):
        """Store imported secrets, then mark profiles; undo the vault on failure."""
        from smftp.models.entries import Connection
        from smftp.utils.profiles import read_credential, restore_credential
        flags = {identity(item): item.get("remember", False) for item in updated}
        stored = []  # (identity, previous vault value)
        for site in imported:
            key = identity(site)
            secret = result.passwords.get(key)
            if secret is None:
                continue
            try:
                previous = read_credential(key)
                connection = Connection(**site)
                connection.password = secret
                connection.remember = True
                store_credential(connection)
                stored.append((key, previous))
            except Exception:
                result.warnings.append(f"{site['name']} : coffre-fort indisponible ; mot de passe non importé.")
        if not stored:
            return
        keys = {key for key, _ in stored}
        for item in updated:
            if identity(item) in keys:
                item["remember"] = True
        try:
            save_sites(updated)
        except (OSError, ValueError, TypeError):
            # The profiles would not reference these secrets: remove them again.
            for key, previous in stored:
                try:
                    restore_credential(key, previous)
                except Exception:
                    result.warnings.append(f"{key} : mot de passe importé resté dans le coffre-fort ; supprimez-le depuis le gestionnaire d’identifiants.")
            for item in updated:
                item["remember"] = flags.get(identity(item), False)
            result.warnings.append("Profils importés, mais leur mise à jour finale a échoué : les mots de passe importés ont été retirés du coffre-fort.")
        self.refresh_sites()

    def export_sites(self):
        import json
        filename, _ = QFileDialog.getSaveFileName(self, self.tr("Exporter les signets"), "serveurs.json", "JSON (*.json)")
        if filename:
            try:
                Path(filename).write_text(json.dumps([normalize_profile(site) for site in self.sites], ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                self.on_error(self.tr("Impossible d’enregistrer ce fichier."))

    def preferences(self):
        from smftp.ui.settings import SettingsDialog
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            dialog.persist(self.settings)
            self.transfers.concurrency.setValue(dialog.count.value())
            self.transfers.verify = dialog.verify.isChecked()

    def sync_running(self):
        scheduled = getattr(self, "scheduler", None)
        return (any(w.isRunning() for w in (getattr(self, "sync_worker", None), getattr(self, "sync_deleter", None)) if w)
                or bool(scheduled and scheduled.planning())
                or bool(getattr(self, "assistant_panel", None) and self.assistant_panel.busy()))

    def synchronize(self):
        from smftp.workers.sync import SyncPlanner
        from PySide6.QtWidgets import QDialog, QComboBox, QCheckBox, QDialogButtonBox, QFormLayout
        if not self.online or self.sync_running():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Synchroniser"))
        form = QFormLayout(dialog)
        direction = QComboBox()
        direction.addItems([self.tr("Local → Distant"), self.tr("Distant → Local")])
        form.addRow(self.tr("Sens de copie"), direction)
        content = QCheckBox(self.tr("Comparer le contenu par empreinte (SHA-256 si le serveur le permet ; plus lent)"))
        content.setToolTip(self.tr("Les fichiers de même taille sont comparés par empreinte au lieu de la date."))
        form.addRow(content)
        mirror = QCheckBox(self.tr("Mode miroir : supprimer dans la destination les éléments absents de la source"))
        form.addRow(mirror)
        hint = QLabel(self.tr("Sans le mode miroir, rien n’est supprimé. Un aperçu est toujours présenté avant toute action."))
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Comparer"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Annuler"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        upload = direction.currentIndex() == 0
        local, remote, connection = self.local.path, self.remote.path, self.connection
        self.sync_worker = SyncPlanner(connection, local, remote, upload, self,
                                       compare_content=content.isChecked(), mirror=mirror.isChecked())
        self.sync_worker.failed.connect(self.on_error)
        self.sync_worker.note.connect(self.log)
        self.sync_worker.planned.connect(lambda plan: self.show_sync_plan(plan, connection, local, remote, upload))
        self.log(self.tr("Comparaison récursive des dossiers en cours…"))
        self.sync_worker.start()

    def show_sync_plan(self, plan, connection, local, remote, upload):
        import posixpath
        from PySide6.QtWidgets import QDialog, QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QCheckBox
        from PySide6.QtGui import QBrush, QColor
        from smftp.ui.explorer import size_label
        from smftp.workers.sync import DELETIONS, DELETE_DIR
        if not plan:
            self.log(self.tr("Synchronisation : aucun fichier nouveau ou différent."))
            QMessageBox.information(self, self.tr("Synchroniser"), self.tr("Les dossiers sont déjà synchronisés dans ce sens : aucun fichier nouveau ou différent."))
            return
        mirror = any(row[1] in DELETIONS for row in plan)
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Aperçu de synchronisation — miroir" if mirror else "Aperçu de synchronisation — aucune suppression"))
        dialog.resize(760, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tr("Sélectionnez les éléments à traiter. Les suppressions sont définitives ; "
                                        "un fichier modifié depuis la comparaison est conservé.") if mirror else
                                self.tr("Sélectionnez les fichiers à copier. Rien n’est supprimé.")))
        table = QTreeWidget()
        table.setHeaderLabels([self.tr("Élément"), self.tr("Action"), self.tr("Taille")])
        table.setColumnWidth(0, 450)
        warning = QBrush(QColor("#d64545"))
        for path, state, size, _ in plan:
            item = QTreeWidgetItem([path + ("/" if state == DELETE_DIR else ""), self.tr(state), "" if state == DELETE_DIR else size_label(size)])
            item.setCheckState(0, Qt.CheckState.Checked)
            if state in DELETIONS:
                item.setForeground(1, warning)
            table.addTopLevelItem(item)
        layout.addWidget(table)
        summary = QLabel()
        layout.addWidget(summary)
        all_box = QCheckBox(self.tr("Tout sélectionner"))
        all_box.setChecked(True)
        layout.addWidget(all_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        copy_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        copy_button.setText(self.tr("Appliquer la sélection" if mirror else "Copier la sélection"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def checked_rows():
            return [i for i in range(table.topLevelItemCount()) if table.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]

        def update_summary(*_):
            rows = checked_rows()
            copies = [i for i in rows if plan[i][1] not in DELETIONS]
            text = self.tr("%n fichier(s) à copier", "", len(copies)) + f" • {size_label(sum(plan[i][2] for i in copies))}"
            if mirror:
                text += " • " + self.tr("%n suppression(s)", "", len(rows) - len(copies))
            summary.setText(text)
            copy_button.setEnabled(bool(rows))

        def toggle_all(checked):
            state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            for i in range(table.topLevelItemCount()):
                table.topLevelItem(i).setCheckState(0, state)

        table.itemChanged.connect(update_summary)
        all_box.toggled.connect(toggle_all)
        update_summary()
        if not dialog.exec():
            return
        selected = [plan[i] for i in checked_rows()]
        deletions = [row for row in selected if row[1] in DELETIONS]
        if deletions:
            side = self.tr("sur le serveur") if upload else self.tr("sur ce PC")
            if QMessageBox.question(self, self.tr("Confirmer les suppressions"),
                    self.tr("%n élément(s) vont être supprimés définitivement %1. Continuer ?", "", len(deletions)).replace("%1", side),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                deletions = []
                self.log(self.tr("Synchronisation : suppressions annulées ; seules les copies sont lancées."))
        copies = set()  # id() of the copy tasks queued for this plan.
        for path, state, _, expected in selected:
            if state in DELETIONS:
                continue
            from smftp.utils.paths import local_child
            destination = Path(local)
            try:
                for part in path.split("/"):
                    destination = local_child(destination, part)
            except ValueError as error:
                self.on_error(str(error))
                return
            task = Transfer(connection, str(destination), posixpath.join(remote, path), upload,
                            conflict="ask", planned=True, expected=expected)
            if self.transfers.add(task) is not None:
                copies.add(id(task))
        if deletions:
            self.delete_after_copies(copies, connection, local, remote, upload, deletions)

    def delete_after_copies(self, copies, connection, local, remote, upload, deletions):
        """Mirror deletions start once every copy of the same plan has finished: on a
        case-insensitive destination a deletion running alongside could remove a file
        that a copy has just written under another case."""
        from smftp.workers.sync import SyncDeleter

        def start():
            self.sync_deleter = SyncDeleter(connection, local, remote, upload, deletions, self)
            self.sync_deleter.report.connect(self.log)
            self.sync_deleter.done.connect(lambda deleted, kept: (
                self.log(self.tr("Synchronisation miroir : %1 supprimé(s), %2 conservé(s).").replace("%1", str(deleted)).replace("%2", str(kept))),
                self.refresh()))
            self.sync_deleter.start()

        if not copies:
            start()
            return
        self.log(self.tr("Synchronisation miroir : les suppressions auront lieu une fois les copies terminées."))
        cancelled = []

        def finished(task, status):
            if id(task) not in copies:
                return
            copies.discard(id(task))
            if status == "Annulé":
                cancelled.append(task)
            if copies:
                return
            self.transfers.outcome.disconnect(finished)
            if cancelled:
                self.log(self.tr("Synchronisation miroir : copies annulées ; aucune suppression n’a été faite."))
            else:
                start()
        self.transfers.outcome.connect(finished)

    def edit_remote(self, external=False):
        import tempfile
        selected = self.remote.selected()
        if not self.online or len(selected) != 1 or selected[0][1]:
            return
        remote = selected[0][0]
        plain_text = False
        from smftp.utils.paths import runs_when_opened
        if external and runs_when_opened(posixpath.basename(remote)):
            # Opening a .bat, .exe, .lnk, .vbs... with its Windows association runs it.
            answer = QMessageBox.question(
                self, self.tr("Fichier exécutable"),
                self.tr("« %1 » est un type de fichier que Windows exécute à l’ouverture au lieu de l’afficher.\n\n"
                        "Par sécurité, SMFTP ne le lance pas. Voulez-vous l’ouvrir comme texte dans le Bloc-notes ?")
                .replace("%1", posixpath.basename(remote)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            plain_text = True
        item = self.remote.view.selectedItems()[0]
        size = item.data(1, Qt.ItemDataRole.UserRole)
        if size > 20 * 1024 * 1024:
            self.on_error(self.tr("L’aperçu est limité à 20 Mo. Téléchargez ce fichier pour l’ouvrir."))
            return
        try:
            directory = tempfile.TemporaryDirectory(prefix="smftp-", dir=self.settings.value("temp_directory", "") or None)
            self.temp_dirs.append(directory)
            from smftp.utils.paths import local_child
            local = str(local_child(directory.name, posixpath.basename(remote)))
            task = Transfer(self.connection, local, remote, False, conflict="overwrite")
            record = dict(local=local, remote=remote, connection=self.connection, external=external, plain_text=plain_text,
                          baseline=(size, item.data(3, Qt.ItemDataRole.UserRole)))
            self.edit_downloads[id(task)] = record
            self.transfers.add(task, transient=True)
        except (OSError, ValueError):
            self.on_error(self.tr("Impossible de créer la copie temporaire."))

    def open_downloaded(self, record):
        from smftp.ui.editor import Editor, ImagePreview
        local = record["local"]
        self.edit_records[local] = record
        if record["external"]:
            from smftp.utils.paths import runs_when_opened, mark_from_internet
            import os
            # SmartScreen and Office Protected View then treat the copy as downloaded.
            mark_from_internet(local)
            self.watcher.addPath(local)
            if record.get("plain_text") or runs_when_opened(local):
                if os.name == "nt":
                    from PySide6.QtCore import QProcess
                    windows = os.environ.get("SystemRoot", r"C:\Windows")
                    started = QProcess.startDetached(str(Path(windows) / "System32" / "notepad.exe"), [local])
                    if not (started[0] if isinstance(started, tuple) else started):
                        self.on_error(self.tr("Impossible d’ouvrir le Bloc-notes."))
                    return
                record["external"] = False  # Other systems: SMFTP's own text editor.
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(local)):
                self.on_error(self.tr("Aucune application ne peut ouvrir ce fichier."))
                return
            else:
                return
        try:
            if Path(local).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
                dialog = ImagePreview(local, self)
            else:
                dialog = Editor(local, record["remote"], self)
                dialog.saved.connect(lambda: self.save_remote(record))
                record["editor"] = dialog
            self.editors.append(dialog)
            dialog.show()
        except (OSError, UnicodeError):
            self.on_error(self.tr("L’éditeur intégré prend en charge les fichiers texte UTF-8 et les images."))

    def external_changed(self, local):
        record = self.edit_records.get(local)
        if not record or record.get("debouncing"):
            return
        record["debouncing"] = True
        def changed():
            record["debouncing"] = False
            if Path(local).exists():
                if local not in self.watcher.files():
                    self.watcher.addPath(local)
                if QMessageBox.question(self, self.tr("Fichier modifié"), self.tr("Envoyer les modifications de %s au serveur ?") % Path(local).name) == QMessageBox.StandardButton.Yes:
                    self.save_remote(record)
        QTimer.singleShot(700, changed)

    def save_remote(self, record):
        record["unsent"] = True
        if not self.online or record["connection"].identity != self.connection.identity:
            self.on_error(self.tr("Reconnectez le serveur d’origine avant d’envoyer ce fichier."))
            return
        # A new save supersedes a failed upload of the same document.
        for key, (task, actions) in list(self.parked.items()):
            if actions.get("edit_uploads") is record:
                del actions["edit_uploads"]
                if not actions:
                    del self.parked[key]
        if any(r is record for r in self.pending_saves.values()) or any(r is record for r in self.edit_uploads.values()):
            # Keep the running save intact and send the newest copy once it completes.
            record["save_again"] = True
            self.log(self.tr("Un envoi est déjà en cours pour %s : la nouvelle version sera envoyée ensuite.") % record["remote"])
            return
        self.request_inspection(record)

    def request_inspection(self, record):
        """Inspect the remote file of this edit record on its own server only."""
        self.inspection_counter += 1
        token = f"edit-{self.inspection_counter}"
        self.pending_saves[token] = record
        self.inspect_requested.emit(token, record["remote"], record["connection"].identity)

    def edit_sent(self, record):
        record["unsent"] = False
        if record.get("editor"):
            record["editor"].message.setText(self.tr("Modifications envoyées au serveur."))
        if record.pop("save_again", False):
            self.save_remote(record)

    def inspected_file(self, token, entry):
        record = self.pending_saves.pop(token, None)
        if not record:
            return
        baseline = (entry.size, entry.modified) if entry else None
        if record.pop("refresh_baseline", False):
            record["baseline"] = baseline
            record.pop("baseline_unknown", None)
            self.edit_sent(record)
            return
        if record.get("baseline_unknown"):
            question = self.tr("La version distante n’a pas pu être relevée après le dernier envoi (connexion changée). Envoyer quand même et écraser le fichier distant ?")
        elif baseline != record["baseline"]:
            question = self.tr("Le fichier a changé sur le serveur. Écraser cette version ?")
        else:
            question = None
        if question:
            if QMessageBox.question(self, self.tr("Fichier distant modifié"), question, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                record.pop("save_again", None)
                return
        record.pop("baseline_unknown", None)
        # Any save requested from now on is included in this upload, which reads the file when it starts.
        record.pop("save_again", None)
        task = Transfer(record["connection"], record["local"], record["remote"], True, conflict="overwrite")
        self.edit_uploads[id(task)] = record
        self.transfers.add(task, transient=True)

    def inspection_failed(self, token):
        record = self.pending_saves.pop(token, None)
        if record:
            record.pop("save_again", None)
            if record.get("editor"):
                record["editor"].message.setText(self.tr("Vérification impossible : la copie locale est conservée ; réessayez l’enregistrement."))

    def on_disconnected(self):
        self.online = False
        self.update_site_states()
        self.pending_saves.clear()
        self.disconnect_button.setEnabled(False)
        self.remote.view.clear()
        self.remote.icons.clear()
        self.remote.last_entries = []
        self.remote.external_cache.clear()
        self.remote.external_metadata.clear()
        self.remote.empty.show()
        self.remote.count.setText(self.tr("Non connecté"))
        running = self.transfers.pending_count()
        if running:
            self.statusBar().showMessage(self.tr("●  Déconnecté    •    %n transfert(s) en file sur leurs propres connexions", "", running))
        else:
            self.statusBar().showMessage(self.tr("●  Déconnecté"))

    def on_session_error(self, error):
        # Full diagnostics arrive in the preceding progress signal.
        self.busy = False
        self.connect_button.setEnabled(True)
        self.activity_tabs.setCurrentWidget(self.log_panel)
        summary = error.summary if isinstance(error, ErrorInfo) else str(error)
        if not isinstance(error, ErrorInfo):
            self.log("Erreur : " + summary)
        self.statusBar().showMessage(self.tr("Erreur : %s") % summary)
        if (isinstance(error, ErrorInfo) and error.retryable and
            self.settings.value("auto_reconnect", False, type=bool) and
            not self.closing and self.retry_count < 3):
            self.retry_count += 1
            delay = self.retry_count * 3
            self.log(self.tr("Reconnexion automatique %s/3 dans %s secondes…") % (self.retry_count, delay))
            self.retry_timer.start(delay * 1000)

    def on_error(self, message):
        self.busy = False
        self.connect_button.setEnabled(True)
        info = message if isinstance(message, ErrorInfo) else ErrorInfo(str(message), "")
        secrets = tuple(filter(None, [self.connection.password if self.connection else "",
                                      getattr(self, "last_secret", "")]))
        summary, details = redact(info.summary, secrets), redact(info.details, secrets)
        self.log(summary + ("\n" + details if details else ""))
        self.activity_tabs.setCurrentWidget(self.log_panel)
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Opération impossible"))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(summary)
        if details:
            box.setInformativeText(self.tr("Ouvrez les détails pour voir la réponse du serveur et le contexte. Les secrets sont masqués."))
            box.setDetailedText(details)
        box.exec()

    def confirm_host(self, error):
        self.busy = False
        self.connect_button.setEnabled(True)
        if self.trust_host(error):
            self.start_connection()

    def trust_host(self, error):
        """Ask before recording an unknown SSH server key; True once it is trusted."""
        answer = QMessageBox.question(self, self.tr("Vérifier l’identité du serveur"),
            self.tr("Première connexion à %s\n\nEmpreinte %s :\n%s\n\nComparez cette empreinte avec celle fournie par votre administrateur. Faire confiance à ce serveur ?") % (error.host, error.key_type, error.fingerprint),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        try:
            from smftp.core.clients import trust_host_key
            trust_host_key(error, str(data_dir() / "known_hosts"))
            return True
        except Exception:
            self.on_error(self.tr("Impossible d’enregistrer la clé du serveur."))
            return False

    def toggle_theme(self):
        self.dark = not self.dark
        self.settings.setValue("dark", self.dark)
        self.apply_theme()

    def apply_theme(self):
        name = "dark" if self.dark else "light"
        path = Path(__file__).resolve().parents[2] / "resources" / "themes" / f"{name}.qss"
        self.setStyleSheet(path.read_text(encoding="utf-8"))
        from smftp.ui.icons import recolor
        recolor(self, self.dark)
        if hasattr(self, "terminal"):
            self.terminal.set_dark(self.dark)
            self.update_site_states()

    def closeEvent(self, event):
        if not self.closing:
            dirty = any(editor.dirty for editor in self.editors if hasattr(editor, "dirty"))
            unsent = any(record.get("unsent") for record in self.edit_records.values())
            import sys
            terminals = len(self.terminal.sessions())
            if "--smoke-test" not in sys.argv and (self.isVisible() or self.online or self.transfers.active() or dirty or unsent or terminals):
                message = self.tr("Voulez-vous quitter SMFTP ?")
                if self.online or self.busy:
                    message += self.tr("\nLa connexion au serveur sera fermée.")
                if terminals:
                    message += self.tr("\n%n session(s) de terminal seront fermées.", "", terminals)
                if self.transfers.active():
                    message += self.tr("\nLes transferts seront interrompus et la file sera conservée en pause pour le prochain lancement.")
                if self.scheduler.enabled_count():
                    message += self.tr("\nLes tâches planifiées ne s’exécuteront pas tant que SMFTP est fermé.")
                if dirty or unsent:
                    message += self.tr("\nLes modifications non enregistrées ou non envoyées des copies temporaires seront perdues.")
                if QMessageBox.question(self, self.tr("Confirmer la fermeture"), message,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
        local_active = any(w.isRunning() for w in getattr(self, "local_workers", []))
        sync_active = self.sync_running() or any(w.isRunning() for w in self.search_workers)
        self.terminal.close_all()
        if self.thread.isRunning() or self.transfers.active() or local_active or sync_active or self.terminal.running():
            event.ignore()
            if not self.closing:
                self.closing = True
                self.retry_timer.stop()
                self.hide()
                self.transfers.stop_all()
                self.scheduler.stop()
                self.assistant_panel.stop()
                for worker in self.search_workers:
                    worker.requestInterruption()
                for worker in (getattr(self, "sync_worker", None), getattr(self, "sync_deleter", None)):
                    if worker:
                        worker.requestInterruption()
                # Quit after queued operations and cleanup have completed.
                self.session.disconnected.connect(self.thread.quit)
                self.disconnect_requested.emit()
                self.close_timer = QTimer(self)
                self.close_timer.timeout.connect(self.finish_close)
                self.close_timer.start(100)
        else:
            event.accept()

    def finish_close(self):
        sync_active = self.sync_running() or any(w.isRunning() for w in self.search_workers)
        if (self.closing and not self.thread.isRunning() and not self.transfers.active() and not sync_active
                and not self.terminal.running() and not any(w.isRunning() for w in getattr(self, "local_workers", []))):
            if hasattr(self, "close_timer"):
                self.close_timer.stop()
            self.close()
            QApplication.instance().quit()
