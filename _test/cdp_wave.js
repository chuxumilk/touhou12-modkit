/* 浏览器端验证音乐页的波形播放器。
 *
 * 覆盖：打开播放器 → 波形绘制 → 循环点标记 → 点波形跳转 →
 *       拖动 A 标记 → 设为当前位置 → 播放/停止 → 界面数值一致。
 *
 * 用法: node _test/cdp_wave.js <serverPort> <debugPort>
 */
const http = require("http");
const net = require("net");
const crypto = require("crypto");

const SERVER_PORT = parseInt(process.argv[2] || "8799", 10);
const DEBUG_PORT = parseInt(process.argv[3] || "9222", 10);

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

  console.log("① 打开音乐页并试听曲目 0");
  await ev(`document.querySelector('[data-tab="bgm"]').click()`);
  await sleep(1500);
  const rows = await ev(`document.querySelectorAll("#bgm-table tbody tr").length`);
  check("音乐表格有数据", rows >= 18, rows + " 行");
  check("表格里不再有内嵌 audio（改用播放器）",
        (await ev(`document.querySelectorAll("#bgm-table audio").length`)) === 0);

  await ev(`document.querySelectorAll("#bgm-table tbody tr")[0]
        .querySelector('[data-act="play"]').click()`);
  await sleep(500);
  const shown = await ev(`!document.querySelector("#bgm-player").classList.contains("hidden")`);
  check("播放器已展开", shown === true);

  console.log("\n② 波形数据与绘制");
  // 等波形算完（首次约 0.3~1 秒）
  let ready = false;
  for (let i = 0; i < 60; i++) {
    const h = await ev(`document.querySelector("#bgm-wave-hint").className`);
    if (h.includes("hidden") || h.includes("error")) { ready = true; break; }
    await sleep(500);
  }
  const hintClass = await ev(`document.querySelector("#bgm-wave-hint").className`);
  check("波形已就绪（提示已隐藏）", hintClass.includes("hidden"), hintClass);
  const peaksInfo = await ev(`JSON.stringify({
    points: (WAVE.peaks || []).length,
    sec: WAVE.sec,
    totalText: document.querySelector("#bgm-total").textContent,
    nonZero: (WAVE.peaks || []).filter(x => x > 0).length })`);
  const pi = JSON.parse(peaksInfo);
  console.log("   " + peaksInfo);
  check("包络点数 > 100", pi.points > 100, String(pi.points));
  check("波形不是平的（有非零点）", pi.nonZero > 20, pi.nonZero + " 个非零点");
  check("总时长显示到界面", /^\d+:\d\d\.\d$/.test(pi.totalText), pi.totalText);

  console.log("\n③ 画布真的画了东西（取像素统计）");
  const pix = JSON.parse(await ev(`(() => {
    const c = document.querySelector("#bgm-canvas");
    const g = c.getContext("2d");
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let painted = 0, blue = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i+3] > 0) { painted++;
        if (d[i] < 200 && d[i+2] > 180) blue++; }
    }
    return JSON.stringify({ w: c.width, h: c.height, painted, blue });
  })()`));
  console.log("   " + JSON.stringify(pix));
  check("画布有内容", pix.painted > 1000, pix.painted + " 像素");
  check("能看到蓝色（循环区/标记）", pix.blue > 50, pix.blue + " 像素");

  console.log("\n④ 点波形跳转");
  // 先等音频元数据就绪 —— seek 需要在有 duration 之后才生效。
  // 这比依赖自动播放更贴近真实场景（用户点波形时页面早就加载完了）。
  await ev(`(async () => {
    const a = document.querySelector("#bgm-audio");
    if (a.readyState >= 1) return "已就绪";
    await new Promise((res) => {
      const done = () => res();
      a.addEventListener("loadedmetadata", done, { once: true });
      a.addEventListener("canplay", done, { once: true });
      a.load();
      setTimeout(done, 8000);
    });
    return "readyState=" + a.readyState;
  })()`);
  await sleep(400);
  const audioState = await ev(`JSON.stringify({
    readyState: document.querySelector("#bgm-audio").readyState,
    duration: document.querySelector("#bgm-audio").duration })`);
  console.log("   audio: " + audioState);
  const seeked = JSON.parse(await ev(`(() => {
    const c = document.querySelector("#bgm-canvas");
    const r = c.getBoundingClientRect();
    const a = document.querySelector("#bgm-audio");
    c.onclick({ clientX: r.left + r.width * 0.5 });
    return JSON.stringify({ t: a.currentTime, expect: WAVE.sec / 2 });
  })()`));
  console.log("   " + JSON.stringify(seeked));
  check("播放位置跳到中点（±1 秒）",
        Math.abs(seeked.t - seeked.expect) < 1.0,
        `t=${seeked.t.toFixed(2)} expect=${seeked.expect.toFixed(2)}`);

  console.log("\n⑤ 循环区随循环点移动");
  const before = await ev(`WAVE.loopBytes`);
  await ev(`(() => { const i = document.querySelector('input[data-loop="0"]');
    i.value = "20.0"; setBgmLoop({ index: 0 }); })()`);
  await sleep(2500);
  const after = JSON.parse(await ev(`JSON.stringify({
    loopBytes: WAVE.loopBytes,
    show: document.querySelector("#bgm-loop-show").textContent,
    inputVal: document.querySelector('input[data-loop="0"]').value })`));
  console.log("   " + JSON.stringify(after));
  check("循环点已更新为 20 秒",
        Math.abs(after.loopBytes / 176400 - 20) < 0.2,
        (after.loopBytes / 176400).toFixed(2) + " 秒");
  check("侧栏显示同步", Math.abs(parseFloat(after.show) - 20) < 0.2, after.show);
  check("输入框同步", Math.abs(parseFloat(after.inputVal) - 20) < 0.2, after.inputVal);

  console.log("\n⑥ 拖动 A 标记");
  const dragged = JSON.parse(await ev(`(() => {
    const c = document.querySelector("#bgm-canvas");
    const r = c.getBoundingClientRect();
    // 先按住 A 标记（当前在 20/59.5 处）
    const xA = r.left + r.width * (WAVE.loopBytes / 176400 / WAVE.sec);
    c.onmousedown({ clientX: xA, preventDefault() {} });
    const dragging = WAVE.drag;
    // 拖到 60% 处
    window.dispatchEvent(new MouseEvent("mousemove",
      { clientX: r.left + r.width * 0.6 }));
    const mid = WAVE.loopBytes / 176400;
    window.dispatchEvent(new MouseEvent("mouseup"));
    return JSON.stringify({ dragging, mid, afterDrag: WAVE.drag });
  })()`));
  console.log("   " + JSON.stringify(dragged));
  check("按下 A 标记能进入拖动状态", dragged.dragging === "A", dragged.dragging);
  check("拖动过程中循环点随鼠标变化",
        Math.abs(dragged.mid - 0.6 * 59.49) < 1.5,
        dragged.mid.toFixed(2) + " 秒");
  check("松手后退出拖动状态", dragged.afterDrag === null);
  await sleep(2500);

  console.log("\n⑦ 播放 / 停止与播放时间显示");
  await ev(`document.querySelector("#bgm-play").click()`);
  await sleep(1800);
  const playing = JSON.parse(await ev(`JSON.stringify({
    paused: document.querySelector("#bgm-audio").paused,
    btn: document.querySelector("#bgm-play").textContent,
    timer: WAVE.timer !== null,
    cur: document.querySelector("#bgm-cur").textContent })`));
  console.log("   " + JSON.stringify(playing));
  check("已开始播放", playing.paused === false);
  check("按钮变成暂停", /暂停/.test(playing.btn), playing.btn);
  check("播放头定时器在跑", playing.timer === true);
  check("当前时间在走（不是 0:00.0）", playing.cur !== "0:00.0", playing.cur);

  await ev(`document.querySelector("#bgm-stop").click()`);
  await sleep(400);
  const stopped = JSON.parse(await ev(`JSON.stringify({
    paused: document.querySelector("#bgm-audio").paused,
    t: document.querySelector("#bgm-audio").currentTime,
    timer: WAVE.timer !== null })`));
  console.log("   " + JSON.stringify(stopped));
  check("已停止", stopped.paused === true);
  check("回到 0", stopped.t === 0);
  check("定时器已清理（不常驻轮询）", stopped.timer === false);

  console.log("\n⑧ 「设为当前位置」按钮");
  // 走与应用相同的 seek 路径（元数据没就绪时会重试）
  await ev(`seekWaveTo(33.0)`);
  await sleep(800);
  await ev(`document.querySelector("#bgm-loop-here").click()`);
  await sleep(2500);
  const here = JSON.parse(await ev(`JSON.stringify({
    loopSec: WAVE.loopBytes / 176400,
    show: document.querySelector("#bgm-loop-show").textContent,
    inputVal: document.querySelector('input[data-loop="0"]').value })`));
  console.log("   " + JSON.stringify(here));
  check("循环点设为当前播放位置（33 秒 ±1）",
        Math.abs(here.loopSec - 33) < 1.0, here.loopSec.toFixed(2));
  check("输入框同步为 33", Math.abs(parseFloat(here.inputVal) - 33) < 1.0,
        here.inputVal);

  check("全程无 JS 异常", errs.length === 0, errs.slice(0, 2).join(" | "));
  console.log("=".repeat(58));
  console.log(failures === 0 ? "全部通过 ✓" : ("失败 " + failures + " 项"));
  ws.close();
  process.exit(failures === 0 ? 0 : 1);
}
main().catch((e) => { console.error("测试脚本失败:", e.message); process.exit(2); });
