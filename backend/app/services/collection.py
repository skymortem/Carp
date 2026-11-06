"""Сбор данных: один цикл для кнопки и планировщика.

Отсюда же накапливаются две вещи, которых нет в самом StarLine:
история ошибок OBD (API отдаёт только текущий список) и последние
настройки устройства (автозапуск).
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.models.car import DeviceSettings, ObdError, StarSnap
from app.services import weather
from app.services.starline import StarLineClient, StarLineError

logger = logging.getLogger(__name__)

save_lock = asyncio.Lock()

# Поля, которые не участвуют в проверке «такой снимок уже был»: погода
# приходит из другого источника и меняется сама по себе.
DEDUPE_IGNORED = {"outside_temp"}


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


async def collect_full(client, device_id: str) -> tuple[dict, dict]:
    """Снять показания и диагностику за один цикл.

    Возвращает (snapshot, extras), где extras:
      r_start — настройки автозапуска (dict),
      errors  — текущие ошибки OBD (list) либо None, если запрос не удался
                (тогда историю не трогаем, чтобы не закрыть активные коды).
    """
    device_data = await client.get_device_data(device_id)
    snapshot = StarLineClient.parse_snapshot(device_data)
    # An events window is NOT an accumulated engine-hours counter.

    snapshot["outside_temp"] = await weather.current_temperature(
        snapshot.get("lat"), snapshot.get("lon")
    )

    extras: dict = {"r_start": StarLineClient.parse_r_start(device_data), "errors": None}
    try:
        extras["errors"] = await client.get_obd_errors(device_id)
    except StarLineError as exc:
        logger.info("Ошибки OBD недоступны: %s", exc)

    return snapshot, extras


async def collect_snapshot(client, device_id):
    snapshot, _ = await collect_full(client, device_id)
    return snapshot


async def save_snapshot(db, car_id, snapshot):
    comparable = {k: v for k, v in snapshot.items() if k not in DEDUPE_IGNORED}
    async with save_lock:
        existing = await db.scalar(select(StarSnap).where(
            StarSnap.car_id == car_id,
            *(getattr(StarSnap, key) == value for key, value in comparable.items())
        ).limit(1))
        if existing is not None:
            return existing
        snap = StarSnap(car_id=car_id, **snapshot)
        db.add(snap)
        await db.commit()
        await db.refresh(snap)
        return snap


async def sync_obd_errors(db, car_id: int, items: list[dict]) -> None:
    """Свести текущий список ошибок с историей.

    Новые коды добавляются, встреченные обновляются, пропавшие закрываются
    (is_active=False), а не удаляются — история остаётся для дашборда и ТО.
    """
    now = utcnow_naive()
    rows = (await db.execute(
        select(ObdError).where(ObdError.car_id == car_id, ObdError.is_active.is_(True))
    )).scalars().all()
    active = {row.code: row for row in rows}
    seen: set[str] = set()

    for item in items:
        code = str(item.get("error") or "").strip()
        if not code:
            continue
        seen.add(code)
        description = _describe(item.get("descriptions"))
        warning = _integer(item.get("warning_level"))

        row = active.get(code)
        if row is not None:
            row.last_seen = now
            if description:
                row.description = description
            if warning is not None:
                row.warning_level = warning
            continue

        db.add(ObdError(
            car_id=car_id,
            code=code,
            description=description,
            warning_level=warning,
            first_seen=_as_naive_utc(item.get("error_ts")) or now,
            last_seen=now,
            is_active=True,
        ))

    for code, row in active.items():
        if code not in seen:
            row.is_active = False
            row.resolved_at = now

    await db.commit()


async def save_device_settings(db, car_id: int, values: dict) -> None:
    """Сохранить последние прочитанные настройки устройства (одна строка на машину)."""
    row = await db.scalar(select(DeviceSettings).where(DeviceSettings.car_id == car_id))
    if row is None:
        row = DeviceSettings(car_id=car_id)
        db.add(row)
    row.fetched_at = utcnow_naive()
    for key, value in values.items():
        if hasattr(row, key):
            setattr(row, key, value)
    await db.commit()


async def collect_and_store(client, db, car) -> tuple[dict, dict]:
    """Полный цикл: снимок + ошибки OBD + настройки автозапуска."""
    snapshot, extras = await collect_full(client, car.starline_device_id)
    snap = await save_snapshot(db, car.id, snapshot)

    if extras.get("errors") is not None:
        await sync_obd_errors(db, car.id, extras["errors"])
    if extras.get("r_start"):
        await save_device_settings(db, car.id, extras["r_start"])

    return snapshot, {"snap": snap, **extras}


def _describe(raw) -> Optional[str]:
    if isinstance(raw, (list, tuple)):
        parts = [str(item).strip() for item in raw if str(item).strip()]
        return "; ".join(parts)[:500] or None
    if isinstance(raw, str):
        return raw.strip()[:500] or None
    return None


def _integer(raw) -> Optional[int]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
