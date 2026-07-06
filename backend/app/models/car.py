from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
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

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", backref="cars")
    snaps = relationship("StarSnap", back_populates="car", order_by="StarSnap.ts.desc()")


class StarSnap(Base):
    """Слепок данных со StarLine в момент времени"""
    __tablename__ = "star_snaps"

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
    engine_on = Column(Boolean, nullable=True)           # state.ign
    is_armed = Column(Boolean, nullable=True)            # state.arm
    ctemperature = Column(Float, nullable=True)          # common.ctemp
    etemperature = Column(Float, nullable=True)          # common.etemp

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    car = relationship("Car", back_populates="snaps")