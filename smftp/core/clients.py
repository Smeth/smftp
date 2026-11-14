"""Protocol adapters. Instances must be used by one worker thread at a time."""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
import base64
import ftplib
import hashlib
import ssl
import stat
import posixpath
from pathlib import Path
from smftp.models.entries import Connection, Entry
from smftp.core.protocol_log import LoggedFTP, LoggedFTPS
from smftp.core.errors import redact


class UnknownHostError(Exception):
    def __init__(self, host: str, key_type: str, key: str, fingerprint: str):
        super().__init__("L’identité de ce serveur doit être confirmée.")
        self.host, self.key_type, self.key, self.fingerprint = host, key_type, key, fingerprint


class DestinationExists(FileExistsError):
    def __init__(self, source: str, target: str):
        super().__init__(f"La destination existe déjà : {target}")
        self.source = source
        self.target = target


class BaseClient(ABC):
    def __init__(self, reporter=None):
        self.reporter = reporter
        self.secrets = ()

    def report(self, message: str) -> None:
        if self.reporter:
            self.reporter(redact(message, self.secrets))

    @abstractmethod
    def connect(self, connection: Connection) -> str: ...

    @abstractmethod
    def listdir(self, path: str) -> list[Entry]: ...

    @abstractmethod
    def close(self) -> None: ...

    def upload(self, local: str, remote: str, progress, offset: int = 0) -> None:
        raise NotImplementedError

    def download(self, remote: str, local: str, progress, offset: int = 0) -> None:
        raise NotImplementedError

    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    def rename(self, source: str, target: str) -> None:
        raise NotImplementedError

    def overwrite_move(self, source: str, target: str) -> None:
        """Move a file onto an existing file; servers that refuse keep both intact."""
        self.rename(source, target)

    def move(self, source: str, target: str, replace: bool = False) -> None:
        """Rename/move without silently replacing an existing destination."""
        if posixpath.normpath(source) == posixpath.normpath(target):
            return
        existing = self.exists(target)
        if existing is None:
            self.rename(source, target)
            return
        if not replace:
            raise DestinationExists(source, target)
        moving = self.exists(source)
        if existing.is_dir or (moving and moving.is_dir):
            raise IsADirectoryError("Le remplacement d’un dossier n’est pas pris en charge : renommez ou supprimez d’abord la destination.")
        self.overwrite_move(source, target)

    def remove(self, path: str, directory: bool = False) -> None:
        if directory:
            for item in self.listdir(path):
                if item.name in (".", "..") or any(c in item.name for c in "/\\\x00"):
                    raise ValueError("Nom distant invalide ; suppression interrompue.")
                self.remove(posixpath.join(path, item.name), item.is_dir)
            self.rmdir(path)
        else:
            self.unlink(path)

    def exists(self, path: str) -> Entry | None:
        parent, name = posixpath.split(path)
        return next((e for e in self.listdir(parent or "/") if e.name == name), None)

    def makedirs(self, path: str) -> None:
        current = "/" if path.startswith("/") else ""
        for part in path.split("/"):
            if not part:
                continue
            current = posixpath.join(current, part)
            item = self.exists(current)
            if item is None:
                self.mkdir(current)
            elif not item.is_dir:
                raise NotADirectoryError(current)


