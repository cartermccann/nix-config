// Execute the adapter's actual callbacks with a recording launcher boundary.
// The session helper owns endpoint arbitration; no QML path may bypass it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const qml = readFileSync(new URL('../overlay/shell/NiriMsg.qml', import.meta.url), 'utf8');
function callback(name) {
  const start = qml.indexOf(`  function ${name}(`);
  assert.ok(start >= 0);
  const end = qml.indexOf('\n  function ', start + 1);
  return qml.slice(start, end);
}
function setup() {
  const calls = [];
  const context = vm.createContext({
    Quickshell: { execDetached: argv => calls.push(Array.from(argv)) },
    workspaces: [{id: 1, isFocused: true}, {id: 2, isFocused: false}],
    _isCreation: id => id === 3,
    _intent: () => ({}),
    _request: (action, args, done) => done(true),
    _windows: {}, _unread: {}, monitors: [], _focusedOutput: '',
    keyboardLayouts: [], keyboardLayoutIndex: 0,
    _haveSnapshot: true, _focusedWorkspaceId: 1,
    _workspaceLauncherOpen: false, dragOrigin: null,
    _windowRecord: w => w, _trackUnread: () => {}, _recompute: () => {},
    Model: { workspaceRecord: ws => ws },
  });
  vm.runInContext(callback('openLauncher') + callback('_applySnapshot'), context);
  return {context, calls};
}

test('sidebar launcher requests use the ownership guard on focused, other and new workspaces', () => {
  const {context, calls} = setup();
  for (const id of [1, 2, 3]) context.openLauncher(id);
  context.openLauncher(99);
  assert.deepEqual(calls, Array.from({length: 3}, () => ['pond-session', 'launcher', 'open']));
  calls.length = 0;
  context._request = (action, args, done) => done(false);
  context.openLauncher(2);
  assert.deepEqual(calls, []);
});

test('empty-workspace open and subsequent occupied-workspace close use the same guard', () => {
  const {context, calls} = setup();
  const snapshot = {windows: [], monitors: [], keyboard: {names: [], index: 0},
    workspaces: [{id: 2, isFocused: true}]};
  context._applySnapshot(snapshot);
  snapshot.windows = [{id: '0xa', workspaceId: 2}];
  context._applySnapshot(snapshot);
  assert.deepEqual(calls, [['pond-session', 'launcher', 'open'], ['pond-session', 'launcher', 'close']]);
});
