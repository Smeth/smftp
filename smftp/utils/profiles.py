"""Validated FileZilla/JSON imports and OS credential-vault operations."""
import base64
from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET
from smftp.i18n import tr
from smftp.models.entries import Connection, DEFAULT_PORTS, normalize_encoding

def normalize_profile(site: dict) -> dict:
    if not isinstance(site, dict):
        raise ValueError(tr("Le profil doit être un objet."))
    protocol = site.get("protocol", "SFTP")
    host, user = site.get("host", ""), site.get("user", "")
    port = int(site.get("port", DEFAULT_PORTS.get(protocol, 21)))
    if protocol not in DEFAULT_PORTS or not 1 <= port <= 65535:
        raise ValueError(tr("Protocole ou port invalide."))
    if not isinstance(host, str) or not host.strip() or any(c in host for c in "/\\@\r\n\t "):
        raise ValueError(tr("Hôte invalide : saisissez une adresse sans préfixe ftp:// ni chemin."))
    if not isinstance(user, str) or not user.strip() or any(c in user for c in "\r\n"):
        raise ValueError(tr("Utilisateur manquant ou invalide."))
    return dict(host=host.strip(), user=user.strip(), protocol=protocol, port=port,
                key_file=str(site.get("key_file", "")), remember=bool(site.get("remember", False)),
                name=str(site.get("name", "")).strip() or host.strip(),
                group=str(site.get("group", "")).strip(),
                local_directory=str(site.get("local_directory", "")),
                remote_directory=str(site.get("remote_directory", "")),
                timeout=max(5, min(300, int(site.get("timeout", 20)))),
                passive=site.get("passive", True) not in (False, "false", "0", 0),
                encoding=normalize_encoding(site.get("encoding", "auto")))


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


def read_credential(key: str):
    import keyring
    return keyring.get_password("SMFTP", key)


def restore_credential(key: str, previous) -> None:
    """Put back the vault value seen before an import (None = no secret)."""
    import keyring
    if previous is None:
        if keyring.get_password("SMFTP", key) is not None:
            keyring.delete_password("SMFTP", key)
    else:
        keyring.set_password("SMFTP", key, previous)


def filezilla_encoding(server):
    """SMFTP encoding for a FileZilla server entry; None when the custom one is unknown."""
    kind = (server.findtext("EncodingType") or "Auto").strip()
    if kind in ("", "Auto"):
        return "auto"
    if kind == "UTF-8":
        return "utf-8"
    custom = (server.findtext("CustomEncoding") or "").strip()
    value = normalize_encoding(custom)
    return value if value != "auto" else None


def ignored_filezilla_options(server) -> list[str]:
    """FileZilla settings that SMFTP profiles do not carry over."""
    ignored = []
    offset = (server.findtext("TimezoneOffset") or "0").strip()
    if offset not in ("", "0"):
        ignored.append(tr("décalage horaire de {offset} min").format(offset=offset))
    encoding = filezilla_encoding(server)
    if encoding is None:
        ignored.append(tr("encodage {name}").format(name=(server.findtext("CustomEncoding") or "").strip() or tr("inconnu")))
    connections = (server.findtext("MaximumMultipleConnections") or "0").strip()
    if connections not in ("", "0"):
        ignored.append(tr("limite de {count} connexion(s)").format(count=connections))
    if (server.findtext("BypassProxy") or "0").strip() == "1":
        ignored.append(tr("contournement du proxy"))
    if (server.findtext("Logontype") or "").strip() in ("3", "4"):
        ignored.append(tr("authentification interactive ou par compte (saisie demandée à la connexion)"))
    if (server.findtext("Account") or "").strip():
        ignored.append(tr("compte FTP (ACCT)"))
    return ignored


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


def drop_network_paths(site: dict, label: str, result: "ImportResult") -> dict:
    """An imported file may come from someone else: a network path in it would make
    Windows authenticate to that server as soon as the profile is used."""
    from smftp.utils.paths import is_network_path
    fields = {"local_directory": tr("dossier local de départ"), "key_file": tr("clé SSH")}
    for field, description in fields.items():
        if is_network_path(site.get(field, "")):
            result.warnings.append(tr("{label} : {description} sur un chemin réseau ({path}) non importé(e) ; "
                                      "choisissez-le dans Modifier si vous lui faites confiance.").format(
                label=label, description=description, path=site[field]))
            site[field] = ""
    return site


