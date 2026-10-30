from datetime import date, datetime, timedelta
from typing import Literal, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from app.auth.deps import get_current_user, get_optional_user, require_approved_organizer
from app.database import get_db
from app.models import Event, Show, SeatCategoryPrice, Screen, Seat, Venue, Booking
from app.models.enums import (BookingStatus, EventCategory, EventStatus, Role, SeatCategory, ShowStatus)
from app.redis_client import get_redis
from app.schemas.event import EventIn, EventUpdate, ShowIn, ShowUpdate
from app.services.booking_service import cancel_show
from app.services.serializers import event_to_dict, show_to_dict
from app.utils.cache import PREFIX, cache_get, cache_set, clear_event_cache
from app.utils.pagination import Page
from app.utils.time import now

router = APIRouter(tags=["Events & Shows"])
CLEAN_GAP = timedelta(minutes=15)


def _visible(event: Event, user) -> bool:
    if event.status == EventStatus.PUBLISHED:
        return True
    if user is None:
        return False
    return user.role == Role.ADMIN or (user.role == Role.ORGANIZER and event.organizer_id == user.id)


def _own_event(db: Session, event_id: int, user) -> Event:
    e = db.get(Event, event_id)
    if not e:
        raise HTTPException(404, "Event not found")
    if e.organizer_id != user.id:
        raise HTTPException(403, "You can manage only your own events")
    return e


# ---------------- events ----------------
@router.post("/events", status_code=201)
def create_event(body: EventIn, db: Session = Depends(get_db), user=Depends(require_approved_organizer)):
    data = body.model_dump()
    data["status"] = EventStatus(data["status"])
    e = Event(organizer_id=user.id, **data)
    db.add(e)
    db.commit()
    db.refresh(e)
    clear_event_cache()
    return event_to_dict(e)


@router.get("/events")
def list_events(
    request: Request,
    title: Optional[str] = None, city: Optional[str] = None, category: Optional[EventCategory] = None,
    language: Optional[str] = None, from_date: Optional[date] = None, to_date: Optional[date] = None,
    min_price: Optional[float] = Query(None, ge=0), max_price: Optional[float] = Query(None, ge=0),
    sort_by: Literal["show_date", "price", "title"] = "show_date", order: Literal["asc", "desc"] = "asc",
    page: Page = Depends(), db: Session = Depends(get_db), user=Depends(get_optional_user),
):
    scope = "public"
    if user and user.role == Role.ADMIN:
        scope = "admin"
    elif user and user.role == Role.ORGANIZER:
        scope = f"org{user.id}"
    key = f"{PREFIX}list:{scope}:{sorted(request.query_params.items())}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    q = db.query(Event)
    if scope == "public":
        q = q.filter(Event.status == EventStatus.PUBLISHED)
    elif scope.startswith("org"):
        q = q.filter((Event.status == EventStatus.PUBLISHED) | (Event.organizer_id == user.id))
    if title:
        q = q.filter(Event.title.ilike(f"%{title}%"))
    if category:
        q = q.filter(Event.category == category)
    if language:
        q = q.filter(Event.language.ilike(language))

    if any(v is not None for v in (city, from_date, to_date, min_price, max_price)):
        sub = (select(Show.event_id).join(Screen, Screen.id == Show.screen_id)
               .join(Venue, Venue.id == Screen.venue_id)
               .join(SeatCategoryPrice, SeatCategoryPrice.show_id == Show.id))
        conds = [Show.status == ShowStatus.SCHEDULED]
        if city:
            conds.append(Venue.city.ilike(city))
        if from_date:
            conds.append(Show.show_date >= from_date)
        if to_date:
            conds.append(Show.show_date <= to_date)
        if min_price is not None:
            conds.append(SeatCategoryPrice.price >= min_price)
        if max_price is not None:
            conds.append(SeatCategoryPrice.price <= max_price)
        q = q.filter(Event.id.in_(sub.where(and_(*conds))))

    next_date = (select(func.min(Show.show_date)).where(Show.event_id == Event.id,
                                                        Show.status == ShowStatus.SCHEDULED)
                 .correlate(Event).scalar_subquery())
    min_p = (select(func.min(SeatCategoryPrice.price)).join(Show, Show.id == SeatCategoryPrice.show_id)
             .where(Show.event_id == Event.id).correlate(Event).scalar_subquery())
    col = {"show_date": next_date, "price": min_p, "title": Event.title}[sort_by]
    total = q.count()
    items = q.order_by(col.asc() if order == "asc" else col.desc(), Event.id).offset(page.skip).limit(page.limit).all()
    result = {"total": total, "skip": page.skip, "limit": page.limit, "items": [event_to_dict(e) for e in items]}
    cache_set(key, result)
    return result


