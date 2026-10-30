import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from app.jobs.scheduler import scheduler, start_expiry_listener, start_scheduler
from app.routers import admin, auth, bookings, events, reports, seats, tickets, venues, waitlist, ws
from app.websockets.manager import manager

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.loop = asyncio.get_running_loop()
    start_scheduler()          # jobs start automatically with the app
    start_expiry_listener()    # lock expiry -> live seat map push
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Event & Movie Ticket Booking System", version="1.0.0", lifespan=lifespan)


@app.exception_handler(IntegrityError)
async def integrity_handler(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "Conflict: duplicate or invalid data"})


for r in (auth, admin, venues, events, seats, bookings, tickets, waitlist, reports, ws):
    app.include_router(r.router)


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
