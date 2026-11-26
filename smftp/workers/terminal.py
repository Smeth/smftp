"""Interactive SSH shell running on its own connection and thread.

The terminal never shares the file browser's session: a long command or a lost
network only affects its own tab.
"""
import shlex
import threading
from PySide6.QtCore import QThread, Signal
from smftp.core.clients import ShellClient, UnknownHostError
from smftp.core.errors import describe_error
from smftp.utils.storage import data_dir
from smftp.i18n import tr


class ShellWorker(QThread):
    status = Signal(str)  # Connection steps, for the log (secrets already masked).
    opened = Signal()
    output = Signal(bytes)
    failed = Signal(object)  # ErrorInfo
    host_unknown = Signal(object)  # UnknownHostError
    closed = Signal()

    def __init__(self, connection, columns=80, lines=24, client_factory=None):
        super().__init__()
        self.connection = connection
        self.size = (max(2, columns), max(2, lines))
        self.client_factory = client_factory or (lambda hosts, report: ShellClient(hosts, report))
        self.channel = None
        self.client = None
        self.stopping = False
        self.lock = threading.Lock()

    def run(self):
        connection = self.connection
        try:
            hosts = data_dir() / "known_hosts"
            hosts.touch(exist_ok=True)
            self.client = self.client_factory(str(hosts), self.status.emit)
            self.client.connect(connection)
            if self.stopping:
                return
            self.status.emit(tr("Statut : ouverture du terminal interactif…"))
            with self.lock:
                self.channel = self.client.open_shell(*self.size)
            self.opened.emit()
            if connection.remote_directory:
                self.send(f"cd {shlex.quote(connection.remote_directory)}\n".encode())
            while not self.stopping:
                data = self.channel.recv(32768)
                if not data:
                    break
                self.output.emit(data)
        except UnknownHostError as error:
            self.status.emit(tr("Statut : clé SSH inconnue ; confirmation de l’empreinte nécessaire."))
            self.host_unknown.emit(error)
        except Exception as error:
            if not self.stopping:
                self.failed.emit(describe_error(error, connection, tr("Terminal SSH")))
        finally:
            self.release()
            self.closed.emit()

    def send(self, data: bytes) -> None:
        """Keyboard input from the GUI thread; dropped while the shell is not open."""
        with self.lock:
            channel = self.channel
        if channel is None or channel.closed:
            return
        try:
            channel.sendall(data)
        except Exception:
            pass  # The read loop reports the closed session.

    def resize(self, columns: int, lines: int) -> None:
        self.size = (max(2, columns), max(2, lines))
        with self.lock:
            channel = self.channel
        if channel is not None and not channel.closed:
            try:
                channel.resize_pty(width=self.size[0], height=self.size[1])
            except Exception:
                pass

    def stop(self) -> None:
        """Close the session; the read loop ends and ``closed`` is emitted."""
        self.stopping = True
        self.release()

    def release(self) -> None:
        with self.lock:
            channel, client = self.channel, self.client
            self.channel = None
        for item in (channel, client):
            if item is not None:
                try:
                    item.close()
                except Exception:
                    pass
