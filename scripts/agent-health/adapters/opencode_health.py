#!/usr/bin/env python3
"""Bounded native OpenCode audit. Exports aggregates, never arguments/results."""
import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from tool_health import command_name

UTC = dt.timezone.utc
PATTERNS = [
    ('cancelled_or_rejected', r'user (?:rejected|denied|cancelled)|rejected by (?:the )?user|(?:operation|task) (?:cancelled|canceled)|tool(?: execution| call)? (?:aborted|cancelled)'),
    ('file_not_found', r'file not found|no such file or directory|does not exist|ENOENT'),
    ('patch_mismatch', r'oldString not found|old string not found|could not find|failed to find expected|no match found|multiple matches|not unique|exactly once'),
    ('read_before_edit', r'must read|read the file|before (?:editing|overwriting)|has not been read'),
    ('invalid_arguments', r'invalid (?:argument|parameter)|validation error|expected .*received|schema|missing required'),
    ('rate_limit', r'rate.limit|too many requests'),
    ('authentication', r'unauthorized|unauthenticated|invalid.api.key|token.expired|authentication required'),
    ('permission', r'permission denied|not permitted|forbidden'),
    ('timeout', r'timed? ?out|timeout|deadline exceeded'),
    ('network', r'ECONNREFUSED|ECONNRESET|ENOTFOUND|fetch failed|network error|socket hang up'),
    ('missing_command', r'command not found|not found in PATH'),
    ('test_or_build_failure', r'tests? failed|build failed|error TS\d+|FAIL(?:ED|URE)?[ :\n]'),
    ('python_error', r'Traceback \(most recent call last\)|SyntaxError|TypeError|AttributeError|ModuleNotFoundError|IndexError'),
]


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).isoformat() if ms is not None else None


def name(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,180}', value) else 'unknown'


def category(text):
    for label, pattern in PATTERNS:
        if re.search(pattern, text or '', re.I):
            return label
    return 'unclassified'


def percentile(values, p):
    values = sorted(values)
    return round(values[max(0, math.ceil(len(values) * p) - 1)], 2) if values else None


def summarize(records):
    terminal = [r for r in records if r['outcome'] in {'success', 'failure'}]
    timed = [r['duration'] for r in terminal if r['duration'] is not None]
    counts = collections.Counter(r['outcome'] for r in records)
    failed = counts['failure']
    return {
        'calls': len(records), 'terminal': len(terminal), 'success': counts['success'],
        'failure': failed, 'failure_percent': round(100 * failed / len(terminal), 2) if terminal else None,
        'cancelled': counts['cancelled'], 'pending': counts['pending'], 'unknown': counts['unknown'],
        'native_statuses': dict(collections.Counter(r['native_status'] for r in records)),
        'completed_with_nonzero_shell_exit': sum(r['shell_nonzero'] for r in records),
        'timed_terminal_calls': len(timed), 'native_duration_p50_ms': percentile(timed, .5),
        'native_duration_p95_ms': percentile(timed, .95),
    }


def normalize(row):
    created, call_id, tool, status, start, end, exit_code, error, failed_output, *extra = row
    tool = name(tool)
    status = name(status)
    shell_nonzero = tool == 'bash' and isinstance(exit_code, (int, float)) and exit_code != 0
    family = command_name(extra[0]) if extra and tool == 'bash' else None
    diagnostic = error if isinstance(error, str) else ''
    failure_category = category(diagnostic or (failed_output if shell_nonzero else ''))
    if shell_nonzero and exit_code == 1 and family in {'rg','grep'} and not (failed_output or '').strip():
        failure_category = 'search_no_match_candidate'
    if status in {'cancelled', 'canceled', 'interrupted'} or (status == 'error' and failure_category == 'cancelled_or_rejected'):
        outcome = 'cancelled'
    elif status == 'error' or (status == 'completed' and shell_nonzero):
        outcome = 'failure'
    elif status == 'completed':
        outcome = 'success'
    elif status in {'pending', 'running'}:
        outcome = 'pending'
    else:
        outcome = 'unknown'
    duration = end - start if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start else None
    return {'created': created, 'call_id': call_id, 'tool': tool, 'native_status': status,
            'outcome': outcome, 'duration': duration, 'shell_nonzero': shell_nonzero,
            'shell_family': family,
            'session_id': extra[1] if len(extra) > 1 else None,
            'category': failure_category if outcome == 'failure' else None}


