"""SSH terminal: protocol model, real loopback shell, screen and keyboard, window integration."""
import socket
import threading
import time
import paramiko
import pytest
from PySide6.QtCore import Qt, QEvent, QObject, Signal
from PySide6.QtGui import QKeyEvent, QGuiApplication
from PySide6.QtWidgets import QApplication
from smftp.models.entries import Connection, is_terminal, uses_ssh, DEFAULT_PORTS, protocol_label
from smftp.ui.terminal import MARGIN


def app():
    return QApplication.instance() or QApplication([])


def wait_for(condition, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


# ----- model and profiles -----
def test_ssh_protocol_model_and_profiles():
    from smftp.utils.profiles import normalize_profile
    assert DEFAULT_PORTS["SSH"] == 22
    assert is_terminal("SSH") and not is_terminal("SFTP")
    assert uses_ssh("SSH") and uses_ssh("SFTP") and not uses_ssh("FTP")
    assert protocol_label("SSH") == "SSH (terminal)"
    profile = normalize_profile({"protocol": "SSH", "host": "vps.example", "user": "root"})
    assert profile["port"] == 22 and profile["protocol"] == "SSH"
    # Same server, different use: an SSH terminal profile and an SFTP profile can coexist.
    sftp = Connection("vps.example", "root", "SFTP", 22)
    ssh = Connection("vps.example", "root", "SSH", 22)
    assert sftp.identity != ssh.identity


def test_connection_dialog_ssh_profile(tmp_path):
    app()
    from smftp.ui.connection_dialog import ConnectionDialog, parse_address
    assert parse_address("ssh://root@vps.example:2222")["protocol"] == "SSH"
    key = tmp_path / "id_ed25519"
    key.write_text("key")
    dialog = ConnectionDialog(None, None, sites=[])
    dialog.set_protocol("SSH")
    assert dialog.port.value() == 22
    assert dialog.key.isEnabled() and not dialog.local_directory.isEnabled()
    assert not dialog.transfer_mode.isEnabled() and not dialog.encoding.isEnabled()
    assert "terminal" in dialog.ok_button.text()
    dialog.host.setText("vps.example")
    dialog.user.setText("root")
    dialog.key.setText(str(key))
    connection = dialog.connection()
    assert connection.protocol == "SSH" and connection.key_file == str(key)
    assert not dialog.problems()
    dialog.set_protocol("FTP")
    assert dialog.ok_button.text() == "Enregistrer et connecter"


# ----- real loopback SSH server with an interactive shell -----
@pytest.fixture
def shell_server():
    host_key = paramiko.RSAKey.generate(2048)
    state = {"pty": None, "resized": None, "received": b""}

    class Server(paramiko.ServerInterface):
        def __init__(self):
            self.shell = threading.Event()

        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ("test", "secret") else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
            state["pty"] = (term.decode() if isinstance(term, bytes) else term, width, height)
            return True

        def check_channel_shell_request(self, channel):
            self.shell.set()
            return True

        def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
            state["resized"] = (width, height)
            return True

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    port = listener.getsockname()[1]
    transports = []

    def serve():
        while True:
            try:
                sock, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(sock,), daemon=True).start()

    def handle(sock):
        # One thread per client: a client that gives up (unknown key) must not stop the server.
        transport = paramiko.Transport(sock)
        transport.add_server_key(host_key)
        transports.append(transport)
        server = Server()
        try:
            transport.start_server(server=server)
        except (paramiko.SSHException, EOFError, OSError):
            return
        run_shell(transport, server)

    def run_shell(transport, server):
        channel = transport.accept(10)
        if channel is None or not server.shell.wait(10):
            return
        channel.sendall(b"\x1b[1;32mBienvenue\x1b[0m\r\nuser@test:~$ ")
        while True:
            data = channel.recv(1024)
            if not data:
                break
            state["received"] += data
            if b"exit\r" in state["received"]:
                channel.sendall(b"\r\nlogout\r\n")
                channel.close()
                break
            channel.sendall(data.replace(b"\r", b"\r\n"))

    threading.Thread(target=serve, daemon=True).start()
    hosts = paramiko.HostKeys()
    hosts.add(f"[127.0.0.1]:{port}", host_key.get_name(), host_key)
    yield port, hosts, state
    listener.close()
    for transport in transports:
        transport.close()


