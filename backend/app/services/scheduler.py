"""Планировщик сбора данных со StarLine.

Запускается вместе с сервером, собирает данные раз в час
для всех пользователей, у которых есть подключённая StarLine машина.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.car import Car
from app.services.starline import configured_client, StarLineError
from app.services.collection import collect_and_store
from app.services import health

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _remember_error(car_id: int, message: str) -> None:
    """Показать причину сбоя на дашборде; ошибка записи не должна ронять сбор."""
    try:
        async with async_session() as db:
            await health.record_error(db, message)
    except Exception:
        logger.exception("Не удалось сохранить текст ошибки сбора для car=%s", car_id)


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

    try:
        client = configured_client()
    except StarLineError:
        logger.info("StarLine credentials incomplete; collection skipped")
        return

    for car in cars:
        try:
            async with async_session() as db:
                snapshot, extra = await collect_and_store(client, db, car)
                await health.clear_error(db)
            logger.info(
                "Collected car=%s: mileage=%s fuel=%s motohrs=%s errors=%s",
                car.id, snapshot["mileage_km"],
                snapshot["fuel_litres"], snapshot["motohours_minutes"],
                len(extra.get("errors") or []),
            )
        except StarLineError as e:
            logger.error("Collect failed for car=%s: %s", car.id, e)
            await _remember_error(car.id, str(e))
        except Exception as e:
            logger.exception("Unexpected error for car=%s: %s", car.id, e)
            await _remember_error(car.id, f"{type(e).__name__}: {e}")

    logger.info("Scheduled collect: done")


async def run_collect_once():
    """Задача периодического сбора."""
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
