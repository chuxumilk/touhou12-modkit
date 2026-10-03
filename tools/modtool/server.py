# -*- coding: utf-8 -*-
"""东方星莲船 (TH12) 魔改工具 —— 本地 Web 后端。

用法::

    python tools/modtool/server.py [--port 8765] [--no-browser]

启动后浏览器访问 http://127.0.0.1:8765/ 即可。
所有写操作都会自动备份原文件（*.modtool.bak）。
"""

import argparse
import array
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
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", ".."))
if WS not in sys.path:
    sys.path.insert(0, WS)

from thtk import anm, archive, bgm, msg  # noqa: E402

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
# 实时进度（前端轮询 /api/progress）
# ----------------------------------------------------------------------
class Progress(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.data = {"active": False, "label": "", "detail": "",
                     "current": 0, "total": 0, "token": 0,
                     "background": False}

    def start(self, label, total=0, detail="", background=False):
        """开始一个进度；background=True 的任务不会抢占前台进度。

        返回 token，更新时带上它，避免后台任务覆盖前台任务的进度。
        """
        with self.lock:
            if background and self.data.get("active") and \
                    not self.data.get("background"):
                return None
            self.seq += 1
            self.data = {"active": True, "label": label, "detail": detail,
                         "current": 0, "total": int(total or 0),
                         "token": self.seq, "background": background}
            return self.seq

    def _check(self, token):
        return token is None or self.data.get("token") == token

    def update(self, current=None, total=None, detail=None, label=None,
               token=None):
        with self.lock:
            if not self._check(token):
                return
            d = self.data
            d["active"] = True
            if label is not None:
                d["label"] = label
            if total is not None:
                d["total"] = int(total)
            if current is not None:
                d["current"] = int(current)
            if detail is not None:
                d["detail"] = detail

    def step(self, detail=None, label=None, token=None):
        with self.lock:
            if not self._check(token):
                return
            self.data["current"] = self.data.get("current", 0) + 1
            if detail is not None:
                self.data["detail"] = detail
            if label is not None:
                self.data["label"] = label

    def done(self, token=None):
        with self.lock:
            if not self._check(token):
                return
            self.data = {"active": False, "label": "", "detail": "",
                         "current": 0, "total": 0, "token": self.seq,
                         "background": False}

    def get(self):
        with self.lock:
            d = dict(self.data)
            d.pop("token", None)
            return d


PROGRESS = Progress()


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


def normalize_input_path(path):
    """整理用户填的路径：去空白/去引号/去掉 \\\\?\\ 长路径前缀。"""
    p = (path or "").strip().strip('"').strip()
    # 复制路径时可能带上 "\\?\" 或 "\\.\" 前缀，去掉才能当普通路径用
    if p.startswith("\\\\?\\UNC\\"):
        p = "\\\\" + p[8:]
    elif p.startswith("\\\\?\\") or p.startswith("\\\\.\\"):
        p = p[4:]
    return p.strip()


def set_game_dir(path):
    """切换游戏目录（便于测试/多份游戏）。"""
    global GAME_DIR, BGM_DAT
    GAME_DIR = os.path.abspath(normalize_input_path(path))
    BGM_DAT = os.path.join(GAME_DIR, "thbgm.dat")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    """写配置文件。写失败时返回错误字符串（以前是静默吞掉，用户看不到）。"""
    try:
        if not os.path.isdir(DATA_DIR):
            os.makedirs(DATA_DIR)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        return None
    except Exception as ex:
        return "%s（配置路径：%s）" % (ex, CONFIG_PATH)


def check_game_dir(path):
    """返回该目录里可用的版本列表；空列表表示不是 TH12 游戏目录。"""
    found = []
    for key, info in GAMES.items():
        try:
            if os.path.isfile(os.path.join(path, info["dat"])):
                found.append(key)
        except OSError:
            pass
    return found


# 扫描时跳过这些目录（体积大 / 与游戏无关）
SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windows", "winsxs",
    "node_modules", ".git", "__pycache__", "appdata", "program files",
    "program files (x86)", "programdata", "$windows.~bt", "$windows.~ws",
}


# 目录名里出现这些词就先扫（游戏目录一般躲在这些名字下面）
PRIORITY_WORDS = (
    "th12", "touhou", "东方", "游戏", "game", "games", "stg", "弹幕",
    "东方project", "同人", "魔改", "mod",
)


def _priority(name):
    """越小越先扫。命中关键词的排前面，纯数字序号目录次之。"""
    low = name.lower()
    for i, w in enumerate(PRIORITY_WORDS):
        if w in low:
            return i
    return len(PRIORITY_WORDS)


def _subdirs(path, use_priority=False):
    try:
        names = os.listdir(path)
    except OSError:
        return []
    out = []
    for name in names:
        if name.lower() in SKIP_DIRS or name.startswith("$"):
            continue
        sub = os.path.join(path, name)
        try:
            if os.path.isdir(sub):
                out.append(sub)
        except OSError:
            pass
    if use_priority:
        out.sort(key=lambda p: (_priority(os.path.basename(p)),
                                len(os.path.basename(p))))
    return out


def scan_for_games(root, depth=2, budget=None, priority=False):
    """在 root 下按 depth 层扫描真正的游戏目录。

    返回 [{"path":..., "games":[...], "depth":n}]。
    只下探目录，不读取 .dat 内容；priority=True 时先扫名字像游戏的目录，
    这样即使预算用完，也大概率已经把游戏找到了。
    """
    if budget is None:
        budget = [1200]                      # 最多检查这么多个目录
    found = []
    if not root or not os.path.isdir(root):
        return found

    def walk(path, level):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        games = check_game_dir(path)
        if games:
            found.append({"path": path, "games": games, "depth": level})
            if level > 0:
                return                       # 已经是游戏目录，不再往里钻
        if level >= depth:
            return
        for sub in _subdirs(path, use_priority=priority):
            if budget[0] <= 0:
                return
            walk(sub, level + 1)

    # root 本身也算第 0 层
    walk(root, 0)
    return found


def _reg_recent_dirs():
    """从注册表里读游戏最近用过的路径（ZUN 的游戏会存在自己名字的键下）。

    用户跑过一次游戏后，这里就能直接拿到游戏目录，比全盘扫描快得多。
    """
    out = []
    try:
        import winreg
    except ImportError:
        return out
    keys = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in ("Software\\Microsoft\\Windows\\CurrentVersion\\App Paths",
                    "Software\\ZUN", "Software\\WOW6432Node\\ZUN"):
            keys.append((hive, sub))
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as k:
                n = winreg.QueryInfoKey(k)[0]
                for i in range(n):
                    try:
                        name = winreg.EnumKey(k, i)
                    except OSError:
                        continue
                    if "th12" not in name.lower() and "touhou" not in name.lower():
                        continue
                    try:
                        with winreg.OpenKey(k, name) as k2:
                            m = winreg.QueryInfoKey(k2)[1]
                            for j in range(m):
                                vname, vdata, _ = winreg.EnumValue(k2, j)
                                if not isinstance(vdata, str):
                                    continue
                                if "th12" not in vdata.lower():
                                    continue
                                d = vdata if os.path.isdir(vdata) \
                                    else os.path.dirname(vdata)
                                if d and os.path.isdir(d):
                                    out.append(d)
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _path_dirs():
    """PATH 里出现 th12 / th12c 的话，取它所在目录。"""
    import shutil as _shutil
    out = []
    for exe in ("th12.exe", "th12c.exe"):
        p = _shutil.which(exe)
        if p:
            out.append(os.path.dirname(p))
    return out


def _scan_roots(include_drives=True):
    """生成扫描起点列表（常见位置 + 用过的目录 + 注册表 + PATH + 盘符根目录）。"""
    roots = [
        os.path.join(EXE_DIR, "game"),
        EXE_DIR,
        os.getcwd(),
        os.path.dirname(EXE_DIR),
        os.path.dirname(os.path.dirname(EXE_DIR)),
        os.path.dirname(os.path.dirname(os.path.dirname(EXE_DIR))),
    ]
    cfg = load_config()
    # 用过的目录：优先扫它的上一级，这样「游戏就在旁边」的情况一次就命中
    for key in ("game_dir", "last_dir"):
        p = cfg.get(key)
        if p and os.path.isdir(p):
            roots.append(p)
            roots.append(os.path.dirname(p))
            roots.append(os.path.dirname(os.path.dirname(p)))
    for key in ("recent_dirs", "known_dirs"):
        for p in cfg.get(key) or []:
            if os.path.isdir(p):
                roots.append(p)
                roots.append(os.path.dirname(p))
    roots.extend(_reg_recent_dirs())
    roots.extend(_path_dirs())
    if include_drives:
        for drive in ("D:", "E:", "F:", "C:"):
            if os.path.isdir(drive + "\\"):
                roots.append(drive + "\\")
    return roots


