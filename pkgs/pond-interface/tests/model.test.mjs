// Minimap model (overlay/shell/hypr/Model.js), run with `node --test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../overlay/shell/hypr/Model.js", import.meta.url), "utf8")
  .replace(/^\.pragma library\s*$/m, "");
const Model = new Function(source + "\nreturn { isAgent, isWorking, needsAttention, workspaceRecord, creationRows,"
  + " computeRows, workspaceForNumber, slotOf };")();

const appInfo = appId => ({ iconName: appId, iconSource: "icon:" + appId });
const ws = (id, output, extra = {}) => Model.workspaceRecord(Object.assign(
  { id, name: String(id), output, isActive: false, isFocused: false, lastWindow: "" }, extra), {});
const win = (id, workspaceId, order, extra = {}) => Object.assign(
  { id, workspaceId, order, appId: "app", title: "", serial: 1 }, extra);

test("agent detection uses markers, not a terminal's mere presence", () => {
  const ghostty = title => ({ appId: "com.mitchellh.ghostty", title });
  assert.equal(Model.isAgent(ghostty("~/nix-config")), false);
  assert.equal(Model.isAgent(ghostty("fish /home/cjm")), false);
  assert.equal(Model.isAgent(ghostty("✳ Claude Code")), true);
  assert.equal(Model.isAgent(ghostty("⠙ codex")), true);
  assert.equal(Model.isAgent(ghostty("[!] approve?")), true);
  assert.equal(Model.isAgent(ghostty("codex")), true);
  assert.equal(Model.isAgent({ appId: "zen", title: "✳ Claude Code" }), false);
});

test("rows: occupied and selected workspaces per output, plus one creation row", () => {
  const workspaces = [ws(1, "DP-1", { isActive: true, isFocused: true }), ws(3, "DP-1"),
                      ws(2, "HDMI-A-1", { isActive: true })];
  const windows = { "0xa": win("0xa", 1, 0), "0xb": win("0xb", 3, 0), "0xc": win("0xc", 2, 0) };
  const { rows, creation } = Model.computeRows(workspaces, windows, ["DP-1", "HDMI-A-1"], appInfo, {});
  assert.deepEqual(rows.map(r => [r.outputName, r.workspaceId, r.sidebarWorkspaceNumber, r.isCreationRow]),
    [["DP-1", 1, 1, false], ["DP-1", 3, 2, false], ["DP-1", 4, 0, true],
     ["HDMI-A-1", 2, 1, false], ["HDMI-A-1", 5, 0, true]]);
  // Virtual creation IDs never collide across outputs.
  assert.deepEqual(creation["DP-1"], { id: 4, output: "DP-1", virtual: true });
  assert.deepEqual(creation["HDMI-A-1"], { id: 5, output: "HDMI-A-1", virtual: true });
});

test("a selected empty workspace is the creation row and is numbered", () => {
  const workspaces = [ws(1, "DP-1"), ws(6, "DP-1", { isActive: true, isFocused: true })];
  const { rows } = Model.computeRows(workspaces, { "0xa": win("0xa", 1, 0) }, ["DP-1"], appInfo, {});
  assert.deepEqual(rows.map(r => [r.workspaceId, r.sidebarWorkspaceNumber, r.isCreationRow, r.isActive]),
    [[1, 1, false, false], [6, 2, true, true]]);
});

test("window records follow adapter order and mark agent terminals", () => {
  const windows = {
    "0x2": win("0x2", 1, 1, { appId: "com.mitchellh.ghostty", title: "⠋ codex", serial: 6 }),
    "0x1": win("0x1", 1, 0, { appId: "zen-beta" }),
    "0x3": win("0x3", 1, 2, { appId: "com.mitchellh.ghostty", title: "✳ Claude Code", serial: 3 }),
  };
  const { rows } = Model.computeRows([ws(1, "DP-1", { isActive: true, isFocused: true })], windows,
                                     ["DP-1"], appInfo, { "0x3": true });
  const records = JSON.parse(rows[0].windowsJson);
  assert.deepEqual(records.map(r => [r.winId, r.dotState, r.iconSource]),
    [["0x1", "", "icon:zen-beta"], ["0x2", "working", ""], ["0x3", "unread", ""]]);
  assert.deepEqual(records.map(r => r.loaderVariant), [2, 3, 4]);
});

test("ListModel roles keep stable types (string window IDs)", () => {
  const { rows } = Model.computeRows([ws(1, "DP-1", { isActive: true, lastWindow: "" })], {}, ["DP-1"], appInfo, {});
  for (const row of rows) assert.equal(typeof row.activeWindowId, "string");
  const record = Model.workspaceRecord({ id: 2, output: "DP-1", lastWindow: "0xa" }, { "0xa": {} });
  assert.equal(record.activeWindowId, "0xa");
  assert.equal(Model.workspaceRecord({ id: 2, output: "DP-1", lastWindow: "0xgone" }, {}).activeWindowId, "");
});

