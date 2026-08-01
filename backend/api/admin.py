"""
backend/api/admin.py — everything gated to approver/admin roles:
reviewing and deciding on orders, managing items and stock, managing
users, and reading the audit log. Every route here declares its own
required role via require_role(...) — there is no "admin panel" that's
merely hidden in the UI; the backend refuses the request outright.
"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy.orm import Session, joinedload

from backend.db.session import get_db
from backend.schemas.schemas import (
    OrderOut, ApproveRequest, RejectRequest, OrdersUpdateResponse,
    ItemOut, ItemCreate, ItemUpdate, InventoryAdjustRequest,
    UserOut, UserCreate, UserUpdate, AuditLogOut,
)
from backend.models.models import Order, Item, User, AuditLog
from backend.auth.dependencies import require_role
from backend.services import order_service, item_service, user_service

router = APIRouter(prefix="/admin", tags=["admin"])

POLL_INTERVAL_SECONDS = 1.0
POLL_MAX_WAIT_SECONDS = 25.0


# ---- Orders / approvals -----------------------------------------------------
@router.get("/orders/pending", response_model=list[OrderOut])
def pending_orders(db: Session = Depends(get_db),
                   _u: User = Depends(require_role("admin", "approver"))):
    orders = (db.query(Order).options(joinedload(Order.lines))
            .filter_by(status="pending").order_by(Order.created_at).all())
    return [order_service.to_order_out(o) for o in orders]


@router.get("/orders/updates", response_model=OrdersUpdateResponse)
async def poll_pending_orders(
    since: datetime | None = Query(None),
    db: Session = Depends(get_db),
    _u: User = Depends(require_role("admin", "approver")),
):
    """Same long-poll pattern as /orders/updates, scoped to ALL pending
    orders instead of one user's own — this is what the admin app polls
    so new team requests appear without a manual refresh."""
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    async def _check():
        q = db.query(Order).options(joinedload(Order.lines)).filter_by(
            status="pending")
        if since is not None:
            q = q.filter(Order.updated_at > since)
        return q.order_by(Order.updated_at.desc()).all()

    if since is None:
        orders = await _check()
        return OrdersUpdateResponse(server_time=datetime.now(timezone.utc),
                                    orders=[order_service.to_order_out(o)
                                            for o in orders])
    waited = 0.0
    while waited < POLL_MAX_WAIT_SECONDS:
        orders = await _check()
        if orders:
            return OrdersUpdateResponse(
                server_time=datetime.now(timezone.utc),
                orders=[order_service.to_order_out(o) for o in orders])
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
    return OrdersUpdateResponse(server_time=datetime.now(timezone.utc), orders=[])


@router.post("/orders/{order_id}/approve", response_model=OrderOut)
def approve(order_id: int, body: ApproveRequest, db: Session = Depends(get_db),
           user: User = Depends(require_role("admin", "approver"))):
    order = order_service.approve_order(
        db, order_id=order_id, approver=user, reason=body.reason,
        line_overrides=body.line_overrides, allow_negative=body.allow_negative,
        source="admin_app")
    return order_service.to_order_out(order)


@router.post("/orders/{order_id}/reject", response_model=OrderOut)
def reject(order_id: int, body: RejectRequest, db: Session = Depends(get_db),
          user: User = Depends(require_role("admin", "approver"))):
    order = order_service.reject_order(
        db, order_id=order_id, approver=user, reason=body.reason,
        source="admin_app")
    return order_service.to_order_out(order)


# ---- Items / inventory (admin only) ------------------------------------------
@router.get("/items", response_model=list[ItemOut])
def list_all_items(db: Session = Depends(get_db),
                   _u: User = Depends(require_role("admin"))):
    items = db.query(Item).order_by(Item.name).all()
    return [ItemOut.from_orm_item(i) for i in items]


@router.post("/items", response_model=ItemOut, status_code=201)
def create_item(body: ItemCreate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    item = item_service.create_item(db, data=body.model_dump(), actor=user,
                                    source="admin_app")
    return ItemOut.from_orm_item(item)


@router.put("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: int, body: ItemUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    item = item_service.update_item(db, item_id=item_id, data=data,
                                    actor=user, source="admin_app")
    return ItemOut.from_orm_item(item)


@router.post("/inventory/adjust", response_model=ItemOut)
def adjust_inventory(body: InventoryAdjustRequest, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    item = item_service.adjust_inventory(
        db, item_id=body.item_id, delta=body.delta, reason=body.reason,
        allow_negative=body.allow_negative, actor=user, source="admin_app")
    return ItemOut.from_orm_item(item)


# ---- Item photos (admin only) --------------------------------------------------
@router.post("/items/{item_id}/images", response_model=ItemOut, status_code=201)
async def upload_item_image(item_id: int, file: UploadFile = File(...),
                            db: Session = Depends(get_db),
                            user: User = Depends(require_role("admin"))):
    content = await file.read()
    item_service.add_item_image(db, item_id=item_id,
                                filename=file.filename or "",
                                content=content, actor=user,
                                source="admin_app")
    item = db.query(Item).filter_by(id=item_id).first()
    return ItemOut.from_orm_item(item)


@router.delete("/items/{item_id}/images/{image_id}", response_model=ItemOut)
def delete_item_image(item_id: int, image_id: int,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_role("admin"))):
    item_service.delete_item_image(db, item_id=item_id, image_id=image_id,
                                   actor=user, source="admin_app")
    item = db.query(Item).filter_by(id=item_id).first()
    return ItemOut.from_orm_item(item)


# ---- Users (admin only) -------------------------------------------------------
@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db),
              _u: User = Depends(require_role("admin"))):
    return [UserOut.from_orm_user(u) for u in db.query(User).order_by(User.username).all()]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    new_user = user_service.create_user(
        db, username=body.username, full_name=body.full_name,
        password=body.password, role_names=body.roles, actor=user,
        source="admin_app")
    return UserOut.from_orm_user(new_user)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    updated = user_service.update_user(
        db, user_id=user_id, full_name=body.full_name, active=body.active,
        role_names=body.roles, password=body.password, actor=user,
        source="admin_app")
    return UserOut.from_orm_user(updated)


# ---- Audit log (admin only) ---------------------------------------------------
@router.get("/audit", response_model=list[AuditLogOut])
def audit_log(limit: int = Query(200, le=1000), db: Session = Depends(get_db),
             _u: User = Depends(require_role("admin"))):
    rows = (db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all())
    return [AuditLogOut(id=r.id, user=r.user.username if r.user else None,
                        action=r.action, object_type=r.object_type,
                        object_id=r.object_id, old_value=r.old_value,
                        new_value=r.new_value, source=r.source,
                        created_at=r.created_at)
            for r in rows]
