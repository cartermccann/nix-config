#!/usr/bin/env python3
"""Install only this guard's targeted hooks, preserving unrelated settings."""
import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile

MATCHER = '.*[Gg][Mm][Aa][Ii][Ll].*|mcp__codex_apps__monday_com_get_updates|codex_apps\\.monday_com\\.get_updates'


def atomic(path, content, mode=0o600):
    path=Path(path)
    if path.is_symlink():raise RuntimeError('Refusing a symlink-managed target')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),mode);f.write(content);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def updated_hooks(original, command, runtime):
    document=json.loads(json.dumps(original));hooks=document.setdefault('hooks',{})
    if not isinstance(hooks,dict):raise ValueError('Invalid hooks object')
    mapping={'PreToolUse':'pre','PostToolUse':'post'}
    if runtime=='claude':mapping['PostToolUseFailure']='failure'
    marker=' --runtime '+runtime+' --event '
    base=' '.join(shlex.quote(x) for x in command)+' --installation-id agent-tool-guard-v1'
    for event,phase in mapping.items():
        items=hooks.setdefault(event,[])
        if not isinstance(items,list):raise ValueError('Invalid hook list')
        expected=base+marker+phase
        handler={'type':'command','command':expected,'timeout':10}
        if runtime=='claude' and phase=='pre':handler['onFailure']='block'
        found=False
        for item in items:
            if isinstance(item,dict) and item.get('matcher')==MATCHER:
                handlers=item.get('hooks')
                if not isinstance(handlers,list):continue
                for index,old in enumerate(handlers):
                    if isinstance(old,dict) and isinstance(old.get('command'),str) and ' --installation-id agent-tool-guard-v1' in old['command'] and marker+phase in old['command']:
                        handlers[index]=handler;found=True
        if not found:items.append({'matcher':MATCHER,'hooks':[handler]})
    return document


def install_json(path,command,runtime):
    path=Path(path)
    if path.is_symlink():raise RuntimeError('Refusing a symlink-managed settings file')
    before=path.read_text() if path.exists() else None
    doc=json.loads(before) if before else {}
    updated=updated_hooks(doc,command,runtime)
    after=json.dumps(updated,indent=2)+'\n'
    if doc==updated:return False
    if (path.read_text() if path.exists() else None)!=before:raise RuntimeError('Concurrent settings edit detected')
    mode=stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    atomic(path,after,mode)
    if json.loads(path.read_text())!=updated:raise RuntimeError('Settings read-back mismatch')
    return True


def install_plugin(path,command):
    template=Path(__file__).with_name('opencode-plugin.template.js').read_text()
    content=template.replace('__GUARD_COMMAND__',json.dumps(command))
    path=Path(path)
    if path.exists() and path.read_text()==content:return False
    if path.exists() and "id: 'local.agent-tool-guard'" not in path.read_text():raise RuntimeError('Unrelated plugin already occupies target')
    atomic(path,content)
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--python',required=True);p.add_argument('--hook',type=Path,required=True)
    p.add_argument('--codex-home',type=Path,action='append',default=[])
    p.add_argument('--claude-settings',type=Path)
    p.add_argument('--opencode-plugin',type=Path)
    a=p.parse_args();command=[a.python,str(a.hook.resolve())];changed=[]
    for home in a.codex_home:
        if install_json(home/'hooks.json',command,'codex'):changed.append('codex hook configuration')
    if a.claude_settings and install_json(a.claude_settings,command,'claude'):changed.append('claude hook configuration')
    if a.opencode_plugin and install_plugin(a.opencode_plugin,command):changed.append('opencode local plugin')
    print(json.dumps({'changed':changed,'hook_approval_required':'Review new command hooks with Codex /hooks in each configured home. No trust records were changed.'}))

if __name__=='__main__':main()
