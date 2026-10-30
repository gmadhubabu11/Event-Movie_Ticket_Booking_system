from sqlalchemy import Column, Integer, String, Text, Date, Time, DateTime, Numeric, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base, TimestampMixin
from app.models.enums import (EventCategory, AgeRating, EventStatus, ShowStatus, SeatCategory, enum_type)


class Event(Base, TimestampMixin):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True)
    organizer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, index=True)
    category = Column(enum_type(EventCategory), nullable=False)
    language = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    duration_minutes = Column(Integer, nullable=False)
    age_rating = Column(enum_type(AgeRating), nullable=False)
    status = Column(enum_type(EventStatus), default=EventStatus.DRAFT, nullable=False)

    organizer = relationship("User", back_populates="events")
    shows = relationship("Show", back_populates="event", cascade="all, delete-orphan")


class Show(Base, TimestampMixin):
    __tablename__ = "shows"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    screen_id = Column(Integer, ForeignKey("screens.id"), nullable=False, index=True)
    show_date = Column(Date, nullable=False, index=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    start_at = Column(DateTime, nullable=False, index=True)
    end_at = Column(DateTime, nullable=False)
    booking_closes_at = Column(DateTime, nullable=False)
    status = Column(enum_type(ShowStatus), default=ShowStatus.SCHEDULED, nullable=False)

    event = relationship("Event", back_populates="shows")
    screen = relationship("Screen", back_populates="shows")
    prices = relationship("SeatCategoryPrice", back_populates="show", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="show")
    waitlist = relationship("Waitlist", back_populates="show")


class SeatCategoryPrice(Base, TimestampMixin):
    __tablename__ = "seat_category_prices"
    __table_args__ = (UniqueConstraint("show_id", "category", name="uq_show_category"),)
    id = Column(Integer, primary_key=True)
    show_id = Column(Integer, ForeignKey("shows.id"), nullable=False, index=True)
    category = Column(enum_type(SeatCategory), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)

    show = relationship("Show", back_populates="prices")
