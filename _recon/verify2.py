# -*- coding: utf-8 -*-
"""对照验证（增量落盘版）：我的 thtk 包 vs 官方 thdat。

用法: python verify2.py [--slow]
  --slow  额外执行大文件 LZSS 压缩往返（很慢，纯 Python 实现）
"""
import os, sys, subprocess, hashlib, io, time, struct, random

WS = r"D:\010-Important-Work\DSH相关\东方魔改"
sys.path.insert(0, WS)
from thtk import archive, crypto, lzss

GAME = os.path.join(WS, "game", "[th12] 东方星莲船 (汉化版+日文版)")
DAT = os.path.join(GAME, "th12.dat")
DAT_CN = os.path.join(GAME, "th12c.dat")
THDAT = r"C:\thtk\build-portable\thdat\thdat.exe"
OUT = os.path.join(WS, "_recon", "verify2.txt")
SLOW = "--slow" in sys.argv

_fh = io.open(OUT, "w", encoding="utf-8")
def w(s=""):
    _fh.write(str(s) + "\n")
    _fh.flush()

def log(label, ok, detail=""):
    w("  [%s] %-46s %s" % ("PASS" if ok else "FAIL", label, detail))
    return ok

fails = 0

# ============ 1. 头部自证 ============
w("=== 1. header decryption ===")
data = open(DAT, "rb").read()
h = crypto.th_crypt(data[:16], crypto.HEADER_KEY, crypto.HEADER_STEP, 16, 16)
list_size, list_zsize, count = struct.unpack_from("<III", h, 4)
list_size -= archive.CONST_LIST_SIZE
list_zsize -= archive.CONST_LIST_ZSIZE
count -= archive.CONST_ENTRY_COUNT
fails += not log("magic == 'THA1'", h[:4] == b"THA1", repr(h[:4]))
fails += not log("fields plausible", 0 < count < 5000 and 0 < list_zsize < len(data),
                 "list_size=%d list_zsize=%d count=%d" % (list_size, list_zsize, count))

# ============ 2. 解析归档 ============
w()
w("=== 2. archive parse ===")
t0 = time.time()
a = archive.Archive.from_file(DAT)
dt = time.time() - t0
w("  parsed %d entries in %.2fs" % (len(a), dt))
inf = a.info()
for k in ("format", "list_size", "list_zsize", "total_size",
          "total_uncompressed", "total_stored"):
    w("    %-20s %s" % (k, inf[k]))

# ============ 3. 与 thdat -l 逐条对比 ============
w()
w("=== 3. entry table vs official thdat -l ===")
r = subprocess.run([THDAT, "-l", "12", DAT], capture_output=True)
official = []
for ln in r.stdout.decode("cp936", "replace").splitlines()[1:]:
    p = ln.split()
    if len(p) >= 3 and p[1].isdigit():
        official.append((p[0], int(p[1]), int(p[2])))
w("  official=%d  mine=%d" % (len(official), len(a)))
same = 0
diffs = []
for i, (nm, sz, zs) in enumerate(official):
    if i < len(a.entries):
        e = a.entries[i]
        if e.name == nm and e.size == sz and e.zsize == zs:
            same += 1
            continue
        diffs.append("#%d official=(%s,%d,%d) mine=(%s,%d,%d)" % (i, nm, sz, zs, e.name, e.size, e.zsize))
    else:
        diffs.append("#%d official=%s MISSING in mine" % (i, nm))
fails += not log("all entries identical", same == len(official),
                 "%d/%d match" % (same, len(official)))
for d in diffs[:8]:
    w("      %s" % d)

# ============ 4. 解压内容 vs thdat -x ============
w()
w("=== 4. extracted bytes vs official thdat -x ===")
EX = os.path.join(WS, "_recon", "x_official")
os.makedirs(EX, exist_ok=True)
if not os.path.exists(os.path.join(EX, "ascii.anm")):
    subprocess.run([THDAT, "-x", "12", DAT, "-C", EX], capture_output=True)
ok = bad = 0
bad_list = []
for i in range(len(a)):
    e = a.entries[i]
    p = os.path.join(EX, e.name)
    if not os.path.exists(p):
        continue
    mine = a.read(i)
    ref = open(p, "rb").read()
    if mine == ref:
        ok += 1
    else:
        bad += 1
        bad_list.append("%s mine=%d ref=%d" % (e.name, len(mine), len(ref)))
