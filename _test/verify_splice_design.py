# -*- coding: utf-8 -*-
"""新拼接设计的结构自测（用可区分的段落，不靠肉眼）。

新设计：整个轨道 = [循环体][循环体]，循环点 = 循环体长度。
旧设计（已废弃）：[引子][循环体][引子]，循环点 = 引子长度 ——
循环点设得越靠后引子越长、循环感越弱，用户实测等同于「没生效」。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from thtk import bgm as B  # noqa: E402

fails = []
checks = [0]


def check(cond, label, extra=""):
    checks[0] += 1
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s %s" % (label, extra))
        fails.append(label)


def main():
    # 四段互不相同的内容，代表「前奏 / 循环体第1段 / 第2段 / 第3段」
    segs = [b"A" * 8, b"B" * 8, b"C" * 8, b"D" * 8]
    src = b"".join(segs)                    # 32 字节
    LOOP = 8                                # 循环点 = 8，即从 B 开始循环

    print("[1] 循环点 8 字节（丢弃前奏 A，从 B 开始循环）")
    out, nl = B.splice_loop_pcm(src, LOOP)
    print("    输入 %d 字节 = %s" % (len(src), src.decode()))
    print("    输出 %d 字节 = %s" % (len(out), out.decode()))
    print("    新循环点 = %d" % nl)
    body = src[LOOP:]                       # B C D
    check(out == body + body, "输出 = [循环体][循环体]")
    check(nl == len(body), "新循环点 = 循环体长度", "%d vs %d" % (nl, len(body)))

    # 关键：模拟引擎整块循环，确认听不到前奏 A
    seq = []
    for _ in range(3):
        for i in range(0, len(out), 8):
            seq.append(out[i:i + 8].decode())
    heard = " ".join(seq)
    print("    模拟播放三段: %s" % heard)
    check("A" not in set("".join(seq)),
          "全程不再出现前奏 A（循环体独占整条轨道）")
    # 段是 8 字节，用每段的第一个字节当标签比较
    tags = [s[:1] for s in seq]
    check(tags[:6] == ["B", "C", "D", "B", "C", "D"],
          "第一遍完整播的就是循环体", str(tags[:6]))
    check(tags[6:12] == tags[:6], "第二遍与第一遍完全相同（无缝循环）")

    print("\n[2] 循环点 0（整首循环）：内容不变，循环点写 0")
    out2, nl2 = B.splice_loop_pcm(src, 0)
    check(out2 == src, "内容不变")
    check(nl2 == 0, "循环点 = 0", str(nl2))

    print("\n[3] 循环点越界：按整首循环处理，不崩")
    out3, nl3 = B.splice_loop_pcm(src, 99999)
    check(out3 == src, "越界时内容不变")
    check(nl3 == 0, "越界时循环点 = 0")

    print("\n[4] 循环点 4（非 4 字节对齐）：应向下对齐")
    out4, nl4 = B.splice_loop_pcm(src, 5)
    check(nl4 == len(src) - 4, "按 4 对齐后的长度算",
          "nl=%d" % nl4)

    print("\n[5] 空音频 / 极短音频不崩")
    out5, nl5 = B.splice_loop_pcm(b"", 4)
    check(out5 == b"" and nl5 == 0, "空输入安全")
    out6, nl6 = B.splice_loop_pcm(b"\x00\x00\x00\x00", 4)
    check(nl6 == 0, "4 字节输入且 loop=4 时按整首循环")

    print("\n" + "=" * 62)
    print("%d 项检查，%d 失败" % (checks[0], len(fails)))
    for f in fails:
        print("  - %s" % f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
