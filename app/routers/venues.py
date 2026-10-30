from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth.deps import get_current_user, require_roles
from app.database import get_db
from app.models import Venue, Screen, Seat, BookedSeat, Show
from app.models.enums import Role, ShowStatus
from app.schemas.venue import (VenueIn, VenueUpdate, VenueOut, ScreenIn, ScreenOut, SeatBulkIn, SeatStatusIn)
from app.utils.pagination import Page

router = APIRouter(tags=["Venues, Screens & Seats"])
admin_only = require_roles(Role.ADMIN)


@router.post("/venues", response_model=VenueOut, status_code=201)
def create_venue(body: VenueIn, db: Session = Depends(get_db), _=Depends(admin_only)):
    v = Venue(**body.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@router.get("/venues")
def list_venues(city: Optional[str] = None, page: Page = Depends(), db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    q = db.query(Venue)
    if user.role != Role.ADMIN:
        q = q.filter(Venue.is_active.is_(True))
    if city:
        q = q.filter(Venue.city.ilike(f"%{city}%"))
    total = q.count()
    items = q.order_by(Venue.id).offset(page.skip).limit(page.limit).all()
    return {"total": total, "skip": page.skip, "limit": page.limit,
            "items": [VenueOut.model_validate(v) for v in items]}


@router.put("/venues/{venue_id}", response_model=VenueOut)
def update_venue(venue_id: int, body: VenueUpdate, db: Session = Depends(get_db), _=Depends(admin_only)):
    v = db.get(Venue, venue_id)
    if not v:
        raise HTTPException(404, "Venue not found")
    for k, val in body.model_dump(exclude_unset=True).items():
        setattr(v, k, val)
    db.commit()
    db.refresh(v)
    return v


@router.post("/venues/{venue_id}/screens", response_model=ScreenOut, status_code=201)
def create_screen(venue_id: int, body: ScreenIn, db: Session = Depends(get_db), _=Depends(admin_only)):
    if not db.get(Venue, venue_id):
        raise HTTPException(404, "Venue not found")
    s = Screen(venue_id=venue_id, **body.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.post("/screens/{screen_id}/seats/bulk", status_code=201)
def bulk_seats(screen_id: int, body: SeatBulkIn, db: Session = Depends(get_db), _=Depends(admin_only)):
    screen = db.get(Screen, screen_id)
    if not screen:
        raise HTTPException(404, "Screen not found")
    rows = [chr(c) for c in range(ord(body.row_start), ord(body.row_end) + 1)]
    existing = {(s.row_label, s.seat_number) for s in db.query(Seat).filter(Seat.screen_id == screen_id)}
    dup = [f"{r}{n}" for r in rows for n in range(1, body.seats_per_row + 1) if (r, n) in existing]
    if dup:
        raise HTTPException(409, {"message": "Seats already exist", "seats": dup[:50]})
    db.add_all([Seat(screen_id=screen_id, row_label=r, seat_number=n, category=body.category)
                for r in rows for n in range(1, body.seats_per_row + 1)])
    db.flush()
    screen.total_seats = db.query(Seat).filter(Seat.screen_id == screen_id).count()
    db.commit()
    return {"created": len(rows) * body.seats_per_row, "rows": rows, "screen_total_seats": screen.total_seats}


@router.get("/screens/{screen_id}/layout")
def screen_layout(screen_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    screen = db.get(Screen, screen_id)
    if not screen:
        raise HTTPException(404, "Screen not found")
    seats = db.query(Seat).filter(Seat.screen_id == screen_id).all()
    seats.sort(key=lambda s: (len(s.row_label), s.row_label, s.seat_number))
    rows = {}
    for s in seats:
        rows.setdefault(s.row_label, []).append({
            "id": s.id, "seat_number": s.seat_number, "category": s.category, "is_active": s.is_active,
            "status": "Available" if s.is_active else "Unavailable"})
    return {"screen_id": screen.id, "name": screen.name, "venue_id": screen.venue_id,
            "total_seats": screen.total_seats,
            "rows": [{"row_label": k, "seats": v} for k, v in rows.items()]}


@router.put("/seats/{seat_id}/status")
def seat_status(seat_id: int, body: SeatStatusIn, db: Session = Depends(get_db), _=Depends(admin_only)):
    seat = db.get(Seat, seat_id)
    if not seat:
        raise HTTPException(404, "Seat not found")
    if not body.is_active:
        used = (db.query(BookedSeat.id).join(Show, Show.id == BookedSeat.show_id)
                .filter(BookedSeat.seat_id == seat_id, BookedSeat.active_seat_id.isnot(None),
                        Show.status == ShowStatus.SCHEDULED).first())
        if used:
            raise HTTPException(409, "Seat has confirmed bookings for upcoming shows")
    seat.is_active = body.is_active
    db.commit()
    return {"id": seat.id, "row_label": seat.row_label, "seat_number": seat.seat_number,
            "is_active": seat.is_active}
