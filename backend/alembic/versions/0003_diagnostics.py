"""диагностика: ошибки OBD, настройки устройства, уличная температура

- star_snaps: battery_type (volt|percent) и outside_temp (погода по координатам);
- obd_errors: история кодов ошибок, активные помечаются is_active;
- device_settings: последние прочитанные настройки автозапуска.

Колонки в star_snaps добавляются через batch: SQLite не умеет менять
ограничения таблицы обычным ALTER.

Все операции проверяют текущее состояние схемы: база, собранная через
create_all() (тесты, разовые скрипты), уже содержит эти колонки и таблицы,
и падать на дубликате она не должна.

Revision ID: 0003_diagnostics
Revises: 0002_snap_car_ts_index
Create Date: 2026-11-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_diagnostics"
down_revision = "0002_snap_car_ts_index"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    present_columns = _columns(bind, "star_snaps")
    missing = [
        sa.Column("battery_type", sa.String(), nullable=True),
        sa.Column("outside_temp", sa.Float(), nullable=True),
    ]
    missing = [column for column in missing if column.name not in present_columns]
    if missing:
        with op.batch_alter_table("star_snaps") as batch:
            for column in missing:
                batch.add_column(column)

    tables = _tables(bind)

    if "obd_errors" not in tables:
        op.create_table(
            "obd_errors",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("car_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("warning_level", sa.Integer(), nullable=True),
            sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["car_id"], ["cars.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = _indexes(bind, "obd_errors")
    for name, columns, unique in (
        ("ix_obd_errors_id", ["id"], False),
        ("ix_obd_errors_car_id", ["car_id"], False),
        ("ix_obd_errors_car_active", ["car_id", "is_active"], False),
    ):
        if name not in existing_indexes:
            op.create_index(name, "obd_errors", columns, unique=unique)

    if "device_settings" not in tables:
        op.create_table(
            "device_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("car_id", sa.Integer(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("r_start_active", sa.Boolean(), nullable=True),
            sa.Column("r_start_temp", sa.Integer(), nullable=True),
            sa.Column("r_start_temp_on", sa.Boolean(), nullable=True),
            sa.Column("r_start_period_h", sa.Integer(), nullable=True),
            sa.Column("r_start_period_on", sa.Boolean(), nullable=True),
            sa.Column("r_start_battery", sa.Integer(), nullable=True),
            sa.Column("r_start_battery_on", sa.Boolean(), nullable=True),
            sa.Column("r_start_cron", sa.Integer(), nullable=True),
            sa.Column("r_start_cron_on", sa.Boolean(), nullable=True),
            sa.ForeignKeyConstraint(["car_id"], ["cars.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = _indexes(bind, "device_settings")
    for name, columns, unique in (
        ("ix_device_settings_id", ["id"], False),
        ("ix_device_settings_car_id", ["car_id"], True),
    ):
        if name not in existing_indexes:
            op.create_index(name, "device_settings", columns, unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "device_settings" in tables:
        for name in ("ix_device_settings_car_id", "ix_device_settings_id"):
            if name in _indexes(bind, "device_settings"):
                op.drop_index(name, table_name="device_settings")
        op.drop_table("device_settings")

    if "obd_errors" in tables:
        for name in ("ix_obd_errors_car_active", "ix_obd_errors_car_id", "ix_obd_errors_id"):
            if name in _indexes(bind, "obd_errors"):
                op.drop_index(name, table_name="obd_errors")
        op.drop_table("obd_errors")

    present_columns = _columns(bind, "star_snaps")
    droppable = [name for name in ("outside_temp", "battery_type") if name in present_columns]
    if droppable:
        with op.batch_alter_table("star_snaps") as batch:
            for name in droppable:
                batch.drop_column(name)
