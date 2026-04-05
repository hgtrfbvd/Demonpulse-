"""
users.py - DemonPulse V8 Full User Management
Handles: CRUD, per-user accounts, permissions, sessions, activity
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# All pages that can be toggled per-user
ALL_PAGES = {
    "home", "live", "betting", "reports", "simulator",
    "ai_learning", "settings", "audit", "users", "backtest",
    "data", "quality", "performance",
}

ROLE_DEFAULTS = {
    "admin":    ALL_PAGES,
    "operator": {"home", "live", "betting", "reports"},
    "viewer":   {"home", "reports"},
}


# ─────────────────────────────────────────────────────────────────
# USER CRUD
# ─────────────────────────────────────────────────────────────────
def get_all_users() -> list[dict]:
    """Full user list with account summary joined."""
    from db import get_db, safe_query, T
    rows = safe_query(
        lambda: get_db().table(T("users"))
                .select("id,username,display_name,email,role,active,last_login,login_count,created_at,created_by,last_ip")
                .order("created_at").execute().data, []
    ) or []
    # Attach bankroll summary
    accounts = safe_query(
        lambda: get_db().table(T("user_accounts"))
                .select("user_id,bankroll,total_pl,total_bets,total_wins").execute().data, []
    ) or []
    acct_map = {a["user_id"]: a for a in accounts}
    for r in rows:
        acct = acct_map.get(r["id"], {})
        r["bankroll"]   = acct.get("bankroll", 1000.0)
        r["total_pl"]   = acct.get("total_pl", 0.0)
        r["total_bets"] = acct.get("total_bets", 0)
        r["total_wins"] = acct.get("total_wins", 0)
    return rows


def get_user_full(user_id: str) -> dict | None:
    """Get a single user with all extended data."""
    from db import get_db, safe_query, T
    user = safe_query(
        lambda: get_db().table(T("users")).select("*")
                .eq("id", user_id).single().execute().data
    )
    if not user:
        return None
    # Account data
    acct = safe_query(
        lambda: get_db().table(T("user_accounts")).select("*")
                .eq("user_id", user_id).single().execute().data
    ) or {}
    # Permissions
    perms = safe_query(
        lambda: get_db().table(T("user_permissions")).select("*")
                .eq("user_id", user_id).single().execute().data
    ) or {}
    # Strip password hash
    user.pop("password_hash", None)
    return {**user, "account": acct, "permissions": perms}


def create_user_full(
    username: str,
    password: str,
    role: str = "operator",
    display_name: str = "",
    email: str = "",
    active: bool = True,
    starting_bankroll: float = 1000.0,
    creator_username: str = "admin",
) -> dict:
    """Create a user with full account scaffolding."""
    from auth import create_user, ROLE_PERMISSIONS
    from db import get_db, safe_query, T
    from audit import log_event

    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"Invalid role: {role}")
    if not username or len(username) < 2:
        raise ValueError("Username must be at least 2 characters")
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    # Create base user
    new_user = create_user(username, password, role)
    uid = new_user["id"]

    # Set extended fields
    safe_query(lambda: get_db().table(T("users")).update({
        "display_name": display_name or username,
        "email": email,
        "active": active,
        "created_by": creator_username,
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("id", uid).execute())

    # Upsert user_accounts — safe whether trigger has fired or not
    # on_conflict="user_id" handles both INSERT (trigger didn't fire yet) and UPDATE
    safe_query(lambda: get_db().table(T("user_accounts")).upsert({
        "user_id": uid,
        "bankroll": starting_bankroll,
        "peak_bank": starting_bankroll,
    }, on_conflict="user_id").execute())

    # Compute effective permissions
    effective = list(ROLE_DEFAULTS.get(role, set()))
    safe_query(lambda: get_db().table(T("user_permissions")).upsert({
        "user_id": uid,
        "granted": [],
        "revoked": [],
        "effective": effective,
        "updated_at": datetime.utcnow().isoformat(),
        "updated_by": creator_username,
    }, on_conflict="user_id").execute())

    log_event(None, creator_username, "USER_CREATE", f"users/{uid}", data={
        "username": username, "role": role, "starting_bankroll": starting_bankroll,
    })

    # Seed activity
    _record_activity(uid, "ACCOUNT_CREATED", {"created_by": creator_username, "role": role})

    return {**new_user, "display_name": display_name or username, "active": active}


def update_user_profile(
    user_id: str,
    actor_username: str,
    **kwargs,
) -> dict:
    """Update user fields. Returns changed fields for audit."""
    from db import get_db, safe_query, T
    from audit import log_event

    allowed = {"username", "display_name", "email", "role", "active"}
    changes = {k: v for k, v in kwargs.items() if k in allowed}
    if not changes:
        return {}

    changes["updated_at"] = datetime.utcnow().isoformat()

    # If role is changing, recompute permissions
    if "role" in changes:
        new_role = changes["role"]
        _recompute_permissions(user_id, new_role, actor_username)
        log_event(None, actor_username, "ROLE_CHANGED", f"users/{user_id}",
                  data={"new_role": new_role}, severity="WARN")
        _record_activity(user_id, "ROLE_CHANGED", {"new_role": new_role, "changed_by": actor_username})

    safe_query(lambda: get_db().table(T("users")).update(changes).eq("id", user_id).execute())

    if "active" in changes:
        action = "USER_ENABLED" if changes["active"] else "USER_DISABLED"
        log_event(None, actor_username, action, f"users/{user_id}", severity="WARN")
        _record_activity(user_id, action, {"changed_by": actor_username})
    else:
        log_event(None, actor_username, "USER_EDITED", f"users/{user_id}", data=changes)

    return changes


def reset_password(user_id: str, new_password: str, actor_username: str) -> bool:
    """Reset a user's password. Revokes all sessions."""
    from auth import hash_password
    from db import get_db, safe_query, T
    from audit import log_event

    if not new_password or len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters")

    pw_hash = hash_password(new_password)
    safe_query(lambda: get_db().table(T("users")).update({
        "password_hash": pw_hash,
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("id", user_id).execute())

    # Revoke all sessions for this user
    revoke_all_sessions(user_id, actor_username)

    log_event(None, actor_username, "PASSWORD_CHANGED", f"users/{user_id}", severity="WARN")
    _record_activity(user_id, "PASSWORD_RESET", {"reset_by": actor_username})
    return True


def delete_user(user_id: str, actor_username: str) -> bool:
    """Permanently delete a user. Requires env.is_live guard at route level."""
    from db import get_db, safe_query, T
    from audit import log_event

    user = safe_query(
        lambda: get_db().table(T("users")).select("username,role").eq("id", user_id).single().execute().data
    )
    if not user:
        return False
    if user.get("role") == "admin":
        # Prevent deleting last admin
        admins = safe_query(
            lambda: get_db().table(T("users")).select("id").eq("role", "admin").eq("active", True).execute().data, []
        ) or []
        if len(admins) <= 1:
            raise ValueError("Cannot delete the last active admin account")

    # Revoke sessions first
    revoke_all_sessions(user_id, actor_username)

    # CASCADE will handle user_accounts, user_permissions, user_sessions, user_activity
    safe_query(lambda: get_db().table(T("users")).delete().eq("id", user_id).execute())

    log_event(None, actor_username, "USER_DELETE", f"users/{user_id}",
              data={"deleted_username": user.get("username")}, severity="CRITICAL")
    return True


# ─────────────────────────────────────────────────────────────────
# SESSIONS / FORCE LOGOUT
# ─────────────────────────────────────────────────────────────────
def record_login(user_id: str, ip: str | None = None) -> None:
    """Update last_login, last_ip, and increment login_count on successful login."""
    from db import get_db, safe_query, T
    now = datetime.now(timezone.utc).isoformat()
    patch: dict = {"last_login": now, "updated_at": now}
    if ip:
        patch["last_ip"] = ip
    result = safe_query(lambda: get_db().table(T("users")).update(patch).eq("id", user_id).execute())
    if result is None:
        log.warning(f"record_login: DB update failed for user_id={user_id}; skipping counter increment")
        return
    # Atomic counter increment via the SQL helper function (best-effort)
    try:
        from supabase_client import get_client
        get_client().rpc("increment_login_count", {"p_user_id": user_id}).execute()
    except Exception as e:
        log.warning(f"login_count increment skipped for user_id={user_id}: {e}")


def register_session(user_id: str, jti: str, ip: str | None, user_agent: str | None, ttl_seconds: int):
    """Record a new token in user_sessions."""
    from db import get_db, safe_query, T
    expires_at = (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat()
    safe_query(lambda: get_db().table(T("user_sessions")).insert({
        "user_id":    user_id,
        "token_jti":  jti,
        "ip_address": ip,
        "user_agent": user_agent,
        "expires_at": expires_at,
        "revoked":    False,
    }).execute())


def is_session_revoked(jti: str) -> bool:
    """Check if a token has been force-revoked."""
    from db import get_db, safe_query, T
    row = safe_query(
        lambda: get_db().table(T("user_sessions")).select("revoked")
                .eq("token_jti", jti).single().execute().data
    )
    if not row:
        return False  # Unknown token — allow (backwards compat)
    return bool(row.get("revoked"))


def revoke_all_sessions(user_id: str, actor_username: str):
    """Force-revoke all active sessions for a user (force logout)."""
    from db import get_db, safe_query, T
    from audit import log_event
    now = datetime.utcnow().isoformat()
    safe_query(lambda: get_db().table(T("user_sessions")).update({
        "revoked": True, "revoked_at": now, "revoked_by": actor_username,
    }).eq("user_id", user_id).eq("revoked", False).execute())
    log_event(None, actor_username, "FORCE_LOGOUT", f"users/{user_id}", severity="WARN")
    _record_activity(user_id, "FORCE_LOGOUT", {"revoked_by": actor_username})


def get_active_sessions(user_id: str) -> list[dict]:
    """List active (non-revoked, non-expired) sessions."""
    from db import get_db, safe_query, T
    now = datetime.utcnow().isoformat()
    return safe_query(
        lambda: get_db().table(T("user_sessions"))
                .select("id,ip_address,user_agent,created_at,expires_at")
                .eq("user_id", user_id).eq("revoked", False).gte("expires_at", now)
                .order("created_at", desc=True).execute().data, []
    ) or []


# ─────────────────────────────────────────────────────────────────
# PERMISSIONS
# ─────────────────────────────────────────────────────────────────
def get_user_permissions(user_id: str) -> dict:
    from db import get_db, safe_query, T
    row = safe_query(
        lambda: get_db().table(T("user_permissions")).select("*")
                .eq("user_id", user_id).single().execute().data
    ) or {}
    return row


def update_user_permissions(user_id: str, granted: list, revoked: list, actor_username: str) -> dict:
    """
    Update per-user permission overrides.
    effective = (role_defaults ∪ granted) ∖ revoked
    """
    from db import get_db, safe_query, T
    from audit import log_event

    # Get current role
    user = safe_query(
        lambda: get_db().table(T("users")).select("role").eq("id", user_id).single().execute().data
    ) or {}
    role = user.get("role", "viewer")
    base = set(ROLE_DEFAULTS.get(role, set()))
    effective = sorted((base | set(granted)) - set(revoked))

    safe_query(lambda: get_db().table(T("user_permissions")).upsert({
        "user_id":    user_id,
        "granted":    list(set(granted)),
        "revoked":    list(set(revoked)),
        "effective":  effective,
        "updated_at": datetime.utcnow().isoformat(),
        "updated_by": actor_username,
    }, on_conflict="user_id").execute())

    log_event(None, actor_username, "PERMISSIONS_CHANGED", f"users/{user_id}",
              data={"granted": granted, "revoked": revoked, "effective": effective}, severity="WARN")
    _record_activity(user_id, "PERMISSIONS_CHANGED",
                     {"granted": granted, "revoked": revoked, "changed_by": actor_username})
    return {"granted": granted, "revoked": revoked, "effective": effective}


def _recompute_permissions(user_id: str, new_role: str, actor_username: str):
    """After role change, recompute effective permissions preserving overrides."""
    from db import get_db, safe_query, T
    row = safe_query(
        lambda: get_db().table(T("user_permissions")).select("granted,revoked")
                .eq("user_id", user_id).single().execute().data
    ) or {}
    granted = row.get("granted") or []
    revoked = row.get("revoked") or []
    base = set(ROLE_DEFAULTS.get(new_role, set()))
    effective = sorted((base | set(granted)) - set(revoked))
    safe_query(lambda: get_db().table(T("user_permissions")).upsert({
        "user_id": user_id, "granted": granted, "revoked": revoked,
        "effective": effective,
        "updated_at": datetime.utcnow().isoformat(),
        "updated_by": actor_username,
    }, on_conflict="user_id").execute())


def resolve_permissions(user_id: str, role: str) -> set:
    """
    Resolve effective permissions for a user.
    Priority: user_permissions.effective (if exists) → role defaults.
    """
    from db import get_db, safe_query, T
    row = safe_query(
        lambda: get_db().table(T("user_permissions")).select("effective")
                .eq("user_id", user_id).single().execute().data
    )
    if row and row.get("effective"):
        return set(row["effective"])
    return set(ROLE_DEFAULTS.get(role, set()))


# ─────────────────────────────────────────────────────────────────
# PER-USER BANKROLL
# ─────────────────────────────────────────────────────────────────
def get_user_account(user_id: str) -> dict:
    from db import get_db, safe_query, T
    return safe_query(
        lambda: get_db().table(T("user_accounts")).select("*")
                .eq("user_id", user_id).single().execute().data
    ) or {"user_id": user_id, "bankroll": 1000.0, "total_pl": 0.0, "session_pl": 0.0}


def update_user_bankroll(user_id: str, new_bankroll: float, actor_username: str):
    from db import get_db, safe_query, T
    from audit import log_event
    acct = get_user_account(user_id)
    old = acct.get("bankroll", 0)
    peak = max(acct.get("peak_bank", 0), new_bankroll)
    safe_query(lambda: get_db().table(T("user_accounts")).upsert({
        "user_id": user_id, "bankroll": new_bankroll, "peak_bank": peak,
        "updated_at": datetime.utcnow().isoformat(),
    }, on_conflict="user_id").execute())
    log_event(None, actor_username, "BANKROLL_SET", f"users/{user_id}/bankroll",
              data={"old": old, "new": new_bankroll}, severity="WARN")
    _record_activity(user_id, "BANKROLL_SET", {"old": old, "new": new_bankroll, "set_by": actor_username})


def apply_bet_pl(user_id: str, pl: float):
    """Apply a settled bet P/L to the user's account."""
    from db import get_db, safe_query, T
    acct = get_user_account(user_id)
    new_bank = round((acct.get("bankroll") or 1000) + pl, 2)
    new_total = round((acct.get("total_pl") or 0) + pl, 2)
    new_session = round((acct.get("session_pl") or 0) + pl, 2)
    wins = (acct.get("total_wins") or 0) + (1 if pl > 0 else 0)
    bets = (acct.get("total_bets") or 0) + 1
    peak = max(acct.get("peak_bank") or 0, new_bank)
    now = datetime.utcnow().isoformat()
    safe_query(lambda: get_db().table(T("user_accounts")).upsert({
        "user_id": user_id, "bankroll": new_bank,
        "total_pl": new_total, "session_pl": new_session,
        "total_bets": bets, "total_wins": wins, "peak_bank": peak,
        "updated_at": now,
        "created_at": now,   # W-09: required for INSERT path of upsert (no DB default in code)
    }, on_conflict="user_id").execute())


def reset_session_pl(user_id: str, actor_username: str):
    from db import get_db, safe_query, T
    safe_query(lambda: get_db().table(T("user_accounts")).update({
        "session_pl": 0.0, "last_session_reset": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("user_id", user_id).execute())
    _record_activity(user_id, "SESSION_RESET", {"reset_by": actor_username})


# ─────────────────────────────────────────────────────────────────
# PER-USER SETTINGS
# ─────────────────────────────────────────────────────────────────
def get_user_settings(user_id: str) -> dict:
    from db import get_db, safe_query, T
    row = safe_query(
        lambda: get_db().table(T("user_accounts")).select("settings,alerts,admin_notes")
                .eq("user_id", user_id).single().execute().data
    ) or {}
    return {"settings": row.get("settings") or {}, "alerts": row.get("alerts") or {},
            "admin_notes": row.get("admin_notes") or ""}


def update_user_settings(user_id: str, settings: dict | None = None,
                          alerts: dict | None = None, admin_notes: str | None = None):
    from db import get_db, safe_query, T
    patch = {"updated_at": datetime.utcnow().isoformat()}
    if settings is not None: patch["settings"] = settings
    if alerts is not None:   patch["alerts"] = alerts
    if admin_notes is not None: patch["admin_notes"] = admin_notes
    safe_query(lambda: get_db().table(T("user_accounts")).update(patch)
               .eq("user_id", user_id).execute())


# ─────────────────────────────────────────────────────────────────
# PER-USER BETS & ACTIVITY
# ─────────────────────────────────────────────────────────────────
def get_user_bets(user_id: str, limit: int = 100) -> list[dict]:
    from db import get_db, safe_query, T
    return safe_query(
        lambda: get_db().table(T("bet_log")).select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True).limit(limit).execute().data, []
    ) or []


def get_user_activity(user_id: str, limit: int = 100) -> list[dict]:
    from db import get_db, safe_query, T
    return safe_query(
        lambda: get_db().table(T("user_activity")).select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True).limit(limit).execute().data, []
    ) or []


def _record_activity(user_id: str, action: str, detail: dict | None = None, ip: str | None = None):
    try:
        from db import get_db, safe_query, T
        safe_query(lambda: get_db().table(T("user_activity")).insert({
            "user_id":    user_id,
            "action":     action,
            "detail":     detail or {},   # W-05: pass dict directly into JSONB column
            "ip_address": ip,
            "created_at": datetime.utcnow().isoformat(),
        }).execute())
    except Exception as e:
        log.error(f"Activity log failed: {e}")
