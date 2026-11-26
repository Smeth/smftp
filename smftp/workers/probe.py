"""One-shot connection test for the connection dialog.

Connects, authenticates and lists the start folder (which, for FTP, also opens a
data channel and so checks the passive / active mode), then disconnects. Nothing
is saved and the main session is not touched.
"""
from PySide6.QtCore import QThread, Signal
from smftp.core.clients import make_client, UnknownHostError
from smftp.core.errors import describe_error
from smftp.utils.storage import data_dir
from smftp.i18n import tr

# Probes still running when their dialog closed: kept alive until they finish,
# so that closing the dialog never waits on a slow network timeout.
_orphans = set()


class ConnectionProbe(QThread):
    step = Signal(str)
    succeeded = Signal(str)
    failed = Signal(object)  # ErrorInfo
    host_unknown = Signal(object)  # UnknownHostError

    def __init__(self, connection):
        super().__init__()
        self.connection = connection
        self.finished.connect(lambda: _orphans.discard(self))

    def run(self):
        connection = self.connection
        client = None
        phase = "connect"
        try:
            hosts = data_dir() / "known_hosts"
            hosts.touch(exist_ok=True)
            client = make_client(connection.protocol, str(hosts), self.step.emit)
            home = client.connect(connection)
            if connection.protocol == "SSH":
                self.succeeded.emit(tr("Connexion SSH réussie : serveur vérifié et authentification acceptée."))
                return
            phase = "list"
            path = connection.remote_directory or home
            entries = client.listdir(path)
            summary = tr("Connexion réussie : {count} élément(s) dans {path}.").format(count=len(entries), path=path)
            if connection.protocol != "SFTP":
                summary += " " + (tr("Mode passif.") if connection.passive else tr("Mode actif."))
            self.succeeded.emit(summary)
        except UnknownHostError as error:
            self.host_unknown.emit(error)
        except Exception as error:
            info = describe_error(error, connection, tr("Test de connexion"))
            if phase == "list" and connection.protocol != "SFTP":
                from dataclasses import replace
                other = tr("actif") if connection.passive else tr("passif")
                info = replace(info, details=(
                    tr("L’authentification a réussi mais la lecture du dossier a échoué. Si le dossier existe, "
                       "le canal de données est sans doute bloqué : essayez le mode {mode} "
                       "(Options avancées → Mode de transfert).").format(mode=other) + "\n\n" + info.details))
            self.failed.emit(info)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def abandon(self):
        """Stop reporting to the dialog; let the thread end on its own."""
        for signal in (self.step, self.succeeded, self.failed, self.host_unknown):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        if self.isRunning():
            _orphans.add(self)
