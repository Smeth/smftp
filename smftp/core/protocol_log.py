"""FTP protocol tracing without ftplib debug output or credential disclosure."""
import ftplib
import ssl


class FTPTrace:
    trace = None

    def emit_trace(self, message: str) -> None:
        if self.trace:
            self.trace(message)

    def putcmd(self, line: str) -> None:
        command = line.split(" ", 1)[0].upper()
        safe = f"{command} [masqué]" if command in ("PASS", "ACCT", "ADAT") else line
        self.emit_trace("Commande : " + safe)
        super().putcmd(line)

    def getresp(self) -> str:
        try:
            response = super().getresp()
        except (ftplib.error_reply, ftplib.error_temp, ftplib.error_perm, ftplib.error_proto) as error:
            self.emit_trace("Réponse : " + str(error))
            raise
        self.emit_trace("Réponse : " + response)
        return response

    def auth(self):
        self.emit_trace("Statut : négociation TLS et vérification du certificat…")
        response = super().auth()
        self.emit_trace("Statut : connexion TLS établie, certificat vérifié.")
        return response


class LoggedFTP(FTPTrace, ftplib.FTP):
    pass


class SessionReuse:
    """Data connections resume the TLS session of the control connection.

    Many servers require it so that nobody else can take over a data
    connection: vsftpd (require_ssl_reuse, on by default), FileZilla Server,
    ProFTPD with TLSOptions RequireSessionReuse. ftplib starts a new session.
    """
    last_data_session_reused = None  # For diagnostics and tests.

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
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
        self.emit_trace("Statut : FTPS implicite — négociation TLS et vérification du certificat dès la connexion…")
        response = super().connect(*args, **kwargs)
        self.emit_trace("Statut : connexion TLS établie, certificat vérifié.")
        return response
