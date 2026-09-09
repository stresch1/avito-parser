"""
Автоматическое решение капчи Яндекс SmartCaptcha (пазл-слайдер "Переместите слайдером
деталь, чтобы сложить пазл"), которую Avito показывает на блок-странице
"Доступ ограничен: проблема с IP", через сервис распознавания 2captcha.

Точные селекторы sitekey и способ подстановки токена (callback vs submit формы) —
best-effort, не проверены на реальной странице (см. README). Если решение не
срабатывает, парсер тихо откатывается на ручной режим (окно браузера ждёт,
пока капчу решат вручную).
"""
import asyncio
import os

from playwright.async_api import Page
from twocaptcha import TwoCaptcha

from .paths import data_dir

# Библиотека 2captcha ходит на 2captcha.com через requests, которая по умолчанию
# подхватывает системный прокси Windows (например, от VPN-клиентов вроде AdGuardVpn).
# Если такой прокси сейчас не поднят, запросы падают с ProxyError. Наши запросы к
# 2captcha не должны идти через него — исключаем этот хост из системного прокси.
os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY", ""), "2captcha.com"]))

CAPTCHA_KEY_FILE = data_dir() / "captcha_key.txt"


def get_api_key() -> str | None:
    if not CAPTCHA_KEY_FILE.exists():
        return None
    key = CAPTCHA_KEY_FILE.read_text(encoding="utf-8").strip()
    return key or None


def save_api_key(key: str) -> None:
    CAPTCHA_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAPTCHA_KEY_FILE.write_text(key.strip() + "\n", encoding="utf-8")


_FIND_SITEKEY_JS = """
() => {
    const el = document.querySelector(
        '[data-sitekey], .smart-captcha, #smart-captcha, [id*="smartcaptcha"], [class*="smart-captcha"]'
    );
    if (el) {
        const key = el.getAttribute('data-sitekey');
        if (key) return key;
    }
    const iframe = document.querySelector('iframe[src*="smartcaptcha.yandex"]');
    if (iframe) {
        try {
            const u = new URL(iframe.src);
            const key = u.searchParams.get('sitekey');
            if (key) return key;
        } catch (e) { /* некорректный URL — пропускаем */ }
    }
    return null;
}
"""


async def find_sitekey(page: Page) -> str | None:
    try:
        return await page.evaluate(_FIND_SITEKEY_JS)
    except Exception:
        return None


async def has_smartcaptcha(page: Page) -> bool:
    return (await find_sitekey(page)) is not None


async def solve_smartcaptcha(page: Page, api_key: str) -> dict:
    """Решает капчу через 2captcha и подставляет токен на страницу.
    Возвращает {"success": bool, "reason": str} — reason объясняет, на каком шаге
    остановились, если не получилось (для диагностики через progress_cb)."""
    sitekey = await find_sitekey(page)
    if not sitekey:
        return {"success": False, "reason": "sitekey не найден на странице"}

    solver = TwoCaptcha(api_key, defaultTimeout=180, pollingInterval=5)
    try:
        result = await asyncio.to_thread(solver.yandex_smart, sitekey=sitekey, url=page.url)
    except Exception as e:
        return {"success": False, "reason": f"ошибка 2captcha: {e}"}

    token = result.get("code") if isinstance(result, dict) else None
    if not token:
        return {"success": False, "reason": f"2captcha не вернул токен: {result}"}

    injected = await page.evaluate(
        """(token) => {
            let input = document.querySelector('input[name="smart-token"]');
            if (!input) {
                input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'smart-token';
                (document.querySelector('form') || document.body).appendChild(input);
            }
            input.value = token;

            const widget = document.querySelector('[data-callback]');
            const callbackName = widget ? widget.getAttribute('data-callback') : null;
            if (callbackName && typeof window[callbackName] === 'function') {
                window[callbackName](token);
                return 'callback';
            }

            const form = input.closest('form');
            if (form) {
                if (form.requestSubmit) form.requestSubmit(); else form.submit();
                return 'form-submit';
            }
            return 'no-form-no-callback';
        }""",
        token,
    )
    if injected in ("callback", "form-submit"):
        return {"success": True, "reason": f"токен подставлен через {injected}"}
    return {"success": False, "reason": f"токен получен, но не удалось его применить: {injected}"}
