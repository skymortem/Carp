from datetime import timedelta, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.car import Car, DeviceSettings, FuelFill, ObdError, StarSnap, ServicePlan
from app.services.auth import get_current_user_from_cookie, maybe_user
from app.services.freshness import car_freshness, naive_utcnow

from app.services.maintenance import current_readings, plan_status

router = APIRouter(tags=["pages"])

def describe_battery(raw_value, raw_type) -> Optional[dict]:
    """АКБ: StarLine отдаёт либо вольты, либо проценты.

    В ответах API встречается процент, помеченный как "volt" (например 66
    при battery_type=volt), поэтому решаем по величине: в 12-вольтовой сети
    не бывает больше ~20, всё выше — проценты.
    """
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None

    if str(raw_type or "").lower() == "percent" or value > 20:
        return {"value": int(round(value)), "unit": "%", "low": value < 30, "kind": "percent"}
    return {"value": round(value, 1), "unit": "В", "low": value < 11.8, "kind": "volt"}


RECENT_SNAPS_LIMIT = 20

templates_dir = Path(__file__).resolve().parent.parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(templates_dir)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render(name: str, request: Request, **context) -> str:
    template = jinja_env.get_template(name)
    return template.render(request=request, **context)


@router.get("/", response_class=HTMLResponse)
async def index_page(
    request: Request,
    user: Optional[User] = Depends(maybe_user),
    db: AsyncSession = Depends(get_db),
):
    """Публичная главная: описание проекта и вход.

    Раньше для незалогиненного здесь был только экран входа — для
    репутационных списков (и для человека) это неотличимо от фишинговой
    страницы, поэтому у установки есть нормальное «лицо».
    """
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    owner_exists = await db.scalar(select(User.id).limit(1)) is not None
    return HTMLResponse(render("index.html", request=request, owner_exists=owner_exists))


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Дашборд: последние показания, свежесть сбора, план ТО.

    В шаблон уходят только 20 последних снимков (таблица). Графики строятся
    из /api/series по агрегатам — иначе каждый рендер тянул 2000 строк
    и история обрывалась на 2000-м снимке (~83 дня при сборе раз в час).
    """
    result = await db.execute(select(Car).where(Car.user_id == user.id).limit(1))
    car = result.scalar_one_or_none()

    snaps = []
    latest = None
    snap_count = 0
    service_plans = []
    starline_connected = False
    current_km = current_mh = None
    freshness = None
    battery = None
    device_settings = None
    obd_errors = []
    fuel_fills = []
    fuel_totals = {"count": 0, "litres": 0.0, "cost": 0.0}

    if car:
        snap_count = await db.scalar(
            select(func.count()).select_from(StarSnap).where(StarSnap.car_id == car.id)
        ) or 0
        result = await db.execute(
            select(StarSnap).where(StarSnap.car_id == car.id)
            .order_by(desc(StarSnap.ts), desc(StarSnap.id)).limit(RECENT_SNAPS_LIMIT)
        )
        snaps_orm = result.scalars().all()
        snaps = [
            {
                "id": s.id,
                "ts": s.ts.replace(tzinfo=timezone.utc).isoformat() if s.ts else None,
                "mileage_km": s.mileage_km,
                "fuel_litres": s.fuel_litres,
                "fuel_percent": s.fuel_percent,
                "speed_kmh": s.speed_kmh,
                "is_moving": s.is_moving,
                "engine_on": s.engine_on,
                "is_armed": s.is_armed,
                "gsm_lvl": s.gsm_lvl,
                "battery_v": s.battery_v,
                "battery_type": s.battery_type,
                "outside_temp": s.outside_temp,
                "ctemperature": s.ctemperature,
                "etemperature": s.etemperature,
                "motohours_minutes": s.motohours_minutes,
                "lat": s.lat,
                "lon": s.lon,
                "created_at": s.created_at.replace(tzinfo=timezone.utc).isoformat() if s.created_at else None,
            }
            for s in snaps_orm
        ]
        latest = snaps[0] if snaps else None
        starline_connected = bool(car.starline_device_id)
        freshness = await car_freshness(db, car.id)
        battery = describe_battery(latest.get("battery_v"), latest.get("battery_type")) if latest else None

        device_settings = await db.scalar(
            select(DeviceSettings).where(DeviceSettings.car_id == car.id)
        )
        obd_errors = (await db.execute(
            select(ObdError)
            .where(ObdError.car_id == car.id, ObdError.is_active.is_(True))
            .order_by(ObdError.first_seen)
        )).scalars().all()

        current_km, current_mh = await current_readings(db, car.id)
        plan_result = await db.execute(select(ServicePlan).where(ServicePlan.car_id == car.id))
        plans = plan_result.scalars().all()

        fuel_fills = (await db.execute(
            select(FuelFill).where(FuelFill.car_id == car.id)
            .order_by(FuelFill.detected_at.desc()).limit(5)
        )).scalars().all()
        total_count, total_litres, total_cost = (await db.execute(
            select(func.count(FuelFill.id), func.sum(FuelFill.volume_litres), func.sum(FuelFill.total_cost))
            .where(FuelFill.car_id == car.id, FuelFill.status == "done")
        )).one()
        fuel_totals = {"count": total_count or 0, "litres": total_litres or 0.0, "cost": total_cost or 0.0}

        service_plans = []
        for p in plans:
            service_plans.append({"name": p.name, **plan_status(p, current_km, current_mh)})

        # Сортируем: срочные сверху
        service_plans.sort(key=lambda x: (not x["urgent"], x["name"]))

    return HTMLResponse(render("dashboard.html", request=request,
        user=user, car=car, snaps=snaps, latest=latest, snap_count=snap_count,
        freshness=freshness, starline_connected=starline_connected,
        battery=battery, device_settings=device_settings, obd_errors=obd_errors,
        initial_motohours=getattr(car, "initial_motohours", 0) if car else 0,
        motohours_reset_at=getattr(car, "motohours_reset_at", None) if car else None,
        service_plans=service_plans, current_km=current_km, current_mh=current_mh,
        fuel_fills=fuel_fills, fuel_totals=fuel_totals,
        collect_interval_minutes=settings.collect_interval_minutes))


@router.get("/api/series")
async def series(
    days: int = Query(365, ge=1, le=36500, description="Глубина истории в днях"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_from_cookie),
):
    """Дневные агрегаты снимков для графиков.

    Раньше дашборд встраивал 2000 последних снимков прямо в HTML: страница
    тяжелела, а история молча обрывалась на 2000-й записи (~83 дня при
    сборе раз в час). Здесь агрегация выполняется базой, поэтому глубина
    ограничена только сроком хранения данных.

    Границы суток — UTC (в SQLite `date(ts)` от строки без таймзоны),
    поэтому для UTC+6 «сегодня» может включать раннее утро.
    """
    car = await db.scalar(select(Car).where(Car.user_id == user.id).limit(1))
    if car is None:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    since = naive_utcnow() - timedelta(days=days)
    day = func.date(StarSnap.ts).label("d")
    rows = (await db.execute(
        select(
            day,
            func.min(StarSnap.mileage_km).label("m_min"),
            func.max(StarSnap.mileage_km).label("m_max"),
            func.avg(StarSnap.fuel_litres).label("fuel_avg"),
            func.avg(StarSnap.speed_kmh).label("speed_avg"),
            func.count().label("n"),
        )
        .where(StarSnap.car_id == car.id, StarSnap.ts >= since)
        .group_by(day)
        .order_by(day)
    )).all()

    points = [
        {
            "d": row.d,
            "mileage_min": row.m_min,
            "mileage_max": row.m_max,
            "fuel_avg": round(row.fuel_avg, 2) if row.fuel_avg is not None else None,
            "speed_avg": round(row.speed_avg, 1) if row.speed_avg is not None else None,
            "n": row.n,
        }
        for row in rows
        if row.d
    ]

    return {
        "car_id": car.id,
        "days": days,
        "bucket": "day",
        "timezone": "UTC",
        "points": points,
        "total_points": len(points),
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    owner_exists = await db.scalar(select(User.id).limit(1)) is not None
    return HTMLResponse(render("auth/login.html", request=request, owner_exists=owner_exists))


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, db: AsyncSession = Depends(get_db)):
    # Регистрация закрыта после создания владельца: показывать форму,
    # которая гарантированно вернёт 403, — плохой сценарий.
    if await db.scalar(select(User.id).limit(1)) is not None:
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse(render("auth/register.html", request=request, owner_exists=False))


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(
    request: Request,
    user: User = Depends(get_current_user_from_cookie),
):
    """Страница настройки StarLine подключения."""
    return HTMLResponse(render("setup.html", request=request, user=user))


@router.get("/starline-console", response_class=HTMLResponse)
async def starline_console_page(
    request: Request,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Тестовая консоль StarLine API."""
    from app.models.car import Car
    result = await db.execute(
        select(Car).where(Car.user_id == user.id, Car.starline_device_id.isnot(None)).limit(1)
    )
    car = result.scalar_one_or_none()
    device_id = car.starline_device_id if car else "—"
    return HTMLResponse(render("starline_console.html", request=request, user=user, device_id=device_id))