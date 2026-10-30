import logging
import secrets
import string
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models import (Booking, BookedSeat, Payment, Refund, Seat, Show, Waitlist)
from app.models.enums import (BookingStatus, PaymentStatus, RefundReason, RefundStatus, ShowStatus,
                              WaitlistStatus)
from app.services.email_service import queue_email
from app.services.pricing import calc_bill, refund_percent, q
from app.services.seat_service import (LOCK_TTL, MAX_SEATS, booked_ids, build_seat_map, effective_prices,
                                       ensure_bookable, get_owners, lock_key, my_booked_ids, release,
                                       seat_label)
from app.services.ticket_service import generate_ticket_pdf, seat_labels
from app.utils.cache import clear_event_cache
from app.utils.time import now
from app.websockets.manager import manager

log = logging.getLogger(__name__)


# ---------- helpers ----------
def next_booking_number(db: Session, r) -> str:
    day = now().strftime("%Y%m%d")
    while True:
        key = f"bkgseq:{day}"
        n = r.incr(key)
        r.expire(key, 172800)
        number = f"BKG-{day}-{n:04d}"
        if not db.query(Booking.id).filter(Booking.booking_number == number).first():
            return number


def new_txn_id(db: Session) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        txn = f"TXN-{now().strftime('%Y%m%d')}-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.query(Payment.id).filter(Payment.transaction_id == txn).first():
            return txn


def _show_text(show: Show) -> str:
    return (f"{show.event.title} at {show.screen.venue.name} ({show.screen.name}) on "
            f"{show.show_date:%d %b %Y} {show.start_time:%I:%M %p}")


def _success_payment(booking: Booking) -> Optional[Payment]:
    for p in sorted(booking.payments, key=lambda x: x.id, reverse=True):
        if p.status == PaymentStatus.SUCCESS:
            return p
    return None


def _available_updates(show_id: int, booking: Booking) -> List[dict]:
    return [{"seat_id": bs.seat_id, "label": seat_label(bs.seat), "status": "Available"}
            for bs in booking.booked_seats]


# ---------- create booking ----------
def create_booking(db: Session, r, user, show_id: int, seat_ids: List[int]) -> Booking:
    show = db.get(Show, show_id)
    if not show:
        raise HTTPException(404, "Show not found")
    ensure_bookable(show)
    ids = list(dict.fromkeys(seat_ids))
    owners = get_owners(r, show_id, ids)
    not_mine = [i for i in ids if owners[i] != user.id]
    if not_mine:
        raise HTTPException(409, {"message": "Seats must be locked by you before booking (lock may have expired)",
                                  "seat_ids": not_mine})
    seats = db.query(Seat).filter(Seat.id.in_(ids), Seat.screen_id == show.screen_id).all()
    if len(seats) != len(ids):
        raise HTTPException(404, "Some seats do not belong to this show's screen")
    if any(not s.is_active for s in seats):
        raise HTTPException(409, "Some seats are unavailable")
    if booked_ids(db, show_id) & set(ids):
        raise HTTPException(409, "Some seats are already booked")
    dup = (db.query(BookedSeat.id).join(Booking, Booking.id == BookedSeat.booking_id)
           .filter(Booking.show_id == show_id, Booking.customer_id == user.id,
                   Booking.status == BookingStatus.PENDING, BookedSeat.seat_id.in_(ids)).first())
    if dup:
        raise HTTPException(409, "A pending booking already exists for these seats; pay for it or cancel it")
    if len(my_booked_ids(db, show_id, user.id) | set(ids)) > MAX_SEATS:
        raise HTTPException(400, f"You can book at most {MAX_SEATS} seats per show")

    ttls = [r.ttl(lock_key(show_id, i)) for i in ids]
    if any(t is None or t <= 0 for t in ttls):
        raise HTTPException(409, "Seat lock expired, please lock the seats again")
    expires_at = now() + timedelta(seconds=min(ttls))

    prices = effective_prices(db, show)
    missing = [seat_label(s) for s in seats if s.category not in prices]
    if missing:
        raise HTTPException(400, {"message": "No price set for these seats' category", "seats": missing})
    bill = calc_bill([prices[s.category] for s in seats])

    booking = Booking(booking_number=next_booking_number(db, r), customer_id=user.id, show_id=show_id,
                      seat_count=len(seats), status=BookingStatus.PENDING, expires_at=expires_at, **bill)
    db.add(booking)
    db.flush()
    for s in seats:
        db.add(BookedSeat(booking_id=booking.id, show_id=show_id, seat_id=s.id, price=prices[s.category]))
    db.commit()
    db.refresh(booking)
    return booking


