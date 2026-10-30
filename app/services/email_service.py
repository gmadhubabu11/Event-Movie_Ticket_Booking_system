import logging
import smtplib
from email.message import EmailMessage
from typing import Optional
from app.config import settings

log = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str, attachment: Optional[str] = None) -> None:
    """Never raises: an email failure must not break the main action."""
    if not settings.SMTP_HOST:
        log.warning("SMTP not configured; skipped email to %s: %s", to, subject)
        return
    try:
        msg = EmailMessage()
        msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if attachment:
            with open(attachment, "rb") as f:
                msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                                   filename=attachment.replace("\\", "/").split("/")[-1])
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as s:
            s.starttls()
            if settings.SMTP_USER:
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
    except Exception:
        log.exception("Failed to send email to %s (%s)", to, subject)


def queue_email(bg, to: str, subject: str, body: str, attachment: Optional[str] = None) -> None:
    if bg is not None:
        bg.add_task(send_email, to, subject, body, attachment)
    else:
        send_email(to, subject, body, attachment)
