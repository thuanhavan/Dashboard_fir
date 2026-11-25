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

    def test_cached_does_not_store_exceptions(self):
        from firdash import cache
        cache._ttl.clear()
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
