# -*- coding: utf-8 -*-
"""TH12 BGM 数据 (thbgm.dat + thbgm.fmt) 解析与替换。

``thbgm.dat`` 布局::

    0x00  "ZWAV" + u32 version(=1) + u32(=0x1200) + u32(=0)
    0x10  轨道 0 的 PCM（16bit 立体声 44100Hz，无 WAV 头）
          ...轨道 1、2 ...

``thbgm.fmt``（存放在 th12.dat / th12c.dat 归档里）每条 0x34 字节::

    0x00  char name[16]      "th12_01.wav"
    0x10  u32 offset         在 thbgm.dat 中的绝对偏移
    0x14  u32 preload        预读字节数（游戏开播前读入内存的缓冲大小）
    0x18  u32 loop           循环起点（相对 offset 的字节数，4 的倍数）
    0x1C  u32 end            轨道长度（字节）
    0x20  WAVEFORMATEX       16 字节: tag, channels, rate, avg, align, bits
    0x30  u32 zero
"""

import os
import shutil
import struct

#: thbgm.dat 头部长度（轨道数据从 0x10 开始）
DATA_HEADER_SIZE = 16
#: thbgm.fmt 单条大小
ENTRY_SIZE = 0x34
#: 默认音频格式
SAMPLE_RATE = 44100
CHANNELS = 2
BITS = 16
BLOCK_ALIGN = CHANNELS * BITS // 8
BYTES_PER_SEC = SAMPLE_RATE * BLOCK_ALIGN


class BgmError(Exception):
    """BGM 数据结构不正确。"""


class Track(object):
    """thbgm.fmt 中的一条曲目。"""

    __slots__ = ("index", "name", "offset", "preload", "loop", "end",
                 "format_tag", "channels", "sample_rate", "avg_bytes",
                 "block_align", "bits", "zero", "extra")

    def __init__(self, index=0):
        self.index = index
        self.name = ""
        self.offset = DATA_HEADER_SIZE
        self.preload = 0
        self.loop = 0
        self.end = 0
        self.format_tag = 1
        self.channels = CHANNELS
        self.sample_rate = SAMPLE_RATE
        self.avg_bytes = BYTES_PER_SEC
        self.block_align = BLOCK_ALIGN
        self.bits = BITS
        self.zero = 0
        self.extra = b""          # 原始 0x34 字节（用于保留未知字段）

    @property
    def duration(self):
        """时长（秒）。"""
        if self.avg_bytes:
            return self.end / float(self.avg_bytes)
        return 0.0

    @property
    def loop_seconds(self):
        if self.avg_bytes:
            return self.loop / float(self.avg_bytes)
        return 0.0

    def __repr__(self):
        return "<Track %d %s %d bytes loop=%d>" % (
            self.index, self.name, self.end, self.loop)


class BgmFmt(object):
    """解析后的 thbgm.fmt。"""

    def __init__(self):
        self.tracks = []
        self.tail = b""       # 末尾多余字节（原样保留）

    @classmethod
    def from_bytes(cls, data):
        obj = cls()
        pos = 0
        index = 0
        while pos + ENTRY_SIZE <= len(data):
            name_raw = data[pos:pos + 16].split(b"\0", 1)[0]
            if not name_raw:
                break
            t = Track(index)
            t.name = name_raw.decode("cp932", "replace")
            (t.offset, t.preload, t.loop, t.end) = \
                struct.unpack_from("<IIII", data, pos + 0x10)
            (t.format_tag, t.channels, t.sample_rate, t.avg_bytes,
             t.block_align, t.bits, t.zero) = \
                struct.unpack_from("<HHIIHHI", data, pos + 0x20)
            t.extra = bytes(data[pos:pos + ENTRY_SIZE])
            obj.tracks.append(t)
            pos += ENTRY_SIZE
            index += 1
        obj.tail = bytes(data[pos:])
        return obj

    @classmethod
    def from_file(cls, path):
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    def to_bytes(self):
        out = bytearray()
        for t in self.tracks:
            raw = bytearray(t.extra) if len(t.extra) == ENTRY_SIZE \
                else bytearray(ENTRY_SIZE)
            name = t.name.encode("cp932", "replace")[:15]
            raw[0:16] = name + b"\0" * (16 - len(name))
            struct.pack_into("<IIII", raw, 0x10,
                             t.offset, t.preload, t.loop, t.end)
            struct.pack_into("<HHIIHHI", raw, 0x20,
                             t.format_tag, t.channels, t.sample_rate,
                             t.avg_bytes, t.block_align, t.bits, t.zero)
            out += raw
        out += self.tail
        return bytes(out)

    def index_of(self, name):
        for i, t in enumerate(self.tracks):
            if t.name.lower() == name.lower():
                return i
        return -1


