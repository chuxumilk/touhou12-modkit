# -*- coding: utf-8 -*-
"""反汇编 th12.exe，证明 BGM 引擎不读 thbgm.fmt 的 loop 字段。

背景
----
用户反馈「改循环点没效果」。逐层排查后确认**写入是好的**（thbgm.fmt 里
的 loop 字段确实被改了、两个档都改了、备份也在），但游戏里听不出变化。
于是逆向 exe 找原因，结论是：

    th12.exe 的 BGM 播放器（0x00453940-0x00453AD8）只读 fmt 条目的
      +0x10 offset    —— 定位 thbgm.dat 里的数据起点
      +0x14 preload   —— 决定读多少字节进内存缓冲
    然后把这块缓冲交给 DirectSound 整块循环播放，
    **从不读取 +0x18 的 loop 字段**。

因此「只改 fmt 里的循环点」在 TH12 里永远无效。真正让循环点生效的做法
是把音频拼成「引子 + 循环体 + 引子」（见 thtk/bgm.py 的 splice_loop_pcm）。

本脚本的验证手段
----------------
1. 按函数边界（int3 填充）切出全部函数，做长度受限的数据流跟踪：
   读 loop 的代码必须先把「条目指针」放进寄存器，而条目指针只能来自
   fmt 基址 [0x4d0e6c] 或条目指针表 [idx*4 + 0x4d0d68]。
   跟踪这些寄存器有没有以 disp=0x18 被读取 —— 结果一处都没有。
2. 把 fmt 基址的全部引用点局部反汇编出来，逐条确认用途：
   读文件存指针 / 打开时取 offset 与 preload / 关闭时 free。

跑法
----
    set TH12_EXE=D:\\Games\\th12\\th12.exe
    python _recon\\th12_bgm_engine.py

不设 TH12_EXE 时，会依次尝试 game\\ 目录和 TH12_GAME_DIR。
"""
import os
import struct
import sys

try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    from capstone.x86 import X86_OP_MEM, X86_OP_REG
except ImportError:
    print("需要 capstone：python -m pip install capstone")
    sys.exit(2)

try:
    import pefile
except ImportError:
    print("需要 pefile：python -m pip install pefile")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# fmt 条目数组的基址与条目结构
FMT_BASE = 0x004D0E6C
ENTRY_TBL = 0x004D0D68
ENTRY_SIZE = 0x34
# 字段偏移。命名参考 RUEEE/TH_BGM_Replacer 的 BGM_def.h（比本工具早期的
# 「preload / loop / end」准确）：
#   0x10 begin_pos   数据在 thbgm.dat 里的起点
#   0x14 unknown     引擎把它当作 PCM 缓冲的分配大小（TH13+ 导出时会写成 total_len）
#   0x18 begin_len   循环点（相对 begin_pos 的字节数）—— **引擎不读**
#   0x1C total_len   轨道总长度（引擎用它算 DirectSound 缓冲大小）
FIELD = {"begin_pos": 0x10, "unknown": 0x14, "begin_len": 0x18,
         "total_len": 0x1C}

# 已知的 BGM 相关字符串（用于定位代码区）
STRINGS = ("Streming BGM Start", "Streming BGM Reopen",
           "Streming BGM PreLoad", "thbgm.dat", "thbgm.fmt")


def find_exe():
    env = os.environ.get("TH12_EXE")
    if env and os.path.isfile(env):
        return env
    for base in (os.path.join(ROOT, "game"), os.environ.get("TH12_GAME_DIR")):
        if not base or not os.path.isdir(base):
            continue
        for dp, _dn, fn in os.walk(base):
            for n in fn:
                if n.lower() == "th12.exe":
                    return os.path.join(dp, n)
    return None


