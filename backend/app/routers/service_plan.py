import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ConfigDict
from datetime import date as Date
from app.services.maintenance import current_readings, plan_status, format_hours
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.car import Car, ServicePlan
from app.services.auth import get_current_user_from_cookie

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/service-plan", tags=["service"])


class ServicePlanForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    interval_km: Optional[int] = Field(default=None, gt=0)
    interval_months: Optional[int] = Field(default=None, gt=0, le=1200)
    interval_motohours: Optional[int] = Field(default=None, gt=0)
    notes: Optional[str] = None


class PlanUpdateForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    interval_km: Optional[int] = Field(default=None, gt=0)
    interval_months: Optional[int] = Field(default=None, gt=0, le=1200)
    interval_motohours: Optional[int] = Field(default=None, gt=0)
    notes: Optional[str] = None


class CompleteForm(BaseModel):
    plan_ids: list[int] = Field(min_length=1)
    mileage_km: Optional[int] = Field(default=None, ge=0)
    motohours: Optional[int] = Field(default=None, ge=0)
    date: Optional[Date] = None


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
    """Создать шаблон операции ТО (без привязки к пробегу/дате)."""
    car = await get_car(user, db)
    if not any((body.interval_km, body.interval_months, body.interval_motohours)):
        raise HTTPException(422, "Укажите хотя бы один интервал ТО")
    plan = ServicePlan(car_id=car.id, **body.model_dump(exclude_none=True))
    db.add(plan)
    await db.commit()
    return {"ok": True, "id": plan.id}


@router.put("/update/{plan_id}")
async def update_plan(
    plan_id: int,
    body: PlanUpdateForm,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Редактировать шаблон операции ТО."""
    car = await get_car(user, db)
    result = await db.execute(
        select(ServicePlan).where(ServicePlan.id == plan_id, ServicePlan.car_id == car.id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    data = body.model_dump(exclude_unset=True)
    if 'name' in data and (data['name'] is None or not data['name'].strip()):
        raise HTTPException(422, "Укажите название операции")
    if not any(data.get(key, getattr(plan, key)) for key in
               ('interval_km', 'interval_months', 'interval_motohours')):
        raise HTTPException(422, "Укажите хотя бы один интервал ТО")
    for key, val in data.items():
        setattr(plan, key, val)
    await db.commit()
    return {"ok": True}


@router.post("/complete")
async def complete_plans(
    body: CompleteForm,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """Отметить ТО для выбранных операций с указанным пробегом/моточасами/датой."""
    car = await get_car(user, db)
    current_km, current_mh = await current_readings(db, car.id)
    set_km = body.mileage_km if body.mileage_km is not None else current_km
    set_mh = body.motohours * 60 if body.motohours is not None else current_mh
    set_date = (datetime.combine(body.date, datetime.min.time(), tzinfo=timezone.utc)
                if body.date else datetime.now(timezone.utc))
    if set_date > datetime.now(timezone.utc):
        raise HTTPException(422, "Дата ТО не может быть в будущем")

    updated = []
    for pid in dict.fromkeys(body.plan_ids):
        result = await db.execute(
            select(ServicePlan).where(ServicePlan.id == pid, ServicePlan.car_id == car.id)
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise HTTPException(404, "Операция ТО не найдена")
        if (plan.interval_km and set_km is None) or (plan.interval_motohours and set_mh is None):
            raise HTTPException(422, "Нет показаний: укажите пробег и моточасы для выбранных интервалов")
        plan.last_mileage_km = set_km
        plan.last_motohours = set_mh
        plan.last_date = set_date
        updated.append(pid)

        # Запись в историю
        from app.models.car import ServiceLog
        log = ServiceLog(
            car_id=car.id, plan_id=plan.id, plan_name=plan.name,
            mileage_km=set_km, motohours=set_mh, done_at=set_date,
        )
        db.add(log)

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
    from app.models.car import ServiceLog
    from sqlalchemy import update
    await db.execute(update(ServiceLog).where(ServiceLog.plan_id == plan.id).values(plan_id=None))
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
    current_km, current_mh = await current_readings(db, car.id)

    result = await db.execute(select(ServicePlan).where(ServicePlan.car_id == car.id))
    plans = result.scalars().all()

    now = datetime.now(timezone.utc)
    plans_data = []
    for p in plans:
        state = plan_status(p, current_km, current_mh, now)
        plans_data.append({
            "id": p.id,
            "name": p.name,
            "interval_km": p.interval_km,
            "interval_months": p.interval_months,
            "interval_motohours": p.interval_motohours,
            "notes": p.notes,
            "last_info": f"{p.last_mileage_km if p.last_mileage_km is not None else '—'} км · {p.last_date.strftime('%d.%m.%Y') if p.last_date else '—'} · {format_hours(p.last_motohours)}",
            **state,
        })

    return HTMLResponse(render_template("service_plan.html", request=request,
        user=user, car=car, plans=plans_data,
        current_km=current_km, current_mh=current_mh))


@router.get("/history")
async def get_history(
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    """API: история выполненных ТО."""
    car = await get_car(user, db)
    from app.models.car import ServiceLog
    result = await db.execute(
        select(ServiceLog).where(ServiceLog.car_id == car.id).order_by(desc(ServiceLog.done_at)).limit(50)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "plan_name": log.plan_name,
            "mileage_km": log.mileage_km,
            "motohours": format_hours(log.motohours),
            "done_at": log.done_at.strftime("%d.%m.%Y") if log.done_at else "—",
        }
        for log in logs
    ]


def render_template(name: str, request: Request, **context) -> str:
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    d = Path(__file__).resolve().parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(d)), autoescape=select_autoescape(["html", "xml"]))
    return env.get_template(name).render(request=request, **context)