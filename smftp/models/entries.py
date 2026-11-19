"""Shared protocol models."""
from dataclasses import dataclass, field


# Stored protocol value -> label shown to the user.
PROTOCOL_LABELS = {"SFTP": "SFTP", "FTPS": "FTPS explicite", "FTPSI": "FTPS implicite", "FTP": "FTP"}
DEFAULT_PORTS = {"SFTP": 22, "FTPS": 21, "FTPSI": 990, "FTP": 21}
# FTP file-name encodings: stored value -> label. "auto" = UTF-8, switching to Latin-1
# for the whole session when a listing is not valid UTF-8.
ENCODINGS = {"auto": "Automatique (UTF-8, sinon Latin-1)", "utf-8": "UTF-8",
             "latin-1": "ISO-8859-1 (Latin-1)", "iso-8859-15": "ISO-8859-15 (Latin-9, €)",
             "cp1252": "Windows-1252 (Europe occidentale)", "iso-8859-2": "ISO-8859-2 (Europe centrale)",
             "cp1250": "Windows-1250 (Europe centrale)", "cp1251": "Windows-1251 (cyrillique)",
             "koi8-r": "KOI8-R (russe)", "cp850": "CP850 (DOS Europe occidentale)", "cp437": "CP437 (DOS)",
             "shift_jis": "Shift_JIS (japonais)", "euc-kr": "EUC-KR (coréen)",
             "gbk": "GBK (chinois simplifié)", "big5": "Big5 (chinois traditionnel)"}


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
    return PROTOCOL_LABELS.get(protocol, protocol)


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

    @property
    def identity(self) -> str:
        return f"{self.protocol}://{self.user}@{self.host}:{self.port}"
