import logging
from datetime import datetime, timezone
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
    last_mileage_km: Optional[int] = None
    last_motohours: Optional[int] = None
    last_date: Optional[str] = None  # ISO date
    interval_km: Optional[int] = None
    interval_months: Optional[int] = None
    interval_motohours: Optional[int] = None
    notes: Optional[str] = None


async def get_car(user: User, db: AsyncSession) -> Car:
    result = await db.execute(select(Car).where(Car.user_id == user.id).limit(1))
    car = result.scalar_one_or_none()
    if not car:
        raise HTTPException(status_code=400, detail="No car found. Connect StarLine first.")
    return car


@router.post("/add")
async def add_plan(
    body: ServicePlanForm,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    car = await get_car(user, db)
    data = body.model_dump(exclude_none=True)
    # Конвертируем строку даты в datetime
    if "last_date" in data and data["last_date"]:
        try:
            data["last_date"] = datetime.strptime(data["last_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            data.pop("last_date", None)
    plan = ServicePlan(car_id=car.id, **data)
    db.add(plan)
    await db.commit()
    return {"ok": True, "id": plan.id}


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


@router.post("/done/{plan_id}")
async def mark_done(
    plan_id: int,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Отметить выполнение ТО — обновить last_* на текущие значения."""
    car = await get_car(user, db)
    result = await db.execute(
        select(ServicePlan).where(ServicePlan.id == plan_id, ServicePlan.car_id == car.id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Берём последние данные из StarSnap
    snap_result = await db.execute(
        select(StarSnap).where(StarSnap.car_id == car.id).order_by(desc(StarSnap.ts)).limit(1)
    )
    snap = snap_result.scalar_one_or_none()

    plan.last_mileage_km = snap.mileage_km if snap else None
    plan.last_motohours = snap.motohours_minutes if snap else None
    plan.last_date = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.get("/page", response_class=HTMLResponse)
async def service_plan_page(
    request: Request,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    car = await get_car(user, db)

    # Текущие данные
    snap_result = await db.execute(
        select(StarSnap).where(StarSnap.car_id == car.id).order_by(desc(StarSnap.ts)).limit(1)
    )
    snap = snap_result.scalar_one_or_none()
    current_km = snap.mileage_km if snap else 0
    current_mh = snap.motohours_minutes if snap else 0

    # Список планов с расчётом статуса
    result = await db.execute(select(ServicePlan).where(ServicePlan.car_id == car.id))
    plans = result.scalars().all()

    plans_data = []
    for p in plans:
        status = []
        overdue = False

        if p.interval_km and p.last_mileage_km is not None:
            km_left = p.last_mileage_km + p.interval_km - current_km
            status.append(f"{km_left} км" if km_left > 0 else "⚠️ КМ!")
            if km_left <= 0:
                overdue = True

        if p.interval_months and p.last_date is not None:
            months_passed = (datetime.now(timezone.utc) - p.last_date.replace(tzinfo=timezone.utc)).days / 30
            months_left = p.interval_months - months_passed
            status.append(f"{months_left:.0f} мес" if months_left > 0 else "⚠️ Время!")
            if months_left <= 0:
                overdue = True

        if p.interval_motohours and p.last_motohours is not None:
            if current_mh:
                mh_left = p.last_motohours + p.interval_motohours - current_mh
                h_left = mh_left // 60
                status.append(f"{h_left} ч" if mh_left > 0 else "⚠️ Моточасы!")
                if mh_left <= 0:
                    overdue = True

        plans_data.append({
            "id": p.id,
            "name": p.name,
            "last_mileage_km": p.last_mileage_km,
            "last_motohours": p.last_motohours,
            "last_date": p.last_date.strftime("%d.%m.%Y") if p.last_date else "—",
            "interval_km": p.interval_km,
            "interval_months": p.interval_months,
            "interval_motohours": p.interval_motohours,
            "notes": p.notes,
            "status": ", ".join(status) if status else "Нет данных",
            "overdue": overdue,
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