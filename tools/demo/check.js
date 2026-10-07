#!/usr/bin/env node
/* Browser check of the static demo (docs/index.html), with no dependencies (Node >= 22).
 *
 *   node tools/demo/check.js [OUT_DIR]
 *
 * Launches google-chrome (or $CHROME) headless with --remote-debugging-port, opens the demo
 * via file://, and drives it through the Chrome DevTools Protocol: every map x every problem
 * (all interval cases, anchored and free) x single/multi agent, every preset, each witness
 * played to the end, clicks on the map, undo/clear, the theme toggle, reduced motion, dark
 * mode, desktop (1280x900) and phone (375x812, 360x740) widths, the slider's aria-valuetext,
 * Problem 2's shading (none outside the sensors' interval), and (hover: none) touch screens.  It fails (exit 1) on any
 * console error or warning, uncaught exception, network request that is not file://, or
 * horizontal overflow at phone width.  Screenshots go to OUT_DIR (default: a new temp dir).
 */
"use strict";

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "..", "..");
const INDEX = pathToFileURL(path.join(ROOT, "docs", "index.html")).href;
const OUT = path.resolve(process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), "cd-demo-shots-")));
const CHROME = process.env.CHROME || "google-chrome";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const failures = [];
function check(cond, msg) {
  if (!cond) { failures.push(msg); console.log("  FAIL " + msg); }
}

// ------------------------------------------------------------------ Chrome + CDP

// Chrome occasionally hangs at startup without writing DevToolsActivePort and then ignores
// SIGTERM; kill such an instance hard and try again rather than leaking it.
async function launch() {
  for (let attempt = 1; ; attempt++) {
    try {
      return await launchOnce();
    } catch (e) {
      if (attempt >= 3) throw e;
      console.log("  (" + e.message + "; retrying)");
    }
  }
}

function killChrome(proc, profile) {
  try { proc.kill("SIGKILL"); } catch (e) { /* already gone */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
}

async function launchOnce() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "cd-chrome-"));
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--disable-background-networking", "--disable-component-update",
    "--disable-sync", "--disable-default-apps", "--hide-scrollbars", "--mute-audio",
    "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  proc.stderr.on("data", () => {});
  const portFile = path.join(profile, "DevToolsActivePort");
  for (let i = 0; i < 200 && !fs.existsSync(portFile); i++) await sleep(50);
  if (!fs.existsSync(portFile)) { killChrome(proc, profile); throw new Error("Chrome did not start (" + CHROME + ")"); }
  let port = "";
  for (let i = 0; i < 50 && !port; i++) { port = fs.readFileSync(portFile, "utf8").split("\n")[0]; if (!port) await sleep(50); }
  let targets = [];
  for (let i = 0; i < 50; i++) {
    targets = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json();
    if (targets.some((t) => t.type === "page")) break;
    await sleep(100);
  }
  const page = targets.find((t) => t.type === "page");
  if (!page) { killChrome(proc, profile); throw new Error("Chrome has no page target"); }
  return { proc, profile, wsUrl: page.webSocketDebuggerUrl };
}

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.id = 0;
    this.pending = new Map();
    this.handlers = {};
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new Error(msg.error.message)); else p.resolve(msg.result);
      } else {
        (this.handlers[msg.method] || []).forEach((h) => h(msg.params));
      }
    };
  }
  open() { return new Promise((res, rej) => { this.ws.onopen = res; this.ws.onerror = rej; }); }
  send(method, params) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  on(method, fn) { (this.handlers[method] = this.handlers[method] || []).push(fn); }
  once(method) { return new Promise((res) => { const fn = (p) => { this.handlers[method] = this.handlers[method].filter((h) => h !== fn); res(p); }; this.on(method, fn); }); }
}

// ------------------------------------------------------------------ page helpers

