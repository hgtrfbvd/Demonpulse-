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
import secrets   # M-03: moved from inside generate_token() to top level
from functools import wraps
from datetime import datetime
from flask import request, jsonify, g
from env import env

log = logging.getLogger(__name__)

SECRET_KEY = os.environ.get("JWT_SECRET", "dpv8-dev-secret-change-in-prod")
TOKEN_TTL  = int(os.environ.get("SESSION_TIMEOUT_MIN", "480")) * 60

ROLE_PERMISSIONS = {
    "admin":    {"home","live","betting","reports","simulator","ai_learning",
                 "settings","audit","users","backtest","data","quality","performance"},
    "operator": {"home","live","betting","reports"},
    "viewer":   {"home","reports"},
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
    return base64.urlsafe_b64decode(s + "=" * (4 - len(s) % 4))

def _sign(h: str, p: str) -> str:
    sig = hmac.new(SECRET_KEY.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return _b64e(sig)

def generate_token(user_id: str, username: str, role: str) -> tuple[str, str]:
    """Returns (token, jti). Register jti in user_sessions after calling this."""
    jti = secrets.token_hex(16)
    header  = _b64e(json.dumps({"alg":"HS256","typ":"JWT"}).encode())
    payload = _b64e(json.dumps({
        "sub": user_id, "username": username, "role": role,
        "iat": int(time.time()), "exp": int(time.time()) + TOKEN_TTL,
        "jti": jti,               # unique token ID for revocation
        "env": env.mode,
    }).encode())
    return f"{header}.{payload}.{_sign(header, payload)}", jti

def decode_token(token: str) -> dict | None:
    try:
        h, p, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(h, p)):
            return None
        payload = json.loads(_b64d(p))
        if payload.get("exp", 0) < time.time():
            return None
        # Check session revocation (non-fatal — if DB down, allow)
        jti = payload.get("jti")
        if jti:
            try:
                from users import is_session_revoked
                if is_session_revoked(jti):
                    return None
            except Exception:
                pass
        return payload
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────
# PASSWORD (PBKDF2-SHA256)
# ─────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    dk   = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"pbkdf2:sha256:{salt}:{dk.hex()}"

def check_password(password: str, stored: str) -> bool:
    try:
        _, algo, salt, dk_hex = stored.split(":")
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
        return False
    LOGIN_RATE[ip] = attempts + [now]
    return True

def reset_rate_limit(ip: str):
    LOGIN_RATE.pop(ip, None)


# ─────────────────────────────────────────────────────────────────
# USER LOOKUPS
# ─────────────────────────────────────────────────────────────────
def get_user_by_username(username: str) -> dict | None:
    try:
        from db import get_db, safe_query, T
        return safe_query(
            lambda: get_db().table(T("users")).select("*")
                    .eq("username", username.lower()).single().execute().data
        )
    except Exception:
        return None

def get_user_by_id(user_id: str) -> dict | None:
    try:
        from db import get_db, safe_query, T
        return safe_query(
            lambda: get_db().table(T("users")).select("id,username,role,active,created_at")
                    .eq("id", user_id).single().execute().data
        )
    except Exception:
        return None

def create_user(username: str, password: str, role: str = "operator") -> dict:
    from db import get_db, safe_query, T
    import uuid
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"Invalid role: {role}")
    user_id = str(uuid.uuid4())
    result = safe_query(lambda: get_db().table(T("users")).insert({
        "id": user_id,
        "username": username.lower(),
        "password_hash": hash_password(password),
        "role": role,
        "active": True,
        "created_at": datetime.utcnow().isoformat(),
    }).execute())
    return {"id": user_id, "username": username, "role": role}


# ─────────────────────────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────────────────────────
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
# DECORATORS
# ─────────────────────────────────────────────────────────────────
def get_current_user():
    token = None
    auth  = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    if not token:
        token = request.cookies.get("dp_token")
    return decode_token(token) if token else None

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
                    log_event(user["sub"], user["username"], "ACCESS_DENIED",
                              request.endpoint or "unknown", {"roles_required": list(roles)})
                except Exception:
                    pass
                return jsonify({"error": "Forbidden", "code": 403}), 403
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator

def can_access(user: dict, page: str) -> bool:
    return page in ROLE_PERMISSIONS.get(user.get("role", "viewer"), set())
