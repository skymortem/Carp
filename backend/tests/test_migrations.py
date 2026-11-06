"""Миграции: свежая база, база «до Alembic», повторный запуск."""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.models.car  # noqa: F401  — регистрирует таблицы в Base.metadata
import app.models.user  # noqa: F401
from app.database import Base
from app.migrations import BASELINE_REVISION, run_migrations

HEAD = "0002_snap_car_ts_index"


def _async_url(tmp_path, name="fresh.db"):
    return f"sqlite+aiosqlite:///{tmp_path / name}"


def _sync_url(tmp_path, name="fresh.db"):
    return f"sqlite:///{tmp_path / name}"


async def _tables_and_revision(async_engine):
    async with async_engine.connect() as conn:
        tables = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        revision = (await conn.execute(text("select version_num from alembic_version"))).scalar()
        indexes = await conn.run_sync(lambda sync: {i["name"] for i in inspect(sync).get_indexes("star_snaps")})
    return tables, revision, indexes


@pytest.mark.asyncio
async def test_fresh_database_is_created_by_migrations(tmp_path):
    url = _async_url(tmp_path)
    engine = create_async_engine(url)
    try:
        await run_migrations(database_url=url, db_engine=engine)
        tables, revision, indexes = await _tables_and_revision(engine)
    finally:
        await engine.dispose()

    assert {"users", "cars", "star_snaps", "service_plans", "service_logs", "alembic_version"} <= tables
    assert revision == HEAD
    # Композитный индекс добавляется уже миграцией 0002, а не базовой схемой.
    assert "ix_star_snaps_car_id_ts" in indexes


@pytest.mark.asyncio
async def test_legacy_database_is_stamped_and_keeps_data(tmp_path):
    """Установка, созданная create_all до Alembic: данные не должны пострадать."""
    sync_engine = create_engine(_sync_url(tmp_path, "legacy.db"))
    try:
        Base.metadata.create_all(sync_engine)
        with sync_engine.begin() as conn:
            conn.execute(text(
                "insert into users (id, email, username, hashed_password) "
                "values (1, 'owner@example.test', 'Owner', 'x')"
            ))
            conn.execute(text(
                "insert into cars (id, user_id, name, initial_odometer, initial_motohours) "
                "values (1, 1, 'Legacy car', 1000, 0)"
            ))
    finally:
        sync_engine.dispose()

    url = _async_url(tmp_path, "legacy.db")
    engine = create_async_engine(url)
    try:
        await run_migrations(database_url=url, db_engine=engine)
        tables, revision, indexes = await _tables_and_revision(engine)
        async with engine.connect() as conn:
            car_name = (await conn.execute(text("select name from cars where id = 1"))).scalar()
    finally:
        await engine.dispose()

    assert revision == HEAD
    assert "ix_star_snaps_car_id_ts" in indexes
    assert car_name == "Legacy car"


@pytest.mark.asyncio
async def test_migrations_are_idempotent(tmp_path):
    url = _async_url(tmp_path, "twice.db")
    engine = create_async_engine(url)
    try:
        await run_migrations(database_url=url, db_engine=engine)
        await run_migrations(database_url=url, db_engine=engine)
        _, revision, _ = await _tables_and_revision(engine)
    finally:
        await engine.dispose()
    assert revision == HEAD


def test_baseline_revision_constant_matches_history():
    """Защита от случайного переименования базовой ревизии: на неё ссылается stamp."""
    assert BASELINE_REVISION == "0001_baseline"
