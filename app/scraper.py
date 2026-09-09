"""
Скрапер Avito на Playwright.

ВАЖНО: Avito регулярно меняет вёрстку и защиту от ботов. Селекторы ниже основаны
на атрибутах data-marker, которые Avito использует для аналитики и обычно
меняются реже, чем CSS-классы, но полной гарантии стабильности нет.
Если парсинг перестанет находить поля — см. README, раздел "Если Avito сломал вёрстку".
"""
import asyncio
import random
import re
from datetime import datetime
from contextlib import suppress
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, BrowserContext

from .columns import PARAM_LABEL_MAP
from .proxies import get_proxy_cycler
from .socks_relay import resolve_proxy
from .captcha_solver import get_api_key as get_captcha_api_key, find_sitekey, solve_smartcaptcha
from .cookie_service import (
    get_api_key as get_cookie_service_key,
    fetch_cookies as fetch_spfa_cookies,
    unblock_cookies as unblock_spfa_cookies,
    cookies_to_playwright,
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36",
]

NAV_TIMEOUT = 30000
ITEM_CONCURRENCY = 4  # сколько объявлений открывать одновременно (вкладок в контексте)
CAPTCHA_SOLVE_ATTEMPTS = 3  # у 2captcha не 100% успех на Yandex SmartCaptcha, пробуем несколько раз
# Общий потолок на обработку одного объявления (включая возможное решение капчи).
# page.evaluate/page.reload не имеют собственного таймаута и могут зависнуть навсегда,
# подвесив всю партию параллельных вкладок — этот предел страхует от такого зависания.
ITEM_TIMEOUT = 1800
BATCH_PAUSE_EVERY = 100  # после стольких обработанных объявлений — пауза
BATCH_PAUSE_SECONDS = 25  # длительность паузы (сек)


class Cancelled(Exception):
    pass


async def _human_delay(a=1.2, b=3.0):
    await asyncio.sleep(random.uniform(a, b))


class Blocked(Exception):
    code = "AVITO_IP_BLOCKED"

    def __init__(self, message: str, rows: list[dict] | None = None,
                 pending_cards_by_link: dict[str, list[dict]] | None = None):
        super().__init__(message)
        self.rows = rows or []
        self.pending_cards_by_link = pending_cards_by_link or {}


class BrowserClosed(Exception):
    code = "BROWSER_CLOSED"

    def __init__(self, message: str, rows: list[dict] | None = None,
                 pending_cards_by_link: dict[str, list[dict]] | None = None):
        super().__init__(message)
        self.rows = rows or []
        self.pending_cards_by_link = pending_cards_by_link or {}


def _is_browser_closed_error(error: Exception) -> bool:
    message = str(error).lower()
    return "target page, context or browser has been closed" in message or "browser has been closed" in message


async def _is_blocked_page(page: Page) -> bool:
    title = (await page.title()) or ""
    if "Доступ ограничен" in title:
        return True
    try:
        body = await page.inner_text("body")
    except Exception:
        body = ""
    return "Доступ ограничен" in body or "проблема с IP" in body


async def _set_page_title(page: Page, title: str) -> None:
    """Меняет заголовок вкладки, чтобы было явно видно в её названии (например, в таскбаре),
    что сейчас идёт автоматический процесс и окно закрывать не нужно."""
    try:
        await page.evaluate("(t) => { document.title = t; }", title)
    except Exception:
        pass


