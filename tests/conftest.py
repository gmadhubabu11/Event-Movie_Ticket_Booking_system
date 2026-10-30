import os
import tempfile

os.environ.update(
    DATABASE_URL="sqlite:///./test.db", REDIS_URL="redis://localhost:6379/0", JWT_SECRET_KEY="test-secret",
    QR_HMAC_SECRET="test-qr", LOGIN_RATE_LIMIT="1000", LOCK_RATE_LIMIT="1000",
    TICKETS_DIR=tempfile.mkdtemp(),
)
import fakeredis  # noqa: E402
import app.redis_client as rc  # noqa: E402

rc.redis_client = fakeredis.FakeRedis(decode_responses=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models import User  # noqa: E402
from app.models.enums import Role  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)  # tests only; the app itself uses Alembic
    rc.redis_client.flushall()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def make_user(db, name, email, role, venue_id=None, approved=True):
    u = User(name=name, email=email, phone="9999999999", password_hash=hash_password("Passw0rd!"), role=role,
             is_active=True, is_approved=approved, venue_id=venue_id)
    db.add(u)
    db.commit()
    return u


def login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": "Passw0rd!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def world(client, db):
    """Venue + screen (2 rows x 6 seats, Silver/Gold) + users + a published show 3h from now."""
    from datetime import datetime, timedelta
    make_user(db, "Admin", "admin@t.com", Role.ADMIN)
    make_user(db, "Org", "org@t.com", Role.ORGANIZER)
    make_user(db, "Cust1", "c1@t.com", Role.CUSTOMER)
    make_user(db, "Cust2", "c2@t.com", Role.CUSTOMER)
    h = {k: login(client, f"{e}@t.com") for k, e in
         {"admin": "admin", "org": "org", "c1": "c1", "c2": "c2"}.items()}
    v = client.post("/venues", json={"name": "Venue One", "city": "Hyderabad", "address": "Banjara Hills"}, headers=h["admin"]).json()
    sc = client.post(f"/venues/{v['id']}/screens", json={"name": "Screen 1"}, headers=h["admin"]).json()
    client.post(f"/screens/{sc['id']}/seats/bulk", json={"row_start": "A", "row_end": "A", "seats_per_row": 8,
                                                          "category": "Silver"}, headers=h["admin"])
    client.post(f"/screens/{sc['id']}/seats/bulk", json={"row_start": "B", "row_end": "B", "seats_per_row": 8,
                                                          "category": "Gold"}, headers=h["admin"])
    make_user(db, "Staff", "staff@t.com", Role.STAFF, venue_id=v["id"])
    h["staff"] = login(client, "staff@t.com")
    ev = client.post("/events", json={"title": "Movie", "category": "Movie", "language": "Telugu",
                                      "duration_minutes": 120, "age_rating": "U", "status": "Published"},
                     headers=h["org"])
    assert ev.status_code == 201, ev.text
    start = datetime.now() + timedelta(hours=3)
    sh = client.post(f"/events/{ev.json()['id']}/shows", headers=h["org"], json={
        "screen_id": sc["id"], "show_date": start.date().isoformat(), "start_time": start.strftime("%H:%M:%S"),
        "prices": [{"category": "Silver", "price": 200}, {"category": "Gold", "price": 300}]})
    assert sh.status_code == 201, sh.text
    seats = client.get(f"/shows/{sh.json()['id']}/seats", headers=h["c1"]).json()["seats"]
    ids = {s["label"]: s["seat_id"] for s in seats}
    return {"h": h, "show": sh.json()["id"], "ids": ids, "venue": v["id"], "screen": sc["id"],
            "event": ev.json()["id"]}
