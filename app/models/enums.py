import enum
from sqlalchemy import Enum


def enum_type(e):
    return Enum(e, values_callable=lambda x: [m.value for m in x], name=e.__name__.lower())


class Role(str, enum.Enum):
    ADMIN = "Admin"
    ORGANIZER = "Organizer"
    CUSTOMER = "Customer"
    STAFF = "Staff"


class SeatCategory(str, enum.Enum):
    SILVER = "Silver"
    GOLD = "Gold"
    RECLINER = "Recliner"


class EventCategory(str, enum.Enum):
    MOVIE = "Movie"
    CONCERT = "Concert"
    COMEDY = "Comedy"
    SPORTS = "Sports"
    WORKSHOP = "Workshop"


class AgeRating(str, enum.Enum):
    U = "U"
    UA = "UA"
    A = "A"


class EventStatus(str, enum.Enum):
    DRAFT = "Draft"
    PUBLISHED = "Published"
    CANCELLED = "Cancelled"
    COMPLETED = "Completed"


class ShowStatus(str, enum.Enum):
    SCHEDULED = "Scheduled"
    CANCELLED = "Cancelled"
    COMPLETED = "Completed"


class BookingStatus(str, enum.Enum):
    PENDING = "Pending Payment"
    CONFIRMED = "Confirmed"
    CANCELLED = "Cancelled"
    EXPIRED = "Expired"


class PaymentMethod(str, enum.Enum):
    UPI = "UPI"
    CARD = "Card"
    NET_BANKING = "Net Banking"


class PaymentStatus(str, enum.Enum):
    SUCCESS = "Success"
    FAILED = "Failed"
    REFUNDED = "Refunded"


class RefundReason(str, enum.Enum):
    CUSTOMER = "Customer Cancellation"
    SHOW = "Show Cancellation"
    LATE = "Late Payment"


class RefundStatus(str, enum.Enum):
    PROCESSED = "Processed"


class WaitlistStatus(str, enum.Enum):
    WAITING = "Waiting"
    NOTIFIED = "Notified"
