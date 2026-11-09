"""Shared protocol models."""
from dataclasses import dataclass, field


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
