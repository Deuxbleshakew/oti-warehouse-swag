"""Authenticated catalog, favorites, reusable projects, and item photos."""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.db.session import get_db
from backend.schemas.schemas import ItemOut, ProjectOut, PortalRequestCreate
from backend.models.models import (
    Item, ItemImage, ItemImageBlob, Project, User, CountRequest, UserFavorite,
    Kit, KitImage, Notification, AppSetting, PortalRequest,
)
from backend.auth.dependencies import get_current_user, get_current_user_flexible
from backend.services.item_service import resolve_stored_image_path, image_content_type
from backend.services import access_service

router = APIRouter(tags=["catalog"])


def _serve_image_record(image: ItemImage, db: Session):
    if image.blob is not None:
        return Response(content=image.blob.content,
                        media_type=image.blob.content_type,
                        headers={"Cache-Control": "private, max-age=86400"})
    path = resolve_stored_image_path(image.filename)
    if path:
        with open(path, "rb") as fh:
            content = fh.read()
        content_type = image_content_type(path)
        try:
            db.add(ItemImageBlob(image_id=image.id, content=content,
                                 content_type=content_type))
            db.commit()
        except Exception:
            db.rollback()
        return Response(content=content, media_type=content_type,
                        headers={"Cache-Control": "private, max-age=86400"})
    parsed = urlparse((image.filename or "").strip())
    if parsed.scheme in ("http", "https"):
        return RedirectResponse(image.filename)
    raise HTTPException(status.HTTP_404_NOT_FOUND,
                        "This image file is missing. Re-upload it once.")


