# -*- coding: utf-8 -*-
"""ZUN 自定义 LZSS —— 对应 thtk 的 ``thtk/thlzss.c`` + ``thtk/bits.c``。

格式规格（摘自 thtk 源码注释，已由 TH12 实测验证）::

    每一条目前 1 个标志位：1 = 后随 8 位字面量；0 = 后随 (偏移, 长度)
    偏移 13 位，长度 4 位，最小匹配 3，故最大匹配 3+15 = 18
    字典 8192 字节，写入从下标 1 开始；字典指针及其前 18 字节不可读
    零偏移 + 零长度 作为数据结束标记

位序为 **MSB-first**（见 bits.c 的 ``bitstream_write1``：``byte <<= 1``）。
"""

LZSS_DICTSIZE = 0x2000
LZSS_DICTSIZE_MASK = 0x1FFF
LZSS_MIN_MATCH = 3
LZSS_MAX_MATCH = 18

HASH_SIZE = 0x10000
HASH_NULL = 0


class LzssError(Exception):
    """LZSS 数据不完整或不符合预期长度（归档可能已损坏）。"""


# --------------------------------------------------------------------------
# 位流
# --------------------------------------------------------------------------
class BitReader(object):
    """MSB-first 位读取器，语义与 thtk 的 ``bitstream_read`` 一致。"""

    __slots__ = ("_data", "_pos", "_byte", "_bits")

    def __init__(self, data):
        self._data = data
        self._pos = 0
        self._byte = 0
        self._bits = 0

    def read(self, nbits):
        while nbits > self._bits:
            if self._pos < len(self._data):
                c = self._data[self._pos]
                self._pos += 1
            else:
                c = 0  # thtk 在文件尾读失败时按 0 处理
            self._byte = ((self._byte << 8) | c) & 0xFFFFFFFF
            self._bits += 8
        self._bits -= nbits
        return (self._byte >> self._bits) & ((1 << nbits) - 1)


class BitWriter(object):
    """MSB-first 位写入器，语义与 thtk 的 ``bitstream_write*`` 一致。"""

    __slots__ = ("_out", "_byte", "_bits", "byte_count")

    def __init__(self):
        self._out = bytearray()
        self._byte = 0
        self._bits = 0
        self.byte_count = 0

    def write1(self, bit):
        self._byte = ((self._byte << 1) | (bit & 1)) & 0xFF
        self._bits += 1
        if self._bits == 8:
            self._out.append(self._byte)
            self._bits = 0
            self._byte = 0
            self.byte_count += 1

    def write(self, nbits, value):
        for i in range(nbits - 1, -1, -1):
            self.write1((value >> i) & 1)

    def finish(self):
        """对应 bitstream_finish：不足一字节则左移补零输出。"""
        if self._bits:
            self._byte = (self._byte << (8 - self._bits)) & 0xFF
            self._out.append(self._byte)
            self._bits = 0
            self._byte = 0
            self.byte_count += 1
        return bytes(self._out)


# --------------------------------------------------------------------------
# 解压
# --------------------------------------------------------------------------
def unlzss(data, output_size):
    """把 ``data`` 解压为**恰好** ``output_size`` 字节。

    对应 thtk 的 ``th_unlzss``。偏移为 0 即遇到结束标记，提前结束。

    与 thtk 的差别：这里会**校验最终长度**。遇到结束标记或数据提前读完时，
    ThBitReader 会按 0 继续喂数据（这是对齐 thtk 的刻意行为），
    若不再校验，损坏/截断的归档会被静默解出短数据、当成正常内容导出，
    错误就顺着下游扩散了。
    """
    dict_ = bytearray(LZSS_DICTSIZE)
    dict_head = 1
    br = BitReader(data)
    out = bytearray(output_size)      # 预分配，避免反复扩容
    written = 0

    while written < output_size:
        if br.read(1):
            c = br.read(8)
            out[written] = c
            written += 1
            dict_[dict_head] = c
            dict_head = (dict_head + 1) & LZSS_DICTSIZE_MASK
        else:
            match_offset = br.read(13)
            if not match_offset:
                break                     # 结束标记
            match_len = br.read(4) + LZSS_MIN_MATCH
            if written + match_len > output_size:
                raise LzssError(
                    "LZSS 数据异常：匹配长度超出目标大小"
                    "（已写出 %d，还要写 %d，目标 %d）"
                    % (written, match_len, output_size))
            for i in range(match_len):
                c = dict_[(match_offset + i) & LZSS_DICTSIZE_MASK]
                out[written] = c
                written += 1
                dict_[dict_head] = c
                dict_head = (dict_head + 1) & LZSS_DICTSIZE_MASK

    if written != output_size:
        raise LzssError(
            "LZSS 数据不完整：期望 %d 字节，只解出 %d 字节"
            "（结束标记提前出现或数据被截断，归档可能已损坏）"
            % (output_size, written))
    return bytes(out)


