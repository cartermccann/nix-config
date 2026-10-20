import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import codex_health as audit
from tool_health import timestamp


class CodexHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.db=sqlite3.connect(self.home/'thread_history_1.sqlite')
        self.db.execute('CREATE TABLE thread_items(thread_id TEXT,item_id TEXT,item_type TEXT,created_at_ms INTEGER,started_at_ms INTEGER,completed_at_ms INTEGER,item_json TEXT)')
        c=sqlite3.connect(self.home/'state_5.sqlite')
        c.execute('CREATE TABLE threads(id TEXT,source TEXT,originator TEXT)')
        c.execute('INSERT INTO threads VALUES (?,?,?)',('thread','cli','T3 Code'));c.commit();c.close()
        self.end=timestamp('2026-10-20T16:00:00Z');self.start=timestamp('2026-10-13T16:00:00Z')

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def add(self, ident, typ='mcpToolCall', **kwargs):
        p={'type':typ,'tool':'read','server':'fixture','status':'completed','arguments':{'secret':'ARG_SENTINEL'},'result':{'content':[{'text':'Exit code 1, timeout, ERROR_SENTINEL'}]}}
        p.update(kwargs);ms=int(self.end.timestamp()*1000)
        self.db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?)',('thread',ident,typ,ms,ms,ms,json.dumps(p)));self.db.commit()

    def report(self,cap=100):return audit.analyze(self.home,self.start,self.end,cap)

    def test_success_text_does_not_fail_and_private_data_omitted(self):
        self.add('one');r=self.report()
        self.assertEqual(r['summary']['success'],1)
        self.assertNotIn('ARG_SENTINEL',json.dumps(r));self.assertNotIn('ERROR_SENTINEL',json.dumps(r))
        self.assertIn('t3-code',r['by_originator'])

    def test_errors_and_cancellation(self):
        self.add('one',result={'isError':True,'error':'timeout'})
        self.add('two',status='cancelled',result={'isError':True,'error':'timeout'})
        self.assertEqual(self.report()['summary']['failed'],1)
        self.assertEqual(self.report()['summary']['cancelled'],1)
        self.assertEqual(self.report()['failure_categories'],{'timeout':1})

    def test_shell_nonzero_and_unknown_status(self):
        self.add('one',typ='commandExecution',command='rg needle file',exitCode=1,aggregatedOutput='',result=None)
        self.add('two',typ='webSearch',status=None)
        self.assertEqual(self.report()['summary']['failed'],1)
        self.assertEqual(self.report()['summary']['unknown_or_pending'],1)
        self.assertEqual(self.report()['failure_categories'],{'search_no_match_candidate':1})

    def test_native_duration_and_dedup(self):
        self.add('one',durationMs=23);self.add('one',durationMs=23)
        r=self.report();self.assertEqual(r['summary']['calls'],1);self.assertEqual(r['summary']['p95_ms'],23)

    def test_readonly_hash_and_cap(self):
        self.add('one');before=hashlib.sha256((self.home/'thread_history_1.sqlite').read_bytes()).hexdigest()
        self.report();self.assertEqual(before,hashlib.sha256((self.home/'thread_history_1.sqlite').read_bytes()).hexdigest())
        self.add('two')
        with self.assertRaises(RuntimeError):self.report(cap=1)


if __name__=='__main__':unittest.main()
