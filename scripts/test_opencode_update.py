"""Check dependency drift recovery and pin safety without network access."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("opencode_update", Path(__file__).with_name("opencode-update.py"))
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)
OLD = "sha256-" + "A" * 43 + "="
NEW = "sha256-" + "B" * 43 + "="
DRV = "/nix/store/" + "a" * 32 + "-opencode-node_modules-1.19.5.drv"
MISMATCH = (f"error: hash mismatch in fixed-output derivation '{DRV}':\n"
            f"  specified: {OLD}\n  got: {NEW}\n")


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.path = self.repo / updater.PIN
        self.path.parent.mkdir(parents=True)
        self.original = json.dumps(dict(version="1.19.5", srcHash=OLD, nodeModulesHash=OLD)).encode()
        self.path.write_bytes(self.original)

    def test_current_version_still_validates_dependency_package_and_server(self):
        with patch.object(updater, "run", return_value="") as run, \
             patch.object(updater, "latest_release", return_value="1.19.5"), \
             patch.object(updater, "derivation", return_value=DRV) as drv, \
             patch.object(updater.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "")):
            self.assertFalse(updater.update(self.repo, "perkons", "cjm"))
        self.assertEqual([call.args[3] for call in drv.call_args_list], ["dependency", "package", "server"])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_bun_drift_recovered_without_version_bump(self):
        with patch.object(updater, "run", return_value=""), \
             patch.object(updater, "latest_release", return_value="1.19.5"), \
             patch.object(updater, "derivation", return_value=DRV), \
             patch.object(updater.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, MISMATCH)):
            self.assertTrue(updater.update(self.repo, "perkons", "cjm"))
        self.assertEqual(json.loads(self.path.read_bytes())["nodeModulesHash"], NEW)

    def test_wrong_derivation_and_multiple_mismatches_rejected(self):
        for output in (MISMATCH.replace(DRV, DRV + "-other"), MISMATCH * 2, "registry network failure"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                updater.dependency_hash(output, DRV, OLD)

    def test_package_failure_restores_exact_original_bytes(self):
        def commands(args, repo, timeout=120):
            if args[:2] == ["nix", "build"]:
                raise RuntimeError("compile failure")
            return ""
        with patch.object(updater, "run", side_effect=commands), \
             patch.object(updater, "latest_release", return_value="1.19.5"), \
             patch.object(updater, "derivation", return_value=DRV), \
             patch.object(updater.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, MISMATCH)), \
             self.assertRaisesRegex(RuntimeError, "compile failure"):
            updater.update(self.repo, "perkons", "cjm")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_concurrent_edit_preserved(self):
        edited = b'operator edit'
        def commands(args, repo, timeout=120):
            if args[:2] == ["nix", "build"]:
                self.path.write_bytes(edited)
                raise RuntimeError("compile failure")
            return ""
        with patch.object(updater, "run", side_effect=commands), \
             patch.object(updater, "latest_release", return_value="1.19.5"), \
             patch.object(updater, "derivation", return_value=DRV), \
             patch.object(updater.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, MISMATCH)), \
             self.assertRaisesRegex(RuntimeError, "compile failure"):
            updater.update(self.repo, "perkons", "cjm")
        self.assertEqual(self.path.read_bytes(), edited)

    def test_trusted_dirty_pin_still_validates_all_builds(self):
        digest = hashlib.sha256(self.original).hexdigest()
        def commands(args, repo, timeout=120):
            return " M " + updater.PIN if args[:2] == ["git", "status"] else ""
        with patch.object(updater, "run", side_effect=commands), \
             patch.object(updater, "latest_release", return_value="1.19.5"), \
             patch.object(updater, "derivation", return_value=DRV) as drv, \
             patch.object(updater.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "")):
            self.assertFalse(updater.update(self.repo, "perkons", "cjm", digest))
        self.assertEqual([call.args[3] for call in drv.call_args_list], ["dependency", "package", "server"])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_dirty_pin_without_trusted_digest_refused(self):
        with patch.object(updater, "run", return_value=" M " + updater.PIN), \
             patch.object(updater, "latest_release") as release, \
             self.assertRaisesRegex(ValueError, "local edits"):
            updater.update(self.repo, "perkons", "cjm")
        release.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_changed_trusted_pin_and_invalid_digests_refused(self):
        for digest in ("0" * 64, "A" * 64, "bad"):
            with self.subTest(digest=digest), patch.object(updater, "latest_release") as release, \
                 self.assertRaises(ValueError):
                updater.update(self.repo, "perkons", "cjm", digest)
            release.assert_not_called()
            self.assertEqual(self.path.read_bytes(), self.original)

    def test_trusted_pin_changed_during_validation_preserved(self):
        digest = hashlib.sha256(self.original).hexdigest()
        edited = b"changed concurrently"
        def commands(args, repo, timeout=120):
            if args[:2] == ["nix", "build"]:
                self.path.write_bytes(edited)
            return " M " + updater.PIN if args[:2] == ["git", "status"] else ""
        with patch.object(updater, "run", side_effect=commands), \
             patch.object(updater, "latest_release", return_value="1.19.5"), \
             patch.object(updater, "derivation", return_value=DRV), \
             patch.object(updater.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "")), \
             self.assertRaisesRegex(ValueError, "changed during validation"):
            updater.update(self.repo, "perkons", "cjm", digest)
        self.assertEqual(self.path.read_bytes(), edited)

    def test_v2_release_cannot_replace_provider(self):
        with self.assertRaisesRegex(ValueError, "stable OpenCode v1"):
            updater.stable_version("2.0.0")

    def test_json_stdout_excludes_nix_warnings(self):
        result = subprocess.CompletedProcess([], 0, '{"hash":"ok"}', "warning: dirty tree")
        with patch.object(updater.subprocess, "run", return_value=result):
            self.assertEqual(json.loads(updater.run(["nix"], self.repo)), {"hash": "ok"})


if __name__ == "__main__":
    unittest.main()
