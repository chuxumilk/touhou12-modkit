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
import shutil
import struct
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

WEB_DIR = os.path.join(HERE, "web")
STAGING_DIR = os.path.join(HERE, "staging")
BACKUP_SUFFIX = ".modtool.bak"

GAME_DIR = os.path.join(WS, "game",
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
    """允许通过命令行切换游戏目录（便于测试/多份游戏）。"""
    global GAME_DIR, BGM_DAT
    GAME_DIR = os.path.abspath(path)
    BGM_DAT = os.path.join(GAME_DIR, "thbgm.dat")


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def ensure_backup(path):
    """首次写入前备份原文件（只备份一次，保留最原始版本）。"""
    bak = path + BACKUP_SUFFIX
    if os.path.exists(path) and not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


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
        self.fmt = None
        self.bgm_pending = {}     # index -> 暂存的 PCM 文件路径
        self.bgm_loop = {}        # index -> 新的循环点（字节）
        self.bgm_replaced = set()
        self.jobs = {}
        self.job_seq = 0
        self.game_key = "jp"
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


def save_archive_entry(key, name, data):
    """替换归档里的一个条目（自动备份）。"""
    with STATE.lock:
        a = STATE.archive(key)
        idx = a.index_of(name)
        if idx < 0:
            raise ApiError("归档中没有条目: %s" % name, 404)
        path = STATE.archive_path(key)
        ensure_backup(path)
        a.save_patched(path, {idx: data})
        STATE.anm_cache = {}
    return {"ok": True, "name": name, "size": len(data)}


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
    save_archive_entry(key, anm_name, f.to_bytes())
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
    return {"ok": True, "seconds": len(pcm) / float(bgm.BYTES_PER_SEC)}


def bgm_set_loop(index, loop_bytes):
    fmt = STATE.bgm_fmt()
    if index < 0 or index >= len(fmt.tracks):
        raise ApiError("曲目序号不存在", 404)
    loop_bytes = max(0, int(loop_bytes))
    loop_bytes -= loop_bytes % 4
    with STATE.lock:
        STATE.bgm_loop[index] = loop_bytes
    return {"ok": True, "loop": loop_bytes}


def bgm_cancel():
    with STATE.lock:
        for path in STATE.bgm_pending.values():
            try:
                os.remove(path)
            except OSError:
                pass
        STATE.bgm_pending = {}
        STATE.bgm_loop = {}
        STATE.bgm_replaced = set()
    return {"ok": True}


def _apply_bgm(job):
    fmt = STATE.bgm_fmt()
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


def start_bgm_apply():
    job = STATE.new_job()

    def runner():
        try:
            _apply_bgm(job)
            job["state"] = "done"
            job["progress"] = 1.0
        except Exception as ex:
            job["state"] = "error"
            job["message"] = str(ex)
            job["trace"] = traceback.format_exc()

    t = threading.Thread(target=runner)
    t.daemon = True
    t.start()
    return {"job": job["id"]}


def get_msg(key, name):
    a = STATE.archive(key)
    raw = a.read_by_name(name)
    encoding = GAMES[key]["encoding"]
    f = msg.MsgFile.from_bytes(raw, encoding)
    entries = []
    for i, entry in enumerate(f.entries):
        instrs = []
        for j, ins in enumerate(entry.instructions):
            item = {"index": j, "time": ins.time, "type": ins.type,
                    "length": len(ins.data)}
            if ins.is_text:
                item["text"] = ins.text(encoding)
            instrs.append(item)
        entries.append({"index": i, "extra": entry.extra,
                        "instructions": instrs})
    return {"name": name, "encoding": encoding, "entries": entries}


def save_msg(key, name, payload):
    """payload: {"entries": [{"index": i, "texts": {instr_index: text}}]}"""
    a = STATE.archive(key)
    raw = a.read_by_name(name)
    encoding = GAMES[key]["encoding"]
    f = msg.MsgFile.from_bytes(raw, encoding)
    edits = payload.get("entries", [])
    changed = 0
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
                    ins.set_text(text, encoding)
                    changed += 1
    if changed:
        save_archive_entry(key, name, f.to_bytes())
    return {"ok": True, "changed": changed}


def get_musiccmt(key):
    a = STATE.archive(key)
    raw = a.read_by_name("musiccmt.txt")
    encoding = GAMES[key]["encoding"]
    return {"text": raw.decode(encoding, "replace"), "encoding": encoding}


def save_musiccmt(key, text):
    encoding = GAMES[key]["encoding"]
    data = text.encode(encoding, "replace")
    save_archive_entry(key, "musiccmt.txt", data)
    return {"ok": True, "size": len(data)}


def list_backups():
    out = []
    for key in GAMES:
        path = STATE.archive_path(key)
        bak = path + BACKUP_SUFFIX
        if os.path.exists(bak):
            out.append({
                "key": key, "path": bak,
                "name": os.path.basename(bak),
                "size": os.path.getsize(bak),
                "time": os.path.getmtime(bak),
            })
    bak = BGM_DAT + BACKUP_SUFFIX
    if os.path.exists(bak):
        out.append({
            "key": "bgm", "path": bak,
            "name": os.path.basename(bak),
            "size": os.path.getsize(bak),
            "time": os.path.getmtime(bak),
        })
    for item in out:
        item["size_text"] = human_size(item["size"])
        item["time_text"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(item["time"]))
    return {"backups": out}


def restore_backup(which):
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
        raise ApiError("备份不存在", 404)
    with STATE.lock:
        shutil.copy2(bak, target)
        STATE.invalidate()
    return {"ok": True}


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
                raise ApiError("游戏目录里没有 th12.dat / th12c.dat")
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
        if route == "musiccmt":
            return self._json(get_musiccmt(q.get("game", "jp")))
        if route == "backups":
            return self._json(list_backups())
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
                return self._json(start_bgm_apply())
            if route == "bgm.cancel":
                return self._json(bgm_cancel())
            if route == "msg":
                payload = json.loads(body.decode("utf-8") or "{}")
                return self._json(save_msg(
                    q.get("game", "jp"), q.get("name", ""), payload))
            if route == "musiccmt":
                payload = json.loads(body.decode("utf-8") or "{}")
                return self._json(save_musiccmt(
                    q.get("game", "jp"), payload.get("text", "")))
            if route == "restore":
                return self._json(restore_backup(q.get("key", "")))
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

    if args.game_dir:
        set_game_dir(args.game_dir)

    if not os.path.isdir(GAME_DIR):
        print("找不到游戏目录: %s" % GAME_DIR)
        return 1

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
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
