# -*- coding: utf-8 -*-
"""替换 BGM 的输入格式兼容性测试。

用户实际会拖进来的音频格式五花八门，这个套件覆盖常见情况：
  单声道 / 8bit / 24bit / 32bit / 48kHz / 22050Hz / 96kHz
  / 带额外 chunk 的 WAV / 奇数长度 / 非 PCM（应明确拒绝）
以及异常输入：空文件、截断 WAV、非 WAV 文件。

重点验证：**转换后的 PCM 必须严格是 16bit / 立体声 / 44100Hz**，
且长度与源音频时长吻合（音高不变形 = 采样率换算正确）。
"""
import math
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

from thtk import bgm as B  # noqa: E402

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


def make_wav(path, seconds, rate=44100, channels=2, bits=16, freq=330,
             extra_chunk=False, tag=1):
    """按指定参数生成 WAV（可插入额外 chunk / 伪造非 PCM 格式）。"""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        s = math.sin(2 * math.pi * freq * i / rate)
        if bits == 8:
            v = int(128 + 100 * s)
            v = max(0, min(255, v))
            for _ in range(channels):       # 每声道 1 字节
                frames.append(v)
        elif bits == 16:
            iv = int(9000 * s)
            for _ in range(channels):
                frames += struct.pack("<h", iv)
        elif bits == 24:
            iv = int(2000000 * s)
            b = iv.to_bytes(3, "little", signed=True)
            for _ in range(channels):
                frames += b
        elif bits == 32:
            iv = int(500000000 * s)
            for _ in range(channels):
                frames += struct.pack("<i", iv)
    data = bytes(frames)
    block = channels * (bits // 8)
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if extra_chunk:
        # 加一个 LIST 块，并让它的长度为奇数以测试补位处理
        lst = b"INFOabc"
        chunks += b"LIST" + struct.pack("<I", len(lst)) + lst + b"\x00"
    chunks += b"data" + struct.pack("<I", len(data)) + data
    if len(data) & 1:
        chunks += b"\x00"
    body = b"WAVE" + chunks
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def conv(path):
    with open(path, "rb") as f:
        return B.convert_to_bgm_pcm(f.read())


def main():
    work = tempfile.mkdtemp(prefix="th12_fmt_")
    SEC = 3.0

    # 期望值必须**按输入位深**算：同样 3 秒，8bit 立体声只有 264,600 字节，
    # 转成 16bit 立体声才是 529,200 字节。以前这里写死 529,200，
    # 把本来正确的 8bit 转换误报成失败。
    expect = int(SEC * BPS)

    print("目标：任意输入都要转成 16bit/立体声/44100Hz，"
          "时长 = %.1f 秒 = %d 字节\n" % (SEC, expect))

    cases = [
        ("标准 16bit 立体声 44100", dict()),
        ("单声道 16bit 44100", dict(channels=1)),
        ("8bit 立体声 44100", dict(bits=8)),
        ("8bit 单声道 44100", dict(bits=8, channels=1)),
        ("24bit 立体声 44100", dict(bits=24)),
        ("32bit 立体声 44100", dict(bits=32)),
        ("16bit 立体声 48000", dict(rate=48000)),
        ("16bit 立体声 22050", dict(rate=22050)),
        ("16bit 立体声 96000", dict(rate=96000)),
        ("8bit 单声道 8000", dict(rate=8000, channels=1, bits=8)),
        ("带额外 chunk（奇数长度）", dict(extra_chunk=True)),
        ("24bit 单声道 48000", dict(rate=48000, channels=1, bits=24)),
    ]
    for name, kw in cases:
        p = os.path.join(work, "t.wav")
        make_wav(p, SEC, **kw)
        try:
            pcm = conv(p)
        except Exception as ex:
            check(False, "%s 转换成功" % name, str(ex))
            continue
        # 时长必须吻合（允许重采样引入的少量误差）
        tol = 2205 * 4
        ok_len = abs(len(pcm) - expect) <= tol
        ok_align = len(pcm) % 4 == 0
        print("    %-26s -> %8d 字节 (%.3f 秒)  对齐=%s"
              % (name, len(pcm), len(pcm) / float(BPS), ok_align))
        check(ok_len, "%s 时长换算正确" % name,
              "%d vs 期望 %d" % (len(pcm), expect))
        check(ok_align, "%s 长度 4 字节对齐" % name)

    # ---------------- 异常输入必须明确拒绝 ----------------
    print("\n异常输入（必须明确报错，不能静默产出坏数据）")
    bad = []

    p = os.path.join(work, "bad1.wav")
    make_wav(p, 1.0, tag=3)          # IEEE float 标记
    bad.append(("非 PCM（format tag=3）", p))

    p2 = os.path.join(work, "bad2.wav")
    with open(p2, "wb") as f:
        f.write(b"NOTAWAVFILE" + b"\x00" * 40)
    bad.append(("不是 WAV", p2))

    p3 = os.path.join(work, "bad3.wav")
    good = os.path.join(work, "t.wav")
    make_wav(good, 1.0)
    raw = open(good, "rb").read()
    with open(p3, "wb") as f:
        f.write(raw[:len(raw) // 2])   # 截断
    bad.append(("截断的 WAV", p3))

    p4 = os.path.join(work, "bad4.wav")
    open(p4, "wb").close()
    bad.append(("空文件", p4))

    for name, path in bad:
        try:
            conv(path)
            check(False, "%s 被拒绝" % name, "竟然接受了")
        except B.BgmError as ex:
            check(True, "%s 被拒绝" % name)
            print("      -> %s" % str(ex)[:56])
        except Exception as ex:
            check(False, "%s 抛的是 BgmError" % name,
                  "实际 %s: %s" % (type(ex).__name__, ex))

    # ---------------- 端到端：替换后落盘 ----------------
    print("\n端到端：把一个单声道 48kHz 的 WAV 替换进游戏并保存")
    game = os.path.join(work, "game")
    os.makedirs(game)
    for n in os.listdir(SRC):
        if n in ("th12.dat", "th12c.dat", "thbgm.dat"):
            shutil.copy2(os.path.join(SRC, n), os.path.join(game, n))
    import server  # noqa: E402
    server.set_game_dir(game)
    server.STATE.invalidate()

    odd = os.path.join(work, "odd.wav")
    make_wav(odd, 12.0, rate=48000, channels=1, bits=16)
    with open(odd, "rb") as f:
        r = server.bgm_replace(0, f.read())
    print("    替换: %s" % r)
    check(abs(r.get("seconds", 0) - 12.0) < 0.1, "12 秒单声道 48kHz 被接受",
          str(r.get("seconds")))
    res = server.save_all("fmt test")
    check(res.get("ok") and not res.get("errors"), "保存成功",
          str(res.get("errors")))

    from thtk import archive
    a = archive.Archive.from_file(os.path.join(game, "th12.dat"))
    fmt = B.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))
    t = fmt.tracks[0]
    want = int(12.0 * BPS)
    print("    落盘: end=%d (期望 %d)  loop=%d  preload=%d"
          % (t.end, want, t.loop, t.preload))
    check(abs(t.end - want) <= 2205 * 4, "落盘长度 = 12 秒",
          "%d vs %d" % (t.end, want))
    check(t.channels == 2, "声道数写成 2", str(t.channels))
    check(t.sample_rate == 44100, "采样率写成 44100", str(t.sample_rate))
    check(t.bits == 16, "位深写成 16", str(t.bits))
    check(t.preload >= t.end, "preload 覆盖整条")

    print("\n" + "=" * 66)
    print("%d 项检查，%d 失败" % (checks[0], len(fails)))
    for f in fails:
        print("  - %s" % f)
    shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
