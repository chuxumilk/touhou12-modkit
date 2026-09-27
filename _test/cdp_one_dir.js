/* 复现：在浏览器里真实选中某个目录 → 应用，看每一步发生了什么。
 *
 * 用法: node _test/cdp_one_dir.js <serverPort> <debugPort> <gameDir>
 */
const http = require("http");
const net = require("net");
const crypto = require("crypto");

const SERVER_PORT = parseInt(process.argv[2] || "8799", 10);
const DEBUG_PORT = parseInt(process.argv[3] || "9222", 10);
const DIR = process.argv[4] || "";

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
      s.on("error", () => {
        s.destroy();
        if (Date.now() - start > timeoutMs) return reject(new Error("端口未就绪"));
        setTimeout(attempt, 300);
      });
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
  let seq = 0; const pending = new Map(); const trace = [];
  ws.onMessage((t) => { let m; try { m = JSON.parse(t); } catch (e) { return; }
    if (m.method === "Network.requestWillBeSent" && /\/api\//.test(m.params.request.url))
      trace.push("-> " + m.params.request.method + " " + m.params.request.url.replace(/^http:\/\/[^/]+/, ""));
    if (m.method === "Network.responseReceived" && /\/api\//.test(m.params.response.url))
      trace.push("<- " + m.params.response.status + " " + m.params.response.url.replace(/^http:\/\/[^/]+/, "").slice(0, 90));
    if (m.method === "Runtime.exceptionThrown") trace.push("[异常] " + JSON.stringify(m.params.exceptionDetails.text));
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const cmd = (method, params) => { const id = ++seq;
    return new Promise((res, rej) => { pending.set(id, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
      ws.send({ id, method, params: params || {} }); }); };
  const ev = async (e) => { const r = await cmd("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) return "EXC: " + JSON.stringify(r.exceptionDetails.text);
    return r.result.value; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  await cmd("Runtime.enable"); await cmd("Network.enable");
  await sleep(3000);

  console.log("目标目录: " + DIR);
  console.log("=== 打开设置弹窗 ===");
  await ev(`document.querySelector("#btn-settings").click()`);
  await sleep(1500);
  console.log("  候选数:", await ev(`document.querySelectorAll("#settings-candidates .cand").length`));
  console.log("  输入框预填:", JSON.stringify(await ev(`document.querySelector("#settings-path").value`)));

  console.log("=== 模拟用户粘贴路径 ===");
  await ev(`(() => { const i = document.querySelector("#settings-path");
    i.value = ${JSON.stringify(DIR)};
    i.dispatchEvent(new Event("input", {bubbles: true})); })()`);
  await sleep(1500);
  console.log("  实时校验显示:", JSON.stringify(await ev(`document.querySelector("#settings-live").textContent`)));
  console.log("  校验样式:", await ev(`document.querySelector("#settings-live").className`));

  console.log("=== 点「应用」 ===");
  trace.length = 0;
  await ev(`document.querySelector("#settings-apply").click()`);
  await sleep(4000);
  trace.forEach((l) => console.log("  " + l));
  console.log("  弹窗关闭:", await ev(`document.querySelector("#settings-modal").classList.contains("hidden")`));
  console.log("  顶部显示目录:", JSON.stringify(await ev(`document.querySelector("#game-dir").textContent`)));
  console.log("  游戏版本选项:", JSON.stringify(await ev(`[...document.querySelectorAll("#game-select option")].map(o=>o.value)`)));
  console.log("  toast:", JSON.stringify(await ev(`(document.querySelector("#toast")||{}).textContent||""`)));
  console.log("  live:", JSON.stringify(await ev(`document.querySelector("#settings-live").textContent`)));
  console.log("  按钮高亮:", await ev(`document.querySelector("#btn-settings").classList.contains("attention")`));
  ws.close();
  process.exit(0);
}
main().catch((e) => { console.error("失败:", e.message); process.exit(2); });