// injected into the page: small DOM drivers (no access to the app's internals)
const HELPERS = `
window.__t = {
  sel(id, v) { const e = document.getElementById(id); e.value = String(v); e.dispatchEvent(new Event("change", {bubbles: true})); },
  agents(v) { const r = document.querySelector('input[name="agents"][value="' + v + '"]'); r.checked = true; r.dispatchEvent(new Event("change", {bubbles: true})); },
  check(id, on) { const e = document.getElementById(id); if (e.checked !== on) e.click(); },
  type(id, v) { const e = document.getElementById(id); e.value = v; e.dispatchEvent(new Event("input", {bubbles: true})); },
  click(id) { document.getElementById(id).click(); },
  fast() {
    const s = document.getElementById("speed");
    if (!s.querySelector('option[value="60"]')) { const o = document.createElement("option"); o.value = "60"; o.textContent = "60x"; s.appendChild(o); }
    s.value = "60";
  },
  state() {
    const q = (s) => document.querySelector(s);
    const scrub = q("#scrub"), marker = q("#map .marker");
    const nan = Array.from(document.querySelectorAll("#map *")).some((e) => Array.from(e.attributes).some((a) => /NaN|undefined/.test(a.value)));
    return {
      badge: q("#result .badge") ? q("#result .badge").textContent : null,
      headline: q("#result .headline") ? q("#result .headline").textContent : null,
      reason: q("#result .reason") ? q("#result .reason").textContent : null,
      path: q("#result dd.path") ? q("#result dd.path").textContent : null,
      original: q("#original").hidden ? null : q("#original").textContent,
      differs: !!q("#original .differs") && !q("#original").hidden,
      playing: q("#play").dataset.state, scrub: +scrub.value, scrubMax: +scrub.max, disabled: q("#play").disabled,
      caption: q("#step-caption").textContent, marker: marker.getAttribute("visibility"),
      valuetext: scrub.getAttribute("aria-valuetext"),
      regionLabels: q("#map .layer-region-labels").getAttribute("visibility"),
      possible: document.querySelectorAll("#map .possible").length,
      currentChips: document.querySelectorAll(".chip-current").length,
      stale: !q("#stale-note").hidden, nan,
      story: q("#story").value, history: q("#history").value, preset: q("#preset").value,
    };
  },
  async playToEnd(timeoutMs) {
    const t0 = Date.now(), play = document.getElementById("play");
    if (play.disabled) return "disabled";
    this.fast();
    if (play.dataset.state !== "playing") {
      const scrub = document.getElementById("scrub");
      if (+scrub.value >= +scrub.max) { scrub.value = "0"; scrub.dispatchEvent(new Event("input", {bubbles: true})); }
      play.click();
    }
    while (Date.now() - t0 < timeoutMs) {
      await new Promise((r) => setTimeout(r, 30));
      const scrub = document.getElementById("scrub");
      if (play.dataset.state === "paused" && +scrub.value >= +scrub.max) return "end";
    }
    return "timeout";
  },
  overflow() {
    const w = document.documentElement.clientWidth, bad = [];
    document.querySelectorAll("body *").forEach((e) => {
      const r = e.getBoundingClientRect();
      if (r.width && (r.right > w + 0.5 || r.left < -0.5) && !e.closest(".skip, .visually-hidden")) bad.push(e.tagName + (e.id ? "#" + e.id : "") + "." + e.className);
    });
    return { scrollWidth: document.documentElement.scrollWidth, clientWidth: w, bad: bad.slice(0, 8) };
  },
  center(sel) { const r = document.querySelector(sel).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; },
};
true;
`;

