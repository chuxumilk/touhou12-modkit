# -*- coding: utf-8 -*-
"""端到端验证真实修改流程（不改游戏文件，全部只进「暂存」）。

流程：
  1. 指向游戏目录，确认两个版本都能打开
  2. 导出一张贴图 PNG → 读回它的字节（证明能读）
  3. 用这张 PNG 做「替换贴图」→ 应进暂存（pending +1），此时游戏文件未变
  4. 改一个 .msg 文本 → 应进暂存（pending +1）
  5. 检查待保存列表 / 备份列表
  6. 最后清空暂存（不留垃圾）

用法: python _test/verify_workflow.py [port]
"""
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
BASE = "http://127.0.0.1:%d" % PORT


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
if not GOOD:
    raise SystemExit(
        "找不到游戏目录。请设置环境变量，例如：\n"
        "  set TH12_GAME_DIR=D:\\Games\\th12")

PASS, FAIL = [], []


def check(title, cond, detail=""):
    (PASS if cond else FAIL).append(title)
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", title,
                            ("— " + str(detail)) if detail else ""))


def call(method, path, payload=None, raw=None, timeout=180):
    data = raw if raw is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read(), ex.headers.get("Content-Type", "")
    except Exception as ex:
        return None, repr(ex).encode(), ""


def jcall(method, path, payload=None, raw=None, timeout=180):
    code, body, _ = call(method, path, payload, raw=raw, timeout=timeout)
    try:
        return code, json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return code, {"_raw": body[:200]}


print("服务: %s" % BASE)
print("=" * 76)

print("① 指向游戏目录")
code, r = jcall("POST", "/api/config", {"game_dir": GOOD})
check("设置目录成功", code == 200 and not r.get("warning"),
      r.get("warning", "")[:60])
code, st = jcall("GET", "/api/state")
check("两个版本可用", len(st.get("games") or []) == 2,
      str([g["key"] for g in st.get("games") or []]))

print("\n② 读贴图（列出 ascii.anm 的贴图并导出 PNG）")
code, tx = jcall("GET", "/api/textures?game=jp&anm=ascii.anm")
tex = (tx.get("textures") or [])
check("能列出贴图", bool(tex), "%d 张" % len(tex))
if not tex:
    print("   没有贴图，后面步骤跳过"); sys.exit(1)
t0 = tex[0]
print("   第一张: %s  %sx%s  format=%s"
      % (t0.get("name"), t0.get("width"), t0.get("height"), t0.get("format")))
code, png, ctype = call(
    "GET", "/api/texture.png?game=jp&anm=ascii.anm&index=%d" % t0["index"])
check("导出 PNG 成功", code == 200 and png[:8] == b"\x89PNG\r\n\x1a\n",
      "%d 字节" % len(png))

print("\n③ 替换贴图 → 应进暂存（不动游戏文件）")
before = os.path.getsize(os.path.join(GOOD, "th12.dat"))
before_mtime = os.path.getmtime(os.path.join(GOOD, "th12.dat"))
code, res = jcall("POST",
                  "/api/texture?game=jp&anm=ascii.anm&index=%d" % t0["index"],
                  raw=png)
check("替换接口返回 200", code == 200, str(res)[:80])
code, pd = jcall("GET", "/api/pending")
check("暂存里出现这条修改", (pd.get("count") or 0) >= 1,
      "待保存 %s 项" % pd.get("count"))
after = os.path.getsize(os.path.join(GOOD, "th12.dat"))
after_mtime = os.path.getmtime(os.path.join(GOOD, "th12.dat"))
check("游戏文件未被改动（只是暂存）",
      before == after and before_mtime == after_mtime,
      "%d -> %d 字节" % (before, after))

print("\n④ 改对话文本 → 也应进暂存")
code, ms = jcall("GET", "/api/msg?game=jp&name=st01_00a.msg")
entries = ms.get("entries") or []
check("读到对话条目", bool(entries), "%d 条" % len(entries))
changed = 0
if entries:
    # 找出第一处文本，按前端真实用法提交：entries[].texts{指令序号: 新文本}
    for e in entries:
        texts = {}
        for ins in (e.get("instructions") or []):
            if ins.get("name") == "textAdd":
                texts[str(ins["index"])] = (ins.get("text") or "") + "改"
        if texts:
            payload = {"entries": [{"index": e["index"], "texts": texts}]}
            code, res = jcall("POST", "/api/msg?game=jp&name=st01_00a.msg",
                              payload)
            check("保存对话接口返回 200", code == 200, str(res)[:70])
            changed = res.get("changed") or 0
            break
    check("确实暂存了对话修改", changed > 0, "changed=%s" % changed)
    code, pd = jcall("GET", "/api/pending")
    check("暂存项增加", (pd.get("count") or 0) >= 2,
          "待保存 %s 项" % pd.get("count"))
    names = [it.get("name") or it.get("target") or it.get("label")
             for it in (pd.get("items") or [])]
    print("   暂存内容: %s" % names)

print("\n⑤ 归档列表 / 备份列表")
code, ar = jcall("GET", "/api/archive?game=jp")
check("归档条目数正常", len(ar.get("entries") or []) == 180,
      "%d 条" % len(ar.get("entries") or []))
code, bk = jcall("GET", "/api/backups")
check("备份列表可读", code == 200,
      "%d 个备份" % len(bk.get("backups") or []))

print("\n⑥ 清空暂存（不留垃圾）")
code, res = jcall("POST", "/api/pending.clear", {})
check("清空成功", code == 200 and res.get("ok"), str(res)[:60])
code, pd = jcall("GET", "/api/pending")
check("暂存已空", (pd.get("count") or 0) == 0, "剩余 %s" % pd.get("count"))

print("\n" + "=" * 76)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - %s" % f)
sys.exit(1 if FAIL else 0)
