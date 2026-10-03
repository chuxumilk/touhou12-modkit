# -*- coding: utf-8 -*-
"""复现「替换 BGM 后循环点改不了」。

流程完全走 API，和网页操作一致：
  1. 指向副本游戏目录
  2. 导出一首 BGM 的 WAV
  3. 上传替换它（内容改一点，保证能看出区别）
  4. 设置循环点
  5. 保存到游戏
  6. 直接从磁盘读回 thbgm.fmt，检查 loop 是否等于设定值

用法: python _test/verify_bgm_loop.py [port]
"""
import io
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
BYTES_PER_SEC = 44100 * 4

PASS, FAIL = [], []


def check(title, cond, detail=""):
    (PASS if cond else FAIL).append(title)
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", title,
                            ("— " + str(detail)) if detail else ""))


def call(method, path, payload=None, raw=None, timeout=600):
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


def find_source_game():
    ws_game = os.path.join(WS, "game", "[th12] 东方星莲船 (汉化版+日文版)")
    if os.path.isdir(ws_game):
        return ws_game
    env = os.environ.get("TH12_GAME_DIR")
    if env and os.path.isdir(env):
        return env
    return None


src_game = find_source_game()
if not src_game:
    raise SystemExit("找不到游戏目录，请设置 TH12_GAME_DIR")

tmp = tempfile.mkdtemp(prefix="th12-bgm-test-")
print("测试目录: %s" % tmp)
for name in ("th12.dat", "thbgm.dat"):
    s = os.path.join(src_game, name)
    if os.path.isfile(s):
        shutil.copyfile(s, os.path.join(tmp, name))
        print("  复制 %s（%.1f MB）" % (name, os.path.getsize(s) / 1048576.0))
if not os.path.isfile(os.path.join(tmp, "thbgm.dat")):
    raise SystemExit("源目录没有 thbgm.dat，无法测试")

print("=" * 78)
code, r = jcall("POST", "/api/config", {"game_dir": tmp})
check("设置目录", code == 200 and bool(r.get("games")), str(r)[:60])

print("\n① 初始状态")
code, bgm = jcall("GET", "/api/bgm")
tracks = bgm.get("tracks") or []
check("读到曲目列表", len(tracks) > 0, "%d 首" % len(tracks))
if not tracks:
    sys.exit(1)
t0 = tracks[0]
print("   曲目 0: %s  时长 %.1f 秒  循环点 %s 秒"
      % (t0["name"], t0["duration"], t0["loop_seconds"]))

print("\n② 导出曲目 0 的 WAV")
code, wav = call("GET", "/api/bgm.wav?index=0")
check("导出成功", code == 200 and wav[:4] == b"RIFF",
      "HTTP %s, %d 字节" % (code, len(wav)))
if code != 200 or wav[:4] != b"RIFF":
    sys.exit(1)

print("\n③ 把这个 WAV 原样上传替换（内容不变，长度不变）")
code, r = jcall("POST", "/api/bgm.replace?index=0", raw=wav)
check("替换接口返回 200", code == 200, str(r)[:70])
code, pend = jcall("GET", "/api/pending")
kinds = [i.get("kind") for i in (pend.get("items") or [])]
check("待保存里有 bgm 项", "bgm" in kinds, str(kinds))

print("\n④ 设置曲目 0 的循环点（目标 10.00 秒）")
TARGET_SEC = 10.0
TARGET_BYTES = int(TARGET_SEC * BYTES_PER_SEC)
code, r = jcall("POST", "/api/bgm.loop?index=0&loop=%d" % TARGET_BYTES)
check("设置循环点返回 200", code == 200, str(r)[:70])
code, bgm2 = jcall("GET", "/api/bgm")
t0b = (bgm2.get("tracks") or [{}])[0]
print("   接口回报: loop=%s  pending_loop=%s"
      % (t0b.get("loop"), t0b.get("pending_loop")))
check("接口反映出新循环点",
      abs((t0b.get("loop_seconds") or 0) - TARGET_SEC) < 0.01 or
      abs(((t0b.get("pending_loop") or 0) / BYTES_PER_SEC) - TARGET_SEC) < 0.01,
      "loop_seconds=%s pending_loop=%s" % (t0b.get("loop_seconds"),
                                           t0b.get("pending_loop")))

print("\n⑤ 保存到游戏（会重建 thbgm.dat + 写回 thbgm.fmt）")
# 先记下这一轮的「源音频长度」：保存后 track.loop 会等于 源长 − 循环点
src_len = None
code, bgm_pre = jcall("GET", "/api/bgm")
if code == 200 and bgm_pre.get("tracks"):
    src_len = bgm_pre["tracks"][0].get("size")
