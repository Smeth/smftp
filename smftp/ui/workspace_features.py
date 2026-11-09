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
from smftp.models.entries import Connection
from smftp.utils.profiles import normalize_profile, profile_for, identity
from smftp.workers.transfer import Transfer


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
        self.add_command(menu, "Nouvelle connexion…", lambda: self.new_connection(), "Ctrl+Shift+N")
        self.add_command(menu, "Gestionnaire de sites…", self.manage_sites, "Ctrl+Shift+S")
        self.add_command(menu, "Reconnecter", self.reconnect, "Ctrl+R")
        self.add_command(menu, "Déconnecter", self.disconnect_session)
        self.recent_menu = menu.addMenu(self.tr("Connexions récentes"))
        self.update_recent_menu()
        menu.addSeparator()
        self.add_command(menu, "Quitter", self.close, "Ctrl+Q")
        edit = self.menuBar().addMenu(self.tr("Édition"))
        for title, callback, shortcut in [
            ("Copier", lambda: self.copy_files(False), "Ctrl+C"),
            ("Couper", lambda: self.copy_files(True), "Ctrl+X"),
            ("Coller", self.paste_files, "Ctrl+V"),
            ("Sélectionner tout", self.select_all_files, "Ctrl+A"),
            ("Nouveau dossier", lambda: self.file_action("mkdir"), ""),
            ("Renommer", lambda: self.file_action("rename"), ""),
            ("Supprimer", lambda: self.file_action("remove"), ""),
            ("Propriétés", lambda: self.file_action("properties"), "Alt+Return"),
            ("Recherche récursive…", self.recursive_search, "Ctrl+Shift+F")]:
            self.add_command(edit, title, callback, shortcut)
        transfers = self.menuBar().addMenu(self.tr("Transferts"))
        for title, callback in [("Téléverser la sélection", lambda: self.file_action("transfer", self.local)),
            ("Télécharger la sélection", lambda: self.file_action("transfer", self.remote)),
            ("Mettre tout en pause", self.transfers.pause_all), ("Reprendre tout", self.transfers.resume_all),
            ("Annuler tous les transferts…", self.cancel_transfers),
            ("Relancer la sélection", self.transfers.retry), ("Effacer les tâches terminées", self.transfers.clear_finished),
            ("Limites et conflits…", self.preferences)]:
            self.add_command(transfers, title, callback)
        display = self.display_menu
        self.add_command(display, "Fichiers distants cachés", self.remote.show_hidden, checkable=True)
        self.add_command(display, "Vue en icônes", self.set_icon_views, checkable=True)
        self.add_command(display, "Panneaux verticaux", lambda checked: self.panel_splitter.setOrientation(
            Qt.Orientation.Vertical if checked else Qt.Orientation.Horizontal), checkable=True)
        self.add_command(display, "Journal", lambda: self.activity_tabs.setCurrentWidget(self.logs))
        self.add_command(display, "File de transferts", lambda: self.activity_tabs.setCurrentWidget(self.transfers))
        self.add_command(display, "Effacer le journal", self.logs.clear)
        self.add_command(display, "Thème clair / sombre", self.toggle_theme)
        self.add_command(self.tools_menu, "Commande FTP…", self.ftp_command)
        help_menu = self.menuBar().addMenu(self.tr("Aide"))
        self.add_command(help_menu, "Documentation et raccourcis", self.show_help, "F1")
        self.add_command(help_menu, "Diagnostic", self.show_diagnostic)
        self.add_command(help_menu, "À propos", lambda: QMessageBox.information(self, "SMFTP", self.tr("SMFTP 0.2\nClient FTP, FTPS et SFTP pour le bureau.")))
        self.remote.file_opened.connect(lambda: self.edit_remote(False))
        order = ["Fichier", "Édition", "Affichage", "Serveurs", "Transferts", "Outils", "Aide"]
        actions = list(self.menuBar().actions())
        for action in actions:
            self.menuBar().removeAction(action)
        for title in order:
            for action in actions:
                if action.text() == self.tr(title):
                    self.menuBar().addAction(action)
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

    def connect_profile(self, site):
        if self.busy or self.closing:
            return
        connection = Connection(**normalize_profile(site))
        try:
            import keyring
            secret = keyring.get_password("SMFTP", connection.identity)
        except Exception:
            secret = None
        has_key = connection.protocol == "SFTP" and bool(connection.key_file)
        if secret is None and not has_key and connection.user != "anonymous":
            self.new_connection(site)
            return
        connection.password = secret or ""
        self.retry_timer.stop()
        self.retry_count = 0
        self.connection = connection
        self.start_connection()

    def disconnect_session(self):
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
        profile = profile_for(self.connection)
        self.recent_sites = [profile] + [site for site in self.recent_sites
            if isinstance(site, dict) and identity(site) != self.connection.identity]
        self.recent_sites = self.recent_sites[:10]
        self.settings.setValue("recent_sites", self.recent_sites)
        self.update_recent_menu()
        if self.connection.local_directory:
            self.local.navigate(self.connection.local_directory)
        self.transfers.authorize(self.connection)

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
            item = QTreeWidgetItem([site.get("name") or site["host"], site["host"], site["protocol"]])
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
        for title, callback in [("Connecter", lambda: chosen(self.connect_profile)),
            ("Modifier", lambda: chosen(self.new_connection)), ("Supprimer", lambda: chosen(self.delete_profile)),
            ("Nouveau", lambda: (dialog.accept(), self.new_connection()))]:
            button = QPushButton(self.tr(title))
            button.clicked.connect(callback)
            row.addWidget(button)
        tree.itemDoubleClicked.connect(lambda *args: chosen(self.connect_profile))
        layout.addLayout(row)
        dialog.exec()

    def cancel_transfers(self):
        if QMessageBox.question(self, self.tr("Annuler les transferts"), self.tr("Annuler tous les transferts en cours et en attente ?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.transfers.cancel_all()

    def update_speed_limit(self, value):
        self.settings.setValue("speed_limit", value)
        for record in self.transfers.records:
            record["worker"].speed_limit = value * 1024

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
        for source, directory in selected:
            name = posixpath.basename(source) if remote_source else Path(source).name
            destination = posixpath.join(panel.path, name) if remote_source else str(Path(panel.path) / name)
            if source == destination:
                if cut:
                    continue
                destination += ".copie"
            if remote_source and cut:
                self.operation_requested.emit("rename", [source, destination])
            elif not remote_source:
                self.start_local("move" if cut else "copy", [source, destination])
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
            return
        selected = self.remote.selected()
        if not selected:
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
        worker.found.connect(lambda path, entry: table.addTopLevelItem(QTreeWidgetItem([path, "Dossier" if entry.is_dir else "Fichier", size_label(entry.size)])))
        worker.failed.connect(self.on_error)
        worker.finished.connect(lambda: label.setText(self.tr("Recherche terminée : %n résultat(s).", "", table.topLevelItemCount())))
        def navigate(item, column):
            path = item.text(0)
            panel.search.clear()
            panel.navigate(posixpath.dirname(path) if panel.remote else str(Path(path).parent))
        table.itemDoubleClicked.connect(navigate)
        stop = QPushButton(self.tr("Arrêter"))
        stop.clicked.connect(worker.requestInterruption)
        layout.addWidget(stop)
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
        QMessageBox.information(self, self.tr("Documentation"), self.tr(
            "Double-cliquez sur un profil pour vous connecter directement. Clic droit → Modifier pour changer les identifiants.\n"
            "Clé SSH : utilisez Parcourir dans le formulaire.\n\n"
            "Ctrl+K : connexion · Ctrl+R : reconnecter · F5 : actualiser\n"
            "F2 : renommer · Suppr : supprimer · Ctrl+N : dossier\n"
            "Ctrl+C/X/V : copier/couper/coller · Ctrl+Shift+F : recherche récursive\n"
            "La file sauvegardée est restaurée en pause. Connectez le profil, puis Reprendre tout.\n"
            "Glisser vers Windows : clic droit distant → Préparer le glisser-déposer externe, puis glisser après téléchargement."))

    def ftp_command(self):
        if not self.online or self.connection.protocol not in ("FTP", "FTPS"):
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
        self.activity_tabs.setCurrentWidget(self.logs)
        self.command_requested.emit(command.strip())

    def show_diagnostic(self):
        import platform
        from PySide6 import __version__
        import keyring
        from smftp.utils.storage import data_dir
        try:
            backend = type(keyring.get_keyring()).__name__
        except Exception:
            backend = self.tr("Indisponible")
        QMessageBox.information(self, self.tr("Diagnostic"),
            f"SMFTP 0.2\nPython {platform.python_version()}\nQt / PySide6 {__version__}\n"
            f"{platform.system()} {platform.release()}\nCoffre-fort : {backend}\nDonnées : {data_dir()}\n"
            f"File : {len(self.transfers.records)} tâche(s)")
