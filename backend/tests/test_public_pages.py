"""Публичная главная, robots.txt и sitemap.xml.

Смысл этих страниц не в маркетинге: у установки должно быть нормальное
публичное «лицо», а не экран ввода пароля на молодом домене — иначе
репутационные списки (и человек) читают сайт как фишинговый.
"""
import pytest


@pytest.mark.asyncio
async def test_public_landing_renders_for_anonymous(client):
    response = await client.get("/")
    assert response.status_code == 200
    html = response.text

    assert "Учёт автомобиля" in html
    assert "StarLine" in html
    assert "self-hosted" in html.lower() or "Self-hosted" in html
    assert "github.com/skymortem/Carp" in html
    assert "/register" in html  # установка ещё без владельца
    assert "Создать аккаунт" in html


@pytest.mark.asyncio
async def test_landing_hides_registration_when_owner_exists(owner):
    """Владелец уже есть — регистрация закрыта, значит и кнопки быть не должно."""
    client = owner
    # Сбрасываем cookie владельца, оставаясь на той же базе.
    client.cookies.delete("access_token")
    response = await client.get("/")
    assert response.status_code == 200
    assert "Войти" in response.text
    assert "Создать аккаунт" not in response.text


@pytest.mark.asyncio
async def test_register_page_redirects_when_owner_exists(owner):
    """Форма регистрации, которая гарантированно вернёт 403, не показывается."""
    owner.cookies.delete("access_token")
    response = await owner.get("/register", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_logged_in_user_is_redirected_to_dashboard(owner):
    response = await owner.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_robots_keeps_internal_pages_out_of_index(client):
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    body = response.text
    assert "User-agent: *" in body
    assert "Allow: /" in body
    for private in ("/dashboard", "/login", "/setup", "/api/"):
        assert f"Disallow: {private}" in body
    assert "Sitemap: http://test/sitemap.xml" in body


@pytest.mark.asyncio
async def test_sitemap_lists_only_public_root(client):
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]

    body = response.text
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<loc>http://test/</loc>" in body
    assert "/dashboard" not in body


@pytest.mark.asyncio
async def test_landing_has_no_third_party_trackers(client):
    """На публичной странице не должно быть аналитики и внешних шрифтов."""
    html = (await client.get("/")).text
    for tracker in ("google-analytics", "googletagmanager", "mc.yandex", "facebook", "hotjar"):
        assert tracker not in html
