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
from fastapi.responses import HTMLResponse

from backend.api import auth, catalog, orders, admin, v111
from backend.db.session import Base, engine, SessionLocal
from backend.db.schema_upgrade import ensure_additive_columns
from backend.services.item_service import backfill_legacy_image_blobs, ensure_location_balances
from backend.models.models import Item

_FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
_ORDER_HTML_PATH = os.path.join(_FRONTEND_DIR, "order.html")
_FOLDER_BUILDER_PATH = os.path.join(_FRONTEND_DIR, "folder-builder.html")

# Safe migration-lite step: create_all never deletes or rewrites existing
# tables, but it does add newly introduced tables such as item_image_blobs.
# This keeps upgraded local installs working even if init_db.py is not rerun.
Base.metadata.create_all(bind=engine)
ensure_additive_columns(engine)
_legacy_db = SessionLocal()
try:
    backfill_legacy_image_blobs(_legacy_db)
    for _item in _legacy_db.query(Item).all(): ensure_location_balances(_legacy_db, _item)
    _legacy_db.commit()
finally:
    _legacy_db.close()

APP_BUILD = "1.11"

app = FastAPI(
    title="Otiwarehouse API",
    description="Internal inventory + ordering backend. Internal network "
                "or VPN only — never expose this to the public internet.",
    version="1.0",
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
    if request.url.path in {"/", "/order.html", "/folder-builder.html"} or request.url.path.startswith("/badge-studio"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Oti-Build"] = APP_BUILD
    return response

app.include_router(auth.router)
# v1.11 routes go first: /catalog/suggest-code and /catalog/item-submissions/*
# must win over catalog.py's /catalog/{item_id}.
app.include_router(v111.router)
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


@app.get("/folder-builder.html", response_class=HTMLResponse, include_in_schema=False)
def serve_folder_builder():
    with open(_FOLDER_BUILDER_PATH, encoding="utf-8") as f:
        return f.read()


# ---- v1.11 static assets: logo/favicons and Badge Studio -------------------
_BRAND_DIR = os.path.join(_FRONTEND_DIR, "brand")
_BADGE_DIR = os.path.join(_FRONTEND_DIR, "badge-studio")
_STATIC_TYPES = {".png": "image/png", ".ico": "image/x-icon", ".svg": "image/svg+xml",
                 ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".html": "text/html; charset=utf-8", ".json": "application/json",
                 ".webmanifest": "application/manifest+json"}


def _static_file(base_dir: str, name: str, cache: str):
    from fastapi import HTTPException
    from fastapi.responses import Response
    safe = os.path.normpath(name).lstrip("/\\")
    path = os.path.join(base_dir, safe)
    if safe.startswith("..") or not os.path.isfile(path):
        raise HTTPException(404, "Not found")
    with open(path, "rb") as fh:
        data = fh.read()
    ext = os.path.splitext(path)[1].lower()
    return Response(content=data, media_type=_STATIC_TYPES.get(ext, "application/octet-stream"),
                    headers={"Cache-Control": cache})


@app.get("/brand/{name}", include_in_schema=False)
def brand_asset(name: str):
    return _static_file(_BRAND_DIR, name, "public, max-age=86400")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return _static_file(_BRAND_DIR, "favicon.ico", "public, max-age=86400")


@app.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon():
    return _static_file(_BRAND_DIR, "apple-touch-icon.png", "public, max-age=86400")


@app.get("/badge-studio", include_in_schema=False)
@app.get("/badge-studio/", include_in_schema=False)
def badge_studio_index():
    return _static_file(_BADGE_DIR, "index.html", "no-store")


@app.get("/badge-studio/{name}", include_in_schema=False)
def badge_studio_asset(name: str):
    # The JS/CSS bundle is large; let the browser cache it but revalidate per build.
    return _static_file(_BADGE_DIR, name, "public, max-age=300")
