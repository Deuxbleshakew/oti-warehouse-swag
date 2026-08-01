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

from backend.api import auth, catalog, orders, admin
from backend.config import ASSETS_DIR as _ASSETS_DIR

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

app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")

app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(orders.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    """Unauthenticated — just confirms the service is up. Useful for the
    admin app / a load balancer / a quick curl to check it's alive."""
    return {"status": "ok"}
