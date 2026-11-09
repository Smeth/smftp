"""Actionable diagnostics with secrets removed before crossing UI/log boundaries."""
from dataclasses import dataclass
import base64
import errno
import ftplib
import re
import socket
import ssl
from urllib.parse import quote, quote_plus

from smftp.models.entries import Connection


@dataclass(frozen=True)
class ErrorInfo:
    summary: str
    details: str
    retryable: bool = False


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        variants = {secret, quote(secret, safe=""), quote_plus(secret),
                    base64.b64encode(secret.encode()).decode(), repr(secret)[1:-1]}
        for value in sorted(variants, key=len, reverse=True):
            text = text.replace(value, "[masqué]")
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://[^\s/@:]+:)[^\s/@]+(@)", r"\1[masqué]\2", text)
    text = re.sub(r"(?im)\b(PASS(?:WORD)?|PASSPHRASE|TOKEN|SECRET)\s*[:= ]\s*[^\r\n]+", r"\1 [masqué]", text)
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32)[:8000]


def describe_error(error: Exception, connection: Connection | None = None,
                   operation: str = "Opération réseau") -> ErrorInfo:
    name = type(error).__name__
    text = str(error)
    nested = getattr(error, "errors", {})
    causes = list(nested.values()) if isinstance(nested, dict) else []
    numbers = {getattr(e, "errno", None) for e in [error, *causes]}
    numbers.update(getattr(e, "winerror", None) for e in [error, *causes])
    if name in ("XMLImportError", "XMLCorrectionRequired"):
        summary = text
    elif isinstance(error, socket.gaierror):
        summary = "Hôte introuvable. Vérifiez l’adresse du serveur et votre DNS."
    elif isinstance(error, (TimeoutError, socket.timeout)) or numbers & {errno.ETIMEDOUT, 10060}:
        summary = "Délai de connexion dépassé. Vérifiez l’hôte, le port et le pare-feu."
    elif isinstance(error, ConnectionRefusedError) or numbers & {errno.ECONNREFUSED, 10061}:
        summary = "Connexion refusée par le serveur. Vérifiez le port et que le service FTP/SSH est démarré."
    elif numbers & {errno.ENETUNREACH, errno.EHOSTUNREACH, 10051, 10065}:
        summary = "Serveur inaccessible depuis ce réseau. Vérifiez la connexion, le VPN et le routage."
    elif isinstance(error, (ConnectionResetError, BrokenPipeError, EOFError)) or numbers & {10054, 10053}:
        summary = "Le serveur a interrompu la connexion. Vérifiez le protocole choisi et les règles du serveur."
    elif isinstance(error, ssl.SSLCertVerificationError):
        summary = "Certificat TLS invalide, expiré ou ne correspondant pas à l’hôte. La connexion est bloquée."
    elif isinstance(error, ssl.SSLError):
        summary = "Échec de négociation TLS. Vérifiez que ce port accepte le FTPS explicite."
    elif name == "BadHostKeyException":
        summary = "La clé SSH du serveur a changé. Connexion bloquée : vérifiez son identité auprès de l’administrateur."
    elif name == "PasswordRequiredException":
        summary = "La clé privée SSH est chiffrée. Saisissez sa phrase secrète."
    elif name in ("AuthenticationException", "BadAuthenticationType", "UnableToAuthenticate", "PartialAuthentication"):
        summary = "Authentification SSH refusée. Vérifiez l’utilisateur, le mot de passe, la clé et les méthodes autorisées."
    elif isinstance(error, (ftplib.error_perm, ftplib.error_temp, ftplib.error_reply, ftplib.error_proto)):
        code = text[:3]
        summary = {
            "530": "Authentification FTP refusée (530). Vérifiez les identifiants et les restrictions du compte.",
            "532": "Le serveur exige un compte supplémentaire (532). Contactez son administrateur.",
            "550": "Accès FTP refusé ou chemin inexistant (550). Consultez la réponse du serveur dans les détails.",
            "553": "Nom de fichier ou chemin refusé par le serveur (553).",
            "421": "Service FTP indisponible ou limite de connexions atteinte (421). Réessayez plus tard.",
            "425": "Le serveur ne peut pas ouvrir le canal de données (425). Vérifiez les ports passifs et le pare-feu.",
            "426": "Le transfert a été interrompu par le serveur (426).",
            "452": "Espace de stockage insuffisant sur le serveur (452).",
            "500": "Commande FTP non reconnue (500). Vérifiez les capacités du serveur.",
            "502": "Commande FTP non prise en charge (502), notamment MLSD pour la navigation.",
        }.get(code, f"Le serveur FTP a refusé l’opération ({code if code.isdigit() else name}). Consultez sa réponse dans les détails.")
    elif isinstance(error, PermissionError):
        summary = "Accès refusé. Vérifiez les permissions du fichier, du dossier ou de la clé privée."
    elif isinstance(error, FileNotFoundError):
        summary = "Fichier ou dossier introuvable. Vérifiez aussi le chemin de votre clé SSH."
    elif numbers & {errno.ENOSPC, 112}:
        summary = "Espace disque insuffisant. Libérez de l’espace avant de réessayer."
    elif isinstance(error, ModuleNotFoundError):
        summary = "Une dépendance Python est manquante. Réinstallez les paquets de requirements.txt."
    elif name in ("SSHException", "IncompatiblePeer"):
        summary = "Négociation SSH impossible. Vérifiez le protocole, le port et la compatibilité des algorithmes."
    else:
        summary = "L’opération a échoué. Consultez les détails techniques ci-dessous pour connaître la cause rapportée."
    context = operation
    if connection:
        context += f" — {connection.protocol} {connection.host}:{connection.port}"
    details = f"{context}\nType : {name}\nRéponse / cause : {text or '(aucun détail fourni)'}"
    for address, cause in nested.items() if isinstance(nested, dict) else []:
        details += f"\n{address} : {type(cause).__name__} : {cause}"
    secrets = (connection.password,) if connection else ()
    retryable = isinstance(error, (ConnectionError, TimeoutError, EOFError, socket.gaierror)) or bool(
        numbers & {errno.ETIMEDOUT, errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH, 10060, 10061, 10054, 10053, 10051, 10065})
    if isinstance(error, ftplib.error_temp) and text[:3] in ("421", "425", "426"):
        retryable = True
    return ErrorInfo(redact(summary, secrets), redact(details, secrets), retryable)
