# -*- coding: utf-8 -*-
"""TH12 贴图容器 (.anm) 解析与替换。

TH11/TH12 使用 ``anm_header11_t``（version = 7）的 ANM 格式：

    条目链（通过 header.nextoffset 串起来，最后一个 nextoffset = 0）:
        0x00 u32 version          (= 7)
        0x04 u16 sprites          精灵数
        0x06 u16 scripts          脚本数
        0x08 u16 zero1
        0x0A u16 w, h             条目画布尺寸
        0x0E u16 format           贴图格式
        0x10 u32 nameoffset       条目名（相对本条目起点，如 "ascii/ascii.png"）
        0x14 u16 x, y             在画布中的偏移
        0x18 u32 memorypriority
        0x1C u32 thtxoffset       THTX 贴图数据偏移（相对本条目起点）
        0x20 u16 hasdata
        0x22 u8  lowresscale
        0x23 u8  jpeg_quality
        0x24 u32 nextoffset       下一条目偏移（相对本条目起点）
        0x28 u16 w_max, h_max
        0x2C u32 zero2[5]
        ---- 0x40 ----
        u32 sprite_offsets[sprites]         （相对本条目起点的 sprite_t 偏移）
        anm_offset_t script_offsets[scripts]（i32 id; u32 offset，相对本条目起点）
        ... sprite/script 数据 ...
        THTX 贴图:
            char magic[4] = "THTX"
            u16 zero, u16 format, u16 w, u16 h, u32 size
            u8 data[size]

贴图格式（``format_t``）:
    1 = BGRA8888  每像素 4 字节 B,G,R,A
    3 = RGB565    每像素 2 字节 LE: R5<<11 | G6<<5 | B5
    5 = A4R4G4B4  每像素 2 字节 LE: A4<<12 | R4<<8 | G4<<4 | B4
    7 = GRAY8     每像素 1 字节灰度
"""

import struct

try:
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None

FORMAT_BGRA8888 = 1
FORMAT_RGB565 = 3
FORMAT_ARGB4444 = 5
FORMAT_GRAY8 = 7

FORMAT_NAMES = {
    FORMAT_BGRA8888: "BGRA8888",
    FORMAT_RGB565: "RGB565",
    FORMAT_ARGB4444: "A4R4G4B4",
    FORMAT_GRAY8: "GRAY8",
}

FORMAT_BPP = {
    FORMAT_BGRA8888: 4,
    FORMAT_RGB565: 2,
    FORMAT_ARGB4444: 2,
    FORMAT_GRAY8: 1,
}

HEADER_SIZE = 0x40
THTX_SIZE = 0x10


class AnmError(Exception):
    """ANM 结构不正确。"""


class Texture(object):
    """一条 ANM 条目里的贴图。"""

    __slots__ = ("entry_index", "name", "format", "width", "height",
                 "size", "data", "x", "y", "entry_offset")

    def __init__(self, entry_index, name, format_, width, height,
                 size, data, x, y, entry_offset):
        self.entry_index = entry_index
        self.name = name
        self.format = format_
        self.width = width
        self.height = height
        self.size = size
        self.data = data
        self.x = x
        self.y = y
        self.entry_offset = entry_offset

    @property
    def format_name(self):
        return FORMAT_NAMES.get(self.format, "?%d" % self.format)

    def __repr__(self):
        return "<Texture #%d %s %dx%d %s>" % (
            self.entry_index, self.name, self.width, self.height,
            self.format_name)


# ----------------------------------------------------------------------
# 像素转换
# ----------------------------------------------------------------------
def _rgb565_to_rgba(raw, w, h):
    if _np is not None:
        v = _np.frombuffer(raw, dtype="<u2").reshape(h, w)
        r5 = (v >> 11) & 0x1F
        g6 = (v >> 5) & 0x3F
        b5 = v & 0x1F
        # 与 thtk / 官方 thanm 完全一致的位扩展（B 通道低位的补位规则不同）
        r = (r5 << 3) | (r5 & 1) * 7
        g = (g6 << 2) | (g6 & 1) * 3
        b = (b5 << 3) | (b5 & 1)
        a = _np.full((h, w), 255, dtype=_np.uint8)
        return _np.dstack([r, g, b, a]).astype(_np.uint8)
    raise AnmError("需要 numpy 才能转换 RGB565")


def _argb4444_to_rgba(raw, w, h):
    if _np is not None:
        v = _np.frombuffer(raw, dtype="<u2").reshape(h, w)
        r = ((v >> 8) & 0xF) * 17
        g = ((v >> 4) & 0xF) * 17
        b = (v & 0xF) * 17
        a = ((v >> 12) & 0xF) * 17
        return _np.dstack([r, g, b, a]).astype(_np.uint8)
    raise AnmError("需要 numpy 才能转换 A4R4G4B4")


def _bgra8888_to_rgba(raw, w, h):
    if _np is not None:
        a = _np.frombuffer(raw, dtype=_np.uint8).reshape(h, w, 4)
        return a[:, :, [2, 1, 0, 3]].copy()
    raise AnmError("需要 numpy 才能转换 BGRA8888")


