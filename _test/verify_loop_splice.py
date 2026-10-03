# -*- coding: utf-8 -*-
"""实测「循环点拼接」是否真的实现了循环。

th12.exe 的 BGM 引擎只把 offset 起的 total_len 字节读进内存后**整块循环
播放**，从不读循环点字段（三重取证见 _recon/th12_loop_decisive.py）。
所以循环点必须靠改音频实现：

    原曲 [前奏 L][循环体 B]  ->  轨道内容 = [循环体][循环体]，循环点写 B

引擎从缓冲开头整块重放，第一遍播的就是循环体，从头到尾都在循环。
（早期版本用的是 [引子][循环体][引子]，把循环点当「引子长度」，
循环点越靠后引子越长、循环感越弱，主观上等同于「没生效」。）

本脚本用真实游戏目录的**副本**验证：
  1. 保存后 thbgm.dat 里的音频确实等于 [循环体][循环体]
  2. 循环点字段 = 循环体长度
  3. 没设循环点的曲目一个字都没被改
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "modtool"))

from thtk import archive, bgm as B  # noqa: E402

SRC = os.environ.get("TH12_PRISTINE_DIR") or os.environ.get("TH12_GAME_DIR")
if not SRC or not os.path.isdir(SRC):
    print("SKIP: 需要 TH12_PRISTINE_DIR")
    sys.exit(0)

BPS = B.BYTES_PER_SEC
fails = []
checks = [0]


def check(cond, label, extra=""):
    checks[0] += 1
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s %s" % (label, extra))
        fails.append(label)


def read_track_pcm(dat_path, track):
    with open(dat_path, "rb") as f:
        f.seek(track.offset)
        return f.read(track.end)


def read_fmt(arch_path):
    a = archive.Archive.from_file(arch_path)
    return B.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))


def peaks(pcm, n=4000):
    """取每段最大幅度（只看左声道，够用来判断音频内容）。"""
    total = len(pcm) // 4
    step = max(1, total // n)
    out = []
    for k in range(n):
        i = k * step
        if i >= total:
            break
        seg = pcm[i * 4:(i + step) * 4:4]
        out.append(max(seg) if seg else 0)
    return out


def main():
    work = tempfile.mkdtemp(prefix="th12_splice_")
    game = os.path.join(work, "game")
    os.makedirs(game)
    for n in os.listdir(SRC):
        if n in ("th12.dat", "th12c.dat", "thbgm.dat"):
            shutil.copy2(os.path.join(SRC, n), os.path.join(game, n))

    import server  # noqa: E402
    server.set_game_dir(game)
    server.STATE.invalidate()

    tracks = server.list_bgm()["tracks"]
    idx = None
    for t in tracks:
        if t["duration"] > 40:
            idx = t["index"]
            break
    idx = 0 if idx is None else idx
    t0 = [t for t in tracks if t["index"] == idx][0]
    print("测试曲目 #%d %s  原长 %.2f 秒" % (idx, t0["name"], t0["duration"]))

    # 记住「没动过」的一条，最后确认它没被改
    other = [t for t in tracks if t["index"] != idx][0]

    fmt_before = read_fmt(os.path.join(game, "th12.dat"))
    before = read_track_pcm(os.path.join(game, "thbgm.dat"),
                            fmt_before.tracks[idx])
    other_before = read_track_pcm(os.path.join(game, "thbgm.dat"),
                                  fmt_before.tracks[other["index"]])
    A_bytes = len(before)                      # 原曲长度
    print("  原曲 PCM %d 字节 (%.2f 秒)" % (A_bytes, A_bytes / float(BPS)))

    L_sec = min(12.0, t0["duration"] * 0.3)
    L = int(L_sec * BPS) // 4 * 4
    print("\n[1] 设循环点 %.2f 秒" % L_sec)
    r = server.bgm_set_loop(idx, L)
    print("    -> %s" % r)
    check(r.get("ok") and r.get("loop") == L, "接口接受循环点")
    check(abs(r.get("loop_body_seconds", 0)
              - (A_bytes - L) / float(BPS)) < 0.01, "回报循环体时长正确",
          str(r.get("loop_body_seconds")))

    print("\n[2] 保存")
    res = server.save_all("splice test")
    print("    ok=%s errors=%s" % (res.get("ok"), res.get("errors")))
    check(res.get("ok") and not res.get("errors"), "保存成功",
          str(res.get("errors")))

    print("\n[3] 校验拼接结果")
    fmt_after = read_fmt(os.path.join(game, "th12.dat"))
    t = fmt_after.tracks[idx]
    after = read_track_pcm(os.path.join(game, "thbgm.dat"), t)
    body = before[L:]                        # 循环体
    want_len = 2 * len(body)
    print("    保存后 PCM %d 字节 (%.2f 秒)，期望 %d = 2 × 循环体 %d"
          % (len(after), len(after) / float(BPS), want_len, len(body)))
    check(len(after) == want_len, "长度 = 2 × 循环体",
          "%d vs %d" % (len(after), want_len))
    check(t.loop == len(body), "fmt 里 loop = 循环体长度",
          "期望 %d 实际 %d" % (len(body), t.loop))
    check(t.preload >= len(after), "preload 覆盖整条（否则循环体被截断）",
          "preload=%d len=%d" % (t.preload, len(after)))

    check(after == body + body, "整段 = [循环体][循环体]")
    check(after[:len(body)] == body, "前半 = 循环体")
    check(after[len(body):] == body, "后半 = 循环体（与前半逐字节相同）")

    print("\n[4] 回绕无缝 + 前奏确实被丢弃")
    # 引擎播完整块后回到 0，0 处就是循环体开头 —— 与曲尾接得上
    check(after[:4] == before[L:L + 4],
          "回绕后播的就是循环体开头（与原曲循环点处一致）")
    check(after.find(before[:L]) == -1 if L >= 4 else True,
          "前奏 [0,循环点) 已不在轨道里（所以一进游戏就在循环）")

    print("\n[5] 播放序列：全程都是循环体，没有「引子只播一次」的问题")
    # 引擎整块循环，按时间展开就是 body | body | body …
    n = len(body)
    seq = [after[0:n], after[n:2 * n]]
    check(seq[0] == seq[1] == body, "两遍播的都是循环体")
    check(len(after) == 2 * n, "轨道恰好装两遍循环体")

    print("\n[6] 没设循环点的曲目必须一字未改")
    other_after = read_track_pcm(os.path.join(game, "thbgm.dat"),
                                 fmt_after.tracks[other["index"]])
    check(other_after == other_before, "未改动的曲目 PCM 完全一致",
          "%d vs %d" % (len(other_after), len(other_before)))
    check(fmt_after.tracks[other["index"]].loop
          == fmt_before.tracks[other["index"]].loop,
          "未改动的曲目 loop 字段未变")

    print("\n[7] 换成更小的循环点重新保存：必须从原始音频重算，不能叠加")
    L2 = int(6.0 * BPS) // 4 * 4
    server.bgm_set_loop(idx, L2)
    res = server.save_all("splice again")
    fmt3 = read_fmt(os.path.join(game, "th12.dat"))
    t3 = fmt3.tracks[idx]
    after3 = read_track_pcm(os.path.join(game, "thbgm.dat"), t3)
    # 关键：源永远是「未拼接的原始音频」，所以结果 = 2 × (原长 − 新循环点)，
    # 而不是在上一次结果上再拼。
    body2 = before[L2:]
    want3 = 2 * len(body2)
    print("    新循环点 %.2f 秒 -> PCM %d 字节（期望 %d = 2 × 循环体 %d）"
          % (L2 / float(BPS), len(after3), want3, len(body2)))
    check(len(after3) == want3, "长度 = 2 × 新循环体（没有叠加）",
          "%d vs %d" % (len(after3), want3))
    check(t3.loop == len(body2), "新 loop 字段 = 新循环体长度",
          "%d vs %d" % (t3.loop, len(body2)))
    check(after3 == body2 + body2, "整段 = 原始音频按新循环点重拼")

    print("\n[8] 再改第三次，确认始终以原始音频为源")
    L3 = int(20.0 * BPS) // 4 * 4
    server.bgm_set_loop(idx, L3)
    server.save_all("splice third")
    fmt4 = read_fmt(os.path.join(game, "th12.dat"))
    t4 = fmt4.tracks[idx]
    after4 = read_track_pcm(os.path.join(game, "thbgm.dat"), t4)
    body3 = before[L3:]
    print("    第三次循环点 %.2f 秒 -> PCM %d 字节（期望 %d）"
          % (L3 / float(BPS), len(after4), 2 * len(body3)))
    check(len(after4) == 2 * len(body3), "第三次仍是 2 × 循环体",
          "%d vs %d" % (len(after4), 2 * len(body3)))
    check(after4 == body3 + body3, "内容 = 原始音频按第三次循环点重拼")

    print("\n" + "=" * 62)
    print("%d 项检查，%d 失败" % (checks[0], len(fails)))
    for f in fails:
        print("  - %s" % f)
    shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
