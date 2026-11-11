import base64
import hashlib
import hmac
import json
import secrets
import string
import time
from collections.abc import Collection

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pwdlib import PasswordHash

from gitseed_core.config import _DEFAULT_DEV_SECRET_KEY, settings
from gitseed_core.db import get_redis

_password_hasher = PasswordHash.recommended()
_PAT_ALPHABET = string.ascii_letters + string.digits
_RUN_TOKEN_PREFIX = "gsr_"
_HKDF_SALT = b"gitseed-fernet-salt-v2"
_HKDF_INFO = b"gitseed-secret-encryption"
_RUN_TOKEN_HKDF_INFO = b"gitseed-run-token-hmac-v1"
VALID_RUN_TOKEN_SCOPES: frozenset[str] = frozenset({"prepare", "agent", "finalize"})
_ATOMIC_LOGIN_RATE_LIMIT_LUA = (
    "local c = redis.call('INCR', KEYS[1]); "
    "if c == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1])) end; "
    "return c"
)


def _derive_hkdf_fernet(secret: str) -> Fernet:
    """Derive a domain-separated 32-byte Fernet key using RFC 5869 HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    key_bytes = hkdf.derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def _derive_legacy_sha256_fernet(secret: str) -> Fernet:
    """Legacy single-round SHA-256 Fernet key retained in MultiFernet for backward decryption."""
    key_bytes = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def _get_fernet() -> MultiFernet:
    """Build a MultiFernet keyring encrypting with the primary HKDF key and decrypting across current, legacy, and previous keys."""
    primary = settings.GITSEED_SECRET_KEY
    fernets: list[Fernet] = [
        _derive_hkdf_fernet(primary),
        _derive_legacy_sha256_fernet(primary),
    ]
    prev_raw = getattr(settings, "GITSEED_SECRET_PREVIOUS_KEYS", "") or ""
    for item in prev_raw.split(","):
        prev = item.strip()
        if prev and prev != primary:
            fernets.append(_derive_hkdf_fernet(prev))
            fernets.append(_derive_legacy_sha256_fernet(prev))
    if (
        primary != _DEFAULT_DEV_SECRET_KEY
        and getattr(settings, "ENVIRONMENT", "development").strip().lower() != "production"
        and bool(getattr(settings, "GITSEED_ALLOW_INSECURE_DEFAULTS", False))
    ):
        fernets.append(_derive_hkdf_fernet(_DEFAULT_DEV_SECRET_KEY))
        fernets.append(_derive_legacy_sha256_fernet(_DEFAULT_DEV_SECRET_KEY))
    return MultiFernet(fernets)


def encrypt_secrets(secrets_map: dict[str, str]) -> bytes:
    """Encrypt a dictionary of secret key/value pairs using Fernet (AES-128-CBC + HMAC-SHA256)."""
    payload = json.dumps(secrets_map, sort_keys=True).encode("utf-8")
    return bytes(_get_fernet().encrypt(payload))


def decrypt_secrets(ciphertext: bytes | None) -> dict[str, str]:
    """Decrypt a Fernet-encrypted secrets blob back into a dictionary."""
    if not ciphertext:
        return {}
    raw = _get_fernet().decrypt(ciphertext)
    data = json.loads(raw.decode("utf-8"))
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def rotate_encrypted_secrets(ciphertext: bytes | None) -> bytes | None:
    """Re-encrypt a Fernet ciphertext blob under the current primary HKDF key without exposing plaintext."""
    if not ciphertext:
        return None
    return bytes(_get_fernet().rotate(ciphertext))


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return str(_password_hasher.hash(password))


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    return bool(_password_hasher.verify(plain_password, password_hash))


def hash_token(raw_token: str) -> str:
    """Return the hex SHA-256 digest of a raw token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_pat() -> tuple[str, str, str]:
    """Generate a Personal Access Token.

    Returns (raw_token, token_prefix, token_hash) where raw_token is
    'gsp_' + 32 random alphanumeric characters and token_prefix is the
    first 8 characters ('gsp_' + 4 chars) for display.
    """
    body = "".join(secrets.choice(_PAT_ALPHABET) for _ in range(32))
    raw_token = f"gsp_{body}"
    token_prefix = raw_token[:8]
    return raw_token, token_prefix, hash_token(raw_token)


