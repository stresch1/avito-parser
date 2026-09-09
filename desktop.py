"""
Точка входа для десктоп-версии: тот же FastAPI-бэкенд и тот же HTML/JS-интерфейс
(app/static/), что и в браузерной версии — просто открываются в нативном окне
(pywebview) вместо вкладки браузера. Логика парсинга (app/scraper.py) не меняется.

Запуск из исходников:  python desktop.py
Сборка в один .exe:    pyinstaller desktop.spec

Скачивание файлов: обычная ссылка <a href="..."> в нативном окне pywebview (WebView2)
не запускает браузерный диалог сохранения, как в обычном браузере — просто ничего не
происходит. Поэтому в desktop-режиме фронтенд (app/static/app.js) вызывает не ссылку,
а нативный API pywebview (см. класс Api ниже), который сам открывает системный диалог
"Сохранить как" и копирует туда уже готовый файл. В браузерной версии (app/main.py
через uvicorn напрямую) ничего не меняется — там download-ссылка работает как обычно.
"""
import asyncio
import json
import shutil
import socket
import threading
import time
from pathlib import Path

import uvicorn
import webview

from app import db
from app.exporter import EXPORTS_DIR
from app.main import app

HOST = "127.0.0.1"


class Api:
    """Нативный мост JS -> Python для desktop-режима (window.pywebview.api.*)."""

    def save_file(self, task_id: str, index: int = 0) -> dict:
        task = asyncio.run(db.get_task(task_id))
        if not task or not task.get("result_files"):
            return {"ok": False, "error": "Файл не найден"}
        files = json.loads(task["result_files"])
        if index >= len(files):
            return {"ok": False, "error": "Файл не найден"}
        name = Path(files[index]).name
        src = EXPORTS_DIR / name
        if not src.exists():
            return {"ok": False, "error": "Файл был удалён"}

        window = webview.windows[0]
        result = window.create_file_dialog(webview.SAVE_DIALOG, save_filename=name)
        if not result:
            return {"ok": False, "error": "Отменено"}
        dest = result if isinstance(result, str) else result[0]
        shutil.copy(src, dest)
        return {"ok": True, "path": str(dest)}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _run_server(port: int) -> None:
    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Локальный сервер не поднялся вовремя")


def main() -> None:
    port = _free_port()
    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()
    _wait_for_server(port)

    webview.create_window(
        "Парсинг Авито",
        f"http://{HOST}:{port}",
        width=1200,
        height=900,
        min_size=(900, 600),
        js_api=Api(),
    )
    webview.start()


if __name__ == "__main__":
    main()
