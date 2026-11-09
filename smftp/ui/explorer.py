"""Local and remote directory browsers."""
from datetime import datetime
from pathlib import Path
import posixpath
import json
from PySide6.QtCore import Qt, Signal, QDir, QEvent, QMimeData, QUrl, QSize
from PySide6.QtGui import QDrag, QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTreeView, QTreeWidget, QTreeWidgetItem,
                               QHeaderView, QStyle, QFileSystemModel, QListView, QListWidget, QListWidgetItem, QStackedWidget)


def size_label(size: int) -> str:
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024


class EntryItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        return self.data(column, Qt.ItemDataRole.UserRole) < other.data(column, Qt.ItemDataRole.UserRole)


class LocalView(QTreeView):
    def startDrag(self, actions):
        paths = [self.model().filePath(i) for i in self.selectionModel().selectedRows(0)]
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class LocalModel(QFileSystemModel):
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return [self.tr("Nom"), self.tr("Taille"), self.tr("Type"), self.tr("Modifié le")][section] if section < 4 else ""
        return super().headerData(section, orientation, role)


class RemoteView(QTreeWidget):
    def startDrag(self, actions):
        paths = [{"path": path, "directory": directory} for path, directory in self.owner.selected()]
        mime = QMimeData()
        mime.setData("application/x-smftp-remote", json.dumps(paths).encode())
        cached = [self.owner.external_cache.get(p["path"]) for p in paths]
        if paths and all(path and Path(path).exists() for path in cached):
            mime.setUrls([QUrl.fromLocalFile(path) for path in cached])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class RemoteIcons(QListWidget):
    startDrag = RemoteView.startDrag


class LocalIcons(QListView):
    def startDrag(self, actions):
        LocalView.startDrag(self, actions)


