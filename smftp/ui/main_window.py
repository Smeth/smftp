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


class MainWindow(WorkspaceFeatures, QMainWindow):
    connect_requested = Signal(object)
    list_requested = Signal(str)
    disconnect_requested = Signal()
    operation_requested = Signal(str, object)
    inspect_requested = Signal(str)
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
        self.pending_saves = {}
        self.temp_dirs = []
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.external_changed)
        self.sites = load_sites()
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(22, 14, 22, 14)
        brand = QLabel("◈  SMFTP")
        brand.setObjectName("brand")
        toolbar.addWidget(brand)
        subtitle = QLabel(self.tr("Un lien direct avec vos fichiers."))
        subtitle.setObjectName("muted")
        toolbar.addWidget(subtitle)
        toolbar.addStretch()
        theme = QPushButton(self.tr("◐  Apparence"))
        theme.clicked.connect(self.toggle_theme)
        toolbar.addWidget(theme)
        self.disconnect_button = QPushButton(self.tr("Déconnecter"))
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect_session)
        toolbar.addWidget(self.disconnect_button)
        self.connect_button = QPushButton(self.tr("＋  Nouvelle connexion"))
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(lambda: self.new_connection())
        toolbar.addWidget(self.connect_button)
        outer.addLayout(toolbar)
        workspace = QSplitter()
        outer.addWidget(workspace, 1)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(195)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 22, 18, 18)
        label = QLabel(self.tr("EXPLORATEUR"))
        label.setObjectName("eyebrow")
        side.addWidget(label)
        side.addSpacing(18)
        side.addWidget(QLabel(self.tr("Mes serveurs")))
        self.site_list = QListWidget()
        self.site_list.setObjectName("sites")
        self.site_list.addItems([f"◉  {s.get('name') or s['host']}" for s in self.sites])
        self.site_list.itemDoubleClicked.connect(lambda item: self.connect_profile(self.sites[self.site_list.row(item)]))
        self.site_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.site_list.customContextMenuRequested.connect(self.site_context_menu)
        side.addWidget(self.site_list)
        add = QPushButton(self.tr("＋  Ajouter un serveur"))
        add.clicked.connect(lambda: self.new_connection())
        side.addWidget(add)
        note = QLabel(self.tr("VOTRE ESPACE, CONNECTÉ\n\nSFTP · FTPS · FTP\nIdentifiants dans votre\ncoffre-fort système."))
        note.setObjectName("muted")
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
        panels.addWidget(self.local)
        panels.addWidget(self.remote)
        panels.setSizes([530, 530])
        main.addWidget(panels)
        tabs = QTabWidget()
        self.activity_tabs = tabs
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(1000)
        tabs.addTab(self.logs, self.tr("Journal de connexion"))
        self.transfers = Transfers(self)
        self.transfers.concurrency.setValue(self.settings.value("concurrency", 2, type=int))
        self.transfers.completed.connect(self.transfer_completed)
        self.transfers.changed.connect(self.refresh)
        self.transfers.outcome.connect(self.transfer_outcome)
        self.transfers.diagnostic.connect(lambda error: self.log(error.summary + "\n" + error.details))
        self.transfers.activity.connect(self.log)
        self.transfers.speed_changed.connect(self.update_speed)
        tabs.addTab(self.transfers, self.tr("Transferts"))
        tabs.setCurrentIndex(0)
        main.addWidget(tabs)
        main.setSizes([580, 150])
        workspace.addWidget(main)
        workspace.setSizes([220, 1160])
        self.statusBar().showMessage(self.tr("●  Déconnecté    •    Prêt à explorer"))
        self.statusBar().addPermanentWidget(QLabel("SMFTP  /  V1"))
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
        self.session.connected.connect(self.on_connected)
        self.session.progress.connect(self.log)
        self.session.listed.connect(self.on_listed)
        self.session.failed.connect(self.on_session_error)
        self.session.host_unknown.connect(self.confirm_host)
        self.session.disconnected.connect(self.on_disconnected)
        self.thread.finished.connect(self.session.deleteLater)
        self.thread.finished.connect(self.finish_close)
        for text, key, callback in [("Nouvelle connexion", "Ctrl+K", lambda: self.new_connection()),
                                     ("Actualiser", "F5", self.refresh),
                                     ("Rechercher", "Ctrl+F", self.focus_search),
                                     ("Nouveau dossier", "Ctrl+N", lambda: self.file_action("mkdir")),
                                     ("Renommer", "F2", lambda: self.file_action("rename")),
                                     ("Supprimer", "Delete", lambda: self.file_action("remove"))]:
            action = QAction(self.tr(text), self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            self.addAction(action)
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
                self.connection = connection
                self.start_connection()

    def refresh_sites(self):
        self.site_list.clear()
        for site in self.sites:
            group = site.get("group", "")
            label = (group + " / " if group else "") + (site.get("name") or site["host"])
            self.site_list.addItem(f"◉  {label}")
            self.site_list.item(self.site_list.count() - 1).setToolTip(identity(site))

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
        menu.addAction(self.tr("Connecter")).triggered.connect(lambda: self.connect_profile(site))
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
        self.activity_tabs.setCurrentWidget(self.logs)
        self.busy = True
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        self.statusBar().showMessage(self.tr("Connexion en cours…"))
        self.log(self.tr("Ouverture d’une connexion %s.") % self.connection.protocol)
        self.connect_requested.emit(self.connection)

    def on_connected(self, path):
        self.online = True
        self.disconnect_button.setEnabled(True)
        self.statusBar().showMessage(f"●  Connecté    •    {self.connection.host}    •    {self.connection.protocol}")
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
            record["refresh_baseline"] = True
            self.pending_saves[record["remote"]] = record
            self.inspect_requested.emit(record["remote"])
        self.refresh()

    def transfer_outcome(self, task, status):
        if status != "Terminé":
            self.pending_remote_copies.pop(id(task), None)
            self.drag_downloads.pop(id(task), None)
            self.edit_downloads.pop(id(task), None)
            record = self.edit_uploads.pop(id(task), None)
            if record:
                record.pop("save_again", None)
            if record and record.get("editor"):
                record["editor"].message.setText(self.tr("Envoi interrompu. La copie locale est conservée ; réessayez l’enregistrement."))
            self.log(self.tr("Transfert interrompu : %s. Vérifiez la connexion, l’espace disque et les permissions.") % status)

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
                    from smftp.workers.local import LocalOperation
                    self.start_local("move", [source, str(Path(target) / Path(source).name)])
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
            if not selected or QMessageBox.question(self, self.tr("Suppression définitive"), self.tr("Supprimer définitivement %n élément(s), ainsi que leur contenu ?", "", len(selected)), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
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
            dialog = PermissionsDialog(self, current)
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
            if import_passwords:
                from smftp.models.entries import Connection
                for site in imported:
                    secret = result.passwords.get(identity(site))
                    if secret is None:
                        continue
                    try:
                        connection = Connection(**site)
                        connection.password = secret
                        connection.remember = True
                        store_credential(connection)
                        site["remember"] = True
                    except Exception:
                        result.warnings.append(f"{site['name']} : coffre-fort indisponible ; mot de passe non importé.")
            updated = merge_profiles(self.sites, imported)
            save_sites(updated)
            self.sites = updated
            self.refresh_sites()
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

    def synchronize(self):
        from smftp.workers.sync import SyncPlanner
        if not self.online or (hasattr(self, "sync_worker") and self.sync_worker.isRunning()):
            return
        direction, ok = QInputDialog.getItem(self, self.tr("Synchroniser"), self.tr("Sens de copie (aucune suppression) :"), [self.tr("Local → Distant"), self.tr("Distant → Local")], 0, False)
        if not ok:
            return
        upload = direction == self.tr("Local → Distant")
        local, remote, connection = self.local.path, self.remote.path, self.connection
        self.sync_worker = SyncPlanner(connection, local, remote, upload, self)
        self.sync_worker.failed.connect(self.on_error)
        self.sync_worker.planned.connect(lambda plan: self.show_sync_plan(plan, connection, local, remote, upload))
        self.log(self.tr("Comparaison récursive des dossiers en cours…"))
        self.sync_worker.start()

    def show_sync_plan(self, plan, connection, local, remote, upload):
        import posixpath
        from PySide6.QtWidgets import QDialog, QTreeWidget, QTreeWidgetItem, QDialogButtonBox
        from smftp.ui.explorer import size_label
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Aperçu de synchronisation — aucune suppression"))
        dialog.resize(760, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tr("Sélectionnez les fichiers à copier. Taille et date sont comparées ; le contenu n’est pas haché.")))
        table = QTreeWidget()
        table.setHeaderLabels([self.tr("Fichier"), self.tr("Action"), self.tr("Taille")])
        table.setColumnWidth(0, 450)
        for path, state, size in plan:
            item = QTreeWidgetItem([path, self.tr(state), size_label(size)])
            item.setCheckState(0, Qt.CheckState.Checked)
            table.addTopLevelItem(item)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Copier la sélection"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            for i, (path, _, _) in enumerate(plan):
                if table.topLevelItem(i).checkState(0) == Qt.CheckState.Checked:
                    from smftp.utils.paths import local_child
                    destination = Path(local)
                    try:
                        for part in path.split("/"):
                            destination = local_child(destination, part)
                    except ValueError as error:
                        self.on_error(str(error))
                        return
                    self.transfers.add(Transfer(connection, str(destination), posixpath.join(remote, path), upload, conflict="ask"))

    def edit_remote(self, external=False):
        import tempfile
        selected = self.remote.selected()
        if not self.online or len(selected) != 1 or selected[0][1]:
            return
        item = self.remote.view.selectedItems()[0]
        size = item.data(1, Qt.ItemDataRole.UserRole)
        if size > 20 * 1024 * 1024:
            self.on_error(self.tr("L’aperçu est limité à 20 Mo. Téléchargez ce fichier pour l’ouvrir."))
            return
        remote = selected[0][0]
        try:
            directory = tempfile.TemporaryDirectory(prefix="smftp-", dir=self.settings.value("temp_directory", "") or None)
            self.temp_dirs.append(directory)
            from smftp.utils.paths import local_child
            local = str(local_child(directory.name, posixpath.basename(remote)))
            task = Transfer(self.connection, local, remote, False, conflict="overwrite")
            record = dict(local=local, remote=remote, connection=self.connection, external=external,
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
            self.watcher.addPath(local)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(local)):
                self.on_error(self.tr("Aucune application ne peut ouvrir ce fichier."))
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
        if record["remote"] in self.pending_saves or any(r is record for r in self.edit_uploads.values()):
            # Keep the running save intact and send the newest copy once it completes.
            record["save_again"] = True
            self.log(self.tr("Un envoi est déjà en cours pour %s : la nouvelle version sera envoyée ensuite.") % record["remote"])
            return
        self.pending_saves[record["remote"]] = record
        self.inspect_requested.emit(record["remote"])

    def inspected_file(self, path, entry):
        record = self.pending_saves.pop(path, None)
        if not record:
            return
        baseline = (entry.size, entry.modified) if entry else None
        if record.pop("refresh_baseline", False):
            record["baseline"] = baseline
            record["unsent"] = False
            if record.get("editor"):
                record["editor"].message.setText(self.tr("Modifications envoyées au serveur."))
            if record.pop("save_again", False):
                self.save_remote(record)
            return
        if baseline != record["baseline"]:
            if QMessageBox.question(self, self.tr("Fichier distant modifié"), self.tr("Le fichier a changé sur le serveur. Écraser cette version ?"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                record.pop("save_again", None)
                return
        # Any save requested from now on is included in this upload, which reads the file when it starts.
        record.pop("save_again", None)
        task = Transfer(record["connection"], record["local"], record["remote"], True, conflict="overwrite")
        self.edit_uploads[id(task)] = record
        self.transfers.add(task, transient=True)

    def inspection_failed(self, path):
        record = self.pending_saves.pop(path, None)
        if record:
            record.pop("save_again", None)
            if record.get("editor"):
                record["editor"].message.setText(self.tr("Vérification impossible : la copie locale est conservée ; réessayez l’enregistrement."))

    def on_disconnected(self):
        self.online = False
        self.pending_saves.clear()
        self.disconnect_button.setEnabled(False)
        self.remote.view.clear()
        self.remote.icons.clear()
        self.remote.last_entries = []
        self.remote.external_cache.clear()
        self.remote.external_metadata.clear()
        self.remote.empty.show()
        self.remote.count.setText(self.tr("Non connecté"))
        self.statusBar().showMessage(self.tr("●  Déconnecté"))

    def on_session_error(self, error):
        # Full diagnostics arrive in the preceding progress signal.
        self.busy = False
        self.connect_button.setEnabled(True)
        self.activity_tabs.setCurrentWidget(self.logs)
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
        self.activity_tabs.setCurrentWidget(self.logs)
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
        answer = QMessageBox.question(self, self.tr("Vérifier l’identité du serveur"),
            self.tr("Première connexion à %s\n\nEmpreinte %s :\n%s\n\nComparez cette empreinte avec celle fournie par votre administrateur. Faire confiance à ce serveur ?") % (error.host, error.key_type, error.fingerprint),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            try:
                import paramiko
                import base64
                hosts = paramiko.HostKeys(str(data_dir() / "known_hosts"))
                key = paramiko.PKey.from_type_string(error.key_type, base64.b64decode(error.key))
                hosts.add(error.host, error.key_type, key)
                hosts.save(str(data_dir() / "known_hosts"))
                self.start_connection()
            except Exception:
                self.on_error(self.tr("Impossible d’enregistrer la clé du serveur."))

    def toggle_theme(self):
        self.dark = not self.dark
        self.settings.setValue("dark", self.dark)
        self.apply_theme()

    def apply_theme(self):
        name = "dark" if self.dark else "light"
        path = Path(__file__).resolve().parents[2] / "resources" / "themes" / f"{name}.qss"
        self.setStyleSheet(path.read_text(encoding="utf-8"))

    def closeEvent(self, event):
        if not self.closing:
            dirty = any(editor.dirty for editor in self.editors if hasattr(editor, "dirty"))
            unsent = any(record.get("unsent") for record in self.edit_records.values())
            import sys
            if "--smoke-test" not in sys.argv and (self.isVisible() or self.online or self.transfers.active() or dirty or unsent):
                message = self.tr("Voulez-vous quitter SMFTP ?")
                if self.online or self.busy:
                    message += self.tr("\nLa connexion au serveur sera fermée.")
                if self.transfers.active():
                    message += self.tr("\nLes transferts seront interrompus et la file sera conservée en pause pour le prochain lancement.")
                if dirty or unsent:
                    message += self.tr("\nLes modifications non enregistrées ou non envoyées des copies temporaires seront perdues.")
                if QMessageBox.question(self, self.tr("Confirmer la fermeture"), message,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
        local_active = any(w.isRunning() for w in getattr(self, "local_workers", []))
        sync_active = (hasattr(self, "sync_worker") and self.sync_worker.isRunning()) or any(w.isRunning() for w in self.search_workers)
        if self.thread.isRunning() or self.transfers.active() or local_active or sync_active:
            event.ignore()
            if not self.closing:
                self.closing = True
                self.retry_timer.stop()
                self.hide()
                self.transfers.stop_all()
                for worker in self.search_workers:
                    worker.requestInterruption()
                if hasattr(self, "sync_worker"):
                    self.sync_worker.requestInterruption()
                # Quit after queued operations and cleanup have completed.
                self.session.disconnected.connect(self.thread.quit)
                self.disconnect_requested.emit()
                self.close_timer = QTimer(self)
                self.close_timer.timeout.connect(self.finish_close)
                self.close_timer.start(100)
        else:
            event.accept()

    def finish_close(self):
        sync_active = (hasattr(self, "sync_worker") and self.sync_worker.isRunning()) or any(w.isRunning() for w in self.search_workers)
        if self.closing and not self.thread.isRunning() and not self.transfers.active() and not sync_active and not any(w.isRunning() for w in getattr(self, "local_workers", [])):
            if hasattr(self, "close_timer"):
                self.close_timer.stop()
            self.close()
            QApplication.instance().quit()
