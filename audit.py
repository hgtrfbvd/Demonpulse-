from datetime import datetime
from supabase import create_client
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def log_event(user_id=None, username=None, event_type="", resource="", data=None, severity="INFO"):
    try:
        supabase.table("audit_log").insert({
            "user_id": str(user_id) if user_id else None,
            "username": username,
            "event_type": event_type,
            "resource": resource,
            "data": data or {},
            "severity": severity,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        print(f"[AUDIT ERROR] {e}")


def log_action(user_id=None, username=None, action="", target="", details=None):
    log_event(
        user_id=user_id,
        username=username,
        event_type=action,
        resource=target,
        data=details,
    )
