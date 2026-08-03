"""
backend/services/item_service.py — item CRUD and inventory adjustments.
Stock quantity is never edited through the plain item-update path (see
ItemUpdate schema's comment) — it only ever changes through
adjust_inventory(), so every change carries a required reason and a
logged InventoryTransaction.
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from backend.models.models import (Item, InventoryTransaction, User, OrderLine,
                                    CountRequest, ItemLocationBalance)
from backend.services.audit_service import log_action
from backend.services import access_service


def create_item(db: Session, *, data: dict, actor: User, source="api") -> Item:
    if db.query(Item).filter_by(code=data["code"]).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Item code {data['code']} already exists.")
    opening_qty = data.pop("qty_on_hand", None)
    data["nav_item_number"] = (data.get("nav_item_number") or "").strip()
    if not data.get("nav_tracked"):
        data["nav_item_number"] = ""
    counted = opening_qty is not None
    data["qty_on_hand"] = int(opening_qty or 0)
    data["inventory_counted"] = counted
    item = Item(**data)
    db.add(item)
    db.flush()
    db.add(ItemLocationBalance(item_id=item.id, location_name="On-site", quantity=item.qty_on_hand, bin_location=item.location or ""))
    db.add(ItemLocationBalance(item_id=item.id, location_name="Off-site", quantity=0, bin_location=""))
    if counted and item.qty_on_hand:
        db.add(InventoryTransaction(item_id=item.id, delta=item.qty_on_hand,
                                    reason="Initial stock", source=source,
                                    user_id=actor.id,
                                    item_code_snapshot=item.code,
                                    item_name_snapshot=item.name))
    if not counted:
        db.add(CountRequest(item_id=item.id, requester_user_id=actor.id,
                            note="Initial count required", status="open"))
    log_action(db, user_id=actor.id, action="item.create", object_type="item",
              object_id=item.id, new_value={**data, "opening_quantity_provided": counted}, source=source)
    db.commit()
    db.refresh(item)
    return item


def update_item(db: Session, *, item_id: int, data: dict, actor: User,
                source="api") -> Item:
    item = db.query(Item).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    if "nav_item_number" in data:
        data["nav_item_number"] = (data.get("nav_item_number") or "").strip()
    if data.get("nav_tracked") is False:
        data["nav_item_number"] = ""
    old = {k: getattr(item, k) for k in data if hasattr(item, k)}
    for k, v in data.items():
        if v is not None:
            setattr(item, k, v)
    log_action(db, user_id=actor.id, action="item.update", object_type="item",
              object_id=item.id, old_value=old, new_value=data, source=source)
    db.commit()
    db.refresh(item)
    return item


def adjust_inventory(db: Session, *, item_id: int, delta: int, reason: str,
                     allow_negative: bool, actor: User, source="api", inventory_location: str = "On-site") -> Item:
    if not reason or not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "A reason is required for any stock adjustment.")
    item = db.query(Item).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")

    resulting = item.qty_on_hand + delta
    if resulting < 0 and not (allow_negative and actor.has_role("admin")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Adjustment would take {item.code} to {resulting} "
            f"(below zero). Only an admin may explicitly allow that.")

    old_qty = item.qty_on_hand
    item.qty_on_hand = resulting
    loc_name = (inventory_location or "On-site").strip() or "On-site"
    balance = db.query(ItemLocationBalance).filter_by(item_id=item.id, location_name=loc_name).first()
    if not balance:
        balance = ItemLocationBalance(item_id=item.id, location_name=loc_name, quantity=0, bin_location=item.location or "")
        db.add(balance); db.flush()
    loc_result = balance.quantity + delta
    if loc_result < 0 and not (allow_negative and actor.has_role("admin")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Adjustment would take {loc_name} below zero.")
    balance.quantity = loc_result
    db.add(InventoryTransaction(item_id=item.id, delta=delta, reason=reason,
                                source=source, user_id=actor.id,
                                item_code_snapshot=item.code,
                                item_name_snapshot=item.name))
    log_action(db, user_id=actor.id, action="inventory.adjust",
              object_type="item", object_id=item.id,
              old_value={"qty_on_hand": old_qty},
              new_value={"qty_on_hand": resulting, "delta": delta,
                         "reason": reason}, source=source)
    db.commit()
    db.refresh(item)
    return item


# ---- item photos -------------------------------------------------------------
# Files are kept in frontend/assets as a local compatibility/cache copy, while
# the authoritative bytes live in item_image_blobs so cloud restarts cannot
# erase them. DB rows in item_images keep ordering and filenames.
import os
import re
import secrets

from backend.models.models import ItemImage, ItemImageBlob
from backend.config import ASSETS_DIR

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024   # phone photos are ~2-4MB; 8 is headroom


def image_content_type(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp",
    }.get(ext, "application/octet-stream")


def resolve_stored_image_path(stored_value: str) -> str | None:
    """Resolve current filenames and legacy path-shaped values inside assets."""
    from urllib.parse import urlparse

    raw = (stored_value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("\\", "/")
    parsed = urlparse(normalized)
    if parsed.scheme in ("http", "https"):
        normalized = parsed.path
    marker = "/assets/"
    if marker in normalized:
        normalized = normalized.split(marker, 1)[1]
    normalized = normalized.lstrip("/")

    root = os.path.realpath(ASSETS_DIR)
    candidates = [os.path.join(root, normalized),
                  os.path.join(root, os.path.basename(normalized))]
    if os.path.isabs(raw):
        candidates.insert(0, os.path.realpath(raw))
    for candidate in candidates:
        real = os.path.realpath(candidate)
        try:
            inside_root = os.path.commonpath([root, real]) == root
        except ValueError:
            inside_root = False
        if inside_root and os.path.isfile(real):
            return real
    return None


def backfill_legacy_image_blobs(db: Session) -> int:
    """Copy surviving legacy disk photos into persistent database storage."""
    images = (db.query(ItemImage)
              .outerjoin(ItemImageBlob, ItemImageBlob.image_id == ItemImage.id)
              .filter(ItemImageBlob.image_id.is_(None)).all())
    added = 0
    for image in images:
        path = resolve_stored_image_path(image.filename)
        if not path:
            continue
        try:
            with open(path, "rb") as fh:
                content = fh.read()
        except OSError:
            continue
        if not content or len(content) > MAX_IMAGE_BYTES:
            continue
        db.add(ItemImageBlob(image_id=image.id, content=content,
                             content_type=image_content_type(path)))
        added += 1
    if added:
        db.commit()
    return added


def add_item_image(db: Session, *, item_id: int, filename: str,
                   content: bytes, actor: User, source="api") -> ItemImage:
    item = db.query(Item).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Image must be jpg, png, gif, or webp.")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Image is too large (8 MB max).")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file.")

    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", item.code)
    stored_name = f"{safe_code}_{secrets.token_hex(4)}{ext}"
    os.makedirs(ASSETS_DIR, exist_ok=True)
    with open(os.path.join(ASSETS_DIR, stored_name), "wb") as f:
        f.write(content)

    position = (db.query(ItemImage).filter_by(item_id=item.id).count())
    img = ItemImage(item_id=item.id, filename=stored_name, position=position)
    db.add(img)
    db.flush()  # image ID is the one-to-one key for the persistent blob
    content_type = image_content_type(stored_name)
    db.add(ItemImageBlob(image_id=img.id, content=content,
                         content_type=content_type))
    log_action(db, user_id=actor.id, action="item.image_add",
              object_type="item", object_id=item.id,
              new_value=stored_name, source=source)
    db.commit()
    db.refresh(img)
    return img


def delete_item_image(db: Session, *, item_id: int, image_id: int,
                      actor: User, source="api") -> None:
    img = db.query(ItemImage).filter_by(id=image_id, item_id=item_id).first()
    if not img:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found.")
    path = os.path.join(ASSETS_DIR, img.filename)
    db.delete(img)
    log_action(db, user_id=actor.id, action="item.image_delete",
              object_type="item", object_id=item_id,
              old_value=img.filename, source=source)
    db.commit()
    # remove the file after the DB commit succeeds; a leftover file is
    # harmless, a DB row pointing at a deleted file is a broken image
    try:
        os.remove(path)
    except OSError:
        pass


# ---- safe deletion / history editing / recount requests ---------------------
def delete_item(db: Session, *, item_id: int, actor: User, source="api") -> None:
    """Retire an item, preserve its historical label, and release its part #."""
    item = db.query(Item).filter_by(id=item_id).first()
    if not item or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    now = datetime.now(timezone.utc)
    original_code = item.code
    original_name = item.name
    old = {"code": original_code, "name": original_name,
           "qty_on_hand": item.qty_on_hand, "active": item.active}

    # Snapshot old labels onto every historical record before changing the
    # unique catalog code. This lets the original part number be reused safely.
    for line in db.query(OrderLine).filter_by(item_id=item.id).all():
        line.item_code_snapshot = line.item_code_snapshot or original_code
        line.item_name_snapshot = line.item_name_snapshot or original_name
        line.item_location_snapshot = line.item_location_snapshot or item.location
    for tx in db.query(InventoryTransaction).filter_by(item_id=item.id).all():
        tx.item_code_snapshot = tx.item_code_snapshot or original_code
        tx.item_name_snapshot = tx.item_name_snapshot or original_name

    item.deleted_code = original_code
    item.deleted_name = original_name
    item.code = f"DELETED-{item.id}"[:60]
    item.name = f"{original_name} [Deleted Item]"[:200]
    item.active = False
    item.deleted_at = now
    log_action(db, user_id=actor.id, action="item.delete", object_type="item",
               object_id=item_id, old_value=old,
               new_value={"deleted": True, "released_code": original_code,
                          "tombstone_code": item.code,
                          "historical_name": original_name}, source=source)
    db.commit()


