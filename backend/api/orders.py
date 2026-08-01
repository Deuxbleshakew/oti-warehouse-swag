"""
backend/api/orders.py — a requester's own orders: submit, view history,
and long-poll for status changes so the browser can update without a
manual refresh.

Route ORDER matters here: FastAPI/Starlette match routes in the order
they're registered, and "/orders/updates" would otherwise be swallowed by
"/orders/{order_id}" (which happily tries to parse "updates" as an int and
fails) if that came first. Every literal-path route below is registered
before the "/orders/{order_id}" catch-all for exactly this reason.
"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from backend.db.session import get_db
from backend.schemas.schemas import OrderCreate, OrderOut, OrdersUpdateResponse
from backend.models.models import Order, OrderLine, User
from backend.auth.dependencies import get_current_user
from backend.services import order_service

router = APIRouter(tags=["orders"])

POLL_INTERVAL_SECONDS = 1.0
POLL_MAX_WAIT_SECONDS = 25.0   # keep under typical proxy/browser timeouts


def _my_orders_query(db: Session, user_id: int):
    return (db.query(Order).options(
        joinedload(Order.lines).joinedload(OrderLine.item),
        joinedload(Order.project), joinedload(Order.requester))
        .filter_by(requester_user_id=user_id))


@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(body: OrderCreate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    order = order_service.create_order(
        db, requester=user, project_id=body.project_id, notes=body.notes,
        lines=body.lines, source="browser")
    return order_service.to_order_out(order)


@router.get("/orders/my", response_model=list[OrderOut])
def my_orders(db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    orders = _my_orders_query(db, user.id).order_by(Order.created_at.desc()).all()
    return [order_service.to_order_out(o) for o in orders]


@router.get("/orders/updates", response_model=OrdersUpdateResponse)
async def poll_my_orders(
    since: datetime | None = Query(None, description=(
        "ISO timestamp of the last update you saw. Omit on first call to "
        "get current state immediately; pass it back on every call after "
        "that to long-poll for changes.")),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Long-polls up to ~25s. Returns immediately if `since` is omitted
    (bootstrap) or if something already changed; otherwise holds the
    connection and re-checks every second until either a change appears
    or the timeout is hit, at which point it returns an empty list — the
    client is expected to call again right away, in a loop."""
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    async def _check():
        q = _my_orders_query(db, user.id)
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


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    order = (db.query(Order).options(joinedload(Order.lines))
            .filter_by(id=order_id).first())
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found.")
    is_owner = order.requester_user_id == user.id
    is_reviewer = user.has_role("admin") or user.has_role("approver")
    if not (is_owner or is_reviewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Not your order.")
    return order_service.to_order_out(order)
