/* 验证「直接选/填游戏数据文件」这条新路径（浏览器端真实操作）。
 *
 * 用法: node _test/cdp_pick_file.js <serverPort> <debugPort> <datFile> <gameDir>
 */
const http = require("http");
const net = require("net");
const crypto = require("crypto");

const SERVER_PORT = parseInt(process.argv[2] || "8799", 10);
const DEBUG_PORT = parseInt(process.argv[3] || "9222", 10);
const DAT_FILE = process.argv[4] || "";
const GAME_DIR = process.argv[5] || "";

let failures = 0;
function check(t, c, d) {
  if (!c) failures++;
  console.log("  [" + (c ? "PASS" : "FAIL") + "] " + t + (d ? ("  — " + d) : ""));
}
function httpJson(path) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port: DEBUG_PORT, path }, (res) => {
      let d = ""; res.on("data", (c) => (d += c));
      res.on("end", () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    }).on("error", reject);
  });
}
function waitPort(port, timeoutMs) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    (function attempt() {
      const s = net.connect(port, "127.0.0.1");
      s.on("connect", () => { s.destroy(); resolve(); });
      s.on("error", () => { s.destroy();
        if (Date.now() - start > timeoutMs) return reject(new Error("端口未就绪"));
        setTimeout(attempt, 300); });
    })();
  });
}
function wsConnect(wsUrl) {
  return new Promise((resolve, reject) => {
    const u = new URL(wsUrl);
    const key = crypto.randomBytes(16).toString("base64");
    const sock = net.connect(parseInt(u.port, 10), u.hostname, () => {
      sock.write(`GET ${u.pathname}${u.search} HTTP/1.1\r\nHost: ${u.host}\r\n` +
        `Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ${key}\r\n` +
        `Sec-WebSocket-Version: 13\r\n\r\n`);
    });
    let buf = Buffer.alloc(0), frameBuf = Buffer.alloc(0), handshaken = false;
    const handlers = [];
    function decode() {
      for (;;) {
        if (frameBuf.length < 2) return;
        const b1 = frameBuf[1], opcode = frameBuf[0] & 0x0f;
        const masked = (b1 & 0x80) !== 0;
        let len = b1 & 0x7f, off = 2;
        if (len === 126) { if (frameBuf.length < 4) return; len = frameBuf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (frameBuf.length < 10) return; len = Number(frameBuf.readBigUInt64BE(2)); off = 10; }
        let mask = null;
        if (masked) { if (frameBuf.length < off + 4) return; mask = frameBuf.slice(off, off + 4); off += 4; }
        if (frameBuf.length < off + len) return;
        let payload = frameBuf.slice(off, off + len);
        if (mask) { const p = Buffer.from(payload); for (let i = 0; i < p.length; i++) p[i] ^= mask[i % 4]; payload = p; }
        frameBuf = frameBuf.slice(off + len);
        if (opcode === 1) handlers.forEach((h) => h(payload.toString("utf8")));
      }
    }
    sock.on("data", (chunk) => {
      if (!handshaken) {
        buf = Buffer.concat([buf, chunk]);
        const idx = buf.indexOf("\r\n\r\n"); if (idx < 0) return;
        frameBuf = buf.slice(idx + 4); handshaken = true;
        resolve({ send, onMessage: (h) => handlers.push(h), close: () => sock.destroy() });
        decode(); return;
      }
      frameBuf = Buffer.concat([frameBuf, chunk]); decode();
    });
    sock.on("error", reject);
    function send(obj) {
      const payload = Buffer.from(JSON.stringify(obj), "utf8");
      const len = payload.length; let header;
      if (len < 126) header = Buffer.from([0x81, 0x80 | len]);
      else if (len < 65536) { header = Buffer.alloc(4); header[0] = 0x81; header[1] = 0x80 | 126; header.writeUInt16BE(len, 2); }
      else { header = Buffer.alloc(10); header[0] = 0x81; header[1] = 0x80 | 127; header.writeBigUInt64BE(BigInt(len), 2); }
      const mask = crypto.randomBytes(4); const masked = Buffer.from(payload);
      for (let i = 0; i < masked.length; i++) masked[i] ^= mask[i % 4];
      sock.write(Buffer.concat([header, mask, masked]));
    }
  });
}

