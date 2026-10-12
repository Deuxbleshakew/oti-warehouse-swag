"""
backend/api/auth.py — login/logout/me.
"""
from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.schemas.schemas import (LoginRequest, TokenResponse, UserOut,
                                     UserPreferencesOut, UserPreferencesUpdate)
from backend.services import auth_service
from backend.auth.dependencies import get_current_user
from backend.models.models import Session as SessionModel, User, UserFavorite

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    session, user = auth_service.login(db, username=body.username,
                                       password=body.password)
    return TokenResponse(token=session.token, expires_at=session.expires_at,
                         user=UserOut.from_orm_user(user))


@router.post("/auth/logout")
def logout(db: Session = Depends(get_db),
          _user: User = Depends(get_current_user),
          creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    sess = db.query(SessionModel).filter_by(token=creds.credentials).first()
    if sess:
        sess.revoked = True
        db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.from_orm_user(user)


@router.get("/me/preferences", response_model=UserPreferencesOut)
def preferences(db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    favorite_ids = [row.item_id for row in db.query(UserFavorite).filter_by(
        user_id=user.id).all()]
    return UserPreferencesOut(theme=user.theme or "warehouse-dark",
                              favorite_item_ids=favorite_ids)


@router.put("/me/preferences", response_model=UserPreferencesOut)
def update_preferences(body: UserPreferencesUpdate,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    user.theme = body.theme
    db.commit()
    return UserPreferencesOut(
        theme=user.theme,
        favorite_item_ids=[row.item_id for row in db.query(UserFavorite).filter_by(
            user_id=user.id).all()],
    )
