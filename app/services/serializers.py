from app.services.ticket_service import seat_labels


def show_to_dict(show) -> dict:
    screen = show.screen
    venue = screen.venue
    return {
        "id": show.id, "event_id": show.event_id, "event_title": show.event.title,
        "screen_id": screen.id, "screen_name": screen.name,
        "venue_id": venue.id, "venue_name": venue.name, "city": venue.city,
        "show_date": show.show_date, "start_time": show.start_time, "end_time": show.end_time,
        "booking_closes_at": show.booking_closes_at, "status": show.status,
        "prices": [{"category": p.category, "price": p.price} for p in show.prices],
    }


def event_to_dict(e) -> dict:
    return {"id": e.id, "organizer_id": e.organizer_id, "title": e.title, "category": e.category,
            "language": e.language, "description": e.description, "duration_minutes": e.duration_minutes,
            "age_rating": e.age_rating, "status": e.status}


def serialize_booking(b) -> dict:
    show = b.show
    seats = sorted(b.booked_seats, key=lambda x: (x.seat.row_label, x.seat.seat_number))
    return {
        "id": b.id, "booking_number": b.booking_number, "customer_id": b.customer_id,
        "show_id": b.show_id, "event_title": show.event.title, "show_start": show.start_at,
        "seat_count": b.seat_count, "seats": [{"seat_id": x.seat_id, "label": f"{x.seat.row_label}{x.seat.seat_number}",
                                               "category": x.seat.category, "price": x.price} for x in seats],
        "ticket_total": b.ticket_total, "convenience_fee": b.convenience_fee, "gst_amount": b.gst_amount,
        "grand_total": b.grand_total, "status": b.status, "expires_at": b.expires_at,
        "confirmed_at": b.confirmed_at, "cancelled_at": b.cancelled_at, "entered_at": b.entered_at,
        "created_at": b.created_at,
    }


def payment_to_dict(p) -> dict:
    return {"id": p.id, "booking_id": p.booking_id, "transaction_id": p.transaction_id, "amount": p.amount,
            "payment_method": p.payment_method, "status": p.status, "paid_at": p.paid_at}


def refund_to_dict(r) -> dict:
    return {"id": r.id, "booking_id": r.booking_id, "amount": r.amount, "reason": r.reason,
            "status": r.status, "refunded_at": r.refunded_at}
