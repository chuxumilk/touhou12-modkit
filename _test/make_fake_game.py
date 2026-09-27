# -*- coding: utf-8 -*-
"""构造一个结构合法的最小 TH12 假游戏目录，用于本地复现「设置游戏目录」问题。

产物：
  _test/gameA/th12.dat    （含 thbgm.fmt / musiccmt.txt 的最小归档）
  _test/gameA/thbgm.dat   （ZWAV 头 + 少量 PCM）
  _test/gameA/th12.exe    （占位，测试启动按钮的存在性判断）
  _test/gameB/th12c.dat   （另一个版本，用于切换测试）
"""
import io
import os
import struct
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS)

from thtk import archive  # noqa: E402

TEST = os.path.join(WS, "_test")


def make_fmt(count=18):
    """thbgm.fmt：每条 0x34 字节。"""
    out = io.BytesIO()
    for i in range(count):
        name = ("bgm_%02d.wav" % i).encode("ascii")
        name = name + b"\x00" * (16 - len(name))
        offset = 16 + i * 44100 * 4
        out.write(name)
        out.write(struct.pack("<IIII", offset, 0, 0, 44100 * 4))
        # WAVEFORMATEX 16 字节：PCM / 2ch / 44100 / 176400 Bps / 4 align / 16 bit
        out.write(struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16))
        out.write(struct.pack("<I", 0))
    return out.getvalue()


def make_archive(extra_name):
    a = archive.Archive.new()
    a.add("thbgm.fmt", make_fmt())
    a.add("musiccmt.txt", "测试用音乐室评论\r\n".encode("shift_jis", "replace"))
    a.add("st01_00a.msg", struct.pack("<I", 0))          # 最小对话文件
    a.add(extra_name, b"dummy payload")
    return a.to_bytes()


def write_game(folder, dat_name, with_exe):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, dat_name)
    with open(p, "wb") as f:
        f.write(make_archive("stage01.ecl"))
    # thbgm.dat：ZWAV + 4 秒静音 PCM
    with open(os.path.join(folder, "thbgm.dat"), "wb") as f:
        f.write(b"ZWAV" + b"\x00" * 12 + b"\x00" * (44100 * 4 * 2))
    if with_exe:
        with open(os.path.join(folder, "th12.exe"), "wb") as f:
            f.write(b"MZ dummy")
    print("生成: %s (%s, %.1f KB)"
          % (folder, dat_name, os.path.getsize(p) / 1024.0))


if __name__ == "__main__":
    write_game(os.path.join(TEST, "gameA"), "th12.dat", True)
    write_game(os.path.join(TEST, "gameB"), "th12c.dat", False)
    # 一个「不是游戏目录」的目录，用于测试报错文案
    os.makedirs(os.path.join(TEST, "notagame"), exist_ok=True)
    print("生成: %s (空目录)" % os.path.join(TEST, "notagame"))