# 候选目录缓存：开弹窗会频繁调 /api/config，扫描结果缓存 60 秒
_CAND_CACHE = {"time": 0.0, "items": [], "deep": False}
_CAND_TTL = 60.0


def find_candidates(deep=False, force=False):
    """自动查找候选游戏目录。

    - 已知位置（工作目录、游戏目录的上级、用过的目录、盘符下名字像游戏的目录）
      做有限深度遍历
    - deep=True（用户点「重新扫描」）时会做一次限时的全盘遍历，
      并优先用 Everything（若装了）
    - 整个过程有时间上限，绝不把网页卡住
    - 结果缓存，避免每次开设置弹窗都扫一遍磁盘
    """
    now = time.time()
    if (not force and _CAND_CACHE["items"]
            and (now - _CAND_CACHE["time"] < _CAND_TTL)
            and (not deep or _CAND_CACHE["deep"])):
        return _CAND_CACHE["items"]

    budget_s = 30.0 if deep else 6.0
    deadline = time.time() + budget_s
    out = []
    seen = set()
    roots_done = set()

    def add(path, games, level=0):
        key = os.path.abspath(path).lower()
        if key in seen or not games:
            return
        seen.add(key)
        out.append({"path": path, "games": games, "depth": level})

    # ① 装了 Everything 的话，这一步基本就直接找到了
    for d in _by_everything(["th12.exe", "th12c.exe", "th12.dat", "th12c.dat"]):
        add(d, check_game_dir(d))

    # ② 已知位置 + 盘符下「名字像游戏」的目录（放前面扫描）
    for root in _scan_roots(include_drives=False):
        if time.time() > deadline:
            break
        real = os.path.abspath(root).lower()
        if real in roots_done or not os.path.isdir(root):
            continue
        roots_done.add(real)
        for item in scan_for_games(root, depth=3, priority=True,
                                   budget=[600]):
            add(item["path"], item["games"], item["depth"])
    for top in _drive_top_folders(deep=deep):
        if time.time() > deadline:
            break
        real = os.path.abspath(top).lower()
        if real in roots_done:
            continue
        roots_done.add(real)
        for item in scan_for_games(top, depth=4, priority=True,
                                   budget=[1500]):
            add(item["path"], item["games"], item["depth"])

    # ③ 还差得远就接着在全盘里按名字优先级找（限时）
    if (len(out) < 3 or deep) and time.time() < deadline:
        depth = 6 if deep else 3
        for drive in ("D:", "E:", "C:", "F:"):
            if time.time() > deadline:
                break
            if not os.path.isdir(drive + "\\"):
                continue
            fast_walk(drive + "\\", deadline, depth=depth,
                      on_game=lambda p, g: add(p, g, 4))

    cur = GAME_DIR.lower()
    out.sort(key=lambda it: (os.path.abspath(it["path"]).lower() != cur,
                             it["depth"]))
    _CAND_CACHE.update({"time": now, "items": out, "deep": deep})
    return out
    _CAND_CACHE.update({"time": now, "items": out, "deep": deep})
    return out


def _everything_exe():
    """找 Everything 的命令行工具 es.exe（有的话搜索是毫秒级的）。"""
    cands = []
    for p in (os.environ.get("PATH") or "").split(os.pathsep):
        if p:
            cands.append(os.path.join(p, "es.exe"))
    for base in (os.environ.get("ProgramFiles") or r"C:\Program Files",
                 os.environ.get("ProgramFiles(x86)") or "",
                 os.environ.get("LOCALAPPDATA") or "",
                 r"C:\Program Files\Everything",
                 r"C:\Program Files (x86)\Everything",
                 r"C:\tools"):
        if base:
            cands.append(os.path.join(base, "Everything", "es.exe"))
            cands.append(os.path.join(base, "es.exe"))
    for c in cands:
        try:
            if os.path.isfile(c):
                return c
        except OSError:
            pass
    return None


def _by_everything(names, timeout=20):
    """用 Everything 按文件名反查目录（只有装了 Everything 才有）。"""
    es = _everything_exe()
    if not es:
        return []
    dirs = []
    for name in names:
        try:
            r = subprocess.run([es, "-f", name], capture_output=True,
                               timeout=timeout)
        except Exception:
            continue
        for ln in (r.stdout or b"").decode("utf-8", "replace").splitlines():
            ln = ln.strip().strip('"')
            if ln:
                dirs.append(os.path.dirname(ln))
    return [d for d in dirs if d and os.path.isdir(d)]


def fast_walk(root, deadline, depth=3, on_game=None):
    """快速遍历（os.scandir + 时限），用来在全盘里找游戏目录。

    - deadline: time.time() 的绝对时限，超时立刻返回（调用方保证不卡）
    - 名字像游戏的目录先扫，所以即使被时限截断也大概率已经找到
    """
    stack = [(root, 0)]
    while stack:
        if time.time() > deadline:
            return
        path, level = stack.pop()
        games = check_game_dir(path)
        if games and level > 0:
            if on_game:
                on_game(path, games)
            continue
        if level >= depth:
            continue
        try:
            with os.scandir(path) as it:
                subs = []
                for e in it:
                    try:
                        if not e.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if e.name.lower() in SKIP_DIRS or e.name.startswith("$"):
                        continue
                    subs.append(e.path)
        except OSError:
            continue
        # 先扫名字像游戏的（注意 stack 是后进先出，所以按优先级倒序压栈）
        subs.sort(key=lambda p: (_priority(os.path.basename(p)),
                                 len(os.path.basename(p))), reverse=True)
        for sub in subs:
            stack.append((sub, level + 1))


def _drive_top_folders(deep=False):
    """盘符下「名字像游戏」的那一两个文件夹，用于深扫一层。"""
    tops = []
    for drive in ("D:", "E:", "F:", "C:"):
        if not os.path.isdir(drive + "\\"):
            continue
        for sub in _subdirs(drive + "\\", use_priority=True)[:4]:
            tops.append(sub)
    return tops


def find_game_subdir(path, max_nodes=800):
    """在 path 里（最多下探 2 层）找真正装着游戏的子目录。

    用户很容易把目录指到「上一级」（例如选到 game\\ 而不是
    game\\[th12] 东方星莲船\\），这里直接把正确路径找出来。
    """
    if not path or not os.path.isdir(path):
        return None
    nodes = [0]

    def walk(cur, level):
        if nodes[0] > max_nodes or level > 2:
            return None
        for sub in _subdirs(cur, use_priority=True):
            nodes[0] += 1
            if nodes[0] > max_nodes:
                return None
            if check_game_dir(sub):
                return sub
            if level < 2:
                hit = walk(sub, level + 1)
                if hit:
                    return hit
        return None

    return walk(path, 0)


def rescan_candidates(deep=True):
    """用户在弹窗里点「重新扫描」时调用。"""
    with STATE.lock:
        _CAND_CACHE["items"] = []
        _CAND_CACHE["time"] = 0.0
    items = find_candidates(deep=deep, force=True)
    # 记住这次（更深）的结果，保证紧接着的 /api/config 用的是新列表
    _CAND_CACHE.update({"time": time.time(), "items": items, "deep": deep})
    return {"candidates": items, "count": len(items)}


def _warm_candidates():
    """启动时后台预热候选目录：先快扫一遍，再深扫一遍。

    这样第一次开设置弹窗马上就有列表（快扫结果），
    稍后自动补上藏在深处的那几份；用户也可以随时点「重新扫描」。
    """
    try:
        find_candidates(deep=False, force=True)
        time.sleep(0.5)
        rescan_candidates(deep=True)
    except Exception:
        pass


def _bg_refresh_candidates():
    """后台刷新候选缓存（不清空，先让接口用旧结果秒回）。"""
    def work():
        try:
            time.sleep(0.2)
            items = find_candidates(deep=False, force=True)
            _CAND_CACHE.update({"time": time.time(), "items": items,
                                "deep": False})
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()


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


def _durable_copy(src, dst):
    """原子 + 落盘（写临时文件 → fsync → 替换）。

    比 :func:`_atomic_copy` 多一次 fsync：**还原游戏文件**时用它。
    否则断电/崩溃可能留下一个内容还没真正落盘的文件，
    而用户以为「已经还原成功了」。
    """
    tmp = dst + ".tmp"
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1024 * 1024)
        fo.flush()
        os.fsync(fo.fileno())
    os.replace(tmp, dst)
    return dst


