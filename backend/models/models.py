"""
backend/models/models.py — the authoritative schema.

Every table the system needs. Foreign keys, indexes, and created/updated
timestamps throughout. This file is the single source of truth for
structure — the API, the admin app, and the frontend all describe data
shaped like this, but none of them touch the database directly except
through the backend service layer that sits on top of these models.
"""
from datetime import datetime, timezone

from sqlalchemy import (Column, Integer, String, Float, Boolean, Text,
                         ForeignKey, DateTime, UniqueConstraint, Index,
                         LargeBinary)
from sqlalchemy.orm import relationship

from backend.db.session import Base


def utcnow():
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------------
# Users / roles (many-to-many: a user can hold more than one role)
# ----------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(60), unique=True, nullable=False, index=True)
    full_name = Column(String(120), nullable=False, default="")
    password_hash = Column(String(255), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow,
                        nullable=False)
    deleted_at = Column(DateTime, nullable=True, index=True)
    theme = Column(String(30), nullable=False, default="warehouse-dark")
    catalog_access_mode = Column(String(20), nullable=False, default="all")

    roles = relationship("Role", secondary="user_roles", back_populates="users")
    sessions = relationship("Session", back_populates="user",
                            cascade="all, delete-orphan")
    favorites = relationship("UserFavorite", back_populates="user",
                             cascade="all, delete-orphan")
    catalog_permissions = relationship("CatalogPermission", back_populates="user",
                                       cascade="all, delete-orphan")
    project_memberships = relationship("ProjectMember", back_populates="user",
                                       cascade="all, delete-orphan")

    def has_role(self, role_name: str) -> bool:
        return any(r.name == role_name for r in self.roles)


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    name = Column(String(40), unique=True, nullable=False)   # requester|approver|admin
    description = Column(String(255), default="")

    users = relationship("User", secondary="user_roles", back_populates="roles")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"),
                     primary_key=True)


class UserFavorite(Base):
    __tablename__ = "user_favorites"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"),
                     primary_key=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    user = relationship("User", back_populates="favorites")
    item = relationship("Item")


class CatalogPermission(Base):
    """Allow-list entry used when a user is in restricted catalog mode.

    scope_type is item, category, or brand. scope_value stores an item ID as
    text for item rules and the normalized category/brand label otherwise.
    """
    __tablename__ = "catalog_permissions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    scope_type = Column(String(20), nullable=False)
    scope_value = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    user = relationship("User", back_populates="catalog_permissions")

    __table_args__ = (
        UniqueConstraint("user_id", "scope_type", "scope_value",
                         name="uq_catalog_permission"),
        Index("ix_catalog_permission_user_scope", "user_id", "scope_type"),
    )


class Session(Base):
    """API auth sessions — an opaque bearer token, not a JWT. Simpler to
    revoke instantly (delete/flag the row) than a stateless JWT would be,
    which matters for an internal tool where an admin may need to kill a
    session immediately (e.g. someone leaves the team)."""
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="sessions")


