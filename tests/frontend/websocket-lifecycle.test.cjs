const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("../../app/frontend/node_modules/typescript");

// Exercise the component's actual socket effect, including React's setup/cleanup/setup lifecycle.
const file = ts.createSourceFile("AgentComponent.tsx", fs.readFileSync(
  path.join(__dirname, "../../app/frontend/src/components/AgentComponent.tsx"), "utf8"),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let effect;
function find(node) {
  if (ts.isCallExpression(node) && node.expression.getText(file) === "useEffect" &&
      node.arguments[1] && ts.isArrayLiteralExpression(node.arguments[1]) &&
      node.arguments[1].elements.some((item) => item.getText(file) === "agentUrl")) {
    effect = node.arguments[0].getText(file);
  }
  ts.forEachChild(node, find);
}
find(file);
assert.ok(effect, "socket effect exists");
const code = ts.transpileModule(`exports.start = ${effect}`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;

function chat(enabled = true) {
  const sockets = [];
  const timers = [];
  const state = { connected: false, connecting: false, waiting: false, active: null };
  class Socket {
    constructor(url) { this.url = url; sockets.push(this); }
    close() { this.closed = true; }
  }
  const context = {
    exports: {}, enabled, agentUrl: "ws://localhost:8766?sid=test", wsRef: { current: null },
    WebSocket: Socket,
    setIsConnected: (value) => { state.connected = value; },
    setIsConnecting: (value) => { state.connecting = value; },
    setWaiting: (value) => { state.waiting = value; },
    setActive: (value) => { state.active = value; },
    readModeChange: (payload) => payload.mode === "idle" ? null : { agent: payload.mode },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {}, console,
  };
  vm.runInNewContext(code, context);
  return { start: context.exports.start, sockets, timers, state };
}

test("the landing picker does not open a chat connection", () => {
  const c = chat(false);
  assert.equal(c.start(), undefined);
  assert.equal(c.sockets.length, 0);
});

test("late events from the discarded StrictMode connection cannot disable the live chat", () => {
  const c = chat();
  const cleanup = c.start();
  const old = c.sockets[0];
  cleanup();
  c.start();
  const live = c.sockets[1];
  live.onopen();
  old.onclose();
  assert.equal(c.state.connected, true);
  assert.equal(c.timers.length, 0);
  old.onmessage({ data: JSON.stringify({ type: "mode_change", mode: "visualizer" }) });
  assert.equal(c.state.waiting, false);
  live.onmessage({ data: JSON.stringify({ type: "mode_change", mode: "visualizer" }) });
  assert.equal(c.state.waiting, true);
  assert.equal(c.state.active.agent, "visualizer");
});

test("a late open from a retired connection cannot mark the new socket as ready", () => {
  const c = chat();
  const cleanup = c.start();
  const old = c.sockets[0];
  cleanup();
  c.start();
  old.onopen();
  assert.equal(c.state.connected, false);
});

test("a live disconnection reconnects and receives working-state messages", () => {
  const c = chat();
  c.start();
  c.sockets[0].onopen();
  c.sockets[0].onclose();
  assert.equal(c.state.connected, false);
  assert.equal(c.timers.length, 1);
  c.timers[0]();
  c.sockets[1].onopen();
  assert.equal(c.state.connected, true);
  c.sockets[1].onmessage({ data: JSON.stringify({ type: "mode_change", mode: "visualizer" }) });
  assert.equal(c.state.waiting, true);
});
