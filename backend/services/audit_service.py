"""
backend/services/audit_service.py — one place that writes audit_log rows,
so every service calls the same function instead of each hand-rolling its
own INSERT. Keeps the "who/when/action/object/old/new/source" shape
consistent everywhere.
"""
import json
from typing import Optional, Any

from sqlalchemy.orm import Session

from backend.models.models import AuditLog


def _summarize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)[:500]
    return str(value)[:500]


def log_action(db: Session, *, user_id: Optional[int], action: str,
              object_type: str, object_id="", old_value=None, new_value=None,
              source: str = "api"):
    """Write one audit_log row. Does NOT commit — callers batch this with
    their own transaction so the audit row and the actual change land or
    fail together, never one without the other."""
    row = AuditLog(
        user_id=user_id, action=action, object_type=object_type,
        object_id=str(object_id), old_value=_summarize(old_value),
        new_value=_summarize(new_value), source=source,
    )
    db.add(row)
    return row
