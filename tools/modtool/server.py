# -*- coding: utf-8 -*-
"""东方星莲船 (TH12) 魔改工具 —— 本地 Web 后端。

用法::

    python tools/modtool/server.py [--port 8765] [--no-browser]

启动后浏览器访问 http://127.0.0.1:8765/ 即可。
所有写操作都会自动备份原文件（*.modtool.bak）。
"""

import argparse
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", ".."))
if WS not in sys.path:
    sys.path.insert(0, WS)

from thtk import anm, archive, bgm, crypto, msg  # noqa: E402

# ----------------------------------------------------------------------
# 路径：源码运行 / PyInstaller 打包后运行 两种情况
# ----------------------------------------------------------------------
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, "_MEIPASS", EXE_DIR)
    # 配置/日志/暂存放到用户目录，避免装在 Program Files 下没有写权限
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or EXE_DIR, "TH12ModTool")
else:
    EXE_DIR = WS
    BUNDLE_DIR = HERE
    DATA_DIR = HERE

WEB_DIR = os.path.join(BUNDLE_DIR, "web")
STAGING_DIR = os.path.join(DATA_DIR, "staging")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "modtool.log")
BACKUP_META = os.path.join(DATA_DIR, "backups.json")
BACKUP_SUFFIX = ".modtool.bak"


# ----------------------------------------------------------------------
# 操作日志
# ----------------------------------------------------------------------
class Logger(object):
    """记录所有写操作，内存保留最近若干条，同时追加到 JSONL 文件。"""

    def __init__(self, path, memory_limit=800):
        self.path = path
        self.memory_limit = memory_limit
        self.lock = threading.Lock()
        self.entries = []
        self.seq = 0
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with io.open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-self.memory_limit:]
            for line in lines:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                self.entries.append(item)
                self.seq = max(self.seq, int(item.get("id", 0)))
        except OSError:
            pass

    def log(self, action, target="", detail="", level="info"):
        with self.lock:
            self.seq += 1
            item = {
                "id": self.seq,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "level": level,
                "action": action,
                "target": target,
                "detail": detail,
            }
            self.entries.append(item)
            if len(self.entries) > self.memory_limit:
                self.entries = self.entries[-self.memory_limit:]
            try:
                with io.open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            except OSError:
                pass
            line = "[%s] %s %s %s" % (item["time"], action,
                                      target, detail)
            sys.stderr.write("[modtool] " + line + "\n")
            return item

    def list(self, limit=200, level=None):
        with self.lock:
            items = self.entries
            if level:
                items = [i for i in items if i.get("level") == level]
            return list(reversed(items[-limit:]))

    def clear(self):
        with self.lock:
            self.entries = []
            try:
                if os.path.isfile(self.path):
                    os.remove(self.path)
            except OSError:
                pass


LOG = Logger(LOG_PATH)


def log(action, target="", detail="", level="info"):
    return LOG.log(action, target, detail, level)

GAME_DIR = os.path.join(EXE_DIR, "game",
                        "[th12] 东方星莲船 (汉化版+日文版)")

GAMES = {
    "jp": {
        "label": "日文版 (th12.exe)",
        "dat": "th12.dat",
        "encoding": "cp932",
    },
    "cn": {
        "label": "汉化版 (th12c.exe)",
        "dat": "th12c.dat",
        "encoding": "gbk",
    },
}

BGM_DAT = os.path.join(GAME_DIR, "thbgm.dat")


def set_game_dir(path):
    """切换游戏目录（便于测试/多份游戏）。"""
    global GAME_DIR, BGM_DAT
    GAME_DIR = os.path.abspath(path)
    BGM_DAT = os.path.join(GAME_DIR, "thbgm.dat")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def check_game_dir(path):
    """返回该目录里可用的版本列表；空列表表示不是 TH12 游戏目录。"""
    found = []
    for key, info in GAMES.items():
        if os.path.isfile(os.path.join(path, info["dat"])):
            found.append(key)
    return found


def find_candidates():
    """在常见位置找有 th12.dat / th12c.dat 的目录。"""
    roots = [
        os.path.join(EXE_DIR, "game"),
        EXE_DIR,
        os.path.dirname(EXE_DIR),
        os.path.dirname(os.path.dirname(EXE_DIR)),
        os.getcwd(),
    ]
    out = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if not os.path.isdir(path) or path in seen:
                continue
            if check_game_dir(path):
                seen.add(path)
                out.append(path)
    return out


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def ensure_backup(path, comment=""):
    """首次写入前备份原文件（只备份一次，保留最原始版本）。"""
    bak = path + BACKUP_SUFFIX
    if os.path.exists(path) and not os.path.exists(bak):
        _atomic_copy(path, bak)
        _record_backup(bak, comment)
        log("创建备份", os.path.basename(bak),
            human_size(os.path.getsize(bak)))
    elif comment and os.path.exists(bak):
        _record_backup(bak, comment)
    return bak


