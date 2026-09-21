# -*- coding: utf-8 -*-
"""精确提取 rar 内的指定文件（中文路径安全）。
用法: python extract.py <rar> <dest> <inner-path> [inner-path...]
"""
import subprocess, sys, os, io

RAR = r"C:\Program Files\WinRAR\UnRAR.exe"

def main():
    rar, dest = sys.argv[1], sys.argv[2]
    inners = sys.argv[3:]
    os.makedirs(dest, exist_ok=True)
    for inner in inners:
        out_dir = os.path.join(dest, os.path.dirname(inner))
        os.makedirs(out_dir, exist_ok=True)
        cmd = [RAR, "x", "-y", "-o+", "-inul", rar, inner, out_dir + os.sep]
        p = subprocess.run(cmd, capture_output=True)
        print("rc=%d %s" % (p.returncode, inner), flush=True)
        if p.returncode != 0:
            sys.stdout.write(p.stdout.decode("gbk", "replace")[:800])
            sys.stderr.write(p.stderr.decode("gbk", "replace")[:800])

if __name__ == "__main__":
    main()
