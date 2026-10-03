# -*- coding: utf-8 -*-
"""端到端实测：改循环点 -> 保存 -> 重新从盘上读档，循环值是否真的变了。

用真实游戏文件的【副本】，绝不碰原游戏目录。
"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "modtool"))

from thtk import archive, bgm as B  # noqa: E402

SRC = os.environ.get("TH12_PRISTINE_DIR") or os.environ.get("TH12_GAME_DIR")
if not SRC or not os.path.isdir(SRC):
    print("SKIP: 需要 TH12_PRISTINE_DIR 或 TH12_GAME_DIR")
    sys.exit(0)

FMT_ENTRY = "thbgm.fmt"
fails = []
checks = [0]


def check(cond, label, extra=""):
    checks[0] += 1
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s %s" % (label, extra))
        fails.append(label)


def read_fmt_from(arch_path):
    """从归档文件里现读 thbgm.fmt（绕过一切缓存）。"""
    a = archive.Archive.from_file(arch_path)
    raw = a.read_by_name(FMT_ENTRY)
    return B.BgmFmt.from_bytes(raw), raw, a


def which_dats(d):
    out = []
    for n in ("th12.dat", "th12c.dat"):
        p = os.path.join(d, n)
        if os.path.isfile(p):
            out.append(p)
    return out


def main():
    work = tempfile.mkdtemp(prefix="th12_e2e_")
    game = os.path.join(work, "game")
    os.makedirs(game)
    dats_src = which_dats(SRC)
    if not dats_src:
        print("SKIP: 源目录没有 th12.dat / th12c.dat")
        return 0
    print("源目录: %s" % SRC)
    print("副本目录: %s" % game)

    for p in dats_src:
        shutil.copy2(p, os.path.join(game, os.path.basename(p)))
    for n in ("thbgm.dat", "thbgm.fmt"):
        p = os.path.join(SRC, n)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(game, n))

    # 哪个档里有 thbgm.fmt
    print("\n[1] 归档里有没有 thbgm.fmt")
    present = {}
    for p in which_dats(game):
        a = archive.Archive.from_file(p)
        i = a.index_of(FMT_ENTRY)
        present[os.path.basename(p)] = (i, len(a.entries))
        print("  %s: index=%d, 条目数=%d" % (os.path.basename(p), i, len(a.entries)))
    check(any(v[0] >= 0 for v in present.values()),
          "至少一个归档含 thbgm.fmt")

    fmt0, raw0, _ = read_fmt_from(which_dats(game)[0])

    # ---- 直接调服务器逻辑 ----
    import server  # noqa: E402
    server.set_game_dir(game)
    server.STATE.invalidate()

    tracks = server.list_bgm()["tracks"]
    print("\n[2] 选一条真实曲目")
    target = None
    for t in tracks:
        if t.get("duration", 0) > 30:
            target = t
            break
    if target is None:
        print("SKIP: 没有足够长的曲目")
        return 0
    idx = target["index"]
    name = target["name"]
    old_loop = target["loop"]
    print("  目标: #%d %s  原 loop=%d (%.2fs) end=%d"
          % (idx, name, old_loop, target["loop_seconds"], target["size"]))
    NEW_SEC = 12.5
    new_loop = int(NEW_SEC * B.BYTES_PER_SEC) // 4 * 4
    print("\n[3] 设置循环点 -> %.2fs (%d 字节)" % (NEW_SEC, new_loop))
    r = server.bgm_set_loop(idx, new_loop)
    print("  bgm_set_loop -> %s" % r)
    check(r.get("ok"), "bgm_set_loop 返回 ok")
    check(r.get("loop") == new_loop, "返回值等于请求值",
          "得到 %s" % r.get("loop"))
    check(new_loop != old_loop, "新循环点与原值不同（否则测不出问题）")

    # list_bgm 是否反映
    t2 = [t for t in server.list_bgm()["tracks"] if t["index"] == idx][0]
    print("  list_bgm: loop=%d pending_loop=%s" % (t2["loop"], t2["pending_loop"]))

    print("\n[4] 保存到游戏")
    res = server.save_all("e2e loop test")
    print("  save_all -> ok=%s files=%s bgm=%s errors=%s"
          % (res.get("ok"), res.get("files"), res.get("bgm"),
             res.get("errors")))
    check(res.get("ok"), "save_all 成功")
    check(not res.get("errors"), "save_all 无错误", str(res.get("errors")))

    print("\n[5] 从盘上重新读归档，看循环值")
    for p in which_dats(game):
        base = os.path.basename(p)
        try:
            fmt1, raw1, _ = read_fmt_from(p)
        except Exception as ex:
            check(False, "%s 可读" % base, str(ex))
            continue
        t = fmt1.tracks[idx]
        print("  %s: %s loop=%d (%.2fs) preload=%d end=%d"
              % (base, t.name, t.loop, t.loop / float(B.BYTES_PER_SEC),
                 t.preload, t.end))
        check(t.loop == new_loop, "%s 里循环点已更新" % base,
              "期望 %d，实际 %d" % (new_loop, t.loop))
        check(raw1 != raw0, "%s 里 thbgm.fmt 内容确实变了" % base)

    # ---- 独立的 thbgm.fmt 文件（如果游戏用外置的）----
    loose = os.path.join(game, "thbgm.fmt")
    if os.path.isfile(loose):
        f = B.BgmFmt.from_file(loose)
        print("  外置 thbgm.fmt: loop=%d" % f.tracks[idx].loop)
        check(f.tracks[idx].loop == new_loop, "外置 thbgm.fmt 同步更新")

    # ---- 检查 thbgm.dat 里循环点处的数据是否真的可循环 ----
    print("\n[6] thbgm.dat 里循环区间是否有效")
    fmt1, _, _ = read_fmt_from(which_dats(game)[0])
    tr = fmt1.tracks[idx]
    size = os.path.getsize(os.path.join(game, "thbgm.dat"))
    check(tr.offset + tr.end <= size, "轨道末端在文件内",
          "offset+end=%d > %d" % (tr.offset + tr.end, size))
    check(0 < tr.loop < tr.end, "循环点在轨道范围内",
          "loop=%d end=%d" % (tr.loop, tr.end))
    check(tr.loop % 4 == 0, "循环点 4 字节对齐", "loop=%d" % tr.loop)

    print("\n" + "=" * 60)
    print("%d 项检查，%d 失败" % (checks[0], len(fails)))
    for f in fails:
        print("  - %s" % f)
    shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