def read_profiles(filename: str, *, repair_header: bool = False) -> ImportResult:
    path = Path(filename)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError(tr("Fichier de configuration trop volumineux (maximum 10 Mo)."))
    result = ImportResult()
    if path.suffix.lower() != ".xml":
        sites = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(sites, list):
            raise ValueError(tr("Le fichier JSON doit contenir une liste de profils."))
        for site in sites:
            clean = normalize_profile(site)
            result.profiles.append(drop_network_paths(clean, clean.get("name") or clean["host"], result))
        return result
    data = path.read_bytes()
    # Reject declarations even for UTF-16/32 documents (ElementTree accepts them).
    upper = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError(tr("Les déclarations DTD et les entités XML ne sont pas acceptées."))
    corrected, repairs = repair_xml_header(data)
    if repairs:
        message = tr('L’attribut standalone de la déclaration XML accepte uniquement "yes" ou "no", '
                     'pas "true" ou "false".\nCorrection proposée : {repairs}').format(repairs="; ".join(repairs))
        if not repair_header:
            raise XMLCorrectionRequired(message)
        data = corrected
        result.warnings.append(tr("En-tête corrigé uniquement pour cet import : {repairs}. "
                                  "Le fichier d’origine n’a pas été modifié.").format(repairs="; ".join(repairs)))
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        line, column = error.position
        raise XMLImportError(tr("Le fichier XML est invalide à la ligne {line}, colonne {column}. "
                                "Détail du lecteur XML : {detail}").format(line=line, column=column + 1, detail=error)) from error
    servers = root.findall(".//Server")
    if root.tag == "Server":
        servers = [root]
    if not servers:
        raise ValueError(tr("Aucun serveur FileZilla trouvé dans ce XML."))
    parents = {child: parent for parent in root.iter() for child in parent}
    for number, server in enumerate(servers, 1):
        label = server.findtext("Name") or server.findtext("Host") or tr("Serveur {number}").format(number=number)
        # FileZilla: 0 = FTP with explicit TLS when available, 6 = plain FTP only.
        code = (server.findtext("Protocol", "0") or "0").strip()
        protocol = {"0": "FTPS", "1": "SFTP", "3": "FTPSI", "4": "FTPS", "6": "FTP"}.get(code)
        protocol_note = {
            "0": tr("{label} : FileZilla utilisait TLS « si disponible » ; SMFTP l’impose (FTPS explicite). "
                    "Si le serveur ne propose pas TLS, choisissez FTP dans Modifier : la connexion ne sera pas chiffrée."),
            "6": tr("{label} : FTP non chiffré, comme dans FileZilla : mot de passe et fichiers circulent en clair."),
        }.get(code)
        if protocol_note:
            protocol_note = protocol_note.format(label=label)
        if not protocol:
            result.warnings.append(tr("{label} : protocole FileZilla non pris en charge (stockage cloud, etc.).").format(label=label))
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
                result.warnings.append(tr("{label} : chemin distant FileZilla encodé non importé ; choisissez-le dans Modifier.").format(label=label))
                remote_directory = ""
            site = normalize_profile(dict(host=server.findtext("Host", ""), user=user,
                port=server.findtext("Port", str(DEFAULT_PORTS[protocol])),
                protocol=protocol, name=label, key_file=server.findtext("Keyfile", ""),
                group=" / ".join(groups), local_directory=server.findtext("LocalDir", ""),
                remote_directory=remote_directory,
                passive=(server.findtext("PasvMode") or "").strip() != "MODE_ACTIVE",
                encoding=filezilla_encoding(server) or "auto"))
        except (ValueError, TypeError):
            result.warnings.append(tr("{label} : hôte, utilisateur ou port invalide ; profil ignoré.").format(label=label))
            continue
        result.profiles.append(drop_network_paths(site, label, result))
        if protocol_note:
            result.warnings.append(protocol_note)
        ignored = ignored_filezilla_options(server)
        if ignored:
            result.warnings.append(tr("{label} : options FileZilla non reprises : {options}.").format(label=label, options=", ".join(ignored)))
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
                result.warnings.append(tr("{label} : mot de passe chiffré ou illisible ; saisissez-le à la connexion.").format(label=label))
    return result
