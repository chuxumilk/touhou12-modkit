# -*- coding: utf-8 -*-
"""反复修改循环点 / 替换 —— 覆盖真实使用中的各种顺序。

这是交付前的重点：用户会来回改很多次，任何一次算错都会让音频越拼越长
或内容错位。所有场景都在真实游戏目录的**副本**上跑。

覆盖的顺序：
  1. 连改 5 次循环点（不替换），每次保存后长度都必须 = 原长 + 引子
  2. 替换音频 → 改循环点 → 再替换 → 再改循环点
  3. 改循环点 → 放弃暂存 → 重新改（放弃后必须回到干净状态）
  4. 不保存直接改（暂存态）反复覆盖，只有最后一次生效
  5. 保存后再改，重新从原始音频算（不叠加）
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
    print("SKIP: 需要 TH12_PRISTINE_DIR 或 TH12_GAME_DIR")
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


def make_wav(path, seconds, freq=440):
    n = int(seconds * 44100)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        buf = bytearray()
        for i in range(n):
            v = int(8000 * (((i * freq) // 44100) % 2 * 2 - 1))
            buf += struct.pack("<hh", v, v)
        w.writeframes(bytes(buf))
    return path


def read_fmt(arch_path):
    a = archive.Archive.from_file(arch_path)
    return B.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))


def read_pcm(dat_path, track):
    with open(dat_path, "rb") as f:
        f.seek(track.offset)
        return f.read(track.end)


def main():
    work = tempfile.mkdtemp(prefix="th12_repeat_")
    game = os.path.join(work, "game")
    os.makedirs(game)
    for n in os.listdir(SRC):
        if n in ("th12.dat", "th12c.dat", "thbgm.dat"):
            shutil.copy2(os.path.join(SRC, n), os.path.join(game, n))

    import server  # noqa: E402
    server.set_game_dir(game)
    server.STATE.invalidate()

    dat = os.path.join(game, "th12.dat")
    data = os.path.join(game, "thbgm.dat")
    tracks = server.list_bgm()["tracks"]
    idx = 0
    for t in tracks:
        if t["duration"] > 40:
            idx = t["index"]
            break
    t0 = [t for t in tracks if t["index"] == idx][0]
    print("目标曲目 #%d %s  原长 %.2f 秒\n" % (idx, t0["name"], t0["duration"]))

    fmt0 = read_fmt(dat)
    orig_len = fmt0.tracks[idx].end
    orig_pcm = read_pcm(data, fmt0.tracks[idx])
    print("原曲 PCM %d 字节 (%.2f 秒)" % (orig_len, orig_len / float(BPS)))

    # ---------------- 1) 连改 5 次循环点 ----------------
    print("\n[1] 连改 5 次循环点，每次都保存（长度必须恒为 原长 + 引子）")
    for k, sec in enumerate((5.0, 12.0, 3.0, 20.0, 8.0)):
        lb = int(sec * BPS) // 4 * 4
        server.bgm_set_loop(idx, lb)
        res = server.save_all("repeat %d" % k)
        if not res.get("ok") or res.get("errors"):
            check(False, "第 %d 次保存成功" % (k + 1), str(res.get("errors")))
            continue
        fmt = read_fmt(dat)
        t = fmt.tracks[idx]
        pcm = read_pcm(data, t)
        body = orig_pcm[lb:]                 # 循环体 = 原始音频去掉前奏
        want = 2 * len(body)
        ok_len = len(pcm) == want
        # 内容必须等于「循环体重复两遍」，且源始终是原始音频（不叠加）
        want_pcm = body + body
        ok_content = pcm == want_pcm
        print("    第%d次 %.2fs -> %d 字节 (期望 %d = 2×循环体 %d) loop=%d"
              % (k + 1, sec, len(pcm), want, len(body), t.loop))
        check(ok_len, "第%d次 长度 = 2 × 循环体" % (k + 1),
              "%d vs %d" % (len(pcm), want))
        check(ok_content, "第%d次 内容 = 原始音频重拼（没叠加）" % (k + 1))
        check(t.loop == len(body), "第%d次 loop = 循环体长度" % (k + 1),
              "%d vs %d" % (t.loop, len(body)))
        check(t.preload >= len(pcm), "第%d次 preload 覆盖整条" % (k + 1))

    # ---------------- 2) 替换 → 改循环点 → 再替换 → 再改 ----------------
    print("\n[2] 替换 → 改循环点 → 再替换 → 再改（两种操作交叉）")
    w1 = make_wav(os.path.join(work, "a.wav"), 30.0, 300)
    with open(w1, "rb") as f:
        r = server.bgm_replace(idx, f.read())
    n1 = int(30.0 * BPS)
    server.bgm_set_loop(idx, int(6.0 * BPS) // 4 * 4)
    res = server.save_all("交叉1")
    check(res.get("ok") and not res.get("errors"), "交叉1 保存成功",
          str(res.get("errors")))
    fmt = read_fmt(dat)
    t = fmt.tracks[idx]
    print("    替换 30s，循环点 6s -> %d 字节 (期望 %d = 2×24s)"
          % (t.end, 2 * (n1 - int(6.0 * BPS))))
    check(abs(t.end - 2 * (n1 - int(6.0 * BPS))) < 44100,
          "长度 = 2 × (新音频 30s − 循环点 6s)", "%d" % t.end)
    check(t.loop == n1 - int(6.0 * BPS), "loop = 循环体 24 秒",
          "%d" % t.loop)

    # 再替换（更短），之前的拼接结果不能残留
    w2 = make_wav(os.path.join(work, "b.wav"), 15.0, 700)
    with open(w2, "rb") as f:
        server.bgm_replace(idx, f.read())
    server.bgm_set_loop(idx, int(4.0 * BPS) // 4 * 4)
    res = server.save_all("交叉2")
    check(res.get("ok") and not res.get("errors"), "交叉2 保存成功",
          str(res.get("errors")))
    fmt = read_fmt(dat)
    t = fmt.tracks[idx]
    n2 = int(15.0 * BPS)
    print("    再替换 15s，循环点 4s -> %d 字节 (期望 %d = 2×11s)"
          % (t.end, 2 * (n2 - int(4.0 * BPS))))
    check(abs(t.end - 2 * (n2 - int(4.0 * BPS))) < 44100,
          "二次替换后长度 = 2 ×（新音频 − 新循环点），无残留", "%d" % t.end)

    # ---------------- 3) 放弃暂存后重新改 ----------------
    print("\n[3] 改循环点 → 放弃暂存 → 重新改（放弃后必须干净）")
    server.bgm_set_loop(idx, int(7.0 * BPS) // 4 * 4)
    pend = server.list_pending()
    check(pend["count"] > 0, "放弃前有待保存项", str(pend["count"]))
    server.bgm_cancel()
    pend2 = server.list_pending()
    check(pend2["count"] == 0, "放弃后待保存清零", str(pend2["count"]))
    # 放弃后重新设一个并保存：基准是「盘上当前的音频」（也就是放弃时那份）
    cur_len = read_fmt(dat).tracks[idx].end
    server.bgm_set_loop(idx, int(2.0 * BPS) // 4 * 4)
    res = server.save_all("放弃后重设")
    fmt = read_fmt(dat)
    t = fmt.tracks[idx]
    want3 = 2 * (cur_len - int(2.0 * BPS))
    print("    放弃后重设 2s -> %d 字节 (期望 %d = 2×(%d−2s))"
          % (t.end, want3, cur_len))
    check(res.get("ok"), "放弃后重设保存成功", str(res.get("errors")))
    check(t.end == want3, "长度按放弃时盘上音频重算",
          "%d vs %d" % (t.end, want3))

    # ---------------- 4) 暂存态反复覆盖 ----------------
    print("\n[4] 不保存，连改 6 次（只有最后一次生效）")
    # 基准必须是**拼接的源**（未拼接的原始音频），不是文件总长：
    # 上一步的拼接已经改过盘上的音频，拿它当基准会算错。
    origin = server.STATE.bgm_origins.get(idx)
    base = os.path.getsize(origin) if origin else read_fmt(dat).tracks[idx].end
    print("    拼接源（未拼接的原始音频）= %d 字节" % base)
    for sec in (3.0, 9.0, 5.0, 15.0, 7.0, 10.0):
        server.bgm_set_loop(idx, int(sec * BPS) // 4 * 4)
    pend = server.list_pending()
    check(pend["count"] == 1, "6 次改动只算 1 项待保存", str(pend["count"]))
    res = server.save_all("只留最后一次")
    fmt = read_fmt(dat)
    t = fmt.tracks[idx]
    want4 = 2 * (base - int(10.0 * BPS))
    want_loop4 = base - int(10.0 * BPS)
    print("    连改 6 次后保存 -> loop=%d (期望 %d) len=%d (期望 %d)"
          % (t.loop, want_loop4, t.end, want4))
    check(t.loop == want_loop4, "只有最后一次生效（loop = 源长 − 10 秒）",
          "%d vs %d" % (t.loop, want_loop4))
    check(t.end == want4, "长度按最后一次算",
          "%d vs %d" % (t.end, want4))

    # ---------------- 5) 两个档必须一致 ----------------
    print("\n[5] 日文版/汉化版两个档的 fmt 必须一致")
    f_jp = read_fmt(os.path.join(game, "th12.dat"))
    f_cn = read_fmt(os.path.join(game, "th12c.dat"))
    same = all(a.loop == b.loop and a.offset == b.offset and a.end == b.end
               for a, b in zip(f_jp.tracks, f_cn.tracks))
    print("    jp[%d]: loop=%d end=%d" % (idx, f_jp.tracks[idx].loop,
                                          f_jp.tracks[idx].end))
    print("    cn[%d]: loop=%d end=%d" % (idx, f_cn.tracks[idx].loop,
                                          f_cn.tracks[idx].end))
    check(same, "两个档完全一致")

    # ---------------- 6) thbgm.dat 完整性 ----------------
    print("\n[6] thbgm.dat 与 fmt 是否自洽")
    size = os.path.getsize(data)
    need = max(t.offset + t.end for t in f_jp.tracks)
    print("    文件 %d 字节，fmt 要求至少 %d 字节" % (size, need))
    check(size >= need, "文件不小于 fmt 声明的大小", "差 %d" % (need - size))
    srt = sorted(f_jp.tracks, key=lambda x: x.offset)
    ok = all(srt[i].offset + srt[i].end <= srt[i + 1].offset
             for i in range(len(srt) - 1))
    check(ok, "各轨道区间不重叠")

    print("\n" + "=" * 66)
    print("%d 项检查，%d 失败" % (checks[0], len(fails)))
    for f in fails:
        print("  - %s" % f)
    shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
