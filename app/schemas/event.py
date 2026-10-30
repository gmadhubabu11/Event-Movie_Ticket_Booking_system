from datetime import date, time
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from app.models.enums import EventCategory, AgeRating, SeatCategory


class EventIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    category: EventCategory
    language: str = Field(min_length=2, max_length=50)
    description: Optional[str] = None
    duration_minutes: int = Field(ge=30, le=300)
    age_rating: AgeRating
    status: str = "Draft"

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in ("Draft", "Published"):
            raise ValueError("status must be Draft or Published on creation")
        return v


class EventUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=200)
    category: Optional[EventCategory] = None
    language: Optional[str] = Field(None, min_length=2, max_length=50)
    description: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, ge=30, le=300)
    age_rating: Optional[AgeRating] = None
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in ("Draft", "Published", "Cancelled"):
            raise ValueError("status must be Draft, Published or Cancelled")
        return v


class PriceIn(BaseModel):
    category: SeatCategory
    price: float = Field(gt=0)


class ShowIn(BaseModel):
    screen_id: int
    show_date: date
    start_time: time
    prices: List[PriceIn] = Field(min_length=1)

    @field_validator("show_date")
    @classmethod
    def _future(cls, v):
        if v < date.today():
            raise ValueError("Show date must be in the future")
        return v


class ShowUpdate(BaseModel):
    screen_id: Optional[int] = None
    show_date: Optional[date] = None
    start_time: Optional[time] = None
    prices: Optional[List[PriceIn]] = Field(None, min_length=1)

    @field_validator("show_date")
    @classmethod
    def _future(cls, v):
        if v is not None and v < date.today():
            raise ValueError("Show date must be in the future")
        return v
