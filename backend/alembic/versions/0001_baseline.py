"""baseline: схема до внедрения Alembic

Ровно то, что раньше создавал Base.metadata.create_all():
users, cars, star_snaps, service_plans, service_logs.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-06

ВАЖНО: эту ревизию нельзя менять после релиза. Существующие базы
(созданные create_all до Alembic) помечаются ею через `alembic stamp`,
поэтому её содержимое должно совпадать со схемой тех установок.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "cars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("brand", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("plate_number", sa.String(), nullable=True),
        sa.Column("initial_odometer", sa.Integer(), nullable=True),
        sa.Column("starline_device_id", sa.String(), nullable=True),
        sa.Column("initial_motohours", sa.Integer(), nullable=True),
        sa.Column("motohours_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cars_id", "cars", ["id"])

    op.create_table(
        "star_snaps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mileage_km", sa.Integer(), nullable=True),
        sa.Column("fuel_litres", sa.Float(), nullable=True),
        sa.Column("fuel_percent", sa.Float(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("speed_kmh", sa.Float(), nullable=True),
        sa.Column("is_moving", sa.Boolean(), nullable=True),
        sa.Column("gsm_lvl", sa.Integer(), nullable=True),
        sa.Column("battery_v", sa.Float(), nullable=True),
        sa.Column("engine_on", sa.Boolean(), nullable=True),
        sa.Column("is_armed", sa.Boolean(), nullable=True),
        sa.Column("ctemperature", sa.Float(), nullable=True),
        sa.Column("etemperature", sa.Float(), nullable=True),
        sa.Column("motohours_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_star_snaps_id", "star_snaps", ["id"])
    op.create_index("ix_star_snaps_car_id", "star_snaps", ["car_id"])
    op.create_index("ix_star_snaps_ts", "star_snaps", ["ts"])

    op.create_table(
        "service_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("last_mileage_km", sa.Integer(), nullable=True),
        sa.Column("last_motohours", sa.Integer(), nullable=True),
        sa.Column("last_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interval_km", sa.Integer(), nullable=True),
        sa.Column("interval_months", sa.Integer(), nullable=True),
        sa.Column("interval_motohours", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_plans_id", "service_plans", ["id"])
    op.create_index("ix_service_plans_car_id", "service_plans", ["car_id"])

    op.create_table(
        "service_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("plan_name", sa.String(), nullable=False),
        sa.Column("mileage_km", sa.Integer(), nullable=True),
        sa.Column("motohours", sa.Integer(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["service_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_logs_id", "service_logs", ["id"])
    op.create_index("ix_service_logs_car_id", "service_logs", ["car_id"])


def downgrade() -> None:
    op.drop_index("ix_service_logs_car_id", table_name="service_logs")
    op.drop_index("ix_service_logs_id", table_name="service_logs")
    op.drop_table("service_logs")
    op.drop_index("ix_service_plans_car_id", table_name="service_plans")
    op.drop_index("ix_service_plans_id", table_name="service_plans")
    op.drop_table("service_plans")
    op.drop_index("ix_star_snaps_ts", table_name="star_snaps")
    op.drop_index("ix_star_snaps_car_id", table_name="star_snaps")
    op.drop_index("ix_star_snaps_id", table_name="star_snaps")
    op.drop_table("star_snaps")
    op.drop_index("ix_cars_id", table_name="cars")
    op.drop_table("cars")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
