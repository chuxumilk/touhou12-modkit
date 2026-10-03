# -*- coding: utf-8 -*-
"""恢复 tools/modtool/config.json 里用户选定的游戏目录。

自检脚本 run_all.ps1 每次启动服务器都会把 config.json 清成 {}，
所以跑完自检需要把用户的选择写回去。
"""
import io
import json
import os
import sys

GAME = r"D:\010-Important-Work\coding相关\[th12] 东方星莲船 (汉化版+日文版)"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = os.path.join(ROOT, "tools", "modtool", "config.json")


def main():
    if len(sys.argv) > 1:
        game = sys.argv[1]
    else:
        game = GAME
    if not os.path.isdir(game):
        print("目录不存在: %s" % game)
        return 1
    data = {"recent_dirs": [game], "game_dir": game, "last_game": "jp"}
    # 不能带 BOM：server.load_config 用 utf-8 读，带 BOM 会解析失败
    with io.open(CFG, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False)
    print("已写入 %s" % CFG)
    print(io.open(CFG, encoding="utf-8").read())
    # 回读校验
    back = json.load(io.open(CFG, encoding="utf-8"))
    ok = back.get("game_dir") == game
    print("回读校验: %s" % ("OK" if ok else "FAIL -> %r" % back.get("game_dir")))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