def _record_backup(bak, comment=""):
    """把备份的生成时间和备注记到 backups.json。"""
    try:
        meta = {}
        if os.path.isfile(BACKUP_META):
            with io.open(BACKUP_META, "r", encoding="utf-8") as f:
                meta = json.load(f)
        item = meta.setdefault(os.path.basename(bak), {})
        item.setdefault("created", time.time())
        if comment:
            item["comment"] = comment
        item["updated"] = time.time()
        with io.open(BACKUP_META, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except (OSError, ValueError):
        pass


def _backup_meta():
    try:
        if os.path.isfile(BACKUP_META):
            with io.open(BACKUP_META, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return {}


def _atomic_copy(src, dst):
    """原子复制：先写临时文件再替换。

    这样绝不会“就地写入”目标文件——即使目标是硬链接，
    也不会影响到链接指向的另一个文件（避免误改真实游戏文件）。
    """
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    return dst


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def guess_kind(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "anm":
        return "texture"
    if ext == "msg":
        return "dialogue"
    if ext == "ecl":
        return "script"
    if ext == "wav":
        return "sound"
    if ext == "sht":
        return "shot"
    if ext == "std":
        return "stage"
    if ext == "rpy":
        return "replay"
    return "other"


class ApiError(Exception):
    def __init__(self, message, code=400):
        Exception.__init__(self, message)
        self.code = code


# ----------------------------------------------------------------------
# 全局状态
# ----------------------------------------------------------------------
class State(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.archives = {}
        self.anm_cache = {}
        self.texture_index = {}
        self.pending = []
        self.pending_seq = 0
        self.fmt = None
        self.bgm_pending = {}     # index -> 暂存的 PCM 文件路径
        self.bgm_loop = {}        # index -> 新的循环点（字节）
        self.bgm_replaced = set()
        self.jobs = {}
        self.job_seq = 0
        self.game_key = "jp"
        self.batch = []
        self.batch_seq = 0
        if not os.path.isdir(STAGING_DIR):
            os.makedirs(STAGING_DIR)

    # -- 归档 --------------------------------------------------------
    def dat_path(self, key):
        return os.path.join(GAME_DIR, GAMES[key]["dat"])

    def available_games(self):
        """只返回实际存在 .dat 的版本（兼容只有单版本的游戏目录）。"""
        return [k for k in GAMES if os.path.isfile(self.dat_path(k))]

    def archive(self, key):
        if key not in GAMES:
            raise ApiError("未知游戏版本: %s" % key)
        with self.lock:
            if key not in self.archives:
                path = self.dat_path(key)
                if not os.path.isfile(path):
                    raise ApiError("找不到 %s（该游戏目录没有此版本）" % path, 404)
                self.archives[key] = archive.Archive.from_file(path)
            return self.archives[key]

    def archive_path(self, key):
        return self.dat_path(key)

    def anm(self, key, name):
        """带缓存的 ANM 解析。"""
        ck = (key, name)
        with self.lock:
            if ck not in self.anm_cache:
                raw = self.archive(key).read_by_name(name)
                self.anm_cache[ck] = anm.AnmFile.from_bytes(raw)
            return self.anm_cache[ck]

    def invalidate(self):
        with self.lock:
            self.anm_cache = {}
            self.texture_index = {}
            self.archives = {}
            self.fmt = None

    # -- BGM ---------------------------------------------------------
    def bgm_fmt(self):
        if self.fmt is None:
            games = self.available_games()
            if not games:
                raise ApiError("游戏目录里没有 th12.dat / th12c.dat")
            data = self.archive(games[0]).read_by_name("thbgm.fmt")
            self.fmt = bgm.BgmFmt.from_bytes(data)
        return self.fmt

    # -- 任务 --------------------------------------------------------
    def new_job(self):
        with self.lock:
            self.job_seq += 1
            job = {"id": self.job_seq, "state": "running",
                   "progress": 0.0, "message": "准备中…"}
            self.jobs[self.job_seq] = job
            return job


STATE = State()


# ----------------------------------------------------------------------
# 各功能实现
# ----------------------------------------------------------------------
def list_archive(key):
    a = STATE.archive(key)
    out = []
    for e in a.entries:
        out.append({
            "name": e.name,
            "size": e.size,
            "stored": e.zsize,
            "kind": guess_kind(e.name),
        })
    return {"entries": out, "info": a.info(),
            "label": GAMES[key]["label"]}


def save_archive_entry(key, name, data, action="写入归档文件", detail=""):
    """【暂存】修改内容，等用户点“保存”时才写入游戏。"""
    with STATE.lock:
        a = STATE.archive(key)
        if a.index_of(name) < 0:
            raise ApiError("归档中没有条目: %s" % name, 404)
        STATE.pending_seq += 1
        seq = STATE.pending_seq
        staged = os.path.join(STAGING_DIR, "pending_%04d.bin" % seq)
        with open(staged, "wb") as f:
            f.write(data)
        # 同一条目只保留最后一次修改
        STATE.pending = [x for x in STATE.pending
                         if not (x["game"] == key and x["name"] == name)]
        STATE.pending.append({
            "id": seq, "game": key, "name": name,
            "action": action, "detail": detail or human_size(len(data)),
            "size": len(data), "staged": staged,
            "time": time.strftime("%H:%M:%S"),
        })
    return {"ok": True, "name": name, "size": len(data), "staged": True}


def list_pending():
    """列出待保存的修改（归档 + BGM）。"""
    with STATE.lock:
        items = [dict(x) for x in STATE.pending]
        bgm_replace = sorted(STATE.bgm_replaced)
        bgm_loop = dict(STATE.bgm_loop)
    fmt = None
    out = []
    for it in items:
        out.append({
            "kind": "file", "game": it["game"], "name": it["name"],
            "action": it["action"], "detail": it["detail"],
            "size_text": human_size(it["size"]), "time": it["time"],
        })
    if bgm_replace or bgm_loop:
        try:
            fmt = STATE.bgm_fmt()
        except Exception:
            fmt = None
    for idx in bgm_replace:
        name = fmt.tracks[idx].name if fmt else ("曲目 %d" % idx)
        out.append({"kind": "bgm", "game": "bgm", "name": name,
                    "action": "替换BGM", "detail": "整个曲目已替换",
                    "size_text": "", "time": ""})
    for idx, loop in bgm_loop.items():
        if idx in bgm_replace:
            continue
        name = fmt.tracks[idx].name if fmt else ("曲目 %d" % idx)
        out.append({"kind": "bgm", "game": "bgm", "name": name,
                    "action": "循环点", "detail": "%.2f 秒"
                    % (loop / float(bgm.BYTES_PER_SEC)),
                    "size_text": "", "time": ""})
    return {"items": out, "count": len(out)}


def clear_pending():
    with STATE.lock:
        for it in STATE.pending:
            try:
                os.remove(it["staged"])
            except OSError:
                pass
        STATE.pending = []
        for path in STATE.bgm_pending.values():
            try:
                os.remove(path)
            except OSError:
                pass
        STATE.bgm_pending = {}
        STATE.bgm_loop = {}
        STATE.bgm_replaced = set()
    return {"ok": True}


def save_all(comment=""):
    """把暂存的所有修改写入游戏（同时生成备份，并把备注记进备份元数据）。"""
    comment = (comment or "").strip()
    result = {"ok": True, "files": 0, "bgm": False, "errors": [],
              "comment": comment}
    with STATE.lock:
        pending = [dict(x) for x in STATE.pending]
        bgm_pending = dict(STATE.bgm_pending)
        bgm_loop = dict(STATE.bgm_loop)
    if not pending and not bgm_pending and not bgm_loop:
        raise ApiError("没有待保存的修改")

    # ---- 1) BGM：先重建 thbgm.dat，并把新 fmt 混入归档修改 ----
    fmt_bytes = None
    if bgm_pending or bgm_loop:
        fmt = STATE.bgm_fmt()
        for idx, loop in bgm_loop.items():
            fmt.tracks[idx].loop = loop
        new_dat = BGM_DAT + ".new"
        try:
            bgm.rebuild_bgm_dat(BGM_DAT, new_dat, bgm_pending, fmt)
            ensure_backup(BGM_DAT, comment)
            os.replace(new_dat, BGM_DAT)
            fmt_bytes = fmt.to_bytes()
            result["bgm"] = True
            log("保存BGM修改", "%d 首替换 / %d 首循环点"
                % (len(bgm_pending), len(bgm_loop)),
                comment or human_size(os.path.getsize(BGM_DAT)))
        except Exception as ex:
            result["errors"].append("BGM: %s" % ex)
            raise

    # ---- 2) 归档：按版本分组，一次写入 ----
    by_game = {}
    for it in pending:
        by_game.setdefault(it["game"], []).append(it)
    if fmt_bytes:
        for key in STATE.available_games():
            by_game.setdefault(key, [])
    for key, items in by_game.items():
        a = STATE.archive(key)
        replacements = {}
        for it in items:
            try:
                with open(it["staged"], "rb") as f:
                    replacements[it["name"]] = f.read()
            except OSError as ex:
                result["errors"].append("%s: %s" % (it["name"], ex))
        if fmt_bytes:
            replacements["thbgm.fmt"] = fmt_bytes
        if not replacements:
            continue
        # 写盘前自检：ANM 结构必须合法
        bad = []
        for name, blob in list(replacements.items()):
            if name.lower().endswith(".anm"):
                problems = anm.AnmFile.from_bytes(blob).validate()
                if problems:
                    bad.append("%s: %s" % (name, "；".join(problems[:2])))
                    del replacements[name]
        result["errors"].extend(bad)
        if not replacements:
            continue
        path = STATE.archive_path(key)
        ensure_backup(path, comment)
        idx_map = {}
        for name, blob in replacements.items():
            i = a.index_of(name)
            if i >= 0:
                idx_map[i] = blob
        try:
            a.save_patched(path, idx_map)
        except PermissionError:
            raise ApiError("文件被占用：请先关闭游戏（th12.exe / th12c.exe）")
        STATE.anm_cache = {}
        STATE.texture_index = {}
        result["files"] += len(replacements)
        for it in items:
            log(it["action"], "%s / %s" % (GAMES[key]["label"], it["name"]),
                (it["detail"] + ("　备注: " + comment if comment else "")))

    # ---- 3) 清空暂存 ----
    clear_pending()
    return result


def list_textures(key, anm_name):
    f = STATE.anm(key, anm_name)
    out = []
    for t in f.textures:
        out.append({
            "index": t.entry_index,
            "name": t.name,
            "width": t.width,
            "height": t.height,
            "format": t.format,
            "format_name": t.format_name,
            "size": t.size,
            "x": t.x,
            "y": t.y,
        })
    return {"anm": anm_name, "textures": out}


def texture_png(key, anm_name, index):
    f = STATE.anm(key, anm_name)
    t = _find_texture(f, index)
    rgba = f.rgba(t)
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, "PNG")
    return buf.getvalue()


def _find_texture(f, index):
    for t in f.textures:
        if t.entry_index == index:
            return t
    raise ApiError("贴图序号不存在: %s" % index, 404)


def replace_texture(key, anm_name, index, png_data):
    from PIL import Image
    import numpy as np
    f = STATE.anm(key, anm_name)
    t = _find_texture(f, index)
    try:
        img = Image.open(io.BytesIO(png_data)).convert("RGBA")
    except Exception as ex:
        raise ApiError("无法读取 PNG: %s" % ex)
    rgba = np.array(img)
    encoded = anm.encode_rgba(t.format, rgba)
    new_texture = f.replace_texture(t, encoded, img.width, img.height)
    problems = f.validate()
    if problems:
        raise ApiError("贴图重建后结构异常，已放弃写入: %s"
                       % "；".join(problems[:3]))
    save_archive_entry(
        key, anm_name, f.to_bytes(), action="替换贴图",
        detail="%s #%d %dx%d %s" % (t.name, t.entry_index,
                                    img.width, img.height,
                                    new_texture.format_name))
    return {"ok": True, "width": img.width, "height": img.height,
            "format": new_texture.format_name}


def list_bgm():
    fmt = STATE.bgm_fmt()
    tracks = []
    for t in fmt.tracks:
        tracks.append({
            "index": t.index,
            "name": t.name,
            "duration": t.duration,
            "loop": t.loop,
            "loop_seconds": t.loop_seconds,
            "size": t.end,
            "pending": t.index in STATE.bgm_replaced,
            "pending_loop": STATE.bgm_loop.get(t.index),
        })
    return {"tracks": tracks,
            "pending_count": len(STATE.bgm_replaced),
            "dat_size": os.path.getsize(BGM_DAT) if os.path.exists(BGM_DAT) else 0}


def bgm_wav(index):
    fmt = STATE.bgm_fmt()
    if index < 0 or index >= len(fmt.tracks):
        raise ApiError("曲目序号不存在", 404)
    t = fmt.tracks[index]
    path = STATE.bgm_pending.get(index)
    if path:
        with open(path, "rb") as f:
            pcm = f.read()
    else:
        pcm = bgm.read_track_pcm(BGM_DAT, t)
    return bgm.to_wav(pcm)


def bgm_replace(index, wav_data):
    fmt = STATE.bgm_fmt()
    if index < 0 or index >= len(fmt.tracks):
        raise ApiError("曲目序号不存在", 404)
    try:
        pcm = bgm.convert_to_bgm_pcm(wav_data)
    except bgm.BgmError as ex:
        raise ApiError(str(ex))
    path = os.path.join(STAGING_DIR, "track_%02d.pcm" % index)
    with open(path, "wb") as f:
        f.write(pcm)
    with STATE.lock:
        STATE.bgm_pending[index] = path
        STATE.bgm_replaced.add(index)
    log("替换BGM（暂存）", fmt.tracks[index].name,
        "%.1f 秒 / %s" % (len(pcm) / float(bgm.BYTES_PER_SEC),
                          human_size(len(pcm))))
    return {"ok": True, "seconds": len(pcm) / float(bgm.BYTES_PER_SEC)}


def bgm_set_loop(index, loop_bytes):
    fmt = STATE.bgm_fmt()
    if index < 0 or index >= len(fmt.tracks):
        raise ApiError("曲目序号不存在", 404)
    loop_bytes = max(0, int(loop_bytes))
    loop_bytes -= loop_bytes % 4
    with STATE.lock:
        STATE.bgm_loop[index] = loop_bytes
    log("设置BGM循环点", fmt.tracks[index].name,
        "%.2f 秒" % (loop_bytes / float(bgm.BYTES_PER_SEC)))
    return {"ok": True, "loop": loop_bytes}


def bgm_cancel():
    with STATE.lock:
        n = len(STATE.bgm_pending)
        for path in STATE.bgm_pending.values():
            try:
                os.remove(path)
            except OSError:
                pass
        STATE.bgm_pending = {}
        STATE.bgm_loop = {}
        STATE.bgm_replaced = set()
    if n:
        log("放弃BGM修改", "%d 项" % n)
    return {"ok": True}


def _apply_bgm(job):
    fmt = STATE.bgm_fmt()
    replaced = len(STATE.bgm_pending)
    loops = len(STATE.bgm_loop)
    # 1) 应用循环点
    for idx, loop in STATE.bgm_loop.items():
        fmt.tracks[idx].loop = loop
    # 2) 重建 thbgm.dat 到临时文件
    new_dat = BGM_DAT + ".new"
    job["message"] = "正在重建 thbgm.dat…"

    def progress(done, total):
        job["progress"] = done / float(total)

    bgm.rebuild_bgm_dat(BGM_DAT, new_dat, STATE.bgm_pending, fmt,
                        progress=progress)
    # 3) 更新两个归档里的 thbgm.fmt（先写临时文件）
    fmt_bytes = fmt.to_bytes()
    job["message"] = "正在更新 thbgm.fmt…"
    job["progress"] = 1.0
    new_archives = {}
    for key in STATE.available_games():
        a = STATE.archive(key)
        idx = a.index_of("thbgm.fmt")
        blob = a.to_bytes_patched({idx: fmt_bytes})
        path = STATE.archive_path(key) + ".new"
        with open(path, "wb") as f:
            f.write(blob)
        new_archives[key] = path
    # 4) 备份 + 原子替换
    job["message"] = "正在备份原文件…"
    ensure_backup(BGM_DAT)
    for key in STATE.available_games():
        ensure_backup(STATE.archive_path(key))
    job["message"] = "正在写入…"
    os.replace(new_dat, BGM_DAT)
    for key, path in new_archives.items():
        os.replace(path, STATE.archive_path(key))
    # 5) 重新加载内存状态
    with STATE.lock:
        for path in STATE.bgm_pending.values():
            try:
                os.remove(path)
            except OSError:
                pass
        STATE.bgm_pending = {}
        STATE.bgm_loop = {}
        STATE.bgm_replaced = set()
        STATE.invalidate()
    job["message"] = "完成"
    log("应用BGM修改", "%d 首替换 / %d 首循环点" % (replaced, loops),
        human_size(os.path.getsize(BGM_DAT)))


def start_bgm_apply(comment=""):
    """兼容旧接口：BGM 的“应用”现在走统一保存。"""
    return save_all(comment)


def get_msg(key, name):
    a = STATE.archive(key)
    raw = a.read_by_name(name)
    encoding = GAMES[key]["encoding"]
    f = msg.MsgFile.from_bytes(raw, encoding)
    info = msg.describe_file(name)
    labels = msg.speaker_labels(name)
    entries = []
    for i, entry in enumerate(f.entries):
        instrs = []
        speaker = None
        for j, ins in enumerate(entry.instructions):
            if ins.type == msg.SPEAKER_PLAYER:
                speaker = "player"
            elif ins.type == msg.SPEAKER_BOSS:
                speaker = "boss"
            elif ins.type == msg.SPEAKER_NONE:
                speaker = "none"
            item = {
                "index": j,
                "time": ins.time,
                "type": ins.type,
                "length": len(ins.data),
                "name": msg.INSTR_NAMES.get(ins.type, "ins_%d" % ins.type),
            }
            if ins.is_text:
                item["text"] = ins.text(encoding)
                item["speaker"] = speaker
                item["speaker_label"] = labels.get(speaker, "")
            instrs.append(item)
        entries.append({"index": i, "extra": entry.extra,
                        "instructions": instrs})
    return {"name": name, "encoding": encoding, "info": info,
            "player": info.get("player"), "boss": info.get("boss"),
            "scene": info.get("scene"), "labels": labels,
            "entries": entries}


def msg_document(key, name):
    """导出对话文档（UTF-8 文本）。"""
    a = STATE.archive(key)
    raw = a.read_by_name(name)
    encoding = GAMES[key]["encoding"]
    f = msg.MsgFile.from_bytes(raw, encoding)
    return msg.export_document(f, name, encoding)


def msg_import_document(key, name, text, dry_run=False):
    """导入编辑过的对话文档并写回归档（dry_run 时只预览差异）。"""
    a = STATE.archive(key)
    raw = a.read_by_name(name)
    encoding = GAMES[key]["encoding"]
    f = msg.MsgFile.from_bytes(raw, encoding)
    changed, unmatched, bad_chars, changes = msg.import_document(
        text, f, encoding, dry_run=dry_run)
    if changed and not dry_run:
        save_archive_entry(key, name, f.to_bytes(), action="导入对话文档",
                           detail="修改 %d 句" % changed)
    return {"ok": True, "changed": changed,
            "unmatched": unmatched[:20],
            "unmatched_count": len(unmatched),
            "bad_chars": bad_chars[:20],
            "dry_run": bool(dry_run),
            "changes": [
                {"entry": e, "instr": i, "old": o, "new": n}
                for e, i, o, n in changes[:200]
            ]}


def save_msg(key, name, payload):
    """payload: {"entries": [{"index": i, "texts": {instr_index: text}}]}"""
    a = STATE.archive(key)
    raw = a.read_by_name(name)
    encoding = GAMES[key]["encoding"]
    f = msg.MsgFile.from_bytes(raw, encoding)
    edits = payload.get("entries", [])
    changed = 0
    bad_chars = []
    for item in edits:
        ei = int(item["index"])
        if ei < 0 or ei >= len(f.entries):
            continue
        entry = f.entries[ei]
        for ji, text in item.get("texts", {}).items():
            ji = int(ji)
            if 0 <= ji < len(entry.instructions):
                ins = entry.instructions[ji]
                if ins.is_text and ins.text(encoding) != text:
                    for ch in msg.check_encoding(text, encoding):
                        if ch not in bad_chars:
                            bad_chars.append(ch)
                    ins.set_text(text, encoding)
                    changed += 1
    if changed:
        save_archive_entry(key, name, f.to_bytes(), action="保存对话",
                           detail="修改 %d 句" % changed)
    return {"ok": True, "changed": changed, "bad_chars": bad_chars[:20]}


def get_musiccmt(key):
    a = STATE.archive(key)
    raw = a.read_by_name("musiccmt.txt")
    encoding = GAMES[key]["encoding"]
    return {"text": raw.decode(encoding, "replace"), "encoding": encoding}


# ----------------------------------------------------------------------
# 游戏目录 / 启动游戏
# ----------------------------------------------------------------------
def get_config():
    games = STATE.available_games()
    exes = {}
    for key in games:
        exe = os.path.join(GAME_DIR, "th12.exe" if key == "jp"
                           else "th12c.exe")
        exes[key] = os.path.basename(exe) if os.path.isfile(exe) else None
    return {
        "game_dir": GAME_DIR,
        "games": [{"key": k, "label": GAMES[k]["label"]} for k in games],
        "exes": exes,
        "candidates": find_candidates(),
    }


def set_config(payload):
    path = (payload.get("game_dir") or "").strip().strip('"')
    if not path:
        raise ApiError("请填写游戏目录")
    if not os.path.isdir(path):
        raise ApiError("目录不存在: %s" % path)
    found = check_game_dir(path)
    if not found:
        raise ApiError("这个目录里没有 th12.dat 或 th12c.dat，"
                       "不是 TH12 游戏目录")
    with STATE.lock:
        set_game_dir(path)
        STATE.invalidate()
        STATE.game_key = found[0]
    cfg = load_config()
    cfg["game_dir"] = GAME_DIR
    save_config(cfg)
    log("切换游戏目录", GAME_DIR, "版本: %s" % "/".join(found))
    return get_config()


def pick_directory():
    """弹出一个 Windows 原生“选择文件夹”对话框（用 PowerShell，
    源码运行和打包运行都能用）。"""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms\r\n"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog\r\n"
        "$d.Description = '选择 TH12 游戏目录（里面有 th12.dat）'\r\n"
        "$d.ShowNewFolderButton = $false\r\n"
        "if ($d.ShowDialog() -eq "
        "[System.Windows.Forms.DialogResult]::OK) "
        "{ [Console]::Out.Write($d.SelectedPath) }\r\n"
    )
    tmp = os.path.join(STAGING_DIR, "pickdir.ps1")
    try:
        if not os.path.isdir(STAGING_DIR):
            os.makedirs(STAGING_DIR)
        with io.open(tmp, "w", encoding="utf-8-sig") as f:
            f.write(script)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
             "-File", tmp],
            capture_output=True, text=True, timeout=900)
    except Exception as ex:
        raise ApiError("无法打开文件夹选择框: %s" % ex)
    return {"path": (out.stdout or "").strip()}


