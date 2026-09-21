# -*- coding: utf-8 -*-
"""TH12 归档流密码 —— 对应 thtk 的 ``thtk/thcrypt.c``。

算法（ZUN 自创，非标准密码学，仅为混淆）：

    把数据按 ``block`` 字节切成若干块。块内做一次"两路交汇"的
    字节重排 + 异或，每处理一个字节 key 就自增 ``step``：

        lo = 0, hi = block - 1
        循环 block//2 次:
            明文[lo] = 密文[lo]      ^ key
            明文[hi] = 密文[lo + inc] ^ (key + step*inc)
            lo++, hi--, key += step

其中 ``inc = (block >> 1) + (block & 1)``。块的奇偶性决定末尾
多出的那一个字节如何处理。处理完一块后 ``key += step * inc``。

三个关键细节（thtk 的 :c:func:`th_decrypt` 原文行为，必须一致）：

1. **长度截断**：``size < block//4`` 时整个缓冲区都不处理（size=0）；
   否则 ``size -= (size % block < block//4) * (size % block) + size % 2``。
2. **limit 向上取整到 block 的整数倍**。
3. **实际处理长度 = min(size, limit)**。

加密与解密是同一个运算（异或自逆），thtk 里分成
:c:func:`th_encrypt` / :c:func:`th_decrypt` 只是为了可读性，
数学上完全等价。本模块只实现一个 :func:`th_crypt`。
"""


def _normalize(size, block):
    """复刻 thtk 对 size / limit 的边界处理，返回实际处理长度。"""
    if size < (block >> 2):
        size = 0
    else:
        size -= (size % block < (block >> 2)) * (size % block) + size % 2
    limit = block
    if limit % block != 0:
        limit = limit + (block - (limit % block))
    return size if size < limit else limit


def th_crypt(data, key, step, block, limit=None):
    """对 ``data`` 就地施加/撤销 ZUN 流密码，返回新的 ``bytes``。

    :param data:  待处理数据（bytes / bytearray）
    :param key:   初始密钥字节（0..255）
    :param step:  密钥自增步长（0..255）
    :param block: 块大小
    :param limit: 处理上限；``None`` 表示等于 ``block``（thtk 语义）
    """
    buf = bytearray(data)
    size = len(buf)
    if limit is None:
        limit = block

    # --- 复刻 thtk 的 size / limit 规范化 ---
    if size < (block >> 2):
        size = 0
    else:
        size -= (size % block < (block >> 2)) * (size % block) + size % 2

    if limit % block != 0:
        limit = limit + (block - (limit % block))

    end = size if size < limit else limit
    if end <= 0:
        return bytes(buf)

    increment = (block >> 1) + (block & 1)
    temp = bytearray(block)
    pos = 0
    key &= 0xFF
    step &= 0xFF

    while pos < end:
        b = block
        inc = increment
        if end - pos < block:
            b = end - pos
            inc = (b >> 1) + (b & 1)

        src = pos
        out = b - 1
        while out > 0:
            temp[out] = buf[src] ^ key
            out -= 1
            temp[out] = buf[src + inc] ^ ((key + step * inc) & 0xFF)
            out -= 1
            src += 1
            key = (key + step) & 0xFF
        if b & 1:
            temp[out] = buf[src] ^ key
            key = (key + step) & 0xFF
        key = (key + step * inc) & 0xFF

        buf[pos:pos + b] = temp[:b]
        pos += b

    return bytes(buf)


#: TH12 归档 **头部** 的密码参数（thtk: th95_open）
HEADER_KEY, HEADER_STEP, HEADER_BLOCK = 0x1B, 0x37, 16
#: TH12 归档 **条目表** 的密码参数（thtk: th95_open）
LIST_KEY, LIST_STEP, LIST_BLOCK = 0x3E, 0x9B, 0x80


def th_encrypt(data, key, step, block, limit=None):
    """ZUN 流密码的**加密**方向（thtk 的 ``th_encrypt``）。

    与 :func:`th_crypt`（= thtk ``th_decrypt``）互逆，但**不是**同一个函数：
    两者都做"块内重排 + 逐字节递增密钥异或"，只是重排方向不同。
    写归档必须用这个函数。
    """
    buf = bytearray(data)
    size = len(buf)
    if limit is None:
        limit = block

    if size < (block >> 2):
        size = 0
    else:
        size -= (size % block < (block >> 2)) * (size % block) + size % 2

    if limit % block != 0:
        limit = limit + (block - (limit % block))

    end = size if size < limit else limit
    if end <= 0:
        return bytes(buf)

    increment = (block >> 1) + (block & 1)
    temp = bytearray(block)
    pos = 0
    key &= 0xFF
    step &= 0xFF

    while pos < end:
        b = block
        inc = increment
        if end - pos < block:
            b = end - pos
            inc = (b >> 1) + (b & 1)

        out = 0
        src = pos + b - 1
        while src > pos:
            temp[out] = buf[src] ^ key
            src -= 1
            temp[out + inc] = buf[src] ^ ((key + step * inc) & 0xFF)
            src -= 1
            out += 1
            key = (key + step) & 0xFF
        if b & 1:
            temp[out] = buf[src] ^ key
            key = (key + step) & 0xFF
        key = (key + step * inc) & 0xFF

        buf[pos:pos + b] = temp[:b]
        pos += b

    return bytes(buf)

#: TH12 每个条目的密码参数表，索引 = (文件名字节和 & 7)
#: 摘自 thtk/dattypes.h 的 th12_crypt_params[]
TH12_CRYPT_PARAMS = (
    # key   step   block   limit
    (0x1B, 0x73, 0x040, 0x3800),  # 0
    (0x51, 0x9E, 0x040, 0x4000),  # 1
    (0xC1, 0x15, 0x400, 0x2C00),  # 2
    (0x03, 0x91, 0x080, 0x6400),  # 3
    (0xAB, 0xDC, 0x080, 0x6E00),  # 4
    (0x12, 0x43, 0x200, 0x3C00),  # 5
    (0x35, 0x79, 0x400, 0x3C00),  # 6
    (0x99, 0x7D, 0x080, 0x2800),  # 7
)

#: 其他版本（若将来扩展）—— 摘自 thtk/dattypes.h
TH95_CRYPT_PARAMS = (
    (0x1B, 0x37, 0x40, 0x2800),
    (0x51, 0xE9, 0x40, 0x3000),
    (0xC1, 0x51, 0x80, 0x3200),
    (0x03, 0x19, 0x400, 0x7800),
    (0xAB, 0xCD, 0x200, 0x2800),
    (0x12, 0x34, 0x80, 0x3200),
    (0x35, 0x97, 0x80, 0x2800),
    (0x99, 0x37, 0x400, 0x2000),
)


def crypt_param_index(name):
    """TH12 条目密码参数索引 = 文件名字节的算术和 & 7。

    对应 thtk/thdat95.c 的 ``th95_get_crypt_param_index``。
    注意：TH12 用"名字和"选参数，而 th08/th09 用**扩展名**选参数。
    """
    total = 0
    for ch in name.encode("ascii", "replace"):
        total += ch
    return total & 7


def entry_crypt_params(name, params=TH12_CRYPT_PARAMS):
    """按条目名取得 (key, step, block, limit)。"""
    return params[crypt_param_index(name)]