class Explorer(QWidget):
    requested = Signal(str)
    dropped = Signal(object, str, bool)
    context_requested = Signal(object)
    file_opened = Signal()

    def __init__(self, remote: bool = False):
        super().__init__()
        self.remote = remote
        self.external_cache = {}
        self.external_metadata = {}
        self.hidden = False
        self.last_entries = []
        self.path = "/" if remote else str(Path.home())
        self.history = []
        self.position = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 12)
        title = QHBoxLayout()
        heading = QLabel(self.tr("SERVEUR DISTANT" if remote else "ESPACE LOCAL"))
        heading.setObjectName("eyebrow")
        title.addWidget(heading)
        title.addStretch()
        self.count = QLabel(self.tr("Non connecté" if remote else "Votre ordinateur"))
        self.count.setObjectName("muted")
        title.addWidget(self.count)
        layout.addLayout(title)
        nav = QHBoxLayout()
        self.navigation_buttons = []
        for label, title, callback in [
                ("←", "Dossier précédent", lambda: self.travel(-1)),
                ("→", "Dossier suivant", lambda: self.travel(1)),
                ("↑", "Dossier parent", self.parent_folder),
                ("↻", "Actualiser", self.refresh)]:
            button = QPushButton(label)
            button.setObjectName("navigation")
            button.setFixedSize(40, 40)
            button.setToolTip(self.tr(title))
            button.setAccessibleName(self.tr(title))
            button.clicked.connect(callback)
            self.navigation_buttons.append(button)
            nav.addWidget(button)
        self.update_navigation()
        self.address = QLineEdit(self.path)
        self.address.setPlaceholderText(self.tr("Chemin du dossier"))
        self.address.returnPressed.connect(lambda: self.navigate(self.address.text()))
        nav.addWidget(self.address)
        layout.addLayout(nav)
        self.crumbs = QHBoxLayout()
        layout.addLayout(self.crumbs)
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Rechercher dans ce dossier…"))
        self.search.textChanged.connect(self.filter_entries)
        layout.addWidget(self.search)
        if remote:
            self.view = RemoteView()
            self.view.owner = self
            self.view.setHeaderLabels([self.tr(x) for x in ["Nom", "Taille", "Type", "Modifié le", "Permissions"]])
            self.view.itemDoubleClicked.connect(self.open_remote)
            self.view.setSortingEnabled(True)
        else:
            self.model = LocalModel(self)
            self.model.setRootPath(self.path)
            self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
            self.view = LocalView()
            self.view.setModel(self.model)
            self.view.setRootIndex(self.model.index(self.path))
            self.view.doubleClicked.connect(self.open_local)
            self.view.setSortingEnabled(True)
        self.view.setRootIsDecorated(False)
        self.view.setAlternatingRowColors(False)
        self.view.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.view.setDragEnabled(True)
        self.view.setAcceptDrops(True)
        self.view.viewport().setAcceptDrops(True)
        self.view.setDropIndicatorShown(True)
        self.view.viewport().installEventFilter(self)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(lambda point: self.context_requested.emit(self.view.viewport().mapToGlobal(point)))
        self.view.setUniformRowHeights(True)
        self.view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.icons = RemoteIcons() if remote else LocalIcons()
        self.icons.setViewMode(QListView.ViewMode.IconMode)
        self.icons.setResizeMode(QListView.ResizeMode.Adjust)
        self.icons.setIconSize(QSize(42, 42))
        self.icons.setGridSize(QSize(130, 90))
        self.icons.setWordWrap(True)
        self.icons.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.icons.setDragEnabled(True)
        self.icons.setAcceptDrops(True)
        self.icons.viewport().setAcceptDrops(True)
        self.icons.viewport().installEventFilter(self)
        self.icons.setDropIndicatorShown(True)
        self.icons.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.icons.customContextMenuRequested.connect(lambda point: self.context_requested.emit(self.icons.viewport().mapToGlobal(point)))
        if remote:
            self.icons.owner = self
            self.icons.itemSelectionChanged.connect(self.sync_icon_selection)
            self.icons.itemDoubleClicked.connect(self.open_icon)
        else:
            self.icons.setModel(self.model)
            self.icons.setModelColumn(0)
            self.icons.setRootIndex(self.model.index(self.path))
            self.icons.setSelectionModel(self.view.selectionModel())
            self.icons.doubleClicked.connect(self.open_local)
        self.stack.addWidget(self.icons)
        layout.addWidget(self.stack)
        self.empty = QLabel(self.tr("Connectez un serveur pour parcourir vos fichiers.\nVos connexions restent sous votre contrôle."))
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setVisible(remote)
        layout.addWidget(self.empty)
        if not remote:
            self.commit(self.path)

    def navigate(self, path: str) -> None:
        if self.remote:
            self.requested.emit(posixpath.normpath(path))
        elif Path(path).is_dir():
            self.view.setRootIndex(self.model.index(path))
            self.icons.setRootIndex(self.model.index(path))
            self.commit(str(Path(path).resolve()))
        else:
            self.address.setText(self.path)

    def commit(self, path: str) -> None:
        self.path = path
        self.address.setText(path)
        if self.position < 0 or self.history[self.position] != path:
            self.history = self.history[:self.position + 1] + [path]
            self.position = len(self.history) - 1
        while self.crumbs.count():
            item = self.crumbs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        parts = path.replace("\\", "/").split("/")
        for i, part in enumerate(parts):
            if not part and i > 0:
                continue
            target = "/".join(parts[:i + 1]) or "/"
            button = QPushButton(part or "/")
            button.setObjectName("breadcrumb")
            button.clicked.connect(lambda checked=False, p=target: self.navigate(p))
            self.crumbs.addWidget(button)
        self.crumbs.addStretch()
        self.update_navigation()

    def update_navigation(self) -> None:
        back, forward, parent, refresh = self.navigation_buttons
        back.setEnabled(self.position > 0)
        forward.setEnabled(0 <= self.position < len(self.history) - 1)
        connected = not self.remote or self.position >= 0
        parent.setEnabled(connected and (
            posixpath.normpath(self.path) != "/" if self.remote
            else Path(self.path).parent != Path(self.path)))
        refresh.setEnabled(connected)

    def travel(self, offset: int) -> None:
        target = self.position + offset
        if 0 <= target < len(self.history):
            self.position = target
            self.navigate(self.history[target])

    def parent_folder(self) -> None:
        self.navigate(posixpath.dirname(self.path) or "/" if self.remote else str(Path(self.path).parent))

    def refresh(self) -> None:
        self.navigate(self.path)

    def open_local(self, index) -> None:
        if self.model.isDir(index):
            self.navigate(self.model.filePath(index))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.model.filePath(index)))

    def open_remote(self, item, column) -> None:
        if item.data(0, Qt.ItemDataRole.UserRole + 1):
            self.navigate(posixpath.join(self.path, item.text(0)))
        else:
            self.file_opened.emit()

    def set_entries(self, path: str, entries: list) -> None:
        for remote in list(self.external_cache):
            if posixpath.dirname(remote) == posixpath.normpath(path):
                entry = next((e for e in entries if e.name == posixpath.basename(remote)), None)
                if not entry or self.external_metadata.get(remote) != (entry.size, entry.modified):
                    self.external_cache.pop(remote, None)
                    self.external_metadata.pop(remote, None)
        self.last_entries = entries
        self.commit(path)
        self.empty.hide()
        self.view.setSortingEnabled(False)
        self.view.clear()
        self.icons.clear()
        for entry in entries:
            kind = "Dossier" if entry.is_dir else (Path(entry.name).suffix.lstrip(".").upper() or "Fichier")
            date = datetime.fromtimestamp(entry.modified).strftime("%d/%m/%Y %H:%M") if entry.modified else "—"
            item = EntryItem([entry.name, "—" if entry.is_dir else size_label(entry.size), kind, date, entry.permissions])
            values = [entry.name.casefold(), entry.size, kind, entry.modified, entry.permissions]
            for col, value in enumerate(values):
                item.setData(col, Qt.ItemDataRole.UserRole, value)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, entry.is_dir)
            icon = QStyle.StandardPixmap.SP_DirIcon if entry.is_dir else QStyle.StandardPixmap.SP_FileIcon
            item.setIcon(0, self.style().standardIcon(icon))
            if not entry.is_dir:
                suffix = Path(entry.name).suffix.lower()
                icon = (QStyle.StandardPixmap.SP_FileDialogContentsView if suffix in (".py", ".js", ".php", ".html", ".json", ".css")
                        else QStyle.StandardPixmap.SP_FileDialogInfoView if suffix in (".png", ".jpg", ".gif", ".webp")
                        else QStyle.StandardPixmap.SP_DriveHDIcon if suffix in (".zip", ".gz", ".7z", ".tar") else icon)
                item.setIcon(0, self.style().standardIcon(icon))
            self.view.addTopLevelItem(item)
            grid_item = QListWidgetItem(item.icon(0), entry.name)
            grid_item.setData(Qt.ItemDataRole.UserRole, entry.is_dir)
            self.icons.addItem(grid_item)
        self.view.setSortingEnabled(True)
        self.count.setText(self.tr("%n élément(s)", "", len(entries)))
        self.filter_entries(self.search.text())

    def set_icon_mode(self, enabled):
        self.stack.setCurrentWidget(self.icons if enabled else self.view)

    def sync_icon_selection(self):
        names = {item.text() for item in self.icons.selectedItems()}
        for i in range(self.view.topLevelItemCount()):
            item = self.view.topLevelItem(i)
            item.setSelected(item.text(0) in names)

    def open_icon(self, item):
        if item.data(Qt.ItemDataRole.UserRole):
            self.navigate(posixpath.join(self.path, item.text()))
        else:
            self.file_opened.emit()

    def filter_entries(self, text: str) -> None:
        if self.remote:
            for i in range(self.view.topLevelItemCount()):
                item = self.view.topLevelItem(i)
                item.setHidden(text.casefold() not in item.text(0).casefold() or
                               (not self.hidden and item.text(0).startswith(".")))
            for i in range(self.icons.count()):
                item = self.icons.item(i)
                item.setHidden(text.casefold() not in item.text().casefold() or (not self.hidden and item.text().startswith(".")))
        else:
            self.model.setNameFilters([f"*{text}*"] if text else [])
            self.model.setNameFilterDisables(False)

    def show_hidden(self, enabled: bool) -> None:
        self.hidden = enabled
        if self.remote:
            self.filter_entries(self.search.text())
        if not self.remote:
            flags = QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot
            self.model.setFilter(flags | QDir.Filter.Hidden if enabled else flags)

    def selected(self):
        if self.remote:
            return [(posixpath.join(self.path, item.text(0)), bool(item.data(0, Qt.ItemDataRole.UserRole + 1))) for item in self.view.selectedItems()]
        return [(self.model.filePath(i), self.model.isDir(i)) for i in self.view.selectionModel().selectedRows(0)]

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop) and watched in (self.view.viewport(), self.icons.viewport()):
            mime = event.mimeData()
            if mime.hasUrls() or mime.hasFormat("application/x-smftp-remote"):
                event.acceptProposedAction()
                if event.type() == QEvent.Type.Drop:
                    target = self.path
                    is_icons = watched == self.icons.viewport()
                    active_view = self.icons if is_icons else self.view
                    index = active_view.indexAt(event.position().toPoint())
                    if index.isValid():
                        if self.remote:
                            item = active_view.itemFromIndex(index)
                            directory = item.data(Qt.ItemDataRole.UserRole) if is_icons else item.data(0, Qt.ItemDataRole.UserRole + 1)
                            if directory:
                                target = posixpath.join(self.path, item.text() if is_icons else item.text(0))
                        elif self.model.isDir(index):
                            target = self.model.filePath(index)
                    remote_source = mime.hasFormat("application/x-smftp-remote")
                    paths = json.loads(bytes(mime.data("application/x-smftp-remote"))) if remote_source else [{"path": url.toLocalFile(), "directory": Path(url.toLocalFile()).is_dir()} for url in mime.urls() if url.isLocalFile()]
                    self.dropped.emit(paths, target, remote_source)
                return True
        return super().eventFilter(watched, event)
