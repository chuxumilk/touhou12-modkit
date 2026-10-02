/* 在真实浏览器里验证 snakeDocEncoding() / 文档导入的编码防护。
 *
 * 用法: node _test/cdp_encoding.js <serverPort> <debugPort>
 */
const http = require("http");
const net = require("net");
const crypto = require("crypto");

const SERVER_PORT = parseInt(process.argv[2] || "8799", 10);
const DEBUG_PORT = parseInt(process.argv[3] || "9222", 10);

let failures = 0;
function check(title, cond, detail) {
  if (!cond) failures++;
  console.log("  [" + (cond ? "PASS" : "FAIL") + "] " + title +
              (detail ? ("  — " + detail) : ""));
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

  console.log("① 函数是否就位");
  check("sniffDocEncoding 存在", await ev(`typeof sniffDocEncoding === "function"`));
  check("pickFile 存在", await ev(`typeof pickFile === "function"`));

  console.log("\n② 各种真实字节的判定");
  const cases = [
    ["UTF-8 带 BOM", [0xEF, 0xBB, 0xBF, 0xE4, 0xBD, 0xA0, 0xE5, 0xA5, 0xBD], "utf8-bom"],
    ["UTF-16LE+A6 0xFF", [0xFF, 0xFE, 0x60, 0x4F], "utf16"],
    ["UTF-16BE BOM", [0xFE, 0xFF, 0x4F, 0x60], "utf16"],
    ["UTF-8 无 BOM", [0xE4, 0xB8, 0x9C, 0xE6, 0x96, 0xB9], "utf8"],
    ["纯 ASCII", Array.from(Buffer.from("# hello world\n[0.1] test")), "utf8"],
    ["GBK 中文", Array.from(Buffer.from("东方星莲船对话文本测试内容", "binary").length ? [0xB6, 0xAB, 0xB7, 0xBD, 0xD0, 0xC7, 0xC1, 0xAB, 0xB4, 0xAC] : []), "gbk"],
  ];
  for (const [label, bytes, want] of cases) {
    const got = await ev(`sniffDocEncoding(new Uint8Array([${bytes}]).buffer)`);
    check(`${label} → ${want}`, got === want, "实际 " + got);
  }

  console.log("\n③ 真实 GBK 文档（用后端导出的内容转成 GBK）");
  const gbkCheck = await ev(`(async () => {
    // 取一份真实导出文档，按 GBK 重新编码成字节（用页面能拿到的方式：
    // 直接构造一段带中文的 GBK 字节序列来测判定逻辑）
    const gbkBytes = new Uint8Array([
      0x23, 0x20, 0xB6, 0xAB, 0xB7, 0xBD, 0xD0, 0xC7, 0xC1, 0xAB, 0xB4, 0xAC,  // "# 东方星莲船"
      0x0A, 0x5B, 0x30, 0x2E, 0x31, 0x5D, 0x20, 0x5B, 0xD7, 0xD4, 0xBB, 0xFA, 0x5D, 0x20,
      0xC4, 0xE3, 0xBA, 0xC3, 0x0A                                      // "[0.1] [自机] 你好"
    ]);
    return JSON.stringify({
      kind: sniffDocEncoding(gbkBytes.buffer),
      len: gbkBytes.length,
      probe: new TextDecoder("utf-8", {fatal:false}).decode(gbkBytes).includes("\\uFFFD")
    });
  })()`);
  const g = JSON.parse(gbkCheck);
  check("真实 GBK 字节被判为 gbk", g.kind === "gbk", JSON.stringify(g));
  check("GBK 字节按 UTF-8 解码确实产生替换字符", g.probe === true);

  console.log("\n④ 导出接口返回的文档带 BOM（前端拿到即可判定）");
  const bom = await ev(`(async () => {
    const r = await fetch("/api/msg.doc?game=jp&name=st01_00a.msg");
    const b = new Uint8Array(await r.arrayBuffer());
    return JSON.stringify({ status: r.status, b0: b[0], b1: b[1], b2: b[2],
                            kind: sniffDocEncoding(b.buffer) });
  })()`);
  const bm = JSON.parse(bom);
  check("导出成功", bm.status === 200, "HTTP " + bm.status);
  check("前 3 字节是 BOM", bm.b0 === 0xEF && bm.b1 === 0xBB && bm.b2 === 0xBF,
        `${bm.b0},${bm.b1},${bm.b2}`);
  check("被判定为 utf8-bom", bm.kind === "utf8-bom", bm.kind);

  console.log("\n⑤ boot() 容错：单个接口失败不应中断其它页签");
  const bootCheck = await ev(`(async () => {
    const calls = [];
    const orig = window.fetch;
    window.fetch = async (u, o) => {
      const url = String(u);
      calls.push(url);
      if (url.includes("/api/bgm")) {
        return new Response(JSON.stringify({error:"模拟音乐失败"}), 
          {status: 500, headers: {"Content-Type":"application/json"}});
      }
      return orig(u, o);
    };
    try {
      const failed = await refreshAll();
      return JSON.stringify({ failed, calledBgm: calls.some(c=>c.includes("/api/bgm")),
                              calledMusiccmt: calls.some(c=>c.includes("/api/musiccmt")),
                              calledArchive: calls.some(c=>c.includes("/api/archive")) });
    } finally { window.fetch = orig; }
  })()`);
  const bc = JSON.parse(bootCheck);
  check("loadBgm 失败被捕获（不是抛出）", Array.isArray(bc.failed) && bc.failed.length >= 1,
        JSON.stringify(bc.failed));
  check("失败后仍然继续调用了 archive", bc.calledArchive === true);
  check("失败后仍然继续调用了 musiccmt（以前会被跳过）", bc.calledMusiccmt === true);

  check("全程无 JS 异常", errs.length === 0, errs.slice(0, 2).join(" | "));
  console.log("=".repeat(58));
  console.log(failures === 0 ? "全部通过 ✓" : ("失败 " + failures + " 项"));
  ws.close();
  process.exit(failures === 0 ? 0 : 1);
}
main().catch((e) => { console.error("测试脚本失败:", e.message); process.exit(2); });
