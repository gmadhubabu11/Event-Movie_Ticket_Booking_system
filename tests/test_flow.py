from datetime import datetime, timedelta
from app.models import Show, Booking
from app.redis_client import get_redis


def lock(client, w, who, labels):
    return client.post(f"/shows/{w['show']}/lock-seats", headers=w["h"][who],
                       json={"seat_ids": [w["ids"][l] for l in labels]})


def book(client, w, who, labels):
    return client.post("/bookings", headers=w["h"][who],
                       json={"show_id": w["show"], "seat_ids": [w["ids"][l] for l in labels]})


def pay(client, w, who, booking_id, status="Success"):
    return client.post(f"/bookings/{booking_id}/pay", headers=w["h"][who],
                       json={"payment_method": "UPI", "simulate_status": status})


def test_auth_rules(client, db):
    r = client.post("/auth/register", json={"name": "Bob", "email": "b@t.com", "phone": "12345", "password": "secret1",
                                            "role": "Customer"})
    assert r.status_code == 422                                   # phone must be 10 digits
    r = client.post("/auth/register", json={"name": "Bob", "email": "b@t.com", "phone": "1234567890",
                                            "password": "secret1", "role": "Admin"})
    assert r.status_code == 422                                   # cannot self-register as Admin
    r = client.post("/auth/register", json={"name": "Bob", "email": "b@t.com", "phone": "1234567890",
                                            "password": "secret1", "role": "Organizer"})
    assert r.status_code == 201 and r.json()["is_approved"] is False
    assert client.post("/auth/register", json={"name": "Bob", "email": "b@t.com", "phone": "1234567890",
                                               "password": "secret1", "role": "Customer"}).status_code == 409
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    h = {"Authorization": "Bearer " + client.post("/auth/login", data={"username": "b@t.com", "password": "secret1"}).json()["access_token"]}
    assert client.post("/events", headers=h, json={"title": "X", "category": "Movie", "language": "en",
                                                   "duration_minutes": 100, "age_rating": "U"}).status_code == 403
    assert client.get("/reports/top-events", headers=h).status_code == 403


def test_seat_locking_all_or_nothing(client, world):
    assert lock(client, world, "c1", ["A1", "A2"]).status_code == 200
    r = lock(client, world, "c2", ["A5", "A2"])                  # A2 taken -> nothing locked
    assert r.status_code == 409
    assert r.json()["detail"]["unavailable_seats"][0]["label"] == "A2"
    seats = {s["label"]: s["status"] for s in client.get(f"/shows/{world['show']}/seats", headers=world["h"]["c2"]).json()["seats"]}
    assert seats["A5"] == "Available" and seats["A2"] == "Locked"
    # only own locks can be released
    r = client.request("DELETE", f"/shows/{world['show']}/lock-seats", headers=world["h"]["c2"],
                       json={"seat_ids": [world["ids"]["A2"]]})
    assert r.status_code == 403
    r = client.request("DELETE", f"/shows/{world['show']}/lock-seats", headers=world["h"]["c1"],
                       json={"seat_ids": [world["ids"]["A2"]]})
    assert r.status_code == 200


def test_single_empty_seat_rule(client, world):
    assert lock(client, world, "c2", ["A4"]).status_code == 200   # someone else holds A4
    r = lock(client, world, "c1", ["A1", "A2"])                   # would leave A3 alone
    assert r.status_code == 400 and "A3" in r.json()["detail"]["seats_left_empty"]
    assert lock(client, world, "c1", ["A1", "A2", "A3"]).status_code == 200


def test_max_ten_seats(client, world):
    ids = list(world["ids"])[:11]
    r = client.post(f"/shows/{world['show']}/lock-seats", headers=world["h"]["c1"],
                    json={"seat_ids": [world["ids"][l] for l in ids[:10]]})
    assert r.status_code == 200
    r = lock(client, world, "c1", [ids[10]])
    assert r.status_code == 400


