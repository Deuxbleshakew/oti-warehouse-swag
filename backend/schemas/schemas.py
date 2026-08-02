"""Pydantic request/response models for the API contract."""
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
    password: Optional[str] = None


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
    image_ids: List[int] = []
    open_count_requests: int = 0

    @classmethod
    def from_orm_item(cls, item, include_sensitive=True,
                      open_count_requests: int = 0):
        return cls(
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
            open_count_requests=open_count_requests,
        )


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


class InventoryAdjustRequest(BaseModel):
    item_id: int
    delta: int
    reason: str
    allow_negative: bool = False


class InventoryTransactionOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    item_name: str
    delta: int
    reason: str
    source: str
    user: Optional[str]
    created_at: datetime
    updated_at: datetime


class InventoryTransactionUpdate(BaseModel):
    delta: int
    reason: str = Field(min_length=1, max_length=255)
    allow_negative: bool = False


# ---- Projects ----------------------------------------------------------------
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
    delivery_date: str = ""
    ship_by_date: str = ""
    location: str = ""
    shipping_address1: str = ""
    shipping_address2: str = ""
    shipping_city: str = ""
    shipping_state: str = ""
    shipping_postal_code: str = ""
    shipping_service: str = "UPS Ground"
    # accepted for old clients but ignored; transit is map-driven by state
    ups_ground_days: Optional[int] = None
    attendees: Optional[int] = None
    budget: Optional[float] = None
    status: str = "planning"
    notes: str = ""


class ProjectEdit(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    owner: Optional[str] = None
    event_date: Optional[str] = None
    location: Optional[str] = None
    shipping_address1: Optional[str] = None
    shipping_address2: Optional[str] = None
    shipping_city: Optional[str] = None
    shipping_state: Optional[str] = None
    shipping_postal_code: Optional[str] = None
    attendees: Optional[int] = None


# ---- Orders -------------------------------------------------------------------
class OrderLineIn(BaseModel):
    item_id: int
    qty: int = Field(gt=0)
    estimated: bool = False


class OrderEditLine(BaseModel):
    item_id: int
    qty: int = Field(gt=0)
    estimated: bool = False


class OrderCreate(BaseModel):
    project_id: Optional[int] = None
    new_project: Optional[ProjectCreate] = None
    save_project: bool = False
    notes: str = ""
    lines: List[OrderLineIn] = Field(min_length=1)


class PendingOrderUpdate(BaseModel):
    notes: Optional[str] = None
    project: Optional[ProjectEdit] = None
    lines: Optional[List[OrderEditLine]] = Field(default=None, min_length=1)


class AdminOrderUpdate(PendingOrderUpdate):
    pass


class OrderLineOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    item_name: str
    qty_requested: int
    qty_estimated: bool = False
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
    picking_started_at: Optional[datetime] = None
    fulfilled_at: Optional[datetime] = None
    incomplete: bool = False
    incomplete_reasons: List[str] = []
    can_self_edit: bool = False
    tracking_numbers: List[str] = []
    proof_photo_ids: List[int] = []
    lines: List[OrderLineOut]


class ApproveRequest(BaseModel):
    reason: str = ""
    line_overrides: Optional[Dict[int, int]] = None
    allow_negative: bool = False


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1)


class OrdersUpdateResponse(BaseModel):
    server_time: datetime
    orders: List[OrderOut]


# ---- Count requests ----------------------------------------------------------
class CountRequestCreate(BaseModel):
    note: str = Field(default="", max_length=255)


class CountRequestResolve(BaseModel):
    resolution_note: str = Field(default="", max_length=255)


class CountRequestOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    item_name: str
    requester: str
    note: str
    status: str
    resolution_note: str
    created_at: datetime
    resolved_at: Optional[datetime]


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
