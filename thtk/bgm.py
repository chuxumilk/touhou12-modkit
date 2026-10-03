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

    会严格校验每个 chunk 声明的长度没有超出文件实际大小。以前不校验，
    截断的 WAV（下载中断、复制不全）会被**静默接受**并产出半截音频，
    用户拿到一条长度不对的 BGM 却看不到任何提示。
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise BgmError("不是 WAV 文件")
    pos = 12
    fmt = None
    pcm = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csize = struct.unpack_from("<I", data, pos + 4)[0]
        avail = len(data) - (pos + 8)
        if csize > avail:
            raise BgmError(
                "WAV 文件不完整：%s 块声明 %d 字节，实际只剩 %d 字节"
                "（文件可能没下载完或被截断）"
                % (cid.decode("latin1", "replace"), csize, avail))
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
    if not pcm:
        raise BgmError("WAV 的 data 块是空的")
    return pcm, fmt[2], fmt[1], fmt[5]


def convert_to_bgm_pcm(data):
    """把任意 PCM WAV 转成 TH12 需要的 16bit/立体声/44100Hz PCM。

    转换顺序很关键：**先把声道数规整成 2，再做位深转换**。
    反过来（先位深后声道）会错：`audioop.tostereo()` 是单声道语义的，
    对已经是立体声的数据会按「单声道样本数」重新解释，帧数直接翻倍。
    """
    import audioop
    pcm, rate, channels, bits = parse_wav(data)
    if channels > 2:
        # 多声道先下混成立体声（保持原位深，宽度对得上）
        pcm = audioop.tomono(pcm, bits // 8, 0.5, 0.5)
        pcm = audioop.tostereo(pcm, bits // 8, 1, 1)
        channels = 2
    elif channels == 1:
        pcm = audioop.tostereo(pcm, bits // 8, 1, 1)
        channels = 2
    if bits != 16:
        pcm = audioop.lin2lin(pcm, bits // 8, 2)
        bits = 16
    if rate != SAMPLE_RATE:
        pcm, _ = audioop.ratecv(pcm, 2, channels, rate, SAMPLE_RATE, None)
    # 长度对齐到 4 字节
    if len(pcm) % 4:
        pcm += b"\0" * (4 - len(pcm) % 4)
    return pcm


BYTES_PER_SEC = SAMPLE_RATE * 4          # 16bit 立体声 = 每采样 4 字节
# 循环体最短长度。太短的循环听上去像卡带，而且拼接后总长几乎不变，
# 用户会以为「没生效」，所以给一个下限并在接口层直接拒绝。
MIN_LOOP_SECONDS = 0.5


def align4(n):
    """把字节数向下对齐到 4（一个立体声 16bit 采样）。"""
    return int(n) - int(n) % 4


def splice_loop_pcm(pcm, loop_bytes):
    """把 PCM 变成「整个轨道就是循环体」，让游戏真的在这里无限循环。

    为什么必须动音频：th12.exe 的 BGM 引擎只把 offset 起的 total_len 字节
    读进内存后**整块循环播放**，从不读取 +0x18 的循环点字段
    （见 _recon/th12_loop_decisive.py 的三重取证）。所以只改 fmt 字段没用。

    引擎的循环方式决定了正确做法：
        它播完整个缓冲后回到缓冲**开头**重放。
    因此只要让「缓冲内容」本身等于我们想反复听的那一段，就实现了循环。

    设循环体 = [L, end)，其中 L 是用户指定的循环点。产出：

        [ 循环体 ][ 循环体 ]        长度 = 2 × (end - L)
         0..B      B..2B            B = end - L

    并把 fmt 里的循环点写成 B。这样：
        · 引擎若从缓冲开头循环（实测如此）→ 从头到尾听到的都是循环体
        · 引擎若在 loop 字段处循环（保险起见）→ 跳回 B 处仍是循环体

    **以前的做法是「引子 + 循环体 + 引子」，把循环点当成「引子长度」，
    结果循环点设得越靠后、引子越长、循环感越弱** —— 实测用户把循环点设在
    208.5 秒曲子的 194.5 秒处时，要等三分多钟才听得到那 14 秒开始重复，
    主观上等同于「没生效」。现在改成丢掉循环点之前的前奏，保证一进游戏
    就在循环。

    :param pcm: 裸 PCM（16bit 立体声 44100Hz，按字节切即可）
    :param loop_bytes: 循环点 L，相对轨道起点的字节数
    :return: (新的 PCM, 新的循环点字节数)
             loop_bytes 不在 (0, len) 内时按「整首都循环」处理
    """
    total = len(pcm)
    if total <= 0:
        return pcm, 0
    loop_bytes = align4(loop_bytes)
    if loop_bytes <= 0 or loop_bytes >= total:
        # 循环点在最开头（或未设）＝ 整首循环，内容不用动，
        # 但要把循环点写成 0，让引擎从缓冲开头重放。
        return pcm, 0
    body = pcm[loop_bytes:]
    return body + body, len(body)


def rebuild_bgm_dat(src_path, dst_path, replacements, fmt,
                    loops=None, origins=None, progress=None):
    """重建 thbgm.dat。

    :param src_path: 原 thbgm.dat
    :param dst_path: 输出路径
    :param replacements: {轨道下标: pcm_bytes 或暂存文件路径}；未提供的轨道
        原样复制（除非该轨道出现在 loops 里，那时需要读出来做拼接）
    :param fmt: BgmFmt，会就地更新 offset/preload/end/loop
    :param loops: {轨道下标: 循环点字节数}。**显式列为待拼接**：只要某条轨道
        出现在这里，它的音频就会被拼成「引子 + 循环体」，从而让游戏真的在
        这个位置循环（原因见 splice_loop_pcm 的说明）。不在其中的轨道保持
        原样，绝不改动 —— 这是「没设循环点的曲子不会被碰」的保证。
    :param origins: {轨道下标: 那份「尚未拼接过的原始 PCM」的文件路径}。
        拼接是对源音频做的，所以带拼接的轨道必须在暂存里留一份原始音频：
        否则用户改第二次循环点时，源已经含上一次的拼接结果，会越拼越长。
        传了 origins 的轨道一律以该文件为准，并跳过 replacements。
    :param progress: 可选回调 progress(done, total)
    :return: None
    """
    loops = loops or {}
    origins = origins or {}
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
                loop = loops.get(i)
                origin = origins.get(i)
                has_loop = i in loops          # 显式设定过（0 也算）
                if origin:
                    # 暂存里有「未拼接过的原始音频」，一律以它为准：
                    # 这样反复改循环点不会把上一次的拼接结果再拼一遍
                    with open(origin, "rb") as f:
                        pcm = f.read()
                if pcm is None and not has_loop:
                    # 没动过这条：原样复制，连循环点字段都不碰
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
                else:
                    if pcm is None:
                        # 只改循环点：把原音频读出来再拼
                        src.seek(track.offset)
                        pcm = src.read(track.end)
                        if len(pcm) != track.end:
                            raise BgmError("源文件在轨道 %s 处提前结束"
                                           % track.name)
                    elif not isinstance(pcm, (bytes, bytearray)):
                        # 传入了暂存文件路径
                        with open(pcm, "rb") as f:
                            pcm = f.read()
                    if has_loop:
                        pcm, new_loop = splice_loop_pcm(pcm, loop)
                    else:
                        new_loop = 0
                    out.write(pcm)
                    length = len(pcm)
                    # 拼接后轨道内容就是「循环体重复两遍」，循环点写在
                    # 循环体长度处：无论引擎从缓冲开头重放（实测如此）还是
                    # 在 loop 字段处回跳，听到的都是循环体，行为一致。
                    track.loop = new_loop
                track.offset = offset
                track.end = length
                if pcm is not None:
                    # 预读整条轨道，并保留余量。
                    # 原版 18 条轨道的 unknown(+0x14) 一律是 total_len 的
                    # 1.03~1.27 倍，也就是「预读缓冲必须覆盖整段音频」。
                    # 引擎只把它读进内存后整块循环，所以这里必须 >= length，
                    # 否则循环体会被截断。
                    track.preload = max(int(length * 1.1), length)
                offset += length
                if progress:
                    progress(i + 1, total)
        os.replace(tmp, dst_path)
    finally:
        src.close()
