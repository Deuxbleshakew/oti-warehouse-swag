"""Approver/admin API: order lifecycle, inventory, recounts and users."""
import asyncio
import json
from datetime import datetime, timezone

from fastapi import (APIRouter, Depends, Query, UploadFile, File, Form,
                     HTTPException, status)
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from backend.db.session import get_db
from backend.schemas.schemas import (
    OrderOut, ApproveRequest, RejectRequest, OrdersUpdateResponse,
    AdminOrderUpdate, ItemOut, ItemCreate, ItemUpdate,
    InventoryAdjustRequest, InventoryTransactionOut,
    InventoryTransactionUpdate, UserOut, UserCreate, UserUpdate,
    AuditLogOut, CountRequestOut, CountRequestResolve,
)
from backend.models.models import (
    Order, OrderLine, Item, User, AuditLog, InventoryTransaction, CountRequest,
)
from backend.auth.dependencies import require_role
from backend.services import order_service, item_service, user_service

router = APIRouter(prefix="/admin", tags=["admin"])
POLL_INTERVAL_SECONDS = 1.0
POLL_MAX_WAIT_SECONDS = 25.0


def _orders_query(db: Session):
    return db.query(Order).options(
        joinedload(Order.lines).joinedload(OrderLine.item),
        joinedload(Order.project), joinedload(Order.requester),
        joinedload(Order.tracking_numbers), joinedload(Order.proof_photos),
    )


def _transaction_out(row: InventoryTransaction) -> InventoryTransactionOut:
    return InventoryTransactionOut(
        id=row.id, item_id=row.item_id, item_code=row.item.code,
        item_name=row.item.name, delta=row.delta, reason=row.reason,
        source=row.source, user=(row.user.full_name or row.user.username)
        if row.user else None, created_at=row.created_at,
        updated_at=row.updated_at or row.created_at,
    )


def _count_out(row: CountRequest) -> CountRequestOut:
    return CountRequestOut(
        id=row.id, item_id=row.item_id, item_code=row.item.code,
        item_name=row.item.name,
        requester=row.requester.full_name or row.requester.username,
        note=row.note or "", status=row.status,
        resolution_note=row.resolution_note or "",
        created_at=row.created_at, resolved_at=row.resolved_at,
    )


# ---- Orders / approvals / fulfillment ---------------------------------------
@router.get("/orders", response_model=list[OrderOut])
def all_orders(status_filter: str | None = Query(None, alias="status"),
               db: Session = Depends(get_db),
               _u: User = Depends(require_role("admin", "approver"))):
    q = _orders_query(db)
    if status_filter and status_filter != "all":
        q = q.filter(Order.status == status_filter)
    orders = q.order_by(Order.created_at.desc()).all()
    return [order_service.to_order_out(o) for o in orders]


@router.get("/orders/pending", response_model=list[OrderOut])
def pending_orders(db: Session = Depends(get_db),
                   _u: User = Depends(require_role("admin", "approver"))):
    orders = (_orders_query(db).filter(Order.status == "pending")
              .order_by(Order.created_at).all())
    return [order_service.to_order_out(o) for o in orders]


@router.get("/orders/updates", response_model=OrdersUpdateResponse)
async def poll_pending_orders(
    since: datetime | None = Query(None),
    db: Session = Depends(get_db),
    _u: User = Depends(require_role("admin", "approver")),
):
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    async def _check():
        q = _orders_query(db)
        if since is not None:
            q = q.filter(Order.updated_at > since)
        return q.order_by(Order.updated_at.desc()).all()

    if since is None:
        orders = await _check()
        return OrdersUpdateResponse(
            server_time=datetime.now(timezone.utc),
            orders=[order_service.to_order_out(o) for o in orders])
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


@router.put("/orders/{order_id}", response_model=OrderOut)
def edit_order(order_id: int, body: AdminOrderUpdate,
               db: Session = Depends(get_db),
               user: User = Depends(require_role("admin", "approver"))):
    order = order_service.edit_order(
        db, order_id=order_id, actor=user, notes=body.notes,
        project_data=(body.project.model_dump(exclude_none=True)
                      if body.project else None),
        lines=body.lines, owner_only_pending=False, source="admin_app")
    return order_service.to_order_out(order)


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


@router.post("/orders/{order_id}/pick", response_model=OrderOut)
def pick_order(order_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_role("admin", "approver"))):
    return order_service.to_order_out(order_service.start_picking(
        db, order_id=order_id, actor=user, source="admin_app"))


