import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "delta-agent-guidance.py"
spec = importlib.util.spec_from_file_location("guidance", SCRIPT)
guidance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guidance)


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.source = self.home / "instruction.md"
        self.source.write_text("## Delta enrollment\n\nUse the approved CLI.\n")
        self.codex = self.home / ".codex/AGENTS.md"
        self.claude = self.home / ".claude/CLAUDE.md"
        self.codex.parent.mkdir()
        self.claude.parent.mkdir()

    def test_preserves_content_modes_and_repeats_without_changes(self):
        original = b"# Existing\r\nKeep my rules.\r\n"
        self.codex.write_bytes(original)
        self.codex.chmod(0o640)
        self.claude.write_text("Other instructions")
        self.assertEqual(guidance.install(self.home, self.source), 2)
        self.assertTrue(self.codex.read_bytes().startswith(original))
        self.assertEqual(stat.S_IMODE(self.codex.stat().st_mode), 0o640)
        snapshots = self.codex.read_bytes(), self.claude.read_bytes()
        self.assertEqual(guidance.install(self.home, self.source), 0)
        self.assertEqual((self.codex.read_bytes(), self.claude.read_bytes()), snapshots)
        self.assertEqual(self.codex.read_text().count(guidance.BEGIN), 1)

    def test_updates_only_managed_section(self):
        self.codex.write_text("prefix\n" + guidance.BEGIN + "\nold\n" + guidance.END + "\nsuffix\n")
        guidance.install(self.home, self.source)
        text = self.codex.read_text()
        self.assertTrue(text.startswith("prefix\n"))
        self.assertTrue(text.endswith("\nsuffix\n"))
        self.assertNotIn("\nold\n", text)

    def test_preflight_rejects_symlink_before_other_file_changes(self):
        self.codex.write_text("unchanged")
        other = self.home / "other"
        other.write_text("outside")
        self.claude.symlink_to(other)
        with self.assertRaises(OSError):
            guidance.install(self.home, self.source)
        self.assertEqual(self.codex.read_text(), "unchanged")
        self.assertEqual(other.read_text(), "outside")

    def test_malformed_markers_fifo_and_writable_directory_are_rejected(self):
        self.codex.write_text(guidance.BEGIN)
        with self.assertRaises(ValueError):
            guidance.install(self.home, self.source)
        self.codex.unlink()
        os.mkfifo(self.codex)
        with self.assertRaises(ValueError):
            guidance.install(self.home, self.source)
        self.codex.unlink()
        self.codex.parent.chmod(0o777)
        with self.assertRaises(ValueError):
            guidance.install(self.home, self.source)

    def test_concurrent_edit_is_preserved(self):
        self.codex.write_text("original")
        old_snapshot = guidance.snapshot
        calls = 0
        def concurrent(path, **kwargs):
            nonlocal calls
            if path == self.codex:
                calls += 1
                if calls == 2:
                    self.codex.write_text("concurrent edit")
            return old_snapshot(path, **kwargs)
        with patch.object(guidance, "snapshot", side_effect=concurrent), self.assertRaises(ValueError):
            guidance.install(self.home, self.source)
        self.assertEqual(self.codex.read_text(), "concurrent edit")

    def test_owned_readonly_file_is_not_replaced(self):
        self.codex.write_text("read-only instructions")
        self.codex.chmod(0o444)
        with self.assertRaises(ValueError):
            guidance.install(self.home, self.source)
        self.assertEqual(self.codex.read_text(), "read-only instructions")
        self.assertFalse(self.claude.exists())


if __name__ == "__main__":
    unittest.main()
