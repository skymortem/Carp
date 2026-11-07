"""Моточасы из журнала событий: пары «запуск/остановка» и накопительный счётчик."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.car import Car, EngineSession, StarSnap
from app.services import enginetime

START = 1037   # Двигатель запущен
STOP = 1042    # Двигатель остановлен


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


def test_total_minutes_counts_closed_and_open_sessions():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        EngineSession(car_id=1, started_at=now - timedelta(hours=4),
                      ended_at=now - timedelta(hours=3, minutes=30)),
        EngineSession(car_id=1, started_at=now - timedelta(hours=1), ended_at=None),
    ]
    assert round(enginetime.total_minutes(rows, now)) == 90  # 30 мин + 60 мин незакрытой


def test_total_minutes_stops_at_snapshot_time():
    """Снимок двухчасовой давности не должен получать сегодняшнюю наработку."""
    row = EngineSession(car_id=1, started_at=datetime(2026, 11, 8, 8, 0),
                        ended_at=datetime(2026, 11, 8, 10, 0))
    assert enginetime.total_minutes([row], datetime(2026, 11, 8, 9, 0)) == 60


class FakeClient:
    def __init__(self, events):
        self.events = events

    async def get_events(self, device_id, period_start, period_end):
        return self.events


@pytest.mark.asyncio
async def test_sync_sessions_is_idempotent_and_closes_open_session(owner):
    from app.database import async_session

    base = int((datetime.now(timezone.utc) - timedelta(hours=5)).timestamp())
    async with async_session() as db:
        car = await db.get(Car, 1)
        client = FakeClient(_events((base, START), (base + 1800, STOP)))

        assert await enginetime.sync_sessions(db, client, car) == 1
        # Повторный разбор того же окна не создаёт дублей.
        assert await enginetime.sync_sessions(db, client, car) == 0

        minutes = await enginetime.cumulative_minutes(db, car.id)
        assert minutes == 30

        # Журнал догнал остановку уже открытой сессии.
        client.events = _events((base, START), (base + 3600, STOP))
        assert await enginetime.sync_sessions(db, client, car) == 1
        assert await enginetime.cumulative_minutes(db, car.id) == 60


@pytest.mark.asyncio
async def test_backfill_fills_only_snapshots_after_first_session(owner):
    from app.database import async_session

    start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    async with async_session() as db:
        db.add(EngineSession(car_id=1, started_at=start, ended_at=start + timedelta(minutes=40)))
        db.add(StarSnap(car_id=1, ts=start - timedelta(hours=1)))                       # до сессий
        db.add(StarSnap(car_id=1, ts=start + timedelta(minutes=20)))                    # внутри
        await db.commit()

    async with async_session() as db:
        assert await enginetime.backfill_snapshots(db, 1) == 1
        rows = (await db.execute(
            StarSnap.__table__.select().order_by(StarSnap.ts)
        )).all()
        assert rows[0].motohours_minutes is None
        assert rows[1].motohours_minutes == 20
