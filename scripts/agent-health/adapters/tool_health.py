#!/usr/bin/env python3
"""Read-only, bounded T3 tool-call audit. Never exports arguments or results."""
import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time

UTC = dt.timezone.utc
TOOL_TYPES = {'commandExecution', 'command_execution', 'mcpToolCall', 'mcp_tool_call', 'dynamic_tool_call', 'dynamic_tool', 'webSearch', 'web_search', 'fileChange', 'file_change', 'imageView', 'image_view', 'collabAgentToolCall', 'collab_agent_tool_call'}
PATTERNS = [
    ('rate_limit', r'RATE_LIMIT_EXCEEDED|rate.limit|too many requests|\b429\b'),
    ('authentication', r'unauthorized|unauthenticated|invalid.api.key|token.expired|\b401\b'),
    ('permission', r'permission denied|not permitted|forbidden|\b403\b'),
    ('timeout', r'timed? ?out|timeout|deadline exceeded'),
    ('network', r'ECONNREFUSED|ECONNRESET|ENOTFOUND|fetch failed|network error|socket hang up'),
    ('missing_command', r'command not found|not found in PATH|No such file or directory'),
    ('invalid_tool_or_arguments', r'unknown tool|invalid.arguments|invalid.params|unsupported.*(tool|method|operation)|unexpected keyword|not a function|is not defined|unrecognized (key|argument)|validation error'),
    ('python_error', r'Traceback \(most recent call last\)|SyntaxError|TypeError|AttributeError|ModuleNotFoundError|IndexError'),
    ('browser_unavailable', r'browser.*(unavailable|not available|not connected)|no.*browser.*connected|page.*closed|target.*closed'),
    ('preview_client_failure', r'Preview automation.*failed on client'),
    ('device_unavailable', r'No (android|ios) devices were found'),
    ('patch_mismatch', r'patch.*(failed|invalid)|Failed to find expected lines|Invalid Context'),
    ('test_or_build_failure', r'FAIL[ :]|tests? failed|build failed|error TS\d+'),
]


def timestamp(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000, UTC)
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)
    except (ValueError, OverflowError, TypeError):
        return None


def safe_name(value):
    # Names only; never include paths, command text, or arbitrary error text.
    value = str(value or 'unknown')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}', value):
        return 'unknown'
    value = value.replace('mcp__t3-code__', 't3-code.').replace('mcp__t3_code__', 't3-code.')
    return value


def command_name(value):
    if isinstance(value, dict):
        value = value.get('cmd', value.get('command', ''))
    if not isinstance(value, str):
        return 'shell'
    # Only expose known executable basenames, not arbitrary first words or args.
    match = re.match(r'\s*(?:/[^\s]+/)?([a-zA-Z0-9_-]+)\b', value)
    name = match[1] if match else ''
    return name if name in {'agent-tools','rg','grep','git','gh','python','python3','node','npm','pnpm','bun','nix','bash','fish','cat','sed','curl','ls','mkdir','jq','find','fd','ssh','timeout','test','true','false','head','tail','wc','sleep'} else 'shell'


def error_category(output, name, exit_code=None):
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=True)
    # Match fixed patterns locally; none of the raw text is returned.
    for label, pattern in PATTERNS:
        if re.search(pattern, text, re.I):
            return label
    if name in {'rg','grep'} and exit_code == 1 and not text.strip():
        return 'search_no_match_candidate'
    return 'unclassified'


def explicit_error(value):
    if not isinstance(value, dict):
        return False
    if value.get('isError') is True or value.get('is_error') is True:
        return True
    if value.get('error') not in (None, False, '', {}):
        return True
    # Inspect structural envelope only, never regex successful tool contents for errors.
    return any(explicit_error(value.get(k)) for k in ('result', 'structuredContent') if isinstance(value.get(k), dict))


def readonly(path, seconds=20):
    if not path.is_file():
        return None
    c = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    c.execute('PRAGMA query_only=ON')
    deadline = time.monotonic() + seconds
    c.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    c.execute('BEGIN')
    return c


def tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def bounded_rows(c, sql, params, cap):
    rows = c.execute(sql + ' LIMIT ?', (*params, cap + 1)).fetchall()
    if len(rows) > cap:
        raise RuntimeError('Row cap exceeded; reduce --days or increase --max-rows explicitly')
    return rows


