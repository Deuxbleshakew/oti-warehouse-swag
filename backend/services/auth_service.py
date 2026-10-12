"""
backend/services/auth_service.py — login. Everything else auth-related
(session verification, role checks) lives in backend/auth/dependencies.py
since those run as FastAPI dependencies, not as callable service functions.
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models.models import User
from backend.auth.security import verify_password
from backend.auth.dependencies import create_session
from backend.services.audit_service import log_action


def login(db: Session, *, username: str, password: str, source: str = "api"):
    user = db.query(User).filter_by(username=username).first()
    if not user or not user.active or not verify_password(password,
                                                            user.password_hash):
        # deliberately identical error for "no such user" and "wrong
        # password" — don't reveal which one it was
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Invalid username or password.")
    session = create_session(db, user)
    log_action(db, user_id=user.id, action="auth.login", object_type="user",
              object_id=user.id, source=source)
    db.commit()
    return session, user
