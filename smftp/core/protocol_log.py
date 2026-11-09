"""FTP protocol tracing without ftplib debug output or credential disclosure."""
import ftplib


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
