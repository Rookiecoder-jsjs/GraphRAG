"""User-related Pydantic models."""
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class UserBase(BaseModel):
    """Base user model."""
    username: str = Field(..., min_length=3, max_length=50)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        if not _USERNAME_RE.match(value):
            raise ValueError(
                "username may only contain letters, digits, '.', '_' and '-'"
            )
        return value


class UserCreate(UserBase):
    """User creation model."""
    password: str = Field(..., min_length=8, max_length=100)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value):
            raise ValueError("password must contain at least one letter")
        if not re.search(r"\d", value):
            raise ValueError("password must contain at least one digit")
        return value


class UserInDB(UserBase):
    """User model as stored in database."""
    id: int
    password_hash: str
    created_at: datetime

    class Config:
        from_attributes = True


class UserResponse(UserBase):
    """User response model (without sensitive data)."""
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Token payload data."""
    username: Optional[str] = None
