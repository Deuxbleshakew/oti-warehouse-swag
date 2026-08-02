"""
backend/main.py — the FastAPI application.

Run with:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000

--host 0.0.0.0 is what makes it reachable from other machines on the
network/VPN at all — 127.0.0.1 (the default) would only ever be reachable
from this same machine. Bind it to 0.0.0.0 only once you're ready for
other devices to reach it; for local-only testing, drop that flag.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from backend.api import auth, catalog, orders, admin
from backend.config import ASSETS_DIR as _ASSETS_DIR
from backend.db.session import Base, engine, SessionLocal
from backend.db.schema_upgrade import ensure_additive_columns
from backend.services.item_service import backfill_legacy_image_blobs

_FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
_ORDER_HTML_PATH = os.path.join(_FRONTEND_DIR, "order.html")

# Safe migration-lite step: create_all never deletes or rewrites existing
# tables, but it does add newly introduced tables such as item_image_blobs.
# This keeps upgraded local installs working even if init_db.py is not rerun.
Base.metadata.create_all(bind=engine)
ensure_additive_columns(engine)
_legacy_db = SessionLocal()
try:
    backfill_legacy_image_blobs(_legacy_db)
finally:
    _legacy_db.close()

APP_BUILD = "5.7-workflow"

app = FastAPI(
    title="Oti-Warehouse Swag API",
    description="Internal inventory + ordering backend. Internal network "
                "or VPN only — never expose this to the public internet.",
    version="1.0.0",
)

# ---- CORS -------------------------------------------------------------------
# The frontend is a static HTML file opened from a shared folder or served
# from this same machine — its "origin" varies (file://, or a LAN IP/
# hostname). For an internal tool with a known, small set of places the
# frontend gets opened from, allow_origins=["*"] combined with real bearer-
# token auth on every request is a reasonable trade — there are no cookies
# involved for CORS to leak, and every request still needs a valid session
# token that only comes from a successful /auth/login. If you later serve
# the frontend from a fixed URL, tighten this to that exact origin instead.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.middleware("http")
async def prevent_stale_frontend_cache(request, call_next):
    """The ordering page changes independently of the API data. Explicitly
    disable browser/proxy caching for the HTML so a redeploy cannot keep
    showing an older interface under the same URL."""
    response = await call_next(request)
    if request.url.path in {"/", "/order.html"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Oti-Build"] = APP_BUILD
    return response

app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")

app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(orders.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    """Unauthenticated — just confirms the service is up. Useful for the
    admin app / a load balancer / a quick curl to check it's alive."""
    return {"status": "ok", "build": APP_BUILD}


@app.get("/version", include_in_schema=False)
def version():
    return {"build": APP_BUILD}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/order.html", response_class=HTMLResponse, include_in_schema=False)
def serve_order_page():
    """Serves the ordering page at the site's own root, so visiting the
    deployed URL from any device — phone included — is the entire
    experience. No separate file to carry around or keep updated; the
    page's own JS defaults its API calls to this same origin (see
    order.html's DEFAULT_API_BASE), so there's nothing to configure
    either. Read from disk on every request rather than cached, so it
    always reflects whatever's currently deployed — this file is tiny,
    so that cost is negligible."""
    with open(_ORDER_HTML_PATH, encoding="utf-8") as f:
        return f.read()
