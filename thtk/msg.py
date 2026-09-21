# -*- coding: utf-8 -*-
"""TH12 对话文件 (.msg) 解析 —— 对应 thtk 的 ``thmsg/thmsg06.c``。

TH10 以后（含 TH12）的 .msg 沿用 TH06 的**指令流**结构：

    文件头:
        u32 entry_count
        条目表: entry_count * (i32 offset, i32 extra)
                offset = 该条目首指令在文件中的偏移
                extra  = 条目标识（原样保留，TH12 里通常是 0x100）
    指令流（紧跟条目表之后，直到文件尾）:
        u16 time
        u8  type
        u8  length
        u8  data[length]

条目分隔：``time == 0 且 type == 0`` 的零长指令即"条目开始"标记，
其文件偏移正好等于条目表里的 offset。

文本：``type == 17`` 的指令 data 是一段 **XOR 混淆** 的字符串
（滚动密钥 key=0x77, step1=+7, step2=+16），解码后是 Shift-JIS
（汉化版是 GBK）。字符串按 4 字节对齐补零。
"""

import struct

#: 文本指令的 type
TEXT_TYPE = 17
#: 文本 XOR 参数（thtk: util_xor(data, len, 0x77, 7, 16)）
TEXT_XOR_KEY = 0x77
TEXT_XOR_STEP1 = 7
TEXT_XOR_STEP2 = 16


class MsgError(Exception):
    """对话文件结构不正确。"""


def rolling_xor(data, key=TEXT_XOR_KEY, step1=TEXT_XOR_STEP1,
                step2=TEXT_XOR_STEP2):
    """ZUN 滚动密钥 XOR（自逆），对应 thtk 的 ``util_xor``。"""
    out = bytearray(data)
    key &= 0xFF
    step1 &= 0xFF
    step2 &= 0xFF
    for i in range(len(out)):
        out[i] ^= key
        key = (key + step1) & 0xFF
        step1 = (step1 + step2) & 0xFF
    return bytes(out)


class Instruction(object):
    """一条 .msg 指令。"""

    __slots__ = ("time", "type", "data")

    def __init__(self, time, type_, data):
        self.time = time
        self.type = type_
        self.data = data

    @property
    def is_entry_marker(self):
        """条目开始标记（time=0, type=0, 空 data）。"""
        return self.time == 0 and self.type == 0 and not self.data

    @property
    def is_text(self):
        return self.type == TEXT_TYPE

    def text(self, encoding="cp932"):
        """解码文本指令；非文本指令返回 None。"""
        if not self.is_text:
            return None
        raw = rolling_xor(self.data)
        raw = raw.split(b"\0", 1)[0]
        return raw.decode(encoding, "replace")

    def set_text(self, text, encoding="cp932"):
        """写入文本指令（自动补零到 4 字节对齐，与 thtk 写回逻辑一致）。"""
        if not self.is_text:
            raise MsgError("不是文本指令")
        raw = text.encode(encoding, "replace")
        raw += b"\0" * (4 - (len(raw) % 4))
        self.data = rolling_xor(raw)

    def __repr__(self):
        return "<Instr t=%d type=%d len=%d>" % (
            self.time, self.type, len(self.data))


class Entry(object):
    """一个对话条目。"""

    __slots__ = ("extra", "instructions", "offset")

    def __init__(self, extra=0):
        self.extra = extra
        self.instructions = []
        self.offset = 0

    def texts(self):
        return [i for i in self.instructions if i.is_text]

    def __repr__(self):
        return "<Entry extra=%d instrs=%d>" % (
            self.extra, len(self.instructions))


def _parse_stream(data, start, end):
    """把 [start, end) 解析成指令列表。"""
    instrs = []
    pos = start
    while pos + 4 <= end:
        time, type_, length = struct.unpack_from("<HBB", data, pos)
        if pos + 4 + length > end:
            break
        instrs.append(Instruction(
            time, type_, bytes(data[pos + 4:pos + 4 + length])))
        pos += 4 + length
    return instrs


class MsgFile(object):
    """解析后的 .msg 文件。"""

    def __init__(self, encoding="cp932"):
        self.entries = []
        self.encoding = encoding

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data, encoding="cp932"):
        if len(data) < 4:
            raise MsgError("文件过小")
        entry_count = struct.unpack_from("<I", data, 0)[0]
        if entry_count > 100000:
            raise MsgError("条目数量异常: %d" % entry_count)
        table_end = 4 + entry_count * 8
        if table_end > len(data):
            raise MsgError("条目表被截断")

        raw = []
        for i in range(entry_count):
            off, extra = struct.unpack_from("<ii", data, 4 + i * 8)
            raw.append((off, extra))

        obj = cls(encoding)
        bounds = [off for off, _ in raw] + [len(data)]
        for i in range(entry_count):
            start = raw[i][0]
            end = bounds[i + 1]
            entry = Entry(raw[i][1])
            entry.offset = start
            if 0 <= start <= end <= len(data):
                entry.instructions = _parse_stream(data, start, end)
            obj.entries.append(entry)
        return obj

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_bytes(self):
        """重建 .msg 文件（重新计算条目偏移）。"""
        body = bytearray()
        offsets = []
        table_end = 4 + len(self.entries) * 8
        for entry in self.entries:
            offsets.append(table_end + len(body))
            for instr in entry.instructions:
                if len(instr.data) > 255:
                    raise MsgError("指令数据过长（%d 字节）" % len(instr.data))
                body += struct.pack("<HBB", instr.time & 0xFFFF,
                                    instr.type & 0xFF, len(instr.data))
                body += instr.data
        header = bytearray(struct.pack("<I", len(self.entries)))
        for off, entry in zip(offsets, self.entries):
            header += struct.pack("<ii", off, entry.extra)
        return bytes(header + body)

    # ------------------------------------------------------------------
    # 便捷接口
    # ------------------------------------------------------------------
    def plain_text(self):
        """返回 [(条目号, 指令号, 文本)]。"""
        out = []
        for i, entry in enumerate(self.entries):
            for j, instr in enumerate(entry.instructions):
                if instr.is_text:
                    out.append((i, j, instr.text(self.encoding)))
        return out


def _cjk_ratio(s):
    if not s:
        return 0.0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk / float(len(s))


def _bad_ratio(s):
    if not s:
        return 0.0
    bad = sum(1 for ch in s
              if ch == "\ufffd" or "\uff61" <= ch <= "\uff9f")
    return bad / float(len(s))


def guess_encoding(data):
    """猜测 .msg 文本编码：优先 GBK（汉化版），否则 Shift-JIS。"""
    best = None
    for enc in ("cp932", "gbk"):
        try:
            msg = MsgFile.from_bytes(data, enc)
        except MsgError:
            continue
        sample = "".join(t for _, _, t in msg.plain_text()[:60])
        score = _cjk_ratio(sample) - _bad_ratio(sample)
        if best is None or score > best[0]:
            best = (score, enc)
    return best[1] if best else "cp932"