def generate_invite_token() -> tuple[str, str]:
    """Generate a one-time user invite token.

    Returns (raw_token, token_hash) where raw_token is 'gsi_' + 32 random
    alphanumeric characters and token_hash is its SHA-256 hex digest.
    """
    body = "".join(secrets.choice(_PAT_ALPHABET) for _ in range(32))
    raw_token = f"gsi_{body}"
    return raw_token, hash_token(raw_token)


def _session_key(session_id: str) -> str:
    return f"session:{session_id}"


def _user_sessions_key(user_id: str) -> str:
    return f"user_sessions:{user_id}"


def _login_rate_keys(email: str, client_ip: str | None) -> list[str]:
    normalized_email = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
    keys = [f"login_fail:email:{normalized_email}"]
    if client_ip:
        keys.append(f"login_fail:ip:{client_ip}")
    return keys


_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 300  # 5 minutes


async def is_login_rate_limited(email: str, client_ip: str | None = None) -> bool:
    """Return True if the email or client IP has exceeded the failed login threshold."""
    redis = get_redis()
    for key in _login_rate_keys(email, client_ip):
        val = await redis.get(key)
        if val is not None and int(val) >= _LOGIN_MAX_ATTEMPTS:
            return True
    return False


async def record_failed_login(email: str, client_ip: str | None = None) -> None:
    """Atomically increment failed login counters in Valkey with a 5-minute TTL window."""
    redis = get_redis()
    eval_fn = getattr(redis, "eval", None)
    for key in _login_rate_keys(email, client_ip):
        if callable(eval_fn):
            await eval_fn(_ATOMIC_LOGIN_RATE_LIMIT_LUA, 1, key, _LOGIN_WINDOW_SECONDS)
        else:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, _LOGIN_WINDOW_SECONDS)


async def clear_failed_logins(email: str, client_ip: str | None = None) -> None:
    """Clear failed login counters for the email and client IP after a successful login."""
    redis = get_redis()
    keys = _login_rate_keys(email, client_ip)
    if keys:
        await redis.delete(*keys)


async def create_session(user_id: str) -> str:
    """Create a web session in Valkey and return the opaque session ID."""
    session_id = secrets.token_urlsafe(32)
    redis = get_redis()
    await redis.set(
        _session_key(session_id),
        user_id,
        ex=settings.SESSION_TTL_SECONDS,
    )
    user_set_key = _user_sessions_key(user_id)
    await redis.sadd(user_set_key, session_id)
    await redis.expire(user_set_key, settings.SESSION_TTL_SECONDS)
    return session_id


async def resolve_session(session_id: str) -> str | None:
    """Look up a web session in Valkey and refresh its sliding TTL."""
    redis = get_redis()
    key = _session_key(session_id)
    raw_user_id = await redis.get(key)
    user_id: str | None = (
        raw_user_id.decode("utf-8")
        if isinstance(raw_user_id, bytes)
        else raw_user_id
    )
    if user_id is not None:
        await redis.expire(key, settings.SESSION_TTL_SECONDS)
        await redis.expire(_user_sessions_key(user_id), settings.SESSION_TTL_SECONDS)
    return user_id


async def delete_session(session_id: str) -> None:
    """Remove a web session from Valkey and untrack it from the user's session set."""
    redis = get_redis()
    key = _session_key(session_id)
    raw_user_id = await redis.get(key)
    user_id: str | None = (
        raw_user_id.decode("utf-8")
        if isinstance(raw_user_id, bytes)
        else raw_user_id
    )
    await redis.delete(key)
    if user_id is not None:
        await redis.srem(_user_sessions_key(user_id), session_id)


async def revoke_user_sessions(
    user_id: str, *, except_session_id: str | None = None
) -> None:
    """Purge all active Valkey web sessions for a user (optionally keeping one)."""
    redis = get_redis()
    user_set_key = _user_sessions_key(user_id)
    session_ids = await redis.smembers(user_set_key)
    for sid in session_ids:
        sid_str = str(sid)
        if except_session_id and sid_str == except_session_id:
            continue
        await redis.delete(_session_key(sid_str))
        await redis.srem(user_set_key, sid_str)
    if not except_session_id:
        await redis.delete(user_set_key)


