from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base, TimestampMixin
from app.models.enums import Role, enum_type


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    phone = Column(String(10), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(enum_type(Role), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_approved = Column(Boolean, default=False, nullable=False)  # organizer approval
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=True)  # staff only

    venue = relationship("Venue", back_populates="staff")
    events = relationship("Event", back_populates="organizer")
    bookings = relationship("Booking", back_populates="customer", foreign_keys="Booking.customer_id")
