import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from app.auth.deps import user_from_token
from fastapi import HTTPException
from app.database import SessionLocal
from app.models import Show
from app.redis_client import get_redis
from app.services.seat_service import ws_seat_map
from app.websockets.manager import manager

router = APIRouter(tags=["Live Seat Map"])
log = logging.getLogger(__name__)


def _auth_and_map(token: str, show_id: int):
    db = SessionLocal()
    try:
        user = user_from_token(token, db)
        show = db.get(Show, show_id)
        if not show:
            return None, None
        return user.id, ws_seat_map(db, get_redis(), show, user.id)
    except HTTPException:
        return None, None
    finally:
        db.close()


@router.websocket("/ws/shows/{show_id}/seats")
async def seat_socket(websocket: WebSocket, show_id: int, token: str = ""):
    user_id, seat_map = await run_in_threadpool(_auth_and_map, token, show_id)
    if user_id is None:
        await websocket.close(code=1008)  # invalid token / unknown show
        return
    await manager.connect(show_id, websocket, user_id)
    try:
        await websocket.send_json(seat_map)  # full map first
        while True:
            await websocket.receive_text()  # keep alive; client messages ignored
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket error")
    finally:
        manager.disconnect(show_id, websocket)


@router.get("/seat-map-test", include_in_schema=False)
def seat_map_test():
    return FileResponse("static/seat_map_test.html")
