"""Approver/admin API: orders, inventory, projects, access, and users."""
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
    ProjectCreate, ProjectEdit, ProjectMembersUpdate, ProjectOut,
    AdminProjectOut, ProjectMemberOut, CatalogPermissionsUpdate,
)
from backend.models.models import (
    Order, OrderLine, Item, User, AuditLog, InventoryTransaction, CountRequest,
    Project, ProjectMember, CatalogPermission,
)
from backend.auth.dependencies import require_role
from backend.services import order_service, item_service, user_service
from backend.services.audit_service import log_action
from backend.services.shipping_service import build_shipping_plan, ShippingPlanError, previous_business_day

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
        id=row.id, item_id=row.item_id,
        item_code=(row.item_code_snapshot or row.item.deleted_code or row.item.code),
        item_name=(row.item_name_snapshot or row.item.deleted_name or row.item.name),
        delta=row.delta, reason=row.reason, source=row.source,
        user=(row.user.full_name or row.user.username) if row.user else None,
        created_at=row.created_at, updated_at=row.updated_at or row.created_at,
    )


def _count_out(row: CountRequest) -> CountRequestOut:
    return CountRequestOut(
        id=row.id, item_id=row.item_id,
        item_code=row.item.deleted_code or row.item.code,
        item_name=row.item.deleted_name or row.item.name,
        requester=row.requester.full_name or row.requester.username,
        note=row.note or "", status=row.status,
        resolution_note=row.resolution_note or "",
        created_at=row.created_at, resolved_at=row.resolved_at,
        system_qty_before=row.system_qty_before, physical_qty=row.physical_qty,
        adjustment_delta=row.adjustment_delta,
    )


def _project_out(project: Project) -> AdminProjectOut:
    base = ProjectOut.model_validate(project).model_dump()
    members = [ProjectMemberOut(
        user_id=row.user_id, username=row.user.username,
        full_name=row.user.full_name, access_level=row.access_level,
    ) for row in sorted(project.members,
                        key=lambda r: ((r.user.full_name or r.user.username).lower()))
               if row.user and row.user.deleted_at is None]
    return AdminProjectOut(**base, members=members)


