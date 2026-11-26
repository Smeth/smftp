"""Serialized network operations running outside the GUI thread."""
import logging
from PySide6.QtCore import QObject, Signal, Slot
from smftp.core.clients import make_client, UnknownHostError, DestinationExists, FTPClient
from smftp.models.entries import Connection
from smftp.utils.storage import data_dir
from smftp.core.errors import describe_error
from smftp.i18n import tr


class Session(QObject):
    progress = Signal(str)
    connected = Signal(str)
    listed = Signal(str, object)
    failed = Signal(object)
    host_unknown = Signal(object)
    disconnected = Signal()
    operated = Signal()
    inspected = Signal(str, object)  # token, entry
    inspect_failed = Signal(str)  # token
    move_conflict = Signal(str, str)  # source, existing target

    def __init__(self):
        super().__init__()
        self.client = None
        self.connection = None
        self.operation = tr("Connexion")
        self.cancelled = False

    def abort_connect(self) -> None:
        """Called from the window thread while connect_to() is blocked on the network."""
        self.cancelled = True
        client = self.client
        if client is not None:
            client.abort()

    @Slot(object)
    def connect_to(self, connection: Connection) -> None:
        self.close()
        self.connection = connection
        self.operation = tr("Connexion")
        self.cancelled = False
        try:
            hosts = data_dir() / "known_hosts"
            hosts.touch(exist_ok=True)
            self.client = make_client(connection.protocol, str(hosts), self.progress.emit)
            path = self.client.connect(connection)
            path = connection.remote_directory or path
            self.connected.emit(path)
            self.list_path(path)
        except UnknownHostError as error:
            self.progress.emit(tr("Statut : clé SSH inconnue ; confirmation de l’empreinte nécessaire."))
            self.close()
            self.host_unknown.emit(error)
        except Exception as error:
            self.close()
            if self.cancelled:
                from smftp.core.errors import ErrorInfo
                self.progress.emit(tr("Statut : connexion annulée."))
                self.failed.emit(ErrorInfo(tr("Connexion annulée."), ""))
                return
            self.report(error)

    @Slot(str)
    def list_path(self, path: str) -> None:
        self.operation = tr("Lecture du dossier distant")
        if self.client:
            self.client.report(tr("Statut : lecture du dossier distant {path}…").format(path=path))
            try:
                entries = self.client.listdir(path)
                self.client.report(tr("Statut : dossier distant chargé ({count} éléments).").format(count=len(entries)))
                self.listed.emit(path, entries)
            except Exception as error:
                self.report(error)

    def report(self, error: Exception) -> None:
        info = describe_error(error, self.connection, self.operation)
        logging.getLogger("smftp").warning("%s\n%s", info.summary, info.details)
        self.progress.emit(tr("Erreur : {summary}").format(summary=info.summary) + "\n" + info.details)
        self.failed.emit(info)

    @Slot(str, object)
    def operate(self, operation, arguments):
        self.operation = {"mkdir": tr("Création de dossier"), "rename": tr("Déplacement / renommage"), "replace": tr("Remplacement"),
                          "remove": tr("Suppression"), "chmod": tr("Modification des permissions")}.get(operation, operation)
        if not self.client:
            self.failed.emit(tr("Connectez d’abord un serveur."))
            return
        try:
            if operation == "rename":
                # Never let the server silently replace an existing destination.
                self.client.move(*arguments, replace=False)
            elif operation == "replace":
                self.client.move(*arguments, replace=True)
            elif operation in ("mkdir", "remove", "chmod"):
                getattr(self.client, operation)(*arguments)
            else:
                raise ValueError
            self.operated.emit()
        except DestinationExists as error:
            self.move_conflict.emit(error.source, error.target)
        except Exception as error:
            self.report(error)

    @Slot(str, str, str)
    def inspect_path(self, token, path, identity):
        self.operation = tr("Vérification du fichier distant")
        try:
            if not self.client or not self.connection:
                raise ConnectionError(tr("Aucune session ouverte."))
            if self.connection.identity != identity:
                # The same path on another server is a different file.
                raise ConnectionError(tr("La session ouverte ne correspond plus au serveur d’origine de ce fichier."))
            self.inspected.emit(token, self.client.exists(path))
        except Exception as error:
            # Release only this pending save before the generic diagnostic.
            self.inspect_failed.emit(token)
            self.report(error)

    @Slot(str)
    def command(self, command):
        self.operation = tr("Commande FTP")
        try:
            if not isinstance(self.client, FTPClient):
                raise ValueError(tr("Connectez un serveur FTP ou FTPS."))
            verb = command.split()[0].upper() if command.split() else ""
            if not verb or any(c in command for c in "\r\n") or verb in ("USER", "PASS", "ACCT", "ADAT", "AUTH", "REIN", "QUIT", "CCC", "PROT", "PBSZ", "PORT", "EPRT",
                "LIST", "MLSD", "NLST", "RETR", "STOR", "APPE", "STOU"):
                raise ValueError(tr("Commande interdite dans la console : utilisez les commandes de connexion de l’interface."))
            self.client.ftp.sendcmd(command)
            self.operated.emit()
        except Exception as error:
            self.report(error)

    @Slot()
    def close(self) -> None:
        if self.client:
            self.client.report(tr("Statut : fermeture de la connexion."))
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        self.disconnected.emit()
