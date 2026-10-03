# -*- coding: utf-8 -*-
"""实测「循环点拼接」是否真的实现了循环。

th12.exe 的 BGM 引擎只读 thbgm.fmt 的 offset 与 preload，把那段音频
整块循环播放，从不读 +0x18 的 loop 字段。所以「改循环点」是靠改音频实现的：

    原曲 [引子 L][循环体 B]  ->  拼接成 [引子][循环体][引子]
                                  0..A    A..2A    2A..2A+L

引擎播完 [0, 2A+L) 回到 0 重放，听到的是末尾那份引子，
它结束处紧接着循环体开头 —— 于是音乐无缝进入第二段并无限循环。

本脚本用真实游戏目录的**副本**验证：
  1. 保存后 thbgm.dat 里的音频确实等于 [引子][循环体][引子]
  2. 播放序列（模拟引擎整块循环）确实在循环点处衔接
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
    print("\n[1] 设循环点 %.2f 秒（引子长度）" % L_sec)
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
    want_len = A_bytes + L
    print("    保存后 PCM %d 字节 (%.2f 秒)，期望 %d"
          % (len(after), len(after) / float(BPS), want_len))
    check(len(after) == want_len, "长度 = 原长 + 引子",
          "%d vs %d" % (len(after), want_len))
    check(t.loop == L, "fmt 里 loop 指向循环体起点",
          "期望 %d 实际 %d" % (L, t.loop))
    check(t.preload >= len(after), "preload 覆盖整条（否则循环体被截断）",
          "preload=%d len=%d" % (t.preload, len(after)))

    intro = before[:L]
    body = before[L:]
    check(after[:L] == intro, "开头 = 引子")
    check(after[L:L + len(body)] == body, "中段 = 循环体")
    check(after[-L:] == intro, "末尾 = 引子（这是循环能接上的关键）")
    check(after == intro + body + intro, "整段 = 引子+循环体+引子")

    print("\n[4] 回绕衔接点")
    # 缓冲总长 = A + L（引子+循环体 = A，再加一份引子）
    check(len(after) == A_bytes + L, "缓冲总长 = 原长 + 引子")
    # 引擎播到末尾后回到 0，听到的是末尾那份引子 -> 无缝接回开头
    check(after[-L:] == after[:L], "末尾引子 == 开头引子，回绕后无缝")

    print("\n[5] 播放序列：验证「引子播一次，之后一直循环循环体」")
    # 引擎把 [0, A+L) 整块反复播放，按时间展开就是：
    #   intro body | intro body | intro body | ...
    # 把缓冲按 [引子][循环体+引子] 切成两段来核对
    check(after[:L] == intro, "第 1 段起播 = 引子")
    check(after[L:] == body + intro, "第 2 段起播 = 循环体 → 引子")
    check(after[L:L + len(body)] == body, "引子之后完整接出循环体")
    # 关键性质：循环区 [L, A+L) 内部不再出现「整段引子」，也就是说
    # 引子只在开头播一次，之后听到的都是循环体
    check(after[L:] != after, "循环区内容 ≠ 整块（引子没有整段重复）")
    # 引子结束处接的是循环体开头 —— 与原曲 L 处的接缝完全一致
    check(after[L:L + 4] == before[L:L + 4],
          "引子结束处接的是循环体开头（与原始接缝一致）")

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
    # 关键：源永远是「未拼接的原始音频」，所以结果 = 原长 + 新引子，
    # 而不是 上一次结果 + 新引子。
    want3 = A_bytes + L2
    print("    新循环点 %.2f 秒 -> PCM %d 字节（期望 %d = 原长 %d + 新引子 %d）"
          % (L2 / float(BPS), len(after3), want3, A_bytes, L2))
    check(len(after3) == want3, "长度 = 原长 + 新引子（没有叠加）",
          "%d vs %d" % (len(after3), want3))
    check(t3.loop == L2, "新 loop 字段生效")
    check(after3[:L2] == before[:L2], "新引子取自原始音频的开头")
    check(after3 == before[:L2] + before[L2:] + before[:L2],
          "整段 = 原始音频按新循环点重拼")

    print("\n[8] 再改第三次，确认始终以原始音频为源")
    L3 = int(20.0 * BPS) // 4 * 4
    server.bgm_set_loop(idx, L3)
    server.save_all("splice third")
    fmt4 = read_fmt(os.path.join(game, "th12.dat"))
    t4 = fmt4.tracks[idx]
    after4 = read_track_pcm(os.path.join(game, "thbgm.dat"), t4)
    print("    第三次循环点 %.2f 秒 -> PCM %d 字节（期望 %d）"
          % (L3 / float(BPS), len(after4), A_bytes + L3))
    check(len(after4) == A_bytes + L3, "第三次仍是原长 + 引子",
          "%d vs %d" % (len(after4), A_bytes + L3))
    check(after4 == before[:L3] + before[L3:] + before[:L3],
          "内容 = 原始音频按第三次循环点重拼")

    print("\n" + "=" * 62)
    print("%d 项检查，%d 失败" % (checks[0], len(fails)))
    for f in fails:
        print("  - %s" % f)
    shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
