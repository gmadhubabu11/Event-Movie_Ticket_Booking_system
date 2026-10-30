import re
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from app.models.enums import Role


def _phone(v: str) -> str:
    if not re.fullmatch(r"\d{10}", v):
        raise ValueError("Phone number must be exactly 10 digits")
    return v


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone: str
    password: str = Field(min_length=6, max_length=72)
    role: Literal["Customer", "Organizer"]

    _v = field_validator("phone")(_phone)


class StaffCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone: str
    password: str = Field(min_length=6, max_length=72)
    venue_id: int

    _v = field_validator("phone")(_phone)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    phone: str
    role: Role
    is_active: bool
    is_approved: bool
    venue_id: Optional[int] = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
