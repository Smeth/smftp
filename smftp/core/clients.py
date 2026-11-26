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
from smftp.core.protocol_log import LoggedFTP, LoggedFTPS, LoggedImplicitFTPS
from smftp.core.checksum import ALGORITHMS, LABELS, normalize_algorithm, parse_digest
from smftp.core.errors import redact
from smftp.i18n import tr


class UnknownHostError(Exception):
    def __init__(self, host: str, key_type: str, key: str, fingerprint: str):
        super().__init__(tr("L’identité de ce serveur doit être confirmée."))
        self.host, self.key_type, self.key, self.fingerprint = host, key_type, key, fingerprint


class DestinationExists(FileExistsError):
    def __init__(self, source: str, target: str):
        super().__init__(tr("La destination existe déjà : {target}").format(target=target))
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

    def abort(self) -> None:
        """Called from another thread: make a blocking network call return at once."""
        try:
            self.close()
        except Exception:
            pass

    def upload(self, local: str, remote: str, progress, offset: int = 0) -> None:
        raise NotImplementedError

    def download(self, remote: str, local: str, progress, offset: int = 0) -> None:
        raise NotImplementedError

    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    def checksum(self, path: str) -> tuple[str, str] | None:
        """(hashlib algorithm, hex digest) computed by the server, or None if unsupported."""
        return None

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
            raise IsADirectoryError(tr("Le remplacement d’un dossier n’est pas pris en charge : renommez ou supprimez d’abord la destination."))
        self.overwrite_move(source, target)

    def remove(self, path: str, directory: bool = False, depth: int = 0) -> None:
        if directory:
            if depth > 100:
                raise ValueError(tr("Arborescence trop profonde (boucle de liens ?) ; suppression interrompue."))
            for item in self.listdir(path):
                if item.name in (".", "..") or any(c in item.name for c in "/\\\x00"):
                    raise ValueError(tr("Nom distant invalide ; suppression interrompue."))
                # A link is removed itself, never followed into its target.
                is_link = item.permissions.startswith("l")
                self.remove(posixpath.join(path, item.name), item.is_dir and not is_link, depth + 1)
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


# Encoding found by "auto" for a server (connection identity -> codec), so that the
# transfer connections opened later use the same one as the browsing session.
detected_encodings = {}