# ----------------------------------------------------------------------------
# Projects / cost centers (also doubles as "event" tagging on orders)
# ----------------------------------------------------------------------------
class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), unique=True, nullable=False)
    description = Column(Text, default="")
    purpose = Column(String(255), default="")
    owner = Column(String(120), default="")
    customer = Column(String(120), default="")
    event_date = Column(String(20), default="")       # YYYY-MM-DD
    delivery_date = Column(String(20), default="")    # business day before event
    ship_by_date = Column(String(20), default="")     # latest warehouse ship date
    location = Column(String(255), default="")        # event venue / internal location
    shipping_address1 = Column(String(255), default="")
    shipping_address2 = Column(String(255), default="")
    shipping_city = Column(String(120), default="")
    shipping_state = Column(String(2), default="")
    shipping_postal_code = Column(String(20), default="")
    shipping_service = Column(String(40), default="UPS Ground")
    ups_ground_days = Column(Integer, nullable=True)
    attendees = Column(Integer, nullable=True)
    budget = Column(Float, nullable=True)
    status = Column(String(30), nullable=False, default="planning")
    notes = Column(Text, default="")
    active = Column(Boolean, nullable=False, default=True)
    address_mode = Column(String(20), nullable=False, default="variable")
    access_restricted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime, nullable=True, index=True)
    deleted_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow,
                        nullable=False)

    orders = relationship("Order", back_populates="project")
    members = relationship("ProjectMember", back_populates="project",
                           cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"),
                        primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    access_level = Column(String(20), nullable=False, default="viewer")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    project = relationship("Project", back_populates="members")
    user = relationship("User", back_populates="project_memberships")

    __table_args__ = (
        Index("ix_project_members_user", "user_id"),
    )


# ----------------------------------------------------------------------------
# Items / inventory
# ----------------------------------------------------------------------------
class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    code = Column(String(60), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    category = Column(String(80), default="")
    brand = Column(String(40), default="")
    color = Column(String(10), default="")            # hex, e.g. #1a2b3c
    color_name = Column(String(60), default="")
    measures = Column(String(120), default="")
    location = Column(String(120), default="")         # bin/shelf
    qty_on_hand = Column(Integer, nullable=False, default=0)
    inventory_counted = Column(Boolean, nullable=False, default=True)
    nav_tracked = Column(Boolean, nullable=False, default=False)
    nav_item_number = Column(String(80), default="")
    reorder_threshold = Column(Integer, nullable=False, default=0)
    cost = Column(Float, nullable=False, default=0.0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow,
                        nullable=False)
    deleted_at = Column(DateTime, nullable=True, index=True)
    deleted_code = Column(String(60), nullable=True)
    deleted_name = Column(String(200), nullable=True)

    images = relationship("ItemImage", back_populates="item",
                          cascade="all, delete-orphan",
                          order_by="ItemImage.position")
    transactions = relationship("InventoryTransaction", back_populates="item")
    nav_adjustments = relationship("NavAdjustmentTask", back_populates="item")
    location_balances = relationship("ItemLocationBalance", back_populates="item", cascade="all, delete-orphan", order_by="ItemLocationBalance.location_name")

    __table_args__ = (
        Index("ix_items_category", "category"),
        Index("ix_items_active", "active"),
    )



class ItemLocationBalance(Base):
    __tablename__ = "item_location_balances"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True)
    location_name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    bin_location = Column(String(120), default="")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    item = relationship("Item", back_populates="location_balances")
    __table_args__ = (UniqueConstraint("item_id", "location_name", name="uq_item_location_balance"),)


class ItemImage(Base):
    __tablename__ = "item_images"

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"),
                     nullable=False)
    filename = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, default=0)

    item = relationship("Item", back_populates="images")
    blob = relationship("ItemImageBlob", back_populates="image",
                        cascade="all, delete-orphan", uselist=False)


class ItemImageBlob(Base):
    """Persistent image bytes stored in the database.

    The original app stored only a filename in ItemImage and wrote the bytes to
    local disk. That works on one permanent PC, but ephemeral cloud services can
    erase local files during a restart. Keeping the bytes in a separate one-to-
    one table preserves existing ItemImage IDs and lets create_all add this table
    to an existing database without rewriting the original table.
    """
    __tablename__ = "item_image_blobs"

    image_id = Column(Integer, ForeignKey("item_images.id", ondelete="CASCADE"),
                      primary_key=True)
    content = Column(LargeBinary, nullable=False)
    content_type = Column(String(80), nullable=False,
                          default="application/octet-stream")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    image = relationship("ItemImage", back_populates="blob")


class InventoryTransaction(Base):
    """Every stock change, ever — the audit trail for quantity itself.
    Positive delta = stock added, negative = stock removed."""
    __tablename__ = "inventory_transactions"

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    delta = Column(Integer, nullable=False)
    reason = Column(String(255), nullable=False)        # required, per spec
    source = Column(String(20), nullable=False, default="admin_app")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow,
                        nullable=False)
    item_code_snapshot = Column(String(60), default="")
    item_name_snapshot = Column(String(200), default="")
    inventory_location = Column(String(100), nullable=False, default="0")

    item = relationship("Item", back_populates="transactions")
    user = relationship("User")

    __table_args__ = (
        Index("ix_inv_tx_item_id", "item_id"),
    )


