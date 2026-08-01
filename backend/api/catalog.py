"""
backend/api/catalog.py — browsing the item catalog and the project list.
Read-only for any authenticated user; editing goes through /admin/items
(items) — there's no admin write path for projects yet, since nothing
outside this router creates them today (seed_demo.py inserts them
directly). Add a POST here once something needs to create projects
through the API.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.schemas.schemas import ItemOut, ProjectOut
from backend.models.models import Item, Project, User
from backend.auth.dependencies import get_current_user

router = APIRouter(tags=["catalog"])


@router.get("/catalog", response_model=list[ItemOut])
def list_catalog(db: Session = Depends(get_db),
                 _user: User = Depends(get_current_user)):
    items = db.query(Item).filter_by(active=True).order_by(Item.name).all()
    return [ItemOut.from_orm_item(i) for i in items]


@router.get("/catalog/{item_id}", response_model=ItemOut)
def get_catalog_item(item_id: int, db: Session = Depends(get_db),
                     _user: User = Depends(get_current_user)):
    item = db.query(Item).filter_by(id=item_id, active=True).first()
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    return ItemOut.from_orm_item(item)


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db),
                  _user: User = Depends(get_current_user)):
    """Active projects/events, for tagging an order to one at submit time.
    Any authenticated user can read this list — it's just a picker, not
    sensitive data."""
    projects = (db.query(Project).filter_by(active=True)
               .order_by(Project.name).all())
    return [ProjectOut.model_validate(p) for p in projects]
