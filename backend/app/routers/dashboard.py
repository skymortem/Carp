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
from app.models.car import Car, StarSnap
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

    return HTMLResponse(render("dashboard.html", request=request,
        user=user, car=car, snaps=snaps,
        snaps_reversed=snaps_reversed, starline_connected=starline_connected,
        initial_motohours=getattr(car, "initial_motohours", 0) if car else 0,
        motohours_reset_at=getattr(car, "motohours_reset_at", None) if car else None))


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