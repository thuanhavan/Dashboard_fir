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

        with mock.patch.object(c, "run_checked", fake_run):
            got = c.usage_by_month(["def-kanj241_cpu", "def-kanj241_gpu", "def-acilab_cpu", "def-acilab_gpu"],
                                   months=2, today=dt.date(2026, 11, 25))
        self.assertEqual(sorted(got), ["2026-10", "2026-11"])
        self.assertEqual(got["2026-11"]["hvt632"], {"cpu": 138236.0, "gpu": 24.0})
        self.assertEqual(got["2026-11"]["nazifa"], {"cpu": 0.0, "gpu": 419.0})
        self.assertEqual(len(calls), 4)
        cpu = sorted(x for x in calls if "gres/gpu" not in x)
        gpu = sorted(x for x in calls if "gres/gpu" in x)
        self.assertEqual(len(gpu), 2)
        self.assertIn("accounts=def-acilab_cpu,def-kanj241_cpu start=2026-10-01 end=2026-11-01", cpu[0])
        self.assertTrue(cpu[1].endswith("format=Account,Login,Used") and "end=now" in cpu[1],
                        "current month must end at 'now' (sreport rejects future end times)")
        self.assertIn("accounts=def-acilab_gpu,def-kanj241_gpu start=2026-10-01 end=2026-11-01", gpu[0])

    def test_failure_propagates(self):
        def boom(cmd, timeout=90):
            raise RuntimeError("sreport failed")

        with mock.patch.object(c, "run_checked", boom):
            with self.assertRaises(RuntimeError):
                c.usage_by_month(["def-a_cpu"], months=1, today=dt.date(2026, 11, 25))

    def test_no_accounts_makes_no_calls(self):
        with mock.patch.object(c, "run_checked") as rc:
            got = c.usage_by_month([], months=2, today=dt.date(2026, 11, 25))
        rc.assert_not_called()
        self.assertEqual(got, {"2026-10": {}, "2026-11": {}})

    def test_month_starts_crosses_year(self):
        self.assertEqual(c._month_starts(3, dt.date(2026, 1, 15)),
                         [dt.date(2025, 11, 1), dt.date(2025, 12, 1), dt.date(2026, 1, 1), dt.date(2026, 2, 1)])

    def test_run_checked(self):
        self.assertEqual(c.run_checked("echo hi"), "hi\n")
        with self.assertRaises(RuntimeError):
            c.run_checked("exit 3")


class QueueWaitTests(unittest.TestCase):
    def test_parse_skips_unstarted_and_garbage(self):
        got = c.parse_queue_wait((FIX / "sacct_wait.txt").read_text())
        self.assertEqual(got, [("2026-11-20", 1800.0), ("2026-11-21", 86400.0), ("2026-11-23", 0.0),
                               ("2026-11-24", 0.0), ("2026-11-24", 1800.0)])

    def test_queue_wait_command(self):
        calls = []
        with mock.patch.object(c, "run_checked", lambda cmd, timeout=90: calls.append((cmd, timeout)) or ""):
            self.assertEqual(c.queue_wait(["b_cpu", "a_cpu", "b_cpu"], 30), [])
            self.assertEqual(c.queue_wait([], 30), [])
        self.assertEqual(calls, [("SLURM_TIME_FORMAT=standard sacct -a -A a_cpu,b_cpu -S now-30days -X -n -P -o Submit,Start,State", 180)])

    def test_queue_wait_failure_propagates(self):
        def boom(cmd, timeout=90):
            raise RuntimeError("sacct failed")
        with mock.patch.object(c, "run_checked", boom):
            with self.assertRaises(RuntimeError):
                c.queue_wait(["a_cpu"], 7)


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


if __name__ == "__main__":
    unittest.main()
