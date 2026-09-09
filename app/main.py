import asyncio
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .columns import COLUMNS
from .exporter import export_rows, EXPORTS_DIR
from .proxies import load_proxies, save_proxies
from .captcha_solver import get_api_key as get_captcha_api_key, save_api_key as save_captcha_api_key
from .cookie_service import (
    get_api_key as get_cookie_service_key,
    save_api_key as save_cookie_service_key,
    get_balance as get_spfa_balance,
)
from .scraper import run_scrape, Cancelled, Blocked, BrowserClosed
from .paths import data_dir, resource_path

STATIC_DIR = resource_path("static")

app = FastAPI(title="Парсинг Авито")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_cancel_flags: dict[str, bool] = {}
_captcha_wait_until: dict[str, float] = {}


@app.on_event("startup")
async def on_startup():
    await db.init_db()


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


class NewTask(BaseModel):
    source: str = "avito"
    mode: str = "full"
    links: list[str]
    limit_per_link: int = 0
    only_region: bool = True
    merge_file: bool = True
    email: str | None = None
    columns: list[str] | None = None


@app.get("/api/columns")
async def get_columns():
    return {"columns": COLUMNS}


@app.get("/api/proxies")
async def get_proxies():
    proxies = load_proxies()
    raw = ""
    try:
        raw = (data_dir() / "proxies.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    return {"count": len(proxies), "raw": raw}


class ProxiesPayload(BaseModel):
    raw: str


@app.post("/api/proxies")
async def set_proxies(payload: ProxiesPayload):
    save_proxies(payload.raw)
    return {"count": len(load_proxies())}


@app.get("/api/captcha-key")
async def get_captcha_key():
    key = get_captcha_api_key()
    # ключ не отдаём целиком обратно на фронт — только замаскированный хвост,
    # чтобы не светить его в devtools/логах браузера
    masked = ("•" * 8 + key[-4:]) if key else ""
    return {"configured": bool(key), "masked": masked}


class CaptchaKeyPayload(BaseModel):
    api_key: str


@app.post("/api/captcha-key")
async def set_captcha_key(payload: CaptchaKeyPayload):
    save_captcha_api_key(payload.api_key)
    return {"configured": True}


@app.get("/api/cookie-service-key")
async def get_cookie_service_key_route():
    key = get_cookie_service_key()
    masked = ("•" * 8 + key[-4:]) if key else ""
    balance = None
    if key:
        result = await get_spfa_balance(key)
        if result["success"]:
            balance = result["balance"]
    return {"configured": bool(key), "masked": masked, "balance": balance}


class CookieServiceKeyPayload(BaseModel):
    api_key: str


@app.post("/api/cookie-service-key")
async def set_cookie_service_key(payload: CookieServiceKeyPayload):
    save_cookie_service_key(payload.api_key)
    return {"configured": True}


@app.post("/api/tasks")
async def create_task(payload: NewTask):
    links = [link.strip() for link in payload.links if link.strip()]
    if not links:
        raise HTTPException(400, "Нужна хотя бы одна ссылка на выдачу Avito")
    for link in links:
        parsed = urlparse(link)
        if parsed.scheme not in ("http", "https") or not parsed.netloc.endswith("avito.ru"):
            raise HTTPException(
                400,
                f"Это не похоже на корректную ссылку на avito.ru: {link} "
                "(если вставляли ссылку в поле, где уже был текст — проверьте, "
                "не склеились ли два адреса)",
            )

    task_id = uuid.uuid4().hex[:12]
    await db.create_task(
        task_id, payload.source, payload.mode, links, payload.limit_per_link,
        payload.only_region, payload.merge_file, payload.email, payload.columns,
    )
    asyncio.create_task(_run_task(task_id, payload))
    return {"id": task_id}


LOG_FILE = data_dir() / "progress.log"


def _export_task_rows(task_id: str, payload: NewTask, rows: list[dict]) -> list[str]:
    # В БД сохраняем только имя файла, не абсолютный путь — если папку с программой
    # перенести (или отдать другому человеку), старые ссылки на скачивание не должны
    # ломаться. Полный путь всегда собирается заново из текущей EXPORTS_DIR.
    if payload.merge_file or len(payload.links) == 1:
        path = export_rows(rows, f"avito_{task_id}.xlsx", payload.columns)
        return [path.name]

    # без объединения — по файлу на каждую ссылку (группируем по "Номер поисковой выдачи")
    result_files = []
    per_link_rows: dict[int, list[dict]] = {}
    for r in rows:
        per_link_rows.setdefault(r.get("Номер поисковой выдачи", 0), []).append(r)
    for idx, link_rows in enumerate(per_link_rows.values(), start=1):
        path = export_rows(link_rows, f"avito_{task_id}_{idx}.xlsx", payload.columns)
        result_files.append(path.name)
    return result_files


async def _run_task(task_id: str, payload: NewTask,
                    resume_cards_by_link: dict[str, list[dict]] | None = None):
    _cancel_flags[task_id] = False
    await db.update_task(task_id, status="running", started_at=time.time())
    pending_cards_by_link: dict[str, list[dict]] = resume_cards_by_link.copy() if resume_cards_by_link else {}

    async def progress_cb(text: str):
        await db.update_task(task_id, progress_text=text)
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{task_id}] {text}\n")
        except Exception:
            pass

    def should_cancel():
        return _cancel_flags.get(task_id, False)

    def should_continue_captcha_wait():
        return time.time() < _captcha_wait_until.get(task_id, 0)

    async def state_cb(link: str, pending_cards: list[dict], rows: list[dict]):
        if pending_cards:
            pending_cards_by_link[link] = pending_cards
        else:
            pending_cards_by_link.pop(link, None)
        await db.update_task(
            task_id,
            items_count=len(rows),
            pending_cards=json.dumps(pending_cards_by_link, ensure_ascii=False) if pending_cards_by_link else None,
        )

    try:
        rows = await run_scrape(
            payload.links, payload.mode, payload.limit_per_link,
            progress_cb=progress_cb, should_cancel=should_cancel,
            state_cb=state_cb, resume_cards_by_link=resume_cards_by_link,
            should_continue_captcha_wait=should_continue_captcha_wait,
        )
        result_files = _export_task_rows(task_id, payload, rows)

        await db.update_task(
            task_id, status="done", finished_at=time.time(),
            items_count=len(rows), result_files=json.dumps(result_files, ensure_ascii=False),
            progress_text=f"Готово: {len(rows)} объявлений", pending_cards=None, error_code=None,
        )
    except Cancelled:
        await db.update_task(task_id, status="error", error="Отменено пользователем",
                              finished_at=time.time())
    except Blocked as e:
        rows = e.rows
        if e.pending_cards_by_link:
            pending_cards_by_link.update(e.pending_cards_by_link)
        result_files = _export_task_rows(task_id, payload, rows) if rows else []
        message = (
            f"Avito временно ограничил доступ с текущего IP. Собрано {len(rows)} объявлений; "
            "можно скачать частичный файл и позже нажать «Добрать недособранное»."
        )
        await db.update_task(
            task_id, status="error", error=message, error_code=e.code,
            finished_at=time.time(), items_count=len(rows),
            result_files=json.dumps(result_files, ensure_ascii=False),
            pending_cards=json.dumps(pending_cards_by_link, ensure_ascii=False) if pending_cards_by_link else None,
            progress_text=message,
        )
    except BrowserClosed as e:
        rows = e.rows
        if e.pending_cards_by_link:
            pending_cards_by_link.update(e.pending_cards_by_link)
        result_files = _export_task_rows(task_id, payload, rows) if rows else []
        message = (
            f"Окно браузера было закрыто. Собрано {len(rows)} объявлений; "
            "можно скачать частичный файл и позже нажать «Добрать недособранное»."
        )
        await db.update_task(
            task_id, status="error", error=message, error_code=e.code,
            finished_at=time.time(), items_count=len(rows),
            result_files=json.dumps(result_files, ensure_ascii=False),
            pending_cards=json.dumps(pending_cards_by_link, ensure_ascii=False) if pending_cards_by_link else None,
            progress_text=message,
        )
    except Exception as e:
        await db.update_task(task_id, status="error", error=str(e),
                              finished_at=time.time())
    finally:
        _cancel_flags.pop(task_id, None)
        _captcha_wait_until.pop(task_id, None)


