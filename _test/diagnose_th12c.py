# -*- coding: utf-8 -*-
"""查清 th12c.dat 里 48 个条目「解压不完整」是数据坏了还是校验过严。

判据：
  * 原始数据里若不压缩的条目（zsize == size）正常 → 说明归档结构没问题
  * 让 unlzss 不顾 output_size 一直解，看流自己在哪结束：
      - 自然结束时长度 == 条目表里的 size  → 说明是我这边判据错了
      - 自然结束时长度 != size            → 条目表与数据不一致（真损坏）
      - 中途抛错                          → 数据本身坏了

用法: python _test/diagnose_th12c.py <th12c.dat 路径>
"""
import os
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS)
sys.path.insert(0, os.path.join(WS, "tools", "modtool"))
from thtk import archive, crypto, lzss  # noqa: E402

DEFAULT = os.environ.get("TH12C_DAT") or os.path.join(
    WS, "game", "[th12] 东方星莲船 (汉化版+日文版)", "th12c.dat")
path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
if not os.path.isfile(path):
    raise SystemExit("找不到 %s" % path)


def raw_unlzss(data, limit):
    """不设目标长度，解到流自己结束为止（用于判断真实长度）。"""
    dict_ = bytearray(lzss.LZSS_DICTSIZE)
    head = 1
    br = lzss.BitReader(data)
    out = bytearray()
    while len(out) < limit:
        if br.read(1):
            c = br.read(8)
            out.append(c)
            dict_[head] = c
            head = (head + 1) & lzss.LZSS_DICTSIZE_MASK
        else:
            off = br.read(13)
            if not off:
                return bytes(out), "结束标记"
            n = br.read(4) + lzss.LZSS_MIN_MATCH
            for i in range(n):
                c = dict_[(off + i) & lzss.LZSS_DICTSIZE_MASK]
                out.append(c)
                dict_[head] = c
                head = (head + 1) & lzss.LZSS_DICTSIZE_MASK
    return bytes(out), "达到上限 %d" % limit


print("归档: %s（%.1f MB）" % (path, os.path.getsize(path) / 1048576.0))
fsize = os.path.getsize(path)
a = archive.Archive.from_file(path)
print("条目数: %d" % len(a.entries))
print()

ok = short = over = broken = uncompressed = 0
rows = []
for e in a.entries:
    if e.zsize == e.size:
        uncompressed += 1
        continue
    blob = a._data[e.offset:e.offset + e.zsize]
    key, step, block, limit = crypto.entry_crypt_params(e.name)
    blob = crypto.th_crypt(blob, key, step, block, limit)
    try:
        got, why = raw_unlzss(blob, e.size * 2 + 1024)
    except Exception as ex:
        broken += 1
        rows.append((e.name, e.size, e.zsize, -1, "解压抛错: %s" % str(ex)[:40]))
        continue
    n = len(got)
    if n == e.size:
        # 用正常路径复核（不该报错）
        try:
            lzss.unlzss(blob, e.size)
            ok += 1
        except lzss.LzssError as ex:
            rows.append((e.name, e.size, e.zsize, n, "长度对但标准路径报错! %s" % ex))
    elif n < e.size:
        short += 1
        rows.append((e.name, e.size, e.zsize, n, "流提前结束（%s）" % why))
    else:
        over += 1
        rows.append((e.name, e.size, e.zsize, n, "流比声明更长（%s）" % why))

print("压缩存储的条目里：")
print("  正常            : %d" % ok)
print("  解出来偏短      : %d" % short)
print("  解出来偏长      : %d" % over)
print("  解压直接抛错    : %d" % broken)
print("  （另有 %d 条本来就未压缩存储）" % uncompressed)
print()
if rows:
    print("有问题条目前 20 个：")
    print("  %-18s %10s %10s %10s  %s" % ("名称", "声明size", "zsize", "实解长度", "说明"))
    for name, size, zsize, got, why in rows[:20]:
        print("  %-18s %10d %10d %10s  %s"
              % (name, size, zsize, got if got >= 0 else "-", why))
print()
print("=== 结论 ===")
if broken:
    print("有 %d 条解压直接抛错 → 数据本身坏了，确属损坏" % broken)
elif short or over:
    print("解压从不抛错，只是长度与条目表声明的不一致（偏短 %d、偏长 %d）。" % (short, over))
    print("含义：**归档的条目表 size 字段与数据流对不上**，而不是数据流损坏。")
    print("官方 thdat 对这类归档也是「解出多少算多少」，所以：")
    print("  - 不应该因此拒绝整份归档（否则这份游戏就完全用不了了）")
    print("  - 但应当在界面上提示用户：这些文件导出后可能不完整")
    print("  - 库里 unlzss() 保留严格契约（默认报错），")
    print("    归档读取路径用 allow_partial=True 尽力而为")
else:
    print("全部条目都能解到声明长度 → 无损坏，无不一致")
