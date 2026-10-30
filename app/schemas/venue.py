from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from app.models.enums import SeatCategory


class VenueIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    city: str = Field(min_length=2, max_length=100)
    address: str = Field(min_length=2, max_length=255)
    is_active: bool = True


class VenueUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    city: Optional[str] = Field(None, min_length=2, max_length=100)
    address: Optional[str] = Field(None, min_length=2, max_length=255)
    is_active: Optional[bool] = None


class VenueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    city: str
    address: str
    is_active: bool


class ScreenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    total_seats: int = Field(0, ge=0)


class ScreenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    venue_id: int
    name: str
    total_seats: int


class SeatBulkIn(BaseModel):
    row_start: str = Field(examples=["A"])
    row_end: str = Field(examples=["E"])
    seats_per_row: int = Field(ge=1, le=100, examples=[12])
    category: SeatCategory

    @field_validator("row_start", "row_end")
    @classmethod
    def _letter(cls, v):
        v = v.strip().upper()
        if len(v) != 1 or not v.isalpha():
            raise ValueError("Row must be a single letter A-Z")
        return v

    @model_validator(mode="after")
    def _order(self):
        if self.row_start > self.row_end:
            raise ValueError("row_start must be <= row_end")
        return self


class SeatStatusIn(BaseModel):
    is_active: bool
