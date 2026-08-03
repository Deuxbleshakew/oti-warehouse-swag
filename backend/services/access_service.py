"""Central permission checks for catalog items and shared projects.

The browser never decides access on its own. Every catalog list, direct item
lookup, order submission, project list, order view, and proof-photo request
calls this module so restricted content cannot be recovered through a crafted
API request.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from backend.models.models import (
    CatalogPermission, Item, Project, ProjectMember, User,
)


def is_privileged(user: User) -> bool:
    return user.has_role("admin") or user.has_role("approver")


def normalized(value: str | None) -> str:
    return " ".join((value or "").split()).strip().casefold()


def catalog_permission_sets(user: User) -> dict[str, set[str]]:
    result = {"item": set(), "category": set(), "brand": set()}
    for row in user.catalog_permissions:
        if row.scope_type in result:
            result[row.scope_type].add(normalized(row.scope_value))
    return result


def can_view_item(user: User, item: Item) -> bool:
    if is_privileged(user) or (user.catalog_access_mode or "all") == "all":
        return item.active and item.deleted_at is None
    rules = catalog_permission_sets(user)
    return bool(
        str(item.id) in rules["item"]
        or normalized(item.category) in rules["category"]
        or normalized(item.brand) in rules["brand"]
    ) and item.active and item.deleted_at is None


def visible_items_query(db: Session, user: User):
    q = db.query(Item).filter(Item.active.is_(True), Item.deleted_at.is_(None))
    if is_privileged(user) or (user.catalog_access_mode or "all") == "all":
        return q
    rows = db.query(CatalogPermission).filter_by(user_id=user.id).all()
    item_ids = []
    categories = []
    brands = []
    for row in rows:
        if row.scope_type == "item" and str(row.scope_value).isdigit():
            item_ids.append(int(row.scope_value))
        elif row.scope_type == "category":
            categories.append(normalized(row.scope_value))
        elif row.scope_type == "brand":
            brands.append(normalized(row.scope_value))
    clauses = []
    if item_ids:
        clauses.append(Item.id.in_(item_ids))
    if categories:
        clauses.append(func.lower(func.trim(Item.category)).in_(categories))
    if brands:
        clauses.append(func.lower(func.trim(Item.brand)).in_(brands))
    if not clauses:
        return q.filter(Item.id == -1)
    return q.filter(or_(*clauses))


def project_membership(db: Session, project_id: int, user_id: int) -> ProjectMember | None:
    return db.query(ProjectMember).filter_by(project_id=project_id,
                                              user_id=user_id).first()


def can_view_project(db: Session, user: User, project: Project | None) -> bool:
    if project is None or is_privileged(user):
        return True
    if not bool(getattr(project, "access_restricted", False)):
        return True
    return project_membership(db, project.id, user.id) is not None


def can_edit_project_order(db: Session, user: User, project: Project | None) -> bool:
    if is_privileged(user):
        return True
    if project is None:
        return False
    membership = project_membership(db, project.id, user.id)
    return bool(membership and membership.access_level in {"owner", "editor"})


def visible_projects_query(db: Session, user: User):
    q = db.query(Project).filter(Project.active.is_(True))
    if is_privileged(user):
        return q
    member_project_ids = db.query(ProjectMember.project_id).filter(
        ProjectMember.user_id == user.id)
    return q.filter(or_(Project.access_restricted.is_(False),
                        Project.access_restricted.is_(None),
                        Project.id.in_(member_project_ids)))


def ensure_project_owner(db: Session, project: Project, user: User) -> ProjectMember:
    row = project_membership(db, project.id, user.id)
    if row is None:
        row = ProjectMember(project_id=project.id, user_id=user.id,
                            access_level="owner")
        db.add(row)
    elif row.access_level != "owner":
        row.access_level = "owner"
    return row
