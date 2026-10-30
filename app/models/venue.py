from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base, TimestampMixin
from app.models.enums import SeatCategory, enum_type


class Venue(Base, TimestampMixin):
    __tablename__ = "venues"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    city = Column(String(100), nullable=False, index=True)
    address = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    screens = relationship("Screen", back_populates="venue", cascade="all, delete-orphan")
    staff = relationship("User", back_populates="venue")


class Screen(Base, TimestampMixin):
    __tablename__ = "screens"
    id = Column(Integer, primary_key=True, index=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    total_seats = Column(Integer, default=0, nullable=False)

    venue = relationship("Venue", back_populates="screens")
    seats = relationship("Seat", back_populates="screen", cascade="all, delete-orphan")
    shows = relationship("Show", back_populates="screen")


class Seat(Base, TimestampMixin):
    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("screen_id", "row_label", "seat_number", name="uq_seat_position"),)

    id = Column(Integer, primary_key=True, index=True)
    screen_id = Column(Integer, ForeignKey("screens.id"), nullable=False, index=True)
    row_label = Column(String(5), nullable=False)
    seat_number = Column(Integer, nullable=False)
    category = Column(enum_type(SeatCategory), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    screen = relationship("Screen", back_populates="seats")