def launch_game(key=None):
    games = STATE.available_games()
    if not games:
        raise ApiError("游戏目录里没有 th12.dat / th12c.dat")
    if not key or key not in games:
        key = STATE.game_key if STATE.game_key in games else games[0]
    exe_name = "th12.exe" if key == "jp" else "th12c.exe"
    exe = os.path.join(GAME_DIR, exe_name)
    if not os.path.isfile(exe):
        raise ApiError("找不到 %s（该目录里没有启动程序）" % exe_name)
    try:
        subprocess.Popen([exe], cwd=GAME_DIR, close_fds=True)
    except Exception as ex:
        raise ApiError("启动失败: %s" % ex)
    with STATE.lock:
        STATE.game_key = key
    cfg = load_config()
    cfg["last_game"] = key
    save_config(cfg)
    log("启动游戏", exe_name, GAME_DIR)
    return {"ok": True, "exe": exe_name}


def save_musiccmt(key, text):
    encoding = GAMES[key]["encoding"]
    data = text.encode(encoding, "replace")
    save_archive_entry(key, "musiccmt.txt", data, action="保存音乐室评论",
                       detail="%d 字符" % len(text))
    return {"ok": True, "size": len(data)}


# ----------------------------------------------------------------------
# 批量导入（按文件名自动识别）
# ----------------------------------------------------------------------
#: 贴图文件名格式： ``{anm}_{index}.png`` 或 ``{贴图名}@{anm}@{index}.png``
BATCH_TEXTURE_RE = re.compile(
    r"^(?P<anm>[^@/\\]+?)(?:\.anm)?_(?P<idx>\d+)\.png$", re.IGNORECASE)
