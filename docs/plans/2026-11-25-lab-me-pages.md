# Lab and Me Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use usask-foundation:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 16-panel Boards layout with two pages, Lab (whole picture of all groups) and Me (the signed-in member), per `docs/specs/2026-11-25-lab-me-pages-design.md`.

**Architecture:** `render.py` becomes a thin shell (top bar with tabs and a days select, CSS, sidebar). Page content comes from `lab.py` and `me.py`, which gather data in parallel and assemble cards from `panels.py`. Shared TTL caching moves to a new `cache.py`. Two collectors are added (`usage_by_month` via sreport, `queue_wait` via sacct). Every panel is built through `safe()` so one failing command yields a "no data" card, not a blank page.

**Tech Stack:** Python 3.11+ standard library only (the dashboard has no dependencies). Tests with `unittest` (`python3 -m unittest discover -s tests -v`). Runs on Fir inside JupyterHub (compute node) or on a login node.

## Global Constraints

- Standard library only; no new dependencies (README: "Standard library only, so no install is needed").
- Python 3.11 on Fir: no syntax newer than 3.11.
- URL parameters are exactly `page` (`lab` | `me`) and `days` (7 | 30 | 90); defaults `lab`, 30 for Lab, 7 for Me. All links relative (JupyterHub prefix).
- Quota tiles turn amber at 85 % and red at 95 % (`charts.status_fill` already does this).
- Me shows only the user's own records; no other member's name appears on that page.
- Commit at every task boundary with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` as the last line.
- Previous layout is tagged `v1-thuan-boards`; do not delete anything from git history.

## File structure

| File | Responsibility |
|---|---|
| `firdash/collectors.py` | Runs cluster commands, parses output. Add `parse_sreport`, `usage_by_month`, `parse_queue_wait`, `queue_wait`; `user_breakdown` also returns file counts; delete collectors whose panels are gone (`filesystems`, `arrays`, `reservations`, `pending_lab`, `group_jobs`). |
| `firdash/charts.py` | SVG primitives. Two one-line changes: `line()` tolerates float values; `columns()` takes `max_bw`. |
| `firdash/cache.py` | NEW. `cached(key, ttl, fn, *args)`, `storage()` bundle (groups, quotas, per-user breakdown, nearline as-of). |
| `firdash/panels.py` | NEW. Everything that returns a card or a chart body: moved helpers (`pct`, `quota_chart`, `user_colors`, `usage_chart`, `nodes_chart`, `fairshare_chart`, `jobs_per_day`, `efficiency_card`, `kpi`, `quota_tile`, `card`, `table`, `REASONS`) and new ones (`safe`, `gather`, `val`, `fold`, `extend_colors`, `member_chart`, `usage_month_chart`, `queue_wait_chart`, `my_share_chart`, `pending_table`, `problems_table`, `hours_text`, `month_label`). |
| `firdash/lab.py` | NEW. `build_lab(days) -> (kpi_html, grid_html, alerts, colors)`. |
| `firdash/me.py` | NEW. `build_me(days) -> (kpi_html, grid_html, alerts, colors)`. |
| `firdash/render.py` | Shell only: `build(params)`, `shell(...)`, CSS, sidebar, top bar. Under 250 lines. |
| `firdash/server.py` | `--page` for `--html`; `--days` default None so page defaults apply. |
| `tests/` | NEW. `test_collectors.py`, `test_panels.py`, `test_pages.py`, `fixtures/`. |
| `README.md` | Pages section replaces the old "Pages (sidebar)" list and the trend bullet. |

---

### Task 1: sreport usage collector

**Files:**
- Modify: `firdash/collectors.py` (top imports; append after `stats_asof`)
- Create: `tests/__init__.py` (empty), `tests/fixtures/sreport_cpu.txt`, `tests/fixtures/sreport_gpu.txt`, `tests/test_collectors.py`

**Interfaces:**
- Produces: `parse_sreport(text: str, tres: str | None) -> dict[str, float]` (user → hours; account-total rows with empty login skipped); `usage_by_month(accounts: list[str], months: int = 3) -> dict[str, dict[str, dict[str, float]]]` keyed `"YYYY-MM"` → user → `{"cpu": h, "gpu": h}`; `_month_starts(n, today=None) -> list[date]` (n month starts oldest first, plus next month's first day).

- [ ] **Step 1: Fixtures**

`tests/fixtures/sreport_cpu.txt` (real output shape of `sreport -t hours -P -n cluster AccountUtilizationByUser accounts=… format=Account,Login,Used`):

```
def-acilab_cpu||88816
def-acilab_cpu|fangliu|3009
def-acilab_cpu|hvt632|67594
def-acilab_cpu|yaw|18214
def-kanj241_cpu||132768
def-kanj241_cpu|fangliu|9430
def-kanj241_cpu|hvt632|70642
def-kanj241_cpu|yaw|34412
```

`tests/fixtures/sreport_gpu.txt` (`--tres=gres/gpu format=Account,Login,TRESName,Used`):

```
def-acilab_gpu||gres/gpu|241
def-acilab_gpu|yaw|gres/gpu|241
def-kanj241_gpu||gres/gpu|803
def-kanj241_gpu|hvt632|gres/gpu|24
def-kanj241_gpu|nazifa|gres/gpu|419
```

Create empty `tests/__init__.py`.

- [ ] **Step 2: Write the failing tests**

`tests/test_collectors.py`:

```python
import datetime as dt
import unittest
from pathlib import Path
from unittest import mock

from firdash import collectors as c

FIX = Path(__file__).parent / "fixtures"


class SreportTests(unittest.TestCase):
    def test_parse_cpu_sums_accounts_and_skips_totals(self):
        got = c.parse_sreport((FIX / "sreport_cpu.txt").read_text(), None)
        self.assertEqual(got, {"fangliu": 12439.0, "hvt632": 138236.0, "yaw": 52626.0})

    def test_parse_gpu_uses_tres_column(self):
        got = c.parse_sreport((FIX / "sreport_gpu.txt").read_text(), "gres/gpu")
        self.assertEqual(got, {"yaw": 241.0, "hvt632": 24.0, "nazifa": 419.0})

    def test_month_starts(self):
        got = c._month_starts(3, dt.date(2026, 11, 25))
        self.assertEqual(got, [dt.date(2026, 9, 1), dt.date(2026, 10, 1), dt.date(2026, 11, 1), dt.date(2026, 12, 1)])

    def test_usage_by_month_runs_cpu_and_gpu_per_month(self):
        calls = []

        def fake_run(cmd, timeout=90):
            calls.append(cmd)
            return (FIX / ("sreport_gpu.txt" if "gres/gpu" in cmd else "sreport_cpu.txt")).read_text()

        with mock.patch.object(c, "run", fake_run), mock.patch.object(c, "_month_starts",
                lambda n, today=None: [dt.date(2026, 10, 1), dt.date(2026, 11, 1), dt.date(2026, 12, 1)]):
            got = c.usage_by_month(["def-kanj241_cpu", "def-kanj241_gpu", "def-acilab_cpu", "def-acilab_gpu"], months=2)
        self.assertEqual(sorted(got), ["2026-10", "2026-11"])
        self.assertEqual(got["2026-11"]["hvt632"], {"cpu": 138236.0, "gpu": 24.0})
        self.assertEqual(got["2026-11"]["nazifa"], {"cpu": 0.0, "gpu": 419.0})
        self.assertEqual(len(calls), 4)
        self.assertIn("accounts=def-acilab_cpu,def-kanj241_cpu start=2026-10-01 end=2026-11-01", calls[0])
        self.assertIn("--tres=gres/gpu", calls[1])
        self.assertTrue(calls[2].endswith("format=Account,Login,Used") and "end=now" in calls[2],
                        "current month must end at 'now' (sreport rejects future end times)")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify it fails**

Run: `python3 -m unittest tests.test_collectors -v`
Expected: 4 failures/errors, `AttributeError: module 'firdash.collectors' has no attribute 'parse_sreport'`.

- [ ] **Step 4: Implement**

In `firdash/collectors.py`, change the import block to:

```python
import datetime as dt
import json
import os
import re
import subprocess
import time
```

Append after `stats_asof()`:

```python
def _month_starts(n, today=None):
    """First day of the last n calendar months, oldest first, then the first day of next month (end bound)."""
    today = today or dt.date.today()
    starts = [today.replace(day=1)]
    for _ in range(n - 1):
        starts.insert(0, (starts[0] - dt.timedelta(days=1)).replace(day=1))
    return starts + [(starts[-1] + dt.timedelta(days=32)).replace(day=1)]


def parse_sreport(text, tres):
    """{user: hours} from `sreport -P -n cluster AccountUtilizationByUser`. Rows with an empty login are
    account totals and are skipped. With tres (e.g. 'gres/gpu') the row is Account|Login|TRESName|Used."""
    out = {}
    for line in text.splitlines():
        p = line.split("|")
        if tres:
            if len(p) < 4 or not p[1] or p[2] != tres:
                continue
            user, used = p[1], p[3]
        else:
            if len(p) < 3 or not p[1]:
                continue
            user, used = p[1], p[2]
        try:
            out[user] = out.get(user, 0.0) + float(used)
        except ValueError:
            continue
    return out


def usage_by_month(accounts, months=3):
    """{'YYYY-MM': {user: {'cpu': core-hours, 'gpu': gpu-hours}}} for the last `months` calendar months.
    One sreport call per month for the *_cpu accounts and one with --tres=gres/gpu for the *_gpu accounts."""
    cpu = sorted(a for a in set(accounts) if a.endswith("_cpu"))
    gpu = sorted(a for a in set(accounts) if a.endswith("_gpu"))
    bounds = _month_starts(months)
    today = dt.date.today()
    out = {}
    for s, e in zip(bounds, bounds[1:]):
        end = "now" if e > today else str(e)
        m = out.setdefault(s.strftime("%Y-%m"), {})
        if cpu:
            text = run(f"sreport -t hours -P -n cluster AccountUtilizationByUser accounts={','.join(cpu)} "
                       f"start={s} end={end} format=Account,Login,Used", 60)
            for u, h in parse_sreport(text, None).items():
                m.setdefault(u, {"cpu": 0.0, "gpu": 0.0})["cpu"] += h
        if gpu:
            text = run(f"sreport -t hours -P -n cluster AccountUtilizationByUser accounts={','.join(gpu)} "
                       f"start={s} end={end} --tres=gres/gpu format=Account,Login,TRESName,Used", 60)
            for u, h in parse_sreport(text, "gres/gpu").items():
                m.setdefault(u, {"cpu": 0.0, "gpu": 0.0})["gpu"] += h
    return out
```

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m unittest tests.test_collectors -v`
Expected: `OK` (4 tests).

- [ ] **Step 6: Commit**

```bash
git add firdash/collectors.py tests/__init__.py tests/fixtures/sreport_cpu.txt tests/fixtures/sreport_gpu.txt tests/test_collectors.py
git commit -m "collectors: core-hours and GPU-hours per member per month from sreport

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: queue-wait collector, file counts in the breakdown, dead collectors removed

