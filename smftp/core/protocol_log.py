"""FTP protocol tracing without ftplib debug output or credential disclosure."""
import ftplib
import ssl
from smftp.i18n import tr


class FTPTrace:
    trace = None

    def emit_trace(self, message: str) -> None:
        if self.trace:
            self.trace(message)

    def putcmd(self, line: str) -> None:
        command = line.split(" ", 1)[0].upper()
        safe = f"{command} [masqué]" if command in ("PASS", "ACCT", "ADAT") else line
        # The command itself is protocol text: only the prefix is translated.
        self.emit_trace(tr("Commande : {command}").format(command=safe))
        super().putcmd(line)

    def getresp(self) -> str:
        try:
            response = super().getresp()
        except (ftplib.error_reply, ftplib.error_temp, ftplib.error_perm, ftplib.error_proto) as error:
            self.emit_trace(tr("Réponse : {response}").format(response=error))
            raise
        self.emit_trace(tr("Réponse : {response}").format(response=response))
        return response

    def auth(self):
        self.emit_trace(tr("Statut : négociation TLS et vérification du certificat…"))
        response = super().auth()
        self.emit_trace(tr("Statut : connexion TLS établie, certificat vérifié."))
        return response


class ProxyActiveModeError(ValueError):
    """Active mode needs the server to connect back to this computer: never through a proxy."""


class ProxySupport:
    """Control and passive data connections opened by ``open_socket`` (a proxy tunnel).

    ``open_socket(host, port, timeout)`` returns a connected socket; None keeps
    ftplib's direct connections. Through a proxy the data connection goes to the
    server name of the profile and the port announced by PASV / EPSV: the address
    in the PASV reply is the server's own view (often private), useless to the proxy.
    """
    open_socket = None

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if self.open_socket is None:
            return super().connect(host, port, timeout, source_address)
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        self.sock = self.open_socket(self.host, self.port, self.timeout)
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def passive_port(self) -> int:
        try:
            return ftplib.parse227(self.sendcmd("PASV"))[1]
        except ftplib.error_perm:
            return ftplib.parse229(self.sendcmd("EPSV"), ("", 0))[1]

    def plain_transfer(self, cmd, rest=None):
        """ftplib.FTP.ntransfercmd, with the data connection through the proxy."""
        if self.open_socket is None:
            return ftplib.FTP.ntransfercmd(self, cmd, rest)
        if not self.passiveserver:
            raise ProxyActiveModeError(tr("Le mode actif est impossible à travers un proxy : "
                                          "choisissez le mode passif dans le profil."))
        conn = self.open_socket(self.host, self.passive_port(), self.timeout)
        try:
            if rest is not None:
                self.sendcmd(f"REST {rest}")
            response = self.sendcmd(cmd)
            if response[0] == "2":
                response = self.getresp()
            if response[0] != "1":
                raise ftplib.error_reply(response)
        except BaseException:
            conn.close()
            raise
        size = ftplib.parse150(response) if response[:3] == "150" else None
        return conn, size

    def ntransfercmd(self, cmd, rest=None):
        return self.plain_transfer(cmd, rest)


class LoggedFTP(FTPTrace, ProxySupport, ftplib.FTP):
    pass


class SessionReuse(ProxySupport):
    """Data connections resume the TLS session of the control connection.

    Many servers require it so that nobody else can take over a data
    connection: vsftpd (require_ssl_reuse, on by default), FileZilla Server,
    ProFTPD with TLSOptions RequireSessionReuse. ftplib starts a new session.
    """
    last_data_session_reused = None  # For diagnostics and tests.

    def ntransfercmd(self, cmd, rest=None):
        conn, size = self.plain_transfer(cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
            self.last_data_session_reused = conn.session_reused
        return conn, size


class LoggedFTPS(FTPTrace, SessionReuse, ftplib.FTP_TLS):
    pass


class ImplicitFTP_TLS(SessionReuse, ftplib.FTP_TLS):
    """Wraps the control socket as soon as it is connected (implicit FTPS)."""
    _sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._sock = value


class LoggedImplicitFTPS(FTPTrace, ImplicitFTP_TLS):
    def connect(self, *args, **kwargs):
        self.emit_trace(tr("Statut : FTPS implicite — négociation TLS et vérification du certificat dès la connexion…"))
        response = super().connect(*args, **kwargs)
        self.emit_trace(tr("Statut : connexion TLS établie, certificat vérifié."))
        return response
