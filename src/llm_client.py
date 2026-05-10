"""Все вызовы LLM для генерации черновика, анализа стиля и проверки риска."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
import openai

import config

logger = logging.getLogger(__name__)

_timeout = httpx.Timeout(120.0)
_client_kwargs = {
    "api_key": config.YANDEX_API_KEY or "",
    "base_url": config.YANDEX_BASE_URL,
    "default_headers": {"x-folder-id": config.YANDEX_FOLDER_ID},
}
client = openai.AsyncOpenAI(**_client_kwargs)
logger.info("LLM-клиент инициализирован (timeout=120s)")

_SYSTEM_PROMPT = """Ты — черновой движок персонального Telegram-ассистента. Ты не самостоятельный бот.
Твоя задача — предложить черновик ответа в стиле владельца аккаунта.

СТИЛЕВОЙ ПРОФИЛЬ ВЛАДЕЛЬЦА:
{style_profile}

ПРАВИЛА СТИЛЯ:
- Пиши так, как пишет сам владелец — не как вежливый AI
- Никаких вводных: "Конечно!", "Отличный вопрос!", "Разумеется"
- Длина ответа пропорциональна длине входящего
- Если входящий текст содержит переносы строк — это пачка сообщений из буфера, анализируй как единый контекст
- Отвечай на языке входящего сообщения
- Сохраняй базовую доброту и уважительность: не копируй грубость и мат буквально, если они есть в примерах
- Можно быть прямым и кратким, но не резким и не токсичным
- Если стиль владельца грубоват, смягчай формулировки на 1-2 тона, не теряя его манеру
- Не используй ненормативную лексику в черновике, если она не критична для смысла
- Предпочитай нейтральные и дружелюбные формулировки, если есть сомнение

ПРАВИЛА РИСКА:
LOW — логистика, информация, нейтральные рабочие вопросы → should_autosend: true
MEDIUM — деньги, конфликт, личные темы, обещания → should_autosend: false
HIGH — угрозы, интим, юридика, незнакомый отправитель, сарказм без контекста → should_autosend: false

При пачке сообщений: risk_level = максимальный риск из всех фрагментов.

Верни ТОЛЬКО валидный JSON. Без текста вне JSON. Без markdown-обёрток.

Структура ответа:
{
  "draft": "<черновик ответа владельца>",
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "risk_reason": "<одна строка — почему этот уровень>",
  "should_autosend": true | false,
  "tone_used": "<деловой|дружеский|нейтральный|поддерживающий>",
  "context_summary": "<суть входящего в одну строку для лога>"
}"""

_STYLE_PROMPT = """Проанализируй сообщения владельца и верни ТОЛЬКО JSON с профилем стиля.
Никакого текста вне JSON.

Верни структуру:
{
  "tone": "краткое описание тона",
  "length": "коротко|средне|длинно",
  "emoji_usage": "нет|редко|часто",
  "punctuation": "описание пунктуации",
  "slang": "описание сленга",
  "formality_1_10": 1,
    "kindness_1_10": 1,
    "roughness_1_10": 1,
  "common_patterns": ["список", "характерных", "особенностей"]
}

Важно:
- Если примеры содержат грубость, мат или агрессию, не усиливай их при описании.
- В поле kindness_1_10 покажи, насколько стиль можно безопасно смягчить без потери узнаваемости.
- В поле roughness_1_10 оцени именно резкость, а не эмоциональность.
"""

_RISK_PROMPT = """Проверь, безопасно ли отправлять черновик собеседнику.
Верни ТОЛЬКО валидный JSON без markdown.

