import json
import os
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


class FairshareTests(unittest.TestCase):
    def test_failure_propagates(self):
        with mock.patch.object(c, "run_checked", side_effect=RuntimeError("sshare failed")):
            self.assertRaises(RuntimeError, c.fairshare)


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

    def test_fs_stats_survives_truncated_live_json(self):
        with mock.patch("builtins.open", mock.mock_open(read_data='{"hsm_size": [')):
            self.assertEqual(c._fs_stats("project", "g"), {"quota": None, "stats": [], "asof": None})

    def test_fs_stats_uses_newest_copy_and_writes_shared_first(self):
        import tempfile
        from firdash.charts import size
        with tempfile.TemporaryDirectory() as shared, tempfile.TemporaryDirectory() as home:
            for d, t in ((shared, "2026-11-20 08:00"), (home, "2026-11-25 08:00")):
                with open(os.path.join(d, "nearline-g.json"), "w") as f:
                    json.dump({"time": t, "quota": None, "stats": [{"uid": "a", "count": 1}]}, f)
            with mock.patch.object(c, "STATS_DIRS", [shared, home]):
                self.assertEqual(c._fs_stats("nearline", "g")["asof"], "2026-11-25")
                c._save_stats("nearline-g.json", {"time": "2026-11-26 08:00", "stats": []})
                self.assertEqual(c._fs_stats("nearline", "g")["asof"], "2026-11-26")
            with mock.patch.object(c, "STATS_DIRS", ["/proc/no-such-dir", home]):
                self.assertTrue(c._save_stats("x.json", {}).startswith(home))
        self.assertEqual((size(1_541_477), size(3 * 2**30), size(500)), ("1.5 MiB", "3 GiB", "0 KiB"))

    def test_removed_collectors_are_gone(self):
        for name in ("group_jobs", "filesystems", "arrays", "reservations", "pending_lab"):
            self.assertFalse(hasattr(c, name), name)


class RunningNowTests(unittest.TestCase):
    def test_running_now_sums_cpus_and_gpus(self):
        out = "cpu=4,mem=64G,node=1,billing=16000\ncpu=12,mem=100G,node=1,billing=5,gres/gpu=2\n\n"
        with mock.patch.object(c, "run_checked", return_value=out) as rc:
            self.assertEqual(c.running_now(["a_cpu", "a_gpu"]), {"jobs": 2, "gpu_jobs": 1, "cpus": 16, "gpus": 2})
        self.assertIn("-A a_cpu,a_gpu -t RUNNING", rc.call_args[0][0])


class FailLoudlyTests(unittest.TestCase):
    def test_job_collectors_propagate_run_checked_errors(self):
        calls = {"jobs": (), "recent": (7,), "pending_start": (), "efficiency": (7,), "nodes": ()}
        for name, args in calls.items():
            with self.subTest(name), \
                 mock.patch.object(c, "run_checked", side_effect=RuntimeError("boom")), \
                 mock.patch.object(c, "run", side_effect=AssertionError("unchecked run() used")):
                with self.assertRaises(RuntimeError):
                    getattr(c, name)(*args)


if __name__ == "__main__":
    unittest.main()


class ScanTests(unittest.TestCase):
    def test_scan_my_files_groups_by_two_levels_and_writes_cache(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        listing = {"'/p/g' --maxdepth 1": "/p/g/top.txt\n",
                   "'/p/g/A'": "/p/g/A/B/one.bin\n/p/g/A/B/deep/two.bin\n/p/g/A/three.bin\n", "'/p/g/Z'": ""}

        def fake_run(cmd, timeout=90):
            for k, v in listing.items():
                if k in cmd:
                    return v
            return ""
        listing["'/p/g/loop'"] = "/p/g/loop/A/B/one.bin\n"     # a symlink back to the tree must not be followed
        sizes = {"/p/g/A/B/one.bin": 10, "/p/g/A/B/deep/two.bin": 20, "/p/g/A/three.bin": 5, "/p/g/top.txt": 1}
        st = lambda path: mock.Mock(st_size=sizes[path])
        with mock.patch.object(c, "run", fake_run), mock.patch.object(c, "USER", "me"), \
                mock.patch.object(c, "MYFILES", tmp + "/myfiles.json"), \
                mock.patch.object(c.os.path, "realpath", lambda x: "/p/g"), mock.patch.object(c.os.path, "isdir", lambda x: True), \
                mock.patch.object(c.os, "listdir", lambda x: ["Z", "A", "loop"]), mock.patch.object(c.os, "lstat", st), \
                mock.patch.object(c.os.path, "islink", lambda x: x.endswith("/loop")):
            got = c.scan_my_files(["g"])
        self.assertEqual(got["total"], 4)
        self.assertEqual(got["rows"], [["g/A/B", 2, 30], ["g/A", 1, 5], ["g", 1, 1]])
        self.assertFalse(c.SCAN.is_set())
        with mock.patch.object(c, "MYFILES", tmp + "/myfiles.json"):
            self.assertEqual(c.my_files_cached()["rows"], got["rows"])
        with mock.patch.object(c, "MYFILES", tmp + "/missing.json"):
            self.assertIsNone(c.my_files_cached())

    def test_scan_counts_only_above_size_limit(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        many = "".join(f"/p/g/A/f{i}\n" for i in range(5))
        with mock.patch.object(c, "run", lambda cmd, timeout=90: many if "'/p/g/A'" in cmd else ""), \
                mock.patch.object(c, "SIZE_LIMIT", 3), mock.patch.object(c, "MYFILES", tmp + "/m.json"), \
                mock.patch.object(c.os.path, "realpath", lambda x: "/p/g"), mock.patch.object(c.os.path, "isdir", lambda x: True), \
                mock.patch.object(c.os, "listdir", lambda x: ["A"]), mock.patch.object(c.os, "lstat", lambda p: mock.Mock(st_size=1)), \
                mock.patch.object(c.os.path, "islink", lambda x: False):
            got = c.scan_my_files(["g"])
        self.assertEqual(got["rows"], [["g/A", 5, None]])
