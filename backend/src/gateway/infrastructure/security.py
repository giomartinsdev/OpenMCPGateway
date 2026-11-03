"""Password and API-key primitives.

Argon2id for operator passwords (memory-hard, the current recommendation).
SHA-256 for API keys: keys are 32 random bytes so unkeyed hashing is fine — the
preimage space makes rainbow tables irrelevant, and a fast hash keeps the proxy
hot path cheap. Do not reuse this reasoning for user-chosen passwords.
"""

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def generate_api_key() -> tuple[str, str]:
    """Return (plaintext, sha256_hex).

    The hash is what authentication reads; the plaintext also travels sealed
    (see `seal`/`open_sealed`) so the panel can show it to the operator who
    issued it — once. The proxy never touches the sealed copy.
    """
    plaintext = f"omg_{secrets.token_urlsafe(32)}"
    return plaintext, hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _sealing_key(secret: str) -> bytes:
    """AES-256 key derived from the deployment secret.

    Derivation rather than a second environment variable: a deployment already
    cannot start without `OMG_SESSION_SECRET`, and a separate key would be one
    more value to rotate in lockstep with the one that signs sessions.
    """
    return hashlib.sha256(f"omg-key-seal:{secret}".encode()).digest()

def seal(plaintext: str, *, secret: str) -> str:
    """Encrypt a credential for storage, as base64(nonce + ciphertext)."""
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_sealing_key(secret)).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + sealed).decode()

def open_sealed(sealed: str, *, secret: str) -> str | None:
    """Decrypt a sealed credential. None when it cannot be opened.

    A wrong secret (a deployment that rotated `OMG_SESSION_SECRET`) must read as
    "not available", not as a 500 on a page whose whole job is to be usable.
    """
    try:
        raw = base64.b64decode(sealed)
        return AESGCM(_sealing_key(secret)).decrypt(raw[:12], raw[12:], None).decode()
    except Exception:
        return None