@router.get("/item-images/{image_id}", include_in_schema=False)
def get_item_image(image_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user_flexible)):
    image = db.query(ItemImage).filter_by(id=image_id).first()
    if not image or not access_service.can_view_item(user, image.item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item image not found.")
    return _serve_image_record(image, db)


@router.get("/catalog", response_model=list[ItemOut])
def list_catalog(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    counts = dict(db.query(CountRequest.item_id, func.count(CountRequest.id))
                  .filter(CountRequest.status == "open")
                  .group_by(CountRequest.item_id).all())
    favorite_ids = {row.item_id for row in db.query(UserFavorite).filter_by(
        user_id=user.id).all()}
    items = access_service.visible_items_query(db, user).order_by(Item.name).all()
    return [ItemOut.from_orm_item(i, open_count_requests=counts.get(i.id, 0),
                                  favorite=i.id in favorite_ids)
            for i in items]


@router.get("/catalog/{item_id}/image", include_in_schema=False)
def get_catalog_item_image(item_id: int, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user_flexible)):
    item = db.query(Item).filter_by(id=item_id).first()
    if not item or not item.images or not access_service.can_view_item(user, item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item image not found.")
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
                     user: User = Depends(get_current_user)):
    item = db.query(Item).filter_by(id=item_id).first()
    if not item or not access_service.can_view_item(user, item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    favorite = db.query(UserFavorite).filter_by(user_id=user.id,
                                                item_id=item.id).first() is not None
    return ItemOut.from_orm_item(item, favorite=favorite)


@router.post("/catalog/{item_id}/favorite", status_code=204)
def add_favorite(item_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    item = db.query(Item).filter_by(id=item_id).first()
    if not item or not access_service.can_view_item(user, item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    if not db.query(UserFavorite).filter_by(user_id=user.id, item_id=item_id).first():
        db.add(UserFavorite(user_id=user.id, item_id=item_id))
        db.commit()
    return None


@router.delete("/catalog/{item_id}/favorite", status_code=204)
def remove_favorite(item_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    row = db.query(UserFavorite).filter_by(user_id=user.id, item_id=item_id).first()
    if row:
        db.delete(row)
        db.commit()
    return None


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    projects = access_service.visible_projects_query(db, user).order_by(Project.name).all()
    return [ProjectOut.model_validate(p) for p in projects]



@router.get("/kit-images/{kit_id}", include_in_schema=False)
def get_kit_image(kit_id:int, db:Session=Depends(get_db), user:User=Depends(get_current_user_flexible)):
    k=db.query(Kit).filter_by(id=kit_id,active=True).first()
    if not k or not k.image: raise HTTPException(status.HTTP_404_NOT_FOUND,"Kit image not found.")
    # A kit is visible only when every component is visible to this user.
    if any(not access_service.can_view_item(user,c.item) for c in k.components):
        raise HTTPException(status.HTTP_404_NOT_FOUND,"Kit image not found.")
    return Response(content=k.image.content,media_type=k.image.content_type,headers={"Cache-Control":"private, max-age=3600"})

@router.get("/catalog-kits")
def catalog_kits(db: Session=Depends(get_db), user: User=Depends(get_current_user)):
    result=[]
    for k in db.query(Kit).filter_by(active=True).order_by(Kit.name):
        comps=[]; buildable=None
        for c in sorted(k.components,key=lambda x:x.position):
            if not access_service.can_view_item(user,c.item): break
            possible=max(0,c.item.qty_on_hand)//max(1,c.quantity)
            buildable=possible if buildable is None else min(buildable,possible)
            comps.append({"item_id":c.item_id,"item_code":c.item.code,"item_name":c.item.name,"quantity":c.quantity,"position":c.position,"image_id":c.item.images[0].id if c.item.images else None})
        else:
            result.append({"id":k.id,"name":k.name,"code":k.code,"description":k.description or "","custom":k.custom,"buildable_quantity":buildable or 0,"image_available":bool(k.image),"components":comps})
    return result

@router.get("/notifications")
def my_notifications(db:Session=Depends(get_db), user:User=Depends(get_current_user)):
    rows=db.query(Notification).filter_by(user_id=user.id).order_by(Notification.created_at.desc()).limit(100).all()
    return [{"id":n.id,"kind":n.kind,"title":n.title,"message":n.message or "","object_type":n.object_type or "","object_id":n.object_id,"read":n.read_at is not None,"created_at":n.created_at.isoformat()} for n in rows]

@router.post("/notifications/{notification_id}/read", status_code=204)
def read_notification(notification_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    from datetime import datetime, timezone
    n=db.query(Notification).filter_by(id=notification_id,user_id=user.id).first()
    if n:n.read_at=datetime.now(timezone.utc);db.commit()


@router.get("/announcement")
def active_announcement(db: Session=Depends(get_db), user:User=Depends(get_current_user)):
    msg=db.query(AppSetting).filter_by(key="announcement_message").first()
    active=db.query(AppSetting).filter_by(key="announcement_active").first()
    is_active=bool(active and (active.value or "").lower()=="true")
    return {"message": (msg.value if msg else "") if is_active else "", "active": is_active}

@router.post("/admin-requests/{request_type}", status_code=201)
def create_admin_portal_request(request_type:str, body:PortalRequestCreate, db:Session=Depends(get_db), user:User=Depends(get_current_user)):
    if not user.has_role("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN,"Admin access required.")
    if request_type not in {"new_user","new_item"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,"Unknown request type.")
    row=PortalRequest(request_type=request_type,requested_by_user_id=user.id,title=body.title.strip(),details=body.details.strip(),status="open")
    db.add(row);db.commit();db.refresh(row)
    return {"id":row.id,"request_type":row.request_type,"title":row.title,"details":row.details,"status":row.status,"created_at":row.created_at}

@router.get("/admin-requests")
def my_admin_portal_requests(db:Session=Depends(get_db), user:User=Depends(get_current_user)):
    if not user.has_role("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN,"Admin access required.")
    rows=db.query(PortalRequest).order_by(PortalRequest.created_at.desc()).limit(100).all()
    return [{"id":r.id,"request_type":r.request_type,"requester":r.requester.full_name or r.requester.username,"title":r.title,"details":r.details or "","status":r.status,"created_at":r.created_at} for r in rows]
