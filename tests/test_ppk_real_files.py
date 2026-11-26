"""PuTTY keys written by PuTTYgen itself (not by our own test generator)."""
from pathlib import Path
import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from smftp.core.ppk import PPKError, load_ppk

FIXTURES = Path(__file__).parent / "fixtures" / "putty"
PASSPHRASE = "Test Passphrase"


def public_numbers(paramiko_key):
    inner = getattr(paramiko_key, "key", None) or getattr(paramiko_key, "signing_key", None)
    return inner.public_key().public_numbers()


@pytest.mark.parametrize("name,reference,encrypted", [
    ("rsa-2048-format-2.ppk", "rsa-2048.pem", False),
    ("rsa-2048-encrypted-format-2.ppk", "rsa-2048.pem", True),       # AES + SHA-1 derivation
    ("rsa-2048-encrypted-format-3.ppk", "rsa-2048.pem", True),       # AES + Argon2id
    ("ecdsa-sha2-nistp256-encrypted-format-3.ppk", "ecdsa-sha2-nistp256.pem", True),
])
def test_puttygen_keys_give_the_same_key_as_openssl(name, reference, encrypted):
    key = load_ppk(str(FIXTURES / name), PASSPHRASE if encrypted else None)
    expected = load_pem_private_key((FIXTURES / reference).read_bytes(), None).public_key().public_numbers()
    assert public_numbers(key) == expected


def test_wrong_or_missing_passphrase_and_damaged_file_are_refused():
    with pytest.raises(PPKError):
        load_ppk(str(FIXTURES / "rsa-2048-encrypted-format-3.ppk"), "Not Test Passphrase")
    with pytest.raises(PPKError):
        load_ppk(str(FIXTURES / "rsa-2048-encrypted-format-3.ppk"), None)
    with pytest.raises(PPKError):
        load_ppk(str(FIXTURES / "test-invalid-private-mac.ppk"), None)
