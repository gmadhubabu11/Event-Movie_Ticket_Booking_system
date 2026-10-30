from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.auth.deps import get_current_user
from app.auth.security import hash_password, verify_password, create_access_token
from app.config import settings
from app.database import get_db
from app.models import User
from app.models.enums import Role
from app.schemas.auth import RegisterIn, UserOut, TokenOut
from app.utils.rate_limit import hit

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "Email already registered")
    role = Role(body.role)
    user = User(name=body.name, email=email, phone=body.phone, password_hash=hash_password(body.password),
                role=role, is_active=True, is_approved=(role == Role.CUSTOMER))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """In Swagger 'Authorize', put the email in the username field."""
    ip = request.client.host if request.client else "unknown"
    hit(f"rl:login:{ip}", settings.LOGIN_RATE_LIMIT)
    user = db.query(User).filter(User.email == form.username.lower()).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    if not user.is_active:
        raise HTTPException(403, "Account is inactive")
    return TokenOut(access_token=create_access_token(user.id, user.role.value), role=user.role.value)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
