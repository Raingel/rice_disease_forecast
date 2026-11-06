"""Prevent invalid one-time GitHub Actions YAML from generating red workflow runs.

The two manual historical backfill workflows must remain manual and must not
silently start on a push or schedule after their display title is repaired.
"""
from __future__ import annotations

from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    ".github/workflows/era5-archive-once.yml":
        ("One-time: Run ERA5 Archive Download Once", "run-era5-archive"),
    ".github/workflows/blastdt2-backfill.yml":
        ("One-time: BlastDT2 Backfill", "run-blastdt2-backfill"),
}


class OneTimeWorkflowYamlTests(unittest.TestCase):
    def test_workflows_parse_and_remain_manual_only(self):
        for filename, (expected_name, job_name) in CASES.items():
            with self.subTest(file=filename):
                path = ROOT / filename
                raw = path.read_text(encoding="utf-8")
                parsed = yaml.load(raw, Loader=yaml.BaseLoader)
                self.assertIsInstance(parsed, dict)
                self.assertEqual(parsed["name"], expected_name)
                self.assertEqual(set(parsed["on"]), {"workflow_dispatch"})
                self.assertEqual(set(parsed["jobs"]), {job_name})
                self.assertEqual(parsed["permissions"]["contents"], "write")
                self.assertEqual(parsed["jobs"][job_name]["runs-on"], "ubuntu-latest")
                self.assertIn("git push", str(parsed["jobs"][job_name]))
                self.assertNotIn("schedule", parsed["on"])
                self.assertNotIn("push", parsed["on"])


if __name__ == "__main__":
    unittest.main()
