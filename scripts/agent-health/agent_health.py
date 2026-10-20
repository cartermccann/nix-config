#!/usr/bin/env python3
"""Private, bounded daily snapshots of independent local tool stores."""
import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

UTC = dt.timezone.utc
SOURCES = ('codex', 'codex_cliproxy', 'claude', 'opencode', 'opencode_stable', 't3')
SCHEMA = 1
# Export names only from this fixed vocabulary. Unknown/custom tools collapse to other.
TOOLS = set('agent-tools unknown shell rg grep git gh python python3 node npm pnpm bun nix bash fish cat sed curl ls mkdir jq find fd ssh timeout test true false head tail wc sleep Bash Read Write Edit MultiEdit Glob Grep WebFetch WebSearch TodoWrite Task Agent Skill ToolSearch ExitPlanMode EnterPlanMode NotebookEdit AskUserQuestion read write edit multiedit glob grep webfetch websearch todowrite task skill question patch apply_patch exec_command write_stdin functions.exec functions.wait commandExecution mcpToolCall fileChange collabAgentToolCall webSearch imageView command_execution dynamic_tool web_search file_change'.split())
# Explicit names observed in the prior local aggregate audit. Never wildcard MCP names.
TOOLS.update("""codex_apps.gmail.search_emails codex_apps.gmail.search_email_ids codex_apps.gmail.apply_labels_to_emails codex_apps.gmail.batch_read_email codex_apps.gmail.batch_modify_email codex_apps.gmail.list_labels codex_apps.gmail.get_profile codex_apps.gmail.read_email codex_apps.gmail.send_email codex_apps.gmail.batch_read_email_threads codex_apps.gmail.read_email_thread codex_apps.monday_com.get_updates codex_apps.monday_com.create_update codex_apps.monday_com.get_board_items_page codex_apps.monday_com.list_users_and_teams codex_apps.monday_com.get_type_details codex_apps.monday_com.get_board_info codex_apps.monday_com.all_api_read codex_apps.monday_com.update_items codex_apps.monday_com.get_graphql_schema codex_apps.monday_com.search codex_apps.monday_com.all_api_write codex_apps.monday_com.create_items cua_repl.js mcp__cua_repl__js mcp__claude_ai_Gmail__get_thread mcp__claude_ai_Gmail__search_threads""".split())
for action in 'preview_click preview_snapshot preview_evaluate preview_navigate preview_resize preview_type preview_open preview_press preview_status preview_wait_for html_preview t3_preview_close'.split():
    TOOLS.update(('t3-code.' + action, 'mcp__t3-code__' + action, 'mcp__t3_code__' + action))
for action in 'create_update create_item change_item_column_values move_item_to_group get_board_items_page list_users_and_teams get_board_info search get_user_context get_updates'.split():
    TOOLS.add('mcp__monday-api-mcp__' + action)