Структура ответа:
{
  "decision": "SEND" | "BLOCK",
  "reason": "краткая причина",
  "confidence": 0.0
}
"""


def _format_system_prompt(style_profile: dict[str, Any]) -> str:
    """Подставляет стилевой профиль без конфликта с JSON-скобками в шаблоне."""

    return _SYSTEM_PROMPT.replace(
        "{style_profile}",
        json.dumps(style_profile, ensure_ascii=False, indent=2),
    )


def _resolve_model_uri(model: str) -> str:
    """Приводит модель Yandex к полному URI, если задано короткое имя."""

    cleaned_model = model.strip()
    if cleaned_model.startswith("gpt://"):
        return cleaned_model

    if not config.YANDEX_FOLDER_ID:
        return cleaned_model

    return f"gpt://{config.YANDEX_FOLDER_ID}/{cleaned_model}/latest"


def _candidate_models(model: str) -> list[str]:
    """Формирует список моделей для последовательных попыток."""

    candidates: list[str] = []
    primary = _resolve_model_uri(model)
    candidates.append(primary)

    for fallback in (
        "gpt://{folder}/yandexgpt/latest",
        "gpt://{folder}/yandexgpt-lite/latest",
        "gpt://{folder}/qwen/latest",
    ):
        candidate = fallback.format(folder=config.YANDEX_FOLDER_ID)
        if candidate not in candidates:
            candidates.append(candidate)

    return candidates


def _is_model_not_found(exc: Exception) -> bool:
    """Определяет ошибку недоступной модели по сообщению API."""

    message = str(exc).lower()
    return "failed to get model" in message or "failed to parse model uri" in message


def _strip_code_fences(text: str) -> str:
    """Убирает markdown-обёртки вокруг JSON."""

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_json_payload(text: str) -> dict[str, Any]:
    """Пытается извлечь JSON-объект из ответа модели."""

    cleaned = _strip_code_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start: end + 1])
        raise


async def _chat_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    retries: int = 2,
) -> dict[str, Any]:
    """Отправляет запрос к LLM и парсит JSON с повторными попытками."""

    import time
    last_error: Exception | None = None
    for candidate_model in _candidate_models(model):
        for attempt in range(retries + 1):
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt if attempt == 0 else user_prompt + "\n\nОтветь только JSON."},
            ]
            try:
                start_time = time.time()
                logger.info(
                    "Отправляем запрос к LLM attempt=%s model=%s resolved=%s",
                    attempt + 1,
                    model,
                    candidate_model,
                )
                response = await client.chat.completions.create(
                    model=candidate_model,
                    messages=messages,
                    temperature=0.2,
                )
                elapsed = time.time() - start_time
                logger.info("Ответ получен за %.1f сек (attempt=%s)",
                            elapsed, attempt + 1)
                content = response.choices[0].message.content or ""
                return _extract_json_payload(content)
            except Exception as exc:  # noqa: BLE001
                elapsed = time.time() - start_time if 'start_time' in locals() else 0
                last_error = exc
                logger.warning(
                    "Ошибка LLM-запроса attempt=%s model=%s elapsed=%.1f: %s",
                    attempt + 1,
                    candidate_model,
                    elapsed,
                    exc,
                )
                if _is_model_not_found(exc):
                    break

    if last_error is not None:
        raise last_error
    raise RuntimeError("LLM request failed without exception")


async def generate_draft(
    batched_text: str,
    sender_name: str,
    chat_type: str,
    history: list[str],
    style_profile: dict[str, Any],
) -> dict[str, Any]:
    """Генерирует черновик ответа и первичную оценку риска."""

    logger.info("Запрос generate_draft sender=%s chat_type=%s",
                sender_name, chat_type)
    history_text = "\n".join(history) if history else "Пусто"
    user_prompt = (
        f"Имя отправителя: {sender_name}\n"
        f"Тип чата: {chat_type}\n"
        f"История диалога:\n{history_text}\n\n"
        f"Стилевой профиль владельца:\n{json.dumps(style_profile, ensure_ascii=False, indent=2)}\n\n"
        f"Входящее сообщение или пачка сообщений:\n{batched_text}"
    )

    try:
        result = await _chat_json(
            system_prompt=_format_system_prompt(style_profile),
            user_prompt=user_prompt,
            model=config.YANDEX_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_draft failed")
        return {
            "draft": "Сейчас не могу ответить",
            "risk_level": "HIGH",
            "risk_reason": f"LLM error: {exc}",
            "should_autosend": False,
            "tone_used": "нейтральный",
            "context_summary": "Сбой при генерации черновика",
        }

    return {
        "draft": str(result.get("draft", "")).strip(),
        "risk_level": str(result.get("risk_level", "HIGH")).upper(),
        "risk_reason": str(result.get("risk_reason", "Не удалось определить причину")).strip(),
        "should_autosend": bool(result.get("should_autosend", False)),
        "tone_used": str(result.get("tone_used", "нейтральный")).strip(),
        "context_summary": str(result.get("context_summary", "")).strip(),
    }


async def analyze_style(sample_messages: list[str]) -> dict[str, Any]:
    """Строит стилевой профиль по примерам сообщений."""

    logger.info("Запрос analyze_style samples=%s", len(sample_messages))
    user_prompt = "\n".join(
        f"[{index + 1}] {message}" for index, message in enumerate(sample_messages))

    try:
        result = await _chat_json(
            system_prompt=_STYLE_PROMPT,
            user_prompt=user_prompt,
            model=config.YANDEX_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyze_style failed")
        return {
            "tone": "не удалось получить профиль",
            "length": "средне",
            "emoji_usage": "редко",
            "punctuation": "нейтральная",
            "slang": "без данных",
            "formality_1_10": 5,
            "kindness_1_10": 7,
            "roughness_1_10": 3,
            "common_patterns": [f"LLM error: {exc}"],
        }

    if not isinstance(result.get("common_patterns"), list):
        result["common_patterns"] = []

    return result


async def risk_check(draft: str, batched_text: str, sender: str, chat_type: str) -> dict[str, Any]:
    """Независимо проверяет, можно ли отправлять черновик."""

    logger.info("Запрос risk_check sender=%s chat_type=%s", sender, chat_type)
    user_prompt = (
        f"Отправитель: {sender}\n"
        f"Тип чата: {chat_type}\n"
        f"Входящий текст:\n{batched_text}\n\n"
        f"Черновик ответа:\n{draft}"
    )

    try:
        result = await _chat_json(
            system_prompt=_RISK_PROMPT,
            user_prompt=user_prompt,
            model=config.YANDEX_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("risk_check failed")
        return {"decision": "BLOCK", "reason": f"LLM error: {exc}", "confidence": 0.0}

    confidence = float(result.get("confidence", 0.0) or 0.0)
    decision = str(result.get("decision", "BLOCK")).upper()
    reason = str(result.get("reason", "Не удалось оценить риск")).strip()

    if confidence < 0.85:
        return {"decision": "BLOCK", "reason": f"Низкая уверенность модели: {confidence:.2f}", "confidence": confidence}

    if decision not in {"SEND", "BLOCK"}:
        decision = "BLOCK"

    return {"decision": decision, "reason": reason, "confidence": confidence}