BATCH_TEXTURE_ALT_RE = re.compile(
    r"^(?P<name>.*)@(?P<anm>[^@]+)@(?P<idx>\d+)\.png$", re.IGNORECASE)


def _png_size(data):
    """从 PNG 头部读尺寸（不依赖 PIL）。"""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack_from(">II", data, 16)


def _index_cache_path(key):
    """贴图索引缓存文件路径（用 .dat 的大小+时间戳做版本号）。"""
    dat = STATE.archive_path(key)
    st = os.stat(dat)
    return os.path.join(STAGING_DIR, "texindex_v2_%s_%d_%d.json"
                        % (key, st.st_size, int(st.st_mtime)))


def _texture_index(key):
    """构建 贴图名(小写) -> [匹配项] 的索引（内存 + 磁盘缓存）。

    匹配项形如 ``{"anm": ..., "index": ..., "w":..., "h":..., "x":..., "y":...}``
    """
    with STATE.lock:
        cached = STATE.texture_index.get(key)
    if cached is not None:
        return cached
    # 磁盘缓存（游戏文件没变就直接用）
    cache_path = None
    try:
        cache_path = _index_cache_path(key)
        if os.path.isfile(cache_path):
            with io.open(cache_path, "r", encoding="utf-8") as f:
                index = json.load(f)
            with STATE.lock:
                STATE.texture_index[key] = index
            return index
    except Exception:
        cache_path = None

    a = STATE.archive(key)
    index = {}
    for e in a.entries:
        if not e.name.lower().endswith(".anm"):
            continue
        try:
            f = STATE.anm(key, e.name)
        except Exception:
            continue
        for t in f.textures:
            base = t.name.rsplit("/", 1)[-1].lower()
            index.setdefault(base, []).append({
                "anm": e.name, "index": t.entry_index,
                "name": t.name, "format": t.format_name,
                "w": t.width, "h": t.height, "x": t.x, "y": t.y,
            })
    with STATE.lock:
        STATE.texture_index[key] = index
    # 写磁盘缓存，并清掉旧版本
    if cache_path:
        try:
            for old in os.listdir(STAGING_DIR):
                if old.startswith("texindex_") and \
                        os.path.join(STAGING_DIR, old) != cache_path:
                    os.remove(os.path.join(STAGING_DIR, old))
            with io.open(cache_path, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False)
        except OSError:
            pass
    return index