def _write_durable(path, blob):
    """把 bytes 原子 + 落盘地写到 ``path``（写临时文件 → fsync → 替换）。"""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def _backup_problem(path, kind="archive"):
    """检查一个备份文件能不能安全还原。

    还原是「回到安全状态」的最后一道手段，如果备份本身是坏的，
    写回去只会把游戏也弄坏 —— 所以宁可拒绝，也不动手。
    返回问题描述字符串；返回 ``None`` 表示没问题。
    """
    try:
        size = os.path.getsize(path)
    except OSError as ex:
        return "读不到备份文件：%s" % ex
    if size <= 0:
        return "备份文件是空的（0 字节）"
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError as ex:
        return "读不到备份文件：%s" % ex

    if kind == "bgm":
        if head[:4] != b"ZWAV":
            return ("备份不是有效的 thbgm.dat（文件头应为 ZWAV，"
                    "实际是 %r）" % head[:4])
        if size <= 16:
            return "备份只有文件头、没有音频数据（%d 字节）" % size
        return None

    try:
        ar = archive.Archive.from_file(path)
    except Exception as ex:
        return "备份的归档结构已损坏：%s" % ex
    bad = [e for e in ar.entries if e.offset < 0 or e.offset >= size]
    if bad:
        return ("备份里有 %d 个条目的位置超出文件范围（如 %s），"
                "备份本身不完整" % (len(bad), bad[0].name))
    # 抽查最小的几个条目，确认数据真的能解出来
    for e in sorted(ar.entries, key=lambda x: x.zsize)[:5]:
        try:
            ar.read_by_name(e.name)
        except Exception as ex:
            return "备份里的 %s 读不出来：%s" % (e.name, ex)
    return None


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
        self.bgm_loop = {}        # index -> 新的循环点（字节，保存时做音频拼接）
        self.bgm_replaced = set()
        self.bgm_orig_loop = {}   # index -> 改动前的循环点（秒），仅用于界面显示
        self.bgm_orig_len = {}    # index -> 改动前的曲长（秒），仅用于界面显示
        self.bgm_origins = {}     # index -> 未拼接的原始 PCM 暂存路径
        self.bgm_error = {}       # key -> 上次读取失败的原因
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
            self.bgm_error = {}

    # -- BGM ---------------------------------------------------------
    def bgm_fmt(self):
        """读取 thbgm.fmt。

        游戏数据损坏时（例如归档被写坏、条目被截断）这里会抛错。错误会被
        记住，避免每次请求都去做一遍昂贵的失败读取；换目录时会清空。
        """
        if self.fmt is None:
            games = self.available_games()
            if not games:
                raise ApiError("游戏目录里没有 th12.dat / th12c.dat")
            key = games[0]
            if key in self.bgm_error:
                raise ApiError(self.bgm_error[key])
            try:
                data = self.archive(key).read_by_name("thbgm.fmt")
                self.fmt = bgm.BgmFmt.from_bytes(data)
            except ApiError:
                raise
            except Exception as ex:
                msg = ("读不出 thbgm.fmt（音乐数据可能已损坏）：%s\n"
                       "贴图 / 对话仍然可以正常修改，只是音乐页用不了。"
                       % ex)
                self.bgm_error[key] = msg
                raise ApiError(msg)
        return self.fmt

    def bgm_status(self):
        """音乐信息（失败时返回错误文本，不抛异常），供 /api/state 使用。"""
        try:
            return list_bgm(), None
        except Exception as ex:
            return None, str(ex)

    # -- 任务 --------------------------------------------------------
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
        # 同一条目只保留最后一次修改 —— 被顶掉的那份暂存文件必须**删掉**。
        # 以前只从列表里剔除、文件留着，而这些文件从此没有任何引用，
        # clear_pending() 也遍历不到，于是暂存目录越积越大
        # （实测攒到过 24 个孤儿分片、55 MB）。
        for old in STATE.pending:
            if old["game"] == key and old["name"] == name:
                old_staged = old.get("staged")
                if old_staged and old_staged != staged:
                    try:
                        os.remove(old_staged)
                    except OSError:
                        pass
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
    for idx in sorted(set(bgm_replace) | set(bgm_loop)):
        name = fmt.tracks[idx].name if fmt else ("曲目 %d" % idx)
        loop = bgm_loop.get(idx)
        parts = []
        if idx in bgm_replace:
            parts.append("整个曲目已替换")
        if loop is not None:
            body = None
            if fmt:
                pend_len = _pending_pcm_len(idx)
                total = pend_len if pend_len else fmt.tracks[idx].end
                body = (total - loop) / float(bgm.BYTES_PER_SEC)
            text = "循环点 %.2f 秒" % (loop / float(bgm.BYTES_PER_SEC))
            if body is not None:
                text += "（保存时按此拼接音频，循环体 %.2f 秒）" % body
            parts.append(text)
        out.append({"kind": "bgm", "game": "bgm", "name": name,
                    "action": "替换BGM" if idx in bgm_replace else "循环点",
                    "detail": "；".join(parts),
                    "size_text": "", "time": ""})
    return {"items": out, "count": len(out)}


def _drop_bgm_origins(keep=False):
    """清掉「未拼接原始音频」的记录（调用方需已持有 STATE.lock）。

    :param keep: True 表示**什么都别清** —— 文件与记录都保留。保存成功后
        必须这样：那些文件是「未经拼接的原始音频」，下次再改循环点还要以
        它们为准，删了或忘了记录就会退化成「从已拼接的结果再拼一次」，
        音频会越拼越长（这里踩过一次：只护住了删文件，忘了护住清记录）。
    """
    if keep:
        return
    for path in STATE.bgm_origins.values():
        try:
            os.remove(path)
        except OSError:
            pass
    STATE.bgm_origins = {}


def clear_pending(keep_bgm_origins=False):
    """清空待保存项。

    :param keep_bgm_origins: 保存成功后传 True，保留拼接用的原始音频。
    """
    with STATE.lock:
        for it in STATE.pending:
            try:
                os.remove(it["staged"])
            except OSError:
                pass
        STATE.pending = []
        _drop_bgm_origins(keep=keep_bgm_origins)
        for path in STATE.bgm_pending.values():
            try:
                os.remove(path)
            except OSError:
                pass
        STATE.bgm_pending = {}
        STATE.bgm_loop = {}
        STATE.bgm_replaced = set()
        STATE.bgm_orig_loop = {}
        STATE.bgm_orig_len = {}
    return {"ok": True}


def clean_staging_orphans():
    """启动时清掉暂存目录里没人引用的分片。

    暂存是「这次会话还没保存的改动」，进程重启后 STATE.pending 一律为空，
    所以磁盘上遗留的 pending_*.bin / track_*.pcm / src_*.pcm 全都是孤儿 ——
    留着只会白占空间（实测攒到过 55 MB；src_*.pcm 是整条曲子的 PCM，
    单条可能十几 MB，更需要清）。
    返回 (删除文件数, 释放字节数)。
    """
    removed = 0
    freed = 0
    if not os.path.isdir(STAGING_DIR):
        return removed, freed
    try:
        names = os.listdir(STAGING_DIR)
    except OSError:
        return removed, freed
    for name in names:
        if not (name.startswith("pending_") or name.startswith("track_")
                or name.startswith("src_")):
            continue
        path = os.path.join(STAGING_DIR, name)
        try:
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            os.remove(path)
            removed += 1
            freed += size
        except OSError:
            continue
    return removed, freed


def _copy_range(src_path, offset, length, dst_path, chunk=1 << 20):
    """把 src_path 的 [offset, offset+length) 字节复制到 dst_path。

    分块复制，避免为了暂存一条曲子把整段读进内存（原曲单条可到 40 MB）。
    """
    remaining = length
    with open(src_path, "rb") as fin, open(dst_path, "wb") as fout:
        fin.seek(offset)
        while remaining > 0:
            data = fin.read(min(chunk, remaining))
            if not data:
                raise OSError("源文件在偏移 %d 处提前结束" % (offset + length
                                                             - remaining))
            fout.write(data)
            remaining -= len(data)


