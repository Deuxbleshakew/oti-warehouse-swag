"""
backend/services/item_service.py — item CRUD and inventory adjustments.
Stock quantity is never edited through the plain item-update path (see
ItemUpdate schema's comment) — it only ever changes through
adjust_inventory(), so every change carries a required reason and a
logged InventoryTransaction.
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models.models import Item, InventoryTransaction, User
from backend.services.audit_service import log_action


def create_item(db: Session, *, data: dict, actor: User, source="api") -> Item:
    if db.query(Item).filter_by(code=data["code"]).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Item code {data['code']} already exists.")
    item = Item(**data)
    db.add(item)
    db.flush()
    if item.qty_on_hand:
        db.add(InventoryTransaction(item_id=item.id, delta=item.qty_on_hand,
                                    reason="Initial stock", source=source,
                                    user_id=actor.id))
    log_action(db, user_id=actor.id, action="item.create", object_type="item",
              object_id=item.id, new_value=data, source=source)
    db.commit()
    db.refresh(item)
    return item


def update_item(db: Session, *, item_id: int, data: dict, actor: User,
                source="api") -> Item:
    item = db.query(Item).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
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
                     allow_negative: bool, actor: User, source="api") -> Item:
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
    db.add(InventoryTransaction(item_id=item.id, delta=delta, reason=reason,
                                source=source, user_id=actor.id))
    log_action(db, user_id=actor.id, action="inventory.adjust",
              object_type="item", object_id=item.id,
              old_value={"qty_on_hand": old_qty},
              new_value={"qty_on_hand": resulting, "delta": delta,
                         "reason": reason}, source=source)
    db.commit()
    db.refresh(item)
    return item


# ---- item photos -------------------------------------------------------------
# Files land in frontend/assets (the same dir main.py serves at /assets) with
# a collision-proof name; DB rows in item_images keep the ordering.
import os
import re
import secrets

from backend.models.models import ItemImage
from backend.config import ASSETS_DIR

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024   # phone photos are ~2-4MB; 8 is headroom


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
