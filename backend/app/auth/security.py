"""Password hashing and verification."""
import bcrypt

# bcrypt has a hard 72-byte limit on the input password.
_BCRYPT_MAX_BYTES = 72


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


def get_password_hash(password: str) -> str:
    """Generate a bcrypt hash from a plain password.

    Truncates the input to 72 bytes (bcrypt's hard limit) without mutating
    the caller's string. Two passwords that only differ after byte 72 will
    produce the same hash - this is an unavoidable bcrypt limitation; callers
    should enforce a length policy at the API layer.
    """
    encoded = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")
