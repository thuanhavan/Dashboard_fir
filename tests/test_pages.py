import datetime as dt
import unittest
from unittest import mock

from firdash import cache, collectors as c, lab, me, panels, render

ST = {"groups": ["def-a", "def-b"],
      "quotas": [{"label": "def-a /project", "used": 86e12, "quota": 100e12, "files": 1.29e6, "fquota": 2e6},
                 {"label": "def-b /project", "used": 58e12, "quota": 100e12, "files": 445e3, "fquota": 500e3},
                 {"label": "def-a /nearline", "used": 19e12, "quota": 279e12, "files": 846, "fquota": 5000},
                 {"label": "me /home", "used": 5e9, "quota": 50e9, "files": 56e3, "fquota": 500e3},
                 {"label": "me /scratch", "used": 940e9, "quota": 19e12, "files": 9236, "fquota": 1e6}],
      "breakdown": {("project", "def-a"): {"me": {"disk": 20e9, "tape": 0.0, "files": 659}, "otheruser": {"disk": 27e12, "tape": 0.0, "files": 100}},
                    ("project", "def-b"): {"otheruser": {"disk": 16e12, "tape": 0.0, "files": 10}},
                    ("nearline", "def-a"): {"me": {"disk": 0.0, "tape": 22e12, "files": 24}, "otheruser": {"disk": 0.0, "tape": 65e12, "files": 99}}},
      "asof": "2026-11-25"}
T = dt.date.today()
FS = [["def-a_cpu", "0.5", "0.6", "0.15", "0.3"], ["def-a_gpu", "0.5", "0.1", "0.41", "4.0"]]
ND = {"Regular CPU": [150000, 14000, 7000, 171000, 1000]}
HIST = [["1", "j", "COMPLETED", "00:10:00", "01:00:00", "4G", "0:0", "def-a_cpu", f"{T.isoformat()}T01:00:00", "otheruser"],
        ["2", "j", "TIMEOUT", "01:00:00", "01:00:00", "4G", "0:0", "def-a_cpu", f"{T.isoformat()}T02:00:00", "me"]]
USAGE = {"2026-10": {"otheruser": {"cpu": 40000.0, "gpu": 0.0}}, "2026-11": {"otheruser": {"cpu": 50000.0, "gpu": 24.0}, "me": {"cpu": 3600.0, "gpu": 0.0}}}
WAITS = [(T.isoformat(), 1800.0), ((T - dt.timedelta(days=1)).isoformat(), 7200.0)]


def boom(exc):
    def f(*a, **k):
        raise exc
    return f


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
        self.addCleanup(cache._ttl.clear)
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
        self.assertIn("otheruser", colors)

    def test_lab_survives_a_failing_collector(self):
        kpis, grid, alerts, colors = self.run_lab(usage_by_month=boom(RuntimeError("sreport down")))
        self.assertIn("no data · RuntimeError: sreport down", grid)
        self.assertIn("Space quota", grid)
        self.assertEqual(kpis.count('class="kpi"'), 6)
        self.assertEqual(kpis.count("<b>—</b>"), 2)               # CPU and GPU tiles

    def test_lab_survives_fairshare_failure(self):
        calls = []
        rec = lambda *a: calls.append(a) or []
        kpis, grid, alerts, colors = self.run_lab(fairshare=boom(RuntimeError("sshare down")), recent=rec,
                                                  usage_by_month=rec, queue_wait=rec)
        self.assertEqual(calls, [])
        self.assertGreaterEqual(grid.count("no data · RuntimeError: no Slurm accounts"), 3)
        self.assertEqual(kpis.count("<b>—</b>"), 3)               # CPU, GPU, queue wait

    def test_lab_survives_storage_failure(self):
        kpis, grid, alerts, colors = self.run_lab(storage=boom(OSError("lfs hung")))
        self.assertIn("no data · OSError: lfs hung", grid)
        self.assertIn("CPU core-hours per month by member", grid)
        self.assertEqual(alerts, [])
        self.assertIn("no data", kpis.split("/project space")[1].split("</div></div>")[0])

    def test_alert_at_95_percent(self):
        st = dict(ST, quotas=[dict(ST["quotas"][0], used=96e12)] + ST["quotas"][1:])
        kpis, grid, alerts, colors = self.run_lab(storage=lambda: st)
        self.assertEqual(alerts, ["def-a /project space 96%"])

    def test_waits_are_cached_for_the_storage_ttl(self):
        calls = []
        kpis, grid, alerts, colors = self.run_lab(queue_wait=lambda a, d: calls.append(1) or WAITS)
        self.assertEqual(len(calls), 1)
        self.assertIn(("wait", 30, ("def-a_cpu", "def-a_gpu")), cache._ttl)
        self.assertTrue(("usage", ("def-a_cpu", "def-a_gpu")) in cache._ttl)