def _ensure_bgm_origins(fmt, bgm_loop, bgm_pending):
    """确保每个「要拼接」的轨道在暂存里都有一份未拼接的原始音频。

    为什么需要：拼接是对源音频做的。如果第二次改循环点时源已经是上一次
    拼接的结果（引子多了一份），就会越拼越长、内容也错。
    所以第一次给某条轨道设循环点时，先把它当时的音频原样存一份到暂存，
    之后每次保存都以这份为准，无论改多少次循环点结果都正确。

    有暂存替换时，替换后的新音频本身就是「原始音频」，直接用它。
    """
    need = set(bgm_loop) - set(STATE.bgm_origins)
    if not need:
        return dict(STATE.bgm_origins)
    origins = dict(STATE.bgm_origins)
    for idx in sorted(need):
        if idx < 0 or idx >= len(fmt.tracks):
            continue
        track = fmt.tracks[idx]
        dst = os.path.join(STAGING_DIR, "src_%02d.pcm" % idx)
        src_file = bgm_pending.get(idx)
        try:
            if src_file and os.path.isfile(src_file):
                shutil.copyfile(src_file, dst)
            else:
                _copy_range(BGM_DAT, track.offset, track.end, dst)
        except OSError as ex:
            log("BGM 暂存原始音频失败", track.name, str(ex))
            continue
        origins[idx] = dst
        STATE.bgm_origins[idx] = dst
        log("暂存BGM原始音频", track.name,
            "%s（用于反复修改循环点时保持结果正确）"
            % human_size(os.path.getsize(dst)))
    return origins


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

    # 本次要处理的版本列表：先快照，避免后面边改状态边遍历
    games = STATE.available_games()

    # ---- 1) BGM：重建 thbgm.dat 到临时文件（先不替换）----
    temp_bgm = None
    fmt_bytes = None
    if bgm_pending or bgm_loop:
        fmt = STATE.bgm_fmt()
        # 不用在这里写 fmt.tracks[idx].loop：rebuild_bgm_dat 会按 loops
        # 设置它（并保证与拼接后的音频一致）。这里只负责重建。
        origins = _ensure_bgm_origins(fmt, bgm_loop, bgm_pending)
        temp_bgm = BGM_DAT + ".new"
        total_tracks = len(fmt.tracks)
        tok = PROGRESS.start("正在重建 thbgm.dat（约 400MB）",
                             total_tracks)
        try:
            # loops 才是真正驱动循环的：列在里面的轨道会被拼成
            # 「引子 + 循环体」。只改 fmt 的 loop 字段没用（引擎不读）。
            bgm.rebuild_bgm_dat(
                BGM_DAT, temp_bgm, bgm_pending, fmt, loops=bgm_loop,
                origins=origins,
                progress=lambda done, total: PROGRESS.update(
                    current=done, total=total, token=tok,
                    detail=fmt.tracks[min(done, total - 1)].name
                    if total else ""))
        except Exception as ex:
            PROGRESS.done(token=tok)
            result["errors"].append("BGM: %s" % ex)
            raise
        fmt_bytes = fmt.to_bytes()

    # ---- 2) 归档：把改动打包到内存 / 临时文件（也先不替换）----
    by_game = {}
    for it in pending:
        by_game.setdefault(it["game"], []).append(it)
    if fmt_bytes:
        for key in games:
            by_game.setdefault(key, [])

    archives = {}          # key -> {"blob":..., "tmp":..., "path":...}
    arch_items = {}        # key -> 已成功打包的条目（用于写日志）
    for key, items in by_game.items():
        a = STATE.archive(key)
        replacements = {}
        tok = PROGRESS.start("正在写入 %s" % GAMES[key]["dat"],
                             len(items) + 1)
        for it in items:
            try:
                with open(it["staged"], "rb") as f:
                    replacements[it["name"]] = f.read()
            except OSError as ex:
                result["errors"].append("%s: %s" % (it["name"], ex))
            PROGRESS.step(it["name"], token=tok)
        if fmt_bytes:
            replacements["thbgm.fmt"] = fmt_bytes
        if not replacements:
            PROGRESS.done(token=tok)
            continue
        # 写盘前自检：ANM 结构必须合法
        bad = []
        for name, blob in list(replacements.items()):
            if name.lower().endswith(".anm"):
                problems = anm.AnmFile.from_bytes(blob).validate()
                if problems:
                    bad.append("%s: %s" % (name, "；".join(problems[:2])))
                    del replacements[name]
        if not replacements:
            PROGRESS.done(token=tok)
            continue
        idx_map = {}
        for name, blob in replacements.items():
            i = a.index_of(name)
            if i >= 0:
                idx_map[i] = blob
        PROGRESS.update(detail="正在重打包归档 %s …" % GAMES[key]["dat"],
                        token=tok)
        try:
            blob = a.to_bytes_patched(idx_map)
        except Exception:
            PROGRESS.done(token=tok)
            raise
        archives[key] = {"blob": blob,
                         "tmp": STATE.archive_path(key) + ".new",
                         "path": STATE.archive_path(key)}
        arch_items[key] = items
        result["errors"].extend(bad)
        PROGRESS.done(token=tok)

    # ---- 3) 两阶段提交：先把所有临时文件落盘，成功后才逐个替换 ----
    # 这一步的顺序很关键。以前是「先替换 thbgm.dat，再去写归档里的 thbgm.fmt」，
    # 第二步失败就留下【新音乐 + 旧偏移表】的组合，游戏里音乐直接错位。
    # 现在所有内容先写成临时文件并落盘，确认全部成功再统一替换。
    temps = []
    try:
        if temp_bgm and os.path.isfile(temp_bgm):
            temps.append(temp_bgm)
        for key, item in archives.items():
            _write_durable(item["tmp"], item["blob"])
            temps.append(item["tmp"])

        # 备份必须在替换之前做完（备份的是"当前"内容）
        if temp_bgm:
            ensure_backup(BGM_DAT, comment)
        for key, item in archives.items():
            ensure_backup(item["path"], comment)

        if temp_bgm:
            os.replace(temp_bgm, BGM_DAT)
            result["bgm"] = True
        for key, item in archives.items():
            os.replace(item["tmp"], item["path"])
    except PermissionError:
        for tmp in temps:
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise ApiError("文件被占用：请先关闭游戏（th12.exe / th12c.exe）")
    except Exception:
        # 失败就把临时文件清掉；此时还没有替换任何游戏文件，
        # 最多是"先替换的那几个"已经生效（os.replace 本身是原子的）
        for tmp in temps:
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        raise

    # ---- 4) 提交成功后再更新内存状态、写日志 ----
    if result["bgm"]:
        log("保存BGM修改", "%d 首替换 / %d 首循环点"
            % (len(bgm_pending), len(bgm_loop)),
            comment or human_size(os.path.getsize(BGM_DAT)))
    # 内存缓存必须整体丢掉，而且【不能】放在下面的循环里 ——
    # 以前 STATE.fmt 的清理写在 for archives 循环内部，只改 BGM 循环点、
    # 不改任何归档条目的那一次保存不会进循环……但 thbgm.fmt 其实也是
    # 归档条目（被算进了 archives），所以之前"侥幸正确"。真正的原因：
    # save_all 前半段直接改了 STATE.bgm_fmt() 返回对象的 loop 字段，
    # 缓存和磁盘短暂不一致；只要以后有人改这里的分支，就会变成
    # "第一次生效、后续写回旧值"。放在循环外，语义才明确。
    with STATE.lock:
        # 只清 anm_cache/texture_index 不够：STATE.archives 里缓存的
        # Archive 对象还持有替换前的条目表，下一次读（例如
        # /api/musiccmt）会从它读出旧内容，而磁盘其实已经是新的。
        for key in archives:
            STATE.archives.pop(key, None)
        STATE.fmt = None
        STATE.anm_cache = {}
        STATE.texture_index = {}

    for key, item in archives.items():
        items = arch_items.get(key, [])
        result["files"] += len(items)
        for it in items:
            log(it["action"], "%s / %s" % (GAMES[key]["label"], it["name"]),
                (it["detail"] + ("　备注: " + comment if comment else "")))

    # ---- 5) 清空暂存 ----
    # 保留 bgm 的「未拼接原始音频」：下次再改同一条曲子的循环点时，
    # 必须仍然以原始音频为源来拼，否则会叠加成越来越长的音频。
    clear_pending(keep_bgm_origins=True)
    PROGRESS.done()
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


def _pending_pcm_len(index):
    """取暂存的替换 PCM 长度；没有暂存则返回 None。

    `/api/bgm` 必须用它来报「替换后的真实长度」。否则替换完一首更短的曲子后，
    界面显示的仍是原曲时长，用户按旧时长设循环点，保存时又按新长度校验，
    很容易设成非法值被归零 —— 表现就是「换了 BGM 后循环点改不了」。
    """
    path = STATE.bgm_pending.get(index)
    if not path:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    return size if size > 0 else None


