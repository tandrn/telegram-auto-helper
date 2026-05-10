"""Буферизация входящих сообщений по chat_id."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from config import BUFFER_WAIT_SECONDS

logger = logging.getLogger(__name__)

BufferCallback = Callable[[int, list[str]], Awaitable[None]]


class MessageBuffer:
    """Буфер, который группирует сообщения одного чата в одну пачку."""

    def __init__(self) -> None:
        self._buffers: dict[int, list[str]] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def add(self, chat_id: int, text: str, callback: BufferCallback) -> None:
        """Добавляет сообщение в буфер и перезапускает таймер ожидания."""

        async with self._lock:
            messages = self._buffers.setdefault(chat_id, [])
            messages.append(text)

            existing_task = self._tasks.pop(chat_id, None)
            if existing_task is not None:
                existing_task.cancel()

            self._tasks[chat_id] = asyncio.create_task(
                self._wait_and_fire(chat_id, callback),
                name=f"buffer:{chat_id}",
            )
            logger.info(
                "Сообщение добавлено в буфер chat_id=%s, size=%s", chat_id, len(messages))

    async def flush(self, chat_id: int) -> list[str]:
        """Принудительно очищает буфер и возвращает накопленные сообщения."""

        async with self._lock:
            task = self._tasks.pop(chat_id, None)
            if task is not None:
                task.cancel()

            messages = self._buffers.pop(chat_id, [])

        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "Ошибка при принудительной очистке буфера chat_id=%s", chat_id)

        logger.info("Буфер принудительно очищен chat_id=%s, size=%s",
                    chat_id, len(messages))
        return messages

    async def _wait_and_fire(self, chat_id: int, callback: BufferCallback) -> None:
        """Ждет окно буферизации и запускает callback."""

        try:
            await asyncio.sleep(BUFFER_WAIT_SECONDS)
            async with self._lock:
                messages = self._buffers.pop(chat_id, [])
                self._tasks.pop(chat_id, None)
        except asyncio.CancelledError:
            logger.info("Таймер буфера отменен chat_id=%s", chat_id)
            raise
        except Exception:
            logger.exception("Сбой таймера буфера chat_id=%s", chat_id)
            return

        if not messages:
            logger.info("Буфер пуст после ожидания chat_id=%s", chat_id)
            return

        logger.info("Буфер сработал chat_id=%s, messages=%s",
                    chat_id, len(messages))
        try:
            await callback(chat_id, messages)
        except Exception:
            logger.exception("Ошибка callback буфера chat_id=%s", chat_id)
