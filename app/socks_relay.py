"""
Локальный SOCKS5-релей без авторизации.

Chromium не умеет авторизовываться на SOCKS5-прокси (only HTTP(S) proxy auth
поддерживается нативно в browser.new_context(proxy=...)). Поэтому для
SOCKS5-прокси с логином/паролем поднимаем на 127.0.0.1 локальный SOCKS5-сервер
без авторизации: он сам логинится в апстрим и проксирует байты 1:1.
"""
import socket
import socketserver
import struct
import threading

_relays: dict[tuple, int] = {}
_lock = threading.Lock()


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("upstream/client closed connection")
        buf += chunk
    return buf


def _read_client_request(conn: socket.socket) -> bytes:
    """Читает SOCKS5-хендшейк и CONNECT-запрос от клиента (Chromium),
    отвечает 'без авторизации' и возвращает сырые байты CONNECT-запроса
    для ретрансляции апстриму."""
    nmethods = _recv_exact(conn, 2)[1]
    _recv_exact(conn, nmethods)
    conn.sendall(bytes([5, 0]))  # ver=5, method=0 (no auth)

    header = _recv_exact(conn, 4)
    ver, cmd, rsv, atyp = header
    if atyp == 1:
        addr_field = _recv_exact(conn, 4)
    elif atyp == 3:
        length = _recv_exact(conn, 1)
        addr_field = length + _recv_exact(conn, length[0])
    elif atyp == 4:
        addr_field = _recv_exact(conn, 16)
    else:
        raise ValueError(f"unsupported ATYP {atyp}")
    port_field = _recv_exact(conn, 2)
    return header + addr_field + port_field


def _connect_upstream(request: bytes, host: str, port: int, user: str, pwd: str) -> socket.socket:
    up = socket.create_connection((host, port), timeout=15)
    up.sendall(bytes([5, 1, 2]))  # предлагаем метод user/pass
    ver, method = _recv_exact(up, 2)
    if method != 2:
        up.close()
        raise ConnectionError("upstream proxy does not support user/pass auth")

    uname, pword = user.encode(), pwd.encode()
    auth_req = bytes([1, len(uname)]) + uname + bytes([len(pword)]) + pword
    up.sendall(auth_req)
    _, status = _recv_exact(up, 2)
    if status != 0:
        up.close()
        raise ConnectionError("upstream proxy auth failed")

    up.sendall(request)
    reply_header = _recv_exact(up, 4)
    if reply_header[1] != 0:
        up.close()
        raise ConnectionError(f"upstream CONNECT failed, rep={reply_header[1]}")
    atyp_reply = reply_header[3]
    if atyp_reply == 1:
        _recv_exact(up, 4 + 2)
    elif atyp_reply == 3:
        length = _recv_exact(up, 1)[0]
        _recv_exact(up, length + 2)
    elif atyp_reply == 4:
        _recv_exact(up, 16 + 2)
    return up


def _pipe(src: socket.socket, dst: socket.socket):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        host, port, user, pwd = self.server.upstream
        conn = self.request
        try:
            request = _read_client_request(conn)
        except (ConnectionError, ValueError, OSError):
            conn.close()
            return
        try:
            up = _connect_upstream(request, host, port, user, pwd)
        except (ConnectionError, OSError):
            conn.sendall(bytes([5, 1, 0, 1, 0, 0, 0, 0, 0, 0]))  # general failure
            conn.close()
            return

        conn.sendall(bytes([5, 0, 0, 1, 0, 0, 0, 0, 0, 0]))  # success

        t1 = threading.Thread(target=_pipe, args=(conn, up), daemon=True)
        t2 = threading.Thread(target=_pipe, args=(up, conn), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        conn.close()
        up.close()


class _RelayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_relay(host: str, port: int, user: str, pwd: str) -> int:
    """Поднимает (или переиспользует) локальный релей для данного апстрима.
    Возвращает локальный порт на 127.0.0.1."""
    key = (host, port, user, pwd)
    with _lock:
        if key in _relays:
            return _relays[key]
        server = _RelayServer(("127.0.0.1", 0), _Handler)
        server.upstream = key
        local_port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _relays[key] = local_port
        return local_port


def resolve_proxy(proxy: dict | None) -> dict | None:
    """Если это SOCKS5-прокси с логином/паролем, подменяет его на локальный
    релей без авторизации (Chromium не умеет авторизовываться на SOCKS5).
    Остальные прокси (http/https, либо socks5 без авторизации) не трогает."""
    if not proxy:
        return proxy
    server = proxy.get("server", "")
    if not server.startswith("socks5://") or "username" not in proxy:
        return proxy

    host_port = server[len("socks5://"):]
    host, port_s = host_port.rsplit(":", 1)
    local_port = start_relay(host, int(port_s), proxy["username"], proxy["password"])
    return {"server": f"socks5://127.0.0.1:{local_port}"}
