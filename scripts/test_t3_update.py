#!/usr/bin/env python3
"""Exercise update decisions and restoration without network or Nix builds."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("t3_update", Path(__file__).with_name("t3-update.py"))
t3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t3)

HASH = "sha256-" + "A" * 42 + "E="
DRV = "/nix/store/" + "a" * 32 + "-t3code-pnpm-deps.drv"
MISMATCH = (f"error: hash mismatch in fixed-output derivation '{DRV}':\n"
            f"         specified: {t3.FAKE_HASH}\n         got:       {HASH}\n")


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.pin = self.repo / t3.PIN
        self.pin.parent.mkdir(parents=True)
        # Noncanonical spacing deliberately checks byte-for-byte restoration.
        self.original = b'{"version":"0.1.0", "srcHash":"old-source", "pnpmHash":"old-deps"}\n'
        self.pin.write_bytes(self.original)

    def run_commands(self, args, repo, timeout=120):
        if args[:2] == ["git", "status"]:
            return ""
        if args[:3] == ["nix", "store", "prefetch-file"]:
            return json.dumps({"hash": HASH})
        if args[:2] == ["nix", "build"]:
            return ""
        raise AssertionError(args)

    def context(self, latest="0.1.3", result=None):
        return (
            patch.object(t3, "run", side_effect=self.run_commands),
            patch.object(t3, "latest_release", return_value=latest),
            patch.object(t3, "derivation", return_value=DRV),
            patch.object(t3.subprocess, "run", return_value=result or subprocess.CompletedProcess([], 1, MISMATCH)),
        )

    def test_successful_command_keeps_stderr_out_of_json(self):
        result = subprocess.CompletedProcess([], 0, '{"hash":"value"}', "warning: dirty tree")
        with patch.object(t3.subprocess, "run", return_value=result):
            self.assertEqual(json.loads(t3.run(["nix"], self.repo)), {"hash": "value"})

    def test_current_does_not_write_or_build(self):
        with patch.object(t3, "run", return_value="") as run, patch.object(t3, "latest_release", return_value="0.1.0"):
            self.assertFalse(t3.update(self.repo, "perkons", "cjm"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.pin.read_bytes(), self.original)

    def test_dirty_pin_is_preserved_before_network(self):
        with patch.object(t3, "run", return_value=" M " + t3.PIN), patch.object(t3, "latest_release") as release:
            self.assertFalse(t3.update(self.repo, "perkons", "cjm"))
        release.assert_not_called()
        self.assertEqual(self.pin.read_bytes(), self.original)

    def test_downgrade_is_rejected(self):
        with patch.object(t3, "run", return_value=""), patch.object(t3, "latest_release", return_value="0.0.99"):
            with self.assertRaisesRegex(ValueError, "downgrade"):
                t3.update(self.repo, "perkons", "cjm")
        self.assertEqual(self.pin.read_bytes(), self.original)

    def test_success_writes_both_hashes_and_builds_package(self):
        a, b, c, d = self.context()
        with a as run, b, c, d as depbuild:
            self.assertTrue(t3.update(self.repo, "perkons", "cjm"))
        self.assertEqual(json.loads(self.pin.read_bytes()), {"version": "0.1.3", "srcHash": HASH, "pnpmHash": HASH})
        depbuild.assert_called_once()
        self.assertEqual(run.call_args.args[0][:2], ["nix", "build"])

    def test_unrelated_hash_mismatch_restores_original(self):
        result = subprocess.CompletedProcess([], 1, MISMATCH.replace("t3code-pnpm-deps", "unrelated"))
        a, b, c, d = self.context(result=result)
        with a, b, c, d, self.assertRaisesRegex(ValueError, "expected T3"):
            t3.update(self.repo, "perkons", "cjm")
        self.assertEqual(self.pin.read_bytes(), self.original)

    def test_ambiguous_hashes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            t3.dependency_hash(MISMATCH + MISMATCH, DRV)

    def test_fake_hash_success_is_rejected_and_restored(self):
        a, b, c, d = self.context(result=subprocess.CompletedProcess([], 0, ""))
        with a, b, c, d, self.assertRaisesRegex(ValueError, "unexpectedly"):
            t3.update(self.repo, "perkons", "cjm")
        self.assertEqual(self.pin.read_bytes(), self.original)

    def test_final_build_failure_restores_exact_bytes(self):
        def commands(args, repo, timeout=120):
            if args[:2] == ["nix", "build"]:
                raise RuntimeError("package compilation failed")
            return self.run_commands(args, repo, timeout)
        a, b, c, d = self.context()
        with a as run, b, c, d:
            run.side_effect = commands
            with self.assertRaisesRegex(RuntimeError, "compilation"):
                t3.update(self.repo, "perkons", "cjm")
        self.assertEqual(self.pin.read_bytes(), self.original)

    def test_timeout_restores_exact_bytes(self):
        a, b, c, d = self.context()
        with a, b, c, d as depbuild:
            depbuild.side_effect = subprocess.TimeoutExpired(["nix"], 1800)
            with self.assertRaises(subprocess.TimeoutExpired):
                t3.update(self.repo, "perkons", "cjm")
        self.assertEqual(self.pin.read_bytes(), self.original)

    def test_concurrent_edit_is_never_overwritten(self):
        edited = b'{"version":"operator edit"}'
        def commands(args, repo, timeout=120):
            if args[:2] == ["nix", "build"]:
                self.pin.write_bytes(edited)
                raise RuntimeError("interrupted build")
            return self.run_commands(args, repo, timeout)
        a, b, c, d = self.context()
        with a as run, b, c, d:
            run.side_effect = commands
            with self.assertRaises(RuntimeError):
                t3.update(self.repo, "perkons", "cjm")
        self.assertEqual(self.pin.read_bytes(), edited)

    def test_atomic_write_refuses_concurrent_edit(self):
        with self.assertRaisesRegex(ValueError, "concurrent edit"):
            t3.atomic_write(self.pin, b"candidate", b"stale")
        self.assertEqual(self.pin.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