fails += not log("all extracted files byte-identical", bad == 0,
                 "%d ok, %d mismatched" % (ok, bad))
for b in bad_list[:10]:
    w("      %s" % b)

# ============ 5. LZSS 往返（合成数据）============
w()
w("=== 5. lzss round-trip (synthetic) ===")
random.seed(1234)
syn = [
    ("empty", b""),
    ("one byte", b"A"),
    ("repetitive", b"hello world " * 200),
    ("zeros", b"\0" * 5000),
    ("random 9KB", bytes(random.randrange(256) for _ in range(9000))),
    ("mixed", (b"ABCD" * 50 + bytes(random.randrange(256) for _ in range(300))) * 3),
    ("256 pattern", bytes(range(256)) * 40),
]
for nm, src in syn:
    z = lzss.lzss(src)
    back = lzss.unlzss(z, len(src))
    fails += not log("round-trip %s" % nm, back == src,
                     "in=%d lzss=%d" % (len(src), len(z)))

# ============ 6. 解密后真实数据的 LZSS 往返 ============
w()
w("=== 6. lzss round-trip on real decrypted entries ===")
w("  (--slow mode; compressing large files in pure Python is slow)")
nmax = len(a) if SLOW else 0
if nmax == 0:
    for idx in range(min(6, len(a))):
        e = a.entries[idx]
        if e.size > 200000:
            continue
        raw = a.read(idx)
        t = time.time()
        z = lzss.lzss(raw)
        back = lzss.unlzss(z, len(raw))
        fails += not log("round-trip %s" % e.name, back == raw,
                         "in=%d lzss=%d %.2fs" % (len(raw), len(z), time.time() - t))
else:
    for idx in range(len(a)):
        e = a.entries[idx]
        raw = a.read(idx)
        z = lzss.lzss(raw)
        back = lzss.unlzss(z, len(raw))
        ok_ = back == raw
        fails += not ok_
        w("  [%s] %-20s in=%-9d lzss=%-9d ratio=%.1f%%" % (
            "PASS" if ok_ else "FAIL", e.name, len(raw), len(z),
            100.0 * len(z) / max(1, len(raw))))

# ============ 7. 归档写回往返 ============
w()
w("=== 7. archive rebuild round-trip (self-consistency) ===")
try:
    a2 = archive.Archive.from_bytes(a.to_bytes())
    fails += not log("rebuilt archive re-parses", len(a2) == len(a),
                     "%d entries" % len(a2))
    n_ok = 0
    for i in range(len(a2)):
        if a2.read(i) == a.read(i):
            n_ok += 1
    fails += not log("rebuilt archive content identical", n_ok == len(a),
                     "%d/%d entries" % (n_ok, len(a)))
except Exception as ex:
    fails += 1
    w("  [FAIL] rebuild raised: %r" % (ex,))

# ============ 8. 汉化版 ============
w()
w("=== 8. localized th12c.dat ===")
try:
    acn = archive.Archive.from_file(DAT_CN)
    fails += not log("th12c.dat parses", len(acn) > 0,
                     "%d entries, list %d->%d" % (len(acn), acn.list_size, acn.list_zsize))
    r2 = subprocess.run([THDAT, "-l", "12", DAT_CN], capture_output=True)
    off2 = [ln.split()[0] for ln in r2.stdout.decode("cp936", "replace").splitlines()[1:]
            if len(ln.split()) >= 3 and ln.split()[1].isdigit()]
    mine_names = [e.name for e in acn.entries]
    fails += not log("th12c names match official", mine_names[:len(off2)] == off2,
                     "mine=%d official=%d" % (len(mine_names), len(off2)))
    # 与官方解包对比一个文件
    EXCN = os.path.join(WS, "_recon", "x_official_cn")
    os.makedirs(EXCN, exist_ok=True)
    subprocess.run([THDAT, "-x", "12", DAT_CN, "-C", EXCN], capture_output=True)
    cnt = 0
    for i in range(min(20, len(acn))):
        p = os.path.join(EXCN, acn.entries[i].name)
        if os.path.exists(p) and acn.read(i) == open(p, "rb").read():
            cnt += 1
    fails += not log("th12c content matches (first 20)", cnt == 20, "%d/20" % cnt)
except Exception as ex:
    fails += 1
    w("  [FAIL] th12c: %r" % (ex,))

w()
w("=========================================")
w("TOTAL FAILURES: %d" % fails)
w("=========================================")
_fh.close()
print("done, failures =", fails)