def _gray8_to_rgba(raw, w, h):
    if _np is not None:
        g = _np.frombuffer(raw, dtype=_np.uint8).reshape(h, w)
        return _np.dstack([g, g, g, _np.full((h, w), 255, dtype=_np.uint8)])
    raise AnmError("需要 numpy 才能转换 GRAY8")


def decode_rgba(fmt, raw, w, h):
    """把原始贴图字节解码成 (h, w, 4) 的 RGBA uint8 数组。"""
    need = w * h * FORMAT_BPP.get(fmt, 0)
    if need == 0:
        raise AnmError("未知贴图格式: %d" % fmt)
    if len(raw) < need:
        raise AnmError("贴图数据不足（%d < %d）" % (len(raw), need))
    raw = raw[:need]
    if fmt == FORMAT_BGRA8888:
        return _bgra8888_to_rgba(raw, w, h)
    if fmt == FORMAT_RGB565:
        return _rgb565_to_rgba(raw, w, h)
    if fmt == FORMAT_ARGB4444:
        return _argb4444_to_rgba(raw, w, h)
    if fmt == FORMAT_GRAY8:
        return _gray8_to_rgba(raw, w, h)
    raise AnmError("未知贴图格式: %d" % fmt)


def encode_rgba(fmt, rgba):
    """把 (h, w, 4) RGBA 数组编码成原始贴图字节。"""
    if _np is None:
        raise AnmError("需要 numpy 才能转换贴图")
    arr = _np.asarray(rgba)
    h, w = arr.shape[:2]
    r = arr[:, :, 0].astype(_np.uint16)
    g = arr[:, :, 1].astype(_np.uint16)
    b = arr[:, :, 2].astype(_np.uint16)
    a = arr[:, :, 3].astype(_np.uint16)
    if fmt == FORMAT_BGRA8888:
        out = _np.empty((h, w, 4), dtype=_np.uint8)
        out[:, :, 0] = b
        out[:, :, 1] = g
        out[:, :, 2] = r
        out[:, :, 3] = a
        return out.tobytes()
    if fmt == FORMAT_RGB565:
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        return v.astype("<u2").tobytes()
    if fmt == FORMAT_ARGB4444:
        v = ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
        return v.astype("<u2").tobytes()
    if fmt == FORMAT_GRAY8:
        gray = ((r * 299 + g * 587 + b * 114) // 1000).astype(_np.uint8)
        return gray.tobytes()
    raise AnmError("未知贴图格式: %d" % fmt)


# ----------------------------------------------------------------------
# ANM 解析
# ----------------------------------------------------------------------
def _read_cstr(data, pos):
    end = data.find(b"\0", pos)
    if end < 0:
        end = len(data)
    return data[pos:end].decode("cp932", "replace")


class AnmFile(object):
    """解析后的 .anm 文件（保留原始字节，替换时按需重建）。"""

    def __init__(self):
        self.data = b""
        self.textures = []
        self.entry_offsets = []

    @classmethod
    def from_bytes(cls, data):
        obj = cls()
        obj.data = bytes(data)
        pos = 0
        index = 0
        n = len(data)
        while pos + HEADER_SIZE <= n:
            (version, sprites, scripts, zero1, w, h, fmt,
             nameoffset, x, y, memprio, thtxoffset, hasdata) = \
                struct.unpack_from("<IHHHHHHIHHIIH", data, pos)
            nextoffset = struct.unpack_from("<I", data, pos + 0x24)[0]
            if version not in (0, 2, 3, 4, 7, 8):
                break
            obj.entry_offsets.append(pos)
            if hasdata and 0 < thtxoffset < n - pos:
                t = pos + thtxoffset
                magic, zero, tfmt, tw, th, tsize = \
                    struct.unpack_from("<4sHHHHI", data, t)
                if magic == b"THTX":
                    name = _read_cstr(data, pos + nameoffset) \
                        if 0 < nameoffset < n - pos else ""
                    obj.textures.append(Texture(
                        index, name, tfmt, tw, th,
                        tsize, data[t + THTX_SIZE:t + THTX_SIZE + tsize],
                        x, y, pos))
            index += 1
            if not nextoffset:
                break
            pos += nextoffset
        return obj

    @classmethod
    def from_file(cls, path):
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    def rgba(self, texture):
        """取某条贴图的 RGBA 像素。"""
        return decode_rgba(texture.format, texture.data,
                           texture.width, texture.height)

    # ------------------------------------------------------------------
    # 替换贴图并重建文件
    # ------------------------------------------------------------------
    def find(self, index):
        """按条目序号取贴图（每次调用都返回最新对象）。"""
        for t in self.textures:
            if t.entry_index == index:
                return t
        return None

    def replace_texture(self, texture, new_raw, new_w, new_h):
        """替换第 ``texture.entry_index`` 条贴图的像素数据。

        :param new_raw: 新贴图原始字节（尺寸必须与 new_w/new_h 匹配）
        :param new_w,new_h: 新尺寸（可与原尺寸不同）
        """
        data = bytearray(self.data)
        pos = texture.entry_offset
        old_next = struct.unpack_from("<I", data, pos + 0x24)[0]
        thtx = pos + struct.unpack_from("<I", data, pos + 0x1C)[0]
        old_size = struct.unpack_from("<I", data, thtx + 0x0C)[0]

        # 新 THTX 块
        new_thtx = bytearray()
        new_thtx += b"THTX"
        new_thtx += struct.pack("<HHHHI", 0, texture.format,
                                new_w, new_h, len(new_raw))
        new_thtx += new_raw

        # 条目 = 原条目 [0, thtxoffset) + 新 THTX
        entry_prefix = data[pos:thtx]
        new_entry = bytes(entry_prefix) + bytes(new_thtx)
        # 注意：链表中最后一条的 nextoffset 必须保持 0，
        # 否则游戏会沿链走过文件末尾读到垃圾数据（表现为进关卡崩溃）
        new_next = len(new_entry) if old_next else 0

        # 组装文件：替换该条目，其后内容整体前移/后移
        if old_next:
            tail = data[pos + old_next:]
        else:
            tail = b""
        out = data[:pos] + new_entry + tail

        # 修正头部字段
        struct.pack_into("<I", out, pos + 0x24, new_next)
        old_hdr_w, old_hdr_h = struct.unpack_from("<HH", out, pos + 0x0A)
        if (old_hdr_w, old_hdr_h) == (texture.width, texture.height):
            struct.pack_into("<HH", out, pos + 0x0A, new_w, new_h)
        # THTX 头里的 w/h 已在 new_thtx 里写好
        self.data = bytes(out)
        # 重新解析（条目偏移变化）
        fresh = AnmFile.from_bytes(self.data)
        self.textures = fresh.textures
        self.entry_offsets = fresh.entry_offsets
        return fresh.textures[texture.entry_index]

    def replace_many(self, crops):
        """一次替换多张同组贴图（用于导入“合成图”）。

        :param crops: ``[(entry_index, x, y, w, h, rgba 子图), ...]``
        :return: 实际替换的条目数
        """
        count = 0
        for entry_index, x, y, w, h, sub in crops:
            tex = self.find(entry_index)
            if tex is None:
                continue
            if sub.shape[0] != h or sub.shape[1] != w:
                raise AnmError("切图尺寸不符: 期望 %dx%d" % (w, h))
            raw = encode_rgba(tex.format, sub)
            self.replace_texture(tex, raw, w, h)
            count += 1
        return count

    def to_bytes(self):
        return self.data

    # ------------------------------------------------------------------
    # 结构自检
    # ------------------------------------------------------------------
    def validate(self):
        """检查条目链是否合法（末尾必须是 nextoffset == 0）。

        返回问题描述列表，空列表表示没问题。
        """
        problems = []
        data = self.data
        n = len(data)
        pos = 0
        seen = set()
        index = 0
        while True:
            if pos in seen:
                problems.append("条目链出现环: offset %d" % pos)
                break
            seen.add(pos)
            if pos + HEADER_SIZE > n:
                problems.append("条目 %d 头部超出文件末尾 (offset %d)"
                                % (index, pos))
                break
            version = struct.unpack_from("<I", data, pos)[0]
            if version not in (0, 2, 3, 4, 7, 8):
                problems.append("条目 %d 的 version 非法: %d" % (index, version))
                break
            thtxoffset = struct.unpack_from("<I", data, pos + 0x1C)[0]
            nextoffset = struct.unpack_from("<I", data, pos + 0x24)[0]
            hasdata = struct.unpack_from("<H", data, pos + 0x20)[0]
            if hasdata and thtxoffset:
                if pos + thtxoffset + THTX_SIZE > n:
                    problems.append("条目 %d 的 THTX 偏移越界" % index)
                    break
                magic, zero, fmt, w, h, size = struct.unpack_from(
                    "<4sHHHHI", data, pos + thtxoffset)
                if magic != b"THTX":
                    problems.append("条目 %d 的 THTX 魔数错误: %r"
                                    % (index, magic))
                need = w * h * FORMAT_BPP.get(fmt, 0)
                if need and size < need:
                    problems.append("条目 %d 贴图数据不足: %d < %d"
                                    % (index, size, need))
                if pos + thtxoffset + THTX_SIZE + size > n:
                    problems.append("条目 %d 贴图数据超出文件末尾" % index)
            if not nextoffset:
                break
            pos += nextoffset
            index += 1
        return problems


def composed_size(textures):
    """一组同名贴图拼成的大图尺寸（各条目按自己的 x/y 摆放）。"""
    if not textures:
        return (0, 0)
    return (max(t.x + t.width for t in textures),
            max(t.y + t.height for t in textures))


def group_by_name(textures):
    """按贴图名分组，保持出现顺序。"""
    groups = {}
    order = []
    for t in textures:
        key = t.name.lower()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)
    return [(k, groups[k]) for k in order]
