# -*- coding: utf-8 -*-
"""验收：切到「数据损坏」的游戏目录时，工具必须仍然可用（降级而不是瘫痪）。

需要一个「归档被写坏」的目录来做测试：
  set TH12_DAMAGED_DIR=D:\\path\\to\\broken-copy
没有设置的话，脚本会提示并跳过损坏相关的用例（其余照跑）。

用法: python _test/verify_damaged.py [port]
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(title, cond, detail=""):
    (PASS if cond else FAIL).append(title)
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", title,
                            ("— " + str(detail)) if detail else ""))


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


def jcall(*a, **kw):
    code, body = call(*a, **kw)
    try:
        return code, json.loads(body)
    except Exception:
        return code, {"_raw": body[:200]}


def find_game_dir():
    """找一份可用的游戏目录：环境变量 > 仓库内的常见位置。"""
    ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    envv = os.environ.get("TH12_GAME_DIR")
    if envv and os.path.isdir(envv):
        return envv
    for c in (os.path.join(ws, "game", "[th12] 东方星莲船 (汉化版+日文版)"),
              os.path.join(ws, "测试", "[th12] 东方星莲船 (汉化版+日文版)")):
        if os.path.isdir(c):
            return c
    return None


GOOD = find_game_dir()
DAMAGED = os.environ.get("TH12_DAMAGED_DIR")
if DAMAGED and not os.path.isdir(DAMAGED):
    DAMAGED = None
if DAMAGED and GOOD and os.path.normcase(DAMAGED) == os.path.normcase(GOOD):
    DAMAGED = None

if not GOOD:
    raise SystemExit(
        "找不到游戏目录。请设置环境变量，例如：\n"
        "  set TH12_GAME_DIR=D:\\Games\\th12")

print("服务    : %s" % BASE)
print("好目录  : %s" % GOOD)
print("坏目录  : %s" % (DAMAGED or "（未指定，将跳过损坏相关用例）"))
print("=" * 76)


def test_damaged_is_reported():
    print("\n① /api/check 应当当场发现归档损坏")
    code, r = jcall("GET", "/api/check?path=" + urllib.parse.quote(DAMAGED))
    check("HTTP 200（不是 500）", code == 200, code)
    check("仍然判定为可用目录", r.get("ok") is True)
    check("报告了数据损坏", bool(r.get("problems")),
          ((r.get("problems") or [""])[0].splitlines() or [""])[0][:60])


def test_degrade():
    print("\n② 切到损坏目录后，页面主接口必须还能用")
    code, r = jcall("POST", "/api/config", {"game_dir": DAMAGED})
    check("设置目录 HTTP 200", code == 200, code)
    check("返回里带 warning", bool(r.get("warning")),
          (r.get("warning") or "")[:60])
    code, st = jcall("GET", "/api/state")
    check("state 不再 500（关键）", code == 200, code)
    check("state 仍返回游戏版本", bool(st.get("games")),
          str([g["key"] for g in st.get("games") or []]))
    check("state 带 bgm_error 说明", bool(st.get("bgm_error")),
          str(st.get("bgm_error"))[:50])
    check("state 的 bgm 为 None（降级）", st.get("bgm") is None)

    print("\n③ 贴图 / 对话 / 归档仍然可用（这是重点）")
    code, ar = jcall("GET", "/api/archive?game=jp")
    check("归档列表可读", code == 200 and bool(ar.get("entries")),
          "%d 个条目" % len(ar.get("entries") or []))
    code, tx = jcall("GET", "/api/textures?game=jp&anm=ascii.anm")
    check("贴图可解析", code == 200 and bool(tx.get("textures")),
          "%d 张贴图" % len(tx.get("textures") or []))
    code, ms = jcall("GET", "/api/msg?game=jp&name=st01_00a.msg")
    check("对话可解析", code == 200 and bool(ms.get("entries")))
    code, pm = jcall("GET", "/api/pending")
    check("待保存列表可读", code == 200)
    code, r = jcall("GET", "/api/bgm?game=jp")
    check("音乐页给出可读的错误（不是 500 空白）", code in (400, 500) and
          "thbgm" in str(r.get("error", "")), str(r.get("error"))[:52])


def test_recover():
    print("\n④ 切回好的目录后，一切恢复（错误缓存要被清掉）")
    code, r = jcall("POST", "/api/config", {"game_dir": GOOD})
    check("切回好目录 HTTP 200", code == 200, code)
    check("好目录没有 warning", not r.get("warning"), str(r.get("warning"))[:40])
    code, st = jcall("GET", "/api/state")
    check("state 正常", code == 200 and not st.get("bgm_error"), code)
    check("音乐信息恢复", bool((st.get("bgm") or {}).get("tracks")),
          "%d 首" % len((st.get("bgm") or {}).get("tracks") or []))
    code, bg = jcall("GET", "/api/bgm?game=jp")
    check("音乐列表可读", code == 200 and bool(bg.get("tracks")),
          "%d 首" % len(bg.get("tracks") or []))


if DAMAGED:
    test_damaged_is_reported()
    test_degrade()
else:
    print("\n（跳过 ①②③：没有指定 TH12_DAMAGED_DIR）")
test_recover()

print("\n" + "=" * 76)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - %s" % f)
sys.exit(1 if FAIL else 0)
