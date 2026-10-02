# -*- coding: utf-8 -*-
"""逐条实测子代理报告里的「硬主张」，只记录可复现的结果。

用法: python _test/verify_review_claims.py
"""
import io
import os
import re
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS)
sys.path.insert(0, os.path.join(WS, "tools", "modtool"))
from thtk import archive, crypto, lzss, msg  # noqa: E402

GAME = os.environ.get("TH12_GAME_DIR") or os.path.join(
    WS, "game", "[th12] 东方星莲船 (汉化版+日文版)")

RESULTS = []


def claim(desc, expect_bug, fn):
    """fn() 返回 (是否复现出问题, 说明)。"""
    try:
        got, detail = fn()
    except Exception as ex:
        got, detail = None, "调用异常: %r" % (ex,)
    if got is None:
        verdict = "无法判定"
    elif got == expect_bug:
        verdict = "★ 报告成立" if expect_bug else "报告不成立（无问题）"
    else:
        verdict = "★ 报告不成立（无问题）" if expect_bug else "★ 报告成立"
    RESULTS.append((verdict, desc, detail))
    print("[%s] %s\n        %s" % (verdict, desc, detail))


print("=" * 78)
print("逐条实测（游戏目录: %s）" % GAME)
print("=" * 78)

# ---------------------------------------------------------------- P0-1
print("\n### P0-1 to_bytes_patched 是否写坏被替换的条目")


def c_patched():
    """替换一个「本身是压缩存储」的条目，看写回后能否读回原数据。"""
    src = os.path.join(GAME, "th12.dat")
    if not os.path.isfile(src):
        return None, "找不到 %s" % src
    a = archive.Archive.from_file(src)
    # 找一个压缩存储的条目（zsize < size）
    cand = [e for e in a.entries if e.zsize < e.size]
    if not cand:
        return None, "这份归档里没有压缩存储的条目"
    e = cand[0]
    orig = a.read_by_name(e.name)
    idx = a.index_of(e.name)
    blob = a.to_bytes_patched({idx: orig})
    a2 = archive.Archive.from_bytes(blob)
    back = a2.read_by_name(e.name)
    e2 = next(x for x in a2.entries if x.name == e.name)
    ok = back == orig
    return (not ok), ("条目 %s（原 size=%d zsize=%d）→ 写回后 size=%d zsize=%d，"
                      "读回一致=%s" % (e.name, e.size, e.zsize,
                                       e2.size, e2.zsize, ok))


claim("替换压缩条目后数据被写坏", True, c_patched)

# ---------------------------------------------------------------- P0-2
print("\n### P0-2 unlzss 对截断输入是否静默返回短数据")


