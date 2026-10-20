#!/usr/bin/env python3
"""Aggregate native Codex materialized tool records, without exporting content."""
import argparse
import collections
import datetime as dt
import json
from pathlib import Path
import sqlite3
import time

from tool_health import command_name, error_category, explicit_error, percentile, safe_name, timestamp

TOOL_TYPES={'commandExecution','mcpToolCall','fileChange','collabAgentToolCall','webSearch','imageView','sleep'}

def connect(path, deadline):
    c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
    return c


def summarize(rows):
    terminal=[r for r in rows if r['outcome'] in {'success','failure'}]
    failed=sum(r['outcome']=='failure' for r in rows)
    measured=[r['duration_ms'] for r in terminal if r['duration_ms'] is not None]
    return {'calls':len(rows),'terminal':len(terminal),'success':len(terminal)-failed,'failed':failed,
            'failure_percent':round(100*failed/len(terminal),2) if terminal else None,
            'cancelled':sum(r['outcome']=='cancelled' for r in rows),
            'unknown_or_pending':sum(r['outcome']=='unknown' for r in rows),
            'native_timed':len(measured),'p50_ms':percentile(measured,.5),'p95_ms':percentile(measured,.95),
            'max_ms':max(measured) if measured else None}


def analyze(home, since, until, max_rows=100000, timeout=30):
    deadline=time.monotonic()+timeout
    h=connect(home/'thread_history_1.sqlite',deadline)
    s=connect(home/'state_5.sqlite',deadline)
    origin={}
    try:
        for ident,source,originator in s.execute('SELECT id,source,originator FROM threads LIMIT ?', (max_rows+1,)):
            if len(origin)>=max_rows:raise RuntimeError('Thread cap exceeded')
            origin[ident]={'originator':{'T3 Code':'t3-code','Codex Desktop':'codex-desktop'}.get(originator, safe_name(originator) if originator else 'unknown'),
                           'kind':'subagent' if str(source).startswith('{') else safe_name(source)}
        lo=int(since.timestamp()*1000);hi=int(until.timestamp()*1000)
        native=list(h.execute("SELECT thread_id,item_id,item_type,created_at_ms,started_at_ms,completed_at_ms,item_json FROM thread_items WHERE created_at_ms BETWEEN ? AND ? AND item_type IN ('commandExecution','mcpToolCall','fileChange','collabAgentToolCall','webSearch','imageView','sleep') LIMIT ?",(lo,hi,max_rows+1)))
        if len(native)>max_rows:raise RuntimeError('Tool row cap exceeded')
        records=[];unmapped=0;seen=set()
        for thread,ident,typ,created,started,completed,payload in native:
            key=(thread,ident)
            if key in seen:continue
            seen.add(key);d=json.loads(payload);status=d.get('status')
            output=d.get('aggregatedOutput') if typ=='commandExecution' else d.get('result')
            err=d.get('error');code=d.get('exitCode')
            if status in {'cancelled','interrupted','declined'}:outcome='cancelled'
            elif status in {'failed','errored','error'} or explicit_error(output) or err or (isinstance(code,int) and code!=0):outcome='failure'
            elif status in {'completed','success','succeeded'}:outcome='success'
            else:outcome='unknown'
            name=command_name(d.get('command')) if typ=='commandExecution' else safe_name((d.get('server')+'.' if d.get('server') else '')+str(d.get('tool',typ)))
            duration=d.get('durationMs')
            if not isinstance(duration,(int,float)) or duration<0:duration=None
            orig=origin.get(thread,{'originator':'unmapped','kind':'unmapped'})
            unmapped+=thread not in origin
            records.append({'tool':name,'type':typ,'outcome':outcome,'duration_ms':duration,'originator':orig['originator'],'kind':orig['kind'],
                            'timestamp':dt.datetime.fromtimestamp(created/1000,dt.timezone.utc).isoformat(),
                            'failure_category':error_category(err or output,name,code) if outcome=='failure' else None})
        h.rollback();s.rollback()
    finally:h.close();s.close()
    by_tool=collections.defaultdict(list);by_type=collections.defaultdict(list);by_origin=collections.defaultdict(list);by_kind=collections.defaultdict(list)
    for r in records:
        by_tool[r['tool']].append(r);by_type[r['type']].append(r);by_origin[r['originator']].append(r);by_kind[r['kind']].append(r)
    return {'schema_version':1,'source':'native_codex_thread_history','window':{'since':since.isoformat(),'until':until.isoformat()},
            'summary':summarize(records),'coverage':{'source_database':str(home/'thread_history_1.sqlite'),'scanned_tool_rows':len(native),'unique_calls':len(records),'unmapped_origin_calls':unmapped,
            'first':min((r['timestamp'] for r in records),default=None),'last':max((r['timestamp'] for r in records),default=None)},
            'by_tool':sorted([{'tool':k,**summarize(v)} for k,v in by_tool.items()],key=lambda r:(-r['failed'],-r['calls'])),
            'by_type':{k:summarize(v) for k,v in by_type.items()},'by_originator':{k:summarize(v) for k,v in by_origin.items()},'by_session_kind':{k:summarize(v) for k,v in by_kind.items()},
            'explicit_sleep_requests':{'calls':len(by_type.get('sleep',[])),
                'total_requested_ms':sum(r['duration_ms'] or 0 for r in by_type.get('sleep',[])),
                'note':'Declared sleep intervals, not measured blocked time; parallel waits overlap and may be intentional.'},
            'failure_categories':dict(collections.Counter(r['failure_category'] for r in records if r['outcome']=='failure')),
            'limitations':['Native Codex materialized tool records only; do not add to T3, which can mirror these same calls.',
                'Missing final status is unknown, not assumed success. Built-in searches/images/sleeps may omit status.',
                'Tool outcome failures include expected shell nonzero exits and failed tests, not just infrastructure faults.',
                'durationMs is used only when stored explicitly. No timing is inferred from conversation timestamps.',
                'Nested batched calls may not be individually materialized; these are recorded native tools, not every underlying network request.',
                'Originator labels come from a separate read-only state snapshot; unmapped or ambiguous attribution stays unknown.',
                'Only aggregate names, counts and fixed error categories are exported; no prompts, raw arguments/results or session IDs.']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,default=Path.home()/'.codex');p.add_argument('--as-of',default='2026-10-20T16:00:00Z')
    p.add_argument('--days',type=float,default=7);p.add_argument('--max-rows',type=int,default=100000);p.add_argument('--timeout',type=float,default=30)
    a=p.parse_args();end=timestamp(a.as_of)
    if end is None or not 0<a.days<=366 or a.max_rows<1 or a.timeout<=0:p.error('Invalid window or limits')
    try:r=analyze(a.home,end-dt.timedelta(days=a.days),end,a.max_rows,a.timeout)
    except (OSError,sqlite3.Error,ValueError,RuntimeError) as e:p.exit(2,'Audit stopped safely: '+str(e)+'\n')
    print(json.dumps(r,indent=2))

if __name__=='__main__':main()
