"""Диагностическая консоль: что пропускаем, а что нет.

Консоль — инструмент чтения. Проверяем обе стороны: read-only эндпоинты
проходят, управляющие команды получают 403 и наружу не уходят.
"""
import pytest

from app.routers import starline as starline_router


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return "{}"


class _FakeClient:
    def __init__(self, calls):
        self._calls = calls

    async def api_request(self, method, endpoint, **kwargs):
        self._calls.append((method, endpoint, kwargs))
        return _FakeResponse({"code": 200, "endpoint": endpoint})


READ_ONLY = [
    ("GET", "/json/device/{device_id}/obd_errors"),
    ("GET", "/json/device/{device_id}/obd_params"),
    ("GET", "/json/device/{device_id}/ctrls_library"),
    ("GET", "/json/v4/device/{device_id}/settings"),
    ("GET", "/json/v3/library/events"),
    ("POST", "/json/v1/device/{device_id}/details"),
    ("POST", "/json/v2/device/{device_id}/driving_score"),
    ("POST", "/json/v2/device/{device_id}/driving_score_history"),
    ("POST", "/json/v1/device/{device_id}/ways"),
    ("POST", "/json/v2/device/{device_id}/events"),
    ("POST", "/json/v1/device/{device_id}/getObdData"),
]

CONTROL = [
    ("POST", "/json/v1/device/{device_id}/set_param"),
    ("POST", "/json/v2/device/{device_id}/async"),
    ("POST", "/json/v2/device/{device_id}/controls"),
    ("POST", "/json/v1/device/{device_id}/settings/webasto"),
    ("POST", "/json/v2/device/{device_id}/settings/remote_start"),
    ("GET", "/json/v1/user/1/devices"),
]


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    monkeypatch.setattr(starline_router, "configured_client", lambda: _FakeClient(calls))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("method,endpoint", READ_ONLY)
async def test_read_only_endpoints_pass(owner, upstream, method, endpoint):
    response = await owner.post(
        "/starline/api-raw",
        json={"method": method, "endpoint": endpoint, "body": {"day": 1700000000}},
    )
    assert response.status_code == 200
    assert upstream[0][1] == endpoint.replace("{device_id}", "device1")
    # Тело запроса уходит наверх только для POST.
    assert ("json" in upstream[0][2]) is (method == "POST")


@pytest.mark.asyncio
@pytest.mark.parametrize("method,endpoint", CONTROL)
async def test_control_endpoints_are_refused(owner, upstream, method, endpoint):
    response = await owner.post(
        "/starline/api-raw",
        json={"method": method, "endpoint": endpoint, "body": {"type": "ign", "ign": 1}},
    )
    assert response.status_code == 403
    assert "диагностические" in response.json()["detail"]
    assert upstream == []  # ни один запрос не дошёл до StarLine


@pytest.mark.asyncio
async def test_console_page_lists_new_quick_actions(owner):
    html = (await owner.get("/starline-console")).text
    assert "OBD errors" in html
    assert "Driving score" in html
    assert "Ways (24h)" in html
