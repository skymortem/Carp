"""Заправки: обнаружение прироста топлива и цена литра из Telegram."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.car import Car, FuelFill, StarSnap
from app.services import fuel, telegram


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _snapshot(ts, litres):
    return {"ts": ts, "fuel_litres": litres}


@pytest.mark.asyncio
async def test_small_increase_is_not_a_fill(owner):
    from app.database import async_session

    async with async_session() as db:
        car = await db.get(Car, 1)
        db.add(StarSnap(car_id=1, ts=_now_naive() - timedelta(hours=1), fuel_litres=40.0))
        await db.commit()

        fill = await fuel.detect_fill(db, car, _snapshot(_now_naive(), 42.0))
        assert fill is None
        assert await db.scalar(select(FuelFill)) is None


@pytest.mark.asyncio
async def test_increase_creates_fill_with_volume(owner):
    from app.database import async_session

    async with async_session() as db:
        car = await db.get(Car, 1)
        db.add(StarSnap(car_id=1, ts=_now_naive() - timedelta(hours=1), fuel_litres=18.0))
        await db.commit()

        fill = await fuel.detect_fill(db, car, _snapshot(_now_naive(), 60.0))
        assert fill is not None
        assert fill.volume_litres == 42.0
        assert fill.status == "awaiting_price"
        assert fill.total_cost is None


@pytest.mark.asyncio
async def test_second_step_of_same_refuel_updates_volume(owner):
    from app.database import async_session

    async with async_session() as db:
        car = await db.get(Car, 1)
        db.add(StarSnap(car_id=1, ts=_now_naive() - timedelta(hours=2), fuel_litres=20.0))
        await db.commit()

        first = await fuel.detect_fill(db, car, _snapshot(_now_naive() - timedelta(hours=1), 45.0))
        db.add(StarSnap(car_id=1, ts=_now_naive() - timedelta(hours=1), fuel_litres=45.0))
        await db.commit()

        second = await fuel.detect_fill(db, car, _snapshot(_now_naive(), 60.0))
        assert second.id == first.id
        assert second.volume_litres == 40.0
        assert len((await db.execute(FuelFill.__table__.select())).all()) == 1


@pytest.mark.asyncio
async def test_price_answer_saves_cost(owner, monkeypatch):
    sent = []

    async def fake_send(text, chat_id=None):
        sent.append(text)
        return True

    monkeypatch.setattr(telegram, "send_message", fake_send)

    from app.database import async_session
    async with async_session() as db:
        db.add(FuelFill(car_id=1, detected_at=_now_naive(), prev_litres=20.0,
                        new_litres=62.0, volume_litres=42.0, status="awaiting_price"))
        await db.commit()

    reply = await fuel.handle_message("59,90 ₽", "1")
    assert "42.0 л" in reply and "2515.80" in reply

    async with async_session() as db:
        fill = await db.scalar(select(FuelFill))
        assert fill.status == "done"
        assert fill.price_per_litre == 59.9
        assert fill.total_cost == 2515.8


@pytest.mark.asyncio
async def test_unparsable_answer_asks_again(owner):
    from app.database import async_session
    async with async_session() as db:
        db.add(FuelFill(car_id=1, detected_at=_now_naive(), prev_litres=0.0,
                        new_litres=40.0, volume_litres=40.0, status="awaiting_price"))
        await db.commit()

    reply = await fuel.handle_message("дорого", "1")
    assert "число" in reply.lower()

    async with async_session() as db:
        assert (await db.scalar(select(FuelFill))).status == "awaiting_price"


@pytest.mark.asyncio
async def test_skip_closes_the_question(owner):
    from app.database import async_session
    async with async_session() as db:
        db.add(FuelFill(car_id=1, detected_at=_now_naive(), prev_litres=0.0,
                        new_litres=30.0, volume_litres=30.0, status="awaiting_price"))
        await db.commit()

    assert "без цены" in (await fuel.handle_message("/skip", "1"))
    async with async_session() as db:
        fill = await db.scalar(select(FuelFill))
        assert fill.status == "skipped"
        assert fill.price_per_litre is None


def test_parse_price_variants():
    assert fuel.parse_price("59.9") == 59.9
    assert fuel.parse_price("59,90 ₽") == 59.9
    assert fuel.parse_price("цена 61 рубль") == 61
    assert fuel.parse_price("дорого") is None
    assert fuel.parse_price("0") is None
    # Сумма чека — не цена литра, такое лучше переспросить.
    assert fuel.parse_price("2515") is None