def trusted(tmp_path, monkeypatch, hosts):
    monkeypatch.setenv("SMFTP_DATA_DIR", str(tmp_path / "data"))
    from smftp.utils.storage import data_dir
    data_dir().mkdir(parents=True, exist_ok=True)
    hosts.save(str(data_dir() / "known_hosts"))


def test_real_shell_session(shell_server, tmp_path, monkeypatch):
    app()
    port, hosts, state = shell_server
    trusted(tmp_path, monkeypatch, hosts)
    from smftp.workers.terminal import ShellWorker
    worker = ShellWorker(Connection("127.0.0.1", "test", "SSH", port, "secret", remote_directory="/var/www"), 100, 30)
    received, events = [], []
    worker.output.connect(received.append)
    worker.opened.connect(lambda: events.append("opened"))
    worker.closed.connect(lambda: events.append("closed"))
    worker.failed.connect(lambda info: events.append(info.summary))
    worker.start()
    assert wait_for(lambda: "opened" in events), events
    assert state["pty"] == ("xterm-256color", 100, 30)
    assert wait_for(lambda: b"Bienvenue" in b"".join(received))
    # The profile's remote folder is entered once the shell is open.
    assert wait_for(lambda: b"cd /var/www\n" in state["received"])
    worker.resize(120, 40)
    assert wait_for(lambda: state["resized"] == (120, 40))
    worker.send(b"ls\r")
    assert wait_for(lambda: b"ls\r\n" in b"".join(received))
    worker.send(b"exit\r")
    assert wait_for(lambda: "closed" in events)
    worker.wait(5000)
    assert not [e for e in events if e not in ("opened", "closed")]


def test_real_shell_wrong_password_and_unknown_host(shell_server, tmp_path, monkeypatch):
    app()
    port, hosts, _ = shell_server
    from smftp.workers.terminal import ShellWorker
    monkeypatch.setenv("SMFTP_DATA_DIR", str(tmp_path / "empty"))
    unknown = []
    worker = ShellWorker(Connection("127.0.0.1", "test", "SSH", port, "secret"))
    worker.host_unknown.connect(unknown.append)
    worker.start()
    assert wait_for(lambda: unknown)
    assert unknown[0].fingerprint.startswith("SHA256:")
    worker.wait(5000)
    trusted(tmp_path, monkeypatch, hosts)
    failures = []
    worker = ShellWorker(Connection("127.0.0.1", "test", "SSH", port, "wrong"))
    worker.failed.connect(failures.append)
    worker.start()
    assert wait_for(lambda: failures)
    assert "wrong" not in failures[0].summary + failures[0].details
    worker.wait(5000)


def test_probe_reports_ssh_success(shell_server, tmp_path, monkeypatch):
    app()
    port, hosts, _ = shell_server
    trusted(tmp_path, monkeypatch, hosts)
    from smftp.workers.probe import ConnectionProbe
    probe = ConnectionProbe(Connection("127.0.0.1", "test", "SSH", port, "secret"))
    results = []
    probe.succeeded.connect(results.append)
    probe.failed.connect(lambda info: results.append(info.summary))
    probe.start()
    assert wait_for(lambda: results)
    probe.wait(5000)
    assert results[0].startswith("Connexion SSH réussie")


# ----- screen, keyboard, clipboard -----
def key(view, key, text="", modifiers=Qt.KeyboardModifier.NoModifier):
    sent = []
    view.typed.connect(sent.append)
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text))
    view.typed.disconnect()
    return b"".join(sent)


def test_view_renders_colors_and_keys():
    app()
    from smftp.ui.terminal import TerminalView
    view = TerminalView()
    view.resize(800, 400)
    view.feed("\x1b[31mrouge\x1b[0m é\r\n".encode("utf-8"))
    assert view.line_text(view.screen.buffer[0]) == "rouge é"
    assert view.screen.buffer[0][0].fg == "red"
    assert view.color("red", True).name() != view.color("default", True).name()
    assert key(view, Qt.Key.Key_Up) == b"\x1b[A"
    view.feed(b"\x1b[?1h")  # Application cursor keys (vim, less).
    assert key(view, Qt.Key.Key_Up) == b"\x1bOA"
    assert key(view, Qt.Key.Key_C, "\x03", Qt.KeyboardModifier.ControlModifier) == b"\x03"
    assert key(view, Qt.Key.Key_Return, "\r") == b"\r"
    assert key(view, Qt.Key.Key_Backspace, "\x08") == b"\x7f"
    assert key(view, Qt.Key.Key_Tab, "\t") == b"\t"
    assert key(view, Qt.Key.Key_F5) == b"\x1b[15~"
    assert key(view, Qt.Key.Key_E, "é") == "é".encode("utf-8")
    assert key(view, Qt.Key.Key_B, "b", Qt.KeyboardModifier.AltModifier) == b"\x1bb"
    view.grab()  # Paints without error.


