import logging
from contextlib import asynccontextmanager
from pathlib import Path

from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.migrations import run_migrations
from app.routers import auth, starline, dashboard, reports, service_plan, settings as settings_router
from app.services import fuel, telegram
from app.services.scheduler import start as start_scheduler, stop as stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# HTTP URLs can contain StarLine authentication tokens.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — applying migrations...")
    await run_migrations()
    start_scheduler()
    telegram.set_handler(fuel.handle_message)
    telegram.start()
    logger.info("DB ready! Scheduler active.")
    yield
    stop_scheduler()
    await telegram.stop()
    logger.info("Shutting down.")


app = FastAPI(title="Carp — Car Expense Tracker", lifespan=lifespan)

@app.exception_handler(HTTPException)
async def unauthenticated_redirect(request: Request, exc: HTTPException):
    """Не оставлять человека в тупике с «{"detail":"Not logged in"}».

    Веб-приложение с экрана «Домой» на iPhone хранит cookie отдельно от Safari,
    и его сессия рано или поздно истекает. Раньше страница в этом случае
    отдавала JSON ошибки 401: ни формы входа, ни ссылок — только текст. Для
    обычного перехода (HTML) отправляем на /login с возвратом назад, для HTMX —
    заголовком HX-Redirect. API продолжает отвечать JSON, как и раньше.
    """
    if exc.status_code == 401:
        path = request.url.path
        if request.headers.get("hx-request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        if "text/html" in request.headers.get("accept", "") and not path.startswith("/api"):
            return RedirectResponse(f"/login?next={quote(path)}", status_code=302)
    return await http_exception_handler(request, exc)


# Статика — создаём папку если нет
static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# PWA: манифест и service worker должны жить в корне, иначе их scope
# ограничится /static/ и приложение не станет «устанавливаемым».
@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse(
        static_dir / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(
        static_dir / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(static_dir / "icons" / "favicon-32.png", media_type="image/png")


@app.get("/robots.txt", include_in_schema=False)
async def robots(request: Request):
    base = str(request.base_url).rstrip("/")
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        # Внутренние экраны и API поисковикам не нужны, а форма входа
        # в индексе — ровно то, из-за чего домен выглядит фишингом.
        "Disallow: /dashboard\n"
        "Disallow: /login\n"
        "Disallow: /register\n"
        "Disallow: /setup\n"
        "Disallow: /starline-console\n"
        "Disallow: /service-plan\n"
        "Disallow: /reports\n"
        "Disallow: /auth/\n"
        "Disallow: /starline/\n"
        "Disallow: /api/\n"
        "\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request):
    base = str(request.base_url).rstrip("/")
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{base}/</loc><changefreq>monthly</changefreq><priority>1.0</priority></url>\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")

# Роутеры
app.include_router(auth.router)
app.include_router(starline.router)
app.include_router(dashboard.router)
app.include_router(service_plan.router)
app.include_router(reports.router)
app.include_router(settings_router.router)
