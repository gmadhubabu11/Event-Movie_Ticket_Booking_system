import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.auth.deps import get_current_user, require_roles
from app.database import get_db
from app.models import Booking, Show
from app.models.enums import BookingStatus, Role
from app.routers.bookings import load_booking
from app.schemas.booking import ScanIn
from app.services.ticket_service import generate_ticket_pdf, seat_labels, verify_qr
from app.utils.time import now
from datetime import timedelta

router = APIRouter(tags=["Tickets & Entry"])


@router.get("/bookings/{booking_id}/ticket")
def download_ticket(booking_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    b = load_booking(db, booking_id, user)
    if b.status != BookingStatus.CONFIRMED:
        raise HTTPException(400, "Ticket is available only for Confirmed bookings")
    if not b.ticket_path or not os.path.exists(b.ticket_path):
        b.ticket_path = generate_ticket_pdf(b)
        db.commit()
    return FileResponse(b.ticket_path, media_type="application/pdf", filename=f"{b.booking_number}.pdf")


@router.post("/tickets/scan")
def scan(body: ScanIn, db: Session = Depends(get_db), staff=Depends(require_roles(Role.STAFF))):
    number = verify_qr(body.qr_value)
    if not number:
        raise HTTPException(400, "Invalid QR code")
    b = db.query(Booking).filter(Booking.booking_number == number).first()
    if not b:
        raise HTTPException(404, "Booking not found")
    show = b.show
    if show.screen.venue_id != staff.venue_id:
        raise HTTPException(403, "This ticket is for a show at a different venue")
    if b.status != BookingStatus.CONFIRMED:
        raise HTTPException(400, f"Ticket rejected: booking is {b.status.value}")
    t = now()
    if t < show.start_at - timedelta(hours=1):
        raise HTTPException(400, "Entry opens 1 hour before the show starts")
    if t > show.end_at:
        raise HTTPException(400, "Show has ended")
    if b.entered_at:
        raise HTTPException(409, f"Ticket already used at {b.entered_at:%d %b %Y %I:%M:%S %p}")
    # atomic: only one scan can win even if two gates scan at the same instant
    res = db.execute(update(Booking).where(Booking.id == b.id, Booking.entered_at.is_(None))
                     .values(entered_at=t, scanned_by=staff.id))
    db.commit()
    if res.rowcount == 0:
        db.refresh(b)
        raise HTTPException(409, f"Ticket already used at {b.entered_at:%d %b %Y %I:%M:%S %p}")
    db.refresh(b)
    labels = seat_labels(b)
    return {"message": "Entry allowed", "booking_number": b.booking_number, "customer_name": b.customer.name,
            "seats": labels, "seat_count": len(labels), "entered_at": b.entered_at}


@router.get("/shows/{show_id}/entries")
def entries(show_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    show = db.get(Show, show_id)
    if not show:
        raise HTTPException(404, "Show not found")
    if user.role == Role.STAFF:
        if show.screen.venue_id != user.venue_id:
            raise HTTPException(403, "Show is not at your venue")
    elif user.role == Role.ORGANIZER:
        if show.event.organizer_id != user.id:
            raise HTTPException(403, "Not your show")
    elif user.role != Role.ADMIN:
        raise HTTPException(403, "Insufficient permissions")
    bookings = db.query(Booking).filter(Booking.show_id == show_id, Booking.status == BookingStatus.CONFIRMED).all()
    entered = [b for b in bookings if b.entered_at]
    return {"show_id": show_id, "total_bookings": len(bookings), "entered_bookings": len(entered),
            "total_seats": sum(b.seat_count for b in bookings), "entered_seats": sum(b.seat_count for b in entered)}