async def _try_spfa_unblock(page: Page, progress_cb=None) -> bool:
    """Если подключен spfa.ru — сначала пробуем снять блокировку через него (разблокировка
    текущих cookies, а если это не поможет — свежие cookies с нуля). Это быстрее и надёжнее
    решения капчи постфактум через 2captcha. Возвращает True, если блокировка снята."""
    api_key = get_cookie_service_key()
    if not api_key:
        return False
    context = page.context

    cookie_id = getattr(context, "_spfa_cookie_id", None)
    if cookie_id is not None:
        if progress_cb:
            await progress_cb("Блок Avito — прошу spfa.ru разблокировать текущие cookies...")
        result = await unblock_spfa_cookies(api_key, cookie_id)
        if result["success"]:
            await asyncio.sleep(5)
            await page.reload(timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            if not await _is_blocked_page(page):
                if progress_cb:
                    await progress_cb("spfa.ru: cookies разблокированы, продолжаю парсинг...")
                return True
        elif progress_cb:
            await progress_cb(f"spfa.ru: разблокировка не удалась ({result['reason']})")

    if progress_cb:
        await progress_cb("Запрашиваю новые cookies у spfa.ru...")
    fetched = await fetch_spfa_cookies(api_key)
    if not fetched["success"]:
        if progress_cb:
            await progress_cb(f"spfa.ru: не удалось получить cookies ({fetched['reason']})")
        return False

    await context.clear_cookies()
    await context.add_cookies(cookies_to_playwright(fetched["cookies"]))
    context._spfa_cookie_id = fetched["id"]
    await page.reload(timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
    if not await _is_blocked_page(page):
        if progress_cb:
            await progress_cb("spfa.ru: применены новые cookies, продолжаю парсинг...")
        return True
    if progress_cb:
        await progress_cb("spfa.ru: новые cookies не сняли блокировку")
    return False


async def _goto_with_retry(page: Page, url: str, progress_cb=None, max_wait_s: int = 300,
                           should_continue_captcha_wait=None) -> None:
    """Открывает страницу. Если Avito показал блок/капчу — не перезапрашивает страницу заново
    (это сбрасывает капчу), а ждёт на этой же странице, пока капча не будет решена вручную
    в открытом окне браузера (headless=False)."""
    resp = await page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
    blocked_by_status = resp is not None and resp.status in (403, 429)
    if not blocked_by_status and not await _is_blocked_page(page):
        return

    await _set_page_title(page, "⏳ НЕ ЗАКРЫВАЙТЕ — идёт обход блокировки Avito...")

    if await _try_spfa_unblock(page, progress_cb):
        return

    # Одна автоматическая перезагрузка часто снимает мягкую блокировку без капчи
    # (антибот-проверка успевает отработать и выставить cookies за первый заход)
    await asyncio.sleep(2)
    await page.reload(timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
    if not await _is_blocked_page(page):
        return

    # Если настроен ключ 2captcha и на странице есть капча Яндекс SmartCaptcha —
    # пробуем решить её автоматически, прежде чем ждать ручного решения
    api_key = get_captcha_api_key()
    if not api_key:
        if progress_cb:
            await progress_cb("Ключ 2captcha не задан — автоматическое решение капчи пропущено")
    elif not (sitekey := await find_sitekey(page)):
        if progress_cb:
            await progress_cb("Капча SmartCaptcha на странице не обнаружена (sitekey не найден)")
    else:
        # 2captcha решает Яндекс SmartCaptcha не со 100% успехом — пробуем несколько раз
        # подряд (каждая попытка — новый пазл, шанс решения не одинаковый)
        for attempt in range(1, CAPTCHA_SOLVE_ATTEMPTS + 1):
            await _set_page_title(
                page, f"⏳ НЕ ЗАКРЫВАЙТЕ — решаю капчу через 2captcha ({attempt}/{CAPTCHA_SOLVE_ATTEMPTS})..."
            )
            if progress_cb:
                await progress_cb(f"Обнаружена капча (sitekey={sitekey}), решаю через 2captcha "
                                   f"(попытка {attempt}/{CAPTCHA_SOLVE_ATTEMPTS}, может занять до 3 минут)...")
            result = await solve_smartcaptcha(page, api_key)
            if progress_cb:
                await progress_cb(f"2captcha: {result['reason']}")
            if result["success"]:
                await asyncio.sleep(3)
                if not await _is_blocked_page(page):
                    if progress_cb:
                        await progress_cb("Капча решена автоматически, продолжаю парсинг...")
                    return
                elif progress_cb:
                    await progress_cb("Токен принят, но блокировка всё ещё показывается")
                break
            sitekey = await find_sitekey(page) or sitekey

    # До ручного решения снимаем блокировку шрифтов/видео (см. _new_context, картинки
    # с недавних пор не блокируются вовсе) и перезагружаем страницу — некоторые виды
    # блокировки (например экран "Доступ ограничен: проблема с IP" с попапом
    # "Подтверждение") грузятся заметно дольше обычной страницы — даём больше времени,
    # чем на обычную навигацию, и не глотаем ошибку молча.
    try:
        await page.context.unroute("**/*")
        await page.reload(timeout=NAV_TIMEOUT * 2, wait_until="load")
    except Exception as e:
        if progress_cb:
            await progress_cb(
                f"Не удалось перезагрузить страницу перед ручным решением капчи "
                f"({e}) — графика капчи может не отобразиться"
            )

    await _set_page_title(page, "🖐 РЕШИТЕ КАПЧУ ВРУЧНУЮ В ЭТОМ ОКНЕ (не закрывайте его)")
    if progress_cb:
        await progress_cb(
            "Avito показал блок/капчу — решите её в открывшемся окне браузера, "
            f"жду до {max_wait_s} сек. Если нужно больше времени, нажмите "
            "«Продолжить после капчи» в задаче."
        )
    poll_s = 3
    waited = 0
    while waited < max_wait_s or (should_continue_captcha_wait and should_continue_captcha_wait()):
        await asyncio.sleep(poll_s)
        waited += poll_s
        if not await _is_blocked_page(page):
            if progress_cb:
                await progress_cb("Блокировка снята, продолжаю парсинг...")
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
            return

    raise Blocked(
        "Avito заблокировал этот IP ('Доступ ограничен: проблема с IP') и капча не была решена "
        f"за {max_wait_s} сек. Подождите 30-60 минут перед повторной попыткой "
        "или используйте резидентный/мобильный прокси."
    )


async def _new_context(browser, proxy: dict | None, progress_cb=None) -> BrowserContext:
    user_agent = random.choice(USER_AGENTS)
    spfa_key = get_cookie_service_key()
    spfa_cookies = None
    if spfa_key:
        if progress_cb:
            await progress_cb("Запрашиваю стартовые cookies у spfa.ru...")
        fetched = await fetch_spfa_cookies(spfa_key)
        if fetched["success"]:
            spfa_cookies = fetched
            if fetched.get("user_agent"):
                user_agent = fetched["user_agent"]
            if progress_cb:
                await progress_cb("spfa.ru: стартовые cookies получены")
        elif progress_cb:
            await progress_cb(f"spfa.ru: не удалось получить стартовые cookies ({fetched['reason']}), "
                               "продолжаю без них")

    context = await browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
        proxy=resolve_proxy(proxy),
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    # Картинки не блокируем — экраны блокировки/капчи Avito (в т.ч. попап "Подтверждение"
    # у "Доступ ограничен: проблема с IP") сами используют картинки для отрисовки, и обрыв
    # их загрузки оставлял этот попап пустым даже после снятия блокировки перед ручным
    # решением (см. _goto_with_retry). Шрифты/видео парсингу не нужны — их обрываем.
    async def _block_heavy_resources(route):
        if route.request.resource_type in ("media", "font"):
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", _block_heavy_resources)
    if spfa_cookies:
        await context.add_cookies(cookies_to_playwright(spfa_cookies["cookies"]))
        context._spfa_cookie_id = spfa_cookies["id"]
    return context


def _clean_text(t: str | None) -> str:
    if not t:
        return ""
    return re.sub(r"\s+", " ", t).strip()


async def _extract_search_page_cards(page: Page) -> list[dict]:
    """Собирает базовые данные (url, заголовок, цена, город, продвижение) прямо с карточек
    страницы выдачи — без захода в каждое объявление. Используется как резерв: если открыть
    само объявление не получится (бан/ошибка), в таблице всё равно останется строка с этими
    полями, а не пропуск объявления."""
    cards = await page.query_selector_all('[data-marker="item"]')
    result = []
    seen = set()
    for card in cards:
        try:
            link_el = await card.query_selector('[itemprop="url"], a[data-marker="item-title"]')
            href = await link_el.get_attribute("href") if link_el else None
            if not href:
                continue
            url = urljoin("https://www.avito.ru", href.split("?")[0])
            if url in seen:
                continue
            seen.add(url)

            title_el = await card.query_selector('[itemprop="name"], [data-marker="item-title"]')
            title = _clean_text(await title_el.inner_text()) if title_el else ""

            price_el = await card.query_selector('[data-marker="item-price"], [itemprop="price"]')
            price_raw = ""
            if price_el:
                price_raw = (await price_el.get_attribute("content")) or (await price_el.inner_text())
            price = re.sub(r"[^\d]", "", price_raw or "")

            city_el = await card.query_selector('[data-marker="item-address"]')
            city = _clean_text(await city_el.inner_text()) if city_el else ""

            card_text = _clean_text(await card.inner_text())
            # Best-effort: точные названия бейджей на карточке не проверены вживую
            # (см. README, вёрстка Avito меняется) — если окажется неточно, поправим
            # под реальный текст бейджей на скриншоте карточки.
            xl = "XL" in card_text
            highlighted = "Выделено" in card_text
            promoted = any(w in card_text for w in ("Продвинуто", "Поднято", "TOP", "Premium", "VIP"))

            result.append({
                "url": url, "title": title, "price": price, "city": city,
                "xl": "Да" if xl else "",
                "highlighted": "Да" if highlighted else "",
                "promoted": "Да" if promoted else "",
            })
        except Exception:
            continue
    return result


MAX_SEARCH_PAGES = 500
MAX_PRICE_SPLIT_DEPTH = 6


def _with_price_range(search_url: str, pmin: int | None, pmax: int | None) -> str:
    parts = []
    if pmin is not None:
        parts.append(f"pmin={pmin}")
    if pmax is not None:
        parts.append(f"pmax={pmax}")
    if not parts:
        return search_url
    sep = "&" if "?" in search_url else "?"
    return f"{search_url}{sep}{'&'.join(parts)}"


PAGE_COUNT_SELECTOR = '[data-marker="page-title/count"]'
# Сколько объявлений можем реально не досчитаться против счётчика Avito и не считать
# это недобором — счётчик и фактическая выдача немного расходятся даже в норме
# (объявления снимают/добавляют, пока идёт сбор).
COUNT_TOLERANCE_ABS = 5
COUNT_TOLERANCE_REL = 0.01


async def _extract_page_count(page: Page) -> int | None:
    """Число объявлений, которое сама Avito показывает в заголовке выдачи (счётчик рядом
    с названием категории, например 'Новые экскаваторы 1 841'). Это единственный надёжный
    ориентир, сколько объявлений реально должно быть собрано — по форме последней страницы
    (пустая/неполная/с повторами) отличить настоящий конец каталога от обрыва пагинации
    Avito нельзя, оба выглядят одинаково."""
    try:
        el = await page.query_selector(PAGE_COUNT_SELECTOR)
        if not el:
            return None
        digits = re.sub(r"[^\d]", "", (await el.inner_text()) or "")
        return int(digits) if digits else None
    except Exception:
        return None


async def _collect_listing_cards_single(context: BrowserContext, search_url: str,
                                         progress_cb=None,
                                         should_continue_captcha_wait=None) -> tuple[list[dict], int | None]:
    """Идёт по страницам одной выдачи, пока на странице реально есть объявления. Не
    полагается на конкретный селектор кнопки 'следующая страница' — Avito меняет вёрстку
    пагинации чаще, чем факт наличия карточек [data-marker="item"] на странице.

    Возвращает (карточки, page_count) — page_count это то, что Avito показывает в счётчике
    выдачи для этого запроса (см. _extract_page_count), или None, если счётчик не нашли."""
    page = await context.new_page()
    all_cards: list[dict] = []
    seen_urls = set()
    page_count: int | None = None
    try:
        url = search_url
        page_num = 1
        while page_num <= MAX_SEARCH_PAGES:
            await _goto_with_retry(
                page, url, progress_cb,
                should_continue_captcha_wait=should_continue_captcha_wait,
            )
            await _human_delay()
            try:
                await page.wait_for_selector('[data-marker="item"]', timeout=NAV_TIMEOUT)
            except Exception:
                # страница пуста — конец выдачи
                break
            if page_num == 1:
                page_count = await _extract_page_count(page)
            page_cards = await _extract_search_page_cards(page)
            if not page_cards:
                break

            new_count = 0
            for c in page_cards:
                if c["url"] not in seen_urls:
                    seen_urls.add(c["url"])
                    all_cards.append(c)
                    new_count += 1

            if progress_cb:
                await progress_cb(f"Страница выдачи {page_num}: найдено {len(page_cards)} объявлений "
                                   f"(всего {len(all_cards)} из {page_count if page_count is not None else '?'})")
            if new_count == 0:
                # та же страница/повтор — дальше двигаться некуда
                break

            sep = "&" if "?" in search_url else "?"
            page_num += 1
            url = f"{search_url}{sep}p={page_num}"
        return all_cards, page_count
    finally:
        await page.close()


LISTING_RANGE_CONCURRENCY = 3  # сколько ценовых диапазонов дособирать одновременно


async def collect_listing_cards(context: BrowserContext, search_url: str,
                                 limit: int, progress_cb=None,
                                 should_continue_captcha_wait=None) -> list[dict]:
    """Собирает карточки по всей выдаче. Если после обхода всех страниц одного запроса
    собрано заметно меньше, чем показывает счётчик Avito (см. _extract_page_count) — Avito
    обрубил глубину пагинации раньше конца каталога. В этом случае автоматически делит
    выдачу по диапазону цены (pmin/pmax) пополам и дособирает каждую половину отдельно —
    так можно вытащить больше объявлений, чем Avito отдаёт за один непрерывный проход.
    Дочерние диапазоны обходятся параллельно (несколько вкладок), иначе на неровном
    распределении цен глубокое разбиение может растянуться надолго."""
    all_cards: list[dict] = []
    seen_urls: set[str] = set()
    sem = asyncio.Semaphore(LISTING_RANGE_CONCURRENCY)

    def add(cards: list[dict]) -> None:
        for c in cards:
            if c["url"] not in seen_urls:
                seen_urls.add(c["url"])
                all_cards.append(c)

    async def scrape_range(pmin: int | None, pmax: int | None, depth: int) -> None:
        if limit and len(all_cards) >= limit:
            return
        url = _with_price_range(search_url, pmin, pmax)
        async with sem:
            cards, page_count = await _collect_listing_cards_single(
                context, url, progress_cb,
                should_continue_captcha_wait=should_continue_captcha_wait,
            )
        add(cards)
        got_in_range = len({c["url"] for c in cards})
        if page_count is None or (limit and len(all_cards) >= limit):
            return
        missing = page_count - got_in_range
        tolerance = max(COUNT_TOLERANCE_ABS, int(page_count * COUNT_TOLERANCE_REL))
        if missing <= tolerance:
            return
        if depth >= MAX_PRICE_SPLIT_DEPTH:
            if progress_cb:
                await progress_cb(f"⚠ В диапазоне цены {pmin or 0}–{pmax if pmax is not None else '∞'} "
                                   f"не хватает {missing} объявлений (из {page_count}), но глубина "
                                   f"разбиения исчерпана — дальше не делю, эти объявления будут пропущены")
            return
        prices = sorted({int(c["price"]) for c in cards if c.get("price", "").isdigit()})
        if len(prices) < 2:
            # нечем делить дальше (в этом диапазоне все объявления с одной ценой) —
            # придётся смириться с недобором именно здесь
            if progress_cb:
                await progress_cb(f"⚠ В диапазоне цены {pmin or 0}–{pmax if pmax is not None else '∞'} "
                                   f"не хватает {missing} объявлений (из {page_count}), но делить дальше "
                                   f"нечем (все с одной ценой) — эти объявления будут пропущены")
            return
        mid = prices[len(prices) // 2]
        lo = pmin if pmin is not None else 0
        if progress_cb:
            await progress_cb(f"Собрано {got_in_range} из {page_count} по счётчику Avito — "
                               f"делю диапазон цены {lo}–{pmax if pmax is not None else '∞'} "
                               f"пополам (граница {mid}), чтобы добрать остальное")
        await asyncio.gather(
            scrape_range(lo, mid, depth + 1),
            scrape_range(mid + 1, pmax, depth + 1),
        )

    await scrape_range(None, None, 0)
    if limit and len(all_cards) >= limit:
        all_cards = all_cards[:limit]
    return all_cards


_EXTRACT_ITEM_JS = """
() => {
    function txt(sel) {
        const el = document.querySelector(sel);
        return el ? el.innerText.replace(/\\s+/g, ' ').trim() : '';
    }
    const views_el = document.querySelector('[data-marker="item-view/total-views"]');
    const seller_link_el = document.querySelector(
        '[data-marker="seller-link/link"], [data-marker="seller-info/link"], ' +
        'a[data-marker="seller-info/name"], [data-marker="seller-info/name"] a'
    );
    const geo_el = document.querySelector('[itemprop="geo"], [data-marker="map"]');

    // Тип продавца: бейдж показывается не всегда (у частных лиц его часто просто нет
    // в вёрстке) — пробуем несколько селекторов бейджа
    const sellerTypeRaw = txt('[data-marker="seller-info/label"]')
        || txt('[data-marker="seller-info/badge"]')
        || txt('[data-marker="seller-info/type"]');

    // Адрес: пробуем несколько известных селекторов, а если ни один не сработал —
    // достаём его из JSON-LD (структурированные данные для поисковиков, которые
    // Avito обычно меняет реже, чем вёрстку)
    let address = txt('[data-marker="item-view/item-address"]')
        || txt('[itemprop="address"]')
        || txt('[data-marker="item-address"]')
        || txt('.style-item-address-string');
    let addressSource = 'dom';
    if (!address) {
        for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
            try {
                const data = JSON.parse(script.textContent);
                const items = Array.isArray(data) ? data : [data];
                for (const item of items) {
                    const addr = item && item.address;
                    if (addr) {
                        address = [addr.streetAddress, addr.addressLocality, addr.addressRegion]
                            .filter(Boolean).join(', ');
                        addressSource = 'json-ld';
                        break;
                    }
                }
            } catch (e) { /* не JSON-LD или сломанный — пропускаем */ }
            if (address) break;
        }
    }

    const viewsFullText = views_el ? views_el.innerText.trim() : '';
    const totalMatch = viewsFullText.match(/\\d[\\d\\s]*/);
    const viewsTotal = totalMatch ? totalMatch[0].replace(/\\s/g, '') : '';

    // "Сегодня" может быть не в тексте самого элемента, а в соседнем блоке или в
    // title/aria-label тултипа (иконка со всплывающей подсказкой) — проверяем всё подряд
    let viewsToday = '';
    const todayRe = /(\\d[\\d\\s]*)\\s*(?:за\\s+)?сегодня/i;
    if (views_el) {
        const container = views_el.closest('[class*="stats"]') || views_el.parentElement || views_el;
        const containerMatch = (container.innerText || '').match(todayRe);
        if (containerMatch) {
            viewsToday = containerMatch[1].replace(/\\s/g, '');
        } else {
            for (const el of [views_el, ...views_el.querySelectorAll('*')]) {
                const attrText = (el.getAttribute('title') || '') + ' ' + (el.getAttribute('aria-label') || '');
                const attrMatch = attrText.match(todayRe);
                if (attrMatch) {
                    viewsToday = attrMatch[1].replace(/\\s/g, '');
                    break;
                }
            }
        }
    }

    const params = {};
    document.querySelectorAll('[data-marker="item-view/item-params"] li, [data-marker="item-params"] li')
        .forEach(li => {
            const text = li.innerText.replace(/\\s+/g, ' ').trim();
            const idx = text.indexOf(':');
            if (idx > -1) {
                params[text.slice(0, idx).trim().toLowerCase()] = text.slice(idx + 1).trim();
            }
        });
    return {
        title: txt('[data-marker="item-view/title-info"]'),
        price_raw: txt('[data-marker="item-view/item-price"]'),
        address: address || '',
        address_source: addressSource,
        seller_name: txt('[data-marker="seller-info/name"]'),
        seller_type: sellerTypeRaw,
        description: txt('[data-marker="item-view/item-description"]'),
        date_posted: txt('[data-marker="item-view/item-date"]'),
        views: viewsTotal || viewsFullText,
        views_today: viewsToday,
        seller_link: seller_link_el ? seller_link_el.getAttribute('href') : '',
        breadcrumbs: Array.from(document.querySelectorAll(
            '[data-marker="breadcrumbs"] a, nav[aria-label="Хлебные крошки"] a'
        )).map(e => e.innerText.trim()),
        images: Array.from(document.querySelectorAll('[data-marker="item-view/gallery"] img'))
            .map(e => e.getAttribute('src') || e.getAttribute('data-src'))
            .filter(Boolean),
        lat: geo_el ? (geo_el.getAttribute('data-lat') || '') : '',
        lon: geo_el ? (geo_el.getAttribute('data-lon') || '') : '',
        params: params,
    };
}
"""


_COMPANY_MARKERS = ("компан", "дилер", "магазин", "автосалон", "официальный")


async def scrape_item(page: Page, url: str, index: int, card: dict | None = None,
                      progress_cb=None,
                      should_continue_captcha_wait=None) -> dict:
    """Открывает объявление и вытаскивает все поля одним JS-запросом (page.evaluate)
    вместо десятка отдельных обращений к странице — так намного быстрее.
    card — данные с карточки выдачи (XL/Выделено/Продвижение видны только там,
    на странице самого объявления таких бейджей нет).

    Принимает уже открытую вкладку (page) вместо того, чтобы открывать и закрывать
    свою на каждый вызов — вкладки переиспользуются из пула в run_scrape, так дешевле
    (не тратимся на создание/закрытие вкладки на каждое объявление)."""
    await _goto_with_retry(
        page, url, progress_cb=progress_cb,
        should_continue_captcha_wait=should_continue_captcha_wait,
    )
    await _human_delay(0.4, 1.0)

    item_id_match = re.search(r"(\d+)$", url.rstrip("/"))
    item_id = item_id_match.group(1) if item_id_match else ""

    data = await page.evaluate(_EXTRACT_ITEM_JS)

    price = re.sub(r"[^\d]", "", data["price_raw"] or "")
    address = data["address"] or ""
    seller_type_raw = data["seller_type"] or ""
    seller_name = data["seller_name"] or ""
    breadcrumbs = data["breadcrumbs"] or []
    images = data["images"] or []
    seller_link = urljoin(url, data["seller_link"]) if data["seller_link"] else ""

    # Бейдж типа продавца показывается не всегда — отсутствие бейджа на Avito
    # означает частное лицо, а не "неизвестно"
    is_company = any(m in seller_type_raw.lower() for m in _COMPANY_MARKERS)
    if seller_type_raw:
        seller_type = seller_type_raw
    else:
        seller_type = "Частное лицо" if seller_name else ""

    params = {}
    for key, value in (data["params"] or {}).items():
        mapped = PARAM_LABEL_MAP.get(key)
        if mapped:
            params[mapped] = value

    card = card or {}
    row = {
        "Номер поисковой выдачи": index,
        "URL": url,
        "Заголовок": data["title"] or "",
        "Категория": breadcrumbs[0] if len(breadcrumbs) > 0 else "",
        "Подкатегория": breadcrumbs[1] if len(breadcrumbs) > 1 else "",
        "Подкатегория 2": breadcrumbs[2] if len(breadcrumbs) > 2 else "",
        "Подкатегория 3": breadcrumbs[3] if len(breadcrumbs) > 3 else "",
        "Подкатегория 4": breadcrumbs[4] if len(breadcrumbs) > 4 else "",
        "Цена": price,
        "Цена без скидки": "",
        "Валюта": "₽" if price else "",
        "НДС": "",
        "XL": card.get("xl", ""),
        "Выделено": card.get("highlighted", ""),
        "Продвижение": card.get("promoted", ""),
        "Номер объявления": item_id,
        "Ссылка по номеру": f"https://www.avito.ru/{item_id}" if item_id else "",
        "Просмотры всего": data["views"] or "",
        "Просмотры сегодня": data["views_today"] or "",
        "Дата объявления": data["date_posted"] or "",
        "Дата сбора": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "Город": address.split(",")[0] if address else "",
        "Полный адрес": address,
        "Широта": data["lat"] or "",
        "Долгота": data["lon"] or "",
        "Продавец": seller_name,
        "Тип продавца": seller_type,
        "Компания": seller_name if is_company else "",
        "Контактное лицо": "" if is_company else seller_name,
        "Ссылка на продавца": seller_link,
        "Описание полное": data["description"] or "",
        "Ссылки на картинки": ", ".join(images),
    }
    row.update(params)
    return row


async def run_scrape(links: list[str], mode: str, limit_per_link: int,
                      progress_cb=None, should_cancel=None, state_cb=None,
                      resume_cards_by_link: dict[str, list[dict]] | None = None,
                      should_continue_captcha_wait=None) -> list[dict]:
    """Главная точка входа. progress_cb(str) — коллбек прогресса, should_cancel() -> bool."""
    proxy_cycler = get_proxy_cycler()
    rows: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, channel="chrome")
        try:
            for link in links:
                if should_cancel and should_cancel():
                    raise Cancelled()
                proxy = next(proxy_cycler) if proxy_cycler else None
                context = await _new_context(browser, proxy, progress_cb)
                try:
                    if resume_cards_by_link and link in resume_cards_by_link:
                        cards = resume_cards_by_link[link]
                        if progress_cb:
                            await progress_cb(f"Продолжаю недособранные объявления: {len(cards)} шт. ({link})")
                    else:
                        if progress_cb:
                            await progress_cb(f"Собираю список объявлений: {link}")
                        cards = await collect_listing_cards(
                            context, link, limit_per_link, progress_cb,
                            should_continue_captcha_wait=should_continue_captcha_wait,
                        )
                    total = len(cards)
                    done_count = 0
                    link_rows: list[dict] = []
                    completed_urls: set[str] = set()
                    pause_event = asyncio.Event()
                    pause_event.set()  # изначально не на паузе

                    async def save_state() -> None:
                        if not state_cb:
                            return
                        pending = [c for c in cards if c["url"] not in completed_urls]
                        await state_cb(link, pending, rows + link_rows)

                    await save_state()

                    # Пул вкладок вместо открытия+закрытия новой на каждое объявление —
                    # заодно сама очередь ограничивает параллелизм (как раньше семафор):
                    # если все вкладки заняты, следующий page_pool.get() просто ждёт.
                    page_pool: asyncio.Queue[Page] = asyncio.Queue()
                    for _ in range(ITEM_CONCURRENCY):
                        page_pool.put_nowait(await context.new_page())

                    async def process_card(card: dict, i: int) -> dict:
                        nonlocal done_count
                        await pause_event.wait()
                        page = await page_pool.get()
                        try:
                            if should_cancel and should_cancel():
                                raise Cancelled()
                            item_url = card["url"]
                            try:
                                try:
                                    row = await asyncio.wait_for(
                                        scrape_item(
                                            page, item_url, i, card,
                                            progress_cb=progress_cb,
                                            should_continue_captcha_wait=should_continue_captcha_wait,
                                        ),
                                        timeout=ITEM_TIMEOUT,
                                    )
                                except (Blocked, BrowserClosed):
                                    raise
                                except Exception:
                                    await _human_delay(2.0, 4.0)
                                    row = await asyncio.wait_for(
                                        scrape_item(
                                            page, item_url, i, card,
                                            progress_cb=progress_cb,
                                            should_continue_captcha_wait=should_continue_captcha_wait,
                                        ),
                                        timeout=ITEM_TIMEOUT,
                                    )  # одна повторная попытка
                            except (Blocked, BrowserClosed):
                                raise
                            except Exception as e:
                                if _is_browser_closed_error(e):
                                    raise BrowserClosed("Окно браузера было закрыто пользователем") from e
                                # не получилось и со второй попытки — используем данные
                                # с карточки выдачи, чтобы не терять строку целиком
                                if progress_cb:
                                    await progress_cb(
                                        f"Не удалось открыть {item_url} ({e}), "
                                        "использую данные с карточки выдачи"
                                    )
                                row = {
                                    "Номер поисковой выдачи": i,
                                    "URL": item_url,
                                    "Заголовок": card.get("title", ""),
                                    "Цена": card.get("price", ""),
                                    "Валюта": "₽" if card.get("price") else "",
                                    "XL": card.get("xl", ""),
                                    "Выделено": card.get("highlighted", ""),
                                    "Продвижение": card.get("promoted", ""),
                                    "Город": card.get("city", ""),
                                    "Дата сбора": datetime.now().strftime("%d.%m.%Y %H:%M"),
                                }
                            link_rows.append(row)
                            completed_urls.add(item_url)
                            done_count += 1
                            if progress_cb:
                                await progress_cb(f"Обработано {done_count}/{total} ({link})")
                            await save_state()
                            await _human_delay(0.4, 1.2)

                            if done_count % BATCH_PAUSE_EVERY == 0 and done_count < total:
                                pause_event.clear()
                                if progress_cb:
                                    await progress_cb(
                                        f"Пауза {BATCH_PAUSE_SECONDS} сек после {done_count} объявлений..."
                                    )
                                await asyncio.sleep(BATCH_PAUSE_SECONDS)
                                pause_event.set()

                            return row
                        finally:
                            await page_pool.put(page)

                    tasks = [
                        asyncio.create_task(process_card(card, i))
                        for i, card in enumerate(cards, start=1)
                    ]
                    try:
                        await asyncio.gather(*tasks)
                    except (Blocked, BrowserClosed) as e:
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        for task in tasks:
                            with suppress(asyncio.CancelledError, Exception):
                                await task
                        await save_state()
                        pending = [c for c in cards if c["url"] not in completed_urls]
                        raise type(e)(str(e), rows + link_rows, {link: pending}) from e
                    rows.extend(link_rows)
                except (Blocked, BrowserClosed) as e:
                    if not e.rows:
                        e.rows = rows
                    raise
                except Exception as e:
                    if _is_browser_closed_error(e):
                        raise BrowserClosed("Окно браузера было закрыто пользователем", rows) from e
                    raise
                finally:
                    # если браузер уже закрыт/упал, close() тоже упадёт — это не должно
                    # обнулять уже собранные rows (иначе теряется весь прогресс)
                    try:
                        await context.close()
                    except Exception:
                        pass
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    return rows
