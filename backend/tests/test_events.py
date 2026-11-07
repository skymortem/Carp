"""Журнал событий: одно окно вместо тридцати запросов подряд."""
import pytest

from app.services.starline import StarLineClient, StarLineError


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _client(calls, fail_longer_than=None):
    client = StarLineClient("id", "secret", "login", "password")

    async def api_request(method, endpoint, **kwargs):
        body = kwargs.get("json") or {}
        span = body["period_end"] - body["period_start"]
        calls.append(span)
        if fail_longer_than is not None and span > fail_longer_than:
            raise StarLineError("StarLine недоступен или вернул некорректный ответ")
        return FakeResponse({"code": 200, "events": [
            {"type": 1037, "timestamp": body["period_start"]},
        ]})

    client.api_request = api_request
    return client


@pytest.mark.asyncio
async def test_month_is_fetched_in_one_request():
    calls = []
    client = _client(calls)
    events = await client.get_events("d1", 0, 30 * 86400)
    assert len(events) == 1
    assert calls == [30 * 86400]


@pytest.mark.asyncio
async def test_falls_back_to_daily_windows_when_big_request_fails():
    calls = []
    client = _client(calls, fail_longer_than=86400)
    events = await client.get_events("d1", 0, 3 * 86400)
    assert len(events) == 3
    # Первый — большой, дальше трое суточных.
    assert calls == [3 * 86400, 86400, 86400, 86400]


@pytest.mark.asyncio
async def test_small_window_error_is_not_retried_in_pieces():
    calls = []
    client = _client(calls, fail_longer_than=0)
    with pytest.raises(StarLineError):
        await client.get_events("d1", 0, 3600)
    assert calls == [3600]
