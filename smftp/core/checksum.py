"""Content fingerprints shared by transfer verification and synchronization."""
import hashlib
import re

# Preferred first. Names are hashlib names.
ALGORITHMS = ("sha256", "sha512", "sha1", "md5")
HEX_LENGTH = {"sha256": 64, "sha512": 128, "sha1": 40, "md5": 32}
LABELS = {"sha256": "SHA-256", "sha512": "SHA-512", "sha1": "SHA-1", "md5": "MD5"}


def normalize_algorithm(name: str) -> str | None:
    """Map server spellings (SHA-256, XSHA256, sha256sum…) to a hashlib name."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    key = key.removeprefix("x").removesuffix("sum")
    return key if key in HEX_LENGTH else None


def parse_digest(text: str, algorithm: str) -> str | None:
    """First token that is a hexadecimal digest of the expected length."""
    length = HEX_LENGTH[algorithm]
    for token in text.replace("\\", " ").split():
        if len(token) == length and re.fullmatch(r"[0-9a-fA-F]+", token):
            return token.lower()
    return None


def local_digest(path, algorithm: str, checkpoint=None, block: int = 1024 * 1024) -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        while chunk := stream.read(block):
            digest.update(chunk)
            if checkpoint:
                checkpoint()
    return digest.hexdigest()
