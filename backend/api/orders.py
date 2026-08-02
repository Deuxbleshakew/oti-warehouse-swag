"""Requester order submission, history, self-editing and count requests."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from backend.db.session import get_db
from backend.schemas.schemas import (
    OrderCreate, OrderOut, OrdersUpdateResponse, PendingOrderUpdate,
    CountRequestCreate, CountRequestOut,
)
from backend.models.models import (
    Order, OrderLine, User, OrderProofPhoto, CountRequest,
)
from backend.auth.dependencies import get_current_user
from backend.services import order_service, item_service

router = APIRouter(tags=["orders"])
POLL_INTERVAL_SECONDS = 1.0
POLL_MAX_WAIT_SECONDS = 25.0


def _my_orders_query(db: Session, user_id: int):
    return (db.query(Order).options(
        joinedload(Order.lines).joinedload(OrderLine.item),
        joinedload(Order.project), joinedload(Order.requester),
        joinedload(Order.tracking_numbers), joinedload(Order.proof_photos),
    ).filter_by(requester_user_id=user_id))


def _count_out(row: CountRequest) -> CountRequestOut:
    return CountRequestOut(
        id=row.id, item_id=row.item_id, item_code=row.item.code,
        item_name=row.item.name,
        requester=row.requester.full_name or row.requester.username,
        note=row.note or "", status=row.status,
        resolution_note=row.resolution_note or "",
        created_at=row.created_at, resolved_at=row.resolved_at,
    )


@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(body: OrderCreate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    order = order_service.create_order(
        db, requester=user, project_id=body.project_id,
        project_data=(body.new_project.model_dump() if body.new_project else None),
        save_project=body.save_project, notes=body.notes, lines=body.lines,
        source="browser")
    return order_service.to_order_out(order)


@router.get("/orders/my", response_model=list[OrderOut])
def my_orders(db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    orders = _my_orders_query(db, user.id).order_by(Order.created_at.desc()).all()
    return [order_service.to_order_out(o) for o in orders]


@router.put("/orders/{order_id}", response_model=OrderOut)
def edit_my_pending_order(order_id: int, body: PendingOrderUpdate,
                          db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    order = order_service.edit_order(
        db, order_id=order_id, actor=user, notes=body.notes,
        project_data=(body.project.model_dump(exclude_none=True)
                      if body.project else None),
        lines=body.lines, owner_only_pending=True, source="browser")
    return order_service.to_order_out(order)


@router.post("/catalog/{item_id}/count-requests",
             response_model=CountRequestOut, status_code=201)
def request_count(item_id: int, body: CountRequestCreate,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    row = item_service.create_count_request(
        db, item_id=item_id, requester=user, note=body.note, source="browser")
    return _count_out(row)


@router.get("/order-proof/{photo_id}", include_in_schema=False)
def order_proof_photo(photo_id: int, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    photo = (db.query(OrderProofPhoto).options(joinedload(OrderProofPhoto.order))
             .filter_by(id=photo_id).first())
    if not photo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proof photo not found.")
    is_owner = photo.order.requester_user_id == user.id
    is_reviewer = user.has_role("admin") or user.has_role("approver")
    if not (is_owner or is_reviewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your order proof.")
    return Response(content=photo.content, media_type=photo.content_type,
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/orders/updates", response_model=OrdersUpdateResponse)
async def poll_my_orders(
    since: datetime | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    async def _check():
        q = _my_orders_query(db, user.id)
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


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    order = (db.query(Order).options(
        joinedload(Order.lines).joinedload(OrderLine.item),
        joinedload(Order.project), joinedload(Order.requester),
        joinedload(Order.tracking_numbers), joinedload(Order.proof_photos),
    ).filter_by(id=order_id).first())
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found.")
    is_owner = order.requester_user_id == user.id
    is_reviewer = user.has_role("admin") or user.has_role("approver")
    if not (is_owner or is_reviewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your order.")
    return order_service.to_order_out(order)