class FTPClient(BaseClient):
    tls = False

    def connect(self, connection: Connection) -> str:
        self.secrets = (connection.password,)
        self.ftp = (LoggedFTPS(context=ssl.create_default_context())
                    if self.tls else LoggedFTP())
        self.ftp.trace = self.report
        self.report(f"Statut : résolution de {connection.host} et connexion TCP au port {connection.port}…")
        self.ftp.connect(connection.host, connection.port, timeout=connection.timeout)
        self.report("Statut : serveur FTP joignable ; authentification en cours…")
        self.ftp.login(connection.user, connection.password)
        self.report("Statut : authentification FTP réussie.")
        if self.tls:
            self.ftp.prot_p()
            self.report("Statut : chiffrement TLS du canal de données activé.")
        self.report("Statut : lecture du dossier de départ…")
        return self.ftp.pwd()

    def listdir(self, path: str) -> list[Entry]:
        entries = []
        try:
            rows = list(self.ftp.mlsd(path))
        except ftplib.error_perm as error:
            if str(error)[:3] not in ("500", "501", "502", "504"):
                raise
            from smftp.core.listing import parse_list_line
            self.report("Statut : MLSD indisponible ; lecture de la liste avec LIST.")
            lines = []
            self.ftp.retrlines(f"LIST {path}", lines.append)
            return [entry for line in lines if (entry := parse_list_line(line)) is not None and entry.name not in (".", "..")]
        for name, facts in rows:
            if facts.get("type") in ("cdir", "pdir"):
                continue
            modified = 0.0
            if facts.get("modify"):
                modified = datetime.strptime(facts["modify"].split(".")[0], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            entries.append(Entry(name, facts.get("type") == "dir", int(facts.get("size", 0)), modified, facts.get("unix.mode", facts.get("perm", ""))))
        return entries

    def close(self) -> None:
        if hasattr(self, "ftp"):
            self.ftp.close()

    def upload(self, local, remote, progress, offset=0):
        done = offset
        total = Path(local).stat().st_size
        def callback(block):
            nonlocal done
            done += len(block)
            progress(done, total)
        with open(local, "rb") as stream:
            stream.seek(offset)
            self.ftp.storbinary(f"STOR {remote}", stream, callback=callback, rest=offset or None)

    def download(self, remote, local, progress, offset=0):
        entry = self.exists(remote)
        total = entry.size if entry else 0
        done = offset
        with open(local, "ab" if offset else "wb") as stream:
            def callback(block):
                nonlocal done
                stream.write(block)
                done += len(block)
                progress(done, total)
            self.ftp.retrbinary(f"RETR {remote}", callback, rest=offset or None)

    def mkdir(self, path):
        self.ftp.mkd(path)

    def rename(self, source, target):
        self.ftp.rename(source, target)

    def replace(self, source, target):
        # Server RNTO semantics decide whether an existing target can be replaced.
        # Never delete the old destination to force an overwrite.
        self.ftp.rename(source, target)

    def rmdir(self, path):
        self.ftp.rmd(path)

    def unlink(self, path):
        self.ftp.delete(path)

    def chmod(self, path, mode):
        self.ftp.sendcmd(f"SITE CHMOD {mode:o} {path}")


class FTPSClient(FTPClient):
    tls = True


class SFTPClient(BaseClient):
    def __init__(self, known_hosts: str, reporter=None):
        super().__init__(reporter)
        self.known_hosts = known_hosts

    def connect(self, connection: Connection) -> str:
        import paramiko
        self.secrets = (connection.password,)

        class ConfirmHost(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
                raise UnknownHostError(hostname, key.get_name(), key.get_base64(), f"SHA256:{digest}")

        self.ssh = paramiko.SSHClient()
        self.ssh.load_system_host_keys()
        self.ssh.load_host_keys(self.known_hosts)
        self.ssh.set_missing_host_key_policy(ConfirmHost())
        self.report(f"Statut : résolution de {connection.host} et connexion SSH au port {connection.port}…")
        reporter = self.report
        class ReportingTransport(paramiko.Transport):
            def start_client(self, *args, **kwargs):
                reporter("Statut : connexion TCP établie ; négociation SSH en cours…")
                result = super().start_client(*args, **kwargs)
                reporter("Statut : négociation SSH terminée ; vérification de la clé du serveur…")
                return result

            def auth_password(self, *args, **kwargs):
                reporter("Statut : clé du serveur acceptée ; authentification SSH par mot de passe…")
                return super().auth_password(*args, **kwargs)

            def auth_publickey(self, *args, **kwargs):
                reporter("Statut : clé du serveur acceptée ; authentification SSH par clé privée…")
                return super().auth_publickey(*args, **kwargs)

        self.ssh.connect(connection.host, port=connection.port, username=connection.user,
                         password=connection.password or None,
                         key_filename=connection.key_file or None,
                         passphrase=connection.password or None,
                         timeout=connection.timeout, auth_timeout=connection.timeout,
                         banner_timeout=connection.timeout, channel_timeout=connection.timeout,
                         look_for_keys=False, allow_agent=False, transport_factory=ReportingTransport)
        self.report("Statut : authentification SSH réussie ; ouverture du sous-système SFTP…")
        self.ssh.get_transport().set_keepalive(30)
        self.sftp = self.ssh.open_sftp()
        self.sftp.get_channel().settimeout(connection.timeout)
        self.report("Statut : sous-système SFTP ouvert ; lecture du dossier de départ…")
        return self.sftp.normalize(".")

    def upload(self, local, remote, progress, offset=0):
        total = Path(local).stat().st_size
        done = offset
        with open(local, "rb") as source, self.sftp.open(remote, "r+b" if offset else "wb") as target:
            source.seek(offset)
            target.seek(offset)
            while block := source.read(65536):
                target.write(block)
                done += len(block)
                progress(done, total)

    def download(self, remote, local, progress, offset=0):
        total = self.sftp.stat(remote).st_size
        done = offset
        with self.sftp.open(remote, "rb") as source, open(local, "ab" if offset else "wb") as target:
            source.seek(offset)
            while block := source.read(65536):
                target.write(block)
                done += len(block)
                progress(done, total)

    def mkdir(self, path):
        self.sftp.mkdir(path)

    def rename(self, source, target):
        self.sftp.rename(source, target)

    def overwrite_move(self, source, target):
        # Atomic OpenSSH extension; the moved file keeps its own permissions.
        self.sftp.posix_rename(source, target)

    def replace(self, source, target):
        if self.exists(target) is None:
            self.sftp.rename(source, target)
        else:
            # OpenSSH extension performs an atomic replacement; no unsafe fallback.
            mode = stat.S_IMODE(self.sftp.stat(target).st_mode)
            self.sftp.chmod(source, mode)
            self.sftp.posix_rename(source, target)

    def rmdir(self, path):
        self.sftp.rmdir(path)

    def unlink(self, path):
        self.sftp.remove(path)

    def chmod(self, path, mode):
        self.sftp.chmod(path, mode)

    def listdir(self, path: str) -> list[Entry]:
        return [Entry(item.filename, stat.S_ISDIR(item.st_mode or 0), item.st_size or 0,
                      item.st_mtime or 0, stat.filemode(item.st_mode or 0))
                for item in self.sftp.listdir_attr(path)]

    def close(self) -> None:
        if hasattr(self, "sftp"):
            self.sftp.close()
        if hasattr(self, "ssh"):
            self.ssh.close()
