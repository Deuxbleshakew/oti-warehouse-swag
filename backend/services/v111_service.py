"""v1.11 helpers shared by the catalog, order and admin routers.

Kept in one small module so the new behaviour (pending-order quantities,
substitutes, item review, kit review, part-number suggestions) has a single
definition the website, the admin app and the reports all agree on.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.models import (
    Item, Kit, KitComponent, Notification, Order, OrderLine, User,
)


def pending_qty_map(db: Session, item_ids: list[int] | None = None) -> dict[int, int]:
    """Quantity requested by still-pending (not yet approved) orders.

    Approval already deducts stock from qty_on_hand, so approved/picking
    orders are reflected there. Pending orders are the gap: several people
    can request the same last units. This map lets the catalog show
    "free to order" = on hand - pending requests.
    """
    q = (db.query(OrderLine.item_id, func.coalesce(func.sum(OrderLine.qty_requested), 0))
         .join(Order, Order.id == OrderLine.order_id)
         .filter(Order.status == "pending", Order.deleted_at.is_(None)))
    if item_ids is not None:
        if not item_ids:
            return {}
        q = q.filter(OrderLine.item_id.in_(item_ids))
    return {int(i): int(n or 0) for i, n in q.group_by(OrderLine.item_id).all()}


def substitute_map(db: Session, items: list[Item]) -> dict[int, Item]:
    ids = {int(i.substitute_item_id) for i in items if getattr(i, "substitute_item_id", None)}
    if not ids:
        return {}
    rows = db.query(Item).filter(Item.id.in_(ids), Item.deleted_at.is_(None),
                                 Item.active.is_(True)).all()
    by_id = {r.id: r for r in rows if (r.review_status or "live") == "live"}
    return {i.id: by_id[i.substitute_item_id] for i in items
            if getattr(i, "substitute_item_id", None) in by_id}


def item_out_kwargs(db: Session, items: list[Item]) -> tuple[dict[int, int], dict[int, Item]]:
    ids = [i.id for i in items]
    return pending_qty_map(db, ids), substitute_map(db, items)


def notify_admins(db: Session, kind: str, title: str, message: str,
                  object_type: str = "item", object_id: int | None = None) -> None:
    for u in db.query(User).filter(User.active.is_(True), User.deleted_at.is_(None)).all():
        if u.has_role("admin"):
            db.add(Notification(user_id=u.id, kind=kind, title=title[:160],
                                message=(message or "")[:500], object_type=object_type,
                                object_id=object_id))


def notify_user(db: Session, user_id: int | None, kind: str, title: str, message: str,
                object_type: str = "item", object_id: int | None = None) -> None:
    if not user_id:
        return
    db.add(Notification(user_id=user_id, kind=kind, title=title[:160],
                        message=(message or "")[:500], object_type=object_type,
                        object_id=object_id))


def refresh_kit_review(db: Session, kit: Kit) -> str:
    """A kit is live only when every component item is live."""
    pending = any((c.item is None) or (c.item.review_status or "live") != "live"
                  for c in kit.components)
    kit.review_status = "pending_review" if pending else "live"
    return kit.review_status


def refresh_kits_for_item(db: Session, item_id: int) -> list[Kit]:
    kits = (db.query(Kit).join(KitComponent, KitComponent.kit_id == Kit.id)
            .filter(KitComponent.item_id == item_id).all())
    changed = []
    for kit in kits:
        before = kit.review_status or "live"
        after = refresh_kit_review(db, kit)
        if before != after:
            changed.append(kit)
    return changed


_CODE_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")


def suggest_code(db: Session, prefix: str = "LIT") -> str:
    """Next free part number like LIT-001, LIT-002 ... for the given prefix."""
    prefix = re.sub(r"[^A-Za-z0-9-]", "", (prefix or "LIT").upper()).strip("-") or "LIT"
    base = prefix + "-"
    highest = 0
    for (code,) in db.query(Item.code).filter(Item.code.like(base + "%")).all():
        tail = (code or "")[len(base):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    candidate = highest + 1
    while db.query(Item.id).filter(Item.code == f"{base}{candidate:03d}").first():
        candidate += 1
    return f"{base}{candidate:03d}"


def utcnow():
    return datetime.now(timezone.utc)
