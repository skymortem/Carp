"""Планировщик сбора данных со StarLine.

Запускается вместе с сервером, собирает данные раз в час
для всех пользователей, у которых есть подключённая StarLine машина.
"""
import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.car import Car, StarSnap
from app.services.starline import StarLineClient, StarLineError

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def collect_all_cars():
    """Собрать данные со StarLine для всех машин всех пользователей."""
    if not settings.starline_app_id:
        logger.info("Skipping collect — StarLine not configured in .env")
        return

    logger.info("Scheduled collect: starting...")
    async with async_session() as db:
        result = await db.execute(
            select(Car).where(Car.starline_device_id.isnot(None))
        )
        cars = result.scalars().all()

    if not cars:
        logger.info("Scheduled collect: no StarLine cars found")
        return

    client = StarLineClient(
        settings.starline_app_id,
        settings.starline_app_secret or "",
        settings.starline_login or "",
        settings.starline_password or "",
    )

    for car in cars:
        try:
            data = client.get_device_data(car.starline_device_id)
            snapshot = StarLineClient.parse_snapshot(data)
            snap = StarSnap(car_id=car.id, **snapshot)
            async with async_session() as db:
                db.add(snap)
                await db.commit()
            logger.info(
                "Collected car=%s: mileage=%s fuel=%s motohrs=%s",
                car.id, snapshot["mileage_km"],
                snapshot["fuel_litres"], snapshot["motohours_minutes"],
            )
        except StarLineError as e:
            logger.error("Collect failed for car=%s: %s", car.id, e)
        except Exception as e:
            logger.exception("Unexpected error for car=%s: %s", car.id, e)

    logger.info("Scheduled collect: done")


async def run_collect_once():
    """Запустить сбор синхронно (для теста при старте)."""
    loop = asyncio.get_event_loop()
    await collect_all_cars()


def start():
    """Запустить планировщик."""
    interval = max(settings.collect_interval_minutes, 30)  # минимум 30 минут
    scheduler.add_job(
        run_collect_once, "interval", minutes=interval,
        id="starline_collect", replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started: every %d minutes", interval)


def stop():
    """Остановить планировщик."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")