def test_view_window_shortcuts_go_to_the_shell():
    app()
    from smftp.ui.terminal import TerminalView
    view = TerminalView()
    for code, modifiers in [(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier), (Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier),
                            (Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)]:
        event = QKeyEvent(QEvent.Type.ShortcutOverride, code, modifiers)
        view.event(event)
        assert event.isAccepted()
    event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_I,
                      Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    event.ignore()
    view.event(event)
    assert not event.isAccepted()  # Ctrl+Shift+I still toggles the assistant.


def test_view_resize_scrollback_selection_and_paste():
    app()
    from smftp.ui.terminal import TerminalView
    view = TerminalView()
    sizes = []
    view.resized.connect(lambda c, l: sizes.append((c, l)))
    view.resize(view.cell_width * 50 + 2 * MARGIN, view.cell_height * 10 + 2 * MARGIN)
    view.show()
    assert wait_for(lambda: sizes and sizes[-1] == (50, 10))
    view.feed(b"".join(f"ligne {i}\r\n".encode() for i in range(40)))
    assert len(view.screen.history.top) > 0
    view.scroll_by(5)
    rows, first = view.visible_lines()
    assert view.scroll == 5 and first == len(view.screen.history.top) - 5
    sent = []
    view.typed.connect(sent.append)
    view.send_input(b"x")
    assert view.scroll == 0
    view.selection = ((first, 0), (first, 7))
    view.scroll = 5
    assert view.selected_text().startswith("ligne")
    view.copy()
    assert QGuiApplication.clipboard().text().startswith("ligne")
    QGuiApplication.clipboard().setText("un\ndeux")
    sent.clear()
    view.paste()
    assert sent == [b"un\rdeux"]
    view.feed(b"\x1b[?2004h")  # Bracketed paste: the shell does not run pasted lines one by one.
    sent.clear()
    view.paste()
    assert sent == [b"\x1b[200~un\rdeux\x1b[201~"]
    view.close()


def test_screen_answers_device_queries():
    app()
    from smftp.ui.terminal import TerminalView
    view = TerminalView()
    sent = []
    view.typed.connect(sent.append)
    view.feed(b"\x1b[6n")  # Cursor position report, used by some prompts.
    assert sent and sent[0].startswith(b"\x1b[") and sent[0].endswith(b"R")


# ----- window integration -----
class FakeWorker(QObject):
    status = Signal(str)
    opened = Signal()
    output = Signal(bytes)
    failed = Signal(object)
    host_unknown = Signal(object)
    closed = Signal()
    finished = Signal()
    created = []

    def __init__(self, connection, columns=80, lines=24):
        super().__init__()
        self.connection = connection
        self.sent = []
        self.alive = False
        FakeWorker.created.append(self)

    def start(self):
        self.alive = True
        self.opened.emit()
        self.output.emit(b"root@vps:~# ")

    def isRunning(self):
        return False

    def send(self, data):
        self.sent.append(data)

    def resize(self, columns, lines):
        pass

    def stop(self):
        self.alive = False


@pytest.fixture
def window(monkeypatch):
    app()
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda service, key: "secret")
    import smftp.ui.terminal as terminal
    monkeypatch.setattr(terminal.TerminalArea, "__init__", wrap_factory(terminal.TerminalArea.__init__))
    from smftp.ui.main_window import MainWindow
    FakeWorker.created.clear()
    window = MainWindow()
    window.sites = [{"name": "Bengadok", "protocol": "SSH", "host": "vps.example", "user": "root", "port": 22},
                    {"name": "Site", "protocol": "SFTP", "host": "files.example", "user": "web", "port": 22},
                    {"name": "Vieux FTP", "protocol": "FTP", "host": "ftp.example", "user": "web", "port": 21}]
    window.refresh_sites()
    yield window
    window.closing = True
    window.terminal.close_all()
    window.thread.quit()
    window.thread.wait(2000)