JB = [["10", "run1", "RUNNING", "00:05:00", "01:00:00", "1", "4", "4G", "def-a_cpu", "fc1"],
      ["11", "pend1", "PENDING", "00:00:00", "01:00:00", "1", "4", "4G", "def-a_cpu", "(Priority)"]]
PEND = [["11", "pend1", "def-a_cpu", "4", "Priority", f"{(T + dt.timedelta(days=1)).isoformat()}T09:00:00"]]
MYHIST = [["2", "j", "TIMEOUT", "01:00:00", "01:00:00", "4G", "0:0", "def-a_cpu", f"{T.isoformat()}T02:00:00", "me"]]
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
        self.addCleanup(cache._ttl.clear)
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
        self.assertNotIn("otheruser", kpis + grid)                     # no other member's name
        self.assertIn("def-a · 0.02% of the group quota", kpis)
        self.assertEqual(alerts, [])

    def test_me_survives_failures(self):
        kpis, grid, alerts, colors = self.run_me(efficiency=boom(RuntimeError("sacct slow")))
        self.assertIn("no data · RuntimeError: sacct slow", grid)
        self.assertEqual(kpis.count('class="kpi"'), 5)
        kpis, grid, alerts, colors = self.run_me(recent=boom(RuntimeError("sacct down")))
        self.assertIn("no data · RuntimeError: sacct down", grid)     # problems fold shows the reason, not "(0)"
        self.assertRegex(kpis, r"My problem jobs.*?<b>—</b>")
        short = MYHIST + [["3", "x", "", "", "", "", "", "", "", ""], ["4", "x"]]
        kpis, grid, alerts, colors = self.run_me(recent=lambda d, a=None: short)
        self.assertRegex(kpis, r"My problem jobs.*?<b>1</b>")
        kpis, grid, alerts, colors = self.run_me(jobs=boom(RuntimeError("squeue down")))
        self.assertRegex(kpis, r"My jobs.*?<b>—</b>")
        kpis, grid, alerts, colors = self.run_me(pending_start=boom(RuntimeError("sprio down")))
        self.assertIn("no data · RuntimeError: sprio down", grid)
        self.assertIn("Pending jobs", grid)

    def test_me_storage_failure(self):
        kpis, grid, alerts, colors = self.run_me(storage=boom(OSError("lfs hung")))
        self.assertIn("no data · OSError: lfs hung", grid)
        self.assertEqual(kpis.count('class="kpi"'), 5)
        self.assertEqual(alerts, [])

    def test_me_alert_on_own_quota(self):
        st = dict(ST, quotas=ST["quotas"][:3] + [dict(ST["quotas"][3], used=48e9)] + ST["quotas"][4:])
        kpis, grid, alerts, colors = self.run_me(storage=lambda: st)
        self.assertEqual(alerts, ["me /home space 96%"])


if __name__ == "__main__":
    unittest.main()


class ShellTests(unittest.TestCase):
    def _with(self, patches, fn):
        self.addCleanup(cache._ttl.clear)
        for p in patches:
            p.start()
        try:
            return fn()
        finally:
            for p in patches:
                p.stop()

    def test_build_defaults_and_tabs(self):
        html = self._with(fake_lab(), lambda: render.build({}))
        html7 = self._with(fake_lab(), lambda: render.build({"page": "lab", "days": "7"}))
        bad = self._with(fake_lab(), lambda: render.build({"page": "nonsense", "days": "999"}))
        self.assertIn("<title>Research Computing Dashboard · Lab</title>", html)
        self.assertIn('href="?page=lab&amp;days=30"', html)
        self.assertIn('href="?page=me&amp;days=7"', html)
        self.assertIn("last 30 d", html)
        self.assertIn("last 7 d", html7)
        self.assertIn("last 30 d", bad)                   # unknown page/days fall back to Lab / 30
        self.assertNotIn("Boards", html)
        self.assertNotIn("<script>", html)
        self.assertIn('<option value="30" selected>', html)

    def test_static_me_page(self):
        html = self._with(fake_me(), lambda: render.build({"page": "me", "static": "1"}))
        self.assertIn("<title>Research Computing Dashboard · Me</title>", html)
        self.assertIn("static snapshot", html)
        self.assertNotIn("<select", html)
        self.assertIn("last 7 d", html)

    def test_hatch_patterns_are_unique(self):
        html = self._with(fake_lab(), lambda: render.build({}))
        import re
        ids = re.findall(r'<pattern id="(h[0-9a-f]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(ids)
