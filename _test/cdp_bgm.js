/* 浏览器端验证「替换 BGM 后，界面显示的时长/循环点上限是否正确」。
 *
 * 做法：先用 API 把曲目 0 替换成 20 秒的曲子，再让界面 reload，
 * 然后检查表格里显示的内容 —— 这正是用户看到的。
 *
 * 用法: node _test/cdp_bgm.js <serverPort> <debugPort>
 */
const http = require("http");
const net = require("net");
const crypto = require("crypto");

const SERVER_PORT = parseInt(process.argv[2] || "8799", 10);
const DEBUG_PORT = parseInt(process.argv[3] || "9222", 10);
const BPS = 44100 * 4;

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
  let seq = 0; const pending = new Map(); const errs = [];
  ws.onMessage((t) => { let m; try { m = JSON.parse(t); } catch (e) { return; }
    if (m.method === "Runtime.exceptionThrown") errs.push(JSON.stringify(m.params.exceptionDetails.text));
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const cmd = (method, params) => { const id = ++seq;
    return new Promise((res, rej) => { pending.set(id, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
      ws.send({ id, method, params: params || {} }); }); };
  const ev = async (e) => { const r = await cmd("Runtime.evaluate",
      { expression: e, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error("页面异常: " + JSON.stringify(r.exceptionDetails.text));
    return r.result.value; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  await cmd("Runtime.enable");
  await sleep(2500);

  console.log("① 切到音乐页，读替换前的界面数据");
  await ev(`document.querySelector('[data-tab="bgm"]').click()`);
  await sleep(1500);
  const before = JSON.parse(await ev(`(() => {
    const tr = document.querySelectorAll("#bgm-table tbody tr")[0];
    const inp = tr.querySelector("input[data-loop]");
    return JSON.stringify({
      cells: [...tr.querySelectorAll("td")].slice(0,5).map(td => td.textContent.trim().replace(/\\s+/g," ")),
      max: inp.getAttribute("max"), value: inp.value });
  })()`));
  console.log("   替换前: " + JSON.stringify(before));

  console.log("\n② 用 API 把曲目 0 换成 20 秒的静音曲，再让界面刷新");
  const replaced = await ev(`(async () => {
    const n = 20 * ${BPS};
    const buf = new ArrayBuffer(44 + n);
    const dv = new DataView(buf);
    const w = (o, s) => { for (let i=0;i<s.length;i++) dv.setUint8(o+i, s.charCodeAt(i)); };
    w(0,"RIFF"); dv.setUint32(4, 36+n, true); w(8,"WAVE"); w(12,"fmt ");
    dv.setUint32(16,16,true); dv.setUint16(20,1,true); dv.setUint16(22,2,true);
    dv.setUint32(24,44100,true); dv.setUint32(28,${BPS},true);
    dv.setUint16(32,4,true); dv.setUint16(34,16,true); w(36,"data");
    dv.setUint32(40,n,true);
    const r = await fetch("/api/bgm.replace?index=0", {method:"POST", body:buf});
    await loadBgm(); await loadPending();
    return JSON.stringify({ status: r.status, seconds: (await r.json()).seconds });
  })()`);
  console.log("   " + replaced);
  await sleep(1200);

  console.log("\n③ 界面应显示新时长（20 秒），并给出循环点上限");
  const after = JSON.parse(await ev(`(() => {
    const tr = document.querySelectorAll("#bgm-table tbody tr")[0];
    const inp = tr.querySelector("input[data-loop]");
    return JSON.stringify({
      cells: [...tr.querySelectorAll("td")].slice(0,5).map(td => td.textContent.trim().replace(/\\s+/g," ")),
      max: inp.getAttribute("max"), value: inp.value });
  })()`));
  console.log("   替换后: " + JSON.stringify(after));
  check("时长列显示 0:20.0（新音频）", /0:20\.0/.test(after.cells[2]),
        after.cells[2]);
  check("备注里带出原曲时长", /原/.test(after.cells[2]), after.cells[2]);
  check("状态列标出「已暂存替换」", /已暂存替换/.test(after.cells[4]),
        after.cells[4]);
  check("循环点输入框上限 = 20 秒", Math.abs(parseFloat(after.max) - 20) < 0.15,
        after.max);

  console.log("\n④ 设一个合法循环点（12 秒）→ 界面应显示「待保存」");
  await ev(`(() => { const inp = document.querySelector('input[data-loop="0"]');
    inp.value = "12.0";
    inp.closest("tr").querySelector('[data-act="loop"]').click(); })()`);
  await sleep(2000);
  const looped = JSON.parse(await ev(`(() => {
    const tr = document.querySelectorAll("#bgm-table tbody tr")[0];
    const inp = tr.querySelector("input[data-loop]");
    return JSON.stringify({ value: inp.value, cell: tr.querySelectorAll("td")[3].textContent.trim().replace(/\\s+/g," ") });
  })()`));
  console.log("   " + JSON.stringify(looped));
  check("输入框显示 12.0", Math.abs(parseFloat(looped.value) - 12) < 0.15,
        looped.value);
  check("该单元格标出「待保存」", /待保存/.test(looped.cell), looped.cell);

  console.log("\n⑤ 音乐页两个按钮应可用（以前只改循环点时是禁用）");
  const btns = JSON.parse(await ev(`JSON.stringify({
    apply: document.querySelector("#bgm-apply").disabled,
    cancel: document.querySelector("#bgm-cancel").disabled,
    barHidden: document.querySelector("#pending-bar").classList.contains("hidden"),
    barCount: document.querySelector("#pending-count").textContent })`));
  console.log("   " + JSON.stringify(btns));
  check("「保存到游戏…」可用", btns.apply === false);
  check("「放弃音乐修改」可用", btns.cancel === false);
  check("底部待保存条已出现", btns.barHidden === false);
  check("待保存计数 >= 1", parseInt(btns.barCount, 10) >= 1, btns.barCount);

  check("全程无 JS 异常", errs.length === 0, errs.slice(0, 2).join(" | "));
  console.log("=".repeat(58));
  console.log(failures === 0 ? "全部通过 ✓" : ("失败 " + failures + " 项"));
  ws.close();
  process.exit(failures === 0 ? 0 : 1);
}
main().catch((e) => { console.error("测试脚本失败:", e.message); process.exit(2); });
