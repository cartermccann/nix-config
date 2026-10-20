import { readFile, mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { strict as assert } from 'node:assert';
const root = await mkdtemp(path.join(os.tmpdir(),'guard-plugin-'));
process.env.AGENT_TOOL_GUARD_STATE=path.join(root,'state','state.sqlite');
try {
  const source=(await readFile(new URL('./opencode-plugin.template.js',import.meta.url),'utf8')).replace('__GUARD_COMMAND__',JSON.stringify([process.env.GUARD_PYTHON || 'python3',new URL('./hook.py',import.meta.url).pathname]));
  const {default:plugin}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const hooks={};
  await plugin.setup({tool:{hook:async(name,fn)=>{hooks[name]=fn;}}});
  const monday={tool:'mcp__codex_apps__monday_com_get_updates',sessionID:'s',id:'bad',input:{objectIds:['1']}};
  await assert.rejects(hooks['execute.before'](monday),/Invalid arguments/);
  const event={tool:'mcp__codex_apps__gmail_search_email_ids',sessionID:'s',id:'one',input:{link_id:'fake',query:'test'}};
  await hooks['execute.before'](event);
  await hooks['execute.after']({...event,status:'error',error:new Error('RATE_LIMIT_EXCEEDED')});
  await assert.rejects(hooks['execute.before']({...event,id:'two'}),/cooling down/);
  const legacy=await plugin.server();
  await assert.rejects(legacy['tool.execute.before']({...monday,callID:'bad'}, {args:monday.input}),/Invalid arguments/);
  process.env.AGENT_TOOL_GUARD_STATE=path.join(root,'legacy','state.sqlite');
  const old={tool:event.tool,sessionID:'legacy',callID:'one'};
  await legacy['tool.execute.before'](old,{args:event.input});
  await legacy['tool.execute.after'](old,{isError:true,content:[{type:'text',text:'RATE_LIMIT_EXCEEDED'}]});
  await assert.rejects(legacy['tool.execute.before']({...old,callID:'two'},{args:event.input}),/cooling down/);
  console.log('OpenCode V2 and legacy hook integration: 5 assertions passed');
} finally {await rm(root,{recursive:true,force:true});}
