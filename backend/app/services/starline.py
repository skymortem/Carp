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

    # ── 1. Код приложения ──────────────────────────────────────────

    def _get_app_code(self) -> str:
        md5_secret = hashlib.md5(self.app_secret.encode("utf-8")).hexdigest()
        url = f"{SLID_BASE}/apiV3/application/getCode/"
        params = {"appId": self.app_id, "secret": md5_secret}
        resp = httpx.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("state") != 1:
            raise StarLineError(f"getCode failed: {data}")
        return data["desc"]["code"]

    # ── 2. Токен приложения ────────────────────────────────────────

    def _get_app_token(self, app_code: str) -> str:
        md5_secret = hashlib.md5((self.app_secret + app_code).encode("utf-8")).hexdigest()
        url = f"{SLID_BASE}/apiV3/application/getToken/"
        params = {"appId": self.app_id, "secret": md5_secret}
        resp = httpx.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("state") != 1:
            raise StarLineError(f"getToken failed: {data}")
        return data["desc"]["token"]

    # ── 3. Пользовательский токен SLID ─────────────────────────────

    def _get_user_token(self, app_token: str, client_ip: str = "") -> str:
        url = f"{SLID_BASE}/apiV3/user/login/"
        sha1_pass = hashlib.sha1(self.password.encode("utf-8")).hexdigest()
        params = {"token": app_token}
        if client_ip:
            params["user_ip"] = client_ip
        payload = {"login": self.login, "pass": sha1_pass}
        resp = httpx.post(url, params=params, data=payload, timeout=15)
        data = resp.json()
        if data.get("state") != 1:
            error_msg = data.get("desc", {}).get("message", str(data))
            if "captcha" in error_msg.lower():
                raise StarLineError(
                    "StarLine требует капчу. Слишком много запросов. "
                    "Подожди 2-3 минуты и попробуй снова."
                )
            raise StarLineError(f"User login failed: {data}")
        # Сохраняем все данные пользователя из SLID
        user_desc = data.get("desc", {})
        self._user_id = str(user_desc.get("id", ""))
        self._user_login = user_desc.get("login", self.login)
        logger.info("SLID login response: %s", data)
        return data["desc"]["user_token"]

    # ── 4. slnet_token (24ч) ───────────────────────────────────────

    def _get_slnet_token(self, slid_token: str) -> str:
        url = f"{API_BASE}/json/v2/auth.slid"
        payload = {"slid_token": slid_token}
        resp = httpx.post(url, json=payload, timeout=15)
        slnet = resp.cookies.get("slnet")
        if not slnet:
            raise StarLineError(f"Auth.slid failed — no slnet cookie: {resp.text}")

        # Сохраняем WebAPI user_id из ответа auth.slid (отличается от SLID user_id!)
        body = resp.json()
        self._webapi_user_id = str(body.get("user_id", ""))
        logger.info("auth.slid → WebAPI user_id: %s", self._webapi_user_id)
        return slnet

    # ── Публичный: полная авторизация ─────────────────────────────

    def auth(self) -> str:
        """Полный цикл авторизации. Возвращает slnet_token (живёт 24ч)."""
        logger.info("StarLine: step 1 — get app code")
        app_code = self._get_app_code()
        logger.info("StarLine: step 2 — get app token")
        app_token = self._get_app_token(app_code)
        self._app_token = app_token
        logger.info("StarLine: step 3 — user login")
        user_token = self._get_user_token(app_token)
        self._user_token = user_token
        logger.info("StarLine: step 4 — get slnet token")
        slnet = self._get_slnet_token(user_token)
        self._slnet_token = slnet
        logger.info("StarLine: auth successful")
        return slnet

    # ── Получение данных устройства ────────────────────────────────

    def get_device_data(self, device_id: str) -> dict:
        """Получить полные данные об устройстве.
        Возвращает DeviceData (obd.mileage, obd.fuel_litres, position, common и т.д.)"""
        if not self._slnet_token:
            self.auth()

        url = f"{API_BASE}/json/v3/device/{device_id}/data"
        cookies = {"slnet": self._slnet_token}
        resp = httpx.get(url, cookies=cookies, timeout=15)

        # Если 401 — токен протух, переавторизоваться
        if resp.status_code == 401:
            logger.info("StarLine: token expired, re-authing")
            self.auth()
            cookies = {"slnet": self._slnet_token}
            resp = httpx.get(url, cookies=cookies, timeout=15)

        data = resp.json()
        if data.get("code") != 200:
            raise StarLineError(f"getDeviceData failed: {data}")

        device_data = data["data"]
        logger.info("getDeviceData keys: %s", list(device_data.keys()))
        logger.info("getDeviceData obd: %s", device_data.get("obd"))
        logger.info("getDeviceData position: %s", device_data.get("position"))
        if device_data.get("common"):
            logger.info("getDeviceData common: %s", device_data.get("common"))
        if device_data.get("event"):
            logger.info("getDeviceData event: %s", device_data.get("event"))
        return device_data

    # ── Получение списка устройств пользователя ────────────────────

    def get_user_devices(self) -> list[dict]:
        """Получить список устройств пользователя."""
        if not self._slnet_token:
            self.auth()
        if not self._webapi_user_id:
            raise StarLineError("No WebAPI user_id from auth.slid")

        cookies = {"slnet": self._slnet_token}
        uid = self._webapi_user_id

        endpoints = [
            f"{API_BASE}/json/v1/user/{uid}/devices",
            f"{API_BASE}/json/v1/user/{uid}/deviceList",
            f"{API_BASE}/json/v2/user/{uid}/user_info",
        ]
        for url in endpoints:
            try:
                resp = httpx.get(url, cookies=cookies, timeout=15)
                data = resp.json()
                logger.info("getUserDevices %s → %s", url.split("/")[-1], data)
                if data.get("code") == 200:
                    items = data.get("devices") or data.get("data") or []
                    if items:
                        return items
            except Exception:
                continue

        raise StarLineError("Could not find devices for this user")

    # ── Получение трека (пробег из трека) ──────────────────────────

    def get_last_position(self, device_id: str) -> dict:
        """Получить последние данные о местоположении (может содержать пробег)."""
        if not self._slnet_token:
            self.auth()
        cookies = {"slnet": self._slnet_token}
        # Пробуем position endpoint
        url = f"{API_BASE}/json/v1/device/{device_id}/position"
        resp = httpx.get(url, cookies=cookies, timeout=15)
        data = resp.json()
        logger.info("getPosition response: %s", data)
        return data

    # ── Получение данных OBD (пробег по CAN) ─────────────────────

    def get_obd_data(self, device_id: str) -> dict:
        """Получить историю пробега по CAN.
        POST /json/v1/device/{device_id}/getObdData"""
        if not self._slnet_token:
            self.auth()
        url = f"{API_BASE}/json/v1/device/{device_id}/getObdData"
        cookies = {"slnet": self._slnet_token}
        # Пробуем с пустым JSON телом
        resp = httpx.post(url, cookies=cookies, json={}, timeout=15)
        if resp.status_code in (401, 400):
            self.auth()
            cookies = {"slnet": self._slnet_token}
            resp = httpx.post(url, cookies=cookies, json={}, timeout=15)
        data = resp.json()
        logger.info("getObdData response: %s", data)
        return data

    # ── Метод-хелп: распарсить сниппет ─────────────────────────────

    @staticmethod
    def parse_snapshot(device_data: dict) -> dict:
        """Извлечь нужные поля из DeviceData в плоский словарь."""
        obd = device_data.get("obd") or {}
        position = device_data.get("position") or {}
        common = device_data.get("common") or {}
        state = device_data.get("state") or {}

        ts_raw = obd.get("ts") or device_data.get("activity_ts") or 0
        try:
            ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        except (ValueError, OSError):
            ts = datetime.now(tz=timezone.utc)

        return {
            "ts": ts,
            "mileage_km": obd.get("mileage"),
            "fuel_litres": obd.get("fuel_litres"),
            "fuel_percent": obd.get("fuel_percent"),
            "lat": position.get("y"),
            "lon": position.get("x"),
            "speed_kmh": position.get("s"),
            "is_moving": bool(position.get("is_move")),
            "gsm_lvl": common.get("gsm_lvl"),
            "battery_v": common.get("battery"),
            "engine_on": bool(state.get("ign")),
            "is_armed": bool(state.get("arm")),
            "ctemperature": common.get("ctemp"),
            "etemperature": common.get("etemp"),
            "motohours_minutes": state.get("motohrs"),
        }