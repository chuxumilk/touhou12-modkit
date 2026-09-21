# -*- coding: utf-8 -*-
"""th12.exe 逆向侦察：PE 结构 + 关键立即数搜索 + 代码段 dump 准备。"""
import struct, re, os

EXE = r"D:\th12\exe\[th12] 东方星莲船 (汉化版+日文版)\th12.exe"
OUT = r"D:\010-Important-Work\DSH相关\东方魔改\_recon\exe_probe.txt"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

exe = open(EXE, "rb").read()
L = []
def w(s=""):
    L.append(str(s))

w("EXE size = %d" % len(exe))
w("MZ = %r" % exe[:2])
pe_off = struct.unpack_from("<I", exe, 0x3C)[0]
w("PE header offset = 0x%X  sig=%r" % (pe_off, exe[pe_off:pe_off+4]))
coff = pe_off + 4
machine, nsec, tds, nsym, optsz, chars = struct.unpack_from("<HHIIIH", exe, coff)
w("machine=0x%04X sections=%d optsize=%d" % (machine, nsec, optsz))
opt = coff + 20
magic = struct.unpack_from("<H", exe, opt)[0]
w("opt magic = 0x%04X (%s)" % (magic, "PE32" if magic == 0x10b else "PE32+"))
base_of_code = struct.unpack_from("<I", exe, opt + 20)[0]
image_base = struct.unpack_from("<I", exe, opt + 28)[0]
w("image_base=0x%08X base_of_code=0x%X" % (image_base, base_of_code))

secs = []
sec_off = opt + optsz
for i in range(nsec):
    o = sec_off + i * 40
    name = "".join(chr(b) if 32 <= b < 127 else "." for b in exe[o:o+8].rstrip(b"\0"))
    vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", exe, o + 8)
    sflags = struct.unpack_from("<I", exe, o + 36)[0]
    secs.append((name, vaddr, vsize, raddr, rsize, sflags))
    w("  sec %-8s VA=0x%08X VS=0x%06X RAW=0x%06X RS=0x%06X FLAGS=0x%08X"
      % (name, vaddr, vsize, raddr, rsize, sflags))

def rva2off(rva):
    for name, vaddr, vsize, raddr, rsize, fl in secs:
        if vaddr <= rva < vaddr + max(vsize, rsize):
            return raddr + (rva - vaddr)
    return None

# 导出表 / 导入表
ddoff = opt + (96 if magic == 0x10b else 112)
w()
w("=== data directories (RVA,Size) ===")
names = ["Export","Import","Resource","Exception","Security","Reloc","Debug",
         "Arch","GlobalPtr","TLS","LoadConfig","BoundImport","IAT","DelayImport",
         "CLR","Reserved"]
for i in range(16):
    rva, sz = struct.unpack_from("<II", exe, ddoff + i * 8)
    if rva or sz:
        w("  %-12s RVA=0x%08X size=0x%X  (file 0x%X)" % (names[i], rva, sz, rva2off(rva) or 0))

# ---- 关键立即数 / 字节串搜索 ----
w()
w("=== constant / signature search over whole file ===")
pats = {
    "PBGZ": b"PBGZ",
    "ZGBP (LE of 0x5a474250)": b"ZGBP",
    "edz\\0 (LE imm 0x00616465)": b"edz\x00",
    "edz any (e d z)": b"edz",
    "'zde\\0'": b"zde\x00",
    "th12.dat": b"th12.dat",
    "thbgm.dat": b"thbgm.dat",
    "scoreth12.dat": b"scoreth12.dat",
    "123456 (as int LE)": struct.pack("<I", 123456),
    "345678 (as int LE)": struct.pack("<I", 345678),
    "567891 (as int LE)": struct.pack("<I", 567891),
    "0x00080000": struct.pack("<I", 0x80000),
}
for label, pat in pats.items():
    idxs = [m.start() for m in re.finditer(re.escape(pat), exe)]
    w("  %-28s n=%-4d %s" % (label, len(idxs), ["0x%X" % i for i in idxs[:10]]))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("written")