# ---------- payment ----------
def _late_payment(db: Session, r, booking: Booking, method, bg) -> dict:
    """Payment succeeded but the seats are gone: refund automatically."""
    customer, show = booking.customer, booking.show
    payment = Payment(booking_id=booking.id, transaction_id=new_txn_id(db), amount=booking.grand_total,
                      payment_method=method, status=PaymentStatus.REFUNDED, paid_at=now())
    db.add(payment)
    db.flush()
    db.add(Refund(booking_id=booking.id, payment_id=payment.id, amount=booking.grand_total,
                  reason=RefundReason.LATE, status=RefundStatus.PROCESSED, refunded_at=now()))
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now()
    db.commit()
    queue_email(bg, customer.email, f"Payment refunded - {booking.booking_number}",
                f"Hi {customer.name},\n\nYour payment of Rs. {booking.grand_total} for {_show_text(show)} was "
                f"received after your seat hold expired and the seats were taken by someone else. "
                f"The full amount has been refunded and the booking is cancelled.")
    return {"payment": payment, "booking": booking,
            "message": "Payment received after seat lock expired and seats are no longer available. "
                       "Amount refunded automatically; booking cancelled."}


def pay_booking(db: Session, r, booking: Booking, method, simulate: str, bg) -> dict:
    if booking.status == BookingStatus.CONFIRMED:
        raise HTTPException(409, "Booking is already confirmed")
    if booking.status in (BookingStatus.EXPIRED, BookingStatus.CANCELLED):
        raise HTTPException(400, f"Cannot pay for a {booking.status.value} booking")
    customer, show = booking.customer, booking.show

    if simulate == "Failed":
        payment = Payment(booking_id=booking.id, transaction_id=new_txn_id(db), amount=booking.grand_total,
                          payment_method=method, status=PaymentStatus.FAILED)
        db.add(payment)
        db.commit()
        queue_email(bg, customer.email, f"Payment failed - {booking.booking_number}",
                    f"Hi {customer.name},\n\nYour payment of Rs. {booking.grand_total} for booking "
                    f"{booking.booking_number} failed. Your seats are held until "
                    f"{booking.expires_at:%I:%M %p}; you can retry the payment before then.")
        return {"payment": payment, "booking": booking,
                "message": "Payment failed. Booking is still Pending Payment; you can retry."}

    seat_ids = [bs.seat_id for bs in booking.booked_seats]
    owners = get_owners(r, show.id, seat_ids)
    taken = booked_ids(db, show.id)
    acquired: List[int] = []
    can_confirm = show.status == ShowStatus.SCHEDULED
    for sid in seat_ids:
        if not can_confirm:
            break
        if sid in taken:
            can_confirm = False
        elif owners[sid] == customer.id:
            continue
        elif owners[sid] is None and r.set(lock_key(show.id, sid), str(customer.id), nx=True, ex=120):
            acquired.append(sid)  # lock had expired but seat is still free: re-hold it
        else:
            can_confirm = False

    payment = None
    if can_confirm:
        try:
            payment = Payment(booking_id=booking.id, transaction_id=new_txn_id(db), amount=booking.grand_total,
                              payment_method=method, status=PaymentStatus.SUCCESS, paid_at=now())
            db.add(payment)
            booking.status = BookingStatus.CONFIRMED
            booking.confirmed_at = now()
            for bs in booking.booked_seats:
                bs.active_seat_id = bs.seat_id  # unique(show_id, active_seat_id) guards double booking
            db.flush()
            booking.ticket_path = generate_ticket_pdf(booking)
            db.commit()
        except IntegrityError:
            db.rollback()
            can_confirm = False
        except Exception:
            db.rollback()
            release(r, show.id, acquired, customer.id)
            raise

    if not can_confirm:
        release(r, show.id, acquired, customer.id)
        return _late_payment(db, r, booking, method, bg)

    # after commit: release Redis locks, push live update, send email
    release(r, show.id, seat_ids, customer.id)
    manager.push(show.id, [{"seat_id": bs.seat_id, "label": seat_label(bs.seat), "status": "Booked"}
                           for bs in booking.booked_seats])
    queue_email(bg, customer.email, f"Booking confirmed - {booking.booking_number}",
                f"Hi {customer.name},\n\nYour booking {booking.booking_number} is confirmed.\n"
                f"{_show_text(show)}\nSeats: {', '.join(seat_labels(booking))}\nAmount paid: Rs. {booking.grand_total}\n\n"
                f"Your ticket is attached. Show the QR code at the entry gate.", booking.ticket_path)
    return {"payment": payment, "booking": booking, "message": "Payment successful. Booking confirmed."}