CATEGORIES = set('missing_command_or_file search_no_match cancelled_or_interrupted rate_limit authentication permission timeout network missing_command invalid_tool_or_arguments python_error browser_unavailable preview_client_failure device_unavailable patch_mismatch test_or_build_failure search_no_match_candidate unclassified cancelled_or_rejected file_not_found read_before_edit invalid_arguments mixed_classification explicit_error shell_nonzero unknown'.split())
METRICS = set('calls terminal terminal_calls success succeeded failed failure cancelled cancelled_or_interrupted pending_or_other unknown_or_pending unknown unknown_outcomes unresolved interrupted conflicting_outcomes native_timed timed_calls timed_terminal_calls completed_with_nonzero_shell_exit message_interval_upperbound_count failure_percent failure_percent_of_known_outcomes observed_failure_percent_of_calls p50_ms p95_ms p99_ms max_ms native_duration_p50_ms native_duration_p95_ms message_interval_upperbound_p50_ms message_interval_upperbound_p95_ms'.split())
COVERAGE = set('scanned_tool_rows unique_calls unmapped_origin_calls retained_part_rows malformed_parts_in_window missing_call_ids duplicate_call_ids_within_source duplicate_session_call_rows_removed entries_examined jsonl_files_enumerated older_mtime_files_excluded eligible_files eligible_bytes selected_files selected_bytes_at_enumeration files_opened files_fully_read subagent_files_opened bytes_read lines_read raw_tool_use_blocks raw_tool_result_blocks duplicate_tool_use_blocks duplicate_tool_result_blocks calls_missing_id results_missing_id duplicate_id_name_conflicts budget_skipped_files budget_skipped_bytes read_cap_hit read_time_cap_hit enumeration_cap_hit unreadable_files unreadable_directories directories_beyond_depth symlinks_skipped invalid_json_candidate_lines candidate_lines_outside_window_or_undated orphan_result_ids calls_with_explicit_is_error_flag calls_with_numeric_exit_status results_before_call_timestamp audit_elapsed_seconds'.split())
PARTIAL = set('budget_skipped_files read_cap_hit read_time_cap_hit enumeration_cap_hit unreadable_files unreadable_directories directories_beyond_depth invalid_json_candidate_lines malformed_parts_in_window duplicate_id_name_conflicts'.split())
LIMITATIONS = [
    'Independent sources may mirror the same calls. Never sum across sources or T3 projections.',
    'Recorded tool failures include expected nonzero exits and failing tests; these are not infrastructure outage rates.',
    'Tool breakdowns include only an explicit known-name allowlist; unlisted custom tools still contribute to source summaries.',
    'Helper adoption is visible only when agent-tools is the recorded leading executable; nested or wrapped invocations cannot be measured reliably.',
    'No success rate is inferred for unknown, pending, missing or unreadable outcomes.',
    'Latest two closed UTC days are replaced on collection; older late updates are not reconciled automatically.',
    'Codex and OpenCode use recorded native durations. Claude message intervals and T3 lifecycle intervals are proxies, not comparable execution latencies.',
    'Claude coverage is a bounded scan of recently modified project JSONL files; raw content is processed in memory only.',
    'T3 legacy windows use observation time. Native and T3 V2 windows use recorded call start or creation time.',
    'This collector measures tool outcomes, not reasoning quality, user satisfaction, or overall agent reliability.'
]


