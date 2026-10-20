import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).parent))
from core import Guard, Policy
from hook import read_envelope,error_info,identities,pre,post,reconcile_codex

TOOL='mcp__codex_apps__gmail_search_email_ids'
class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.g=Guard(self.root/'state'/'state.sqlite',policy=Policy(min_spacing_seconds=0))
        self.data={'tool_name':TOOL,'session_id':'root','agent_id':'child','tool_use_id':'call','tool_input':{'link_id':'account','query':'in:inbox'}}
    def tearDown(self):self.tmp.cleanup()
    def test_stream_projection_and_large_response(self):
        d={**self.data,'tool_response':{'content':'SENSITIVE'*20000}}
        r=read_envelope(io.StringIO(json.dumps(d)))
        self.assertEqual(r['agent_id'],'child');self.assertIsNone(r['tool_response'])
        self.assertNotIn('SENSITIVE',json.dumps(r))
    def test_nested_json_and_escaped_strings(self):
        d={**self.data,'irrelevant':[{'quoted':'a\\"b'},True,None,3],'tool_response':{'isError':True,'content':[{'type':'text','text':'429'}]}}
        r=read_envelope(io.StringIO(json.dumps(d)));self.assertEqual(r['tool_response'],d['tool_response'])
    def test_malformed_and_duplicate_metadata(self):
        for data in ['bad','{"tool_name":"a","tool_name":"b"}','{} true','{"tool_name":','{"tool_name":"a",}']:
            with self.assertRaises(ValueError):read_envelope(io.StringIO(data))
    def test_successful_body_does_not_trigger_backoff(self):
        self.assertEqual(error_info({'content':[{'type':'text','text':'Email discusses 429 rate limits'}]}),(None,None,None))
        self.g.reserve(*identities(self.data),self.data['tool_input'])
        post({**self.data,'tool_response':{'content':[{'type':'text','text':'429'}]}},self.g)
        self.assertTrue(self.g.reserve(TOOL,'next','call2',self.data['tool_input'])['allowed'])
    def test_failure_event_sets_shared_cooldown(self):
        self.g.reserve(*identities(self.data),self.data['tool_input'])
        post({**self.data,'error':'RATE_LIMIT_EXCEEDED'},self.g,failure=True)
        d=self.g.reserve(TOOL,'next','call2',self.data['tool_input'])
        self.assertEqual(d['reason'],'rate_limit_cooldown')
    def test_agent_id_is_native_identity(self):
        self.assertEqual(identities(self.data)[1],'child')
    def test_native_error_reconciliation(self):
        h=self.root/'codex';h.mkdir();path=h/'thread_history_1.sqlite'
        c=sqlite3.connect(path);c.execute('CREATE TABLE thread_items(thread_id,item_id,item_type,completed_at_ms,item_json)')
        d={'server':'codex_apps','tool':'gmail.search_email_ids','status':'failed','error':{'message':'RATE_LIMIT_EXCEEDED'}}
        c.execute('INSERT INTO thread_items VALUES(?,?,?,?,?)',('child','call','mcpToolCall',1700000001000,json.dumps(d)))
        c.commit();c.close();before=hashlib.sha256(path.read_bytes()).hexdigest()
        self.g.clock=lambda:1700000000.0
        self.g.reserve(*identities(self.data),self.data['tool_input'])
        self.assertEqual(reconcile_codex(self.g,[h],now=1700000002)['matched_completions'],1)
        self.assertEqual(reconcile_codex(self.g,[h])['matched_completions'],0)
        self.assertEqual(pre({**self.data,'tool_use_id':'next'},self.g,max_wait=0,reconcile=lambda g:None)['reason'],'rate_limit_cooldown')
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
    def test_old_native_completion_cannot_release_new_lease(self):
        h=self.root/'old-codex';h.mkdir();c=sqlite3.connect(h/'thread_history_1.sqlite')
        c.execute('CREATE TABLE thread_items(thread_id,item_id,item_type,completed_at_ms,item_json)')
        d={'server':'codex_apps','tool':'gmail.search_email_ids','status':'completed'}
        c.execute('INSERT INTO thread_items VALUES(?,?,?,?,?)',('child','call','mcpToolCall',int((time.time()-30)*1000),json.dumps(d)))
        c.commit();c.close()
        self.g.reserve(*identities(self.data),self.data['tool_input'])
        self.assertEqual(reconcile_codex(self.g,[h])['matched_completions'],0)
        blocked=self.g.reserve(TOOL,'different','new',self.data['tool_input'])
        self.assertEqual(blocked['reason'],'gmail_in_flight')
    def test_cli_invalid_monday_blocked(self):
        d={'tool_name':'mcp__codex_apps__monday_com_get_updates','session_id':'s','tool_use_id':'c','tool_input':{'objectIds':['1']}}
        r=subprocess.run([sys.executable,str(Path(__file__).with_name('hook.py')),'--runtime','codex','--event','pre'],input=json.dumps(d),capture_output=True,text=True,env={**os.environ,'AGENT_TOOL_GUARD_STATE':str(self.root/'state'/'state.sqlite')})
        self.assertEqual(r.returncode,0)
        self.assertEqual(json.loads(r.stdout)['hookSpecificOutput']['permissionDecision'],'deny')

    def test_unrelated_native_records_do_not_write_guard_state(self):
        h=self.root/'busy-codex';h.mkdir();c=sqlite3.connect(h/'thread_history_1.sqlite')
        c.execute('CREATE TABLE thread_items(thread_id,item_id,item_type,completed_at_ms,item_json)')
        d=json.dumps({'server':'codex_apps','tool':'gmail.search_email_ids','status':'failed','error':{'message':'429'}})
        c.executemany('INSERT INTO thread_items VALUES(?,?,?,?,?)',
                      [('other',str(i),'mcpToolCall',int(time.time()*1000),d) for i in range(2000)])
        c.commit();c.close()
        self.g.reserve(*identities(self.data),self.data['tool_input'])
        original=self.g.finish
        self.g.finish=lambda *a,**k: self.fail('Unowned completion must not call finish')
        started=time.monotonic()
        self.assertEqual(reconcile_codex(self.g,[h])['matched_completions'],0)
        self.assertLess(time.monotonic()-started,1.5)
        self.g.finish=original

if __name__=='__main__':unittest.main()
