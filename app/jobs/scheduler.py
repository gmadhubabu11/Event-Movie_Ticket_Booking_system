import logging
import re
import threading
import time as _time
from datetime import timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from app.database import SessionLocal
from app.models import Booking, Event, Seat, Show
from app.models.enums import BookingStatus, EventStatus, ShowStatus
from app.redis_client import get_redis
from app.services.email_service import send_email
from app.utils.cache import clear_event_cache
from app.utils.time import now
from app.websockets.manager import manager

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def expire_bookings():
    db = SessionLocal()
    try:
        rows = db.query(Booking).filter(Booking.status == BookingStatus.PENDING, Booking.expires_at < now()).all()
        for b in rows:
            b.status = BookingStatus.EXPIRED
        db.commit()
        if rows:
            log.info("Expired %d bookings", len(rows))
    except Exception:
        db.rollback()
        log.exception("expire_bookings failed")
    finally:
        db.close()


def send_reminders():
    db = SessionLocal()
    try:
        t = now()
        rows = (db.query(Booking).join(Show, Show.id == Booking.show_id)
                .filter(Booking.status == BookingStatus.CONFIRMED, Booking.reminder_sent.is_(False),
                        Show.status == ShowStatus.SCHEDULED, Show.start_at > t,
                        Show.start_at <= t + timedelta(hours=3)).all())
        for b in rows:
            s = b.show
            send_email(b.customer.email, f"Reminder: {s.event.title} starts soon",
                       f"Hi {b.customer.name},\n\nReminder: {s.event.title} at {s.screen.venue.name} "
                       f"({s.screen.name}) starts at {s.start_time:%I:%M %p} on {s.show_date:%d %b %Y}. "
                       f"Booking {b.booking_number}. Carry your QR ticket.")
            b.reminder_sent = True  # only once per booking
        db.commit()
    except Exception:
        db.rollback()
        log.exception("send_reminders failed")
    finally:
        db.close()


def complete_shows():
    db = SessionLocal()
    try:
        rows = db.query(Show).filter(Show.status == ShowStatus.SCHEDULED, Show.end_at < now()).all()
        for s in rows:
            s.status = ShowStatus.COMPLETED
        db.commit()
        if rows:
            clear_event_cache()
    except Exception:
        db.rollback()
        log.exception("complete_shows failed")
    finally:
        db.close()


def complete_events():
    db = SessionLocal()
    try:
        changed = False
        for e in db.query(Event).filter(Event.status == EventStatus.PUBLISHED).all():
            live = [s for s in e.shows if s.status != ShowStatus.CANCELLED]
            if live and all(s.status == ShowStatus.COMPLETED for s in live):
                e.status = EventStatus.COMPLETED
                changed = True
        db.commit()
        if changed:
            clear_event_cache()
    except Exception:
        db.rollback()
        log.exception("complete_events failed")
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(expire_bookings, "interval", minutes=1, id="expire_bookings", replace_existing=True)
    scheduler.add_job(send_reminders, "interval", minutes=30, id="send_reminders", replace_existing=True)
    scheduler.add_job(complete_shows, "interval", minutes=15, id="complete_shows", replace_existing=True)
    scheduler.add_job(complete_events, "cron", hour=0, minute=5, id="complete_events", replace_existing=True)
    scheduler.start()


# ---- Redis lock expiry -> live WebSocket update ----
_KEY = re.compile(r"^lock:show:(\d+):seat:(\d+)$")


def _on_expired(key: str):
    m = _KEY.match(key)
    if not m:
        return
    show_id, seat_id = int(m.group(1)), int(m.group(2))
    db = SessionLocal()
    try:
        seat = db.get(Seat, seat_id)
        if seat:
            manager.push(show_id, [{"seat_id": seat_id, "label": f"{seat.row_label}{seat.seat_number}",
                                    "status": "Available"}])
    finally:
        db.close()


def start_expiry_listener():
    r = get_redis()
    try:
        r.config_set("notify-keyspace-events", "Ex")
    except Exception:
        log.warning("Could not enable keyspace notifications; set notify-keyspace-events Ex in redis.conf")

    def run():
        while True:
            try:
                ps = get_redis().pubsub()
                ps.psubscribe("__keyevent@*__:expired")
                for msg in ps.listen():
                    if msg.get("type") == "pmessage":
                        _on_expired(msg["data"])
            except Exception:
                log.warning("expiry listener reconnecting")
                _time.sleep(3)

    threading.Thread(target=run, daemon=True, name="redis-expiry-listener").start()
