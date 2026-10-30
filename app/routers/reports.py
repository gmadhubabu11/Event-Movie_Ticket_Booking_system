from datetime import date, datetime, time, timedelta
from io import BytesIO
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.auth.deps import require_roles
from app.database import get_db
from app.models import BookedSeat, Booking, Event, Payment, Refund, Seat, Show
from app.models.enums import (BookingStatus, EventStatus, PaymentStatus, RefundReason, Role, SeatCategory)

router = APIRouter(prefix="/reports", tags=["Reports"])
admin = require_roles(Role.ADMIN)
admin_org = require_roles(Role.ADMIN, Role.ORGANIZER)


def _range(from_date: Optional[date], to_date: Optional[date]):
    to_date = to_date or date.today()
    from_date = from_date or (to_date - timedelta(days=30))
    return from_date, to_date


def _f(x):
    return float(x or 0)


@router.get("/admin-dashboard")
def dashboard(db: Session = Depends(get_db), _=Depends(admin)):
    start = datetime.combine(date.today(), time.min)
    end = start + timedelta(days=1)
    bookings = db.query(func.count(Booking.id)).filter(Booking.confirmed_at >= start, Booking.confirmed_at < end).scalar()
    revenue = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.status == PaymentStatus.SUCCESS, Payment.paid_at >= start, Payment.paid_at < end).scalar()
    ref_count, ref_amt = db.query(func.count(Refund.id), func.coalesce(func.sum(Refund.amount), 0)).filter(
        Refund.refunded_at >= start, Refund.refunded_at < end).one()
    active = db.query(func.count(Event.id)).filter(Event.status == EventStatus.PUBLISHED).scalar()
    scanned = db.query(func.coalesce(func.sum(Booking.seat_count), 0)).filter(
        Booking.entered_at >= start, Booking.entered_at < end).scalar()
    return {"date": date.today(), "todays_bookings": bookings, "revenue": _f(revenue), "refund_count": ref_count,
            "refund_amount": _f(ref_amt), "active_events": active, "tickets_scanned": int(scanned or 0)}


def _event_sales(db: Session, user, from_date, to_date):
    from_date, to_date = _range(from_date, to_date)
    sq = db.query(Show).join(Event, Event.id == Show.event_id).filter(Show.show_date.between(from_date, to_date))
    if user.role == Role.ORGANIZER:
        sq = sq.filter(Event.organizer_id == user.id)
    shows = sq.order_by(Show.start_at).all()
    ids = [s.id for s in shows]
    sold, refunds = {}, {}
    if ids:
        for sid, cnt, tix, tot in db.query(Booking.show_id, func.count(Booking.id), func.coalesce(func.sum(Booking.seat_count), 0),
                                          func.coalesce(func.sum(Booking.grand_total), 0)).filter(
                Booking.show_id.in_(ids), Booking.status == BookingStatus.CONFIRMED).group_by(Booking.show_id):
            sold[sid] = (int(tix), _f(tot))
        for sid, amt in db.query(Booking.show_id, func.coalesce(func.sum(Refund.amount), 0)).join(
                Refund, Refund.booking_id == Booking.id).filter(Booking.show_id.in_(ids)).group_by(Booking.show_id):
            refunds[sid] = _f(amt)
    events = {}
    for s in shows:
        t, rev = sold.get(s.id, (0, 0.0))
        ev = events.setdefault(s.event_id, {"event_id": s.event_id, "title": s.event.title, "tickets_sold": 0,
                                            "revenue": 0.0, "refunds": 0.0, "shows": []})
        row = {"show_id": s.id, "show_date": s.show_date, "start_time": s.start_time, "status": s.status,
               "tickets_sold": t, "revenue": rev, "refunds": refunds.get(s.id, 0.0)}
        ev["shows"].append(row)
        ev["tickets_sold"] += t
        ev["revenue"] += rev
        ev["refunds"] += row["refunds"]
    return {"from_date": from_date, "to_date": to_date, "events": list(events.values())}


@router.get("/event-sales")
def event_sales(from_date: Optional[date] = None, to_date: Optional[date] = None,
                db: Session = Depends(get_db), user=Depends(admin_org)):
    return _event_sales(db, user, from_date, to_date)


