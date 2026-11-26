"""Workspace commands, direct profile connections and secondary dialogs."""
from dataclasses import replace
from pathlib import Path
import posixpath
import sys
import tempfile
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QInputDialog,
    QMessageBox, QStyle, QSystemTrayIcon, QMenu)
from smftp.models.entries import Connection, is_terminal, uses_ssh, protocol_label
from smftp.core.errors import redact
from smftp.ui.connection_dialog import ConnectionDialog
from smftp.utils.profiles import normalize_profile, profile_for, identity
from smftp.workers.transfer import Transfer


HELP_HTML = """
<h2>Bien démarrer</h2>
<p><b>Nouvelle connexion</b> (Ctrl+K) : choisissez SFTP de préférence (chiffré), puis l’hôte, l’utilisateur et le mot de passe.
Cochez « Mémoriser » pour garder le mot de passe dans le coffre-fort Windows. Ensuite, un double-clic sur le serveur dans
« Mes serveurs » suffit. Vos sites FileZilla s’importent par <b>Serveurs → Importer</b>.</p>
<h2>Fichiers</h2>
<p>À gauche votre ordinateur, à droite le serveur. Glissez des fichiers d’un panneau à l’autre pour les copier.
Clic droit : télécharger, renommer, supprimer, permissions, modifier un fichier texte.</p>
<h2>Transferts</h2>
<p>L’onglet <b>Transferts</b> montre la file : pause, annulation, relance. Chaque fichier est vérifié après la copie
(taille, et empreinte quand le serveur le permet). Un fichier existant n’est jamais remplacé sans votre accord.</p>
<h2>Synchronisation</h2>
<p><b>Outils → Synchroniser les dossiers</b> compare les deux dossiers affichés et vous montre la liste avant toute copie.
Le <b>mode miroir</b> supprime aussi dans la destination ce qui n’existe plus dans la source : vérifiez l’aperçu.</p>
<h2>Terminal SSH, tâches planifiées, assistant</h2>
<p>Un profil « SSH (terminal) » ouvre une console. <b>Outils → Tâches planifiées</b> lance des envois, téléchargements
ou synchronisations à heure fixe, tant que SMFTP est ouvert (ou réduit dans la zone de notification).
L’<b>Assistant IA</b> répond à vos questions et prépare des actions ; chacune vous est demandée avant d’être faite.</p>
<h2>Raccourcis</h2>
<table cellpadding="3">
<tr><td>Ctrl+K</td><td>Nouvelle connexion</td><td>Ctrl+R</td><td>Reconnecter</td></tr>
<tr><td>F5</td><td>Actualiser</td><td>Ctrl+F</td><td>Rechercher dans le dossier</td></tr>
<tr><td>Entrée</td><td>Ouvrir</td><td>Retour arrière, Alt+↑</td><td>Dossier parent</td></tr>
<tr><td>Alt+← / Alt+→</td><td>Dossier précédent / suivant</td><td>Ctrl+N</td><td>Nouveau dossier</td></tr>
<tr><td>F2</td><td>Renommer</td><td>Suppr</td><td>Supprimer</td></tr>
<tr><td>Ctrl+C / X / V</td><td>Copier / couper / coller</td><td>Ctrl+Maj+F</td><td>Recherche dans les sous-dossiers</td></tr>
<tr><td>Ctrl+Maj+T</td><td>Nouvelle session de terminal</td><td>Ctrl+Maj+I</td><td>Assistant</td></tr>
<tr><td>Alt+lettre</td><td>Ouvrir un menu (Alt+F : Fichier…)</td><td>F1</td><td>Cette aide</td></tr>
</table>
<p>Documentation complète : <a href="https://github.com/Smeth/smftp#readme">github.com/Smeth/smftp</a> ·
<a href="https://github.com/Smeth/smftp/blob/main/docs/CONFIDENTIALITE.md">confidentialité</a></p>
"""


