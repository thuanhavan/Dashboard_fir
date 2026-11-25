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


if __name__ == "__main__":
    unittest.main()
