"""Several servers open at once, one tab each (smftp/ui/remote_tabs.py)."""
from threading import Event, Thread
import time
import pytest
from PySide6.QtWidgets import QApplication
from smftp.models.entries import Connection
from smftp.ui.main_window import MainWindow
from smftp.ui.remote_tabs import MAX_TABS


def app():
    return QApplication.instance() or QApplication([])


def wait_for(condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.instance().processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def make_server(root):
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    auth = DummyAuthorizer()
    auth.add_user("test", "secret", str(root), perm="elradfmwMT")

    class Handler(FTPHandler):
        pass
    Handler.authorizer = auth
    return FTPServer(("127.0.0.1", 0), Handler)


@pytest.fixture
def two_servers(tmp_path):
    """Two FTP servers, each with its own file: alpha.txt on the first, beta.txt on the second.
    pyftpdlib shares one event loop per process: a single thread serves both."""
    roots, servers = [], []
    for name in ("alpha", "beta"):
        root = tmp_path / name
        root.mkdir()
        (root / f"{name}.txt").write_text(name, encoding="utf-8")
        roots.append(root)
        servers.append(make_server(root))
    ports = [server.socket.getsockname()[1] for server in servers]
    stop = Event()

    def serve():
        # Closed from its own thread: closing from another one races with select().
        while not stop.is_set():
            servers[0].serve_forever(timeout=0.05, blocking=False)
        servers[0].close_all()  # Closes the shared loop, so both servers.
    thread = Thread(target=serve, daemon=True)
    thread.start()
    yield list(zip(ports, roots))
    stop.set()
    thread.join(timeout=2)


def connection(port, name):
    return Connection("127.0.0.1", "test", "FTP", port, "secret", name=name)


def names(explorer):
    return {entry.name for entry in explorer.last_entries}


def close_all(window):
    """Close the window and wait for every server session thread."""
    tabs = window.all_remote_tabs()
    window.close()
    stopped = wait_for(lambda: not any(tab.thread.isRunning() for tab in tabs), 6)
    from PySide6.QtCore import QCoreApplication, QEvent
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return len(tabs), stopped


@pytest.fixture
def window():
    app()
    win = MainWindow()
    yield win
    if not win.closing:
        close_all(win)


def open_both(window, two_servers):
    (port_a, _), (port_b, _) = two_servers
    assert window.open_connection(connection(port_a, "Alpha"))
    first = window.visible_tab
    assert wait_for(lambda: first.online and "alpha.txt" in names(first.explorer))
    assert window.open_connection(connection(port_b, "Beta"))
    second = window.visible_tab
    assert second is not first
    assert wait_for(lambda: second.online and "beta.txt" in names(second.explorer))
    return first, second


def test_two_servers_in_two_tabs_with_independent_listings(window, two_servers):
    assert window.remote_tab_bar.isHidden()  # One empty tab: no bar, as before.
    first, second = open_both(window, two_servers)
    assert window.remote_tabs == [first, second] and window.remote_tab_bar.count() == 2
    assert not window.remote_tab_bar.isHidden()
    assert [window.remote_tab_bar.tabText(i) for i in range(2)] == ["Alpha", "Beta"]
    assert names(first.explorer) == {"alpha.txt"} and names(second.explorer) == {"beta.txt"}
    # The window's names follow the tab on screen.
    assert window.remote is second.explorer and window.connection.name == "Beta"
    assert window.session is second.session and window.thread is second.thread
    assert first.thread is not second.thread and first.thread.isRunning() and second.thread.isRunning()
    assert window.windowTitle() == "SMFTP — Beta" and window.ftp_command_action.isEnabled()
    assert window.disconnect_button is second.disconnect_button and window.disconnect_button.parent() is window.remote
    window.show_remote_tab(first)
    assert window.remote is first.explorer and window.windowTitle() == "SMFTP — Alpha"
    assert window.remote_stack.currentWidget() is first.explorer
    assert "Connecté" in window.statusBar().currentMessage()


def test_a_listing_for_the_background_tab_leaves_the_panel_on_screen_alone(window, two_servers):
    first, second = open_both(window, two_servers)
    (_, root_a), _ = two_servers
    (root_a / "nouveau.txt").write_text("x", encoding="utf-8")
    shown_before = names(window.remote)
    first.list_requested.emit("/")  # Refresh of the first server, its tab in the background.
    assert wait_for(lambda: "nouveau.txt" in names(first.explorer))
    assert window.visible_tab is second and window.remote is second.explorer
    assert names(second.explorer) == shown_before == {"beta.txt"}
    assert window.windowTitle() == "SMFTP — Beta"
    assert not first.busy and not second.busy
    # Several servers open: the log says which one a line is about.
    assert "[Alpha] " in window.logs.toPlainText() and "[Beta] " in window.logs.toPlainText()


def test_closing_a_tab_disconnects_only_that_server(window, two_servers):
    first, second = open_both(window, two_servers)
    window.close_remote_tab(first)
    assert window.remote_tabs == [second] and window.visible_tab is second
    assert wait_for(lambda: not first.thread.isRunning())
    assert second.online and second.thread.isRunning()
    second.list_requested.emit("/")
    assert wait_for(lambda: not second.busy) and names(second.explorer) == {"beta.txt"}
    # « Déconnecter » on the last tab: disconnected, the empty tab stays.
    window.disconnect_session()
    assert wait_for(lambda: not second.online)
    assert window.remote_tabs == [second] and second.thread.isRunning()
    assert window.windowTitle() == window.base_title


def test_closing_the_visible_tab_shows_its_neighbour(window, two_servers):
    first, second = open_both(window, two_servers)
    window.last_panel = second.explorer
    window.close_remote_tab(second)
    assert window.visible_tab is first and window.remote is first.explorer
    assert window.last_panel is first.explorer
    assert window.windowTitle() == "SMFTP — Alpha"
    assert window.sync_navigation_button.parent() is first.explorer


def test_double_click_on_an_open_profile_shows_its_tab(window, two_servers, monkeypatch):
    from smftp.utils.profiles import profile_for
    (port_a, _), (port_b, _) = two_servers
    window.sites = [profile_for(connection(port_a, "Alpha")), profile_for(connection(port_b, "Beta"))]
    window.refresh_sites()
    monkeypatch.setattr(window, "stored_secret", lambda connection: "secret")
    window.connect_profile(window.sites[0])
    first = window.visible_tab
    assert wait_for(lambda: first.online and not first.busy)
    window.connect_profile(window.sites[1])  # Another profile: a new tab.
    second = window.visible_tab
    assert second is not first and len(window.remote_tabs) == 2
    assert wait_for(lambda: second.online and not second.busy)
    assert all(window.site_list.item(row).font().bold() for row in range(2))  # Both servers open.
    started = []
    monkeypatch.setattr(window, "start_connection", lambda: started.append(True))
    window.connect_profile(window.sites[0])  # Already open: its tab comes back, no new connection.
    assert window.visible_tab is first and len(window.remote_tabs) == 2 and not started
    assert window.site_list.item(0).isSelected() and not window.site_list.item(1).isSelected()


def test_switching_tabs_stops_synchronized_browsing(window, two_servers):
    first, second = open_both(window, two_servers)
    window.sync_navigation_button.click()
    assert window.sync_navigation_action.isChecked() and window.sync_base is not None
    window.show_remote_tab(first)
    assert not window.sync_navigation_action.isChecked() and window.sync_base is None
    assert window.sync_navigation_button.parent() is first.explorer


def test_the_number_of_tabs_is_limited(window, monkeypatch):
    sent = []
    monkeypatch.setattr(window, "connect_requested", type("S", (), {"emit": lambda self, c: sent.append(c)})())
    for number in range(MAX_TABS):
        assert window.open_connection(Connection(f"h{number}.test", "u", "SFTP"))
    assert len(window.remote_tabs) == MAX_TABS and len(sent) == MAX_TABS
    assert not window.open_connection(Connection("de-trop.test", "u", "SFTP"))
    assert len(window.remote_tabs) == MAX_TABS and "Limite de 8" in window.statusBar().currentMessage()


def test_closing_the_window_stops_every_session_thread(two_servers):
    app()
    window = MainWindow()
    first, second = open_both(window, two_servers)
    assert first.thread.isRunning() and second.thread.isRunning()
    assert close_all(window) == (2, True)


def test_closing_a_tab_asks_only_about_the_transfers_of_its_server(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from smftp.workers.transfer import Transfer
    monkeypatch.setattr(window, "connect_requested", type("S", (), {"emit": lambda self, c: None})())
    first_connection, second_connection = Connection("a.test", "u", "SFTP"), Connection("b.test", "u", "SFTP")
    window.open_connection(first_connection)
    first = window.visible_tab
    window.open_connection(second_connection)
    second = window.visible_tab
    for tab in (first, second):
        tab.online, tab.busy = True, False
    window.transfers.pause_all()
    window.transfers.add(Transfer(first_connection, str(tmp_path / "f"), "/f", True))
    window.transfers.records[0]["state"] = "pending"
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: asked.append(box.text()) or 0)
    try:
        window.close_remote_tab(first)  # No answer chosen: the tab stays open.
        assert asked and "1 transfert" in asked[0] and first in window.remote_tabs
        asked.clear()
        window.close_remote_tab(second)  # No transfer to that server: no question.
        assert not asked and window.remote_tabs == [first]
    finally:
        window.transfers.records[0]["state"] = "paused_pending"