def read_source(path, since, until, cap, seconds):
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    conn.execute('PRAGMA query_only=ON')
    deadline = time.monotonic() + seconds
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    conn.execute('BEGIN')
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'part', 'session'} <= tables:
            raise RuntimeError('Unsupported native schema')
        retained = conn.execute('SELECT min(time_created),max(time_created),count(*) FROM part').fetchone()
        # Integer filter precedes JSON parsing; no text/reasoning parts are selected or exported.
        rows = conn.execute("""
            SELECT time_created, json_extract(data,'$.callID'), json_extract(data,'$.tool'),
                   json_extract(data,'$.state.status'), json_extract(data,'$.state.time.start'),
                   json_extract(data,'$.state.time.end'), json_extract(data,'$.state.metadata.exit'),
                   substr(json_extract(data,'$.state.error'),1,200000),
                   CASE WHEN json_extract(data,'$.tool')='bash'
                         AND json_extract(data,'$.state.metadata.exit') != 0
                        THEN substr(json_extract(data,'$.state.output'),1,200000) END,
                   CASE WHEN json_extract(data,'$.tool')='bash'
                         AND json_extract(data,'$.state.metadata.exit') != 0
                        THEN substr(json_extract(data,'$.state.input.command'),1,20000) END,
                   session_id
            FROM part WHERE time_created BETWEEN ? AND ?
              AND json_valid(data) AND json_extract(data,'$.type')='tool'
            ORDER BY time_created LIMIT ?
        """, (since, until, cap + 1)).fetchall()
        if len(rows) > cap:
            raise RuntimeError('Tool row cap exceeded')
        malformed = conn.execute('SELECT count(*) FROM part WHERE time_created BETWEEN ? AND ? AND NOT json_valid(data)', (since, until)).fetchone()[0]
        modern = {}
        if 'session_message' in tables:
            modern['retained_rows'] = conn.execute('SELECT count(*) FROM session_message').fetchone()[0]
            modern['window_rows'] = conn.execute('SELECT count(*) FROM session_message WHERE time_created BETWEEN ? AND ?', (since, until)).fetchone()[0]
            # Unknown modern schemas must not silently become zero-call reports.
            if modern['window_rows']:
                raise RuntimeError('Modern session_message rows in window require schema adapter')
        records = []
        identified = {}
        duplicate_identities = 0
        for row in rows:
            record = normalize(row)
            identity = (record['session_id'], record['call_id'])
            if all(isinstance(value, str) and value for value in identity):
                if identity in identified:
                    if record != identified[identity]:
                        raise RuntimeError('Conflicting rows for one session/tool-call identity')
                    duplicate_identities += 1
                    continue
                identified[identity] = record
            records.append(record)
        ids = [r['call_id'] for r in records if isinstance(r['call_id'], str) and r['call_id']]
        duplicates = len(ids) - len(set(ids))
        bytool = collections.defaultdict(list)
        for r in records:
            bytool[r['tool']].append(r)
        coverage = {
            'retained_part_rows': retained[2], 'retained_part_first': iso(retained[0]),
            'retained_part_last': iso(retained[1]), 'first_call': iso(min((r['created'] for r in records), default=None)),
            'last_call': iso(max((r['created'] for r in records), default=None)),
            'malformed_parts_in_window': malformed, 'missing_call_ids': len(records)-len(ids),
            'duplicate_call_ids_within_source': duplicates,
            'duplicate_session_call_rows_removed': duplicate_identities,
            'modern_session_message': modern,
        }
        result = {'source': path.name, 'coverage': coverage, **summarize(records),
                  'failure_categories': dict(collections.Counter(r['category'] for r in records if r['outcome'] == 'failure')),
                  'nonzero_shell_families': dict(collections.Counter(r['shell_family'] for r in records if r['shell_nonzero'])),
                  'tools': sorted([{'tool': n, **summarize(rs)} for n,rs in bytool.items()], key=lambda x:(-x['failure'],-x['calls'])),
                  'actionable_failure_classes': [
                      {'tool': n, 'class': cat, 'count': count}
                      for (n,cat),count in collections.Counter((r['tool'],r['category']) for r in records if r['outcome']=='failure').most_common()]}
        return result, {'call_ids': set(ids), 'session_call_ids': {
            (r['session_id'], r['call_id']) for r in records if r['session_id'] and r['call_id']}}
    finally:
        conn.rollback()
        conn.close()


