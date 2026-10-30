from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth.deps import require_roles
from app.auth.security import hash_password
from app.database import get_db
from app.models import User, Venue
from app.models.enums import Role
from app.schemas.auth import StaffCreate, UserOut

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.put("/organizers/{user_id}/approve", response_model=UserOut)
def approve_organizer(user_id: int, db: Session = Depends(get_db), _=Depends(require_roles(Role.ADMIN))):
    user = db.get(User, user_id)
    if not user or user.role != Role.ORGANIZER:
        raise HTTPException(404, "Organizer not found")
    user.is_approved = True
    db.commit()
    db.refresh(user)
    return user


@router.post("/staff", response_model=UserOut, status_code=201)
def create_staff(body: StaffCreate, db: Session = Depends(get_db), _=Depends(require_roles(Role.ADMIN))):
    if not db.get(Venue, body.venue_id):
        raise HTTPException(404, "Venue not found")
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "Email already registered")
    user = User(name=body.name, email=email, phone=body.phone, password_hash=hash_password(body.password),
                role=Role.STAFF, is_active=True, is_approved=True, venue_id=body.venue_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
