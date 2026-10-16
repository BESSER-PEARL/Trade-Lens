const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("../../app/frontend/node_modules/typescript");

function page(timeOrigin, savedStorage) {
  const question = "Show our exports to the United States. Show the months since 2023";
  const storage = savedStorage || new Map([["lux.pendingAsk", JSON.stringify({ q: question, at: 200 })]]);
  const exports = {};
  const source = fs.readFileSync(path.join(__dirname, "../../app/frontend/src/components/Welcome.tsx"), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  vm.runInNewContext(code, {
    exports,
    require: () => ({}),
    performance: { timeOrigin },
    sessionStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
  });
  return { ...exports, question, storage };
}

test("the outgoing landing page leaves the question for the reloaded page", () => {
  const p = page(100);
  p.leavePendingAsk(p.question);
  assert.equal(p.sendPendingAsk(() => assert.fail("sent before reload")), false);
  assert.ok(p.storage.has("lux.pendingAsk"));
});

test("a disconnected chat retains the full question and sends it once after reconnect", () => {
  const p = page(300);
  assert.equal(p.sendPendingAsk(() => false), false);
  assert.ok(p.storage.has("lux.pendingAsk"));
  const sent = [];
  assert.equal(p.sendPendingAsk((q) => { sent.push(q); return true; }), true);
  assert.deepEqual(sent, [p.question]);
  assert.equal(p.sendPendingAsk(() => assert.fail("duplicate send")), false);
});

test("a send exception leaves the question available for retry", () => {
  const p = page(300);
  assert.equal(p.sendPendingAsk(() => { throw new Error("socket closed"); }), false);
  assert.ok(p.storage.has("lux.pendingAsk"));
  assert.equal(p.sendPendingAsk(() => true), true);
});

test("a reloaded page forwards the question even when the browser masks the navigation clock", () => {
  const p = page(0);
  const sent = [];
  assert.equal(p.sendPendingAsk((q) => { sent.push(q); return true; }), true);
  assert.deepEqual(sent, [p.question]);
});

test("the landing-to-chat handoff works with identical, rounded navigation timestamps", () => {
  const landing = page(200);
  landing.leavePendingAsk(landing.question);
  assert.equal(landing.sendPendingAsk(() => assert.fail("sent before reload")), false);
  const dashboard = page(200, landing.storage);
  const sent = [];
  assert.equal(dashboard.sendPendingAsk((q) => { sent.push(q); return true; }), true);
  assert.deepEqual(sent, [landing.question]);
  assert.equal(dashboard.sendPendingAsk(() => assert.fail("duplicate")), false);
});