def test_booking_pay_ticket_scan(client, world, db):
    assert book(client, world, "c1", ["B1", "B2"]).status_code == 409   # not locked yet
    assert lock(client, world, "c1", ["B1", "B2"]).status_code == 200
    r = book(client, world, "c1", ["B1", "B2"])
    assert r.status_code == 201
    b = r.json()
    assert b["status"] == "Pending Payment"
    assert b["ticket_total"] == 600 and b["convenience_fee"] == 60 and b["gst_amount"] == 118.8
    assert b["grand_total"] == 778.8

    f = pay(client, world, "c1", b["id"], "Failed")
    assert f.status_code == 200 and f.json()["payment"]["status"] == "Failed"
    assert f.json()["booking"]["status"] == "Pending Payment"
    ok = pay(client, world, "c1", b["id"])
    assert ok.status_code == 200 and ok.json()["booking"]["status"] == "Confirmed"
    assert ok.json()["payment"]["amount"] == 778.8
    assert pay(client, world, "c1", b["id"]).status_code == 409        # already confirmed
    assert len(client.get(f"/bookings/{b['id']}/payments", headers=world["h"]["c1"]).json()) == 2
    seats = {s["label"]: s["status"] for s in client.get(f"/shows/{world['show']}/seats", headers=world["h"]["c2"]).json()["seats"]}
    assert seats["B1"] == "Booked"
    assert lock(client, world, "c2", ["B1"]).status_code == 409
    # customers see only their own bookings
    assert client.get(f"/bookings/{b['id']}", headers=world["h"]["c2"]).status_code == 403
    assert client.get("/bookings", headers=world["h"]["c2"]).json()["total"] == 0
    assert client.get("/bookings", headers=world["h"]["org"]).json()["total"] == 1

    t = client.get(f"/bookings/{b['id']}/ticket", headers=world["h"]["c1"])
    assert t.status_code == 200 and t.content.startswith(b"%PDF")

    # scanning: build the signed QR value, move the show to start in 30 min
    from app.services.ticket_service import qr_value
    qr = qr_value(b["booking_number"])
    r = client.post("/tickets/scan", headers=world["h"]["staff"], json={"qr_value": qr})
    assert r.status_code == 400                                        # too early (3h before)
    s = db.get(Show, world["show"])
    s.start_at = datetime.now() + timedelta(minutes=30)
    s.end_at = s.start_at + timedelta(hours=2)
    db.commit()
    assert client.post("/tickets/scan", headers=world["h"]["c1"], json={"qr_value": qr}).status_code == 403
    assert client.post("/tickets/scan", headers=world["h"]["staff"], json={"qr_value": "BKG-1|fake"}).status_code == 400
    r = client.post("/tickets/scan", headers=world["h"]["staff"], json={"qr_value": qr})
    assert r.status_code == 200 and r.json()["seat_count"] == 2 and r.json()["customer_name"] == "Cust1"
    r = client.post("/tickets/scan", headers=world["h"]["staff"], json={"qr_value": qr})
    assert r.status_code == 409 and "already used" in r.json()["detail"]
    e = client.get(f"/shows/{world['show']}/entries", headers=world["h"]["staff"]).json()
    assert e["total_bookings"] == 1 and e["entered_bookings"] == 1


def test_late_payment_auto_refund(client, world):
    assert lock(client, world, "c1", ["A1", "A2"]).status_code == 200
    b = book(client, world, "c1", ["A1", "A2"]).json()
    r = get_redis()
    for l in ("A1", "A2"):                                             # simulate lock expiry
        r.delete(f"lock:show:{world['show']}:seat:{world['ids'][l]}")
    assert lock(client, world, "c2", ["A1", "A2"]).status_code == 200  # someone else grabs them
    res = pay(client, world, "c1", b["id"])
    assert res.json()["payment"]["status"] == "Refunded"
    assert res.json()["booking"]["status"] == "Cancelled"
    refunds = client.get(f"/bookings/{b['id']}/refunds", headers=world["h"]["c1"]).json()
    assert refunds[0]["reason"] == "Late Payment" and refunds[0]["amount"] == b["grand_total"]


def test_cancellation_policy_and_waitlist(client, world, db):
    labels = [l for l in world["ids"]]
    # c1 books everything except nothing: fill the whole show with c1 (8 + 8 seats, 10-seat cap) -> use 2 customers
    def full_book(who, chosen):
        assert client.post(f"/shows/{world['show']}/lock-seats", headers=world["h"][who],
                           json={"seat_ids": [world["ids"][l] for l in chosen]}).status_code == 200
        b = book(client, world, who, chosen).json()
        assert pay(client, world, who, b["id"]).json()["booking"]["status"] == "Confirmed"
        return b
    b1 = full_book("c1", labels[:8])
    full_book("c2", labels[8:])
    # sold out -> waitlist works only now; org/admin cannot join
    c3 = client.post("/auth/register", json={"name": "Cust3", "email": "c3@t.com", "phone": "9999999991",
                                             "password": "Passw0rd!", "role": "Customer"})
    assert c3.status_code == 201
    from tests.conftest import login
    h3 = login(client, "c3@t.com")
    r = client.post(f"/shows/{world['show']}/waitlist", headers=h3, json={"seat_count": 2, "category": "Silver"})
    assert r.status_code == 201
    assert client.post(f"/shows/{world['show']}/waitlist", headers=h3, json={"seat_count": 2, "category": "Silver"}).status_code == 409
    assert client.post(f"/shows/{world['show']}/waitlist", headers=world["h"]["c1"],
                       json={"seat_count": 1, "category": "Silver"}).status_code == 409   # already booked

    # 3h before show -> 50% of ticket total (8 x 200 = 1600 -> 800)
    r = client.put(f"/bookings/{b1['id']}/cancel", headers=world["h"]["c1"])
    assert r.status_code == 200 and r.json()["refund_amount"] == 800
    s = client.get(f"/shows/{world['show']}/seats", headers=h3).json()["seats"]
    assert all(x["status"] == "Available" for x in s if x["label"].startswith("A"))
    wl = client.get(f"/shows/{world['show']}/waitlist", headers=world["h"]["admin"]).json()
    assert wl["items"][0]["status"] == "Notified"

    # >24h -> 100%
    b2 = full_book("c1", labels[:2])
    sh = db.get(Show, world["show"]); sh.start_at = datetime.now() + timedelta(hours=30); db.commit()
    r = client.put(f"/bookings/{b2['id']}/cancel", headers=world["h"]["c1"])
    assert r.json()["refund_amount"] == 400
    # <2h -> not allowed
    b3 = full_book("c1", labels[:2])
    sh = db.get(Show, world["show"]); sh.start_at = datetime.now() + timedelta(hours=1); db.commit()
    assert client.put(f"/bookings/{b3['id']}/cancel", headers=world["h"]["c1"]).status_code == 400