let running = null;  // the Chrome to kill if main() throws

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const { proc, profile, wsUrl } = await launch();
  running = { proc, profile };
  const cdp = new CDP(wsUrl);
  await cdp.open();
  const problems = [];
  cdp.on("Runtime.consoleAPICalled", (p) => {
    if (["error", "warning", "assert"].includes(p.type)) {
      problems.push("console." + p.type + ": " + p.args.map((a) => a.value !== undefined ? a.value : a.description).join(" "));
    }
  });
  cdp.on("Runtime.exceptionThrown", (p) => problems.push("exception: " + (p.exceptionDetails.exception ? p.exceptionDetails.exception.description : p.exceptionDetails.text)));
  cdp.on("Log.entryAdded", (p) => { if (["error", "warning"].includes(p.entry.level)) problems.push("log." + p.entry.level + ": " + p.entry.text + " " + (p.entry.url || "")); });
  const requests = [];
  cdp.on("Network.requestWillBeSent", (p) => requests.push(p.request.url));
  await Promise.all(["Runtime.enable", "Log.enable", "Network.enable", "Page.enable"].map((m) => cdp.send(m)));

  const evaluate = async (expr) => {
    const r = await cdp.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error("evaluate failed: " + expr + ": " + (r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text));
    return r.result.value;
  };
  const viewport = (w, h, mobile) => cdp.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile: !!mobile });
  const media = (features) => cdp.send("Emulation.setEmulatedMedia", { features });
  const load = async () => {
    const loaded = cdp.once("Page.loadEventFired");
    await cdp.send("Page.navigate", { url: INDEX });
    await loaded;
    await evaluate(HELPERS);
    await sleep(50);
  };
  const shot = async (name, full) => {
    await sleep(350);  // let CSS transitions settle
    let params = { format: "png" };
    if (full) {
      const m = await cdp.send("Page.getLayoutMetrics");
      const size = m.cssContentSize || m.contentSize;
      params = { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: size.width, height: size.height, scale: 1 } };
    }
    const r = await cdp.send("Page.captureScreenshot", params);
    fs.writeFileSync(path.join(OUT, name + ".png"), Buffer.from(r.data, "base64"));
  };
  const S = () => evaluate("__t.state()");
  const sel = (id, v) => evaluate("__t.sel(" + JSON.stringify(id) + "," + JSON.stringify(v) + ")");
  const realClick = async (selector) => {
    const [x, y] = await evaluate("__t.center(" + JSON.stringify(selector) + ")");
    const hit = await evaluate("(() => { const e = document.elementFromPoint(" + x + "," + y + "); return e && e.closest(" + JSON.stringify(selector) + ") ? true : (e ? e.tagName + '.' + e.getAttribute('class') + ' @' + scrollY + ' ' + innerWidth : 'none'); })()");
    check(hit === true, "the centre of " + selector + " is clickable (" + hit + " at " + x + "," + y + ")");
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    for (const type of ["mousePressed", "mouseReleased"]) {
      await cdp.send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
    }
  };

  const data = JSON.parse(await (async () => {
    await viewport(1280, 900, false);
    await media([{ name: "prefers-color-scheme", value: "light" }, { name: "prefers-reduced-motion", value: "no-preference" }]);
    await load();
    return evaluate("JSON.stringify({maps: CD_DATA.map_order, presets: CD_DATA.presets.map(p => ({id: p.id, consistent: p.expected.consistent, path: p.expected.path_string, bug: p.bug && p.bug.id, original: p.original || null}))})");
  })());

  // ---------------------------------------------------------------- first load
  console.log("load (desktop, light)");
  let s = await S();
  check(s.badge === "consistent", "initial run is consistent (got " + s.badge + ")");
  check(s.playing === "playing", "initial witness autoplays");
  check(await evaluate("__t.playToEnd(20000)") === "end", "initial witness plays to the end");
  await evaluate('document.getElementById("speed").value = "1"');
  await shot("desktop-light");
  await shot("desktop-light-full", true);

  // ---------------------------------------------------------------- map clicks, undo, clear
  console.log("map clicks, undo, clear");
  await evaluate('__t.click("clear")');
  s = await S();
  check(s.story === "" && s.history === "" && s.stale, "Clear empties story and history and marks the answer stale");
  await realClick('#map [data-kind="room"][data-name="A"]');
  await realClick('#map [data-kind="room"][data-name="C"]');
  await realClick('#map [data-kind="beam"][data-name="b1"]');
  await realClick('#map [data-kind="occupancy"][data-name="o1"]');
  await realClick('#map [data-kind="occupancy"][data-name="o1"]');
  s = await S();
  check(s.story === "AC", "room clicks build the story (got " + JSON.stringify(s.story) + ")");
  check(s.history === "b1 o1 o1", "sensor clicks build the history (got " + JSON.stringify(s.history) + ")");
  const chips = await evaluate('Array.from(document.querySelectorAll("#history-chips .chip")).map(c => c.textContent).join("|")');
  check(chips === "b1|o1 on|o1 off", "history chips show the occupancy toggle (got " + chips + ")");
  await evaluate('__t.click("undo")');
  s = await S();
  check(s.history === "b1 o1", "Undo removes the last click (got " + JSON.stringify(s.history) + ")");
  await evaluate('__t.click("run")');
  s = await S();
  check(s.badge === "malformed", "AC / b1 o1 is a malformed single-agent history (got " + s.badge + ")");
  await realClick('#map [data-kind="occupancy"][data-name="o1"]');
  await evaluate('__t.click("run")');
  s = await S();
  check(s.badge !== null && !s.stale, "Run after clicks gives a fresh answer");
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 1, y: 1 });  // no hover styles in later shots

  // ---------------------------------------------------------------- matrix
  console.log("maps x problems x agents");
  const variants = [];
  for (const map of data.maps) {
    for (const agents of ["single", "multi"]) {
      variants.push({ map, agents, problem: 1 });
      for (let c = 1; c <= 6; c++) variants.push({ map, agents, problem: 2, kase: c });
      variants.push({ map, agents, problem: 3, anchored: 1 });
      variants.push({ map, agents, problem: 3, anchored: 0 });
      variants.push({ map, agents, problem: 4 });
      variants.push({ map, agents, problem: 1, unreported: true });
    }
  }
  let played = 0, lastMap = null;
  for (const v of variants) {
    if (v.map !== lastMap) { await sel("map-select", v.map); lastMap = v.map; }
    await sel("problem", v.problem);
    if (v.kase) await sel("case", v.kase);
    if (v.anchored !== undefined) await sel("anchored", v.anchored);
    await evaluate('__t.agents("' + v.agents + '")');
    await evaluate('__t.check("unreported", ' + !!v.unreported + ")");
    await evaluate('__t.click("run")');
    s = await S();
    const label = JSON.stringify(v);
    check(["consistent", "inconsistent", "malformed"].includes(s.badge), label + ": verdict badge (got " + s.badge + ")");
    check(!s.nan, label + ": no NaN/undefined in the SVG");
    if (!s.disabled) {
      const end = await evaluate("__t.playToEnd(20000)");
      check(end === "end", label + ": playback reaches the end (" + end + ")");
      s = await S();
      if (s.badge === "consistent") {
        check(s.marker === "visible" && /^Step (\d+) of \1:/.test(s.caption), label + ": marker shown, last step reached (" + s.caption + ")");
        played++;
      }
    }
    check(!s.nan, label + ": no NaN/undefined in the SVG after playback");
  }
  console.log("  " + variants.length + " variants, " + played + " witnesses played");
  await evaluate('__t.check("unreported", false)');

  // ---------------------------------------------------------------- presets
  console.log("presets");
  for (const p of data.presets) {
    await sel("preset", p.id);
    s = await S();
    check(s.preset === p.id, p.id + ": preset selected");
    check((s.badge === "consistent") === p.consistent, p.id + ": verdict " + s.badge + " vs expected consistent=" + p.consistent);
    if (p.path) check(s.path === p.path, p.id + ": witness " + s.path + " vs " + p.path);
    if (p.bug) {
      check(s.differs && s.original.indexOf("Bug " + p.bug) >= 0, p.id + ": original's difference named " + p.bug);
      if (p.original.path_string) check(s.original.indexOf(p.original.path_string) >= 0, p.id + ": original's path shown");
    }
    if (!s.disabled) check(await evaluate("__t.playToEnd(20000)") === "end", p.id + ": playback reaches the end");
    if (p.id === "bug_B5") await shot("desktop-light-bug-B5");
    if (p.id === "icra_sec3a_p3") await shot("desktop-light-icra-p3");
  }

  // stepping and scrubbing
  console.log("stepping");
  await sel("preset", "icra_fig1_ABAC");
  await evaluate('__t.click("play")');  // pause the autoplay
  await evaluate('document.getElementById("scrub").value = "0"; document.getElementById("scrub").dispatchEvent(new Event("input"))');
  let steps = 0;
  for (let i = 0; i < 100; i++) {
    s = await S();
    if (s.scrub >= s.scrubMax) break;
    await evaluate('__t.click("step-fwd")');
    steps++;
  }
  s = await S();
  check(s.scrub >= s.scrubMax && steps >= 5, "step forward walks to the end (" + steps + " steps)");
  await evaluate('__t.click("step-back")');
  const back = await S();
  check(back.scrub < s.scrub && back.playing === "paused", "step back moves back");
  await evaluate('document.getElementById("scrub").value = "500"; document.getElementById("scrub").dispatchEvent(new Event("input"))');
  s = await S();
  check(s.marker === "visible" && s.currentChips >= 1, "scrubbing to the middle shows x and the current tokens");
  check(s.valuetext === s.caption && /^Step \d+ of \d+: /.test(s.valuetext || ""), "the slider's aria-valuetext is the step caption (" + s.valuetext + ")");
  await shot("desktop-light-scrub");

  // inconsistent: slots mode; its caption names region R4, so the region labels come on
  await evaluate('__t.check("show-regions", false)');
  check((await S()).regionLabels === "hidden", "region labels hidden");
  await sel("preset", "star_eq1_eq2_single");
  s = await S();
  check(!s.disabled && s.marker === "hidden" && s.possible > 0, "inconsistent answer still scrubs where x could be");
  check(/^Slot 1 of \d+: after 0 of 6 recordings/.test(s.valuetext || ""), "slots: the slider's aria-valuetext names the slot (" + s.valuetext + ")");
  check(s.regionLabels === "visible", "a preset whose caption names a region turns the region labels on");
  await shot("desktop-light-eq2-regions");
  await evaluate('__t.check("show-regions", false)');

  // verdict reasons use the chips' names for recordings
  await sel("preset", "icra_sec3a_p1");
  s = await S();
  check(/recording 5 \(b4\)/.test(s.reason || "") && !/\(b4 A\)/.test(s.reason || ""), "reasons name recordings like the chips (" + s.reason + ")");

  // Problem 2: no shading outside the sensors' interval, the walk inside the shading within it
  console.log("Problem 2 shading");
  await sel("preset", "");
  await sel("map-select", "icra_fig2");
  await sel("problem", "2");
  await sel("case", "1");
  await evaluate('__t.type("story", "ABDEC"); __t.type("history", "b1 b3 o2 o2 b4"); __t.click("run")');
  await evaluate('__t.click("play")');
  await evaluate('document.getElementById("scrub").value = "0"; document.getElementById("scrub").dispatchEvent(new Event("input"))');
  const p2 = { outside: 0, inside: 0, bad: [] };
  for (let i = 0; i < 60; i++) {
    s = await S();
    if (/Outside the sensors' interval x may be anywhere/.test(s.caption)) {
      p2.outside++;
      if (s.possible !== 0) p2.bad.push("shaded outside: " + s.caption);
      if (p2.outside === 4) await shot("desktop-light-p2-outside");
    } else {
      p2.inside++;
      if (s.possible === 0) p2.bad.push("no shading inside: " + s.caption);
      if (p2.inside === 3) await shot("desktop-light-p2-inside");
    }
    if (s.scrub >= s.scrubMax) break;
    await evaluate('__t.click("step-fwd")');
  }
  check(p2.outside >= 5 && p2.inside >= 5 && p2.bad.length === 0, "Problem 2 shading follows the sensors' interval " + JSON.stringify(p2));

  // touch screens: no sticky hover look over "x could be here"
  console.log("touch (hover: none)");
  await media([{ name: "prefers-color-scheme", value: "light" }, { name: "hover", value: "none" }]);
  const hoverNone = await evaluate('matchMedia("(hover: none)").matches');
  if (!hoverNone) console.log("  (this Chrome cannot emulate hover: none; touch check skipped)");
  await sel("preset", "icra_fig1_ABAC");
  await evaluate('__t.click("play")');
  await evaluate('document.getElementById("scrub").value = "0"; document.getElementById("scrub").dispatchEvent(new Event("input"))');
  {
    const [x, y] = await evaluate('__t.center(\'#map [data-kind="room"][data-name="A"]\')');
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await sleep(350);
    const fills = await evaluate(`(() => { const g = document.querySelector('#map [data-kind="room"][data-name="A"]');
      return { possible: g.classList.contains("possible"), hovered: g.matches(":hover"), fill: getComputedStyle(g.querySelector(".room-fill")).fill,
               ring: getComputedStyle(g.querySelector(".poss-ring")).stroke }; })()`);
    check(!hoverNone || (fills.possible && fills.hovered && !/rgba?\(36, 86, 196/.test(fills.fill) && fills.ring !== "none"),
          "a hovered room where x could be keeps its shading under (hover: none) " + JSON.stringify(fills));
    await shot("touch-room-A-hovered");
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 1, y: 1 });
  }
  await media([{ name: "prefers-color-scheme", value: "light" }]);

  // ---------------------------------------------------------------- dark
  console.log("dark mode (emulated)");
  await media([{ name: "prefers-color-scheme", value: "dark" }]);
  await load();
  await evaluate("__t.playToEnd(20000)");
  const bg = await evaluate("getComputedStyle(document.body).backgroundColor");
  check(bg === "rgb(17, 19, 23)", "dark background under prefers-color-scheme: dark (got " + bg + ")");
  await shot("desktop-dark");
  await sel("preset", "bug_B5");
  await shot("desktop-dark-bug-B5");
  await sel("preset", "icra_sec3a_p4");
  await evaluate("__t.playToEnd(20000)");
  await shot("desktop-dark-icra-p4");

  // manual theme toggle, persisted
  console.log("theme toggle");
  await evaluate('__t.click("theme-toggle")');
  check(await evaluate('document.documentElement.getAttribute("data-theme")') === "light", "toggle switches dark -> light");
  await load();
  check(await evaluate('document.documentElement.getAttribute("data-theme")') === "light", "theme choice persists across reloads");
  await evaluate('localStorage.removeItem("cd-theme")');

  // ---------------------------------------------------------------- reduced motion
  console.log("reduced motion");
  await media([{ name: "prefers-color-scheme", value: "light" }, { name: "prefers-reduced-motion", value: "reduce" }]);
  await load();
  await sleep(300);
  s = await S();
  check(s.playing === "paused" && s.scrub === 0, "no autoplay under prefers-reduced-motion");
  await evaluate('__t.click("step-fwd")');
  await evaluate('__t.click("step-fwd")');
  s = await S();
  check(s.scrub > 0 && /^Step 3 of/.test(s.caption), "stepping works under reduced motion (" + s.caption + ")");
  await media([{ name: "prefers-color-scheme", value: "light" }, { name: "prefers-reduced-motion", value: "no-preference" }]);

  // ---------------------------------------------------------------- phone widths
  for (const [w, h] of [[375, 812], [360, 740]]) {
    for (const theme of ["light", "dark"]) {
      console.log("phone " + w + "x" + h + " " + theme);
      await viewport(w, h, true);
      await media([{ name: "prefers-color-scheme", value: theme }]);
      await load();
      await evaluate("__t.playToEnd(20000)");
      let o = await evaluate("__t.overflow()");
      check(o.scrollWidth <= o.clientWidth && o.bad.length === 0, w + "px " + theme + ": no horizontal overflow " + JSON.stringify(o));
      if (w === 375) { await shot("phone-" + theme); await shot("phone-" + theme + "-full", true); }
      for (const pid of ["bug_B5", "icra_sec3a_p4", "icra_p2_case2"]) {
        await sel("preset", pid);
        await evaluate("__t.playToEnd(20000)");
        o = await evaluate("__t.overflow()");
        check(o.scrollWidth <= o.clientWidth && o.bad.length === 0, w + "px " + theme + " " + pid + ": no horizontal overflow " + JSON.stringify(o));
        if (w === 375 && pid === "bug_B5") await shot("phone-" + theme + "-bug-B5-full", true);
      }
    }
  }

  // ---------------------------------------------------------------- copy with spaces, keyboard, consistency
  console.log("copy in a path with spaces; keyboard; clicks = typing; undo; live caption");
  await viewport(1280, 900, false);
  await media([{ name: "prefers-color-scheme", value: "light" }, { name: "prefers-reduced-motion", value: "no-preference" }]);
  const spacedRoot = fs.mkdtempSync(path.join(os.tmpdir(), "cd demo copy "));
  const spacedDocs = path.join(spacedRoot, "docs with spaces");
  fs.cpSync(path.join(ROOT, "docs"), spacedDocs, { recursive: true });
  {
    const loaded = cdp.once("Page.loadEventFired");
    await cdp.send("Page.navigate", { url: pathToFileURL(path.join(spacedDocs, "index.html")).href });
    await loaded;
    await evaluate(HELPERS);
    await sleep(50);
  }
  check(/%20/.test(await evaluate("location.href")), "the copy's URL has escaped spaces");
  s = await S();
  check(s.badge === "consistent", "the copy in a path with spaces runs (got " + s.badge + ")");

  // keyboard-only picks on the map: Enter / Space on the focused feature
  const key = async (k, code, kc, shift) => {
    const modifiers = shift ? 8 : 0;
    await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: k, code, windowsVirtualKeyCode: kc, modifiers });
    if (k === " ") await cdp.send("Input.dispatchKeyEvent", { type: "char", key: k, code, text: " ", windowsVirtualKeyCode: kc });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: kc, modifiers });
  };
  const focusMap = (kind, name) => evaluate('document.querySelector(\'#map [data-kind="' + kind + '"][data-name="' + name + '"]\').focus()');
  await evaluate('__t.click("clear")');
  await focusMap("room", "A"); await key("Enter", "Enter", 13);
  await focusMap("room", "C"); await key(" ", "Space", 32);
  await focusMap("occupancy", "o1"); await key("Enter", "Enter", 13); await key("Enter", "Enter", 13);
  await focusMap("beam", "b1"); await key("Enter", "Enter", 13);
  s = await S();
  check(s.story === "AC" && s.history === "o1 o1 b1", "keyboard picks build the story and history (got " + JSON.stringify([s.story, s.history]) + ")");
  await evaluate('__t.click("run")');
  const viaKeys = await S();
  await evaluate('__t.type("story", "AC"); __t.type("history", "o1 o1 b1"); __t.click("run")');
  const typed = await S();
  check(viaKeys.badge === typed.badge && viaKeys.path === typed.path && viaKeys.original === typed.original,
        "keyboard/clicked and typed questions give the same answer");

  // the B3 question built by clicks must not be presented as a valid walk of the original
  await evaluate('__t.click("clear")');
  for (const sel of ['[data-kind="room"][data-name="A"]', '[data-kind="room"][data-name="C"]',
                     '[data-kind="beam"][data-name="b1"]', '[data-kind="beam"][data-name="b1"]', '[data-kind="beam"][data-name="b1"]']) {
    await evaluate("document.querySelector(" + JSON.stringify("#map " + sel) + ").scrollIntoView({block: 'center'})");
    await realClick("#map " + sel);
  }
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 1, y: 1 });
  await evaluate('__t.click("run")');
  s = await S();
  check(s.story === "AC" && s.history === "b1 b1 b1", "clicks build the B3 question (got " + JSON.stringify([s.story, s.history]) + ")");
  check(s.original && !/another valid walk/.test(s.original),
        "B3 built by clicks: the original's 4-crossing walk for 3 recordings is not called valid (" + s.original + ")");
  await shot("b3-via-clicks-full", true);
  await evaluate('__t.type("history", "b1 b1 b1 "); __t.click("run")');
  s = await S();
  check(s.original && !/another valid walk/.test(s.original),
        "B3 with a trailing space: the original's walk is not called valid (" + s.original + ")");

  // Undo after a map switch must not leave the old map's story on the new map
  const reloaded = cdp.once("Page.loadEventFired");
  await evaluate("location.reload()");
  await reloaded;
  await evaluate(HELPERS);
  await sel("map-select", data.maps[1]);
  await evaluate('__t.click("undo")');
  const u = await evaluate('({map: document.getElementById("map-select").value, story: document.getElementById("story").value})');
  const startStory = "ACBAC";  // star_fig2's default story
  check(!(u.map === data.maps[1] && u.story === startStory),
        "Undo after a map switch restores the map with the inputs (got map " + u.map + ", story " + u.story + ")");

  // the step caption is an aria-live region: rewrite it only when its text changes
  await evaluate(`window.__cap = { n: 0, texts: new Set() };
    new MutationObserver((ms) => { __cap.n += ms.length; __cap.texts.add(document.getElementById("step-caption").textContent); })
      .observe(document.getElementById("step-caption"), { childList: true, characterData: true, subtree: true }); true`);
  await sel("preset", "icra_fig1_ABAC");
  await sleep(1500);
  const cap = await evaluate("({n: __cap.n, distinct: __cap.texts.size})");
  check(cap.n <= 2 * cap.distinct + 2, "the aria-live step caption changes only with its text (" + cap.n + " mutations, " + cap.distinct + " texts in 1.5 s)");
  try { fs.rmSync(spacedRoot, { recursive: true, force: true }); } catch (e) { /* ignore */ }

  // fixes of the browser-QA round: focus ring, occupancy shading, chips after an input error,
  // stale answers, the disabled slider, the Problem menu's width
  console.log("focus ring, occupancy shading, input-error chips, stale answer, slider, menu width");
  await load();
  await focusMap("room", "C");
  await key("Tab", "Tab", 9); await key("Tab", "Tab", 9, true);  // keyboard focus => :focus-visible
  const ring = await evaluate(`(() => { const g = document.querySelector('#map [data-kind="room"][data-name="C"]');
    const r = g.querySelector(".focus-ring"), cs = getComputedStyle(r);
    return { focused: document.activeElement === g, width: parseFloat(cs.strokeWidth), stroke: cs.stroke }; })()`);
  check(ring.focused && ring.width >= 3 && ring.stroke !== "none" && !/rgba\(0, 0, 0, 0\)|transparent/.test(ring.stroke),
        "a focused room shows a solid focus ring inside its walls " + JSON.stringify(ring));
  await shot("focus-room-C");
  await focusMap("beam", "b1");
  await key("Tab", "Tab", 9); await key("Tab", "Tab", 9, true);
  await shot("focus-beam-b1");
  await evaluate("document.activeElement.blur()");
  await sel("preset", "bug_B1");
  await evaluate('__t.click("play")');
  await evaluate('document.getElementById("scrub").value = "1000"; document.getElementById("scrub").dispatchEvent(new Event("input"))');
  for (let i = 0; i < 20; i++) {
    const cap = (await S()).caption;
    if (/^Step 1 of/.test(cap)) break;
    await evaluate('__t.click("step-back")');
  }
  // step through B1 and record how o1 looks whenever x could be inside it (on and off)
  const occSeen = {};
  for (let i = 0; i < 20; i++) {
    await sleep(300);  // let the fill transition finish
    const o = await evaluate(`(() => { const g = document.querySelector('#map [data-kind="occupancy"][data-name="o1"]');
      if (!g.classList.contains("possible")) return null;
      const on = g.classList.contains("active");
      return { on, paint: on ? getComputedStyle(g.querySelector(".occ-poss-ring")).stroke
                             : getComputedStyle(g.querySelector(".occ-poss")).fill }; })()`);
    if (o && !occSeen[o.on ? "on" : "off"]) {
      occSeen[o.on ? "on" : "off"] = o.paint;
      if (o.on) await shot("occ-possible-active-B1");
    }
    if ((await S()).scrub >= 1000) break;
    await evaluate('__t.click("step-fwd")');
  }
  const painted = (c) => c && c !== "none" && c !== "transparent" && !/rgba\(0, 0, 0, 0\)/.test(c);
  check(painted(occSeen.on), "an active occupancy region where x could be is marked green (" + JSON.stringify(occSeen) + ")");
  await shot("occ-possible-B1");
  await evaluate('__t.type("story", "a c"); __t.type("history", "b9"); __t.click("run")');
  s = await S();
  const errChips = await evaluate('[...document.querySelectorAll("#story-chips .chip, #history-chips .chip")].map(c => c.className + ":" + c.textContent)');
  check(s.badge === "input error" && !errChips.some((c) => /chip-empty/.test(c)) && errChips.some((c) => /chip-bad.*b9/.test(c)),
        "after an input error the chips show the inputs, not 'empty' " + JSON.stringify(errChips));
  check(s.disabled && s.scrub === 0, "with no walk to play the disabled slider is back at the start (" + s.scrub + ")");
  await sel("preset", "icra_fig1_ABAC");
  await sleep(150);
  await evaluate('__t.type("history", "b2 o2 o2 o1")');
  await sleep(150);
  s = await S();
  const dim = await evaluate('getComputedStyle(document.querySelector("#map .layer-path")).opacity');
  check(s.stale && s.playing === "paused" && s.possible === 0 && +dim < 1,
        "changed inputs pause playback, hide the shading and dim the walk " + JSON.stringify({ stale: s.stale, playing: s.playing, possible: s.possible, dim }));
  await shot("stale-answer");
  await evaluate('__t.type("history", "b2 o2 o2 o1 o1")');
  s = await S();
  check(!s.stale && s.possible > 0, "undoing the edit brings the answer and its shading back");
  const fits = await evaluate(`(() => { const e = document.getElementById("problem"), cs = getComputedStyle(e);
    const c = document.createElement("canvas").getContext("2d"); c.font = cs.font;
    const w = Math.max(...[...e.options].map(o => c.measureText(o.textContent).width));
    return { need: Math.ceil(w), have: e.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) }; })()`);
  check(fits.need <= fits.have, "the Problem menu fits its longest option at 1280 px " + JSON.stringify(fits));

  // ---------------------------------------------------------------- verdict
  const external = requests.filter((u) => !u.startsWith("file:"));
  check(problems.length === 0, "no console errors/warnings/exceptions: " + JSON.stringify(problems.slice(0, 10)));
  check(external.length === 0, "no non-file requests: " + JSON.stringify(external.slice(0, 10)));
  console.log(requests.length + " requests, all file://: " + (external.length === 0));
  console.log("screenshots: " + OUT);

  cdp.ws.close();
  proc.kill("SIGTERM");
  await sleep(200);
  if (proc.exitCode === null && proc.signalCode === null) { try { proc.kill("SIGKILL"); } catch (e) { /* gone */ } }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  if (failures.length) {
    console.log("\n" + failures.length + " FAILURE(S)");
    process.exit(1);
  }
  console.log("\nall checks passed");
}

main().catch((e) => {
  console.error(e);
  if (running) killChrome(running.proc, running.profile);  // never leave a headless Chrome behind
  process.exit(2);
});