def search_textures(key, query, limit=400):
    """按贴图名 / 路径 / ANM 名全局搜索贴图。"""
    query = (query or "").strip().lower()
    if not query:
        return {"query": query, "total": 0, "results": []}
    index = _texture_index(key)
    results = []
    for items in index.values():
        for it in items:
            haystack = "%s %s" % (it["name"].lower(), it["anm"].lower())
            if query in haystack:
                results.append(it)
    # 名字开头命中的排前面
    results.sort(key=lambda it: (
        not it["name"].lower().rsplit("/", 1)[-1].startswith(query),
        it["anm"], it["index"]))
    return {"query": query, "total": len(results),
            "results": results[:limit]}


def list_anm_files(key):
    """列出归档里所有 .anm 及其贴图数量。"""
    a = STATE.archive(key)
    out = []
    for e in a.entries:
        if not e.name.lower().endswith(".anm"):
            continue
        out.append({"name": e.name, "size": e.size})
    return {"anms": out}


def warm_up_texture_index():
    """启动后在后台预热贴图索引（顺便缓存 ANM 解析结果）。"""
    for key in STATE.available_games():
        try:
            _texture_index(key)
        except Exception:
            pass


def _classify_png(key, base, data):
    """识别一张 PNG：显式序号 → 贴图名 → 合成图。"""
    a = STATE.archive(key)
    m = BATCH_TEXTURE_RE.match(base) or BATCH_TEXTURE_ALT_RE.match(base)
    if m and m.groupdict().get("idx") is not None:
        anm_name = m.group("anm")
        idx = int(m.group("idx"))
        for cand in (anm_name, anm_name + ".anm"):
            if a.index_of(cand) >= 0:
                return {"kind": "texture", "target": cand, "index": idx,
                        "matches": [{"anm": cand, "index": idx}],
                        "mode": "single",
                        "detail": "贴图 #%d" % idx}
    # 按“贴图名”匹配（支持合成图）
    matches = _texture_index(key).get(base.lower())
    if not matches:
        return None
    size = _png_size(data)
    # 先按 ANM 分组，看有没有整组刚好等于合成图尺寸
    groups = {}
    for item in matches:
        groups.setdefault(item["anm"], []).append(item)
    if size:
        for anm_name, group in groups.items():
            cw = max(x["x"] + x["w"] for x in group)
            ch = max(x["y"] + x["h"] for x in group)
            if (cw, ch) == size:
                detail = "合成图 %dx%d → %s（%d 张）" % (
                    cw, ch, anm_name, len(group))
                return {"kind": "texture", "target": anm_name, "index": None,
                        "matches": group, "mode": "composed",
                        "detail": detail}
        exact = [x for x in matches if (x["w"], x["h"]) == size]
        if exact:
            target = exact[0]
            detail = "%s #%d" % (target["anm"], target["index"])
            if len(exact) > 1:
                detail += "（另有 %d 张同尺寸同名）" % (len(exact) - 1)
            return {"kind": "texture", "target": target["anm"],
                    "index": target["index"], "matches": [target],
                    "mode": "single", "detail": detail}
    # 尺寸都不符：取最大的一组，按缩放替换
    best = max(matches, key=lambda x: x["w"] * x["h"])
    return {"kind": "texture", "target": best["anm"],
            "index": best["index"], "matches": [best], "mode": "single",
            "detail": "尺寸不符（%s #%d %dx%d），将缩放替换"
                      % (best["anm"], best["index"], best["w"], best["h"])}


def _batch_classify(key, filename, data):
    """判断一个待导入文件的目标。"""
    base = os.path.basename(filename.replace("\\", "/"))
    a = STATE.archive(key)
    # 1) 贴图 PNG
    if base.lower().endswith(".png"):
        info = _classify_png(key, base, data)
        if info:
            return info
        return {"kind": "unknown", "target": base,
                "detail": "找不到同名贴图"}
    # 2) 对话文档 .txt
    if base.lower().endswith(".txt"):
        stem = base[:-4]
        for cand in (stem, stem + ".msg"):
            i = a.index_of(cand)
            if i >= 0 and cand.endswith(".msg"):
                return {"kind": "dialogue", "target": cand,
                        "detail": "对话文档"}
        if stem == "musiccmt":
            return {"kind": "musiccmt", "target": "musiccmt.txt",
                    "detail": "音乐室评论"}
        return {"kind": "unknown", "target": base,
                "detail": "归档里没有对应的 .msg"}
    # 3) 原样替换（文件名与归档条目一致）
    i = a.index_of(base)
    if i >= 0:
        return {"kind": "raw", "target": base,
                "detail": human_size(a.entries[i].size)}
    return {"kind": "unknown", "target": base, "detail": "归档里没有这个文件"}


