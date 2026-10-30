import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.websockets.manager import manager


def test_ws_full_map_then_live_update(client, world):
    tok = world["h"]["c1"]["Authorization"].split()[1]
    tok2 = world["h"]["c2"]["Authorization"].split()[1]
    with TestClient(app) as c:  # runs lifespan: sets manager.loop
        with pytest.raises(Exception):
            with c.websocket_connect(f"/ws/shows/{world['show']}/seats?token=bad"):
                pass
        with c.websocket_connect(f"/ws/shows/{world['show']}/seats?token={tok}") as w1, \
             c.websocket_connect(f"/ws/shows/{world['show']}/seats?token={tok2}") as w2:
            m1, m2 = w1.receive_json(), w2.receive_json()
            assert m1["type"] == "seat_map" and len(m1["seats"]) == 16
            r = c.post(f"/shows/{world['show']}/lock-seats", headers=world["h"]["c1"],
                       json={"seat_ids": [world["ids"]["A1"]]})
            assert r.status_code == 200
            u1, u2 = w1.receive_json(), w2.receive_json()
            assert u1["seats"][0]["status"] == "Yours" and u2["seats"][0]["status"] == "Locked"
            assert u2["message"] == "Seat A1 is now Locked"
