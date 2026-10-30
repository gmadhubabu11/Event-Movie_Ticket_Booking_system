from datetime import date, datetime, time
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.auth.deps import get_current_user, require_roles
from app.database import get_db
from app.models import Booking, Event, Show
from app.models.enums import BookingStatus, Role
from app.redis_client import get_redis
from app.schemas.booking import BookingIn, PayIn
from app.services.booking_service import cancel_booking, create_booking, pay_booking
from app.services.serializers import payment_to_dict, refund_to_dict, serialize_booking
from app.utils.pagination import Page

router = APIRouter(tags=["Bookings & Payments"])


def load_booking(db: Session, booking_id: int, user) -> Booking:
    b = db.get(Booking, booking_id)
    if not b:
        raise HTTPException(404, "Booking not found")
    if user.role == Role.ADMIN:
        return b
    if user.role == Role.CUSTOMER and b.customer_id == user.id:
        return b
    if user.role == Role.ORGANIZER and b.show.event.organizer_id == user.id:
        return b
    raise HTTPException(403, "You cannot access this booking")


@router.post("/bookings", status_code=201)
def book(body: BookingIn, db: Session = Depends(get_db), user=Depends(require_roles(Role.CUSTOMER))):
    return serialize_booking(create_booking(db, get_redis(), user, body.show_id, body.seat_ids))


@router.get("/bookings")
def list_bookings(
    status_filter: Optional[BookingStatus] = Query(None, alias="status"),
    show_id: Optional[int] = None, from_date: Optional[date] = None, to_date: Optional[date] = None,
    page: Page = Depends(), db: Session = Depends(get_db), user=Depends(get_current_user),
):
    q = db.query(Booking)
    if user.role == Role.CUSTOMER:
        q = q.filter(Booking.customer_id == user.id)
    elif user.role == Role.ORGANIZER:
        q = q.join(Show, Show.id == Booking.show_id).join(Event, Event.id == Show.event_id) \
             .filter(Event.organizer_id == user.id)
    elif user.role != Role.ADMIN:
        raise HTTPException(403, "Insufficient permissions")
    if status_filter:
        q = q.filter(Booking.status == status_filter)
    if show_id:
        q = q.filter(Booking.show_id == show_id)
    if from_date:
        q = q.filter(Booking.created_at >= datetime.combine(from_date, time.min))
    if to_date:
        q = q.filter(Booking.created_at <= datetime.combine(to_date, time.max))
    total = q.count()
    items = q.order_by(Booking.id.desc()).offset(page.skip).limit(page.limit).all()
    return {"total": total, "skip": page.skip, "limit": page.limit, "items": [serialize_booking(b) for b in items]}


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return serialize_booking(load_booking(db, booking_id, user))


@router.post("/bookings/{booking_id}/pay")
def pay(booking_id: int, body: PayIn, bg: BackgroundTasks, db: Session = Depends(get_db),
        user=Depends(require_roles(Role.CUSTOMER))):
    b = load_booking(db, booking_id, user)
    res = pay_booking(db, get_redis(), b, body.payment_method, body.simulate_status, bg)
    return {"message": res["message"], "payment": payment_to_dict(res["payment"]),
            "booking": serialize_booking(res["booking"])}


@router.get("/bookings/{booking_id}/payments")
def payments(booking_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    b = load_booking(db, booking_id, user)
    return [payment_to_dict(p) for p in sorted(b.payments, key=lambda p: p.id)]


@router.put("/bookings/{booking_id}/cancel")
def cancel(booking_id: int, bg: BackgroundTasks, db: Session = Depends(get_db),
           user=Depends(require_roles(Role.CUSTOMER))):
    b = load_booking(db, booking_id, user)
    res = cancel_booking(db, get_redis(), b, bg)
    return {"message": res["message"], "refund_amount": res["refund_amount"],
            "refund_percent": res.get("refund_percent"), "booking": serialize_booking(res["booking"])}


@router.get("/bookings/{booking_id}/refunds")
def refunds(booking_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    b = load_booking(db, booking_id, user)
    return [refund_to_dict(r) for r in sorted(b.refunds, key=lambda r: r.id)]
