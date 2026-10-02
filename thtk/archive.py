# -*- coding: utf-8 -*-
"""THA1 归档容器 —— 对应 thtk 的 ``thtk/thdat95.c``。

TH12 (东方星莲船) 的 ``th12.dat`` / ``th12c.dat`` 使用 ZUN 的 **THA1**
容器格式。该格式被 th95/10/103/11/12/125/128/13…20 共用，但**每个版本的
条目密码参数不同**，本模块只实现 TH12 的参数（其他版本见 crypto.py）。

------------------------------------------------------------
文件布局
------------------------------------------------------------

    +-------------------------------- 0x00
    | 16 字节头部（整体加密）        |
    |   magic[4] = "THA1"            |
    |   u32 list_size  (+123456789)  |
    |   u32 list_zsize (+987654321)  |
    |   u32 entry_count(+135792468)  |
    +-------------------------------- 0x10  <-- 数据区起点
    | 条目 0 数据 (加密, 可能压缩)   |
    | 条目 1 数据                    |
    | ...                            |
    +-------------------------------- filesize - list_zsize
    | 条目表 (加密 + LZSS 压缩)      |
    +-------------------------------- filesize

头部密码参数: key=0x1b step=0x37 block=16 limit=16
条目表参数  : key=0x3e step=0x9b block=0x80 limit=list_size
条目数据参数: 由 ``文件名字节和 & 7`` 从 th12_crypt_params 中选一组

------------------------------------------------------------
条目表结构（解压后）
------------------------------------------------------------
      name[namelen] + 零填充到 4 字节对齐
      u32 offset    # 相对文件起点
      u32 size      # 解压后（原始）大小
      u32 zero      # 恒为 0
"""

import os
import struct

from . import crypto, lzss

#: THA1 头部魔数
MAGIC = b"THA1"
#: 三个字段的存储常量偏移
CONST_LIST_SIZE = 123456789
CONST_LIST_ZSIZE = 987654321
CONST_ENTRY_COUNT = 135792468
#: 头部长度 / 数据区起点
HEADER_SIZE = 16
#: 归档格式标识（供 CLI 展示与自动探测）
FORMAT_NAME = "THA1 (ZUN)"


class ArchiveError(Exception):
    """归档结构不正确。"""


class Entry(object):
    """归档中的一个条目。"""

    __slots__ = ("name", "offset", "size", "zsize", "_payload")

    def __init__(self, name, offset=0, size=0, zsize=0):
        self.name = name
        self.offset = offset    # 数据在文件中的偏移
        self.size = size        # 解压后大小
        self.zsize = zsize      # 存储大小

    @property
    def compressed(self):
        """存储大小 < 原始大小 即表示经过 LZSS 压缩。"""
        return self.zsize < self.size

    def __repr__(self):
        return "<Entry %s size=%d stored=%d>" % (
            self.name, self.size, self.zsize)


def _build_list(entries):
    """构建未压缩的条目表字节流。"""
    buf = bytearray()
    for e in entries:
        name = e.name.encode("ascii")
        namelen = len(name)
        pad = 4 - (namelen % 4)
        buf += name + b"\0" * pad
        buf += struct.pack("<III", e.offset, e.size, 0)
    return bytes(buf)


def _parse_list(data, count):
    """解析已解压的条目表。"""
    entries = []
    pos = 0
    n = len(data)
    for _ in range(count):
        if pos >= n:
            raise ArchiveError("条目表在读取第 %d 条时提前结束" % (len(entries) + 1))
        end = data.find(b"\0", pos)
        if end < 0:
            raise ArchiveError("条目 %d 的名字缺少终止符" % (len(entries) + 1))
        name = data[pos:end].decode("ascii", "replace")
        namelen = end - pos
        pos += namelen + (4 - namelen % 4)   # 对齐到 4 字节
        if pos + 12 > n:
            raise ArchiveError("条目 %s 的元数据被截断" % name)
        offset, size, _zero = struct.unpack_from("<III", data, pos)
        pos += 12
        entries.append(Entry(name, offset, size, 0))
    return entries