def _apply_project_fields(project: Project, data: dict) -> None:
    for key, value in data.items():
        if value is not None and hasattr(project, key):
            setattr(project, key, value.strip() if isinstance(value, str) else value)
    project.shipping_service = "UPS Ground"
    if project.shipping_state:
        project.shipping_state = project.shipping_state.upper()
    project.delivery_date = ""
    project.ship_by_date = ""
    project.ups_ground_days = None
    if project.event_date:
        try:
            event = datetime.strptime(project.event_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Event date must use YYYY-MM-DD.") from exc
        project.delivery_date = previous_business_day(event).isoformat()
        if project.shipping_state:
            try:
                plan = build_shipping_plan(event_date=project.event_date,
                                           shipping_state=project.shipping_state)
            except ShippingPlanError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
            for key, value in plan.as_dict().items():
                setattr(project, key, value)


# ---- Orders -----------------------------------------------------------------
@router.get("/orders", response_model=list[OrderOut])
def all_orders(status_filter: str | None = Query(None, alias="status"),
               db: Session = Depends(get_db),
               user: User = Depends(require_role("admin", "approver"))):
    q = _orders_query(db).filter(Order.deleted_at.is_(None))
    if status_filter and status_filter != "all":
        q = q.filter(Order.status == status_filter)
    return [order_service.to_order_out(o, viewer=user, db=db) for o in
            q.order_by(Order.created_at.desc()).all()]


@router.get("/orders/pending", response_model=list[OrderOut])
def pending_orders(db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin", "approver"))):
    rows = (_orders_query(db).filter(Order.deleted_at.is_(None),
                                     Order.status == "pending")
            .order_by(Order.created_at).all())
    return [order_service.to_order_out(o, viewer=user, db=db) for o in rows]


@router.get("/orders/updates", response_model=OrdersUpdateResponse)
async def poll_pending_orders(since: datetime | None = Query(None),
                              db: Session = Depends(get_db),
                              user: User = Depends(require_role("admin", "approver"))):
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    async def check():
        q = _orders_query(db).filter(Order.deleted_at.is_(None))
        if since is not None:
            q = q.filter(Order.updated_at > since)
        return q.order_by(Order.updated_at.desc()).all()

    waited = 0.0
    while True:
        rows = await check()
        if rows or since is None or waited >= POLL_MAX_WAIT_SECONDS:
            return OrdersUpdateResponse(
                server_time=datetime.now(timezone.utc),
                orders=[order_service.to_order_out(o, viewer=user, db=db)
                        for o in rows])
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS


@router.put("/orders/{order_id}", response_model=OrderOut)
def edit_order(order_id: int, body: AdminOrderUpdate,
               db: Session = Depends(get_db),
               user: User = Depends(require_role("admin", "approver"))):
    order = order_service.edit_order(
        db, order_id=order_id, actor=user, notes=body.notes,
        project_data=(body.project.model_dump(exclude_none=True)
                      if body.project else None),
        lines=body.lines, owner_only_pending=False, source="admin_app")
    return order_service.to_order_out(order, viewer=user, db=db)


@router.delete("/orders/{order_id}", status_code=204)
def delete_order(order_id: int, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    order_service.delete_order(db, order_id=order_id, actor=user,
                               source="admin_app")
    return None


@router.post("/orders/{order_id}/approve", response_model=OrderOut)
def approve(order_id: int, body: ApproveRequest, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin", "approver"))):
    order = order_service.approve_order(
        db, order_id=order_id, approver=user, reason=body.reason,
        line_overrides=body.line_overrides, allow_negative=body.allow_negative,
        source="admin_app")
    return order_service.to_order_out(order, viewer=user, db=db)


@router.post("/orders/{order_id}/reject", response_model=OrderOut)
def reject(order_id: int, body: RejectRequest, db: Session = Depends(get_db),
           user: User = Depends(require_role("admin", "approver"))):
    order = order_service.reject_order(db, order_id=order_id, approver=user,
                                       reason=body.reason, source="admin_app")
    return order_service.to_order_out(order, viewer=user, db=db)


@router.post("/orders/{order_id}/pick", response_model=OrderOut)
def pick_order(order_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_role("admin", "approver"))):
    order = order_service.start_picking(db, order_id=order_id, actor=user,
                                        source="admin_app")
    return order_service.to_order_out(order, viewer=user, db=db)


@router.post("/orders/{order_id}/fulfill", response_model=OrderOut)
async def fulfill_order(order_id: int, tracking_numbers: str = Form(...),
                        files: list[UploadFile] = File(...),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_role("admin", "approver"))):
    try:
        parsed = json.loads(tracking_numbers)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Tracking numbers must be a JSON list.") from exc
    if not isinstance(parsed, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Tracking numbers must be a list.")
    photos = [(upload.filename or "proof.jpg",
               upload.content_type or "application/octet-stream",
               await upload.read()) for upload in files]
    order = order_service.fulfill_order(
        db, order_id=order_id, actor=user,
        tracking_numbers=[str(value) for value in parsed], photos=photos,
        source="admin_app")
    return order_service.to_order_out(order, viewer=user, db=db)


# ---- Items / inventory -------------------------------------------------------
@router.get("/items", response_model=list[ItemOut])
def list_all_items(db: Session = Depends(get_db),
                   _u: User = Depends(require_role("admin"))):
    counts = dict(db.query(CountRequest.item_id, func.count(CountRequest.id))
                  .filter(CountRequest.status == "open")
                  .group_by(CountRequest.item_id).all())
    rows = db.query(Item).filter(Item.deleted_at.is_(None)).order_by(Item.name).all()
    return [ItemOut.from_orm_item(i, open_count_requests=counts.get(i.id, 0))
            for i in rows]


@router.post("/items", response_model=ItemOut, status_code=201)
def create_item(body: ItemCreate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    return ItemOut.from_orm_item(item_service.create_item(
        db, data=body.model_dump(), actor=user, source="admin_app"))


@router.put("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: int, body: ItemUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    return ItemOut.from_orm_item(item_service.update_item(
        db, item_id=item_id, data=data, actor=user, source="admin_app"))


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    item_service.delete_item(db, item_id=item_id, actor=user,
                             source="admin_app")
    return None


@router.post("/inventory/adjust", response_model=ItemOut)
def adjust_inventory(body: InventoryAdjustRequest, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    return ItemOut.from_orm_item(item_service.adjust_inventory(
        db, item_id=body.item_id, delta=body.delta, reason=body.reason,
        allow_negative=body.allow_negative, actor=user, source="admin_app"))


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


@router.delete("/inventory/transactions/{transaction_id}", status_code=204)
def delete_inventory_transaction(transaction_id: int,
                                 db: Session = Depends(get_db),
                                 user: User = Depends(require_role("admin"))):
    item_service.delete_inventory_transaction(
        db, transaction_id=transaction_id, actor=user, source="admin_app")
    return None


# ---- Recounts ---------------------------------------------------------------
@router.get("/count-requests", response_model=list[CountRequestOut])
def count_requests(status_filter: str = Query("open", alias="status"),
                   db: Session = Depends(get_db),
                   _u: User = Depends(require_role("admin"))):
    q = db.query(CountRequest).options(joinedload(CountRequest.item),
                                       joinedload(CountRequest.requester))
    if status_filter != "all":
        q = q.filter(CountRequest.status == status_filter)
    return [_count_out(row) for row in q.order_by(CountRequest.created_at.desc()).all()]


@router.post("/count-requests/{request_id}/resolve",
             response_model=CountRequestOut)
def resolve_count_request(request_id: int, body: CountRequestResolve,
                          db: Session = Depends(get_db),
                          user: User = Depends(require_role("admin"))):
    row = item_service.resolve_count_request(
        db, request_id=request_id, actor=user,
        physical_quantity=body.physical_quantity,
        resolution_note=body.resolution_note, source="admin_app")
    return _count_out(row)


# ---- Photos -----------------------------------------------------------------
@router.post("/items/{item_id}/images", response_model=ItemOut, status_code=201)
async def upload_item_image(item_id: int, file: UploadFile = File(...),
                            db: Session = Depends(get_db),
                            user: User = Depends(require_role("admin"))):
    item_service.add_item_image(db, item_id=item_id,
                                filename=file.filename or "",
                                content=await file.read(), actor=user,
                                source="admin_app")
    return ItemOut.from_orm_item(db.query(Item).filter_by(id=item_id).first())


@router.delete("/items/{item_id}/images/{image_id}", response_model=ItemOut)
def delete_item_image(item_id: int, image_id: int,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_role("admin"))):
    item_service.delete_item_image(db, item_id=item_id, image_id=image_id,
                                   actor=user, source="admin_app")
    return ItemOut.from_orm_item(db.query(Item).filter_by(id=item_id).first())


# ---- Projects / shared status -----------------------------------------------
@router.get("/projects", response_model=list[AdminProjectOut])
def admin_projects(db: Session = Depends(get_db),
                   _u: User = Depends(require_role("admin"))):
    rows = (db.query(Project).options(joinedload(Project.members).joinedload(ProjectMember.user))
            .order_by(Project.active.desc(), Project.name).all())
    return [_project_out(row) for row in rows]


@router.post("/projects", response_model=AdminProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    if db.query(Project).filter(func.lower(Project.name) == body.name.strip().lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A project with that name already exists.")
    data = body.model_dump(exclude={"ups_ground_days", "delivery_date", "ship_by_date"})
    project = Project(**data)
    project.access_restricted = bool(body.access_restricted)
    _apply_project_fields(project, data)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id,
                         access_level="owner"))
    log_action(db, user_id=user.id, action="project.create",
               object_type="project", object_id=project.id,
               new_value={"name": project.name, "address_mode": project.address_mode},
               source="admin_app")
    db.commit()
    row = (db.query(Project).options(joinedload(Project.members).joinedload(ProjectMember.user))
           .filter_by(id=project.id).first())
    return _project_out(row)


@router.put("/projects/{project_id}", response_model=AdminProjectOut)
def update_project(project_id: int, body: ProjectEdit,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    project = db.query(Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    old = {"name": project.name, "active": project.active,
           "address_mode": project.address_mode}
    _apply_project_fields(project, body.model_dump(exclude_none=True))
    log_action(db, user_id=user.id, action="project.update",
               object_type="project", object_id=project.id, old_value=old,
               new_value={"name": project.name, "active": project.active,
                          "address_mode": project.address_mode},
               source="admin_app")
    db.commit()
    row = (db.query(Project).options(joinedload(Project.members).joinedload(ProjectMember.user))
           .filter_by(id=project.id).first())
    return _project_out(row)


@router.put("/projects/{project_id}/members", response_model=AdminProjectOut)
def update_project_members(project_id: int, body: ProjectMembersUpdate,
                           db: Session = Depends(get_db),
                           user: User = Depends(require_role("admin"))):
    project = db.query(Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    desired = {}
    for raw in body.members:
        try:
            uid = int(raw.get("user_id"))
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Each project member needs a valid user ID.")
        level = str(raw.get("access_level", "viewer")).lower()
        if level not in {"viewer", "editor", "owner"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Member access must be viewer, editor, or owner.")
        member_user = db.query(User).filter(User.id == uid,
                                             User.deleted_at.is_(None)).first()
        if not member_user:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"User {uid} is not available.")
        desired[uid] = level
    db.query(ProjectMember).filter_by(project_id=project.id).delete(
        synchronize_session=False)
    for uid, level in desired.items():
        db.add(ProjectMember(project_id=project.id, user_id=uid,
                             access_level=level))
    project.access_restricted = True
    log_action(db, user_id=user.id, action="project.members_update",
               object_type="project", object_id=project.id,
               new_value={"members": desired}, source="admin_app")
    db.commit()
    row = (db.query(Project).options(joinedload(Project.members).joinedload(ProjectMember.user))
           .filter_by(id=project.id).first())
    return _project_out(row)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    project = db.query(Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    linked = db.query(Order.id).filter_by(project_id=project.id).first()
    if linked:
        project.active = False
        action = "project.deactivate"
    else:
        db.delete(project)
        action = "project.delete"
    log_action(db, user_id=user.id, action=action, object_type="project",
               object_id=project_id, old_value={"name": project.name},
               new_value={"active": False}, source="admin_app")
    db.commit()
    return None


# ---- Users / catalog visibility ---------------------------------------------
@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db),
               _u: User = Depends(require_role("admin"))):
    return [UserOut.from_orm_user(u) for u in
            db.query(User).filter(User.deleted_at.is_(None)).order_by(User.username).all()]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    return UserOut.from_orm_user(user_service.create_user(
        db, username=body.username, full_name=body.full_name,
        password=body.password, role_names=body.roles, actor=user,
        source="admin_app"))


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    updated = user_service.update_user(
        db, user_id=user_id, full_name=body.full_name, active=body.active,
        role_names=body.roles, password=body.password,
        catalog_access_mode=body.catalog_access_mode,
        actor=user, source="admin_app")
    return UserOut.from_orm_user(updated)


@router.get("/users/{user_id}/catalog-permissions")
def get_catalog_permissions(user_id: int, db: Session = Depends(get_db),
                            _u: User = Depends(require_role("admin"))):
    target = db.query(User).filter(User.id == user_id,
                                   User.deleted_at.is_(None)).first()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    rows = db.query(CatalogPermission).filter_by(user_id=user_id).all()
    return {
        "catalog_access_mode": target.catalog_access_mode or "all",
        "item_ids": [int(r.scope_value) for r in rows
                     if r.scope_type == "item" and r.scope_value.isdigit()],
        "categories": [r.scope_value for r in rows if r.scope_type == "category"],
        "brands": [r.scope_value for r in rows if r.scope_type == "brand"],
    }


@router.put("/users/{user_id}/catalog-permissions")
def update_catalog_permissions(user_id: int, body: CatalogPermissionsUpdate,
                               db: Session = Depends(get_db),
                               user: User = Depends(require_role("admin"))):
    target = db.query(User).filter(User.id == user_id,
                                   User.deleted_at.is_(None)).first()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    valid_item_ids = {row.id for row in db.query(Item.id).filter(
        Item.id.in_(set(body.item_ids)), Item.deleted_at.is_(None)).all()} if body.item_ids else set()
    db.query(CatalogPermission).filter_by(user_id=user_id).delete(
        synchronize_session=False)
    for item_id in sorted(valid_item_ids):
        db.add(CatalogPermission(user_id=user_id, scope_type="item",
                                 scope_value=str(item_id)))
    for category in sorted({" ".join(v.split()).strip() for v in body.categories if v.strip()}):
        db.add(CatalogPermission(user_id=user_id, scope_type="category",
                                 scope_value=category))
    for brand in sorted({" ".join(v.split()).strip() for v in body.brands if v.strip()}):
        db.add(CatalogPermission(user_id=user_id, scope_type="brand",
                                 scope_value=brand))
    target.catalog_access_mode = body.catalog_access_mode
    log_action(db, user_id=user.id, action="user.catalog_permissions_update",
               object_type="user", object_id=user_id,
               new_value=body.model_dump(), source="admin_app")
    db.commit()
    return body.model_dump()


@router.get("/catalog-options")
def catalog_options(db: Session = Depends(get_db),
                    _u: User = Depends(require_role("admin"))):
    items = db.query(Item).filter(Item.deleted_at.is_(None)).order_by(Item.name).all()
    return {
        "categories": sorted({i.category for i in items if i.category}, key=str.lower),
        "brands": sorted({i.brand for i in items if i.brand}, key=str.lower),
        "items": [{"id": i.id, "code": i.code, "name": i.name,
                   "category": i.category, "brand": i.brand} for i in items],
    }


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    user_service.delete_user(db, user_id=user_id, actor=user,
                             source="admin_app")
    return None


# ---- Audit ------------------------------------------------------------------
@router.get("/audit", response_model=list[AuditLogOut])
def audit_log(limit: int = Query(200, le=1000), db: Session = Depends(get_db),
              _u: User = Depends(require_role("admin"))):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return [AuditLogOut(
        id=r.id, user=(r.user.full_name or r.user.username) if r.user else None,
        action=r.action, object_type=r.object_type, object_id=r.object_id,
        old_value=r.old_value, new_value=r.new_value, source=r.source,
        created_at=r.created_at) for r in rows]