def report(paths, as_of, days=7, cap=100000, seconds=20):
    if not math.isfinite(days) or not 0 < days <= 366:
        raise ValueError('days must be finite and in (0, 366]')
    if cap < 1:
        raise RuntimeError('row cap must be positive')
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('finite timeout must be positive')
    parsed = dt.datetime.fromisoformat(as_of.replace('Z','+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('as-of must include a timezone')
    until = int(parsed.timestamp()*1000)
    since = until - int(days*86400000)
    sources = []
    identifiers = []
    for p in paths:
        result, ids = read_source(p,since,until,cap,seconds)
        sources.append(result)
        identifiers.append(ids)
    overlaps = [{'source_a':paths[i].name,'source_b':paths[j].name,
                 'matching_call_ids':len(identifiers[i]['call_ids']&identifiers[j]['call_ids']),
                 'matching_session_call_ids':len(identifiers[i]['session_call_ids']&identifiers[j]['session_call_ids'])}
                for i in range(len(paths)) for j in range(i+1,len(paths))]
    return {'schema_version':1,'window':{'since':iso(since),'until':iso(until),'days':days},
            'sources':sources,'cross_database_overlap':overlaps,
            'limits':[
                'Sources are independent and must not be summed when call IDs overlap.',
                'Identical repeated session/call identities are counted once; conflicting identity states fail closed. IDs reused by distinct sessions remain distinct calls.',
                'Window uses native part.time_created. No T3 projection or replay timestamps are used.',
                'Outcomes are retained current states of calls created in the window, not reconstructed historical states at the anchor.',
                'Failure means native error state or completed bash with nonzero native metadata.exit. Expected shell nonzero exits are included.',
                'User rejection/cancellation diagnostics are separate from failure. Unknown statuses remain unknown.',
                'Completed outputs are not searched for error words. Semantic failures lacking native error/exit metadata may be missed.',
                'Native state.time.end minus state.time.start supplies duration; this is distinct from T3 ingestion timing.',
                'Only fixed diagnostic categories are exported; prompts, arguments, outputs, call IDs and private paths are omitted.',
                'Modern session_message rows cause a fail-closed error until explicitly supported.',
                'Each database is queried in its own read-only transaction with row cap and SQL timeout; cross-source snapshot is not atomic.'
            ]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of',default='2026-10-20T16:00:00Z')
    parser.add_argument('--days',type=float,default=7)
    parser.add_argument('--max-rows',type=int,default=100000)
    parser.add_argument('--timeout',type=float,default=20)
    parser.add_argument('--database',type=Path,action='append')
    args=parser.parse_args()
    if not math.isfinite(args.days) or args.days <= 0:
        parser.error('--days must be positive and finite')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be positive and finite')
    if args.max_rows < 1:
        parser.error('--max-rows must be at least 1')
    paths=args.database or [Path.home()/'.local/share/opencode'/n for n in ('opencode.db','opencode-stable.db')]
    try:
        result = report(paths,args.as_of,args.days,args.max_rows,args.timeout)
    except (ValueError, RuntimeError, sqlite3.Error) as error:
        parser.exit(2, 'Audit stopped safely: ' + str(error) + '\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
