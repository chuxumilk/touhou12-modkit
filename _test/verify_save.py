# -*- coding: utf-8 -*-
"""真实写入测试：跑一次「保存到游戏」，确认写到一半自检、写完整合都没问题。

【重要】只在游戏目录的副本上跑（会真的改文件）。
用法: python _test/verify_save.py <port> <gameDir>
"""
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
GAME = sys.argv[2] if len(sys.argv) > 2 else ""
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(title, cond, detail=""):
    (PASS if cond else FAIL).append(title)
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", title,
                            ("— " + str(detail)) if detail else ""))


def call(method, path, payload=None, raw=None, timeout=300):
    data = raw if raw is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if payload is not None:
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


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if not GAME or not os.path.isdir(GAME):
    print("用法: python _test/verify_save.py <port> <游戏目录>")
    sys.exit(2)

DAT_JP = os.path.join(GAME, "th12.dat")
DAT_CN = os.path.join(GAME, "th12c.dat")
CMT = os.path.join(GAME, "musiccmt.txt")

print("服务    : %s" % BASE)
print("游戏目录: %s" % GAME)
print("=" * 76)

# 先备份一份原始 musiccmt（万一没有 .bak）
BACKUP = CMT + ".verify-save.bak"
if os.path.isfile(CMT) and not os.path.isfile(BACKUP):
    shutil.copyfile(CMT, BACKUP)
    print("已备份 musiccmt.txt -> %s" % os.path.basename(BACKUP))

before = {p: (os.path.getsize(p), md5(p)) for p in (DAT_JP, DAT_CN)
          if os.path.isfile(p)}

print("① 指向目录（副本）")
code, r = jcall("POST", "/api/config", {"game_dir": GAME})
check("设置目录 HTTP 200", code == 200, r.get("message", "")[:60])
if r.get("warning"):
    print("   注意（目录本身有历史损坏）: %s" % r["warning"][:70])

print("\n② 读原始 musiccmt")
GAME_KEY = "cn"      # 用汉化版（原版目录里常有，且日文版注释可能被汉化包改过）
code, mc = jcall("GET", "/api/musiccmt?game=" + GAME_KEY)
orig = mc.get("text", "")
check("读到 musiccmt", code == 200 and len(orig) > 0, "%d 字符" % len(orig))

print("\n③ 改注释并【真正保存到游戏】")
new_text = orig.rstrip("\r\n") + "\r\n# verify-save 测试行 %d\r\n" % int(time.time())
code, res = jcall("POST", "/api/musiccmt?game=" + GAME_KEY, {"text": new_text})
check("提交注释修改", code == 200, str(res)[:60])
time.sleep(0.5)
code, pd = jcall("GET", "/api/pending")
check("有待保存项", (pd.get("count") or 0) >= 1, "%s 项" % pd.get("count"))

code, saved = jcall("POST", "/api/save", {"comment": "verify_save 自动测试"})
check("保存到游戏返回成功", code == 200 and saved.get("ok") is not False,
      "files=%s errors=%s" % (saved.get("files"), saved.get("errors")))
if saved.get("errors"):
    print("   保存时的错误: %s" % saved["errors"])

print("\n④ 归档完整性（写完自检应当拦住坏数据）")
time.sleep(0.5)
for p in (DAT_JP, DAT_CN):
    if not os.path.isfile(p):
        continue
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        from thtk import archive
        a = archive.Archive.from_file(p)
        size = os.path.getsize(p)
        beyond = [e for e in a.entries if e.offset >= size]
        fails = []
        for e in sorted(a.entries, key=lambda x: x.zsize)[:8]:
            try:
                a.read_by_name(e.name)
            except Exception as ex:
                fails.append("%s(%s)" % (e.name, ex))
        check("%s 条目表完整" % os.path.basename(p), not beyond,
              "%d 条, 越界 %d" % (len(a.entries), len(beyond)))
        check("%s 抽查可读" % os.path.basename(p), not fails, str(fails[:2]))
    except Exception as ex:
        check("%s 能重新打开" % os.path.basename(p), False, repr(ex)[:80])

print("\n⑤ 保存后功能仍可用")
code, st = jcall("GET", "/api/state")
check("state 正常", code == 200, code)
if st.get("bgm_error"):
    print("   （音乐仍不可用，属该副本历史损坏）: %s" % st["bgm_error"][:50])
code, ar = jcall("GET", "/api/archive?game=" + GAME_KEY)
check("归档可读", code == 200 and bool(ar.get("entries")),
      "%d 条" % len(ar.get("entries") or []))
code, mc2 = jcall("GET", "/api/musiccmt?game=" + GAME_KEY)
check("注释已写入", "verify-save 测试行" in (mc2.get("text") or ""))

print("\n⑥ 还原注释（保持目录干净）")
code, res = jcall("POST", "/api/musiccmt?game=" + GAME_KEY, {"text": orig})
code, saved2 = jcall("POST", "/api/save", {"comment": "还原 verify_save 的测试改动"})
check("还原成功", code == 200 and saved2.get("ok") is not False,
      "files=%s" % saved2.get("files"))
code, mc3 = jcall("GET", "/api/musiccmt?game=" + GAME_KEY)
check("注释已还原", "verify-save 测试行" not in (mc3.get("text") or ""))
if os.path.isfile(BACKUP):
    os.remove(BACKUP)

print("\n" + "=" * 76)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - %s" % f)
sys.exit(1 if FAIL else 0)
