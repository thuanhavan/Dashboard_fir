"""TTL cache shared by both pages, and the storage bundle (slow: lfs quota + robinhood stats, 10 min)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import collectors as c

CACHE_TTL = 60
STORAGE_TTL = 600
_ttl = {}                      # key -> (expires_at, value)
_lock = threading.Lock()       # guards _ttl and _key_locks
_key_locks = {}                # key -> Lock held while that key is being computed (single flight)


def _fresh(key):
    hit = _ttl.get(key)
    return hit if hit and time.time() < hit[0] else None


def cached(key, ttl, fn, *args):
    """fn(*args), memoised under key for ttl seconds. Exceptions are not cached.

    Single flight: a second caller missing the same key waits for the first and then reads its value."""
    with _lock:
        hit = _fresh(key)
        if hit:
            return hit[1]
        key_lock = _key_locks.setdefault(key, threading.Lock())
    with key_lock:
        with _lock:
            hit = _fresh(key)
        if hit:
            return hit[1]
        v = fn(*args)
        now = time.time()
        with _lock:
            for k in [k for k, (exp, _) in _ttl.items() if exp <= now]:
                del _ttl[k]
            _ttl[key] = (now + ttl, v)
    return v


def storage():
    return cached("storage", STORAGE_TTL, _storage)


def _storage():
    gs = c.groups()
    pairs = [(fs, g) for fs in ("project", "nearline") for g in gs]
    with ThreadPoolExecutor(8) as ex:
        uq = ex.submit(c.user_quotas)
        list(ex.map(lambda k: c.fs_stats(*k), pairs))   # read each lfs quota + robinhood file once (60 s memo)
        gq = ex.submit(c.group_quotas)
        bd = {k: ex.submit(c.user_breakdown, *k) for k in pairs}
        return {"groups": gs, "quotas": gq.result() + uq.result(),
                "breakdown": {k: f.result() for k, f in bd.items()}, "asof": c.stats_asof()}
