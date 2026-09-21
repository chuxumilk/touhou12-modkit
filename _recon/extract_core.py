# -*- coding: utf-8 -*-
"""用 ASCII 通配符提取，绕开非 ASCII 路径的命令行编码问题。"""
import subprocess, os, sys, json

RAR = r"C:\Program Files\WinRAR\UnRAR.exe"
RARFILE = r"D:\010-Important-Work\DSH相关\东方魔改\[th12]+东方星莲船+(汉化版+日文版).rar"
DEST = r"D:\th12"

jobs = [
    ("dat",   r"*th12.dat"),
    ("exe",   r"*th12.exe"),
    ("datcn", r"*th12c.dat"),
    ("execn", r"*th12c.exe"),
]
os.makedirs(DEST, exist_ok=True)
report = []
for sub, pattern in jobs:
    out = os.path.join(DEST, sub)
    os.makedirs(out, exist_ok=True)
    cmd = [RAR, "x", "-y", "-o+", "-inul", RARFILE, pattern, out + os.sep]
    p = subprocess.run(cmd, capture_output=True)
    got = []
    for root, dirs, files in os.walk(out):
        for f in files:
            fp = os.path.join(root, f)
            got.append((fp, os.path.getsize(fp)))
    report.append({"job": sub, "pattern": pattern, "rc": p.returncode,
                   "files": got,
                   "stdout": p.stdout.decode("gbk", "replace")[:400],
                   "stderr": p.stderr.decode("gbk", "replace")[:400]})

with open(os.path.join(DEST, "_extract_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("done")
