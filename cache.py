"""
cache.py - In-memory cache, request deduplication, rate limiting
Feature coverage: 44, 45, 46
"""
import time
import hashlib
import logging
import threading
from collections import defaultdict

log = logging.getLogger(__name__)

_cache = {}
_dedup = {}
_rate_limits = defaultdict(list)
_lock = threading.Lock()

# ----------------------------------------------------------------
# CACHE
# ----------------------------------------------------------------
def cache_set(key: str, value, ttl: int = 90):
    with _lock:
        _cache[key] = {"value": value, "expires": time.time() + ttl, "created": time.time()}

def cache_get(key: str):
    with _lock:
        entry = _cache.get(key)
        if not entry:
            return None
        if time.time() > entry["expires"]:
            del _cache[key]
            return None
        return entry["value"]

def cache_age(key: str):
    with _lock:
        entry = _cache.get(key)
        if not entry:
            return None
        return round(time.time() - entry["created"], 1)

def cache_clear(key: str = None):
    with _lock:
        if key:
            _cache.pop(key, None)
        else:
            _cache.clear()
            _dedup.clear()

def cache_stats():
    with _lock:
        now = time.time()
        active = [
            {"key": k[:20], "ttl_remaining": round(v["expires"] - now, 1), "age": round(now - v["created"], 1)}
            for k, v in _cache.items() if v["expires"] > now
        ]
    return {"active_entries": len(active), "entries": active}

def make_key(command: str, code: str = "GREYHOUND") -> str:
    raw = f"{command.strip().lower()}:{code}"
    return hashlib.md5(raw.encode()).hexdigest()

def is_duplicate(key: str, window: float = 3.0) -> bool:
    with _lock:
        last = _dedup.get(key)
        if last and time.time() - last < window:
            return True
        _dedup[key] = time.time()
    return False

def check_rate_limit(domain: str, max_per_minute: int = 20) -> bool:
    with _lock:
        now = time.time()
        window = 60
        _rate_limits[domain] = [t for t in _rate_limits[domain] if now - t < window]
        if len(_rate_limits[domain]) >= max_per_minute:
            log.warning(f"Rate limit hit for {domain}")
            return False
        _rate_limits[domain].append(now)
    return True

def get_rate_stats():
    with _lock:
        now = time.time()
        return {
            domain: len([t for t in times if now - t < 60])
            for domain, times in _rate_limits.items()
        }