def test_show_cancel_refunds_everything(client, world):
    assert lock(client, world, "c1", ["A1"]).status_code == 200
    b = book(client, world, "c1", ["A1"]).json()
    pay(client, world, "c1", b["id"])
    r = client.put(f"/shows/{world['show']}/cancel", headers=world["h"]["org"])
    assert r.status_code == 200 and r.json()["cancelled_bookings"] == 1
    ref = client.get(f"/bookings/{b['id']}/refunds", headers=world["h"]["c1"]).json()[0]
    assert ref["reason"] == "Show Cancellation" and ref["amount"] == b["grand_total"]   # fee + GST included
    assert lock(client, world, "c2", ["A5"]).status_code == 400                           # cancelled show


def test_show_rules(client, world):
    h = world["h"]["org"]
    ev = world["event"]
    start = datetime.now() + timedelta(hours=3, minutes=130)       # overlaps within the 15-min gap
    body = {"screen_id": world["screen"], "show_date": start.date().isoformat(),
            "start_time": start.strftime("%H:%M:%S"),
            "prices": [{"category": "Silver", "price": 100}, {"category": "Gold", "price": 100}]}
    assert client.post(f"/events/{ev}/shows", headers=h, json=body).status_code == 409
    body["prices"] = [{"category": "Silver", "price": 100}]
    assert client.post(f"/events/{ev}/shows", headers=h, json=body).status_code == 400  # missing Gold price
    body["show_date"] = "2020-01-01"
    assert client.post(f"/events/{ev}/shows", headers=h, json=body).status_code == 422  # past date


def test_event_listing_cache_filters_reports(client, world):
    r = client.get("/events?city=Hyderabad&category=Movie&language=Telugu&skip=0&limit=10")
    assert r.status_code == 200 and r.json()["total"] == 1
    assert client.get("/events?city=Mumbai").json()["total"] == 0
    assert client.get("/events?min_price=250").json()["total"] == 1
    assert client.get("/events?max_price=100").json()["total"] == 0
    assert client.get("/events?sort_by=price&order=desc").status_code == 200
    assert get_redis().keys("cache:events:*")                         # cached
    client.put(f"/events/{world['event']}", headers=world["h"]["org"], json={"title": "Renamed"})
    assert client.get("/events").json()["items"][0]["title"] == "Renamed"   # cache cleared on update
    for p in ("admin-dashboard", "top-events", "refunds", "event-sales", "occupancy"):
        assert client.get(f"/reports/{p}", headers=world["h"]["admin"]).status_code == 200, p
    assert client.get("/reports/event-sales/export", headers=world["h"]["org"]).status_code == 200
    assert client.get("/reports/admin-dashboard", headers=world["h"]["org"]).status_code == 403


def test_best_seats_and_layout(client, world):
    r = client.get(f"/shows/{world['show']}/best-seats?count=3&category=Gold", headers=world["h"]["c1"])
    assert r.status_code == 200 and len(r.json()["seats"]) == 3
    lay = client.get(f"/screens/{world['screen']}/layout", headers=world["h"]["c1"]).json()
    assert [row["row_label"] for row in lay["rows"]] == ["A", "B"]
    sid = world["ids"]["A1"]
    client.put(f"/seats/{sid}/status", headers=world["h"]["admin"], json={"is_active": False})
    assert lock(client, world, "c1", ["A1"]).status_code == 409        # inactive seat can't be locked


def test_rate_limit(client, world, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "LOCK_RATE_LIMIT", 2)
    assert lock(client, world, "c1", ["A1"]).status_code == 200
    assert lock(client, world, "c1", ["A1"]).status_code == 200
    r = lock(client, world, "c1", ["A1"])
    assert r.status_code == 429 and r.json()["detail"]["retry_after_seconds"] > 0
