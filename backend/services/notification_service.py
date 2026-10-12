"""Optional SMS alerts for newly submitted orders.

SMS is enabled only when all Twilio environment variables are present. Nothing
is hardcoded, and an unavailable provider never causes order submission to
fail. The SQLAlchemy order is converted to a plain snapshot before the worker
thread starts, avoiding detached-session failures after the request closes.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def _sms_settings():
    values = {
        "sid": os.getenv("TWILIO_ACCOUNT_SID", "").strip(),
        "token": os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
        "from": os.getenv("TWILIO_FROM_NUMBER", "").strip(),
        "to": os.getenv("ORDER_ALERT_TO_NUMBER", "").strip(),
        "public_url": os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/"),
    }
    return values if all(values[k] for k in ("sid", "token", "from", "to")) else None


def _snapshot(order) -> dict:
    project = order.project
    lines = list(order.lines)
    address_values = (
        getattr(project, "shipping_address1", "") if project else "",
        getattr(project, "shipping_city", "") if project else "",
        getattr(project, "shipping_state", "") if project else "",
        getattr(project, "shipping_postal_code", "") if project else "",
    )
    return {
        "id": int(order.id),
        "requester": order.requester.full_name or order.requester.username,
        "project": project.name if project else "No project",
        "line_count": len(lines),
        "units": sum(int(line.qty_requested) for line in lines),
        "incomplete": (any(not (value or "").strip() for value in address_values)
                       or any(bool(line.qty_estimated) for line in lines)),
    }


def _send_sms(snapshot: dict) -> None:
    cfg = _sms_settings()
    if not cfg:
        return
    incomplete = " · INCOMPLETE" if snapshot["incomplete"] else ""
    link = (f" {cfg['public_url']}/?order={snapshot['id']}"
            if cfg["public_url"] else "")
    body = (
        f"New swag order #{snapshot['id']}: {snapshot['requester']} · "
        f"{snapshot['project']} · {snapshot['line_count']} items / "
        f"{snapshot['units']} units{incomplete}.{link}"
    )[:1500]
    payload = urlencode({"From": cfg["from"], "To": cfg["to"],
                         "Body": body}).encode("utf-8")
    req = Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{cfg['sid']}/Messages.json",
        data=payload,
        headers={
            "Authorization": "Basic " + base64.b64encode(
                f"{cfg['sid']}:{cfg['token']}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urlopen(req, timeout=12) as response:
        response.read()


def notify_new_order_async(order) -> None:
    """Snapshot now, then send in the background."""
    snapshot = _snapshot(order)

    def work():
        try:
            _send_sms(snapshot)
        except Exception as exc:  # provider/network failures must not reject order
            logger.warning("New-order SMS failed for order %s: %s",
                           snapshot["id"], exc)

    threading.Thread(target=work, daemon=True).start()
