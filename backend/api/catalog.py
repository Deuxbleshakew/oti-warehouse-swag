"""Authenticated catalog, favorites, reusable projects, and item photos."""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.db.session import get_db
from backend.schemas.schemas import ItemOut, ProjectOut, PortalRequestCreate, KitCreate
from backend.models.models import (
    Item, ItemImage, ItemImageBlob, Project, User, CountRequest, UserFavorite,
    Kit, KitImage, KitComponent, Notification, AppSetting, PortalRequest, Item,
)
from backend.auth.dependencies import get_current_user, get_current_user_flexible
from backend.services.item_service import resolve_stored_image_path, image_content_type
from backend.services import access_service
from backend.services.v111_service import item_out_kwargs, refresh_kit_review, notify_admins

router = APIRouter(tags=["catalog"])


def _serve_image_record(image: ItemImage, db: Session):
    if image.blob is not None:
        return Response(content=image.blob.content,
                        media_type=image.blob.content_type,
                        headers={"Cache-Control": "private, no-store, max-age=0", "Pragma": "no-cache"})
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
                        headers={"Cache-Control": "private, no-store, max-age=0", "Pragma": "no-cache"})
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


def _latest_count_map(db: Session, item_ids: list[int]):
    result = {}
    if not item_ids:
        return result
    rows = (db.query(CountRequest)
            .filter(CountRequest.item_id.in_(item_ids), CountRequest.status == "resolved")
            .order_by(CountRequest.item_id, CountRequest.resolved_at.desc()).all())
    for row in rows:
        if row.item_id not in result:
            result[row.item_id] = row
    return result


