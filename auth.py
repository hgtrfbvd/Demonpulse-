"""
auth.py - V9 Authentication & Role Enforcement
JWT-based auth, PBKDF2 passwords, role-based access control

ENV NOTES:
  LIVE: bootstrap creates real admin only if no users exist (safe, one-time)
  TEST: bootstrap also creates demo users (admin/operator/viewer)
        demo credentials only work in TEST mode
"""

import os
import json
import time
import base64
import secrets
import logging
import hashlib
import hmac
from functools import wraps
from datetime import datetime

from flask import request, jsonify, g

from env import env

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("JWT_SECRET", "").strip()
TOKEN_TTL = int(os.environ.get("SESSION_TIMEOUT_MIN", "480")) * 60

ROLE_PERMISSIONS = {
    "admin": {
        "home",
        "live",
        "betting",
        "reports",
        "simulator",
        "ai_learning",
        "settings",
        "audit",
        "users",
        "backtest",
        "data",
        "quality",
        "performance",
    },
    "operator": {"home", "live", "betting", "reports"},
    "viewer": {"home", "reports"},
}

LOGIN_RATE = {}
MAX_LOGIN_ATTEMPTS = 10
RATE_WINDOW = 300

PBKDF2_SCHEME = "pbkdf2"
PBKDF2_ALGO = "sha256"
PBKDF2_ITERATIONS = 260000
SALT_BYTES = 16


# ─────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────
def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _require_secret_key():
    if not SECRET_KEY:
        raise RuntimeError("JWT_SECRET is not configured")


def _user_pk(user: dict | None):
    if not user:
        return None
    return user.get("sub") or user.get("id")


def _clean_username(username: str) -> str:
    return (username or "").strip().lower()


def _validate_role(role: str):
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"Invalid role: {role}")


# ─────────────────────────────────────────────────────────────────
# JWT (HMAC-SHA256, no external lib)
# ─────────────────────────────────────────────────────────────────
def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64d(s: str) -> bytes:
    padding = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + ("=" * padding))


