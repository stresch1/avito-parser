"""
Пути к файлам приложения — единая точка, которая работает одинаково что при запуске
из исходников (python -m uvicorn ...), что из собранного PyInstaller .exe.

PyInstaller (--onefile) распаковывает упакованные ресурсы (статика, шаблоны) во
временную папку sys._MEIPASS при каждом запуске — она не годится для пользовательских
данных (БД, ключи API, прокси, выгрузки), они бы терялись между запусками. Поэтому:
- resource_path()  — для read-only ресурсов, упакованных вместе с приложением (static/)
- data_dir()       — для пользовательских данных, всегда рядом с .exe (или с исходниками
                      в dev-режиме), переживает перезапуск и обновление приложения
"""
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_path(*parts: str) -> Path:
    if _is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parent  # app/
    return base.joinpath(*parts)


def data_dir() -> Path:
    if _is_frozen():
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent  # корень проекта
    d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
