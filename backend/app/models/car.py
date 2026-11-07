from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Car(Base):
    __tablename__ = "cars"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    brand = Column(String, default="")
    model = Column(String, default="")
    year = Column(Integer, nullable=True)
    plate_number = Column(String, default="")
    initial_odometer = Column(Integer, default=0)

    # Привязка к StarLine
    starline_device_id = Column(String, nullable=True)

    # Моточасы (точка отсчёта для счётчика)
    initial_motohours = Column(Integer, default=0)
    motohours_reset_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", backref="cars")
    snaps = relationship("StarSnap", back_populates="car", order_by="StarSnap.ts.desc()")


class StarSnap(Base):
    """Слепок данных со StarLine в момент времени"""
    __tablename__ = "star_snaps"

    # Композитный индекс объявлен и в модели, и в миграции 0002: без этого
    # alembic --autogenerate считает существующий индекс «лишним» и предлагает
    # его удалить (индекс есть в базе, но не в metadata).
    __table_args__ = (
        Index("ix_star_snaps_car_id_ts", "car_id", "ts"),
    )

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False, index=True)

    ts = Column(DateTime(timezone=True), nullable=False, index=True)  # когда сняты показания

    # OBD данные
    mileage_km = Column(Integer, nullable=True)         # obd.mileage
    fuel_litres = Column(Float, nullable=True)           # obd.fuel_litres
    fuel_percent = Column(Float, nullable=True)          # obd.fuel_percent

    # Позиция
    lat = Column(Float, nullable=True)                   # position.y
    lon = Column(Float, nullable=True)                   # position.x
    speed_kmh = Column(Float, nullable=True)             # position.s
    is_moving = Column(Boolean, nullable=True)           # position.is_move

    # Состояние
    gsm_lvl = Column(Integer, nullable=True)
    battery_v = Column(Float, nullable=True)             # common.battery
    battery_type = Column(String, nullable=True)         # common.battery_type: volt | percent
    # Уличная температура: в API StarLine её нет, берём по координатам
    # (Open-Meteo) в момент сбора. None, если сети не было — это не ошибка.
    outside_temp = Column(Float, nullable=True)
    engine_on = Column(Boolean, nullable=True)           # state.ign
    is_armed = Column(Boolean, nullable=True)            # state.arm
    ctemperature = Column(Float, nullable=True)          # common.ctemp
    etemperature = Column(Float, nullable=True)          # common.etemp
    motohours_minutes = Column(Integer, nullable=True)   # state.motohrs

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    car = relationship("Car", back_populates="snaps")


class ServicePlan(Base):
    """План ТО — регулярные операции по обслуживанию автомобиля."""
    __tablename__ = "service_plans"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # название операции (например "Замена масла")

    # Последнее выполнение
    last_mileage_km = Column(Integer, nullable=True)  # пробег при последней замене
    last_motohours = Column(Integer, nullable=True)   # моточасы при последней замене
    last_date = Column(DateTime(timezone=True), nullable=True)  # дата последней замены

    # Интервалы (хотя бы один должен быть указан)
    interval_km = Column(Integer, nullable=True)          # км между заменами
    interval_months = Column(Integer, nullable=True)      # месяцев между заменами
    interval_motohours = Column(Integer, nullable=True)   # моточасов между заменами

    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    car = relationship("Car", backref="service_plans")


class ServiceLog(Base):
    """История выполненных ТО."""
    __tablename__ = "service_logs"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("service_plans.id"), nullable=True)
    plan_name = Column(String, nullable=False)  # название операции на момент выполнения
    mileage_km = Column(Integer, nullable=True)  # пробег при ТО
    motohours = Column(Integer, nullable=True)   # моточасы при ТО
    done_at = Column(DateTime(timezone=True), server_default=func.now())  # дата выполнения


class ObdError(Base):
    """Код ошибки OBD, когда-либо увиденный у машины.

    StarLine отдаёт только текущий список из кеша, поэтому «ошибка есть»
    мы определяем по наличию кода в последнем ответе: пропавшие коды
    помечаются закрытыми, а не удаляются — история остаётся.
    """
    __tablename__ = "obd_errors"

    __table_args__ = (
        Index("ix_obd_errors_car_active", "car_id", "is_active"),
    )

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False, index=True)
    code = Column(String, nullable=False)                 # obd_errors[].error, напр. "P0171"
    description = Column(String, nullable=True)           # obd_errors[].descriptions
    warning_level = Column(Integer, nullable=True)        # obd_errors[].warning_level
    first_seen = Column(DateTime(timezone=True), nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class DeviceSettings(Base):
    """Последние известные настройки устройства (только чтение).

    Сейчас используется блок автозапуска (data.r_start) и отметка о том,
    что настройки вообще удалось прочитать: fetched_at.
    """
    __tablename__ = "device_settings"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False, index=True, unique=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)

    # data.r_start.<условие> = {has: bool, value: int}
    # state.r_start: «двигатель СЕЙЧАС запущен автозапуском», а не «автозапуск включён»;
    # включённость задают флаги r_start_*_on (data.r_start.<условие>.has).
    r_start_active = Column(Boolean, nullable=True)
    r_start_temp = Column(Integer, nullable=True)         # °C, например -3
    r_start_temp_on = Column(Boolean, nullable=True)
    r_start_period_h = Column(Integer, nullable=True)     # часы между запусками
    r_start_period_on = Column(Boolean, nullable=True)
    r_start_battery = Column(Integer, nullable=True)      # порог по АКБ, как его отдаёт API
    r_start_battery_on = Column(Boolean, nullable=True)
    r_start_cron = Column(Integer, nullable=True)         # Unix-время ближайшего запуска по расписанию
    r_start_cron_on = Column(Boolean, nullable=True)

    car = relationship("Car", backref="device_settings")


class EngineSession(Base):
    """Интервал работы двигателя, восстановленный из событий StarLine.

    У части устройств (например S96v2) `state.motohrs` приходит `null`,
    поэтому накопительные моточасы приходится считать самим: события
    «Двигатель запущен/остановлен» есть у всех и идут с точностью до секунды.
    """

    __tablename__ = "engine_sessions"

    __table_args__ = (
        # Ключ дедупликации: одно и то же событие запуска не должно
        # превратиться в две сессии при повторном разборе окна.
        Index("ix_engine_sessions_car_started", "car_id", "started_at", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)  # None — двигатель ещё работает


class FuelFill(Base):
    """Заправка: обнаруженный прирост топлива и цена литра.

    Объём считает Carp (разница уровней между соседними снимками), цену
    спрашивает у владельца Telegram-бот. Стоимость = цена × объём.
    """

    __tablename__ = "fuel_fills"

    __table_args__ = (
        Index("ix_fuel_fills_car_detected", "car_id", "detected_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False, index=True)

    detected_at = Column(DateTime(timezone=True), nullable=False)
    prev_litres = Column(Float, nullable=True)   # уровень до заправки
    new_litres = Column(Float, nullable=True)    # уровень после
    volume_litres = Column(Float, nullable=False)  # new - prev

    price_per_litre = Column(Float, nullable=True)
    total_cost = Column(Float, nullable=True)    # price_per_litre * volume_litres

    # awaiting_price — вопрос задан/ждёт ответа, done — цена есть, skipped — владелец отказался
    status = Column(String, nullable=False, default="awaiting_price")
    asked_at = Column(DateTime(timezone=True), nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)

    car = relationship("Car", backref="fuel_fills")


class AppState(Base):
    """Мелкое состояние сервисов: offset Telegram, метки синхронизации."""

    __tablename__ = "app_state"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())