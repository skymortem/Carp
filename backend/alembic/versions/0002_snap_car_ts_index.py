"""composite index (car_id, ts) for star_snaps

Агрегация графиков и выборка «последний снимок по машине» фильтруют
по car_id и сортируют по ts. Отдельные индексы заставляют SQLite читать
все снимки машины и сортировать их — композитный индекс снимает и то, и другое.

Индекс объявлен и в модели (__table_args__), поэтому при create_all()
он может уже существовать. Миграция проверяет это перед созданием:
установки, обновлённые в обход Alembic, не должны падать на дубликате.

Revision ID: 0002_snap_car_ts_index
Revises: 0001_baseline
Create Date: 2026-11-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_snap_car_ts_index"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_star_snaps_car_id_ts"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("star_snaps")}
    if INDEX_NAME not in existing:
        op.create_index(INDEX_NAME, "star_snaps", ["car_id", "ts"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("star_snaps")}
    if INDEX_NAME in existing:
        op.drop_index(INDEX_NAME, table_name="star_snaps")
