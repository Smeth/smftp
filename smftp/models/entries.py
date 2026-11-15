"""Shared protocol models."""
from dataclasses import dataclass, field


# Stored protocol value -> label shown to the user.
PROTOCOL_LABELS = {"SFTP": "SFTP", "FTPS": "FTPS explicite", "FTPSI": "FTPS implicite", "FTP": "FTP"}
DEFAULT_PORTS = {"SFTP": 22, "FTPS": 21, "FTPSI": 990, "FTP": 21}


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

    @property
    def identity(self) -> str:
        return f"{self.protocol}://{self.user}@{self.host}:{self.port}"