def delete_inventory_transaction(db: Session, *, transaction_id: int,
                                 actor: User, source="admin_app") -> None:
    tx = db.query(InventoryTransaction).filter_by(id=transaction_id).first()
    if not tx:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Inventory transaction not found.")
    reason_key = (tx.reason or "").strip().lower()
    order_generated = (reason_key.startswith("order #") or
                       reason_key.startswith("deleted order #"))
    before_qty = tx.item.qty_on_hand
    after_qty = before_qty
    if not order_generated:
        # Deleting a manual adjustment reverses its effect. Order-generated
        # rows are historical breadcrumbs only and never put shipped stock back.
        after_qty = before_qty - tx.delta
        tx.item.qty_on_hand = after_qty
    snapshot = {"item_id": tx.item_id, "item_code": (tx.item_code_snapshot or tx.item.deleted_code or tx.item.code),
                "delta": tx.delta, "reason": tx.reason, "source": tx.source,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
                "order_generated": order_generated,
                "qty_before": before_qty, "qty_after": after_qty}
    log_action(db, user_id=actor.id, action="inventory.transaction_delete",
               object_type="inventory_transaction", object_id=tx.id,
               old_value=snapshot, new_value={"deleted": True}, source=source)
    db.delete(tx)
    db.commit()


def update_inventory_transaction(db: Session, *, transaction_id: int,
                                 delta: int, reason: str, allow_negative: bool,
                                 actor: User, source="admin_app") -> InventoryTransaction:
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "A reason is required.")
    tx = db.query(InventoryTransaction).filter_by(id=transaction_id).first()
    if not tx:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Inventory transaction not found.")
    difference = int(delta) - tx.delta
    resulting = tx.item.qty_on_hand + difference
    if resulting < 0 and not (allow_negative and actor.has_role("admin")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This edit would take {tx.item.code} to {resulting}. "
            "Enable the admin negative-stock override only when intentional.")
    old = {"delta": tx.delta, "reason": tx.reason,
           "qty_on_hand": tx.item.qty_on_hand}
    tx.item.qty_on_hand = resulting
    tx.delta = int(delta)
    tx.reason = reason
    tx.updated_at = datetime.now(timezone.utc)
    log_action(db, user_id=actor.id, action="inventory.transaction_edit",
               object_type="inventory_transaction", object_id=tx.id,
               old_value=old, new_value={"delta": tx.delta,
                                         "reason": tx.reason,
                                         "qty_on_hand": resulting},
               source=source)
    db.commit()
    db.refresh(tx)
    return tx


