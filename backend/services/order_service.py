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

from backend.models.models import (Order, OrderLine, Item, Approval,
                                    InventoryTransaction, User)
from backend.services.audit_service import log_action


class OrderError(HTTPException):
    def __init__(self, detail: str, code: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(status_code=code, detail=detail)


def create_order(db: Session, *, requester: User, project_id: Optional[int],
                 notes: str, lines: list, source: str = "api") -> Order:
    if not lines:
        raise OrderError("An order needs at least one item.")

    order = Order(requester_user_id=requester.id, project_id=project_id,
                  status="pending", notes=notes)
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
                                    for l in lines], "project_id": project_id},
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
    from backend.schemas.schemas import OrderOut, OrderLineOut
    return OrderOut(
        id=order.id, status=order.status,
        requester=order.requester.username,
        project=order.project.name if order.project else None,
        project_id=order.project_id, notes=order.notes,
        created_at=order.created_at, updated_at=order.updated_at,
        lines=[OrderLineOut(id=l.id, item_id=l.item_id,
                            item_code=l.item.code, item_name=l.item.name,
                            qty_requested=l.qty_requested,
                            qty_approved=l.qty_approved)
               for l in order.lines],
    )
