"""Shared protocol models."""
from dataclasses import dataclass, field
from smftp.i18n import tr


# Stored protocol value -> French label. Shown through protocol_label(), which translates it.
PROTOCOL_LABELS = {"SFTP": "SFTP", "FTPS": "FTPS explicite", "FTPSI": "FTPS implicite", "FTP": "FTP",
                   "SSH": "SSH (terminal)"}
DEFAULT_PORTS = {"SFTP": 22, "FTPS": 21, "FTPSI": 990, "FTP": 21, "SSH": 22}
# SSH profiles open a terminal, not the file panels.
TERMINAL_PROTOCOLS = {"SSH"}
# Protocols that authenticate over SSH (private key, host-key check).
SSH_PROTOCOLS = {"SFTP", "SSH"}
# FTP file-name encodings: stored value -> French label, shown through encoding_label().
# "auto" = UTF-8, switching to Latin-1 for the whole session when a listing is not valid UTF-8.
ENCODINGS = {"auto": "Automatique (UTF-8, sinon Latin-1)", "utf-8": "UTF-8",
             "latin-1": "ISO-8859-1 (Latin-1)", "iso-8859-15": "ISO-8859-15 (Latin-9, €)",
             "cp1252": "Windows-1252 (Europe occidentale)", "iso-8859-2": "ISO-8859-2 (Europe centrale)",
             "cp1250": "Windows-1250 (Europe centrale)", "cp1251": "Windows-1251 (cyrillique)",
             "koi8-r": "KOI8-R (russe)", "cp850": "CP850 (DOS Europe occidentale)", "cp437": "CP437 (DOS)",
             "shift_jis": "Shift_JIS (japonais)", "euc-kr": "EUC-KR (coréen)",
             "gbk": "GBK (chinois simplifié)", "big5": "Big5 (chinois traditionnel)"}


# Proxy kinds: stored value -> label, shown through proxy_label().
PROXY_TYPES = {"none": "Aucun", "socks5": "SOCKS5", "http": "HTTP CONNECT"}
DEFAULT_PROXY_PORTS = {"none": 1080, "socks5": 1080, "http": 8080}


def proxy_label(kind: str) -> str:
    """Label of a proxy kind shown to the user, in the interface language."""
    return tr("Aucun") if kind == "none" else PROXY_TYPES.get(kind, kind)


def normalize_offset(value) -> int | None:
    """Server time offset in minutes (-24 h to +24 h); None means automatic."""
    if value is None or value == "" or value == "auto":
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return max(-1440, min(1440, minutes))


def format_offset(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "−"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def normalize_encoding(value) -> str:
    """A codec Python knows, else "auto"."""
    import codecs
    text = str(value or "").strip()
    if not text or text.lower() == "auto":
        return "auto"
    try:
        name = codecs.lookup(text).name
    except LookupError:
        return "auto"
    return next((key for key in ENCODINGS if key != "auto" and codecs.lookup(key).name == name), text.lower())


def protocol_label(protocol: str) -> str:
    """Label shown to the user, in the interface language."""
    translated = {"FTPS": tr("FTPS explicite"), "FTPSI": tr("FTPS implicite"), "SSH": tr("SSH (terminal)")}
    return translated.get(protocol) or PROTOCOL_LABELS.get(protocol, protocol)


def encoding_label(encoding: str) -> str:
    """Label of a file-name encoding shown to the user, in the interface language."""
    translated = {
        "auto": tr("Automatique (UTF-8, sinon Latin-1)"),
        "iso-8859-15": tr("ISO-8859-15 (Latin-9, €)"),
        "cp1252": tr("Windows-1252 (Europe occidentale)"),
        "iso-8859-2": tr("ISO-8859-2 (Europe centrale)"),
        "cp1250": tr("Windows-1250 (Europe centrale)"),
        "cp1251": tr("Windows-1251 (cyrillique)"),
        "koi8-r": tr("KOI8-R (russe)"),
        "cp850": tr("CP850 (DOS Europe occidentale)"),
        "shift_jis": tr("Shift_JIS (japonais)"),
        "euc-kr": tr("EUC-KR (coréen)"),
        "gbk": tr("GBK (chinois simplifié)"),
        "big5": tr("Big5 (chinois traditionnel)"),
    }
    return translated.get(encoding) or ENCODINGS.get(encoding, encoding)


def is_terminal(protocol: str) -> bool:
    return protocol in TERMINAL_PROTOCOLS


def uses_ssh(protocol: str) -> bool:
    return protocol in SSH_PROTOCOLS


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    modified: float = 0
    permissions: str = ""


@dataclass
class Connection:
    host: str
    user: str
    protocol: str = "SFTP"
    port: int = 22
    password: str = field(default="", repr=False)
    key_file: str = ""
    remember: bool = False
    name: str = ""
    group: str = ""
    local_directory: str = ""
    remote_directory: str = ""
    timeout: int = 20
    passive: bool = True  # FTP / FTPS data channel: passive (PASV) or active (PORT).
    encoding: str = "auto"  # FTP / FTPS file names; see ENCODINGS.
    # Proxy in front of the server: "none", "socks5" or "http" (HTTP CONNECT).
    proxy_type: str = "none"
    proxy_host: str = ""
    proxy_port: int = 1080
    proxy_user: str = ""
    proxy_password: str = field(default="", repr=False)  # Vault only, never in sites.json.
    use_agent: bool = False  # SFTP / SSH: also offer the keys of Pageant or the OpenSSH agent.
    # FTP LIST times: server clock minus UTC, in minutes; None = detected with MDTM.
    time_offset: int | None = None

    @property
    def identity(self) -> str:
        return f"{self.protocol}://{self.user}@{self.host}:{self.port}"
