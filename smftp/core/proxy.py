"""Outgoing connections through a SOCKS5 (RFC 1928 / 1929) or HTTP CONNECT proxy.

No extra dependency: both handshakes are a few bytes. The server name is sent to
the proxy, which resolves it (useful when only the proxy can resolve internal
names). The returned socket is connected to the server through the proxy and can
be used by ftplib (control and passive data channels) and paramiko (``sock=``).
"""
import base64
import ipaddress
import socket
import struct
from smftp.i18n import tr


class ProxyError(OSError):
    """The proxy refused or broke the tunnel; the message is shown to the user."""


def socks5_reason(code: int) -> str:
    texts = {
        1: tr("défaillance générale du serveur SOCKS"),
        2: tr("connexion interdite par les règles du proxy"),
        3: tr("réseau injoignable"),
        4: tr("hôte injoignable"),
        5: tr("connexion refusée par le serveur de destination"),
        6: tr("délai (TTL) expiré"),
        7: tr("commande non prise en charge"),
        8: tr("type d’adresse non pris en charge"),
    }
    return texts.get(code, tr("code {code}").format(code=code))


def proxy_password(connection) -> str:
    """The password typed in the form, else the one kept in the vault."""
    if connection.proxy_password or not connection.proxy_user:
        return connection.proxy_password
    try:
        from smftp.utils.profiles import proxy_key, read_credential
        return read_credential(proxy_key(connection.identity)) or ""
    except Exception:
        return ""


def uses_proxy(connection) -> bool:
    return getattr(connection, "proxy_type", "none") in ("socks5", "http") and bool(connection.proxy_host)


def describe(connection) -> str:
    kind = {"socks5": "SOCKS5", "http": "HTTP CONNECT"}[connection.proxy_type]
    return f"{kind} {connection.proxy_host}:{connection.proxy_port}"


def receive_exactly(sock, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ProxyError(tr("Le proxy a fermé la connexion pendant la négociation."))
        data += chunk
    return data


def socks5_handshake(sock, host: str, port: int, user: str = "", password: str = "") -> None:
    methods = b"\x00\x02" if user else b"\x00"
    sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
    version, method = receive_exactly(sock, 2)
    if version != 5:
        raise ProxyError(tr("Ce proxy ne répond pas en SOCKS5."))
    if method == 0x02:
        if not user:
            raise ProxyError(tr("Le proxy SOCKS5 exige un identifiant : renseignez l’utilisateur et le mot de passe du proxy."))
        name, secret = user.encode("utf-8"), password.encode("utf-8")
        if len(name) > 255 or len(secret) > 255:
            raise ProxyError(tr("Identifiant ou mot de passe du proxy trop long (255 octets maximum)."))
        sock.sendall(b"\x01" + bytes([len(name)]) + name + bytes([len(secret)]) + secret)
        _, status = receive_exactly(sock, 2)
        if status != 0:
            raise ProxyError(tr("Le proxy SOCKS5 a refusé l’identifiant ou le mot de passe."))
    elif method == 0xFF:
        raise ProxyError(tr("Le proxy SOCKS5 n’accepte aucune méthode d’authentification proposée."))
    elif method != 0x00:
        raise ProxyError(tr("Le proxy SOCKS5 demande une méthode d’authentification non prise en charge."))
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise ProxyError(tr("Nom d’hôte trop long pour SOCKS5."))
        target = b"\x03" + bytes([len(encoded)]) + encoded
    else:
        target = (b"\x01" if address.version == 4 else b"\x04") + address.packed
    sock.sendall(b"\x05\x01\x00" + target + struct.pack("!H", port))
    version, reply, _, kind = receive_exactly(sock, 4)
    if version != 5:
        raise ProxyError(tr("Réponse SOCKS5 invalide."))
    if reply != 0:
        raise ProxyError(tr("Le proxy SOCKS5 n’a pas pu joindre {host}:{port} : {reason}.").format(
            host=host, port=port, reason=socks5_reason(reply)))
    if kind == 1:
        receive_exactly(sock, 4)
    elif kind == 4:
        receive_exactly(sock, 16)
    elif kind == 3:
        receive_exactly(sock, receive_exactly(sock, 1)[0])
    else:
        raise ProxyError(tr("Réponse SOCKS5 invalide."))
    receive_exactly(sock, 2)


def http_connect(sock, host: str, port: int, user: str = "", password: str = "") -> None:
    target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if user:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        lines.append(f"Proxy-Authorization: Basic {token}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
    # Read byte by byte: whatever follows the header belongs to the server.
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise ProxyError(tr("Le proxy a fermé la connexion pendant la négociation."))
        header += chunk
        if len(header) > 16384:
            raise ProxyError(tr("Réponse du proxy HTTP trop longue."))
    status = header.split(b"\r\n", 1)[0].decode("latin-1").strip()
    parts = status.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise ProxyError(tr("Ce proxy ne répond pas en HTTP."))
    code = int(parts[1])
    if code == 407:
        raise ProxyError(tr("Le proxy HTTP exige un identifiant valide (407) : vérifiez l’utilisateur et le mot de passe du proxy."))
    if not 200 <= code < 300:
        raise ProxyError(tr("Le proxy HTTP a refusé la connexion à {host}:{port} : {status}").format(
            host=host, port=port, status=status))


def open_socket(connection, host: str, port: int, timeout) -> socket.socket:
    """A socket connected to host:port, through the profile's proxy if any."""
    if not uses_proxy(connection):
        return socket.create_connection((host, port), timeout)
    sock = socket.create_connection((connection.proxy_host, connection.proxy_port), timeout)
    try:
        handshake = socks5_handshake if connection.proxy_type == "socks5" else http_connect
        handshake(sock, host, port, connection.proxy_user, proxy_password(connection))
    except BaseException:
        sock.close()
        raise
    return sock
