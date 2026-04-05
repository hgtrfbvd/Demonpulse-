"""
repositories/users_repo.py — User management data access
=========================================================
Covers: users, user_accounts, user_permissions, user_sessions, user_activity.

Security rules enforced here:
- users and audit_log are always-live (never test-prefixed)
- password hashes are never returned by query helpers
- role changes are always logged via audit
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import (
    TABLE_USERS,
    TABLE_USER_ACCOUNTS,
    TABLE_USER_PERMS,
    TABLE_USER_SESSIONS,
    TABLE_USER_ACTIVITY,
    VALID_ROLES,
    ROLE_VIEWER,
)

log = logging.getLogger(__name__)


class UsersRepo:
    """Repository for user tables."""

    # ── USER READS ────────────────────────────────────────────────

    @staticmethod
    def get_by_username(username: str) -> Optional[dict]:
        """Fetch full user record (including password_hash) for auth only."""
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .select("*")
                .eq("username", username)
                .limit(1)
                .execute()
                .data,
            default=[],
            context="UsersRepo.get_by_username",
        ) or []
        return rows[0] if rows else None

    @staticmethod
    def get_by_id(user_id: str) -> Optional[dict]:
        """Fetch user record by UUID (excludes password_hash)."""
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .select("id,username,role,active,last_login,created_at")
                .eq("id", user_id)
                .limit(1)
                .execute()
                .data,
            default=[],
            context="UsersRepo.get_by_id",
        ) or []
        return rows[0] if rows else None

    @staticmethod
    def list_all() -> list[dict]:
        """List all users (no password hashes)."""
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .select("id,username,role,active,last_login,created_at")
                .order("created_at")
                .execute()
                .data,
            default=[],
            context="UsersRepo.list_all",
        ) or []

    @staticmethod
    def count() -> int:
        """Return total user count (used for bootstrap checks)."""
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .select("id", count="exact")
                .execute(),
            default=None,
            context="UsersRepo.count",
        )
        if rows and hasattr(rows, "count"):
            return rows.count or 0
        return 0

    @staticmethod
    def exists(username: str) -> bool:
        """Return True if a user with this username exists."""
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .select("id")
                .eq("username", username)
                .limit(1)
                .execute()
                .data,
            default=[],
            context="UsersRepo.exists",
        ) or []
        return len(rows) > 0

    # ── USER WRITES ───────────────────────────────────────────────

    @staticmethod
    def create(
        username: str,
        password_hash: str,
        role: str = ROLE_VIEWER,
        display_name: str = "",
        email: str = "",
        created_by: Optional[str] = None,
    ) -> Optional[dict]:
        """Insert a new user record."""
        role = role if role in VALID_ROLES else ROLE_VIEWER
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .insert({
                    "username":      username,
                    "password_hash": password_hash,
                    "role":          role,
                    "active":        True,
                    "created_at":    _now(),
                    "updated_at":    _now(),
                })
                .execute()
                .data,
            default=None,
            context="UsersRepo.create",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    @staticmethod
    def update_role(user_id: str, role: str) -> bool:
        """Update a user's role. Role must be in VALID_ROLES."""
        if role not in VALID_ROLES:
            log.warning(f"UsersRepo: invalid role '{role}'")
            return False
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .update({"role": role, "updated_at": _now()})
                .eq("id", user_id)
                .execute()
                .data,
            default=None,
            context="UsersRepo.update_role",
        )
        return bool(result)

    @staticmethod
    def set_active(user_id: str, active: bool) -> bool:
        """Enable or disable a user account."""
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .update({"active": active, "updated_at": _now()})
                .eq("id", user_id)
                .execute()
                .data,
            default=None,
            context="UsersRepo.set_active",
        )
        return bool(result)

    @staticmethod
    def record_login(user_id: str, ip: str = "") -> bool:
        """Update last_login timestamp."""
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USERS))
                .update({
                    "last_login":  _now(),
                    "updated_at":  _now(),
                })
                .eq("id", user_id)
                .execute()
                .data,
            default=None,
            context="UsersRepo.record_login",
        )
        return bool(result)

    # ── USER ACCOUNTS ─────────────────────────────────────────────

    @staticmethod
    def get_account(user_id: str) -> Optional[dict]:
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USER_ACCOUNTS))
                .select("*")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
                .data,
            default=[],
            context="UsersRepo.get_account",
        ) or []
        return rows[0] if rows else None

    @staticmethod
    def upsert_account(user_id: str, **fields) -> Optional[dict]:
        fields["user_id"] = user_id
        fields["updated_at"] = _now()
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USER_ACCOUNTS))
                .upsert(fields, on_conflict="user_id")
                .execute()
                .data,
            default=None,
            context="UsersRepo.upsert_account",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    # ── USER PERMISSIONS ──────────────────────────────────────────

    @staticmethod
    def get_permissions(user_id: str) -> list[dict]:
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USER_PERMS))
                .select("*")
                .eq("user_id", user_id)
                .execute()
                .data,
            default=[],
            context="UsersRepo.get_permissions",
        ) or []

    @staticmethod
    def set_permission(user_id: str, page: str, allowed: bool) -> bool:
        """Grant or revoke a single page in the user's effective permissions array.

        Aligns with the canonical array-based user_permissions design used by
        users.py (granted[], revoked[], effective[]) on_conflict="user_id".
        """
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USER_PERMS))
                .select("granted,revoked,effective")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
                .data,
            default=[],
            context="UsersRepo.set_permission.read",
        ) or []
        row = rows[0] if rows else {}
        granted      = list(row.get("granted") or [])
        revoked_list = list(row.get("revoked") or [])
        if allowed:
            if page not in granted:
                granted.append(page)
            if page in revoked_list:
                revoked_list.remove(page)
        else:
            if page in granted:
                granted.remove(page)
            if page not in revoked_list:
                revoked_list.append(page)
        effective = sorted(set(granted) - set(revoked_list))
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USER_PERMS))
                .upsert(
                    {
                        "user_id":   user_id,
                        "granted":   granted,
                        "revoked":   revoked_list,
                        "effective": effective,
                    },
                    on_conflict="user_id",
                )
                .execute()
                .data,
            default=None,
            context="UsersRepo.set_permission",
        )
        return bool(result)

    # ── USER SESSIONS ─────────────────────────────────────────────

    @staticmethod
    def create_session(user_id: str, token_hash: str, ip: str = "", ttl_minutes: int = 480) -> Optional[dict]:
        from datetime import timedelta
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USER_SESSIONS))
                .insert({
                    "user_id":    user_id,
                    "token_jti":  token_hash,
                    "ip_address": ip,
                    "expires_at": expires.isoformat(),
                    "created_at": _now(),
                })
                .execute()
                .data,
            default=None,
            context="UsersRepo.create_session",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    @staticmethod
    def invalidate_sessions(user_id: str) -> bool:
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USER_SESSIONS))
                .delete()
                .eq("user_id", user_id)
                .execute()
                .data,
            default=None,
            context="UsersRepo.invalidate_sessions",
        )
        return result is not None

    # ── USER ACTIVITY ─────────────────────────────────────────────

    @staticmethod
    def log_activity(user_id: str, action: str, page: str = "", data: Optional[dict] = None) -> None:
        safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_USER_ACTIVITY))
                .insert({
                    "user_id":    user_id,
                    "action":     action,
                    "resource":   page,
                    "detail":     data or {},
                    "created_at": _now(),
                })
                .execute(),
            context="UsersRepo.log_activity",
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
