"""Окружение Alembic для асинхронного SQLAlchemy (aiosqlite / asyncpg).

URL базы берётся из вызывающего кода (app/migrations.py), а не из alembic.ini,
чтобы не дублировать секреты и не читать .env дважды.
"""
import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.database import Base
# Импорт пакета моделей регистрирует все таблицы в Base.metadata.
import app.models  # noqa: F401
from app.models import car as _car  # noqa: F401
from app.models import user as _user  # noqa: F401

config = context.config
target_metadata = Base.metadata

# fileConfig() намеренно не вызывается: он перенастраивает корневой логгер
# и глушит логи приложения (мы видим "[INFO] app.main" только благодаря
# current config). Для CLI-режима подробности не нужны.
#
# URL задаёт вызывающий код (app/migrations.py). Если миграция запускается
# руками из CLI, берём его из настроек приложения — тогда работает
# `alembic revision --autogenerate` из каталога backend с обычным .env.
if not config.get_main_option("sqlalchemy.url"):
    from app.config import settings

    config.set_main_option("sqlalchemy.url", settings.database_url)


def _include_object(obj, name, type_, reflected, compare_to):
    """Игнорируем служебные таблицы SQLite, если они ever встретятся."""
    if type_ == "table" and name.startswith("sqlite_"):
        return False
    return True


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # batch-режим: ALTER в SQLite выполняется через пересоздание таблицы
        render_as_batch=True,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
