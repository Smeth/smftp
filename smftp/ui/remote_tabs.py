"""Several servers open at once, one tab each in the server panel.

Each ``RemoteTab`` owns everything that belongs to one server: its connection, its
browsing ``Session`` running in its own ``QThread``, its file panel (``Explorer``),
its state (online, busy), its automatic reconnection and its pending inspections.

The rest of the window keeps using ``self.connection``, ``self.session``,
``self.remote``, ``self.online``, ``self.busy``… These are properties that point to
the *current* tab: the tab whose session signal is being handled (see
``tab_context``), otherwise the tab on screen. A listing that arrives for a tab in
the background therefore updates that tab's panel, never the one on screen.
"""
from contextlib import contextmanager
from PySide6.QtCore import QObject, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QPushButton, QStackedWidget, QTabBar, QVBoxLayout, QWidget
from smftp.ui.explorer import Explorer
from smftp.workers.session import Session

MAX_TABS = 8


class RemoteTab(QObject):
    """One server: connection, session thread, file panel and state."""
    connect_requested = Signal(object)
    list_requested = Signal(str)
    disconnect_requested = Signal()
    operation_requested = Signal(str, object)
    inspect_requested = Signal(str, str, str)
    command_requested = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.connection = None
        self.online = False
        self.busy = False
        self.retry_count = 0
        self.removed = False  # Tab closed: its session is shutting down.
        self.tokens = set()  # Inspection tokens sent through this session.
        self.explorer = Explorer(remote=True)
        self.disconnect_button = QPushButton(self.tr("Déconnecter"))
        self.disconnect_button.setObjectName("compact")
        self.disconnect_button.setFixedHeight(24)
        self.disconnect_button.setEnabled(False)
        self.explorer.title_row.addWidget(self.disconnect_button)
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self.on_retry)
        self.thread = QThread(self)
        self.session = Session()
        self.session.moveToThread(self.thread)
        for signal, slot in ((self.connect_requested, self.session.connect_to),
                             (self.list_requested, self.session.list_path),
                             (self.disconnect_requested, self.session.close),
                             (self.operation_requested, self.session.operate),
                             (self.inspect_requested, self.session.inspect_path),
                             (self.command_requested, self.session.command)):
            signal.connect(slot)
        # Bound methods of this QObject: queued from the session thread to the window thread.
        self.session.progress.connect(self.on_progress)
        self.session.connected.connect(self.on_connected)
        self.session.listed.connect(self.on_listed)
        self.session.failed.connect(self.on_failed)
        self.session.host_unknown.connect(self.on_host_unknown)
        self.session.disconnected.connect(self.on_disconnected)
        self.session.operated.connect(self.on_operated)
        self.session.inspected.connect(self.on_inspected)
        self.session.inspect_failed.connect(self.on_inspect_failed)
        self.session.move_conflict.connect(self.on_move_conflict)
        self.thread.finished.connect(self.session.deleteLater)

    # ----- state -----
    def free(self):
        """No server shown and none being reached: a new connection may use this tab."""
        return not self.online and not self.busy

    def connecting(self):
        return self.busy and not self.online

    def title(self):
        if self.connection is None:
            return self.tr("Non connecté")
        return self.connection.name or self.connection.host

    def state(self):
        """Name of the state dot (same colours as the terminal tabs)."""
        return "open" if self.online else "connecting" if self.busy else "closed"

    # ----- session signals, handled in this tab's context -----
    def on_progress(self, message):
        self.window.tab_event(self, self.window.log, message)

    def on_connected(self, path):
        self.window.tab_event(self, self.window.on_connected, path)

    def on_listed(self, path, entries):
        self.window.tab_event(self, self.window.on_listed, path, entries)

    def on_failed(self, error):
        self.window.tab_event(self, self.window.on_session_error, error)
        if self is self.window.visible_tab:
            self.window.tab_event(self, self.window.synchronized_listing_failed, error)

    def on_host_unknown(self, error):
        self.window.tab_event(self, self.window.confirm_host, error)

    def on_disconnected(self):
        self.window.tab_event(self, self.window.on_disconnected)
        if self is self.window.visible_tab:
            self.window.tab_event(self, self.window.stop_synchronized_navigation)

    def on_operated(self):
        self.window.tab_event(self, self.window.operation_completed)

    def on_inspected(self, token, entry):
        self.tokens.discard(token)
        self.window.tab_event(self, self.window.inspected_file, token, entry)

    def on_inspect_failed(self, token):
        self.tokens.discard(token)
        self.window.tab_event(self, self.window.inspection_failed, token)

    def on_move_conflict(self, source, target):
        self.window.tab_event(self, self.window.move_conflict, source, target)

    def on_retry(self):
        self.window.tab_event(self, self.window.retry_connection)

    # ----- file panel signals (same thread: called directly) -----
    def on_requested(self, path):
        self.window.tab_event(self, self.window.request_listing, path)

    def on_dropped(self, paths, target, source_remote):
        self.window.tab_event(self, self.window.handle_drop, paths, target, source_remote, True)

    def on_context(self, point):
        self.window.tab_event(self, self.window.context_menu, self.explorer, point)

    def on_file_opened(self):
        self.window.tab_event(self, self.window.edit_remote, False)

    def on_path_changed(self, path):
        if self is self.window.visible_tab:
            self.window.follow_navigation(path, True)

    def on_thread_finished(self):
        # QThread.finished comes from the ending thread: this slot runs in the window thread.
        self.window.remote_tab_finished(self)

    def on_disconnect_clicked(self):
        self.window.tab_event(self, self.window.disconnect_or_cancel)


