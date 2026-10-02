/* 东方星莲船 魔改工具 —— 前端逻辑 */
"use strict";

const S = {
  game: "jp",
  entries: [],
  anmName: null,
  textures: [],
  searchMode: false,
  msgName: null,
  msgData: null,
  bgm: null,
  pending: null,
  lastComment: "",
  pendingCount: 0,
  settings: null,
  pathEdited: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ---------------- 基础 ---------------- */
async function api(path, opts) {
  const res = await fetch(path, opts);
  const ctype = res.headers.get("Content-Type") || "";
  if (!res.ok) {
    let msg = res.statusText;
    if (ctype.includes("json")) {
      try { msg = (await res.json()).error || msg; } catch (e) {}
    }
    throw new Error(msg);
  }
  return ctype.includes("json") ? res.json() : res;
}

let toastTimer = null;
function toast(msg, isError) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
}

function fmtDuration(sec) {
  sec = Math.max(0, sec || 0);
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(1);
  return `${m}:${s.padStart(4, "0")}`;
}

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

function pickFile(accept) {
  return new Promise((resolve) => {
    const input = $("#hidden-file");
    input.value = "";
    input.accept = accept || "";
    // 用户取消对话框时也要 resolve，否则调用方的 await 会永远挂住
    // （表现为「点了没反应」，而且闭包一直留着）。
    let done = false;
    const finish = (f) => {
      if (done) return;
      done = true;
      input.onchange = null;
      window.removeEventListener("focus", onFocus);
      resolve(f || null);
    };
    const onFocus = () => {
      // 对话框关闭后窗口重新获得焦点：给 change 事件一点时间先到
      setTimeout(() => finish(input.files && input.files[0]), 400);
    };
    input.onchange = () => finish(input.files[0] || null);
    window.addEventListener("focus", onFocus, { once: true });
    input.click();
  });
}

/**
 * 粗判文本编码：返回 "utf8-bom" | "utf16" | "gbk" | "utf8"。
 *
 * 为什么需要它：`File.text()` 是**硬性** UTF-8 解码。用户把导出的文档
 * 在记事本里「另存为 ANSI」之后，GBK 字节会在浏览器里就变成 U+FFFD，
 * 后端再怎么容错也救不回来 —— 译文会被静默毁掉。
 */
function sniffDocEncoding(buf) {
  const b = new Uint8Array(buf);
  if (b.length >= 3 && b[0] === 0xEF && b[1] === 0xBB && b[2] === 0xBF) {
    return "utf8-bom";
  }
  if (b.length >= 2 && ((b[0] === 0xFF && b[1] === 0xFE) ||
                        (b[0] === 0xFE && b[1] === 0xFF))) {
    return "utf16";
  }
  if (!b.length) return "utf8";
  // 用非致命解码器试探：出现 U+FFFD 说明不是合法 UTF-8
  const probe = new TextDecoder("utf-8", { fatal: false })
    .decode(b.subarray(0, Math.min(b.length, 65536)));
  if (!probe.includes("\uFFFD")) return "utf8";
  // 再看像不像 GBK：GBK 是双字节，首字节 0x81-0xFE、次字节 0x40-0xFE
  let pairs = 0, ascii = 0;
  for (let i = 0; i < b.length && i < 4096; i++) {
    if (b[i] < 0x80) { ascii++; continue; }
    if (b[i] >= 0x81 && b[i] <= 0xFE && i + 1 < b.length &&
        b[i + 1] >= 0x40 && b[i + 1] <= 0xFE) { pairs++; i++; }
  }
  return pairs > 0 && pairs * 4 > ascii ? "gbk" : "utf8";
}

/* ---------------- 标签切换 ---------------- */
$$("#tabs button").forEach((btn) => {
  btn.onclick = () => {
    $$("#tabs button").forEach((b) => b.classList.remove("active"));
    $$(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "backup") loadBackups();
    if (btn.dataset.tab === "log") loadLogs();
    if (btn.dataset.tab === "batch") loadBatch();
  };
});

/* ---------------- 初始化 ---------------- */
async function initSettings() {
  try {
    S.settings = await api("/api/config");
  } catch (e) {
    S.settings = null;
  }
}

async function boot() {
  const st = await api("/api/state");
  $("#game-dir").textContent = st.game_dir || "";
  if (!st.games || st.games.length === 0) {
    // 没配好目录：顶部按钮高亮提示，但不再强弹对话框挡住其它内容
    $("#btn-settings").classList.add("attention");
    toast("还没有设置游戏目录，点右上角「⚙ 设置目录」选一份 TH12", true);
    await initSettings();
    return;
  }
  if (st.bgm_error) {
    // 音乐数据坏了不影响贴图/对话，只提醒一下
    toast("音乐数据读取失败：" + String(st.bgm_error).split("\n")[0]
          + "（贴图 / 对话仍可正常修改）", true);
  }
  $("#btn-settings").classList.remove("attention");
  const sel = $("#game-select");
  sel.innerHTML = "";
  st.games.forEach((g) => {
    const opt = document.createElement("option");
    opt.value = g.key;
    opt.textContent = g.label;
    sel.appendChild(opt);
  });
  S.game = st.default_game || st.games[0].key;
  sel.value = S.game;
  sel.onchange = async () => {
    S.game = sel.value;
    S.anmName = null;
    S.msgName = null;
    S.textures = [];
    $("#texture-grid").innerHTML = "";
    $("#msg-body").innerHTML = "";
    $("#texture-title").textContent = "请选择左侧的贴图文件";
    $("#msg-title").textContent = "请选择左侧的对话文件";
    $("#msg-save").disabled = true;
    await refreshAll();
  };
  updatePending(st.bgm);
  await refreshAll();
}

/**
 * 刷新各个页签的数据。**每一项单独容错**：
 * 以前是顺序 await，任意一个接口抛错都会中断后面的初始化
 * （例如音乐数据损坏时 loadBgm 抛错 → 音乐室评论页不加载），
 * 而且异常冒到顶层后会把具体提示覆盖成笼统的「初始化失败」。
 */
async function refreshAll() {
  const jobs = [
    ["待保存列表", loadPending],
    ["归档列表", loadArchive],
    ["音乐", loadBgm],
    ["音乐室评论", loadMusiccmt],
  ];
  const failed = [];
  for (const [label, fn] of jobs) {
    try {
      await fn();
    } catch (ex) {
      failed.push(`${label}(${ex.message})`);
    }
  }
  if (failed.length) {
    toast("部分内容加载失败：" + failed.join("；")
          + "　其它页签仍可使用", true);
  }
  return failed;
}

