from typing import List, Literal
from pydantic import BaseModel, Field
from app.models.enums import PaymentMethod, SeatCategory


class LockIn(BaseModel):
    seat_ids: List[int] = Field(min_length=1, max_length=10)


class BookingIn(BaseModel):
    show_id: int
    seat_ids: List[int] = Field(min_length=1, max_length=10)


class PayIn(BaseModel):
    payment_method: PaymentMethod
    simulate_status: Literal["Success", "Failed"] = "Success"


class ScanIn(BaseModel):
    qr_value: str = Field(min_length=5)


class WaitlistIn(BaseModel):
    seat_count: int = Field(ge=1, le=10)
    category: SeatCategory
