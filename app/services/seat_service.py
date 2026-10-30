from datetime import timedelta
from typing import Dict, List, Optional, Set
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from app.models import Seat, BookedSeat, Booking, Show
from app.models.enums import BookingStatus, ShowStatus
from app.services.pricing import dynamic_price
from app.services.seat_rules import find_gap_violations
from app.websockets.manager import manager
from app.utils.time import now

LOCK_TTL = 600  # 10 minutes
MAX_SEATS = 10

RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""


def lock_key(show_id: int, seat_id: int) -> str:
    return f"lock:show:{show_id}:seat:{seat_id}"


def seat_label(seat: Seat) -> str:
    return f"{seat.row_label}{seat.seat_number}"


def get_owners(r, show_id: int, seat_ids: List[int]) -> Dict[int, Optional[int]]:
    if not seat_ids:
        return {}
    vals = r.mget([lock_key(show_id, s) for s in seat_ids])
    return {s: (int(v) if v is not None else None) for s, v in zip(seat_ids, vals)}


def release(r, show_id: int, seat_ids: List[int], user_id: int) -> List[int]:
    """Delete a lock only if it still belongs to user_id (atomic via Lua)."""
    done = []
    for s in seat_ids:
        if r.eval(RELEASE_LUA, 1, lock_key(show_id, s), str(user_id)):
            done.append(s)
    return done


def booked_ids(db: Session, show_id: int) -> Set[int]:
    rows = db.query(BookedSeat.seat_id).filter(BookedSeat.show_id == show_id,
                                               BookedSeat.active_seat_id.isnot(None)).all()
    return {sid for (sid,) in rows}


def my_booked_ids(db: Session, show_id: int, user_id: int) -> Set[int]:
    rows = (db.query(BookedSeat.seat_id).join(Booking, Booking.id == BookedSeat.booking_id)
            .filter(Booking.show_id == show_id, Booking.customer_id == user_id,
                    Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING])).all())
    return {sid for (sid,) in rows}


def ensure_bookable(show: Show):
    if show.status != ShowStatus.SCHEDULED:
        raise HTTPException(400, f"Show is {show.status.value}")
    if now() >= show.booking_closes_at:
        raise HTTPException(400, "Booking is closed for this show")


def effective_prices(db: Session, show: Show) -> dict:
    total = db.query(Seat).filter(Seat.screen_id == show.screen_id, Seat.is_active.is_(True)).count()
    booked = len(booked_ids(db, show.id))
    return {p.category: dynamic_price(p.price, booked, total) for p in show.prices}


def build_seat_map(db: Session, r, show: Show, viewer_id: Optional[int] = None) -> List[dict]:
    seats = db.query(Seat).filter(Seat.screen_id == show.screen_id).all()
    seats.sort(key=lambda s: (len(s.row_label), s.row_label, s.seat_number))
    booked = booked_ids(db, show.id)
    owners = get_owners(r, show.id, [s.id for s in seats])
    active_total = sum(1 for s in seats if s.is_active)
    prices = {p.category: p.price for p in show.prices}
    out = []
    for s in seats:
        if not s.is_active:
            st = "Unavailable"
        elif s.id in booked:
            st = "Booked"
        elif owners.get(s.id) is not None:
            st = "Locked"
        else:
            st = "Available"
        base = prices.get(s.category)
        out.append({
            "seat_id": s.id, "row_label": s.row_label, "seat_number": s.seat_number,
            "label": seat_label(s), "category": s.category, "status": st,
            "price": dynamic_price(base, len(booked), active_total) if base is not None else None,
            "locked_by_me": viewer_id is not None and owners.get(s.id) == viewer_id and st == "Locked",
        })
    return out


def ws_seat_map(db: Session, r, show: Show, viewer_id: int) -> dict:
    seats = build_seat_map(db, r, show, viewer_id)
    for s in seats:
        if s["locked_by_me"]:
            s["status"] = "Yours"
    return jsonable_encoder({"type": "seat_map", "show_id": show.id, "seats": seats})


