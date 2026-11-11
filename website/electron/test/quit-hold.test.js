const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { createQuitHold } = require("../quit-hold");

function harness({ timeoutMs = 30_000 } = {}) {
  const quits = [];
  const logs = [];
  const timers = [];
  const hold = createQuitHold({
    app: { quit: () => quits.push("quit") },
    log: (line) => logs.push(line),
    timeoutMs,
    setTimeoutFn: (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t; },
    clearTimeoutFn: (t) => { if (t) t.cleared = true; },
  });
  return { hold, quits, logs, timers };
}

function quitEvent() {
  return { prevented: 0, preventDefault() { this.prevented += 1; } };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const flush = () => new Promise((resolve) => { setImmediate(resolve); });

test("a quit with nothing to stop is not held", () => {
  const { hold, quits } = harness();
  hold.track(null);
  const event = quitEvent();
  hold.onWillQuit(event);
  assert.strictEqual(event.prevented, 0);
  assert.strictEqual(quits.length, 0);
});

test("the exit is held until the gateway stop settles, then the quit is replayed once", async () => {
  const { hold, quits } = harness();
  const stop = deferred();
  hold.track(stop.promise);

  const first = quitEvent();
  hold.onWillQuit(first);
  assert.strictEqual(first.prevented, 1, "exiting now would orphan the gateway");
  assert.strictEqual(quits.length, 0);

  // A second quit request while the stop is still running is held as well.
  const impatient = quitEvent();
  hold.onWillQuit(impatient);
  assert.strictEqual(impatient.prevented, 1);

  stop.resolve();
  await flush();
  assert.deepStrictEqual(quits, ["quit"], "one replay, however many quits were held");

  // The replayed quit passes, even though its before-quit starts a new stop.
  hold.track(deferred().promise);
  const replayed = quitEvent();
  hold.onWillQuit(replayed);
  assert.strictEqual(replayed.prevented, 0);
});

test("a stop that already settled before the windows closed does not hold the quit", async () => {
  const { hold, quits } = harness();
  hold.track(Promise.resolve());
  await flush();
  const event = quitEvent();
  hold.onWillQuit(event);
  assert.strictEqual(event.prevented, 0);
  assert.strictEqual(quits.length, 0);
});

test("a rejected stop still releases the quit", async () => {
  const { hold, quits } = harness();
  const stop = deferred();
  hold.track(stop.promise);
  hold.onWillQuit(quitEvent());
  stop.reject(new Error("stop failed"));
  await flush();
  assert.deepStrictEqual(quits, ["quit"]);
});

test("a stop that never settles is abandoned at the timeout", async () => {
  const { hold, quits, timers, logs } = harness({ timeoutMs: 1234 });
  hold.track(deferred().promise);
  hold.onWillQuit(quitEvent());
  assert.strictEqual(timers.length, 1);
  assert.strictEqual(timers[0].ms, 1234);
  timers[0].fn();
  assert.deepStrictEqual(quits, ["quit"]);
  assert.ok(logs.some((line) => line.includes("did not finish within 1234ms")));
});

test("a later quit tracks the stop of a gateway started after an earlier stop settled", async () => {
  // An update lane prevents before-quit, so will-quit never fires for that quit;
  // if the install then fails the gateway is started again and a later quit
  // must wait for THAT gateway.
  const { hold, quits } = harness();
  hold.track(Promise.resolve());
  await flush();
  const later = deferred();
  hold.track(later.promise);
  const event = quitEvent();
  hold.onWillQuit(event);
  assert.strictEqual(event.prevented, 1);
  later.resolve();
  await flush();
  assert.deepStrictEqual(quits, ["quit"]);
});

test("main.js hands the quit-time gateway stop to the hold and consults it on will-quit", () => {
  // The module is inert unless both lifecycle handlers call it.
  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  assert.match(main, /quitHold\.track\(gateway\.stopOnQuit\(\)\)/);
  assert.match(main, /app\.on\("will-quit", \(event\) => \{[\s\S]*?quitHold\.onWillQuit\(event\)/);
});
