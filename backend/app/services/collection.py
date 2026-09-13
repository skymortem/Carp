"""Одинаковый сбор и защита от повторных снимков для кнопки и планировщика."""
import asyncio
from sqlalchemy import select
from app.models.car import StarSnap
from app.services.starline import StarLineClient

save_lock = asyncio.Lock()


async def collect_snapshot(client, device_id):
    data = await client.get_device_data(device_id)
    snapshot = StarLineClient.parse_snapshot(data)
    # An events window is NOT an accumulated engine-hours counter.
    return snapshot


async def save_snapshot(db, car_id, snapshot):
    async with save_lock:
        existing = await db.scalar(select(StarSnap).where(
            StarSnap.car_id == car_id,
            *(getattr(StarSnap, key) == value for key, value in snapshot.items())
        ).limit(1))
        if existing is not None:
            return existing
        snap = StarSnap(car_id=car_id, **snapshot)
        db.add(snap)
        await db.commit()
        await db.refresh(snap)
        return snap
