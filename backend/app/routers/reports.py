"""Страница «Отчёты»: пробег по месяцам, расходы на топливо и цена литра.

Дашборд оставлен для текущего состояния (показания, диагностика, ТО), а
историческая аналитика живёт здесь. Данные считает база: страница не должна
тянуть тысячи снимков в браузер.
"""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.car import Car, FuelFill
from app.models.user import User
from app.routers.dashboard import render
from app.services.auth import get_current_user_from_cookie
from app.services.freshness import naive_utcnow

router = APIRouter(tags=["reports"])


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    user: User = Depends(get_current_user_from_cookie),
    db: AsyncSession = Depends(get_db),
):
    car = await db.scalar(select(Car).where(Car.user_id == user.id).limit(1))
    return HTMLResponse(render("reports.html", request=request, user=user, car=car))


@router.get("/api/reports/fuel")
async def fuel_report(
    days: int = Query(365, ge=1, le=3650, description="Глубина отчёта в днях"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_from_cookie),
):
    """Топливо: расходы по месяцам и цена литра по заправкам.

    Считаются только заправки со статусом `done` — без цены их не к чему
    относить, а «0 ₽» в графике расходов выглядело бы как бесплатная заправка.
    Суммы берутся из сохранённой стоимости, а не пересчитываются заново:
    владелец мог ответить ценой, которую мы обязаны уважать.
    """
    car = await db.scalar(select(Car).where(Car.user_id == user.id).limit(1))
    if car is None:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    since = naive_utcnow() - timedelta(days=days)
    month = func.strftime("%Y-%m", FuelFill.detected_at).label("month")

    rows = (await db.execute(
        select(
            month,
            func.sum(FuelFill.volume_litres).label("litres"),
            func.sum(FuelFill.total_cost).label("cost"),
            func.count(FuelFill.id).label("fills"),
            func.avg(FuelFill.price_per_litre).label("price_avg"),
        )
        .where(FuelFill.car_id == car.id, FuelFill.status == "done",
               FuelFill.detected_at >= since)
        .group_by(month)
        .order_by(month)
    )).all()

    months = [
        {
            "m": row.month,
            "litres": round(row.litres or 0, 1),
            "cost": round(row.cost or 0, 2),
            "fills": row.fills,
            "price_avg": round(row.price_avg, 2) if row.price_avg is not None else None,
        }
        for row in rows
        if row.month
    ]

    price_rows = (await db.execute(
        select(FuelFill.detected_at, FuelFill.price_per_litre, FuelFill.volume_litres)
        .where(FuelFill.car_id == car.id, FuelFill.status == "done",
               FuelFill.price_per_litre.isnot(None),
               FuelFill.detected_at >= since)
        .order_by(FuelFill.detected_at)
    )).all()

    prices = [
        {
            "d": row.detected_at.isoformat() if row.detected_at else None,
            "price": round(row.price_per_litre, 2),
            "volume": round(row.volume_litres or 0, 1),
        }
        for row in price_rows
    ]

    return {
        "days": days,
        "months": months,
        "prices": prices,
        "totals": {
            "cost": round(sum(item["cost"] for item in months), 2),
            "litres": round(sum(item["litres"] for item in months), 1),
        },
    }
