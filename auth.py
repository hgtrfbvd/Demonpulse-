"""
auth.py - V8 Authentication & Role Enforcement
JWT-based auth, PBKDF2 passwords, role-based access control

ENV NOTES:
  LIVE: bootstrap creates real admin only if no users exist (safe, one-time)
  TEST: bootstrap also creates demo users (admin/operator/viewer)
        demo credentials only work in TEST mode
"""

import os
import logging
import hashlib
import hmac
import time
import json
import base64
import secrets
from functools import wraps
from datetime import datetime

from flask import request, jsonify, g

from env import env

log = logging.getLogger(__name__)

SECRET_KEY = os.environ.get("JWT_SECRET", "dpv8-dev-secret-change-in-prod")
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


# ─────────────────────────────────────────────────────────────────
# JWT (HMAC-SHA256, no external lib)
# ─────────────────────────────────────────────────────────────────
def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    padding = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + ("=" * padding))


def _sign(h: str, p: str) -> str:
    sig = hmac.new(SECRET_KEY.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return _b64e(sig)


def generate_token(user_id: str, username: str, role: str) -> tuple[str, str]:
    """Returns (token, jti)."""
    now = int(time.time())
    jti = secrets.token_hex(16)

    header = _b64e(
        json.dumps(
            {"alg": "HS256", "typ": "JWT"},
            separators=(",", ":"),
        ).encode()
    )

    payload = _b64e(
        json.dumps(
            {
                "sub": user_id,
                "username": username,
                "role": role,
                "iat": now,
                "exp": now + TOKEN_TTL,
                "jti": jti,
                "env": env.mode,
            },
            separators=(",", ":"),
        ).encode()
    )

    token = f"{header}.{payload}.{_sign(header, payload)}"
    return token, jti


def decode_token(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        h, p, sig = parts
        expected_sig = _sign(h, p)
        if not hmac.compare_digest(sig, expected_sig):
            return None

        payload = json.loads(_b64d(p).decode())

        now = int(time.time())
        if int(payload.get("exp", 0)) < now:
            return None

        if payload.get("env") != env.mode:
            return None

        if not payload.get("sub") or not payload.get("username") or not payload.get("role"):
            return None

        if payload.get("role") not in ROLE_PERMISSIONS:
            return None

        user = get_user_by_id(payload["sub"])
        if not user or not user.get("active", False):
            return None

        jti = payload.get("jti")
        if jti:
            try:
                from users import is_session_revoked
                if is_session_revoked(jti):
                    return None
            except Exception as e:
                log.warning(f"Session revoke check failed for jti={jti}: {e}")

        return payload

    except Exception as e:
        log.warning(f"Token decode failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# PASSWORD (PBKDF2-SHA256)
# ─────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"pbkdf2:sha256:{salt}:{dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, algo, salt, dk_hex = stored.split(":")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(algo, password.encode(), salt.encode(), 260000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
# RATE LIMITING
# ─────────────────────────────────────────────────────────────────
def check_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in LOGIN_RATE.get(ip, []) if now - t < RATE_WINDOW]
    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        LOGIN_RATE[ip] = attempts
        return False
    LOGIN_RATE[ip] = attempts + [now]
    return True


def reset_rate_limit(ip: str):
    LOGIN_RATE.pop(ip, None)


# ─────────────────────────────────────────────────────────────────
# USER LOOKUPS
# ─────────────────────────────────────────────────────────────────
def get_user_by_username(username: str) -> dict | None:
    from db import get_db, T

    if not username:
        return None

    try:
        rows = (
            get_db()
            .table(T("users"))
            .select("*")
            .eq("username", username.lower())
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


def create_user(username: str, password: str, role: str = "operator") -> dict:
    from db import get_db, T
    import uuid

    username = (username or "").strip().lower()

    if not username:
        raise ValueError("Username is required")

    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"Invalid role: {role}")

    existing = get_user_by_username(username)
    if existing:
        return {
            "id": existing["id"],
            "username": existing["username"],
            "role": existing["role"],
        }

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
                    "created_at": datetime.utcnow().isoformat(),
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
      - ensure admin exists
      - operator/viewer are NOT auto-created

    TEST mode:
      - ensure admin/operator/viewer all exist
    """
    try:
        admin_pw = os.environ.get("ADMIN_PASSWORD", "DemonPulse2025!")

        admin_user = get_user_by_username("admin")
        if not admin_user:
            create_user("admin", admin_pw, "admin")
            log.info(f"[{env.mode}] Bootstrapped admin user")
        else:
            log.info(f"[{env.mode}] Admin user already exists")

        if env.is_test:
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
        else:
            log.info("[LIVE] Only admin is auto-managed at bootstrap")

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
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Unauthorized", "code": 401}), 401

            if user.get("role") not in roles:
                try:
                    from audit import log_event
                    log_event(
                        user["sub"],
                        user["username"],
                        "ACCESS_DENIED",
                        request.endpoint or "unknown",
                        {"roles_required": list(roles)},
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
    if not user:
        return False

    try:
        from users import resolve_permissions
        perms = resolve_permissions(user.get("sub"), user.get("role", "viewer"))
        return page in perms
    except Exception:
        return page in ROLE_PERMISSIONS.get(user.get("role", "viewer"), set())
