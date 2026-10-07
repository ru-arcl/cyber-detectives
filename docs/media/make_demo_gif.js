#!/usr/bin/env node
/* Regenerates docs/media/demo.gif: a recording of the static demo (docs/index.html).
 *
 *   node docs/media/make_demo_gif.js [--out docs/media/demo.gif] [--frames DIR]
 *
 * Needs Node >= 22, google-chrome (or $CHROME) and python3 with Pillow.  No other tools: the
 * frames are real screenshots of the page, taken by headless Chrome over the DevTools Protocol,
 * and Pillow (not ImageMagick or ffmpeg) assembles them into the GIF.
 *
 * The page is opened via file:// at 990 x 1100 CSS px (the two-column layout), light theme,
 * reduced motion (so no CSS transition is caught half-way and the page does not autoplay).
 * The script then plays the part of the user: it picks questions from the Example menu and
 * moves the playback slider, capturing the app (map, question and answer) after every move:
 *
 *   1. the page's opening question, story ACBAC against history b1 o1 o1 o2 o2 b2 on STAR
 *      Fig. 2: consistent; the witness walk plays with "where x could be" shaded;
 *   2. the example "STAR eq. (1) + (2), single agent": inconsistent; the slider steps through
 *      the recordings, showing where the recordings alone allow x to be;
 *   3. the example "STAR eq. (1) + (3), multiple agents": consistent once other agents may
 *      keep the occupancy sensors on; its witness walk plays.
 *
 * Frames are taken at 2x device scale, downscaled to 800 px wide (Lanczos) and quantized to one
 * shared 256-colour palette (fast octree, which keeps the thin red beams red) without dithering;
 * identical frames are merged into one longer frame.  On the same machine (same Chrome, fonts and
 * Pillow) two runs give a byte-identical GIF.
 * The walks play at 1.25x the page's 1x speed, 10 frames per second.
 * --frames DIR keeps the PNG frames there (default: a temporary directory, removed).
 */
"use strict";

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "..", "..");
const INDEX = pathToFileURL(path.join(ROOT, "docs", "index.html")).href;
const CHROME = process.env.CHROME || "google-chrome";

const VIEW_W = 990, VIEW_H = 1100, SCALE = 2;   // CSS viewport and device scale of the capture
const OUT_W = 800;                              // GIF width in pixels
const FPS = 10;                                 // walk frames per second of playback time
const SPEED = 1.25;                             // playback speed of the walks (the page's 1x = 1)
const COLORS = 256;

function parseArgs(argv) {
  const a = { out: path.join(__dirname, "demo.gif"), frames: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--out") a.out = path.resolve(argv[++i]);
    else if (argv[i] === "--frames") a.frames = path.resolve(argv[++i]);
    else { console.error("usage: node docs/media/make_demo_gif.js [--out GIF] [--frames DIR]"); process.exit(2); }
  }
  return a;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ Chrome + CDP
// (the same launch protocol as tools/demo/check.js, which runs its checks when required and so
// cannot be imported: profile in a temp dir, port from DevToolsActivePort, hard kill on hangs)

let chrome = null;  // {proc, profile}: killed on every exit path

function killChrome() {
  if (!chrome) return;
  const { proc, profile } = chrome;
  chrome = null;
  try { proc.kill("SIGKILL"); } catch (e) { /* already gone */ }
  // Chrome may still be flushing its profile for a moment after the kill
  for (let i = 0; i < 20; i++) {
    try { fs.rmSync(profile, { recursive: true, force: true }); return; } catch (e) { spawnSync("sleep", ["0.1"]); }
  }
}
process.on("exit", killChrome);
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => { killChrome(); process.exit(130); });

async function launchOnce() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "cd-chrome-"));
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--disable-background-networking", "--disable-component-update",
    "--disable-sync", "--disable-default-apps", "--hide-scrollbars", "--mute-audio",
    "--font-render-hinting=none", "--force-color-profile=srgb",
    "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  proc.stderr.on("data", () => {});
  chrome = { proc, profile };
  const portFile = path.join(profile, "DevToolsActivePort");
  for (let i = 0; i < 200 && !fs.existsSync(portFile); i++) await sleep(50);
  if (!fs.existsSync(portFile)) { killChrome(); throw new Error("Chrome did not start (" + CHROME + ")"); }
  let port = "";
  for (let i = 0; i < 50 && !port; i++) { port = fs.readFileSync(portFile, "utf8").split("\n")[0]; if (!port) await sleep(50); }
  let targets = [];
  for (let i = 0; i < 50; i++) {
    targets = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json();
    if (targets.some((t) => t.type === "page")) break;
    await sleep(100);
  }
  const page = targets.find((t) => t.type === "page");
  if (!page) { killChrome(); throw new Error("Chrome has no page target"); }
  return page.webSocketDebuggerUrl;
}

