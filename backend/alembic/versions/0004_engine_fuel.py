"""моточасы из событий, заправки и состояние сервисов

- engine_sessions: интервалы работы двигателя (StarLine отдаёт state.motohrs = null,
  поэтому наработку считаем по событиям «Двигатель запущен/остановлен»);
- fuel_fills: обнаруженные заправки, цена литра и стоимость;
- app_state: offset Telegram-бота и одноразовые метки (бэкфилл моточасов).

Все операции проверяют схему: база, собранная через create_all() (тесты),
уже содержит эти таблицы и падать на дубликате не должна.

Revision ID: 0004_engine_fuel
Revises: 0003_diagnostics
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_engine_fuel"
down_revision = "0003_diagnostics"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "engine_sessions" not in tables:
        op.create_table(
            "engine_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("car_id", sa.Integer(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["car_id"], ["cars.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    existing = _indexes(bind, "engine_sessions")
    for name, columns, unique in (
        ("ix_engine_sessions_id", ["id"], False),
        ("ix_engine_sessions_car_id", ["car_id"], False),
        ("ix_engine_sessions_car_started", ["car_id", "started_at"], True),
    ):
        if name not in existing:
            op.create_index(name, "engine_sessions", columns, unique=unique)

    if "fuel_fills" not in tables:
        op.create_table(
            "fuel_fills",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("car_id", sa.Integer(), nullable=False),
            sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("prev_litres", sa.Float(), nullable=True),
            sa.Column("new_litres", sa.Float(), nullable=True),
            sa.Column("volume_litres", sa.Float(), nullable=False),
            sa.Column("price_per_litre", sa.Float(), nullable=True),
            sa.Column("total_cost", sa.Float(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["car_id"], ["cars.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    existing = _indexes(bind, "fuel_fills")
    for name, columns, unique in (
        ("ix_fuel_fills_id", ["id"], False),
        ("ix_fuel_fills_car_id", ["car_id"], False),
        ("ix_fuel_fills_car_detected", ["car_id", "detected_at"], False),
    ):
        if name not in existing:
            op.create_index(name, "fuel_fills", columns, unique=unique)

    if "app_state" not in tables:
        op.create_table(
            "app_state",
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("value", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
            sa.PrimaryKeyConstraint("key"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "app_state" in tables:
        op.drop_table("app_state")

    if "fuel_fills" in tables:
        for name in ("ix_fuel_fills_car_detected", "ix_fuel_fills_car_id", "ix_fuel_fills_id"):
            if name in _indexes(bind, "fuel_fills"):
                op.drop_index(name, table_name="fuel_fills")
        op.drop_table("fuel_fills")

    if "engine_sessions" in tables:
        for name in ("ix_engine_sessions_car_started", "ix_engine_sessions_car_id", "ix_engine_sessions_id"):
            if name in _indexes(bind, "engine_sessions"):
                op.drop_index(name, table_name="engine_sessions")
        op.drop_table("engine_sessions")
