# -*- coding: utf-8 -*-
"""验证子代理报告的头号发现：to_bytes_patched 会把被替换的条目写坏。

主张：patch 后 size 被覆盖成「存储长度」，而 read() 用 zsize == size 判定
「未压缩」，于是重新载入归档后读出来的不是原始数据，而是压缩流。

用法: python _test/verify_patched_bug.py
"""
import hashlib
import io
import os
import shutil
import sys
import tempfile

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS)
sys.path.insert(0, os.path.join(WS, "tools", "modtool"))
from thtk import archive  # noqa: E402

SRC = os.environ.get("TH12_DAT") or os.path.join(
    WS, "game", "[th12] 东方星莲船 (汉化版+日文版)", "th12.dat")
if not os.path.isfile(SRC):
    raise SystemExit("找不到测试用 th12.dat，请设 TH12_DAT 环境变量")

print("测试归档: %s（%.1f MB）" % (SRC, os.path.getsize(SRC) / 1048576.0))
tmp = os.path.join(tempfile.gettempdir(), "patched-bug-test.dat")
shutil.copyfile(SRC, tmp)

# --- 1) 先看 thbgm.fmt 在归档里的真实形态（是否压缩） ---
a0 = archive.Archive.from_file(tmp)
e = next((x for x in a0.entries if x.name == "thbgm.fmt"), None)
if e is None:
    raise SystemExit("这份归档里没有 thbgm.fmt")
print("\n[1] thbgm.fmt 在归档里的条目信息")
print("    offset=%d  size(原始长度)=%d  zsize(存储长度)=%d" % (e.offset, e.size, e.zsize))
print("    压缩了吗: %s" % ("是" if e.zsize < e.size else "否"))
orig = a0.read_by_name("thbgm.fmt")
print("    原始数据 %d 字节, md5=%s" % (len(orig), hashlib.md5(orig).hexdigest()))
print("    条目表里的顺序（第 %d 条，共 %d 条）"
      % (a0.index_of("thbgm.fmt"), len(a0.entries)))
witness = a0.entries[a0.index_of("thbgm.fmt") + 1]
print("    紧随其后的条目: %s" % witness.name)

# --- 2) 用 replace_texture 那条路（to_bytes_patched）把它原样替换 ---
print("\n[2] 用 to_bytes_patched 把 thbgm.fmt 原样写回（模拟「保存 BGM」）")
idx = a0.index_of("thbgm.fmt")
blob = a0.to_bytes_patched({idx: orig})
with open(tmp, "wb") as f:
    f.write(blob)
print("    写回完成，文件 %d 字节" % len(blob))

# --- 3) 重新载入（模拟游戏或下次启动读取） ---
print("\n[3] 重新载入归档后读 thbgm.fmt")
a1 = archive.Archive.from_file(tmp)
e1 = next((x for x in a1.entries if x.name == "thbgm.fmt"), None)
print("    offset=%d  size=%d  zsize=%d" % (e1.offset, e1.size, e1.zsize))
try:
    back = a1.read_by_name("thbgm.fmt")
except Exception as ex:
    print("    读取抛错: %r" % (ex,))
    back = None

if back is None:
    print("\n结论: 读取直接失败 —— bug 成立")
else:
    same = back == orig
    print("    读回 %d 字节, md5=%s" % (len(back), hashlib.md5(back).hexdigest()))
    print("    与原数据一致: %s" % same)
    if not same:
        print("\n>>> 确认 BUG：读回来的不是原始数据 <<<")
        print("    原始前 16 字节:", orig[:16].hex())
        print("    读回前 16 字节:", back[:16].hex())
        # 原始数据的前两字节是不是 LZSS 头（bit0=DIC）
        if len(orig) >= 2:
            flags = orig[0] | (orig[1] << 8)
            print("    原始数据首两字节按 LZSS 头解析: DIC=%d" % (flags & 1))
        try:
            from thtk import lzss
            dec = lzss.unlzss(orig, len(orig))
            print("    把原始数据当 LZSS 流解压: %d 字节" % len(dec))
        except Exception as ex:
            print("    当 LZSS 流解压失败: %r" % (ex,))

# --- 4) 未替换的其它条目受影响吗（对照） ---
print("\n[4] 对照组：未被替换的条目")
for name in ("st01_00a.msg",):
    try:
        b0 = a0.read_by_name(name)
        b1 = a1.read_by_name(name)
        print("    %-14s 原始 %d 字节 / 读回 %d 字节  一致=%s"
              % (name, len(b0), len(b1), b0 == b1))
    except Exception as ex:
        print("    %-14s 异常 %r" % (name, ex))

os.remove(tmp)
