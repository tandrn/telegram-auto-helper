"""Точка входа Telegram-автопомощника на базе LLM."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urlparse
from typing import Any
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

import config
from buffer import MessageBuffer
from src.llm_client import analyze_style, generate_draft
from src.logger import get_by_id, get_recent, log_interaction, update_action
from src.risk_router import RiskRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()
buffer = MessageBuffer()
risk_router = RiskRouter()
style_profile_path = config.STYLE_PROFILE_PATH


class SetupStyle(StatesGroup):
    """Состояние сбора примеров для стилевого профиля."""

    waiting_for_samples = State()


class EditDraftFlow(StatesGroup):
    """Состояние ожидания исправленного черновика."""

    waiting_for_text = State()


class CommandReply(StatesGroup):
    """Вспомогательная группа состояний для команд."""

    waiting_for_text = State()


def load_style_profile() -> dict[str, Any]:
    """Загружает стилевой профиль из JSON-файла."""

    if not style_profile_path.exists():
        return {}

    try:
        with style_profile_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            return data
    except Exception:
        logger.exception("Не удалось прочитать style_profile.json")

    return {}


def save_style_profile(profile: dict[str, Any]) -> None:
    """Сохраняет стилевой профиль в JSON-файл."""

    with style_profile_path.open("w", encoding="utf-8") as file:
        json.dump(profile, file, ensure_ascii=False, indent=2)


def build_telegram_session() -> AiohttpSession | None:
    """Создает Telegram-сессию с прокси, если оно задано и поддерживается."""

    proxy_url = config.TELEGRAM_PROXY_URL
    if not proxy_url:
        return None

    scheme = urlparse(proxy_url).scheme.lower()
    if scheme not in {"http", "https", "socks4", "socks5"}:
        raise RuntimeError(
            "TELEGRAM_PROXY_URL имеет неподдерживаемую схему "
            f"'{scheme}'. Для aiogram нужны http/https/socks4/socks5. "
            "Формат stg://proxy?... не поддерживается напрямую."
        )

    logger.info("Используется прокси для Telegram: %s", proxy_url)
    return AiohttpSession(proxy=proxy_url)


async def build_history(chat_id: int) -> list[str]:
    """Собирает историю диалога для LLM по последним записям этого чата."""

    recent = await get_recent(100)
    relevant = [row for row in recent if int(row.get("chat_id", 0)) == chat_id]
    relevant = list(reversed(relevant[-config.MAX_HISTORY_MESSAGES:]))
    history: list[str] = []
    for row in relevant:
        sender = str(row.get("sender_name", ""))
        incoming = str(row.get("incoming_text", ""))
        draft = str(row.get("draft", ""))
        history.append(f"{sender}: {incoming} -> {draft}".strip())
    return history


async def on_buffer_ready(chat_id: int, messages: list[str], sender_name: str, chat_type: str, bot: Bot) -> None:
    """Обрабатывает пачку сообщений после срабатывания буфера."""

    batched_text = "\n".join(messages)
    received_at = datetime.now(timezone.utc).isoformat()
    logger.info("on_buffer_ready chat_id=%s messages=%s",
                chat_id, len(messages))
    history = await build_history(chat_id)
    style_profile = load_style_profile()

    result = await generate_draft(
        batched_text=batched_text,
        sender_name=sender_name,
        chat_type=chat_type,
        history=history,
        style_profile=style_profile,
    )
    result["batched_text"] = batched_text
    result["chat_type"] = chat_type
    result["received_at"] = received_at
    result["sender_name"] = sender_name
    result["chat_id"] = chat_id

    interaction_id = await log_interaction(
        chat_id=chat_id,
        sender_name=sender_name,
        incoming_text=messages[0],
        batched_text=batched_text,
        draft=str(result.get("draft", "")),
        risk_level=str(result.get("risk_level", "HIGH")),
        risk_reason=str(result.get("risk_reason", "")),
        action_taken=None,
        final_sent=None,
        tone_used=str(result.get("tone_used", "")),
        context_summary=str(result.get("context_summary", "")),
    )
    result["interaction_id"] = interaction_id
    result["interaction_row"] = await get_by_id(interaction_id)

    action = await risk_router.route(result=result, chat_id=chat_id, sender_name=sender_name, bot=bot)
    final_sent = str(result.get("draft", "")
                     ) if action == "AUTO_SENT" else None
    await update_action(interaction_id, action, final_sent)
    logger.info("Поток завершен interaction_id=%s action=%s",
                interaction_id, action)


is_owner = F.from_user.id == config.OWNER_CHAT_ID


@router.message(Command("start"))
async def start_handler(message: Message) -> None:
    """Приветствует пользователя и объясняет режимы работы."""

    if message.from_user and message.from_user.id == config.OWNER_CHAT_ID:
        text = (
            "Привет, Владелец! Я твой Telegram-автопомощник.\n\n"
            "Команды:\n"
            "/setup — собрать стиль\n"
            "/log — история ответов\n"
            "/status — текущие настройки"
        )
    else:
        text = "Привет! Я автопомощник. Напиши сообщение, и я постараюсь ответить или передам его владельцу."

    await message.answer(text)


@router.message(Command("setup"), is_owner)
async def setup_handler(message: Message, state: FSMContext) -> None:
    """Запускает сбор примеров сообщений для анализа стиля."""

    await state.set_state(SetupStyle.waiting_for_samples)
    await state.update_data(samples=[])
    await message.answer(
        "Давай соберем твой стиль удобно! 🚀\n\n"
        "Открой любой чат с друзьями или коллегами, **выдели свои типичные сообщения галочками** (сразу 20-30 штук) и просто **перешли их мне**.\n"
        "Я буду складывать их в копилку. Можно пересылать частями из разных чатов.\n\n"
        "Как пришлешь достаточно — отправь команду /done, и я посчитаю твой профиль."
    )


@router.message(Command("done"), is_owner)
async def setup_done_handler(message: Message, state: FSMContext) -> None:
    """Завершает сбор сообщений и формирует профиль."""

    current_state = await state.get_state()
    if current_state != SetupStyle.waiting_for_samples.state:
        return

    data = await state.get_data()
    samples = data.get("samples", [])

    if len(samples) < 10:
        await message.answer(f"Пока в копилке всего {len(samples)} сообщений. Перешли еще хотя бы до 10, а лучше больше!")
        return

    await message.answer(f"Отлично! Собрано {len(samples)} примеров. Анализирую стиль, это займет около 10-15 секунд...")
    profile = await analyze_style(samples[:50])
    save_style_profile(profile)
    await state.clear()
    await message.answer("✅ Стилевой профиль готов и сохранен! Можешь проверить его командой /status.")


@router.message(SetupStyle.waiting_for_samples, F.text | F.caption, is_owner)
async def setup_collect_handler(message: Message, state: FSMContext) -> None:
    """Собирает пересланные или написанные сообщения в копилку."""

    text = (message.text or message.caption or "").strip()
    if text.startswith("/"):
        return

    data = await state.get_data()
    samples = data.get("samples", [])

    # Если кинули одним большим сообщением через Enter (старый режим)
    if "\n" in text and not getattr(message, "forward_origin", None):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        samples.extend(lines)
    else:
        samples.append(text)

    await state.update_data(samples=samples)

    # Чтобы не спамить в ответ на пересылку пачки из 30 сообщений,
    # отвечаем только на каждое 10-е
    if len(samples) == 1 or len(samples) % 10 == 0:
        await message.answer(f"В копилке: {len(samples)} сообщений. Жду еще...\nЖми /done когда закончишь.")


@router.message(Command("log"), is_owner)
async def log_handler(message: Message) -> None:
    """Показывает последние записи из базы данных."""

    rows = await get_recent(5)
    if not rows:
        await message.answer("Лог пуст.")
        return

    lines = []
    for row in rows:
        lines.append(
            f"#{row['id']} | {row['timestamp']} | chat_id={row['chat_id']} | {row['sender_name']} | {row['risk_level']} | {row.get('action_taken') or 'PENDING'}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("status"), is_owner)
async def status_handler(message: Message) -> None:
    """Показывает текущий стилевой профиль и настройки приложения."""

    profile = load_style_profile()
    text = (
        f"BUFFER_WAIT_SECONDS: {config.BUFFER_WAIT_SECONDS}\n"
        f"MAX_HISTORY_MESSAGES: {config.MAX_HISTORY_MESSAGES}\n"
        f"DB_PATH: {config.DB_PATH}\n"
        f"OWNER_CHAT_ID: {config.OWNER_CHAT_ID}\n\n"
        f"Style profile:\n{json.dumps(profile, ensure_ascii=False, indent=2) or '{}'}"
    )
    await message.answer(text)


@router.callback_query(F.data.startswith("yes:"), is_owner)
async def approve_draft_handler(query: CallbackQuery, bot: Bot) -> None:
    """Отправляет черновик собеседнику после подтверждения владельцем."""

    await query.answer()
    if query.data is None:
        return

    _, interaction_raw, chat_raw = query.data.split(":", 2)
    interaction_id = int(interaction_raw)
    chat_id = int(chat_raw)
    row = await get_by_id(interaction_id)
    if row is None:
        await query.message.answer("Не удалось найти запись в логе.") if query.message else None
        return

    draft = str(row.get("draft", "")).strip()
    await bot.send_message(chat_id=chat_id, text=draft)
    await update_action(interaction_id, "YES_SENT", draft)

    if query.message is not None:
        await query.message.edit_text(f"✅ Черновик отправлен в chat_id={chat_id}")
    logger.info("Черновик подтвержден interaction_id=%s", interaction_id)


@router.callback_query(F.data.startswith("manual:"), is_owner)
async def edit_draft_handler(query: CallbackQuery, state: FSMContext) -> None:
    """Запрашивает у владельца исправленный текст черновика."""

    await query.answer()
    if query.data is None:
        return

    _, interaction_raw, chat_raw = query.data.split(":", 2)
    interaction_id = int(interaction_raw)
    chat_id = int(chat_raw)
    row = await get_by_id(interaction_id)
    await state.set_state(EditDraftFlow.waiting_for_text)
    await state.update_data(interaction_id=interaction_id, chat_id=chat_id)

    if query.message is not None:
        source_text = str(row.get("batched_text", "")) if row else ""
        draft_text = str(row.get("draft", "")) if row else ""
        await query.message.answer(
            "Отправь свой вариант одним сообщением. Можно просто переписать черновик ниже.\n\n"
            f"Исходный текст:\n{source_text[:1200]}\n\n"
            f"Черновик:\n{draft_text[:1200]}"
        )
    logger.info("Ожидание исправленного текста interaction_id=%s",
                interaction_id)


@router.message(EditDraftFlow.waiting_for_text, F.text, is_owner)
async def edited_text_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    """Отправляет владельцем исправленный текст собеседнику и обновляет лог."""

    data = await state.get_data()
    interaction_id = int(data.get("interaction_id", 0))
    chat_id = int(data.get("chat_id", 0))
    edited_text = message.text.strip()

    if interaction_id == 0 or chat_id == 0:
        await message.answer("Не удалось восстановить контекст редактирования.")
        await state.clear()
        return

    await bot.send_message(chat_id=chat_id, text=edited_text)
    await update_action(interaction_id, "MANUAL_SENT", edited_text)
    await state.clear()
    await message.answer("Исправленный текст отправлен.")
    logger.info("Черновик отредактирован interaction_id=%s", interaction_id)


@router.callback_query(F.data.startswith("no:"), is_owner)
async def reject_draft_handler(query: CallbackQuery) -> None:
    """Отклоняет черновик без отправки собеседнику."""

    await query.answer()
    if query.data is None:
        return

    _, interaction_raw = query.data.split(":", 1)
    interaction_id = int(interaction_raw)
    await update_action(interaction_id, "NO", None)

    if query.message is not None:
        await query.message.edit_text("❌ Черновик отклонен")
    logger.info("Черновик отклонен interaction_id=%s", interaction_id)


@router.message(F.text | F.caption)
async def default_message_handler(message: Message, bot: Bot) -> None:
    """Буферизует любое входящее некомандное текстовое сообщение."""

    text = message.text or message.caption
    if not text or text.startswith("/"):
        return

    sender_name = message.from_user.full_name if message.from_user else "Unknown"
    chat_type = message.chat.type
    logger.info("Получено сообщение chat_id=%s sender=%s",
                message.chat.id, sender_name)

    async def _buffer_callback(chat_id: int, messages: list[str], *, sender: str = sender_name, kind: str = chat_type) -> None:
        await on_buffer_ready(chat_id, messages, sender, kind, bot)

    await buffer.add(message.chat.id, text, _buffer_callback)


async def main() -> None:
    """Запускает polling-режим Telegram-бота."""

    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")

    telegram_session = build_telegram_session()
    if telegram_session is None:
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    else:
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN, session=telegram_session)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Бот запускается в polling-режиме")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