def list_bgm():
    fmt = STATE.bgm_fmt()
    tracks = []
    for t in fmt.tracks:
        pend_len = _pending_pcm_len(t.index)
        # 「源长度」= 下次拼接会用的音频长度（暂存替换 > 暂存原始音频 > 盘上）。
        # 界面上的循环点上限、时长都必须按它算，不能用盘上轨道长度 ——
        # 保存过一次之后盘上是「循环体×2」，会更短，用户就再也设不了
        # 更长的循环点了（实测踩到过）。
        src = _bgm_source_len(t.index, t)
        eff_bytes = t.avg_bytes or bgm.BYTES_PER_SEC
        # 保存时会把「循环点之后的部分」重复两遍作为整条轨道
        # （见 thtk/bgm.py 的 splice_loop_pcm），所以保存后长度 = 2 × 循环体，
        # 循环体 = 源长度 − 循环点。
        loop = STATE.bgm_loop.get(t.index)   # 只有用户设过才在字典里
        body = (src - loop) if loop else src
        spliced = 2 * body if loop else src
        tracks.append({
            "index": t.index,
            "name": t.name,
            "duration": src / float(eff_bytes) if eff_bytes else 0.0,
            # 保存后会变成的长度（= 2 × 循环体）
            "duration_after": spliced / float(eff_bytes) if eff_bytes else 0.0,
            "loop": t.loop,
            "loop_seconds": t.loop / float(eff_bytes) if eff_bytes else 0.0,
            "size": src,
            "orig_size": t.end,
            "pending_size": pend_len,
            "pending": t.index in STATE.bgm_replaced,
            "pending_loop": loop,
            "pending_loop_seconds": (loop / float(eff_bytes))
                                    if (loop and eff_bytes) else 0.0,
            # 改动前的原值，界面用来显示「原 X.XX 秒」
            "orig_loop_seconds": STATE.bgm_orig_loop.get(t.index),
            "orig_duration_seconds": STATE.bgm_orig_len.get(t.index),
            "loop_body_seconds": ((src - loop) / float(eff_bytes))
                                 if (loop and eff_bytes) else None,
            # 循环点上限：必须小于「源音频长度」（不是盘上轨道长度）
            "max_loop_seconds": (src / float(eff_bytes)) if eff_bytes
                                else 0.0,
        })
    return {"tracks": tracks,
            "pending_count": len(STATE.bgm_replaced) + len(STATE.bgm_loop),
            "dat_size": os.path.getsize(BGM_DAT) if os.path.exists(BGM_DAT) else 0}


def bgm_wav(index):
    """导出某条曲目的 WAV。

    注意：`thbgm.dat` 里存的是**裸 PCM**（没有 WAV 头），
    暂存的替换文件也是裸 PCM。以前这条分支直接把裸 PCM 当返回值发出去，
    于是「替换后导出 WAV」拿到的其实不是合法 WAV（缺 44 字节头）。
    """
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


# 波形包络缓存：{(索引, 文件mtime, 长度, 点数): peaks}
_PEAK_CACHE = {}
_PEAK_CACHE_MAX = 24
_PEAK_WARMING = set()