# --------------------------------------------------------------------------
# 压缩
# --------------------------------------------------------------------------
def _generate_key(array, base):
    return (((array[(base + 1) & LZSS_DICTSIZE_MASK] << 8) |
             array[(base + 2) & LZSS_DICTSIZE_MASK]) ^ (array[base] << 4))


def lzss(data):
    """压缩 ``data``，返回压缩后的 ``bytes``。

    这是 thtk ``th_lzss`` 的忠实翻译（哈希 + 双向链表匹配查找），
    目的不是最强压缩率，而是让输出与 thtk / ZUN 工具**逐字节一致**，
    从而保证游戏能正确读取。
    """
    input_size = len(data)
    hash_ = [HASH_NULL] * HASH_SIZE
    prev = [HASH_NULL] * LZSS_DICTSIZE
    next_ = [HASH_NULL] * LZSS_DICTSIZE
    dict_ = bytearray(LZSS_DICTSIZE)
    dict_head = 1
    waiting_bytes = 0
    bytes_read = 0
    bw = BitWriter()

    # 预读前 LZSS_MAX_MATCH 字节
    for i in range(LZSS_MAX_MATCH):
        if bytes_read >= input_size:
            break
        dict_[dict_head + i] = data[bytes_read]
        bytes_read += 1
        waiting_bytes += 1

    dict_head_key = _generate_key(dict_, dict_head)

    while waiting_bytes:
        match_len = LZSS_MIN_MATCH - 1
        match_offset = 0

        offset = hash_[dict_head_key]
        while offset != HASH_NULL and waiting_bytes > match_len:
            # 先看较远处一个字符，快速排除不可能更长的匹配
            if dict_[(dict_head + match_len) & LZSS_DICTSIZE_MASK] == \
               dict_[(offset + match_len) & LZSS_DICTSIZE_MASK]:
                i = 0
                while i < match_len and \
                        dict_[(dict_head + i) & LZSS_DICTSIZE_MASK] == \
                        dict_[(offset + i) & LZSS_DICTSIZE_MASK]:
                    i += 1
                if i < match_len:
                    offset = next_[offset]
                    continue
                match_len += 1
                while match_len < waiting_bytes and \
                        dict_[(dict_head + match_len) & LZSS_DICTSIZE_MASK] == \
                        dict_[(offset + match_len) & LZSS_DICTSIZE_MASK]:
                    match_len += 1
                match_offset = offset
            offset = next_[offset]

        if match_len < LZSS_MIN_MATCH:
            match_len = 1
            bw.write1(1)
            bw.write(8, dict_[dict_head])
        else:
            bw.write1(0)
            bw.write(13, match_offset)
            bw.write(4, match_len - LZSS_MIN_MATCH)

        # 把 match_len 个字节推进字典
        for _ in range(match_len):
            offset = (dict_head + LZSS_MAX_MATCH) & LZSS_DICTSIZE_MASK

            if offset != HASH_NULL:
                key = _generate_key(dict_, offset)
                # list_remove：总是移除链表尾节点
                next_[prev[offset]] = HASH_NULL
                if prev[offset] == HASH_NULL and hash_[key] == offset:
                    hash_[key] = HASH_NULL

            if dict_head != HASH_NULL:
                # list_add
                next_[dict_head] = hash_[dict_head_key]
                prev[dict_head] = HASH_NULL
                prev[hash_[dict_head_key]] = dict_head
                hash_[dict_head_key] = dict_head

            if bytes_read < input_size:
                dict_[offset] = data[bytes_read]
                bytes_read += 1
            else:
                waiting_bytes -= 1

            dict_head = (dict_head + 1) & LZSS_DICTSIZE_MASK
            dict_head_key = _generate_key(dict_, dict_head)

    # 结束标记：0 + 13 位零偏移 + 4 位零长度
    bw.write1(0)
    bw.write(13, HASH_NULL)
    bw.write(4, 0)

    return bw.finish()


def compress_if_smaller(data):
    """按 TH12 归档的存储策略决定压缩或原样存储。

    返回 ``(stored_bytes, uncompressed_size, stored_size)``。
    对应 thtk ``th95_write``：压缩后若 **不更小** 则原样存储，
    此时 ``size == zsize``（游戏据此跳过解压）。
    """
    size = len(data)
    z = lzss(data)
    if len(z) >= size:
        return data, size, size
    return z, size, len(z)
