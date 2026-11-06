"""Offline guardrails for the two scheduled GitHub main-branch forecast writers.

Simulate a queued checkout that becomes stale while another publisher updates
main; verify refresh and fail-closed publication without touching GitHub.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / ".github/workflows/daily-forecast.yml"
BLASTAM = ROOT / ".github/workflows/blastam-forecast.yml"
ARCHIVE = ROOT / ".github/workflows/weather-archive-backfill.yml"
SCRIPT = ROOT / "scripts/prepare_forecast_main_checkout.sh"
GROUP = "rice-forecast-main-writer"


def _git(cwd, *args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True
    )
    if check and result.returncode:
        raise AssertionError(
            f"git {' '.join(args)} failed: {result.stdout} {result.stderr}"
        )
    return result


def _load(path):
    # BaseLoader preserves the key 'on' rather than YAML 1.1 boolean True.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class WorkflowWriterSafetyTests(unittest.TestCase):
    def test_workflows_are_valid_yaml_and_share_one_cross_workflow_lock(self):
        keys = []
        for path in (DAILY, BLASTAM):
            with self.subTest(workflow=path.name):
                doc = _load(path)
                self.assertIsInstance(doc["on"]["schedule"], list)
                self.assertEqual(doc["permissions"]["contents"], "write")
                concurrency = doc["concurrency"]
                keys.append(concurrency["group"])
                self.assertEqual(concurrency["group"], GROUP)
                self.assertEqual(concurrency["cancel-in-progress"], "false")
                # GitHub 2026 queue extension: do not silently replace
                # already waiting manual/scheduled runs.
                self.assertEqual(concurrency["queue"], "max")
        self.assertEqual(keys, [GROUP, GROUP])

    def test_scheduled_times_and_manual_backfill_inputs_are_unchanged(self):
        self.assertEqual(
            _load(DAILY)["on"]["schedule"],
            [{"cron": "0 16 * * *"}],
        )
        self.assertEqual(
            _load(BLASTAM)["on"]["schedule"],
            [{"cron": "30 17 * * *"}],
        )
        inputs = _load(DAILY)["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"start_date", "end_date"})
        self.assertIn("workflow_dispatch", _load(BLASTAM)["on"])

    def test_each_writer_refreshes_main_after_checkout_before_calculation(self):
        for path, job in (
            (DAILY, "run-forecast-pipeline"),
            (BLASTAM, "run-blastam-pipeline"),
        ):
            with self.subTest(workflow=path.name):
                steps = _load(path)["jobs"][job]["steps"]
                names = [step.get("name", "") for step in steps]
                checkout = next(i for i, step in enumerate(steps)
                                if step.get("uses", "").startswith("actions/checkout@"))
                sync = next(i for i, step in enumerate(steps)
                            if step.get("run", "").strip()
                            == "bash scripts/prepare_forecast_main_checkout.sh")
                compute = next(i for i, step in enumerate(steps)
                               if "pipeline.sh" in step.get("run", ""))
                publish = next(i for i, step in enumerate(steps)
                               if step.get("name") == "Commit and push updated outputs")
                self.assertLess(checkout, sync)
                self.assertLess(sync, compute)
                self.assertLess(compute, publish)
                self.assertEqual(steps[checkout]["with"]["fetch-depth"], "0")
                script = steps[publish]["run"]
                self.assertIn("git push origin HEAD:refs/heads/main", script)
                self.assertNotIn("git push --force", script)
                self.assertNotIn("git push -f", script)
                self.assertNotIn("git reset --hard", script)
                self.assertIn("git commit -m", script)
                self.assertNotIn("FORECAST_MODEL_CATALOG_MODE", str(steps))
                self.assertNotIn("RECENT_STATION_COVERAGE_MODE", str(steps))
                self.assertNotIn("cancel-in-progress: true",
                                 path.read_text(encoding="utf-8"))

    def test_weather_archive_is_on_separate_data_branch(self):
        archive = _load(ARCHIVE)
        self.assertEqual(
            archive["concurrency"]["group"], "weather-archive-2013-writer"
        )
        script = (ROOT / "scripts/weather_archive_action.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("BRANCH = 'weather-archive-2013'", script)
        self.assertNotIn("BRANCH = 'main'", script)

    def test_existing_pipeline_execution_and_publication_scope_is_retained(self):
        daily = _load(DAILY)["jobs"]["run-forecast-pipeline"]["steps"]
        blastam = _load(BLASTAM)["jobs"]["run-blastam-pipeline"]["steps"]
        self.assertIn("bash scripts/run_daily_pipeline.sh",
                      [s.get("run", "") for s in daily])
        self.assertIn("bash scripts/run_blastam_pipeline.sh",
                      [s.get("run", "") for s in blastam])
        self.assertIn(
            "rice_blast_prediction/epirice_lb_states",
            next(s["run"] for s in daily
                 if s.get("name") == "Commit and push updated outputs"),
        )
        self.assertIn(
            "ERA5_archive",
            next(s["run"] for s in blastam
                 if s.get("name") == "Commit and push updated outputs"),
        )


class FreshMainCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.remote = self.home / "remote.git"
        _git(self.home, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.writer = self.home / "writer"
        _git(self.home, "clone", str(self.remote), str(self.writer))
        _git(self.writer, "config", "user.email", "test@example.invalid")
        _git(self.writer, "config", "user.name", "CI Regression")
        (self.writer / "data.csv").write_text("version=1\n")
        _git(self.writer, "add", "data.csv")
        _git(self.writer, "commit", "-m", "initial daily data")
        _git(self.writer, "push", "origin", "main")
        self.actor = self.home / "actor"
        _git(self.home, "clone", str(self.remote), str(self.actor))
        _git(self.actor, "config", "user.email", "test@example.invalid")
        _git(self.actor, "config", "user.name", "Queued Forecast")
        self.start_sha = _git(self.actor, "rev-parse", "HEAD").stdout.strip()

    def _update_remote(self, content):
        (self.writer / "data.csv").write_text(content)
        _git(self.writer, "add", "data.csv")
        _git(self.writer, "commit", "-m", f"publish {content.strip()}")
        _git(self.writer, "push", "origin", "main")
        return _git(self.writer, "rev-parse", "HEAD").stdout.strip()

    def _refresh(self, ref="refs/heads/main"):
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.actor,
            text=True, capture_output=True,
            env={**os.environ, "GITHUB_REF": ref},
        )

    def test_queued_run_reads_latest_main_instead_of_trigger_sha(self):
        latest = self._update_remote("version=2\n")
        self.assertNotEqual(self.start_sha, latest)
        self.assertEqual(_git(self.actor, "rev-parse", "HEAD").stdout.strip(),
                         self.start_sha)
        result = self._refresh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git(self.actor, "rev-parse", "HEAD").stdout.strip(),
                         latest)
        self.assertEqual((self.actor / "data.csv").read_text(), "version=2\n")
        self.assertEqual(_git(self.actor, "status", "--porcelain").stdout, "")

    def test_manual_run_from_non_main_ref_is_rejected_before_mutation(self):
        before = _git(self.actor, "rev-parse", "HEAD").stdout.strip()
        result = self._refresh(ref="refs/heads/dev")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only permitted", result.stderr)
        self.assertEqual(_git(self.actor, "rev-parse", "HEAD").stdout.strip(),
                         before)

    def test_dirty_checkout_rejected_without_discarding_changes(self):
        (self.actor / "data.csv").write_text("uncommitted changes\n")
        latest = self._update_remote("version=2\n")
        result = self._refresh()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not clean", result.stderr)
        self.assertNotEqual(_git(self.actor, "rev-parse", "HEAD").stdout.strip(),
                            latest)
        self.assertEqual((self.actor / "data.csv").read_text(),
                         "uncommitted changes\n")

    def test_external_main_update_still_rejects_non_fast_forward_push(self):
        self.assertEqual(self._refresh().returncode, 0)
        (self.actor / "new_forecast.csv").write_text("fresh prediction\n")
        _git(self.actor, "add", "new_forecast.csv")
        _git(self.actor, "commit", "-m", "forecast generated")
        self._update_remote("version=2\n")
        attempted = _git(self.actor, "push", "origin",
                         "HEAD:refs/heads/main", check=False)
        self.assertNotEqual(attempted.returncode, 0)
        self.assertIn("rejected", attempted.stderr)
        # The external publisher's remote revision must be protected.
        self.assertEqual(
            _git(self.remote, "show", "main:data.csv").stdout,
            "version=2\n",
        )
        self.assertNotIn(
            "new_forecast.csv",
            _git(self.remote, "ls-tree", "-r", "--name-only", "main").stdout,
        )


if __name__ == "__main__":
    unittest.main()