@app.get("/api/tasks")
async def get_tasks():
    tasks = await db.list_tasks()
    for t in tasks:
        t["links"] = json.loads(t["links"])
        t["result_files"] = json.loads(t["result_files"]) if t["result_files"] else []
    return {"tasks": tasks}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    task["links"] = json.loads(task["links"])
    task["result_files"] = json.loads(task["result_files"]) if task["result_files"] else []
    return task


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    _cancel_flags[task_id] = True
    return {"ok": True}


@app.post("/api/tasks/{task_id}/continue-captcha")
async def continue_captcha(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    if task["status"] != "running":
        raise HTTPException(400, "Эта задача сейчас не ждёт капчу")
    _captcha_wait_until[task_id] = max(_captcha_wait_until.get(task_id, 0), time.time() + 600)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/repeat")
async def repeat_task(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    payload = NewTask(
        source=task["source"], mode=task["mode"], links=json.loads(task["links"]),
        limit_per_link=task["limit_per_link"], only_region=bool(task["only_region"]),
        merge_file=bool(task["merge_file"]), email=task["email"],
        columns=json.loads(task["columns"]) if task["columns"] else None,
    )
    return await create_task(payload)


@app.post("/api/tasks/{task_id}/repeat-unfinished")
async def repeat_unfinished_task(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    if not task["pending_cards"]:
        raise HTTPException(400, "Для этой задачи нет сохранённого хвоста")
    if task["status"] == "running":
        raise HTTPException(400, "Сначала дождитесь остановки текущей задачи")

    pending_cards_by_link = json.loads(task["pending_cards"])
    links = [link for link in json.loads(task["links"]) if pending_cards_by_link.get(link)]
    if not links:
        raise HTTPException(400, "Для этой задачи нет недособранных объявлений")

    payload = NewTask(
        source=task["source"], mode=task["mode"], links=links,
        limit_per_link=0, only_region=bool(task["only_region"]),
        merge_file=bool(task["merge_file"]), email=task["email"],
        columns=json.loads(task["columns"]) if task["columns"] else None,
    )
    new_task_id = uuid.uuid4().hex[:12]
    await db.create_task(
        new_task_id, payload.source, payload.mode, payload.links, payload.limit_per_link,
        payload.only_region, payload.merge_file, payload.email, payload.columns,
    )
    asyncio.create_task(_run_task(new_task_id, payload, pending_cards_by_link))
    return {"id": new_task_id}


@app.get("/api/tasks/{task_id}/download")
async def download_task(task_id: str, index: int = 0):
    task = await db.get_task(task_id)
    if not task or not task["result_files"]:
        raise HTTPException(404, "Файл не найден")
    files = json.loads(task["result_files"])
    if index >= len(files):
        raise HTTPException(404, "Файл не найден")
    # Раньше в БД хранился полный абсолютный путь — если папку с программой перенести
    # (или запустить у другого человека), такой путь ломается. Новые записи хранят только
    # имя файла и всегда ищутся в текущей EXPORTS_DIR; для старых записей с полным путём
    # от другой машины берём из него только имя файла и ищем там же.
    name = Path(files[index]).name
    path = EXPORTS_DIR / name
    if not path.exists():
        raise HTTPException(404, "Файл был удалён")
    return FileResponse(path, filename=path.name,
                         media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
