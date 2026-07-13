import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.car import Car, StarSnap, ServicePlan
from app.services.auth import get_current_user_from_cookie

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/service-plan", tags=["service"])


class ServicePlanForm(BaseModel):
    name: str
    interval_km: Optional[int] = None
    interval_months: Optional[int] = None
    interval_motohours: Optional[int] = None
    notes: Optional[str] = None


class CompleteForm(BaseModel):
    plan_ids: list[int]
    mileage_km: Optional[int] = None
    motohours: Optional[int] = None
    date: Optional[str] = None  # ISO date, default today


async def get_car(user: User, db: AsyncSession) -> Car:
    result = await db.execute(select(Car).where(Car.user_id == user.id).limit(1))
    car = result.scalar_one_or_none()
    if not car:
        raise HTTPException(status_code=400, detail="No car found. Connect StarLine first.")
    return car


async def get_latest_snap(car_id: int, db: AsyncSession):
    result = await db.execute(
        select(StarSnap).where(StarSnap.car_id == car_id).order_by(desc(StarSnap.ts)).limit(1)
    )
    return result.scalar_one_or_none()


@router.post("/add")
async def add_plan(
    body: ServicePlanForm,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Создать шаблон операции ТО (без привязки к пробегу/дате)."""
    car = await get_car(user, db)
    plan = ServicePlan(car_id=car.id, **body.model_dump(exclude_none=True))
    db.add(plan)
    await db.commit()
    return {"ok": True, "id": plan.id}


@router.post("/complete")
async def complete_plans(
    body: CompleteForm,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Отметить ТО для выбранных операций с указанным пробегом/моточасами/датой."""
    car = await get_car(user, db)
    snap = await get_latest_snap(car.id, db)

    # Данные для простановки: приоритет — из запроса, затем — из последнего снепшота, затем — сегодня/0
    set_km = body.mileage_km if body.mileage_km is not None else (snap.mileage_km if snap else 0)
    set_mh = body.motohours if body.motohours is not None else (snap.motohours_minutes if snap else 0)
    if body.date:
        try:
            set_date = datetime.strptime(body.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            set_date = datetime.now(timezone.utc)
    else:
        set_date = datetime.now(timezone.utc)

    updated = []
    for pid in body.plan_ids:
        result = await db.execute(
            select(ServicePlan).where(ServicePlan.id == pid, ServicePlan.car_id == car.id)
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            continue
        plan.last_mileage_km = set_km
        plan.last_motohours = set_mh
        plan.last_date = set_date
        updated.append(pid)

    await db.commit()
    return {"ok": True, "updated": updated}


@router.post("/delete/{plan_id}")
async def delete_plan(
    plan_id: int,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    car = await get_car(user, db)
    result = await db.execute(
        select(ServicePlan).where(ServicePlan.id == plan_id, ServicePlan.car_id == car.id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    await db.delete(plan)
    await db.commit()
    return {"ok": True}


@router.get("/page", response_class=HTMLResponse)
async def service_plan_page(
    request: Request,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    car = await get_car(user, db)
    snap = await get_latest_snap(car.id, db)
    current_km = snap.mileage_km if snap else 0
    current_mh = snap.motohours_minutes if snap else 0

    result = await db.execute(select(ServicePlan).where(ServicePlan.car_id == car.id))
    plans = result.scalars().all()

    now = datetime.now(timezone.utc)
    plans_data = []
    for p in plans:
        status = []
        overdue = False
        active = False  # есть пробег/дата для отсчёта

        if p.interval_km and p.last_mileage_km is not None:
            active = True
            km_left = p.last_mileage_km + p.interval_km - current_km
            status.append(f"{km_left} км" if km_left > 0 else "⚠️ Просрочено!")
            if km_left <= 0:
                overdue = True

        if p.interval_months and p.last_date is not None:
            active = True
            next_date = p.last_date.replace(tzinfo=timezone.utc) + timedelta(days=p.interval_months * 30)
            days = (next_date - now).days
            status.append(f"{days} дн" if days > 0 else "⚠️ Просрочено!")
            if days <= 0:
                overdue = True

        if p.interval_motohours and p.last_motohours is not None:
            active = True
            if current_mh:
                mh_left = p.last_motohours + p.interval_motohours - current_mh
                h_left = mh_left // 60
                status.append(f"{h_left} ч" if mh_left > 0 else "⚠️ Просрочено!")
                if mh_left <= 0:
                    overdue = True

        plans_data.append({
            "id": p.id,
            "name": p.name,
            "interval_km": p.interval_km,
            "interval_months": p.interval_months,
            "interval_motohours": p.interval_motohours,
            "notes": p.notes,
            "last_info": f"{p.last_mileage_km or '—'} км · {p.last_date.strftime('%d.%m.%Y') if p.last_date else '—'} · {p.last_motohours or '—'} м/ч" if active else "—",
            "status": ", ".join(status) if status else "—",
            "overdue": overdue,
            "active": active,
        })

    return HTMLResponse(render_template("service_plan.html", request=request,
        user=user, car=car, plans=plans_data,
        current_km=current_km, current_mh=current_mh))


def render_template(name: str, request: Request, **context) -> str:
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    d = Path(__file__).resolve().parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(d)), autoescape=select_autoescape(["html", "xml"]))
    return env.get_template(name).render(request=request, **context)