def parse_time(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return result.astimezone(UTC)


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def numbers(obj, keys):
    if not isinstance(obj, dict):
        raise ValueError('invalid aggregate shape')
    return {k: v for k, v in obj.items() if k in keys and (v is None or numeric(v))}


def metrics(obj):
    result = numbers(obj, METRICS)
    if 'calls' not in result or type(result['calls']) is not int:
        raise ValueError('missing aggregate count')
    return result


def safe_categories(obj):
    if not isinstance(obj, dict):
        raise ValueError('invalid category shape')
    result = {}
    for key, value in obj.items():
        if type(value) is int and value >= 0:
            name = key if key in CATEGORIES else 'unclassified'
            result[name] = result.get(name, 0) + value
    return result


def safe_tools(rows):
    # Unknown names are omitted rather than exporting a token disguised as a tool.
    output = []
    for row in rows:
        name = row.get('tool')
        if name in TOOLS:
            output.append({'tool': name, **metrics(row)})
            if len(output) == 128:
                break
    return output


def source_paths(home, source):
    return {
        'codex': [home / '.codex/thread_history_1.sqlite', home / '.codex/state_5.sqlite'],
        'codex_cliproxy': [home / '.codex_cliproxy/thread_history_1.sqlite', home / '.codex_cliproxy/state_5.sqlite'],
        'claude': [home / '.claude/projects'],
        'opencode': [home / '.local/share/opencode/opencode.db'],
        'opencode_stable': [home / '.local/share/opencode/opencode-stable.db'],
        't3': [home / '.t3/userdata/statev2.sqlite' if (home / '.t3/userdata/statev2.sqlite').is_file() else home / '.t3/userdata/state.sqlite'],
    }[source]


def scan_source(home, source, start, end, observed):
    """Runs in a child, exporting only a whitelist of aggregate fields."""
    if not all(path.exists() for path in source_paths(home, source)):
        return {'status': 'missing', 'issues': ['source_missing']}
    sys.path.insert(0, str(Path(__file__).parent / 'adapters'))
    import tool_health
    import codex_health
    import claude_health
    import opencode_health
    # Existing adapters use inclusive endpoints, so represent [start,end) exactly.
    last = end - dt.timedelta(microseconds=1)
    days = (last - start).total_seconds() / 86400
    groups = {}
    issues = []
    if source.startswith('codex'):
        raw = codex_health.analyze(source_paths(home, source)[0].parent, start, last, 50000, 15)
        groups['native'] = {'summary': metrics(raw['summary']), 'tools': safe_tools(raw['by_tool']), 'failure_categories': safe_categories(raw['failure_categories']), 'coverage': numbers(raw['coverage'], COVERAGE)}
        if len(raw['by_tool']) > 128:
            issues.append('tool_group_limit')
    elif source == 'claude':
        raw = claude_health.audit(source_paths(home, source)[0], start, last, max_files=500, max_bytes=256_000_000, max_entries=50000, max_seconds=15, observed_until=observed)
        groups['native'] = {'summary': metrics(raw['summary']), 'tools': safe_tools(raw['tools']), 'failure_categories': safe_categories(raw['failure_categories']), 'coverage': numbers(raw['coverage'], COVERAGE)}
        if len(raw['tools']) > 128:
            issues.append('tool_group_limit')
    elif source.startswith('opencode'):
        raw = opencode_health.read_source(source_paths(home, source)[0], int(start.timestamp()*1000), int(end.timestamp()*1000)-1, 50000, 15)[0]
        groups['native'] = {'summary': metrics(raw), 'tools': safe_tools(raw['tools']), 'failure_categories': safe_categories(raw['failure_categories']), 'coverage': numbers(raw['coverage'], COVERAGE)}
        if len(raw['tools']) > 128:
            issues.append('tool_group_limit')
    else:
        raw = tool_health.report(home / '.t3', days, last, 50000)
        for item in raw['coverage']:
            name = item['source']
            if name not in ('t3_v2', 't3_legacy'):
                continue
            rows = [row for row in raw['tools'] if row['source'] == name]
            groups[name] = {'summary': metrics(raw['by_source'].get(name, tool_health.summary([]))), 'tools': safe_tools(rows), 'coverage': {'calls': item['calls']}, 'failure_categories': safe_categories(raw['failure_categories_by_source'].get(name, {}))}
            if len(rows) > 128:
                issues.append('tool_group_limit')
        if not groups:
            return {'status': 'error', 'issues': ['unsupported_schema']}
    for group in groups.values():
        issues.extend(sorted(k for k, v in group['coverage'].items() if k in PARTIAL and v))
    if source == 'claude' and groups['native']['coverage'].get('jsonl_files_enumerated', 0) == 0:
        issues.append('no_retained_history_files')
    return {'status': 'partial' if issues else 'ok', 'issues': sorted(set(issues)), 'groups': groups}


def run_source(home, source, start, end, observed, timeout=20):
    command = [sys.executable, str(Path(__file__).resolve()), '_source', '--home', str(home), '--source', source, '--start', start.isoformat(), '--end', end.isoformat(), '--as-of', observed.isoformat()]
    try:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        if process.returncode or len(process.stdout) > 2_000_000:
            return {'status': 'error', 'issues': ['adapter_failed']}
        result = json.loads(process.stdout)
        if result.get('status') not in ('ok', 'partial', 'missing', 'error'):
            raise ValueError('invalid child status')
        return result
    except subprocess.TimeoutExpired:
        return {'status': 'error', 'issues': ['source_timeout']}
    except (OSError, ValueError):
        return {'status': 'error', 'issues': ['adapter_failed']}


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('state directory must be a real owned directory')
    path.chmod(0o700)


def atomic_json(path, value):
    if path.is_symlink():
        raise ValueError('refusing symlink state file')
    descriptor, temporary = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        if os.fstat(stream.fileno()).st_size > 2_000_000:
            raise ValueError('oversized state file')
        return json.load(stream)


def collect(home, state, as_of, sources, runner=run_source, observed_at=None):
    observed_at = observed_at or dt.datetime.now(UTC)
    private_dir(state)
    snapshots = state / 'snapshots'
    private_dir(snapshots)
    descriptor = os.open(state / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'schema_version': SCHEMA, 'status': 'busy', 'issues': ['collection_locked']}, 3
        anchor = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
        attempts = []
        deadline = time.monotonic() + 270
        for offset in (2, 1):
            start = anchor - dt.timedelta(days=offset)
            end = start + dt.timedelta(days=1)
            for source in sources:
                path = snapshots / (start.date().isoformat() + '_' + source + '.json')
                old = read_json(path) if path.exists() else {}
                result = runner(home, source, start, end, observed_at) if time.monotonic() < deadline else {'status': 'error', 'issues': ['collection_time_budget']}
                attempt = {'observed_at': observed_at.isoformat(), **result}
                envelope = {'schema_version': SCHEMA, 'source': source, 'window': {'start': start.isoformat(), 'end_exclusive': end.isoformat()}, 'latest_attempt': attempt, 'last_success': attempt if result['status'] == 'ok' else old.get('last_success')}
                atomic_json(path, envelope)
                attempts.append({'source': source, 'date': start.date().isoformat(), 'status': result['status'], 'issues': result['issues'], 'has_success_snapshot': envelope['last_success'] is not None})
        cutoff = (anchor - dt.timedelta(days=90)).date().isoformat()
        for path in snapshots.iterdir():
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}_(?:' + '|'.join(SOURCES) + r')\.json', path.name) and path.name[:10] < cutoff:
                if path.is_symlink():
                    raise ValueError('refusing symlink retention file')
                path.unlink()
        status = 'ok' if all(a['status'] == 'ok' for a in attempts) else 'partial'
        report = {'schema_version': SCHEMA, 'status': status, 'observed_at': observed_at.isoformat(), 'requested_as_of': as_of.isoformat(), 'window_policy': 'latest_two_closed_UTC_days', 'retention_days': 90, 'attempts': attempts, 'limitations': LIMITATIONS}
        atomic_json(state / 'latest.json', report)
        return report, 0 if status == 'ok' else 2
    finally:
        os.close(descriptor)