async function launch() {
  for (let attempt = 1; ; attempt++) {
    try { return await launchOnce(); } catch (e) {
      if (attempt >= 3) throw e;
      console.log("  (" + e.message + "; retrying)");
    }
  }
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

// ------------------------------------------------------------------ page drivers (injected)

const HELPERS = `
window.__g = {
  raf() { return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true)))); },
  preset(id) { const e = document.getElementById("preset"); e.value = id; e.dispatchEvent(new Event("change", {bubbles: true})); return this.raf(); },
  scrub(v) { const e = document.getElementById("scrub"); e.value = String(v); e.dispatchEvent(new Event("input", {bubbles: true})); return this.raf(); },
  // the question on screen, solved again with the app's own functions: its walk's timeline
  timeline() {
    const CD = window.CyberDetectives, q = (s) => document.querySelector(s);
    const st = { mapName: q("#map-select").value, problem: +q("#problem").value, kase: +q("#case").value,
                 anchored: q("#anchored").value === "1", unreported: q("#unreported").checked,
                 agents: q('input[name="agents"]:checked').value, story: q("#story").value, history: q("#history").value };
    const R = CD.app.solve(st);
    if (!R.path) return { status: R.status, total: 0, slots: R.possible ? R.events.length + 1 : 0 };
    const tl = CD.draw.makeTimeline(CD.draw.tracePath(window.CD_DATA.maps[st.mapName], R.path), { speed: 200, minStep: 0.35 });
    return { status: R.status, total: tl.total, slots: 0 };
  },
  state() {
    const q = (s) => document.querySelector(s);
    return { badge: q("#result .badge") ? q("#result .badge").textContent : null, caption: q("#step-caption").textContent,
             scrubMax: +q("#scrub").max, preset: q("#preset").value, story: q("#story").value, history: q("#history").value };
  },
  // the part of the page to record: the app's width, from the map panel's top to the lower of
  // the map panel and the answer's reason (or, without one, its first fact)
  extent() {
    const q = (s) => document.querySelector(s), lay = q("#app").getBoundingClientRect();
    const map = q("#map-title").closest("section").getBoundingClientRect();
    const tail = q("#result .reason") || q("#result .facts dd") || q("#result .verdict");
    return { left: lay.left + 16, right: lay.right - 16, top: map.top + scrollY,
             bottom: Math.max(map.bottom, tail.getBoundingClientRect().bottom) + scrollY };
  },
};
true;
`;

// ------------------------------------------------------------------ GIF assembly (Pillow)

const ASSEMBLE = String.raw`
import json, sys
from PIL import Image
spec = json.load(open(sys.argv[1]))
out_w, colors = spec["width"], spec["colors"]
frames, durations = [], []
for f in spec["frames"]:
    im = Image.open(f["png"]).convert("RGB")
    h = round(im.height * out_w / im.width)
    frames.append(im.resize((out_w, h), Image.LANCZOS))
    durations.append(f["ms"])
# one palette for the whole GIF, from a strip of evenly spaced frames
pick = frames[:: max(1, len(frames) // 12)]
strip = Image.new("RGB", (out_w, sum(p.height for p in pick)))
y = 0
for p in pick:
    strip.paste(p, (0, y)); y += p.height
pal = strip.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
q[0].save(spec["out"], save_all=True, append_images=q[1:], duration=durations, loop=0,
          optimize=True, disposal=1)
print("%d frames, %d x %d" % (len(q), q[0].width, q[0].height))
`;

// ------------------------------------------------------------------ main

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const frameDir = args.frames || fs.mkdtempSync(path.join(os.tmpdir(), "cd-gif-frames-"));
  fs.mkdirSync(frameDir, { recursive: true });
  const cdp = new CDP(await launch());
  await cdp.open();
  const problems = [];
  cdp.on("Runtime.exceptionThrown", (p) => problems.push(p.exceptionDetails.text));
  await Promise.all(["Runtime.enable", "Page.enable"].map((m) => cdp.send(m)));
  const evaluate = async (expr) => {
    const r = await cdp.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error("evaluate failed: " + expr + ": " + (r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text));
    return r.result.value;
  };
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: VIEW_W, height: VIEW_H, deviceScaleFactor: SCALE, mobile: false });
  await cdp.send("Emulation.setEmulatedMedia", { features: [
    { name: "prefers-color-scheme", value: "light" }, { name: "prefers-reduced-motion", value: "reduce" }] });
  const load = async () => {
    const loaded = cdp.once("Page.loadEventFired");
    await cdp.send("Page.navigate", { url: INDEX });
    await loaded;
    await evaluate("document.fonts.ready.then(() => true)");
    await evaluate(HELPERS);
    await evaluate("__g.raf()");
  };
  await load();

  const frames = [];
  let clip = null;
  const capture = async (ms) => {
    const r = await cdp.send("Page.captureScreenshot", { format: "png", clip: Object.assign({ scale: 1 }, clip) });
    const png = path.join(frameDir, "f" + String(frames.length).padStart(4, "0") + ".png");
    const buf = Buffer.from(r.data, "base64");
    const prev = frames.length ? frames[frames.length - 1] : null;
    if (prev && prev.data.equals(buf)) { prev.ms += ms; return; }  // unchanged: hold longer
    fs.writeFileSync(png, buf);
    frames.push({ png, ms, data: buf });
  };
  const QUESTIONS = [null, "star_eq1_eq2_single", "star_eq1_eq3_multi"];  // null: the opening question
  const fixClip = async () => {
    // one clip for every frame: measure all three questions, then reload the page
    const ext = [await evaluate("__g.extent()")];
    for (const id of QUESTIONS.slice(1)) { await evaluate("__g.preset(" + JSON.stringify(id) + ")"); ext.push(await evaluate("__g.extent()")); }
    const pad = 8, top = Math.min(...ext.map((e) => e.top)) - pad, bottom = Math.max(...ext.map((e) => e.bottom)) + 2 * pad;
    clip = { x: Math.round(ext[0].left - pad), y: Math.round(top), width: Math.round(ext[0].right - ext[0].left + 2 * pad), height: Math.round(bottom - top) };
    if (clip.y + clip.height > VIEW_H) throw new Error("the recorded area does not fit the viewport: " + JSON.stringify(clip));
    await load();
  };
  // play the current witness walk: slider positions at FPS frames per second of playback
  const playWalk = async (holdEnd) => {
    const tl = await evaluate("__g.timeline()");
    if (!tl.total) throw new Error("no witness walk to play: " + JSON.stringify(tl));
    const n = Math.max(2, Math.round(tl.total / SPEED * FPS));
    await capture(900);
    for (let i = 1; i <= n; i++) {
      await evaluate("__g.scrub(" + Math.round(i / n * 1000) + ")");
      await capture(i === n ? holdEnd : Math.round(1000 / FPS));
    }
    return tl;
  };
  const log = async (label) => {
    const s = await evaluate("__g.state()");
    console.log(label + ": " + s.badge + " | " + s.story + " / " + s.history + " | " + s.caption);
    return s;
  };

  // 1. the opening question (consistent): play its witness
  await fixClip();
  await evaluate("__g.scrub(0)");
  let s = await log("opening question");
  if (s.badge !== "consistent") throw new Error("the opening question is not consistent");
  await playWalk(2600);

  // 2. STAR eq. (1) + (2), single agent: inconsistent; step through the recordings
  await evaluate("__g.preset(" + JSON.stringify(QUESTIONS[1]) + ")");
  s = await log("star_eq1_eq2_single");
  if (s.badge !== "inconsistent") throw new Error("star_eq1_eq2_single is not inconsistent");
  const tl2 = await evaluate("__g.timeline()");
  await capture(1800);
  for (let h = 1; h < tl2.slots; h++) {
    await evaluate("__g.scrub(" + h + ")");
    await capture(h === tl2.slots - 1 ? 3200 : 1100);
  }

  // 3. STAR eq. (1) + (3), multiple agents: consistent; play its witness
  await evaluate("__g.preset(" + JSON.stringify(QUESTIONS[2]) + ")");
  await evaluate("__g.scrub(0)");
  s = await log("star_eq1_eq3_multi");
  if (s.badge !== "consistent") throw new Error("star_eq1_eq3_multi is not consistent");
  await playWalk(3200);

  if (problems.length) throw new Error("page errors: " + problems.join("; "));
  cdp.ws.close();
  killChrome();

  const spec = path.join(frameDir, "frames.json");
  fs.writeFileSync(spec, JSON.stringify({ out: args.out, width: OUT_W, colors: COLORS,
    frames: frames.map((f) => ({ png: f.png, ms: f.ms })) }));
  const py = spawnSync("python3", ["-c", ASSEMBLE, spec], { encoding: "utf8" });
  if (py.status !== 0) throw new Error("python3/Pillow failed: " + py.stderr);
  if (!args.frames) fs.rmSync(frameDir, { recursive: true, force: true });
  const kb = (fs.statSync(args.out).size / 1024).toFixed(0);
  console.log("wrote " + path.relative(process.cwd(), args.out) + ": " + py.stdout.trim() + ", " + kb + " KB, " +
              (frames.reduce((t, f) => t + f.ms, 0) / 1000).toFixed(1) + " s");
}

main().catch((e) => {
  console.error(e);
  killChrome();
  process.exit(1);
});
