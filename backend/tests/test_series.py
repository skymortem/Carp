"""Свежесть данных и агрегаты для графиков."""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import async_session
from app.models.car import StarSnap
from app.services.freshness import describe_freshness, humanize_minutes

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def test_freshness_never_collected():
    state = describe_freshness(None, 60, now=NOW)
    assert state["state"] == "never"
    assert state["age_minutes"] is None


def test_freshness_thresholds():
    fresh = describe_freshness(NOW - timedelta(minutes=50), 60, now=NOW)
    late = describe_freshness(NOW - timedelta(minutes=120), 60, now=NOW)
    stale = describe_freshness(NOW - timedelta(hours=5), 60, now=NOW)

    assert (fresh["state"], late["state"], stale["state"]) == ("ok", "late", "stale")
    assert fresh["age_minutes"] == 50
    assert stale["age_text"] == "5 ч назад"


def test_freshness_accepts_naive_sqlite_datetime():
    """SQLite отдаёт ts без таймзоны — сравнение не должно падать."""
    naive = (NOW - timedelta(minutes=10)).replace(tzinfo=None)
    state = describe_freshness(naive, 60, now=NOW)
    assert state["state"] == "ok"
    assert state["age_minutes"] == 10
    # Наружу — всегда с явным UTC, иначе JS посчитает возраст со сдвигом TZ.
    assert state["last_ts"].isoformat().endswith("+00:00")


def test_humanize_minutes():
    assert humanize_minutes(0) == "только что"
    assert humanize_minutes(37) == "37 мин назад"
    assert humanize_minutes(3 * 60) == "3 ч назад"
    assert humanize_minutes(50 * 60) == "2 дн назад"


@pytest.mark.asyncio
async def test_status_reports_freshness(owner):
    async with async_session() as db:
        db.add(StarSnap(car_id=1, ts=datetime.now(timezone.utc).replace(tzinfo=None), mileage_km=500))
        await db.commit()

    payload = (await owner.get("/starline/status")).json()
    assert payload["freshness"]["state"] == "ok"
    assert payload["last_snap_ts"] is not None
    assert payload["collect_interval_minutes"] >= 1


@pytest.mark.asyncio
async def test_series_requires_auth(client):
    assert (await client.get("/api/series")).status_code == 401


@pytest.mark.asyncio
async def test_series_returns_daily_aggregates(owner):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as db:
        # Вчера: 100 → 130 → 160 км (3 снимка), сегодня: 160 → 220 км (2 снимка).
        for days_ago, values in ((1, (100, 130, 160)), (0, (160, 220))):
            ts = now - timedelta(days=days_ago)
            for value in values:
                db.add(StarSnap(car_id=1, ts=ts, mileage_km=value, fuel_litres=40.0))
        await db.commit()

    response = await owner.get("/api/series?days=30")
    assert response.status_code == 200
    payload = response.json()

    assert payload["bucket"] == "day"
    assert payload["total_points"] == len(payload["points"]) == 2
    by_day = {p["d"]: p for p in payload["points"]}
    today_key = now.date().isoformat()
    yesterday_key = (now - timedelta(days=1)).date().isoformat()

    assert by_day[today_key]["mileage_min"] == 160 and by_day[today_key]["mileage_max"] == 220
    assert by_day[yesterday_key]["mileage_min"] == 100 and by_day[yesterday_key]["mileage_max"] == 160
    assert by_day[yesterday_key]["n"] == 3 and by_day[today_key]["n"] == 2


@pytest.mark.asyncio
async def test_series_respects_window(owner):
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
    async with async_session() as db:
        db.add(StarSnap(car_id=1, ts=old, mileage_km=10))
        await db.commit()

    assert (await owner.get("/api/series?days=30")).json()["total_points"] == 0
    assert (await owner.get("/api/series?days=800")).json()["total_points"] == 1


@pytest.mark.asyncio
async def test_dashboard_renders_freshness_badge(owner):
    async with async_session() as db:
        db.add(StarSnap(car_id=1, ts=datetime.now(timezone.utc).replace(tzinfo=None), mileage_km=500))
        await db.commit()

    html = (await owner.get("/dashboard")).text
    # Таблица последних записей и график переехали на /reports, поэтому
    # проверяем индикаторы, которые остались на дашборде.
    assert "Моточасы" in html
    assert "Температура" in html
