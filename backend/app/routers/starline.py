import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.car import Car, StarSnap
from app.services.auth import get_current_user_from_cookie
from app.services.starline import StarLineClient, StarLineError
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/starline", tags=["starline"])


class StarLineConfig(BaseModel):
    app_id: str
    app_secret: str
    login: str
    password: str
    device_id: Optional[str] = None  # можно указать вручную


@router.post("/connect")
async def connect_to_starline(
    body: StarLineConfig,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Подключить StarLine для текущего пользователя: авторизоваться, найти device_id."""
    try:
        client = StarLineClient(body.app_id, body.app_secret, body.login, body.password)
        slnet = client.auth()

        # Если device_id указан вручную — используем его
        if body.device_id:
            device_id = body.device_id
            device_name = "Car (manual)"

            # Проверим, что device_id валидный
            data = client.get_device_data(device_id)
            obd = data.get("obd") or {}
            mileage = obd.get("mileage", "N/A")
            fuel = obd.get("fuel_litres", "N/A")
        else:
            # Автоопределение: найдём устройство
            try:
                devices = client.get_user_devices()
            except StarLineError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Auto-detect failed: {e}. Try providing device_id manually.",
                )

            if not devices:
                raise HTTPException(
                    status_code=404,
                    detail="No StarLine devices found. Try providing device_id manually.",
                )

            device = devices[0]
            device_id = device.get("device_id")
            device_name = device.get("alias") or device.get("typename") or "Car"

            data = client.get_device_data(device_id)
            obd = data.get("obd") or {}
            mileage = obd.get("mileage", "N/A")
            fuel = obd.get("fuel_litres", "N/A")

        # Сохраним или обновим информацию о машине пользователя
        result = await db.execute(select(Car).where(Car.user_id == user.id).limit(1))
        car = result.scalar_one_or_none()

        if car:
            car.starline_device_id = device_id
        else:
            car = Car(
                user_id=user.id,
                name=device_name,
                starline_device_id=device_id,
                initial_odometer=mileage if isinstance(mileage, int) else 0,
            )
            db.add(car)

        await db.commit()
        await db.refresh(car)

        # Сохраняем первый снепшот
        snapshot = StarLineClient.parse_snapshot(data)
        snap = StarSnap(car_id=car.id, **snapshot)
        db.add(snap)

        # Автоустановка начальных моточасов при первом подключении
        if car.initial_motohours == 0 and snapshot.get("motohours_minutes"):
            car.initial_motohours = snapshot["motohours_minutes"]

        await db.commit()
        await db.refresh(snap)

        return {
            "ok": True,
            "device_id": device_id,
            "device_name": device_name,
            "mileage_km": mileage,
            "fuel_litres": fuel,
            "car_id": car.id,
        }

    except StarLineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/fetch")
async def fetch_data(
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Одноразовый ручной сбор данных со StarLine и сохранение в БД."""
    # Проверим, есть ли у пользователя машина со StarLine
    result = await db.execute(
        select(Car).where(Car.user_id == user.id, Car.starline_device_id.isnot(None)).limit(1)
    )
    car = result.scalar_one_or_none()
    if not car:
        raise HTTPException(status_code=400, detail="No StarLine-connected car found. Call /connect first.")

    # Берём credentials из .env
    if not settings.starline_app_id:
        raise HTTPException(status_code=400, detail="StarLine not configured in .env")

    client = StarLineClient(
        settings.starline_app_id,
        settings.starline_app_secret,
        settings.starline_login or "",
        settings.starline_password or "",
    )

    try:
        data = client.get_device_data(car.starline_device_id)
        snapshot = StarLineClient.parse_snapshot(data)

        # Если mileage нет в OBD — пробуем через getObdData (CAN-пробег)
        if snapshot["mileage_km"] is None:
            try:
                obd_data = client.get_obd_data(car.starline_device_id)
                if isinstance(obd_data, dict):
                    obd_result = obd_data.get("data") or obd_data.get("result") or obd_data
                    if isinstance(obd_result, list) and len(obd_result) > 0:
                        last = obd_result[-1]
                        snapshot["mileage_km"] = last.get("mileage") or last.get("value") or last.get("odometer")
                    elif isinstance(obd_result, dict):
                        snapshot["mileage_km"] = obd_result.get("mileage") or obd_result.get("value") or obd_result.get("odometer")
            except Exception as e:
                logger.warning("getObdData failed: %s", e)

        # Если всё ещё нет — пробуем через position
        if snapshot["mileage_km"] is None:
            try:
                pos_data = client.get_last_position(car.starline_device_id)
            except Exception as e:
                logger.warning("getPosition failed: %s", e)

        snap = StarSnap(car_id=car.id, **snapshot)
        db.add(snap)
        await db.commit()
        await db.refresh(snap)

        return {"ok": True, "snap_id": snap.id, "data": {
            "ts": snapshot["ts"].isoformat(),
            "mileage_km": snapshot["mileage_km"],
            "fuel_litres": snapshot["fuel_litres"],
            "fuel_percent": snapshot["fuel_percent"],
            "speed_kmh": snapshot["speed_kmh"],
            "engine_on": snapshot["engine_on"],
            "is_moving": snapshot["is_moving"],
        }}

    except StarLineError as e:
        raise HTTPException(status_code=502, detail=f"StarLine API error: {e}")


@router.post("/test-device")
async def test_device(
    body: StarLineConfig,
    user: User = Depends(get_current_user_from_cookie),
):
    """Проверить, работает ли device_id: авторизоваться и попробовать получить данные."""
    try:
        client = StarLineClient(body.app_id, body.app_secret, body.login, body.password)
        client.auth()

        if not body.device_id:
            return {"ok": False, "error": "No device_id provided"}

        data = client.get_device_data(body.device_id)
        obd = data.get("obd") or {}
        position = data.get("position") or {}
        common = data.get("common") or {}
        state = data.get("state") or {}

        return {
            "ok": True,
            "device_id": body.device_id,
            "data": {
                "mileage_km": obd.get("mileage"),
                "fuel_litres": obd.get("fuel_litres"),
                "fuel_percent": obd.get("fuel_percent"),
                "lat": position.get("y"),
                "lon": position.get("x"),
                "speed_kmh": position.get("s"),
                "engine_on": bool(state.get("ign")),
                "is_armed": bool(state.get("arm")),
                "battery_v": common.get("battery"),
                "ctemperature": common.get("ctemp"),
                "etemperature": common.get("etemp"),
            },
            "raw_keys": list(data.keys()),
        }

    except StarLineError as e:
        return {"ok": False, "error": str(e), "device_id": body.device_id}


@router.get("/status")
async def status(
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Посмотреть статус подключения StarLine."""
    result = await db.execute(
        select(Car).where(Car.user_id == user.id, Car.starline_device_id.isnot(None)).limit(1)
    )
    car = result.scalar_one_or_none()

    if not car:
        return {"connected": False}

    return {
        "connected": True,
        "car_id": car.id,
        "car_name": car.name,
        "device_id": car.starline_device_id,
        "snaps_count": len(car.snaps) if car.snaps else 0,
    }


@router.post("/reset-motohours")
async def reset_motohours(
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Сбросить счётчик моточасов — установить текущие как точку отсчёта."""
    result = await db.execute(
        select(Car).where(Car.user_id == user.id, Car.starline_device_id.isnot(None)).limit(1)
    )
    car = result.scalar_one_or_none()
    if not car:
        raise HTTPException(status_code=400, detail="No StarLine car found")

    # Получаем текущие моточасы из последнего снепшота
    snap_result = await db.execute(
        select(StarSnap).where(StarSnap.car_id == car.id)
        .order_by(StarSnap.ts.desc()).limit(1)
    )
    snap = snap_result.scalar_one_or_none()
    if not snap or snap.motohours_minutes is None:
        raise HTTPException(status_code=400, detail="No motohours data yet. Start the engine and fetch first.")

    car.initial_motohours = snap.motohours_minutes
    car.motohours_reset_at = snap.ts
    await db.commit()

    return {"ok": True, "initial_motohours": car.initial_motohours, "reset_at": snap.ts.isoformat() if snap.ts else None}