from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.models.enums import Role
from app.auth.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_optional = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def user_from_token(token: str, db: Session) -> User:
    try:
        payload = decode_token(token)
        user = db.get(User, int(payload["sub"]))
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    return user_from_token(token, db)


def get_optional_user(token: Optional[str] = Depends(oauth2_optional),
                      db: Session = Depends(get_db)) -> Optional[User]:
    return user_from_token(token, db) if token else None


def require_roles(*roles: Role):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user
    return checker


def require_approved_organizer(user: User = Depends(require_roles(Role.ORGANIZER))) -> User:
    if not user.is_approved:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organizer account is not approved by Admin yet")
    return user
