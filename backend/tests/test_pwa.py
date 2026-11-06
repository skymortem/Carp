"""PWA: манифест, service worker, иконки.

Главное — манифест и service worker должны отдаваться с корня, иначе
scope ограничится /static/ и Safari/Chrome не предложат установку.
"""
import json

import pytest


@pytest.mark.asyncio
async def test_manifest_is_served_with_correct_type(client):
    response = await client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert "manifest" in response.headers["content-type"]

    manifest = json.loads(response.text)
    assert manifest["start_url"] == "/dashboard"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["name"] and manifest["short_name"] == "Carp"
    assert manifest["theme_color"] == "#0e1116"

    sizes = {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]}
    assert ("192x192", "any") in sizes
    assert ("512x512", "any") in sizes
    assert ("512x512", "maskable") in sizes


@pytest.mark.asyncio
async def test_service_worker_is_served_from_root_with_scope(client):
    response = await client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert "no-cache" in response.headers["cache-control"]
    # HTML не кешируем: это личные данные, которые не должны оставаться
    # в кеше устройства после выхода.
    assert "caches.match(request)" in response.text


@pytest.mark.asyncio
async def test_icons_and_favicon_are_available(client):
    for path in (
        "/static/icons/icon-192.png",
        "/static/icons/icon-512.png",
        "/static/icons/apple-touch-icon.png",
        "/static/icons/favicon-32.png",
        "/static/offline.html",
        "/favicon.ico",
    ):
        response = await client.get(path)
        assert response.status_code == 200, path


@pytest.mark.asyncio
async def test_base_template_has_ios_app_meta(owner):
    html = (await owner.get("/dashboard")).text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'rel="apple-touch-icon"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'name="apple-mobile-web-app-status-bar-style"' in html
    assert "viewport-fit=cover" in html
    assert "navigator.serviceWorker.register('/sw.js')" in html
    assert "env(safe-area-inset-top)" in html
