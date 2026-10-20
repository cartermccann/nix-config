#!/usr/bin/env python3
"""Bounded read-only Claude JSONL tool telemetry, exporting aggregates only."""
import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import time

UTC = dt.timezone.utc
PATTERNS = [
    ('test_or_build_failure', r'\btests? failed\b|\bFAIL(?:ED)?\b[^\n]{0,80}(?:test|spec)|\bbuild failed\b|error TS\d+|\bassertionerror\b'),
    ('search_no_match', r'^\s*Exit code:?\s*1\s*$'),
    ('rate_limit', r'RATE_LIMIT_EXCEEDED|rate.limit|too many requests|\b429\b'),
    ('authentication', r'unauthorized|unauthenticated|invalid.api.key|token.expired|\b401\b'),
    ('permission', r'permission denied|not permitted|forbidden|\b403\b'),
    ('timeout', r'timed? ?out|timeout|deadline exceeded'),
    ('network', r'ECONNREFUSED|ECONNRESET|ENOTFOUND|fetch failed|network error|socket hang up'),
    ('missing_command_or_file', r'command not found|not found in PATH|No such file or directory'),
    ('invalid_tool_or_arguments', r'unknown tool|invalid.arguments|invalid.params|unsupported.*(?:tool|method|operation)|unexpected keyword|not a function|is not defined|unrecognized (?:key|argument)|validation error'),
    ('python_error', r'Traceback \(most recent call last\)|SyntaxError|TypeError|AttributeError|ModuleNotFoundError|IndexError'),
    ('browser_unavailable', r'browser.*(?:unavailable|not available|not connected)|no.*browser.*connected|page.*closed|target.*closed'),
    ('patch_mismatch', r'patch.*(?:failed|invalid)|Failed to find expected lines|Invalid Context'),
    ('cancelled_or_interrupted', r'interrupted by (?:the )?user|user (?:cancelled|canceled)|tool use (?:cancelled|canceled)'),
]
DIAGNOSTICS = {
    'rate_limit': 'Respect provider backoff and reduce concurrent calls.',
    'authentication': 'Check the affected integration login without exporting credentials.',
    'permission': 'Check tool permissions and filesystem access for the intended operation.',
    'timeout': 'Inspect the operation timeout and workload before retrying.',
    'network': 'Check the affected provider connection and network state.',
    'missing_command_or_file': 'Verify the executable or path in the project environment.',
    'invalid_tool_or_arguments': 'Validate tool availability and argument schema before invoking.',
    'python_error': 'Inspect the failing script locally; this is not proof of provider outage.',
    'browser_unavailable': 'Verify the selected browser or page remains connected.',
    'patch_mismatch': 'Reread the current file before applying the patch.',
    'test_or_build_failure': 'Inspect the failing test/build locally; do not count as infrastructure outage.',
    'search_no_match': 'A search with no matches can intentionally return exit status 1.',
    'unclassified': 'Inspect the original call locally; no arbitrary output is exported.',
}


def timestamp(value):
    try:
        if not isinstance(value, str):
            return None
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def safe_name(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}', value) else 'unknown'


def text_content(value):
    # Local classification only. Never include this value in the report.
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(b.get('text', '') for b in value if isinstance(b, dict) and isinstance(b.get('text'), str))
    return ''


