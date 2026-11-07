"""
StarLine API клиент.
Реализует полный цикл авторизации и получения данных.

Процесс:
  1. get_app_code()      — GET  https://id.starline.ru/apiV3/application/getCode
  2. get_app_token()     — GET  .../getToken
  3. get_user_token()    — POST .../user/login
  4. get_slnet_token()   — POST https://developer.starline.ru/json/v2/auth.slid
  5. get_device_data()   — GET  https://developer.starline.ru/json/v3/device/{id}/data
"""
import asyncio
import time
from functools import lru_cache
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SLID_BASE = "https://id.starline.ru"
API_BASE = "https://developer.starline.ru"


class StarLineError(Exception):
    """Ошибка StarLine API"""
    pass


class StarLineClient:
    def __init__(self, app_id: str, app_secret: str, login: str, password: str):
        self._auth_lock = asyncio.Lock()
        self._authenticated_at = 0
        self.app_id = app_id
        self.app_secret = app_secret
        self.login = login
        self.password = password
        self._slnet_token: Optional[str] = None
        self._app_token: Optional[str] = None
        self._user_token: Optional[str] = None
        self._user_id: Optional[str] = None
        self._user_login: Optional[str] = None
        self._webapi_user_id: Optional[str] = None

    async def _request(self, method, url, _allow_unauthorized=False, **kwargs):
        try:
            cookies = kwargs.pop("cookies", None)
            async with httpx.AsyncClient(cookies=cookies) as client:
                response = await client.request(method, url, **kwargs)
            if response.status_code == 401 and _allow_unauthorized:
                return response
            response.raise_for_status()
            # Validate JSON here so malformed upstream responses become controlled errors.
            if not isinstance(response.json(), dict):
                raise ValueError("Expected JSON object")
            return response
        except (httpx.HTTPError, ValueError) as exc:
            raise StarLineError("StarLine недоступен или вернул некорректный ответ") from exc

    @staticmethod
    def _required(data, key):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise StarLineError("В ответе StarLine отсутствует обязательное поле")
        return value

    async def ensure_auth(self):
        async with self._auth_lock:
            if not self._slnet_token or time.monotonic() - self._authenticated_at >= 23 * 3600:
                await self.auth()

    async def api_request(self, method, endpoint, **kwargs):
        await self.ensure_auth()
        token = self._slnet_token
        response = await self._request(method, f"{API_BASE}{endpoint}",
            cookies={"slnet": token}, timeout=15, _allow_unauthorized=True, **kwargs)
        if response.status_code == 401:
            async with self._auth_lock:
                if self._slnet_token == token:
                    await self.auth()
            response = await self._request(method, f"{API_BASE}{endpoint}",
                cookies={"slnet": self._slnet_token}, timeout=15, **kwargs)
        return response

    # ── 1. Код приложения ──────────────────────────────────────────

    async def _get_app_code(self) -> str:
        md5_secret = hashlib.md5(self.app_secret.encode("utf-8")).hexdigest()
        url = f"{SLID_BASE}/apiV3/application/getCode/"
        params = {"appId": self.app_id, "secret": md5_secret}
        resp = await self._request("GET", url, params=params, timeout=15)
        data = resp.json()
        if data.get("state") != 1 or not isinstance(data.get("desc"), dict):
            raise StarLineError("Не удалось получить код приложения StarLine")
        return self._required(data["desc"], "code")

    # ── 2. Токен приложения ────────────────────────────────────────

    async def _get_app_token(self, app_code: str) -> str:
        md5_secret = hashlib.md5((self.app_secret + app_code).encode("utf-8")).hexdigest()
        url = f"{SLID_BASE}/apiV3/application/getToken/"
        params = {"appId": self.app_id, "secret": md5_secret}
        resp = await self._request("GET", url, params=params, timeout=15)
        data = resp.json()
        if data.get("state") != 1 or not isinstance(data.get("desc"), dict):
            raise StarLineError("Не удалось получить токен приложения StarLine")
        return self._required(data["desc"], "token")

    # ── 3. Пользовательский токен SLID ─────────────────────────────

    async def _get_user_token(self, app_token: str, client_ip: str = "") -> str:
        url = f"{SLID_BASE}/apiV3/user/login/"
        sha1_pass = hashlib.sha1(self.password.encode("utf-8")).hexdigest()
        params = {"token": app_token}
        if client_ip:
            params["user_ip"] = client_ip
        payload = {"login": self.login, "pass": sha1_pass}
        resp = await self._request("POST", url, params=params, data=payload, timeout=15)
        data = resp.json()
        if data.get("state") != 1 or not isinstance(data.get("desc"), dict):
            error_msg = str(data.get("desc", ""))
            if "captcha" in error_msg.lower():
                raise StarLineError(
                    "StarLine требует капчу. Слишком много запросов. "
                    "Подожди 2-3 минуты и попробуй снова."
                )
            raise StarLineError("Ошибка входа в StarLine")
        # Сохраняем все данные пользователя из SLID
        user_desc = data.get("desc", {})
        self._user_id = str(user_desc.get("id", ""))
        self._user_login = user_desc.get("login", self.login)
        return self._required(data["desc"], "user_token")

    # ── 4. slnet_token (24ч) ───────────────────────────────────────

    async def _get_slnet_token(self, slid_token: str) -> str:
        url = f"{API_BASE}/json/v2/auth.slid"
        payload = {"slid_token": slid_token}
        resp = await self._request("POST", url, json=payload, timeout=15)
        slnet = resp.cookies.get("slnet")
        if not slnet:
            raise StarLineError("StarLine не вернул сессионную cookie")

        # Сохраняем WebAPI user_id из ответа auth.slid (отличается от SLID user_id!)
        body = resp.json()
        self._webapi_user_id = str(body.get("user_id", ""))
        logger.info("auth.slid → WebAPI user_id: %s", self._webapi_user_id)
        return slnet

    # ── Публичный: полная авторизация ─────────────────────────────

    async def auth(self) -> str:
        """Полный цикл авторизации. Возвращает slnet_token (живёт 24ч)."""
        logger.info("StarLine: step 1 — get app code")
        app_code = await self._get_app_code()
        logger.info("StarLine: step 2 — get app token")
        app_token = await self._get_app_token(app_code)
        self._app_token = app_token
        logger.info("StarLine: step 3 — user login")
        user_token = await self._get_user_token(app_token)
        self._user_token = user_token
        logger.info("StarLine: step 4 — get slnet token")
        slnet = await self._get_slnet_token(user_token)
        self._slnet_token = slnet
        self._authenticated_at = time.monotonic()
        logger.info("StarLine: auth successful")
        return slnet

    # ── Получение данных устройства ────────────────────────────────

    async def get_device_data(self, device_id: str) -> dict:
        """Получить полные данные об устройстве.
        Возвращает DeviceData (obd.mileage, obd.fuel_litres, position, common и т.д.)"""
        resp = await self.api_request("GET", f"/json/v3/device/{device_id}/data")
        data = resp.json()
        if data.get("code") != 200:
            raise StarLineError("StarLine не вернул данные устройства")

        device_data = data.get("data")
        if not isinstance(device_data, dict):
            raise StarLineError("StarLine вернул некорректные данные устройства")
        return device_data

    async def get_obd_errors(self, device_id: str) -> list[dict]:
        """Текущие ошибки OBD из кеша устройства.

        Это снимок «что горит прямо сейчас», а не история: StarLine отдаёт
        только актуальный список, поэтому историю накапливает Carp.
        """
        resp = await self.api_request("GET", f"/json/device/{device_id}/obd_errors")
        try:
            data = resp.json()
        except ValueError as exc:
            raise StarLineError("StarLine вернул некорректный ответ по ошибкам OBD") from exc

        if isinstance(data, dict) and data.get("code") not in (None, 200):
            raise StarLineError("StarLine не вернул ошибки OBD")
        items = data.get("obd_errors") if isinstance(data, dict) else None
        return [item for item in (items or []) if isinstance(item, dict)]

    # ── События устройства (для подсчёта моточасов) ─────────────────

    async def get_events(self, device_id: str, period_start: int, period_end: int) -> list[dict]:
        """События журнала StarLine за окно [period_start, period_end] (unix, сек).

        Журнал отдаёт «Двигатель запущен/остановлен» с точностью до секунды —
        по этим парам Carp считает накопительные моточасы, потому что
        `state.motohrs` у части устройств всегда null.

        Окно запрашиваем одним запросом: проверено, что StarLine отдаёт 30 дней
        целиком (самое старое событие приходит ровно на границе окна). Резать на
        сутки — это 30 запросов подряд, а на залповый поток StarLine отвечает
        отказом, и первый сбор после установки падал. Дробим только если
        большой запрос не прошёл.
        """
        try:
            return await self._events_window(device_id, int(period_start), int(period_end))
        except StarLineError as exc:
            if int(period_end) - int(period_start) <= 24 * 3600:
                raise
            logger.info("Журнал за большое окно не отдался (%s) — читаю по суткам", exc)

        events: list[dict] = []
        window = 24 * 3600
        start = int(period_start)
        end = int(period_end)
        while start < end:
            chunk_end = min(start + window, end)
            events.extend(await self._events_window(device_id, start, chunk_end))
            start = chunk_end
            await asyncio.sleep(0.3)  # не долбим API очередью запросов
        return events

    async def _events_window(self, device_id: str, start: int, end: int) -> list[dict]:
        resp = await self.api_request("POST", f"/json/v2/device/{device_id}/events",
                                      json={"period_start": start, "period_end": end})
        data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (None, 200):
            raise StarLineError("StarLine не вернул журнал событий")
        items = data.get("events") if isinstance(data, dict) else None
        return [item for item in (items or []) if isinstance(item, dict)]

    # ── Получение списка устройств пользователя ────────────────────

    async def get_user_devices(self) -> list[dict]:
        """Получить список устройств пользователя."""
        await self.ensure_auth()
        if not self._webapi_user_id:
            raise StarLineError("No WebAPI user_id from auth.slid")

        uid = self._webapi_user_id

        endpoints = [
            f"{API_BASE}/json/v1/user/{uid}/devices",
            f"{API_BASE}/json/v1/user/{uid}/deviceList",
            f"{API_BASE}/json/v2/user/{uid}/user_info",
        ]
        for url in endpoints:
            try:
                resp = await self.api_request("GET", url.removeprefix(API_BASE))
                data = resp.json()
                if data.get("code") == 200:
                    items = data.get("devices") or data.get("data") or []
                    if isinstance(items, dict):
                        items = items.get("devices", [])
                    if isinstance(items, list):
                        items = [item for item in items if isinstance(item, dict) and item.get("device_id")]
                        if items:
                            return items
            except Exception:
                continue

        raise StarLineError("Could not find devices for this user")

    # ── Метод-хелп: распарсить сниппет ─────────────────────────────

    @staticmethod
    def parse_snapshot(device_data: dict) -> dict:
        """Извлечь нужные поля из DeviceData в плоский словарь."""
        def section(name):
            value = device_data.get(name)
            return value if isinstance(value, dict) else {}
        obd, position, common, state = (section(name) for name in ('obd', 'position', 'common', 'state'))

        def counter(value):
            try:
                if value is None or isinstance(value, bool):
                    return None
                number = float(value)
                return int(number) if number >= 0 and number.is_integer() else None
            except (ValueError, TypeError, OverflowError):
                return None

        def flag(value):
            if value in (True, 1, '1'):
                return True
            if value in (False, 0, '0'):
                return False
            return None

        ts_raw = obd.get("ts") or device_data.get("activity_ts")
        try:
            if ts_raw is None or int(ts_raw) <= 0:
                raise ValueError("Missing timestamp")
            ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            ts = datetime.now(tz=timezone.utc)

        return {
            "ts": ts,
            "mileage_km": counter(obd.get("mileage")),
            "fuel_litres": obd.get("fuel_litres"),
            "fuel_percent": obd.get("fuel_percent"),
            "lat": position.get("y"),
            "lon": position.get("x"),
            "speed_kmh": position.get("s"),
            "is_moving": flag(position.get("is_move")),
            "gsm_lvl": common.get("gsm_lvl"),
            "battery_v": common.get("battery"),
            "engine_on": flag(state.get("ign")),
            "is_armed": flag(state.get("arm")),
            "ctemperature": common.get("ctemp"),
            "etemperature": common.get("etemp"),
            "battery_type": common.get("battery_type"),
            "motohours_minutes": counter(state.get("motohrs")),
        }

    @staticmethod
    def parse_r_start(device_data: dict) -> dict:
        """Настройки автозапуска из data.r_start.

        Каждое условие приходит как {"has": bool, "value": int}: has — условие
        включено, value — порог (температура °C, период в часах, порог по АКБ в
        сотых вольта, Unix-время для расписания).

        Внимание: `state.r_start` — не «автозапуск включён», а «двигатель прямо
        сейчас запущен автозапуском». Включённость определяется флагами `has`
        условий: при включённом автозапуске state.r_start обычно false.
        """
        r_start = device_data.get("r_start")
        r_start = r_start if isinstance(r_start, dict) else {}
        state = device_data.get("state")
        state = state if isinstance(state, dict) else {}

        def condition(name: str) -> tuple[bool | None, int | None]:
            raw = r_start.get(name)
            if not isinstance(raw, dict):
                return None, None
            raw_value = raw.get("value")
            value: int | None = None
            if raw_value is not None and not isinstance(raw_value, bool):
                try:
                    value = int(raw_value)
                except (TypeError, ValueError):
                    value = None
            return bool(raw.get("has")), value

        def flag(raw) -> bool | None:
            if raw in (True, 1, '1'):
                return True
            if raw in (False, 0, '0'):
                return False
            return None

        temp_on, temp = condition("temp")
        period_on, period = condition("period")
        battery_on, battery = condition("battery")
        cron_on, cron = condition("cron")

        return {
            "r_start_active": flag(state.get("r_start")),
            "r_start_temp_on": temp_on,
            "r_start_temp": temp,
            "r_start_period_on": period_on,
            "r_start_period_h": period,
            "r_start_battery_on": battery_on,
            "r_start_battery": battery,
            "r_start_cron_on": cron_on,
            "r_start_cron": cron,
        }

@lru_cache(maxsize=1)
def cached_client(app_id, app_secret, login, password):
    return StarLineClient(app_id, app_secret, login, password)


def configured_client():
    from app.config import settings
    credentials = (settings.starline_app_id, settings.starline_app_secret,
                   settings.starline_login, settings.starline_password)
    if not all(credentials):
        raise StarLineError("Подключите StarLine в настройках")
    return cached_client(*credentials)
