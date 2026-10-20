"""Core tests exercise real SQLite files, including independent processes."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from guard.core import Guard, Policy, is_gmail, validate_arguments


READ = "mcp__codex_apps__gmail_search_emails"
WRITE = "mcp__codex_apps__gmail_send_email"
MONDAY = "mcp__codex_apps__monday_com_get_updates"


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _process_reserve(path, start, output, identity):
    start.wait(5)
    result = Guard(path).reserve(READ, identity, identity, {"link_id": "account", "query": "in:inbox"})
    output.put(result)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private" / "state.sqlite3"
        self.clock = Clock()
        self.guard = Guard(self.path, clock=self.clock, random_value=lambda: 0)

    def tearDown(self):
        self.temp.cleanup()

    def reserve(self, owner="a", tool=READ, account="account", **fields):
        return self.guard.reserve(tool, owner, owner, {"link_id": account, "query": "in:inbox", **fields})

    def finish(self, owner="a", tool=READ, **kwargs):
        return self.guard.finish(tool, owner, owner, **kwargs)

    def test_name_matching_uses_namespace_tokens(self):
        for tool in (READ, "gmail.read", "mcp.gmail-get", "codex_apps.gmail.search_emails"):
            self.assertTrue(is_gmail(tool))
        for tool in ("mygmail_read", "gmailish.read", "mcp__notgmail__read", "search"):
            self.assertFalse(is_gmail(tool))
        self.assertTrue(self.guard.reserve("search", "a", "a", {"query": "gmail"})["allowed"])
        self.assertFalse(self.path.exists())

    def test_spacing_is_global_even_after_release(self):
        first = self.reserve()
        self.assertTrue(first["allowed"])
        self.assertEqual(self.finish(outcome="success", lease_id=first["lease_id"])["reason"], "released")
        self.assertEqual(self.reserve("b", account="other")["reason"], "gmail_spacing")
        self.clock.now += 0.75
        self.assertTrue(self.reserve("b", account="other")["allowed"])

    def test_independent_instances_share_lease(self):
        self.assertTrue(self.reserve()["allowed"])
        other = Guard(self.path, clock=self.clock)
        result = other.reserve(WRITE, "b", "b", {"link_id": "other"})
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "gmail_in_flight")
        self.assertEqual(result["retry_after_seconds"], 120)

    def test_real_processes_cannot_reserve_simultaneously(self):
        ctx = multiprocessing.get_context("fork")
        start, output = ctx.Event(), ctx.Queue()
        processes = [ctx.Process(target=_process_reserve, args=(str(self.path), start, output, str(i)))
                     for i in range(4)]
        for process in processes:
            process.start()
        start.set()
        results = [output.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sum(result["allowed"] for result in results), 1, results)

    def test_thread_instances_cannot_reserve_simultaneously(self):
        def attempt(identity):
            return Guard(self.path, clock=self.clock).reserve(READ, str(identity), str(identity), {"link_id": "a", "query": "in:inbox"})
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(attempt, range(8)))
        self.assertEqual(sum(result["allowed"] for result in results), 1, results)

    def test_duplicate_owner_is_not_allowed_to_execute_twice(self):
        self.assertTrue(self.reserve()["allowed"])
        self.assertFalse(self.reserve()["allowed"])

    def test_different_owner_cannot_release_lease(self):
        first = self.reserve()
        self.assertEqual(self.finish("b", outcome="success", lease_id=first["lease_id"])["reason"], "lease_mismatch")
        self.assertEqual(self.finish(outcome="success", lease_id="wrong")["reason"], "lease_mismatch")
        self.assertFalse(self.reserve("b")["allowed"])
        self.assertTrue(self.finish(outcome="success", lease_id=first["lease_id"])["allowed"])

    def test_lease_expires_and_late_owner_cannot_release_new_lease(self):
        first = self.reserve()
        self.clock.now += 120
        second = self.reserve("b")
        self.assertTrue(second["allowed"])
        self.assertNotEqual(first["lease_id"], second["lease_id"])
        self.assertEqual(self.finish(outcome="success", lease_id=first["lease_id"])["reason"], "lease_mismatch")
        self.assertFalse(self.reserve("c")["allowed"])

    def test_rate_limit_shared_globally_and_survives_restart(self):
        first = self.reserve()
        result = self.finish(outcome="error", lease_id=first["lease_id"], status_code=429)
        self.assertEqual(result["retry_after_seconds"], 60)
        other = Guard(self.path, clock=self.clock)
        result = other.reserve(READ, "other", "other", {"link_id": "different-account", "query": "in:inbox"})
        self.assertEqual(result["reason"], "rate_limit_cooldown")
        self.assertEqual(result["retry_after_seconds"], 60)
        self.clock.now += 60
        self.assertTrue(other.reserve(READ, "other", "other", {"link_id": "different-account", "query": "in:inbox"})["allowed"])

    def test_rate_limit_backoff_doubles_and_caps(self):
        expected = [60, 120, 240, 480, 900, 900]
        for delay in expected:
            self.assertTrue(self.reserve()["allowed"])
            result = self.finish(outcome="error", error={"code": "RATE_LIMIT_EXCEEDED"})
            self.assertEqual(result["retry_after_seconds"], delay)
            self.clock.now += delay

    def test_jitter_is_bounded_and_never_above_cap(self):
        self.guard = Guard(self.path, clock=self.clock, random_value=lambda: 99)
        for expected in (66, 132, 264, 528, 900):
            self.assertTrue(self.reserve()["allowed"])
            result = self.finish(outcome="error", status_code="429")
            self.assertAlmostEqual(result["retry_after_seconds"], expected)
            self.clock.now += expected

    def test_retry_after_seconds_is_never_shortened_by_cap(self):
        self.assertTrue(self.reserve()["allowed"])
        result = self.finish(outcome="error", status_code=429, retry_after="1800")
        self.assertEqual(result["retry_after_seconds"], 1800)

    def test_retry_after_http_date_is_respected(self):
        self.assertTrue(self.reserve()["allowed"])
        date = format_datetime(datetime.fromtimestamp(self.clock.now + 1_200, timezone.utc), usegmt=True)
        self.assertEqual(self.finish(outcome="error", status_code=429, retry_after=date)["retry_after_seconds"], 1_200)

    def test_invalid_retry_after_uses_backoff(self):
        for value in ("bad date", "NaN", "Infinity", -1, True, {}, None):
            with self.subTest(value=value):
                self.assertTrue(self.reserve()["allowed"])
                delay = self.finish(outcome="error", status_code=429, retry_after=value)["retry_after_seconds"]
                self.assertGreaterEqual(delay, 60)
                self.clock.now += delay

    def test_success_mentioning_rate_limit_is_not_classified(self):
        self.assertTrue(self.reserve()["allowed"])
        result = self.finish(outcome="success", error="429 RATE_LIMIT_EXCEEDED", status_code=429)
        self.assertEqual(result["reason"], "released")
        self.clock.now += 0.75
        self.assertTrue(self.reserve()["allowed"])

    def test_403_and_unrelated_errors_only_release(self):
        for error, status in (("Forbidden", 403), ("resource 1429 missing", 500),
                              ("NOT_RATE_LIMIT_EXCEEDED_YET", None), ({"body": "429"}, 500)):
            with self.subTest(error=error):
                self.assertTrue(self.reserve()["allowed"])
                self.assertEqual(self.finish(outcome="error", error=error, status_code=status)["reason"], "released")
                self.clock.now += 0.75

    def test_explicit_nested_rate_error_is_classified(self):
        self.assertTrue(self.reserve()["allowed"])
        self.assertEqual(self.finish(outcome="error", error={"error": {"code": "RATE_LIMIT_EXCEEDED"}})["reason"], "rate_limit_recorded")

    def test_explicit_outcome_required_and_invalid_does_not_release(self):
        self.assertTrue(self.reserve()["allowed"])
        self.assertEqual(self.finish(outcome="unknown")["reason"], "invalid_outcome")
        self.assertFalse(self.reserve("b")["allowed"])

    def test_writes_are_paced_but_never_replayed(self):
        decision = self.reserve(tool=WRITE, query=100, message_ids=[])
        self.assertTrue(decision["allowed"])
        self.assertFalse(self.reserve("b", tool=WRITE)["allowed"])
        result = self.finish(tool=WRITE, outcome="error", status_code=429)
        self.assertEqual(result["reason"], "rate_limit_recorded")
        self.assertFalse(self.reserve("b", tool=WRITE)["allowed"])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lease").fetchone()[0], 0)

    def test_bad_read_fields_are_blocked(self):
        bad = [{"link_id": ""}, {"link_id": 12}, {"query": []}, {"max_results": 0},
               {"max_results": -1}, {"max_results": True}, {"max_results": 1.5},
               {"message_id": " "}, {"message_ids": []}, {"message_ids": "id"},
               {"message_ids": [""]}, {"message_ids": ["id"] * 101}]
        for fields in bad:
            with self.subTest(fields=fields):
                self.assertEqual(self.reserve(**fields)["reason"], "invalid_arguments")
        self.assertFalse(self.path.exists())

    def test_current_read_requires_link_and_alias_is_validated(self):
        for tool in (READ, "codex_apps.gmail.search_emails"):
            self.assertFalse(self.guard.reserve(tool, "a", "a", {"query": "x"})["allowed"])
        self.assertTrue(validate_arguments("mcp__gmail__search", {"query": "x"}))
        self.assertTrue(validate_arguments(READ, {"link_id": "link", "query": "", "unknown": "allowed"}))

    def test_current_gmail_operations_require_their_inputs(self):
        required = {"read_email": ("message_id", "123"),
                    "batch_read_email": ("message_ids", ["123"])}
        for prefix in ("mcp__codex_apps__gmail_", "codex_apps.gmail."):
            for operation, (field, value) in required.items():
                tool = prefix + operation
                with self.subTest(tool=tool):
                    self.assertFalse(validate_arguments(tool, {"link_id": "a"}))
                    self.assertTrue(validate_arguments(tool, {"link_id": "a", field: value}))

    def test_label_only_gmail_search_does_not_require_query(self):
        for prefix in ("mcp__codex_apps__gmail_", "codex_apps.gmail."):
            for operation in ("search_emails", "search_email_ids"):
                tool = prefix + operation
                with self.subTest(tool=tool):
                    self.assertTrue(validate_arguments(tool, {"link_id": "a", "label_ids": ["INBOX"]}))
                    self.assertFalse(validate_arguments(tool, {"link_id": "a", "query": []}))

    def test_simple_manifest_enforces_required_types_ranges_and_unknown_keys(self):
        schema = {"required": ["query", "link_id"], "properties": {
            "query": {"type": "string", "enum": ["in:inbox"]},
            "link_id": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
            "message_ids": {"type": "array", "minItems": 1, "maxItems": 2},
        }, "additionalProperties": False}
        manifest = {READ: schema}
        base = {"link_id": "a", "query": "in:inbox"}
        self.assertTrue(validate_arguments(READ, base, manifest))
        for updates in ({"query": "other"}, {"max_results": 101}, {"max_results": True},
                        {"extra": 1}, {"message_ids": []}, {"message_ids": ["1", "2", "3"]}):
            self.assertFalse(validate_arguments(READ, {**base, **updates}, manifest))
        self.assertFalse(validate_arguments(READ, {"link_id": "a"}, manifest))
        self.assertTrue(validate_arguments(WRITE, {}, {WRITE: schema}))
        self.assertTrue(validate_arguments("unknown", {}, {"unknown": schema}))

    def test_current_monday_requires_singular_contract(self):
        good = {"link_id": "a", "objectId": "123", "objectType": "Item"}
        self.assertTrue(self.guard.reserve(MONDAY, "a", "a", good)["allowed"])
        self.assertFalse(self.path.exists())
        for fields in ({"objectIds": ["123"]}, {"objectId": ""}, {"objectId": 123},
                       {"objectType": "item"}, {"link_id": ""}, {"page": 0}, {"page": True},
                       {"limit": 101}, {"includeAssets": 1}, {"fromDate": "2026-01-01"}):
            self.assertFalse(self.guard.reserve(MONDAY, "a", "a", {**good, **fields})["allowed"], fields)
        self.assertTrue(validate_arguments("codex_apps.monday_com.get_updates", {
            **good, "objectType": "Board", "fromDate": "2026-01-01", "toDate": "2026-01-02", "limit": 100,
            "includeReplies": True}))
        self.assertTrue(validate_arguments("mcp__monday__get_updates", {"objectIds": ["123"]}))

    def test_thread_ids_allow_nullable_alternatives(self):
        single = "mcp__codex_apps__gmail_read_email_thread"
        batch = "mcp__codex_apps__gmail_batch_read_email_threads"
        self.assertTrue(validate_arguments(single, {"link_id": "a", "thread_id": "t", "message_id": None}))
        self.assertTrue(validate_arguments(batch, {"link_id": "a", "thread_ids": ["t"], "message_ids": None}))
        self.assertTrue(validate_arguments(batch, {"link_id": "a", "message_ids": [str(i) for i in range(101)]}))
        for tool, args in ((single, {"message_id": None}), (batch, {"thread_ids": [], "message_ids": None}),
                           (single, {"thread_id": 4}), (batch, {"thread_ids": [False]})):
            self.assertFalse(validate_arguments(tool, {"link_id": "a", **args}))

    def test_batch_mutations_are_not_subject_to_read_schemas(self):
        self.assertTrue(validate_arguments("mcp__codex_apps__gmail_batch_modify_email",
                                           {"message_ids": [str(i) for i in range(101)]}))
        self.assertTrue(self.guard.reserve(WRITE, 's', 'large-write', {'body': 'x' * 200_000})['allowed'])

    def test_monday_dates_are_parseable_ordered_and_timezone_normalized(self):
        base = {"link_id": "a", "objectId": "1", "objectType": "Board"}
        for start, end in (("", ""), ("invalid", "2026-01-01"),
                           ("2026-02-30", "2026-03-01"),
                           ("2026-01-02", "2026-01-01"),
                           ("2026-01-01T00:00:00Z", "2026-01-01T01:00:00+02:00")):
            with self.subTest(start=start, end=end):
                self.assertFalse(validate_arguments(MONDAY, {**base, "fromDate": start, "toDate": end}))
        for start, end in (("2026-01-01", "2026-01-01"),
                           ("2026-01-01T01:00:00+02:00", "2026-01-01T00:00:00Z"),
                           ("2026-01-01", "2026-01-01T01:00:00Z")):
            with self.subTest(start=start, end=end):
                self.assertTrue(validate_arguments(MONDAY, {**base, "fromDate": start, "toDate": end}))

    def test_payload_size_and_non_json_are_rejected_without_storage(self):
        for extra in ("x" * 65_537, float("nan"), object()):
            self.assertEqual(self.reserve(extra=extra)["reason"], "invalid_arguments")
        self.assertFalse(self.path.exists())

    def test_malformed_tool_and_manifest_fail_closed(self):
        for tool in (None, [], "", "x" * 4097):
            self.assertEqual(self.guard.reserve(tool, "a", "a", {})["reason"], "invalid_arguments")
        for schema in (None, "invalid", {"properties": []}, {"required": "query"}):
            guard = Guard(self.path, manifest={READ: schema})
            self.assertEqual(guard.reserve(READ, "a", "a", {"link_id": "a", "query": "in:inbox"})["reason"], "invalid_arguments")
        self.assertFalse(self.path.exists())

    def test_private_files_and_no_sensitive_storage(self):
        session, call = "SESSION_SENTINEL_32", "CALL_SENTINEL_21"
        arguments = {"link_id": "ACCOUNT_SENTINEL_77", "query": "QUERY_SENTINEL_51",
                     "message_ids": ["MESSAGE_SENTINEL_53"]}
        first = self.guard.reserve(READ, session, call, arguments)
        result = self.guard.finish(READ, session, call, outcome="error", lease_id=first["lease_id"],
                                  error="429 ERROR_SENTINEL_82")
        self.assertTrue(result["allowed"])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        disk = b"".join(file.read_bytes() for file in self.path.parent.iterdir() if file.is_file())
        output = json.dumps([first, result]).encode()
        for sentinel in (session, call, *arguments.values(), "ERROR_SENTINEL_82"):
            if isinstance(sentinel, list):
                sentinel = sentinel[0]
            self.assertNotIn(sentinel.encode(), disk)
            self.assertNotIn(sentinel.encode(), output)

    def test_corrupt_database_fails_closed_without_echoing_errors(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_bytes(b"CORRUPT_SECRET_SENTINEL")
        self.path.chmod(0o600)
        result = self.reserve()
        self.assertEqual(result["reason"], "state_unavailable")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_insecure_directory_and_file_fail_closed(self):
        self.path.parent.mkdir(mode=0o755)
        self.assertEqual(self.reserve()["reason"], "state_unavailable")
        self.path.parent.chmod(0o700)
        self.path.touch(mode=0o644)
        self.assertEqual(self.reserve()["reason"], "state_unavailable")

    def test_symlink_file_directory_and_journal_fail_closed(self):
        target = Path(self.temp.name) / "target"
        target.mkdir(mode=0o700)
        self.path.parent.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.reserve()["reason"], "state_unavailable")
        self.path.parent.unlink()
        self.path.parent.mkdir(mode=0o700)
        target_file = target / "database"
        target_file.touch(mode=0o600)
        self.path.symlink_to(target_file)
        self.assertEqual(self.reserve()["reason"], "state_unavailable")
        self.path.unlink()
        self.path.with_name(self.path.name + "-journal").symlink_to(target_file)
        self.assertEqual(self.reserve()["reason"], "state_unavailable")
        self.assertEqual(target_file.read_bytes(), b"")

    def test_custom_policy_and_clock_are_used(self):
        self.guard = Guard(self.path, policy=Policy(min_spacing_seconds=2, lease_seconds=10), clock=self.clock)
        self.assertTrue(self.reserve()["allowed"])
        self.assertEqual(self.reserve("b")["retry_after_seconds"], 10)
        self.finish(outcome="success")
        self.assertEqual(self.reserve("b")["retry_after_seconds"], 2)


if __name__ == "__main__":
    unittest.main()
