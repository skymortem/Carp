"""Ошибка сбора видна из браузера, а не только в journalctl."""
import pytest

from app.services import health


@pytest.mark.asyncio
async def test_error_is_saved_shown_and_cleared(owner):
    from app.database import async_session

    async with async_session() as db:
        assert await health.last_error(db) is None
        await health.record_error(db, "StarLine недоступен или вернул некорректный ответ")
        data = await health.last_error(db)
        assert "недоступен" in data["text"]
        assert data["at"]

        await health.clear_error(db)
        assert await health.last_error(db) is None


@pytest.mark.asyncio
async def test_failed_fetch_is_reported_and_success_clears(owner, monkeypatch):
    from app.database import async_session

    async def boom(*args, **kwargs):
        raise RuntimeError("что-то сломалось")

    monkeypatch.setattr("app.routers.starline.configured_client", lambda: object())
    monkeypatch.setattr("app.routers.starline.collect_and_store", boom)
    response = await owner.post("/starline/fetch")
    assert response.status_code == 500
    assert "что-то сломалось" in response.json()["detail"]

    async with async_session() as db:
        assert "что-то сломалось" in (await health.last_error(db))["text"]

    page = (await owner.get("/dashboard")).text
    assert "Последний сбор не удался" in page
    assert "что-то сломалось" in page

    async with async_session() as db:
        await health.clear_error(db)
    assert "Последний сбор не удался" not in (await owner.get("/dashboard")).text