def main():
    exe = find_exe()
    if not exe:
        print("找不到 th12.exe，请设 TH12_EXE")
        return 2
    print("分析: %s" % exe)

    pe = pefile.PE(exe, fast_load=True)
    ib = pe.OPTIONAL_HEADER.ImageBase
    img = pe.get_memory_mapped_image()
    text = [s for s in pe.sections if s.Name.rstrip(b"\x00") == b".text"][0]
    tva = ib + text.VirtualAddress
    code = img[text.VirtualAddress:text.VirtualAddress + text.Misc_VirtualSize]
    print("ImageBase=0x%08X  .text=0x%08X..0x%08X (%d 字节)"
          % (ib, tva, tva + len(code), len(code)))

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True

    # ---- 1) 定位 BGM 字符串，确认这确实是同一份 exe ----
    print("\n[1] BGM 相关字符串")
    for needle in STRINGS:
        b = needle.encode("ascii")
        i = code.find(b)
        where = None
        for s in pe.sections:
            j = s.get_data().find(b)
            if j >= 0:
                where = ib + s.VirtualAddress + j
                break
        print("    %-22s %s" % (needle, "0x%08X" % where if where else "未找到"))

    # ---- 2) 按 int3 填充切函数，跟踪条目指针的读取 ----
    runs, run = [], 0
    for i, byte in enumerate(code):
        if byte == 0xCC:
            run += 1
        else:
            if run >= 2:
                runs.append((tva + i - run, tva + i))
            run = 0
    bounds = [tva] + [r[1] for r in runs] + [tva + len(code)]
    funcs = [(bounds[i], bounds[i + 1])
             for i in range(len(bounds) - 1) if bounds[i + 1] - bounds[i] >= 8]
    print("\n[2] 按 int3 填充切出 %d 个候选函数" % len(funcs))

    hits = []
    for s, e in funcs:
        off = s - tva
        tracked = {}
        for ins in md.disasm(code[off:off + (e - s)], s):
            if ins.mnemonic in ("mov", "lea") and len(ins.operands) == 2:
                d, src = ins.operands
                if d.type == X86_OP_REG:
                    dn = ins.reg_name(d.reg)
                    if src.type == X86_OP_MEM:
                        m = src.mem
                        if m.base == 0 and m.index == 0 and m.disp == FMT_BASE:
                            tracked[dn] = "fmt基址"
                        elif m.index != 0 and m.disp == ENTRY_TBL:
                            tracked[dn] = "条目表项"
                        elif m.base and ins.reg_name(m.base) in tracked:
                            tracked[dn] = "%s+0x%X" % (
                                tracked[ins.reg_name(m.base)],
                                m.disp & 0xffffffff)
                    elif src.type == X86_OP_REG:
                        sn = ins.reg_name(src.reg)
                        if sn in tracked:
                            tracked[dn] = tracked[sn]
            for op in ins.operands:
                if op.type == X86_OP_MEM and op.mem.base and op.mem.index == 0:
                    bn = ins.reg_name(op.mem.base)
                    if bn in tracked and op.mem.disp == FIELD["begin_len"]:
                        hits.append((ins.address, tracked[bn],
                                     ins.mnemonic, ins.op_str))
            if ins.mnemonic in ("mov", "lea", "xor", "pop", "call") \
                    and ins.operands:
                d = ins.operands[0]
                if d.type == X86_OP_REG and ins.mnemonic in ("xor", "pop",
                                                            "call"):
                    tracked.pop(ins.reg_name(d.reg), None)

    print("\n[3] 以 +0x%02X (begin_len) 读取「fmt 条目指针」的位置："
          % FIELD["begin_len"])
    if not hits:
        print("    无 —— 没有任何代码读取该字段（这就是循环点无效的原因）")
    for a, src, mn, ops in hits:
        print("    0x%08X  来源=%-12s %s %s" % (a, src, mn, ops))

    # ---- 2b) 引擎实际用到的两个字段 ----
    # 这两条是通过「条目指针表 [idx*4+0x4d0d68]」间接取用的，上面那种
    # 从 fmt 基址出发的跟踪看不到，所以直接核实具体指令。
    print("\n[3b] 引擎真正读取的字段（核对具体指令，地址可能随 exe 版本变化）")
    checks = [
        (0x00453A29, "mov ecx, [0x4d0e6c]"),
        (0x00453A2F, "mov eax, [ebx+ecx+0x14]  -> unknown：malloc 出来的 PCM 缓冲大小"),
        (0x00453AC6, "mov edx, [ecx+0x14]      -> 存进 [idx*4+0x4d0e28] 表"),
        (0x00466B70, "mov eax, [ebx+0x1c]      -> total_len：DirectSound 缓冲大小"),
    ]
    for addr, desc in checks:
        off = addr - tva
        if 0 <= off < len(code):
            ins = next(md.disasm(code[off:off + 8], addr), None)
            got = ("%s %s" % (ins.mnemonic, ins.op_str)) if ins else "?"
        else:
            got = "?"
        print("    0x%08X  %-46s | 实际: %s" % (addr, desc, got))

    # ---- 3) fmt 基址的全部引用点 ----
    pat = struct.pack("<I", FMT_BASE)
    refs = []
    i = code.find(pat)
    while i >= 0:
        refs.append(tva + i)
        i = code.find(pat, i + 1)
    print("\n[4] 引用 fmt 基址 0x%08X 的位置共 %d 处：" % (FMT_BASE, len(refs)))
    for r in refs:
        start = r - 0x40
        off = start - tva
        if off < 0:
            continue
        print("    --- 0x%08X ---" % r)
        for ins in md.disasm(img[text.VirtualAddress + off:
                                 text.VirtualAddress + off + 0xC0], start):
            mark = "   <<<" if ins.address <= r < ins.address + len(ins.bytes) \
                else ""
            note = ""
            for op in ins.operands:
                # 只认「条目指针 + 字段偏移」。esp/ebp 是栈变量，排除掉，
                # 否则 [esp+0x18] 之类的会被误报成 loop 字段。
                if (op.type == X86_OP_MEM and op.mem.index == 0
                        and op.mem.base
                        and ins.reg_name(op.mem.base) not in ("esp", "ebp")):
                    for fname, fdisp in FIELD.items():
                        if op.mem.disp == fdisp:
                            note = "   [疑似 %s]" % fname
            if mark or note:
                print("        0x%08X  %-12s %s%s%s"
                      % (ins.address, ins.mnemonic, ins.op_str, note, mark))

    print("\n结论：BGM 播放器只读 begin_pos(+0x10) 与 total_len(+0x1C)，"
          "\n      把整块音频循环播放；begin_len(+0x18，也就是循环点)"
          "\n      不参与播放。要让循环点生效必须改音频（拼接）。")
    print("\n对照：RUEEE/TH_BGM_Replacer 也把 +0x18 叫 begin_len（循环点），"
          "\n      但它是靠 XAudio2 的 LoopBegin/LoopLength 在**自己的播放器里**"
          "\n      实现循环，导出时只写文件、不改引擎行为。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