def wrap_factory(original):
    def init(self, dark=True, worker_factory=None, parent=None):
        original(self, dark, FakeWorker, parent)
    return init


def test_ssh_profile_opens_terminal_view(window):
    assert window.site_list.item(0).text() == "Bengadok"
    assert not window.site_list.item(0).font().bold()
    assert window.center.currentWidget() is window.files_view
    window.connect_profile(window.sites[0])
    assert window.center.currentWidget() is window.terminal
    session = window.terminal.current()
    assert session.connection.password == "secret" and session.state == "open"
    assert any("root@vps:~#" in line for line in session.view.screen.display)
    session.view.send_input(b"ls\r")
    assert FakeWorker.created[-1].sent == [b"ls\r"]
    # Double-clicking again brings back the same session instead of opening a second one.
    window.connect_profile(window.sites[0])
    assert window.terminal.tabs.count() == 1
    window.new_terminal_session()
    assert window.terminal.tabs.count() == 2
    assert window.terminal.tabs.tabText(1).endswith("Bengadok (2)")
    assert "Bengadok" in window.recent_menu.actions()[0].text()


def test_views_follow_the_server_type(window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "connect_requested", type("S", (), {"emit": lambda self, c: started.append(c)})())
    window.connect_profile(window.sites[0])
    assert window.center.currentWidget() is window.terminal
    window.connect_profile(window.sites[2])  # FTP: back to the file panels.
    assert window.center.currentWidget() is window.files_view and started[-1].protocol == "FTP"
    window.connect_profile(window.sites[0])  # Double click on the SSH server already open: back to its tab.
    assert window.terminal.tabs.count() == 1
    assert window.center.currentWidget() is window.terminal
    window.terminal.close_all()  # Last tab closed: back to the files.
    assert window.center.currentWidget() is window.files_view


def test_sftp_profile_can_open_a_terminal(window, monkeypatch):
    import smftp.ui.main_window as module
    menus = []

    class Menu:
        def __init__(self, parent=None):
            self.texts = []
            menus.append(self.texts)

        def addAction(self, text):
            self.texts.append(text)
            return type("Action", (), {"triggered": type("T", (), {"connect": lambda self, f: None})()})()

        def addSeparator(self):
            pass

        def exec(self, *args):
            pass

    monkeypatch.setattr(module, "QMenu", Menu)
    monkeypatch.setattr(window.site_list, "itemAt", lambda point: window.site_list.item(point))
    monkeypatch.setattr(window.site_list.viewport(), "mapToGlobal", lambda point: point)
    for row in range(3):
        window.site_context_menu(row)
    sftp_menu, ftp_menu, ssh_menu = menus[1], menus[2], menus[0]
    assert "Connecter" in sftp_menu and "Ouvrir un terminal" in sftp_menu
    assert "Ouvrir un terminal" not in ftp_menu and "Connecter" in ftp_menu
    assert "Ouvrir le terminal" in ssh_menu and "Connecter" not in ssh_menu
    window.open_terminal(window.sites[1], new=True)
    assert window.terminal.current().connection.protocol == "SFTP"
    assert window.center.currentWidget() is window.terminal


def test_ssh_profiles_are_not_offered_for_file_tasks(window):
    keys = window.scheduler.profiles()
    assert all(not key.startswith("SSH://") for key in keys)
    assert len(keys) == 2


def test_unknown_host_in_terminal_asks_then_retries(window, monkeypatch):
    window.connect_profile(window.sites[0])
    session = window.terminal.current()
    restarted = []
    monkeypatch.setattr(session, "start", lambda: restarted.append(True))
    monkeypatch.setattr(window, "trust_host", lambda error: True)
    window.terminal_host_unknown(object(), session)
    assert restarted
    monkeypatch.setattr(window, "trust_host", lambda error: False)
    window.terminal_host_unknown(object(), session)
    assert len(restarted) == 1


def test_theme_switch_reaches_terminal(window):
    window.connect_profile(window.sites[0])
    view = window.terminal.current().view
    before = view.colors["background"]
    window.toggle_theme()
    assert view.colors["background"] != before
    window.toggle_theme()