class Archive(object):
    """一个 THA1 归档。

    读取模式：``Archive.from_file(path)``
    新建模式：``Archive.new()``，再 ``add(name, data)``，最后 ``save(path)``
    """

    def __init__(self):
        self.entries = []
        self._data = b""          # 原始文件内容（读取模式）
        self._by_name = {}
        self.path = None
        self.list_size = 0
        self.list_zsize = 0
        #: 读出时长度不足的条目：``[(名字, 声明长度, 实得长度)]``。
        #: 现实中的归档确实会有条目表的 size 与数据流对不上的情况
        #: （实测某份汉化版 th12c.dat 里 179 条中有 107 条如此），
        #: 这时尽力读出已有数据，同时把不一致记录下来供上层提示用户。
        self.partial_entries = []

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data, source=None):
        """从内存解析一个 THA1 归档。"""
        if len(data) < HEADER_SIZE:
            raise ArchiveError("文件过小，不足一个 THA1 头部（%d 字节）" % len(data))

        raw_header = data[:HEADER_SIZE]
        header = crypto.th_crypt(
            raw_header,
            crypto.HEADER_KEY, crypto.HEADER_STEP, crypto.HEADER_BLOCK,
            crypto.HEADER_BLOCK)

        if header[:4] != MAGIC:
            raise ArchiveError(
                "魔数不是 THA1（解出 %r）。该文件可能不是 THA1 归档，"
                "或不是 TH95/10/11/12 系的 .dat" % (header[:4],))

        list_size, list_zsize, entry_count = struct.unpack_from("<III", header, 4)
        list_size -= CONST_LIST_SIZE
        list_zsize -= CONST_LIST_ZSIZE
        entry_count -= CONST_ENTRY_COUNT

        n = len(data)
        if not (0 < list_zsize <= n):
            raise ArchiveError("条目表压缩尺寸非法: %d（文件 %d 字节）" % (list_zsize, n))
        if entry_count < 0 or entry_count > 1000000:
            raise ArchiveError("条目数量非法: %d" % entry_count)

        table_off = n - list_zsize
        ztable = data[table_off:]

        table = crypto.th_crypt(
            ztable,
            crypto.LIST_KEY, crypto.LIST_STEP, crypto.LIST_BLOCK,
            list_size)
        table = lzss.unlzss(table, list_size)

        obj = cls()
        obj._data = data
        obj.path = source
        obj.list_size = list_size
        obj.list_zsize = list_zsize
        obj.entries = _parse_list(table, entry_count)

        # 计算每条的存储长度：next.offset - this.offset，最后一条到条目表起点
        prev = None
        for e in obj.entries:
            if prev is not None:
                prev.zsize = e.offset - prev.offset
            prev = e
        if prev is not None:
            prev.zsize = table_off - prev.offset

        obj._reindex()
        return obj

    @classmethod
    def from_file(cls, path):
        with open(path, "rb") as f:
            data = f.read()
        return cls.from_bytes(data, source=path)

    # ------------------------------------------------------------------
    # 基本信息
    # ------------------------------------------------------------------
    def _reindex(self):
        self._by_name = {}
        for i, e in enumerate(self.entries):
            self._by_name.setdefault(e.name.lower(), i)

    def __len__(self):
        return len(self.entries)

    def index_of(self, name):
        """按名字查条目下标（大小写不敏感）。找不到返回 -1。"""
        return self._by_name.get(name.lower(), -1)

    def info(self):
        return {
            "format": FORMAT_NAME,
            "entries": len(self.entries),
            "list_size": self.list_size,
            "list_zsize": self.list_zsize,
            "total_size": len(self._data),
            "total_uncompressed": sum(e.size for e in self.entries),
            "total_stored": sum(e.zsize for e in self.entries),
        }

    # ------------------------------------------------------------------
    # 条目数据
    # ------------------------------------------------------------------
    def read(self, index):
        """读出第 ``index`` 条的原始（已解密、已解压）数据。"""
        e = self.entries[index]
        blob = self._data[e.offset:e.offset + e.zsize]
        if len(blob) != e.zsize:
            raise ArchiveError("条目 %s 数据被截断" % e.name)

        key, step, block, limit = crypto.entry_crypt_params(e.name)
        blob = crypto.th_crypt(blob, key, step, block, limit)

        if e.zsize == e.size:
            return blob                     # 未压缩存储
        if not e.compressed:
            raise ArchiveError("条目 %s 的尺寸字段异常 (size=%d zsize=%d)"
                               % (e.name, e.size, e.zsize))
        # allow_partial：真实归档里存在「条目表 size 与数据流不一致」的情况
        # （实测某份汉化版 th12c.dat 有 107 条如此）。这类归档用官方
        # thdat 也是「解出多少算多少」，所以这里同样尽力而为，
        # 但把不一致记录下来（Archive.partial_entries），让上层能提示用户。
        got = lzss.unlzss(blob, e.size, allow_partial=True)
        if len(got) != e.size:
            self.partial_entries.append((e.name, e.size, len(got)))
        return got

    def read_by_name(self, name):
        i = self.index_of(name)
        if i < 0:
            raise KeyError(name)
        return self.read(i)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    @classmethod
    def new(cls):
        return cls()

    def add(self, name, data):
        """追加一个条目（名字需唯一）。"""
        if self.index_of(name) >= 0:
            raise ArchiveError("条目名重复: %s" % name)
        e = Entry(name, 0, len(data), 0)
        e._payload = data            # 暂存，save 时写入
        self.entries.append(e)
        self._reindex()
        return e

    def replace(self, name, data):
        """替换已有条目数据（保持名字与顺序）。"""
        i = self.index_of(name)
        if i < 0:
            raise KeyError(name)
        e = self.entries[i]
        e.size = len(data)
        e._payload = data
        return e

    def remove(self, name):
        i = self.index_of(name)
        if i < 0:
            raise KeyError(name)
        del self.entries[i]
        self._reindex()

    def to_bytes(self):
        """序列化为完整的 .dat 字节流。"""
        # 1) 计算每条实际存储内容
        payloads = []
        offset = HEADER_SIZE
        for e in self.entries:
            data = getattr(e, "_payload", None)
            if data is None:
                data = self.read(self.index_of(e.name))
            stored, size, zsize = lzss.compress_if_smaller(data)
            key, step, block, limit = crypto.entry_crypt_params(e.name)
            stored = crypto.th_encrypt(stored, key, step, block, limit)
            e.offset = offset
            e.size = size
            e.zsize = zsize
            payloads.append(stored)
            offset += zsize

        # 2) 条目表：压缩 + 加密
        table = _build_list(self.entries)
        list_size = len(table)
        list_z = lzss.lzss(table)
        list_zsize = len(list_z)
        list_z = crypto.th_encrypt(
            list_z, crypto.LIST_KEY, crypto.LIST_STEP, crypto.LIST_BLOCK,
            list_size)

        # 3) 头部：加密
        header = bytearray(struct.pack(
            "<4sIII", MAGIC,
            list_size + CONST_LIST_SIZE,
            list_zsize + CONST_LIST_ZSIZE,
            len(self.entries) + CONST_ENTRY_COUNT))
        header = crypto.th_encrypt(
            bytes(header),
            crypto.HEADER_KEY, crypto.HEADER_STEP, crypto.HEADER_BLOCK,
            crypto.HEADER_BLOCK)

        self.list_size = list_size
        self.list_zsize = list_zsize
        return header + b"".join(payloads) + list_z

    def save(self, path):
        """整包重写到 ``path``（原子替换 + 写完自检）。"""
        blob = self.to_bytes()
        self._verify_blob(blob, path)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        self.path = path
        return len(blob)

    # ------------------------------------------------------------------
    # 快速增量保存（只重压被替换的条目，其余原样搬运）
    # ------------------------------------------------------------------
    def to_bytes_patched(self, replacements, compress=False):
        """``replacements`` 为 {条目名或下标: 新原始数据}。

        与 :meth:`to_bytes` 的区别：

        * 未修改的条目直接复用文件里已加密/已压缩的字节，不重新压缩；
        * 修改过的条目默认**不压缩**存储（``zsize == size``），
          游戏同样支持，从而避免纯 Python LZSS 压缩带来的长时间等待。
        """
        rep = {}
        for key, data in replacements.items():
            idx = key if isinstance(key, int) else self.index_of(key)
            if idx < 0:
                raise KeyError(key)
            rep[idx] = data

        payloads = []
        offset = HEADER_SIZE
        for i, e in enumerate(self.entries):
            if i in rep:
                data = rep[i]
                if compress:
                    stored, size, zsize = lzss.compress_if_smaller(data)
                else:
                    stored, size, zsize = data, len(data), len(data)
                key, step, block, limit = crypto.entry_crypt_params(e.name)
                stored = crypto.th_encrypt(stored, key, step, block, limit)
            else:
                stored = self._data[e.offset:e.offset + e.zsize]
                size, zsize = e.size, e.zsize
            e.offset = offset
            e.size = size
            e.zsize = zsize
            payloads.append(stored)
            offset += zsize

        table = _build_list(self.entries)
        list_size = len(table)
        list_z = lzss.lzss(table)
        list_zsize = len(list_z)
        list_z = crypto.th_encrypt(
            list_z, crypto.LIST_KEY, crypto.LIST_STEP, crypto.LIST_BLOCK,
            list_size)

        header = bytearray(struct.pack(
            "<4sIII", MAGIC,
            list_size + CONST_LIST_SIZE,
            list_zsize + CONST_LIST_ZSIZE,
            len(self.entries) + CONST_ENTRY_COUNT))
        header = crypto.th_encrypt(
            bytes(header),
            crypto.HEADER_KEY, crypto.HEADER_STEP, crypto.HEADER_BLOCK,
            crypto.HEADER_BLOCK)

        self.list_size = list_size
        self.list_zsize = list_zsize
        return bytes(header) + b"".join(payloads) + list_z

    def save_patched(self, path, replacements, compress=False):
        """增量保存到 ``path``（原子替换 + 写完自检）。"""
        blob = self.to_bytes_patched(replacements, compress=compress)
        # 写之前先自检：确认生成的数据本身是自洽的
        self._verify_blob(blob, path)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # 重新载入，保持内存状态与磁盘一致
        fresh = Archive.from_bytes(blob, source=path)
        self.entries = fresh.entries
        self._data = fresh._data
        self.path = path
        self.list_size = fresh.list_size
        self.list_zsize = fresh.list_zsize
        self._reindex()
        return len(blob)

    @staticmethod
    def _verify_blob(blob, path):
        """写完自检：条目表必须能解析，而且每个条目都要落在文件范围内。

        这样宁可报错、保留原文件，也不会写出一份「打不开」的归档。
        """
        size = len(blob)
        fresh = Archive.from_bytes(blob, source=path)
        bad = [e for e in fresh.entries if e.offset < 0 or e.offset >= size]
        if bad:
            raise ArchiveError(
                "生成的归档自检失败：%d 个条目的位置超出文件范围（如 %s），"
                "已放弃写入，原文件保持不变"
                % (len(bad), bad[0].name))
        # 抽查最小的几个条目，确认数据能真的解出来
        for e in sorted(fresh.entries, key=lambda x: x.zsize)[:5]:
            fresh.read_by_name(e.name)