@router.get("/events/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db), user=Depends(get_optional_user)):
    scope = "admin" if user and user.role == Role.ADMIN else (f"org{user.id}" if user and user.role == Role.ORGANIZER else "public")
    key = f"{PREFIX}detail:{scope}:{event_id}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    e = db.get(Event, event_id)
    if not e or not _visible(e, user):
        raise HTTPException(404, "Event not found")
    shows = [s for s in e.shows if s.status == ShowStatus.SCHEDULED and s.start_at >= now()]
    shows.sort(key=lambda s: s.start_at)
    result = {**event_to_dict(e), "shows": [show_to_dict(s) for s in shows]}
    cache_set(key, result)
    return result


@router.put("/events/{event_id}")
def update_event(event_id: int, body: EventUpdate, bg: BackgroundTasks, db: Session = Depends(get_db),
                 user=Depends(require_approved_organizer)):
    e = _own_event(db, event_id, user)
    data = body.model_dump(exclude_unset=True)
    new_status = data.pop("status", None)
    for k, v in data.items():
        setattr(e, k, v)
    if new_status:
        if e.status == EventStatus.CANCELLED:
            raise HTTPException(400, "Cancelled event cannot be changed")
        e.status = EventStatus(new_status)
    db.commit()
    if new_status == "Cancelled":
        for s in list(e.shows):
            if s.status == ShowStatus.SCHEDULED:
                cancel_show(db, get_redis(), s, bg)
    clear_event_cache()
    db.refresh(e)
    return event_to_dict(e)


# ---------------- shows ----------------
def _screen_categories(db: Session, screen_id: int) -> set:
    return {c for (c,) in db.query(Seat.category).filter(Seat.screen_id == screen_id,
                                                          Seat.is_active.is_(True)).distinct()}


def _check_prices(db: Session, screen_id: int, prices) -> None:
    needed = _screen_categories(db, screen_id)
    if not needed:
        raise HTTPException(400, "Screen has no active seats")
    given = [p.category for p in prices]
    if len(given) != len(set(given)):
        raise HTTPException(400, "Duplicate category in prices")
    if set(given) != needed:
        raise HTTPException(400, {"message": "Every seat category on the screen needs exactly one price",
                                  "required_categories": sorted(c.value for c in needed)})


def _check_overlap(db: Session, screen_id: int, start_at: datetime, end_at: datetime, exclude_id=None):
    q = db.query(Show).filter(Show.screen_id == screen_id, Show.status != ShowStatus.CANCELLED,
                              Show.start_at < end_at + CLEAN_GAP, Show.end_at > start_at - CLEAN_GAP)
    if exclude_id:
        q = q.filter(Show.id != exclude_id)
    clash = q.first()
    if clash:
        raise HTTPException(409, f"Overlaps with show #{clash.id} on this screen (15-minute cleaning gap applies)")


def _times(event: Event, show_date, start_time):
    start_at = datetime.combine(show_date, start_time)
    end_at = start_at + timedelta(minutes=event.duration_minutes)
    return start_at, end_at, start_at - timedelta(minutes=30)


@router.post("/events/{event_id}/shows", status_code=201)
def create_show(event_id: int, body: ShowIn, db: Session = Depends(get_db),
                user=Depends(require_approved_organizer)):
    e = _own_event(db, event_id, user)
    if e.status == EventStatus.CANCELLED:
        raise HTTPException(400, "Event is cancelled")
    screen = db.get(Screen, body.screen_id)
    if not screen or not screen.venue.is_active:
        raise HTTPException(404, "Screen not found or venue inactive")
    start_at, end_at, closes = _times(e, body.show_date, body.start_time)
    if start_at <= now():
        raise HTTPException(400, "Show must be scheduled in the future")
    _check_prices(db, screen.id, body.prices)
    _check_overlap(db, screen.id, start_at, end_at)
    show = Show(event_id=e.id, screen_id=screen.id, show_date=body.show_date, start_time=body.start_time,
                end_time=end_at.time(), start_at=start_at, end_at=end_at, booking_closes_at=closes)
    show.prices = [SeatCategoryPrice(category=p.category, price=p.price) for p in body.prices]
    db.add(show)
    db.commit()
    db.refresh(show)
    clear_event_cache()
    return show_to_dict(show)


@router.get("/events/{event_id}/shows")
def list_shows(event_id: int, db: Session = Depends(get_db), user=Depends(get_optional_user)):
    e = db.get(Event, event_id)
    if not e or not _visible(e, user):
        raise HTTPException(404, "Event not found")
    shows = sorted(e.shows, key=lambda s: s.start_at)
    return [show_to_dict(s) for s in shows]


def _own_show(db: Session, show_id: int, user) -> Show:
    s = db.get(Show, show_id)
    if not s:
        raise HTTPException(404, "Show not found")
    if user.role != Role.ADMIN and s.event.organizer_id != user.id:
        raise HTTPException(403, "You can manage only your own shows")
    return s


@router.put("/shows/{show_id}")
def update_show(show_id: int, body: ShowUpdate, db: Session = Depends(get_db),
                user=Depends(require_approved_organizer)):
    s = _own_show(db, show_id, user)
    if s.status != ShowStatus.SCHEDULED:
        raise HTTPException(400, f"Show is {s.status.value}")
    has_bookings = db.query(Booking.id).filter(
        Booking.show_id == s.id, Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING])).first()
    confirmed = db.query(Booking.id).filter(Booking.show_id == s.id,
                                            Booking.status == BookingStatus.CONFIRMED).first()
    if body.prices is not None and confirmed:
        raise HTTPException(409, "Prices cannot be changed after the first confirmed booking")
    if (body.screen_id or body.show_date or body.start_time) and has_bookings:
        raise HTTPException(409, "Schedule/screen cannot be changed once bookings exist")
    screen_id = body.screen_id or s.screen_id
    if body.screen_id and not db.get(Screen, body.screen_id):
        raise HTTPException(404, "Screen not found")
    start_at, end_at, closes = _times(s.event, body.show_date or s.show_date, body.start_time or s.start_time)
    if start_at <= now():
        raise HTTPException(400, "Show must be scheduled in the future")
    _check_overlap(db, screen_id, start_at, end_at, exclude_id=s.id)
    if body.prices is not None:
        _check_prices(db, screen_id, body.prices)
        s.prices = [SeatCategoryPrice(category=p.category, price=p.price) for p in body.prices]
    elif body.screen_id and body.screen_id != s.screen_id:
        _check_prices(db, screen_id, [type("P", (), {"category": p.category})() for p in s.prices])
    s.screen_id, s.show_date, s.start_time = screen_id, start_at.date(), start_at.time()
    s.end_time, s.start_at, s.end_at, s.booking_closes_at = end_at.time(), start_at, end_at, closes
    db.commit()
    db.refresh(s)
    clear_event_cache()
    return show_to_dict(s)


@router.put("/shows/{show_id}/cancel")
def cancel_show_route(show_id: int, bg: BackgroundTasks, db: Session = Depends(get_db),
                      user=Depends(require_approved_organizer)):
    s = _own_show(db, show_id, user)
    return cancel_show(db, get_redis(), s, bg)