class RemoteTabsFeatures:
    """Mixin of MainWindow: the server tabs and the properties that follow the current tab."""
    _context_tab = None
    visible_tab = None

    # ----- the current tab -----
    @property
    def tab(self):
        return self._context_tab or self.visible_tab

    @property
    def connection(self):
        return self.tab.connection

    @connection.setter
    def connection(self, value):
        self.tab.connection = value

    @property
    def online(self):
        return self.tab.online

    @online.setter
    def online(self, value):
        self.tab.online = value

    @property
    def busy(self):
        return self.tab.busy

    @busy.setter
    def busy(self, value):
        self.tab.busy = value

    @property
    def retry_count(self):
        return self.tab.retry_count

    @retry_count.setter
    def retry_count(self, value):
        self.tab.retry_count = value

    @property
    def retry_timer(self):
        return self.tab.retry_timer

    @property
    def session(self):
        return self.tab.session

    @property
    def thread(self):
        return self.tab.thread

    @property
    def remote(self):
        return self.tab.explorer

    @property
    def disconnect_button(self):
        return self.tab.disconnect_button

    @contextmanager
    def tab_context(self, tab):
        """Point the current-tab properties at ``tab`` for the duration of the block,
        without changing the tab on screen."""
        previous = self._context_tab
        self._context_tab = tab
        try:
            yield
        finally:
            self._context_tab = previous

    def tab_event(self, tab, handler, *args):
        """Run a window handler for a signal of ``tab`` (session or file panel)."""
        if tab.removed:
            return  # A closed tab only finishes shutting its session down.
        with self.tab_context(tab):
            handler(*args)
        if not tab.removed:
            self.update_remote_tab(tab)

    def is_visible_context(self):
        return self.tab is self.visible_tab

    # ----- building -----
    def build_remote_tabs(self):
        """The server panel: a tab bar (shown once a server is open) above one file panel per tab."""
        self.remote_tabs = []
        self.closing_tabs = []  # Closed tabs whose session thread is still finishing.
        self.remote_area = QWidget()
        layout = QVBoxLayout(self.remote_area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.remote_tab_bar = QTabBar()
        self.remote_tab_bar.setObjectName("remoteTabs")
        self.remote_tab_bar.setDocumentMode(True)
        self.remote_tab_bar.setExpanding(False)
        self.remote_tab_bar.setDrawBase(False)
        self.remote_tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.remote_tab_bar.currentChanged.connect(self.remote_tab_changed)
        layout.addWidget(self.remote_tab_bar)
        self.remote_stack = QStackedWidget()
        layout.addWidget(self.remote_stack, 1)
        self.visible_tab = self.add_remote_tab()
        self.connect_requested.connect(self.route_connect)
        self.list_requested.connect(self.route_list)
        self.disconnect_requested.connect(self.route_disconnect)
        self.operation_requested.connect(self.route_operation)
        self.inspect_requested.connect(self.route_inspect)
        self.command_requested.connect(self.route_command)
        return self.remote_area

    # The window's request signals go to the session of the current tab.
    def route_connect(self, connection):
        self.tab.connect_requested.emit(connection)

    def route_list(self, path):
        self.tab.list_requested.emit(path)

    def route_disconnect(self):
        self.tab.disconnect_requested.emit()

    def route_operation(self, operation, arguments):
        self.tab.operation_requested.emit(operation, arguments)

    def route_inspect(self, token, path, identity):
        self.tab.tokens.add(token)
        self.tab.inspect_requested.emit(token, path, identity)

    def route_command(self, command):
        self.tab.command_requested.emit(command)

    def add_remote_tab(self):
        from smftp.ui.icons import icon_button, make_icon
        tab = RemoteTab(self)
        explorer = tab.explorer
        explorer.requested.connect(tab.on_requested)
        explorer.dropped.connect(tab.on_dropped)
        explorer.context_requested.connect(tab.on_context)
        explorer.file_opened.connect(tab.on_file_opened)
        explorer.path_changed.connect(tab.on_path_changed)
        explorer.view.clicked.connect(lambda index, p=explorer: setattr(self, "last_panel", p))
        explorer.icons.clicked.connect(lambda index, p=explorer: setattr(self, "last_panel", p))
        tab.disconnect_button.clicked.connect(tab.on_disconnect_clicked)
        tab.thread.finished.connect(self.finish_close)
        tab.thread.finished.connect(tab.on_thread_finished)
        if self.visible_tab is not None:
            # Same display options and column widths as the panel already on screen.
            current = self.visible_tab.explorer
            explorer.show_hidden(current.hidden)
            explorer.set_icon_mode(current.stack.currentWidget() is current.icons)
            if current.columns_fitted:
                explorer.view.header().restoreState(current.header_state())
                explorer.columns_fitted = True
        self.remote_tabs.append(tab)
        self.remote_stack.addWidget(explorer)
        index = self.remote_tab_bar.addTab(tab.title())
        close = icon_button("close", self.tr("Fermer cet onglet (déconnecte ce serveur)"),
                            lambda checked=False, t=tab: self.close_remote_tab(t))
        close.setFixedSize(20, 20)
        close.setIconSize(QSize(12, 12))
        close.setIcon(make_icon("close", getattr(self, "dark", True)))
        self.remote_tab_bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        tab.thread.start()
        self.update_remote_tab(tab)
        return tab

    # ----- showing -----
    def show_remote_tab(self, tab):
        if tab in self.remote_tabs:
            self.remote_tab_bar.setCurrentIndex(self.remote_tabs.index(tab))

    def remote_tab_changed(self, index):
        if not 0 <= index < len(getattr(self, "remote_tabs", [])):
            return
        tab = self.remote_tabs[index]
        previous = self.visible_tab
        self.visible_tab = tab
        self.remote_stack.setCurrentWidget(tab.explorer)
        if previous is tab:
            return
        if previous is not None and getattr(self, "last_panel", None) is previous.explorer:
            self.last_panel = tab.explorer
        button = getattr(self, "sync_navigation_button", None)
        if button is not None:
            # Synchronized browsing pairs two folders: it does not follow another server.
            self.stop_synchronized_navigation()
            row = tab.explorer.title_row
            row.insertWidget(row.indexOf(tab.disconnect_button), button)
        self.show_remote_state()

    def show_remote_state(self):
        """Title, status bar, menus and server list for the tab on screen."""
        from smftp.models.entries import protocol_label
        tab = self.visible_tab
        connection = tab.connection
        if tab.online and connection:
            self.setWindowTitle(f"SMFTP — {connection.name or connection.host}")
        else:
            self.setWindowTitle(self.base_title)
        if not hasattr(self, "terminal"):
            return
        if self.center.currentWidget() is self.files_view:
            if tab.online and connection:
                self.statusBar().showMessage(self.tr("●  Connecté    •    {host}    •    {protocol}").format(
                    host=connection.host, protocol=protocol_label(connection.protocol)))
            elif tab.connecting():
                self.statusBar().showMessage(self.tr("Connexion en cours…"))
            else:
                self.statusBar().showMessage(self.tr("●  Déconnecté"))
        self.update_connect_button()
        self.update_server_actions()
        if hasattr(self, "bookmarks_menu"):
            self.update_bookmarks_menu()
        self.update_site_states()

    def update_connect_button(self):
        """« Nouvelle connexion » waits only while the server on screen is being reached."""
        self.connect_button.setEnabled(not self.visible_tab.connecting())

    def update_server_actions(self):
        action = getattr(self, "ftp_command_action", None)
        if action is not None:
            tab = self.visible_tab
            action.setEnabled(bool(tab.online and tab.connection
                                   and tab.connection.protocol in ("FTP", "FTPS", "FTPSI")))

    def update_remote_tab(self, tab):
        """Tab text, state dot and tooltip; the bar is hidden while only one empty tab exists."""
        if tab not in getattr(self, "remote_tabs", []):
            return
        from smftp.ui.terminal import state_icon
        from smftp.models.entries import protocol_label
        index = self.remote_tabs.index(tab)
        bar = self.remote_tab_bar
        bar.setTabText(index, tab.title())
        bar.setTabIcon(index, state_icon(tab.state()))
        states = {"open": self.tr("connecté"), "connecting": self.tr("connexion…"), "closed": self.tr("déconnecté")}
        if tab.connection is not None:
            c = tab.connection
            bar.setTabToolTip(index, f"{c.user}@{c.host}:{c.port} — {protocol_label(c.protocol)}\n"
                                     f"{states[tab.state()]}")
        else:
            bar.setTabToolTip(index, states["closed"])
        bar.setVisible(len(self.remote_tabs) > 1 or self.remote_tabs[0].connection is not None)
        if tab is self.visible_tab and hasattr(self, "connect_button"):
            self.update_connect_button()
            self.update_server_actions()

    # ----- finding and opening -----
    def find_remote_tab(self, identity, online=False):
        """The tab showing this server (the current one first), or None."""
        candidates = [self.tab] + [t for t in self.remote_tabs if t is not self.tab]
        for tab in candidates:
            if tab.connection is not None and tab.connection.identity == identity and (tab.online or not online):
                return tab
        return None

    def open_connection(self, connection):
        """Connect in the tab of this server, the empty tab on screen, or a new tab."""
        if self.closing:
            return False
        existing = self.find_remote_tab(connection.identity)
        if existing is not None and existing.connecting():
            self.show_remote_tab(existing)
            self.set_view("files")
            self.statusBar().showMessage(self.tr("Une connexion est déjà en cours : attendez ou cliquez sur « Annuler »."), 5000)
            return False
        target = existing
        if target is None and self.visible_tab.free():
            target = self.visible_tab
        if target is None:
            if len(self.remote_tabs) >= MAX_TABS:
                self.set_view("files")
                self.statusBar().showMessage(self.tr("Limite de {count} serveurs ouverts atteinte : fermez un onglet "
                                                     "pour en ouvrir un autre.").format(count=MAX_TABS), 8000)
                return False
            target = self.add_remote_tab()
        self.show_remote_tab(target)
        self.retry_timer.stop()
        self.retry_count = 0
        self.connection = connection
        self.start_connection()
        return True

    # ----- closing -----
    def confirm_disconnect(self, tab):
        """Same question as « Déconnecter » when transfers to this server are running.
        Returns False when the user cancels."""
        from PySide6.QtWidgets import QMessageBox
        running = self.transfers.pending_count(tab.connection.identity if tab.connection else None)
        if not running:
            return True
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
            return False
        else:
            self.log(self.tr("Navigation déconnectée ; %n transfert(s) continuent.", "", running))
        return True

    def close_remote_tab(self, tab):
        """Close a server tab: its session is disconnected, the others are untouched.
        The last tab stays, empty, ready for the next connection."""
        if self.closing or tab.removed or tab not in self.remote_tabs:
            return
        if (tab.online or tab.busy) and not self.confirm_disconnect(tab):
            return
        tab.retry_timer.stop()
        tab.retry_count = 0
        if len(self.remote_tabs) == 1:
            if tab.connecting():
                tab.session.abort_connect()
            tab.disconnect_requested.emit()
            return
        if tab.connecting():
            tab.session.abort_connect()
        for token in tab.tokens:
            record = self.pending_saves.pop(token, None)
            if record:
                record.pop("save_again", None)
        tab.tokens.clear()
        tab.removed = True
        index = self.remote_tabs.index(tab)
        self.remote_tabs.remove(tab)
        self.closing_tabs.append(tab)
        if tab is self.visible_tab:
            self.visible_tab = None
        self.remote_tab_bar.removeTab(index)  # Shows a neighbour tab.
        self.remote_stack.removeWidget(tab.explorer)
        tab.explorer.hide()
        if self.visible_tab is None:
            self.remote_tab_changed(self.remote_tab_bar.currentIndex())
        if getattr(self, "last_panel", None) is tab.explorer:
            self.last_panel = self.visible_tab.explorer
        if self._context_tab is tab:
            self._context_tab = None
        tab.session.disconnected.connect(tab.thread.quit)
        tab.disconnect_requested.emit()
        if tab.connection is not None:
            self.log(self.tr("Onglet fermé : {server}.").format(server=tab.title()))
        self.update_remote_tab(self.remote_tabs[0])
        self.show_remote_state()

    def remote_tab_finished(self, tab):
        """The session thread of a closed tab has ended: free its panel."""
        if tab in self.closing_tabs and not self.closing:
            self.closing_tabs.remove(tab)
            tab.explorer.deleteLater()
            tab.deleteLater()

    def all_remote_tabs(self):
        return list(self.remote_tabs) + list(self.closing_tabs)

    def show_remote_hidden(self, enabled):
        for tab in self.remote_tabs:
            tab.explorer.show_hidden(enabled)
