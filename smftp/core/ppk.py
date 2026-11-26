"""PuTTY private keys (.ppk, format versions 2 and 3) read into paramiko keys.

Format (PuTTY's sshpubk.c): text headers, the public blob and the private blob
in Base64, and a MAC over both. Encrypted files use AES-256-CBC; the key comes
from SHA-1 of the passphrase (version 2) or from Argon2 (version 3, provided by
the ``cryptography`` package that paramiko already depends on). RSA, Ed25519 and
ECDSA (NIST P-256/384/521) keys are supported; DSA keys are no longer accepted by
paramiko.
"""
import base64
import hashlib
import hmac
import io
import struct
from smftp.i18n import tr


class PPKError(ValueError):
    """A .ppk file that cannot be used; the message is shown to the user."""


class PPKPassphraseRequired(PPKError):
    pass


CURVES = {"nistp256": "SECP256R1", "nistp384": "SECP384R1", "nistp521": "SECP521R1"}


def is_ppk(path: str) -> bool:
    return str(path or "").lower().endswith(".ppk")


class Reader:
    """SSH wire format: uint32 length-prefixed strings and mpints."""

    def __init__(self, data: bytes):
        self.data, self.position = data, 0

    def string(self) -> bytes:
        if self.position + 4 > len(self.data):
            raise PPKError(tr("Clé PuTTY illisible : contenu tronqué."))
        size = struct.unpack(">I", self.data[self.position:self.position + 4])[0]
        start = self.position + 4
        if start + size > len(self.data):
            raise PPKError(tr("Clé PuTTY illisible : contenu tronqué."))
        self.position = start + size
        return self.data[start:start + size]

    def mpint(self) -> int:
        return int.from_bytes(self.string(), "big", signed=True)


def ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def parse_fields(text: str) -> tuple[int, str, dict, bytes, bytes]:
    lines = [line.rstrip("\r") for line in text.split("\n")]
    fields, public, private = {}, b"", b""
    version = algorithm = None
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip():
            continue
        key, separator, value = line.partition(": ")
        if not separator:
            raise PPKError(tr("Ce fichier n’est pas une clé PuTTY (.ppk) valide."))
        if key.startswith("PuTTY-User-Key-File-"):
            try:
                version = int(key.rsplit("-", 1)[1])
            except ValueError:
                raise PPKError(tr("Ce fichier n’est pas une clé PuTTY (.ppk) valide.")) from None
            algorithm = value.strip()
        elif key in ("Public-Lines", "Private-Lines"):
            try:
                count = int(value)
            except ValueError:
                raise PPKError(tr("Ce fichier n’est pas une clé PuTTY (.ppk) valide.")) from None
            if count < 0 or index + count > len(lines):
                raise PPKError(tr("Clé PuTTY illisible : contenu tronqué."))
            try:
                blob = base64.b64decode("".join(lines[index:index + count]), validate=True)
            except ValueError:
                raise PPKError(tr("Ce fichier n’est pas une clé PuTTY (.ppk) valide.")) from None
            index += count
            if key == "Public-Lines":
                public = blob
            else:
                private = blob
        else:
            fields[key] = value.strip()
    if version is None:
        raise PPKError(tr("Ce fichier n’est pas une clé PuTTY (.ppk) valide."))
    if version not in (2, 3):
        raise PPKError(tr("Clé PuTTY au format {version} non prise en charge : convertissez-la avec PuTTYgen "
                          "(Fichier → Enregistrer la clé privée).").format(version=version))
    return version, algorithm, fields, public, private


def argon2_key(fields: dict, passphrase: bytes, length: int) -> bytes:
    try:
        from cryptography.hazmat.primitives.kdf import argon2
    except ImportError:
        raise PPKError(tr("Cette clé PuTTY (format 3) est chiffrée avec Argon2, que cette installation ne sait pas "
                          "calculer. Mettez à jour le paquet cryptography, ou enregistrez la clé au format 2 "
                          "(PuTTYgen → Paramètres de la clé) ou OpenSSH.")) from None
    kinds = {"Argon2id": "Argon2id", "Argon2i": "Argon2i", "Argon2d": "Argon2d"}
    name = kinds.get(fields.get("Key-Derivation", ""))
    function = getattr(argon2, name, None) if name else None
    if function is None:
        raise PPKError(tr("Dérivation de clé PuTTY non prise en charge : {name}.").format(
            name=fields.get("Key-Derivation", "?")))
    try:
        salt = bytes.fromhex(fields["Argon2-Salt"])
        memory, passes = int(fields["Argon2-Memory"]), int(fields["Argon2-Passes"])
        lanes = int(fields["Argon2-Parallelism"])
    except (KeyError, ValueError):
        raise PPKError(tr("Ce fichier n’est pas une clé PuTTY (.ppk) valide.")) from None
    if not (8 * lanes <= memory <= 4 * 1024 * 1024 and 1 <= passes <= 1000 and 1 <= lanes <= 64):
        raise PPKError(tr("Paramètres Argon2 de la clé PuTTY hors limites."))
    return function(salt=salt, length=length, iterations=passes, lanes=lanes,
                    memory_cost=memory).derive(passphrase)


