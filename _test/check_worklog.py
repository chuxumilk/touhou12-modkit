# -*- coding: utf-8 -*-
"""校验工作记录 Markdown 的基本结构是否完整。"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOC = os.path.join(ROOT, "改动总结-2026-10-03.md")

FENCE = "`" * 3


def main():
    if not os.path.isfile(DOC):
        print("找不到 %s" % DOC)
        return 1
    s = io.open(DOC, encoding="utf-8").read()
    lines = s.splitlines()
    print("文件: %s" % os.path.basename(DOC))
    print("行数 %d，字符 %d" % (len(lines), len(s)))

    n_fence = s.count(FENCE)
    print("代码块围栏 %d 个 -> %s"
          % (n_fence, "配对正常" if n_fence % 2 == 0 else "★ 未配对"))

    heads = [l for l in lines if l.startswith("#")]
    print("标题 %d 个，最大层级 %d"
          % (len(heads), min(len(h) - len(h.lstrip("#")) for h in heads)))

    # 表格里每行的列数是否一致
    bad = []
    tbl = []
    for i, l in enumerate(lines, 1):
        if l.startswith("|"):
            tbl.append((i, l.count("|")))
        elif tbl:
            widths = {c for _n, c in tbl}
            if len(widths) > 1:
                bad.append(tbl[0][0])
            tbl = []
    print("表格列数不一致的起始行: %s" % (bad or "无"))

    # 内部链接的目标文件是否存在
    import re
    missing = []
    for m in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", s):
        tgt = m.group(1)
        if tgt.startswith(("http", "#")):
            continue
        p = os.path.normpath(os.path.join(ROOT, tgt))
        if not os.path.exists(p):
            missing.append(tgt)
    print("失效的相对链接: %s" % (missing or "无"))

    # 提到的关键文件是否真的存在
    must = ["thtk/bgm.py", "tools/modtool/server.py", "tools/modtool/web/app.js",
            "tools/modtool/web/index.html", "_test/run_all.ps1",
            "_test/verify_loop_splice.py", "_recon/th12_bgm_engine.py",
            "CHANGELOG.md", "_recon/README.md", "tools/modtool/README.md"]
    gone = [f for f in must if not os.path.exists(os.path.join(ROOT, f))]
    print("文中提到但不存在的文件: %s" % (gone or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
