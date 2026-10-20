import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import tool_health as audit


class ToolHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / 'userdata').mkdir()
        self.db = self.home / 'userdata/statev2.sqlite'
        self.c = sqlite3.connect(self.db)
        self.c.executescript('''
        CREATE TABLE projection_thread_activities(thread_id TEXT, created_at TEXT, kind TEXT, payload_json TEXT);
        CREATE TABLE orchestration_v2_projection_provider_threads(provider_thread_id TEXT,payload_json TEXT);
        CREATE TABLE orchestration_v2_projection_turn_items(updated_at TEXT,type TEXT,payload_json TEXT);
        CREATE TABLE orchestration_v2_projection_runs(provider TEXT,status TEXT,requested_at TEXT);
        ''')
        self.until = audit.timestamp('2026-10-20T16:00:00Z')

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def legacy(self, ident='one', created='2026-10-20T16:00:00Z', result=None, status='completed', thread='a', name='test.tool', duration=7):
        d = {'toolCallId':ident, 'itemType':'mcpToolCall','status':status,'data':{'item':{'type':'mcpToolCall','tool':name,'result':result,'durationMs':duration}}}
        self.c.execute('INSERT INTO projection_thread_activities VALUES (?,?,?,?)',(thread,created,'tool.completed',json.dumps(d)))
        self.c.commit()

    def v2(self, status='completed', result=None, name='test.tool', started='2026-10-20T16:00:00Z'):
        d={'id':'one','type':'dynamic_tool','status':status,'toolName':name,'output':result,'startedAt':started,'completedAt':started,'updatedAt':started}
        self.c.execute('INSERT INTO orchestration_v2_projection_turn_items VALUES (?,?,?)',(started,'dynamic_tool',json.dumps(d)))
        self.c.commit()

    def report(self, cap=100):
        return audit.report(self.home,7,self.until,cap)

    def test_boundary_and_offset_exclusion(self):
        self.legacy()
        self.legacy('old', '2026-10-13T17:00:00+02:00')
        self.v2()
        r=self.report()
        self.assertEqual(r['by_source']['t3_legacy']['calls'],1)
        self.assertEqual(r['by_source']['t3_v2']['calls'],1)

    def test_utc_daily_bucket(self):
        self.legacy(created='2026-10-20T01:00:00+02:00')
        self.assertEqual(self.report()['daily'][0]['date'],'2026-10-19')

    def test_dedup_is_per_thread(self):
        self.legacy(); self.legacy(); self.legacy(thread='b')
        self.assertEqual(self.report()['by_source']['t3_legacy']['calls'],2)

    def test_structural_error_not_error_related_success_text(self):
        self.legacy(result={'content':[{'type':'text','text':'Test error handling and timeout cases'}]})
        self.legacy('two',result={'isError':True,'error':'timeout'})
        r=self.report()
        self.assertEqual(r['by_source']['t3_legacy']['failed'],1)
        self.assertEqual(r['failure_categories'],{'timeout':1})

    def test_cancelled_errors_excluded_from_failure_categories(self):
        self.v2(status='cancelled',result={'isError':True,'error':'timeout'})
        r=self.report()
        self.assertEqual(r['by_source']['t3_v2']['failed'],0)
        self.assertEqual(r['failure_categories'],{})

    def test_missing_timing_stays_missing(self):
        self.legacy(duration=None)
        r=self.report()['by_source']['t3_legacy']
        self.assertEqual(r['timed_calls'],0)
        self.assertIsNone(r['p95_ms'])

    def test_private_arguments_results_and_path_names_not_exported(self):
        sentinel='/home/cjm/.config/credentials/PRIVATE_SENTINEL'
        self.legacy(name=sentinel,result={'content':[{'text':sentinel}]})
        r=json.dumps(self.report())
        self.assertNotIn('PRIVATE_SENTINEL',r)
        self.assertEqual(audit.safe_name(sentinel),'unknown')

    def test_search_no_match_and_known_executable_only(self):
        self.assertEqual(audit.error_category('', 'rg', 1),'search_no_match_candidate')
        self.assertEqual(audit.command_name('SECRET=value program --token SECRET'),'shell')
        self.assertEqual(audit.command_name('python3 script.py'),'python3')

    def test_ro_database_and_no_source_changes(self):
        self.legacy()
        before=hashlib.sha256(self.db.read_bytes()).hexdigest()
        c=audit.readonly(self.db)
        with self.assertRaises(sqlite3.OperationalError):
            c.execute('DELETE FROM projection_thread_activities')
        c.close();self.report()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),before)

    def test_row_cap(self):
        self.legacy();self.legacy('two')
        with self.assertRaisesRegex(RuntimeError,'Row cap'):
            self.report(cap=1)


if __name__=='__main__':
    unittest.main()