def test_shrinking_keeps_the_cursor_on_the_prompt():
    app()
    from smftp.ui.terminal import TerminalView
    view = TerminalView()
    view.fit(view.cell_width * 40 + 2 * MARGIN, view.cell_height * 20 + 2 * MARGIN)
    view.feed(b"".join(f"ligne {i}\r\n".encode() for i in range(10)) + b"user@host:~$ ")
    assert view.screen.cursor.y == 10
    view.fit(view.cell_width * 40 + 2 * MARGIN, view.cell_height * 5 + 2 * MARGIN)
    assert view.screen.lines == 5 and view.screen.cursor.y == 4
    assert view.line_text(view.screen.buffer[4]) == "user@host:~$"
    assert view.line_text(view.history_lines()[-1]) == "ligne 5"  # Lines above went to the history.
    view.fit(view.cell_width * 40 + 2 * MARGIN, view.cell_height * 3 + 2 * MARGIN)  # Cursor near the top: nothing moves.
    fresh = TerminalView()
    fresh.fit(fresh.cell_width * 40 + 2 * MARGIN, fresh.cell_height * 20 + 2 * MARGIN)
    fresh.feed(b"$ ")
    fresh.fit(fresh.cell_width * 40 + 2 * MARGIN, fresh.cell_height * 10 + 2 * MARGIN)
    assert fresh.screen.cursor.y == 0 and fresh.line_text(fresh.screen.buffer[0]) == "$"


def test_transient_layout_sizes_do_not_cut_lines():
    app()
    from smftp.ui.terminal import TerminalView
    view = TerminalView()
    view.resize(view.cell_width * 100 + 2 * MARGIN, view.cell_height * 20 + 2 * MARGIN)
    view.show()
    assert wait_for(lambda: view.screen.columns == 100)
    view.feed(b"x" * 90 + b"\r\n")
    view.resize(200, 120)  # A layout pass at the minimum size, immediately corrected.
    view.resize(view.cell_width * 100 + 2 * MARGIN, view.cell_height * 20 + 2 * MARGIN)
    wait_for(lambda: False, 0.2)
    assert view.screen.columns == 100
    assert view.line_text(view.screen.buffer[0]) == "x" * 90
    view.close()


def test_trusting_an_unknown_key_reconnects_for_real(shell_server, tmp_path, monkeypatch):
    app()
    port, hosts, state = shell_server
    monkeypatch.setenv("SMFTP_DATA_DIR", str(tmp_path / "fresh"))
    from smftp.ui.terminal import TerminalSession
    import smftp.core.clients as clients
    session = TerminalSession({}, Connection("127.0.0.1", "test", "SSH", port, "secret"))
    asked = []

    def trust(error, s):
        asked.append(error)
        clients.trust_host_key(error, str(tmp_path / "fresh" / "known_hosts"))
        s.start()  # As the window does once the fingerprint is accepted, possibly before the old thread ends.

    session.host_unknown.connect(trust)
    session.start()
    assert wait_for(lambda: session.state == "open"), session.state
    assert len(asked) == 1
    assert wait_for(lambda: any("Bienvenue" in line for line in session.view.screen.display))
    session.stop()
    session.worker.wait(5000)


def test_sidebar_marks_open_servers(window):
    window.connect_profile(window.sites[0])
    item = window.site_list.item(0)
    assert item.font().bold() and item.isSelected()
    assert "1 session" in item.toolTip()
    assert not window.site_list.item(1).font().bold()
    window.set_view("files")
    assert not item.isSelected() and item.font().bold()  # Still open, not on screen.
    window.terminal.close_all()
    assert not window.site_list.item(0).font().bold()


def test_file_commands_are_off_in_the_terminal_view(window):
    window.connect_profile(window.sites[0])
    assert not window.edit_menu.menuAction().isEnabled()
    assert not any(a.isEnabled() for a in window.transfers_menu.actions())
    assert not any(a.isEnabled() for a in window.file_shortcuts)  # Delete, F2, F5…
    assert not window.speed_label.isVisible()
    window.set_view("files")
    assert window.edit_menu.menuAction().isEnabled()
    assert all(a.isEnabled() for a in window.file_shortcuts)


def test_double_click_on_the_connected_file_server_does_not_reconnect(window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "start_connection", lambda: started.append(True))
    window.online = True
    window.connection = Connection("files.example", "web", "SFTP", 22)
    window.connect_profile(window.sites[0])
    window.connect_profile(window.sites[1])
    assert window.center.currentWidget() is window.files_view and not started
    window.online = False


