"""Моточасы из журнала событий StarLine.

Почему не `state.motohrs`: на части устройств (проверено на S96v2, CAN 4852)
это поле приходит `null` всегда — счётчик наработки не заполняется, и в базе
моточасы оставались пустыми. Журнал событий при этом отдаёт пары
«Двигатель запущен» / «Двигатель остановлен» с точностью до секунды, поэтому
наработку Carp считает сам и складывает интервалы работы двигателя.

Правила:
  * накопительные моточасы = сумма интервалов «двигатель работал» до момента T;
  * повторный разбор того же окна не создаёт дублей (ключ car_id + started_at);
  * незакрытая сессия считается до текущего момента — двигатель ещё работает;
  * если устройство всё-таки отдаёт `state.motohrs`, его значение главнее.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import select

from app.models.car import EngineSession

logger = logging.getLogger(__name__)

# Коды событий StarLine. Основа — 1037/1042 («Двигатель запущен/остановлен»),
# остальные варианты пусков (с брелка, с сервера, дистанционные, по условию)
# добавлены на случай, если конкретное устройство шлёт только их.
ENGINE_ON = {
    601, 603, 604,                     # общие «двигатель запущен»
    1037, 1040,                        # запуск / начал заводиться
    605, 613, 614, 615, 616, 617,
    618, 619, 620,                     # пуски по способу инициирования
}
ENGINE_OFF = {
    602,                               # общий «двигатель остановлен»
    1042,                              # остановлен
    606, 621, 622, 623, 624, 625,
    626, 627, 628,                     # остановки по способу инициирования
}

SYNC_MAX_DAYS = 30      # глубина журнала, которую отдаёт StarLine
OVERLAP_SECONDS = 6 * 3600  # перекрытие окон: события на стыке не теряются


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _ts_to_dt(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)


def sessions_from_events(events: Iterable[dict]) -> list[tuple[int, Optional[int]]]:
    """Свернуть журнал в интервалы работы двигателя.

    Возвращает список (start_ts, end_ts|None) в unix-секундах, по возрастанию.
    Второй элемент None означает «двигатель ещё работает».
    """
    ordered = sorted(
        (int(item["timestamp"]), int(item["type"]))
        for item in events
        if isinstance(item, dict) and item.get("timestamp") and item.get("type") is not None
    )
    sessions: list[tuple[int, Optional[int]]] = []
    started: Optional[int] = None

    for ts, code in ordered:
        if code in ENGINE_ON:
            # Повторный «запущен» внутри уже открытой сессии — то же событие,
            # пришедшее другим кодом (бортовой + дистанционный пуск).
            if started is None:
                started = ts
        elif code in ENGINE_OFF and started is not None:
            if ts >= started:
                sessions.append((started, ts))
            started = None

    if started is not None:
        sessions.append((started, None))
    return sessions


async def sync_sessions(db, client, car, now: Optional[datetime] = None) -> int:
    """Дотянуть сессии работы двигателя из журнала StarLine.

    Возвращает число новых/обновлённых интервалов. Ошибки сети пробрасывает
    вызывающему: сбор снимка из-за журнала падать не должен, но и молча
    терять моточасы не хочется — решение принимает collection.
    """
    if not car.starline_device_id:
        return 0

    now = utc_naive(now or datetime.now(timezone.utc))
    last_start = await db.scalar(
        select(EngineSession.started_at)
        .where(EngineSession.car_id == car.id)
        .order_by(EngineSession.started_at.desc())
        .limit(1)
    )
    if last_start is None:
        since = now - timedelta(days=SYNC_MAX_DAYS)
    else:
        since = utc_naive(last_start) - timedelta(seconds=OVERLAP_SECONDS)
        floor = now - timedelta(days=SYNC_MAX_DAYS)
        since = max(since, floor)

    start_ts = int(since.replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(now.replace(tzinfo=timezone.utc).timestamp())
    events = await client.get_events(car.starline_device_id, start_ts, end_ts)
    parsed = sessions_from_events(events)
    if not parsed:
        return 0

    existing = {
        utc_naive(row.started_at): row
        for row in (await db.execute(
            select(EngineSession).where(EngineSession.car_id == car.id)
        )).scalars().all()
    }

    changed = 0
    for start, end in parsed:
        started_at = _ts_to_dt(start)
        ended_at = _ts_to_dt(end) if end is not None else None
        row = existing.get(started_at)
        if row is None:
            row = EngineSession(car_id=car.id, started_at=started_at, ended_at=ended_at)
            db.add(row)
            existing[started_at] = row
            changed += 1
        elif ended_at is not None and (row.ended_at is None or utc_naive(row.ended_at) < ended_at):
            # Сессия была открытой (или закрылась раньше) — журнал уточнил конец.
            row.ended_at = ended_at
            changed += 1

    await db.commit()
    return changed


def total_minutes(sessions: Iterable[EngineSession], until: Optional[datetime] = None) -> float:
    """Сумма работы двигателя в минутах на момент `until` (по умолчанию — сейчас)."""
    limit = utc_naive(until) if until is not None else datetime.max
    minutes = 0.0
    for row in sessions:
        started = utc_naive(row.started_at)
        if started >= limit:
            continue
        ended = utc_naive(row.ended_at) if row.ended_at is not None else _open_end(limit)
        if ended <= started:
            continue
        minutes += (min(ended, limit) - started).total_seconds() / 60
    return minutes


def _open_end(limit: datetime) -> datetime:
    # Незакрытая сессия: считаем до текущего момента, но не дальше предела.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return min(now, limit)


async def cumulative_minutes(db, car_id: int, until: Optional[datetime] = None) -> Optional[int]:
    """Накопительные моточасы (минуты) или None, если сессий ещё нет."""
    rows = (await db.execute(
        select(EngineSession).where(EngineSession.car_id == car_id)
    )).scalars().all()
    if not rows:
        return None
    return int(round(total_minutes(rows, until)))


async def backfill_snapshots(db, car_id: int, limit: int = 5000) -> int:
    """Заполнить motohours_minutes у снимков, где его нет.

    Нужен один раз после включения подсчёта: история уже накоплена, а
    колонка пустая — иначе графики и «план ТО» видят прочерк до первого
    нового сбора.
    """
    from app.models.car import StarSnap

    rows = (await db.execute(
        select(EngineSession).where(EngineSession.car_id == car_id)
        .order_by(EngineSession.started_at)
    )).scalars().all()
    if not rows:
        return 0

    snaps = (await db.execute(
        select(StarSnap).where(StarSnap.car_id == car_id, StarSnap.motohours_minutes.is_(None))
        .order_by(StarSnap.ts).limit(limit)
    )).scalars().all()
    if not snaps:
        return 0

    updated = 0
    first_session = utc_naive(rows[0].started_at)
    for snap in snaps:
        ts = utc_naive(snap.ts)
        # До первой сессии считать нечего: «0 моточасов» соврало бы сильнее, чем пусто.
        if ts < first_session:
            continue
        snap.motohours_minutes = int(round(total_minutes(rows, ts)))
        updated += 1

    if updated:
        await db.commit()
    return updated
