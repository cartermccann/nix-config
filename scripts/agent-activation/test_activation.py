import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

from activation import MARKER, registrations, summarize_native, probe_handler


class ActivationTests(unittest.TestCase):
    def response(self, trust="trusted", enabled=True):
        return {"data": [{"errors": [], "warnings": [], "hooks": [
            {"command": "python hook.py" + MARKER, "eventName": event, "enabled": enabled, "trustStatus": trust}
            for event in ("preToolUse", "postToolUse")]}]}

    def test_modified_and_untrusted_are_not_active(self):
        for trust in ("modified", "untrusted"):
            self.assertEqual(summarize_native(self.response(trust))["status"], "approval_or_repair_required")

    def test_disabled_or_missing_hook_not_active(self):
        self.assertEqual(summarize_native(self.response(enabled=False))["status"], "approval_or_repair_required")
        response = self.response()
        response["data"][0]["hooks"].pop()
        self.assertEqual(summarize_native(response)["status"], "approval_or_repair_required")

    def test_trusted_registered_is_not_provider_proof(self):
        result = summarize_native(self.response())
        self.assertEqual(result["status"], "trusted_registered")
        self.assertEqual(result["live_provider_enforcement"], "not_tested")

    def test_no_unrelated_commands_or_errors_exported(self):
        response = self.response()
        response["data"][0]["errors"] = ["PRIVATE"]
        response["data"][0]["hooks"].append({"command": "PRIVATE"})
        result = summarize_native(response)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(result["loader_issue_count"], 1)

    def test_arbitrary_config_command_not_executed(self):
        result = probe_handler("sh -c 'touch /tmp/never-run'" + MARKER, "codex", "PreToolUse")
        self.assertEqual(result["status"], "command_shape_unverified")

    def test_event_phase_mismatch_cannot_pass(self):
        hook = Path.home() / "nix-config/scripts/agent-tool-guard/guard/hook.py"
        command = shlex.join([sys.executable, str(hook), "--installation-id", "agent-tool-guard-v1",
                              "--runtime", "codex", "--event", "post"])
        result = probe_handler(command, "codex", "PreToolUse")
        self.assertEqual(result["status"], "event_phase_mismatch")

    def test_missing_configuration_is_visible(self):
        with tempfile.TemporaryDirectory() as root:
            result = registrations(Path(root) / "absent.json", "claude")
        self.assertEqual(result["status"], "configuration_unavailable")


if __name__ == "__main__":
    unittest.main()