def read_data_header(path):
    """读取 thbgm.dat 的 16 字节头部。"""
    with open(path, "rb") as f:
        head = f.read(DATA_HEADER_SIZE)
    if len(head) < DATA_HEADER_SIZE:
        raise BgmError("thbgm.dat 过小")
    return head


def read_track_pcm(path, track):
    """读出某条曲目的 PCM 字节。"""
    with open(path, "rb") as f:
        f.seek(track.offset)
        data = f.read(track.end)
    if len(data) != track.end:
        raise BgmError("轨道 %s 数据被截断" % track.name)
    return data


def wav_header(pcm_size):
    """生成标准 44 字节 WAV 头。"""
    return (b"RIFF" + struct.pack("<I", 36 + pcm_size) + b"WAVE" +
            b"fmt " + struct.pack("<IHHIIHH", 16, 1, CHANNELS, SAMPLE_RATE,
                                  BYTES_PER_SEC, BLOCK_ALIGN, BITS) +
            b"data" + struct.pack("<I", pcm_size))


def to_wav(pcm):
    """给 PCM 数据套上 WAV 头。"""
    return wav_header(len(pcm)) + pcm


def parse_wav(data):
    """解析 WAV，返回 (pcm_bytes, sample_rate, channels, bits)。

    仅处理未压缩 PCM；其他格式抛 BgmError。
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise BgmError("不是 WAV 文件")
    pos = 12
    fmt = None
    pcm = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csize = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + csize]
        if cid == b"fmt ":
            if len(body) < 16:
                raise BgmError("WAV fmt 块损坏")
            (tag, ch, rate, avg, align, bits) = \
                struct.unpack_from("<HHIIHH", body, 0)
            fmt = (tag, ch, rate, avg, align, bits)
        elif cid == b"data":
            pcm = body
        pos += 8 + csize + (csize & 1)
    if fmt is None or pcm is None:
        raise BgmError("WAV 缺少 fmt/data 块")
    if fmt[0] != 1:
        raise BgmError("只支持未压缩 PCM WAV（当前格式 %d）" % fmt[0])
    return pcm, fmt[2], fmt[1], fmt[5]


def convert_to_bgm_pcm(data):
    """把任意 PCM WAV 转成 TH12 需要的 16bit/立体声/44100Hz PCM。"""
    import audioop
    pcm, rate, channels, bits = parse_wav(data)
    if bits != 16:
        pcm = audioop.lin2lin(pcm, bits // 8, 2)
        bits = 16
    if channels == 1:
        pcm = audioop.tostereo(pcm, 2, 1, 1)
        channels = 2
    elif channels > 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        pcm = audioop.tostereo(pcm, 2, 1, 1)
        channels = 2
    if rate != SAMPLE_RATE:
        pcm, _ = audioop.ratecv(pcm, 2, channels, rate, SAMPLE_RATE, None)
    # 长度对齐到 4 字节
    if len(pcm) % 4:
        pcm += b"\0" * (4 - len(pcm) % 4)
    return pcm


def rebuild_bgm_dat(src_path, dst_path, replacements, fmt,
                    progress=None):
    """重建 thbgm.dat。

    :param src_path: 原 thbgm.dat
    :param dst_path: 输出路径
    :param replacements: {轨道下标: pcm_bytes}；未提供的轨道原样复制
    :param fmt: BgmFmt，会就地更新 offset/preload/end
    :param progress: 可选回调 progress(done, total)
    :return: None
    """
    total = len(fmt.tracks)
    head = read_data_header(src_path)
    src = open(src_path, "rb")
    try:
        tmp = dst_path + ".tmp"
        with open(tmp, "wb") as out:
            out.write(head)
            offset = DATA_HEADER_SIZE
            for i, track in enumerate(fmt.tracks):
                out.seek(offset)
                pcm = replacements.get(i)
                if pcm is None:
                    src.seek(track.offset)
                    remaining = track.end
                    while remaining > 0:
                        chunk = src.read(min(1 << 20, remaining))
                        if not chunk:
                            raise BgmError("源文件在轨道 %s 处提前结束"
                                           % track.name)
                        out.write(chunk)
                        remaining -= len(chunk)
                    length = track.end
                elif isinstance(pcm, (bytes, bytearray)):
                    out.write(pcm)
                    length = len(pcm)
                else:
                    # 传入了暂存文件路径
                    with open(pcm, "rb") as f:
                        shutil.copyfileobj(f, out, 1 << 20)
                    length = os.path.getsize(pcm)
                track.offset = offset
                track.end = length
                if pcm is not None:
                    # 预读整个轨道；循环点保持用户设定（默认 0）
                    track.preload = length
                    if track.loop > length:
                        track.loop = 0
                offset += length
                if progress:
                    progress(i + 1, total)
        os.replace(tmp, dst_path)
    finally:
        src.close()