/* ---------------- 设置游戏目录 / 启动游戏 ---------------- */
function shortPath(p) {
  if (!p) return "(未设置)";
  const parts = p.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 3) return p;
  return parts[0] + "\\…\\" + parts.slice(-2).join("\\");
}

function setStatus(text, cls) {
  const el = $("#settings-status");
  if (!el) return;
  el.textContent = text;
  el.className = "status" + (cls ? " " + cls : "");
}

async function openSettings() {
  const modal = $("#settings-modal");
  modal.classList.remove("hidden");
  $("#settings-live").textContent = "";
  $("#settings-live").className = "live";
  // 用户可能在 config 返回前就已经粘贴了路径：那就别再用旧值覆盖他的输入
  S.pathEdited = false;
  const wasEmpty = !$("#settings-path").value.trim();
  try {
    S.settings = await api("/api/config");
  } catch (e) { /* 用旧数据 */ }
  if (S.pathEdited) {
    // 期间用户自己动过输入框：保留他的内容，只刷新状态和候选
    showCurrentDir();
    renderCandidates();
    return;
  }
  showCurrentDir();
  const cur = (S.settings && S.settings.game_dir) || "";
  if (wasEmpty || !$("#settings-path").value.trim()) {
    $("#settings-path").value = cur;
  }
  renderCandidates();
  // 当前目录本身是好的就不用提醒，否则立刻给出可操作提示
  if (!(S.settings && S.settings.games && S.settings.games.length)) {
    validatePath($("#settings-path").value.trim());
  }
}

function showCurrentDir() {
  const cfg = S.settings || {};
  const games = cfg.games || [];
  if (games.length) {
    setStatus("当前：已连接 " + games.map((g) => g.label).join(" / ")
              + "\n" + (cfg.game_dir || ""), "ok");
  } else if (cfg.dir_exists) {
    setStatus("当前目录里没有 th12.dat / th12c.dat：\n" + (cfg.game_dir || "")
              + "\n请在下面选一个游戏目录，或点「重新扫描」。", "err");
  } else {
    setStatus("尚未设置游戏目录（下面扫描到的目录可以直接点选）", "err");
  }
}

function renderCandidates() {
  const box = $("#settings-candidates");
  if (!box) return;
  const cfg = S.settings || {};
  const items = (cfg.candidates || []).filter((c) => c && c.path);
  box.innerHTML = "";
  if (!items.length) {
    const d = document.createElement("div");
    d.className = "cand-empty";
    d.textContent = "没扫描到游戏目录。点「重新扫描」，或用「浏览…」手动选文件夹；"
      + "也可以直接在输入框粘贴路径。";
    box.appendChild(d);
    return;
  }
  items.forEach((c) => {
    const div = document.createElement("div");
    div.className = "cand";
    const p = document.createElement("span");
    p.className = "cand-path";
    p.textContent = c.path;
    const tag = document.createElement("span");
    tag.className = "cand-tag";
    tag.textContent = (c.games || [])
      .map((k) => (k === "jp" ? "日文" : "汉化")).join("+");
    div.appendChild(p);
    div.appendChild(tag);
    div.onclick = () => {
      S.pathEdited = true;      // 点了候选就等于用户做了选择
      $("#settings-path").value = c.path;
      validatePath(c.path);
    };
    box.appendChild(div);
  });
}

function showLive(text, cls) {
  const el = $("#settings-live");
  if (!el) return;
  el.textContent = text || "";
  el.className = "live" + (cls ? " " + cls : "");
}

let validateTimer = null;
async function validatePath(path) {
  const p = (path || "").trim();
  if (!p) {
    showLive("", "");
    showCurrentDir();
    return;
  }
  showLive("检查中…", "");
  try {
    const r = await api("/api/check?path=" + encodeURIComponent(p));
    const problems = r.problems || [];
    if (problems.length) {
      // 目录能用，但数据有损坏（例如归档被写坏、thbgm 对不上）
      showLive("⚠ " + problems[0].split("\n")[0], "warn");
    } else if (r.ok) {
      showLive("✓ " + "可用（" + (r.games || []).map(
        (k) => (k === "jp" ? "日文版" : "汉化版")).join(" / ") + "）"
        + (r.from_file ? "　已根据文件定位到目录" : ""), "ok");
    } else if (r.suggest) {
      showLive("⚠ 选到上一级了，游戏在：" + r.suggest
               + "（点「应用」会自动用它）", "warn");
    } else {
      showLive("✗ " + (r.message || "这个目录不能用"), "err");
    }
  } catch (e) {
    showLive("✗ 检查失败：" + e.message, "err");
  }
}

function scheduleValidate() {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(() => validatePath($("#settings-path").value),
                             350);
}

$("#btn-settings").onclick = () => openSettings();
$("#settings-cancel").onclick = () => $("#settings-modal").classList.add("hidden");
$("#settings-modal").onclick = () => $("#settings-modal").classList.add("hidden");
$("#settings-path").oninput = () => {
  S.pathEdited = true;      // 记下「用户自己动过」，避免被旧的预填值覆盖
  scheduleValidate();
};

