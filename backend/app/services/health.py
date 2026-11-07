"""Последняя ошибка сбора — чтобы её было видно из браузера.

Ошибка StarLine раньше жила только в journalctl: владелец видел редирект на
`alert()` или, хуже, просто прочерки. Теперь причина сохраняется в
`app_state` и показывается баннером на дашборде, а успешный сбор её стирает.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.models.car import AppState

ERROR_KEY = "last_collect_error"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def record_error(db, message: str) -> None:
    """Сохранить текст ошибки (без секретов: только сообщение исключения)."""
    text = (message or "").strip()[:500] or "неизвестная ошибка"
    row = await db.get(AppState, ERROR_KEY)
    payload = json.dumps({"at": _now_iso(), "text": text}, ensure_ascii=False)
    if row is None:
        db.add(AppState(key=ERROR_KEY, value=payload))
    else:
        row.value = payload
    await db.commit()


async def clear_error(db) -> None:
    row = await db.get(AppState, ERROR_KEY)
    if row is not None:
        await db.delete(row)
        await db.commit()


async def last_error(db) -> Optional[dict]:
    raw = await db.scalar(select(AppState.value).where(AppState.key == ERROR_KEY))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return {"at": None, "text": raw}
    if not isinstance(data, dict):
        return None
    return data
