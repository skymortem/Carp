"""Моточасы из журнала событий StarLine.

Почему не `state.motohrs`: на части устройств (проверено на S96v2, CAN 4852)
это поле приходит `null` всегда — счётчик наработки не заполняется, и в базе
моточасы оставались пустыми. Журнал событий при этом отдаёт пары
«Двигатель запущен» / «Двигатель остановлен» с точностью до секунды, поэтому
наработку Carp считает сам и складывает интервалы работы двигателя.

Точка отсчёта (baseline):
  * первый снимок с пробегом в базе — «с момента, как машина появилась в Carp»,
    а не «последние 30 дней»;
  * но не глубже журнала StarLine: он хранится 30 дней, раньше считать нечего;
  * выбранная дата фиксируется в app_state и больше не сдвигается — иначе
    счётчик «уезжал» бы вперёд и старые моточасы пропадали.

Правила:
  * накопительные моточасы = сумма интервалов «двигатель работал» до момента T;
  * повторный разбор того же окна не создаёт дублей (ключ car_id + started_at);
  * незакрытая сессия считается до текущего момента — двигатель ещё работает;
  * если устройство всё-таки отдаёт `state.motohrs`, его значение главнее.
"""
import bisect
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import delete, func, select

from app.models.car import AppState, EngineSession, StarSnap

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

JOURNAL_DAYS = 30          # глубина журнала, которую отдаёт StarLine
OVERLAP_SECONDS = 6 * 3600  # перекрытие окон: события на стыке не теряются


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _ts_to_dt(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)


def _dt_to_ts(value: datetime) -> int:
    return int(value.replace(tzinfo=timezone.utc).timestamp())


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


class Cumulative:
    """Ответ «сколько наработано на момент T» по списку сессий.

    Префиксные суммы считаются один раз, дальше каждый снимок — бинарный
    поиск: иначе пересчёт истории был бы O(снимки × сессии).
    """

    def __init__(self, sessions: Iterable[EngineSession], now: Optional[datetime] = None):
        self._now = utc_naive(now or datetime.now(timezone.utc))
        self._starts: list[datetime] = []
        self._ends: list[datetime] = []
        self._prefix: list[float] = []

        total = 0.0
        for row in sorted(sessions, key=lambda item: utc_naive(item.started_at)):
            started = utc_naive(row.started_at)
            ended = utc_naive(row.ended_at) if row.ended_at is not None else self._now
            if ended <= started:
                continue
            total += (ended - started).total_seconds() / 60
            self._starts.append(started)
            self._ends.append(ended)
            self._prefix.append(total)

    @property
    def first(self) -> Optional[datetime]:
        return self._starts[0] if self._starts else None

    def at(self, moment: Optional[datetime] = None) -> float:
        """Наработка в минутах на момент `moment` (по умолчанию — сейчас)."""
        limit = utc_naive(moment) if moment is not None else self._now
        index = bisect.bisect_right(self._starts, limit) - 1
        if index < 0:
            return 0.0
        before = self._prefix[index - 1] if index else 0.0
        started, ended = self._starts[index], self._ends[index]
        return before + max(0.0, (min(ended, limit) - started).total_seconds() / 60)


def _baseline_key(car_id: int) -> str:
    return f"engine_baseline_{car_id}"


async def baseline(db, car, now: Optional[datetime] = None) -> datetime:
    """Дата, с которой считаются моточасы. Выбирается один раз и не сдвигается."""
    now = utc_naive(now or datetime.now(timezone.utc))
    raw = await db.scalar(select(AppState.value).where(AppState.key == _baseline_key(car.id)))
    if raw:
        try:
            return utc_naive(datetime.fromisoformat(raw))
        except ValueError:
            logger.warning("Не читается сохранённая точка отсчёта моточасов: %r", raw)

    floor = now - timedelta(days=JOURNAL_DAYS)
    first_record = await db.scalar(
        select(func.min(StarSnap.ts)).where(
            StarSnap.car_id == car.id, StarSnap.mileage_km.isnot(None)
        )
    )
    moment = max(utc_naive(first_record), floor) if first_record else floor
    moment = moment.replace(microsecond=0)
    db.add(AppState(key=_baseline_key(car.id), value=moment.isoformat()))
    await db.commit()
    logger.info("Моточасы: точка отсчёта для car=%s — %s", car.id, moment)
    return moment


