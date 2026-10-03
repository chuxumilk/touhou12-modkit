# -*- coding: utf-8 -*-
"""把游戏目录复制一份到临时目录，打印副本路径。

为什么不用 PowerShell 的 Copy-Item：
  - Windows PowerShell 5.1 在「路径含 < > 之类字符」时会把它当通配符；
  - 非 ASCII 路径经 5.1 的脚本解析容易被换成 GBK，直接变成乱码路径。

Python 读 sys.argv 拿到的就是本来的 UTF-8 路径，可靠得多。
输出：第一行是副本的绝对路径（纯 ASCII），其余诊断信息走 stderr。
"""
import os
import shutil
import sys
import tempfile

SKIP_EXT = (".modtool.bak", ".modtool.prev", ".new", ".restore", ".tmp")


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: make_game_copy.py <game_dir>\n")
        return 2
    src = sys.argv[1]
    if not os.path.isdir(src):
        sys.stderr.write("not a directory: %r\n" % src)
        return 1

    # 副本要保留原来的目录层级：有些套件测的是「指到上一级时自动纠正到
    # 里面的游戏目录」和「扫描能发现游戏目录」，扁平副本会让那几项失败。
    # 所以把副本放成 <临时目录>/<原目录名>/，调用方拿到的是里面的游戏目录。
    base = tempfile.mkdtemp(prefix="th12-runall-")
    dst = os.path.join(base, os.path.basename(src.rstrip("\\/")))
    try:
        os.makedirs(dst)
        for name in os.listdir(src):
            if name.endswith(SKIP_EXT):
                continue
            s = os.path.join(src, name)
            d = os.path.join(dst, name)
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
    except Exception as ex:
        sys.stderr.write("copy failed: %s\n" % ex)
        shutil.rmtree(base, ignore_errors=True)
        return 1

    has = [n for n in ("th12.dat", "th12c.dat") if os.path.isfile(
        os.path.join(dst, n))]
    if not has:
        sys.stderr.write("copy has no th12.dat / th12c.dat\n")
        shutil.rmtree(base, ignore_errors=True)
        return 1
    sys.stderr.write("copied %s -> %s (%s)\n" % (src, dst, ",".join(has)))
    # 只把路径给调用方，必须是最后一行、且干净
    sys.stdout.write(dst + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
