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
        self.assertIn("other: 5", html)   # b has no colour, so it is folded into "other"
        self.assertIn("other: 0", html)   # ... which still has a November value
        self.assertNotIn("b:", html)
        self.assertEqual(p.usage_month_chart({}, "gpu", {}), '<p class="muted">No usage recorded in this period.</p>')

    def test_queue_wait_chart_buckets_weeks(self):
        import datetime as dt
        today = dt.date.today()
        waits = [((today - dt.timedelta(days=d)).isoformat(), 3600.0 * (1 + d)) for d in range(0, 14)]
        html = p.queue_wait_chart(waits, 14)
        self.assertIn("median", html)
        self.assertIn("90th", html)
        self.assertEqual(html.count("<polyline"), 2)
        self.assertIn("median: 4.0", html)    # newest week waits 1..7 h, upper nearest-rank median
        self.assertIn("median: 11.0", html)   # older week waits 8..14 h
        self.assertEqual(html.count("wk to"), 4)  # 2 labels, 2 tooltips

    def test_queue_wait_chart_empty_week_shows_zero(self):
        import datetime as dt
        today = dt.date.today()
        waits = [((today - dt.timedelta(days=d)).isoformat(), 3600.0) for d in range(0, 3)]
        html = p.queue_wait_chart(waits, 14)
        self.assertIn("median: 0.0", html)
        self.assertIn("a week with no started jobs shows 0", html)

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
        self.assertIn("me: 1.0 TiB of 10.0 TiB (10.00%)", html)
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


class WindowTests(unittest.TestCase):
    def test_in_window_drops_old_and_future(self):
        t = p.dt.date.today()
        w = [((t - p.dt.timedelta(days=n)).isoformat(), 1.0) for n in (-1, 0, 29, 30)]
        self.assertEqual([x[0] for x in p.in_window(w, 30)], [w[1][0], w[2][0]])


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
        self.assertEqual(got["b"], SERIES[0])                 # first unused colour
        full = p.extend_colors({f"u{i}": col for i, col in enumerate(SERIES)}, ["new"])
        self.assertNotIn("new", full)                          # capped at len(SERIES)

    def test_user_colors_caps_at_series_length(self):
        bd = {("project", "def-a"): {f"u{i}": {"disk": float(i + 1), "tape": 0.0, "files": 1} for i in range(11)}}
        bd[("project", "def-a")][p.c.USER] = {"disk": 0.5, "tape": 0.0, "files": 1}
        got = p.user_colors(bd)
        self.assertEqual(len(got), 8)
        self.assertEqual(list(got)[0], p.c.USER)
        self.assertEqual(len(set(got.values())), 8)
        self.assertEqual(list(got)[1], "u10")                   # then by total storage

    def test_jupyter_hint(self):
        row = ["1", "spawner-jupyterhub", "TIMEOUT", "01:00:00", "01:00:00", "4G", "0:0", "a", "2026-11-25T01:00:00", "me"]
        self.assertIn("JupyterHub session reached its time limit", p.problems_table([row]))


class CacheTests(unittest.TestCase):
    def test_cached_calls_once_within_ttl(self):
        from firdash import cache
        n = []
        f = lambda: n.append(1) or len(n)
        cache._ttl.clear()
        self.addCleanup(cache._ttl.clear)
        self.assertEqual(cache.cached("k", 60, f), 1)
        self.assertEqual(cache.cached("k", 60, f), 1)
        self.assertEqual(len(n), 1)

    def test_cached_is_single_flight(self):
        import threading
        import time
        from firdash import cache
        cache._ttl.clear()
        self.addCleanup(cache._ttl.clear)
        calls = []

        def slow():
            calls.append(1)
            time.sleep(0.2)
            return "v"
        got = []
        ts = [threading.Thread(target=lambda: got.append(cache.cached("cold", 60, slow))) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(got, ["v", "v"])

    def test_cached_drops_expired_entries_on_write(self):
        from firdash import cache
        cache._ttl.clear()
        self.addCleanup(cache._ttl.clear)
        cache._ttl["old"] = (0.0, "stale")
        cache.cached("new", 60, lambda: 1)
        self.assertNotIn("old", cache._ttl)

    def test_cached_does_not_store_exceptions(self):
        from firdash import cache
        cache._ttl.clear()
        self.addCleanup(cache._ttl.clear)
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("first call fails")
            return "ok"

        with self.assertRaises(RuntimeError):
            cache.cached("flaky", 60, flaky)
        self.assertEqual(cache.cached("flaky", 60, flaky), "ok")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
