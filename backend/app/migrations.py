"""Применение миграций Alembic при старте приложения.

Раньше схема создавалась `Base.metadata.create_all()`, который не умеет
менять существующие таблицы: новая колонка в модели просто не появлялась
в базе. Теперь единственный источник истины — alembic/versions.

Особый случай — установки, созданные до Alembic: таблицы есть, а
`alembic_version` нет. Их нельзя прогонять через 0001 (таблицы уже существуют),
поэтому база сначала помечается как `0001_baseline`, а дальше идёт обычный
`upgrade head`. Данные при этом не трогаются.
"""
import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.database import engine

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_DIR = BACKEND_DIR / "alembic"

# Ревизия, соответствующая схеме, которую создавал create_all() до Alembic.
BASELINE_REVISION = "0001_baseline"
VERSION_TABLE = "alembic_version"
APP_TABLES = ("users", "cars", "star_snaps", "service_plans", "service_logs")


def alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _table_names(db_engine) -> set[str]:
    async with db_engine.connect() as connection:
        return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))


async def run_migrations(database_url: str | None = None, db_engine=None) -> None:
    """Довести схему до head. Безопасно вызывать при каждом старте."""
    engine_ = db_engine or engine
    url = database_url or str(engine_.url)
    tables = await _table_names(engine_)
    config = alembic_config(url)

    is_legacy = VERSION_TABLE not in tables and any(t in tables for t in APP_TABLES)
    if is_legacy:
        logger.warning(
            "База создана без Alembic — помечаю как %s и применяю остальные миграции",
            BASELINE_REVISION,
        )
        # Alembic синхронный и внутри env.py вызывает asyncio.run(),
        # поэтому его нельзя выполнять в текущем цикле событий.
        await asyncio.to_thread(command.stamp, config, BASELINE_REVISION)

    await asyncio.to_thread(command.upgrade, config, "head")
    logger.info("Миграции применены (head)")


def current_revision(database_url: str) -> str | None:
    """Текущая ревизия без запуска event loop — для CLI/диагностики."""
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    sync_url = database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")
    engine_ = create_engine(sync_url)
    try:
        with engine_.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine_.dispose()
