"""Страница «Отчёты»: пробег по месяцам, расходы на топливо и цена литра."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.car import FuelFill


def _fill(days_ago: float, volume: float, price: float | None, status: str = "done"):
    return FuelFill(
        car_id=1,
        detected_at=(datetime.now(timezone.utc).replace(tzinfo=None)
                     - timedelta(days=days_ago)).replace(microsecond=0),
        prev_litres=10.0,
        new_litres=10.0 + volume,
        volume_litres=volume,
        price_per_litre=price,
        total_cost=round(price * volume, 2) if price is not None else None,
        status=status,
    )


@pytest.mark.asyncio
async def test_reports_page_requires_login(client):
    response = await client.get("/reports", follow_redirects=False)
    assert response.status_code in (302, 303, 401)


@pytest.mark.asyncio
async def test_reports_page_shows_three_sections(owner):
    response = await owner.get("/reports")
    assert response.status_code == 200
    page = response.text
    for heading in ("Пробег по месяцам", "Расходы на топливо по месяцам", "Цена топлива"):
        assert heading in page
    assert "mileageChart" in page and "fuelCostChart" in page and "priceChart" in page


@pytest.mark.asyncio
async def test_dashboard_no_longer_has_charts_or_last_records(owner):
    page = (await owner.get("/dashboard")).text
    assert "Пробег по месяцам" not in page
    assert "Последние записи" not in page
    assert "/reports" in page  # ссылка на отчёты вместо графиков


@pytest.mark.asyncio
async def test_fuel_report_sums_costs_per_month(owner):
    from app.database import async_session

    today = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as db:
        db.add(_fill(1, 40.0, 60.0))    # текущий месяц: 2400
        db.add(_fill(2, 30.0, 62.0))    # текущий месяц: 1860
        db.add(_fill(35, 50.0, 55.0))   # прошлый месяц: 2750
        db.add(_fill(3, 20.0, None, status="awaiting_price"))  # без цены — не в счёт
        await db.commit()

    payload = (await owner.get("/api/reports/fuel?days=365")).json()
    by_month = {item["m"]: item for item in payload["months"]}
    current = today.strftime("%Y-%m")
    previous = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    assert by_month[current]["cost"] == 4260.0
    assert by_month[current]["litres"] == 70.0
    assert by_month[current]["fills"] == 2
    assert by_month[previous]["cost"] == 2750.0
    assert payload["totals"]["cost"] == 7010.0

    # Цена литра — по каждой заправке, без записей без цены.
    assert [item["price"] for item in payload["prices"]] == [55.0, 62.0, 60.0]


@pytest.mark.asyncio
async def test_fuel_report_is_empty_without_fills(owner):
    payload = (await owner.get("/api/reports/fuel")).json()
    assert payload["months"] == []
    assert payload["prices"] == []
    assert payload["totals"] == {"cost": 0.0, "litres": 0.0}


@pytest.mark.asyncio
async def test_fuel_report_respects_window(owner):
    from app.database import async_session

    async with async_session() as db:
        db.add(_fill(400, 40.0, 50.0))  # старше года
        db.add(_fill(1, 10.0, 50.0))
        await db.commit()

    payload = (await owner.get("/api/reports/fuel?days=365")).json()
    assert sum(item["cost"] for item in payload["months"]) == 500.0
    assert len(payload["prices"]) == 1
