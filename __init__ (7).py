"""
backend/services/user_service.py — admin user management.
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from backend.models.models import User, Role
from backend.auth.security import hash_password
from backend.services.audit_service import log_action


def _resolve_roles(db: Session, role_names: list) -> list:
    roles = []
    for name in role_names:
        role = db.query(Role).filter_by(name=name).first()
        if not role:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Unknown role: {name}")
        roles.append(role)
    return roles


def create_user(db: Session, *, username: str, full_name: str, password: str,
                role_names: list, actor: User, source="api") -> User:
    if db.query(User).filter_by(username=username).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Username {username} already exists.")
    user = User(username=username, full_name=full_name,
               password_hash=hash_password(password), active=True)
    user.roles = _resolve_roles(db, role_names)
    db.add(user)
    db.flush()
    log_action(db, user_id=actor.id, action="user.create", object_type="user",
              object_id=user.id,
              new_value={"username": username, "roles": role_names},
              source=source)
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, *, user_id: int, full_name=None, active=None,
                role_names=None, password=None, actor: User,
                source="api") -> User:
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    old = {"full_name": user.full_name, "active": user.active,
          "roles": [r.name for r in user.roles]}
    if full_name is not None:
        user.full_name = full_name
    if active is not None:
        user.active = active
    if role_names is not None:
        user.roles = _resolve_roles(db, role_names)
    if password:
        user.password_hash = hash_password(password)
    new = {"full_name": user.full_name, "active": user.active,
          "roles": [r.name for r in user.roles],
          "password_changed": bool(password)}
    log_action(db, user_id=actor.id, action="user.update", object_type="user",
              object_id=user.id, old_value=old, new_value=new, source=source)
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, *, user_id: int, actor: User, source="api") -> None:
    """History-preserving deletion.

    Foreign-key history remains attributable, but the account disappears from
    user management, cannot authenticate, and its former username becomes
    available for reuse.
    """
    if user_id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "You cannot delete the account you are currently using.")
    user = db.query(User).filter_by(id=user_id).first()
    if not user or user.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    now = datetime.now(timezone.utc)
    old = {"username": user.username, "full_name": user.full_name,
           "roles": [role.name for role in user.roles]}
    original_label = user.full_name or user.username
    suffix = now.strftime("%Y%m%d%H%M%S")
    user.username = (f"deleted-{user.id}-{suffix}")[:60]
    user.full_name = (f"{original_label} [Deleted User]")[:120]
    user.password_hash = "!deleted-account-no-login!"
    user.active = False
    user.deleted_at = now
    user.roles = []
    for session in user.sessions:
        session.revoked = True
    log_action(db, user_id=actor.id, action="user.delete", object_type="user",
               object_id=user_id, old_value=old,
               new_value={"deleted": True, "historical_label": user.full_name},
               source=source)
    db.commit()

