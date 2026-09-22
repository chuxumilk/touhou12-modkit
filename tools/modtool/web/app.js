/* 东方星莲船 魔改工具 —— 前端逻辑 */
"use strict";

const S = {
  game: "jp",
  entries: [],
  anmName: null,
  textures: [],
  msgName: null,
  msgData: null,
  bgm: null,
  pendingCount: 0,
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
    input.onchange = () => resolve(input.files[0] || null);
    input.click();
  });
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
async function boot() {
  const st = await api("/api/state");
  $("#game-dir").textContent = st.game_dir || "";
  if (!st.games || st.games.length === 0) {
    openSettings(st.game_dir || "", true);
    return;
  }
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
    await loadArchive();
    await loadMusiccmt();
  };
  updatePending(st.bgm);
  await loadArchive();
  await loadBgm();
  await loadMusiccmt();
}

/* ---------------- 设置游戏目录 / 启动游戏 ---------------- */
function openSettings(currentDir, isFirst) {
  const modal = $("#settings-modal");
  $("#settings-path").value = currentDir || "";
  modal.dataset.first = isFirst ? "1" : "";
  modal.classList.remove("hidden");
  loadCandidates();
}

function closeSettings() {
  if ($("#settings-modal").dataset.first === "1") return;   // 首次必须设置
  $("#settings-modal").classList.add("hidden");
}

async function loadCandidates() {
  try {
    const cfg = await api("/api/config");
    const box = $("#settings-candidates");
    box.innerHTML = "";
    (cfg.candidates || []).forEach((p) => {
      const div = document.createElement("div");
      div.className = "cand";
      div.textContent = p;
      div.onclick = () => { $("#settings-path").value = p; };
      box.appendChild(div);
    });
  } catch (e) { /* 忽略 */ }
}

$("#btn-settings").onclick = () => openSettings($("#game-dir").textContent);
$("#settings-cancel").onclick = closeSettings;
$("#settings-modal").onclick = closeSettings;

$("#settings-browse").onclick = async () => {
  toast("请在弹窗中选择游戏文件夹…");
  try {
    const res = await api("/api/pick-dir", { method: "POST" });
    if (res.path) $("#settings-path").value = res.path;
  } catch (ex) {
    toast("打开选择框失败: " + ex.message, true);
  }
};

