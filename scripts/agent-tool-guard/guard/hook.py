#!/usr/bin/env python3
"""Harness adapter. Never log inputs, tool responses, identifiers or credentials."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from core import Guard, is_gmail

MAX_ENVELOPE = 128 * 1024 * 1024
MAX_VALUE = 128 * 1024
KEEP = {'session_id','agent_id','tool_use_id','tool_name','tool_input','tool_response','error','is_interrupt','_explicit_error','retry_after'}


def read_envelope(stream):
    """Stream top-level fields; discard oversized response values with bounded RAM."""
    total=0
    def chars():
        nonlocal total
        while True:
            chunk=stream.read(8192)
            if not chunk:return
            total+=len(chunk.encode('utf-8'))
            if total>MAX_ENVELOPE:raise ValueError('oversized envelope')
            yield from chunk
    it=iter(chars());pushed=None
    def get():
        nonlocal pushed
        if pushed is not None:c=pushed;pushed=None;return c
        return next(it,'')
    def nonspace():
        c=get()
        while c and c.isspace():c=get()
        return c
    def value(first, limit):
        nonlocal pushed
        parts=[];size=0;depth=0;quoted=False;escaped=False;c=first
        while c:
            if not quoted and depth==0 and c in ',}':pushed=c;break
            size+=1
            if size<=limit:parts.append(c)
            if quoted:
                if escaped:escaped=False
                elif c=='\\':escaped=True
                elif c=='"':quoted=False
            elif c=='"':quoted=True
            elif c in '[{':depth+=1
            elif c in ']}':
                depth-=1
                if depth<0:raise ValueError('bad JSON')
            if not quoted and depth==0 and (first in '[{' or first=='"'):
                break
            c=get()
        if quoted or depth:raise ValueError('incomplete JSON')
        if size>limit:return None
        return json.loads(''.join(parts))
    if nonspace()!='{':raise ValueError('expected envelope object')
    result={};after_comma=False
    while True:
        c=nonspace()
        if c=='}':
            if after_comma:raise ValueError('trailing comma')
            break
        if c!='"':raise ValueError('expected key')
        key=value(c,256)
        if not isinstance(key,str) or nonspace()!=':':raise ValueError('bad key')
        item=value(nonspace(),MAX_VALUE if key in KEEP else 0)
        if key in KEEP:
            if key in result:raise ValueError('duplicate metadata')
            result[key]=item
        c=nonspace()
        if c=='}':break
        if c!=',':raise ValueError('expected comma')
        after_comma=True
    if nonspace():raise ValueError('trailing JSON')
    return result


def state_path():
    override=os.environ.get('AGENT_TOOL_GUARD_STATE')
    if override:return Path(override)
    return Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'agent-tool-guard'/'state.sqlite'


def error_info(response):
    if not isinstance(response,dict):return None,None,None
    failed=response.get('isError') is True or response.get('is_error') is True
    err=response.get('error')
    if err not in (None,False,'',{}):failed=True
    nested=response.get('structuredContent')
    if isinstance(nested,dict) and (nested.get('isError') is True or nested.get('error')):
        failed=True;err=nested.get('error',err)
    if not failed:return None,None,None
    if not err:
        content=response.get('content',[])
        err='\n'.join(c.get('text','') for c in content if isinstance(c,dict) and c.get('type')=='text') if isinstance(content,list) else ''
    headers=response.get('headers',{})
    meta=response.get('_meta',{})
    if not headers and isinstance(meta,dict):headers=meta.get('headers',{})
    retry=next((v for k,v in headers.items() if k.lower()=='retry-after'),None) if isinstance(headers,dict) else None
    return err if isinstance(err,(str,dict)) else '',response.get('status_code'),retry


def identities(data):
    return data.get('tool_name',''),data.get('agent_id') or data.get('session_id',''),data.get('tool_use_id','')


def reconcile_codex(guard, roots=None, now=None):
    """Recover completion from a bounded native-record tail; no transcript reads."""
    roots=roots or [Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))),Path.home()/'.codex',Path.home()/'.codex_cliproxy']
    now=time.time() if now is None else now
    budget=time.monotonic()+.75
    active=guard.active_lease()
    if active is None:return {'available_sources':0,'matched_completions':0}
    matched=0;available=0
    for root in dict.fromkeys(Path(r) for r in roots):
        if time.monotonic()>=budget:break
        path=root/'thread_history_1.sqlite'
        if not path.is_file():continue
        conn=None
        try:
            conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
            conn.execute('PRAGMA query_only=ON')
            deadline=min(budget,time.monotonic()+.2)
            conn.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            rows=conn.execute('''SELECT thread_id,item_id,
              json_extract(item_json,'$.server'),json_extract(item_json,'$.tool'),
              json_extract(item_json,'$.status'),json_extract(item_json,'$.result.isError'),
              CASE WHEN json_extract(item_json,'$.status')='failed' OR json_extract(item_json,'$.result.isError')=1
              THEN substr(COALESCE(json_extract(item_json,'$.error'),json_extract(item_json,'$.result.content')),1,65536) END,
              json_extract(item_json,'$.result._meta.headers.Retry-After'),completed_at_ms
              FROM (SELECT thread_id,item_id,item_type,completed_at_ms,item_json FROM thread_items ORDER BY rowid DESC LIMIT 512)
              WHERE item_type='mcpToolCall' AND completed_at_ms>=? AND json_valid(item_json)
              AND (json_extract(item_json,'$.status') IN ('failed','completed'))''',(int((now-300)*1000),)).fetchall()
            available+=1
            for session,call,server,tool,status,is_error,error,retry,completed_ms in rows:
                if time.monotonic()>=budget:break
                if not isinstance(server,str) or not isinstance(tool,str):continue
                names=[f'mcp__{server}__{tool.replace(".","_")}',f'{server}.{tool}']
                for name in names:
                    if not is_gmail(name):continue
                    if guard._owner(name,session,call)!=active['owner_hash'] or completed_ms/1000<active['started']:continue
                    outcome='error' if status=='failed' or is_error else 'success'
                    decision=guard.finish(name,session,call,outcome=outcome,error=error,retry_after=retry,completed_at=completed_ms/1000,lease_id=active['lease_id'])
                    if decision.get('allowed'):return {'available_sources':available,'matched_completions':1}
        except (sqlite3.Error,OSError,ValueError):pass
        finally:
            if conn is not None:conn.close()
    return {'available_sources':available,'matched_completions':matched}


def pre(data,guard,*,runtime='generic',max_wait=5.0,reconcile=reconcile_codex):
    tool,session,call=identities(data)
    deadline=time.monotonic()+max_wait
    while True:
        if is_gmail(tool):reconcile(guard)
        decision=guard.reserve(tool,session,call,data.get('tool_input'))
        if decision['allowed']:return decision
        if decision['reason'] not in {'gmail_spacing','gmail_in_flight'} or time.monotonic()>=deadline:return decision
        time.sleep(min(.2,max(0.01,deadline-time.monotonic())))


def post(data,guard,*,failure=False):
    tool,session,call=identities(data)
    error,code,retry=error_info(data.get('tool_response'))
    if failure or data.get('_explicit_error') is True:
        error=data.get('error','')
    failed=failure or data.get('_explicit_error') is True or error is not None
    return guard.finish(tool,session,call,outcome='error' if failed else 'success',error=error,status_code=code,retry_after=data.get('retry_after',retry))


def reason_text(decision):
    reasons={
      'invalid_arguments':'Invalid arguments. Use the current tool schema; Monday get_updates requires link_id, objectId and objectType (Item or Board). Gmail read calls require the current account link_id and documented message/query fields.',
      'state_unavailable':'Shared tool guard state is unavailable; the call was not sent.',
      'gmail_in_flight':'Another Gmail call is still running. Wait for it to finish; do not fan out more Gmail requests.',
      'gmail_spacing':'Gmail pacing interval has not elapsed.',
      'rate_limit_cooldown':'Gmail is cooling down after a rate limit. Do not retry or switch accounts to bypass the cooldown.'}
    reason=reasons.get(decision['reason'],'Tool guard blocked this call.')
    delay=decision.get('retry_after_seconds',0)
    return reason+(f' Retry after {max(1,int(delay+0.999))} seconds.' if delay else '')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',choices=['codex','claude','generic'],default='generic')
    p.add_argument('--installation-id',default='agent-tool-guard-v1')
    p.add_argument('--event',choices=['pre','post','failure','probe'],required=True)
    a=p.parse_args();guard=Guard(state_path())
    if a.event=='probe':
        print(json.dumps({'state_path_configured':True,'codex_observer':reconcile_codex(guard)}));return 0
    try:data=read_envelope(sys.stdin)
    except (ValueError,OSError,RecursionError):
        if a.event=='pre':print('Tool guard could not validate the request envelope.',file=sys.stderr);return 2
        return 0
    if a.event=='pre':
        decision=pre(data,guard,runtime=a.runtime)
        if not decision['allowed']:
            text=reason_text(decision)
            if a.runtime=='generic':print(json.dumps({**decision,'message':text}));return 2
            print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':text}}));return 0
        if a.runtime=='generic':print(json.dumps(decision))
    else:post(data,guard,failure=a.event=='failure')
    return 0

if __name__=='__main__':raise SystemExit(main())
