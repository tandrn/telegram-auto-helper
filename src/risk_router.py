"""Маршрутизация результата LLM по уровням риска."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

import config
from src.llm_client import risk_check

logger = logging.getLogger(__name__)


_RUDE_MARKERS = (
    "бля",
    "сука",
    "хуй",
    "пизд",
    "ебан",
    "ебать",
    "мудак",
    "урод",
    "твар",
)


def _clip(text: str, limit: int = 1200) -> str:
    """Урезает текст для сообщений в Telegram, чтобы не превышать лимит."""

    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n… [обрезано]"


def _has_rude_language(text: str) -> bool:
    """Определяет, есть ли в тексте грубая лексика."""

    lowered = text.lower()
    return any(marker in lowered for marker in _RUDE_MARKERS)


class RiskRouter:
    """Выбирает действие по уровню риска и флагу автосендa."""

    @staticmethod
    async def _send_to_owner(bot: Bot, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> bool:
        """Пытается отправить сообщение владельцу и не роняет поток при ошибке Telegram."""

        if config.OWNER_CHAT_ID <= 0:
            logger.error(
                "OWNER_CHAT_ID не задан или некорректен: %s", config.OWNER_CHAT_ID)
            return False

        try:
            await bot.send_message(chat_id=config.OWNER_CHAT_ID, text=text, reply_markup=reply_markup)
            return True
        except TelegramBadRequest:
            logger.exception(
                "Не удалось отправить сообщение владельцу chat_id=%s", config.OWNER_CHAT_ID)
            return False

    async def route(self, result: dict[str, Any], chat_id: int, sender_name: str, bot: Bot) -> str:
        """Маршрутизирует результат генерации: автосенд, черновик владельцу или блокировку."""

        risk_level = str(result.get("risk_level", "HIGH")).upper()
        should_autosend = bool(result.get("should_autosend", False))
        draft = str(result.get("draft", "")).strip()
        risk_reason = str(result.get("risk_reason", "")).strip()
        batched_text = str(result.get("batched_text", "")).strip()
        received_at = str(result.get("received_at", "")).strip()
        interaction_id = result.get("interaction_id")
        interaction_row = result.get("interaction_row") or {}
        timestamp = str(interaction_row.get("timestamp", received_at)).strip()
        force_owner_review = _has_rude_language(
            batched_text) or _has_rude_language(draft)
        logger.info(
            "Routing result interaction_id=%s chat_id=%s sender=%s risk_level=%s should_autosend=%s",
            interaction_id,
            chat_id,
            sender_name,
            risk_level,
            should_autosend,
        )

        if risk_level == "LOW" and should_autosend:
            check = await risk_check(draft=draft, batched_text=str(result.get("batched_text", "")), sender=sender_name, chat_type=str(result.get("chat_type", "private")))
            if check.get("decision") == "SEND" and not force_owner_review:
                await bot.send_message(chat_id=chat_id, text=draft)
                logger.info(
                    "Черновик отправлен автоматически chat_id=%s interaction_id=%s", chat_id, interaction_id)
                return "AUTO_SENT"

            markup = self._owner_keyboard(interaction_id, chat_id)
            owner_text = (
                f"✉️ Черновик требует подтверждения\n"
                f"Отправитель: {sender_name}\n"
                f"Chat ID: {chat_id}\n"
                f"Время: {timestamp}\n"
                f"Причина: {check.get('reason', 'unknown')}\n"
                f"Ручная проверка: {'да' if force_owner_review else 'нет'}\n\n"
                f"Исходный текст:\n{_clip(batched_text)}\n\n"
                f"Черновик:\n{_clip(draft, 1000)}"
            )
            await self._send_to_owner(bot, owner_text, reply_markup=markup)
            logger.warning(
                "Автоотправка заблокирована risk_check chat_id=%s interaction_id=%s", chat_id, interaction_id)
            return "DRAFT_SENT_TO_OWNER"

        if risk_level == "MEDIUM" or not should_autosend:
            markup = self._owner_keyboard(interaction_id, chat_id)
            owner_text = (
                f"✉️ Новый черновик для подтверждения\n"
                f"Отправитель: {sender_name}\n"
                f"Chat ID: {chat_id}\n"
                f"Время: {timestamp}\n"
                f"Риск: {risk_level}\n"
                f"Причина: {risk_reason}\n\n"
                f"Исходный текст:\n{_clip(batched_text)}\n\n"
                f"Черновик:\n{_clip(draft, 1000)}"
            )
            await self._send_to_owner(bot, owner_text, reply_markup=markup)
            logger.info(
                "Черновик отправлен владельцу interaction_id=%s", interaction_id)
            return "DRAFT_SENT_TO_OWNER"

        owner_text = (
            f"⛔ Сообщение заблокировано\n"
            f"Отправитель: {sender_name}\n"
            f"Chat ID: {chat_id}\n"
            f"Время: {timestamp}\n"
            f"Причина: {risk_reason}\n\n"
            f"Исходный текст:\n{_clip(batched_text)}\n\n"
            f"Контекст:\n{str(result.get('context_summary', ''))}"
        )
        await self._send_to_owner(bot, owner_text)
        logger.info("Сообщение заблокировано interaction_id=%s",
                    interaction_id)
        return "BLOCKED"

    @staticmethod
    def _owner_keyboard(interaction_id: Any, chat_id: int) -> InlineKeyboardMarkup:
        """Создаёт inline-кнопки для владельца."""

        interaction_part = str(interaction_id or "0")
        approve_data = f"yes:{interaction_part}:{chat_id}"
        edit_data = f"manual:{interaction_part}:{chat_id}"
        reject_data = f"no:{interaction_part}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да", callback_data=approve_data),
                    InlineKeyboardButton(
                        text="✏️ Написать самому", callback_data=edit_data),
                ],
                [InlineKeyboardButton(
                    text="❌ Нет", callback_data=reject_data)],
            ]
        )
        return keyboard