async def sync_sessions(db, client, car, now: Optional[datetime] = None) -> tuple[int, int]:
    """Дотянуть сессии работы двигателя из журнала StarLine.

    Возвращает (новых/уточнённых, удалённых). Удаляются сессии раньше точки
    отсчёта — это разовая чистка данных, накопленных прежней версией, которая
    считала от «последних 30 дней» независимо от истории Carp.
    """
    if not car.starline_device_id:
        return 0, 0

    now = utc_naive(now or datetime.now(timezone.utc))
    start_point = await baseline(db, car, now)

    last_start = await db.scalar(
        select(EngineSession.started_at)
        .where(EngineSession.car_id == car.id)
        .order_by(EngineSession.started_at.desc())
        .limit(1)
    )
    if last_start is None:
        since = start_point
    else:
        since = max(utc_naive(last_start) - timedelta(seconds=OVERLAP_SECONDS), start_point)

    events = await client.get_events(car.starline_device_id, _dt_to_ts(since), _dt_to_ts(now))
    parsed = sessions_from_events(events)
    start_ts = _dt_to_ts(start_point)

    existing = {
        utc_naive(row.started_at): row
        for row in (await db.execute(
            select(EngineSession).where(EngineSession.car_id == car.id)
        )).scalars().all()
    }

    changed = 0
    for start, end in parsed:
        if end is not None and end <= start_ts:
            continue  # сессия целиком до точки отсчёта
        # «Запущен» мог случиться до точки отсчёта, а «остановлен» — после:
        # обрезаем начало, иначе сессия захватит время вне истории Carp.
        started_at = _ts_to_dt(max(start, start_ts))
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

    removed = await db.execute(
        delete(EngineSession).where(
            EngineSession.car_id == car.id,
            EngineSession.started_at < start_point,
        )
    )
    removed_count = removed.rowcount or 0
    await db.commit()
    return changed, removed_count


def total_minutes(sessions: Iterable[EngineSession], until: Optional[datetime] = None,
                  now: Optional[datetime] = None) -> float:
    """Сумма работы двигателя в минутах на момент `until` (по умолчанию — сейчас)."""
    return Cumulative(sessions, now).at(until)


async def cumulative_minutes(db, car_id: int, until: Optional[datetime] = None) -> Optional[int]:
    """Накопительные моточасы (минуты) или None, если сессий ещё нет."""
    rows = (await db.execute(
        select(EngineSession).where(EngineSession.car_id == car_id)
    )).scalars().all()
    if not rows:
        return None
    return int(round(Cumulative(rows).at(until)))


async def recompute_snapshots(db, car_id: int, limit: int = 20000) -> int:
    """Пересчитать motohours_minutes у снимков по актуальным сессиям.

    Нужен после первой синхронизации и после чистки сессий: история уже
    накоплена, а колонка либо пустая, либо посчитана по прежней точке отсчёта.
    Снимки раньше первой сессии остаются пустыми — «0 моточасов» соврал бы
    сильнее, чем прочерк.
    """
    rows = (await db.execute(
        select(EngineSession).where(EngineSession.car_id == car_id)
    )).scalars().all()
    if not rows:
        return 0

    series = Cumulative(rows)
    first = series.first
    snaps = (await db.execute(
        select(StarSnap).where(StarSnap.car_id == car_id)
        .order_by(StarSnap.ts).limit(limit)
    )).scalars().all()

    updated = 0
    for snap in snaps:
        ts = utc_naive(snap.ts)
        value = None if first is None or ts < first else int(round(series.at(ts)))
        if snap.motohours_minutes != value:
            snap.motohours_minutes = value
            updated += 1

    if updated:
        await db.commit()
    return updated