def report(state):
    descriptor = os.open(state / '.lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        latest = read_json(state / 'latest.json')
        records = []
        for path in sorted((state / 'snapshots').iterdir()):
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}_(?:' + '|'.join(SOURCES) + r')\.json', path.name):
                records.append(read_json(path))
        return {**latest, 'snapshots': records}
    finally:
        os.close(descriptor)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('collect', 'report', '_source'))
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--as-of')
    parser.add_argument('--source', choices=('all',) + SOURCES, default='all')
    parser.add_argument('--start', help=argparse.SUPPRESS)
    parser.add_argument('--end', help=argparse.SUPPRESS)
    args = parser.parse_args()
    state = args.state_dir or args.home / '.local/state/agent-health'
    try:
        as_of = parse_time(args.as_of) if args.as_of else dt.datetime.now(UTC)
        if args.command == '_source':
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
            if args.source == 'all':
                raise ValueError('worker needs one source')
            result = scan_source(args.home, args.source, parse_time(args.start), parse_time(args.end), as_of)
            code = 0
        elif args.command == 'collect':
            result, code = collect(args.home, state, as_of, SOURCES if args.source == 'all' else (args.source,))
        else:
            result = report(state)
            code = 0 if result['status'] == 'ok' else 2
    except (OSError, ValueError, TypeError, KeyError):
        result, code = {'schema_version': SCHEMA, 'status': 'error', 'issues': ['state_or_adapter_error']}, 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
