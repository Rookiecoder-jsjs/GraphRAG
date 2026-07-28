"""User-related Pydantic models."""
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
# bcrypt only hashes the first 72 BYTES of a password; anything beyond is
# silently ignored. Enforce the byte limit at the API layer so two passwords
# differing only past byte 72 can't collide to the same hash.
_BCRYPT_MAX_BYTES = 72


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
    password: str = Field(..., min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value):
            raise ValueError("password must contain at least one letter")
        if not re.search(r"\d", value):
            raise ValueError("password must contain at least one digit")
        if len(value.encode("utf-8")) > _BCRYPT_MAX_BYTES:
            raise ValueError(
                "password is too long (max 72 bytes after UTF-8 encoding)"
            )
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
