# -*- coding: utf-8 -*-
"""对比所有游戏副本里 thbgm.fmt 的循环点，看哪份被改过。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "modtool"))

from thtk import archive, bgm as B  # noqa: E402

ROOTS = [r"D:\04-游戏和娱乐\东方Project", r"D:\临时工作区"]

FOUND = []
for r in ROOTS:
    for dp, dn, fn in os.walk(r):
        if "th12.dat" in fn:
            FOUND.append(dp)

# 基准：未被工具写过的原始归档（.bak 是工具第一次保存前的原样）
print("对比 thbgm.fmt 里的循环点（秒）\n")

rows = []
for d in FOUND:
    for dat in ("th12.dat", "th12c.dat"):
        p = os.path.join(d, dat)
        if not os.path.isfile(p):
            continue
        try:
            a = archive.Archive.from_file(p)
            raw = a.read_by_name("thbgm.fmt")
            fmt = B.BgmFmt.from_bytes(raw)
        except Exception as ex:
            print("  %-70s 读取失败: %s" % (p, ex))
            continue
        loops = tuple(round(t.loop / float(B.BYTES_PER_SEC), 2)
                      for t in fmt.tracks)
        rows.append((d, dat, loops, [t.name for t in fmt.tracks]))

if not rows:
    print("没有找到归档")
    sys.exit(0)

base = rows[0][2]
print("基准（%s / %s）:" % (rows[0][0][-40:], rows[0][1]))
print("  " + " ".join("%.1f" % x for x in base))
print()

for d, dat, loops, names in rows:
    diff = [(names[i], base[i], loops[i])
            for i in range(min(len(base), len(loops))) if base[i] != loops[i]]
    tag = "已改过 %d 处" % len(diff) if diff else "与基准相同"
    print("=== %s" % d)
    print("    %s -> %s" % (dat, tag))
    for n, o, nw in diff:
        print("        %-14s %.2fs -> %.2fs" % (n, o, nw))
    print()
