import { spawn } from 'node:child_process';

const command = __GUARD_COMMAND__;
const target = (name) => /(^|[_.-])gmail([_.-]|$)/i.test(name) || ['mcp__codex_apps__monday_com_get_updates', 'codex_apps.monday_com.get_updates'].includes(name);

function invoke(event, data) {
  return new Promise((resolve, reject) => {
    const child = spawn(command[0], [...command.slice(1), '--runtime', 'generic', '--event', event], { stdio: ['pipe', 'pipe', 'ignore'] });
    let output = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Tool guard timed out; request was not approved.')); }, 8000);
    child.on('error', () => { clearTimeout(timer); reject(new Error('Tool guard unavailable; request was not approved.')); });
    child.stdout.on('data', (chunk) => {
      output += chunk;
      if (output.length > 16384) { child.kill(); clearTimeout(timer); reject(new Error('Invalid tool guard response.')); }
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (event !== 'pre') return resolve({});
      let decision;
      try { decision = JSON.parse(output); } catch { return reject(new Error('Tool guard did not approve the request.')); }
      if (code !== 0 || decision.allowed !== true) return reject(new Error(decision.message || 'Tool guard blocked this call.'));
      resolve(decision);
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(data));
  });
}

const identity = (event, args) => ({ tool_name: event.tool, session_id: event.sessionID, tool_use_id: event.id ?? event.callID, tool_input: args });
const errorText = (error) => String(error?.message ?? error ?? '').slice(0, 65536);

export default {
  id: 'local.agent-tool-guard',
  async setup(ctx) {
    await ctx.tool.hook('execute.before', async (event) => {
      if (target(event.tool)) await invoke('pre', identity(event, event.input));
    });
    await ctx.tool.hook('execute.after', async (event) => {
      if (!target(event.tool)) return;
      const failed = event.status === 'error';
      try { await invoke(failed ? 'failure' : 'post', { ...identity(event, event.input), ...(failed ? { error: errorText(event.error) } : {}) }); }
      catch { /* A completion-recording failure leaves the bounded lease to expire. */ }
    });
  },
  async server() {
    return {
      'tool.execute.before': async (input, output) => {
        if (target(input.tool)) await invoke('pre', identity(input, output.args));
      },
      'tool.execute.after': async (input, result) => {
        if (!target(input.tool)) return;
        const failed = result?.isError === true;
        const error = failed ? (result.content ?? []).filter((part) => part.type === 'text').map((part) => part.text).join('\n').slice(0, 65536) : undefined;
        try { await invoke(failed ? 'failure' : 'post', { ...identity(input, input.args), ...(failed ? { error } : {}) }); }
        catch { /* The shared lease expires if completion cannot be recorded. */ }
      },
    };
  },
};
