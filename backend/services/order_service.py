"""Order creation, editing, approval and fulfillment workflow."""
from __future__ import annotations

from datetime import datetime, timezone, date
from typing import Optional, Dict

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from backend.models.models import (
    Order, OrderLine, Item, Approval, InventoryTransaction, User, Project, Notification,
    OrderTracking, OrderProofPhoto, ProjectMember, NavAdjustmentTask,
)
from backend.schemas.schemas import OrderOut, OrderLineOut, ProjectOut
from backend.services.audit_service import log_action
from backend.services.notification_service import notify_new_order_async
from backend.services import access_service
from backend.services.shipping_service import (
    build_shipping_plan, ShippingPlanError, previous_business_day,
)


MAX_PROOF_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_PROOF_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class OrderError(HTTPException):
    def __init__(self, detail: str, code: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(status_code=code, detail=detail)


def _unique_project_name(db: Session, requested_name: str) -> str:
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


def _clean_project_fields(data: dict) -> dict:
    clean = dict(data)
    text_fields = (
        "name", "description", "purpose", "owner", "customer", "event_date",
        "location", "shipping_address1", "shipping_address2", "shipping_city",
        "shipping_state", "shipping_postal_code", "notes", "address_mode",
    )
    for field in text_fields:
        if field in clean and clean[field] is not None:
            clean[field] = str(clean[field]).strip()
    if clean.get("shipping_state"):
        clean["shipping_state"] = clean["shipping_state"].upper()
    if "address_mode" in clean:
        clean["address_mode"] = clean.get("address_mode") or "variable"
        if clean["address_mode"] not in {"fixed", "variable"}:
            raise OrderError("Address mode must be fixed or variable.")
    clean["shipping_service"] = "UPS Ground"
    clean.pop("ups_ground_days", None)  # always map-driven
    clean.pop("delivery_date", None)
    clean.pop("ship_by_date", None)
    return clean


def _calculate_project_dates(data: dict) -> dict:
    event_date = (data.get("event_date") or "").strip()
    state_code = (data.get("shipping_state") or "").strip().upper()
    data["delivery_date"] = ""
    data["ship_by_date"] = ""
    data["ups_ground_days"] = None
    if not event_date:
        return data
    try:
        event = date.fromisoformat(event_date)
    except ValueError as exc:
        raise OrderError("Event date must be a valid date.") from exc
    data["delivery_date"] = previous_business_day(event).isoformat()
    if state_code:
        try:
            plan = build_shipping_plan(event_date=event_date,
                                       shipping_state=state_code)
        except ShippingPlanError as exc:
            raise OrderError(str(exc)) from exc
        data.update(plan.as_dict())
    return data


def _project_dict(project: Project) -> dict:
    return {key: getattr(project, key) for key in (
        "name", "description", "purpose", "owner", "customer", "event_date",
        "delivery_date", "ship_by_date", "location", "shipping_address1",
        "shipping_address2", "shipping_city", "shipping_state",
        "shipping_postal_code", "shipping_service", "ups_ground_days",
        "attendees", "budget", "status", "notes", "active", "address_mode",
        "access_restricted",
    )}


def _copy_project_members(db: Session, source: Project, target: Project,
                          *, skip_user_id: int | None = None) -> None:
    """Copy a template's sharing list to its per-order snapshot."""
    for row in source.members:
        if row.user_id == skip_user_id:
            continue
        if row.user and row.user.deleted_at is None:
            db.add(ProjectMember(project_id=target.id, user_id=row.user_id,
                                 access_level=row.access_level))


def _ensure_private_project(db: Session, order: Order) -> Project:
    """Avoid editing a reusable project template shared by other orders."""
    if order.project is None:
        project = Project(
            name=_unique_project_name(db, f"Order {order.id} delivery"),
            owner=order.requester.full_name or order.requester.username,
            active=False, access_restricted=True,
        )
        db.add(project)
        db.flush()
        order.project_id = project.id
        order.project = project
        access_service.ensure_project_owner(db, project, order.requester)
        return project
    if not order.project.active:
        return order.project
    source = order.project
    data = _project_dict(source)
    data["name"] = _unique_project_name(db, f"{source.name} - Order {order.id}")
    data["active"] = False
    clone = Project(**data)
    db.add(clone)
    db.flush()
    _copy_project_members(db, source, clone, skip_user_id=order.requester.id)
    access_service.ensure_project_owner(db, clone, order.requester)
    order.project_id = clone.id
    order.project = clone
    return clone


def incomplete_reasons(order: Order) -> list[str]:
    reasons: list[str] = []
    project = order.project
    if project is None:
        reasons.append("Delivery address pending")
    else:
        if not (project.event_date or "").strip():
            reasons.append("Event date pending")
        required_address = (
            project.shipping_address1, project.shipping_city,
            project.shipping_state, project.shipping_postal_code,
        )
        if any(not (value or "").strip() for value in required_address):
            reasons.append("Delivery address pending")
        elif not project.ship_by_date or project.ups_ground_days is None:
            reasons.append("UPS Ground timing pending")
    if any(bool(line.qty_estimated) for line in order.lines):
        reasons.append("Estimated quantities need confirmation")
    return reasons


def _load_order(db: Session, order_id: int, *, include_deleted: bool = False) -> Order:
    query = db.query(Order).options(
        joinedload(Order.lines).joinedload(OrderLine.item),
        joinedload(Order.project), joinedload(Order.requester),
        joinedload(Order.tracking_numbers), joinedload(Order.proof_photos),
    ).filter(Order.id == order_id)
    if not include_deleted:
        query = query.filter(Order.deleted_at.is_(None))
    order = query.first()
    if not order:
        raise OrderError("Order not found.", status.HTTP_404_NOT_FOUND)
    return order


def create_order(db: Session, *, requester: User, project_id: Optional[int],
                 notes: str, lines: list, project_data: Optional[dict] = None,
                 save_project: bool = False, source: str = "api") -> Order:
    if not lines:
        raise OrderError("An order needs at least one item.")
    linked_project = None
    if project_id is not None:
        template = db.query(Project).filter_by(id=project_id, active=True).first()
        if not template or not access_service.can_view_project(db, requester, template):
            raise OrderError("The selected saved project/event is not available.")
        if project_data is None:
            linked_project = template
        else:
            # Variable-address templates become a private per-order snapshot.
            clean = _project_dict(template)
            clean.update(_clean_project_fields(project_data))
            clean["name"] = _unique_project_name(
                db, f"{template.name} - {requester.username}")
            clean["active"] = False
            clean["address_mode"] = template.address_mode or "variable"
            clean["access_restricted"] = True
            if not clean.get("owner"):
                clean["owner"] = requester.full_name or requester.username
            _calculate_project_dates(clean)
            linked_project = Project(**clean)
            db.add(linked_project)
            db.flush()
            _copy_project_members(db, template, linked_project, skip_user_id=requester.id)
            access_service.ensure_project_owner(db, linked_project, requester)
    elif project_data is not None:
        clean = _clean_project_fields(project_data)
        clean["name"] = _unique_project_name(db, clean.get("name", ""))
        if not clean.get("event_date"):
            raise OrderError("An event date is required.")
        if not clean.get("owner"):
            clean["owner"] = requester.full_name or requester.username
        clean["address_mode"] = clean.get("address_mode") or "variable"
        clean["access_restricted"] = True
        _calculate_project_dates(clean)
        clean["active"] = bool(save_project)
        linked_project = Project(**clean)
        db.add(linked_project)
        db.flush()
        access_service.ensure_project_owner(db, linked_project, requester)
        log_action(db, user_id=requester.id, action="project.create",
                   object_type="project", object_id=linked_project.id,
                   new_value={**clean, "active": bool(save_project)},
                   source=source)

    order = Order(requester_user_id=requester.id,
                  project_id=linked_project.id if linked_project else None,
                  status="pending", notes=(notes or "").strip())
    db.add(order)
    db.flush()

    seen = set()
    for ln in lines:
        if ln.item_id in seen:
            raise OrderError("Each item can appear only once in an order.")
        seen.add(ln.item_id)
        item = db.query(Item).filter_by(id=ln.item_id, active=True).first()
        if not item or not access_service.can_view_item(requester, item):
            raise OrderError(f"Item {ln.item_id} is not available to this user.")
        if not bool(getattr(item, "inventory_counted", True)):
            raise OrderError(f"{item.code} has not been counted yet and cannot be ordered.")
        if ln.qty <= 0:
            raise OrderError(f"Quantity for item {ln.item_id} must be positive.")
        db.add(OrderLine(order_id=order.id, item_id=item.id,
                         qty_requested=ln.qty,
                         qty_estimated=bool(getattr(ln, "estimated", False)),
                         item_code_snapshot=item.code,
                         item_name_snapshot=item.name,
                         item_location_snapshot=item.location))

    log_action(db, user_id=requester.id, action="order.create",
               object_type="order", object_id=order.id,
               new_value={
                   "lines": [{"item_id": l.item_id, "qty": l.qty,
                              "estimated": bool(getattr(l, "estimated", False))}
                             for l in lines],
                   "project_id": linked_project.id if linked_project else None,
                   "save_project": bool(save_project),
               }, source=source)
    _notify_admins(db,"new_order",f"New order #{order.id}",f"{requester.full_name or requester.username} submitted an order.",order.id)
    db.commit()
    created = _load_order(db, order.id)
    notify_new_order_async(created)
    return created


def _apply_project_edit(db: Session, order: Order, project_data: Optional[dict]):
    if project_data is None:
        return
    project = _ensure_private_project(db, order)
    old = _project_dict(project)
    clean = _clean_project_fields(project_data)
    if "name" in clean and not clean["name"]:
        raise OrderError("A project/event name is required.")
    for key, value in clean.items():
        if hasattr(project, key) and value is not None:
            setattr(project, key, value)
    calculated = _calculate_project_dates(_project_dict(project))
    for key in ("event_date", "delivery_date", "ship_by_date",
                "shipping_state", "shipping_service", "ups_ground_days"):
        setattr(project, key, calculated.get(key))
    log_action(db, user_id=None, action="project.update_from_order",
               object_type="project", object_id=project.id,
               old_value=old, new_value=_project_dict(project), source="api")


def _validate_edit_lines(db: Session, lines: list) -> list[tuple[Item, int, bool]]:
    result = []
    seen = set()
    for entry in lines:
        if entry.item_id in seen:
            raise OrderError("Each item can appear only once in an order.")
        seen.add(entry.item_id)
        item = db.query(Item).filter_by(id=entry.item_id).first()
        if not item:
            raise OrderError(f"Item {entry.item_id} no longer exists.")
        if entry.qty <= 0:
            raise OrderError("Every order quantity must be greater than zero.")
        result.append((item, int(entry.qty), bool(entry.estimated)))
    if not result:
        raise OrderError("An order needs at least one item.")
    return result


def edit_order(db: Session, *, order_id: int, actor: User,
               notes: Optional[str], project_data: Optional[dict],
               lines: Optional[list], owner_only_pending: bool,
               source: str = "api") -> Order:
    order = _load_order(db, order_id)
    if owner_only_pending:
        may_edit = (order.requester_user_id == actor.id or
                    access_service.can_edit_project_order(db, actor, order.project))
        if not may_edit:
            raise OrderError("You do not have edit access to this order.", status.HTTP_403_FORBIDDEN)
        if order.status != "pending":
            raise OrderError("This order can no longer be edited because it has already been reviewed.")

    old_snapshot = {
        "notes": order.notes,
        "status": order.status,
        "lines": [{"item_id": l.item_id, "qty": l.qty_requested,
                   "approved": l.qty_approved, "estimated": l.qty_estimated}
                  for l in order.lines],
        "project": _project_dict(order.project) if order.project else None,
    }
    if notes is not None:
        order.notes = notes.strip()
    if project_data is not None:
        _apply_project_edit(db, order, project_data)

    if lines is not None:
        planned = _validate_edit_lines(db, lines)
        if owner_only_pending:
            existing_qty = {line.item_id: int(line.qty_requested)
                            for line in order.lines}
            hidden_changes = [item.code for item, qty, _estimated in planned
                              if (not access_service.can_view_item(actor, item)
                                  and (item.id not in existing_qty
                                       or qty > existing_qty[item.id]))]
            if hidden_changes:
                raise OrderError(
                    "You cannot add or increase item(s) that are no longer in your catalog: "
                    + ", ".join(hidden_changes), status.HTTP_403_FORBIDDEN)
        stock_affecting = order.status in {"approved", "picking", "fulfilled"}
        existing = {line.item_id: line for line in order.lines}
        desired_ids = {item.id for item, _, _ in planned}

        # Validate stock changes before mutating anything.
        if stock_affecting:
            for item, qty, _estimated in planned:
                old_approved = (existing[item.id].qty_approved or 0) if item.id in existing else 0
                extra_needed = qty - old_approved
                if extra_needed > item.qty_on_hand:
                    raise OrderError(
                        f"Not enough stock for {item.code}: editing needs {extra_needed} more, "
                        f"but only {item.qty_on_hand} is available.")

        for item_id, line in list(existing.items()):
            if item_id not in desired_ids:
                if stock_affecting and (line.qty_approved or 0):
                    returned = line.qty_approved or 0
                    line.item.qty_on_hand += returned
                    db.add(InventoryTransaction(
                        item_id=item_id, delta=returned,
                        reason=f"Order #{order.id} edit returned removed line",
                        source=source, user_id=actor.id,
                        item_code_snapshot=line.item_code_snapshot or line.item.code,
                        item_name_snapshot=line.item_name_snapshot or line.item.name))
                db.delete(line)

        for item, qty, estimated in planned:
            line = existing.get(item.id)
            if line is None:
                line = OrderLine(order_id=order.id, item_id=item.id,
                                 item_code_snapshot=item.code,
                                 item_name_snapshot=item.name,
                                 item_location_snapshot=item.location)
                db.add(line)
            if stock_affecting:
                old_approved = line.qty_approved or 0
                delta_from_stock = qty - old_approved
                if delta_from_stock:
                    item.qty_on_hand -= delta_from_stock
                    db.add(InventoryTransaction(
                        item_id=item.id, delta=-delta_from_stock,
                        reason=f"Order #{order.id} quantity edited",
                        source=source, user_id=actor.id,
                        item_code_snapshot=item.code,
                        item_name_snapshot=item.name))
                line.qty_approved = qty
                line.qty_estimated = False
            else:
                line.qty_estimated = estimated
                if order.status == "rejected":
                    line.qty_approved = None
            line.qty_requested = qty
            line.item_code_snapshot = line.item_code_snapshot or item.code
            line.item_name_snapshot = line.item_name_snapshot or item.name
            line.item_location_snapshot = line.item_location_snapshot or item.location

    order.updated_at = datetime.now(timezone.utc)
    db.flush()
    new_snapshot = {
        "notes": order.notes,
        "status": order.status,
        "lines": [{"item_id": l.item_id, "qty": l.qty_requested,
                   "approved": l.qty_approved, "estimated": l.qty_estimated}
                  for l in order.lines],
        "project": _project_dict(order.project) if order.project else None,
    }
    log_action(db, user_id=actor.id, action="order.edit",
               object_type="order", object_id=order.id,
               old_value=old_snapshot, new_value=new_snapshot, source=source)
    db.commit()
    return _load_order(db, order.id)


def _notify(db, user_id:int, kind:str, title:str, message:str, object_type="order", object_id=None):
    db.add(Notification(user_id=user_id,kind=kind,title=title,message=message,object_type=object_type,object_id=object_id))

def _notify_admins(db, kind:str,title:str,message:str,object_id=None):
    for u in db.query(User).all():
        if u.active and u.deleted_at is None and (u.has_role("admin") or u.has_role("approver")):
            _notify(db,u.id,kind,title,message,"order",object_id)

def approve_order(db: Session, *, order_id: int, approver: User,
                  reason: str, line_overrides: Optional[Dict[int, int]],
                  allow_negative: bool, source: str = "api") -> Order:
    order = _load_order(db, order_id)
    if order.status != "pending":
        raise OrderError(f"Order is already {order.status}, not pending.")
    missing = incomplete_reasons(order)
    if missing:
        raise OrderError("Complete this order before approval: " + "; ".join(missing) + ".")
    if allow_negative and not approver.has_role("admin"):
        raise OrderError("Only an admin can approve with allow_negative.",
                         status.HTTP_403_FORBIDDEN)

    line_overrides = line_overrides or {}
    planned = []
    for line in order.lines:
        item = line.item
        approved_qty = line_overrides.get(line.item_id, line.qty_requested)
        if not bool(getattr(item, "inventory_counted", True)):
            raise OrderError(f"{item.code} has not been counted yet and cannot be approved.")
        if approved_qty < 0 or approved_qty > line.qty_requested:
            raise OrderError(
                f"Approved qty for item {item.code} must be between 0 and "
                f"the requested {line.qty_requested}.")
        if item.qty_on_hand - approved_qty < 0 and not allow_negative:
            raise OrderError(
                f"Not enough stock for {item.code}: has {item.qty_on_hand}, "
                f"needs {approved_qty}.")
        planned.append((line, item, approved_qty))

    for line, item, approved_qty in planned:
        line.qty_approved = approved_qty
        if approved_qty:
            item.qty_on_hand -= approved_qty
            db.add(InventoryTransaction(
                item_id=item.id, delta=-approved_qty,
                reason=f"Order #{order.id} approved", source=source,
                user_id=approver.id, item_code_snapshot=item.code,
                item_name_snapshot=item.name))
    order.status = "approved"
    _notify(db,order.requester_user_id,"approved",f"Order #{order.id} approved","Your order was approved and is waiting to be picked.",object_id=order.id)
    db.add(Approval(order_id=order.id, approver_user_id=approver.id,
                    decision="approved", reason=reason))
    log_action(db, user_id=approver.id, action="order.approve",
               object_type="order", object_id=order.id,
               old_value={"status": "pending"},
               new_value={"status": "approved",
                          "lines": [{"item_id": p[1].id, "qty": p[2]}
                                    for p in planned]}, source=source)
    db.commit()
    return _load_order(db, order.id)


def reject_order(db: Session, *, order_id: int, approver: User, reason: str,
                 source: str = "api") -> Order:
    order = _load_order(db, order_id)
    if order.status != "pending":
        raise OrderError(f"Order is already {order.status}, not pending.")
    order.status = "rejected"
    _notify(db,order.requester_user_id,"rejected",f"Order #{order.id} rejected",reason,object_id=order.id)
    db.add(Approval(order_id=order.id, approver_user_id=approver.id,
                    decision="rejected", reason=reason))
    log_action(db, user_id=approver.id, action="order.reject",
               object_type="order", object_id=order.id,
               old_value={"status": "pending"},
               new_value={"status": "rejected", "reason": reason},
               source=source)
    db.commit()
    return _load_order(db, order.id)


def start_picking(db: Session, *, order_id: int, actor: User,
                  source: str = "admin_app") -> Order:
    order = _load_order(db, order_id)
    if order.status != "approved":
        raise OrderError("Only an approved order can be marked as being picked.")
    order.status = "picking"
    _notify(db,order.requester_user_id,"picking",f"Order #{order.id} is being picked","The warehouse started gathering your items.",object_id=order.id)
    order.picking_started_at = datetime.now(timezone.utc)
    log_action(db, user_id=actor.id, action="order.pick_start",
               object_type="order", object_id=order.id,
               old_value={"status": "approved"},
               new_value={"status": "picking"}, source=source)
    db.commit()
    return _load_order(db, order.id)


def delete_order(db: Session, *, order_id: int, actor: User,
                 source: str = "admin_app") -> None:
    """Hide an order everywhere without changing inventory already consumed."""
    order = _load_order(db, order_id)
    snapshot = {
        "status": order.status,
        "requester": order.requester.full_name or order.requester.username,
        "project": order.project.name if order.project else None,
        "tracking": [row.tracking_number for row in order.tracking_numbers],
        "photo_count": len(order.proof_photos),
        "lines": [{"item_id": line.item_id,
                   "code": line.item_code_snapshot or (line.item.deleted_code if line.item else "") or (line.item.code if line.item else str(line.item_id)),
                   "qty_requested": line.qty_requested,
                   "qty_approved": line.qty_approved}
                  for line in order.lines],
    }
    order.tracking_numbers.clear()
    order.proof_photos.clear()
    # Keep the stock ledger, but make its orphaned source obvious after the
    # order itself disappears from the order-history screens.
    prefix = f"Order #{order.id}"
    for tx in (db.query(InventoryTransaction)
               .filter(InventoryTransaction.reason.like(prefix + "%")).all()):
        tx.reason = "Deleted " + tx.reason
    now = datetime.now(timezone.utc)
    order.deleted_at = now
    order.updated_at = now
    log_action(db, user_id=actor.id, action="order.delete",
               object_type="order", object_id=order.id, old_value=snapshot,
               new_value={"deleted": True, "inventory_unchanged": True},
               source=source)
    db.commit()


def _create_nav_adjustment_tasks(db: Session, order: Order) -> int:
    """Create one idempotent manual NAV posting task per tracked fulfilled line."""
    created = 0
    fulfilled_at = order.fulfilled_at or datetime.now(timezone.utc)
    project_name = order.project.name if order.project else ""
    for line in order.lines:
        item = line.item
        if not item or not bool(getattr(item, "nav_tracked", False)):
            continue
        quantity = line.qty_approved if line.qty_approved is not None else line.qty_requested
        if not quantity or quantity <= 0:
            continue
        exists = (db.query(NavAdjustmentTask.id)
                  .filter_by(order_id=order.id, order_line_id=line.id).first())
        if exists:
            continue
        db.add(NavAdjustmentTask(
            order_id=order.id, order_line_id=line.id, item_id=item.id,
            project_snapshot=project_name or "",
            item_code_snapshot=(line.item_code_snapshot or item.deleted_code or item.code or ""),
            item_name_snapshot=(line.item_name_snapshot or item.deleted_name or item.name or ""),
            nav_item_number=(getattr(item, "nav_item_number", "") or ""),
            quantity_shipped=int(quantity), status="pending", notes="",
            fulfilled_at=fulfilled_at,
        ))
        created += 1
    return created


def fulfill_order(db: Session, *, order_id: int, actor: User,
                  tracking_numbers: list[str], photos: list[tuple[str, str, bytes]],
                  source: str = "admin_app") -> Order:
    order = _load_order(db, order_id)
    if order.status != "picking":
        raise OrderError("Start picking this order before marking it done.")
    tracking = [" ".join((number or "").split()) for number in tracking_numbers]
    tracking = [number for number in tracking if number]
    if not tracking:
        raise OrderError("At least one tracking number is required.")
    if not photos:
        raise OrderError("At least one completion photo is required.")
    for filename, content_type, content in photos:
        if content_type not in ALLOWED_PROOF_TYPES:
            raise OrderError("Proof photos must be JPG, PNG, GIF, or WebP.")
        if not content or len(content) > MAX_PROOF_IMAGE_BYTES:
            raise OrderError("Each proof photo must be between 1 byte and 10 MB.")

    order.tracking_numbers.clear()
    order.proof_photos.clear()
    db.flush()
    for number in tracking:
        db.add(OrderTracking(order_id=order.id, tracking_number=number))
    for filename, content_type, content in photos:
        db.add(OrderProofPhoto(order_id=order.id,
                               filename=(filename or "proof.jpg")[:255],
                               content_type=content_type, content=content))
    order.status = "fulfilled"
    _notify(db,order.requester_user_id,"fulfilled",f"Order #{order.id} completed","Tracking and shipment proof are now available.",object_id=order.id)
    order.fulfilled_at = datetime.now(timezone.utc)
    nav_task_count = _create_nav_adjustment_tasks(db, order)
    log_action(db, user_id=actor.id, action="order.fulfill",
               object_type="order", object_id=order.id,
               old_value={"status": "picking"},
               new_value={"status": "fulfilled", "tracking": tracking,
                          "photo_count": len(photos), "nav_adjustment_tasks": nav_task_count}, source=source)
    db.commit()
    return _load_order(db, order.id)


def to_order_out(order: Order, viewer: User | None = None, db: Session | None = None) -> OrderOut:
    project_out = ProjectOut.model_validate(order.project) if order.project else None
    project_label = order.project.name if order.project else None
    if order.project and getattr(order.project, "deleted_at", None):
        project_label = f"{order.project.deleted_name or order.project.name} [Deleted Project/Event]"
        project_out = project_out.model_copy(update={"name": project_label, "deleted": True, "active": False})
    reasons = incomplete_reasons(order)
    return OrderOut(
        id=order.id, status=order.status,
        requester=order.requester.full_name or order.requester.username,
        project=project_label,
        project_id=order.project_id, project_details=project_out,
        notes=order.notes or "", created_at=order.created_at,
        updated_at=order.updated_at,
        picking_started_at=order.picking_started_at,
        fulfilled_at=order.fulfilled_at,
        incomplete=bool(reasons), incomplete_reasons=reasons,
        can_self_edit=(order.status == "pending" and (
            viewer is None or order.requester_user_id == viewer.id or
            (db is not None and access_service.can_edit_project_order(db, viewer, order.project))
        )),
        tracking_numbers=[row.tracking_number for row in order.tracking_numbers],
        proof_photo_ids=[row.id for row in order.proof_photos],
        deleted=order.deleted_at is not None,
        lines=[OrderLineOut(
            id=line.id, item_id=line.item_id,
            item_code=(line.item_code_snapshot or
                       (line.item.deleted_code if line.item else "") or
                       (line.item.code if line.item else str(line.item_id))),
            item_name=(line.item_name_snapshot or
                       (line.item.deleted_name if line.item else "") or
                       (line.item.name if line.item else "Deleted item")),
            qty_requested=line.qty_requested,
            qty_estimated=bool(line.qty_estimated),
            qty_approved=line.qty_approved,
            item_location=(line.item_location_snapshot or
                           (line.item.location if line.item else "")),
            item_image_id=(line.item.images[0].id if line.item and line.item.images else None),
        ) for line in order.lines],
    )
