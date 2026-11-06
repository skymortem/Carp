"""Свежесть данных: насколько последний снимок отстал от интервала сбора.

Без этого индикатора отвалившийся токен StarLine выглядит как «всё работает»:
дашборд показывает старые цифры, а ошибка живёт только в journalctl.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.car import StarSnap

# Порог «пора бить тревогу» относительно интервала сбора.
OK_FACTOR = 1.5      # штатно: сбор идёт раз в интервал, плюс запас на джиттер
LATE_FACTOR = 3.0    # один-два пропуска — жёлтый


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive_utcnow() -> datetime:
    """SQLite хранит DATETIME без таймзоны, сравнивать нужно наивные UTC."""
    return utcnow().replace(tzinfo=None)


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def describe_freshness(
    last_ts: Optional[datetime],
    interval_minutes: int,
    now: Optional[datetime] = None,
) -> dict:
    """Состояние: ok / late / stale / never + человекочитаемый возраст."""
    now = now or utcnow()
    interval = max(interval_minutes, 1)

    if last_ts is None:
        return {
            "state": "never",
            "label": "сбора ещё не было",
            "age_minutes": None,
            "age_text": "нет данных",
            "last_ts": None,
            "interval_minutes": interval,
        }

    now_naive = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    age_minutes = max(int((now_naive - _as_naive_utc(last_ts)).total_seconds() // 60), 0)

    # Наружу отдаём значение с явным UTC: иначе браузер прочитает ISO-строку
    # без смещения как локальное время и посчитает возраст с ошибкой на TZ.
    last_ts_utc = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc)
    last_ts_utc = last_ts_utc.astimezone(timezone.utc)

    if age_minutes <= interval * OK_FACTOR:
        state, label = "ok", "данные актуальны"
    elif age_minutes <= interval * LATE_FACTOR:
        state, label = "late", "сбор задерживается"
    else:
        state, label = "stale", "данные устарели"

    return {
        "state": state,
        "label": label,
        "age_minutes": age_minutes,
        "age_text": humanize_minutes(age_minutes),
        "last_ts": last_ts_utc,
        "interval_minutes": interval,
    }


def humanize_minutes(minutes: int) -> str:
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    days = hours // 24
    return f"{days} дн назад"


async def last_snapshot_ts(db: AsyncSession, car_id: int) -> Optional[datetime]:
    return await db.scalar(
        select(StarSnap.ts)
        .where(StarSnap.car_id == car_id)
        .order_by(StarSnap.ts.desc(), StarSnap.id.desc())
        .limit(1)
    )


async def car_freshness(db: AsyncSession, car_id: int) -> dict:
    return describe_freshness(
        await last_snapshot_ts(db, car_id),
        settings.collect_interval_minutes,
    )


def next_expected_at(last_ts: Optional[datetime], interval_minutes: int) -> Optional[datetime]:
    if last_ts is None:
        return None
    base = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc)
    return base + timedelta(minutes=interval_minutes)
