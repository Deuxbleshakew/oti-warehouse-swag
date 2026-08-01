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
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Optional local compatibility/cache directory for item photos. The current
# build stores authoritative image bytes in the database, so temporary cloud
# files can disappear without losing newly uploaded photos. This directory is
# still used to read and backfill images created by older builds.
ASSETS_DIR = os.environ.get("ASSETS_DIR") or os.path.join(
    APP_DIR, "frontend", "assets")
os.makedirs(ASSETS_DIR, exist_ok=True)


def normalize_database_url(url: str) -> str:
    """Fixes up a database URL for two common gotchas with managed Postgres
    (Render, Heroku, etc.):

    1. They hand out "postgres://" URLs — SQLAlchemy 1.4+ only accepts the
       explicit "postgresql://" scheme.
    2. External connections require SSL. Without requesting it explicitly,
       the failure isn't always a clear "SSL required" message — psycopg2
       can instead report the opaque "server closed the connection
       unexpectedly", which looks like a network or credentials problem
       and sends you troubleshooting the wrong thing. Adding
       sslmode=require up front avoids that entirely. (Heroku's own docs
       recommend doing exactly this in code rather than editing the URL
       by hand, since automated credential rotation can silently
       overwrite a manual edit.)

    A no-op for sqlite:// URLs and for postgres URLs that already specify
    sslmode.
    """
    if not url.startswith(("postgres://", "postgresql://")):
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    parts = urlsplit(url)
    if parts.hostname in (None, "localhost", "127.0.0.1"):
        return urlunsplit(parts)   # local dev Postgres rarely has SSL set up

    query = dict(parse_qsl(parts.query))
    query.setdefault("sslmode", "require")
    return urlunsplit(parts._replace(query=urlencode(query)))
