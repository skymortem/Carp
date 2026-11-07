"""Заправки: обнаружение прироста топлива и цена литра через Telegram.

Объём Carp считает сам — разница уровней между соседними снимками. Цену
знает только владелец, поэтому бот спрашивает её один раз на заправку и
сохраняет цену литра и стоимость (цена × объём).

Точность объёма ограничена интервалом сбора: если между снимками успели
проехать, часть заправленного топлива уже сгорела, и разница уровней выйдет
меньше реальной. Для часового сбора это единицы литров в худшем случае.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.models.car import FuelFill, StarSnap
from app.services import telegram

logger = logging.getLogger(__name__)

# Заправка, обнаруженная час назад и всё ещё без цены, считается той же самой:
# уровень мог подрасти двумя шагами, но это один заезд на АЗС.
MERGE_WINDOW = timedelta(hours=3)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


async def previous_litres(db, car_id: int, before: datetime) -> Optional[float]:
    """Уровень топлива в предыдущем снимке (до момента `before`)."""
    return await db.scalar(
        select(StarSnap.fuel_litres)
        .where(StarSnap.car_id == car_id, StarSnap.fuel_litres.isnot(None), StarSnap.ts < before)
        .order_by(StarSnap.ts.desc(), StarSnap.id.desc())
        .limit(1)
    )


async def detect_fill(db, car, snapshot: dict) -> Optional[FuelFill]:
    """Создать запись о заправке, если уровень топлива вырос.

    Возвращает новую/обновлённую запись либо None. Снимок к этому моменту уже
    сохранён, поэтому «предыдущий» ищется строго раньше его времени.
    """
    new_litres = snapshot.get("fuel_litres")
    if new_litres is None:
        return None
    try:
        new_value = float(new_litres)
    except (TypeError, ValueError):
        return None

    ts = snapshot.get("ts")
    detected_at = _as_naive(ts) if isinstance(ts, datetime) else _now()
    prev_value = await previous_litres(db, car.id, detected_at)
    if prev_value is None:
        return None

    delta = new_value - float(prev_value)
    if delta < settings.fuel_fill_min_litres:
        return None

    last = await db.scalar(
        select(FuelFill).where(FuelFill.car_id == car.id)
        .order_by(FuelFill.detected_at.desc()).limit(1)
    )
    if (last is not None and last.status != "done" and last.detected_at is not None
            and detected_at - _as_naive(last.detected_at) <= MERGE_WINDOW):
        # Уровень продолжил расти в следующем снимке — это та же заправка.
        base = last.prev_litres if last.prev_litres is not None else prev_value
        last.new_litres = new_value
        last.volume_litres = round(new_value - float(base), 2)
        await db.commit()
        logger.info("Заправка #%s уточнена: %.1f л", last.id, last.volume_litres)
        return last

    fill = FuelFill(
        car_id=car.id,
        detected_at=detected_at,
        prev_litres=round(float(prev_value), 2),
        new_litres=round(new_value, 2),
        volume_litres=round(delta, 2),
        status="awaiting_price",
    )
    db.add(fill)
    await db.commit()
    await db.refresh(fill)
    logger.info("Обнаружена заправка #%s: +%.1f л (%.1f → %.1f)",
                fill.id, fill.volume_litres, prev_value, new_value)
    return fill


def _format_fill(fill: FuelFill) -> str:
    parts = [f"{fill.volume_litres:.1f} л"]
    if fill.prev_litres is not None and fill.new_litres is not None:
        parts.append(f"уровень {fill.prev_litres:.0f} → {fill.new_litres:.0f} л")
    return ", ".join(parts)


async def ask_next_price(db, fill: Optional[FuelFill] = None) -> bool:
    """Спросить цену литра по заправке (по умолчанию — по самой старой без цены)."""
    if fill is None:
        fill = await pending_question(db)
    if fill is None:
        return False
    text = (
        "⛽ <b>Заправка обнаружена</b>\n"
        f"{_format_fill(fill)}\n\n"
        "Сколько стоил литр? Ответь числом в рублях (например <code>59.90</code>).\n"
        "Если не хочешь сохранять — <code>/skip</code>."
    )
    sent = await telegram.send_message(text)
    if sent:
        fill.asked_at = _now()
        await db.commit()
    return sent


async def pending_question(db) -> Optional[FuelFill]:
    """Самая старая заправка, по которой ещё нужна цена."""
    return await db.scalar(
        select(FuelFill).where(FuelFill.status == "awaiting_price")
        .order_by(FuelFill.detected_at).limit(1)
    )


def parse_price(text: str) -> Optional[float]:
    """Вытащить цену литра из ответа: «59.9», «59,90 ₽», «цена 60».

    Берём первое число. Отрицательные, нулевые и явно нецены (больше 1000 ₽
    за литр) отбрасываем — скорее всего человек ответил суммой чека.
    """
    cleaned = (text or "").replace(",", ".").replace("\u00a0", " ")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        value = float(match.group())
    except ValueError:
        return None
    if not 1 <= value <= 1000:
        return None
    return round(value, 2)


async def handle_message(text: str, chat_id: str) -> str:
    """Обработчик ответов Telegram-бота."""
    from app.database import async_session

    command = (text or "").strip().lower()
    async with async_session() as db:
        fill = await pending_question(db)
        if fill is None:
            if command in ("/fuel", "/fills", "/start", "/help"):
                return await _summary(db)
            return "Сейчас нет заправок, по которым нужна цена. /help — что умею."

        if command in ("/skip", "skip", "пропустить"):
            fill.status = "skipped"
            fill.answered_at = _now()
            await db.commit()
            await ask_next_price(db)
            return f"Ок, заправка #{fill.id} ({fill.volume_litres:.1f} л) сохранена без цены."

        price = parse_price(text)
        if price is None:
            return ("Не понял цену. Пришли число в рублях за литр, например <code>59.90</code>, "
                    "или <code>/skip</code>.")

        fill.price_per_litre = price
        fill.total_cost = round(price * float(fill.volume_litres), 2)
        fill.status = "done"
        fill.answered_at = _now()
        await db.commit()

        await ask_next_price(db)

        return (f"✅ Записал: {fill.volume_litres:.1f} л × {price:.2f} ₽ = "
                f"<b>{fill.total_cost:.2f} ₽</b>")


async def _summary(db) -> str:
    from sqlalchemy import func
    total_fills, total_litres, total_cost = (await db.execute(
        select(func.count(FuelFill.id), func.sum(FuelFill.volume_litres), func.sum(FuelFill.total_cost))
        .where(FuelFill.status == "done")
    )).one()
    if not total_fills:
        return "Заправок с ценой пока нет."
    return (f"Заправок: {total_fills}\n"
            f"Залито: {total_litres:.1f} л\n"
            f"Потрачено: {total_cost or 0:.2f} ₽")
