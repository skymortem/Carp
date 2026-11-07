"""Диагностика на дашборде: температуры, АКБ, ошибки OBD, автозапуск."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.car import DeviceSettings, ObdError, StarSnap
from app.routers.dashboard import describe_battery
from app.services import weather
from app.services.collection import collect_full, sync_obd_errors
from app.services.starline import StarLineClient, StarLineError

# Фрагмент ответа /json/v3/device/{id}/data из официальной документации.
DEVICE_DATA = {
    "device_id": 357976061290482,
    "activity_ts": 1469790702,
    "position": {"x": 30.3, "y": 60.2, "s": 0, "is_move": 0},
    "common": {"gsm_lvl": 20, "ctemp": 21, "etemp": 87, "battery": 12.4, "battery_type": "volt",
               "ts": 1469790702},
    "obd": {"fuel_litres": 25, "fuel_percent": 50, "mileage": 71843, "ts": 1469790702},
    "state": {"ign": False, "arm": True, "r_start": True, "motohrs": 12345},
    "r_start": {
        "cron": {"has": True, "value": 1555932012},
        "battery": {"has": True, "value": 5},
        "period": {"has": True, "value": 8},
        "temp": {"has": True, "value": -3},
    },
}


def test_parse_r_start_official_example():
    parsed = StarLineClient.parse_r_start(DEVICE_DATA)
    assert parsed["r_start_active"] is True
    assert parsed["r_start_temp_on"] is True and parsed["r_start_temp"] == -3
    assert parsed["r_start_period_on"] is True and parsed["r_start_period_h"] == 8
    assert parsed["r_start_battery_on"] is True and parsed["r_start_battery"] == 5
    assert parsed["r_start_cron_on"] is True and parsed["r_start_cron"] == 1555932012


def test_parse_r_start_without_section():
    parsed = StarLineClient.parse_r_start({"state": {}})
    assert parsed["r_start_active"] is None
    assert parsed["r_start_temp"] is None and parsed["r_start_temp_on"] is None


def test_battery_heuristics():
    assert describe_battery(12.4, "volt") == {"value": 12.4, "unit": "В", "low": False, "kind": "volt"}
    assert describe_battery(11.2, "volt")["low"] is True
    # API иногда помечает проценты как volt — решаем по величине.
    assert describe_battery(66, "volt")["unit"] == "%"
    assert describe_battery(18, "percent")["unit"] == "%"
    assert describe_battery(None, "volt") is None
    assert describe_battery("мусор", None) is None


@pytest.mark.asyncio
async def test_sync_obd_errors_keeps_history(owner):
    async with async_session() as db:
        await sync_obd_errors(db, 1, [
            {"error": "P0171", "descriptions": ["Бедная смесь"], "warning_level": 2,
             "error_ts": 1700000000},
            {"error": "P0300", "descriptions": "Пропуски зажигания", "warning_level": 1},
        ])

    async with async_session() as db:
        rows = (await db.execute(select(ObdError).where(ObdError.car_id == 1))).scalars().all()
        assert {row.code for row in rows} == {"P0171", "P0300"}
        assert all(row.is_active for row in rows)
        lean = next(row for row in rows if row.code == "P0171")
        assert lean.description == "Бедная смесь"
        assert lean.first_seen == datetime(2023, 11, 14, 22, 13, 20)  # из error_ts
        mist = next(row for row in rows if row.code == "P0300")
        assert mist.description == "Пропуски зажигания"

    # Второй сбор: P0300 пропал, P0171 остался, добавился P0420.
    async with async_session() as db:
        await sync_obd_errors(db, 1, [
            {"error": "P0171", "descriptions": ["Бедная смесь"]},
            {"error": "P0420", "descriptions": ["Катализатор"]},
        ])

    async with async_session() as db:
        rows = (await db.execute(select(ObdError).where(ObdError.car_id == 1))).scalars().all()
        state = {row.code: row.is_active for row in rows}
        assert state == {"P0171": True, "P0300": False, "P0420": True}
        closed = next(row for row in rows if row.code == "P0300")
        assert closed.resolved_at is not None

    # Третий сбор: ошибка вернулась — это новый эпизод: старая строка осталась
    # закрытой, появилась новая активная (так даты каждого случая не теряются).
    async with async_session() as db:
        await sync_obd_errors(db, 1, [{"error": "P0300", "descriptions": "Пропуски зажигания"}])
    async with async_session() as db:
        rows = (await db.execute(select(ObdError).where(ObdError.car_id == 1, ObdError.code == "P0300"))).scalars().all()
        assert len(rows) == 2
        assert sorted(row.is_active for row in rows) == [False, True]


@pytest.mark.asyncio
async def test_collect_full_survives_weather_and_errors_failures(owner, monkeypatch):
    client = AsyncMock()
    client.get_device_data.return_value = DEVICE_DATA
    client.get_obd_errors.side_effect = StarLineError("нет доступа")

    async def no_weather(*args, **kwargs):
        return None

    monkeypatch.setattr(weather, "current_temperature", no_weather)

    snapshot, extras = await collect_full(client, "device1")
    assert snapshot["battery_v"] == 12.4
    assert snapshot["battery_type"] == "volt"
    assert snapshot["outside_temp"] is None
    # Ошибки не прочитались — extras.errors = None, историю не трогаем.
    assert extras["errors"] is None
    assert extras["r_start"]["r_start_temp"] == -3


@pytest.mark.asyncio
async def test_weather_failure_is_not_fatal(monkeypatch):
    class Boom:
        async def __aenter__(self): raise RuntimeError("нет сети")
        async def __aexit__(self, *exc): return False

    weather.clear_cache()
    monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **kwargs: Boom())
    assert await weather.current_temperature(55.75, 37.61) is None


@pytest.mark.asyncio
async def test_dashboard_shows_diagnostics_cards(owner):
    async with async_session() as db:
        db.add(StarSnap(car_id=1, ts=datetime.now(timezone.utc).replace(tzinfo=None),
                        mileage_km=1000, battery_v=12.4, battery_type="volt",
                        ctemperature=21, etemperature=87, outside_temp=-3))
        db.add(DeviceSettings(car_id=1, fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
                              r_start_active=True, r_start_temp=-3, r_start_temp_on=True,
                              r_start_period_h=8, r_start_period_on=True, r_start_battery_on=True))
        db.add(ObdError(car_id=1, code="P0171", description="Бедная смесь", warning_level=2,
                        first_seen=datetime.now(timezone.utc).replace(tzinfo=None),
                        last_seen=datetime.now(timezone.utc).replace(tzinfo=None), is_active=True))
        await db.commit()

    html = (await owner.get("/dashboard")).text
    assert "Температура" in html and "Двигатель" in html
    assert "12.4" in html and "в норме" in html
    assert "P0171" in html
    assert "Включён" in html and "по температуре -3 °C" in html and "каждые 8 ч" in html


@pytest.mark.asyncio
async def test_autostart_is_enabled_by_condition_flags(owner):
    """Реальный случай S96v2: state.r_start=false, но условия включены."""
    async with async_session() as db:
        db.add(StarSnap(car_id=1, ts=datetime.now(timezone.utc).replace(tzinfo=None), mileage_km=10))
        db.add(DeviceSettings(car_id=1, fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
                              r_start_active=False, r_start_temp=-15, r_start_temp_on=True,
                              r_start_battery=1180, r_start_battery_on=True))
        await db.commit()

    # В шаблоне число и единица разделены неразрывным пробелом — нормализуем.
    html = (await owner.get("/dashboard")).text.replace("\u00a0", " ")
    assert "Включён" in html
    assert "по температуре -15 °C" in html
    # Порог АКБ приходит в сотых вольта
    assert "11.8 В" in html
    assert "двигатель запущен автозапуском" not in html


@pytest.mark.asyncio
async def test_dashboard_reports_no_errors(owner):
    async with async_session() as db:
        db.add(StarSnap(car_id=1, ts=datetime.now(timezone.utc).replace(tzinfo=None), mileage_km=10))
        db.add(DeviceSettings(car_id=1, fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
                              r_start_active=False))
        await db.commit()

    html = (await owner.get("/dashboard")).text
    assert "Ошибок нет" in html
    assert "Выключен" in html
