"""
backend/auth/security.py — password hashing + verification.

Uses the `bcrypt` library directly rather than through passlib. passlib's
bcrypt backend-detection code assumes an older bcrypt API (it looks for
`bcrypt.__about__`, which bcrypt 4.x removed) and raises on import in
combination with current bcrypt releases. Calling bcrypt directly sidesteps
that entirely and is only a few lines either way.
"""
import bcrypt

_MAX_BYTES = 72   # bcrypt's own hard limit — longer inputs raise, not truncate


def hash_password(plain: str) -> str:
    pw = plain.encode("utf-8")[:_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    pw = plain.encode("utf-8")[:_MAX_BYTES]
    try:
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except ValueError:
        return False