$("#settings-rescan").onclick = async () => {
  const btn = $("#settings-rescan");
  btn.disabled = true;
  btn.textContent = "扫描中…";
  try {
    const r = await api("/api/rescan");
    S.settings = await api("/api/config");
    renderCandidates();
    showCurrentDir();
    toast("扫描完成，找到 " + (r.count || 0) + " 个游戏目录");
  } catch (ex) {
    toast("扫描失败: " + ex.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "重新扫描";
  }
};

async function pickWithDialog(kind) {
  toast(kind === "file" ? "请在弹窗中选择 th12.dat / th12c.dat …"
                        : "请在弹窗中选择游戏文件夹…");
  const cur = $("#settings-path").value.trim();
  const url = "/api/pick-dir?kind=" + kind +
              "&path=" + encodeURIComponent(cur);
  try {
    const res = await api(url, { method: "POST" });
    if (res && res.path) {
      $("#settings-path").value = res.path;
      await validatePath(res.path);
      toast("已选择：" + res.path);
    } else if (res && res.fallback) {
      $("#settings-path").value = res.fallback;
      validatePath(res.fallback);
      toast((res.error || "没能打开系统对话框") + "，已保留你填的路径", true);
    } else {
      toast((res && res.error) || "没有选择（可以直接粘贴路径）", true);
    }
  } catch (ex) {
    toast("打开选择框失败: " + ex.message + "（可以直接粘贴路径）", true);
  }
}

$("#settings-browse").onclick = () => pickWithDialog("dir");
$("#settings-browse-file").onclick = () => pickWithDialog("file");

$("#settings-apply").onclick = async () => {
  const path = $("#settings-path").value.trim();
  if (!path) { showLive("✗ 请先填写或选择一个游戏目录", "err"); return; }
  const btn = $("#settings-apply");
  btn.disabled = true;
  try {
    const r = await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ game_dir: path }),
    });
    if (r.used_parent) {
      $("#settings-path").value = r.game_dir || path;
      toast(r.message || ("已自动改用：" + (r.game_dir || "")), true);
    } else {
      toast("游戏目录已切换");
    }
    if (r.config_warning) toast(r.config_warning, true);
    // 目录能用但数据有损坏（例如归档被写坏）→ 直接说清楚，别等用户踩坑
    if (r.warning) toast("注意：" + r.warning, true);
    $("#settings-modal").classList.add("hidden");
    S.anmName = null; S.msgName = null; S.textures = [];
    $("#texture-grid").innerHTML = "";
    $("#msg-body").innerHTML = "";
    $("#anm-list").innerHTML = "";
    $("#msg-list").innerHTML = "";
    await boot();
  } catch (ex) {
    // 报错留在弹窗里，不会一闪而过；顺便再检查一次给出可操作提示
    showLive("✗ " + ex.message, "err");
    toast("设置失败：" + ex.message.split("\n")[0], true);
    validatePath(path);
  } finally {
    btn.disabled = false;
  }
};

$("#btn-launch").onclick = async () => {
  try {
    const res = await api(`/api/launch?game=${S.game}`, { method: "POST" });
    toast("已启动 " + res.exe);
  } catch (ex) {
    toast("启动失败: " + ex.message, true);
  }
};

function updatePending(bgminfo) {
  // 保留旧签名兼容 boot() 的调用；但按钮状态统一由 applyBgmButtons()
  // 根据 /api/pending 决定（见那里的注释）。
  if (bgminfo) S.bgmInfo = bgminfo;
  applyBgmButtons();
}

/* ---------------- 归档列表 ---------------- */
async function loadArchive() {
  const data = await api(`/api/archive?game=${S.game}`);
  S.entries = data.entries;
  renderAnmList();
  renderFileTable();
  renderMsgList();
}

function renderAnmList() {
  const ul = $("#anm-list");
  ul.innerHTML = "";
  S.entries.filter((e) => e.name.endsWith(".anm"))
    .forEach((e) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${e.name}</span>
        <span class="size">${fmtSize(e.size)}</span>`;
      li.dataset.name = e.name;
      if (e.name === S.anmName) li.classList.add("active");
      li.onclick = () => openAnm(e.name, li);
      ul.appendChild(li);
    });
}

/* ---------------- 贴图全局搜索 ---------------- */
let texSearchTimer = null;

function onTexSearchInput() {
  clearTimeout(texSearchTimer);
  const q = $("#tex-search").value.trim();
  texSearchTimer = setTimeout(() => doTexSearch(q), 220);
}

async function doTexSearch(q) {
  const ul = $("#anm-list");
  if (!q) {
    S.searchMode = false;
    $("#side-hint").textContent = "按文件名浏览，或输入关键词全局搜索";
    renderAnmList();
    if (!S.anmName) {
      $("#texture-title").textContent = "请选择左侧的贴图文件";
      $("#texture-grid").innerHTML = "";
      $("#texture-count").textContent = "";
    } else {
      openAnm(S.anmName);
    }
    return;
  }
  S.searchMode = true;
  $("#side-hint").textContent = "正在搜索…";
  try {
    startProgressWatch();
    const data = await api(
      `/api/textures.search?game=${S.game}&q=${encodeURIComponent(q)}`);
    stopProgressWatch();
    $("#side-hint").textContent =
      `找到 ${data.total} 张（显示前 ${data.results.length} 张）`;
    // 左侧：按 ANM 归类
    const byAnm = {};
    data.results.forEach((r) => {
      (byAnm[r.anm] = byAnm[r.anm] || []).push(r);
    });
    ul.innerHTML = "";
    Object.keys(byAnm).sort().forEach((anm) => {
      const head = document.createElement("li");
      head.className = "list-head";
      head.textContent = `${anm}（${byAnm[anm].length}）`;
      ul.appendChild(head);
      byAnm[anm].forEach((r) => {
        const li = document.createElement("li");
        li.innerHTML = `<span>${r.name}</span>
          <span class="size">#${r.index} ${r.w}×${r.h}</span>`;
        li.onclick = () => {
          $$("#anm-list li").forEach((el) => el.classList.remove("active"));
          li.classList.add("active");
          showSearchResult(r, li);
        };
        ul.appendChild(li);
      });
    });
    if (!data.results.length) {
      ul.innerHTML = '<li class="list-empty">没有匹配的贴图</li>';
    }
    // 右侧：直接展示所有命中
    $("#texture-title").textContent = `搜索「${q}」`;
    $("#texture-count").textContent = `共 ${data.total} 张`;
    renderTextureCards(data.results.map((r) => ({
      index: r.index, name: r.name, width: r.w, height: r.h,
      format_name: r.format, size: 0, anm: r.anm,
    })));
  } catch (ex) {
    $("#side-hint").textContent = "搜索失败: " + ex.message;
  }
}

function showSearchResult(r, li) {
  S.anmName = r.anm;
  $("#texture-title").textContent = `${r.anm} — ${r.name}`;
  $("#texture-count").textContent = `#${r.index} · ${r.w}×${r.h} · ${r.format}`;
  renderTextureCards([{
    index: r.index, name: r.name, width: r.w, height: r.h,
    format_name: r.format, size: 0, anm: r.anm,
  }]);
}

$("#texture-export-zip").onclick = () => {
  const anmName = S.exportAnm || S.anmName;
  if (!anmName) {
    toast("先打开一个 .anm 文件", true);
    return;
  }
  const a = document.createElement("a");
  a.href = `/api/textures.zip?game=${S.game}` +
    `&anm=${encodeURIComponent(anmName)}`;
  a.download = anmName.replace(/\.anm$/i, "") + ".zip";
  a.click();
  toast("正在打包 " + anmName + " 的贴图…");
};

$("#tex-search").oninput = onTexSearchInput;
$("#tex-search").onsearch = () => doTexSearch($("#tex-search").value.trim());
$("#tex-search").onkeydown = (ev) => {
  if (ev.key === "Escape") {
    $("#tex-search").value = "";
    doTexSearch("");
  }
};

