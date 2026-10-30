from sqlalchemy import (Column, Integer, String, Boolean, DateTime, Numeric, ForeignKey, UniqueConstraint)
from sqlalchemy.orm import relationship
from app.database import Base, TimestampMixin
from app.models.enums import (BookingStatus, PaymentMethod, PaymentStatus, RefundReason, RefundStatus,
                              WaitlistStatus, SeatCategory, enum_type)


class Booking(Base, TimestampMixin):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    booking_number = Column(String(30), unique=True, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    show_id = Column(Integer, ForeignKey("shows.id"), nullable=False, index=True)
    seat_count = Column(Integer, nullable=False)
    ticket_total = Column(Numeric(10, 2), nullable=False)
    convenience_fee = Column(Numeric(10, 2), nullable=False)
    gst_amount = Column(Numeric(10, 2), nullable=False)
    grand_total = Column(Numeric(10, 2), nullable=False)
    status = Column(enum_type(BookingStatus), default=BookingStatus.PENDING, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    confirmed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    entered_at = Column(DateTime, nullable=True)          # ticket scan time
    scanned_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    ticket_path = Column(String(255), nullable=True)
    reminder_sent = Column(Boolean, default=False, nullable=False)

    customer = relationship("User", back_populates="bookings", foreign_keys=[customer_id])
    show = relationship("Show", back_populates="bookings")
    booked_seats = relationship("BookedSeat", back_populates="booking", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="booking")
    refunds = relationship("Refund", back_populates="booking")


class BookedSeat(Base, TimestampMixin):
    __tablename__ = "booked_seats"
    # MySQL has no partial unique index. active_seat_id is filled only while the booking is
    # Confirmed and set to NULL on cancel; NULLs never collide, so this enforces
    # "one confirmed booking per seat per show".
    __table_args__ = (UniqueConstraint("show_id", "active_seat_id", name="uq_show_active_seat"),)

    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False, index=True)
    show_id = Column(Integer, ForeignKey("shows.id"), nullable=False, index=True)
    seat_id = Column(Integer, ForeignKey("seats.id"), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    active_seat_id = Column(Integer, nullable=True)

    booking = relationship("Booking", back_populates="booked_seats")
    seat = relationship("Seat")


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False, index=True)
    transaction_id = Column(String(40), unique=True, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(enum_type(PaymentMethod), nullable=False)
    status = Column(enum_type(PaymentStatus), nullable=False)
    paid_at = Column(DateTime, nullable=True)

    booking = relationship("Booking", back_populates="payments")


class Refund(Base, TimestampMixin):
    __tablename__ = "refunds"
    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)
    reason = Column(enum_type(RefundReason), nullable=False)
    status = Column(enum_type(RefundStatus), default=RefundStatus.PROCESSED, nullable=False)
    refunded_at = Column(DateTime, nullable=True)

    booking = relationship("Booking", back_populates="refunds")


class Waitlist(Base, TimestampMixin):
    __tablename__ = "waitlist"
    __table_args__ = (UniqueConstraint("show_id", "customer_id", name="uq_waitlist_show_customer"),)
    id = Column(Integer, primary_key=True)
    show_id = Column(Integer, ForeignKey("shows.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    seat_count = Column(Integer, nullable=False)
    category = Column(enum_type(SeatCategory), nullable=False)
    status = Column(enum_type(WaitlistStatus), default=WaitlistStatus.WAITING, nullable=False)
    joined_at = Column(DateTime, nullable=False)
    notified_at = Column(DateTime, nullable=True)

    show = relationship("Show", back_populates="waitlist")
    customer = relationship("User")
