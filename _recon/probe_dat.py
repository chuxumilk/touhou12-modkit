# -*- coding: utf-8 -*-
"""th12.dat 头部结构侦察：dump 原始字节 + 按假设解析 + 边界自洽性校验。"""
import struct, os, io

# 路径跟着仓库走（脚本在 _recon/ 下）；也可用 TH12_DAT 指定待分析文件
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("TH12_DAT") or os.path.join(
    WS, "game", "[th12] 东方星莲船 (汉化版+日文版)", "th12.dat")
OUT = os.path.join(WS, "_recon", "dat_header.txt")

data = open(PATH, "rb").read()
L = []
def w(s=""):
    L.append(str(s))

w("file size = %d (0x%X)" % (len(data), len(data)))
w()
w("=== first 256 bytes ===")
for off in range(0, 0x100, 16):
    chunk = data[off:off+16]
    hexs = " ".join("%02x" % b for b in chunk)
    asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    w("%08X  %-47s  %s" % (off, hexs, asc))

w()
w("=== hypothesis: u32 magic, u16 ver, u16 count, then entries ===")
magic, ver, count = struct.unpack_from("<IHH", data, 0)
w("magic=0x%08X ver=%d count=%d" % (magic, ver, count))
w()

# 尝试多种 entry 布局，看哪种能让所有偏移自洽
layouts = {
    "A: u32 unk, u32 comp, u32 orig, u64 off, 16s md5, 256s name": (4+4+4+8+16+256, "IIQQ16s256s"),
    "B: u32 comp, u32 orig, u32 off, 16s md5, 256s name":          (4+4+4+16+256,    "III16s256s"),
    "C: u32 unk, u32 comp, u32 orig, u32 off, 16s md5, 256s name": (4+4+4+4+16+256,  "IIII16s256s"),
    "D: u32 orig, u32 comp, u32 off, 16s md5, 256s name":          (4+4+4+16+256,    "III16s256s"),
}
for label, (esz, fmt) in layouts.items():
    w("--- layout %s (entry size %d) ---" % (label, esz))
    base = 8
    ok = True
    prev_end = None
    rows = []
    try:
        for i in range(min(count, 6)):
            off = base + i * esz
            vals = struct.unpack_from("<" + fmt, data, off)
            name = vals[-1].split(b"\0")[0]
            off_field = None
            # 找出哪个字段看起来像文件偏移（应递增且 < filesize）
            for vi, v in enumerate(vals[:-1]):
                if isinstance(v, int) and 0 < v < len(data):
                    pass
            rows.append((i, vals[:-1], name))
        for i, nums, name in rows:
            w("  [%d] nums=%s name=%r" % (i, nums, name))
    except Exception as e:
        w("  parse error: %s" % e)
        ok = False
    w()

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("written")