def v2_records(c, since, until, cap):
    providers = {}
    for pid, payload in c.execute('SELECT provider_thread_id,payload_json FROM orchestration_v2_projection_provider_threads'):
        d = json.loads(payload)
        providers[pid] = safe_name(d.get('providerInstanceId', d.get('driver')))
    # Match SQL selection to the Python timestamp fallback. A NULL effective time
    # cannot be assigned to any daily window, so fail closed instead of counting 0.
    effective_time = "COALESCE(julianday(json_extract(payload_json,'$.startedAt')),julianday(json_extract(payload_json,'$.updatedAt')),julianday(updated_at))"
    kinds = "type IN ('command_execution','dynamic_tool','web_search','file_change')"
    invalid = c.execute(f"SELECT 1 FROM orchestration_v2_projection_turn_items WHERE {kinds} AND {effective_time} IS NULL LIMIT 1").fetchone()
    if invalid:
        raise RuntimeError('T3 V2 tool rows lack parseable timestamps')
    rows = bounded_rows(c, f"SELECT payload_json,updated_at FROM orchestration_v2_projection_turn_items WHERE {effective_time} >= julianday(?) AND {effective_time} <= julianday(?) AND {kinds} ORDER BY updated_at DESC", (since.isoformat(), until.isoformat()), cap)
    records = []
    for payload, table_updated_at in rows:
        d = json.loads(payload)
        started = timestamp(d.get('startedAt'))
        ended = timestamp(d.get('completedAt'))
        event_time = started or timestamp(d.get('updatedAt')) or timestamp(table_updated_at)
        if event_time is None:
            raise RuntimeError('T3 V2 timestamp cannot be parsed consistently')
        if not since <= event_time <= until:
            continue
        typ = d['type']
        name = command_name(d.get('input')) if typ == 'command_execution' else safe_name(d.get('toolName',typ))
        output = d.get('output')
        status = d['status']
        failure = status == 'failed' or explicit_error(output) or (typ == 'command_execution' and isinstance(d.get('exitCode'),int) and d['exitCode'] != 0)
        records.append({'source':'t3_v2','id':d['id'],'provider':providers.get(d.get('providerThreadId'),'unknown'),'type':typ,'name':name,'status':status,'failure':failure,'category':error_category(output,name,d.get('exitCode')) if failure else None,'timestamp':event_time.isoformat(),'duration_ms':max(0,(ended-started).total_seconds()*1000) if started and ended and ended>=started else None,'timing_source':'lifecycle' if started and ended else None})
    return records


def legacy_records(c, since, until, cap):
    rows = bounded_rows(c, "SELECT thread_id,created_at,payload_json FROM projection_thread_activities WHERE kind='tool.completed' AND julianday(created_at) >= julianday(?) AND julianday(created_at) <= julianday(?) ORDER BY created_at DESC", (since.isoformat(),until.isoformat()), cap)
    records = []
    seen = set()
    for thread, created, payload in rows:
        observed = timestamp(created)
        if not observed or not since <= observed <= until:
            continue
        d = json.loads(payload)
        data = d.get('data') or {}
        item = data.get('item') or {}
        ident = (thread, d.get('toolCallId') or item.get('id'))
        if not ident[1] or ident in seen:
            continue
        seen.add(ident)
        typ = item.get('type',d.get('itemType','unknown'))
        if typ not in TOOL_TYPES:
            continue
        name = data.get('toolName') or item.get('tool') or item.get('toolName')
        if typ in {'commandExecution','command_execution'}:
            name = command_name(item.get('command',data.get('input')))
        elif item.get('server'):
            name = item['server']+'.'+str(name)
        name = safe_name(name or typ)
        result = data.get('result',item.get('result',item.get('aggregatedOutput')))
        status = d.get('status',item.get('status','unknown'))
        code = item.get('exitCode')
        failure = status == 'failed' or explicit_error(result) or bool(item.get('error')) or (isinstance(code,int) and code != 0)
        duration = item.get('durationMs')
        if not isinstance(duration,(int,float)) or duration<0:
            duration = None
        records.append({'source':'t3_legacy','id':str(ident),'provider':'legacy_unattributed','type':typ,'name':name,'status':status,'failure':failure,'category':error_category(item.get('error') or result,name,code) if failure else None,'timestamp':observed.isoformat(),'duration_ms':duration,'timing_source':'native_duration' if duration is not None else None})
    return records


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    return round(values[max(0,math.ceil(p*len(values))-1)],2)


def summary(records):
    terminal = [r for r in records if r['status'] in {'completed','failed'}]
    measured = [r['duration_ms'] for r in terminal if r['duration_ms'] is not None]
    failed = sum(r['failure'] for r in terminal)
    return {'calls':len(records),'terminal_calls':len(terminal),'failed':failed,'failure_percent':round(100*failed/len(terminal),2) if terminal else None,'cancelled_or_interrupted':sum(r['status'] in {'cancelled','interrupted'} for r in records),'pending_or_other':sum(r['status'] not in {'completed','failed','cancelled','interrupted'} for r in records),'timed_calls':len(measured),'p50_ms':percentile(measured,.50),'p95_ms':percentile(measured,.95),'p99_ms':percentile(measured,.99),'max_ms':max(measured) if measured else None,'timing_sources':dict(collections.Counter(r['timing_source'] for r in terminal if r['duration_ms'] is not None))}


