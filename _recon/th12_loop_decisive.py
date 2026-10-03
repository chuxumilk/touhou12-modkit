# -*- coding: utf-8 -*-
"""决定性判断：TH12 的 BGM 引擎到底会不会在 loop 字段处回跳。

以前只做了「搜索 +0x18 的读取」这一种取证，命中 0 处，但那是**反证**，
不够硬。这里换两个更直接的证据：

证据 A：看引擎喂给 DirectSound 的「分段描述符」是怎么算的。
        如果它按 loop 字段算回跳位置，必然会用到 entry+0x18 的值。
证据 B：把 BGM 打开函数（0x00453A01 起）整体反汇编，列出它对
        entry 各字段的全部访问，看有没有 +0x18。

结论只有两种：
  · 引擎回跳到 0（不读 loop）→ 现在的「拼接」方案是对的
  · 引擎回跳到 loop 处     → 那直接改字段就够了，拼接是多余的
"""
import os
import struct
import sys

from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from capstone.x86 import X86_OP_MEM, X86_OP_REG
import pefile

EXE = os.environ.get("TH12_EXE") or (
    r"D:\010-Important-Work\coding相关\[th12] 东方星莲船 (汉化版+日文版)\th12.exe")
if not os.path.isfile(EXE):
    print("找不到 exe：%s" % EXE)
    sys.exit(1)

FMT_BASE = 0x004D0E6C
pe = pefile.PE(EXE, fast_load=True)
ib = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()
text = [s for s in pe.sections if s.Name.rstrip(b"\x00") == b".text"][0]
tva = ib + text.VirtualAddress
code = img[text.VirtualAddress:text.VirtualAddress + text.Misc_VirtualSize]

md = Cs(CS_ARCH_X86, CS_MODE_32)
md.detail = True


def func_bounds(addr):
    """用 int3 填充切出包含 addr 的函数范围。"""
    prev, nxt = tva, tva + len(code)
    run = 0
    for i, b in enumerate(code):
        a = tva + i
        if b == 0xCC:
            run += 1
            continue
        if run >= 2:
            s, e = tva + i - run, tva + i
            if e <= addr:
                prev = e
            elif s > addr:
                nxt = s
                break
        run = 0
    return prev, nxt


print("=" * 74)
print("证据 B：BGM 打开函数对 fmt 条目各字段的访问")
print("=" * 74)
lo, hi = func_bounds(0x00453A01)
print("函数范围 0x%08X .. 0x%08X" % (lo, hi))
off = lo - tva
seen = {}
for ins in md.disasm(code[off:off + (hi - lo)], lo):
    for op in ins.operands:
        if op.type != X86_OP_MEM or not op.mem.base:
            continue
        d = op.mem.disp & 0xFFFFFFFF
        if d <= 0x34 and not ins.reg_name(op.mem.base) in ("esp", "ebp"):
            seen.setdefault(d, []).append(
                "0x%08X %s %s" % (ins.address, ins.mnemonic, ins.op_str))
for d in sorted(seen):
    tag = {0x10: "begin_pos", 0x14: "unknown",
           0x18: "begin_len(循环点)", 0x1C: "total_len"}.get(d, "")
    print("  +0x%02X %-18s x%d" % (d, tag, len(seen[d])))
    if d == 0x18:
        for s in seen[d]:
            print("      ★ %s" % s)

print()
print("=" * 74)
print("证据 A：全 exe 搜「先取 fmt 基址、再以 +0x18 访问」的代码")
print("=" * 74)
pat = struct.pack("<I", FMT_BASE)
pos, hits = [], []
i = code.find(pat)
while i >= 0:
    pos.append(i)
    i = code.find(pat, i + 1)
for p in pos:
    start = tva + p
    tracked = set()
    chunk = code[p:p + 0x100]
    for ins in md.disasm(chunk, start):
        # 记录哪些寄存器装着 fmt 基址或其派生指针
        if ins.mnemonic in ("mov", "lea", "add") and ins.operands:
            d = ins.operands[0]
            if d.type == X86_OP_REG:
                dn = ins.reg_name(d.reg)
                src = ins.operands[1] if len(ins.operands) > 1 else None
                if src is not None and src.type == X86_OP_MEM:
                    m = src.mem
                    if m.base == 0 and m.index == 0 and m.disp == FMT_BASE:
                        tracked.add(dn)
                    elif m.base and ins.reg_name(m.base) in tracked:
                        tracked.add(dn)
                elif src is not None and src.type == X86_OP_REG:
                    sn = ins.reg_name(src.reg)
                    if sn in tracked:
                        tracked.add(dn)
        for op in ins.operands:
            if op.type != X86_OP_MEM or op.mem.index == 0 or not op.mem.base:
                continue
            if ins.reg_name(op.mem.base) in tracked:
                d = op.mem.disp & 0xFFFFFFFF
                if d in (0x10, 0x14, 0x18, 0x1C):
                    hits.append((ins.address, d, ins.mnemonic, ins.op_str))
for a, d, mn, ops in hits:
    tag = {0x10: "begin_pos", 0x14: "unknown",
           0x18: "begin_len(循环点)", 0x1C: "total_len"}[d]
    print("  0x%08X  +0x%02X %-20s %s %s" % (a, d, tag, mn, ops))
if not any(d == 0x18 for _a, d, _m, _o in hits):
    print("  （以上没有 +0x18 —— 引擎不通过 fmt 基址读循环点）")

print()
print("=" * 74)
print("证据 C：条目指针表 [idx*4+0x4d0d68] 的消费者有没有读 +0x18")
print("=" * 74)
TBL = 0x004D0D68
pat2 = struct.pack("<I", TBL)
refs = []
i = code.find(pat2)
while i >= 0:
    refs.append(tva + i)
    i = code.find(pat2, i + 1)
print("  引用条目指针表的位置共 %d 处" % len(refs))
for r in refs:
    s = max(tva, r - 0x30)
    off2 = s - tva
    print("  --- 0x%08X 附近 ---" % r)
    for ins in md.disasm(code[off2:off2 + 0x90], s):
        mark = ""
        for op in ins.operands:
            if op.type == X86_OP_MEM and op.mem.disp in (0x14, 0x18, 0x1C) \
                    and op.mem.base and ins.reg_name(op.mem.base) not in ("esp", "ebp"):
                mark = "   <== +0x%02X" % op.mem.disp
        if mark or (ins.address <= r < ins.address + len(ins.bytes)):
            print("      0x%08X  %-12s %s%s"
                  % (ins.address, ins.mnemonic, ins.op_str, mark))

print()
print("结论：")
if not any(d == 0x18 for _a, d, _m, _o in hits):
    print("  引擎不读 +0x18（循环点），只读 +0x10 / +0x14 / +0x1C。")
    print("  → 循环只能靠改音频（拼接）实现，现在的方案方向正确。")
else:
    print("  ★ 发现读取 +0x18 的代码 —— 引擎会读循环点，")
    print("    那么直接改字段即可，拼接是多余的，应改回直接写字段。")
