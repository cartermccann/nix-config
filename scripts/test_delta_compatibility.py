"""Candidate checks must use packaged policy and never access a live Delta daemon."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("delta_compatibility", Path(__file__).with_name("delta-compatibility.py"))
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


class CompatibilityTests(unittest.TestCase):
    def test_contract_command_is_separated_from_host_daemon_state_and_network(self):
        with patch.object(compat.shutil, "which", return_value="/bin/bwrap"), \
                patch.object(compat, "run", return_value='{"operation":"contract","min":8,"max":8}') as run:
            self.assertEqual(compat.isolated_contract(Path("/nix/store/candidate/bin/zed-delta"))["max"], 8)
        argv = run.call_args.args[0]
        for flag in ("--unshare-all", "--clearenv", "--new-session", "--die-with-parent"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--ro-bind") + 1:argv.index("--ro-bind") + 3], ["/nix/store", "/nix/store"])
        self.assertNotIn("--bind", argv)
        self.assertNotIn("--ro-bind-try", argv)
        self.assertEqual(argv[-4:], ["/nix/store/candidate/bin/zed-delta", "--json", "thread", "contract"])
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_missing_sandbox_never_falls_back_to_live_cli(self):
        with patch.object(compat.shutil, "which", return_value=None), patch.object(compat, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "refusing an unisolated"):
                compat.isolated_contract(Path("/candidate"))
        run.assert_not_called()

    def fixture(self, root):
        package = root / "lib/python3.13/site-packages/t3_delta"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "engine.py").write_text('''class Engine:
    def __init__(self, *args):
        raise AssertionError("Stateful initialization must never run")
    def poll(self):
        if self.source.environment()["serverVersion"] != "1.2.3":
            raise ValueError("fixture rejects this T3 version")
        self.source.projects()
''')
        (package / "delta_sink.py").write_text('''class DeltaSink:
    def __init__(self, *args):
        raise AssertionError("Stateful initialization must never run")
    def _contract(self):
        value = self._command(["thread", "contract"])
        if value["min"] != 8 or value["max"] != 8:
            raise ValueError("fixture rejects this contract")
        return 8
''')

    def test_candidate_package_policy_accepts_or_rejects_without_stateful_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            result = compat.probe(root, "1.2.3", {"operation": "contract", "min": 8, "max": 8})
            self.assertEqual(result, {"t3_version": "1.2.3", "delta_contract": 8})
            with self.assertRaisesRegex(RuntimeError, "fixture rejects this T3 version"):
                compat.probe(root, "1.2.4", {"operation": "contract", "min": 8, "max": 8})
            with self.assertRaisesRegex(RuntimeError, "fixture rejects this contract"):
                compat.probe(root, "1.2.3", {"operation": "contract", "min": 9, "max": 9})
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["lib"])

    def test_candidate_patch_build_failure_stops_before_any_cli_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch.object(compat, "packages", side_effect=RuntimeError("patch drift")), \
                    patch.object(compat, "isolated_contract") as contract:
                with self.assertRaisesRegex(RuntimeError, "patch drift"):
                    compat.check(repo, "host", "user", "0.1.7")
            contract.assert_not_called()

    def test_success_builds_and_probes_the_same_selected_packages(self):
        selected = {"recorder": {"path": "/nix/store/recorder"}, "delta": {"path": "/nix/store/delta"}}
        value = {"operation": "contract", "min": 8, "max": 8}
        with patch.object(compat, "packages", return_value=selected), \
                patch.object(compat, "isolated_contract", return_value=value) as contract, \
                patch.object(compat, "probe", return_value={"delta_contract": 8}) as probe:
            compat.check(Path("/repo"), "host", "user", "0.1.7")
        contract.assert_called_once_with(Path("/nix/store/delta/bin/zed-delta"))
        probe.assert_called_once_with("/nix/store/recorder", "0.1.7", value)


if __name__ == "__main__":
    unittest.main()
