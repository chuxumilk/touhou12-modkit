# -*- coding: utf-8 -*-
"""独立验证 thbgm.fmt 的 loop 字段确实是「循环起点」。

原理：真正的循环点满足「loop 处的样本 ≈ 开头的样本」（首尾衔接连续），
因此该点的样本跳变应当显著小于随机位置的跳变。
如果 loop 只是某个无关的数字，跳变分布不会异常。
"""
import array
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "modtool"))

from thtk import archive, bgm as B  # noqa: E402

SRC = os.environ.get("TH12_PRISTINE_DIR") or os.environ.get("TH12_GAME_DIR")
if not SRC or not os.path.isdir(SRC):
    print("SKIP")
    sys.exit(0)

dat = os.path.join(SRC, "th12.dat")
a = archive.Archive.from_file(dat)
fmt = B.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))
data_path = os.path.join(SRC, "thbgm.dat")
f = open(data_path, "rb")

BYTES_PER_SEC = B.BYTES_PER_SEC
random.seed(20261003)


def jump_at(track, sample_index):
    """返回该样本处的跳变幅度 |x(i) - x(i-1)|，取双声道较大值。"""
    pos = track.offset + sample_index * 4
    f.seek(pos)
    cur = f.read(4)
    f.seek(pos - 4)
    prev = f.read(4)
    if len(cur) < 4 or len(prev) < 4:
        return None
    c = array.array("h")
    c.frombytes(cur)
    p = array.array("h")
    p.frombytes(prev)
    return max(abs(c[0] - p[0]), abs(c[1] - p[1]))


print("验证 loop 字段是否为真正的循环点")
print("（真循环点：loop 处跳变应显著小于随机位置的平均跳变）\n")
print("%-14s %9s %11s %11s %9s %8s" %
      ("曲目", "loop(s)", "loop处跳变", "随机处均值", "比值", "结论"))

ratios = []
for t in fmt.tracks:
    if t.loop <= 0 or t.loop >= t.end:
        continue
    ls = t.loop // 4                       # loop 位置对应的样本号
    j_loop = jump_at(t, ls)
    if j_loop is None:
        continue
    # 随机取 40 个非 loop 位置做基准
    hi = t.end // 4
    samples = []
    for _ in range(40):
        k = random.randrange(16, hi - 1)
        if abs(k - ls) < 200:
            continue
        j = jump_at(t, k)
        if j is not None:
            samples.append(j)
    if not samples:
        continue
    avg = statistics.mean(samples)
    ratio = j_loop / avg if avg else float("inf")
    ratios.append(ratio)
    verdict = "像循环点" if ratio < 0.6 else ("可疑" if ratio < 1.0 else "不像")
    print("%-14s %9.2f %11d %11.0f %9.2f %8s"
          % (t.name, t.loop / float(BYTES_PER_SEC), j_loop, avg, ratio, verdict))

print()
if ratios:
    print("比值中位数: %.3f" % statistics.median(ratios))
    good = sum(1 for r in ratios if r < 0.6)
    print("明显像循环点的: %d / %d" % (good, len(ratios)))
    if statistics.median(ratios) < 0.7:
        print("\n=> 结论：loop 字段确实是循环起点（跳变显著低于随机位置）")
    else:
        print("\n=> 结论：loop 字段与音频连续性无关，需要重新审视格式")
f.close()
