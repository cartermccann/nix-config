import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "delta_client_setup", Path(__file__).parents[1] / "delta-client-setup.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class DeltaClaudeHooksTest(unittest.TestCase):
    def test_migrates_legacy_hooks_preserves_other_settings_and_is_idempotent(self):
        unrelated = {"type": "command", "command": "other-hook"}
        legacy = {
            "type": "command", "command": "/old/bin/delta-history",
            "args": ["hook", "claude", "--instance", "old"],
            "onFailure": "continue",
        }
        original = {"theme": "dark", "hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [legacy, unrelated]},
        ]}}
        before = json.dumps(original)
        updated = setup.merge(original, "/new/bin/delta-history", "perkons")
        self.assertEqual(json.dumps(original), before)
        self.assertEqual(updated["theme"], "dark")
        self.assertEqual(updated["hooks"]["UserPromptSubmit"][0]["hooks"][1], unrelated)
        for event in ("UserPromptSubmit", "Stop", "SessionEnd"):
            managed = updated["hooks"][event][0]["hooks"][0]
            self.assertTrue(setup.is_delta_hook(managed))
            self.assertNotIn("args", managed)
            self.assertNotIn("onFailure", managed)
        self.assertEqual(setup.merge(updated, "/new/bin/delta-history", "perkons"), updated)

    def test_capture_receives_payload_but_cannot_block_or_inject_context(self):
        with tempfile.TemporaryDirectory(prefix="delta hooks ; ") as directory:
            executable = Path(directory) / "delta-history"
            executable.write_text(
                '#!/bin/sh\n'
                'test "$1 $2 $3 $4" = "hook claude --instance perkons" || exit 17\n'
                'IFS= read -r payload\n'
                'printf "%s\\n" "$payload" >&2\n'
                'printf "capture receipt\\n"\n'
                'exit "${DELTA_TEST_EXIT:-0}"\n'
            )
            executable.chmod(0o700)
            settings = setup.merge({}, str(executable), "perkons")
            command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
            payload = '{"hook_event_name":"UserPromptSubmit","prompt":"test"}'
            for exit_code in (0, 2):
                with self.subTest(exit_code=exit_code):
                    result = subprocess.run(
                        ["sh", "-c", command], input=payload + "\n",
                        capture_output=True, text=True,
                        env={"DELTA_TEST_EXIT": str(exit_code)}, timeout=5,
                    )
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr.strip(), payload)


if __name__ == "__main__":
    unittest.main()