def batch_add(key, filename, data):
    """把一个文件加入批量导入暂存区，返回识别结果。"""
    info = _batch_classify(key, filename, data)
    STATE.batch_seq += 1
    seq = STATE.batch_seq
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    staged = os.path.join(STAGING_DIR, "batch_%03d%s" % (seq, ext))
    with open(staged, "wb") as f:
        f.write(data)
    # 对话文档：先算一遍差异，方便在列表里预览
    changes = []
    if info["kind"] == "dialogue":
        try:
            a = STATE.archive(key)
            encoding = GAMES[key]["encoding"]
            f_msg = msg.MsgFile.from_bytes(a.read_by_name(info["target"]),
                                           encoding)
            text = data.decode("utf-8-sig", "replace")
            n, unmatched, bad, changes = msg.import_document(
                text, f_msg, encoding, dry_run=True)
            info["detail"] = "将修改 %d 句" % n
            if unmatched:
                info["detail"] += "，%d 行未识别" % len(unmatched)
            if bad:
                info["detail"] += "，%d 个字符无法编码" % len(bad)
        except Exception as ex:
            info["detail"] = "解析失败: %s" % ex
    item = {
        "id": seq,
        "file": os.path.basename(filename.replace("\\", "/")),
        "path": filename,
        "size": len(data),
        "staged": staged,
        "game": key,
        "changes": [
            {"entry": e, "instr": i, "old": o, "new": n}
            for e, i, o, n in changes[:50]
        ],
    }
    item.update(info)
    with STATE.lock:
        STATE.batch = [x for x in STATE.batch if x["file"] != item["file"]]
        STATE.batch.append(item)
    return item


def batch_list():
    with STATE.lock:
        items = list(STATE.batch)
    counts = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return {"items": items, "counts": counts,
            "total": len(items),
            "ready": counts.get("texture", 0) + counts.get("dialogue", 0) +
                     counts.get("raw", 0) + counts.get("musiccmt", 0)}


def batch_remove(item_id):
    with STATE.lock:
        for it in list(STATE.batch):
            if it["id"] == item_id:
                STATE.batch.remove(it)
                try:
                    os.remove(it["staged"])
                except OSError:
                    pass
    return {"ok": True}


def batch_clear():
    with STATE.lock:
        for it in STATE.batch:
            try:
                os.remove(it["staged"])
            except OSError:
                pass
        STATE.batch = []
    return {"ok": True}


def batch_apply():
    """应用暂存区里的全部文件（每个归档只写一次）。"""
    with STATE.lock:
        items = list(STATE.batch)
    if not items:
        raise ApiError("暂存区是空的，请先选择文件夹")
    # 按 归档 -> 条目 汇总
    by_game = {}
    for it in items:
        if it["kind"] == "unknown":
            continue
        by_game.setdefault(it["game"], []).append(it)

    report = {"texture": 0, "dialogue": 0, "raw": 0, "musiccmt": 0,
              "unknown": 0, "changed_lines": 0, "bad_chars": [],
              "unmatched": 0, "errors": [], "staged": 0}
    for it in items:
        if it["kind"] == "unknown":
            report["unknown"] += 1

    for key, group in by_game.items():
        a = STATE.archive(key)
        encoding = GAMES[key]["encoding"]
        replacements = {}
        # ---- 贴图：先合并到各自的 ANM ----
        anm_cache = {}
        from PIL import Image
        import numpy as np

        def load_anm(name):
            if name not in anm_cache:
                anm_cache[name] = anm.AnmFile.from_bytes(a.read_by_name(name))
            return anm_cache[name]

        for it in group:
            if it["kind"] != "texture":
                continue
            try:
                with open(it["staged"], "rb") as f:
                    png = f.read()
                img = Image.open(io.BytesIO(png)).convert("RGBA")
                arr = np.array(img)
                matches = it.get("matches") or []
                if not matches:
                    matches = [{"anm": it["target"], "index": it["index"]}]
                if it.get("mode") == "composed":
                    # 合成图：按每条目的 x/y 切回去
                    by_anm = {}
                    for m in matches:
                        by_anm.setdefault(m["anm"], []).append(m)
                    done = 0
                    for anm_name, ms in by_anm.items():
                        f_anm = load_anm(anm_name)
                        crops = []
                        for m in ms:
                            x, y = m.get("x", 0), m.get("y", 0)
                            w, h = m["w"], m["h"]
                            if y + h > arr.shape[0] or x + w > arr.shape[1]:
                                raise ApiError(
                                    "%s: 图片比合成画布小（需要 %dx%d）"
                                    % (it["file"],
                                       max(mm.get("x", 0) + mm["w"]
                                           for mm in ms),
                                       max(mm.get("y", 0) + mm["h"]
                                           for mm in ms)))
                            crops.append((m["index"], x, y, w, h,
                                          arr[y:y + h, x:x + w]))
                        done += f_anm.replace_many(crops)
                        replacements[anm_name] = f_anm.to_bytes()
                    if not done:
                        report["errors"].append("%s: 没有可替换的贴图"
                                                % it["file"])
                        continue
                else:
                    m = matches[0]
                    anm_name = m["anm"]
                    f_anm = load_anm(anm_name)
                    tex = f_anm.find(m["index"])
                    if tex is None:
                        report["errors"].append("%s: 没有 #%d 号贴图"
                                                % (it["file"], m["index"]))
                        continue
                    encoded = anm.encode_rgba(tex.format, arr)
                    f_anm.replace_texture(tex, encoded, img.width, img.height)
                    replacements[anm_name] = f_anm.to_bytes()
                report["texture"] += 1
            except ApiError as ex:
                report["errors"].append(str(ex))
            except Exception as ex:
                report["errors"].append("%s: %s" % (it["file"], ex))
        # ---- 对话文档 ----
        for it in group:
            if it["kind"] != "dialogue":
                continue
            try:
                with io.open(it["staged"], "r", encoding="utf-8-sig",
                             errors="replace") as f:
                    text = f.read()
                name = it["target"]
                base = replacements.get(name)
                f_msg = msg.MsgFile.from_bytes(
                    base if base else a.read_by_name(name), encoding)
                changed, unmatched, bad, _changes = msg.import_document(
                    text, f_msg, encoding)
                if changed:
                    replacements[name] = f_msg.to_bytes()
                report["dialogue"] += 1
                report["changed_lines"] += changed
                report["unmatched"] += len(unmatched)
                for ch in bad:
                    if ch not in report["bad_chars"]:
                        report["bad_chars"].append(ch)
            except Exception as ex:
                report["errors"].append("%s: %s" % (it["file"], ex))
        # ---- 音乐室评论 ----
        for it in group:
            if it["kind"] != "musiccmt":
                continue
            try:
                with io.open(it["staged"], "r", encoding="utf-8-sig",
                             errors="replace") as f:
                    text = f.read()
                replacements["musiccmt.txt"] = text.encode(encoding,
                                                           "replace")
                report["musiccmt"] += 1
            except Exception as ex:
                report["errors"].append("%s: %s" % (it["file"], ex))
        # ---- 原样替换 ----
        for it in group:
            if it["kind"] != "raw":
                continue
            try:
                with open(it["staged"], "rb") as f:
                    replacements[it["target"]] = f.read()
                report["raw"] += 1
            except Exception as ex:
                report["errors"].append("%s: %s" % (it["file"], ex))
        # ---- 加入待保存队列（写盘前自检 ANM 结构） ----
        for name, blob in replacements.items():
            if name.lower().endswith(".anm"):
                problems = anm.AnmFile.from_bytes(blob).validate()
                if problems:
                    report["errors"].append(
                        "%s 结构异常，已跳过: %s"
                        % (name, "；".join(problems[:2])))
                    continue
            action = ("导入贴图" if name.lower().endswith(".anm")
                      else "导入对话文档" if name.lower().endswith(".msg")
                      else "批量替换文件")
            try:
                save_archive_entry(key, name, blob, action=action,
                                   detail="批量导入")
                report["staged"] += 1
            except ApiError as ex:
                report["errors"].append("%s: %s" % (name, ex))
    log("批量导入（暂存）", "共 %d 个文件" % len(items),
        "贴图 %d / 对话 %d / 原样 %d / 评论 %d / 未识别 %d"
        % (report["texture"], report["dialogue"], report["raw"],
           report["musiccmt"], report["unknown"]))
    batch_clear()
    report["ok"] = True
    return report