def decrypt(version: int, fields: dict, private: bytes, passphrase: bytes) -> tuple[bytes, bytes]:
    """(private blob, MAC key) for an encrypted file."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if version == 2:
        key = (hashlib.sha1(b"\0\0\0\0" + passphrase).digest() + hashlib.sha1(b"\0\0\0\1" + passphrase).digest())[:32]
        iv = bytes(16)
        mac_key = hashlib.sha1(b"putty-private-key-file-mac-key" + passphrase).digest()
    else:
        material = argon2_key(fields, passphrase, 80)
        key, iv, mac_key = material[:32], material[32:48], material[48:]
    if len(private) % 16:
        raise PPKError(tr("Clé PuTTY illisible : contenu tronqué."))
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(private) + decryptor.finalize(), mac_key


def crypto_key(algorithm: str, public: bytes, private: bytes):
    """A ``cryptography`` private key from the PuTTY blobs."""
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    pub, priv = Reader(public), Reader(private)
    if pub.string().decode("ascii", "replace") != algorithm:
        raise PPKError(tr("Clé PuTTY incohérente : type de clé différent dans le bloc public."))
    if algorithm == "ssh-rsa":
        e, n = pub.mpint(), pub.mpint()
        d, p, q, iqmp = priv.mpint(), priv.mpint(), priv.mpint(), priv.mpint()
        if p * q != n:
            raise PPKError(tr("Clé PuTTY incohérente : parties publique et privée différentes."))
        numbers = rsa.RSAPrivateNumbers(p, q, d, d % (p - 1), d % (q - 1), iqmp, rsa.RSAPublicNumbers(e, n))
        return numbers.private_key()
    if algorithm == "ssh-ed25519":
        # PuTTY writes the 32-byte RFC 8032 secret as a little-endian integer of fixed
        # length; a shorter or sign-padded form is accepted too. The public point decides.
        from cryptography.hazmat.primitives import serialization
        point, raw = pub.string(), priv.string()
        little = raw[:-1] if len(raw) == 33 and raw[-1] == 0 else raw  # Little-endian, fixed length.
        big = raw.lstrip(b"\0")  # Big-endian mpint form.
        for seed in (little.ljust(32, b"\0"), big.rjust(32, b"\0")[::-1]):
            if len(seed) != 32:
                continue
            key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
            if key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) == point:
                return key
        raise PPKError(tr("Clé PuTTY incohérente : parties publique et privée différentes."))
    if algorithm.startswith("ecdsa-sha2-"):
        curve = CURVES.get(pub.string().decode("ascii", "replace"))
        if curve is None:
            raise PPKError(tr("Courbe ECDSA non prise en charge : {name}.").format(name=algorithm))
        return ec.derive_private_key(priv.mpint(), getattr(ec, curve)())
    if algorithm == "ssh-dss":
        raise PPKError(tr("Les clés DSA ne sont plus acceptées : créez une clé Ed25519 ou RSA avec PuTTYgen."))
    raise PPKError(tr("Type de clé PuTTY non pris en charge : {name}.").format(name=algorithm))


def load_ppk(path: str, passphrase: str | None = None):
    """The paramiko key stored in a .ppk file (passphrase for an encrypted one)."""
    import paramiko
    from cryptography.hazmat.primitives import serialization
    with open(path, "rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise PPKError(tr("Ce fichier n’est pas une clé PuTTY (.ppk) valide."))
    version, algorithm, fields, public, private = parse_fields(raw.decode("utf-8", "replace"))
    encryption = fields.get("Encryption", "none")
    comment = fields.get("Comment", "")
    secret = (passphrase or "").encode("utf-8")
    if encryption == "aes256-cbc":
        if not passphrase:
            raise PPKPassphraseRequired(tr("Cette clé PuTTY est protégée : saisissez sa phrase secrète "
                                           "dans le champ Mot de passe / phrase secrète."))
        private, mac_key = decrypt(version, fields, private, secret)
    elif encryption == "none":
        mac_key = hashlib.sha1(b"putty-private-key-file-mac-key").digest() if version == 2 else b""
    else:
        raise PPKError(tr("Chiffrement de clé PuTTY non pris en charge : {name}.").format(name=encryption))
    data = b"".join(ssh_string(part.encode("utf-8") if isinstance(part, str) else part)
                    for part in (algorithm, encryption, comment, public, private))
    expected = fields.get("Private-MAC", "")
    digest = hmac.new(mac_key, data, hashlib.sha1 if version == 2 else hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, expected.lower()):
        if encryption != "none":
            raise PPKError(tr("Phrase secrète incorrecte pour cette clé PuTTY."))
        raise PPKError(tr("Clé PuTTY endommagée : son code d’intégrité (MAC) ne correspond pas."))
    key = crypto_key(algorithm, public, private)
    text = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                             serialization.NoEncryption()).decode("ascii")
    kind = {"ssh-rsa": paramiko.RSAKey, "ssh-ed25519": paramiko.Ed25519Key}.get(algorithm, paramiko.ECDSAKey)
    result = kind.from_private_key(io.StringIO(text))
    if result.asbytes() != public:
        raise PPKError(tr("Clé PuTTY incohérente : parties publique et privée différentes."))
    return result
