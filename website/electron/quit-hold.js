"use strict";

// Longest the app stays alive after its last window closed, waiting for the
// gateway stop. The stop bounds itself (15s graceful budget, 3s hard timer,
// then the tree sweep's tail); this is the backstop for a stop that never
// settles, so a quit can be late but never ignored.
const QUIT_HOLD_TIMEOUT_MS = 30_000;

/**
 * Keep the app process alive until the gateway stop begun on `before-quit`
 * has finished.
 *
 * `before-quit` cannot await: a handler that only STARTS the stop returns, the
 * windows close, and Electron exits with the stop still in flight. Whatever
 * the stop had not done yet is then never done. On Windows that is the usual
 * outcome rather than a rare one, because a child does not die with its
 * parent there: the gateway and its kiro-cli / MCP descendants live on,
 * holding the port and the data home's locks, and the next launch finds the
 * port in use.
 *
 * The hold sits on `will-quit`, not `before-quit`, because the update lanes
 * hold `before-quit` themselves and replay the quit when their install work is
 * done. A second holder there would replay the quit underneath them. A quit
 * an update lane prevented never reaches `will-quit`, and the quit it issues
 * later arrives with the gateway already stopped, so this hold stays out of
 * the way of both.
 *
 * @param {object} deps
 * @param {{quit: () => void}} deps.app
 * @param {(line: string) => void} [deps.log]
 * @param {number} [deps.timeoutMs]
 * @param {typeof setTimeout} [deps.setTimeoutFn]
 * @param {typeof clearTimeout} [deps.clearTimeoutFn]
 */
function createQuitHold({
  app,
  log = () => {},
  timeoutMs = QUIT_HOLD_TIMEOUT_MS,
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout,
}) {
  // The stop in flight, or null. Cleared when it settles, so a later quit
  // (after an update install failed and the gateway was started again) tracks
  // the stop of that later gateway.
  let pending = null;
  let waiting = false;
  // Latched by the replayed quit: that quit must pass even if its own
  // `before-quit` tracked something new, or a hold could repeat forever.
  let released = false;

  /**
   * Record the stop a `before-quit` handler started. `null`/`undefined` means
   * there was nothing to stop, and nothing is held for it.
   * @param {Promise<unknown>|null|undefined} stop
   */
  function track(stop) {
    if (released || pending || !stop || typeof stop.then !== "function") return;
    const tracked = Promise.resolve(stop).catch(() => {}).then(() => {
      if (pending === tracked) pending = null;
    });
    pending = tracked;
  }

  /** The `will-quit` handler. Prevents the exit while a tracked stop is in flight. */
  function onWillQuit(event) {
    if (released || !pending) return;
    event.preventDefault();
    if (waiting) return;
    waiting = true;
    log("quit: waiting for the gateway to stop before exiting");
    let timer = null;
    const release = (reason) => {
      if (released) return;
      released = true;
      clearTimeoutFn(timer);
      log(`quit: ${reason} — exiting`);
      app.quit();
    };
    timer = setTimeoutFn(
      () => release(`the gateway stop did not finish within ${timeoutMs}ms`),
      timeoutMs,
    );
    pending.then(() => release("gateway stopped"));
  }

  return { track, onWillQuit };
}

module.exports = { createQuitHold, QUIT_HOLD_TIMEOUT_MS };