def _derive_run_token_hmac_key(secret: str) -> bytes:
    """Derive a domain-separated 32-byte HMAC-SHA256 key for run tokens via HKDF."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_RUN_TOKEN_HKDF_INFO,
    )
    return hkdf.derive(secret.encode("utf-8"))


def _get_run_token_hmac_keys() -> list[bytes]:
    """Return active and previous HKDF-derived HMAC keys for run token verification."""
    primary = settings.GITSEED_SECRET_KEY
    keys = [_derive_run_token_hmac_key(primary)]
    prev_raw = getattr(settings, "GITSEED_SECRET_PREVIOUS_KEYS", "") or ""
    for item in prev_raw.split(","):
        prev = item.strip()
        if prev and prev != primary:
            keys.append(_derive_run_token_hmac_key(prev))
    return keys


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode((segment + pad).encode("ascii"))


def create_run_token(
    run_id: str, ttl_seconds: int = 14400, scope: str = "agent"
) -> str:
    """Create an HMAC-authenticated ephemeral token scoped to a single Run and broker role."""
    normalized_scope = (scope or "agent").strip().lower()
    if normalized_scope not in VALID_RUN_TOKEN_SCOPES:
        raise ValueError(f"Invalid run token scope: {scope!r}")
    payload_bytes = json.dumps(
        {
            "run_id": run_id,
            "scope": normalized_scope,
            "exp": int(time.time()) + ttl_seconds,
            "jti": secrets.token_urlsafe(8),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    body = _b64url_encode(payload_bytes)
    key = _derive_run_token_hmac_key(settings.GITSEED_SECRET_KEY)
    sig = _b64url_encode(
        hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{_RUN_TOKEN_PREFIX}{body}.{sig}"


def verify_scoped_run_token(raw_token: str) -> tuple[str, str] | None:
    """Verify an ephemeral run token's HMAC signature and TTL, returning (run_id, scope) or None."""
    cleaned = raw_token.strip()
    if not cleaned.startswith(_RUN_TOKEN_PREFIX):
        return None
    token_part = cleaned.removeprefix(_RUN_TOKEN_PREFIX)
    if "." not in token_part:
        return None
    body, sig_b64 = token_part.split(".", 1)
    if not body or not sig_b64:
        return None
    try:
        provided_sig = _b64url_decode(sig_b64)
    except Exception:
        return None

    matched = False
    signing_bytes = body.encode("ascii")
    for key in _get_run_token_hmac_keys():
        expected_sig = hmac.new(key, signing_bytes, hashlib.sha256).digest()
        if hmac.compare_digest(provided_sig, expected_sig):
            matched = True
            break
    if not matched:
        return None

    try:
        raw_payload = _b64url_decode(body)
        data = json.loads(raw_payload.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        exp = data.get("exp")
        run_id = data.get("run_id")
        scope = data.get("scope", "agent")
        if not isinstance(run_id, str) or not run_id:
            return None
        if not isinstance(scope, str) or scope not in VALID_RUN_TOKEN_SCOPES:
            return None
        if not isinstance(exp, (int, float)) or time.time() > exp:
            return None
        return (run_id, scope)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def verify_run_token(
    raw_token: str,
    required_scope: str | Collection[str] | None = None,
) -> str | None:
    """Verify an ephemeral run token and optional required scope(s), returning run_id or None."""
    verified = verify_scoped_run_token(raw_token)
    if verified is None:
        return None
    run_id, token_scope = verified
    if required_scope is not None:
        if isinstance(required_scope, str):
            if token_scope != required_scope:
                return None
        else:
            if token_scope not in required_scope:
                return None
    return run_id


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_github_app_jwt(app_id: str, private_key_pem: str) -> str:
    """Sign a short-lived RS256 JWT (9 minutes) for GitHub App installation token minting."""
    now = int(time.time())
    header = _b64url_encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    claims = _b64url_encode(
        json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode("utf-8")
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("GitHub App private key must be an RSA private key")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64url_encode(signature)}"


