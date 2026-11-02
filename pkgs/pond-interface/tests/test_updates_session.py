"""Nix update backend: read-only check and confirmed interactive flow."""
import builtins
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from fakes import ROOT


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nix = load("pond_nix_updates", "shell/updates/nix.py")


def lock(revs):
    nodes = {"root": {"inputs": {name: name for name in revs} | {"follower": ["nixpkgs"]}}}
    for name, rev in revs.items():
        nodes[name] = {"locked": {"rev": rev, "lastModified": 1700000000}}
    return {"nodes": nodes, "root": "root", "version": 7}


class FakeNix:
    """Stands in for `nix flake update --output-lock-file` and the builders."""

    def __init__(self, latest, build=0, switch=0):
        self.latest, self.build, self.switch, self.calls = latest, build, switch, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["nix", "flake", "update"]:
            assert "--output-lock-file" in argv, "the real lock must never be updated by nix"
            Path(argv[argv.index("--output-lock-file") + 1]).write_text(json.dumps(self.latest))
            return subprocess.CompletedProcess(argv, 0, "", "")
        code = self.switch if "switch" in argv else self.build
        return subprocess.CompletedProcess(argv, code, "", "")


class Updates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.flake = Path(self.tmp.name) / "flake"
        self.flake.mkdir()
        (self.flake / "flake.nix").write_text("{ }")
        self.original = json.dumps(lock({"nixpkgs": "a" * 40, "hyprland": "b" * 40}))
        (self.flake / "flake.lock").write_text(self.original)
        self.status = Path(self.tmp.name) / "pond-update"
        self.env = mock.patch.dict(os.environ, {"POND_FLAKE": str(self.flake),
                                                "XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def newer(self):
        return lock({"nixpkgs": "c" * 40, "hyprland": "b" * 40})

    def test_check_is_read_only(self):
        result = nix.check(run=FakeNix(self.newer()))
        self.assertTrue(result["available"])
        self.assertEqual([c["input"] for c in result["inputs"]], ["nixpkgs"])
        self.assertEqual((self.flake / "flake.lock").read_text(), self.original)
        self.assertFalse(nix.check(run=FakeNix(json.loads(self.original)))["available"])

    def test_check_errors(self):
        def fail(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "warning: x\nerror: unable to download\n")
        with self.assertRaisesRegex(RuntimeError, "unable to download"):
            nix.check(run=fail)
        with mock.patch.dict(os.environ, {"POND_FLAKE": self.tmp.name + "/missing"}):
            with self.assertRaisesRegex(RuntimeError, "No system flake"):
                nix.check(run=fail)

    def interactive(self, answers, fake):
        replies = iter(answers)
        with mock.patch.object(builtins, "input", lambda prompt="": next(replies)), \
                mock.patch.object(nix, "builder", lambda flake: (["build"], ["switch"])), \
                mock.patch.object(nix, "needs_restart", lambda: False), \
                mock.patch("builtins.print"):
            code = nix.interactive(str(self.status), run=fake)
        return code, self.status.read_text().split("\n")[0]

    def test_declining_changes_nothing(self):
        fake = FakeNix(self.newer())
        self.assertEqual(self.interactive(["n"], fake), (0, "available"))
        self.assertEqual((self.flake / "flake.lock").read_text(), self.original)
        self.assertNotIn(["build"], fake.calls)

    def test_confirmed_update_writes_checked_lock_builds_and_switches(self):
        fake = FakeNix(self.newer())
        self.assertEqual(self.interactive(["y", "y"], fake), (0, "complete"))
        self.assertEqual(json.loads((self.flake / "flake.lock").read_text()), self.newer())
        self.assertEqual([c for c in fake.calls if c in (["build"], ["switch"])], [["build"], ["switch"]])

    def test_build_failure_restores_lock(self):
        fake = FakeNix(self.newer(), build=1)
        self.assertEqual(self.interactive(["y", ""], fake), (1, "failed"))
        self.assertEqual((self.flake / "flake.lock").read_text(), self.original)

    def test_switch_is_optional_and_failure_reported(self):
        self.assertEqual(self.interactive(["y", "n"], FakeNix(self.newer())), (0, "available"))
        (self.flake / "flake.lock").write_text(self.original)
        self.assertEqual(self.interactive(["y", "y"], FakeNix(self.newer(), switch=1)), (1, "failed"))

    def test_up_to_date(self):
        self.assertEqual(self.interactive([], FakeNix(json.loads(self.original))), (0, "complete"))


if __name__ == "__main__":
    unittest.main()
