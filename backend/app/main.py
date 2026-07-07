import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import auth, starline, dashboard, service_plan
from app.services.scheduler import start as start_scheduler, stop as stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — creating tables if needed...")
    await init_db()
    start_scheduler()
    logger.info("DB ready! Scheduler active.")
    yield
    stop_scheduler()
    logger.info("Shutting down.")


app = FastAPI(title="Carp — Car Expense Tracker", lifespan=lifespan)

# Статика — создаём папку если нет
static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Роутеры
app.include_router(auth.router)
app.include_router(starline.router)
app.include_router(dashboard.router)
app.include_router(service_plan.router)