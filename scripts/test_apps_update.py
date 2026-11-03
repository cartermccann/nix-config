"""Shared updater: scope, downgrade protection, and preservation of user edits."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("apps_update", Path(__file__).with_name("apps-update.py"))
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)


class AppsUpdate(unittest.TestCase):
    def test_individual_update_dispatches_only_selected_app(self):
        with mock.patch.object(apps, "update_one") as update:
            self.assertEqual(apps.update(Path("/repo"), "cursor", "host", "user"), 0)
        update.assert_called_once_with(Path("/repo"), "cursor", "host", "user")

    def test_all_continues_after_failure_and_reports_failure(self):
        called = []

        def update(repo, app, host, user):
            called.append(app)
            if app == "t3":
                raise RuntimeError("package build failed")

        with mock.patch.object(apps, "update_one", side_effect=update):
            self.assertEqual(apps.update(Path("/repo"), "all", "host", "user"), 1)
        self.assertEqual(called, list(apps.APPS))

    def test_downgrade_rejected(self):
        with self.assertRaisesRegex(ValueError, "downgrade"):
            apps.newer("3.28.7", "3.27.22")
        self.assertFalse(apps.newer("3.28.7", "3.28.7"))

    def test_atomic_write_preserves_concurrent_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_bytes(b"user change")
            with self.assertRaisesRegex(ValueError, "concurrent edit"):
                apps.atomic_write(path, b"updated", b"original")
            self.assertEqual(path.read_bytes(), b"user change")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_candidate_failure_leaves_original_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            path = repo / apps.PINS["cursor"]
            path.parent.mkdir(parents=True)
            path.write_bytes(b"original")
            with mock.patch.object(apps, "run", return_value=""), \
                    mock.patch.object(apps, "candidate", side_effect=RuntimeError("download failed")):
                with self.assertRaisesRegex(RuntimeError, "download failed"):
                    apps.update_one(repo, "cursor", "host", "user")
            self.assertEqual(path.read_bytes(), b"original")

    def test_failed_post_write_validation_rolls_back_own_change(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            path = repo / apps.PINS["cursor"]
            path.parent.mkdir(parents=True)
            path.write_bytes(b"original")
            real_write = apps.atomic_write

            def write_then_fail(path, data, expected):
                real_write(path, data, expected)
                if data == b"new":
                    raise OSError("interrupted after rename")

            with mock.patch.object(apps, "run", return_value=""), \
                    mock.patch.object(apps, "candidate", return_value=b"new"), \
                    mock.patch.object(apps, "atomic_write", side_effect=write_then_fail):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    apps.update_one(repo, "cursor", "host", "user")
            self.assertEqual(path.read_bytes(), b"original")

    def test_dirty_pin_is_never_fetched_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            path = repo / apps.PINS["cursor"]
            path.parent.mkdir(parents=True)
            path.write_bytes(b"user edit")
            with mock.patch.object(apps, "run", return_value=" M pin"), \
                    mock.patch.object(apps, "candidate") as candidate:
                with self.assertRaisesRegex(ValueError, "review and commit or stash"):
                    apps.update_one(repo, "cursor", "host", "user")
            candidate.assert_not_called()

    def test_exact_prior_owned_dirty_pin_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            path = repo / apps.PINS["cursor"]
            path.parent.mkdir(parents=True)
            path.write_bytes(b"updater change")
            owned = {apps.PINS["cursor"]: {"sha256": apps.digest(b"updater change")}}
            with mock.patch.object(apps, "run", return_value=" M pin"), \
                    mock.patch.object(apps, "candidate", return_value=b"updater change") as candidate:
                self.assertFalse(apps.update_one(repo, "cursor", "host", "user", owned))
                candidate.assert_called_once_with(repo, "cursor", b"updater change")
                path.write_bytes(b"subsequent user edit")
                with self.assertRaisesRegex(ValueError, "local edits"):
                    apps.update_one(repo, "cursor", "host", "user", owned)
            self.assertEqual(candidate.call_count, 1)

    def test_owned_opencode_revalidates_after_flake_lock_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            path = repo / apps.PINS["opencode1"]
            path.parent.mkdir(parents=True)
            path.write_bytes(b"updater change")
            (repo / "flake.lock").write_bytes(b"old lock")
            record = apps.pin_record(repo, "opencode1")
            owned = {apps.PINS["opencode1"]: record}
            (repo / "flake.lock").write_bytes(b"new lock")
            with mock.patch.object(apps, "run", return_value=" M pin"), \
                    mock.patch.object(apps, "update_source_package", return_value=True) as update:
                self.assertTrue(apps.update_one(repo, "opencode1", "host", "user", owned))
            update.assert_called_once_with(repo, "opencode-update", "host", "user", record["sha256"])

    def test_owned_dirty_pin_can_advance_again_and_refresh_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            path = repo / apps.PINS["cursor"]
            path.parent.mkdir(parents=True)
            path.write_bytes(b"first updater change")
            (repo / "flake.lock").write_bytes(b"lock")
            ledger = repo / "owned.json"
            apps.save_owned(ledger, {apps.PINS["cursor"]: apps.pin_record(repo, "cursor")})
            with mock.patch.object(apps, "run", return_value=" M pin"), \
                    mock.patch.object(apps, "candidate", return_value=b"newer release"):
                self.assertEqual(apps.update(repo, "cursor", "host", "user", ledger), 0)
            self.assertEqual(path.read_bytes(), b"newer release")
            self.assertEqual(apps.json.loads(ledger.read_bytes())[apps.PINS["cursor"]],
                             apps.pin_record(repo, "cursor"))

    def test_granola_changes_release_hash_not_native_dependency(self):
        original = b'''let
  dep = fetchurl { hash = "sha512-native"; };
in {
  version = "7.722.0";
  src = fetchurl {
    url = "dynamic";
    hash = "sha256-old";
  };
}'''
        headers = "location: https://dr2v7l5emb758.cloudfront.net/7.726.2/Granola-7.726.2-mac-universal.dmg\r\n"
        with mock.patch.object(apps, "fetch", return_value=headers), \
                mock.patch.object(apps, "prefetch", return_value="sha256-new"):
            result = apps.candidate(Path("/repo"), "granola", original)
        self.assertIn(b"sha512-native", result)
        self.assertIn(b'    hash = "sha256-new";', result)
        self.assertIn(b'  version = "7.726.2";', result)


if __name__ == "__main__":
    unittest.main()