**Files:**
- Modify: `firdash/collectors.py` (`user_breakdown`; append `parse_queue_wait`, `queue_wait`; delete `group_jobs`, `filesystems`, `arrays`, `_ntasks`, `reservations`, `pending_lab`)
- Create: `tests/fixtures/sacct_wait.txt`
- Modify: `tests/test_collectors.py`

**Interfaces:**
- Produces: `parse_queue_wait(text) -> list[tuple[str, float]]` of `(start date 'YYYY-MM-DD', wait seconds)`; `queue_wait(accounts, days) -> same`; `user_breakdown(fs, group)` now returns `{user: {"disk": bytes, "tape": bytes, "files": n}}`.
- Removed (nothing may import them after this task): `group_jobs`, `filesystems`, `arrays`, `reservations`, `pending_lab`.

- [ ] **Step 1: Fixture**

`tests/fixtures/sacct_wait.txt` (`sacct … -X -n -P -o Submit,Start,State`):

```
2026-11-20T09:00:00|2026-11-20T09:30:00|COMPLETED
2026-11-20T10:00:00|2026-11-21T10:00:00|FAILED
2026-11-22T08:00:00|Unknown|PENDING
2026-11-22T08:00:00|None|CANCELLED by 123
2026-11-23T12:00:00|2026-11-23T12:00:00|RUNNING
garbage line
```

- [ ] **Step 2: Write the failing tests** (append to `tests/test_collectors.py` before `if __name__`)

```python
class QueueWaitTests(unittest.TestCase):
    def test_parse_skips_unstarted_and_garbage(self):
        got = c.parse_queue_wait((FIX / "sacct_wait.txt").read_text())
        self.assertEqual(got, [("2026-11-20", 1800.0), ("2026-11-21", 86400.0), ("2026-11-23", 0.0)])

    def test_queue_wait_command(self):
        calls = []
        with mock.patch.object(c, "run", lambda cmd, timeout=90: calls.append(cmd) or ""):
            self.assertEqual(c.queue_wait(["b_cpu", "a_cpu", "b_cpu"], 30), [])
            self.assertEqual(c.queue_wait([], 30), [])
        self.assertEqual(calls, ["sacct -a -A a_cpu,b_cpu -S now-30days -X -n -P -o Submit,Start,State"])


class BreakdownTests(unittest.TestCase):
    def test_user_breakdown_counts_files(self):
        stats = {"quota": None, "asof": None, "stats": [
            {"uid": "alice", "count": 5, "size": 10, "volume": 12, "location": "On disk"},
            {"uid": "alice", "count": 2, "size": 100, "volume": 0, "location": "On tape"},
            {"uid": "root", "count": 9, "size": 9, "volume": 9, "location": "On disk"}]}
        with mock.patch.object(c, "fs_stats", lambda fs, g: stats):
            self.assertEqual(c.user_breakdown("project", "g"), {"alice": {"disk": 12.0, "tape": 0.0, "files": 5}})
            self.assertEqual(c.user_breakdown("nearline", "g"), {"alice": {"disk": 0.0, "tape": 100.0, "files": 2}})

    def test_removed_collectors_are_gone(self):
        for name in ("group_jobs", "filesystems", "arrays", "reservations", "pending_lab"):
            self.assertFalse(hasattr(c, name), name)
```

- [ ] **Step 3: Run to verify it fails**

Run: `python3 -m unittest tests.test_collectors -v`
Expected: `parse_queue_wait` AttributeError, `files` key missing, removed-collectors assertion fails.

- [ ] **Step 4: Implement**

Replace `user_breakdown` with:

```python
def user_breakdown(fs, group):
    """{user: {"disk": bytes, "tape": bytes, "files": n}} from the robinhood stats, same rules as diskusage_rbh."""
    users = {}
    for e in fs_stats(fs, group)["stats"]:
        loc = e.get("location", "")
        if e.get("uid") == "root" or (loc == "On disk") != (fs == "project"):
            continue
        n = e.get("size", 0) if loc == "On tape" else e.get("volume", 0)
        d = users.setdefault(e["uid"], {"disk": 0.0, "tape": 0.0, "files": 0})
        d["tape" if loc == "On tape" else "disk"] += float(n)
        d["files"] += int(e.get("count", 0))
    return {u: v for u, v in users.items() if v["disk"] + v["tape"] > 0}
```

Append after `usage_by_month`:

```python
def parse_queue_wait(text):
    """[(start date 'YYYY-MM-DD', wait seconds)] from sacct rows 'Submit|Start|State'. Jobs that have not
    started (Unknown/None start, PENDING) and unparsable lines are skipped; a negative wait is clamped to 0."""
    out = []
    for line in text.splitlines():
        p = line.split("|")
        if len(p) < 3 or p[1] in ("Unknown", "None", "") or p[2].startswith("PENDING"):
            continue
        try:
            sub, st = dt.datetime.fromisoformat(p[0]), dt.datetime.fromisoformat(p[1])
        except ValueError:
            continue
        out.append((st.date().isoformat(), max(0.0, (st - sub).total_seconds())))
    return out


def queue_wait(accounts, days):
    """Queue wait of every job on the accounts that was active in the last `days` days (sacct, ~7 s for 30 d)."""
    if not accounts:
        return []
    return parse_queue_wait(run(f"sacct -a -A {','.join(sorted(set(accounts)))} -S now-{days}days -X -n -P -o Submit,Start,State", 180))
```

Delete the functions `group_jobs`, `filesystems`, `_ntasks`, `arrays`, `reservations` and `pending_lab` (whole definitions). Keep `recent`, `nodes`, `pending_start`, `_secs`, `_mem`, `efficiency`, `jobs`, `fairshare`.

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m unittest tests.test_collectors -v`
Expected: `OK` (8 tests). Also `python3 -c "import firdash.render"` still imports (render.py is untouched until Task 6; it references none of the deleted names at import time).

- [ ] **Step 6: Commit**

```bash
git add firdash/collectors.py tests/fixtures/sacct_wait.txt tests/test_collectors.py
git commit -m "collectors: queue wait from sacct, file counts per user, drop collectors of removed panels

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: cache.py and panels.py

**Files:**
- Create: `firdash/cache.py`, `firdash/panels.py`, `tests/test_panels.py`
- Modify: `firdash/charts.py:76-84` (`line` ymax), `firdash/charts.py:116-120` (`columns` max_bw)

**Interfaces:**
- Produces (`cache.py`): `CACHE_TTL = 60`, `STORAGE_TTL = 600`, `cached(key, ttl, fn, *args)`, `storage() -> {"groups": [...], "quotas": [row…], "breakdown": {(fs, group): {user: {...}}}, "asof": str | None}` (row = `{"label", "used", "quota", "files", "fquota"}`).
- Produces (`panels.py`): everything listed in the file-structure table. Exact signatures appear in the code below; later tasks import them by name.
- Produces (`charts.py`): `columns(xlabels, series, width=620, height=260, max_bw=28)`; `line()` accepts float values.

- [ ] **Step 1: Write the failing tests**

`tests/test_panels.py`:

