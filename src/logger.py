"""SQLite-логирование взаимодействий с ботом."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from config import DB_PATH

logger = logging.getLogger(__name__)
_DB_PATH = Path(DB_PATH)
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    chat_id INTEGER,
    sender_name TEXT,
    incoming_text TEXT,
    batched_text TEXT,
    draft TEXT,
    risk_level TEXT,
    risk_reason TEXT,
    action_taken TEXT,
    final_sent TEXT,
    tone_used TEXT,
    context_summary TEXT
)
"""


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    """Гарантирует наличие таблицы логов."""

    await db.execute(_SCHEMA)
    await db.commit()


async def log_interaction(
    *,
    chat_id: int,
    sender_name: str,
    incoming_text: str,
    batched_text: str,
    draft: str,
    risk_level: str,
    risk_reason: str,
    action_taken: str | None,
    final_sent: str | None,
    tone_used: str,
    context_summary: str,
) -> int:
    """Добавляет новую запись о взаимодействии и возвращает ее идентификатор."""

    timestamp = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_schema(db)
        cursor = await db.execute(
            """
            INSERT INTO interactions (
                timestamp,
                chat_id,
                sender_name,
                incoming_text,
                batched_text,
                draft,
                risk_level,
                risk_reason,
                action_taken,
                final_sent,
                tone_used,
                context_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                chat_id,
                sender_name,
                incoming_text,
                batched_text,
                draft,
                risk_level,
                risk_reason,
                action_taken,
                final_sent,
                tone_used,
                context_summary,
            ),
        )
        await db.commit()
        interaction_id = int(cursor.lastrowid)
        logger.info("Записан лог interaction_id=%s chat_id=%s",
                    interaction_id, chat_id)
        return interaction_id


async def update_action(interaction_id: int, action: str, final_sent: str | None) -> None:
    """Обновляет действие владельца и итоговый отправленный текст."""

    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_schema(db)
        await db.execute(
            """
            UPDATE interactions
            SET action_taken = ?, final_sent = ?
            WHERE id = ?
            """,
            (action, final_sent, interaction_id),
        )
        await db.commit()
        logger.info("Обновлен лог interaction_id=%s action=%s",
                    interaction_id, action)


async def get_recent(n: int = 20) -> list[dict[str, Any]]:
    """Возвращает последние n записей как список словарей."""

    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM interactions
            ORDER BY id DESC
            LIMIT ?
            """,
            (n,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_by_id(interaction_id: int) -> dict[str, Any] | None:
    """Возвращает запись по идентификатору взаимодействия."""

    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM interactions
            WHERE id = ?
            """,
            (interaction_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None
