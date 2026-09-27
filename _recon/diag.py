# -*- coding: utf-8 -*-
"""把 unrar 的输出以多种编码解码后写入 UTF-8 文件，并检查真实路径是否存在。"""
import subprocess, os, io, sys

# 路径跟着仓库走（脚本在 _recon/ 下）；也可用命令行参数/环境变量指定
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAR = os.environ.get("UNRAR") or r"C:\Program Files\WinRAR\UnRAR.exe"
RARFILE = (sys.argv[1] if len(sys.argv) > 1 else
           os.environ.get("TH12_RAR") or
           os.path.join(WS, "[th12]+东方星莲船+(汉化版+日文版).rar"))
OUT = os.path.join(WS, "_recon", "list.txt")

p = subprocess.run([RAR, "lb", "--", RARFILE], capture_output=True)
raw = p.stdout
lines = io.StringIO()
lines.write("raw len=%d rc=%d\n" % (len(raw), p.returncode))
lines.write("first 64 bytes hex: %s\n" % raw[:64].hex())
for enc in ("gbk", "utf-8", "cp932", "cp936"):
    try:
        txt = raw.decode(enc)
    except Exception as e:
        lines.write("[%s] FAIL %s\n" % (enc, e))
        continue
    names = [l for l in txt.splitlines() if l.strip()]
    lines.write("[%s] entries=%d\n" % (enc, len(names)))
    for n in names[:4]:
        lines.write("    %r\n" % n)
    # 检查前几个路径是否真实存在（Windows 侧的真实解码者）
    for n in names[:4]:
        lines.write("    exists(%s)=%s\n" % (enc, os.path.exists(n)))
    break

with open(OUT, "w", encoding="utf-8") as f:
    f.write(lines.getvalue())
print("written", OUT)