async function main() {
  await waitPort(DEBUG_PORT, 20000);
  const browser = await httpJson("/json/version");
  const bws = await wsConnect(browser.webSocketDebuggerUrl);
  let bseq = 0; const bp = new Map();
  bws.onMessage((t) => { let m; try { m = JSON.parse(t); } catch (e) { return; }
    if (m.id && bp.has(m.id)) { bp.get(m.id)(m); bp.delete(m.id); } });
  const bcmd = (method, params) => { const id = ++bseq;
    return new Promise((res, rej) => { bp.set(id, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
      bws.send({ id, method, params: params || {} }); }); };
  const created = await bcmd("Target.createTarget", { url: `http://127.0.0.1:${SERVER_PORT}/` });
  let page = null;
  for (let i = 0; i < 40 && !page; i++) {
    const list = await httpJson("/json/list");
    page = (list || []).find((t) => t.id === created.targetId);
    if (!page) await new Promise((r) => setTimeout(r, 250));
  }
  bws.close();
  const ws = await wsConnect(page.webSocketDebuggerUrl);
  let seq = 0; const pending = new Map(); const trace = []; const errs = [];
  ws.onMessage((t) => { let m; try { m = JSON.parse(t); } catch (e) { return; }
    if (m.method === "Runtime.exceptionThrown") errs.push(JSON.stringify(m.params.exceptionDetails.text));
    if (m.method === "Network.requestWillBeSent" && /\/api\//.test(m.params.request.url))
      trace.push("-> " + m.params.request.method + " " + m.params.request.url.replace(/^http:\/\/[^/]+/, "").slice(0, 80));
    if (m.method === "Network.responseReceived" && /\/api\//.test(m.params.response.url))
      trace.push("<- " + m.params.response.status + " " + m.params.response.url.replace(/^http:\/\/[^/]+/, "").slice(0, 60));
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const cmd = (method, params) => { const id = ++seq;
    return new Promise((res, rej) => { pending.set(id, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
      ws.send({ id, method, params: params || {} }); }); };
  const ev = async (e) => { const r = await cmd("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error("页面异常: " + JSON.stringify(r.exceptionDetails.text));
    return r.result.value; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitFor(expr, t) {
    const t0 = Date.now();
    for (;;) { if (await ev(expr)) return true;
      if (Date.now() - t0 > (t || 15000)) return false; await sleep(250); }
  }
  const setPath = (p) => ev(`(() => { const i = document.querySelector("#settings-path");
      i.value = ${JSON.stringify(p)};
      i.dispatchEvent(new Event("input", {bubbles: true})); })()`);

  await cmd("Runtime.enable"); await cmd("Network.enable");
  await sleep(2500);

  console.log("① 界面是否有「选文件…」按钮");
  await ev(`document.querySelector("#btn-settings").click()`);
  await sleep(1200);
  check("按钮存在", await ev(`!!document.querySelector("#settings-browse-file")`));

  console.log("② 填入 .dat 文件路径 → 应识别并提示已定位到目录");
  await setPath(DAT_FILE);
  await waitFor(`/可用/.test(document.querySelector("#settings-live").textContent)`, 10000);
  const live = await ev(`document.querySelector("#settings-live").textContent`);
  check("识别为可用", /可用/.test(live), live.slice(0, 60));
  check("提示了已根据文件定位到目录", /定位到目录/.test(live), live.slice(0, 60));

  console.log("③ 点「应用」→ 目录应切换为该文件所在目录");
  trace.length = 0;
  await ev(`document.querySelector("#settings-apply").click()`);
  const closed = await waitFor(`document.querySelector("#settings-modal").classList.contains("hidden")`, 15000);
  await waitFor(`document.querySelectorAll("#game-select option").length === 2`, 15000);
  const after = JSON.parse(await ev(`JSON.stringify({
    dir: document.querySelector("#game-dir").textContent,
    opts: [...document.querySelectorAll("#game-select option")].map(o=>o.value),
    attention: document.querySelector("#btn-settings").classList.contains("attention"),
    toast: (document.querySelector("#toast")||{}).textContent||"" })`));
  check("弹窗关闭", closed === true);
  check("目录 = 文件所在目录",
    after.dir.toLowerCase() === GAME_DIR.toLowerCase(), after.dir);
  check("两个版本都出来", after.opts.length === 2, after.opts.join(","));
  check("按钮不再高亮", after.attention === false);
  trace.forEach((l) => console.log("     " + l));

  console.log("④ 填一个不是游戏数据的文件 → 应明确说明");
  await ev(`document.querySelector("#btn-settings").click()`);
  await sleep(1000);
  await setPath("C:\\Windows\\win.ini");
  await waitFor(`/不是 TH12 的游戏数据/.test(document.querySelector("#settings-live").textContent)`, 10000);
  const bad = await ev(`document.querySelector("#settings-live").textContent`);
  check("说明这不是游戏数据文件", /不是 TH12 的游戏数据/.test(bad), bad.slice(0, 60));

  console.log("⑤ 填不存在的文件 → 应说明不存在");
  await setPath("C:\\nope\\th12c.dat");
  await waitFor(`/不存在/.test(document.querySelector("#settings-live").textContent)`, 10000);
  const miss = await ev(`document.querySelector("#settings-live").textContent`);
  check("说明不存在", /不存在/.test(miss), miss.slice(0, 50));

  check("全程无 JS 异常", errs.length === 0, errs.slice(0, 2).join(" | "));
  console.log("=".repeat(58));
  console.log(failures === 0 ? "全部通过 ✓" : ("失败 " + failures + " 项"));
  ws.close();
  process.exit(failures === 0 ? 0 : 1);
}
main().catch((e) => { console.error("测试脚本失败:", e.message); process.exit(2); });