test("Super+N maps to sidebar numbers on the focused output", () => {
  const workspaces = [ws(2, "DP-1", { isActive: true, isFocused: true }), ws(5, "DP-1"), ws(3, "HDMI-A-1", { isActive: true })];
  const windows = { a: win("a", 2, 0), b: win("b", 5, 0), c: win("c", 3, 0) };
  const { rows } = Model.computeRows(workspaces, windows, ["DP-1", "HDMI-A-1"], appInfo, {});
  assert.equal(Model.workspaceForNumber(rows, "DP-1", 1).workspaceId, 2);
  assert.equal(Model.workspaceForNumber(rows, "DP-1", 2).workspaceId, 5);
  assert.equal(Model.workspaceForNumber(rows, "DP-1", 3).workspaceId, 6); // creation row
  assert.equal(Model.workspaceForNumber(rows, "DP-1", 4), null);
  assert.equal(Model.workspaceForNumber(rows, "HDMI-A-1", 1).workspaceId, 3);
});

test("slotOf reports a window's position in its row", () => {
  const windows = { a: win("a", 1, 0), b: win("b", 1, 1), c: win("c", 2, 0) };
  assert.equal(Model.slotOf(windows, "b"), 1);
  assert.equal(Model.slotOf(windows, "c"), 0);
  assert.equal(Model.slotOf(windows, "gone"), -1);
});

// Cross-display drag geometry and intent (overlay/shell/minimap/DragModel.js).
const dragSource = readFileSync(new URL("../overlay/shell/minimap/DragModel.js", import.meta.url), "utf8")
  .replace(/^\.pragma library\s*$/m, "");
const Drag = new Function(dragSource + "\nreturn { screenAt, toScene, toGlobal, slotAt, rowAt, target, sameTarget,"
  + " commit };")();

test("global pointer resolves to the display under it", () => {
  const rects = [{ x: 0, y: 0, width: 1920, height: 1080 }, { x: 1920, y: -200, width: 2560, height: 1440 }];
  assert.equal(Drag.screenAt(rects, 10, 10).index, 0);
  assert.equal(Drag.screenAt(rects, 1920, 0).index, 1);   // a shared edge belongs to the right display
  assert.equal(Drag.screenAt(rects, 2000, -150).index, 1);
  assert.equal(Drag.screenAt(rects, 10, 1100), null);     // below DP-1, beside nothing
  // A pointer outside the source window maps into the other window's scene.
  const global = Drag.toGlobal(rects[0], 1980, 300);
  assert.deepEqual(Drag.toScene(rects[1], global.x, global.y), { x: 60, y: 500 });
});

test("slots and rows follow the carousel", () => {
  assert.equal(Drag.slotAt(0, 0, 48, 8, 3), 0);
  assert.equal(Drag.slotAt(45, 0, 48, 8, 3), 1);          // the gap before an icon belongs to it
  assert.equal(Drag.slotAt(500, 0, 48, 8, 3), 3);         // clamped after the last remaining window
  assert.equal(Drag.slotAt(45, -48, 48, 8, 9), 2);        // scrolled carousel
  assert.equal(Drag.rowAt([10, 58, null, 106], 48, 60), 1);
  assert.equal(Drag.rowAt([10, 58, null, 106], 48, 200), -1);
});

test("a drop commits the destination's output and creation intent", () => {
  const source = { windowData: { winId: "0xa1" }, workspaceId: 1, slot: 0, output: "DP-1" };
  assert.equal(Drag.commit(source, Drag.target("DP-1", 1, 0, false)), null);   // back where it was
  assert.equal(Drag.commit(source, null), null);
  assert.deepEqual(Drag.commit(source, Drag.target("HDMI-A-1", 4, 0, true)),
    { window: "0xa1", workspace: 4, slot: 0, output: "HDMI-A-1", create: true });
  assert.deepEqual(Drag.commit(source, Drag.target("DP-1", 1, 2, false)),
    { window: "0xa1", workspace: 1, slot: 2, output: "DP-1", create: false });
  assert.ok(Drag.sameTarget(Drag.target("DP-1", 1, 2, false), Drag.target("DP-1", 1, 2, false)));
  assert.ok(!Drag.sameTarget(Drag.target("DP-1", 4, 0, true), Drag.target("HDMI-A-1", 4, 0, true)));
  assert.ok(Drag.sameTarget(null, null) && !Drag.sameTarget(null, Drag.target("DP-1", 1, 0, false)));
});
