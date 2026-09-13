"""Единый расчет сроков ТО. Накопительные моточасы выражены в минутах."""
import calendar
import math
from datetime import datetime, timezone

from sqlalchemy import select
from app.models.car import StarSnap


async def current_readings(db, car_id):
    values = []
    for column in (StarSnap.mileage_km, StarSnap.motohours_minutes):
        value = await db.scalar(select(column).where(
            StarSnap.car_id == car_id, column.isnot(None)
        ).order_by(StarSnap.ts.desc(), StarSnap.id.desc()).limit(1))
        values.append(value)
    return tuple(values)


def format_hours(minutes):
    return '—' if minutes is None else f'{minutes // 60} ч {minutes % 60} мин'


def plan_status(plan, km, minutes, now=None):
    now = now or datetime.now(timezone.utc)
    labels = []
    urgent = overdue = False
    active = plan.last_date is not None

    def counter(interval, baseline, current, threshold, unit, scale=1):
        nonlocal urgent, overdue
        if interval is None:
            return
        if baseline is None:
            labels.append(f'{unit}: нет точки отсчета')
        elif current is None:
            labels.append(f'{unit}: нет показаний')
        elif current < baseline:
            labels.append(f'{unit}: показание ниже значения при ТО — проверьте счетчик')
            urgent = True
        else:
            left = baseline + interval * scale - current
            overdue |= left <= 0
            urgent |= left <= threshold
            labels.append(f'{unit}: ТО требуется' if left <= 0 else f'{math.ceil(left / scale)} {unit}')

    counter(plan.interval_km, plan.last_mileage_km, km, 1000, 'км')
    counter(plan.interval_motohours, plan.last_motohours, minutes, 50 * 60, 'ч', 60)
    if plan.interval_months is not None:
        if plan.last_date is None:
            labels.append('дата: нет точки отсчета')
        else:
            last = plan.last_date.replace(tzinfo=timezone.utc)
            month_index = last.year * 12 + last.month - 1 + plan.interval_months
            year, month = divmod(month_index, 12)
            month += 1
            deadline = last.replace(year=year, month=month,
                day=min(last.day, calendar.monthrange(year, month)[1]))
            seconds = (deadline - now).total_seconds()
            overdue |= seconds <= 0
            urgent |= seconds <= 30 * 86400
            labels.append('дата: ТО требуется' if seconds <= 0 else f'{math.ceil(seconds / 86400)} дн')
    status = ', '.join(labels) or '—'
    return dict(status=status, remain=status, urgent=urgent or overdue,
                overdue=overdue, active=active)
