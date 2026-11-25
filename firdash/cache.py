"""TTL cache shared by both pages, and the storage bundle (slow: lfs quota + robinhood stats, 10 min)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import collectors as c

CACHE_TTL = 60
STORAGE_TTL = 600
_ttl = {}
_lock = threading.Lock()


def cached(key, ttl, fn, *args):
    """fn(*args), memoised under key for ttl seconds. Exceptions are not cached."""
    with _lock:
        hit = _ttl.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn(*args)
    with _lock:
        _ttl[key] = (time.time(), v)
    return v


def storage():
    return cached("storage", STORAGE_TTL, _storage)


def _storage():
    gs = c.groups()
    with ThreadPoolExecutor(8) as ex:
        gq = ex.submit(c.group_quotas)
        uq = ex.submit(c.user_quotas)
        bd = {(fs, g): ex.submit(c.user_breakdown, fs, g) for fs in ("project", "nearline") for g in gs}
        return {"groups": gs, "quotas": gq.result() + uq.result(),
                "breakdown": {k: f.result() for k, f in bd.items()}, "asof": c.stats_asof()}
