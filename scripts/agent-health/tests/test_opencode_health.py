import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import contextlib
import io

from opencode_health import main, normalize, report, summarize


class OpenCodeHealthTests(unittest.TestCase):
    def test_cli_invalid_limits_fail_before_source_access(self):
        for flag, value in [('--days','-1'),('--days','0'),('--days','nan'),('--days','inf'),
                            ('--timeout','-1'),('--timeout','0'),('--timeout','nan'),('--timeout','inf'),
                            ('--max-rows','0'),('--max-rows','-1')]:
            with self.subTest(flag=flag, value=value), mock.patch('sys.argv', ['opencode_health', flag, value]), \
                 mock.patch('opencode_health.report') as read, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main()
                self.assertEqual(raised.exception.code, 2)
                read.assert_not_called()

    def test_native_cancellation_is_separate(self):
        row = normalize((0, 'call', 'task', 'error', 1, 2, None, 'Task cancelled', None))
        self.assertEqual(row['outcome'], 'cancelled')
        self.assertEqual(summarize([row])['terminal'], 0)

    def test_successful_output_is_not_regex_classified(self):
        row = normalize((0, 'call', 'read', 'completed', 1, 2, None, None, 'Error: example in documentation'))
        self.assertEqual(row['outcome'], 'success')

    def test_shell_nonzero_and_no_match_candidate(self):
        row = normalize((0, 'call', 'bash', 'completed', 1, 2, 1, None, '', 'rg test'))
        self.assertEqual(row['outcome'], 'failure')
        self.assertEqual(row['category'], 'search_no_match_candidate')

    def test_unknown_status_and_invalid_duration_are_preserved(self):
        row = normalize((0, 'call', 'read', 'future_status', 2, 1, None, None, None))
        self.assertEqual(row['outcome'], 'unknown')
        self.assertIsNone(row['duration'])
        self.assertIsNone(summarize([])['failure_percent'])

    def test_readonly_adapter_overlap_and_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('one.db', 'two.db')]
            for path in paths:
                with sqlite3.connect(path) as conn:
                    conn.execute('CREATE TABLE session(id TEXT)')
                    conn.execute('CREATE TABLE part(time_created INTEGER, session_id TEXT, data TEXT)')
                    data = {'type': 'tool', 'callID': 'same-call', 'tool': 'read',
                            'state': {'status': 'completed', 'time': {'start': 1792510000000, 'end': 1792510000010}}}
                    conn.execute('INSERT INTO part VALUES (?,?,?)', (1792510000000, 'same-session', json.dumps(data)))
            before = [p.read_bytes() for p in paths]
            result = report(paths, '2026-10-20T16:00:00Z')
            overlap = result['cross_database_overlap'][0]
            self.assertEqual(overlap['matching_call_ids'], 1)
            self.assertEqual(overlap['matching_session_call_ids'], 1)
            self.assertTrue(all(s['success'] == 1 for s in result['sources']))
            self.assertTrue(all(s['native_duration_p95_ms'] == 10 for s in result['sources']))
            self.assertEqual(before, [p.read_bytes() for p in paths])
            with self.assertRaisesRegex(RuntimeError, 'row cap'):
                report(paths, '2026-10-20T16:00:00Z', cap=0)

    def test_modern_schema_rows_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'modern.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE session(id TEXT)')
                conn.execute('CREATE TABLE part(time_created INTEGER, session_id TEXT, data TEXT)')
                conn.execute('CREATE TABLE session_message(time_created INTEGER)')
                conn.execute('INSERT INTO session_message VALUES (1792510000000)')
            with self.assertRaisesRegex(RuntimeError, 'Modern session_message'):
                report([path], '2026-10-20T16:00:00Z')

    def test_same_session_identity_is_counted_once(self):
        result = self.identity_fixture(['session', 'session'])
        source = result['sources'][0]
        self.assertEqual(source['calls'], 1)
        self.assertEqual(source['coverage']['duplicate_session_call_rows_removed'], 1)

    def test_same_call_id_in_distinct_sessions_remains_distinct(self):
        result = self.identity_fixture(['first-session', 'second-session'])
        source = result['sources'][0]
        self.assertEqual(source['calls'], 2)
        self.assertEqual(source['coverage']['duplicate_session_call_rows_removed'], 0)

    def test_conflicting_session_identity_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'Conflicting rows'):
            self.identity_fixture(['session', 'session'], ['completed', 'running'])

    def identity_fixture(self, sessions, statuses=None):
        statuses = statuses or ['completed'] * len(sessions)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'identity.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE session(id TEXT)')
                conn.execute('CREATE TABLE part(time_created INTEGER, session_id TEXT, data TEXT)')
                for session, status in zip(sessions, statuses):
                    data = {'type': 'tool', 'callID': 'same-call', 'tool': 'read',
                            'state': {'status': status, 'time': {'start': 1792510000000, 'end': 1792510000010}}}
                    conn.execute('INSERT INTO part VALUES (?,?,?)', (1792510000000, session, json.dumps(data)))
            return report([path], '2026-10-20T16:00:00Z')


if __name__ == '__main__':
    unittest.main()
