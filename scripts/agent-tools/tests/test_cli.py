import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / "agent-tools"


class CLITests(unittest.TestCase):
    def test_cli_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(CLI), "run", "--cwd", tmp,
                                     "--", sys.executable, "-c", "print('literal result')"],
                                    cwd=tmp, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["command"], "run")
        self.assertEqual(data["stdout"], "literal result\n")

    def test_failed_command_preserved(self):
        result = subprocess.run([sys.executable, str(CLI), "run", "--", sys.executable,
                                 "-c", "raise SystemExit(7)"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(json.loads(result.stdout)["exit_code"], 7)

    def test_invalid_numeric_input_is_json_error(self):
        result = subprocess.run([sys.executable, str(CLI), "run", "--timeout", "nan", "--",
                                 sys.executable, "-c", "print('must not run')"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_bash_and_fish_same_public_interface(self):
        if not shutil.which("fish"):
            self.skipTest("fish required")
        values = []
        for shell in ("bash", "fish"):
            args = [str(CLI), "run", "--", sys.executable, "-c",
                    "import sys;print(sys.argv[1])", "literal $HOME `uname` a b"]
            result = subprocess.run([shell, "--no-config", "-c", shlex.join(args)] if shell == "fish"
                                    else [shell, "--noprofile", "--norc", "-c", shlex.join(args)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values.append(json.loads(result.stdout)["stdout"])
        self.assertEqual(values, ["literal $HOME `uname` a b\n"] * 2)

    def test_usage_errors_are_json_without_echoing_invalid_values(self):
        for args in (("run", "--timeout", "SECRET_INVALID_VALUE", "--", "true"),
                     ("search", "--literal", "needle"), ("unknown-command",)):
            result = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error_code"], "invalid_cli_arguments")
            self.assertNotIn("SECRET_INVALID_VALUE", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