@router.post("/orders/{order_id}/fulfill", response_model=OrderOut)
async def fulfill_order(
    order_id: int,
    tracking_numbers: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "approver")),
):
    try:
        parsed = json.loads(tracking_numbers)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Tracking numbers must be a JSON list.") from exc
    if not isinstance(parsed, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Tracking numbers must be a list.")
    photos = []
    for upload in files:
        photos.append((upload.filename or "proof.jpg",
                       upload.content_type or "application/octet-stream",
                       await upload.read()))
    order = order_service.fulfill_order(
        db, order_id=order_id, actor=user,
        tracking_numbers=[str(value) for value in parsed], photos=photos,
        source="admin_app")
    return order_service.to_order_out(order)


# ---- Items / inventory -------------------------------------------------------
@router.get("/items", response_model=list[ItemOut])
def list_all_items(db: Session = Depends(get_db),
                   _u: User = Depends(require_role("admin"))):
    counts = dict(db.query(CountRequest.item_id, func.count(CountRequest.id))
                  .filter(CountRequest.status == "open")
                  .group_by(CountRequest.item_id).all())
    items = db.query(Item).order_by(Item.name).all()
    return [ItemOut.from_orm_item(i, open_count_requests=counts.get(i.id, 0))
            for i in items]


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


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    item_service.delete_item(db, item_id=item_id, actor=user,
                             source="admin_app")
    return None


@router.post("/inventory/adjust", response_model=ItemOut)
def adjust_inventory(body: InventoryAdjustRequest, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    item = item_service.adjust_inventory(
        db, item_id=body.item_id, delta=body.delta, reason=body.reason,
        allow_negative=body.allow_negative, actor=user, source="admin_app")
    return ItemOut.from_orm_item(item)


@router.get("/inventory/transactions", response_model=list[InventoryTransactionOut])
def inventory_transactions(limit: int = Query(500, ge=1, le=2000),
                           db: Session = Depends(get_db),
                           _u: User = Depends(require_role("admin"))):
    rows = (db.query(InventoryTransaction)
            .options(joinedload(InventoryTransaction.item),
                     joinedload(InventoryTransaction.user))
            .order_by(InventoryTransaction.created_at.desc()).limit(limit).all())
    return [_transaction_out(row) for row in rows]


@router.put("/inventory/transactions/{transaction_id}",
            response_model=InventoryTransactionOut)
def edit_inventory_transaction(transaction_id: int,
                               body: InventoryTransactionUpdate,
                               db: Session = Depends(get_db),
                               user: User = Depends(require_role("admin"))):
    row = item_service.update_inventory_transaction(
        db, transaction_id=transaction_id, delta=body.delta,
        reason=body.reason, allow_negative=body.allow_negative,
        actor=user, source="admin_app")
    return _transaction_out(row)


# ---- Recount requests --------------------------------------------------------
@router.get("/count-requests", response_model=list[CountRequestOut])
def count_requests(status_filter: str = Query("open", alias="status"),
                   db: Session = Depends(get_db),
                   _u: User = Depends(require_role("admin"))):
    q = db.query(CountRequest).options(
        joinedload(CountRequest.item), joinedload(CountRequest.requester))
    if status_filter != "all":
        q = q.filter(CountRequest.status == status_filter)
    return [_count_out(row) for row in
            q.order_by(CountRequest.created_at.desc()).all()]


@router.post("/count-requests/{request_id}/resolve",
             response_model=CountRequestOut)
def resolve_count_request(request_id: int, body: CountRequestResolve,
                          db: Session = Depends(get_db),
                          user: User = Depends(require_role("admin"))):
    row = item_service.resolve_count_request(
        db, request_id=request_id, actor=user,
        resolution_note=body.resolution_note, source="admin_app")
    return _count_out(row)


# ---- Item photos -------------------------------------------------------------
@router.post("/items/{item_id}/images", response_model=ItemOut, status_code=201)
async def upload_item_image(item_id: int, file: UploadFile = File(...),
                            db: Session = Depends(get_db),
                            user: User = Depends(require_role("admin"))):
    content = await file.read()
    item_service.add_item_image(db, item_id=item_id,
                                filename=file.filename or "", content=content,
                                actor=user, source="admin_app")
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


# ---- Users -------------------------------------------------------------------
@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db),
               _u: User = Depends(require_role("admin"))):
    return [UserOut.from_orm_user(u) for u in
            db.query(User).order_by(User.username).all()]


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


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    user_service.delete_user(db, user_id=user_id, actor=user,
                             source="admin_app")
    return None


# ---- Audit -------------------------------------------------------------------
@router.get("/audit", response_model=list[AuditLogOut])
def audit_log(limit: int = Query(200, le=1000), db: Session = Depends(get_db),
              _u: User = Depends(require_role("admin"))):
    rows = (db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all())
    return [AuditLogOut(id=r.id, user=r.user.username if r.user else None,
                        action=r.action, object_type=r.object_type,
                        object_id=r.object_id, old_value=r.old_value,
                        new_value=r.new_value, source=r.source,
                        created_at=r.created_at) for r in rows]
