import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'adapters'))
import agent_health as health


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.state = self.home / 'state'
        self.now = health.parse_time('2026-10-20T17:32:00Z')
        self.value = 2

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, home, source, start, end, observed):
        return {'status': 'ok', 'issues': [], 'groups': {'native': {'summary': {'calls': self.value}}}}

    def run_collect(self, runner=None, sources=('codex',)):
        return health.collect(self.home, self.state, self.now, sources, runner or self.runner, observed_at=self.now)

    def test_idempotent_daily_replacement_and_late_update(self):
        result, code = self.run_collect()
        self.assertEqual(code, 0)
        first = health.report(self.state)
        self.run_collect()
        self.assertEqual(first, health.report(self.state))
        self.value = 3
        self.run_collect()
        updated = health.report(self.state)
        self.assertEqual(len(updated['snapshots']), 2)
        self.assertTrue(all(row['last_success']['groups']['native']['summary']['calls'] == 3 for row in updated['snapshots']))
        self.assertEqual(updated['snapshots'][0]['window'], {'start': '2026-10-18T00:00:00+00:00', 'end_exclusive': '2026-10-19T00:00:00+00:00'})

    def test_failure_and_partial_keep_last_success(self):
        self.run_collect()
        def fail(*args):
            return {'status': 'error', 'issues': ['source_timeout']}
        result, code = self.run_collect(fail)
        self.assertEqual(code, 2)
        for row in health.report(self.state)['snapshots']:
            self.assertEqual(row['latest_attempt']['status'], 'error')
            self.assertEqual(row['last_success']['groups']['native']['summary']['calls'], 2)
        def partial(*args):
            return {'status': 'partial', 'issues': ['read_cap_hit'], 'groups': {'native': {'summary': {'calls': 1}}}}
        self.run_collect(partial)
        for row in health.report(self.state)['snapshots']:
            self.assertEqual(row['latest_attempt']['status'], 'partial')
            self.assertEqual(row['last_success']['groups']['native']['summary']['calls'], 2)

    def test_retention_and_source_separation(self):
        self.run_collect(sources=('codex', 't3'))
        directory = self.state / 'snapshots'
        health.atomic_json(directory / '2026-07-21_codex.json', {})
        health.atomic_json(directory / '2026-07-22_codex.json', {'boundary': True})
        self.run_collect(sources=('codex', 't3'))
        self.assertFalse((directory / '2026-07-21_codex.json').exists())
        self.assertTrue((directory / '2026-07-22_codex.json').exists())
        self.assertEqual(len(list(directory.glob('*codex.json'))), 3)
        latest = health.read_json(self.state / 'latest.json')
        self.assertNotIn('total_calls', latest)
        self.assertEqual({row['source'] for row in latest['attempts']}, {'codex', 't3'})

    def test_permissions_lock_and_symlink_protection(self):
        self.run_collect()
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        for path in self.state.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        with (self.state / '.lock').open('r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, code = self.run_collect()
            self.assertEqual(code, 3)
            self.assertEqual(result['status'], 'busy')
            with self.assertRaises(BlockingIOError):
                health.report(self.state)
        target = self.home / 'sensitive'
        target.write_text('preserve')
        link = self.state / 'link.json'
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            health.atomic_json(link, {})
        self.assertEqual(target.read_text(), 'preserve')

    def test_missing_source_is_not_zero_success(self):
        start = self.now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
        result = health.scan_source(self.home, 'codex', start, start + dt.timedelta(days=1), self.now)
        self.assertEqual(result['status'], 'missing')
        self.assertNotIn('groups', result)
        collected, code = self.run_collect(lambda *args: result)
        self.assertEqual(code, 2)
        self.assertTrue(all(row['last_success'] is None for row in health.report(self.state)['snapshots']))

    def create_codex(self):
        directory = self.home / '.codex'
        directory.mkdir()
        conn = sqlite3.connect(directory / 'thread_history_1.sqlite')
        conn.execute('CREATE TABLE thread_items(thread_id TEXT,item_id TEXT,item_type TEXT,created_at_ms INTEGER,started_at_ms INTEGER,completed_at_ms INTEGER,item_json TEXT)')
        state = sqlite3.connect(directory / 'state_5.sqlite')
        state.execute('CREATE TABLE threads(id TEXT,source TEXT,originator TEXT)')
        state.execute('INSERT INTO threads VALUES (?,?,?)', ('PRIVATE_THREAD', 'PRIVATE_KIND', 'PRIVATE_ORIGIN'))
        state.commit()
        state.close()
        return conn

    def test_boundary_payload_and_untrusted_names_are_not_exported(self):
        conn = self.create_codex()
        start = health.parse_time('2026-10-19T00:00:00Z')
        end = start + dt.timedelta(days=1)
        payload = {'tool': 'SECRET_ALPHANUM', 'status': 'completed', 'arguments': {'password': 'PRIVATE_ARG'}, 'result': {'content': 'PRIVATE_RESULT'}}
        for index, when in enumerate((start, end)):
            ms = int(when.timestamp()*1000)
            conn.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?)', ('PRIVATE_THREAD', 'PRIVATE_ID'+str(index), 'mcpToolCall', ms, ms, ms, json.dumps(payload)))
        conn.commit()
        conn.close()
        result = health.scan_source(self.home, 'codex', start, end, self.now)
        self.assertEqual(result['groups']['native']['summary']['calls'], 1)
        serialized = json.dumps(result)
        for token in ('SECRET_ALPHANUM', 'PRIVATE_', str(self.home)):
            self.assertNotIn(token, serialized)
        self.assertEqual(result['groups']['native']['tools'], [])
        self.assertEqual(health.run_source(self.home, 'codex', start, end, self.now)['status'], 'ok')

    def test_claude_late_result_is_reconciled(self):
        directory = self.home / '.claude/projects/project'
        directory.mkdir(parents=True)
        path = directory / 'session.jsonl'
        call = {'timestamp': '2026-10-19T23:59:30Z', 'message': {'content': [{'type': 'tool_use', 'id': 'SECRET_ID', 'name': 'Read', 'input': {'x': 'SECRET_ARG'}}]}}
        result = {'timestamp': '2026-10-20T00:00:30Z', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'SECRET_ID', 'is_error': False, 'content': 'SECRET_OUTPUT'}]}}
        path.write_text(json.dumps(call)+'\n')
        start = health.parse_time('2026-10-19T00:00:00Z')
        first = health.scan_source(self.home, 'claude', start, start+dt.timedelta(days=1), self.now)
        self.assertEqual(first['groups']['native']['summary']['unresolved'], 1)
        path.write_text(json.dumps(call)+'\n'+json.dumps(result)+'\n')
        second = health.scan_source(self.home, 'claude', start, start+dt.timedelta(days=1), self.now)
        self.assertEqual(second['groups']['native']['summary']['succeeded'], 1)
        self.assertNotIn('SECRET_', json.dumps(second))

    def test_adapter_timeout_is_visible(self):
        start = self.now - dt.timedelta(days=1)
        result = health.run_source(self.home, 'codex', start, self.now, self.now, timeout=0.00001)
        self.assertEqual(result, {'status': 'error', 'issues': ['source_timeout']})

    def test_known_offenders_and_direct_helper_are_retained(self):
        rows = [{'tool': name, 'calls': 1, 'failed': 1} for name in (
            'codex_apps.gmail.search_emails', 't3-code.preview_click',
            'codex_apps.monday_com.get_updates', 'mcp__cua_repl__js',
            'agent-tools', 'SECRET_PLAUSIBLE_TOOL')]
        exported = health.safe_tools(rows)
        self.assertEqual(len(exported), 5)
        self.assertNotIn('SECRET', json.dumps(exported))
        import tool_health
        self.assertEqual(tool_health.command_name('agent-tools doctor --require rg'), 'agent-tools')
        self.assertEqual(tool_health.command_name('bash -c "agent-tools doctor"'), 'bash')

    def test_t3_late_update_remains_in_call_creation_window(self):
        import tool_health
        directory = self.home / '.t3/userdata'
        directory.mkdir(parents=True)
        conn = sqlite3.connect(directory / 'statev2.sqlite')
        conn.executescript('CREATE TABLE orchestration_v2_projection_provider_threads(provider_thread_id TEXT,payload_json TEXT); CREATE TABLE orchestration_v2_projection_turn_items(updated_at TEXT,type TEXT,payload_json TEXT); CREATE TABLE orchestration_v2_projection_runs(provider TEXT,status TEXT,requested_at TEXT);')
        row = {'id': 'PRIVATE_ID', 'type': 'dynamic_tool', 'toolName': 'codex_apps.gmail.search_emails', 'status': 'completed', 'output': {}, 'startedAt': '2026-10-19T23:59:00Z', 'updatedAt': '2026-10-20T00:01:00Z', 'completedAt': '2026-10-20T00:01:00Z'}
        conn.execute('INSERT INTO orchestration_v2_projection_turn_items VALUES (?,?,?)', (row['updatedAt'],row['type'],json.dumps(row)))
        conn.commit()
        conn.close()
        start = health.parse_time('2026-10-19T00:00:00Z')
        result = health.scan_source(self.home, 't3', start, start + dt.timedelta(days=1), self.now)
        self.assertEqual(result['groups']['t3_v2']['summary']['calls'], 1)
        self.assertEqual(result['groups']['t3_v2']['tools'][0]['tool'], 'codex_apps.gmail.search_emails')
        self.assertNotIn('PRIVATE_ID', json.dumps(result))

    def test_t3_table_timestamp_fallback_and_unassignable_time(self):
        directory = self.home / '.t3/userdata'
        directory.mkdir(parents=True)
        conn = sqlite3.connect(directory / 'statev2.sqlite')
        conn.executescript('CREATE TABLE orchestration_v2_projection_provider_threads(provider_thread_id TEXT,payload_json TEXT); CREATE TABLE orchestration_v2_projection_turn_items(updated_at TEXT,type TEXT,payload_json TEXT); CREATE TABLE orchestration_v2_projection_runs(provider TEXT,status TEXT,requested_at TEXT);')
        row = {'id': 'PRIVATE_ID', 'type': 'dynamic_tool', 'toolName': 'codex_apps.gmail.search_emails', 'status': 'completed', 'output': {}}
        conn.execute('INSERT INTO orchestration_v2_projection_turn_items VALUES (?,?,?)', ('2026-10-19T12:00:00Z', row['type'], json.dumps(row)))
        conn.commit()
        start = health.parse_time('2026-10-19T00:00:00Z')
        end = start + dt.timedelta(days=1)
        result = health.scan_source(self.home, 't3', start, end, self.now)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['groups']['t3_v2']['summary']['calls'], 1)
        # Invalid payload time still uses a valid table timestamp.
        row['startedAt'] = 'invalid'
        conn.execute('UPDATE orchestration_v2_projection_turn_items SET payload_json=?', (json.dumps(row),))
        conn.commit()
        self.assertEqual(health.scan_source(self.home, 't3', start, end, self.now)['groups']['t3_v2']['summary']['calls'], 1)
        # With no parseable timestamp, no healthy zero-call snapshot may escape.
        for invalid in (None, 'invalid'):
            conn.execute('UPDATE orchestration_v2_projection_turn_items SET updated_at=?', (invalid,))
            conn.commit()
            with self.assertRaises(RuntimeError):
                health.scan_source(self.home, 't3', start, end, self.now)
            self.assertEqual(health.run_source(self.home, 't3', start, end, self.now)['status'], 'error')
        conn.close()

    def test_all_fixed_adapter_categories_survive_sanitization(self):
        import claude_health
        import opencode_health
        import tool_health
        labels = {'unclassified', 'mixed_classification', 'search_no_match_candidate'}
        for adapter in (claude_health, opencode_health, tool_health):
            labels.update(label for label, _ in adapter.PATTERNS)
        self.assertFalse(labels - health.CATEGORIES)
        categories = {label: 1 for label in labels}
        self.assertEqual(health.safe_categories(categories), categories)
        self.assertEqual(health.safe_categories({'missing_command_or_file': 3}), {'missing_command_or_file': 3})

    def test_nonfinite_metrics_are_excluded(self):
        self.assertEqual(health.metrics({'calls': 1, 'max_ms': float('nan'), 'p95_ms': float('inf'), 'raw': 'SECRET'}), {'calls': 1})
        self.assertEqual(health.safe_categories({'SECRET_TOKEN': 2}), {'unclassified': 2})


if __name__ == '__main__':
    unittest.main()