```python
import unittest
from unittest import mock

from firdash import panels as p
from firdash.charts import SERIES, OTHER


class ChartTests(unittest.TestCase):
    def test_usage_month_chart_stacks_members_and_other(self):
        usage = {"2026-10": {"a": {"cpu": 10, "gpu": 0}, "b": {"cpu": 5, "gpu": 1}},
                 "2026-11": {"a": {"cpu": 20, "gpu": 0}}}
        html = p.usage_month_chart(usage, "cpu", {"a": SERIES[0]})
        self.assertIn("Oct 2026", html)
        self.assertIn("Nov 2026", html)
        self.assertIn("a: 20", html)
        self.assertIn("b: 0", html)       # b has no November usage, series still has a value
        self.assertEqual(p.usage_month_chart({}, "gpu", {}), '<p class="muted">No usage recorded in this period.</p>')

    def test_queue_wait_chart_buckets_weeks(self):
        import datetime as dt
        today = dt.date.today()
        waits = [((today - dt.timedelta(days=d)).isoformat(), 3600.0 * (1 + d)) for d in range(0, 14)]
        html = p.queue_wait_chart(waits, 14)
        self.assertIn("median", html)
        self.assertIn("90th", html)
        self.assertEqual(html.count("<polyline"), 2)

    def test_member_chart_counts_completed_and_problems(self):
        hist = [["1", "x", "COMPLETED", "", "", "", "", "", "2026-11-01T00:00:00", "a"],
                ["2", "x", "FAILED", "", "", "", "", "", "2026-11-01T00:00:00", "a"],
                ["3", "x", "CANCELLED by 1", "", "", "", "", "", "2026-11-01T00:00:00", "b"]]
        html = p.member_chart(hist)
        self.assertIn("a: 1 completed", html)
        self.assertIn("a: 1 problems", html)
        self.assertIn("b: 1 problems", html)

    def test_my_share_chart_reads_own_user(self):
        with mock.patch.object(p.c, "USER", "me"):
            bd = {("project", "g1"): {"me": {"disk": 2**40, "tape": 0.0, "files": 3}, "x": {"disk": 5 * 2**40, "tape": 0.0, "files": 1}},
                  ("nearline", "g1"): {"x": {"disk": 0.0, "tape": 2**40, "files": 1}}}
            quotas = [{"label": "g1 /project", "used": 6 * 2**40, "quota": 10 * 2**40, "files": 4, "fquota": 100},
                      {"label": "g1 /nearline", "used": 2**40, "quota": 20 * 2**40, "files": 1, "fquota": 100}]
            html = p.my_share_chart(bd, quotas)
        self.assertIn("me: 1.0 TiB of 10.0 TiB (10.0%)", html)
        self.assertIn("3 files", html)
        self.assertIn("g1 /nearline", html)
        self.assertNotIn("x:", html)

    def test_problems_table_hints(self):
        bad = [["7", "job", "TIMEOUT", "01:00:00", "01:00:00", "4G", "0:0", "acct", "2026-11-01T01:00:00", "u"]]
        html = p.problems_table(bad)
        self.assertIn("raise --time", html)
        self.assertNotIn("<th>User</th>", html)
        self.assertIn("<th>User</th>", p.problems_table(bad, with_user=True))

    def test_hours_text(self):
        self.assertEqual(p.hours_text(0.25), "15 min")
        self.assertEqual(p.hours_text(3.26), "3.3 h")
        self.assertEqual(p.hours_text(72), "3.0 d")


class SafetyTests(unittest.TestCase):
    def test_safe_turns_exception_into_card(self):
        html = p.safe("Space quota", lambda: 1 / 0)
        self.assertIn("Space quota", html)
        self.assertIn("no data", html)
        self.assertIn("ZeroDivisionError", html)
        self.assertEqual(p.safe("t", lambda: "<b>ok</b>"), "<b>ok</b>")

    def test_gather_keeps_exceptions(self):
        d = p.gather({"a": lambda: 1, "b": lambda: 1 / 0})
        self.assertEqual(d["a"], 1)
        self.assertIsInstance(d["b"], ZeroDivisionError)
        with self.assertRaises(ZeroDivisionError):
            p.val(d, "b")
        self.assertEqual(p.val(d, "a"), 1)
        self.assertIsNone(p.ok(d, "b"))

    def test_extend_colors_keeps_existing(self):
        got = p.extend_colors({"a": SERIES[3]}, ["a", "b"])
        self.assertEqual(got["a"], SERIES[3])
        self.assertEqual(got["b"], SERIES[1])


class CacheTests(unittest.TestCase):
    def test_cached_calls_once_within_ttl(self):
        from firdash import cache
        n = []
        f = lambda: n.append(1) or len(n)
        cache._ttl.clear()
        self.assertEqual(cache.cached("k", 60, f), 1)
        self.assertEqual(cache.cached("k", 60, f), 1)
        self.assertEqual(len(n), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_panels -v`
Expected: `ModuleNotFoundError: No module named 'firdash.panels'`.

- [ ] **Step 3: charts.py edits**

In `line()` replace

```python
    ymax = max([max(v) for _, _, v in series if v] + [1])
```

with

```python
    ymax = int(-(-max([max(v) for _, _, v in series if v] + [1]) // 1))   # ceil: values may be floats
```

In `columns()` change the signature and bar width:

```python
def columns(xlabels, series, width=620, height=260, max_bw=28):
```

```python
    bw = max(min(slot - 4, max_bw), 2)
```

- [ ] **Step 4: Create `firdash/cache.py`**

```python
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
```

- [ ] **Step 5: Create `firdash/panels.py`**

