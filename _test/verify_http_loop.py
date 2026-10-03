# -*- coding: utf-8 -*-
"""通过真实 HTTP 接口走一遍「设循环点 -> 保存」，验证 wire 层。

单函数调用测试已经通过，这里测的是 HTTP 路由 / 参数解析 /
并发是否引入了差异。
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "modtool"))

from thtk import archive, bgm as B  # noqa: E402

SRC = os.environ.get("TH12_PRISTINE_DIR") or os.environ.get("TH12_GAME_DIR")
if not SRC or not os.path.isdir(SRC):
    print("SKIP")
    sys.exit(0)

fails = []
checks = [0]


def check(cond, label, extra=""):
    checks[0] += 1
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s %s" % (label, extra))
        fails.append(label)


def main():
    work = tempfile.mkdtemp(prefix="th12_http_")
    game = os.path.join(work, "game")
    os.makedirs(game)
    for n in os.listdir(SRC):
        if n in ("th12.dat", "th12c.dat", "thbgm.dat"):
            shutil.copy2(os.path.join(SRC, n), os.path.join(game, n))

    import server  # noqa: E402

    # 用独立的数据目录，避免污染真实配置
    server.DATA_DIR = os.path.join(work, "data")
    server.LOG_DIR = os.path.join(server.DATA_DIR, "logs")
    server.STAGING_DIR = os.path.join(server.DATA_DIR, "staging")
    server.CONFIG_PATH = os.path.join(server.DATA_DIR, "config.json")
    server.LOG_PATH = os.path.join(server.LOG_DIR, "modtool.log")
    for d in (server.DATA_DIR, server.LOG_DIR, server.STAGING_DIR):
        os.makedirs(d, exist_ok=True)
    server.STATE.staging = server.STAGING_DIR

    server.set_game_dir(game)
    server.STATE.invalidate()

    port = 18712
    httpd = server.ThreadingHTTPServer(("127.0.0.1", port),
                                      server.Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    time.sleep(0.4)
    print("测试服务器 127.0.0.1:%d  游戏目录副本: %s\n" % (port, game))

    def req(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=600)
        hdr = {"Content-Type": "application/json"}
        payload = json.dumps(body).encode() if body is not None else None
        c.request(method, path, payload, hdr)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw.decode("utf-8"))
        except Exception:
            return r.status, raw

    # 1) 选目录
    st, res = req("POST", "/api/game_dir", {"path": game})
    print("[1] 设置游戏目录 -> HTTP %s %s" % (st, res if st != 200 else "ok"))
    check(st == 200, "设置游戏目录成功")

    # 2) 取曲目列表
    st, res = req("GET", "/api/bgm")
    check(st == 200, "读取曲目列表")
    tracks = res["tracks"]
    idx = None
    for t in tracks:
        if t["duration"] > 30:
            idx = t["index"]
            break
    idx = 0 if idx is None else idx
    t0 = [t for t in tracks if t["index"] == idx][0]
    print("[2] 目标 #%d %s 原 loop=%.2fs duration=%.1fs max_loop=%.1fs"
          % (idx, t0["name"], t0["loop_seconds"], t0["duration"],
             t0["max_loop_seconds"]))

    NEW = 9.25
    nb = int(NEW * B.BYTES_PER_SEC)
    print("\n[3] POST /api/bgm.loop?index=%d&loop=%d" % (idx, nb))
    st, res = req("POST", "/api/bgm.loop?index=%d&loop=%d" % (idx, nb))
    print("    -> HTTP %s %s" % (st, res))
    check(st == 200 and res.get("ok"), "设置循环点 HTTP 成功")
    check(res.get("loop") == nb // 4 * 4, "循环点回显正确",
          str(res.get("loop")))

    # 4) 待保存列表
    st, res = req("GET", "/api/pending")
    print("\n[4] GET /api/pending -> count=%s" % res.get("count"))
    for it in res.get("items", []):
        print("      [%s] %s  %s" % (it["action"], it["name"], it["detail"]))
    check(res.get("count", 0) >= 1, "待保存列表里有循环点")
    has_loop = any(i["action"] == "循环点" for i in res.get("items", []))
    check(has_loop, "待保存列表显示「循环点」条目")

    # 5) 保存
    print("\n[5] POST /api/save")
    st, res = req("POST", "/api/save", {"comment": "http e2e"})
    print("    -> HTTP %s ok=%s bgm=%s files=%s errors=%s"
          % (st, res.get("ok"), res.get("bgm"), res.get("files"),
             res.get("errors")))
    check(st == 200 and res.get("ok"), "保存成功")
    check(res.get("bgm") is True, "响应标记 BGM 已保存")
    check(not res.get("errors"), "无错误", str(res.get("errors")))

    # 6) 从磁盘验证
    print("\n[6] 从磁盘重新读两个档")
    for dat in ("th12.dat", "th12c.dat"):
        a = archive.Archive.from_file(os.path.join(game, dat))
        fmt = B.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))
        t = fmt.tracks[idx]
        print("    %s: loop=%d (%.2fs) preload=%d end=%d"
              % (dat, t.loop, t.loop / float(B.BYTES_PER_SEC),
                 t.preload, t.end))
        check(t.loop == nb // 4 * 4, "%s 循环点已落盘" % dat,
              "期望 %d 实际 %d" % (nb // 4 * 4, t.loop))

    # 7) 再读一次接口，看内存状态是否与磁盘一致
    st, res = req("GET", "/api/bgm")
    t = [x for x in res["tracks"] if x["index"] == idx][0]
    print("\n[7] 保存后 GET /api/bgm -> loop=%.2fs pending_loop=%s"
          % (t["loop_seconds"], t["pending_loop"]))
    check(t["pending_loop"] is None, "保存后暂存循环点已清空")
    check(abs(t["loop_seconds"] - NEW) < 0.01, "接口回显与写入值一致",
          "%.3f" % t["loop_seconds"])

    # 8) 备份是否生成
    print("\n[8] 备份文件")
    for n in ("thbgm.dat", "th12.dat", "th12c.dat"):
        p = os.path.join(game, n + ".modtool.bak")
        ex = os.path.isfile(p)
        print("    %-24s %s" % (n + ".modtool.bak", "有" if ex else "没有"))
        check(ex, "%s 备份已生成" % n)

    httpd.shutdown()
    print("\n" + "=" * 64)
    print("%d 项检查，%d 失败" % (checks[0], len(fails)))
    for f in fails:
        print("  - %s" % f)
    print("（测试目录保留以便排查：%s）" % work)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