print("   本轮源音频长度 = %s 字节（%.2f 秒）"
      % (src_len, (src_len or 0) / float(BYTES_PER_SEC)))
code, saved = jcall("POST", "/api/save", {"comment": "bgm-loop-test"})
check("保存返回成功", code == 200 and saved.get("ok") is not False,
      "bgm=%s files=%s errors=%s" % (saved.get("bgm"), saved.get("files"),
                                     saved.get("errors")))

print("\n⑥ 从磁盘读回 thbgm.fmt，检查音频与循环点")
from thtk import archive, bgm as bgmmod  # noqa: E402

a = archive.Archive.from_file(os.path.join(tmp, "th12.dat"))
raw = a.read_by_name("thbgm.fmt")
fmt = bgmmod.BgmFmt.from_bytes(raw)
tr = fmt.tracks[0]
# 新设计：轨道内容 = [循环体][循环体]，循环点字段 = 循环体长度
#         循环体 = 源音频[循环点 : 源尾]
want_body = (src_len - TARGET_BYTES) if src_len else None
print("   磁盘上: %s offset=%d loop=%d end=%d"
      % (tr.name, tr.offset, tr.loop, tr.end))
if want_body:
    print("   期望  : loop=%d（循环体 %.2f 秒）end=%d（2 × 循环体）"
          % (want_body, want_body / float(BYTES_PER_SEC), 2 * want_body))
check("磁盘上的循环点 == 源长 − 设定值（即循环体长度）",
      want_body is not None and tr.loop == want_body,
      "实际 %d vs 期望 %s" % (tr.loop, want_body))
check("轨道长度 = 2 × 循环体（整条轨道就是循环体重复两遍）",
      want_body is not None and tr.end == 2 * want_body,
      "实际 %d vs 期望 %s" % (tr.end, 2 * want_body if want_body else None))
check("循环点在轨道长度之内（否则游戏无法循环）",
      0 < tr.loop < tr.end, "loop=%d end=%d" % (tr.loop, tr.end))

print("\n⑦ 新建的 thbgm.dat 是否自洽")
dat = os.path.join(tmp, "thbgm.dat")
size = os.path.getsize(dat)
with open(dat, "rb") as f:
    head = f.read(16)
check("头部是 ZWAV", head[:4] == b"ZWAV", repr(head[:4]))
bad = []
for i, t in enumerate(fmt.tracks):
    if t.offset + t.end > size:
        bad.append((i, t.name, t.offset + t.end, size))
check("所有轨道都在文件范围内", not bad,
      str(bad[:2]) if bad else "%d 条, 文件 %d 字节" % (len(fmt.tracks), size))
# 循环点合法性
badloop = [(t.name, t.loop, t.end) for t in fmt.tracks if t.loop > t.end]
check("所有循环点都不超过轨道长度", not badloop, str(badloop[:3]))

print("\n⑧ 只改循环点（不替换曲目）也应生效")
# 这一轮要替换的是曲目 1：先读它的当前长度（= 拼接源），
# 保存后它同样会变成 [循环体][循环体]
code, bgm_pre2 = jcall("GET", "/api/bgm")
src1 = None
if code == 200 and len(bgm_pre2.get("tracks") or []) > 1:
    src1 = bgm_pre2["tracks"][1].get("size")
code, r = jcall("POST", "/api/bgm.loop?index=1&loop=%d" % (5 * BYTES_PER_SEC))
check("设置曲目 1 的循环点", code == 200, str(r)[:60])
code, saved2 = jcall("POST", "/api/save", {"comment": "loop-only"})
check("保存成功", code == 200 and saved2.get("ok") is not False,
      str(saved2)[:80])
a2 = archive.Archive.from_file(os.path.join(tmp, "th12.dat"))
fmt2 = bgmmod.BgmFmt.from_bytes(a2.read_by_name("thbgm.fmt"))
tr1 = fmt2.tracks[1]
want1 = (src1 - 5 * BYTES_PER_SEC) if src1 else None
print("   曲目1: loop=%d end=%d（期望 loop=%s end=%s）"
      % (tr1.loop, tr1.end, want1, 2 * want1 if want1 else None))
check("曲目 1 的循环点 = 源长 − 5 秒", want1 is not None and tr1.loop == want1,
      "实际 %d vs 期望 %s" % (tr1.loop, want1))
check("曲目 1 的轨道长度 = 2 × 循环体",
      want1 is not None and tr1.end == 2 * want1,
      "实际 %d vs 期望 %s" % (tr1.end, 2 * want1 if want1 else None))

print("\n" + "=" * 78)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - %s" % f)
print("\n测试目录保留供检查: %s" % tmp)
sys.exit(1 if FAIL else 0)