def create_count_request(db: Session, *, item_id: int, requester: User,
                         note: str, source="browser") -> CountRequest:
    item = db.query(Item).filter_by(id=item_id, active=True).first()
    if not item or not access_service.can_view_item(requester, item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    existing = (db.query(CountRequest)
                .filter_by(item_id=item_id, requester_user_id=requester.id,
                           status="open").first())
    if existing:
        existing.note = (note or "").strip()[:255]
        existing.created_at = datetime.now(timezone.utc)
        row = existing
        action = "count_request.refresh"
    else:
        row = CountRequest(item_id=item_id, requester_user_id=requester.id,
                           note=(note or "").strip()[:255], status="open")
        db.add(row)
        db.flush()
        action = "count_request.create"
    log_action(db, user_id=requester.id, action=action,
               object_type="count_request", object_id=row.id,
               new_value={"item_id": item_id, "note": row.note}, source=source)
    db.commit()
    db.refresh(row)
    return row


def resolve_count_request(db: Session, *, request_id: int, actor: User,
                          physical_quantity: int, resolution_note: str,
                          source="admin_app") -> CountRequest:
    row = db.query(CountRequest).filter_by(id=request_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Count request not found.")
    if row.status != "open":
        return row
    physical_quantity = int(physical_quantity)
    if physical_quantity < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Physical quantity cannot be negative.")
    item = row.item
    before = int(item.qty_on_hand)
    delta = physical_quantity - before
    item.qty_on_hand = physical_quantity
    item.inventory_counted = True
    if delta:
        db.add(InventoryTransaction(
            item_id=item.id, delta=delta,
            reason=f"Physical recount request #{row.id}: "
                   f"system {before}, counted {physical_quantity}",
            source=source, user_id=actor.id,
            item_code_snapshot=item.deleted_code or item.code,
            item_name_snapshot=item.deleted_name or item.name,
        ))
    row.status = "resolved"
    row.resolved_by_user_id = actor.id
    row.resolution_note = (resolution_note or "Physical recount completed").strip()[:255]
    row.system_qty_before = before
    row.physical_qty = physical_quantity
    row.adjustment_delta = delta
    row.resolved_at = datetime.now(timezone.utc)
    log_action(db, user_id=actor.id, action="count_request.resolve",
               object_type="count_request", object_id=row.id,
               old_value={"status": "open", "system_qty": before},
               new_value={"status": "resolved",
                          "physical_qty": physical_quantity,
                          "adjustment_delta": delta,
                          "resolution_note": row.resolution_note}, source=source)
    db.commit()
    db.refresh(row)
    return row



def ensure_location_balances(db: Session, item: Item) -> None:
    if item.location_balances:
        return
    db.add(ItemLocationBalance(item_id=item.id, location_name="On-site", quantity=item.qty_on_hand, bin_location=item.location or ""))
    db.add(ItemLocationBalance(item_id=item.id, location_name="Off-site", quantity=0, bin_location=""))
    db.flush()

def transfer_inventory(db: Session, *, item_id: int, from_location: str, to_location: str, quantity: int, reason: str, actor: User, source="admin_app") -> Item:
    item = db.query(Item).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    if from_location.strip().lower() == to_location.strip().lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose two different inventory locations.")
    ensure_location_balances(db, item)
    def getloc(name):
        row = db.query(ItemLocationBalance).filter_by(item_id=item.id, location_name=name.strip()).first()
        if not row:
            row = ItemLocationBalance(item_id=item.id, location_name=name.strip(), quantity=0)
            db.add(row); db.flush()
        return row
    src, dst = getloc(from_location), getloc(to_location)
    if src.quantity < quantity:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Only {src.quantity} available at {src.location_name}.")
    src.quantity -= quantity; dst.quantity += quantity
    log_action(db, user_id=actor.id, action="inventory.transfer", object_type="item", object_id=item.id, new_value={"from":src.location_name,"to":dst.location_name,"quantity":quantity,"reason":reason}, source=source)
    db.commit(); db.refresh(item); return item