def result_info(block, metadata, tool_name=None):
    metadata = metadata if isinstance(metadata, dict) else {}
    content = text_content(block.get('content'))
    combined = '\n'.join([content] + [metadata[k] for k in ('stdout', 'stderr') if isinstance(metadata.get(k), str)])
    explicit = block.get('is_error')
    is_shell = tool_name == 'Bash'
    code = next((metadata[k] for k in ('exitCode', 'exit_code') if is_shell and type(metadata.get(k)) is int), None)
    if code is None and is_shell and explicit is not False:
        match = re.search(r'(?im)^\s*(?:Exit code:?|Process exited with code)\s*(-?\d+)\s*$', content)
        code = int(match[1]) if match else None
    interrupted = metadata.get('interrupted') is True
    if code is not None and code != 0:
        outcome = 'failed'
    elif explicit is False:
        outcome = 'succeeded'
    elif interrupted:
        outcome = 'interrupted'
    elif explicit is True or metadata.get('success') is False:
        outcome = 'failed'
    elif code == 0 or metadata.get('success') is True:
        outcome = 'succeeded'
    else:
        outcome = 'unknown'
    category = None
    if outcome == 'failed':
        for label, pattern in PATTERNS:
            if re.search(pattern, combined, re.I | re.M):
                # Exit 1 alone is not proof that the command was a search.
                if label == 'search_no_match':
                    continue
                category = label
                break
        category = category or 'unclassified'
    return {'outcome': outcome, 'category': category, 'exit_code': code, 'has_explicit_flag': type(explicit) is bool}


def enumerate_files(root, since, max_depth, max_files, max_bytes, max_entries, deadline):
    selected = []
    coverage = collections.Counter()
    stopped = False
    stack = [(root, 0)]
    while stack and not stopped:
        path, depth = stack.pop()
        try:
            with os.scandir(path) as scan:
                entries = []
                while coverage['entries_examined'] < max_entries:
                    if time.monotonic() > deadline:
                        coverage['enumeration_cap_hit'] += 1
                        stopped = True
                        break
                    try:
                        entry = next(scan)
                    except StopIteration:
                        break
                    entries.append(entry)
                    coverage['entries_examined'] += 1
                else:
                    coverage['enumeration_cap_hit'] += 1
                    stopped = True
                entries.sort(key=lambda e: e.name)
        except OSError:
            coverage['unreadable_directories'] += 1
            continue
        for entry in entries:
            if time.monotonic() > deadline:
                coverage['enumeration_cap_hit'] += 1
                stopped = True
                break
            if entry.is_symlink():
                coverage['symlinks_skipped'] += 1
                continue
            if entry.is_dir(follow_symlinks=False):
                if depth < max_depth:
                    stack.append((Path(entry.path), depth + 1))
                else:
                    coverage['directories_beyond_depth'] += 1
                continue
            if not entry.name.endswith('.jsonl'):
                continue
            st = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode):
                continue
            coverage['jsonl_files_enumerated'] += 1
            if st.st_mtime < since.timestamp():
                coverage['older_mtime_files_excluded'] += 1
                continue
            coverage['eligible_files'] += 1
            coverage['eligible_bytes'] += st.st_size
            selected.append((st.st_mtime, Path(entry.path), st.st_size))
    chosen = []
    size = 0
    for mtime, path, amount in sorted(selected, reverse=True):
        if len(chosen) >= max_files or size + amount > max_bytes:
            coverage['budget_skipped_files'] += 1
            coverage['budget_skipped_bytes'] += amount
            continue
        chosen.append((path, amount))
        size += amount
    coverage['selected_files'] = len(chosen)
    coverage['selected_bytes_at_enumeration'] = size
    return chosen, coverage


