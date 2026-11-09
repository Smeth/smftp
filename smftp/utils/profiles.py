"""Validated FileZilla/JSON imports and OS credential-vault operations."""
import base64
from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET
from smftp.models.entries import Connection

def normalize_profile(site: dict) -> dict:
    if not isinstance(site, dict):
        raise ValueError("Le profil doit être un objet.")
    protocol = site.get("protocol", "SFTP")
    host, user = site.get("host", ""), site.get("user", "")
    port = int(site.get("port", 22 if protocol == "SFTP" else 21))
    if protocol not in ("SFTP", "FTP", "FTPS") or not 1 <= port <= 65535:
        raise ValueError("Protocole ou port invalide.")
    if not isinstance(host, str) or not host.strip() or any(c in host for c in "/\\@\r\n\t "):
        raise ValueError("Hôte invalide : saisissez une adresse sans préfixe ftp:// ni chemin.")
    if not isinstance(user, str) or not user.strip() or any(c in user for c in "\r\n"):
        raise ValueError("Utilisateur manquant ou invalide.")
    return dict(host=host.strip(), user=user.strip(), protocol=protocol, port=port,
                key_file=str(site.get("key_file", "")), remember=bool(site.get("remember", False)),
                name=str(site.get("name", "")).strip() or host.strip(),
                group=str(site.get("group", "")).strip(),
                local_directory=str(site.get("local_directory", "")),
                remote_directory=str(site.get("remote_directory", "")),
                timeout=max(5, min(300, int(site.get("timeout", 20)))))


def profile_for(connection: Connection) -> dict:
    return normalize_profile(asdict(connection))


def identity(site: dict) -> str:
    return Connection(**normalize_profile(site)).identity


def merge_profiles(existing: list[dict], imported: list[dict]) -> list[dict]:
    result = {identity(site): normalize_profile(site) for site in existing}
    for site in imported:
        clean = normalize_profile(site)
        previous = result.get(identity(clean), {})
        clean["remember"] = clean["remember"] or previous.get("remember", False)
        result[identity(clean)] = clean
    return list(result.values())


def store_credential(connection: Connection) -> None:
    import keyring
    if connection.remember:
        keyring.set_password("SMFTP", connection.identity, connection.password)
    elif keyring.get_password("SMFTP", connection.identity) is not None:
        keyring.delete_password("SMFTP", connection.identity)


@dataclass
class ImportResult:
    profiles: list[dict] = field(default_factory=list)
    passwords: dict[str, str] = field(default_factory=dict, repr=False)
    warnings: list[str] = field(default_factory=list)


class XMLImportError(ValueError):
    """An XML diagnostic safe to show without exposing file contents."""


class XMLCorrectionRequired(XMLImportError):
    """A narrowly scoped header repair requires explicit user acceptance."""


def repair_xml_header(data: bytes) -> tuple[bytes, list[str]]:
    # Latin-1 is a reversible byte mapping for ASCII-compatible XML encodings.
    # Detect Unicode byte order before matching the declaration, not document text.
    if data.startswith((b"\xff\xfe\x00\x00", b"<\x00\x00\x00")):
        encoding = "utf-32-le"
    elif data.startswith((b"\x00\x00\xfe\xff", b"\x00\x00\x00<")):
        encoding = "utf-32-be"
    elif data.startswith((b"\xff\xfe", b"<\x00")):
        encoding = "utf-16-le"
    elif data.startswith((b"\xfe\xff", b"\x00<")):
        encoding = "utf-16-be"
    else:
        encoding = "latin-1"
    try:
        text = data.decode(encoding)
    except UnicodeError:
        return data, []
    declaration = re.match(r"\A(?:\ufeff|\xef\xbb\xbf)?<\?xml\s[^?]*\?>", text)
    if declaration is None:
        return data, []
    repairs = []

    def replace(match):
        old = match.group(3)
        new = "yes" if old == "true" else "no"
        repairs.append(f'standalone="{old}" → standalone="{new}"')
        return match.group(1) + match.group(2) + new + match.group(2)

    header = re.sub(r"(\bstandalone\s*=\s*)([\"'])(true|false)\2", replace, declaration.group())
    return (header + text[declaration.end():]).encode(encoding), repairs


