import json
import time

import aiosqlite

from .paths import data_dir

DB_PATH = data_dir() / "db" / "tasks.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued',      -- queued | running | done | error
    source TEXT NOT NULL,                        -- avito | drom | autoru
    mode TEXT NOT NULL,                           -- full | sellers_only
    links TEXT NOT NULL,                          -- JSON-массив ссылок
    limit_per_link INTEGER NOT NULL DEFAULT 0,
    only_region INTEGER NOT NULL DEFAULT 1,
    merge_file INTEGER NOT NULL DEFAULT 1,
    email TEXT,
    columns TEXT,                                 -- JSON-массив выбранных колонок (null = все)
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    items_count INTEGER NOT NULL DEFAULT 0,
    result_files TEXT,                            -- JSON-массив путей к готовым файлам
    error TEXT,
    progress_text TEXT,
    pending_cards TEXT,                           -- JSON: объявления, которые можно добрать позже
    error_code TEXT
);
"""

MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN pending_cards TEXT",
    "ALTER TABLE tasks ADD COLUMN error_code TEXT",
]


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                await db.execute(sql)
            except aiosqlite.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise
        await db.commit()


async def create_task(task_id: str, source: str, mode: str, links: list[str],
                       limit_per_link: int, only_region: bool, merge_file: bool,
                       email: str | None, columns: list[str] | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tasks
               (id, status, source, mode, links, limit_per_link, only_region, merge_file,
                email, columns, created_at, items_count)
               VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (task_id, source, mode, json.dumps(links, ensure_ascii=False),
             limit_per_link, int(only_region), int(merge_file), email,
             json.dumps(columns, ensure_ascii=False) if columns else None,
             time.time()),
        )
        await db.commit()


async def update_task(task_id: str, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", values + [task_id])
        await db.commit()


async def get_task(task_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def list_tasks(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