def bgm_peaks(index, points=900):
    """计算某条曲目的波形包络（给界面画波形用）。

    只读出 PCM 的**峰值**，不做解码：每 ``block`` 个采样取一次最大绝对值，
    所以哪怕是一首 12 MB 的曲子也能很快算完（纯 Python 逐字节，几万次循环）。
    结果按「索引 + 文件 mtime + 长度 + 点数」缓存，重复开同一首不再重算。
    """
    fmt = STATE.bgm_fmt()
    if index < 0 or index >= len(fmt.tracks):
        raise ApiError("曲目序号不存在", 404)
    points = max(60, min(int(points), 4000))
    t = fmt.tracks[index]
    path = STATE.bgm_pending.get(index)
    if not path:
        path = None                                    # 走 thbgm.dat
    try:
        mtime = os.path.getmtime(path or BGM_DAT)
    except OSError:
        mtime = 0
    length = _pending_pcm_len(index) or t.end
    key = (index, mtime, length, points)
    cached = _PEAK_CACHE.get(key)
    if cached:
        return cached

    step = max(1, length // points)
    n_chunks = max(1, (length + step - 1) // step)
    peaks = []
    left = length
    src = open(path, "rb") if path else open(BGM_DAT, "rb")
    try:
        if not path:
            src.seek(t.offset)
        read = src.read
        for _ in range(n_chunks):
            if left <= 0:
                break
            chunk = read(min(step, left))
            if not chunk:
                break
            # 16bit 小端立体声，每 4 字节一帧（L低 L高 R低 R高）。
            # 必须按 16bit 有符号**完整解析**，不能图快只取高位字节：
            # 单个字节 0x48=72，而它其实是 0x4848=18504 的低字节，
            # 只取字节会把振幅算错（实测算出 83558，实际峰值 30178）。
            arr = array.array("h")
            arr.frombytes(chunk[:len(chunk) // 2 * 2])
            if arr:
                peak = max(abs(min(arr)), abs(max(arr)))
            else:
                peak = 0
            peaks.append(peak)
            left -= len(chunk)
    finally:
        src.close()

    if len(_PEAK_CACHE) >= _PEAK_CACHE_MAX:
        _PEAK_CACHE.clear()
    result = {
        "index": index,
        "name": t.name,
        "points": len(peaks),
        # 归一化到 0..100，前端直接按高度画；原始峰值为 16bit 有符号
        "peaks": [min(100, int(p * 100 / 32768)) for p in peaks],
        "seconds": length / float(bgm.BYTES_PER_SEC) if bgm.BYTES_PER_SEC else 0,
    }
    _PEAK_CACHE[key] = result
    return result


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
        # 记下替换前的原始循环点，界面要显示「原 X.XX 秒」做对比
        STATE.bgm_orig_loop.setdefault(
            index, fmt.tracks[index].loop / float(bgm.BYTES_PER_SEC))
        # 换了音频，之前留的「未拼接原始音频」就过期了 —— 丢掉，
        # 让下次保存时以这份新音频为拼接源重新存一份。
        stale = STATE.bgm_origins.pop(index, None)
        if stale:
            try:
                os.remove(stale)
            except OSError:
                pass
    log("替换BGM（暂存）", fmt.tracks[index].name,
        "%.1f 秒 / %s" % (len(pcm) / float(bgm.BYTES_PER_SEC),
                          human_size(len(pcm))))
    return {"ok": True, "seconds": len(pcm) / float(bgm.BYTES_PER_SEC)}


def _bgm_source_len(index, track):
    """这条曲目**下一次保存时会用来拼接的源音频长度**（字节）。

    这是个容易搞错的概念，单独抽出来：
      · 有暂存替换     → 新音频（替换完还没保存）
      · 有暂存的原始音频 → 那个文件（改循环点用的源）
      · 都没有         → 盘上轨道当前长度

    为什么必须区分：新设计下盘上的轨道内容是「循环体重复两遍」，
    长度 = 2 × 循环体，**不再是拼接源**。如果拿盘上长度当校验基准，
    保存过一次循环点之后就再也设不了更长的循环点了 —— 实测踩到过：
    源 70 秒、设循环点 60 秒并保存后盘上只有 20 秒，
    再想设 30 秒会被误报「超出曲长 20 秒」。
    """
    pend = _pending_pcm_len(index)
    if pend:
        return pend
    path = STATE.bgm_origins.get(index)
    if path:
        try:
            size = os.path.getsize(path)
            if size > 0:
                return size
        except OSError:
            pass
    return track.end


def bgm_set_loop(index, loop_bytes):
    """设置循环点。

    循环点是相对轨道起点的字节数，必须落在 [0, 轨道长度) 内 ——
    替换过音频时按新音频算，否则按原曲算。

    重要：这个值**不是**写进 thbgm.fmt 的 loop 字段就算了。th12.exe 的 BGM
    引擎从不读那个字段（只读 begin_pos 与 unknown，然后把整块循环播放），
    所以保存时会把「循环点之后的部分」重复两遍作为整条轨道，详见
    bgm.splice_loop_pcm。因此这里还要保证「循环体」不会短得离谱。
    """
    fmt = STATE.bgm_fmt()
    if index < 0 or index >= len(fmt.tracks):
        raise ApiError("曲目序号不存在", 404)
    track = fmt.tracks[index]
    loop_bytes = max(0, int(loop_bytes))
    loop_bytes -= loop_bytes % 4
    # 校验基准 = 下一次拼接会用的源音频长度。
    # 注意不能用 track.end：保存过一次之后盘上轨道是「循环体×2」，会更短。
    limit = _bgm_source_len(index, track)
    if limit <= 0:
        raise ApiError("这条曲目没有音频数据，无法设置循环点")
    if loop_bytes >= limit:
        raise ApiError(
            "循环点 %.2f 秒超出了这条曲目的长度 %.2f 秒。"
            "循环点必须小于曲长（它后面那一段才是要反复播放的部分）。"
            % (loop_bytes / float(bgm.BYTES_PER_SEC),
               limit / float(bgm.BYTES_PER_SEC)))
    body = limit - loop_bytes
    if loop_bytes > 0 and body < bgm.MIN_LOOP_SECONDS * bgm.BYTES_PER_SEC:
        raise ApiError(
            "循环体只剩 %.2f 秒，太短了（至少 %.1f 秒）。"
            "请把循环点往前挪。"
            % (body / float(bgm.BYTES_PER_SEC), bgm.MIN_LOOP_SECONDS))
    with STATE.lock:
        STATE.bgm_loop[index] = loop_bytes
        # 记下原始值（第一次设定时），界面显示「原 X.XX 秒 / 原长 Y.YY 秒」
        STATE.bgm_orig_loop.setdefault(
            index, track.loop / float(bgm.BYTES_PER_SEC))
        STATE.bgm_orig_len.setdefault(
            index, track.end / float(bgm.BYTES_PER_SEC))
    log("设置BGM循环点", track.name,
        "%.2f 秒（保存时会拼接音频实现真正的循环）"
        % (loop_bytes / float(bgm.BYTES_PER_SEC)))
    return {"ok": True, "loop": loop_bytes,
            "length": limit,
            "max_loop_seconds": limit / float(bgm.BYTES_PER_SEC),
            "loop_body_seconds": body / float(bgm.BYTES_PER_SEC)}


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
        STATE.bgm_orig_loop = {}
        STATE.bgm_orig_len = {}
        _drop_bgm_origins()
    if n:
        log("放弃BGM修改", "%d 项" % n)
    return {"ok": True}


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
RUNNING_HINT = ("检测到游戏正在运行：%s\n"
                "请先完全退出游戏再写入！\n"
                "游戏运行时它的数据文件可能允许被写入，硬写会导致"
                "【游戏文件损坏】（而不是简单的“文件被占用”）。\n"
                "改完后再打开游戏即可看到效果。")


def running_game_processes():
    """返回当前正在运行的游戏进程名列表（空列表表示没在运行）。

    这是写盘前的**预检查**：游戏运行时数据文件往往仍可写，
    靠写入报错来判断已经太晚——硬写可能直接写坏游戏文件。
    """
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, timeout=10)
    except Exception:
        return []
    try:
        text = (out.stdout or b"").decode("gbk", "replace")
    except Exception:
        text = (out.stdout or b"").decode("utf-8", "replace")
    found = []
    for line in text.splitlines():
        line = line.strip().strip('"')
        if not line:
            continue
        name = line.split('","')[0].strip('"').lower()
        if name in ("th12.exe", "th12c.exe", "custom.exe", "custom_cn.exe"):
            if name not in found:
                found.append(name)
    return found


def ensure_game_closed():
    """写游戏文件之前调用：游戏在运行就直接拒绝，避免写坏。"""
    running = running_game_processes()
    if running:
        raise ApiError(RUNNING_HINT % "、".join(running))


# 用户可能直接把游戏数据文件拖/填进来，这里统一转成「目录」
GAME_FILES = {}
for _k, _info in GAMES.items():
    GAME_FILES[_info["dat"].lower()] = _k
# 顺带接受这些常见的同目录文件（避免用户指错文件就报错）
EXTRA_FILES = ("thbgm.dat", "th12.exe", "th12c.exe", "custom.exe", "custom_cn.exe")


def resolve_game_input(raw):
    """把用户给的东西解析成游戏目录。

    既支持「填目录」，也支持「填/选一个文件」——
    很多人分不清该选哪一层，直接选 th12c.dat 反而最不容易错。

    返回 (目录, 错误信息, 提示信息)。目录为 None 表示无法解析。
    """
    p = normalize_input_path(raw)
    if not p:
        return None, "请填写游戏目录，或者直接选/填 th12.dat、th12c.dat 文件", None

    if os.path.isdir(p):
        return p, None, None

    if os.path.exists(p):                   # 存在但不是目录 → 当文件处理
        name = os.path.basename(p).lower()
        if name not in GAME_FILES and name not in EXTRA_FILES:
            return None, ("这个文件不是 TH12 的游戏数据：%s\n"
                          "请选 th12.dat（日文版）或 th12c.dat（汉化版），"
                          "或者选它们所在的文件夹" % os.path.basename(p)), None
        folder = os.path.dirname(p) or p
        if check_game_dir(folder):
            return folder, None, "已根据文件定位到目录：%s" % folder
        return None, ("%s 所在的目录里没有 th12.dat / th12c.dat：\n%s\n"
                      "可能选错了文件，或者游戏数据不完整"
                      % (os.path.basename(p), folder)), None

    return None, "目录或文件不存在：%s\n检查一下有没有打错，或者用「浏览…」选" % p, None


def config_info():
    """设置弹窗需要的全部信息（当前目录、可用版本、exe、候选目录）。"""
    games = STATE.available_games()
    exes = {}
    for key in games:
        exe = os.path.join(GAME_DIR, "th12.exe" if key == "jp"
                           else "th12c.exe")
        exes[key] = os.path.basename(exe) if os.path.isfile(exe) else None
    cfg = load_config()
    problems, notes = diagnose_game_dir(GAME_DIR, games) if games else ([], [])
    return {
        "game_dir": GAME_DIR,
        "dir_exists": os.path.isdir(GAME_DIR),
        "games": [{"key": k, "label": GAMES[k]["label"]} for k in games],
        "exes": exes,
        "candidates": find_candidates(),
        "config_path": CONFIG_PATH,
        "problems": problems,
        "notes": notes,
        "recent_dirs": [p for p in (cfg.get("recent_dirs") or [])
                        if os.path.isdir(p)],
    }


def diagnose_game_dir(path, keys):
    """体检一个游戏目录：归档数据能不能真的读出来。

    要点：`size` 是解压后的大小，数据在文件里只占 `zsize`，所以
    「offset+size 超出文件」并不代表损坏（压缩数据没占那么多）。
    只有**真的读失败**（数据被截断 / 解压报错）才算坏。

    为了快，只抽查几个小条目 + 当前要用到的 thbgm.fmt，不整包解压。

    返回 ``(问题列表, 提示列表)``：问题是「不能用/有风险」，
    提示是「能用，但你最好知道」。两者分开，因为界面上前者要标红、
    后者只是说明 —— 例如条目表长度与数据流不一致，官方工具也是
    「解出多少算多少」，不该把整份归档判成坏的。
    """
    problems = []
    notes = []
    for key in keys:
        dat = os.path.join(path, GAMES[key]["dat"])
        label = GAMES[key]["label"]
        try:
            size = os.path.getsize(dat)
        except OSError as ex:
            problems.append("%s 读不到：%s" % (label, ex))
            continue
        try:
            ar = archive.Archive.from_file(dat)
        except Exception as ex:
            problems.append("%s 归档解析失败：%s" % (label, ex))
            continue

        # ① offset 本身就落在文件外的，铁定坏了（不用读就知道）
        beyond = [e for e in ar.entries if e.offset >= size]
        if beyond:
            names = "、".join(e.name for e in beyond[:3])
            problems.append("%s 有 %d 个条目的位置超出文件末尾（%s …），"
                            "归档像是写到一半中断了"
                            % (label, len(beyond), names))
            continue

        # ② 抽查：thbgm.fmt（音乐要用）+ 最小的几个条目，真正读一次
        picked = [e for e in ar.entries if e.name == "thbgm.fmt"]
        small = sorted((e for e in ar.entries if e.name != "thbgm.fmt"),
                       key=lambda e: e.zsize)[:5]
        for e in picked + small:
            try:
                ar.read_by_name(e.name)
            except Exception as ex:
                problems.append("%s 里的 %s 读不出来：%s\n"
                                "（归档可能被写坏，建议用备份文件还原）"
                                % (label, e.name, ex))
                break

        # ③ 条目表长度与数据流不一致：只提示，不拦（见 docstring）
        partial = getattr(ar, "partial_entries", None) or []
        if partial:
            sample = "、".join(p[0] for p in partial[:3])
            notes.append(
                "%s 有 %d 个条目的长度与归档记录不一致（如 %s …）："
                "这些文件导出后可能不完整，但贴图/对话仍可正常使用"
                % (label, len(partial), sample))
    return problems, notes


def check_path_info(path):
    """检查一个路径能不能当游戏目录，并给出可操作的提示（供前端实时校验）。

    既接受目录，也接受 th12.dat / th12c.dat 文件。
    """
    raw = normalize_input_path(path)
    info = {
        "input": path,
        "path": raw,
        "ok": False,
        "games": [],
        "exists": False,
        "suggest": None,
        "problems": [],
        "message": "",
    }
    if not raw:
        info["message"] = ("请填游戏目录，或者直接选 / 填 th12.dat、"
                           "th12c.dat 文件（也可以用「浏览…」）")
        return info

    resolved, err, note = resolve_game_input(raw)
    if err:
        info["message"] = err
        info["exists"] = os.path.exists(raw)
        return info
    if resolved != raw:
        info["from_file"] = True
    found = check_game_dir(resolved)
    if not found:
        # 指到了「装着游戏的那一层」：自动往下找一层。
        # 这里仍然给出 suggest，让界面能提示用户实际用的是哪个目录。
        sub = find_game_subdir(resolved)
        if sub:
            games = check_game_dir(sub)
            info.update({"suggest": sub, "games": games, "exists": True,
                         "message": ("这个目录本身没有 th12.dat / th12c.dat，"
                                     "但里面有一份游戏：\n%s" % sub)})
            info["problems"], info["notes"] = diagnose_game_dir(sub, games)
            return info
        info["path"] = resolved
        info["exists"] = True
        info["message"] = ("这个目录里没有 th12.dat 或 th12c.dat。\n"
                           "请选「里面直接放着 th12.dat / th12c.dat」"
                           "的那个文件夹（不是它的上一级），"
                           "或者直接选那个 .dat 文件。")
        return info

    if resolved != raw:
        # 输入的是文件，已定位到它所在目录
        info["path"] = resolved
    info["exists"] = True
    info.update({"ok": True, "games": found,
                 "message": "可用版本：%s" % " / ".join(
                     GAMES[k]["label"] for k in found)})
    if len(found) > 1:
        info["message"] += "；日文版/汉化版共用 thbgm.dat"
    if note:
        # 例如「已根据文件定位到目录：…」，另起一行，别和上面的状态挤在一起
        info["message"] += "\n" + note
    info["problems"], info["notes"] = diagnose_game_dir(resolved, found)
    if info["problems"]:
        info["message"] += "\n⚠ " + info["problems"][0].splitlines()[0]
    elif info["notes"]:
        # 能用，但有需要知情的地方（例如条目长度与记录不一致）
        info["message"] += "\n注：" + info["notes"][0].splitlines()[0]
    return info


def _remember_dir(path):
    """记住用过的目录，方便下次直接在候选里点。"""
    cfg = load_config()
    recent = [p for p in (cfg.get("recent_dirs") or [])
              if os.path.isdir(p) and os.path.abspath(p) != os.path.abspath(path)]
    recent.insert(0, os.path.abspath(path))
    cfg["recent_dirs"] = recent[:8]
    save_config(cfg)


def set_config(payload):
    """设置游戏目录。可以给目录，也可以直接给 th12.dat / th12c.dat 文件。"""
    path = normalize_input_path(payload.get("game_dir"))
    if not path:
        raise ApiError("请填写游戏目录，或者直接选 / 填 th12.dat、th12c.dat 文件")

    resolved, err, note = resolve_game_input(path)
    if err:
        raise ApiError(err)
    from_file = resolved != path
    path = resolved

    found = check_game_dir(path)
    suggest = None
    if not found:
        # 允许指到「里面装着游戏的那一层」，但要把真正用的目录回给前端
        suggest = find_game_subdir(path)
        if not suggest:
            raise ApiError(
                "这个目录里没有 th12.dat 或 th12c.dat，不是 TH12 游戏目录。\n"
                "可以选那个文件夹，或者直接选 th12.dat / th12c.dat 文件。")
        found = check_game_dir(suggest)
        path = suggest

    with STATE.lock:
        set_game_dir(path)
        STATE.invalidate()
        STATE.game_key = found[0]
    _remember_dir(path)
    cfg = load_config()
    cfg["game_dir"] = GAME_DIR
    warn = save_config(cfg)
    # 注意：这里【不能】清空候选缓存——那会让本次请求变成 2~3 秒的磁盘全盘扫描。
    # 改成后台悄悄刷新，接口本身毫秒级返回。
    _bg_refresh_candidates()
    log("切换游戏目录", GAME_DIR, "版本: %s" % "/".join(found))
    info = config_info()
    info["used_parent"] = bool(suggest)
    info["from_file"] = from_file
    msgs = []
    if note:
        msgs.append(note)
    if suggest:
        info["suggest"] = suggest
        msgs.append("你选的目录里没有游戏数据，已自动改用里面的：\n%s" % suggest)
    if not info.get("problems") and info.get("notes"):
        # 能用但有需要知情的地方（例如条目长度与归档记录不一致）
        msgs.append("注：" + info["notes"][0])
    if msgs:
        info["message"] = "\n".join(msgs)
    if info.get("problems"):
        info["warning"] = "；".join(p.splitlines()[0] for p in info["problems"])
    if warn:
        info["config_warning"] = ("目录已生效，但配置没能保存，"
                                  "下次启动会恢复成默认：%s" % warn)
    return info


def pick_directory(fallback="", kind="dir"):
    """弹出 Windows 原生选择框（PowerShell，源码运行和打包运行都能用）。

    kind="dir"  → 选文件夹
    kind="file" → 选游戏数据文件（th12c.dat / th12.dat 等）

    打不开对话框时不再「静默返回空」，而是把原因告诉前端，
    并把 fallback（前端当前填的路径）原样带回，让用户继续手动输入。
    """
    if kind == "file":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms\r\n"
            "$d = New-Object System.Windows.Forms.OpenFileDialog\r\n"
            "$d.Title = '选择游戏数据文件（th12.dat 或 th12c.dat）'\r\n"
            "$d.Filter = 'TH12 游戏数据 (*.dat)|*.dat|所有文件 (*.*)|*.*'\r\n"
            "$d.CheckFileExists = $true\r\n"
            "if ($d.ShowDialog() -eq "
            "[System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Out.Write($d.FileName) }\r\n"
        )
        tmp_name = "pickfile.ps1"
    else:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms\r\n"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog\r\n"
            "$d.Description = '选择 TH12 游戏目录（里面有 th12.dat）'\r\n"
            "$d.ShowNewFolderButton = $false\r\n"
            "if ($d.ShowDialog() -eq "
            "[System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Out.Write($d.SelectedPath) }\r\n"
        )
        tmp_name = "pickdir.ps1"
    tmp = os.path.join(STAGING_DIR, tmp_name)
    try:
        if not os.path.isdir(STAGING_DIR):
            os.makedirs(STAGING_DIR)
        with io.open(tmp, "w", encoding="utf-8-sig") as f:
            f.write(script)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
             "-File", tmp],
            capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"path": "", "fallback": fallback,
                "error": "等待选择超时，请直接在输入框里粘贴路径"}
    except Exception as ex:
        return {"path": "", "fallback": fallback,
                "error": "无法打开选择框: %s（可以直接粘贴路径）" % ex}

    # 显式按 UTF-8 解码，避免系统代码页把中文路径解坏；
    # 失败时再退回系统代码页，保证任何环境下都能拿到路径。
    raw = out.stdout or b""
    picked = ""
    for enc in ("utf-8", "gbk", None):
        try:
            picked = raw.decode(enc) if enc else \
                raw.decode(sys.getfilesystemencoding() or "utf-8", "replace")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    picked = picked.strip().strip('"').strip()
    if picked:
        return {"path": picked, "picked": ("file" if kind == "file"
                                           else "dir")}
    err_raw = out.stderr or b""
    detail = err_raw.decode("utf-8", "replace").strip().splitlines()
    reason = detail[-1] if detail else "没有选择"
    return {"path": "", "fallback": fallback,
            "error": "未选择（%s）" % reason}


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
    anm_entries = [e for e in a.entries if e.name.lower().endswith(".anm")]
    tok = PROGRESS.start("正在建立贴图索引", len(anm_entries),
                         "(首次约需 30 秒，已在缓存后为秒级)",
                         background=True)
    for e in anm_entries:
        try:
            f = STATE.anm(key, e.name)
        except Exception:
            PROGRESS.step(e.name, token=tok)
            continue
        for t in f.textures:
            base = t.name.rsplit("/", 1)[-1].lower()
            index.setdefault(base, []).append({
                "anm": e.name, "index": t.entry_index,
                "name": t.name, "format": t.format_name,
                "w": t.width, "h": t.height, "x": t.x, "y": t.y,
            })
        PROGRESS.step(e.name, token=tok)
    PROGRESS.done(token=tok)
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
    """列出归档里所有 .anm。"""
    a = STATE.archive(key)
    out = []
    for e in a.entries:
        if not e.name.lower().endswith(".anm"):
            continue
        out.append({"name": e.name, "size": e.size})
    return {"anms": out}


