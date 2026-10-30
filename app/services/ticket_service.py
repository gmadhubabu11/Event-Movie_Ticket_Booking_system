import hmac
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Optional
import qrcode
from reportlab.lib.pagesizes import A5
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from app.config import settings


def sign(booking_number: str) -> str:
    return hmac.new(settings.QR_HMAC_SECRET.encode(), booking_number.encode(), hashlib.sha256).hexdigest()


def qr_value(booking_number: str) -> str:
    return f"{booking_number}|{sign(booking_number)}"


def verify_qr(value: str) -> Optional[str]:
    """Returns booking_number if the signature is valid, else None."""
    if "|" not in value:
        return None
    number, sig = value.strip().rsplit("|", 1)
    if hmac.compare_digest(sig, sign(number)):
        return number
    return None


def seat_labels(booking) -> list:
    seats = sorted((bs.seat for bs in booking.booked_seats), key=lambda s: (s.row_label, s.seat_number))
    return [f"{s.row_label}{s.seat_number}" for s in seats]


def generate_ticket_pdf(booking) -> str:
    show = booking.show
    event, screen = show.event, show.screen
    venue = screen.venue
    out_dir = Path(settings.TICKETS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{booking.booking_number}.pdf"

    img = qrcode.make(qr_value(booking.booking_number))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    c = canvas.Canvas(str(path), pagesize=A5)
    w, h = A5
    y = h - 50
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, y, "E-TICKET")
    y -= 30
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, event.title)
    c.setFont("Helvetica", 11)
    rows = [
        ("Booking No", booking.booking_number),
        ("Venue", f"{venue.name}, {venue.city}"),
        ("Screen", screen.name),
        ("Date", show.show_date.strftime("%d %b %Y")),
        ("Time", show.start_time.strftime("%I:%M %p")),
        ("Seats", ", ".join(seat_labels(booking))),
        ("Amount paid", f"Rs. {booking.grand_total}"),
    ]
    y -= 28
    for k, v in rows:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(40, y, f"{k}:")
        c.setFont("Helvetica", 10)
        c.drawString(130, y, str(v))
        y -= 20
    c.drawImage(ImageReader(buf), (w - 170) / 2, 40, width=170, height=170)
    c.setFont("Helvetica", 8)
    c.drawCentredString(w / 2, 28, "Show this QR code at the entry gate. Valid for one entry only.")
    c.showPage()
    c.save()
    return str(path)
