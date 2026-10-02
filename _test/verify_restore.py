# -*- coding: utf-8 -*-
"""验证「还原」的安全性：
  1. 损坏/截断/空的备份必须被**拒绝**，且游戏文件不被改动
  2. 正常备份能还原成功
  3. 目标文件是硬链接时，还原不会「以为改了其实没改」

用法: python _test/verify_restore.py [port] [gameDir]
      gameDir 省略时用临时目录（会自动复制一份 th12.dat 过去，安全）
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
BASE = "http://127.0.0.1:%d" % PORT
GAME = sys.argv[2] if len(sys.argv) > 2 else ""

PASS, FAIL = [], []


def check(title, cond, detail=""):
    (PASS if cond else FAIL).append(title)
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", title,
                            ("— " + str(detail)) if detail else ""))


def call(method, path, payload=None, timeout=300):
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
        return code, {"_raw": body[:300]}


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def find_source_archive():
    for c in (os.path.join(WS, "game", "[th12] 东方星莲船 (汉化版+日文版)",
                           "th12.dat"),
              os.environ.get("TH12_GAME_DIR", "") and
              os.path.join(os.environ["TH12_GAME_DIR"], "th12.dat")):
        if c and os.path.isfile(c):
            return c
    return None


# ---------------------------------------------------------------- 准备目录
tmpdir = None
if not GAME:
    src = find_source_archive()
    if not src:
        raise SystemExit("找不到可用的 th12.dat，请用第二参数指定游戏目录")
    tmpdir = tempfile.mkdtemp(prefix="th12-restore-test-")
    GAME = tmpdir
    shutil.copyfile(src, os.path.join(GAME, "th12.dat"))
    print("用临时目录测试: %s" % GAME)
    print("（从 %s 复制了一份 th12.dat）" % src)
else:
    print("用指定目录测试: %s（注意：会真的动这里的备份文件）" % GAME)

DAT = os.path.join(GAME, "th12.dat")
BAK = DAT + ".modtool.bak"
PREV = DAT + ".modtool.prev"
print("=" * 78)

code, r = jcall("POST", "/api/config", {"game_dir": GAME})
check("设置目录成功", code == 200, r.get("message", "")[:50])
if code != 200:
    sys.exit(1)

original = md5(DAT)

# ---------------------------------------------------------------- 1 空备份
print("\n① 备份是 0 字节 → 必须拒绝")
open(BAK, "wb").close()
code, r = jcall("POST", "/api/restore?key=jp")
check("拒绝还原", code == 400, "HTTP %s" % code)
check("提示说明了原因", "空" in str(r.get("error", "")), str(r.get("error", ""))[:54])
check("游戏文件未被改动", md5(DAT) == original)

# ---------------------------------------------------------------- 2 截断备份
print("\n② 备份被截断（只留一半）→ 必须拒绝")
with open(DAT, "rb") as f:
    data = f.read()
with open(BAK, "wb") as f:
    f.write(data[:len(data) // 2])
code, r = jcall("POST", "/api/restore?key=jp")
check("拒绝还原", code == 400, "HTTP %s" % code)
check("提示说明了原因", "备份" in str(r.get("error", "")),
      str(r.get("error", "")).splitlines()[1][:54] if r.get("error") else "")
check("游戏文件未被改动", md5(DAT) == original)

# ---------------------------------------------------------------- 3 垃圾备份
print("\n③ 备份是垃圾数据 → 必须拒绝")
with open(BAK, "wb") as f:
    f.write(b"not an archive at all" * 100)
code, r = jcall("POST", "/api/restore?key=jp")
check("拒绝还原", code == 400, "HTTP %s" % code)
check("游戏文件未被改动", md5(DAT) == original)

# ---------------------------------------------------------------- 4 好备份
print("\n④ 备份完好 → 应当还原成功")
# 先制造「当前文件与备份不同」：把当前文件改坏一点
with open(DAT, "r+b") as f:
    f.seek(len(data) // 2)
    f.write(b"\xff" * 64)
broken = md5(DAT)
check("当前文件确实被改动了", broken != original)
shutil.copyfile(os.path.join(GAME, "th12.dat") if False else
                find_source_archive() or DAT, BAK) if False else None
# 用原始内容做备份
with open(BAK, "wb") as f:
    f.write(data)
code, r = jcall("POST", "/api/restore?key=jp")
check("还原成功", code == 200 and r.get("ok") is True, str(r)[:60])
check("文件已回到原始内容", md5(DAT) == original,
      "还原后 %s" % md5(DAT)[:12])
check("生成了 .modtool.prev", os.path.isfile(PREV))
if os.path.isfile(PREV):
    check(".prev 保存的是还原前的坏文件", md5(PREV) == broken)

# ---------------------------------------------------------------- 5 硬链接
print("\n⑤ 目标是硬链接（模拟「文件夹里同一份游戏的另一个入口」）→ 还原不能被误导")
real = os.path.join(GAME, "real_game_copy.dat")
if os.path.isfile(real):
    os.remove(real)
try:
    os.link(DAT, real)
    linked = True
except OSError as ex:
    linked = False
    print("     跳过：本文件系统不支持硬链接（%s）" % ex)
if linked:
    real_before = md5(real)
    # 改坏目标文件（硬链接会一起变）
    with open(DAT, "r+b") as f:
        f.seek(100)
        f.write(b"\x00" * 32)
    real_broken = md5(real)
    check("硬链接确实同步变化",
          md5(real) == md5(DAT) and real_broken != real_before)
    with open(BAK, "wb") as f:
        f.write(data)
    code, r = jcall("POST", "/api/restore?key=jp")
    check("还原成功", code == 200 and r.get("ok") is True, str(r)[:50])
    check("目标文件已回到原始内容", md5(DAT) == original)
    # os.replace 是「换掉目录项」，所以硬链接会被**断开**：
    # 目标路径指向新的完整副本（还原成功 ✓），链接另一头仍指向旧 inode（坏数据）。
    # 关键结论：这是预期行为，不是「假成功」；但如果用户真靠硬链接共用数据，
    # 还原只会修好选中的那个路径，另一头要单独还原。
    check("链接另一头仍指向旧数据（链接被断开，符合预期）",
          md5(real) == real_broken,
          "另一头 %s（应为坏数据 %s）" % (md5(real)[:8], real_broken[:8]))

# ---------------------------------------------------------------- 收尾
print("\n" + "=" * 78)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - %s" % f)
if tmpdir:
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("（已清理临时目录）")
sys.exit(1 if FAIL else 0)
