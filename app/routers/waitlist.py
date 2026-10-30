from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth.deps import get_current_user, require_roles
from app.database import get_db
from app.models import Booking, Show, Waitlist
from app.models.enums import BookingStatus, Role, ShowStatus, WaitlistStatus
from app.redis_client import get_redis
from app.schemas.booking import WaitlistIn
from app.services.seat_service import build_seat_map
from app.utils.pagination import Page
from app.utils.time import now

router = APIRouter(tags=["Waitlist"])


def _w(w: Waitlist) -> dict:
    return {"id": w.id, "show_id": w.show_id, "customer_id": w.customer_id, "customer_name": w.customer.name,
            "seat_count": w.seat_count, "category": w.category, "status": w.status, "joined_at": w.joined_at}


@router.post("/shows/{show_id}/waitlist", status_code=201)
def join(show_id: int, body: WaitlistIn, db: Session = Depends(get_db),
         user=Depends(require_roles(Role.CUSTOMER))):
    show = db.get(Show, show_id)
    if not show:
        raise HTTPException(404, "Show not found")
    if show.status != ShowStatus.SCHEDULED or now() >= show.booking_closes_at:
        raise HTTPException(400, "Booking is closed for this show")
    if any(s["status"] == "Available" for s in build_seat_map(db, get_redis(), show)):
        raise HTTPException(400, "Show is not sold out; you can book seats directly")
    if db.query(Booking.id).filter(Booking.show_id == show_id, Booking.customer_id == user.id,
                                   Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING])).first():
        raise HTTPException(409, "You have already booked this show")
    w = db.query(Waitlist).filter(Waitlist.show_id == show_id, Waitlist.customer_id == user.id).first()
    if w and w.status == WaitlistStatus.WAITING:
        raise HTTPException(409, "You are already on the waitlist for this show")
    if w:  # previously notified: allow re-joining
        w.seat_count, w.category, w.status, w.joined_at, w.notified_at = (
            body.seat_count, body.category, WaitlistStatus.WAITING, now(), None)
    else:
        w = Waitlist(show_id=show_id, customer_id=user.id, seat_count=body.seat_count, category=body.category,
                     status=WaitlistStatus.WAITING, joined_at=now())
        db.add(w)
    db.commit()
    db.refresh(w)
    return _w(w)


@router.get("/shows/{show_id}/waitlist")
def list_waitlist(show_id: int, page: Page = Depends(), db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    show = db.get(Show, show_id)
    if not show:
        raise HTTPException(404, "Show not found")
    q = db.query(Waitlist).filter(Waitlist.show_id == show_id)
    if user.role == Role.CUSTOMER:
        q = q.filter(Waitlist.customer_id == user.id)
    elif user.role == Role.ORGANIZER:
        if show.event.organizer_id != user.id:
            raise HTTPException(403, "Not your show")
    elif user.role != Role.ADMIN:
        raise HTTPException(403, "Insufficient permissions")
    total = q.count()
    items = q.order_by(Waitlist.joined_at, Waitlist.id).offset(page.skip).limit(page.limit).all()
    return {"total": total, "skip": page.skip, "limit": page.limit, "items": [_w(w) for w in items]}


@router.delete("/waitlist/{waitlist_id}")
def leave(waitlist_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    w = db.get(Waitlist, waitlist_id)
    if not w:
        raise HTTPException(404, "Waitlist entry not found")
    if not (user.role == Role.ADMIN or (user.role == Role.CUSTOMER and w.customer_id == user.id)):
        raise HTTPException(403, "You can remove only your own waitlist entry")
    db.delete(w)
    db.commit()
    return {"message": "Removed from waitlist"}