@router.get("/directory/users")
def user_directory(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (db.query(User).filter(User.active.is_(True), User.deleted_at.is_(None))
            .order_by(User.full_name, User.username).all())
    return [{"id": row.id, "name": row.full_name or row.username, "username": row.username}
            for row in rows if row.id != user.id]


@router.get("/catalog", response_model=list[ItemOut])
def list_catalog(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    counts = dict(db.query(CountRequest.item_id, func.count(CountRequest.id))
                  .filter(CountRequest.status == "open")
                  .group_by(CountRequest.item_id).all())
    favorite_ids = {row.item_id for row in db.query(UserFavorite).filter_by(
        user_id=user.id).all()}
    items = access_service.visible_items_query(db, user).order_by(Item.name).all()
    latest = _latest_count_map(db, [i.id for i in items])
    pending, subs = item_out_kwargs(db, items)
    return [ItemOut.from_orm_item(
                i, open_count_requests=counts.get(i.id, 0),
                favorite=i.id in favorite_ids,
                pending_qty=pending.get(i.id, 0), substitute=subs.get(i.id),
                last_count_at=(latest[i.id].resolved_at if i.id in latest else None),
                last_count_quantity=(latest[i.id].physical_qty if i.id in latest else None),
                last_count_location=((latest[i.id].inventory_location or "") if i.id in latest else ""))
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
    latest = _latest_count_map(db, [item.id]).get(item.id)
    pending, subs = item_out_kwargs(db, [item])
    return ItemOut.from_orm_item(item, favorite=favorite,
                                 pending_qty=pending.get(item.id, 0), substitute=subs.get(item.id),
                                 last_count_at=(latest.resolved_at if latest else None),
                                 last_count_quantity=(latest.physical_qty if latest else None),
                                 last_count_location=((latest.inventory_location or "") if latest else ""))


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
        if (getattr(k, "review_status", "live") or "live") != "live":
            continue
        comps=[]; buildable=None; required_by_item={}
        invalid=False
        for c in sorted(k.components,key=lambda x:x.position):
            if not access_service.can_view_item(user,c.item): invalid=True; break
            required_by_item[c.item_id]=required_by_item.get(c.item_id,0)+max(1,c.quantity)
            comps.append({"item_id":c.item_id,"item_code":c.item.code,"item_name":c.item.name,"quantity":c.quantity,"position":c.position,"image_id":c.item.images[0].id if c.item.images else None})
        if invalid: continue
        for item_id,required in required_by_item.items():
            item=next(c.item for c in k.components if c.item_id==item_id)
            possible=max(0,item.qty_on_hand)//max(1,required)
            buildable=possible if buildable is None else min(buildable,possible)
        result.append({"id":k.id,"name":k.name,"code":k.code,"brand":k.brand or "","description":k.description or "","custom":k.custom,"kit_type":getattr(k,"kit_type","standard") or "standard","design_json":getattr(k,"design_json","") or "","buildable_quantity":buildable or 0,"image_available":bool(k.image),"created_by_user_id":k.created_by_user_id,"components":comps})
    return result

@router.post("/folder-kits", status_code=201)
def create_folder_kit(body: KitCreate, db: Session=Depends(get_db), user: User=Depends(get_current_user)):
    code=body.code.strip()
    if db.query(Kit).filter(func.lower(Kit.code)==code.lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT,"That kit/item code already exists.")
    if (body.kit_type or "folder") != "folder":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,"This endpoint creates Folder Kits only.")
    kit=Kit(name=body.name.strip(),code=code,brand=body.brand.strip(),description=body.description or "",active=True,custom=True,saved_for_reuse=True,kit_type="folder",design_json=body.design_json or "",created_by_user_id=user.id)
    db.add(kit); db.flush()
    if not body.components:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,"Add at least one catalog item to the folder.")
    for i,c in enumerate(body.components):
        item=db.query(Item).filter_by(id=c.item_id,active=True).first()
        if not item or not can_use_in_folder(user,item):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,f"Item {c.item_id} is not available.")
        db.add(KitComponent(kit_id=kit.id,item_id=item.id,quantity=c.quantity,position=c.position if c.position is not None else i))
    db.flush(); db.refresh(kit)
    state=refresh_kit_review(db,kit)
    if state!="live":
        notify_admins(db,"kit_pending",f"Folder kit {kit.code} waiting on item review",
                      f"{user.full_name or user.username} saved a folder kit that uses items still pending review.","kit",kit.id)
    db.commit(); db.refresh(kit)
    return {"id":kit.id,"name":kit.name,"code":kit.code,"kit_type":"folder","review_status":kit.review_status}


def can_use_in_folder(user: User, item: Item) -> bool:
    """Live items the user can see, plus pending items the user submitted."""
    if access_service.can_view_item(user, item):
        return True
    pending = (getattr(item, "review_status", "live") or "live") == "pending_review"
    return bool(pending and item.deleted_at is None and
                (item.created_by_user_id == user.id or user.has_role("admin")))

@router.post("/folder-kits/{kit_id}/image", status_code=204)
async def upload_folder_kit_image(kit_id:int,file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    kit=db.query(Kit).filter_by(id=kit_id,kit_type="folder").first()
    if not kit or (kit.created_by_user_id!=user.id and not user.has_role("admin")):
        raise HTTPException(status.HTTP_404_NOT_FOUND,"Folder Kit not found.")
    content=await file.read()
    if not content or len(content)>10*1024*1024: raise HTTPException(status.HTTP_400_BAD_REQUEST,"Image must be between 1 byte and 10 MB.")
    ctype=(file.content_type or "image/png").lower()
    if ctype not in {"image/jpeg","image/png","image/webp","image/gif"}: raise HTTPException(status.HTTP_400_BAD_REQUEST,"Use JPG, PNG, GIF, or WebP.")
    if kit.image:
        kit.image.filename=(file.filename or "folder-kit.png")[:255];kit.image.content_type=ctype;kit.image.content=content
    else:
        db.add(KitImage(kit_id=kit.id,filename=(file.filename or "folder-kit.png")[:255],content_type=ctype,content=content))
    db.commit(); return None

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
