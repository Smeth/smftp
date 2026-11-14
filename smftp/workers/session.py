"""Serialized network operations running outside the GUI thread."""
import logging
from PySide6.QtCore import QObject, Signal, Slot
from smftp.core.clients import FTPClient, FTPSClient, SFTPClient, UnknownHostError, DestinationExists
from smftp.models.entries import Connection
from smftp.utils.storage import data_dir
from smftp.core.errors import describe_error


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
        self.operation = "Connexion"

    @Slot(object)
    def connect_to(self, connection: Connection) -> None:
        self.close()
        self.connection = connection
        self.operation = "Connexion"
        try:
            hosts = data_dir() / "known_hosts"
            hosts.touch(exist_ok=True)
            factory = {"FTP": FTPClient, "FTPS": FTPSClient}
            self.client = SFTPClient(str(hosts), self.progress.emit) if connection.protocol == "SFTP" else factory[connection.protocol](self.progress.emit)
            path = self.client.connect(connection)
            path = connection.remote_directory or path
            self.connected.emit(path)
            self.list_path(path)
        except UnknownHostError as error:
            self.progress.emit("Statut : clé SSH inconnue ; confirmation de l’empreinte nécessaire.")
            self.close()
            self.host_unknown.emit(error)
        except Exception as error:
            self.close()
            self.report(error)

    @Slot(str)
    def list_path(self, path: str) -> None:
        self.operation = "Lecture du dossier distant"
        if self.client:
            self.client.report(f"Statut : lecture du dossier distant {path}…")
            try:
                entries = self.client.listdir(path)
                self.client.report(f"Statut : dossier distant chargé ({len(entries)} éléments).")
                self.listed.emit(path, entries)
            except Exception as error:
                self.report(error)

    def report(self, error: Exception) -> None:
        info = describe_error(error, self.connection, self.operation)
        logging.getLogger("smftp").warning("%s\n%s", info.summary, info.details)
        self.progress.emit("Erreur : " + info.summary + "\n" + info.details)
        self.failed.emit(info)

    @Slot(str, object)
    def operate(self, operation, arguments):
        self.operation = {"mkdir": "Création de dossier", "rename": "Déplacement / renommage", "replace": "Remplacement", "remove": "Suppression", "chmod": "Modification des permissions"}.get(operation, operation)
        if not self.client:
            self.failed.emit("Connectez d’abord un serveur.")
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
        self.operation = "Vérification du fichier distant"
        try:
            if not self.client or not self.connection:
                raise ConnectionError("Aucune session ouverte.")
            if self.connection.identity != identity:
                # The same path on another server is a different file.
                raise ConnectionError("La session ouverte ne correspond plus au serveur d’origine de ce fichier.")
            self.inspected.emit(token, self.client.exists(path))
        except Exception as error:
            # Release only this pending save before the generic diagnostic.
            self.inspect_failed.emit(token)
            self.report(error)

    @Slot(str)
    def command(self, command):
        self.operation = "Commande FTP"
        try:
            if not isinstance(self.client, FTPClient):
                raise ValueError("Connectez un serveur FTP ou FTPS.")
            verb = command.split()[0].upper() if command.split() else ""
            if not verb or any(c in command for c in "\r\n") or verb in ("USER", "PASS", "ACCT", "ADAT", "AUTH", "REIN", "QUIT", "CCC", "PROT", "PBSZ", "PORT", "EPRT",
                "LIST", "MLSD", "NLST", "RETR", "STOR", "APPE", "STOU"):
                raise ValueError("Commande interdite dans la console : utilisez les commandes de connexion de l’interface.")
            self.client.ftp.sendcmd(command)
            self.operated.emit()
        except Exception as error:
            self.report(error)

    @Slot()
    def close(self) -> None:
        if self.client:
            self.client.report("Statut : fermeture de la connexion.")
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        self.disconnected.emit()
