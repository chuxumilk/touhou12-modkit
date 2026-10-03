# -*- coding: utf-8 -*-
"""修复后的「设置游戏目录」验收测试。

覆盖：
  1. /api/check 实时校验（可用 / 指到上一级 / 不存在）
  2. /api/config 设置目录（有效路径 / 上一级自动纠正 / 无效报错）
  3. /api/rescan 重新扫描
  4. 设置后 /api/state 与各功能接口是否真的能跑通
  5. 配置持久化（config.json 内容）

用法: python _test/verify_settings.py [port]
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
BASE = "http://127.0.0.1:%d" % PORT
def _pick_dir(candidates, env="TH12_GAME_DIR"):
    """挑一个存在的游戏目录：环境变量 > 候选列表。"""
    envv = os.environ.get(env)
    if envv and os.path.isdir(envv):
        return envv
    for c in candidates:
        if os.path.isdir(c):
            return c
    raise SystemExit(
        "找不到可用的游戏目录。请用环境变量指定，例如：\n"
        "  set %s=D:\\Games\\th12\n"
        "（目录里要有 th12.dat 或 th12c.dat）" % env)


WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(WS, "tools", "modtool", "config.json")
MAIN = _pick_dir([
    os.path.join(WS, "game", "[th12] 东方星莲船 (汉化版+日文版)"),
    os.path.join(WS, "测试", "[th12] 东方星莲船 (汉化版+日文版)"),
])
MOD = os.path.dirname(MAIN)          # 装着游戏的那一层（测「选到上一级」）
PARENT = MOD
# 另一份副本（用于测切换）；没有就退回 MAIN
_test2 = os.path.join(WS, "测试", "[th12] 东方星莲船 (汉化版+日文版)")
TEST = _test2 if os.path.isdir(_test2) else MAIN
ORIG = MOD
# 「自动扫描能否找到游戏目录」只有游戏目录位于扫描根之下时才成立 ——
# 扫描根是工作区各层 + 配置里记过的目录，而自检脚本每次启动服务器都会把
# config.json 清成 {}，所以指向工作区之外的目录（例如自检用的临时副本）
# 时这两项必然失败。那不是缺陷，只是不在扫描范围内，所以显式跳过而不是
# 假装通过；用真实游戏目录跑时会正常覆盖。
_MAIN_IN_WS = os.path.normcase(os.path.normpath(MAIN)).startswith(
    os.path.normcase(os.path.normpath(WS)) + os.sep)
SCANNABLE = _MAIN_IN_WS

PASS, FAIL = [], []


def check(title, cond, detail=""):
    (PASS if cond else FAIL).append(title)
    print("  [%s] %-52s %s" % ("PASS" if cond else "FAIL", title, detail))


def call(method, path, payload=None, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode("utf-8", "replace")
    except Exception as ex:
        return None, "异常: %r" % (ex,)


def jcall(method, path, payload=None, timeout=120):
    code, body = call(method, path, payload, timeout)
    try:
        return code, json.loads(body)
    except Exception:
        return code, {"_raw": body}


print("服务: %s" % BASE)
print("=" * 78)
print("① 初始状态（config.json 为空）")
code, st = jcall("GET", "/api/state")
check("state 返回 need_config", st.get("need_config") is True, str(st.get("games")))
code, cfg = jcall("GET", "/api/config")
check("config 不再报错", code == 200, "HTTP %s" % code)
check("扫描到候选目录（以前恒为 0）", len(cfg.get("candidates") or []) > 0,
      "候选 %d 个" % len(cfg.get("candidates") or []))
cands = [os.path.normcase(os.path.normpath(c["path"]))
         for c in (cfg.get("candidates") or [])]
if SCANNABLE:
    check("候选里包含游戏目录本身",
          os.path.normcase(os.path.normpath(MAIN)) in cands)
else:
    print("  [SKIP] %-52s %s" % ("候选里包含游戏目录本身",
                                 "游戏目录不在扫描范围内"))

# 深度扫描（「重新扫描」）找到的东西取决于后台预热有没有跑完，
# 所以这里轮询等待：最多 40 秒，直到候选里出现 MAIN
deep_paths = []
for _ in range(20):
    _deep_code, deep = jcall("GET", "/api/rescan")
    deep_paths = [os.path.normcase(os.path.normpath(c["path"]))
                  for c in (deep.get("candidates") or [])]
    if os.path.normcase(os.path.normpath(MAIN)) in deep_paths:
        break
    time.sleep(2)
if SCANNABLE:
    check("重新扫描能找到游戏目录（深度扫描）",
          os.path.normcase(os.path.normpath(MAIN)) in deep_paths or
          os.path.normcase(os.path.normpath(TEST)) in deep_paths,
          "深度扫描 %d 个" % len(deep_paths))
else:
    print("  [SKIP] %-52s %s" % ("重新扫描能找到游戏目录（深度扫描）",
                                 "游戏目录不在扫描范围内"))

print("\n② /api/check 实时校验")
code, r = jcall("GET", "/api/check?path=" + urllib.parse.quote(MAIN))
check("有效路径 -> ok", r.get("ok") is True, r.get("message", "")[:60])
code, r = jcall("GET", "/api/check?path=" + urllib.parse.quote(PARENT))
check("指到上一级 -> 给出 suggest", bool(r.get("suggest")),
      str(r.get("suggest"))[-40:])
check("suggest 指向真实游戏目录",
      os.path.normcase(os.path.normpath(r.get("suggest") or "")) ==
      os.path.normcase(os.path.normpath(MAIN)),
      str(r.get("suggest"))[-44:])
code, r = jcall("GET", "/api/check?path=" + urllib.parse.quote(MOD + r"\不存在"))
check("不存在的路径 -> 明确报错", r.get("ok") is False and "不存在" in r.get("message", ""),
      r.get("message", "")[:50])
code, r = jcall("GET", "/api/check?path=")
check("空路径 -> 提示填写", r.get("ok") is False, r.get("message", "")[:40])

print("\n③ 设置目录：指到上一级时自动纠正（新行为）")
code, r = jcall("POST", "/api/config", {"game_dir": PARENT})
check("HTTP 200（以前是 400 报错）", code == 200, "HTTP %s" % code)
check("used_parent 标记", r.get("used_parent") is True)
check("实际生效目录 = 里面的游戏目录",
      (r.get("game_dir") or "").lower().endswith(
          r"[th12] 东方星莲船 (汉化版+日文版)".lower()), r.get("game_dir"))
check("识别出版本", sorted(r.get("games") and [g["key"] for g in r["games"]]) == ["cn", "jp"],
      str([g["key"] for g in r.get("games") or []]))

print("\n④ 设置目录：直接指定「测试」副本")
code, r = jcall("POST", "/api/config", {"game_dir": TEST})
check("HTTP 200", code == 200, "HTTP %s" % code)
check("生效目录 = 测试副本", (r.get("game_dir") or "").lower() == TEST.lower(),
      r.get("game_dir"))
check("识别出版本", sorted([g["key"] for g in r.get("games") or []]) == ["cn", "jp"])

print("\n⑤ 设置目录：无效路径要给清楚的理由")
code, r = jcall("POST", "/api/config", {"game_dir": MOD + r"\不存在的目录"})
check("HTTP 400", code == 400, "HTTP %s" % code)
check("提示含「目录不存在」", "不存在" in r.get("error", ""), r.get("error", "")[:50])
code, r = jcall("POST", "/api/config", {"game_dir": ""})
check("空路径 -> 400 且提示填写", code == 400 and "填写" in r.get("error", ""),
      r.get("error", "")[:40])

print("\n⑥ 切回主目录，验证功能接口真的能跑")
code, r = jcall("POST", "/api/config", {"game_dir": MAIN})
check("切回主目录", code == 200 and not r.get("used_parent"), r.get("game_dir", "")[-40:])
code, st = jcall("GET", "/api/state")
check("state.games 有两个版本", len(st.get("games") or []) == 2)
check("state.bgm 有曲目", bool(st.get("bgm", {}).get("tracks")),
      "%d 首" % len((st.get("bgm") or {}).get("tracks") or []))
code, ar = jcall("GET", "/api/archive?game=jp")
check("归档列表可读", bool(ar.get("entries")), "%d 个条目" % len(ar.get("entries") or []))
code, bm = jcall("GET", "/api/bgm?game=jp")
check("BGM 列表可读", bool(bm.get("tracks")), "%d 首" % len(bm.get("tracks") or []))
code, tx = jcall("GET", "/api/textures?game=jp&anm=ascii.anm")
check("贴图可解析", bool(tx.get("textures")), "%d 张贴图" % len(tx.get("textures") or []))
code, ms = jcall("GET", "/api/msg?game=jp&name=st01_00a.msg")
check("对话可解析（有条目和说话人）",
      code == 200 and bool(ms.get("entries")) and bool(ms.get("info")),
      "%d 个条目, boss=%s" % (len(ms.get("entries") or []),
                              (ms.get("info") or {}).get("boss")))
code, pm = jcall("GET", "/api/pending")
check("待保存列表可读", code == 200, "count=%s" % pm.get("count"))

print("\n⑦ 持久化：config.json 是否记住目录")
time.sleep(0.3)
try:
    saved = json.load(open(CONFIG, encoding="utf-8"))
except Exception as ex:
    saved = {"_err": repr(ex)}
check("config.json 记下 game_dir",
      (saved.get("game_dir") or "").lower() == MAIN.lower(), str(saved.get("game_dir")))
check("config.json 记下 recent_dirs", bool(saved.get("recent_dirs")),
      "%d 条" % len(saved.get("recent_dirs") or []))

print("\n⑧ /api/rescan 重新扫描")
t0 = time.time()
code, rs = jcall("GET", "/api/rescan")
dt = time.time() - t0
check("rescan 成功", code == 200 and rs.get("count", 0) > 0,
      "%d 个候选, %.1fs" % (rs.get("count", 0), dt))

print("\n" + "=" * 78)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  - %s" % f)
sys.exit(1 if FAIL else 0)
