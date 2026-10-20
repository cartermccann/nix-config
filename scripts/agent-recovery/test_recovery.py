"""Negative-control tests: incorrect submissions and broken helpers cannot pass."""
import copy
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import recovery


def submission():
    return {"schema_version": 1, "suite_id": "agent-recovery-v1", "result_type": "agent_behavior",
            "harness": "codex", "model": "synthetic-test-only", "results": [
                {"case_id": c["id"], "status": "completed",
                 "observations": {key: True for key in c["assertions"]},
                 "evidence": [{"kind": "captured_tool_trace", "path": "synthetic-test.json", "sha256": "a" * 64}]}
                for c in recovery.manifest()["cases"]]}


class ScorerTests(unittest.TestCase):
    def test_correct_shape_is_reported_only_not_trace_verified(self):
        result, code = recovery.score(submission())
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["result_type"], "reported_agent_behavior")
        self.assertEqual(result["trace_validation"], "not_performed")

    def test_every_false_assertion_fails(self):
        good = submission()
        for index, record in enumerate(good["results"]):
            for key in record["observations"]:
                bad = copy.deepcopy(good)
                bad["results"][index]["observations"][key] = False
                with self.subTest(case=record["case_id"], assertion=key):
                    result, code = recovery.score(bad)
                    self.assertEqual(code, 1)
                    self.assertEqual(result["status"], "failed")

    def test_not_run_is_incomplete(self):
        value = submission()
        value["results"][0] = {"case_id": "missing_executable", "status": "not_run"}
        result, code = recovery.score(value)
        self.assertEqual((code, result["status"]), (1, "incomplete"))

    def test_missing_unknown_duplicate_cases_and_no_evidence_are_invalid(self):
        variants = []
        value = submission(); value["results"].pop(); variants.append(value)
        value = submission(); value["results"][0]["case_id"] = "unknown"; variants.append(value)
        value = submission(); value["results"][0] = value["results"][1]; variants.append(value)
        value = submission(); value["results"][0]["evidence"] = []; variants.append(value)
        value = submission(); value["results"][0]["evidence"][0]["sha256"] = "fake"; variants.append(value)
        value = submission(); value["results"][0]["observations"]["no_execution"] = 1; variants.append(value)
        value = submission(); value["results"][0]["observations"].pop("no_execution"); variants.append(value)
        value = submission(); value["results"][0]["status"] = "passed"; variants.append(value)
        value = submission(); value["schema_version"] = True; variants.append(value)
        value = submission(); value["result_type"] = "deterministic_tooling"; variants.append(value)
        variants.extend([None, [], {}, True])
        for value in variants:
            with self.subTest(value=type(value).__name__):
                result, code = recovery.score(value)
                self.assertEqual((code, result["status"]), (2, "invalid"))


class MechanicalTests(unittest.TestCase):
    def test_false_observations_cannot_pass(self):
        with patch("recovery.mechanical", return_value={}):
            result, code = recovery.verify(["unused"], "unused")
        self.assertEqual(code, 1)
        self.assertEqual(result["counts"], {"passed": 0, "failed": 10, "not_run": 2})

    def test_exception_cannot_pass_or_echo_secret(self):
        with patch("recovery.mechanical", side_effect=ValueError("SECRET")):
            result, code = recovery.verify(["unused"], "unused")
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_broken_helper_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken.py"
            broken.write_text("print('{\"status\":\"completed\",\"exit_code\":0}')")
            with tempfile.TemporaryDirectory() as fixture:
                observations = recovery.mechanical("failed_test", [sys.executable, str(broken)], "unused", fixture)
            self.assertFalse(all(observations.values()))

    def test_cli_invalid_json_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"; path.write_text("no")
            result = subprocess.run([sys.executable, str(recovery.ROOT / "agent-recovery"), "score", str(path)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "invalid")

    def test_cli_broken_helper_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken.py"
            broken.write_text("print('{\"status\":\"completed\",\"exit_code\":0}')")
            result = subprocess.run([sys.executable, str(recovery.ROOT / "agent-recovery"),
                                     "verify", "--tools", shlex.join([sys.executable, str(broken)])],
                                    capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertGreater(payload["counts"]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
