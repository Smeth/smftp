"""Small loopback SOCKS5 (RFC 1928 / 1929) and HTTP CONNECT proxies for the tests."""
import base64
import socket
import struct
import threading


class ProxyServer:
    """Accepts clients on 127.0.0.1, opens the requested tunnel and relays bytes."""

    def __init__(self, user: str = "", password: str = ""):
        self.user, self.password = user, password
        self.targets = []  # (host, port) of each tunnel opened.
        self.refused = 0
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.sockets = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            client.settimeout(10)
            self.sockets.append(client)
            threading.Thread(target=self.handle, args=(client,), daemon=True).start()

    def handle(self, client):
        try:
            target = self.handshake(client)
        except (OSError, ValueError):
            client.close()
            return
        if target is None:
            client.close()
            return
        upstream = target
        self.sockets.append(upstream)
        client.settimeout(None)
        upstream.settimeout(None)
        threading.Thread(target=self.pump, args=(upstream, client), daemon=True).start()
        self.pump(client, upstream)

    @staticmethod
    def pump(source, destination):
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                destination.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, destination):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def connect_target(self, host, port):
        self.targets.append((host, port))
        return socket.create_connection((host, port), 10)

    def close(self):
        self.stopped.set()
        self.listener.close()
        for sock in self.sockets:
            try:
                sock.close()
            except OSError:
                pass
        self.thread.join(timeout=2)


def receive(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ValueError("closed")
        data += chunk
    return data


class Socks5Proxy(ProxyServer):
    def handshake(self, client):
        version, count = receive(client, 2)
        methods = receive(client, count)
        if self.user:
            if 2 not in methods:
                client.sendall(b"\x05\xff")
                return None
            client.sendall(b"\x05\x02")
            _, size = receive(client, 2)
            user = receive(client, size).decode()
            size = receive(client, 1)[0]
            password = receive(client, size).decode()
            if (user, password) != (self.user, self.password):
                self.refused += 1
                client.sendall(b"\x01\x01")
                return None
            client.sendall(b"\x01\x00")
        else:
            client.sendall(b"\x05\x00")
        _, command, _, kind = receive(client, 4)
        if kind == 1:
            host = socket.inet_ntoa(receive(client, 4))
        elif kind == 3:
            host = receive(client, receive(client, 1)[0]).decode()
        else:
            host = socket.inet_ntop(socket.AF_INET6, receive(client, 16))
        port = struct.unpack("!H", receive(client, 2))[0]
        try:
            upstream = self.connect_target(host, port)
        except OSError:
            client.sendall(b"\x05\x05\x00\x01" + bytes(6))
            return None
        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", 0))
        return upstream


class HTTPConnectProxy(ProxyServer):
    def handshake(self, client):
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            chunk = client.recv(1)
            if not chunk:
                return None
            header += chunk
        lines = header.decode().split("\r\n")
        method, target, _ = lines[0].split(" ")
        if method != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return None
        if self.user:
            expected = "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            given = next((line.split(":", 1)[1].strip() for line in lines[1:]
                          if line.lower().startswith("proxy-authorization:")), "")
            if given != expected:
                self.refused += 1
                client.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                               b"Proxy-Authenticate: Basic realm=\"test\"\r\n\r\n")
                return None
        host, port = target.rsplit(":", 1)
        upstream = self.connect_target(host.strip("[]"), int(port))
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        return upstream