# ---------- waitlist ----------
def notify_waitlist(db: Session, r, show: Show, bg) -> None:
    entries = (db.query(Waitlist).filter(Waitlist.show_id == show.id, Waitlist.status == WaitlistStatus.WAITING)
               .order_by(Waitlist.joined_at, Waitlist.id).all())
    if not entries:
        return
    avail: dict = {}
    for s in build_seat_map(db, r, show):
        if s["status"] == "Available":
            avail[s["category"]] = avail.get(s["category"], 0) + 1
    for e in entries:  # in the order they joined
        if avail.get(e.category, 0) >= e.seat_count:
            avail[e.category] -= e.seat_count
            e.status = WaitlistStatus.NOTIFIED
            e.notified_at = now()
            queue_email(bg, e.customer.email, f"Seats available - {show.event.title}",
                        f"Hi {e.customer.name},\n\n{e.seat_count} {e.category.value} seat(s) are now available for "
                        f"{_show_text(show)}. Book now before they are gone!")
    db.commit()


# ---------- customer cancellation ----------
def cancel_booking(db: Session, r, booking: Booking, bg) -> dict:
    show, customer = booking.show, booking.customer
    if booking.status == BookingStatus.PENDING:
        release(r, show.id, [bs.seat_id for bs in booking.booked_seats], customer.id)
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = now()
        db.commit()
        manager.push(show.id, _available_updates(show.id, booking))
        return {"booking": booking, "refund_amount": Decimal("0.00"), "message": "Pending booking cancelled"}
    if booking.status != BookingStatus.CONFIRMED:
        raise HTTPException(400, f"A {booking.status.value} booking cannot be cancelled")
    if booking.entered_at:
        raise HTTPException(400, "Ticket already used; cannot cancel")
    seconds = (show.start_at - now()).total_seconds()
    pct = refund_percent(seconds)
    if seconds <= 0 or pct is None:
        raise HTTPException(400, "Cancellation is not allowed less than 2 hours before the show")
    amount = q(booking.ticket_total * pct / Decimal("100"))  # fee + GST are never refunded
    payment = _success_payment(booking)
    refund = Refund(booking_id=booking.id, payment_id=payment.id if payment else None, amount=amount,
                    reason=RefundReason.CUSTOMER, status=RefundStatus.PROCESSED, refunded_at=now())
    db.add(refund)
    if payment:
        payment.status = PaymentStatus.REFUNDED
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now()
    updates = _available_updates(show.id, booking)
    for bs in booking.booked_seats:
        bs.active_seat_id = None  # seats become Available immediately
    db.commit()
    manager.push(show.id, updates)
    queue_email(bg, customer.email, f"Booking cancelled - {booking.booking_number}",
                f"Hi {customer.name},\n\nYour booking {booking.booking_number} for {_show_text(show)} is cancelled.\n"
                f"Refund: Rs. {amount} ({pct}% of ticket total; convenience fee and GST are non-refundable).")
    notify_waitlist(db, r, show, bg)
    return {"booking": booking, "refund_amount": amount, "refund_percent": pct,
            "message": "Booking cancelled and refund processed"}


# ---------- organizer show cancellation ----------
def cancel_show(db: Session, r, show: Show, bg) -> dict:
    if show.status != ShowStatus.SCHEDULED:
        raise HTTPException(400, f"Show is already {show.status.value}")
    show.status = ShowStatus.CANCELLED
    bookings = (db.query(Booking).filter(Booking.show_id == show.id,
                                         Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING])).all())
    notes = []
    refunded = Decimal("0.00")
    for b in bookings:
        if b.status == BookingStatus.CONFIRMED:
            pay = _success_payment(b)
            db.add(Refund(booking_id=b.id, payment_id=pay.id if pay else None, amount=b.grand_total,
                          reason=RefundReason.SHOW, status=RefundStatus.PROCESSED, refunded_at=now()))
            if pay:
                pay.status = PaymentStatus.REFUNDED
            refunded += b.grand_total
            notes.append((b.customer.id, b.customer.email, b.customer.name, b.booking_number, b.grand_total))
        b.status = BookingStatus.CANCELLED
        b.cancelled_at = now()
        for bs in b.booked_seats:
            bs.active_seat_id = None
    db.query(Waitlist).filter(Waitlist.show_id == show.id).delete()
    db.commit()
    for k in r.scan_iter(f"lock:show:{show.id}:seat:*"):
        r.delete(k)
    text = _show_text(show)
    for uid, email, name, number, amount in notes:
        queue_email(bg, email, f"Show cancelled - {number}",
                    f"Hi {name},\n\nThe organizer has cancelled {text}. Your booking {number} is cancelled and "
                    f"Rs. {amount} (100% incl. convenience fee and GST) has been refunded.")
        manager.push_user(uid, {"type": "notification", "show_id": show.id,
                                "message": f"Show cancelled: {text}. Refund Rs. {amount} processed."})
    manager.push_raw(show.id, {"type": "show_cancelled", "show_id": show.id, "message": "This show was cancelled"})
    clear_event_cache()
    return {"cancelled_bookings": len(bookings), "refunded_amount": refunded}
