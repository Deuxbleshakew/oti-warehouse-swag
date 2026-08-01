"""
backend/config.py — settings shared across modules that would otherwise
each compute their own (and drift out of sync).

ASSETS_DIR used to be defined separately in both main.py (for the static
mount) and item_service.py (for where uploads get written) — harmless
locally since they computed the same default, but a real bug waiting to
happen in production: setting the ASSETS_DIR env var for a persistent
disk would only have updated one of the two, silently splitting "where
photos are served from" and "where photos get saved" across two
different directories.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Set this env var to a mounted persistent disk's path in production
# (e.g. Render's disk mount point). Most cloud platforms wipe local files
# on every redeploy or restart, silently deleting every uploaded photo —
# defaults to a path next to the repo for local dev, where that risk
# doesn't apply.
ASSETS_DIR = os.environ.get("ASSETS_DIR") or os.path.join(
    APP_DIR, "frontend", "assets")
os.makedirs(ASSETS_DIR, exist_ok=True)
