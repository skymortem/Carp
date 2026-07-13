import os
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.car import Car, StarSnap, ServicePlan
from app.services.auth import get_current_user_from_cookie, maybe_user

router = APIRouter(tags=["pages"])

templates_dir = Path(__file__).resolve().parent.parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(templates_dir)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render(name: str, request: Request, **context) -> str:
    template = jinja_env.get_template(name)
    return template.render(request=request, **context)


@router.get("/", response_class=HTMLResponse)
async def index_page(request: Request, user: Optional[User] = Depends(maybe_user)):
    """Главная страница — если залогинен, показываем дашборд."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return HTMLResponse(render("index.html", request=request))


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Дашборд с графиками и данными."""
    result = await db.execute(select(Car).where(Car.user_id == user.id).limit(1))
    car = result.scalar_one_or_none()

    snaps = []
    snaps_reversed = []
    service_plans = []
    starline_connected = False

    if car:
        result = await db.execute(
            select(StarSnap).where(StarSnap.car_id == car.id)
            .order_by(desc(StarSnap.ts))
            .limit(100)
        )
        snaps_orm = result.scalars().all()
        snaps = [
            {
                "id": s.id,
                "ts": s.ts.isoformat() if s.ts else None,
                "mileage_km": s.mileage_km,
                "fuel_litres": s.fuel_litres,
                "fuel_percent": s.fuel_percent,
                "speed_kmh": s.speed_kmh,
                "is_moving": s.is_moving,
                "engine_on": s.engine_on,
                "is_armed": s.is_armed,
                "gsm_lvl": s.gsm_lvl,
                "battery_v": s.battery_v,
                "ctemperature": s.ctemperature,
                "etemperature": s.etemperature,
                "motohours_minutes": s.motohours_minutes,
                "lat": s.lat,
                "lon": s.lon,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in snaps_orm
        ]
        snaps_reversed = list(reversed(snaps))
        starline_connected = bool(car.starline_device_id)

        # Загружаем планы ТО
        snap_result = await db.execute(
            select(StarSnap).where(StarSnap.car_id == car.id).order_by(desc(StarSnap.ts)).limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        current_km = snap.mileage_km if snap else 0
        current_mh = snap.motohours_minutes if snap else 0

        plan_result = await db.execute(select(ServicePlan).where(ServicePlan.car_id == car.id))
        plans = plan_result.scalars().all()

        service_plans = []
        for p in plans:
            remain = []
            urgent = False

            if p.interval_km and p.last_mileage_km is not None:
                km_left = p.last_mileage_km + p.interval_km - current_km
                label = f"{km_left} км" if km_left > 0 else "⚠️ Просрочено!"
                remain.append(label)
                if km_left < 1000:
                    urgent = True

            if p.interval_months and p.last_date is not None:
                from datetime import datetime, timezone, timedelta
                next_date = p.last_date.replace(tzinfo=timezone.utc) + timedelta(days=p.interval_months * 30)
                now = datetime.now(timezone.utc)
                days_left = (next_date - now).days
                label = f"{days_left} дн" if days_left > 0 else "⚠️ Просрочено!"
                remain.append(label)
                if days_left < 30:
                    urgent = True

            if p.interval_motohours and p.last_motohours is not None:
                if current_mh:
                    interval_minutes = p.interval_motohours * 60
                    mh_left = p.last_motohours + interval_minutes - current_mh
                    h_left = mh_left // 60
                    label = f"{h_left} ч" if mh_left > 0 else "⚠️ Просрочено!"
                    remain.append(label)
                    if mh_left < 50 * 60:
                        urgent = True

            service_plans.append({
                "name": p.name,
                "remain": ", ".join(remain) if remain else "—",
                "urgent": urgent,
            })

        # Сортируем: срочные сверху
        service_plans.sort(key=lambda x: (not x["urgent"], x["name"]))

    return HTMLResponse(render("dashboard.html", request=request,
        user=user, car=car, snaps=snaps,
        snaps_reversed=snaps_reversed, starline_connected=starline_connected,
        initial_motohours=getattr(car, "initial_motohours", 0) if car else 0,
        motohours_reset_at=getattr(car, "motohours_reset_at", None) if car else None,
        service_plans=service_plans))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return HTMLResponse(render("auth/login.html", request=request))


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return HTMLResponse(render("auth/register.html", request=request))


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