def c_unlzss_short():
    """构造一个 LZSS 流并截断，看 unlzss 是否报错。"""
    data = (b"ABCDEFGH" * 400)          # 8000 字节可压缩数据
    z = lzss.lzss(data)
    truncated = z[:len(z) // 2]
    try:
        out = lzss.unlzss(truncated, len(data))
    except Exception as ex:
        return False, "截断流被拒绝（正确行为）: %r" % (ex,)
    return (len(out) != len(data)), \
        ("截断流返回 %d 字节（期望 %d），未抛异常" % (len(out), len(data)))


claim("unlzss 对截断流静默返回短数据", True, c_unlzss_short)

# ---------------------------------------------------------------- P0-3
print("\n### P0-3 .msg 导入是否会把「只剩标签」的行当成改成空文本")


def c_msg_empty():
    """导出文档 → 删掉某句正文只留标签 → 导入，看是否被判为修改。"""
    cands = []
    if os.path.isdir(GAME):
        for n in sorted(os.listdir(GAME)):
            if n.endswith(".msg"):
                cands.append(os.path.join(GAME, n))
    dat = os.path.join(GAME, "th12.dat")
    if not cands and os.path.isfile(dat):
        a = archive.Archive.from_file(dat)
        for e in a.entries:
            if e.name.endswith(".msg"):
                cands.append(e.name)
                break
    if not cands:
        return None, "没有可用的 .msg 测试数据"
    if os.path.isabs(cands[0]):
        raw = open(cands[0], "rb").read()
        name = os.path.basename(cands[0])
    else:
        a = archive.Archive.from_file(dat)
        name = cands[0]
        raw = a.read_by_name(name)
    enc = "cp932" if "th12c" not in name and not name.startswith("th12c") else "gbk"
    f = msg.MsgFile.from_bytes(raw, enc)
    doc = msg.export_document(f, name, enc)
    lines = doc.splitlines()
    # 找到第一个有正文的行，把正文删掉、保留标签
    pat = re.compile(r"^(\s*\[\d+\.\d+\]\s*\[[^\]]*\])(\s+)(.+)$")
    changed_line = None
    for i, ln in enumerate(lines):
        m = pat.match(ln)
        if m and m.group(3).strip():
            lines[i] = m.group(1) + " "      # 只剩标签 + 一个空格
            changed_line = i
            break
    if changed_line is None:
        return None, "文档里没找到可改的正文行"
    edited = "\n".join(lines)
    changed, unmatched, bad, changes = msg.import_document(
        edited, msg.MsgFile.from_bytes(raw, enc), enc, dry_run=True)
    return (changed > 0), ("把第 %d 行正文删空 → changed=%d unmatched=%d"
                           % (changed_line, changed, len(unmatched)))


claim("删空正文被当成一次「改成空文本」的修改", True, c_msg_empty)

# ---------------------------------------------------------------- P0-4
print("\n### P0-4 无法编码的字符是否被静默替换成 ?")
print("        （读代码：msg.py 的 set_text 用 encode(encoding, 'replace')）")


def c_encode_replace():
    src = io.open(os.path.join(WS, "thtk", "msg.py"), encoding="utf-8").read()
    hits = re.findall(r'\.encode\([^)]*"replace"', src)
    return (len(hits) > 0), ("msg.py 里出现 %d 处 encode(..., 'replace')：%s"
                             % (len(hits), hits[:3]))


claim("无法编码的字符被静默替换", True, c_encode_replace)

# ---------------------------------------------------------------- P1
print("\n### P1 archive.read 的边界行为")


def c_read_negative():
    src = os.path.join(GAME, "th12.dat")
    if not os.path.isfile(src):
        return None, "找不到 th12.dat"
    a = archive.Archive.from_file(src)
    last = a.entries[-1].name
    try:
        got = a.read(-1)
        return True, ("read(-1) 没报错，返回了 %d 字节（最后一条是 %s）"
                      % (len(got), last))
    except Exception as ex:
        return False, "read(-1) 抛错（正确）: %r" % (ex,)


claim("read(-1) 静默返回最后一条", True, c_read_negative)


def c_read_oob():
    src = os.path.join(GAME, "th12.dat")
    if not os.path.isfile(src):
        return None, "找不到 th12.dat"
    a = archive.Archive.from_file(src)
    try:
        a.read(len(a.entries) + 10)
        return False, "越界读没报错"
    except archive.ArchiveError as ex:
        return False, "越界读抛 ArchiveError（正确）: %r" % (ex,)
    except IndexError as ex:
        return True, "越界读抛裸 IndexError（与模块其它地方不一致）: %r" % (ex,)


claim("read(越界) 抛裸 IndexError 而非 ArchiveError", True, c_read_oob)


def c_nonascii_name():
    a = archive.Archive.new()
    try:
        a.add("测试.txt", b"hi")
        a.to_bytes()
        return False, "非 ASCII 条目名可用"
    except UnicodeEncodeError as ex:
        return True, "非 ASCII 条目名抛裸 UnicodeEncodeError: %s" % ex
    except Exception as ex:
        return False, "抛的是其它异常: %r" % (ex,)


claim("非 ASCII 条目名抛裸 UnicodeEncodeError", True, c_nonascii_name)

# ---------------------------------------------------------------- crypto
print("\n### P2 crypto 的死代码与除零")


def c_norm_dead():
    src = io.open(os.path.join(WS, "thtk", "crypto.py"), encoding="utf-8").read()
    return ("_normalize" in src and
            len(re.findall(r"_normalize\(", src)) <= 1), \
        "_normalize 出现 %d 次（定义 + 调用）" % len(re.findall(r"_normalize", src))


claim("crypto._normalize 是死代码", True, c_norm_dead)


def c_block0():
    try:
        crypto.th_crypt(b"0123456789", 0x1b, 0x37, 0, None)
        return False, "block=0 没报错"
    except ZeroDivisionError as ex:
        return True, "block=0 抛 ZeroDivisionError: %s" % ex
    except Exception as ex:
        return False, "抛的是其它异常: %r" % (ex,)


claim("block=0 抛 ZeroDivisionError", True, c_block0)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 78)
print("汇总")
print("=" * 78)
ok = [r for r in RESULTS if r[0].startswith("★ 报告成立")]
no = [r for r in RESULTS if "不成立" in r[0]]
un = [r for r in RESULTS if r[0] == "无法判定"]
print("报告成立: %d 条" % len(ok))
for _, d, _ in ok:
    print("   ✓ %s" % d)
print("报告不成立: %d 条" % len(no))
for _, d, _ in no:
    print("   ✗ %s" % d)
if un:
    print("无法判定: %d 条" % len(un))
    for _, d, detail in un:
        print("   ? %s（%s）" % (d, detail))
