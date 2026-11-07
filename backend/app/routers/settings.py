"""Настройки Telegram-бота: токен, chat_id, проверка связи.

Токен и chat_id живут в .env (как и StarLine-креды) — их пишет страница
/setup, читает config.settings. Отдаём наружу только факт «настроено» и
маскированный chat_id: токен в ответе API не нужен никому.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import persist_settings, settings
from app.models.user import User
from app.services import telegram
from app.services.auth import get_current_user_from_cookie

router = APIRouter(prefix="/settings", tags=["settings"])


class TelegramConfig(BaseModel):
    bot_token: Optional[str] = Field(default=None, max_length=200)
    chat_id: Optional[str] = Field(default=None, max_length=64)


def _mask(chat_id: Optional[str]) -> Optional[str]:
    if not chat_id:
        return None
    text = str(chat_id)
    return text[:3] + "…" + text[-2:] if len(text) > 6 else "…"


@router.get("/telegram")
async def telegram_status(user: User = Depends(get_current_user_from_cookie)):
    return {
        "configured": bool(settings.telegram_bot_token),
        "bot_token_set": bool(settings.telegram_bot_token),
        "chat_id": _mask(settings.telegram_chat_id),
        "chat_id_set": bool(settings.telegram_chat_id),
        "fuel_fill_min_litres": settings.fuel_fill_min_litres,
    }


@router.post("/telegram")
async def save_telegram(
    body: TelegramConfig,
    user: User = Depends(get_current_user_from_cookie),
):
    """Сохранить токен и chat_id, перезапустить опрос."""
    values: dict[str, str] = {}
    if body.bot_token is not None:
        token = body.bot_token.strip()
        # Пустое поле — «не менять», а не «стереть»: токен нельзя показать
        # обратно в форме, и стереть его случайной отправкой формы легко.
        if token:
            values["telegram_bot_token"] = token
    if body.chat_id is not None:
        values["telegram_chat_id"] = body.chat_id.strip()

    if values:
        persist_settings(values)
    await telegram.restart()
    return await telegram_status(user)


@router.post("/telegram/test")
async def send_test(user: User = Depends(get_current_user_from_cookie)):
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return {"ok": False, "detail": "Сначала укажи токен бота и chat_id"}
    sent = await telegram.send_message(
        "🚗 Carp на связи. Здесь будут вопросы про цену литра после заправки."
    )
    return {"ok": sent, "detail": None if sent else "Telegram не принял сообщение"}
