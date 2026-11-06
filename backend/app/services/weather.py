"""Уличная температура по координатам машины.

В ответе StarLine (`common`) есть только салон и двигатель — температуры
за бортом там нет. Берём её у Open-Meteo: без ключа, без регистрации,
по последним координатам из снимка.

Сервис намеренно необязательный: любая ошибка сети/ответа возвращает None
(или прошлое значение из кэша), сбор данных из-за погоды не падает.
"""
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_TTL_SECONDS = 30 * 60      # уличная температура не меняется каждую минуту
REQUEST_TIMEOUT = 4.0

# key -> (value, monotonic_ts)
_cache: dict[tuple[float, float], tuple[float, float]] = {}


def _cache_key(lat: float, lon: float) -> tuple[float, float]:
    # Округление ~1 км: соседние снимки попадают в один ключ.
    return round(float(lat), 2), round(float(lon), 2)


def clear_cache() -> None:
    _cache.clear()


async def current_temperature(
    lat: Optional[float],
    lon: Optional[float],
    ttl_seconds: int = CACHE_TTL_SECONDS,
) -> Optional[float]:
    if lat is None or lon is None:
        return None

    try:
        key = _cache_key(lat, lon)
    except (TypeError, ValueError):
        return None

    cached = _cache.get(key)
    now = time.monotonic()
    if cached and now - cached[1] < ttl_seconds:
        return cached[0]

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(API_URL, params={
                "latitude": key[0],
                "longitude": key[1],
                "current": "temperature_2m",
            })
            response.raise_for_status()
            value = response.json()["current"]["temperature_2m"]
        temperature = float(value)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.info("Уличная температура недоступна: %s", exc)
        # Отдаём прошлое значение, если оно было: лучше вчерашнее, чем прочерк.
        return cached[0] if cached else None
    except Exception as exc:  # сеть, TLS, неожиданный формат — сбор не должен падать
        logger.warning("Ошибка получения уличной температуры: %s", exc)
        return cached[0] if cached else None

    _cache[key] = (temperature, now)
    return temperature
