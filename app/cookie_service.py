"""
Интеграция с spfa.ru (Smart Price For Avito) — платный сервис "прогретых" cookies
для обхода блокировок Avito. В отличие от 2captcha (решение капчи постфактум, когда
блокировка уже показана), spfa.ru заранее отдаёт готовую валидную сессию (cookies +
user-agent), которая держится до ~12 часов при регулярной разблокировке.

Документация: https://spfa.ru/api/docs/
"""
import asyncio

import requests

from .paths import data_dir

API_BASE = "https://spfa.ru/api"
REQUEST_TIMEOUT = 20

COOKIE_SERVICE_KEY_FILE = data_dir() / "spfa_key.txt"


def get_api_key() -> str | None:
    if not COOKIE_SERVICE_KEY_FILE.exists():
        return None
    key = COOKIE_SERVICE_KEY_FILE.read_text(encoding="utf-8").strip()
    return key or None


def save_api_key(key: str) -> None:
    COOKIE_SERVICE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_SERVICE_KEY_FILE.write_text(key.strip() + "\n", encoding="utf-8")


def _post(path: str, payload: dict) -> dict:
    resp = requests.post(f"{API_BASE}{path}", json=payload, timeout=REQUEST_TIMEOUT)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["_status"] = resp.status_code
    return data


async def get_balance(api_key: str) -> dict:
    """{"success": True, "balance": 123.45} или {"success": False, "reason": ...}"""
    try:
        data = await asyncio.to_thread(_post, "/balance/", {"api_key": api_key})
    except Exception as e:
        return {"success": False, "reason": f"ошибка запроса к spfa.ru: {e}"}
    if not data.get("success"):
        return {"success": False, "reason": data.get("message") or f"HTTP {data.get('_status')}"}
    return {"success": True, "balance": data.get("balance")}


async def fetch_cookies(api_key: str, full_format: bool = False) -> dict:
    """{"success": True, "id": int, "cookies": {...}, "user_agent": str}
    или {"success": False, "reason": str} при ошибке."""
    try:
        data = await asyncio.to_thread(
            _post, "/cookies/", {"api_key": api_key, "full_format": full_format}
        )
    except Exception as e:
        return {"success": False, "reason": f"ошибка запроса к spfa.ru: {e}"}
    if not data.get("success"):
        return {"success": False, "reason": data.get("message") or f"HTTP {data.get('_status')}"}
    results = data.get("results") or {}
    cookies = results.get("cookies") or {}
    if not cookies:
        return {"success": False, "reason": "spfa.ru не вернул cookies"}
    return {
        "success": True,
        "id": results.get("id"),
        "cookies": cookies,
        "user_agent": results.get("user_agent"),
    }


async def unblock_cookies(api_key: str, cookie_id: int) -> dict:
    """{"success": True} или {"success": False, "reason": str}"""
    try:
        data = await asyncio.to_thread(_post, "/unblock/", {"api_key": api_key, "id": cookie_id})
    except Exception as e:
        return {"success": False, "reason": f"ошибка запроса к spfa.ru: {e}"}
    if not data.get("success"):
        return {"success": False, "reason": data.get("message") or f"HTTP {data.get('_status')}"}
    return {"success": True}


def cookies_to_playwright(cookies: dict, domain: str = ".avito.ru") -> list[dict]:
    """Конвертирует плоский dict cookies из spfa.ru в формат BrowserContext.add_cookies().
    spfa.ru иногда отдаёт null для отдельных cookies (например 'ft') — Playwright требует
    строковое value, такие пропускаем."""
    return [
        {"name": name, "value": value, "domain": domain, "path": "/"}
        for name, value in cookies.items()
        if isinstance(value, str)
    ]