def lock_seats(db: Session, r, show: Show, user, seat_ids: List[int]) -> dict:
    ids = list(dict.fromkeys(seat_ids))
    ensure_bookable(show)
    all_seats = db.query(Seat).filter(Seat.screen_id == show.screen_id).all()
    by_id = {s.id: s for s in all_seats}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise HTTPException(404, {"message": "Seats not found on this show's screen", "seat_ids": missing})

    booked = booked_ids(db, show.id)
    owners = get_owners(r, show.id, list(by_id))

    def reason(i):
        s = by_id[i]
        if not s.is_active:
            return "Unavailable"
        if i in booked:
            return "Booked"
        if owners[i] is not None and owners[i] != user.id:
            return "Locked"
        return None

    unavailable = [{"seat_id": i, "label": seat_label(by_id[i]), "status": reason(i)} for i in ids if reason(i)]
    if unavailable:
        raise HTTPException(409, {"message": "Some seats are not available", "unavailable_seats": unavailable})

    mine = {i for i, o in owners.items() if o == user.id} | my_booked_ids(db, show.id, user.id) | set(ids)
    if len(mine) > MAX_SEATS:
        raise HTTPException(400, f"You can lock or book at most {MAX_SEATS} seats per show")

    taken = (booked | {i for i, o in owners.items() if o is not None and o != user.id}
             | {i for i, s in by_id.items() if not s.is_active}) - mine
    rows: Dict[str, list] = {}
    for s in sorted(all_seats, key=lambda x: (len(x.row_label), x.row_label, x.seat_number)):
        rows.setdefault(s.row_label, []).append((s.id, s.seat_number))
    bad = find_gap_violations(rows, mine, taken)
    if bad:
        raise HTTPException(400, {"message": "Selection would leave a single empty seat",
                                  "seats_left_empty": [seat_label(by_id[i]) for i in bad]})

    acquired: List[int] = []
    for i in ids:
        if owners[i] == user.id:
            continue
        if r.set(lock_key(show.id, i), str(user.id), nx=True, ex=LOCK_TTL):
            acquired.append(i)
        else:  # lost a race: all or nothing
            release(r, show.id, acquired, user.id)
            raise HTTPException(409, {"message": "Some seats are not available",
                                      "unavailable_seats": [{"seat_id": i, "label": seat_label(by_id[i]),
                                                             "status": "Locked"}]})
    ttls = [r.ttl(lock_key(show.id, i)) for i in ids]
    ttl = max(min(t for t in ttls if t is not None and t > 0) if any(t and t > 0 for t in ttls) else LOCK_TTL, 1)
    manager.push(show.id, [{"seat_id": i, "label": seat_label(by_id[i]), "status": "Locked",
                            "owner_id": user.id} for i in acquired])
    return {"show_id": show.id,
            "seats": [{"seat_id": i, "label": seat_label(by_id[i])} for i in ids],
            "lock_expires_at": now() + timedelta(seconds=ttl),
            "lock_seconds": ttl}


def release_seats(db: Session, r, show: Show, user, seat_ids: List[int]) -> dict:
    ids = list(dict.fromkeys(seat_ids))
    seats = {s.id: s for s in db.query(Seat).filter(Seat.id.in_(ids), Seat.screen_id == show.screen_id)}
    missing = [i for i in ids if i not in seats]
    if missing:
        raise HTTPException(404, {"message": "Seats not found", "seat_ids": missing})
    owners = get_owners(r, show.id, ids)
    foreign = [i for i in ids if owners[i] is not None and owners[i] != user.id]
    if foreign:
        raise HTTPException(403, {"message": "You can release only your own locks", "seat_ids": foreign})
    released = release(r, show.id, ids, user.id)
    manager.push(show.id, [{"seat_id": i, "label": seat_label(seats[i]), "status": "Available"}
                           for i in released])
    return {"released": [{"seat_id": i, "label": seat_label(seats[i])} for i in released]}


def suggest_best_seats(seat_map: List[dict], count: int, category: Optional[str]) -> List[dict]:
    """Best adjacent available seats: contiguous run, closest to the middle row / row centre."""
    rows: Dict[str, List[dict]] = {}
    for s in seat_map:
        rows.setdefault(s["row_label"], []).append(s)
    labels = list(rows)
    mid_row = (len(labels) - 1) / 2
    best, best_score = None, None
    for ri, label in enumerate(labels):
        seats = rows[label]
        centre = (len(seats) - 1) / 2
        for i in range(len(seats) - count + 1):
            run = seats[i:i + count]
            ok = all(x["status"] == "Available" and (category is None or x["category"] == category) and
                     (j == 0 or x["seat_number"] == run[j - 1]["seat_number"] + 1) for j, x in enumerate(run))
            if not ok:
                continue
            score = abs(ri - mid_row) * 2 + abs((i + (count - 1) / 2) - centre)
            if best_score is None or score < best_score:
                best, best_score = run, score
    return best or []