# ----------------------------------------------------------------------------
# Orders (header) + order lines (one row per item requested)
# ----------------------------------------------------------------------------
class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    requester_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    # pending -> approved|rejected; approved -> picking -> fulfilled
    notes = Column(Text, default="")
    picking_started_at = Column(DateTime, nullable=True)
    fulfilled_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow,
                        nullable=False)

    requester = relationship("User")
    project = relationship("Project", back_populates="orders")
    lines = relationship("OrderLine", back_populates="order",
                         cascade="all, delete-orphan")
    approvals = relationship("Approval", back_populates="order",
                             cascade="all, delete-orphan")
    tracking_numbers = relationship("OrderTracking", back_populates="order",
                                    cascade="all, delete-orphan",
                                    order_by="OrderTracking.id")
    proof_photos = relationship("OrderProofPhoto", back_populates="order",
                                cascade="all, delete-orphan",
                                order_by="OrderProofPhoto.id")

    __table_args__ = (
        Index("ix_orders_status", "status"),
        Index("ix_orders_requester", "requester_user_id"),
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"),
                      nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty_requested = Column(Integer, nullable=False)
    qty_estimated = Column(Boolean, nullable=False, default=False)
    qty_approved = Column(Integer, nullable=True)   # set only on approval
    item_code_snapshot = Column(String(60), default="")
    item_name_snapshot = Column(String(200), default="")
    item_location_snapshot = Column(String(120), default="")

    order = relationship("Order", back_populates="lines")
    item = relationship("Item")


class OrderTracking(Base):
    __tablename__ = "order_tracking"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    tracking_number = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    order = relationship("Order", back_populates="tracking_numbers")


class OrderProofPhoto(Base):
    __tablename__ = "order_proof_photos"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    filename = Column(String(255), nullable=False, default="proof.jpg")
    content_type = Column(String(80), nullable=False, default="image/jpeg")
    content = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    order = relationship("Order", back_populates="proof_photos")


class NavAdjustmentTask(Base):
    """Manual Microsoft Dynamics NAV inventory posting work generated at fulfillment."""
    __tablename__ = "nav_adjustment_tasks"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    order_line_id = Column(Integer, ForeignKey("order_lines.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False, index=True)
    project_snapshot = Column(String(200), default="")
    item_code_snapshot = Column(String(60), default="")
    item_name_snapshot = Column(String(200), default="")
    nav_item_number = Column(String(80), default="")
    quantity_shipped = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    notes = Column(String(500), default="")
    fulfilled_at = Column(DateTime, nullable=False)
    posted_at = Column(DateTime, nullable=True)
    posted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    order = relationship("Order")
    order_line = relationship("OrderLine")
    item = relationship("Item", back_populates="nav_adjustments")
    posted_by = relationship("User")

    __table_args__ = (
        UniqueConstraint("order_id", "order_line_id", name="uq_nav_adjustment_order_line"),
        Index("ix_nav_adjustment_status_created", "status", "created_at"),
    )


class CountRequest(Base):
    __tablename__ = "count_requests"

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False, index=True)
    requester_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    note = Column(String(255), default="")
    status = Column(String(20), nullable=False, default="open")
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolution_note = Column(String(255), default="")
    system_qty_before = Column(Integer, nullable=True)
    physical_qty = Column(Integer, nullable=True)
    adjustment_delta = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    item = relationship("Item")
    requester = relationship("User", foreign_keys=[requester_user_id])
    resolved_by = relationship("User", foreign_keys=[resolved_by_user_id])

    __table_args__ = (
        Index("ix_count_requests_status_item", "status", "item_id"),
    )


class Approval(Base):
    """One row per approval decision. Kept separate from Order so the
    decision itself — who, when, why — has its own permanent record even
    if an order's status field changes again later."""
    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"),
                      nullable=False)
    approver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    decision = Column(String(20), nullable=False)    # approved | rejected
    reason = Column(String(255), default="")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    order = relationship("Order", back_populates="approvals")
    approver = relationship("User")


# ----------------------------------------------------------------------------
# Audit log — every important action, system-wide
# ----------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(60), nullable=False)          # e.g. "item.update"
    object_type = Column(String(40), nullable=False)      # e.g. "item"
    object_id = Column(String(40), default="")
    old_value = Column(Text, default="")                  # short JSON/text summary
    new_value = Column(Text, default="")
    source = Column(String(20), nullable=False, default="api")  # browser|admin_app|api
    created_at = Column(DateTime, default=utcnow, nullable=False)

    user = relationship("User")

    __table_args__ = (
        Index("ix_audit_object", "object_type", "object_id"),
        Index("ix_audit_created", "created_at"),
    )


# ----------------------------------------------------------------------------
# App-wide settings (key/value)
# ----------------------------------------------------------------------------
class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(80), primary_key=True)
    value = Column(Text, default="")


# ----------------------------------------------------------------------------
# Notifications and reusable/custom kits
# ----------------------------------------------------------------------------
class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(40), nullable=False, default="info")
    title = Column(String(160), nullable=False)
    message = Column(String(500), default="")
    object_type = Column(String(40), default="")
    object_id = Column(Integer, nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    user = relationship("User")

class Kit(Base):
    __tablename__ = "kits"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    code = Column(String(60), unique=True, nullable=False, index=True)
    description = Column(Text, default="")
    active = Column(Boolean, nullable=False, default=True)
    custom = Column(Boolean, nullable=False, default=False)
    saved_for_reuse = Column(Boolean, nullable=False, default=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    components = relationship("KitComponent", back_populates="kit", cascade="all, delete-orphan", order_by="KitComponent.position")

class KitComponent(Base):
    __tablename__ = "kit_components"
    id = Column(Integer, primary_key=True)
    kit_id = Column(Integer, ForeignKey("kits.id", ondelete="CASCADE"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    position = Column(Integer, nullable=False, default=0)
    kit = relationship("Kit", back_populates="components")
    item = relationship("Item")
    __table_args__ = (UniqueConstraint("kit_id", "item_id", "position", name="uq_kit_component_position"),)
