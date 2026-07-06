import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import auth, starline, dashboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — creating tables if needed...")
    await init_db()
    logger.info("DB ready!")
    yield
    logger.info("Shutting down.")


app = FastAPI(title="Carp — Car Expense Tracker", lifespan=lifespan)

# Статика
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Роутеры
app.include_router(auth.router)
app.include_router(starline.router)
app.include_router(dashboard.router)