import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.car import Car, StarSnap
from app.services.auth import get_current_user_from_cookie
from app.services.starline import StarLineClient, StarLineError
from app.config import persist_settings, settings

from app.services.starline import configured_client, cached_client
from app.services.collection import collect_and_store, save_snapshot
from app.services import health
from app.services.freshness import describe_freshness, last_snapshot_ts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/starline", tags=["starline"])


class StarLineApiRequest(BaseModel):
    """Запрос к тестовой консоли StarLine API."""
    method: str = "GET"  # GET или POST
    endpoint: str = ""   # например /json/v3/device/{device_id}/data
    body: dict = {}


@router.post("/api-raw")
async def starline_api_raw(
    req: StarLineApiRequest,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Выполнить диагностический запрос для подключенного устройства."""
    try:
        result = await db.execute(
            select(Car).where(Car.user_id == user.id, Car.starline_device_id.isnot(None)).limit(1)
        )
        car = result.scalar_one_or_none()
        if not car:
            return {"error": "No StarLine car found. Connect first."}

        # Подставляем device_id в endpoint
        endpoint = req.endpoint.replace("{device_id}", car.starline_device_id)

        # Only diagnostic reads for the connected device, including read-only POST endpoints.
        # Управляющие команды (set_param, async, controls, settings/*) сюда не входят
        # намеренно: консоль — инструмент диагностики, а не пульт от машины.
        device = car.starline_device_id
        allowed = {
            ("GET", f"/json/v2/device/{device}/state"),
            ("GET", f"/json/v3/device/{device}/data"),
            ("GET", f"/json/v1/device/{device}/position"),
            ("GET", f"/json/v2/device/{device}"),
            ("GET", f"/json/device/{device}/obd_params"),
            ("GET", f"/json/device/{device}/obd_errors"),
            ("GET", f"/json/device/{device}/ctrls_library"),
            ("GET", f"/json/v4/device/{device}/settings"),
            ("POST", f"/json/v1/device/{device}/getObdData"),
            ("POST", f"/json/v1/device/{device}/details"),
            ("POST", f"/json/v2/device/{device}/events"),
            ("POST", f"/json/v2/device/{device}/driving_score"),
            ("POST", f"/json/v2/device/{device}/driving_score_history"),
            ("POST", f"/json/v1/device/{device}/ways"),
            # Справочник типов событий — общий, без привязки к устройству.
            ("GET", "/json/v3/library/events"),
        }
        if (req.method.upper(), endpoint) not in allowed:
            raise HTTPException(
                403,
                "Разрешены только диагностические запросы для подключённой машины: "
                "data, state, position, obd_params, obd_errors, ctrls_library, "
                "getObdData, details, events, driving_score, driving_score_history, "
                "ways, settings (чтение), справочник /json/v3/library/events. "
                "Команды управления не выполняются.",
            )
        client = configured_client()
        resp = await client.api_request(req.method.upper(), endpoint,
            **({"json": req.body} if req.method.upper() == "POST" else {}))

        try:
            json_data = resp.json()
            return json_data
        except Exception:
            return {"raw": resp.text[:2000], "status": resp.status_code}

    except HTTPException:
        raise
    except StarLineError as e:
        raise HTTPException(502, str(e))


class StarLineConfig(BaseModel):
    app_id: str = Field(min_length=1)
    app_secret: str = Field(min_length=1)
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)
    device_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")  # можно указать вручную


@router.post("/connect")
async def connect_to_starline(
    body: StarLineConfig,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Подключить StarLine для текущего пользователя: авторизоваться, найти device_id."""
    try:
        client = cached_client(body.app_id, body.app_secret, body.login, body.password)
        await client.ensure_auth()

        # Если device_id указан вручную — используем его
        if body.device_id:
            device_id = body.device_id
            device_name = "Car (manual)"

            # Проверим, что device_id валидный
            data = await client.get_device_data(device_id)
            obd = data.get("obd") or {}
            mileage = obd.get("mileage", "N/A")
            fuel = obd.get("fuel_litres", "N/A")
        else:
            # Автоопределение: найдём устройство
            try:
                devices = await client.get_user_devices()
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
            device_id = str(device["device_id"])
            device_name = device.get("name") or device.get("alias") or device.get("typename") or "Car"

            data = await client.get_device_data(device_id)
            obd = data.get("obd") or {}
            mileage = obd.get("mileage", "N/A")
            fuel = obd.get("fuel_litres", "N/A")

        # Сохраним или обновим информацию о машине пользователя
        result = await db.execute(select(Car).where(Car.user_id == user.id).limit(1))
        car = result.scalar_one_or_none()

        if car and car.starline_device_id and car.starline_device_id != str(device_id):
            raise HTTPException(409, "Нельзя заменить устройство у машины с историей ТО")
        if car:
            car.starline_device_id = device_id
            car.name = device_name  # обновляем имя при повторном подключении
        else:
            car = Car(
                user_id=user.id,
                name=device_name,
                starline_device_id=device_id,
                initial_odometer=mileage if isinstance(mileage, int) else 0,
            )
            db.add(car)

        await db.flush()

        persist_settings({
            "starline_app_id": body.app_id, "starline_app_secret": body.app_secret,
            "starline_login": body.login, "starline_password": body.password,
        })

        # Сохраняем первый снепшот
        snapshot = StarLineClient.parse_snapshot(data)
        # Автоустановка начальных моточасов при первом подключении
        if car.initial_motohours == 0 and snapshot.get("motohours_minutes"):
            car.initial_motohours = snapshot["motohours_minutes"]

        await db.commit()
        await save_snapshot(db, car.id, snapshot)

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
    except OSError as e:
        raise HTTPException(500, "Не удалось сохранить настройки подключения на сервере") from e


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

    try:
        client = configured_client()
        snapshot, extra = await collect_and_store(client, db, car)
        await health.clear_error(db)

        return {"ok": True, "snap_id": extra["snap"].id,
                "errors": len(extra.get("errors") or []),
                "fuel_fill": ({"id": extra["fuel_fill"].id, "volume_litres": extra["fuel_fill"].volume_litres}
                              if extra.get("fuel_fill") else None),
                "data": {
            "ts": snapshot["ts"].isoformat(),
            "mileage_km": snapshot["mileage_km"],
            "fuel_litres": snapshot["fuel_litres"],
            "fuel_percent": snapshot["fuel_percent"],
            "speed_kmh": snapshot["speed_kmh"],
            "engine_on": snapshot["engine_on"],
            "is_moving": snapshot["is_moving"],
            "battery_v": snapshot["battery_v"],
            "ctemperature": snapshot["ctemperature"],
            "etemperature": snapshot["etemperature"],
            "outside_temp": snapshot["outside_temp"],
            "motohours_minutes": snapshot.get("motohours_minutes"),
        }}

    except StarLineError as e:
        logger.warning("Ручной сбор не удался: %s", e)
        await health.record_error(db, str(e))
        raise HTTPException(status_code=502, detail=f"StarLine API error: {e}")
    except Exception as e:
        # Непредвиденное (например, ошибка в новом коде) тоже должно попасть
        # на дашборд, иначе владельцу остаётся только journalctl.
        logger.exception("Ручной сбор упал неожиданно")
        await health.record_error(db, f"{type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сбора: {type(e).__name__}: {e}")


@router.post("/test-device")
async def test_device(
    body: StarLineConfig,
    user: User = Depends(get_current_user_from_cookie),
):
    """Проверить, работает ли device_id: авторизоваться и попробовать получить данные."""
    try:
        client = cached_client(body.app_id, body.app_secret, body.login, body.password)
        await client.ensure_auth()

        if not body.device_id:
            return {"ok": False, "error": "No device_id provided"}

        data = await client.get_device_data(body.device_id)
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

    freshness = describe_freshness(
        await last_snapshot_ts(db, car.id), settings.collect_interval_minutes
    )
    return {
        "connected": True,
        "car_id": car.id,
        "car_name": car.name,
        "device_id": car.starline_device_id,
        "snaps_count": await db.scalar(select(func.count()).select_from(StarSnap).where(StarSnap.car_id == car.id)),
        "last_snap_ts": freshness["last_ts"].isoformat() if freshness["last_ts"] else None,
        "collect_interval_minutes": freshness["interval_minutes"],
        "last_error": (await health.last_error(db)),
        "freshness": {
            "state": freshness["state"],
            "label": freshness["label"],
            "age_minutes": freshness["age_minutes"],
            "age_text": freshness["age_text"],
        },
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
        select(StarSnap).where(StarSnap.car_id == car.id, StarSnap.motohours_minutes.isnot(None))
        .order_by(StarSnap.ts.desc(), StarSnap.id.desc()).limit(1)
    )
    snap = snap_result.scalar_one_or_none()
    if not snap or snap.motohours_minutes is None:
        raise HTTPException(status_code=400, detail="No motohours data yet. Start the engine and fetch first.")

    car.initial_motohours = snap.motohours_minutes
    car.motohours_reset_at = snap.ts
    await db.commit()

    return {"ok": True, "initial_motohours": car.initial_motohours, "reset_at": snap.ts.isoformat() if snap.ts else None}