```python
"""Cards and chart bodies shared by the Lab and Me pages. Every function returns an HTML string."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import collectors as c
from .charts import CRIT, GOOD, OTHER, SERIES, bars, columns, count, esc, legend, line, nice_step, size, status_fill

BAD_STATES = ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "CANCELLED")
REASONS = {"Priority": "waiting for higher-priority jobs", "Resources": "next in line, waiting for free nodes",
           "Dependency": "waits for another job", "DependencyNeverSatisfied": "dependency failed: will never start",
           "AssocGrpCPULimit": "account core limit reached", "QOSMaxJobsPerUserLimit": "per-user job limit",
           "ReqNodeNotAvail": "requested nodes unavailable (maintenance?)", "BeginTime": "scheduled for later"}
HINTS = {"TIMEOUT": "ran out of time: raise --time", "OUT_OF_MEMORY": "ran out of memory: raise --mem",
         "FAILED": "exit code ≠ 0: check the job's output file", "NODE_FAIL": "node failed: resubmit",
         "CANCELLED": "cancelled by you or an admin"}


# ---- robustness ----------------------------------------------------------------------------------------------

def gather(tasks):
    """Run {key: callable} in parallel. A failure is stored as the exception instead of raised."""
    out = {}
    with ThreadPoolExecutor(max(len(tasks), 1)) as ex:
        futs = {k: ex.submit(fn) for k, fn in tasks.items()}
        for k, f in futs.items():
            try:
                out[k] = f.result()
            except Exception as e:  # noqa: BLE001 - one collector must not take the page down
                out[k] = e
    return out


def val(d, key):
    """Value from gather(); re-raises a stored exception (so safe() can show it)."""
    v = d[key]
    if isinstance(v, Exception):
        raise v
    return v


def ok(d, key):
    """Value from gather(), or None if that collector failed."""
    v = d[key]
    return None if isinstance(v, Exception) else v


def safe(title, thunk):
    """Build one card; if it raises, show a 'no data' card with the reason instead of blanking the page."""
    try:
        return thunk()
    except Exception as e:  # noqa: BLE001
        return card(title, f'<p class="muted">no data · {esc(type(e).__name__)}: {esc(str(e)[:120])}</p>')


# ---- building blocks -----------------------------------------------------------------------------------------

def card(title, body, span=1):
    return f'<section class="card" style="grid-column:span {span}"><h3>{esc(title)}</h3>{body}</section>'


def fold(summary, body, open_=False):
    """Full-width collapsible section."""
    return (f'<section class="card" style="grid-column:1/-1"><details{" open" if open_ else ""}>'
            f'<summary>{esc(summary)}</summary>{body}</details></section>')


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{body or '<tr><td colspan=99><i>none</i></td></tr>'}</table>"


def kpi(icon, title, sub, value, cls="", bar=None):
    meter = ""
    if bar is not None:
        meter = (f'<div class="meter"><div style="width:{min(bar, 100):.0f}%;background:{status_fill(bar)}"></div>'
                 f'<i style="left:95%"></i></div>')
    return (f'<div class="kpi"><div class="kpi-h">{icon} {esc(title)}</div>'
            f'<div class="kpi-b {cls}"><span>{esc(sub)}</span><b>{value}</b>{meter}</div></div>')


def pct(q, k="used", qk="quota"):
    return 100 * q[k] / max(q[qk], 1)


def quota_tile(q, title=None, note=None):
    """KPI for one quota row: whichever of space / files is fuller."""
    sp, fp = pct(q), pct(q, "files", "fquota")
    which, p = ("space", sp) if sp >= fp else ("files", fp)
    sub = f"{size(q['used'])} / {size(q['quota'])}" if which == "space" else f"{count(q['files'])} / {count(q['fquota'])} files"
    return kpi("⚠" if p >= 95 else "▤", title or q["label"], f"{which} · {sub}" + (f" · {note}" if note else ""),
               f"{p:.0f}%", "crit" if p >= 95 else "", p)


def hours_text(h):
    return f"{h * 60:.0f} min" if h < 1 else f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} d"


def month_label(m):
    return dt.date(int(m[:4]), int(m[5:7]), 1).strftime("%b %Y")


def user_colors(breakdown):
    """Member colours by total storage, the current user first."""
    totals = {}
    for users in breakdown.values():
        for u, v in users.items():
            totals[u] = totals.get(u, 0) + v["disk"] + v["tape"]
    order = [c.USER] + sorted((u for u in totals if u != c.USER), key=lambda u: -totals[u])
    return {u: SERIES[i % len(SERIES)] for i, u in enumerate(order)}


def extend_colors(colors, names):
    """Same mapping plus a colour for every new name (members seen in jobs or usage but not in storage)."""
    out = dict(colors)
    for u in names:
        if u not in out:
            out[u] = SERIES[len(out) % len(SERIES)]
    return out


# ---- storage charts ------------------------------------------------------------------------------------------

def quota_chart(quotas, key, qkey, fmt):
    rows = []
    for q in quotas:
        p = pct(q, key, qkey)
        icon = "⚠ " if p >= 85 else ""
        rows.append((q["label"], [(p, status_fill(p), f"{q['label']}: {fmt(q[key])} of {fmt(q[qkey])} ({p:.1f}%)", False, None)],
                     f'<tspan class="strong">{icon}{p:.0f}%</tspan>  {fmt(q[key])} / {fmt(q[qkey])}'))
    return bars(rows, 100, [0, 25, 50, 75, 100], lambda t: f"{t}%", ref=95)


def usage_chart(breakdown, fs, groups, colors, tape):
    """One stacked bar per group: who uses the space (tape-only hatched when tape=True)."""
    keys = sorted(k for k in breakdown if k[0] == fs and k[1] in groups)
    totals = [sum(v["disk"] + v["tape"] for v in breakdown[k].values()) for k in keys]
    xmax = max(totals + [1])
    rows = []
    for k, tot in zip(keys, totals):
        segs, other = [], 0.0
        for u, v in sorted(breakdown[k].items(), key=lambda kv: (kv[0] != c.USER, -(kv[1]["disk"] + kv[1]["tape"]))):
            t = v["disk"] + v["tape"]
            if u not in colors or t < 0.01 * tot:
                other += t
                continue
            sub = f"{u} {t / 2**40:.1f}"
            if tape and v["disk"] > 0:
                segs.append((v["disk"], colors[u], f"{u}: {size(v['disk'])} on disk", False, sub if v["disk"] >= v["tape"] else None))
            if tape and v["tape"] > 0:
                segs.append((v["tape"], colors[u], f"{u}: {size(v['tape'])} tape-only", True, sub if v["tape"] > v["disk"] else None))
            if not tape:
                segs.append((t, colors[u], f"{u}: {size(t)}", False, sub))
        if other:
            segs.append((other, OTHER, f"other users: {size(other)}", False, None))
        rows.append((k[1], segs, f'<tspan class="strong">{size(tot)}</tspan>'))
    step = max(5, round(xmax / 2**40 / 4 / 5) * 5)
    ticks = [t * 2**40 for t in range(0, int(xmax / 2**40) + 1, step)]
    return bars(rows, xmax, ticks, lambda t: f"{t / 2**40:.0f}", sublabels=True, row_h=30, value_w=90)


def my_share_chart(breakdown, quotas):
    """My /project and /nearline usage per group as % of the group quota, with size and file count."""
    rows = []
    for (fs, g), users in sorted(breakdown.items(), key=lambda kv: (kv[0][1], kv[0][0] != "project")):
        q = next((q for q in quotas if q["label"] == f"{g} /{fs}"), None)
        if not q:
            continue
        mine = users.get(c.USER, {"disk": 0.0, "tape": 0.0, "files": 0})
        used = mine["disk"] + mine["tape"]
        p = 100 * used / max(q["quota"], 1)
        rows.append((f"{g} /{fs}", [(p, SERIES[0], f"{c.USER}: {size(used)} of {size(q['quota'])} ({p:.1f}%)", False, None)],
                     f'<tspan class="strong">{size(used)}</tspan> · {p:.1f}% · {mine["files"]:,} files'))
    if not rows:
        return '<p class="muted">No group storage found.</p>'
    xmax = max([r[1][0][0] for r in rows] + [1.0])
    step = nice_step(xmax)
    xmax = step * -(-xmax // step)
    return bars(rows, xmax, [t * step for t in range(int(xmax / step) + 1)], lambda t: f"{t:g}%", value_w=220)


# ---- compute charts ------------------------------------------------------------------------------------------

def nodes_chart(nodes):
    rows = []
    for kind in ("Regular CPU", "Large-mem CPU", "GPU nodes (H100)"):
        if kind not in nodes:
            continue
        a, i, o, t, n = nodes[kind]
        rows.append((kind, [(100 * a / t, SERIES[0], f"{a:,} cores busy", False, None),
                            (100 * i / t, GOOD, f"{i:,} cores idle", False, None),
                            (100 * o / t, OTHER, f"{o:,} cores down/drained", False, None)],
                     f'<tspan class="strong">{100 * i / t:.0f}% idle</tspan>  {count(i)} / {count(t)} cores'))
    return bars(rows, 100, [0, 50, 100], lambda t: f"{t}%")


def fairshare_chart(fs):
    rows = []
    for acct, norm, eff, fair, level in fs:
        f = float(fair or 0)
        rows.append((acct, [(f, SERIES[0], f"FairShare {f:.3f} · LevelFS {level} · usage {float(eff or 0):.3f} of share {float(norm or 0):.3f}", False, None)],
                     f'<tspan class="strong">{f:.2f}</tspan>'))
    return bars(rows, 1, [0, 0.5, 1], lambda t: f"{t:g}", value_w=80)


def usage_month_chart(usage, kind, colors):
    """Stacked columns: hours per calendar month by member. usage = collectors.usage_by_month(); kind 'cpu' | 'gpu'."""
    months = sorted(usage)
    totals = {}
    for m in months:
        for u, v in usage[m].items():
            totals[u] = totals.get(u, 0.0) + v[kind]
    order = [u for u in sorted(totals, key=lambda u: -totals[u]) if totals[u] > 0]
    if not order:
        return '<p class="muted">No usage recorded in this period.</p>'
    top, rest = order[:len(SERIES)], order[len(SERIES):]
    series = [(u, colors.get(u, SERIES[i % len(SERIES)]), [round(usage[m].get(u, {}).get(kind, 0.0)) for m in months])
              for i, u in enumerate(top)]
    if rest:
        series.append(("other", OTHER, [round(sum(usage[m].get(u, {}).get(kind, 0.0) for u in rest)) for m in months]))
    return columns([month_label(m) for m in months], series, max_bw=90) + legend([(n, col) for n, col, _ in series])


def queue_wait_chart(waits, days):
    """Median and 90th-percentile queue wait per week, in hours. waits = collectors.queue_wait()."""
    if not waits:
        return '<p class="muted">No started jobs in this window.</p>'
    today = dt.date.today()
    n = max(1, -(-days // 7))
    buckets = [[] for _ in range(n)]
    for d, w in waits:
        age = (today - dt.date.fromisoformat(d)).days
        if 0 <= age < 7 * n:
            buckets[n - 1 - age // 7].append(w / 3600)
    q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(f * len(xs)))] if xs else 0.0
    med = [round(q(b, 0.5), 1) for b in buckets]
    p90 = [round(q(b, 0.9), 1) for b in buckets]
    labels = [f"wk to {(today - dt.timedelta(days=7 * k)).strftime('%b %d')}" for k in range(n - 1, -1, -1)]
    return (line(labels, [("median", SERIES[0], med), ("90th pct", CRIT, p90)])
            + legend([("median wait (h)", SERIES[0]), ("90th percentile (h)", CRIT)]))


# ---- job charts and tables ----------------------------------------------------------------------------------

def jobs_per_day(hist, days):
    """hist rows = collectors.recent(): [JobID, JobName, State, Elapsed, Timelimit, ReqMem, ExitCode, Account, End, User]."""
    today = dt.date.today()
    dates = [today - dt.timedelta(days=d) for d in range(days - 1, -1, -1)]
    idx = {d.isoformat(): i for i, d in enumerate(dates)}
    good, bad = [0] * days, [0] * days
    for r in hist:
        i = idx.get(r[8][:10])
        if i is None:
            continue
        if r[2].startswith("COMPLETED"):
            good[i] += 1
        elif r[2].split()[0] in BAD_STATES:
            bad[i] += 1
    labels = [d.strftime("%b %d") for d in dates]
    return line(labels, [("completed", SERIES[0], good), ("problems", CRIT, bad)]) + legend([("completed", SERIES[0]), ("problems", CRIT)])


def member_chart(hist):
    """Jobs by member in the window: completed vs problems."""
    by_user = {}
    for r in hist:
        st_ = r[2].split()[0]
        d = by_user.setdefault(r[9] if len(r) > 9 and r[9] else c.USER, [0, 0])
        if st_ == "COMPLETED":
            d[0] += 1
        elif st_ in BAD_STATES:
            d[1] += 1
    if not by_user:
        return '<p class="muted">No finished jobs in this window.</p>'
    xu = int(nice_step(max(sum(v) for v in by_user.values())) * 4)
    rows = [(u, [(v[0], SERIES[0], f"{u}: {v[0]} completed", False, None), (v[1], CRIT, f"{u}: {v[1]} problems", False, None)],
             f'<tspan class="strong">{v[0]:,}</tspan> ok · {v[1]:,} problems')
            for u, v in sorted(by_user.items(), key=lambda kv: -sum(kv[1]))]
    return (bars(rows, xu, list(range(0, xu + 1, int(nice_step(xu)))), lambda t: f"{t:,}", value_w=170)
            + legend([("completed", SERIES[0]), ("problems", CRIT)]))


def problems_table(bad, with_user=False):
    """bad = recent() rows in BAD_STATES. Newest first, at most 300."""
    head = ["JobID", "Name", "State", "Exit", "Elapsed / limit", "What to do"] + (["User"] if with_user else [])
    rows = []
    for r in bad[::-1][:300]:
        st_ = r[2].split()[0]
        rows.append([r[0], r[1], st_.replace("_", " ").lower(), r[6], f"{r[3]} / {r[4]}", HINTS.get(st_, "")] + ([r[9]] if with_user else []))
    return table(head, rows)


def pending_table(pend):
    """pend = collectors.pending_start(): [JobID, Name, Account, CPUs, Reason, Start]."""
    rows = []
    for r in pend[:200]:
        jid, name, acct, cpus, reason, start = (r + [""] * 6)[:6]
        rows.append([jid, name, acct, cpus, f"{reason} · {REASONS.get(reason, '')}".rstrip(" ·"),
                     start.replace("T", " ") if start not in ("N/A", "") else "unknown"])
    return table(["JobID", "Name", "Account", "CPUs", "Reason", "Expected start"], rows)


def efficiency_card(eff, days):
    """eff = collectors.efficiency(). Returns (card html, cpu_eff %, mem_eff %, idle core-hours)."""
    if not eff:
        return card(f"My job efficiency · last {days} d", '<p class="muted">No finished jobs of 2 min or more in this window.</p>'), None, None, 0
    by = {}
    for j in eff:
        d = by.setdefault(j["name"], {"n": 0, "core_s": 0.0, "cpu_s": 0.0, "req": 0.0, "peak": 0.0, "mem_eff": []})
        d["n"] += 1
        d["core_s"] += j["elapsed"] * j["cpus"]
        d["cpu_s"] += j["cpu"]
        d["req"] = max(d["req"], j["req"])
        d["peak"] = max(d["peak"], j["rss"])
        if j["req"] > 0:
            d["mem_eff"].append(j["rss"] / j["req"])
    top = sorted(by.items(), key=lambda kv: -kv[1]["core_s"])[:10]
    xmax = max([d["req"] for _, d in top] + [1])
    rows = []
    for name, d in top:
        ce = 100 * d["cpu_s"] / max(d["core_s"], 1)
        me = 100 * sorted(d["mem_eff"])[len(d["mem_eff"]) // 2] if d["mem_eff"] else 0
        rows.append((name[:20], [(d["peak"], SERIES[0], f"{name}: peak memory {size(d['peak'])} (max over {d['n']} jobs)", False, None),
                                 (max(d["req"] - d["peak"], 0), OTHER, f"{name}: requested {size(d['req'])}, never used {size(max(d['req'] - d['peak'], 0))}", True, None)],
                     f'mem <tspan class="strong">{me:.0f}%</tspan> · CPU <tspan class="strong">{ce:.0f}%</tspan> · {d["n"]} jobs'))
    step = nice_step(xmax / 2**30)
    ticks = [t * 2**30 for t in range(0, int(xmax / 2**30) + 1, max(int(step), 1))]
    chart = bars(rows, xmax, ticks, lambda t: f"{t / 2**30:.0f}", label_w=150, value_w=210)
    core_s = sum(j["elapsed"] * j["cpus"] for j in eff)
    cpu_eff = 100 * sum(j["cpu"] for j in eff) / max(core_s, 1)
    mem = sorted(j["rss"] / j["req"] for j in eff if j["req"] > 0)
    mem_eff = 100 * mem[len(mem) // 2] if mem else None
    idle_h = (core_s - sum(j["cpu"] for j in eff)) / 3600
    body = (chart + legend([("peak memory used", SERIES[0])]).replace("</div>", '<span><i class="hatch"></i>requested, never used</span></div>')
            + '<p class="muted">GiB. Per job name, top 10 by core-hours; mem = median peak / requested; CPU = CPU time / (elapsed × cores).</p>')
    return card(f"My job efficiency · last {days} d · {len(eff)} jobs", body), cpu_eff, mem_eff, idle_h
```

