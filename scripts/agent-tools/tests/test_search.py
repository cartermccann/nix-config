import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_tools.search import search


@unittest.skipUnless(shutil.which("rg"), "ripgrep required")
class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="search space ")
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(text)
        return path

    def test_literal_metacharacters_and_line_numbers(self):
        self.write("a space.txt", "zero\nliteral $(no-exec) [.*] `whoami`\n")
        result, code = search(self.root, "$(no-exec) [.*] `whoami`")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["results"][0]["line_number"], 2)
        self.assertEqual(result["results"][0]["path"], "a space.txt")

    def test_no_match_is_successful_search(self):
        self.write("a", "ordinary text")
        result, code = search(self.root, "absent")
        self.assertEqual((result["status"], code), ("no_matches", 0))
        self.assertEqual(result["rg_exit_codes"], [1])

    def test_ignore_rules_hidden_opt_in_and_git_exclusion(self):
        self.write(".gitignore", "ignored.txt\n")
        self.write("ignored.txt", "secret needle")
        self.write(".hidden", "needle")
        self.write(".git/hidden-fixture", "needle")
        result, _ = search(self.root, "needle")
        self.assertEqual(result["status"], "no_matches")
        result, code = search(self.root, "needle", hidden=True)
        self.assertEqual(code, 0)
        self.assertEqual([r["path"] for r in result["results"]], [".hidden"])

    def test_result_limit_is_explicit_partial(self):
        self.write("a", "needle\n" * 8)
        result, code = search(self.root, "needle", limit=2)
        self.assertEqual((result["status"], code), ("partial", 3))
        self.assertEqual(len(result["results"]), 2)
        self.assertIn("result_limit", result["partial_reasons"])

    def test_file_count_size_and_selected_byte_limits(self):
        for i in range(4):
            self.write(f"f{i}", "needle" * 10)
        for kwargs, reason in (({"max_files": 1}, "file_limit"),
                               ({"max_file_bytes": 2}, "file_size_limit"),
                               ({"max_bytes": 70}, "selected_bytes_limit")):
            result, code = search(self.root, "needle", **kwargs)
            self.assertEqual(code, 3)
            self.assertIn(reason, result["partial_reasons"])

    def test_output_limit_never_claims_complete(self):
        self.write("long", "needle" + "x" * 100_000)
        result, code = search(self.root, "needle", max_output_bytes=1024)
        self.assertEqual(code, 3)
        self.assertIn("output_limit", result["partial_reasons"])

    def test_glob_narrows_search(self):
        self.write("a.py", "needle")
        self.write("b.txt", "needle")
        result, code = search(self.root, "needle", globs=["*.py"])
        self.assertEqual(code, 0)
        self.assertEqual([r["path"] for r in result["results"]], ["a.py"])

    def test_follow_never_leaves_root(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "other"
            target.write_text("needle")
            (self.root / "escape").symlink_to(target)
            result, code = search(self.root, "needle", follow=True)
            self.assertEqual(code, 3)
            self.assertEqual(result["results"], [])
            self.assertIn("outside_root_symlink", result["partial_reasons"])

    def test_invalid_inputs_and_broad_root(self):
        for kwargs in ({"limit": 0}, {"limit": True}, {"timeout": float("nan")},
                       {"globs": "*.py"}, {"max_bytes": -1}):
            self.assertEqual(search(self.root, "needle", **kwargs)[1], 2)
        self.assertEqual(search(self.root / "missing", "needle")[1], 2)
        self.assertEqual(search(Path.home(), "needle")[0]["error_code"], "root_too_broad")
        self.assertEqual(search("/", "needle")[1], 2)

    def test_missing_executable(self):
        with patch("agent_tools.search.shutil.which", return_value=None):
            self.assertEqual(search(self.root, "needle")[1], 127)

    def test_interrupted_enumeration_never_claims_no_matches(self):
        fake = {"status": "timeout", "timed_out": True, "exit_code": -15,
                "stdout": "", "stderr": "", "stdout_truncated": False,
                "stderr_truncated": False}
        with patch("agent_tools.search.run_command", return_value=(fake, 124)):
            result, code = search(self.root, "needle")
        self.assertEqual((result["status"], code), ("partial", 3))


if __name__ == "__main__":
    unittest.main()
