# -*- coding: utf-8 -*-
"""自证 TH12 归档结构：手工实现 th_crypt 解密头部，验证 THA1 魔数。"""
import struct, os

PATH = r"D:\th12\dat\[th12] 东方星莲船 (汉化版+日文版)\th12.dat"
OUT = r"D:\010-Important-Work\DSH相关\东方魔改\_recon\tha1_proof.txt"
L = []
def w(s=""):
    L.append(str(s))

def th_crypt(data, key, step, block):
    """复刻 thtk thcrypt.c 的 th_decrypt，单块（block == len(data)）。"""
    data = bytearray(data)
    size = len(data)
    if size < block >> 2:
        size = 0
    else:
        size -= (size % block < block >> 2) * (size % block) + size % 2
    limit = block  # limit 按 block 向上取整
    end = size if size < limit else limit
    increment = (block >> 1) + (block & 1)
    temp = bytearray(block)
    pos = 0
    while pos < end:
        b = block
        inc = increment
        if end - pos < block:
            b = end - pos
            inc = (b >> 1) + (b & 1)
        inpos = pos
        out = b - 1
        while out > 0:
            temp[out] = data[inpos] ^ (key & 0xFF); out -= 1
            temp[out] = data[inpos + inc] ^ ((key + step * inc) & 0xFF); out -= 1
            inpos += 1
            key = (key + step) & 0xFF
        if b & 1:
            temp[out] = data[inpos] ^ (key & 0xFF)
            key = (key + step) & 0xFF
        key = (key + step * inc) & 0xFF
        data[pos:pos+b] = temp[:b]
        pos += b
    return bytes(data)

data = open(PATH, "rb").read()
N = len(data)
w("th12.dat size = %d (0x%X)" % (N, N))
w()

raw_header = data[:16]
w("=== raw first 16 bytes ===")
w("  " + " ".join("%02x" % b for b in raw_header))
w()

dec = th_crypt(raw_header, 0x1b, 0x37, 16)
w("=== after th_crypt(key=0x1b, step=0x37, block=16, limit=16) ===")
w("  " + " ".join("%02x" % b for b in dec))
magic = dec[:4]
w("  magic = %r" % magic)
w("  magic == b'THA1' ? %s" % (magic == b"THA1"))
w()

size, zsize, count = struct.unpack_from("<III", dec, 4)
w("=== decoded header fields (before constant subtraction) ===")
w("  size  = %d (0x%X)" % (size, size))
w("  zsize = %d (0x%X)" % (zsize, zsize))
w("  count = %d (0x%X)" % (count, count))
w()
rsize = size - 123456789
rzsize = zsize - 987654321
rcount = count - 135792468
w("=== after subtracting constants ===")
w("  real size        = %d (0x%X)" % (rsize, rsize))
w("  real zsize       = %d (0x%X)" % (rzsize, rzsize))
w("  real entry_count = %d" % rcount)
w()
w("=== consistency checks ===")
w("  entry table at file end? zsize < filesize : %s (%d < %d)" % (rzsize < N, rzsize, N))
w("  table starts at 0x%X, file ends at 0x%X" % (N - rzsize, N))
w("  plausible entry count (1..5000) : %s" % (0 < rcount < 5000))
w("  plausible uncompressed table size: %s" % (0 < rsize < N))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("written")