- [ ] **Step 6: Run to verify it passes**

Run: `python3 -m unittest tests.test_panels tests.test_collectors -v`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add firdash/cache.py firdash/panels.py firdash/charts.py tests/test_panels.py
git commit -m "panels.py and cache.py: shared cards, new usage/queue-wait/my-share charts, safe() and gather()

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Lab page

**Files:**
- Create: `firdash/lab.py`, `tests/test_pages.py`

**Interfaces:**
- Consumes: `cache.storage`, `cache.cached`, `collectors.fairshare/nodes/recent/usage_by_month/queue_wait`, panels listed below.
- Produces: `build_lab(days: int) -> tuple[str, str, list[str], dict[str, str]]` = (kpi html, grid html, alert strings, member colours).

- [ ] **Step 1: Write the failing test**

`tests/test_pages.py`:

```python
import unittest
from unittest import mock

from firdash import cache, collectors as c, lab, panels

ST = {"groups": ["def-a", "def-b"],
      "quotas": [{"label": "def-a /project", "used": 86e12, "quota": 100e12, "files": 1.29e6, "fquota": 2e6},
                 {"label": "def-b /project", "used": 58e12, "quota": 100e12, "files": 445e3, "fquota": 500e3},
                 {"label": "def-a /nearline", "used": 19e12, "quota": 279e12, "files": 846, "fquota": 5000},
                 {"label": "me /home", "used": 5e9, "quota": 50e9, "files": 56e3, "fquota": 500e3},
                 {"label": "me /scratch", "used": 940e9, "quota": 19e12, "files": 9236, "fquota": 1e6}],
      "breakdown": {("project", "def-a"): {"me": {"disk": 20e9, "tape": 0.0, "files": 659}, "x": {"disk": 27e12, "tape": 0.0, "files": 100}},
                    ("project", "def-b"): {"x": {"disk": 16e12, "tape": 0.0, "files": 10}},
                    ("nearline", "def-a"): {"me": {"disk": 0.0, "tape": 22e12, "files": 24}, "x": {"disk": 0.0, "tape": 65e12, "files": 99}}},
      "asof": "2026-11-25"}
FS = [["def-a_cpu", "0.5", "0.6", "0.15", "0.3"], ["def-a_gpu", "0.5", "0.1", "0.41", "4.0"]]
ND = {"Regular CPU": [150000, 14000, 7000, 171000, 1000]}
HIST = [["1", "j", "COMPLETED", "00:10:00", "01:00:00", "4G", "0:0", "def-a_cpu", "2026-11-25T01:00:00", "x"],
        ["2", "j", "TIMEOUT", "01:00:00", "01:00:00", "4G", "0:0", "def-a_cpu", "2026-11-25T02:00:00", "me"]]
USAGE = {"2026-10": {"x": {"cpu": 40000.0, "gpu": 0.0}}, "2026-11": {"x": {"cpu": 50000.0, "gpu": 24.0}, "me": {"cpu": 3600.0, "gpu": 0.0}}}
WAITS = [("2026-11-25", 1800.0), ("2026-11-24", 7200.0)]


def fake_lab(**over):
    """Patch every collector the Lab page uses; keyword overrides replace one of them (e.g. usage=lambda *a: 1/0)."""
    cache._ttl.clear()
    pats = {"storage": lambda: ST, "fairshare": lambda: FS, "nodes": lambda: ND, "recent": lambda d, a=None: HIST,
            "usage_by_month": lambda a: USAGE, "queue_wait": lambda a, d: WAITS}
    pats.update(over)
    ms = [mock.patch.object(cache, "storage", pats.pop("storage")), mock.patch.object(c, "USER", "me")]
    ms += [mock.patch.object(c, k, v) for k, v in pats.items()]
    return ms


class LabPageTests(unittest.TestCase):
    def run_lab(self, **over):
        ps = fake_lab(**over)
        for p in ps:
            p.start()
        try:
            return lab.build_lab(30)
        finally:
            for p in ps:
                p.stop()

    def test_lab_has_six_kpis_and_all_cards(self):
        kpis, grid, alerts, colors = self.run_lab()
        self.assertEqual(kpis.count('class="kpi"'), 6)
        for title in ("Space quota", "File-count quota", "/project by user (TiB)", "/nearline by user (TiB) · login-node copy of 2026-11-25",
                      "CPU core-hours per month by member", "GPU-hours per month by member", "Queue wait per week",
                      "Fairshare · cluster cores now", "Jobs finished per day · all members · last 30 d", "Jobs by member · last 30 d",
                      "Problem jobs, all members, last 30 d (1)"):
            self.assertIn(title, grid, title)
        self.assertIn("def-a · 78.2 TiB / 90.9 TiB", kpis)          # fuller /project space tile names its group
        self.assertIn("53,600", kpis)                                 # November core-hours
        self.assertIn("2.0 h", kpis)                                  # median of [1800 s, 7200 s] picks index 1 -> 7200 s
        self.assertEqual(alerts, [])
        self.assertIn("x", colors)

    def test_lab_survives_a_failing_collector(self):
        kpis, grid, alerts, colors = self.run_lab(usage_by_month=lambda a: (_ for _ in ()).throw(RuntimeError("sreport down")))
        self.assertIn("no data · RuntimeError: sreport down", grid)
        self.assertIn("Space quota", grid)
        self.assertEqual(kpis.count('class="kpi"'), 6)

    def test_lab_survives_storage_failure(self):
        kpis, grid, alerts, colors = self.run_lab(storage=lambda: (_ for _ in ()).throw(OSError("lfs hung")))
        self.assertIn("no data · OSError: lfs hung", grid)
        self.assertIn("CPU core-hours per month by member", grid)

    def test_alert_at_95_percent(self):
        st = dict(ST, quotas=[dict(ST["quotas"][0], used=96e12)] + ST["quotas"][1:])
        kpis, grid, alerts, colors = self.run_lab(storage=lambda: st)
        self.assertEqual(alerts, ["def-a /project space 96%"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_pages -v`
Expected: `ImportError: cannot import name 'lab'`.

- [ ] **Step 3: Create `firdash/lab.py`**

