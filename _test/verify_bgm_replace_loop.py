# -*- coding: utf-8 -*-
"""复现并验收：「替换 BGM 后循环点改不了」。

根因（已确认）：/api/bgm 在曲目被替换后仍然回报**原曲**的 duration/size，
前端据此显示旧时长；而保存时按**新音频长度**校验循环点，
于是用户按旧时长设的值很容易超过新长度、被安全归零 ——
看起来就是「换了 BGM 之后循环点怎么都改不了」。

本脚本用「换成更短的曲子」把这条链路走完，并检查界面拿到的数据是否一致。

用法: python _test/verify_bgm_replace_loop.py [port]
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import urllib.error
import urllib.request

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS)
sys.path.insert(0, os.path.join(WS, "tools", "modtool"))

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
BASE = "http://127.0.0.1:%d" % PORT
BPS = 44100 * 4
PASS, FAIL = [], []


def check(title, cond, detail=""):
    (PASS if cond else FAIL).append(title)
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", title,
                            ("— " + str(detail)) if detail else ""))


def call(method, path, payload=None, raw=None, timeout=900):
    data = raw if raw is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read()
    except Exception as ex:
        return None, repr(ex).encode()


def jcall(*a, **kw):
    code, body = call(*a, **kw)
    try:
        return code, json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return code, {"_raw": body[:200]}


def make_wav(seconds):
    n = int(seconds * BPS)
    if n % 4:
        n += 4 - (n % 4)
    return (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVE" +
            b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, BPS, 4, 16) +
            b"data" + struct.pack("<I", n) + b"\x00" * n), n


src_game = os.environ.get("TH12_GAME_DIR") or os.path.join(
    WS, "game", "[th12] 东方星莲船 (汉化版+日文版)")
tmp = tempfile.mkdtemp(prefix="th12-bgm-repl-")
print("测试目录: %s" % tmp)
for name in ("th12.dat", "thbgm.dat"):
    s = os.path.join(src_game, name)
    if os.path.isfile(s):
        shutil.copyfile(s, os.path.join(tmp, name))
print("=" * 78)

from thtk import archive, bgm as bgmmod  # noqa: E402

code, r = jcall("POST", "/api/config", {"game_dir": tmp})
check("设置目录", code == 200, str(r)[:50])

code, bgm = jcall("GET", "/api/bgm")
t0 = bgm["tracks"][0]
orig_sec = t0["duration"]
orig_size = t0["size"]
print("原曲: %s  %.2f 秒（%d 字节）循环点 %.2f 秒"
      % (t0["name"], orig_sec, orig_size, t0["loop_seconds"]))

# ---------------------------------------------------------------- 替换成 20 秒
print("\n① 替换成 20 秒的曲子")
wav, n20 = make_wav(20.0)
code, r = jcall("POST", "/api/bgm.replace?index=0", raw=wav)
check("替换成功", code == 200, str(r)[:60])

code, bgm = jcall("GET", "/api/bgm")
t0 = bgm["tracks"][0]
print("   接口回报: duration=%.2f size=%d orig_size=%s pending_size=%s"
      % (t0["duration"], t0["size"], t0.get("orig_size"),
         t0.get("pending_size")))
check("★ duration 反映新音频（20 秒）", abs(t0["duration"] - 20.0) < 0.05,
      "实际 %.2f 秒" % t0["duration"])
check("★ size 反映新音频", t0["size"] == n20,
      "实际 %d 期望 %d" % (t0["size"], n20))
check("原长度仍可查（便于界面显示对比）",
      t0.get("orig_size") == orig_size, str(t0.get("orig_size")))
check("循环点上限 = 新音频长度",
      abs((t0.get("max_loop_seconds") or 0) - 20.0) < 0.05,
      str(t0.get("max_loop_seconds")))

# ---------------------------------------------------------------- 按旧时长设循环点
print("\n② 按「原曲时长」设一个循环点（这正是用户会踩的坑）")
bad_sec = round(orig_sec - 1, 1)        # 例如 58.5 秒，超过新的 20 秒
bad_bytes = int(bad_sec * BPS)
code, r = jcall("POST", "/api/bgm.loop?index=0&loop=%d" % bad_bytes)
# 以前这里返回 200，超界值会在重建 thbgm.dat 时被静默清零，
# 用户看到「已设为 58.5 秒」却永远听不到效果。现在当场拒绝并说明原因。
check("接口直接拒绝超界循环点（不再静默清零）", code == 400,
      "HTTP %s %s" % (code, str(r)[:80]))
check("拒绝时说明上限", "20.00" in str(r), str(r)[:100])
code, bgm = jcall("GET", "/api/bgm")
t0 = bgm["tracks"][0]
check("被拒绝的值没有进入暂存", t0.get("pending_loop") in (None, 0),
      str(t0.get("pending_loop")))

# ---------------------------------------------------------------- 改成合法值
print("\n③ 改成合法循环点（12 秒）并保存")
good_bytes = int(12.0 * BPS)
jcall("POST", "/api/bgm.loop?index=0&loop=%d" % good_bytes)
code, saved = jcall("POST", "/api/save", {"comment": "repl+loop"})
check("保存成功", code == 200 and saved.get("ok") is not False,
      "errors=%s" % saved.get("errors"))

a = archive.Archive.from_file(os.path.join(tmp, "th12.dat"))
fmt = bgmmod.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))
tr = fmt.tracks[0]
print("   磁盘: loop=%d（%.2f 秒）end=%d（%.2f 秒）"
      % (tr.loop, tr.loop / float(BPS), tr.end, tr.end / float(BPS)))
# 保存时会把「循环点之后的那一段」重复两遍作为整条轨道，所以
# 最终长度 = 2 × (新音频 − 循环点)。这是循环点真正生效的机制
# （引擎不读 thbgm.fmt 的 loop 字段，只把整块音频循环播放）。
body = n20 - good_bytes
check("轨道长度 = 2 × (新音频 − 循环点)", tr.end == 2 * body,
      "%d vs 2×(%d-%d)=%d" % (tr.end, n20, good_bytes, 2 * body))
check("★ 循环点字段 = 循环体长度", tr.loop == body,
      "实际 %.2f 秒，期望 %.2f 秒" % (tr.loop / float(BPS), body / float(BPS)))
check("preload 覆盖整条（否则循环体会被截断）", tr.preload >= tr.end,
      "preload=%d end=%d" % (tr.preload, tr.end))

# ---------------------------------------------------------------- 导出 WAV 合法
print("\n④ 替换后导出的 WAV 必须是合法 WAV")
wav2, n2 = make_wav(15.0)
jcall("POST", "/api/bgm.replace?index=1", raw=wav2)
code, out = call("GET", "/api/bgm.wav?index=1")
print("   导出 %d 字节" % len(out))
check("有 RIFF 头", out[:4] == b"RIFF", repr(out[:4]))
check("长度 = 44 字节头 + PCM", len(out) == 44 + n2,
      "%d vs %d" % (len(out), 44 + n2))
check("data 块长度字段正确",
      struct.unpack_from("<I", out, 40)[0] == n2,
      str(struct.unpack_from("<I", out, 40)[0]))

# ---------------------------------------------------------------- pending_count
print("\n⑤ 待保存计数：替换与循环点都要算进去")
# 先清干净：放弃暂存，避免上一步的替换干扰计数
jcall("POST", "/api/pending.clear", {})
code, bgm = jcall("GET", "/api/bgm")
check("清空后 pending_count = 0", bgm.get("pending_count") == 0,
      str(bgm.get("pending_count")))

wav3, _ = make_wav(10.0)
jcall("POST", "/api/bgm.replace?index=2", raw=wav3)
code, bgm = jcall("GET", "/api/bgm")
check("只替换曲目 → pending_count = 1", bgm.get("pending_count") == 1,
      str(bgm.get("pending_count")))

jcall("POST", "/api/bgm.loop?index=3&loop=%d" % int(4.0 * BPS))
code, bgm = jcall("GET", "/api/bgm")
check("再设一个循环点 → pending_count = 2", bgm.get("pending_count") == 2,
      str(bgm.get("pending_count")))

jcall("POST", "/api/bgm.loop?index=2&loop=%d" % int(3.0 * BPS))
code, bgm = jcall("GET", "/api/bgm")
check("给已替换的曲目设循环点 → pending_count = 3（两者叠加，不互相覆盖）",
      bgm.get("pending_count") == 3, str(bgm.get("pending_count")))

print("\n⑥ 替换 + 循环点一起保存，两者都要落盘")
code, saved = jcall("POST", "/api/save", {"comment": "both"})
check("保存成功", code == 200 and saved.get("ok") is not False,
      "errors=%s" % saved.get("errors"))
a = archive.Archive.from_file(os.path.join(tmp, "th12.dat"))
fmt = bgmmod.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))
t2, t3 = fmt.tracks[2], fmt.tracks[3]
b2 = int(10.0 * BPS) - int(3.0 * BPS)        # 曲目2：新音频 10 秒，循环点 3 秒
print("   曲目2: end=%d loop=%d（新音频 10 秒，循环体 7 秒）" % (t2.end, t2.loop))
print("   曲目3: end=%d loop=%d（只改循环点）" % (t3.end, t3.loop))
check("曲目2 长度 = 2 × 循环体 7 秒", t2.end == 2 * b2,
      "%d vs %d" % (t2.end, 2 * b2))
check("曲目2 循环点字段 = 循环体 7 秒", t2.loop == b2,
      "实际 %.2f 秒" % (t2.loop / float(BPS)))
check("曲目3 循环点字段 = 循环体长度（原曲长 − 4 秒）",
      t3.loop == t3.end // 2,
      "loop=%d end=%d" % (t3.loop, t3.end))
# 曲目3 只设了循环点（没替换音频），也一样要重拼并把 preload 撑到覆盖整条
check("只改循环点的曲目也做了拼接且 preload 覆盖整条",
      t3.end > 0 and t3.preload >= t3.end,
      "end=%d preload=%d" % (t3.end, t3.preload))

print("\n" + "=" * 78)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - %s" % f)
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if FAIL else 0)