/* ---------------- 贴图 ---------------- */
async function openAnm(name, li) {
  S.anmName = name;
  S.searchMode = false;
  const box = $("#tex-search");
  if (box.value) {
    box.value = "";
    $("#side-hint").textContent = "按文件名浏览，或输入关键词全局搜索";
    renderAnmList();
  }
  $$("#anm-list li").forEach((el) => el.classList.remove("active"));
  if (li) li.classList.add("active");
  S.exportAnm = name;
  $("#texture-export-zip").disabled = false;
  $("#texture-title").textContent = name;
  const grid = $("#texture-grid");
  grid.innerHTML = '<p class="muted" style="padding:8px">正在解析…</p>';
  startProgressWatch();
  try {
    const data = await api(
      `/api/textures?game=${S.game}&anm=${encodeURIComponent(name)}`);
    S.textures = data.textures.map((t) => Object.assign({ anm: name }, t));
    $("#texture-count").textContent = `共 ${data.textures.length} 张贴图`;
    renderTextureCards(S.textures);
  } catch (ex) {
    grid.innerHTML = "";
    toast("解析失败: " + ex.message, true);
  } finally {
    stopProgressWatch();
  }
}

/* 导出文件名：优先用游戏内原始贴图名（face/xx/face02no.png -> face02no.png） */
function texExportName(t) {
  let base = (t.name || "texture").split("/").pop() || "texture";
  if (!/\.png$/i.test(base)) base += ".png";
  return base;
}

function renderTextureCards(list) {
  const grid = $("#texture-grid");
  grid.innerHTML = "";
  if (!list.length) {
    grid.innerHTML = '<p class="muted" style="padding:8px">没有贴图</p>';
    return;
  }
  list.forEach((t) => {
    const anmName = t.anm || S.anmName;
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.index = t.index;
    const url = `/api/texture.png?game=${S.game}` +
      `&anm=${encodeURIComponent(anmName)}&index=${t.index}`;
    const meta = [`#${t.index}`, `${t.width}×${t.height}`,
      t.format_name || t.format];
    if (t.size) meta.push(fmtSize(t.size));
    card.innerHTML = `
      <div class="thumb"><img loading="lazy" src="${url}" alt=""></div>
      <div class="name" title="${t.name}">${t.name || "(无名)"}</div>
      <div class="meta">${meta.join(" · ")}</div>
      ${anmName !== S.anmName || S.searchMode
        ? `<div class="meta anm-tag">${anmName}</div>` : ""}
      <div class="btns">
        <button class="mini" data-act="replace">替换</button>
        <button class="mini" data-act="download">导出</button>
      </div>`;
    card.querySelector(".thumb").onclick = () => {
      $("#lightbox-img").src = url;
      $("#lightbox").classList.remove("hidden");
    };
    card.querySelector('[data-act="download"]').onclick = () => {
      const a = document.createElement("a");
      a.href = url;
      a.download = texExportName(t);
      a.click();
    };
    card.querySelector('[data-act="replace"]').onclick =
      () => replaceTexture(t, anmName);
    grid.appendChild(card);
  });
}

async function replaceTexture(t, anmName) {
  anmName = anmName || t.anm || S.anmName;
  const file = await pickFile("image/png,image/*");
  if (!file) return;
  try {
    const buf = await file.arrayBuffer();
    const res = await api(
      `/api/texture?game=${S.game}&anm=${encodeURIComponent(anmName)}` +
      `&index=${t.index}`,
      { method: "POST", body: buf });
    toast(`已暂存 ${t.name}（${res.width}×${res.height}），记得点下方「保存到游戏」`);
    await loadPending();
    const card = document.querySelector(`.card[data-index="${t.index}"]`);
    if (card) {
      const img = card.querySelector("img");
      img.src = `/api/texture.png?game=${S.game}` +
        `&anm=${encodeURIComponent(anmName)}&index=${t.index}` +
        `&t=${Date.now()}`;
    }
    if (res.width !== t.width || res.height !== t.height) {
      if (S.searchMode) doTexSearch($("#tex-search").value.trim());
      else openAnm(anmName);
    }
  } catch (ex) {
    toast("替换失败: " + ex.message, true);
  }
}

$("#lightbox").onclick = () => $("#lightbox").classList.add("hidden");

/* ---------------- 音乐 ---------------- */
async function loadBgm() {
  const data = await api("/api/bgm");
  S.bgm = data;
  updatePending(data);
  renderBgm();
}

function renderBgm() {
  const tbody = $("#bgm-table tbody");
  tbody.innerHTML = "";
  if (!S.bgm) return;
  S.bgm.tracks.forEach((t) => {
    const tr = document.createElement("tr");
    const loopSec = (t.pending_loop != null ? t.pending_loop : t.loop) /
      (44100 * 4);
    const status = t.pending
      ? '<span class="pill warn">已暂存替换</span>'
      : '<span class="pill">原始</span>';
    tr.innerHTML = `
      <td>${t.index}</td>
      <td>${t.name}</td>
      <td>${fmtDuration(t.duration)}</td>
      <td><input type="number" min="0" step="0.1" style="width:90px"
           value="${loopSec.toFixed(1)}" data-loop="${t.index}"> 秒</td>
      <td>${status}</td>
      <td><audio controls preload="none"
           src="/api/bgm.wav?index=${t.index}"></audio></td>
      <td><div class="row-actions">
        <button class="mini" data-act="replace">替换</button>
        <button class="mini" data-act="loop">设置循环点</button>
        <button class="mini" data-act="download">导出 WAV</button>
      </div></td>`;
    tr.querySelector('[data-act="replace"]').onclick = () => replaceBgm(t);
    tr.querySelector('[data-act="loop"]').onclick = () => setBgmLoop(t);
    tr.querySelector('[data-act="download"]').onclick = () => {
      const a = document.createElement("a");
      a.href = `/api/bgm.wav?index=${t.index}`;
      a.download = t.name.replace(/\.wav$/i, "") + ".wav";
      a.click();
    };
    tbody.appendChild(tr);
  });
}

async function replaceBgm(t) {
  const file = await pickFile("audio/*,.wav,.mp3,.ogg,.flac");
  if (!file) return;
  toast("正在转换音频…");
  try {
    const buf = await file.arrayBuffer();
    const res = await api(`/api/bgm.replace?index=${t.index}`,
      { method: "POST", body: buf });
    toast(`已暂存 ${t.name}（${res.seconds.toFixed(1)} 秒）`);
    await loadBgm();
  } catch (ex) {
    toast("替换失败: " + ex.message, true);
  }
}