```python
"""Lab page: the whole picture of every group the user belongs to. Storage pressure and compute use, equally."""
from . import collectors as c
from .cache import CACHE_TTL, STORAGE_TTL, cached, storage
from .charts import GOOD, OTHER, SERIES, count, legend, size
from .panels import (BAD_STATES, card, extend_colors, fairshare_chart, fold, gather, hours_text, jobs_per_day, kpi,
                     member_chart, month_label, nodes_chart, ok, pct, problems_table, quota_chart, queue_wait_chart,
                     safe, usage_chart, usage_month_chart, user_colors, val)

EMPTY_ST = {"groups": [], "quotas": [], "breakdown": {}, "asof": None}


def group_quotas(st):
    """Group rows only (not the user's /home and /scratch), /project before /nearline."""
    order = {"/project": 0, "/nearline": 1}
    rows = [q for q in st["quotas"] if not q["label"].startswith(c.USER + " ")]
    return sorted(rows, key=lambda q: (order.get(q["label"].split()[1], 9), q["label"]))


def quota_kpi(icon, title, q, key, qkey, fmt):
    p = pct(q, key, qkey)
    return kpi("⚠" if p >= 95 else icon, title, f"{q['label'].split()[0]} · {fmt(q[key])} / {fmt(q[qkey])}",
               f"{p:.0f}%", "crit" if p >= 95 else "", p)


def median(xs):
    return sorted(xs)[min(len(xs) - 1, int(0.5 * len(xs)))] if xs else 0.0


def build_lab(days):
    """Returns (kpi_html, grid_html, alerts, colors)."""
    d = gather({"st": storage,
                "fs": lambda: cached("fairshare", CACHE_TTL, c.fairshare),
                "nd": lambda: cached("nodes", CACHE_TTL, c.nodes)})
    accounts = [r[0] for r in (ok(d, "fs") or [])]
    d.update(gather({"hist": lambda: cached(("lab-hist", days), CACHE_TTL, c.recent, days, accounts),
                     "usage": lambda: cached("usage", STORAGE_TTL, c.usage_by_month, accounts),
                     "waits": lambda: cached(("wait", days), CACHE_TTL, c.queue_wait, accounts, days)}))
    st = ok(d, "st") or EMPTY_ST
    gq = group_quotas(st)
    hist = ok(d, "hist") or []
    usage = ok(d, "usage") or {}
    waits = ok(d, "waits") or []
    bad = [r for r in hist if r[2].split()[0] in BAD_STATES]

    colors = user_colors(st["breakdown"])
    colors = extend_colors(colors, sorted({r[9] for r in hist if len(r) > 9 and r[9]}))
    colors = extend_colors(colors, sorted({u for m in usage.values() for u in m}))
    users = list(colors.items()) + [("other", OTHER)]

    # KPI row: /project space, /project files, nearline total, this month's CPU and GPU hours, median queue wait
    proj = [q for q in gq if q["label"].endswith(" /project")]
    k = []
    q = max(proj, key=lambda q: pct(q), default=None)
    k.append(quota_kpi("▤", "/project space", q, "used", "quota", size) if q else kpi("▤", "/project space", "no data", "—"))
    q = max(proj, key=lambda q: pct(q, "files", "fquota"), default=None)
    k.append(quota_kpi("☰", "/project files", q, "files", "fquota", count) if q else kpi("☰", "/project files", "no data", "—"))
    near = sum(v["disk"] + v["tape"] for (fs_, _), us in st["breakdown"].items() if fs_ == "nearline" for v in us.values())
    k.append(kpi("◫", "/nearline", "disk + tape · all groups", size(near) if st["breakdown"] else "—"))
    month = max(usage) if usage else None
    mlabel = month_label(month) if month else "no data"
    k.append(kpi("▦", "CPU core-hours", mlabel, f"{sum(v['cpu'] for v in usage[month].values()):,.0f}" if month else "—"))
    k.append(kpi("▣", "GPU-hours", mlabel, f"{sum(v['gpu'] for v in usage[month].values()):,.0f}" if month else "—"))
    k.append(kpi("◷", "Queue wait", f"median · last {days} d · {len(waits):,} jobs", hours_text(median([w for _, w in waits]) / 3600) if waits else "—"))

    alerts = [f"{q['label']} {name} {p:.0f}%" for q in gq
              for name, p in (("space", pct(q)), ("files", pct(q, "files", "fquota"))) if p >= 95]

    near_title = "/nearline by user (TiB)" + (f" · login-node copy of {st['asof']}" if st.get("asof") else "")
    grid = "".join([
        safe("Space quota", lambda: card("Space quota", quota_chart(group_quotas(val(d, "st")), "used", "quota", size))),
        safe("File-count quota", lambda: card("File-count quota", quota_chart(group_quotas(val(d, "st")), "files", "fquota", count))),
        safe("/project by user (TiB)", lambda: card("/project by user (TiB)",
             usage_chart(val(d, "st")["breakdown"], "project", val(d, "st")["groups"], colors, False) + legend(users))),
        safe(near_title, lambda: card(near_title,
             usage_chart(val(d, "st")["breakdown"], "nearline", val(d, "st")["groups"], colors, True) + legend(users, True))),
        safe("CPU core-hours per month by member", lambda: card("CPU core-hours per month by member",
             usage_month_chart(val(d, "usage"), "cpu", colors))),
        safe("Queue wait per week", lambda: card(f"Queue wait per week · hours · last {days} d", queue_wait_chart(val(d, "waits"), days))),
        safe("GPU-hours per month by member", lambda: card("GPU-hours per month by member",
             usage_month_chart(val(d, "usage"), "gpu", colors))),
        safe("Fairshare · cluster cores now", lambda: card("Fairshare · cluster cores now",
             fairshare_chart(val(d, "fs")) + '<div style="height:14px"></div>' + nodes_chart(val(d, "nd"))
             + legend([("busy", SERIES[0]), ("idle", GOOD), ("down/drained", OTHER)]))),
        safe("Jobs finished per day", lambda: card(f"Jobs finished per day · all members · last {days} d", jobs_per_day(val(d, "hist"), days))),
        safe("Jobs by member", lambda: card(f"Jobs by member · last {days} d", member_chart(val(d, "hist")))),
        safe("Problem jobs", lambda: fold(f"Problem jobs, all members, last {days} d ({len(bad)})", problems_table(bad, with_user=True))),
    ])
    return "".join(k), grid, alerts, colors
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_pages -v`
Expected: `OK` (4 tests). If `test_lab_has_six_kpis_and_all_cards` fails on the "def-a · …" string, print `kpis` and copy the exact rendering of `size(86e12)` / `size(100e12)` into the assertion (the plan's numbers were computed as 86e12 / 2**40 = 78.2 TiB and 100e12 / 2**40 = 90.9 TiB).

- [ ] **Step 5: Commit**

```bash
git add firdash/lab.py tests/test_pages.py
git commit -m "Lab page: storage, compute use, queue wait and jobs for all groups

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Me page

**Files:**
- Create: `firdash/me.py`
- Modify: `tests/test_pages.py`

**Interfaces:**
- Consumes: `cache.storage`, `collectors.jobs/recent/pending_start/efficiency`, panels.
- Produces: `build_me(days) -> (kpi_html, grid_html, alerts, colors)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_pages.py` before `if __name__`; add `me` to the import line: `from firdash import cache, collectors as c, lab, me, panels`)

```python
JB = [["10", "run1", "RUNNING", "00:05:00", "01:00:00", "1", "4", "4G", "def-a_cpu", "fc1"],
      ["11", "pend1", "PENDING", "00:00:00", "01:00:00", "1", "4", "4G", "def-a_cpu", "(Priority)"]]
PEND = [["11", "pend1", "def-a_cpu", "4", "Priority", "2026-11-26T09:00:00"]]
MYHIST = [["2", "j", "TIMEOUT", "01:00:00", "01:00:00", "4G", "0:0", "def-a_cpu", "2026-11-25T02:00:00", "me"]]
EFF = [{"id": "2", "name": "j", "state": "TIMEOUT", "elapsed": 3600.0, "cpu": 1800.0, "cpus": 1, "req": 4 * 2**30, "rss": 2**30, "account": "def-a_cpu"}]


def fake_me(**over):
    cache._ttl.clear()
    pats = {"storage": lambda: ST, "jobs": lambda: JB, "recent": lambda d, a=None: MYHIST,
            "pending_start": lambda: PEND, "efficiency": lambda d: EFF}
    pats.update(over)
    ms = [mock.patch.object(cache, "storage", pats.pop("storage")), mock.patch.object(c, "USER", "me")]
    ms += [mock.patch.object(c, k, v) for k, v in pats.items()]
    return ms


class MePageTests(unittest.TestCase):
    def run_me(self, **over):
        ps = fake_me(**over)
        for p in ps:
            p.start()
        try:
            return me.build_me(7)
        finally:
            for p in ps:
                p.stop()

    def test_me_kpis_and_cards(self):
        kpis, grid, alerts, colors = self.run_me()
        self.assertEqual(kpis.count('class="kpi"'), 5)
        self.assertIn("files unused for 60 days are purged", kpis)
        self.assertIn("1 · 1", kpis)                                   # running · pending
        for title in ("My /home and /scratch", "My share of group storage", "My jobs finished per day · last 7 d",
                      "My job efficiency · last 7 d · 1 jobs", "Pending jobs (1)", "Problem jobs, last 7 d (1)"):
            self.assertIn(title, grid, title)
        self.assertIn("raise --time", grid)
        self.assertIn("<details open>", grid)
        self.assertNotIn(">x<", grid)                                  # no other member's name

    def test_me_survives_failures(self):
        kpis, grid, alerts, colors = self.run_me(efficiency=lambda d: (_ for _ in ()).throw(RuntimeError("sacct slow")))
        self.assertIn("no data · RuntimeError: sacct slow", grid)
        self.assertEqual(kpis.count('class="kpi"'), 5)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_pages -v`
Expected: `ImportError: cannot import name 'me'`.

- [ ] **Step 3: Create `firdash/me.py`**

```python
"""Me page: the signed-in member's own quotas, share of group storage, and jobs. No other member appears here."""
from . import collectors as c
from .cache import CACHE_TTL, STORAGE_TTL, cached, storage
from .charts import count, size
from .panels import (BAD_STATES, card, efficiency_card, fold, gather, jobs_per_day, kpi, my_share_chart, ok,
                     pending_table, problems_table, quota_chart, quota_tile, safe, val)

EMPTY_ST = {"groups": [], "quotas": [], "breakdown": {}, "asof": None}


def my_quotas(st):
    return [q for q in st["quotas"] if q["label"].startswith(c.USER + " ")]


def build_me(days):
    """Returns (kpi_html, grid_html, alerts, colors)."""
    d = gather({"st": storage,
                "jb": lambda: cached("jobs", CACHE_TTL, c.jobs),
                "hist": lambda: cached(("my-hist", days), CACHE_TTL, c.recent, days),
                "pend": lambda: cached("pending", CACHE_TTL, c.pending_start),
                "eff": lambda: cached(("eff", days), STORAGE_TTL, c.efficiency, days)})
    st = ok(d, "st") or EMPTY_ST
    mine = my_quotas(st)
    jb = ok(d, "jb") or []
    hist = ok(d, "hist") or []
    pend = ok(d, "pend") or []
    running = [r for r in jb if r[2] == "RUNNING"]
    pending = [r for r in jb if r[2] == "PENDING"]
    bad = [r for r in hist if r[2].split()[0] in BAD_STATES]

    my_proj = sum(us.get(c.USER, {"disk": 0.0, "tape": 0.0})["disk"] + us.get(c.USER, {"disk": 0.0, "tape": 0.0})["tape"]
                  for (fs_, _), us in st["breakdown"].items() if fs_ == "project")
    proj_quota = sum(q["quota"] for q in st["quotas"] if q["label"].endswith(" /project") and not q["label"].startswith(c.USER + " "))
    home = next((q for q in mine if q["label"].endswith(" /home")), None)
    scratch = next((q for q in mine if q["label"].endswith(" /scratch")), None)
    k = [quota_tile(home, "/home") if home else kpi("▤", "/home", "no data", "—"),
         quota_tile(scratch, "/scratch", "files unused for 60 days are purged") if scratch else kpi("▤", "/scratch", "no data", "—"),
         kpi("▤", "My /project", f"{100 * my_proj / max(proj_quota, 1):.2f}% of the group quota" if proj_quota else "no data",
             size(my_proj) if st["breakdown"] else "—"),
         kpi("▶", "My jobs", "running · pending", f"{len(running)} · {len(pending)}", "good" if running else ""),
         kpi("✗", "My problem jobs", f"last {days} d", len(bad), "crit" if bad else "")]
    alerts = [f"{q['label']} {name} {p:.0f}%" for q in mine
              for name, p in (("space", 100 * q["used"] / max(q["quota"], 1)), ("files", 100 * q["files"] / max(q["fquota"], 1))) if p >= 95]

    grid = "".join([
        safe("My /home and /scratch", lambda: card("My /home and /scratch",
             '<div class="flab">space</div>' + quota_chart(my_quotas(val(d, "st")), "used", "quota", size)
             + '<div class="flab">files</div>' + quota_chart(my_quotas(val(d, "st")), "files", "fquota", count))),
        safe("My share of group storage", lambda: card("My share of group storage",
             my_share_chart(val(d, "st")["breakdown"], val(d, "st")["quotas"]))),
        safe("My jobs finished per day", lambda: card(f"My jobs finished per day · last {days} d", jobs_per_day(val(d, "hist"), days))),
        safe("My job efficiency", lambda: efficiency_card(val(d, "eff"), days)[0]),
        safe("Pending jobs", lambda: fold(f"Pending jobs ({len(val(d, 'pend'))})", pending_table(val(d, "pend")), open_=True)),
        safe("Problem jobs", lambda: fold(f"Problem jobs, last {days} d ({len(bad)})", problems_table(bad), open_=True)),
    ])
    return "".join(k), grid, alerts, {}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_pages -v`
Expected: `OK` (6 tests).

- [ ] **Step 5: Commit**

```bash
git add firdash/me.py tests/test_pages.py
git commit -m "Me page: own quotas, share of group storage, own jobs with hints

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: render.py shell, server flags, README, manual check on Fir

**Files:**
- Rewrite: `firdash/render.py`
- Modify: `firdash/server.py:80-95` (`--page`, `--days`), `firdash/server.py:149`
- Modify: `README.md` (Pages paragraph, "Shows" list, Usage block)
- Modify: `tests/test_pages.py` (add shell tests)

**Interfaces:**
- Consumes: `lab.build_lab`, `me.build_me`, `panels.esc` (via charts), `charts.hatch_defs/OTHER/SERIES/CRIT/GOOD`.
- Produces: `render.build(params: dict) -> str` (full HTML). `params` keys: `page`, `days`, `static`. `server.page(query)` unchanged signature.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_pages.py`; extend the import line to include `render`)

```python
class ShellTests(unittest.TestCase):
    def test_build_defaults_and_tabs(self):
        ps = fake_lab()
        for p in ps:
            p.start()
        try:
            html = render.build({})
            html7 = render.build({"page": "lab", "days": "7"})
            bad = render.build({"page": "nonsense", "days": "999"})
        finally:
            for p in ps:
                p.stop()
        self.assertIn("<title>Research Computing Dashboard · Lab</title>", html)
        self.assertIn('href="?page=lab&amp;days=30"', html)
        self.assertIn('href="?page=me&amp;days=7"', html)
        self.assertIn("last 30 d", html)
        self.assertIn("last 7 d", html7)
        self.assertIn("last 30 d", bad)                   # unknown page/days fall back to Lab / 30
        self.assertNotIn("Boards", html)
        self.assertNotIn("<script>", html)

    def test_static_me_page(self):
        ps = fake_me()
        for p in ps:
            p.start()
        try:
            html = render.build({"page": "me", "static": "1"})
        finally:
            for p in ps:
                p.stop()
        self.assertIn("<title>Research Computing Dashboard · Me</title>", html)
        self.assertIn("static snapshot", html)
        self.assertNotIn("<select", html)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_pages -v`
Expected: the two ShellTests fail (`render.build` still expects the old params: KeyError/AttributeError on removed collectors such as `c.filesystems`).

- [ ] **Step 3: Rewrite `firdash/render.py`**

```python
"""Page shell: top bar with the Lab / Me tabs and the days select, sidebar, CSS. Content comes from lab.py / me.py."""
import base64
import socket
import time
from pathlib import Path

from . import collectors as c
from . import lab, me
from .cache import CACHE_TTL, STORAGE_TTL
from .charts import CRIT, GOOD, OTHER, SERIES, esc, hatch_defs

PAGES = [("lab", "Lab"), ("me", "Me")]
DAYS = (7, 30, 90)
DEFAULT_DAYS = {"lab": 30, "me": 7}
LOGO = Path(__file__).resolve().parent.parent / "docs" / "logo.png"
ABOUT = ("Lab: storage, compute use and jobs of every group you belong to. Me: your own quotas and jobs. "
         "Read-only: it only reads the cluster's own reports under your account.")


def build(params):
    page = params.get("page") if params.get("page") in DEFAULT_DAYS else "lab"
    days = int(params["days"]) if str(params.get("days", "")).isdigit() and int(params["days"]) in DAYS else DEFAULT_DAYS[page]
    kpis, grid, alerts, colors = (me.build_me if page == "me" else lab.build_lab)(days)
    return shell(page, days, kpis, grid, alerts, colors, bool(params.get("static")))


def logo():
    if not LOGO.is_file():
        return ""
    return f'<img class="logo" src="data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode()}" alt="logo">'


def tabs(page, days):
    return "".join(f'<a class="tab{" on" if p == page else ""}" href="?page={p}&amp;days={days if p == page else DEFAULT_DAYS[p]}">{label}</a>'
                   for p, label in PAGES)


def controls(page, days, static):
    if static:
        return f'<span class="muted">last {days} d · static snapshot</span>'
    opts = "".join(f'<option value="{d}"{" selected" if d == days else ""}>last {d} d</option>' for d in DAYS)
    return (f'<form method="get" class="days"><input type="hidden" name="page" value="{page}">'
            f'<select name="days" onchange="this.form.submit()">{opts}</select></form>')


def shell(page, days, kpis, grid, alerts, colors, static):
    title = dict(PAGES)[page]
    alert_html = "".join(f'<span class="alert">⚠ {esc(a)}</span>' for a in alerts)
    foot = ("static snapshot: rerun to refresh" if static else f"storage every {STORAGE_TTL // 60} min · jobs every {CACHE_TTL} s")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Research Computing Dashboard · {title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{CSS}</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs>{hatch_defs(list(colors.values()) + [OTHER])}</defs></svg>
<aside>
<div class="brand">▲ DRAC <small>{esc(c.USER)}</small></div>
<nav class="tabs">{tabs(page, days)}</nav>
<div class="about"><p>{esc(ABOUT)}</p></div>
<div class="foot">{time.strftime('%Y-%m-%d %H:%M')} · {socket.gethostname().split('.')[0]}<br>{foot}</div>
</aside>
<main>
<div class="top">{logo()}<h1>Research Computing Dashboard<span class="pg">{title} · last {days} d</span></h1>
<div class="ctl"><nav class="tabs">{tabs(page, days)}</nav>{controls(page, days, static)}</div></div>
<div class="alerts">{alert_html}</div>
<div class="kpis">{kpis}</div>
<div class="grid">{grid}</div>
</main>
</body></html>"""


CSS = f"""
:root{{--page:#0b1424;--side:#111d33;--surface:#13213a;--head:#1b3358;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f97a8;
--grid:#22324f;--track:#1c2c48;--border:rgba(255,255,255,.10);--accent:{SERIES[0]}}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--page);color:var(--ink);display:flex;min-height:100vh}}
aside{{width:260px;flex-shrink:0;background:var(--side);padding:18px 16px;display:flex;flex-direction:column;gap:10px;position:sticky;top:0;height:100vh;overflow:auto}}
.brand{{font-size:28px;font-weight:700}} .brand small{{font-size:16px;color:var(--muted);font-weight:400;margin-left:6px}}
.tabs{{display:flex;gap:6px}} aside .tabs{{flex-direction:column}}
.tab{{display:block;padding:9px 14px;border-radius:8px;color:var(--ink2);text-decoration:none;font-size:16px;font-weight:600}}
.tab:hover{{background:var(--track)}} .tab.on{{background:var(--head);color:var(--ink)}}
.about{{margin-top:auto;padding-top:18px;border-top:1px solid var(--border)}} .about p{{font-size:13px;color:var(--ink2);line-height:1.5;margin:0}}
.foot{{font-size:12px;color:var(--muted);line-height:1.5}}
main{{flex:1;padding:22px 28px;min-width:0}}
.top{{position:relative;display:flex;align-items:center;justify-content:space-between;gap:16px;border:1px solid var(--border);border-radius:8px;padding:12px 18px 12px 140px;margin-bottom:12px;min-height:96px}}
.logo{{position:absolute;left:0;top:0;bottom:0;height:100%;width:auto;border-radius:8px 0 0 8px}}
.top h1{{flex:1;font-size:30px;line-height:1.15;margin:0}} .top h1 .pg{{display:block;font-size:14px;font-weight:500;color:var(--muted);margin-top:4px}}
.ctl{{display:flex;align-items:center;gap:12px}} .top .tabs{{display:none}}
select{{background:var(--page);color:var(--ink);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:14px}}
.alerts{{min-height:0;margin-bottom:8px}}
.alert{{display:inline-block;background:{CRIT};color:#fff;border-radius:12px;padding:3px 10px;margin:0 6px 6px 0;font-size:12px;font-weight:600}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:16px;margin-bottom:18px}}
.kpi-h{{background:var(--head);border-radius:6px;padding:10px 14px;font-size:14px;margin-bottom:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.kpi-b{{border:1px solid var(--border);border-radius:6px;padding:10px 14px;background:var(--surface)}}
.kpi-b span{{display:block;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.kpi-b b{{display:block;font-size:30px;font-weight:600;margin-top:4px;white-space:nowrap}}
.kpi-b.crit b{{color:#e66767}} .kpi-b.good b{{color:{GOOD}}} .muted{{color:var(--muted);font-size:13px}}
.meter{{position:relative;height:6px;background:var(--track);border-radius:3px;margin-top:8px}}
.meter div{{height:100%;border-radius:3px}} .meter i{{position:absolute;top:-2px;width:1px;height:10px;background:{CRIT}}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(480px,100%),1fr));gap:16px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}}
.card h3{{margin:0 0 10px;font-size:15px}} .flab{{font-size:12px;color:var(--muted);margin:12px 0 6px}}
svg text{{font-size:12px;fill:var(--ink2)}} svg .lab{{fill:var(--ink)}} svg .strong{{font-weight:600;fill:var(--ink)}}
svg .tick{{font-size:11px;fill:var(--muted);font-variant-numeric:tabular-nums}} svg .sub{{font-size:10.5px}}
svg .grid{{stroke:var(--grid)}} svg .axis{{stroke:var(--muted)}} svg .ref{{stroke:{CRIT};stroke-width:1.2;stroke-dasharray:3 3}}
svg .track{{fill:var(--track)}} svg rect:hover{{opacity:.85}} svg .dot{{stroke:var(--surface);stroke-width:2}}
svg .hover{{fill:transparent}} svg .hover:hover{{fill:rgba(255,255,255,.05)}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2);margin-top:6px}}
.legend i{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}}
.legend .hatch{{background:repeating-linear-gradient(45deg,var(--muted) 0 2px,transparent 2px 5px);border:1px solid var(--muted)}}
details{{margin:4px 0;overflow-x:auto}} summary{{cursor:pointer;font-size:14px;color:var(--ink2);padding:6px 0}}
table{{border-collapse:collapse;font-size:12px;width:100%;margin:6px 0 10px;font-variant-numeric:tabular-nums}}
td,th{{border-bottom:1px solid var(--grid);padding:4px 6px;text-align:left}} th{{color:var(--muted);font-weight:500}}
@media (max-width:820px){{
 body{{display:block}} aside{{display:none}}
 main{{padding:12px}} .top{{flex-direction:column;align-items:stretch;padding:12px;min-height:0}} .logo{{display:none}}
 .top h1{{font-size:22px}} .top .tabs{{display:flex}} .ctl{{justify-content:space-between}}
 .kpis{{grid-template-columns:repeat(2,1fr);gap:10px}} .kpi-b b{{font-size:24px}} .card{{padding:12px}}
}}
@media (min-width:600px) and (max-width:820px){{ .kpis{{grid-template-columns:repeat(3,1fr)}} }}
"""
```

- [ ] **Step 4: server.py**

Replace the two argparse lines and the `--html` block (`server.py:84-95`) with:

```python
    ap.add_argument("--days", type=int, default=None, help="history window when the URL has none (7, 30 or 90)")
    ap.add_argument("--page", choices=("lab", "me"), default="lab", help="page for --html")
    ap.add_argument("--html", metavar="FILE", help="write one page as a static snapshot and exit ('-' = stdout)")
    a = ap.parse_args()

    if a.html:
        html = build({"page": a.page, "days": str(a.days or ""), "static": "1"})
        if a.html == "-":
            sys.stdout.buffer.write(html.encode("ascii", "xmlcharrefreplace"))
        else:
            Path(a.html).write_text(html, encoding="utf-8")
            print(f"wrote {a.html}")
        return
```

and `server.py:149` with:

```python
            q = urlsplit(self.path).query
            self.send(200, page(q if q or not a.days else f"days={a.days}"))
```

`page()` (server.py:72-77) and `CACHE_TTL` import stay as they are; `from .render import CACHE_TTL, build` keeps working because render.py re-exports `CACHE_TTL` from cache.

- [ ] **Step 5: Run all tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: `OK` (all tests from Tasks 1–6). Then `python3 -c "import firdash.server"` prints nothing. `wc -l firdash/render.py` is under 250.

- [ ] **Step 6: README**

Replace the paragraph starting `Pages (sidebar):` with:

```
Pages: **Lab** (every group you belong to: quotas, who uses the space, core-hours and GPU-hours per month, queue
wait, fairshare, cluster load, jobs by member) and **Me** (your /home and /scratch, your share of group storage,
your jobs, efficiency, pending and problem jobs with hints). One control: the "last N days" window (7, 30, 90;
Lab defaults to 30, Me to 7). URL: `?page=lab|me&days=N`.
```

In the "Shows:" list delete the bullets for "Whole-filesystem fill", "Usage trend and 'full in N days' forecast", "Array jobs", "Lab queue / Lab jobs", "Maintenance and reservations" (whatever of these exist) and add:

```
- CPU core-hours and GPU-hours per member per calendar month (sreport), queue wait per week (median and 90th percentile)
```

In the Usage block replace the `--html report.html` line with:

```bash
bin/firdash --html lab.html              # static snapshot of the Lab page, no server
bin/firdash --html me.html --page me     # same for the Me page
```

Add `sreport` to the "Data comes from" sentence.

- [ ] **Step 7: Manual check on Fir**

```bash
rsync -a --exclude .git --exclude __pycache__ ./ fir:~/firdash-dev/
ssh fir 'cd ~/firdash-dev && python3 -m unittest discover -s tests && python3 bin/firdash --html /tmp/lab.html && python3 bin/firdash --html /tmp/me.html --page me && grep -c "no data" /tmp/lab.html /tmp/me.html'
```

Expected: tests `OK` on Python 3.11; both files written; `no data` count 0 on both (if not 0, open the file and read the reason in the card). Then screenshot both pages at 1500 px and 400 px widths (Chrome headless, as done earlier in this project) and compare with the spec's panel lists. Fix anything that is off, re-run the tests, and only then commit.

- [ ] **Step 8: Commit, push, update the shared copy**

```bash
git add firdash/render.py firdash/server.py README.md tests/test_pages.py
git commit -m "Two pages: Lab and Me replace the Boards layout

render.py is the shell (tabs, days select, CSS); content comes from lab.py and me.py through
panels.py; one failing collector shows a 'no data' card instead of a blank page.
Previous layout: tag v1-thuan-boards.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push origin main
ssh fir 'cd /project/def-kanj241/A01_GAIG_data_repo/C01_pipelines/fir-dashboard && git pull --ff-only && rm -rf ~/firdash-dev'
```

Then tell Fang to stop any running JupyterHub server once and open https://fir.gaig.ca.

---

## Self-review

**Spec coverage.** Navigation/chrome → Task 6. Lab KPI row (6 tiles, amber/red) → Task 4 via `quota_kpi` + `status_fill`. Lab storage, compute (sreport, queue wait, fairshare+cores), jobs (per day, by member, problems table) → Tasks 1, 2, 4. Me KPI row (5 tiles incl. scratch purge note), storage, jobs, two expanded tables with hints → Task 5. Data section (`usage_by_month`, `queue_wait`, refresh TTLs) → Tasks 1–3. Code layout → Tasks 3–6 (plus `cache.py`, a small addition to the spec's list so `lab.py`/`me.py` do not import `render.py`). Error handling → `safe`/`gather` (Task 3) used in Tasks 4–5 and tested. Testing → fixtures and tests in every task; manual step in Task 6. Rollback → tag, noted in the commit message.

**Placeholders.** None; every code step is complete.

**Type consistency.** `build_lab`/`build_me` both return `(str, str, list[str], dict)`; `render.build` consumes exactly that. `safe(title, thunk)` is the only signature; `gather` returns a dict read through `val`/`ok`. `user_breakdown` rows carry `files` (Task 2) and `my_share_chart` reads it (Task 3). `columns(..., max_bw=90)` matches the Task 3 signature change. `recent()` row indices (End = 8, User = 9) are used identically in `jobs_per_day`, `member_chart`, `problems_table`.
