"""
backend/api/catalog.py — catalog browsing, reusable projects, and item photos.

Catalog data and reusable project names require authentication. Item photos are
served by a small public endpoint because ordinary <img> tags cannot attach the
app's bearer token. This is no broader than the existing /assets static mount,
but it lets the backend repair legacy image values such as "assets/foo.png",
"/assets/foo.png", Windows-style paths, or full asset URLs.
"""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.db.session import get_db
from backend.schemas.schemas import ItemOut, ProjectOut
from backend.models.models import (Item, ItemImage, ItemImageBlob, Project, User,
                                   CountRequest)
from backend.auth.dependencies import get_current_user
from backend.services.item_service import (resolve_stored_image_path,
                                           image_content_type)

router = APIRouter(tags=["catalog"])


def _serve_image_record(image: ItemImage, db: Session):
    """Serve DB-backed bytes first, then migrate a surviving legacy file.

    Reading a legacy disk image opportunistically stores it in the database so
    the next cloud restart cannot erase it. Missing legacy files still require a
    one-time re-upload because no code can reconstruct bytes that are gone.
    """
    if image.blob is not None:
        return Response(content=image.blob.content,
                        media_type=image.blob.content_type,
                        headers={"Cache-Control": "public, max-age=86400"})

    path = resolve_stored_image_path(image.filename)
    if path:
        with open(path, "rb") as fh:
            content = fh.read()
        content_type = image_content_type(path)
        # Backfill only once. A racing request may insert first; in that rare
        # case rollback the duplicate and still serve the bytes we just read.
        try:
            db.add(ItemImageBlob(image_id=image.id, content=content,
                                 content_type=content_type))
            db.commit()
        except Exception:
            db.rollback()
        return Response(content=content, media_type=content_type,
                        headers={"Cache-Control": "public, max-age=86400"})

    parsed = urlparse((image.filename or "").strip())
    if parsed.scheme in ("http", "https"):
        return RedirectResponse(image.filename)

    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        "The image record exists, but its file is missing. Re-upload this photo once to store it permanently.",
    )


@router.get("/item-images/{image_id}", include_in_schema=False)
def get_item_image(image_id: int, db: Session = Depends(get_db)):
    image = db.query(ItemImage).filter_by(id=image_id).first()
    if not image:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item image not found.")
    return _serve_image_record(image, db)


@router.get("/catalog", response_model=list[ItemOut])
def list_catalog(db: Session = Depends(get_db),
                 _user: User = Depends(get_current_user)):
    counts = dict(db.query(CountRequest.item_id, func.count(CountRequest.id))
                  .filter(CountRequest.status == "open")
                  .group_by(CountRequest.item_id).all())
    items = (db.query(Item)
             .filter(Item.active.is_(True), Item.deleted_at.is_(None))
             .order_by(Item.name).all())
    return [ItemOut.from_orm_item(i, open_count_requests=counts.get(i.id, 0))
            for i in items]


@router.get("/catalog/{item_id}/image", include_in_schema=False)
def get_catalog_item_image(item_id: int, db: Session = Depends(get_db)):
    """Serve the first photo for an active catalog item.

    The route uses the item ID instead of exposing a fragile stored path to the
    browser. It also repairs common legacy path formats before resolving the
    file. A remote URL is redirected only when that exact URL is stored on the
    item's image record.
    """
    item = (db.query(Item).filter(Item.id == item_id, Item.active.is_(True),
                                  Item.deleted_at.is_(None)).first())
    if not item or not item.images:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item image not found.")

    # Older builds can leave a filename record behind after its local file is
    # gone. Try later photos too, so re-uploading a replacement immediately
    # repairs the catalog even before the stale record is manually removed.
    last_missing = None
    for image in item.images:
        try:
            return _serve_image_record(image, db)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            last_missing = exc
    raise last_missing or HTTPException(status.HTTP_404_NOT_FOUND,
                                        "Item image not found.")


@router.get("/catalog/{item_id}", response_model=ItemOut)
def get_catalog_item(item_id: int, db: Session = Depends(get_db),
                     _user: User = Depends(get_current_user)):
    item = (db.query(Item).filter(Item.id == item_id, Item.active.is_(True),
                                  Item.deleted_at.is_(None)).first())
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    return ItemOut.from_orm_item(item)


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db),
                  _user: User = Depends(get_current_user)):
    """Only reusable projects/events appear in the picker.

    One-time projects are still stored and linked to their order for history,
    but are created with active=False and therefore do not clutter this list.
    """
    projects = (db.query(Project).filter_by(active=True)
               .order_by(Project.name).all())
    return [ProjectOut.model_validate(p) for p in projects]
