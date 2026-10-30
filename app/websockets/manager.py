import asyncio
import logging
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class ConnectionManager:
    """Keeps WebSocket connections per show. push*() methods are thread-safe so that
    sync routes, APScheduler jobs and the Redis-expiry thread can all call them."""

    def __init__(self):
        self.rooms: Dict[int, List[Tuple[object, int]]] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, show_id: int, ws, user_id: int):
        await ws.accept()
        self.rooms.setdefault(show_id, []).append((ws, user_id))

    def disconnect(self, show_id: int, ws):
        room = self.rooms.get(show_id, [])
        self.rooms[show_id] = [c for c in room if c[0] is not ws]
        if not self.rooms[show_id]:
            self.rooms.pop(show_id, None)

    async def _send(self, show_id: int, ws, data: dict):
        try:
            await ws.send_json(data)
            return True
        except Exception:
            self.disconnect(show_id, ws)
            return False

    async def _broadcast_updates(self, show_id: int, updates: List[dict]):
        for ws, uid in list(self.rooms.get(show_id, [])):
            seats = [{"seat_id": u["seat_id"], "label": u["label"],
                      "status": "Yours" if u["status"] == "Locked" and u.get("owner_id") == uid else u["status"]}
                     for u in updates]
            msg = "; ".join(f"Seat {s['label']} is now {s['status']}" for s in seats)
            await self._send(show_id, ws, {"type": "seat_update", "show_id": show_id, "seats": seats,
                                           "message": msg})

    async def _broadcast_raw(self, show_id: int, data: dict):
        for ws, _ in list(self.rooms.get(show_id, [])):
            await self._send(show_id, ws, data)

    async def _to_user(self, user_id: int, data: dict):
        for show_id, room in list(self.rooms.items()):
            for ws, uid in list(room):
                if uid == user_id:
                    await self._send(show_id, ws, data)

    def _run(self, coro):
        if self.loop is None or self.loop.is_closed():
            coro.close()
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        except Exception:
            log.exception("ws push failed")

    def push(self, show_id: int, updates: List[dict]):
        if updates and self.rooms.get(show_id):
            self._run(self._broadcast_updates(show_id, updates))

    def push_raw(self, show_id: int, data: dict):
        if self.rooms.get(show_id):
            self._run(self._broadcast_raw(show_id, data))

    def push_user(self, user_id: int, data: dict):
        self._run(self._to_user(user_id, data))


manager = ConnectionManager()