async function setBgmLoop(t) {
  const input = document.querySelector(`input[data-loop="${t.index}"]`);
  const sec = parseFloat(input.value) || 0;
  try {
    const res = await api(
      `/api/bgm.loop?index=${t.index}&loop=${Math.round(sec * 44100 * 4)}`,
      { method: "POST" });
    toast(`循环点已设为 ${(res.loop / 176400).toFixed(2)} 秒`);
    await loadBgm();
    // 必须刷新待保存列表：否则按钮状态与底部待保存条都不会更新，
    // 用户改完循环点却找不到保存入口（只能刷新页面）。
    await loadPending();
  } catch (ex) {
    toast("设置失败: " + ex.message, true);
  }
}

$("#bgm-apply").onclick = () => openSaveDialog();

$("#bgm-cancel").onclick = async () => {
  if (!confirm("放弃暂存的音乐修改吗？")) return;
  await api("/api/bgm.cancel", { method: "POST" });
  toast("已放弃暂存的音乐修改");
  await loadBgm();
  await loadPending();
};

/* ---------------- 对话 ---------------- */
function renderMsgList() {
  const filter = $("#msg-filter").value.trim().toLowerCase();
  const ul = $("#msg-list");
  ul.innerHTML = "";
  S.entries.filter((e) => e.name.endsWith(".msg") &&
      (!filter || e.name.toLowerCase().includes(filter)))
    .forEach((e) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${e.name}</span>
        <span class="size">${fmtSize(e.size)}</span>`;
      if (e.name === S.msgName) li.classList.add("active");
      li.onclick = () => openMsg(e.name, li);
      ul.appendChild(li);
    });
}
$("#msg-filter").oninput = renderMsgList;

async function openMsg(name, li) {
  S.msgName = name;
  $$("#msg-list li").forEach((el) => el.classList.remove("active"));
  if (li) li.classList.add("active");
  $("#msg-title").textContent = name;
  $("#msg-body").innerHTML = '<p class="muted">正在解析…</p>';
  try {
    const data = await api(
      `/api/msg?game=${S.game}&name=${encodeURIComponent(name)}`);
    S.msgData = data;
    const info = [];
    if (data.player) info.push("角色: " + data.player);
    if (data.stage) info.push("关卡: " + data.stage);
    if (data.boss) info.push("Boss: " + data.boss);
    if (data.scene) info.push(data.scene);
    info.push("编码: " + data.encoding);
    $("#msg-info").textContent = info.join(" · ");
    renderMsg();
    $("#msg-save").disabled = false;
    $("#msg-export").disabled = false;
    $("#msg-import").disabled = false;
  } catch (ex) {
    $("#msg-body").innerHTML = "";
    toast("解析失败: " + ex.message, true);
  }
}

function renderMsg() {
  const body = $("#msg-body");
  body.innerHTML = "";
  S.msgData.entries.forEach((entry) => {
    const div = document.createElement("div");
    div.className = "msg-entry";
    const head = document.createElement("header");
    head.textContent = `条目 #${entry.index} · id=${entry.extra}`;
    div.appendChild(head);
    const lines = document.createElement("div");
    lines.className = "lines";
    const ops = document.createElement("div");
    ops.className = "msg-ops";
    entry.instructions.forEach((ins) => {
      if (ins.text !== undefined) {
        const row = document.createElement("div");
        row.className = "msg-line";
        const tag = document.createElement("span");
        tag.className = "speaker speaker-" + (ins.speaker || "unknown");
        tag.textContent = ins.speaker_label || "？";
        tag.title = "指令 " + ins.index + " · " + ins.name;
        const ta = document.createElement("textarea");
        ta.value = ins.text;
        ta.dataset.entry = entry.index;
        ta.dataset.instr = ins.index;
        row.appendChild(tag);
        row.appendChild(ta);
        lines.appendChild(row);
      } else {
        const span = document.createElement("span");
        span.className = "pill";
        span.textContent = ins.name || ("type " + ins.type);
        span.title = `type=${ins.type} time=${ins.time} length=${ins.length}`;
        ops.appendChild(span);
      }
    });
    if (lines.childNodes.length) div.appendChild(lines);
    if (ops.childNodes.length) div.appendChild(ops);
    body.appendChild(div);
  });
}

$("#msg-export").onclick = () => {
  if (!S.msgName) return;
  const a = document.createElement("a");
  a.href = `/api/msg.doc?game=${S.game}` +
    `&name=${encodeURIComponent(S.msgName)}`;
  a.download = S.msgName.replace(/\.msg$/i, "") + ".txt";
  a.click();
  toast("已导出对话文档，可用任意文本编辑器翻译后再导入");
};

function diffSummary(res, limit) {
  const lines = [];
  (res.changes || []).slice(0, limit || 8).forEach((c) => {
    lines.push(`[${c.entry}.${c.instr}]\n  旧: ${c.old}\n  新: ${c.new}`);
  });
  if (res.changes && res.changes.length > (limit || 8)) {
    lines.push(`… 还有 ${res.changes.length - (limit || 8)} 处`);
  }
  if (res.unmatched_count) {
    lines.push(`（${res.unmatched_count} 行无法识别，将跳过）`);
  }
  if (res.bad_chars && res.bad_chars.length) {
    lines.push(`（警告: 这些字符无法用 ${res.encoding || "目标编码"} ` +
      `表示，会变成 ? —— ${res.bad_chars.join(" ")}）`);
  }
  return lines.join("\n");
}

