"""Password hashing and verification.

bcrypt remains the production algorithm. A standards-library PBKDF2 fallback
keeps local utility scripts and recovery installs usable when the optional
binary bcrypt wheel is unavailable; hashes are self-identifying, so both
formats can coexist safely.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

try:  # Render/normal installs use the dependency declared in requirements.txt
    import bcrypt  # type: ignore
except ImportError:  # pragma: no cover - exercised only on minimal installs
    bcrypt = None

_MAX_BYTES = 72
_PBKDF2_PREFIX = "$pbkdf2-sha256$"
_PBKDF2_ROUNDS = 600_000


def _pbkdf2_hash(plain: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return (_PBKDF2_PREFIX + str(_PBKDF2_ROUNDS) + "$" +
            base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=") + "$" +
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="))


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(plain: str) -> str:
    if bcrypt is not None:
        pw = plain.encode("utf-8")[:_MAX_BYTES]
        return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")
    return _pbkdf2_hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    if hashed.startswith(_PBKDF2_PREFIX):
        try:
            _empty, _algorithm, rounds_text, salt_text, digest_text = hashed.split("$", 4)
            rounds = int(rounds_text)
            expected = _b64decode(digest_text)
            actual = hashlib.pbkdf2_hmac(
                "sha256", plain.encode("utf-8"), _b64decode(salt_text), rounds)
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    if bcrypt is None:
        return False
    pw = plain.encode("utf-8")[:_MAX_BYTES]
    try:
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except ValueError:
        return False
