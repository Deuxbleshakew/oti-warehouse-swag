"""
backend/schemas/schemas.py — Pydantic request/response models.

These are the API's public contract — what a client sends and receives.
Kept separate from the SQLAlchemy models (backend/models/models.py) on
purpose: the database shape and the API shape are allowed to drift from
each other over time without one forcing a change in the other.
"""
from datetime import datetime
from typing import List, Optional, Dict

from pydantic import BaseModel, Field, ConfigDict


# ---- Auth -------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    active: bool
    roles: List[str] = []

    @classmethod
    def from_orm_user(cls, user):
        return cls(id=user.id, username=user.username, full_name=user.full_name,
                   active=user.active, roles=[r.name for r in user.roles])


class TokenResponse(BaseModel):
    token: str
    expires_at: datetime
    user: UserOut


class UserCreate(BaseModel):
    username: str
    full_name: str = ""
    password: str
    roles: List[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    active: Optional[bool] = None
    roles: Optional[List[str]] = None
    password: Optional[str] = None    # set to reset


# ---- Items / catalog ---------------------------------------------------------
class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str
    category: str
    brand: str
    color: str
    color_name: str
    measures: str
    location: str
    qty_on_hand: int
    reorder_threshold: int
    cost: float
    active: bool
    images: List[str] = []
    image_ids: List[int] = []   # parallel to images; needed for deletes

    @classmethod
    def from_orm_item(cls, item, include_sensitive=True):
        d = dict(
            id=item.id, code=item.code, name=item.name,
            description=item.description, category=item.category,
            brand=item.brand, color=item.color, color_name=item.color_name,
            measures=item.measures, location=item.location,
            qty_on_hand=item.qty_on_hand if include_sensitive else -1,
            reorder_threshold=item.reorder_threshold,
            cost=item.cost if include_sensitive else 0.0,
            active=item.active,
            images=[img.filename for img in item.images],
            image_ids=[img.id for img in item.images],
        )
        return cls(**d)


class ItemCreate(BaseModel):
    code: str
    name: str
    description: str = ""
    category: str = ""
    brand: str = ""
    color: str = ""
    color_name: str = ""
    measures: str = ""
    location: str = ""
    qty_on_hand: int = 0
    reorder_threshold: int = 0
    cost: float = 0.0
    active: bool = True


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    color: Optional[str] = None
    color_name: Optional[str] = None
    measures: Optional[str] = None
    location: Optional[str] = None
    reorder_threshold: Optional[int] = None
    cost: Optional[float] = None
    active: Optional[bool] = None
    # qty_on_hand deliberately NOT editable here — stock changes must go
    # through /admin/inventory/adjust so every change gets a reason + a
    # logged transaction. Editing it directly here would create silent,
    # unexplained stock changes with no audit trail.


class InventoryAdjustRequest(BaseModel):
    item_id: int
    delta: int                       # positive = add stock, negative = remove
    reason: str                      # required, per spec
    allow_negative: bool = False      # only meaningful for admins; see service


# ---- Projects -----------------------------------------------------------------
class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    owner: str
    event_date: str
    delivery_date: str
    ship_by_date: str = ""
    location: str
    shipping_address1: str = ""
    shipping_address2: str = ""
    shipping_city: str = ""
    shipping_state: str = ""
    shipping_postal_code: str = ""
    shipping_service: str = "UPS Ground"
    ups_ground_days: Optional[int] = None
    attendees: Optional[int]
    budget: Optional[float]
    status: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    purpose: str = ""
    owner: str = ""
    customer: str = ""
    event_date: str = ""
    # Calculated by the backend from the event date and transit time. Kept in
    # the request model for compatibility with older clients, but overwritten.
    delivery_date: str = ""
    ship_by_date: str = ""
    location: str = ""
    shipping_address1: str = ""
    shipping_address2: str = ""
    shipping_city: str = ""
    shipping_state: str = ""
    shipping_postal_code: str = ""
    shipping_service: str = "UPS Ground"
    ups_ground_days: Optional[int] = Field(default=None, ge=1, le=6)
    attendees: Optional[int] = None
    budget: Optional[float] = None
    status: str = "planning"
    notes: str = ""


# ---- Orders -------------------------------------------------------------------
class OrderLineIn(BaseModel):
    item_id: int
    qty: int = Field(gt=0)


class OrderCreate(BaseModel):
    # Choose an existing reusable project OR create a one-time/new project as
    # part of this order. Most events happen once, so new projects default to
    # not appearing in the reusable picker unless save_project is true.
    project_id: Optional[int] = None
    new_project: Optional[ProjectCreate] = None
    save_project: bool = False
    notes: str = ""
    lines: List[OrderLineIn] = Field(min_length=1)


class OrderLineOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    item_name: str
    qty_requested: int
    qty_approved: Optional[int]


class OrderOut(BaseModel):
    id: int
    status: str
    requester: str
    project: Optional[str]
    project_id: Optional[int]
    project_details: Optional[ProjectOut] = None
    notes: str
    created_at: datetime
    updated_at: datetime
    lines: List[OrderLineOut]


class ApproveRequest(BaseModel):
    reason: str = ""
    # per-line overrides for partial approval: {item_id: approved_qty}.
    # Omit entirely to approve every line at its full requested quantity.
    line_overrides: Optional[Dict[int, int]] = None
    allow_negative: bool = False      # admin-only, enforced in the service


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1)


class OrdersUpdateResponse(BaseModel):
    server_time: datetime
    orders: List[OrderOut]


# ---- Audit --------------------------------------------------------------------
class AuditLogOut(BaseModel):
    id: int
    user: Optional[str]
    action: str
    object_type: str
    object_id: str
    old_value: str
    new_value: str
    source: str
    created_at: datetime