def test_connection_steps_and_state_dot_in_the_tab():
    app()
    from smftp.ui.terminal import TerminalArea, state_icon

    class Worker(FakeWorker):
        def start(self):
            self.status.emit("Statut : connexion TCP établie ; négociation SSH en cours…")
            self.opened.emit()
            self.status.emit("Statut : après ouverture, rien n’est ajouté.")

    area = TerminalArea(worker_factory=Worker)
    area.resize(1600, 600)  # Wide enough for the longest step on one line.
    session = area.open({}, Connection("vps.example", "root", "SSH", 22, name="VPS"))
    lines = [line.rstrip() for line in session.view.screen.display if line.strip()]
    assert lines[0].startswith("Connexion SSH à root@vps.example:22")
    assert lines[1] == "Connexion TCP établie ; négociation SSH en cours…"
    assert not any("après ouverture" in line for line in lines)
    assert "connecté" in area.tabs.tabToolTip(0)
    assert not area.tabs.tabIcon(0).isNull() and not state_icon("closed").isNull()
    area.close_all()


def test_select_copies_and_right_click_pastes():
    app()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from smftp.ui.terminal import TerminalView, MARGIN
    view = TerminalView()
    view.fit(view.cell_width * 40 + 2 * MARGIN, view.cell_height * 5 + 2 * MARGIN)
    view.feed(b"bonjour le monde")
    def mouse(kind, x, button, modifiers=Qt.KeyboardModifier.NoModifier):
        point = QPointF(MARGIN + x * view.cell_width, MARGIN + view.cell_height / 2)
        return QMouseEvent(kind, point, point, button, button, modifiers)
    QGuiApplication.clipboard().setText("")
    view.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 0, Qt.MouseButton.LeftButton))
    view.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 7, Qt.MouseButton.LeftButton))
    view.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, 7, Qt.MouseButton.LeftButton))
    assert QGuiApplication.clipboard().text() == "bonjour"
    sent = []
    view.typed.connect(sent.append)
    view.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 3, Qt.MouseButton.RightButton))
    assert sent == [b"bonjour"]
    shown = []
    view.context_menu = lambda point: shown.append(point)
    view.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 3, Qt.MouseButton.RightButton,
                               Qt.KeyboardModifier.ShiftModifier))
    assert shown and sent == [b"bonjour"]


def test_ssh_form_hides_file_only_options():
    app()
    from smftp.ui.connection_dialog import ConnectionDialog
    dialog = ConnectionDialog(None, None, sites=[])
    dialog.set_protocol("SSH")
    assert not dialog.form.isRowVisible(dialog.transfer_mode)
    assert not dialog.form.isRowVisible(dialog.encoding)
    assert not dialog.form.isRowVisible(dialog.local_directory.parentWidget())
    assert "SSH" in dialog.key.placeholderText()
    dialog.set_protocol("FTP")
    assert dialog.form.isRowVisible(dialog.transfer_mode) and dialog.form.isRowVisible(dialog.encoding)


def test_local_types_are_in_french(tmp_path):
    app()
    from smftp.ui.explorer import Explorer
    (tmp_path / "dossier").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "LISEZMOI").write_text("x")
    panel = Explorer()
    panel.navigate(str(tmp_path))
    model = panel.model
    root = model.index(str(tmp_path))
    assert wait_for(lambda: model.rowCount(root) == 3)
    types = {model.fileName(model.index(r, 0, root)): model.index(r, 2, root).data() for r in range(3)}
    assert types == {"dossier": "Dossier", "notes.txt": "Fichier TXT", "LISEZMOI": "Fichier"}


def test_window_starts_with_saved_profiles(monkeypatch):
    """Profiles read at startup get their identity before the theme paints the server list."""
    app()
    import smftp.ui.main_window as module
    saved = [{"name": "Bengadok", "protocol": "SSH", "host": "vps.example", "user": "root", "port": 22},
             {"name": "Site", "protocol": "SFTP", "host": "files.example", "user": "web", "port": 22}]
    monkeypatch.setattr(module, "load_sites", lambda *args: [dict(site) for site in saved])
    window = module.MainWindow()
    try:
        assert window.site_list.count() == 2
        for row in range(2):
            item = window.site_list.item(row)
            assert item.data(Qt.ItemDataRole.UserRole)
            assert "double-clic" in item.toolTip()
    finally:
        window.closing = True
        window.terminal.close_all()
        window.thread.quit()
        window.thread.wait(2000)
