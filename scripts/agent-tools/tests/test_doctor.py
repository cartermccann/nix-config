import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_tools import doctor


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="doctor test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.invalid")

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args], check=check,
                              capture_output=True, text=True, timeout=5)

    def commit(self):
        (self.repo / "tracked.txt").write_text("original\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-qm", "Initial fixture")

    def test_unborn_repository_with_spaces(self):
        data, code = doctor.diagnose(str(self.repo))
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["repository"]["branch"], "main")
        self.assertTrue(data["repository"]["unborn"])
        self.assertIsNone(data["repository"]["head"])
        self.assertFalse(data["worktree_status"]["dirty"])

    def test_clean_detached_and_nested_directory(self):
        self.commit()
        self.git("checkout", "--detach", "-q")
        nested = self.repo / "nested"
        nested.mkdir()
        data, code = doctor.diagnose(str(nested))
        self.assertEqual(code, 0)
        self.assertTrue(data["repository"]["detached"])
        self.assertEqual(data["repository"]["root"], str(self.repo))
        self.assertEqual(len(data["repository"]["head"]), 40)

    def test_dirty_untracked_and_index_unchanged_without_fsmonitor(self):
        self.commit()
        marker = self.root / "must not run"
        monitor = self.root / "fsmonitor.sh"
        monitor.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        monitor.chmod(0o700)
        self.git("config", "core.fsmonitor", str(monitor))
        self.git("config", "core.untrackedCache", "true")
        (self.repo / "tracked.txt").write_text("changed\n")
        (self.repo / "untracked.txt").write_text("new\n")
        index = self.repo / ".git/index"
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        data, code = doctor.diagnose(str(self.repo))
        self.assertEqual(code, 0)
        self.assertEqual(data["worktree_status"]["tracked_changes"], 1)
        self.assertEqual(data["worktree_status"]["untracked"], 1)
        self.assertTrue(data["worktree_status"]["dirty"])
        self.assertFalse(marker.exists())
        self.assertEqual(before, (index.read_bytes(), index.stat().st_mtime_ns))

    def test_linked_worktree_paths(self):
        self.commit()
        linked = self.root / "linked worktree"
        self.git("worktree", "add", "-qb", "linked", str(linked))
        data, code = doctor.diagnose(str(linked))
        self.assertEqual(code, 0)
        self.assertEqual(data["repository"]["worktree"], str(linked))
        self.assertEqual(data["repository"]["common_dir"], str(self.repo / ".git"))
        self.assertNotEqual(data["repository"]["git_dir"], data["repository"]["common_dir"])

    def test_conflict(self):
        self.commit()
        self.git("checkout", "-qb", "other")
        (self.repo / "tracked.txt").write_text("other\n")
        self.git("commit", "-qam", "Other fixture")
        self.git("checkout", "-q", "main")
        (self.repo / "tracked.txt").write_text("main\n")
        self.git("commit", "-qam", "Main fixture")
        self.git("merge", "other", check=False)
        data, code = doctor.diagnose(str(self.repo))
        self.assertEqual(code, 0)
        self.assertTrue(data["worktree_status"]["conflict"])
        self.assertEqual(data["worktree_status"]["conflicts"], 1)

    def test_missing_executable_and_invalid_locations(self):
        data, code = doctor.diagnose(str(self.repo), ["not-an-installed-command-987654"])
        self.assertEqual(code, 2)
        self.assertIn("missing_required_executable", data["errors"])
        for path in (self.root / "absent", self.root):
            data, code = doctor.diagnose(str(path))
            self.assertEqual(code, 2)
            self.assertEqual(data["status"], "error")

    def test_package_names_only_and_environment_presence(self):
        secret = "DO_NOT_EXPOSE_SCRIPT_COMMAND_OR_ENV_VALUE"
        (self.repo / "package.json").write_text(json.dumps({"scripts": {"test": secret, "build": secret}, "packageManager": "pnpm@9"}))
        (self.repo / "pnpm-lock.yaml").touch()
        (self.repo / ".envrc").write_text(secret)
        (self.repo / "flake.nix").touch()
        with patch.dict(os.environ, {"IN_NIX_SHELL": secret, "DIRENV_DIR": secret}):
            data, code = doctor.diagnose(str(self.repo))
        self.assertEqual(code, 0)
        self.assertEqual(data["package"]["scripts"], ["build", "test"])
        self.assertEqual(data["package_manager_clues"], ["pnpm"])
        self.assertTrue(data["dev_environment"]["envrc"])
        self.assertTrue(data["dev_environment"]["in_nix_shell"])
        self.assertTrue(data["dev_environment"]["direnv_present"])
        self.assertNotIn(secret, json.dumps(data))

    def test_malformed_oversized_and_symlink_package(self):
        package = self.repo / "package.json"
        for content, status in [("{bad", "unreadable_or_invalid"), (" " * (doctor.PACKAGE_MAX_BYTES + 1), "oversized")]:
            package.write_text(content)
            data, code = doctor.diagnose(str(self.repo))
            self.assertEqual(code, 2)
            self.assertEqual(data["package"]["status"], status)
        package.unlink()
        package.symlink_to(self.root / "unrelated-secret")
        data, code = doctor.diagnose(str(self.repo))
        self.assertEqual(code, 2)
        self.assertEqual(data["package"]["status"], "unreadable_or_invalid")

    def test_script_names_and_status_are_capped(self):
        (self.repo / "package.json").write_text(json.dumps({"scripts": {f"script-{i}": "do not show" for i in range(120)}}))
        for i in range(15):
            (self.repo / f"file-{i:02d}").touch()
        with patch.object(doctor, "STATUS_MAX_ENTRIES", 3):
            data, code = doctor.diagnose(str(self.repo))
        self.assertEqual(code, 2)
        self.assertEqual(len(data["package"]["scripts"]), 100)
        self.assertEqual(data["package"]["scripts_omitted"], 20)
        self.assertTrue(data["worktree_status"]["partial"])
        self.assertEqual(data["worktree_status"]["entries"], 3)

    def test_byte_capped_status_has_only_complete_records(self):
        for i in range(30):
            (self.repo / f"long-file-name-{i:02d}").touch()
        with patch.object(doctor, "STATUS_MAX_BYTES", 80):
            data, code = doctor.diagnose(str(self.repo))
        self.assertEqual(code, 2)
        self.assertTrue(data["worktree_status"]["partial"])
        self.assertLessEqual(data["worktree_status"]["untracked"], 4)

    def test_inherited_git_directory_does_not_redirect_probe(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        self.git("init", "-q", cwd=foreign)
        with patch.dict(os.environ, {"GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign)}):
            data, code = doctor.diagnose(str(self.repo))
        self.assertEqual(code, 0)
        self.assertEqual(data["repository"]["root"], str(self.repo))

    def test_bare_repository_is_not_a_worktree(self):
        bare = self.root / "bare"
        self.git("init", "--bare", "-q", str(bare))
        data, code = doctor.diagnose(str(bare))
        self.assertEqual(code, 2)
        self.assertIn("not_git_worktree", data["errors"])

    def test_git_subprocess_deadline_and_output_caps(self):
        fake = self.root / "fake git"
        fake.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
        fake.chmod(0o700)
        start = time.monotonic()
        result = doctor._git(str(fake), self.repo, [], start + 0.05)
        self.assertTrue(result["partial"])
        self.assertEqual(result["reason"], "timeout")
        self.assertLess(time.monotonic() - start, 2)
        fake.write_text(f"#!{sys.executable}\nimport sys,time\nsys.stdout.write('x'*100000)\nsys.stdout.flush()\ntime.sleep(30)\n")
        result = doctor._git(str(fake), self.repo, [], time.monotonic() + 2, cap=32)
        self.assertTrue(result["partial"])
        self.assertEqual(result["reason"], "output_limit")
        self.assertEqual(len(result["output"]), 32)


if __name__ == "__main__":
    unittest.main()
