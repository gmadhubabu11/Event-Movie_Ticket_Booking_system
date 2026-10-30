from datetime import datetime


def now() -> datetime:
    """Naive local time. All DB datetimes in this project are naive local time."""
    return datetime.now()
