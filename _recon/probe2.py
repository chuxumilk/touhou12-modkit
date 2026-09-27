# -*- coding: utf-8 -*-
"""th12.dat 结构多假设探测：明文名扫描 / PBGZ解密测试 / 熵与分布 / th06容器测试。"""
import struct, os, re, math, collections

# 路径跟着仓库走（脚本在 _recon/ 下）；也可用 TH12_DAT 指定待分析文件
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("TH12_DAT") or os.path.join(
    WS, "game", "[th12] 东方星莲船 (汉化版+日文版)", "th12.dat")
OUT = os.path.join(WS, "_recon", "dat_probe2.txt")

data = open(PATH, "rb").read()
L = []
def w(s=""):
    L.append(str(s))

w("size = %d (0x%X)" % (len(data), len(data)))

# ---------- 1. 熵分布（按 64KB 窗口）----------
w()
w("=== entropy by 64KB window (encrypted/compressed ~8.0, plaintext lower) ===")
WIN = 65536
ent = []
for off in range(0, len(data), WIN):
    chunk = data[off:off+WIN]
    if len(chunk) < 1024: break
    c = collections.Counter(chunk)
    n = len(chunk)
    h = -sum((v/n) * math.log2(v/n) for v in c.values())
    ent.append((off, h))
for off, h in ent[:8]:
    w("  0x%08X  %.4f" % (off, h))
w("  ...")
for off, h in ent[-4:]:
    w("  0x%08X  %.4f" % (off, h))
allc = collections.Counter(data); n = len(data)
w("  whole-file entropy = %.4f" % (-sum((v/n)*math.log2(v/n) for v in allc.values())))
w("  count of 0x00 bytes = %d (%.3f%%)" % (allc.get(0,0), 100.0*allc.get(0,0)/n))

# ---------- 2. 明文文件名扫描 ----------
w()
w("=== plaintext-ish strings (>=6 printable ASCII) in first 4MB ===")
hits = 0
for m in re.finditer(rb"[\x20-\x7e]{6,}", data[:4*1024*1024]):
    w("  0x%08X  %r" % (m.start(), m.group()))
    hits += 1
    if hits > 25: break
if hits == 0:
    w("  (none)")

# ---------- 3. PBGZ 假设 ----------
w()
w("=== hypothesis: PBGZ (th08) ===")
w("  first4 = %r  -> %s" % (data[:4], "MATCH" if data[:4]==b"PBGZ" else "NO MATCH"))

# ---------- 4. th06/th07 容器假设 (count,offset,size) ----------
w()
w("=== hypothesis: th06/th07 container (count,offset,size u32 LE) ===")
c, o, s = struct.unpack_from("<III", data, 0)
w("  count=%d offset=%d size=%d" % (c, o, s))
w("  offset < filesize? %s ; size < filesize? %s" % (o < len(data), s < len(data)))

# ---------- 5. th105 假设 ----------
w()
w("=== hypothesis: th105 (u16 count, u32 hdrsize) ===")
c16, hs = struct.unpack_from("<HI", data, 0)
w("  count=%d hdrsize=%d (filesize=%d) -> %s" % (c16, hs, len(data), "plausible" if 0 < hs < len(data) else "IMPLAUSIBLE"))

# ---------- 6. 在 exe 中搜索 "edz" 与 PBGZ 常量 ----------
w()
w("=== search 'edz' / 'PBGZ' / 123456-consts in th12.exe ===")
EXE = r"D:\th12\exe\[th12] 东方星莲船 (汉化版+日文版)\th12.exe"
exe = open(EXE, "rb").read()
for pat in (b"edz", b"PBGZ", b"ZGBP", b"thbgm"):
    idxs = [m.start() for m in re.finditer(re.escape(pat), exe)]
    w("  %-8r found at %s" % (pat, ["0x%X" % i for i in idxs[:12]]))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("written", len(L), "lines")
