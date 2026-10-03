# -*- coding: utf-8 -*-
"""实测「替换曲目 + 改循环点」的组合路径。

这是最容易出问题的一条：rebuild_bgm_dat 里有
    if track.loop > length: track.loop = 0
的静默重置，替换短音频时会把用户设的循环点吞掉。
"""
import os
import shutil
import struct
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "modtool"))

from thtk import archive, bgm as B  # noqa: E402

SRC = os.environ.get("TH12_PRISTINE_DIR") or os.environ.get("TH12_GAME_DIR")
if not SRC or not os.path.isdir(SRC):
    print("SKIP: 需要 TH12_PRISTINE_DIR")
    sys.exit(0)

fails = []
checks = [0]


def check(cond, label, extra=""):
    checks[0] += 1
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s %s" % (label, extra))
        fails.append(label)


def make_wav(path, seconds, freq=440):
    """生成 16bit 立体声 44100Hz 的测试 WAV。"""
    n = int(seconds * 44100)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * ((i * freq // 44100) % 2 * 2 - 1))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))
    return path


def read_fmt_from(arch_path):
    a = archive.Archive.from_file(arch_path)
    return B.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))


def main():
    work = tempfile.mkdtemp(prefix="th12_comb_")
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
        # 选一条原曲够长的，这样我们能换一个更短的进去
        if t["duration"] > 60:
            idx = t["index"]
            break
    if idx is None:
        idx = 0
    t0 = [t for t in tracks if t["index"] == idx][0]
    print("目标曲目 #%d %s  原时长 %.1fs" % (idx, t0["name"], t0["duration"]))

    # ---------- 场景 A：替换成【更短】的音频，循环点落在新音频内 ----------
    print("\n" + "=" * 64)
    print("场景 A：替换成 40 秒音频，循环点设 10 秒（应生效）")
    print("=" * 64)
    wav = os.path.join(work, "a.wav")
    make_wav(wav, 40.0)
    with open(wav, "rb") as f:
        r = server.bgm_replace(idx, f.read())
    print("  bgm_replace -> %s" % r)
    check(abs(r.get("seconds", 0) - 40.0) < 0.2, "替换后时长约 40s",
          str(r.get("seconds")))

    loop_a = int(10.0 * B.BYTES_PER_SEC) // 4 * 4
    server.bgm_set_loop(idx, loop_a)
    res = server.save_all("combo A")
    print("  save_all -> ok=%s bgm=%s errors=%s"
          % (res.get("ok"), res.get("bgm"), res.get("errors")))
    check(res.get("ok"), "保存成功")
    check(not res.get("errors"), "无错误", str(res.get("errors")))

    fmt = read_fmt_from(os.path.join(game, "th12.dat"))
    t = fmt.tracks[idx]
    print("  落盘: name=%s loop=%d (%.2fs) preload=%d end=%d"
          % (t.name, t.loop, t.loop / float(B.BYTES_PER_SEC),
             t.preload, t.end))
    check(t.loop == int(40.0 * B.BYTES_PER_SEC) - loop_a,
          "场景A 循环点字段 = 循环体长度",
          "期望 %d 实际 %d" % (int(40.0 * B.BYTES_PER_SEC) - loop_a, t.loop))
    check(t.end == 2 * (int(40.0 * B.BYTES_PER_SEC) - loop_a),
          "场景A 音频 = 2 × 循环体",
          "%d vs %d" % (t.end, 2 * (int(40.0 * B.BYTES_PER_SEC) - loop_a)))

    # ---------- 场景 B：循环点超出新长度，应被【拒绝】而不是静默清零 ----------
    print("\n" + "=" * 64)
    print("场景 B：替换成 20 秒音频后，设 900 秒循环点（应被拒绝）")
    print("=" * 64)
    wav2 = os.path.join(work, "b.wav")
    make_wav(wav2, 20.0)
    with open(wav2, "rb") as f:
        server.bgm_replace(idx, f.read())
    loop_b = int(900.0 * B.BYTES_PER_SEC) // 4 * 4   # 900 秒，远超 20 秒
    rejected = False
    msg = ""
    try:
        server.bgm_set_loop(idx, loop_b)
    except Exception as ex:
        rejected = True
        msg = str(ex)
    print("  900 秒循环点 -> %s" % ("被拒绝: " + msg[:70] if rejected
                                    else "竟然接受了！"))
    check(rejected, "超长循环点被拒绝（不再静默清零）")

    # 合法的值仍应接受
    loop_b2 = int(10.0 * B.BYTES_PER_SEC) // 4 * 4
    r2 = server.bgm_set_loop(idx, loop_b2)
    check(r2.get("ok") and r2.get("loop") == loop_b2, "20 秒内设 10 秒仍可接受")
    res = server.save_all("combo B")
    print("  save_all -> ok=%s errors=%s" % (res.get("ok"), res.get("errors")))
    fmt = read_fmt_from(os.path.join(game, "th12.dat"))
    t = fmt.tracks[idx]
    print("  落盘: loop=%d (%.2fs) preload=%d end=%d"
          % (t.loop, t.loop / float(B.BYTES_PER_SEC), t.preload, t.end))
    check(t.loop == loop_b2, "合法循环点正常落盘",
          "期望 %d 实际 %d" % (loop_b2, t.loop))
    check(t.preload > t.end, "preload 保留了余量（与原版约定一致）",
          "preload=%d end=%d" % (t.preload, t.end))

    # ---------- 场景 C：thbgm.dat 的实际大小 vs 所有轨道总长 ----------
    print("\n" + "=" * 64)
    print("场景 C：thbgm.dat 完整性")
    print("=" * 64)
    size = os.path.getsize(os.path.join(game, "thbgm.dat"))
    fmt = read_fmt_from(os.path.join(game, "th12.dat"))
    # offset 已经是从文件开头算起的绝对位置（第一条 = 16 = 头长度），
    # 所以需要的最小文件长度就是 max(offset + end)，不要再加头。
    need = max(t.offset + t.end for t in fmt.tracks)
    print("  文件 %d 字节；fmt 要求至少 %d 字节" % (size, need))
    check(size >= need, "thbgm.dat 不小于 fmt 声明的大小",
          "差 %d 字节" % (need - size))
    # 每条轨道是否首尾相接、无重叠
    srt = sorted(fmt.tracks, key=lambda x: x.offset)
    ok = True
    detail = ""
    for i in range(len(srt) - 1):
        if srt[i].offset + srt[i].end > srt[i + 1].offset:
            ok = False
            detail = "%s 与 %s 重叠" % (srt[i].name, srt[i + 1].name)
            break
    check(ok, "各轨道区间不重叠", detail)

    # ---------- 场景 D：两个档的 fmt 是否一致 ----------
    print("\n" + "=" * 64)
    print("场景 D：jp / cn 两个档的 fmt 是否一致")
    print("=" * 64)
    f_jp = read_fmt_from(os.path.join(game, "th12.dat"))
    f_cn = read_fmt_from(os.path.join(game, "th12c.dat"))
    same = all(a.loop == b.loop and a.offset == b.offset and a.end == b.end
               for a, b in zip(f_jp.tracks, f_cn.tracks))
    print("  jp[%d].loop=%d  cn[%d].loop=%d"
          % (idx, f_jp.tracks[idx].loop, idx, f_cn.tracks[idx].loop))
    check(same, "两个档的 fmt 完全一致")

    print("\n" + "=" * 64)
    print("%d 项检查，%d 失败" % (checks[0], len(fails)))
    for f in fails:
        print("  - %s" % f)
    shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
