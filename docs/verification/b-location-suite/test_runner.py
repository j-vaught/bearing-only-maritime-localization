"""Scoped orchestration checks without localization searches. J.C. Vaught."""

import importlib.util
import inspect
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("b_suite", Path(__file__).with_name("run_suite.py"))
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def sleeping_group(path):
    os.setsid()
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(path).write_text(str(grandchild.pid))
    time.sleep(120)


class RunnerChecks(unittest.TestCase):
    def test_idle_gpu_utilization_and_compute_context_guard(self):
        snapshot = "0, RTX, 49140, 15, 8\n1, RTX, 49140, 15, 0\n2, RTX, 49140, 38, 0"
        indexed = "0, GPU-a\n1, GPU-b\n2, GPU-c\n3, GPU-d"
        self.assertEqual(runner.busy_devices(snapshot, indexed, "GPU-d, 123"), [])
        self.assertEqual(runner.busy_devices(snapshot, indexed, "GPU-a, 456"), [0])
        self.assertEqual(
            runner.busy_devices(snapshot.replace("49140, 15, 8", "49140, 2048, 0"), indexed, ""),
            [0],
        )

    def test_threshold_boundary(self):
        self.assertFalse(runner.eligible_aid_count(3))
        self.assertFalse(runner.eligible_aid_count(15))
        self.assertTrue(runner.eligible_aid_count(16))

    def test_resume_requires_completed_and_exact_provenance(self):
        self.assertTrue(
            runner.resume_matches({"status": "completed", "provenance_sha256": "a"}, "a")
        )
        for status in ("running", "failed", "invalid", "timeout"):
            self.assertFalse(
                runner.resume_matches({"status": status, "provenance_sha256": "a"}, "a")
            )
        self.assertFalse(
            runner.resume_matches({"status": "completed", "provenance_sha256": "a"}, "b")
        )

    def test_search_boundary_has_no_truth_argument(self):
        self.assertEqual(
            list(inspect.signature(runner.search_child).parameters),
            ["world", "settings", "bearings", "directory"],
        )
        source = inspect.getsource(runner.search_child)
        self.assertNotIn('"truth.json"', source)
        self.assertNotIn("KnownWaterObserver", source)
        settings = runner.read(runner.REFERENCE)["settings"]
        self.assertEqual(
            set(settings),
            {"map", "catalog", "measurements", "marker_tolerance_deg", "search", "preparation"},
        )
        self.assertEqual(settings["search"]["devices"], [0, 1, 2])
        self.assertEqual(settings["search"]["terminal_size_m"], 1.0)
        self.assertEqual(settings["marker_tolerance_deg"], 0.5)

    def test_csv_order_and_exact_stored_b24(self):
        rows = runner.sites()
        self.assertEqual(len(rows), 24)
        self.assertEqual(float(rows[-1]["latitude_deg"]), 25.76)
        self.assertEqual(float(rows[-1]["longitude_deg"]), -80.175)

    def test_geodesic_freeze_and_no_estimate_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            estimate = root / "estimate.json"
            freeze = root / "freeze.json"
            truth = root / "truth.json"
            runner.save(
                estimate,
                {
                    "full_region_search_complete": True,
                    "truth_used_for_selection": False,
                    "estimate": {
                        "status": 0,
                        "flags": 0,
                        "latitude_deg": 32.77,
                        "longitude_deg": -79.905,
                    },
                },
            )
            before = estimate.read_bytes()
            runner.save(freeze, {"estimate_sha256": runner.digest(estimate)})
            runner.save(truth, {"latitude_deg": 32.77, "longitude_deg": -79.905})
            accuracy = runner.geodesic_after_freeze(estimate, freeze, truth)
            self.assertEqual(accuracy["position_error_m"], 0.0)
            self.assertEqual(estimate.read_bytes(), before)
            estimate.write_text(estimate.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "changed after freeze"):
                runner.geodesic_after_freeze(estimate, freeze, truth)

    def test_timeout_cleans_descendants(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ready"
            child = mp.get_context("fork").Process(target=sleeping_group, args=(str(path),))
            child.start()
            deadline = time.perf_counter() + 5
            while not path.exists() and time.perf_counter() < deadline:
                time.sleep(0.02)
            self.assertTrue(path.exists())
            runner.stop_process(child)
            self.assertFalse(child.is_alive())
            self.assertEqual(runner.group_members(child.pid), [])

    def test_external_command_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(TimeoutError, "exceeded"):
                runner.bounded_command(
                    [sys.executable, "-c", "import time; time.sleep(20)"],
                    Path(temporary),
                    "sleep",
                    0.1,
                )

    def test_cache_rebuild_forbidden(self):
        with self.assertRaisesRegex(RuntimeError, "rebuilding is forbidden"):
            runner.forbid_build()


if __name__ == "__main__":
    unittest.main(verbosity=2)
