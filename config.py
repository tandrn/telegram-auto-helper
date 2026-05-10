"""Конфигурация приложения, читаемая из .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent


def _get_int(name: str, default: int) -> int:
    """Безопасно читает целое число из окружения."""

    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        return int(raw_value)
    except ValueError:
        return default


YANDEX_API_KEY: str = os.getenv("YANDEX_API_KEY", "")
YANDEX_MODEL: str = os.getenv("YANDEX_MODEL", "gpt-3.5-turbo")
YANDEX_BASE_URL: str = os.getenv(
    "YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net/v1")
YANDEX_FOLDER_ID: str = os.getenv("YANDEX_FOLDER_ID", "")
YANDEX_PROXY_URL: str | None = os.getenv("YANDEX_PROXY_URL") or None
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_PROXY_URL: str | None = os.getenv("TELEGRAM_PROXY_URL") or None
OWNER_CHAT_ID: int = _get_int("OWNER_CHAT_ID", 0)
BUFFER_WAIT_SECONDS: int = _get_int("BUFFER_WAIT_SECONDS", 20)
MAX_HISTORY_MESSAGES: int = _get_int("MAX_HISTORY_MESSAGES", 5)

_db_path = Path(os.getenv("DB_PATH", "logs/assistant.db"))
if not _db_path.is_absolute():
    _db_path = PROJECT_ROOT / _db_path
DB_PATH: str = str(_db_path)

_style_profile_path = Path(
    os.getenv("STYLE_PROFILE_PATH", "style_profile.json"))
if not _style_profile_path.is_absolute():
    _style_profile_path = PROJECT_ROOT / _style_profile_path
STYLE_PROFILE_PATH: Path = _style_profile_path

__all__ = [
    "YANDEX_API_KEY",
    "YANDEX_MODEL",
    "YANDEX_BASE_URL",
    "YANDEX_FOLDER_ID",
    "YANDEX_PROXY_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_PROXY_URL",
    "OWNER_CHAT_ID",
    "BUFFER_WAIT_SECONDS",
    "MAX_HISTORY_MESSAGES",
    "DB_PATH",
    "STYLE_PROFILE_PATH",
    "PROJECT_ROOT",
]