def _fmt_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (OSError, ValueError, OverflowError):
        return "-"


def list_backups():
    """列出备份及其与当前文件的差异。"""
    meta = _backup_meta()
    items = [("jp", STATE.archive_path("jp")),
             ("cn", STATE.archive_path("cn")),
             ("bgm", BGM_DAT)]
    out = []
    for key, target in items:
        bak = target + BACKUP_SUFFIX
        if not os.path.exists(bak):
            continue
        st = os.stat(bak)
        info = meta.get(os.path.basename(bak), {})
        # copy2 会保留源文件时间，所以“备份时间”优先用元数据里的创建时间
        created = info.get("created") or st.st_ctime
        if created <= 0:
            created = st.st_mtime
        cur_size = os.path.getsize(target) if os.path.exists(target) else 0
        cur_mtime = os.path.getmtime(target) if os.path.exists(target) else 0
        # 时间或大小任一不同都算“备份后被改过”
        changed = bool(cur_mtime) and (
            cur_mtime > created + 1 or cur_size != st.st_size)
        out.append({
            "key": key,
            "name": os.path.basename(bak),
            "target": os.path.basename(target),
            "size": st.st_size,
            "size_text": human_size(st.st_size),
            "created": created,
            "time_text": _fmt_time(created),
            "source_time_text": _fmt_time(st.st_mtime),
            "comment": info.get("comment", ""),
            "current_size": cur_size,
            "current_size_text": human_size(cur_size),
            "current_time_text": _fmt_time(cur_mtime) if cur_mtime else "-",
            "modified": changed,
        })
    return {"backups": out}


def restore_backup(which):
    """把备份写回游戏。还原前会先把当前文件另存为 .modtool.prev。"""
    mapping = {
        "jp": STATE.archive_path("jp"),
        "cn": STATE.archive_path("cn"),
        "bgm": BGM_DAT,
    }
    if which not in mapping:
        raise ApiError("未知备份: %s" % which)
    target = mapping[which]
    bak = target + BACKUP_SUFFIX
    if not os.path.exists(bak):
        raise ApiError("备份不存在（%s 还没有生成过备份）"
                       % os.path.basename(target), 404)
    with STATE.lock:
        if os.path.exists(target):
            # 还原前保留当前状态，避免手滑丢失改动
            try:
                _atomic_copy(target, target + ".modtool.prev")
            except OSError:
                pass
        _atomic_copy(bak, target)
        STATE.invalidate()
    log("还原备份", os.path.basename(bak),
        "-> %s（原文件已存为 .modtool.prev）" % os.path.basename(target))
    return {"ok": True, "target": os.path.basename(target),
            "prev": os.path.basename(target) + ".modtool.prev"}


def restore_all():
    """一键把所有备份写回（保证各文件之间状态一致）。"""
    done = []
    errors = []
    for key in ("jp", "cn", "bgm"):
        try:
            restore_backup(key)
            done.append(key)
        except ApiError as ex:
            errors.append("%s: %s" % (key, ex))
        except PermissionError:
            errors.append("%s: 文件被占用（请先关闭游戏）" % key)
    return {"ok": True, "restored": done, "errors": errors}