class WorkspaceFeatures:
    def initialize_features(self):
        self.clipboard_files = None
        QApplication.clipboard().dataChanged.connect(self.clear_file_clipboard)
        for panel in (self.local, self.remote):
            panel.view.clicked.connect(lambda index, p=panel: setattr(self, "last_panel", p))
            panel.icons.clicked.connect(lambda index, p=panel: setattr(self, "last_panel", p))
        self.pending_remote_copies = {}
        self.drag_downloads = {}
        self.search_workers = []
        self.retry_count = 0
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self.retry_connection)
        self.transfers.concurrency.valueChanged.connect(lambda value: self.settings.setValue("concurrency", value))
        self.transfers.speed.setValue(self.settings.value("speed_limit", 0, type=int))
        self.transfers.speed.valueChanged.connect(self.update_speed_limit)
        self.recent_sites = self.settings.value("recent_sites", [], type=list)
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        self.tray.setToolTip("SMFTP")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        menu = self.menuBar().addMenu(self.tr("Fichier"))
        self.add_command(menu, "Nouvelle connexion…", lambda: self.new_connection(), "Ctrl+K")
        self.add_command(menu, "Gestionnaire de sites…", self.manage_sites, "Ctrl+Shift+S")
        self.add_command(menu, "Reconnecter", self.reconnect, "Ctrl+R")
        self.add_command(menu, "Déconnecter", self.disconnect_session)
        menu.addSeparator()
        self.add_command(menu, "Nouvelle session de terminal", self.new_terminal_session, "Ctrl+Shift+T")
        self.add_command(menu, "Fermer la session de terminal", self.terminal.close_current, "Ctrl+Shift+W")
        menu.addSeparator()
        self.recent_menu = menu.addMenu(self.tr("Connexions récentes"))
        self.update_recent_menu()
        menu.addSeparator()
        self.add_command(menu, "Quitter", self.close, "Ctrl+Q")
        edit = self.menuBar().addMenu(self.tr("Édition"))
        self.edit_menu = edit
        for title, callback, shortcut in [
            ("Copier", lambda: self.copy_files(False), "Ctrl+C"),
            ("Couper", lambda: self.copy_files(True), "Ctrl+X"),
            ("Coller", self.paste_files, "Ctrl+V"),
            ("Sélectionner tout", self.select_all_files, "Ctrl+A"),
            ("Nouveau dossier\tCtrl+N", lambda: self.file_action("mkdir"), ""),
            ("Renommer\tF2", lambda: self.file_action("rename"), ""),
            ("Supprimer\tSuppr", lambda: self.file_action("remove"), ""),
            ("Propriétés", lambda: self.file_action("properties"), "Alt+Return"),
            ("Recherche récursive…", self.recursive_search, "Ctrl+Shift+F")]:
            self.add_command(edit, title, callback, shortcut)
        transfers = self.menuBar().addMenu(self.tr("Transferts"))
        self.transfers_menu = transfers
        for title, callback in [("Téléverser la sélection", lambda: self.file_action("transfer", self.local)),
            ("Télécharger la sélection", lambda: self.file_action("transfer", self.remote)),
            ("Mettre tout en pause", self.transfers.pause_all), ("Reprendre tout", self.transfers.resume_all),
            ("Annuler tous les transferts…", self.cancel_transfers),
            ("Relancer la sélection", self.transfers.retry), ("Effacer les tâches réussies", self.transfers.clear_finished),
            ("Effacer les tâches échouées…", self.transfers.clear_failed),
            ("Préférences de transfert…", self.preferences)]:
            self.add_command(transfers, title, callback)
        display = self.display_menu
        self.add_command(display, "Vue Fichiers", lambda: self.set_view("files"))
        self.add_command(display, "Vue Terminal", lambda: self.set_view("terminal"))
        display.addSeparator()
        display.addAction(self.local_hidden_action)
        self.remote_hidden_action = self.add_command(display, "Fichiers distants cachés", self.remote.show_hidden, checkable=True)
        self.icon_view_action = self.add_command(display, "Vue en icônes", self.set_icon_views, checkable=True)
        self.vertical_action = self.add_command(display, "Panneaux verticaux", lambda checked: self.panel_splitter.setOrientation(
            Qt.Orientation.Vertical if checked else Qt.Orientation.Horizontal), checkable=True)
        self.add_command(display, "Journal", lambda: (self.set_view("files"),
                                                      self.activity_tabs.setCurrentWidget(self.log_panel)))
        self.add_command(display, "File de transferts", lambda: (self.set_view("files"),
                                                                 self.activity_tabs.setCurrentWidget(self.transfers)))
        self.add_command(display, "Effacer le journal", self.clear_log)
        self.add_command(display, "Thème clair / sombre", self.toggle_theme)
        self.add_command(self.tools_menu, "Tâches planifiées…", self.show_schedule, "Ctrl+Shift+P")
        self.add_command(self.tools_menu, "Assistant IA", self.show_assistant, "Ctrl+Shift+I")
        self.add_command(self.tools_menu, "Commande FTP…", self.ftp_command)
        help_menu = self.menuBar().addMenu(self.tr("Aide"))
        self.add_command(help_menu, "Documentation et raccourcis", self.show_help, "F1")
        self.add_command(help_menu, "Diagnostic", self.show_diagnostic)
        from smftp.workers.updates import checks_for_updates
        if checks_for_updates():  # Store builds are updated by the Microsoft Store.
            self.add_command(help_menu, "Rechercher les mises à jour…", self.check_for_updates)
        self.add_command(help_menu, "À propos", self.show_about)
        self.remote.file_opened.connect(lambda: self.edit_remote(False))
        order = ["Fichier", "Édition", "Affichage", "Serveurs", "Transferts", "Outils", "Aide"]
        actions = list(self.menuBar().actions())
        for action in actions:
            self.menuBar().removeAction(action)
        for title in order:
            for action in actions:
                if action.text() == self.tr(title):
                    self.menuBar().addAction(action)
        # Alt+letter opens each menu (Alt+F: Fichier…), as in other Windows applications.
        mnemonics = {"Fichier": "&Fichier", "Édition": "É&dition", "Affichage": "&Affichage", "Serveurs": "&Serveurs",
                     "Transferts": "&Transferts", "Outils": "&Outils", "Aide": "A&ide"}
        for action in self.menuBar().actions():
            label = mnemonics.get(action.text())
            if label:
                action.setText(self.tr(label))
        self.restore_layout()
        self.refresh_sites()
        QTimer.singleShot(0, self.transfers.restore)

    def add_command(self, menu, title, callback, shortcut="", checkable=False):
        action = QAction(self.tr(title), self)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(shortcut)
        if checkable:
            action.toggled.connect(callback)
        else:
            action.triggered.connect(lambda checked=False: callback())
        menu.addAction(action)
        return action

    def active_panel(self):
        focused = QApplication.focusWidget()
        if focused and self.remote.isAncestorOf(focused):
            self.last_panel = self.remote
        elif focused and self.local.isAncestorOf(focused):
            self.last_panel = self.local
        return getattr(self, "last_panel", self.local)

    def stored_secret(self, connection):
        try:
            import keyring
            return keyring.get_password("SMFTP", connection.identity)
        except Exception:
            return None

    def connect_profile(self, site):
        if self.closing:
            return
        if is_terminal(site.get("protocol", "")):
            self.open_terminal(site)
            return
        connection = Connection(**normalize_profile(site))
        if self.online and self.connection and self.connection.identity == connection.identity:
            self.set_view("files")
            return
        if self.busy:
            return
        secret = self.stored_secret(connection)
        has_key = uses_ssh(connection.protocol) and bool(connection.key_file)
        if secret is None and not has_key and connection.user != "anonymous":
            self.new_connection(site)
            return
        connection.password = secret or ""
        self.retry_timer.stop()
        self.retry_count = 0
        self.connection = connection
        self.start_connection()

    def disconnect_session(self):
        running = self.transfers.pending_count()
        if running:
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(self)
            box.setWindowTitle(self.tr("Déconnecter"))
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(self.tr("%n transfert(s) en cours ou en attente.", "", running))
            box.setInformativeText(self.tr("Les transferts utilisent leurs propres connexions : déconnecter la navigation ne les arrête pas."))
            only = box.addButton(self.tr("Déconnecter, transferts continuent"), QMessageBox.ButtonRole.AcceptRole)
            pause = box.addButton(self.tr("Déconnecter et mettre les transferts en pause"), QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(only)
            box.exec()
            clicked = box.clickedButton()
            if clicked is pause:
                self.transfers.pause_all()
                self.log(self.tr("Transferts mis en pause. Transferts → Reprendre tout pour continuer."))
            elif clicked is not only:
                return
            else:
                self.log(self.tr("Navigation déconnectée ; %n transfert(s) continuent.", "", running))
        self.retry_timer.stop()
        self.retry_count = 0
        self.disconnect_requested.emit()

    def reconnect(self):
        if self.connection and not self.busy:
            self.retry_count = 0
            self.start_connection()

    def retry_connection(self):
        if not self.closing and not self.busy and self.connection:
            self.start_connection()

    def record_recent_connection(self):
        self.remember_recent(self.connection)
        if self.connection.local_directory:
            self.local.navigate(self.connection.local_directory)
        self.transfers.authorize(self.connection)

    def remember_recent(self, connection):
        profile = profile_for(connection)
        self.recent_sites = [profile] + [site for site in self.recent_sites
            if isinstance(site, dict) and identity(site) != connection.identity]
        self.recent_sites = self.recent_sites[:10]
        self.settings.setValue("recent_sites", self.recent_sites)
        self.update_recent_menu()

    # ----- terminal (SSH profiles) -----
    def set_view(self, name):
        """Centre of the window: "files" (FTP / FTPS / SFTP panels) or "terminal" (SSH tabs)."""
        files = name != "terminal"
        # File commands act on the panels: off while they are hidden, so nothing happens out of sight.
        for menu in (self.edit_menu, self.transfers_menu):
            menu.menuAction().setEnabled(files)
            for action in menu.actions():
                action.setEnabled(files)
        for action in self.file_shortcuts:
            action.setEnabled(files)
        self.speed_label.setVisible(files)
        if name == "terminal":
            self.center.setCurrentWidget(self.terminal)
            self.terminal_changed(self.terminal.current())
            if self.terminal.current() is not None:
                self.terminal.current().view.setFocus()
        else:
            self.center.setCurrentWidget(self.files_view)
            if self.online and self.connection:
                self.statusBar().showMessage(f"●  Connecté    •    {self.connection.host}    •    "
                                             f"{protocol_label(self.connection.protocol)}")
            else:
                self.statusBar().showMessage(self.tr("●  Déconnecté"))
        self.update_site_states()

    def update_site_states(self):
        """« Mes serveurs »: icon by kind, open servers in colour, the one on screen selected."""
        from PySide6.QtGui import QFont
        from smftp.ui.icons import make_icon
        if not hasattr(self, "terminal"):
            return
        dark = getattr(self, "dark", True)
        terminal_view = self.center.currentWidget() is self.terminal
        current = self.terminal.current() if terminal_view else None
        shown = current.identity if current is not None else (
            self.connection.identity if not terminal_view and self.online and self.connection else None)
        accent = "#6ea8ff" if dark else "#1f6feb"
        self.site_list.blockSignals(True)
        self.site_list.clearSelection()
        for row in range(self.site_list.count()):
            item = self.site_list.item(row)
            site = self.sites[row] if row < len(self.sites) else {}
            key = item.data(Qt.ItemDataRole.UserRole) or (identity(site) if site else "")
            terminal = is_terminal(site.get("protocol", ""))
            sessions = [s for s in self.terminal.sessions() if s.identity == key]
            open_ = bool(sessions) or bool(self.online and self.connection and self.connection.identity == key)
            item.setIcon(make_icon("terminal" if terminal else "folder", dark, accent if open_ else None))
            font = QFont(item.font())
            font.setBold(open_)
            item.setFont(font)
            if open_:
                state = (self.tr("%n session(s) de terminal ouverte(s)", "", len(sessions)) if terminal
                         else self.tr("connecté"))
            else:
                state = self.tr("double-clic pour ouvrir le terminal") if terminal else self.tr("double-clic pour connecter")
            item.setToolTip(f"{key.split('://', 1)[-1]} — {protocol_label(site.get('protocol', ''))}\n{state}")
            if key == shown:
                item.setSelected(True)
        self.site_list.blockSignals(False)

    def open_terminal(self, site, new=False):
        """Terminal on an SSH or SFTP profile, with the vault secret; the form opens when none is stored."""
        if self.closing:
            return
        connection = Connection(**normalize_profile(site))
        if not new:
            session = self.terminal.find(connection.identity)
            if session is not None:
                self.terminal.show_session(session)
                self.set_view("terminal")
                return
        secret = self.stored_secret(connection)
        if secret is None and not connection.key_file:
            dialog = ConnectionDialog(self, site)
            if not dialog.exec():
                return
            connection = dialog.connection()
            if not self.persist_profile(connection, site) or dialog.save_only:
                return
            if not uses_ssh(connection.protocol):
                self.connection = connection
                self.start_connection()
                return
            site = profile_for(connection)
        else:
            connection.password = secret or ""
        self.start_terminal(site, connection)

    def start_terminal(self, site, connection):
        self.log(self.tr("Ouverture d’un terminal SSH sur %s.") % (connection.name or connection.host))
        self.terminal.open(site, connection)
        self.set_view("terminal")
        self.remember_recent(connection)

    def new_terminal_session(self):
        if self.terminal.current() is not None:
            self.terminal.request_new_session()
        else:
            self.statusBar().showMessage(self.tr("Ouvrez d’abord un serveur SSH depuis « Mes serveurs »."), 4000)

    def terminal_changed(self, session):
        self.update_site_states()
        if self.center.currentWidget() is not self.terminal:
            return
        if session is None:
            self.statusBar().showMessage(self.tr("●  Aucun terminal ouvert"))
            return
        states = {"connecting": self.tr("connexion…"), "open": self.tr("connecté"), "closed": self.tr("session terminée")}
        connection = session.connection
        self.statusBar().showMessage(f"●  {self.tr('Terminal')} {states.get(session.state, '')}    •    "
                                     f"{connection.user}@{connection.host}    •    SSH")

    def terminal_failed(self, info, session):
        secrets = tuple(filter(None, [session.connection.password]))
        self.log(redact(f"[Terminal {session.title}] Erreur : {info.summary}\n{info.details}", secrets))
        self.statusBar().showMessage(self.tr("Terminal : %s") % redact(info.summary, secrets), 8000)

    def terminal_host_unknown(self, error, session):
        if self.trust_host(error):
            session.start()
        else:
            session.view.write_notice(self.tr("Connexion annulée : l’identité du serveur n’a pas été confirmée."), "33")

    def update_recent_menu(self):
        self.recent_menu.clear()
        for site in self.recent_sites:
            try:
                profile = normalize_profile(site)
            except (TypeError, ValueError):
                continue
            self.recent_menu.addAction(profile["name"]).triggered.connect(
                lambda checked=False, p=profile: self.connect_profile(p))
        if not self.recent_menu.actions():
            self.recent_menu.addAction(self.tr("Aucune connexion récente")).setEnabled(False)
        else:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction(self.tr("Effacer la liste")).triggered.connect(self.clear_recent)

    def clear_recent(self):
        self.recent_sites = []
        self.settings.setValue("recent_sites", [])
        self.update_recent_menu()

    def forget_recent(self, profile_identity):
        """A deleted profile leaves the recent list too."""
        kept = [site for site in self.recent_sites if isinstance(site, dict) and identity(site) != profile_identity]
        if len(kept) != len(self.recent_sites):
            self.recent_sites = kept
            self.settings.setValue("recent_sites", kept)
            self.update_recent_menu()

    def manage_sites(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Gestionnaire de sites"))
        dialog.resize(720, 460)
        layout = QVBoxLayout(dialog)
        tree = QTreeWidget()
        tree.setHeaderLabels([self.tr("Nom"), self.tr("Hôte"), self.tr("Protocole")])
        groups = {}
        for site in self.sites:
            group = site.get("group") or self.tr("Sans groupe")
            if group not in groups:
                groups[group] = QTreeWidgetItem([group])
                tree.addTopLevelItem(groups[group])
            item = QTreeWidgetItem([site.get("name") or site["host"], site["host"], protocol_label(site["protocol"])])
            item.setData(0, Qt.ItemDataRole.UserRole, site)
            groups[group].addChild(item)
        tree.expandAll()
        tree.setColumnWidth(0, 260)
        layout.addWidget(tree)
        row = QHBoxLayout()
        def chosen(callback):
            item = tree.currentItem()
            site = item.data(0, Qt.ItemDataRole.UserRole) if item else None
            if site:
                dialog.accept()
                callback(site)
        needs_profile = []
        for title, callback, on_profile in [("Connecter", lambda: chosen(self.connect_profile), True),
            ("Modifier", lambda: chosen(self.new_connection), True), ("Supprimer", lambda: chosen(self.delete_profile), True),
            ("Nouveau", lambda: (dialog.accept(), self.new_connection()), False)]:
            button = QPushButton(self.tr(title))
            button.clicked.connect(callback)
            row.addWidget(button)
            if on_profile:
                needs_profile.append(button)
        row.addStretch()
        close = QPushButton(self.tr("Fermer"))
        close.clicked.connect(dialog.reject)
        row.addWidget(close)

        def follow_selection():
            item = tree.currentItem()
            selected = bool(item and item.data(0, Qt.ItemDataRole.UserRole))
            for button in needs_profile:
                button.setEnabled(selected)
        tree.currentItemChanged.connect(lambda *args: follow_selection())
        follow_selection()
        if not self.sites:
            tree.setHeaderLabels([self.tr("Aucun profil : cliquez sur « Nouveau » ou importez vos sites (menu Serveurs).")])
        tree.itemDoubleClicked.connect(lambda *args: chosen(self.connect_profile))
        layout.addLayout(row)
        self.site_manager = dialog
        dialog.exec()

    def schedule_defaults(self):
        """Pre-fill a new task with the open server and the folders on screen."""
        defaults = {"local": self.local.path}
        if self.online and self.connection:
            defaults.update(profile=self.connection.identity, remote=self.remote.path)
        return defaults

    def show_assistant(self):
        self.set_assistant_visible(not self.assistant_panel.isVisible())

    def show_schedule(self):
        from smftp.ui.scheduler import ScheduleDialog
        ScheduleDialog(self.scheduler, self, self.schedule_defaults()).exec()

    def cancel_transfers(self):
        self.transfers.confirm_cancel_all()

    def update_speed_limit(self, value):
        # The queue's shared limiter applies the value; only persist it here.
        self.settings.setValue("speed_limit", value)

    def copy_files(self, cut=False):
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QPlainTextEdit)):
            focused.cut() if cut else focused.copy()
            return
        panel = self.active_panel()
        if not panel.remote and not cut:
            from PySide6.QtCore import QMimeData
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(path) for path, _ in panel.selected()])
            QApplication.clipboard().setMimeData(mime)
        self.clipboard_files = (panel.remote, panel.selected(), cut,
            self.connection.identity if panel.remote and self.connection else None)

    def clear_file_clipboard(self):
        self.clipboard_files = None

    def select_all_files(self):
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QPlainTextEdit)):
            focused.selectAll()
        else:
            panel = self.active_panel()
            panel.stack.currentWidget().selectAll()

    def paste_files(self):
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QPlainTextEdit)):
            focused.paste()
            return
        if not self.clipboard_files:
            mime = QApplication.clipboard().mimeData()
            if mime is None or not mime.hasUrls():
                return
            paths = [(url.toLocalFile(), Path(url.toLocalFile()).is_dir()) for url in mime.urls() if url.isLocalFile()]
            self.clipboard_files = (False, paths, False, None)
        remote_source, selected, cut, source_id = self.clipboard_files
        panel = self.active_panel()
        if remote_source and (not self.online or self.connection.identity != source_id):
            self.on_error(self.tr("Reconnectez le serveur d’origine pour coller ces fichiers."))
            return
        if cut and remote_source != panel.remote:
            self.on_error(self.tr("Le déplacement entre serveurs et disque nécessite de copier puis de supprimer après vérification. Le collage n’a rien supprimé."))
            return
        if remote_source != panel.remote:
            self.handle_drop([dict(path=p, directory=d) for p, d in selected], panel.path, remote_source, panel.remote)
            return
        from smftp.utils.paths import unique_copy_name
        remote_names = {entry.name for entry in getattr(panel, "last_entries", [])}
        for source, directory in selected:
            name = posixpath.basename(source) if remote_source else Path(source).name
            destination = posixpath.join(panel.path, name) if remote_source else str(Path(panel.path) / name)
            same = posixpath.normpath(source) == posixpath.normpath(destination) if remote_source else Path(source) == Path(destination)
            if same:
                if cut:
                    continue
                # Duplicate in place: "nom (copie).ext", then "nom (copie 2).ext"...
                if remote_source:
                    copy_name = unique_copy_name(Path(name), lambda p: p.name in remote_names, directory).name
                    remote_names.add(copy_name)
                    destination = posixpath.join(panel.path, copy_name)
                else:
                    destination = str(unique_copy_name(Path(destination), lambda p: p.exists() or p.is_symlink()))
            if remote_source and cut:
                self.operation_requested.emit("rename", [source, destination])
            elif not remote_source:
                resolved = self.local_destination(source, destination)
                if resolved:
                    self.start_local("move" if cut else "copy", [source, *resolved])
            else:
                temporary = tempfile.TemporaryDirectory(prefix="smftp-copy-")
                self.temp_dirs.append(temporary)
                from smftp.utils.paths import local_child
                try:
                    local = str(local_child(temporary.name, name))
                except ValueError as error:
                    self.on_error(str(error))
                    return
                task = Transfer(self.connection, local, source, False, directory, conflict="overwrite")
                self.pending_remote_copies[id(task)] = (destination, directory)
                self.transfers.add(task, transient=True)
        if cut:
            self.clipboard_files = None

    def prepare_external_drag(self):
        if not self.online:
            self.statusBar().showMessage(self.tr("Connectez un serveur pour préparer un glisser-déposer."), 5000)
            return
        selected = self.remote.selected()
        if not selected:
            self.statusBar().showMessage(self.tr("Sélectionnez d’abord des éléments du serveur."), 5000)
            return
        temporary = tempfile.TemporaryDirectory(prefix="smftp-drag-")
        self.temp_dirs.append(temporary)
        for remote, directory in selected:
            from smftp.utils.paths import local_child
            try:
                local = str(local_child(temporary.name, posixpath.basename(remote)))
            except ValueError as error:
                self.on_error(str(error))
                return
            task = Transfer(self.connection, local, remote, False, directory, conflict="overwrite")
            entry = next((e for e in self.remote.last_entries if e.name == posixpath.basename(remote)), None)
            metadata = (entry.size, entry.modified) if entry else None
            self.drag_downloads[id(task)] = (self.connection.identity, remote, local, metadata)
            self.transfers.add(task, transient=True)
        self.log(self.tr("Préparation du glisser-déposer externe. Une fois terminé, glissez la sélection vers l’Explorateur Windows."))

    def recursive_search(self):
        from smftp.workers.search import SearchWorker
        panel = self.active_panel()
        if panel.remote and not self.online:
            self.statusBar().showMessage(self.tr("Recherche distante : connectez d’abord un serveur."), 5000)
            return
        pattern, ok = QInputDialog.getText(self, self.tr("Recherche récursive"), self.tr("Nom ou motif (ex. *.php) :"))
        if not ok or not pattern:
            return
        if not any(c in pattern for c in "*?["):
            pattern = f"*{pattern}*"
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Recherche récursive"))
        dialog.resize(850, 500)
        layout = QVBoxLayout(dialog)
        label = QLabel(self.tr("Recherche en cours… Limite : 10 000 résultats."))
        layout.addWidget(label)
        table = QTreeWidget()
        table.setHeaderLabels([self.tr("Chemin"), self.tr("Type"), self.tr("Taille")])
        table.setColumnWidth(0, 580)
        layout.addWidget(table)
        worker = SearchWorker(panel.path, pattern, self.connection if panel.remote else None, self)
        self.search_workers.append(worker)
        from smftp.ui.explorer import size_label
        worker.found.connect(lambda path, entry: table.addTopLevelItem(QTreeWidgetItem([path, "Dossier" if entry.is_dir else "Fichier", "—" if entry.is_dir else size_label(entry.size)])))
        worker.failed.connect(self.on_error)
        stopped_by_user = []

        def finished():
            count = table.topLevelItemCount()
            if worker.limit_reached:
                text = self.tr("Recherche interrompue : limite de 10 000 résultats atteinte. Affinez le motif.")
            elif stopped_by_user:
                text = self.tr("Recherche arrêtée : %n résultat(s) trouvé(s) avant l’arrêt, liste incomplète.", "", count)
            else:
                text = self.tr("Recherche terminée : %n résultat(s).", "", count)
            if worker.unreadable:
                text += " " + self.tr("%n dossier(s) inaccessible(s) ignoré(s).", "", worker.unreadable)
            label.setText(text)
            stop.setEnabled(False)

        def navigate(item, column):
            path = item.text(0)
            panel.search.clear()
            if panel.remote:
                panel.reveal(posixpath.dirname(path) or "/", posixpath.basename(path))
            else:
                panel.reveal(str(Path(path).parent), Path(path).name)

        def request_stop():
            stopped_by_user.append(True)
            worker.requestInterruption()

        table.itemDoubleClicked.connect(navigate)
        table.setToolTip(self.tr("Double-cliquez pour afficher l’élément dans son dossier."))
        row = QHBoxLayout()
        stop = QPushButton(self.tr("Arrêter"))
        stop.clicked.connect(request_stop)
        close = QPushButton(self.tr("Fermer"))
        close.clicked.connect(dialog.close)
        row.addStretch()
        row.addWidget(stop)
        row.addWidget(close)
        layout.addLayout(row)
        worker.finished.connect(finished)
        dialog.finished.connect(worker.requestInterruption)
        worker.start()
        dialog.show()
        self.search_dialogs = getattr(self, "search_dialogs", []) + [dialog]

    def notify(self, title, message):
        if self.settings.value("notifications", True, type=bool) and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3500)

    def set_icon_views(self, enabled):
        self.local.set_icon_mode(enabled)
        self.remote.set_icon_mode(enabled)

    def show_help(self):
        """Short guide in the window, the full documentation online."""
        from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Documentation et raccourcis"))
        dialog.resize(720, 620)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml(self.tr(HELP_HTML))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(self.tr("Fermer"))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def ftp_command(self):
        if not self.online or self.connection.protocol not in ("FTP", "FTPS", "FTPSI"):
            self.on_error(self.tr("La console de commandes est disponible pour les connexions FTP et FTPS actives."))
            return
        command, accepted = QInputDialog.getText(self, self.tr("Commande FTP"),
            self.tr("Commande (ex. FEAT, PWD, SYST ou SITE…). La réponse apparaîtra dans le journal :"))
        if not accepted or not command.strip():
            return
        verb = command.split()[0].upper()
        if verb in ("DELE", "RMD", "XRMD", "RNFR", "RNTO", "SITE"):
            if QMessageBox.question(self, self.tr("Commande modifiant le serveur"),
                self.tr("Cette commande peut modifier ou supprimer des données. L’exécuter ?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.activity_tabs.setCurrentWidget(self.log_panel)
        self.command_requested.emit(command.strip())

    def show_about(self):
        from smftp.workers.updates import CURRENT_VERSION
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("À propos de SMFTP"))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(self.tr("<b>SMFTP %s</b><br>Client FTP, FTPS, SFTP et SSH pour le bureau.<br><br>"
                            "Code source : <a href=\"https://github.com/Smeth/smftp\">github.com/Smeth/smftp</a><br>"
                            "<a href=\"https://github.com/Smeth/smftp/blob/main/docs/CONFIDENTIALITE.md\">Politique de confidentialité</a> : "
                            "SMFTP ne collecte aucune donnée.<br>"
                            "Licence : <a href=\"https://polyformproject.org/licenses/noncommercial/1.0.0\">PolyForm Noncommercial 1.0.0</a> — "
                            "usage non commercial libre ; usage commercial sur autorisation.<br>"
                            "Copyright © 2026 Smeth.<br><br>"
                            "Composants tiers sous leur propre licence : Qt / PySide6 (LGPL v3), Paramiko (LGPL v2.1), keyring (MIT)… "
                            "Voir le dossier « licenses » de l’application.") % CURRENT_VERSION)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        box.exec()

    def show_diagnostic(self):
        import platform
        from PySide6 import __version__
        import keyring
        from smftp.utils.storage import data_dir
        try:
            backend = type(keyring.get_keyring()).__name__
        except Exception:
            backend = self.tr("Indisponible")
        from smftp.workers.updates import CURRENT_VERSION
        import paramiko
        text = (f"SMFTP {CURRENT_VERSION}\nPython {platform.python_version()}\nQt / PySide6 {__version__}\n"
                f"Paramiko {paramiko.__version__} • keyring {getattr(keyring, '__version__', '?')}\n"
                f"{platform.system()} {platform.release()} ({platform.machine()})\n"
                f"Coffre-fort : {backend}\nDonnées : {data_dir()}\n"
                f"Connexion : {self.connection.protocol + ' ' + self.connection.host if self.online and self.connection else 'aucune'}\n"
                f"File : {len(self.transfers.records)} tâche(s)")
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Diagnostic"))
        layout = QVBoxLayout(dialog)
        from PySide6.QtWidgets import QPlainTextEdit
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setAccessibleName(self.tr("Informations de diagnostic"))
        view.setMinimumSize(520, 220)
        layout.addWidget(view)
        row = QHBoxLayout()
        copy = QPushButton(self.tr("Copier"))
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(text), copy.setText(self.tr("Copié ✓"))))
        save = QPushButton(self.tr("Enregistrer…"))
        def save_report():
            from PySide6.QtWidgets import QFileDialog
            filename, _ = QFileDialog.getSaveFileName(dialog, self.tr("Enregistrer le diagnostic"), "smftp-diagnostic.txt", "Texte (*.txt)")
            if filename:
                try:
                    Path(filename).write_text(text, encoding="utf-8")
                except OSError:
                    self.on_error(self.tr("Impossible d’enregistrer ce fichier."))
        save.clicked.connect(save_report)
        close = QPushButton(self.tr("Fermer"))
        close.clicked.connect(dialog.accept)
        row.addWidget(copy)
        row.addWidget(save)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)
        dialog.exec()

    def check_for_updates(self):
        from smftp.workers.updates import UpdateWorker, checks_for_updates
        if not checks_for_updates():
            return

        if getattr(self, "update_worker", None) and self.update_worker.isRunning():
            return
        self.update_worker = UpdateWorker(self)
        self.update_worker.completed.connect(self._show_update_result)
        self.update_worker.start()

    def _show_update_result(self, result):
        from PySide6.QtGui import QDesktopServices

        status = result["status"]
        if status == "update":
            answer = QMessageBox.question(
                self, self.tr("Mise à jour disponible"),
                self.tr("La version {version} est disponible sur GitHub.\n\nOuvrir la page de téléchargement ?").format(version=result["version"]),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(result["url"]))
        elif status == "current":
            QMessageBox.information(self, self.tr("Mises à jour"), self.tr("Vous utilisez déjà la dernière version ({version}).").format(version=result["version"]))
        elif status == "none":
            QMessageBox.information(self, self.tr("Mises à jour"), self.tr("Aucune nouvelle mise à jour disponible."))
        else:
            QMessageBox.warning(self, self.tr("Vérification impossible"), self.tr("Impossible de vérifier les mises à jour. Vérifiez votre connexion Internet puis réessayez.\n\n{detail}").format(detail=result.get("detail", "")))
