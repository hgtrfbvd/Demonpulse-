from datetime import datetime
from supabase import create_client
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def log_action(user_id=None, username=None, action="", target="", details=None):
    try:
        supabase.table("audit_log").insert({
            "user_id": str(user_id) if user_id else None,
            "username": username,
            "action": action,
            "target": target,
            "details": details or {},
            "created_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        print(f"[AUDIT ERROR] {e}")