def aggregate(rows):
    counts = collections.Counter(r['outcome'] for r in rows)
    intervals = sorted(r['interval_ms'] for r in rows if r['interval_ms'] is not None)
    known = counts['succeeded'] + counts['failed']
    return {
        'calls': len(rows), 'terminal_calls': len(rows) - counts['unresolved'],
        'succeeded': counts['succeeded'], 'failed': counts['failed'],
        'unknown_outcomes': counts['unknown'], 'unresolved': counts['unresolved'],
        'interrupted': counts['interrupted'], 'conflicting_outcomes': counts['conflicting'],
        'failure_percent_of_known_outcomes': round(100 * counts['failed'] / known, 2) if known else None,
        'observed_failure_percent_of_calls': round(100 * counts['failed'] / len(rows), 2) if rows else None,
        'message_interval_upperbound_count': len(intervals),
        'message_interval_upperbound_p50_ms': round(intervals[(len(intervals)-1)//2], 2) if intervals else None,
        'message_interval_upperbound_p95_ms': round(intervals[max(0, (len(intervals)*95+99)//100-1)], 2) if intervals else None,
    }


def audit(root, since, until, max_depth=5, max_files=500, max_bytes=1_000_000_000, max_entries=100_000, max_seconds=60, observed_until=None):
    observed_until = observed_until or until
    started = time.monotonic()
    deadline = started + max_seconds
    chosen, coverage = enumerate_files(root, since, max_depth, max_files, max_bytes, max_entries, deadline)
    calls, results = {}, collections.defaultdict(list)
    stats = collections.Counter()
    min_time = max_time = None
    for file_index, (path, size) in enumerate(chosen):
        if time.monotonic() > deadline:
            stats['read_time_cap_hit'] += 1
            break
        stats['files_opened'] += 1
        if 'subagents' in path.parts:
            stats['subagent_files_opened'] += 1
        try:
            with path.open('rb') as stream:
                for line_number, line in enumerate(stream, 1):
                    if stats['bytes_read'] + len(line) > max_bytes or time.monotonic() > deadline:
                        stats['read_cap_hit'] += 1
                        break
                    stats['bytes_read'] += len(line)
                    stats['lines_read'] += 1
                    # Skip non-message telemetry without interpreting its content.
                    if b'"tool_use"' not in line and b'"tool_result"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        stats['invalid_json_candidate_lines'] += 1
                        continue
                    when = timestamp(entry.get('timestamp'))
                    message = entry.get('message')
                    blocks = message.get('content') if isinstance(message, dict) else None
                    if not isinstance(blocks, list):
                        continue
                    if when is None or not since <= when <= observed_until:
                        stats['candidate_lines_outside_window_or_undated'] += 1
                        continue
                    for block_index, block in enumerate(blocks):
                        if not isinstance(block, dict) or block.get('type') not in ('tool_use', 'tool_result'):
                            continue
                        min_time = min(min_time, when) if min_time else when
                        max_time = max(max_time, when) if max_time else when
                        if block['type'] == 'tool_use':
                            if when > until:
                                continue
                            stats['raw_tool_use_blocks'] += 1
                            ident = block.get('id')
                            if not isinstance(ident, str) or not ident:
                                stats['calls_missing_id'] += 1
                                ident = ('missing', file_index, line_number, block_index)
                            name = safe_name(block.get('name'))
                            if ident in calls:
                                stats['duplicate_tool_use_blocks'] += 1
                                if calls[ident]['name'] != name:
                                    stats['duplicate_id_name_conflicts'] += 1
                                calls[ident]['when'] = min(calls[ident]['when'], when)
                            else:
                                calls[ident] = {'name': name, 'when': when}
                        else:
                            stats['raw_tool_result_blocks'] += 1
                            ident = block.get('tool_use_id')
                            if not isinstance(ident, str) or not ident:
                                stats['results_missing_id'] += 1
                                continue
                            info = result_info(block, entry.get('toolUseResult'))
                            # Keep only scalar alternate interpretation, resolving identity after all calls.
                            info['_shell_info'] = result_info(block, entry.get('toolUseResult'), 'Bash')
                            info['when'] = when
                            if results[ident]:
                                stats['duplicate_tool_result_blocks'] += 1
                            results[ident].append(info)
        except OSError:
            stats['unreadable_files'] += 1
            continue
        else:
            if 'read_cap_hit' not in stats:
                stats['files_fully_read'] += 1
    rows = []
    flags = collections.Counter()
    for ident, call in calls.items():
        matches = results.get(ident, [])
        if call['name'] == 'Bash':
            matches = [{**m['_shell_info'], 'when': m['when']} for m in matches]
        valid = [m for m in matches if m['when'] >= call['when']]
        if len(valid) < len(matches):
            flags['results_before_call_timestamp'] += len(matches) - len(valid)
        if not valid:
            rows.append({'name': call['name'], 'date': call['when'].date().isoformat(), 'outcome': 'unresolved', 'category': None, 'interval_ms': None})
            continue
        # Repeated copied/forked outcomes count once. Conflicts stay outside known outcomes.
        distinct = {m['outcome'] for m in valid}
        outcome = 'conflicting' if len(distinct) > 1 else valid[0]['outcome']
        first = min(valid, key=lambda m: m['when'])
        if any(m['has_explicit_flag'] for m in valid):
            flags['calls_with_explicit_is_error_flag'] += 1
        if any(m['exit_code'] is not None for m in valid):
            flags['calls_with_numeric_exit_status'] += 1
        categories = {m['category'] for m in valid if m['category']}
        category = next(iter(categories)) if len(categories) == 1 else ('mixed_classification' if categories else None)
        rows.append({'name': call['name'], 'date': call['when'].date().isoformat(), 'outcome': outcome, 'category': category, 'interval_ms': (first['when']-call['when']).total_seconds()*1000})
    flags['orphan_result_ids'] = len(set(results)-set(calls))
    stats.update(flags)
    by_tool, by_day = collections.defaultdict(list), collections.defaultdict(list)
    for row in rows:
        by_tool[row['name']].append(row)
        by_day[row['date']].append(row)
    failures = collections.Counter(r['category'] for r in rows if r['outcome'] == 'failed')
    return {
        'schema_version': 1, 'source': 'claude_native_only',
        'window': {'since': since.isoformat(), 'until': until.isoformat(), 'inclusive': True},
        'limits': {'max_depth': max_depth, 'max_files': max_files, 'max_bytes': max_bytes, 'max_entries': max_entries, 'max_seconds': max_seconds},
        'coverage': {**{k: 0 for k in ('duplicate_tool_use_blocks', 'duplicate_tool_result_blocks', 'calls_missing_id', 'results_missing_id', 'duplicate_id_name_conflicts', 'budget_skipped_files', 'budget_skipped_bytes', 'read_cap_hit', 'read_time_cap_hit', 'enumeration_cap_hit')}, **dict(coverage), **dict(stats), 'first_tool_timestamp': min_time.isoformat() if min_time else None, 'last_tool_timestamp': max_time.isoformat() if max_time else None, 'audit_elapsed_seconds': round(time.monotonic()-started, 3)},
        'summary': aggregate(rows),
        'tools': sorted([{'tool': n, **aggregate(v)} for n, v in by_tool.items()], key=lambda r: (-r['failed'], -r['calls'], r['tool'])),
        'daily': [{'date': d, **aggregate(v)} for d, v in sorted(by_day.items())],
        'failure_categories': dict(failures),
        'fixed_diagnostics': [{'category': k, 'count': v, 'guidance': DIAGNOSTICS.get(k, 'Inspect original evidence locally.')} for k, v in failures.most_common()],
        'limitations': [
            'Native-only sample. Do not add to T3 or other native totals; cross-source overlap is unknown.',
            'File selection uses recent mtime, then record timestamps. Old active sessions are included if recently modified. Older-mtime files are excluded.',
            'Global tool-use ID deduplication removes copied/forked repeats. Missing IDs count independently; conflicting outcomes remain separate.',
            'Omission of optional is_error is normal protocol usage, not evidence of corrupt logging. Absent is_error and absent explicit exit/success indicators produce conservative unknown outcomes, not presumed success. Text exit-code detection applies only to Bash; explicit is_error=false takes precedence over text. Only structured nonzero Bash exit codes override an explicit success flag.',
            'Tool results before the call, results outside the window, and missing results do not establish a terminal outcome.',
            'Timing is message timestamp difference, an upper-bound proxy, not measured execution duration. Parallel calls overlap; do not sum intervals.',
            'Failure labels are local fixed-pattern classifications of explicit failures, not verified root causes. Tests, missing files, and deliberate nonzero exits are not infrastructure outage evidence.',
            'No arguments, commands, project paths, raw results, credentials, individual tool IDs, or session IDs are exported.',
        ],
    }


def notes(report):
    c, s = report['coverage'], report['summary']
    lines = ['# Claude native tool telemetry', '', f"Window: {report['window']['since']} through {report['window']['until']}.", '',
             f"Read {c.get('files_fully_read', 0):,} complete files ({c.get('subagent_files_opened', 0):,} subagent files), {c.get('bytes_read', 0):,} bytes. Enumerated {c.get('jsonl_files_enumerated', 0):,} JSONL files; {c.get('eligible_files', 0):,} had recent mtime. Old files can contain in-window records but are excluded by the stated mtime policy.", '',
             f"Deduplicated {c.get('raw_tool_use_blocks', 0):,} tool-use blocks to {s['calls']:,} calls. Outcomes: {s['succeeded']:,} succeeded, {s['failed']:,} failed, {s['unknown_outcomes']:,} unknown, {s['unresolved']:,} unresolved, {s['interrupted']:,} interrupted, {s['conflicting_outcomes']:,} conflicting. Known-outcome failure share: {s['failure_percent_of_known_outcomes']}%.", '',
             f"Duplicate call blocks: {c.get('duplicate_tool_use_blocks', 0)}. Duplicate result blocks: {c.get('duplicate_tool_result_blocks', 0)}. Missing IDs: {c.get('calls_missing_id', 0)} calls and {c.get('results_missing_id', 0)} results. Orphan result IDs: {c.get('orphan_result_ids', 0)}. Budget-skipped files: {c.get('budget_skipped_files', 0)}; cap hits: {sum(c.get(k, 0) for k in ('read_cap_hit', 'read_time_cap_hit', 'enumeration_cap_hit'))}.", '', 'Top 30 tools by explicit failures, then calls. Full per-tool counts are in the JSON.', '', '| Tool | Calls | Succeeded | Failed | Unknown | Unresolved |', '|---|---:|---:|---:|---:|---:|']
    for t in report['tools'][:30]:
        lines.append(f"| {t['tool']} | {t['calls']} | {t['succeeded']} | {t['failed']} | {t['unknown_outcomes']} | {t['unresolved']} |")
    lines += ['', '## Fixed diagnostic guidance', '']
    for item in report['fixed_diagnostics']:
        lines.append(f"- {item['category']}: {item['count']}. {item['guidance']}")
    lines += ['', '## Interpretation limits', ''] + ['- ' + v for v in report['limitations']]
    return '\n'.join(lines) + '\n'


def self_test():
    import tempfile
    import unittest
    class Tests(unittest.TestCase):
        def test_results(self):
            self.assertEqual(result_info({'is_error': False}, {})['outcome'], 'succeeded')
            self.assertEqual(result_info({}, {})['outcome'], 'unknown')
            self.assertEqual(result_info({'is_error': False, 'content': 'Exit code 2'}, {}, 'Read')['outcome'], 'succeeded')
            self.assertEqual(result_info({'is_error': False, 'content': 'Exit code 2'}, {}, 'Bash')['outcome'], 'succeeded')
            self.assertEqual(result_info({'is_error': False}, {'exitCode': 2}, 'Bash')['outcome'], 'failed')
            self.assertEqual(result_info({'content': 'Exit code 2'}, {}, 'Bash')['outcome'], 'failed')
            self.assertEqual(result_info({'content': 'Exit code 2'}, {}, 'Read')['outcome'], 'unknown')
            self.assertEqual(result_info({'is_error': True, 'content': 'Tests failed'}, {})['category'], 'test_or_build_failure')
            self.assertEqual(result_info({'is_error': True}, {'interrupted': True})['outcome'], 'interrupted')
            self.assertEqual(result_info({}, {'code': 200})['outcome'], 'unknown')
            self.assertIsNone(timestamp('2026-10-20T00:00:00'))
        def test_dedup_and_window(self):
            since = timestamp('2026-10-13T16:00:00Z'); until = timestamp('2026-10-20T16:00:00Z')
            def line(t, b):
                return json.dumps({'timestamp': t, 'message': {'content': [b]}}) + '\n'
            content = line('2026-10-14T00:00:00Z', {'type': 'tool_use', 'id': 'a', 'name': 'Bash', 'input': {'command': 'SECRET'}})
            content += line('2026-10-14T00:00:01Z', {'type': 'tool_result', 'tool_use_id': 'a', 'is_error': False, 'content': 'SECRET'})
            content += line('2026-10-14T00:01:00Z', {'type': 'tool_use', 'id': 'b', 'name': 'Read'})
            content += line('2026-10-21T00:00:01Z', {'type': 'tool_result', 'tool_use_id': 'b', 'is_error': False})
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); (p/'one.jsonl').write_text(content); (p/'two.jsonl').write_text(content)
                os.utime(p/'one.jsonl', (until.timestamp(), until.timestamp())); os.utime(p/'two.jsonl', (until.timestamp(), until.timestamp()))
                report = audit(p, since, until)
                self.assertEqual(report['summary']['calls'], 2)
                self.assertEqual(report['summary']['succeeded'], 1)
                self.assertEqual(report['summary']['unresolved'], 1)
                self.assertEqual(report['summary']['message_interval_upperbound_p50_ms'], 1000)
                self.assertNotIn('SECRET', json.dumps(report))
                limited = audit(p, since, until, max_files=1)
                self.assertEqual(limited['coverage']['budget_skipped_files'], 1)
        def test_read_exit_code_sentinel_and_out_of_order_shell(self):
            since = timestamp('2026-10-13T16:00:00Z'); until = timestamp('2026-10-20T16:00:00Z')
            entries = [
                ('2026-10-14T00:00:01Z', {'type': 'tool_result', 'tool_use_id': 'read', 'content': 'Exit code 1'}),
                ('2026-10-14T00:00:00Z', {'type': 'tool_use', 'id': 'read', 'name': 'Read'}),
                ('2026-10-14T00:00:01Z', {'type': 'tool_result', 'tool_use_id': 'shell', 'content': 'Exit code 1'}),
                ('2026-10-14T00:00:00Z', {'type': 'tool_use', 'id': 'shell', 'name': 'Bash'}),
                ('2026-10-14T00:00:00Z', {'type': 'tool_use', 'id': 'read_ok', 'name': 'Read'}),
                ('2026-10-14T00:00:01Z', {'type': 'tool_result', 'tool_use_id': 'read_ok', 'is_error': False, 'content': 'Exit code 1'}),
            ]
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); target = p/'sample.jsonl'
                target.write_text('\n'.join(json.dumps({'timestamp': t, 'message': {'content': [b]}}) for t, b in entries))
                os.utime(target, (until.timestamp(), until.timestamp()))
                r = audit(p, since, until)
                self.assertEqual(r['summary']['calls'], 3)
                self.assertEqual(r['summary']['failed'], 1)
                self.assertEqual(r['summary']['unknown_outcomes'], 1)
                self.assertEqual(r['summary']['succeeded'], 1)
        def test_same_line_missing_ids(self):
            since = timestamp('2026-10-13T16:00:00Z'); until = timestamp('2026-10-20T16:00:00Z')
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); target = p/'sample.jsonl'
                target.write_text(json.dumps({'timestamp': '2026-10-15T00:00:00Z', 'message': {'content': [{'type': 'tool_use', 'name': 'Read'}, {'type': 'tool_use', 'name': 'Edit'}]}}))
                os.utime(target, (until.timestamp(), until.timestamp()))
                r = audit(p, since, until)
                self.assertEqual(r['coverage']['calls_missing_id'], 2)
                self.assertEqual(r['summary']['calls'], 2)
                self.assertEqual(r['summary']['unresolved'], 2)
                self.assertEqual(r['coverage']['duplicate_tool_use_blocks'], 0)
        def test_scandir_consumption_bounded(self):
            from unittest.mock import patch
            from contextlib import nullcontext
            consumed = []
            class Entry:
                name = 'skip'
                def is_symlink(self):
                    return True
            def entries():
                for i in range(100):
                    consumed.append(i)
                    yield Entry()
            with patch.object(os, 'scandir', return_value=nullcontext(entries())):
                _, coverage = enumerate_files(Path('/unused'), timestamp('2026-10-13T16:00:00Z'), 5, 500, 1000000, 1, time.monotonic()+60)
            self.assertEqual(len(consumed), 1)
            self.assertEqual(coverage['entries_examined'], 1)
            self.assertEqual(coverage['enumeration_cap_hit'], 1)
        def test_cli_stdout_no_default_files(self):
            import subprocess
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                result = subprocess.run([os.sys.executable, str(Path(__file__).resolve()), '--root', str(p)], cwd=p, check=True, capture_output=True, text=True)
                report = json.loads(result.stdout)
                self.assertIn('tools', report)
                self.assertIn('failure_categories', report)
                self.assertEqual(list(p.iterdir()), [])
        def test_conflicting_missing_orphan_and_mtime(self):
            since = timestamp('2026-10-13T16:00:00Z'); until = timestamp('2026-10-20T16:00:00Z')
            blocks = [
                {'type': 'tool_use', 'id': 'a', 'name': 'Bash'},
                {'type': 'tool_result', 'tool_use_id': 'a', 'is_error': False},
                {'type': 'tool_result', 'tool_use_id': 'a', 'is_error': True},
                {'type': 'tool_use', 'name': 'Read'},
                {'type': 'tool_result', 'tool_use_id': 'orphan', 'is_error': False},
            ]
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); target = p/'sample.jsonl'
                target.write_text('\n'.join(json.dumps({'timestamp': '2026-10-15T00:00:00Z', 'message': {'content': [b]}}) for b in blocks))
                os.utime(target, (until.timestamp(), until.timestamp()))
                r = audit(p, since, until)
                self.assertEqual(r['summary']['conflicting_outcomes'], 1)
                self.assertEqual(r['summary']['unresolved'], 1)
                self.assertEqual(r['coverage']['orphan_result_ids'], 1)
                self.assertEqual(r['coverage']['calls_missing_id'], 1)
                os.utime(target, (since.timestamp()-1, since.timestamp()-1))
                self.assertEqual(audit(p, since, until)['summary']['calls'], 0)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


def atomic_write(path, content):
    import tempfile
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.' + path.name + '.', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path.home()/'.claude/projects')
    p.add_argument('--days', type=float, default=7)
    p.add_argument('--as-of', default='2026-10-20T16:00:00Z')
    p.add_argument('--since', default=None)
    p.add_argument('--until', default=None, help='Alias for --as-of')
    p.add_argument('--max-depth', type=int, default=5)
    p.add_argument('--max-files', type=int, default=500)
    p.add_argument('--max-bytes', type=int, default=1_000_000_000)
    p.add_argument('--max-entries', type=int, default=100_000)
    p.add_argument('--max-seconds', type=int, default=60)
    p.add_argument('--output', type=Path, default=None)
    p.add_argument('--notes', type=Path, default=None)
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args()
    if args.self_test:
        raise SystemExit(0 if self_test() else 1)
    until = timestamp(args.until or args.as_of)
    since = timestamp(args.since) if args.since else (until-dt.timedelta(days=args.days) if until else None)
    if args.days <= 0 or since is None or until is None or since > until:
        p.error('valid ordered timestamps required')
    if any(v <= 0 for v in (args.max_files, args.max_bytes, args.max_entries, args.max_seconds)) or args.max_depth < 0:
        p.error('positive budgets and nonnegative depth required')
    report = audit(args.root, since, until, args.max_depth, args.max_files, args.max_bytes, args.max_entries, args.max_seconds)
    if args.output is not None:
        atomic_write(args.output, json.dumps(report, indent=2) + '\n')
    if args.notes is not None:
        atomic_write(args.notes, notes(report))
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
