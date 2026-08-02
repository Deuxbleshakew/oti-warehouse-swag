"""
backend/auth/dependencies.py — FastAPI dependencies that gate every
protected endpoint. This is where "no security decisions in the frontend
only" actually gets enforced: every route that needs a logged-in user (or
a specific role) declares it here, and FastAPI runs this check before the
route's own code ever executes — a request with no token, an expired
token, or the wrong role never reaches business logic at all.
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.models.models import User, Session as SessionModel

SESSION_LIFETIME = timedelta(hours=12)
_bearer = HTTPBearer(auto_error=False)


def create_session(db: Session, user: User) -> SessionModel:
    token = secrets.token_hex(32)   # 64 hex chars, unguessable
    now = datetime.now(timezone.utc)
    sess = SessionModel(token=token, user_id=user.id, created_at=now,
                       expires_at=now + SESSION_LIFETIME, revoked=False)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Missing Authorization header")
    token = creds.credentials
    sess = db.query(SessionModel).filter_by(token=token).first()
    if not sess or sess.revoked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    now = datetime.now(timezone.utc)
    expires_at = sess.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = db.query(User).filter_by(id=sess.user_id).first()
    if not user or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "User not found or inactive")
    return user


def require_role(*allowed_roles: str):
    """Dependency factory: require_role('admin') or
    require_role('admin', 'approver') for endpoints that accept either."""
    def _check(user: User = Depends(get_current_user)) -> User:
        user_role_names = {r.name for r in user.roles}
        if not user_role_names.intersection(allowed_roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of: {', '.join(allowed_roles)}")
        return user
    return _check
