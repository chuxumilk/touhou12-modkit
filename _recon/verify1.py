# -*- coding: utf-8 -*-
"""对照验证：我的 thtk 包 vs 官方 thdat 对同一个 th12.dat 的结果。"""
import os, sys, subprocess, hashlib, io

WS = r"D:\010-Important-Work\DSH相关\东方魔改"
sys.path.insert(0, WS)

from thtk import archive, crypto, lzss

GAME = os.path.join(WS, "game", "[th12] 东方星莲船 (汉化版+日文版)")
DAT = os.path.join(GAME, "th12.dat")
DAT_CN = os.path.join(GAME, "th12c.dat")
THDAT = r"C:\thtk\build-portable\thdat\thdat.exe"
OUT = os.path.join(WS, "_recon", "verify1.txt")

L = []
def w(s=""):
    L.append(str(s))

# ---------- 1. 头部自证 ----------
data = open(DAT, "rb").read()
w("=== 1. header ===")
h = crypto.th_crypt(data[:16], crypto.HEADER_KEY, crypto.HEADER_STEP, 16, 16)
w("  magic=%r  size=%d zsize=%d count=%d" % (
    h[:4], *[v - c for v, c in zip(
        __import__("struct").unpack_from("<III", h, 4),
        (archive.CONST_LIST_SIZE, archive.CONST_LIST_ZSIZE, archive.CONST_ENTRY_COUNT))]))

# ---------- 2. 解析归档 ----------
a = archive.Archive.from_file(DAT)
info = a.info()
w()
w("=== 2. parsed archive ===")
for k, v in info.items():
    w("  %-20s %s" % (k, v))

# ---------- 3. 与 thdat -l 对比条目表 ----------
w()
w("=== 3. compare entry list with official thdat ===")
r = subprocess.run([THDAT, "-l", "12", DAT], capture_output=True)
lines = r.stdout.decode("cp936", "replace").splitlines()[1:]
official = []
for ln in lines:
    parts = ln.split()
    if len(parts) >= 3:
        official.append((parts[0], int(parts[1]), int(parts[2])))
w("  official entries : %d" % len(official))
w("  my entries       : %d" % len(a.entries))
same = 0
diff = []
for i, (nm, sz, zs) in enumerate(official):
    if i >= len(a.entries):
        diff.append((i, nm, "MISSING in mine"))
        continue
    e = a.entries[i]
    if e.name == nm and e.size == sz and e.zsize == zs:
        same += 1
    else:
        diff.append((i, nm, "official=(%s,%d,%d) mine=(%s,%d,%d)" % (
            nm, sz, zs, e.name, e.size, e.zsize)))
w("  identical        : %d / %d" % (same, len(official)))
if diff:
    w("  differences:")
    for d in diff[:10]:
        w("    %s" % (d,))
else:
    w("  -> PERFECT MATCH")

# ---------- 4. 解压内容 vs thdat -x ----------
w()
w("=== 4. extracted content comparison ===")
EX = os.path.join(WS, "_recon", "x_official")
os.makedirs(EX, exist_ok=True)
subprocess.run([THDAT, "-x", "12", DAT, "-C", EX], capture_output=True)
ok = bad = 0
fails = []
for e in a.entries:
    mine = a.read_by_name(e.name)
    ref_path = os.path.join(EX, e.name)
    if not os.path.exists(ref_path):
        continue
    ref = open(ref_path, "rb").read()
    if mine == ref:
        ok += 1
    else:
        bad += 1
        fails.append("%s: mine=%d ref=%d sha_mine=%s sha_ref=%s" % (
            e.name, len(mine), len(ref),
            hashlib.md5(mine).hexdigest()[:12], hashlib.md5(ref).hexdigest()[:12]))
w("  identical files  : %d" % ok)
w("  mismatched       : %d" % bad)
for f in fails[:12]:
    w("    %s" % f)

# ---------- 5. LZSS 往返 ----------
w()
w("=== 5. lzss round-trip ===")
import random
random.seed(1234)
cases = {
    "empty": b"",
    "one": b"A",
    "text": b"hello world " * 200,
    "zeros": b"\0" * 5000,
    "random": bytes(random.randrange(256) for _ in range(9000)),
    "mixed": (b"ABCD" * 50 + bytes(random.randrange(256) for _ in range(300))) * 3,
}
for nm, src in cases.items():
    z = lzss.lzss(src)
    back = lzss.unlzss(z, len(src))
    w("  %-8s in=%-6d lzss=%-6d roundtrip=%s" % (
        nm, len(src), len(z), "OK" if back == src else "FAIL"))

# ---------- 6. 真实文件 LZSS 往返 ----------
w()
w("=== 6. lzss round-trip on real (decrypted) entries ===")
n_ok = n_bad = 0
for idx in range(min(40, len(a.entries))):
    raw = a.read(idx)
    z = lzss.lzss(raw)
    back = lzss.unlzss(z, len(raw))
    if back == raw:
        n_ok += 1
    else:
        n_bad += 1
w("  round-trip OK : %d" % n_ok)
w("  round-trip BAD: %d" % n_bad)

# ---------- 7. 汉化版 ----------
w()
w("=== 7. localized th12c.dat ===")
try:
    acn = archive.Archive.from_file(DAT_CN)
    w("  entries=%d list_size=%d list_zsize=%d" % (
        len(acn), acn.list_size, acn.list_zsize))
    w("  first 5 names: %s" % [e.name for e in acn.entries[:5]])
except Exception as ex:
    w("  FAILED: %r" % (ex,))

with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("written")
