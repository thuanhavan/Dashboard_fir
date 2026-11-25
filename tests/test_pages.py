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

    def test_waits_are_cached_for_the_storage_ttl(self):
        calls = []
        kpis, grid, alerts, colors = self.run_lab(queue_wait=lambda a, d: calls.append(1) or WAITS)
        self.assertEqual(len(calls), 1)
        self.assertIn(("wait", 30), cache._ttl)
        self.assertTrue(any(k == "usage" for k in cache._ttl))


if __name__ == "__main__":
    unittest.main()