$("#msg-import").onclick = async () => {
  if (!S.msgName) return;
  const file = await pickFile(".txt,text/plain");
  if (!file) return;
  try {
    const buf = await file.arrayBuffer();
    const kind = sniffDocEncoding(buf);
    if (kind === "utf16") {
      toast("这个文档像是 UTF-16（记事本「Unicode」编码），"
            + "请另存为「UTF-8」后再导入", true);
      return;
    }
    if (kind === "gbk") {
      const ok = confirm(
        "这个文档不是 UTF-8 编码（像是 GBK/ANSI）。\n\n"
        + "直接导入会出现乱码，工具不会改动游戏文件。\n"
        + "建议在编辑器里「另存为 → 编码选 UTF-8」后重新导入。\n\n"
        + "仍要继续尝试吗？（不推荐）");
      if (!ok) return;
    }
    // 注意：file.text() 是**硬性** UTF-8 解码，GBK 字节会在这里就变成
    // U+FFFD（替换字符），后端再怎么容错也救不回来 —— 所以上面先探测。
    const text = new TextDecoder("utf-8").decode(buf);
    if (text.includes("\uFFFD") && kind !== "gbk") {
      const ok = confirm(
        "文档里有无法按 UTF-8 解码的字节（已变成 �）。\n"
        + "多半是保存成了 GBK/ANSI。\n\n"
        + "继续导入会把这些问题字符写进游戏，确定继续吗？（不推荐）");
      if (!ok) return;
    }
    const url = `/api/msg.preview?game=${S.game}` +
      `&name=${encodeURIComponent(S.msgName)}`;
    const res = await api(url, { method: "POST", body: text });
    if (!res.changed) {
      toast("文档与当前内容一致，没有需要修改的地方");
      return;
    }
    if (!confirm(`将修改 ${res.changed} 句对话：\n\n` +
      diffSummary(res) + "\n\n确认导入吗？")) return;
    const res2 = await api(
      `/api/msg.import?game=${S.game}` +
      `&name=${encodeURIComponent(S.msgName)}`,
      { method: "POST", body: text });
    toast(`已导入并写回 ${res2.changed} 句修改`);
    await openMsg(S.msgName);
    await loadArchive();
  } catch (ex) {
    toast("导入失败: " + ex.message, true);
  }
};

$("#msg-save").onclick = async () => {
  const edits = {};
  $$("#msg-body textarea").forEach((ta) => {
    const ei = ta.dataset.entry;
    const ji = ta.dataset.instr;
    (edits[ei] = edits[ei] || {})[ji] = ta.value;
  });
  const payload = {
    entries: Object.keys(edits).map((ei) => ({
      index: parseInt(ei, 10), texts: edits[ei],
    })),
  };
  try {
    const res = await api(
      `/api/msg?game=${S.game}&name=${encodeURIComponent(S.msgName)}`,
      { method: "POST", body: JSON.stringify(payload) });
    toast(res.changed ? `已暂存 ${res.changed} 处修改，记得点「保存到游戏」` : "没有修改");
    if (res.changed) await loadPending();
    if (res.bad_chars && res.bad_chars.length) {
      toast("注意: 有字符无法用 " + S.msgData.encoding +
        " 表示，已变成 ? —— " + res.bad_chars.join(" "), true);
    }
  } catch (ex) {
    toast("保存失败: " + ex.message, true);
  }
};

/* ---------------- 任意文件 ---------------- */
function renderFileTable() {
  const filter = $("#file-filter").value.trim().toLowerCase();
  const tbody = $("#file-table tbody");
  tbody.innerHTML = "";
  S.entries.filter((e) => !filter || e.name.toLowerCase().includes(filter))
    .forEach((e) => {
      const tr = document.createElement("tr");
      const kindName = {
        texture: "贴图", dialogue: "对话", script: "脚本",
        sound: "音效", shot: "自机", stage: "关卡",
        replay: "录像", other: "其他",
      }[e.kind] || e.kind;
      tr.innerHTML = `
        <td>${e.name}</td>
        <td><span class="pill">${kindName}</span></td>
        <td>${fmtSize(e.size)}</td>
        <td>${fmtSize(e.stored)}</td>
        <td><div class="row-actions">
          <button class="mini" data-act="download">导出</button>
          <button class="mini" data-act="replace">替换</button>
        </div></td>`;
      tr.querySelector('[data-act="download"]').onclick = () => {
        const a = document.createElement("a");
        a.href = `/api/file?game=${S.game}` +
          `&name=${encodeURIComponent(e.name)}`;
        a.download = e.name.split("/").pop();
        a.click();
      };
      tr.querySelector('[data-act="replace"]').onclick =
        () => replaceRawFile(e);
      tbody.appendChild(tr);
    });
}
$("#file-filter").oninput = renderFileTable;

async function replaceRawFile(e) {
  const file = await pickFile();
  if (!file) return;
  if (!confirm(`确定用「${file.name}」替换归档里的「${e.name}」吗？`)) return;
  try {
    const buf = await file.arrayBuffer();
    await api(`/api/file?game=${S.game}&name=${encodeURIComponent(e.name)}`,
      { method: "POST", body: buf });
    toast("已暂存 " + e.name + "，记得点「保存到游戏」");
    await loadPending();
  } catch (ex) {
    toast("替换失败: " + ex.message, true);
  }
}

/* ---------------- 音乐室评论 ---------------- */
async function loadMusiccmt() {
  try {
    const data = await api(`/api/musiccmt?game=${S.game}`);
    $("#musiccmt-text").value = data.text;
  } catch (ex) {
    $("#musiccmt-text").value = "（读取失败: " + ex.message + "）";
  }
}

$("#musiccmt-save").onclick = async () => {
  try {
    await api(`/api/musiccmt?game=${S.game}`, {
      method: "POST",
      body: JSON.stringify({ text: $("#musiccmt-text").value }),
    });
    toast("音乐室评论已暂存，记得点「保存到游戏」");
    await loadPending();
  } catch (ex) {
    toast("保存失败: " + ex.message, true);
  }
};

/* ---------------- 批量导入 ---------------- */
const BATCH_EXT = /\.(png|txt|msg|ecl|sht|std|rpy|wav)$/i;

function batchReadyText(counts) {
  const parts = [];
  if (counts.texture) parts.push("贴图 " + counts.texture);
  if (counts.dialogue) parts.push("对话 " + counts.dialogue);
  if (counts.raw) parts.push("文件 " + counts.raw);
  if (counts.musiccmt) parts.push("评论 " + counts.musiccmt);
  if (counts.unknown) parts.push("未识别 " + counts.unknown);
  return parts.join(" · ") || "空";
}

