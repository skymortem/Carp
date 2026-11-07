"""Минимальный Telegram-бот на long polling.

Свой клиент вместо библиотеки: нужны ровно две операции (getUpdates и
sendMessage), а лишняя зависимость в проекте, где всё остальное тоже
написано на httpx, не окупается.

Бот работает внутри процесса uvicorn — как планировщик сбора. Это ещё один
аргумент за «ровно один worker»: два процесса начнут читать одни и те же
апдейты и отвечать дважды.
"""
import asyncio
import logging
from typing import Awaitable, Callable, Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.car import AppState

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
OFFSET_KEY = "telegram_offset"
HANDLER: Optional[Callable[[str, str], Awaitable[str]]] = None


class TelegramError(Exception):
    """Telegram недоступен или вернул ошибку."""


def set_handler(handler: Callable[[str, str], Awaitable[str]]) -> None:
    """Зарегистрировать обработчик текста: (text, chat_id) -> ответ."""
    global HANDLER
    HANDLER = handler


async def _call(method: str, **params):
    token = settings.telegram_bot_token
    if not token:
        raise TelegramError("Токен Telegram-бота не задан")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(API_URL.format(token=token, method=method), json=params)
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TelegramError("Telegram недоступен или вернул некорректный ответ") from exc
    if not payload.get("ok"):
        raise TelegramError(f"Telegram: {payload.get('description', 'ошибка запроса')}")
    return payload.get("result")


async def send_message(text: str, chat_id: Optional[str] = None) -> bool:
    """Отправить сообщение владельцу. Молча пропускает, если бот не настроен."""
    target = chat_id or settings.telegram_chat_id
    if not settings.telegram_bot_token or not target:
        logger.info("Telegram не настроен — сообщение не отправлено: %s", text.splitlines()[0])
        return False
    try:
        await _call("sendMessage", chat_id=target, text=text,
                    parse_mode="HTML", disable_web_page_preview=True)
        return True
    except TelegramError as exc:
        logger.warning("Не удалось отправить сообщение в Telegram: %s", exc)
        return False


async def _load_offset() -> int:
    async with async_session() as db:
        raw = await db.scalar(select(AppState.value).where(AppState.key == OFFSET_KEY))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def _save_offset(offset: int) -> None:
    async with async_session() as db:
        row = await db.get(AppState, OFFSET_KEY)
        if row is None:
            db.add(AppState(key=OFFSET_KEY, value=str(offset)))
        else:
            row.value = str(offset)
        await db.commit()


async def _handle_update(update: dict) -> None:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    owner = str(settings.telegram_chat_id or "")
    if not owner:
        # Первый запуск: подсказываем владельцу его chat_id, но ничего не сохраняем —
        # иначе любой, кто найдёт бота, привяжет его к себе.
        await send_message(
            f"Твой chat_id: <code>{chat_id}</code>\n"
            "Добавь его в TELEGRAM_CHAT_ID в настройках Carp (страница /setup).",
            chat_id=chat_id,
        )
        return
    if chat_id != owner:
        logger.warning("Сообщение от постороннего чата %s проигнорировано", chat_id)
        return
    if HANDLER is None:
        return
    try:
        reply = await HANDLER(text, chat_id)
    except Exception:
        logger.exception("Ошибка обработки сообщения Telegram")
        reply = "Что-то сломалось на стороне Carp, попробуй ещё раз."
    if reply:
        await send_message(reply, chat_id=chat_id)


async def _poll_loop() -> None:
    offset = await _load_offset()
    failures = 0
    logger.info("Telegram-бот запущен (long polling)")
    while True:
        try:
            updates = await _call("getUpdates", offset=offset, timeout=25,
                                  allowed_updates=["message"], limit=20)
            failures = 0
            for update in updates or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                await _handle_update(update)
            if updates:
                await _save_offset(offset)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # сеть, 401, 409 и прочее — не повод падать
            failures += 1
            delay = min(60, 5 * failures)
            logger.warning("Ошибка опроса Telegram (%s). Повтор через %s с", exc, delay)
            await asyncio.sleep(delay)


_task: Optional[asyncio.Task] = None


def start() -> None:
    """Запустить опрос, если задан токен и бот ещё не запущен."""
    global _task
    if not settings.telegram_bot_token:
        logger.info("Telegram-бот не настроен — опрос не запущен")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_poll_loop())


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
    _task = None


async def restart() -> None:
    """Перезапустить опрос после смены токена в настройках."""
    await stop()
    start()
