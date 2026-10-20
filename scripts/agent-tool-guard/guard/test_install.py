import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).parent))
from install import updated_hooks,install_json,install_plugin

class InstallTests(unittest.TestCase):
    def test_preserves_unrelated_config_and_idempotent(self):
        old={'env':{'PRIVATE':'SENTINEL'},'hooks':{'SessionStart':[{'hooks':[{'command':'keep me'}]}],'PreToolUse':[{'matcher':'Bash','hooks':[{'command':'existing'}]}]}}
        result=updated_hooks(old,['python','/one/hook.py'],'codex')
        self.assertEqual(result['env'],old['env']);self.assertEqual(result['hooks']['SessionStart'],old['hooks']['SessionStart'])
        self.assertEqual(result['hooks']['PreToolUse'][0],old['hooks']['PreToolUse'][0])
        self.assertEqual(updated_hooks(result,['python','/one/hook.py'],'codex'),result)
        updated=updated_hooks(result,['python','/two/hook.py'],'codex')
        self.assertEqual(len(updated['hooks']['PreToolUse']),2)
        self.assertIn('/two/hook.py',updated['hooks']['PreToolUse'][1]['hooks'][0]['command'])
    def test_claude_failure_and_no_allow_bypass(self):
        doc=updated_hooks({},['python','hook.py'],'claude')
        self.assertIn('PostToolUseFailure',doc['hooks'])
        self.assertEqual(doc['hooks']['PreToolUse'][0]['hooks'][0]['onFailure'],'block')
    def test_install_readback_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'hooks.json'
            self.assertTrue(install_json(p,['python','hook.py'],'codex'))
            self.assertFalse(install_json(p,['python','hook.py'],'codex'))
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            link=Path(tmp)/'link';link.symlink_to(p)
            with self.assertRaises(RuntimeError):install_json(link,['python','hook.py'],'codex')

if __name__=='__main__':unittest.main()
