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


class LoggedFTPS(FTPTrace, ftplib.FTP_TLS):
    pass


class ImplicitFTP_TLS(ftplib.FTP_TLS):
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
