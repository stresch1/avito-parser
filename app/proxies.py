import itertools

from .paths import data_dir

PROXIES_FILE = data_dir() / "proxies.txt"

_cycle = None


def _parse_line(line: str) -> dict | None:
    """Формат строки: [scheme://]host:port:login:pass  или  [scheme://]host:port
    scheme по умолчанию http, поддерживается также socks5"""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    scheme = "http"
    if "://" in line:
        scheme, line = line.split("://", 1)
    parts = line.split(":")
    if len(parts) == 2:
        host, port = parts
        return {"server": f"{scheme}://{host}:{port}"}
    if len(parts) == 4:
        host, port, user, pwd = parts
        return {"server": f"{scheme}://{host}:{port}", "username": user, "password": pwd}
    return None


def load_proxies() -> list[dict]:
    if not PROXIES_FILE.exists():
        return []
    proxies = []
    for line in PROXIES_FILE.read_text(encoding="utf-8").splitlines():
        p = _parse_line(line)
        if p:
            proxies.append(p)
    return proxies


def save_proxies(raw_text: str):
    PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROXIES_FILE.write_text(raw_text.strip() + "\n", encoding="utf-8")


def get_proxy_cycler():
    proxies = load_proxies()
    if not proxies:
        return None
    return itertools.cycle(proxies)
