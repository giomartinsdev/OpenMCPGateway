"""Password and API-key primitives.

Argon2id for operator passwords (memory-hard, the current recommendation).
SHA-256 for API keys: keys are 32 random bytes so unkeyed hashing is fine — the
preimage space makes rainbow tables irrelevant, and a fast hash keeps the proxy
hot path cheap. Do not reuse this reasoning for user-chosen passwords.
"""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def generate_api_key() -> tuple[str, str]:
    """Return (plaintext, sha256_hex). Plaintext is shown once, never stored."""
    plaintext = f"omg_{secrets.token_urlsafe(32)}"
    return plaintext, hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
