// Headless Edge/Chrome screenshot driver over the DevTools protocol (no dependencies).
// usage: node tests/shot.mjs <url> <out.png> [waitExpr] [clickSelector] [width] [height]
import { spawn } from "node:child_process";
import { writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";

const [url, out, waitExpr = "true", click = "", width = "1440", height = "1100", preJs = ""] = process.argv.slice(2);
const bins = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Google/Chrome/Application/chrome.exe"];
const bin = bins.find(existsSync);
const port = 9222 + Math.floor(Math.random() * 500);
const proc = spawn(bin, [`--headless=new`, `--disable-gpu`, `--no-first-run`, `--user-data-dir=${tmpdir()}/burstai-cdp-${port}`, `--remote-debugging-port=${port}`, `--window-size=${width},${height}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ver;
for (let i = 0; i < 50 && !ver; i++) { try { ver = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); } catch { await sleep(200); } }
const ws = new WebSocket(ver.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
const send = (method, params = {}, sessionId) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
const { result: { targetId } } = await send("Target.createTarget", { url: "about:blank" });
const { result: { sessionId } } = await send("Target.attachToTarget", { targetId, flatten: true });
await send("Page.enable", {}, sessionId); await send("Runtime.enable", {}, sessionId);
await send("Emulation.setDeviceMetricsOverride", { width: +width, height: +height, deviceScaleFactor: 1, mobile: false }, sessionId);
await send("Page.navigate", { url }, sessionId);
const evalJs = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }, sessionId)).result?.result?.value;
if (preJs) { await sleep(800); await evalJs(preJs); await sleep(1500); }
for (let i = 0; i < 600; i++) { if (await evalJs(`(() => { try { return !!(${waitExpr}); } catch { return false; } })()`)) break; await sleep(250); }
if (click) { for (const sel of click.split("|")) { await evalJs(`document.querySelector(${JSON.stringify(sel)})?.click(); true`); await sleep(400); } }
await sleep(600);
const errs = await evalJs("JSON.stringify(window.__errs || [])");
let shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true }, sessionId);
if (!shot.result) shot = await send("Page.captureScreenshot", { format: "png" }, sessionId);
const { result: { data } } = shot;
writeFileSync(out, Buffer.from(data, "base64"));
console.log("saved", out, "errors:", errs);
proc.kill(); process.exit(0);