def report(home, days, until, cap):
    since = until-dt.timedelta(days=days)
    records = []; coverage=[]; runs=[]
    # statev2 contains migrated legacy projection data. Never sum both databases.
    path = home/'userdata/statev2.sqlite'
    if not path.exists():
        path = home/'userdata/state.sqlite'
    c=readonly(path)
    if c is None:
        raise RuntimeError('No T3 state database found')
    try:
        schema = tables(c)
        if 'orchestration_v2_projection_turn_items' in schema:
            new=v2_records(c,since,until,cap);records.extend(new)
            coverage.append({'source':'t3_v2','path':str(path),'calls':len(new),'first':min((r['timestamp'] for r in new),default=None),'last':max((r['timestamp'] for r in new),default=None)})
            for provider,status,count in c.execute('SELECT provider,status,count(*) FROM orchestration_v2_projection_runs WHERE julianday(requested_at) >= julianday(?) AND julianday(requested_at) <= julianday(?) GROUP BY provider,status',(since.isoformat(),until.isoformat())):
                runs.append({'provider':safe_name(provider),'status':status,'count':count})
        if 'projection_thread_activities' in schema:
            old=legacy_records(c,since,until,cap);records.extend(old)
            coverage.append({'source':'t3_legacy','path':str(path),'calls':len(old),'first':min((r['timestamp'] for r in old),default=None),'last':max((r['timestamp'] for r in old),default=None)})
        c.rollback()
    finally:
        c.close()
    # Expose sources separately: schemas, providers and timing quality differ.
    by_tool=collections.defaultdict(list);by_source=collections.defaultdict(list);by_day=collections.defaultdict(list)
    for r in records:
        by_tool[(r['source'],r['name'])].append(r);by_source[r['source']].append(r);by_day[(r['source'],r['timestamp'][:10])].append(r)
    return {'schema_version':1,'window':{'since':since.isoformat(),'until':until.isoformat(),'days':days},'coverage':coverage,'by_source':{s:summary(rs) for s,rs in by_source.items()},'failure_categories_by_source':{s:dict(collections.Counter(r['category'] for r in rs if r['failure'] and r['status'] in {'completed','failed'})) for s,rs in by_source.items()},'failure_categories':dict(collections.Counter(r['category'] for r in records if r['failure'] and r['status'] in {'completed','failed'})),'tools':sorted([{'source':s,'tool':n,**summary(rs)} for (s,n),rs in by_tool.items()],key=lambda r:(-r['failed'],-r['calls'])),'daily':[{'source':s,'date':d,**summary(rs)} for (s,d),rs in sorted(by_day.items())],'runs':runs,'limitations':['T3 local projections only. Codex/Claude standalone sessions and nested calls inside batched functions.exec are not individually covered.','Legacy and V2 are reported separately; migrations can replay/import history. Do not sum them into a universal rate.','Failures are reported call outcomes, including expected shell nonzero exits and failing tests. They are not an infrastructure outage rate.','Native durationMs is preferred where available; V2 lifecycle deltas may reflect ingestion/replay rather than execution time. Do not compare the timing sources.','No arbitrary command arguments, prompts, results, tokens, or raw error text are exported.','No automatic retries, repairs, service restarts, database changes, or settings changes.']}


def markdown(r):
    lines=['# Local tool-call audit','',f"Window: {r['window']['since']} to {r['window']['until']} (UTC)",'','## Coverage','']
    for c in r['coverage']:
        lines.append(f"- {c['source']}: {c['calls']:,} calls; {c['first']} through {c['last']}.")
    lines+=['','## Outcomes','','| Source | Terminal | Failed | Rate | Timed | p50 ms | p95 ms |','|---|---:|---:|---:|---:|---:|---:|']
    for s,v in r['by_source'].items():lines.append(f"| {s} | {v['terminal_calls']:,} | {v['failed']:,} | {v['failure_percent']}% | {v['timed_calls']:,} | {v['p50_ms']} | {v['p95_ms']} |")
    lines+=['','## Most reported failures','','| Source | Tool / shell family | Failed / terminal | Rate |','|---|---|---:|---:|']
    for t in r['tools'][:25]:lines.append(f"| {t['source']} | {t['tool']} | {t['failed']} / {t['terminal_calls']} | {t['failure_percent']}% |")
    lines+=['','## Failure categories','']+[f'- {k}: {v}' for k,v in r['failure_categories'].items()]
    lines+=['','## Limits','']+['- '+v for v in r['limitations']]
    return '\n'.join(lines)+'\n'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,default=Path.home()/'.t3')
    p.add_argument('--days',type=float,default=7)
    p.add_argument('--as-of',help='Fixed ISO timestamp for reproducible reports')
    p.add_argument('--max-rows',type=int,default=100000)
    p.add_argument('--format',choices=['json','markdown'],default='markdown')
    args=p.parse_args()
    if args.days<=0 or args.days>366 or args.max_rows<=0:
        p.error('days must be in (0,366] and max-rows must be positive')
    until=timestamp(args.as_of) if args.as_of else dt.datetime.now(UTC)
    if until is None:p.error('Invalid --as-of timestamp')
    try:r=report(args.home,args.days,until,args.max_rows)
    except (RuntimeError,sqlite3.Error,json.JSONDecodeError) as e:
        print('Audit stopped safely: '+str(e),file=sys.stderr);return 2
    print(json.dumps(r,indent=2) if args.format=='json' else markdown(r),end='\n' if args.format=='json' else '')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