def _texture_png_bytes(f, texture):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(f.rgba(texture)).save(buf, "PNG")
    return buf.getvalue()


def _composed_rgba(f, group):
    """把同名的一组合成成一张图（各条目按 x/y 摆放）。"""
    import numpy as np
    cw, ch = anm.composed_size(group)
    canvas = np.zeros((ch, cw, 4), dtype=np.uint8)
    for t in group:
        canvas[t.y:t.y + t.height, t.x:t.x + t.width] = f.rgba(t)
    return canvas


def export_textures_zip(key, anm_name):
    """一键导出某个 .anm 的全部贴图。

    文件名与游戏内贴图名一致（如 face02no.png）；
    同名多块会合成成一张图导出，完全重复的名字才加序号。
    """
    import zipfile
    f = STATE.anm(key, anm_name)
    groups = anm.group_by_name(f.textures)
    buf = io.BytesIO()
    used = set()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for full_name, group in groups:
            base = (full_name or "texture").rsplit("/", 1)[-1]
            if base.lower().endswith(".png"):
                base = base[:-4]
            if not base:
                base = "texture"
            if len(group) == 1 and group[0].x == 0 and group[0].y == 0:
                png = _texture_png_bytes(f, group[0])
            else:
                png = _texture_png_bytes_from(f, _composed_rgba(f, group))
            name = base + ".png"
            if name.lower() in used:
                name = "%s_%d.png" % (base, group[0].entry_index)
            used.add(name.lower())
            z.writestr(name, png)
            count += 1
    fname = anm_name.rsplit(".", 1)[0] + ".zip"
    return buf.getvalue(), fname, count