def read_profiles(filename: str, *, repair_header: bool = False) -> ImportResult:
    path = Path(filename)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Fichier de configuration trop volumineux (maximum 10 Mo).")
    result = ImportResult()
    if path.suffix.lower() != ".xml":
        sites = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(sites, list):
            raise ValueError("Le fichier JSON doit contenir une liste de profils.")
        for site in sites:
            clean = normalize_profile(site)
            result.profiles.append(clean)
        return result
    data = path.read_bytes()
    # Reject declarations even for UTF-16/32 documents (ElementTree accepts them).
    upper = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("Les déclarations DTD et les entités XML ne sont pas acceptées.")
    corrected, repairs = repair_xml_header(data)
    if repairs:
        message = ('L’attribut standalone de la déclaration XML accepte uniquement "yes" ou "no", '
                   'pas "true" ou "false".\nCorrection proposée : ' + "; ".join(repairs))
        if not repair_header:
            raise XMLCorrectionRequired(message)
        data = corrected
        result.warnings.append("En-tête corrigé uniquement pour cet import : " + "; ".join(repairs)
                               + ". Le fichier d’origine n’a pas été modifié.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        line, column = error.position
        raise XMLImportError(f"Le fichier XML est invalide à la ligne {line}, colonne {column + 1}. "
                             f"Détail du lecteur XML : {error}") from error
    servers = root.findall(".//Server")
    if root.tag == "Server":
        servers = [root]
    if not servers:
        raise ValueError("Aucun serveur FileZilla trouvé dans ce XML.")
    parents = {child: parent for parent in root.iter() for child in parent}
    for number, server in enumerate(servers, 1):
        label = server.findtext("Name") or server.findtext("Host") or f"Serveur {number}"
        protocol = {"0": "FTP", "1": "SFTP", "4": "FTPS"}.get(server.findtext("Protocol", "0"))
        if not protocol:
            result.warnings.append(f"{label} : protocole FileZilla non pris en charge (FTPS implicite, etc.).")
            continue
        logon = server.findtext("Logontype", "1")
        user = server.findtext("User", "")
        if logon == "0":
            user = user or "anonymous"
        try:
            groups = []
            parent = parents.get(server)
            while parent is not None:
                if parent.tag == "Folder":
                    name = (parent.text or "").strip()
                    if name:
                        groups.insert(0, name)
                parent = parents.get(parent)
            remote_directory = server.findtext("RemoteDir", "")
            if remote_directory and not remote_directory.startswith(("/", ".")):
                result.warnings.append(f"{label} : chemin distant FileZilla encodé non importé ; choisissez-le dans Modifier.")
                remote_directory = ""
            site = normalize_profile(dict(host=server.findtext("Host", ""), user=user,
                port=server.findtext("Port", "22" if protocol == "SFTP" else "21"),
                protocol=protocol, name=label, key_file=server.findtext("Keyfile", ""),
                group=" / ".join(groups), local_directory=server.findtext("LocalDir", ""),
                remote_directory=remote_directory))
        except (ValueError, TypeError):
            result.warnings.append(f"{label} : hôte, utilisateur ou port invalide ; profil ignoré.")
            continue
        result.profiles.append(site)
        password = server.find("Pass")
        if password is not None and password.text:
            encoding = password.get("encoding", "plain")
            try:
                if encoding == "base64":
                    secret = base64.b64decode("".join(password.text.split()), validate=True).decode("utf-8")
                elif encoding == "plain":
                    secret = password.text
                else:
                    raise ValueError
                result.passwords[identity(site)] = secret
            except (ValueError, UnicodeError):
                result.warnings.append(f"{label} : mot de passe chiffré ou illisible ; saisissez-le à la connexion.")
    return result
