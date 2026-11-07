"""Моточасы из журнала событий: точка отсчёта, сессии и накопительный счётчик."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.car import Car, EngineSession, StarSnap
from app.services import enginetime

START = 1037   # Двигатель запущен
STOP = 1042    # Двигатель остановлен


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(moment):
    """Наивный UTC → unix-секунды (обычный .timestamp() посчитал бы локальное время)."""
    return int(moment.replace(tzinfo=timezone.utc).timestamp())


def _events(*pairs):
    return [{"type": code, "timestamp": ts} for ts, code in pairs]


def test_sessions_pair_start_and_stop():
    events = _events((1000, START), (1300, STOP), (2000, START), (2300, STOP))
    assert enginetime.sessions_from_events(events) == [(1000, 1300), (2000, 2300)]


def test_sessions_are_order_independent_and_skip_repeats():
    """Журнал приходит от новых к старым; повторные «запущен» не плодят сессии."""
    events = _events(
        (2300, STOP), (2000, START), (2100, 601), (1700, STOP),
        (1300, STOP), (1000, START), (1000, 1040),
    )
    assert enginetime.sessions_from_events(events) == [(1000, 1300), (2000, 2300)]


def test_stop_without_start_is_ignored_and_open_session_kept():
    events = _events((500, STOP), (1000, START))
    assert enginetime.sessions_from_events(events) == [(1000, None)]


def test_cumulative_counts_closed_and_open_sessions():
    now = _now()
    rows = [
        EngineSession(car_id=1, started_at=now - timedelta(hours=4),
                      ended_at=now - timedelta(hours=3, minutes=30)),
        EngineSession(car_id=1, started_at=now - timedelta(hours=1), ended_at=None),
    ]
    assert round(enginetime.Cumulative(rows, now).at(now)) == 90  # 30 мин + 60 мин открытой


def test_cumulative_stops_at_snapshot_time():
    """Снимок двухчасовой давности не должен получать сегодняшнюю наработку."""
    now = _now()
    row = EngineSession(car_id=1, started_at=now - timedelta(hours=5),
                        ended_at=now - timedelta(hours=3))
    assert enginetime.Cumulative([row], now).at(now - timedelta(hours=4)) == 60


@pytest.mark.asyncio
async def test_baseline_is_first_snapshot_with_mileage(owner):
    """Считаем от появления машины в Carp, а не от «последних 30 дней»."""
    from app.database import async_session

    first = (_now() - timedelta(days=2)).replace(microsecond=0)
    async with async_session() as db:
        car = await db.get(Car, 1)
        db.add(StarSnap(car_id=1, ts=first, mileage_km=57000))
        db.add(StarSnap(car_id=1, ts=_now() - timedelta(hours=1), mileage_km=57303))
        await db.commit()

        assert await enginetime.baseline(db, car) == first
        # Дата фиксируется: новые снимки её не сдвигают.
        db.add(StarSnap(car_id=1, ts=_now(), mileage_km=57310))
        await db.commit()
        assert await enginetime.baseline(db, car) == first


@pytest.mark.asyncio
async def test_baseline_is_capped_by_journal_depth(owner):
    """Журнал StarLine — 30 дней: более старую историю считать не по чему."""
    from app.database import async_session

    async with async_session() as db:
        car = await db.get(Car, 1)
        db.add(StarSnap(car_id=1, ts=_now() - timedelta(days=90), mileage_km=50000))
        await db.commit()

        moment = await enginetime.baseline(db, car)
        expected = _now() - timedelta(days=enginetime.JOURNAL_DAYS)
        assert abs((moment - expected).total_seconds()) < 120


class FakeClient:
    def __init__(self, events):
        self.events = events
        self.windows = []

    async def get_events(self, device_id, period_start, period_end):
        self.windows.append((period_start, period_end))
        return self.events


@pytest.mark.asyncio
async def test_sync_is_idempotent_and_closes_open_session(owner):
    from app.database import async_session

    async with async_session() as db:
        car = await db.get(Car, 1)
        db.add(StarSnap(car_id=1, ts=_now() - timedelta(hours=6), mileage_km=57000))
        await db.commit()

        base = _ts(_now() - timedelta(hours=5))
        client = FakeClient(_events((base, START), (base + 1800, STOP)))

        assert await enginetime.sync_sessions(db, client, car) == (1, 0)
        # Повторный разбор того же окна не создаёт дублей.
        assert await enginetime.sync_sessions(db, client, car) == (0, 0)
        assert await enginetime.cumulative_minutes(db, car.id) == 30

        # Журнал догнал остановку уже открытой сессии.
        client.events = _events((base, START), (base + 3600, STOP))
        assert await enginetime.sync_sessions(db, client, car) == (1, 0)
        assert await enginetime.cumulative_minutes(db, car.id) == 60


@pytest.mark.asyncio
async def test_sessions_before_baseline_are_dropped_and_trimmed(owner):
    """Прежняя версия считала от «последних 30 дней» — лишнее надо убрать."""
    from app.database import async_session

    first_record = (_now() - timedelta(days=2)).replace(microsecond=0)
    old_start = first_record - timedelta(days=20)
    async with async_session() as db:
        car = await db.get(Car, 1)
        db.add(StarSnap(car_id=1, ts=first_record, mileage_km=57000))
        db.add(EngineSession(car_id=1, started_at=old_start,
                             ended_at=old_start + timedelta(hours=5)))
        await db.commit()

        # «Запущен» до точки отсчёта, «остановлен» после: сессия обрезается.
        client = FakeClient(_events(
            (_ts(first_record - timedelta(hours=1)), START),
            (_ts(first_record + timedelta(hours=1)), STOP),
        ))
        changed, removed = await enginetime.sync_sessions(db, client, car)
        assert (changed, removed) == (1, 1)
        assert await enginetime.cumulative_minutes(db, car.id) == 60

        rows = (await db.execute(select(EngineSession))).scalars().all()
        assert len(rows) == 1
        assert enginetime.utc_naive(rows[0].started_at) == first_record


@pytest.mark.asyncio
async def test_recompute_fills_and_nulls_snapshots(owner):
    from app.database import async_session

    first = (_now() - timedelta(hours=4)).replace(microsecond=0)
    async with async_session() as db:
        db.add(EngineSession(car_id=1, started_at=first, ended_at=first + timedelta(minutes=40)))
        db.add(StarSnap(car_id=1, ts=first - timedelta(hours=1), mileage_km=1))   # до сессий
        db.add(StarSnap(car_id=1, ts=first + timedelta(minutes=20), mileage_km=2))
        db.add(StarSnap(car_id=1, ts=first + timedelta(hours=2), mileage_km=3,
                        motohours_minutes=9999))                                   # завышено
        await db.commit()

    async with async_session() as db:
        assert await enginetime.recompute_snapshots(db, 1) == 2
        rows = (await db.execute(select(StarSnap).order_by(StarSnap.ts))).scalars().all()
        assert rows[0].motohours_minutes is None
        assert rows[1].motohours_minutes == 20
        assert rows[2].motohours_minutes == 40
        # Повторный прогон ничего не меняет.
        assert await enginetime.recompute_snapshots(db, 1) == 0