class FTPClient(BaseClient):
    tls = False
    implicit = False
    hash_method = None  # None = not detected yet, False = unsupported.
    auto_encoding = True
    identity = ""
    list_all = None  # LIST -a: None = not tried yet, then True or False.
    hides_dotfiles = False  # True once a plain LIST was used (dotfiles may be missing).
    incomplete_listing = False  # True once LIST lines could not be understood.

    def exists(self, path: str) -> Entry | None:
        found = super().exists(path)
        if found is not None:
            return found
        # A listing may hide the file (dotfiles on vsftpd and similar servers):
        # SIZE and MDTM ask the server about the file itself.
        try:
            self.ftp.sendcmd("TYPE I")
            size = self.ftp.size(path)
        except (ftplib.error_perm, ftplib.error_reply):
            return None
        if size is None:
            return None
        modified = 0.0
        try:
            reply = self.ftp.sendcmd(f"MDTM {path}")
            stamp = reply[4:].strip().split(".")[0]
            modified = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except (ftplib.error_perm, ftplib.error_reply, ValueError):
            pass
        return Entry(posixpath.basename(path), False, int(size), modified)

    def connect(self, connection: Connection) -> str:
        self.secrets = (connection.password,)
        self.timeout = connection.timeout
        if self.implicit:
            self.ftp = LoggedImplicitFTPS(context=ssl.create_default_context())
        else:
            self.ftp = (LoggedFTPS(context=ssl.create_default_context())
                        if self.tls else LoggedFTP())
        self.ftp.trace = self.report
        self.identity = connection.identity
        self.auto_encoding = connection.encoding == "auto"
        # Set before connecting: ftplib builds the control-channel reader in connect().
        self.ftp.encoding = (detected_encodings.get(self.identity, "utf-8") if self.auto_encoding
                             else connection.encoding)
        self.report(tr("Statut : résolution de {host} et connexion TCP au port {port}…").format(
            host=connection.host, port=connection.port))
        self.ftp.connect(connection.host, connection.port, timeout=connection.timeout)
        self.report(tr("Statut : serveur FTP joignable ; authentification en cours…"))
        self.ftp.login(connection.user, connection.password)
        self.report(tr("Statut : authentification FTP réussie."))
        self.ftp.set_pasv(connection.passive)
        self.report(tr("Statut : mode de transfert passif (PASV).") if connection.passive else
                    tr("Statut : mode de transfert actif (PORT) : le serveur se connecte à votre poste pour chaque transfert."))
        if self.tls:
            self.ftp.prot_p()
            self.report(tr("Statut : chiffrement TLS du canal de données activé."))
        if self.auto_encoding and self.ftp.encoding == "utf-8":
            try:
                features = self.ftp.sendcmd("FEAT").upper().split()
                if "UTF8" in features:
                    self.ftp.sendcmd("OPTS UTF8 ON")
            except ftplib.all_errors:
                pass
        self.report((tr("Statut : encodage des noms de fichiers : {encoding} (automatique).") if self.auto_encoding else
                     tr("Statut : encodage des noms de fichiers : {encoding}.")).format(encoding=self.ftp.encoding))
        self.report(tr("Statut : lecture du dossier de départ…"))
        return self.ftp.pwd()

    def set_encoding(self, encoding: str) -> None:
        """Switch the session's name encoding, control channel included."""
        self.ftp.encoding = encoding
        previous = self.ftp.file
        self.ftp.file = self.ftp.sock.makefile("r", encoding=encoding)
        if previous is not None:
            previous.close()  # Closes the reader only; the socket stays open.
        detected_encodings[self.identity] = encoding

    def read_lines(self, command: str) -> list[str]:
        """Run a listing command and decode it here, not in ftplib, so that a name in
        another encoding never leaves the control channel out of step."""
        self.ftp.sendcmd("TYPE A")
        chunks = []
        with self.ftp.transfercmd(command) as conn:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                chunks.append(data)
            if isinstance(conn, ssl.SSLSocket):
                conn.unwrap()
        self.ftp.voidresp()
        raw = b"".join(chunks)
        try:
            text = raw.decode(self.ftp.encoding)
        except UnicodeDecodeError:
            if self.auto_encoding and self.ftp.encoding == "utf-8":
                # Latin-1 maps every byte to one character and back: paths stay exact.
                self.set_encoding("latin-1")
                self.report(tr("Statut : noms de fichiers non UTF-8 ; passage automatique en ISO-8859-1 (Latin-1). "
                               "Choisissez l’encodage exact dans le profil si des accents restent incorrects."))
                text = raw.decode("latin-1")
            else:
                self.report(tr("Avertissement : certains noms ne sont pas valides en {encoding} ; "
                               "ils sont affichés avec « � » et ne pourront pas être ouverts. Changez l’encodage du profil.")
                            .format(encoding=self.ftp.encoding))
                text = raw.decode(self.ftp.encoding, errors="replace")
        return [line.rstrip("\r") for line in text.split("\n") if line.strip()]

    def listdir(self, path: str) -> list[Entry]:
        entries = []
        try:
            lines = self.read_lines(f"MLSD {path}" if path else "MLSD")
        except ftplib.error_perm as error:
            if str(error)[:3] not in ("500", "501", "502", "504"):
                raise
            from smftp.core.listing import parse_listing
            self.report(tr("Statut : MLSD indisponible ; lecture de la liste avec LIST."))
            # Without -a, servers such as vsftpd hide names starting with ".":
            # .htaccess, and the hidden partial files of safe uploads.
            lines = None
            if self.list_all is not False:
                try:
                    lines = self.read_lines(f"LIST -a {path}" if path else "LIST -a")
                except (ftplib.error_perm, ftplib.error_temp) as error:
                    # Plain LIST below reports the real error, if any. Only a
                    # syntax refusal means that this server never takes "-a".
                    if str(error)[:3] in ("500", "501", "502", "504"):
                        self.list_all = False
                else:
                    # A server that takes "-a" for a file name answers with an empty list.
                    if not lines and self.list_all is None:
                        lines = None
                    elif self.list_all is None and lines:
                        self.list_all = True
            if lines is None:
                lines = self.read_lines(f"LIST {path}" if path else "LIST")
                # This listing may leave out names starting with ".": mirror
                # synchronization must not take them for missing files.
                self.hides_dotfiles = True
            entries, skipped = parse_listing(lines)
            if skipped:
                # Mirror synchronization must not take the missing names for deleted files.
                self.incomplete_listing = True
                self.report(tr("Avertissement : {count} ligne(s) de la liste de {path} n’ont pas pu être "
                               "interprétées et ne sont pas affichées.").format(
                    count=skipped, path=path or tr("ce dossier")))
            return entries
        rows = []
        for line in lines:
            found, _, name = line.partition(" ")
            facts = {}
            for fact in found[:-1].split(";"):
                key, _, value = fact.partition("=")
                facts[key.lower()] = value
            rows.append((name, facts))
        for name, facts in rows:
            kind = facts.get("type", "").lower()
            if kind in ("cdir", "pdir"):
                continue
            # A malformed fact costs that value only, not the whole folder.
            modified = 0.0
            try:
                if facts.get("modify"):
                    modified = datetime.strptime(facts["modify"].split(".")[0], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
            try:
                size = int(facts.get("size", 0))
            except ValueError:
                size = 0
            permissions = facts.get("unix.mode", facts.get("perm", ""))
            if kind.startswith(("os.unix=slink", "os.unix=symlink")):
                # Symbolic link (Pure-FTPd, ProFTPD): never followed nor counted as a file.
                permissions = "l" + permissions
                entries.append(Entry(name, False, 0, modified, permissions))
                continue
            entries.append(Entry(name, kind == "dir", size, modified, permissions))
        return entries

    def close(self) -> None:
        if hasattr(self, "ftp"):
            self.ftp.close()

    def abort(self) -> None:
        # shutdown() wakes a recv() blocked in the worker thread (a long server checksum).
        import socket
        for sock in (getattr(getattr(self, "ftp", None), "sock", None),):
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

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

    def detect_hash(self):
        """HASH (draft-bryan-ftpext-hash) first, then the XSHA256/XSHA1/XMD5 extensions."""
        try:
            features = self.ftp.sendcmd("FEAT").splitlines()
        except ftplib.all_errors:
            features = []
        names = {line.strip().split(" ", 1)[0].upper(): line.strip() for line in features[1:-1]}
        if "HASH" in names:
            offered = {}
            for item in names["HASH"].split(" ", 1)[-1].split(";"):
                algorithm = normalize_algorithm(item.rstrip("*"))
                if algorithm:
                    offered[algorithm] = item.endswith("*")
            for algorithm in ALGORITHMS:
                if algorithm in offered:
                    if not offered[algorithm]:
                        try:
                            self.ftp.sendcmd(f"OPTS HASH {LABELS[algorithm]}")
                        except ftplib.all_errors:
                            continue
                    return ("HASH", algorithm)
        for command, algorithm in (("XSHA256", "sha256"), ("XSHA512", "sha512"), ("XSHA1", "sha1"), ("XMD5", "md5")):
            if command in names:
                return (command, algorithm)
        return False

    def checksum(self, path):
        if self.hash_method is None:
            self.hash_method = self.detect_hash()
            if self.hash_method:
                self.report(tr("Statut : empreinte serveur disponible ({method}).").format(
                    method=f"{self.hash_method[0]} {LABELS[self.hash_method[1]]}"))
        if not self.hash_method:
            return None
        command, algorithm = self.hash_method
        sock = self.ftp.sock
        previous = sock.gettimeout() if sock else None
        try:
            # Hashing a large file takes the server longer than a normal reply.
            if sock:
                sock.settimeout(max(previous or 0, 600))
            response = self.ftp.sendcmd(f"{command} {path}")
        except ftplib.error_perm:
            return None
        finally:
            if sock:
                sock.settimeout(previous)
        if command == "HASH":
            parts = response[4:].split(" ", 3)
            if len(parts) < 3 or normalize_algorithm(parts[0]) != algorithm:
                return None
            digest = parse_digest(parts[2], algorithm)
        else:
            digest = parse_digest(response[4:], algorithm)
        return (algorithm, digest) if digest else None

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


class ImplicitFTPSClient(FTPClient):
    """FTPS where TLS starts with the TCP connection (usually port 990)."""
    tls = True
    implicit = True


def trust_host_key(error: UnknownHostError, known_hosts: str) -> None:
    """Record a server key the user has just accepted."""
    import paramiko
    hosts = paramiko.HostKeys(known_hosts)
    key = paramiko.PKey.from_type_string(error.key_type, base64.b64decode(error.key))
    hosts.add(error.host, error.key_type, key)
    hosts.save(known_hosts)


def make_client(protocol: str, known_hosts: str, reporter=None) -> BaseClient:
    if protocol == "SFTP":
        return SFTPClient(known_hosts, reporter)
    if protocol == "SSH":
        return ShellClient(known_hosts, reporter)
    return {"FTP": FTPClient, "FTPS": FTPSClient, "FTPSI": ImplicitFTPSClient}[protocol](reporter)


class SFTPClient(BaseClient):
    # Tried in order; a restricted account (internal-sftp) gets no output and is not retried.
    HASH_COMMANDS = (("sha256", "sha256sum -b --"), ("sha256", "shasum -a 256 -b --"),
                     ("sha1", "sha1sum -b --"), ("md5", "md5sum -b --"))

    def __init__(self, known_hosts: str, reporter=None):
        super().__init__(reporter)
        self.known_hosts = known_hosts
        self.hash_command = None  # None = not detected, False = unsupported.

    def connect(self, connection: Connection) -> str:
        self.open_ssh(connection)
        self.report(tr("Statut : authentification SSH réussie ; ouverture du sous-système SFTP…"))
        self.sftp = self.ssh.open_sftp()
        self.sftp.get_channel().settimeout(connection.timeout)
        self.report(tr("Statut : sous-système SFTP ouvert ; lecture du dossier de départ…"))
        return self.sftp.normalize(".")

    def open_ssh(self, connection: Connection) -> None:
        """Connect and authenticate; an unknown server key raises UnknownHostError."""
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
        self.report(tr("Statut : résolution de {host} et connexion SSH au port {port}…").format(
            host=connection.host, port=connection.port))
        reporter = self.report
        class ReportingTransport(paramiko.Transport):
            def start_client(self, *args, **kwargs):
                reporter(tr("Statut : connexion TCP établie ; négociation SSH en cours…"))
                result = super().start_client(*args, **kwargs)
                reporter(tr("Statut : négociation SSH terminée ; vérification de la clé du serveur…"))
                return result

            def auth_password(self, *args, **kwargs):
                reporter(tr("Statut : clé du serveur acceptée ; authentification SSH par mot de passe…"))
                return super().auth_password(*args, **kwargs)

            def auth_publickey(self, *args, **kwargs):
                reporter(tr("Statut : clé du serveur acceptée ; authentification SSH par clé privée…"))
                return super().auth_publickey(*args, **kwargs)

        self.ssh.connect(connection.host, port=connection.port, username=connection.user,
                         # With a key file the secret is its passphrase: it must never be offered
                         # to the server as a password if the key is refused.
                         password=None if connection.key_file else (connection.password or None),
                         key_filename=connection.key_file or None,
                         passphrase=(connection.password or None) if connection.key_file else None,
                         timeout=connection.timeout, auth_timeout=connection.timeout,
                         banner_timeout=connection.timeout, channel_timeout=connection.timeout,
                         look_for_keys=False, allow_agent=False, transport_factory=ReportingTransport)
        self.ssh.get_transport().set_keepalive(30)
        self.timeout = connection.timeout

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

    def run_hash(self, command: str, algorithm: str, path: str) -> str | None:
        import shlex
        import paramiko
        try:
            channel = self.ssh.get_transport().open_session(timeout=self.timeout)
        except (paramiko.SSHException, OSError):
            return None
        try:
            channel.settimeout(max(self.timeout, 600))
            channel.exec_command(f"{command} {shlex.quote(path)}")
            # EOF on stdin: a forced internal-sftp exits instead of waiting.
            channel.shutdown_write()
            output = b""
            while chunk := channel.recv(4096):
                output += chunk
                if len(output) > 65536:
                    return None
            if channel.recv_exit_status() != 0:
                return None
            return parse_digest(output.decode("utf-8", "replace").split("\n", 1)[0], algorithm)
        except (paramiko.SSHException, OSError):
            return None
        finally:
            channel.close()

    def checksum(self, path):
        if self.hash_command is False:
            return None
        candidates = [self.hash_command] if self.hash_command else self.HASH_COMMANDS
        for algorithm, command in candidates:
            digest = self.run_hash(command, algorithm, path)
            if digest:
                if not self.hash_command:
                    self.report(tr("Statut : empreinte serveur disponible ({method}).").format(method=command.split()[0]))
                self.hash_command = (algorithm, command)
                return algorithm, digest
        if not self.hash_command:
            self.hash_command = False
        return None

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


class ShellClient(SFTPClient):
    """SSH terminal profile: authentication and an interactive shell, no SFTP subsystem."""

    def connect(self, connection: Connection) -> str:
        self.open_ssh(connection)
        self.report(tr("Statut : authentification SSH réussie."))
        return connection.remote_directory or "~"

    def open_shell(self, columns: int, lines: int):
        """Interactive login shell on a pseudo-terminal (xterm, UTF-8)."""
        channel = self.ssh.get_transport().open_session(timeout=self.timeout)
        channel.get_pty(term="xterm-256color", width=columns, height=lines)
        channel.invoke_shell()
        return channel

    def listdir(self, path: str) -> list[Entry]:
        raise NotImplementedError(tr("Un profil SSH ouvre un terminal, pas l’explorateur de fichiers."))

    def close(self) -> None:
        if hasattr(self, "ssh"):
            self.ssh.close()