# ----------------------------------------------------------------------
# HTTP 服务
# ----------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "TH12ModTool/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 基础输出 --------------------------------------------------
    def _send(self, code, body, content_type="application/json",
              extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _error(self, message, code=400):
        self._json({"error": message}, code)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _query(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        return parsed.path, {k: v[0] for k, v in qs.items()}

    # ---- GET ------------------------------------------------------
    def do_GET(self):
        try:
            path, q = self._query()
            if path.startswith("/api/"):
                return self._api_get(path[5:], q)
            return self._static(path)
        except ApiError as ex:
            self._error(str(ex), ex.code)
        except PermissionError:
            self._error("文件被占用：请先关闭游戏（th12.exe / th12c.exe）再操作", 409)
        except Exception as ex:
            self._error("服务器错误: %s" % ex, 500)

    def _api_get(self, route, q):
        if route == "state":
            games = STATE.available_games()
            if not games:
                return self._json({
                    "games": [],
                    "default_game": None,
                    "game_dir": GAME_DIR,
                    "need_config": True,
                    "bgm": None,
                    "backups": [],
                })
            if STATE.game_key not in games:
                STATE.game_key = games[0]
            return self._json({
                "games": [{"key": k, "label": GAMES[k]["label"]}
                          for k in games],
                "default_game": STATE.game_key,
                "game_dir": GAME_DIR,
                "bgm": list_bgm(),
                "backups": list_backups()["backups"],
            })
        if route == "archive":
            return self._json(list_archive(q.get("game", "jp")))
        if route == "file":
            key = q.get("game", "jp")
            name = q.get("name", "")
            data = STATE.archive(key).read_by_name(name)
            fname = urllib.parse.quote(os.path.basename(name))
            return self._send(200, data, "application/octet-stream",
                              {"Content-Disposition":
                               "attachment; filename*=UTF-8''%s" % fname})
        if route == "textures":
            return self._json(list_textures(q.get("game", "jp"),
                                            q.get("anm", "")))
        if route == "textures.search":
            return self._json(search_textures(q.get("game", "jp"),
                                              q.get("q", "")))
        if route == "anms":
            return self._json(list_anm_files(q.get("game", "jp")))
        if route == "texture.png":
            data = texture_png(q.get("game", "jp"), q.get("anm", ""),
                               int(q.get("index", "0")))
            return self._send(200, data, "image/png")
        if route == "bgm":
            return self._json(list_bgm())
        if route == "bgm.wav":
            data = bgm_wav(int(q.get("index", "0")))
            return self._send(200, data, "audio/wav")
        if route == "job":
            job = STATE.jobs.get(int(q.get("id", "0")))
            if not job:
                raise ApiError("任务不存在", 404)
            return self._json(job)
        if route == "msg":
            return self._json(get_msg(q.get("game", "jp"),
                                      q.get("name", "")))
        if route == "msg.doc":
            key = q.get("game", "jp")
            name = q.get("name", "")
            text = msg_document(key, name)
            fname = urllib.parse.quote(
                name.rsplit(".", 1)[0] + ".txt")
            return self._send(200, text.encode("utf-8"),
                              "text/plain; charset=utf-8",
                              {"Content-Disposition":
                               "attachment; filename*=UTF-8''%s" % fname})
        if route == "logs":
            limit = int(q.get("limit", "200"))
            return self._json({"logs": LOG.list(limit)})
        if route == "logs.download":
            if os.path.isfile(LOG_PATH):
                with open(LOG_PATH, "rb") as f:
                    data = f.read()
            else:
                data = b""
            fname = urllib.parse.quote("modtool.log")
            return self._send(200, data, "text/plain; charset=utf-8",
                              {"Content-Disposition":
                               "attachment; filename*=UTF-8''%s" % fname})
        if route == "batch":
            return self._json(batch_list())
        if route == "musiccmt":
            return self._json(get_musiccmt(q.get("game", "jp")))
        if route == "backups":
            return self._json(list_backups())
        if route == "pending":
            return self._json(list_pending())
        if route == "backup.download":
            which = q.get("key", "")
            mapping = {"jp": STATE.archive_path("jp"),
                       "cn": STATE.archive_path("cn"),
                       "bgm": BGM_DAT}
            if which not in mapping:
                raise ApiError("未知备份: %s" % which, 404)
            bak = mapping[which] + BACKUP_SUFFIX
            if not os.path.isfile(bak):
                raise ApiError("备份不存在", 404)
            with open(bak, "rb") as f:
                data = f.read()
            fname = urllib.parse.quote(os.path.basename(bak))
            return self._send(200, data, "application/octet-stream",
                              {"Content-Disposition":
                               "attachment; filename*=UTF-8''%s" % fname})
        if route == "config":
            return self._json(get_config())
        raise ApiError("未知接口: %s" % route, 404)

    # ---- POST -----------------------------------------------------
    def do_POST(self):
        try:
            path, q = self._query()
            if not path.startswith("/api/"):
                raise ApiError("未知接口", 404)
            route = path[5:]
            body = self._read_body()
            if route == "file":
                return self._json(save_archive_entry(
                    q.get("game", "jp"), q.get("name", ""), body))
            if route == "texture":
                return self._json(replace_texture(
                    q.get("game", "jp"), q.get("anm", ""),
                    int(q.get("index", "0")), body))
            if route == "bgm.replace":
                return self._json(bgm_replace(
                    int(q.get("index", "0")), body))
            if route == "bgm.loop":
                return self._json(bgm_set_loop(
                    int(q.get("index", "0")), q.get("loop", "0")))
            if route == "bgm.apply":
                payload = json.loads(body.decode("utf-8") or "{}")
                return self._json(start_bgm_apply(payload.get("comment", "")))
            if route == "bgm.cancel":
                return self._json(bgm_cancel())
            if route == "msg":
                payload = json.loads(body.decode("utf-8") or "{}")
                return self._json(save_msg(
                    q.get("game", "jp"), q.get("name", ""), payload))
            if route == "msg.import":
                text = body.decode("utf-8-sig", "replace")
                return self._json(msg_import_document(
                    q.get("game", "jp"), q.get("name", ""), text))
            if route == "msg.preview":
                text = body.decode("utf-8-sig", "replace")
                return self._json(msg_import_document(
                    q.get("game", "jp"), q.get("name", ""), text,
                    dry_run=True))
            if route == "logs.clear":
                LOG.clear()
                log("清空日志")
                return self._json({"ok": True})
            if route == "batch.add":
                return self._json(batch_add(
                    q.get("game", STATE.game_key), q.get("name", "file"),
                    body))
            if route == "batch.apply":
                return self._json(batch_apply())
            if route == "batch.clear":
                return self._json(batch_clear())
            if route == "batch.remove":
                return self._json(batch_remove(int(q.get("id", "0"))))
            if route == "musiccmt":
                payload = json.loads(body.decode("utf-8") or "{}")
                return self._json(save_musiccmt(
                    q.get("game", "jp"), payload.get("text", "")))
            if route == "restore":
                return self._json(restore_backup(q.get("key", "")))
            if route == "restore.all":
                return self._json(restore_all())
            if route == "save":
                payload = json.loads(body.decode("utf-8") or "{}")
                return self._json(save_all(payload.get("comment", "")))
            if route == "pending.clear":
                return self._json(clear_pending())
            if route == "config":
                payload = json.loads(body.decode("utf-8") or "{}")
                return self._json(set_config(payload))
            if route == "pick-dir":
                return self._json(pick_directory())
            if route == "launch":
                return self._json(launch_game(q.get("game")))
            raise ApiError("未知接口: %s" % route, 404)
        except ApiError as ex:
            self._error(str(ex), ex.code)
        except PermissionError:
            self._error("文件被占用：请先关闭游戏（th12.exe / th12c.exe）再操作", 409)
        except Exception as ex:
            self._error("服务器错误: %s" % ex, 500)

    # ---- 静态文件 --------------------------------------------------
    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = urllib.parse.unquote(path.lstrip("/"))
        target = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not target.startswith(WEB_DIR) or not os.path.isfile(target):
            return self._error("文件不存在", 404)
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(target)[1].lower(),
              "application/octet-stream")
        with open(target, "rb") as f:
            self._send(200, f.read(), ctype)

    def log_message(self, fmt, *args):
        sys.stderr.write("[modtool] %s\n" % (fmt % args))


def main():
    ap = argparse.ArgumentParser(description="TH12 魔改工具 Web 服务")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--game-dir", default=None,
                    help="游戏目录（默认为 game/[th12] …）")
    args = ap.parse_args()

    # 目录优先级：命令行 > config.json > 默认目录
    cfg = load_config()
    if args.game_dir:
        set_game_dir(args.game_dir)
    elif cfg.get("game_dir") and os.path.isdir(cfg["game_dir"]):
        set_game_dir(cfg["game_dir"])
    if cfg.get("last_game"):
        STATE.game_key = cfg["last_game"]

    if not os.path.isdir(GAME_DIR):
        print("找不到游戏目录: %s" % GAME_DIR)
        print("→ 服务仍会启动，请在网页右上角「设置目录」里指定。")
    elif not check_game_dir(GAME_DIR):
        print("警告: %s 里没有 th12.dat / th12c.dat" % GAME_DIR)
        print("→ 请在网页右上角「设置目录」里改。")

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
    # 后台预热贴图索引（首次要解压全部 .anm，之后走磁盘缓存）
    threading.Thread(target=warm_up_texture_index, daemon=True).start()
    url = "http://127.0.0.1:%d/" % args.port
    print("=" * 56)
    print("  东方星莲船 魔改工具")
    print("  游戏目录: %s" % GAME_DIR)
    print("  请在浏览器打开: %s" % url)
    print("  按 Ctrl+C 退出")
    print("=" * 56)
    if not args.no_browser:
        try:
            import webbrowser
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