@router.get("/event-sales/export")
def event_sales_export(from_date: Optional[date] = None, to_date: Optional[date] = None,
                       db: Session = Depends(get_db), user=Depends(admin_org)):
    """Bonus: Excel export of the sales report."""
    data = _event_sales(db, user, from_date, to_date)
    wb = Workbook()
    ws = wb.active
    ws.title = "Event Sales"
    ws.append(["Event", "Show ID", "Date", "Start", "Status", "Tickets Sold", "Revenue", "Refunds"])
    for ev in data["events"]:
        for s in ev["shows"]:
            ws.append([ev["title"], s["show_id"], str(s["show_date"]), str(s["start_time"]), s["status"].value,
                       s["tickets_sold"], s["revenue"], s["refunds"]])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=event_sales.xlsx"})


@router.get("/occupancy")
def occupancy(from_date: Optional[date] = None, to_date: Optional[date] = None,
              db: Session = Depends(get_db), user=Depends(admin_org)):
    from_date, to_date = _range(from_date, to_date)
    sq = db.query(Show).join(Event, Event.id == Show.event_id).filter(Show.show_date.between(from_date, to_date))
    if user.role == Role.ORGANIZER:
        sq = sq.filter(Event.organizer_id == user.id)
    shows = sq.order_by(Show.start_at).all()
    if not shows:
        return {"from_date": from_date, "to_date": to_date, "shows": []}
    screen_ids = {s.screen_id for s in shows}
    totals = {}
    for sid, cat, cnt in db.query(Seat.screen_id, Seat.category, func.count(Seat.id)).filter(
            Seat.screen_id.in_(screen_ids), Seat.is_active.is_(True)).group_by(Seat.screen_id, Seat.category):
        totals.setdefault(sid, {})[cat] = cnt
    booked = {}
    for show_id, cat, cnt in db.query(BookedSeat.show_id, Seat.category, func.count(BookedSeat.id)).join(
            Seat, Seat.id == BookedSeat.seat_id).filter(
            BookedSeat.show_id.in_([s.id for s in shows]), BookedSeat.active_seat_id.isnot(None)
    ).group_by(BookedSeat.show_id, Seat.category):
        booked.setdefault(show_id, {})[cat] = cnt
    out = []
    for s in shows:
        tot = totals.get(s.screen_id, {})
        bk = booked.get(s.id, {})
        t, b = sum(tot.values()), sum(bk.values())
        out.append({"show_id": s.id, "event_title": s.event.title, "show_date": s.show_date, "total_seats": t,
                    "booked_seats": b, "occupancy_percent": round(b * 100 / t, 2) if t else 0.0,
                    "by_category": [{"category": c, "total": n, "booked": bk.get(c, 0),
                                     "occupancy_percent": round(bk.get(c, 0) * 100 / n, 2) if n else 0.0}
                                    for c, n in tot.items()]})
    return {"from_date": from_date, "to_date": to_date, "shows": out}


@router.get("/top-events")
def top_events(from_date: Optional[date] = None, to_date: Optional[date] = None,
               db: Session = Depends(get_db), _=Depends(admin)):
    from_date, to_date = _range(from_date, to_date)
    rows = (db.query(Event.id, Event.title, func.coalesce(func.sum(Booking.seat_count), 0).label("t"))
            .join(Show, Show.event_id == Event.id).join(Booking, Booking.show_id == Show.id)
            .filter(Booking.status == BookingStatus.CONFIRMED, Show.show_date.between(from_date, to_date))
            .group_by(Event.id, Event.title).order_by(func.sum(Booking.seat_count).desc()).limit(10).all())
    return {"from_date": from_date, "to_date": to_date,
            "events": [{"event_id": i, "title": t, "tickets_sold": int(n)} for i, t, n in rows]}


@router.get("/refunds")
def refunds_report(from_date: Optional[date] = None, to_date: Optional[date] = None,
                   db: Session = Depends(get_db), _=Depends(admin)):
    from_date, to_date = _range(from_date, to_date)
    start = datetime.combine(from_date, time.min)
    end = datetime.combine(to_date, time.max)
    got = {r: (c, a) for r, c, a in db.query(Refund.reason, func.count(Refund.id), func.coalesce(func.sum(Refund.amount), 0))
           .filter(Refund.refunded_at.between(start, end)).group_by(Refund.reason)}
    return {"from_date": from_date, "to_date": to_date,
            "by_reason": [{"reason": r, "count": got.get(r, (0, 0))[0], "amount": _f(got.get(r, (0, 0))[1])}
                          for r in RefundReason]}