def _sign(header_b64: str, payload_b64: str) -> str:
    _require_secret_key()
    sig = hmac.new(
        SECRET_KEY.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64e(sig)


def generate_token(user_id: str, username: str, role: str) -> tuple[str, str]:
    """Returns (token, jti)."""
    _require_secret_key()
    _validate_role(role)

    if not user_id or not username:
        raise ValueError("user_id and username are required")

    now = int(time.time())
    jti = secrets.token_hex(16)

    header = _b64e(
        json.dumps(
            {"alg": "HS256", "typ": "JWT"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )

    payload = _b64e(
        json.dumps(
            {
                "sub": str(user_id),
                "username": _clean_username(username),
                "role": role,
                "iat": now,
                "exp": now + TOKEN_TTL,
                "jti": jti,
                "env": env.mode,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )

    token = f"{header}.{payload}.{_sign(header, payload)}"
    return token, jti


def decode_token(token: str) -> dict | None:
    if not token or token.count(".") != 2:
        return None

    try:
        header_b64, payload_b64, sig = token.split(".")

        expected_sig = _sign(header_b64, payload_b64)
        if not hmac.compare_digest(sig, expected_sig):
            return None

        payload = json.loads(_b64d(payload_b64).decode("utf-8"))

        if not isinstance(payload, dict):
            return None

        sub = payload.get("sub")
        username = payload.get("username")
        role = payload.get("role")
        exp = payload.get("exp")
        token_env = payload.get("env")
        jti = payload.get("jti")

        if not isinstance(sub, str) or not sub:
            return None
        if not isinstance(username, str) or not username:
            return None
        if not isinstance(role, str) or role not in ROLE_PERMISSIONS:
            return None
        if not isinstance(exp, int):
            return None
        if not isinstance(token_env, str) or token_env != env.mode:
            return None
        if jti is not None and not isinstance(jti, str):
            return None

        now = int(time.time())
        if exp < now:
            return None

        user = get_user_by_id(sub)
        if not user or not bool(user.get("active", False)):
            return None

        if jti:
            try:
                from users import is_session_revoked
                if is_session_revoked(jti):
                    return None
            except Exception as e:
                log.warning(f"Session revoke check failed for jti={jti}: {e}")
                return None

        return payload

    except Exception as e:
        log.warning(f"Token decode failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# PASSWORD (PBKDF2-SHA256)
# ─────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    salt = os.urandom(SALT_BYTES).hex()
    dk = hashlib.pbkdf2_hmac(
        PBKDF2_ALGO,
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return f"{PBKDF2_SCHEME}:{PBKDF2_ALGO}:{salt}:{dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        if not isinstance(password, str) or not isinstance(stored, str):
            return False

        scheme, algo, salt, dk_hex = stored.split(":")
        if scheme != PBKDF2_SCHEME:
            return False
        if algo != PBKDF2_ALGO:
            return False
        if not salt or not dk_hex:
            return False

        dk = hashlib.pbkdf2_hmac(
            algo,
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PBKDF2_ITERATIONS,
        )
        return hmac.compare_digest(dk.hex(), dk_hex)

    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
# RATE LIMITING
# ─────────────────────────────────────────────────────────────────
def check_rate_limit(ip: str) -> bool:
    ip = (ip or "unknown").strip()
    now = time.time()
    attempts = [t for t in LOGIN_RATE.get(ip, []) if now - t < RATE_WINDOW]

    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        LOGIN_RATE[ip] = attempts
        return False

    LOGIN_RATE[ip] = attempts + [now]
    return True


def reset_rate_limit(ip: str):
    LOGIN_RATE.pop((ip or "unknown").strip(), None)


# ─────────────────────────────────────────────────────────────────
# USER LOOKUPS
# ─────────────────────────────────────────────────────────────────
def get_user_by_username(username: str) -> dict | None:
    from db import get_db, T

    username = _clean_username(username)
    if not username:
        return None

    try:
        rows = (
            get_db()
            .table(T("users"))
            .select("*")
            .eq("username", username)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"User lookup failed for username={username}: {e}")
        raise RuntimeError("USER_LOOKUP_FAILED") from e


def get_user_by_id(user_id: str) -> dict | None:
    from db import get_db, T

    user_id = (user_id or "").strip()
    if not user_id:
        return None

    try:
        rows = (
            get_db()
            .table(T("users"))
            .select("id,username,role,active,created_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"User lookup failed for id={user_id}: {e}")
        raise RuntimeError("USER_LOOKUP_FAILED") from e


def _count_users() -> int:
    from db import get_db, T

    try:
        rows = (
            get_db()
            .table(T("users"))
            .select("id")
            .limit(10000)
            .execute()
            .data
            or []
        )
        return len(rows)
    except Exception as e:
        log.error(f"User count failed: {e}")
        raise RuntimeError("USER_COUNT_FAILED") from e


def create_user(username: str, password: str, role: str = "operator") -> dict:
    from db import get_db, T
    import uuid

    username = _clean_username(username)
    _validate_role(role)

    if not username:
        raise ValueError("Username is required")
    if len(username) < 2:
        raise ValueError("Username must be at least 2 characters")

    existing = get_user_by_username(username)
    if existing:
        raise ValueError(f"User already exists: {username}")

    user_id = str(uuid.uuid4())

    try:
        result = (
            get_db()
            .table(T("users"))
            .insert(
                {
                    "id": user_id,
                    "username": username,
                    "password_hash": hash_password(password),
                    "role": role,
                    "active": True,
                    "created_at": _utc_now_iso(),
                }
            )
            .execute()
        )
    except Exception as e:
        log.error(f"Create user failed for username={username}: {e}")
        raise RuntimeError("USER_CREATE_FAILED") from e

    if not getattr(result, "data", None):
        raise RuntimeError("USER_CREATE_FAILED")

    return {"id": user_id, "username": username, "role": role}


def bootstrap_admin():
    """
    Ensure required default accounts exist.

    LIVE mode:
      - create admin ONLY if the user table is empty
      - operator/viewer are NOT auto-created

    TEST mode:
      - ensure admin/operator/viewer all exist
    """
    try:
        admin_pw = os.environ.get("ADMIN_PASSWORD", "DemonPulse2025!")

        if env.is_live:
            total_users = _count_users()
            if total_users == 0:
                create_user("admin", admin_pw, "admin")
                log.warning("[LIVE] Bootstrapped initial admin because user table was empty")
            else:
                log.info("[LIVE] Bootstrap skipped because users already exist")
            return

        # TEST mode
        if not get_user_by_username("admin"):
            create_user("admin", admin_pw, "admin")
            log.warning("[TEST] Bootstrapped admin user")
        else:
            log.info("[TEST] Admin user already exists")

        if not get_user_by_username("operator"):
            create_user("operator", "Operator2025!", "operator")
            log.warning("[TEST] Bootstrapped operator user")
        else:
            log.info("[TEST] Operator user already exists")

        if not get_user_by_username("viewer"):
            create_user("viewer", "Viewer2025!", "viewer")
            log.warning("[TEST] Bootstrapped viewer user")
        else:
            log.info("[TEST] Viewer user already exists")

    except ValueError as e:
        log.info(f"Bootstrap skipped: {e}")
    except Exception as e:
        log.error(f"Bootstrap failed: {e}")


# ─────────────────────────────────────────────────────────────────
# CURRENT USER / DECORATORS
# ─────────────────────────────────────────────────────────────────
def get_current_user():
    token = None

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()

    if not token:
        token = request.cookies.get("dp_token")

    if not token:
        return None

    return decode_token(token)


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Unauthorized", "code": 401}), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def require_role(*roles):
    valid_roles = set(roles)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Unauthorized", "code": 401}), 401

            if user.get("role") not in valid_roles:
                try:
                    from audit import log_event
                    log_event(
                        _user_pk(user),
                        user.get("username"),
                        "ACCESS_DENIED",
                        request.endpoint or "unknown",
                        {"roles_required": list(valid_roles)},
                    )
                except Exception:
                    pass

                return jsonify({"error": "Forbidden", "code": 403}), 403

            g.user = user
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def can_access(user: dict, page: str) -> bool:
    """
    Resolve page access.
    If user_permissions exists, use that.
    Otherwise fall back to role defaults.
    """
    if not user or not page:
        return False

    try:
        from users import resolve_permissions
        perms = resolve_permissions(_user_pk(user), user.get("role", "viewer"))
        return page in perms
    except Exception:
        return page in ROLE_PERMISSIONS.get(user.get("role", "viewer"), set())
