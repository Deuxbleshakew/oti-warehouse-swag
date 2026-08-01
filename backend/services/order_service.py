"""
backend/services/order_service.py — order creation, approval, rejection.

This is where the core business rules live:
  - stock is never deducted on request, only on approval
  - approval never allows stock to go negative unless an admin explicitly
    opts in per-request (allow_negative)
  - every approval/rejection is a permanent Approval record, separate from
    the order's own mutable status field
  - every stock change gets its own InventoryTransaction row with a
    required reason
"""
from datetime import datetime, timezone
from typing import Optional, Dict

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from backend.models.models import (Order, OrderLine, Item, Approval,
                                    InventoryTransaction, User, Project)
from backend.services.audit_service import log_action
from backend.services.shipping_service import build_shipping_plan, ShippingPlanError


class OrderError(HTTPException):
    def __init__(self, detail: str, code: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(status_code=code, detail=detail)


def _unique_project_name(db: Session, requested_name: str) -> str:
    """Return a display-friendly name that satisfies Project.name uniqueness.

    One-time events can legitimately reuse a human name (for example,
    "Summer Seminar"). The database's original schema made names unique, so
    add a small numeric suffix rather than failing the whole order.
    """
    base = " ".join((requested_name or "").split()).strip()
    if not base:
        raise OrderError("A project/event name is required.")

    candidate = base[:200]
    n = 2
    while (db.query(Project.id)
           .filter(func.lower(Project.name) == candidate.lower()).first()):
        suffix = f" ({n})"
        candidate = base[:200 - len(suffix)] + suffix
        n += 1
    return candidate


def create_order(db: Session, *, requester: User, project_id: Optional[int],
                 notes: str, lines: list, project_data: Optional[dict] = None,
                 save_project: bool = False, source: str = "api") -> Order:
    if not lines:
        raise OrderError("An order needs at least one item.")
    if project_id is not None and project_data is not None:
        raise OrderError("Choose an existing project or create a new one, not both.")

    linked_project = None
    if project_id is not None:
        linked_project = db.query(Project).filter_by(id=project_id, active=True).first()
        if not linked_project:
            raise OrderError("The selected saved project/event no longer exists.")
    elif project_data is not None:
        clean = dict(project_data)
        clean["name"] = _unique_project_name(db, clean.get("name", ""))
        if not (clean.get("owner") or "").strip():
            clean["owner"] = requester.full_name or requester.username

        # Every one-time event gets a real ship-to address and an authoritative
        # shipping deadline. The browser previews the same calculation, but the
        # API repeats it so a modified/older client cannot save inconsistent
        # dates. Exact UPS ZIP quotes may override the map estimate by sending
        # ups_ground_days (1–6).
        required_shipping = {
            "shipping_address1": "Delivery street address",
            "shipping_city": "Delivery city",
            "shipping_state": "Delivery state",
            "shipping_postal_code": "Delivery ZIP code",
        }
        for field, label in required_shipping.items():
            clean[field] = (clean.get(field) or "").strip()
            if not clean[field]:
                raise OrderError(f"{label} is required.")
        clean["shipping_address2"] = (clean.get("shipping_address2") or "").strip()
        clean["location"] = (clean.get("location") or "").strip()
        try:
            plan = build_shipping_plan(
                event_date=clean.get("event_date", ""),
                shipping_state=clean.get("shipping_state", ""),
                ups_ground_days=clean.get("ups_ground_days"),
            )
        except ShippingPlanError as exc:
            raise OrderError(str(exc)) from exc
        clean.update(plan.as_dict())

        linked_project = Project(**clean, active=bool(save_project))
        db.add(linked_project)
        db.flush()
        log_action(
            db,
            user_id=requester.id,
            action="project.create",
            object_type="project",
            object_id=linked_project.id,
            new_value={**clean, "active": bool(save_project)},
            source=source,
        )

    order = Order(
        requester_user_id=requester.id,
        project_id=linked_project.id if linked_project else None,
        status="pending",
        notes=notes,
    )
    db.add(order)
    db.flush()   # assigns order.id without committing

    for ln in lines:
        item = db.query(Item).filter_by(id=ln.item_id, active=True).first()
        if not item:
            raise OrderError(f"Item {ln.item_id} not found or inactive.")
        if ln.qty <= 0:
            raise OrderError(f"Quantity for item {ln.item_id} must be positive.")
        db.add(OrderLine(order_id=order.id, item_id=item.id,
                         qty_requested=ln.qty))
        # NOTE: no stock change here — deliberately. Requesting is not
        # approving.

    log_action(db, user_id=requester.id, action="order.create",
              object_type="order", object_id=order.id,
              new_value={"lines": [{"item_id": l.item_id, "qty": l.qty}
                                    for l in lines],
                         "project_id": linked_project.id if linked_project else None,
                         "new_project": project_data is not None,
                         "save_project": bool(save_project),
                         "ship_by_date": (linked_project.ship_by_date
                                          if linked_project else ""),
                         "delivery_date": (linked_project.delivery_date
                                           if linked_project else "")},
              source=source)
    db.commit()
    db.refresh(order)
    return order


def approve_order(db: Session, *, order_id: int, approver: User,
                  reason: str, line_overrides: Optional[Dict[int, int]],
                  allow_negative: bool, source: str = "api") -> Order:
    order = (db.query(Order).options(joinedload(Order.lines))
            .filter_by(id=order_id).first())
    if not order:
        raise OrderError("Order not found.", status.HTTP_404_NOT_FOUND)
    if order.status != "pending":
        raise OrderError(f"Order is already {order.status}, not pending.")

    # allow_negative is a privileged escape hatch — only an admin may use
    # it, even though an 'approver' can approve orders normally.
    if allow_negative and not approver.has_role("admin"):
        raise OrderError("Only an admin can approve with allow_negative.",
                        status.HTTP_403_FORBIDDEN)

    line_overrides = line_overrides or {}
    old_status = order.status

    # ---- Pass 1: validate every line BEFORE mutating anything -----------
    planned = []   # (line, item, approved_qty)
    for line in order.lines:
        item = db.query(Item).filter_by(id=line.item_id).first()
        if not item:
            raise OrderError(f"Item {line.item_id} no longer exists.")
        approved_qty = line_overrides.get(line.item_id, line.qty_requested)
        if approved_qty < 0 or approved_qty > line.qty_requested:
            raise OrderError(
                f"Approved qty for item {item.code} must be between 0 "
                f"and the requested {line.qty_requested}.")
        resulting_stock = item.qty_on_hand - approved_qty
        if resulting_stock < 0 and not allow_negative:
            raise OrderError(
                f"Not enough stock for {item.code}: has {item.qty_on_hand}, "
                f"needs {approved_qty}. Restock first, reduce the approved "
                f"quantity, or reject this order.")
        planned.append((line, item, approved_qty))

    # ---- Pass 2: everything validated — now actually apply it ------------
    for line, item, approved_qty in planned:
        line.qty_approved = approved_qty
        if approved_qty > 0:
            item.qty_on_hand -= approved_qty
            db.add(InventoryTransaction(
                item_id=item.id, delta=-approved_qty,
                reason=f"Order #{order.id} approved", source=source,
                user_id=approver.id))

    order.status = "approved"
    db.add(Approval(order_id=order.id, approver_user_id=approver.id,
                    decision="approved", reason=reason))
    log_action(db, user_id=approver.id, action="order.approve",
              object_type="order", object_id=order.id,
              old_value={"status": old_status},
              new_value={"status": "approved",
                         "lines": [{"item_id": p[1].id, "qty": p[2]}
                                   for p in planned]},
              source=source)
    db.commit()
    db.refresh(order)
    return order


def reject_order(db: Session, *, order_id: int, approver: User, reason: str,
                 source: str = "api") -> Order:
    order = db.query(Order).filter_by(id=order_id).first()
    if not order:
        raise OrderError("Order not found.", status.HTTP_404_NOT_FOUND)
    if order.status != "pending":
        raise OrderError(f"Order is already {order.status}, not pending.")

    old_status = order.status
    order.status = "rejected"
    db.add(Approval(order_id=order.id, approver_user_id=approver.id,
                    decision="rejected", reason=reason))
    log_action(db, user_id=approver.id, action="order.reject",
              object_type="order", object_id=order.id,
              old_value={"status": old_status},
              new_value={"status": "rejected", "reason": reason},
              source=source)
    db.commit()
    db.refresh(order)
    return order


def to_order_out(order: Order):
    """Build the API-facing shape for one order, with its lines resolved
    to item code/name (the frontend shouldn't need a second round-trip
    just to show what was ordered)."""
    from backend.schemas.schemas import OrderOut, OrderLineOut, ProjectOut
    return OrderOut(
        id=order.id, status=order.status,
        requester=order.requester.username,
        project=order.project.name if order.project else None,
        project_id=order.project_id,
        project_details=(ProjectOut.model_validate(order.project)
                         if order.project else None),
        notes=order.notes,
        created_at=order.created_at, updated_at=order.updated_at,
        lines=[OrderLineOut(id=l.id, item_id=l.item_id,
                            item_code=l.item.code, item_name=l.item.name,
                            qty_requested=l.qty_requested,
                            qty_approved=l.qty_approved)
               for l in order.lines],
    )
