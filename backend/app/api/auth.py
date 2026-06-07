"""Authentication API endpoints."""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import aiosqlite

from app.config import get_settings
from app.database import get_db
from app.auth.security import verify_password, get_password_hash
from app.auth.jwt_handler import create_access_token, verify_token
from app.models.user import UserCreate, UserResponse, Token

router = APIRouter(prefix="/api/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Get current authenticated user from token."""
    payload = verify_token(token)
    username: str = payload.get("sub")

    async with get_db() as db:
        async with db.execute(
            "SELECT id, username, created_at FROM users WHERE username = ?",
            (username,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found"
                )
            return dict(row)


@router.post("/register", response_model=UserResponse)
async def register(user_data: UserCreate):
    """Register a new user."""
    async with get_db() as db:
        # Check if username exists
        async with db.execute(
            "SELECT id FROM users WHERE username = ?",
            (user_data.username,)
        ) as cursor:
            if await cursor.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already registered"
                )

        # Create user
        password_hash = get_password_hash(user_data.password)
        async with db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (user_data.username, password_hash)
        ) as cursor:
            user_id = cursor.lastrowid

        await db.commit()

        # Get created user with timestamp
        async with db.execute(
            "SELECT id, username, created_at FROM users WHERE id = ?",
            (user_id,)
        ) as cursor:
            user = await cursor.fetchone()

        # Create user node in Neo4j
        from app.services.neo4j_client import get_neo4j_client
        neo4j = await get_neo4j_client()
        await neo4j.create_user_node(user_id, user_data.username)

        return dict(user)


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login and get access token."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (form_data.username,)
        ) as cursor:
            user = await cursor.fetchone()

    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"]},
        expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current user info."""
    return current_user