$("#settings-apply").onclick = async () => {
  const path = $("#settings-path").value.trim();
  if (!path) { toast("请填写游戏目录", true); return; }
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ game_dir: path }),
    });
    toast("游戏目录已切换");
    $("#settings-modal").dataset.first = "";
    $("#settings-modal").classList.add("hidden");
    S.anmName = null; S.msgName = null; S.textures = [];
    $("#texture-grid").innerHTML = "";
    $("#msg-body").innerHTML = "";
    $("#anm-list").innerHTML = "";
    $("#msg-list").innerHTML = "";
    await boot();
  } catch (ex) {
    toast("设置失败: " + ex.message, true);
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
  if (!bgminfo) return;
  S.pendingCount = bgminfo.pending_count || 0;
  const badge = $("#pending-badge");
  if (S.pendingCount > 0) {
    badge.textContent = `音乐待应用 ${S.pendingCount} 项`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
  $("#bgm-apply").disabled = S.pendingCount === 0;
  $("#bgm-cancel").disabled = S.pendingCount === 0;
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
  const filter = $("#anm-filter").value.trim().toLowerCase();
  const ul = $("#anm-list");
  ul.innerHTML = "";
  S.entries.filter((e) => e.name.endsWith(".anm") &&
      (!filter || e.name.toLowerCase().includes(filter)))
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

$("#anm-filter").oninput = renderAnmList;

/* ---------------- 贴图 ---------------- */
async function openAnm(name, li) {
  S.anmName = name;
  $$("#anm-list li").forEach((el) => el.classList.remove("active"));
  if (li) li.classList.add("active");
  $("#texture-title").textContent = name;
  const grid = $("#texture-grid");
  grid.innerHTML = '<p class="muted" style="padding:8px">正在解析…</p>';
  try {
    const data = await api(
      `/api/textures?game=${S.game}&anm=${encodeURIComponent(name)}`);
    S.textures = data.textures;
    $("#texture-count").textContent =
      `共 ${data.textures.length} 张贴图`;
    renderTextures();
  } catch (ex) {
    grid.innerHTML = "";
    toast("解析失败: " + ex.message, true);
  }
}

function renderTextures() {
  const grid = $("#texture-grid");
  grid.innerHTML = "";
  S.textures.forEach((t) => {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.index = t.index;
    const url = `/api/texture.png?game=${S.game}` +
      `&anm=${encodeURIComponent(S.anmName)}&index=${t.index}`;
    card.innerHTML = `
      <div class="thumb"><img loading="lazy" src="${url}" alt=""></div>
      <div class="name" title="${t.name}">${t.name || "(无名)"}</div>
      <div class="meta">#${t.index} · ${t.width}×${t.height} ·
        ${t.format_name} · ${fmtSize(t.size)}</div>
      <div class="btns">
        <button class="mini" data-act="replace">替换</button>
        <button class="mini" data-act="download">导出 PNG</button>
      </div>`;
    card.querySelector(".thumb").onclick = () => {
      $("#lightbox-img").src = url;
      $("#lightbox").classList.remove("hidden");
    };
    card.querySelector('[data-act="download"]').onclick = () => {
      const a = document.createElement("a");
      a.href = url;
      a.download = `${S.anmName}_${t.index}.png`;
      a.click();
    };
    card.querySelector('[data-act="replace"]').onclick =
      () => replaceTexture(t);
    grid.appendChild(card);
  });
}

async function replaceTexture(t) {
  const file = await pickFile("image/png,image/*");
  if (!file) return;
  try {
    const buf = await file.arrayBuffer();
    const res = await api(
      `/api/texture?game=${S.game}&anm=${encodeURIComponent(S.anmName)}` +
      `&index=${t.index}`,
      { method: "POST", body: buf });
    toast(`已替换 ${t.name} (${res.width}×${res.height})`);
    // 刷新缩略图（加时间戳避免缓存）
    const card = document.querySelector(`.card[data-index="${t.index}"]`);
    if (card) {
      const img = card.querySelector("img");
      img.src = `/api/texture.png?game=${S.game}` +
        `&anm=${encodeURIComponent(S.anmName)}&index=${t.index}&t=${Date.now()}`;
    }
    // 尺寸变了就整体刷新
    if (res.width !== t.width || res.height !== t.height) openAnm(S.anmName);
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
  } catch (ex) {
    toast("设置失败: " + ex.message, true);
  }
}

$("#bgm-apply").onclick = async () => {
  if (!confirm("确定要把修改写入游戏吗？\n\n会先自动备份原文件，"
    + "然后重建 thbgm.dat（约 400MB，需要一点时间）。")) return;
  try {
    const { job } = await api("/api/bgm.apply", { method: "POST" });
    const bar = $("#bgm-progress");
    bar.classList.remove("hidden");
    const inner = bar.firstElementChild;
    const poll = setInterval(async () => {
      const j = await api("/api/job?id=" + job);
      inner.style.width = Math.round((j.progress || 0) * 100) + "%";
      if (j.state === "done") {
        clearInterval(poll);
        bar.classList.add("hidden");
        toast("音乐修改已写入游戏！");
        await loadBgm();
        await loadBackups();
      } else if (j.state === "error") {
        clearInterval(poll);
        bar.classList.add("hidden");
        toast("写入失败: " + j.message, true);
      }
    }, 500);
  } catch (ex) {
    toast("失败: " + ex.message, true);
  }
};

$("#bgm-cancel").onclick = async () => {
  await api("/api/bgm.cancel", { method: "POST" });
  toast("已放弃暂存的音乐修改");
  await loadBgm();
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
    const text = await file.text();
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
    toast(res.changed ? `已保存 ${res.changed} 处修改` : "没有修改");
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
    toast("已替换 " + e.name);
    await loadArchive();
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
    toast("音乐室评论已保存");
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
  await Promise.all(Array.from({ length: workers }, worker));
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
  try {
    const res = await api("/api/batch.apply", { method: "POST" });
    let msg = `导入完成：贴图 ${res.texture} · 对话 ${res.dialogue} · ` +
      `原样 ${res.raw} · 评论 ${res.musiccmt}`;
    if (res.changed_lines) msg += `（对话共改 ${res.changed_lines} 句）`;
    toast(msg);
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

/* ---------------- 备份 ---------------- */
async function loadBackups() {
  try {
    const data = await api("/api/backups");
    const tbody = $("#backup-table tbody");
    tbody.innerHTML = "";
    if (!data.backups.length) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="muted">还没有备份（第一次修改时会自动创建）</td></tr>';
      return;
    }
    data.backups.forEach((b) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${b.name}</td>
        <td>${b.size_text}</td>
        <td>${b.time_text}</td>
        <td><button class="mini" data-act="restore">还原</button></td>`;
      tr.querySelector('[data-act="restore"]').onclick = async () => {
        if (!confirm(`确定用备份还原「${b.name}」吗？当前修改会丢失。`)) return;
        try {
          await api(`/api/restore?key=${b.key}`, { method: "POST" });
          toast("已还原 " + b.name);
          S.anmName = null; S.msgName = null;
          await loadArchive();
          await loadBgm();
          await loadMusiccmt();
        } catch (ex) {
          toast("还原失败: " + ex.message, true);
        }
      };
      tbody.appendChild(tr);
    });
  } catch (ex) {
    toast("读取备份失败: " + ex.message, true);
  }
}
$("#backup-refresh").onclick = loadBackups;

boot().catch((ex) => toast("初始化失败: " + ex.message, true));
