from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.auth.deps import get_current_user, require_roles
from app.config import settings
from app.database import get_db
from app.models import Show
from app.models.enums import Role, SeatCategory
from app.redis_client import get_redis
from app.schemas.booking import LockIn
from app.services.seat_service import build_seat_map, lock_seats, release_seats, suggest_best_seats
from app.utils.rate_limit import hit

router = APIRouter(tags=["Seats & Locking"])


def _show(db, show_id):
    s = db.get(Show, show_id)
    if not s:
        raise HTTPException(404, "Show not found")
    return s


@router.get("/shows/{show_id}/seats")
def seat_map(show_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    show = _show(db, show_id)  # never cached: always read live from DB + Redis
    return {"show_id": show.id, "show_status": show.status,
            "seats": build_seat_map(db, get_redis(), show, user.id)}


@router.post("/shows/{show_id}/lock-seats")
def lock(show_id: int, body: LockIn, db: Session = Depends(get_db), user=Depends(require_roles(Role.CUSTOMER))):
    hit(f"rl:lock:{user.id}", settings.LOCK_RATE_LIMIT)
    return lock_seats(db, get_redis(), _show(db, show_id), user, body.seat_ids)


@router.delete("/shows/{show_id}/lock-seats")
def unlock(show_id: int, body: LockIn, db: Session = Depends(get_db),
           user=Depends(require_roles(Role.CUSTOMER))):
    return release_seats(db, get_redis(), _show(db, show_id), user, body.seat_ids)


@router.get("/shows/{show_id}/best-seats")
def best_seats(show_id: int, count: int = Query(2, ge=1, le=10), category: Optional[SeatCategory] = None,
               db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Bonus: suggest the best adjacent available seats."""
    show = _show(db, show_id)
    seats = build_seat_map(db, get_redis(), show, user.id)
    run = suggest_best_seats(seats, count, category.value if category else None)
    if not run:
        raise HTTPException(404, "No adjacent seats available for that count/category")
    return {"seats": [{"seat_id": s["seat_id"], "label": s["label"], "category": s["category"],
                       "price": s["price"]} for s in run]}