async function loadBatch() {
  try {
    const data = await api("/api/batch");
    const tbody = $("#batch-table tbody");
    tbody.innerHTML = "";
    if (!data.items.length) {
      tbody.innerHTML =
        '<tr><td colspan="5" class="muted">还没有文件，点右上角「选择文件夹…」</td></tr>';
    }
    data.items.forEach((it) => {
      const kindName = {
        texture: "贴图", dialogue: "对话文档", raw: "原样替换",
        musiccmt: "音乐室评论", unknown: "未识别",
      }[it.kind] || it.kind;
      const tr = document.createElement("tr");
      const canPreview = it.kind === "dialogue" &&
        it.changes && it.changes.length;
      tr.innerHTML = `
        <td>${it.file}</td>
        <td><span class="pill ${it.kind === "unknown" ? "warn" : "ok"}">${kindName}</span></td>
        <td>${it.target}${it.kind === "texture" ? " #" + it.index : ""}</td>
        <td class="muted">${it.detail || ""}</td>
        <td><div class="row-actions">
          ${canPreview ? '<button class="mini" data-act="preview">预览改动</button>' : ""}
          <button class="mini" data-act="del">移除</button>
        </div></td>`;
      if (canPreview) {
        tr.querySelector('[data-act="preview"]').onclick = () =>
          alert(`${it.file}\n\n` + diffSummary(it, 15));
      }
      tr.querySelector('[data-act="del"]').onclick = async () => {
        await api(`/api/batch.remove?id=${it.id}`, { method: "POST" });
        await loadBatch();
      };
      tbody.appendChild(tr);
    });
    $("#batch-apply").disabled = data.ready === 0;
    $("#batch-clear").disabled = data.items.length === 0;
    $("#batch-game").textContent =
      ($("#game-select").selectedOptions[0] || {}).textContent || S.game;
  } catch (ex) {
    toast("读取暂存区失败: " + ex.message, true);
  }
}

async function batchAddFiles(fileList) {
  const files = Array.from(fileList).filter((f) => BATCH_EXT.test(f.name));
  if (!files.length) {
    toast("没有可导入的文件（支持 png / txt / msg / ecl / sht / std / rpy / wav）", true);
    return;
  }
  const bar = $("#batch-progress");
  const inner = bar.firstElementChild;
  bar.classList.remove("hidden");
  inner.style.width = "0%";
  let done = 0;
  const workers = 6;
  let index = 0;
  async function worker() {
    while (index < files.length) {
      const f = files[index++];
      const name = f.webkitRelativePath || f.name;
      try {
        await api(`/api/batch.add?game=${S.game}` +
          `&name=${encodeURIComponent(name)}`,
          { method: "POST", body: f });
      } catch (ex) {
        toast("上传失败: " + f.name + " - " + ex.message, true);
      }
      done++;
      inner.style.width = Math.round(done / files.length * 100) + "%";
    }
  }
  startProgressWatch();
  await Promise.all(Array.from({ length: workers }, worker));
  stopProgressWatch();
  bar.classList.add("hidden");
  await loadBatch();
  const data = await api("/api/batch");
  toast("已识别 " + data.ready + " 个文件（" +
    batchReadyText(data.counts) + "）");
}

$("#batch-folder").onclick = () => {
  const input = $("#hidden-folder");
  input.value = "";
  input.onchange = () => batchAddFiles(input.files);
  input.click();
};

$("#batch-files").onclick = () => {
  const input = $("#hidden-files");
  input.value = "";
  input.onchange = () => batchAddFiles(input.files);
  input.click();
};

$("#batch-clear").onclick = async () => {
  await api("/api/batch.clear", { method: "POST" });
  await loadBatch();
  toast("已清空列表");
};

$("#batch-apply").onclick = async () => {
  const data = await api("/api/batch");
  if (!data.ready) return;
  if (!confirm(`确定导入这 ${data.ready} 个文件吗？\n\n` +
    `会先自动备份原文件。\n（${batchReadyText(data.counts)}）`)) return;
  startProgressWatch();
  try {
    const res = await api("/api/batch.apply", { method: "POST" });
    let msg = `导入完成：贴图 ${res.texture} · 对话 ${res.dialogue} · ` +
      `原样 ${res.raw} · 评论 ${res.musiccmt}`;
    if (res.changed_lines) msg += `（对话共改 ${res.changed_lines} 句）`;
    toast(msg + "，已加入待保存");
    await loadPending();
    if (res.unknown) toast(`有 ${res.unknown} 个文件未识别，已跳过`, true);
    if (res.errors && res.errors.length) {
      toast("部分失败: " + res.errors.slice(0, 3).join("；"), true);
    }
    if (res.bad_chars && res.bad_chars.length) {
      toast("有字符无法编码，已变成 ? —— " + res.bad_chars.join(" "), true);
    }
    await loadBatch();
    await loadArchive();
  } catch (ex) {
    toast("导入失败: " + ex.message, true);
  }
};