def _texture_png_bytes_from(f, rgba):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, "PNG")
    return buf.getvalue()


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
    tok = PROGRESS.start("正在处理批量导入", len(items))
    for it in items:
        PROGRESS.step(it["file"], token=tok)
    PROGRESS.update(current=0, detail="正在解析文件…", token=tok)
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
    PROGRESS.done(token=tok)
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
    """把备份写回游戏。还原前会先把当前文件另存为 .modtool.prev。

    两道防线（缺一不可）：
    1. **先体检备份**：备份若是空的/截断的/结构坏了就拒绝还原。
       还原是「回到安全状态」的最后手段，写回一个坏备份等于把游戏也弄坏。
    2. **原子 + fsync 写入**：写临时文件、落盘、再替换，不会就地写坏目标文件。
    """
    mapping = {
        "jp": STATE.archive_path("jp"),
        "cn": STATE.archive_path("cn"),
        "bgm": BGM_DAT,
    }
    if which not in mapping:
        raise ApiError("未知备份: %s" % which)
    target = mapping[which]
    kind = "bgm" if which == "bgm" else "archive"
    bak = target + BACKUP_SUFFIX
    if not os.path.exists(bak):
        raise ApiError("备份不存在（%s 还没有生成过备份）"
                       % os.path.basename(target), 404)

    problem = _backup_problem(bak, kind)
    if problem:
        raise ApiError(
            "备份有问题，已取消还原（游戏文件没有被改动）：\n%s\n"
            "备份文件：%s\n"
            "可以试试还原别的备份，或手动把 %s 改为 .bak 后缀。"
            % (problem, os.path.basename(bak), os.path.basename(bak)))

    ensure_game_closed()
    with STATE.lock:
        if os.path.exists(target):
            # 还原前保留当前状态，避免手滑丢失改动
            try:
                _durable_copy(target, target + ".modtool.prev")
            except OSError as ex:
                raise ApiError("无法保存当前文件为 .modtool.prev，已取消还原：%s" % ex)
        try:
            _durable_copy(bak, target)
        except OSError as ex:
            raise ApiError("写入失败：%s" % ex)
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

    def _send_ranged(self, body, content_type, extra_headers=None):
        """发送可能被 Range 请求切片的响应（音频这类大二进制）。

        为什么需要：浏览器要**在音频里跳转**（拖进度条、点波形跳转）
        必须能发 Range 请求拿部分内容。以前不理会 Range、一律返回 200 全量，
        结果是 `audio.currentTime = 33` 被浏览器**静默忽略**
        （实测：readyState=4、无错误，但 currentTime 始终是 0），
        表现就是「点了波形没反应」「试听拖不动进度条」。
        """
        total = len(body)
        rng = (self.headers.get("Range") or "").strip()
        base = {"Accept-Ranges": "bytes"}
        if extra_headers:
            base.update(extra_headers)

        m = re.match(r"bytes=(\d*)-(\d*)$", rng)
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else total - 1
            else:                        # bytes=-N：最后 N 字节
                start = max(0, total - int(m.group(2)))
                end = total - 1
            start = max(0, min(start, max(0, total - 1)))
            end = max(start, min(end, total - 1))
            chunk = body[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Content-Range",
                             "bytes %d-%d/%d" % (start, end, total))
            self.send_header("Cache-Control", "no-store")
            for k, v in base.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionAbortedError):
                pass
            return
        self._send(200, body, content_type, base)

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
            # 音乐信息单独取：数据损坏时降级成警告，不能让整个页面打不开
            bgm_info, bgm_err = STATE.bgm_status()
            return self._json({
                "games": [{"key": k, "label": GAMES[k]["label"]}
                          for k in games],
                "default_game": STATE.game_key,
                "game_dir": GAME_DIR,
                "bgm": bgm_info,
                "bgm_error": bgm_err,
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
        if route == "textures.zip":
            key = q.get("game", "jp")
            anm_name = q.get("anm", "")
            data, fname, count = export_textures_zip(key, anm_name)
            log("导出贴图包", "%s / %s" % (GAMES[key]["label"], anm_name),
                "%d 张贴图" % count)
            return self._send(
                200, data, "application/zip",
                {"Content-Disposition": "attachment; filename*=UTF-8''%s"
                 % urllib.parse.quote(fname)})
        if route == "textures.search":
            return self._json(search_textures(q.get("game", "jp"),
                                              q.get("q", "")))
        if route == "anms":
            return self._json(list_anm_files(q.get("game", "jp")))
        if route == "texture.png":
            data = texture_png(q.get("game", "jp"), q.get("anm", ""),
                               int(q.get("index", "0")))
            return self._send_ranged(data, "image/png")
        if route == "bgm":
            return self._json(list_bgm())
        if route == "bgm.wav":
            # 走 _send_ranged：浏览器要在音频里跳转（拖进度条/点波形），
            # 必须支持 Range 请求，否则 currentTime 赋值会被静默忽略。
            data = bgm_wav(int(q.get("index", "0")))
            return self._send_ranged(data, "audio/wav")
        if route == "bgm.peaks":
            # 波形数据只跟音频内容有关，可以缓存（其余接口都是 no-store）
            res = bgm_peaks(int(q.get("index", "0")),
                            int(q.get("points", "900")))
            return self._send(200, json.dumps(res, ensure_ascii=False),
                              "application/json; charset=utf-8",
                              {"Cache-Control": "private, max-age=600"})
        if route == "msg":
            return self._json(get_msg(q.get("game", "jp"),
                                      q.get("name", "")))
        if route == "msg.doc":
            key = q.get("game", "jp")
            name = q.get("name", "")
            text = msg_document(key, name)
            fname = urllib.parse.quote(
                name.rsplit(".", 1)[0] + ".txt")
            # 带 UTF-8 BOM 导出：Windows 记事本等编辑器靠 BOM 判断编码，
            # 没有 BOM 时「另存为 ANSI」会把译文写成 GBK，
            # 再导入就全是乱码。后端导入用 utf-8-sig，能吃掉这个 BOM。
            data = b"\xef\xbb\xbf" + text.encode("utf-8")
            return self._send(200, data,
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
        if route == "progress":
            return self._json(PROGRESS.get())
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
            return self._json(config_info())
        if route == "check":
            # 实时校验用户在弹窗里填的路径（不写入任何东西）
            return self._json(check_path_info(q.get("path", "")))
        if route == "rescan":
            return self._json(rescan_candidates(deep=True))
        raise ApiError("未知接口: %s" % route, 404)

    # ---- POST -----------------------------------------------------
    # 这些接口会真正写游戏文件：写之前先确认游戏没在运行
    WRITE_ROUTES = ("file", "texture", "msg", "msg.import", "musiccmt",
                    "bgm.apply", "bgm.replace", "save", "restore",
                    "restore.all", "batch.apply", "texture.import")

    def do_POST(self):
        try:
            path, q = self._query()
            if not path.startswith("/api/"):
                raise ApiError("未知接口", 404)
            route = path[5:]
            body = self._read_body()
            if route in self.WRITE_ROUTES:
                ensure_game_closed()
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
                kind = "file" if (q.get("kind") or "").lower() == "file" \
                    else "dir"
                return self._json(pick_directory(q.get("path", ""), kind))
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
    elif cfg.get("game_dir"):
        saved = cfg["game_dir"]
        # 存下来的目录可能只是「装着游戏的那一层」，这里要**真正上浮**到
        # 游戏目录本身。以前判断用了 find_game_subdir 放行，但 set_game_dir
        # 拿到的还是原来那一层，结果就是「启动后显示记住了目录，
        # 但 available_games() 为空、一个版本都没有」。
        resolved, err, _note = resolve_game_input(saved)
        if err or not resolved:
            print("上次的游戏目录已失效，已忽略: %s" % saved)
        elif resolved != saved and check_game_dir(resolved):
            set_game_dir(resolved)
            print("上次记录的是上一层目录，已自动改用: %s" % resolved)
        elif check_game_dir(resolved):
            set_game_dir(resolved)
        else:
            print("上次的游戏目录里没有游戏数据，已忽略: %s" % saved)
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
    # 上次会话遗留的暂存分片（没保存就退出了）已无引用，清掉免得白占空间
    n_orphan, freed = clean_staging_orphans()
    if n_orphan:
        print("已清理上次遗留的暂存文件: %d 个（%s）"
              % (n_orphan, human_size(freed)))
    # 后台预热贴图索引（首次要解压全部 .anm，之后走磁盘缓存）
    threading.Thread(target=warm_up_texture_index, daemon=True).start()
    # 后台预热候选目录扫描，这样第一次打开「设置目录」不用等
    threading.Thread(target=_warm_candidates, daemon=True).start()
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