/* ---------------- 日志 ---------------- */
async function loadLogs() {
  try {
    const data = await api("/api/logs?limit=300");
    const tbody = $("#log-table tbody");
    tbody.innerHTML = "";
    if (!data.logs.length) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="muted">还没有操作记录</td></tr>';
      return;
    }
    data.logs.forEach((item) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="muted">${item.time}</td>
        <td>${item.action}</td>
        <td>${item.target || ""}</td>
        <td class="muted">${item.detail || ""}</td>`;
      tbody.appendChild(tr);
    });
  } catch (ex) {
    toast("读取日志失败: " + ex.message, true);
  }
}

$("#log-refresh").onclick = loadLogs;
$("#log-clear").onclick = async () => {
  if (!confirm("确定清空日志吗？")) return;
  await api("/api/logs.clear", { method: "POST" });
  toast("日志已清空");
  await loadLogs();
};
$("#log-download").onclick = () => {
  const a = document.createElement("a");
  a.href = "/api/logs.download";
  a.download = "modtool.log";
  a.click();
};

/* ---------------- 实时进度 ---------------- */
let progressTimer = null;

function renderProgress(p) {
  const panel = $("#progress-panel");
  if (!p || !p.active) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  $("#progress-label").textContent = p.label || "处理中…";
  $("#progress-detail").textContent = p.detail || "";
  const total = p.total || 0;
  const cur = p.current || 0;
  $("#progress-count").textContent = total ? `${cur} / ${total}` : "";
  $("#progress-fill").style.width =
    total ? Math.round(Math.min(1, cur / total) * 100) + "%" : "35%";
}

async function pollProgress() {
  try {
    const p = await api("/api/progress");
    renderProgress(p);
  } catch (e) { /* 忽略 */ }
}

function startProgressWatch() {
  if (progressTimer) return;
  pollProgress();
  progressTimer = setInterval(pollProgress, 600);
}

function stopProgressWatch() {
  if (progressTimer) {
    clearInterval(progressTimer);
    progressTimer = null;
  }
  setTimeout(pollProgress, 800);   // 收尾再查一次，确保面板及时收起
}

/* ---------------- 待保存 / 保存 ---------------- */
function applyBgmButtons() {
  // 音乐页两个按钮的可用性只看「有没有待保存项」，不看具体是哪一类。
  // 以前读的是 /api/bgm 里的 pending_count，而那个字段只统计「替换曲目」、
  // 不含「循环点」——只改循环点时两个按钮都是 disabled，
  // 底部待保存条又不刷新，用户就没有任何入口能保存或放弃这次改动。
  const items = (S.pending && S.pending.items) || [];
  const bgmPending = items.some((i) => i.kind === "bgm");
  $("#bgm-apply").disabled = !bgmPending;
  $("#bgm-cancel").disabled = !bgmPending;
}

async function loadPending() {
  try {
    const data = await api("/api/pending");
    S.pending = data;
    S.pendingCount = data.count || 0;
    applyBgmButtons();
    const bar = $("#pending-bar");
    if (!data.count) {
      bar.classList.add("hidden");
      return;
    }
    bar.classList.remove("hidden");
    $("#pending-count").textContent = data.count;
    const names = data.items.slice(0, 3).map((i) => i.name).join("、");
    $("#pending-summary").textContent =
      data.count > 3 ? `${names} 等` : names;
  } catch (ex) { /* 忽略 */ }
}

$("#pending-view").onclick = async () => {
  const data = await api("/api/pending");
  alert("待保存的修改：\n\n" + data.items.map(
    (i, n) => `${n + 1}. [${i.action}] ${i.name}\n     ${i.detail}`
  ).join("\n"));
};

$("#pending-discard").onclick = async () => {
  if (!confirm("放弃所有未保存的修改吗？（游戏文件不会被改动）")) return;
  await api("/api/pending.clear", { method: "POST" });
  toast("已放弃未保存的修改");
  await loadPending();
  await loadBgm();
};

$("#pending-save").onclick = () => openSaveDialog();

async function openSaveDialog() {
  const data = await api("/api/pending");
  if (!data.count) {
    toast("没有待保存的修改");
    return;
  }
  $("#save-list").innerHTML = data.items.map((i) =>
    `<div class="save-item">
       <span class="pill">${i.action}</span>
       <span class="si-name">${i.name}</span>
       <span class="si-detail">${i.detail || ""}</span>
     </div>`).join("");
  $("#save-comment").value = S.lastComment || "";
  $("#save-modal").classList.remove("hidden");
  setTimeout(() => $("#save-comment").focus(), 50);
}

function closeSaveDialog() { $("#save-modal").classList.add("hidden"); }
$("#save-cancel").onclick = closeSaveDialog;
$("#save-modal").onclick = closeSaveDialog;

$("#save-confirm").onclick = async () => {
  const comment = $("#save-comment").value.trim();
  const btn = $("#save-confirm");
  btn.disabled = true;
  btn.textContent = "正在保存…";
  startProgressWatch();
  try {
    const res = await api("/api/save", {
      method: "POST",
      body: JSON.stringify({ comment }),
    });
    S.lastComment = comment;
    closeSaveDialog();
    let msg = `已保存 ${res.files} 个文件`;
    if (res.bgm) msg += " + BGM";
    toast(msg + (res.errors.length ? "（部分失败）" : ""));
    if (res.errors.length) toast("失败: " + res.errors.join("；"), true);
    S.anmName = null;
    S.msgName = null;
    await loadPending();
    await loadArchive();
    await loadBgm();
    await loadMusiccmt();
    await loadBackups();
  } catch (ex) {
    toast("保存失败: " + ex.message, true);
  } finally {
    stopProgressWatch();
    btn.disabled = false;
    btn.textContent = "保存并备份";
  }
};

/* ---------------- 备份 ---------------- */
async function loadBackups() {
  try {
    const data = await api("/api/backups");
    const tbody = $("#backup-table tbody");
    tbody.innerHTML = "";
    if (!data.backups.length) {
      tbody.innerHTML =
        '<tr><td colspan="5" class="muted">还没有备份（第一次修改文件时会自动创建）</td></tr>';
      return;
    }
    data.backups.forEach((b) => {
      const tr = document.createElement("tr");
      const state = b.modified
        ? `<span class="pill warn">已修改</span>
           <span class="muted">${b.current_size_text} · ${b.current_time_text}</span>`
        : `<span class="pill ok">与备份一致</span>`;
      tr.innerHTML = `
        <td>${b.target}<div class="muted">${b.name}</div></td>
        <td>${b.size_text}</td>
        <td>${b.time_text}
          <div class="muted" title="源文件自身的时间戳">原始: ${b.source_time_text}</div></td>
        <td>${state}</td>
        <td><div class="row-actions">
          <button class="mini" data-act="restore">还原</button>
          <button class="mini" data-act="download">下载备份</button>
        </div></td>`;
      tr.querySelector('[data-act="restore"]').onclick = async () => {
        const extra = b.modified
          ? `\n\n当前文件（${b.current_size_text}，${b.current_time_text}）`
            + `与备份不同，还原后会变成备份的样子。\n`
            + `当前版本会自动存为 ${b.target}.modtool.prev（可再改回来）。`
          : "\n\n当前文件与备份一致，还原不会改变内容。";
        if (!confirm(`用备份还原「${b.target}」吗？${extra}`)) return;
        try {
          await api(`/api/restore?key=${b.key}`, { method: "POST" });
          toast(`已还原 ${b.target}`);
          S.anmName = null;
          S.msgName = null;
          await loadArchive();
          await loadBgm();
          await loadMusiccmt();
          await loadBackups();
        } catch (ex) {
          toast("还原失败: " + ex.message, true);
        }
      };
      tr.querySelector('[data-act="download"]').onclick = () => {
        const a = document.createElement("a");
        a.href = `/api/backup.download?key=${b.key}`;
        a.download = b.name;
        a.click();
      };
      tbody.appendChild(tr);
    });
  } catch (ex) {
    toast("读取备份失败: " + ex.message, true);
  }
}

$("#backup-refresh").onclick = loadBackups;
$("#backup-restore-all").onclick = async () => {
  if (!confirm("确定把日文版 / 汉化版 / 音乐全部还原成备份吗？\n\n"
    + "当前版本会自动存为 *.modtool.prev，可以再找回来。")) return;
  try {
    const res = await api("/api/restore.all", { method: "POST" });
    toast("已还原: " + (res.restored.join(" / ") || "无"));
    if (res.errors && res.errors.length) {
      toast("部分失败: " + res.errors.join("；"), true);
    }
    S.anmName = null;
    S.msgName = null;
    await loadArchive();
    await loadBgm();
    await loadMusiccmt();
    await loadBackups();
  } catch (ex) {
    toast("还原失败: " + ex.message, true);
  }
};

boot().catch((ex) => toast("初始化失败: " + ex.message, true));
