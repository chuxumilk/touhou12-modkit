# -*- coding: utf-8 -*-
"""关键场景：替换成**不同长度**的音乐后再设循环点。

之前的测试上传的是「原样导出」的 WAV（长度不变），那个场景恰好能过。
真实用途是换一首歌：新音频长度与原曲不同，循环点就很容易出问题。

用法: python _test/verify_bgm_loop2.py [port]
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
    """造一段指定秒数的静音 WAV（16bit 立体声 44100Hz）。"""
    n = int(seconds * BPS)
    if n % 4:
        n += 4 - (n % 4)
    pcm = b"\x00" * n
    hdr = (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVE" +
           b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, BPS, 4, 16) +
           b"data" + struct.pack("<I", n))
    return hdr + pcm, n


src_game = os.environ.get("TH12_GAME_DIR") or os.path.join(
    WS, "game", "[th12] 东方星莲船 (汉化版+日文版)")
tmp = tempfile.mkdtemp(prefix="th12-bgm-len-")
print("测试目录: %s" % tmp)
for name in ("th12.dat", "thbgm.dat"):
    s = os.path.join(src_game, name)
    if os.path.isfile(s):
        shutil.copyfile(s, os.path.join(tmp, name))
print("=" * 78)

code, r = jcall("POST", "/api/config", {"game_dir": tmp})
check("设置目录", code == 200, str(r)[:50])

code, bgm = jcall("GET", "/api/bgm")
tracks = bgm.get("tracks") or []
t0 = tracks[0]
print("原曲目 0: %s  时长 %.2f 秒（%d 字节），原循环点 %.2f 秒"
      % (t0["name"], t0["duration"], t0["size"], t0["loop_seconds"]))
orig_end = t0["size"]

from thtk import archive, bgm as bgmmod  # noqa: E402


def read_disk_fmt():
    a = archive.Archive.from_file(os.path.join(tmp, "th12.dat"))
    return bgmmod.BgmFmt.from_bytes(a.read_by_name("thbgm.fmt"))


# =============================================================== 场景 A
print("\n========== 场景 A：换成**更短**的音乐（20 秒）再设循环点 ==========")
wav_a, n_a = make_wav(20.0)
print("   新音频长度: %.2f 秒（%d 字节，原曲 %.2f 秒）"
      % (20.0, n_a, t0["duration"]))
code, r = jcall("POST", "/api/bgm.replace?index=0", raw=wav_a)
check("上传替换成功", code == 200, str(r)[:60])
code, bgm = jcall("GET", "/api/bgm")
t0 = bgm["tracks"][0]
print("   替换后接口回报: size=%s duration=%.2f loop=%s pending_loop=%s"
      % (t0["size"], t0["duration"], t0["loop"], t0["pending_loop"]))

TARGET = int(8.0 * BPS)          # 8 秒循环点，明显小于新长度 20 秒
code, r = jcall("POST", "/api/bgm.loop?index=0&loop=%d" % TARGET)
check("设置循环点 8 秒", code == 200, str(r)[:60])
code, saved = jcall("POST", "/api/save", {"comment": "shorter"})
check("保存成功", code == 200 and saved.get("ok") is not False,
      "errors=%s" % saved.get("errors"))
fmt = read_disk_fmt()
tr = fmt.tracks[0]
print("   磁盘: offset=%d loop=%d end=%d" % (tr.offset, tr.loop, tr.end))
check("新音频长度写对了", tr.end == n_a, "实际 %d 期望 %d" % (tr.end, n_a))
check("★ 循环点 == 8 秒", tr.loop == TARGET,
      "实际 %d（%.2f 秒）" % (tr.loop, tr.loop / float(BPS)))

# =============================================================== 场景 B
print("\n========== 场景 B：换成**更长**的音乐（70 秒）再设循环点 ==========")
wav_b, n_b = make_wav(70.0)
print("   新音频长度: %.2f 秒（%d 字节）" % (70.0, n_b))
code, r = jcall("POST", "/api/bgm.replace?index=0", raw=wav_b)
check("上传替换成功", code == 200, str(r)[:60])
TARGET_B = int(60.0 * BPS)       # 60 秒：比**原曲长度 59.5 秒更长**
code, r = jcall("POST", "/api/bgm.loop?index=0&loop=%d" % TARGET_B)
check("设置循环点 60 秒", code == 200, str(r)[:60])
code, saved = jcall("POST", "/api/save", {"comment": "longer"})
check("保存成功", code == 200 and saved.get("ok") is not False,
      "errors=%s" % saved.get("errors"))
fmt = read_disk_fmt()
tr = fmt.tracks[0]
print("   磁盘: loop=%d end=%d" % (tr.loop, tr.end))
check("新音频长度写对了", tr.end == n_b, "实际 %d 期望 %d" % (tr.end, n_b))
check("★ 循环点 == 60 秒（比原曲长，是个容易踩的场景）",
      tr.loop == TARGET_B, "实际 %d（%.2f 秒）" % (tr.loop, tr.loop / float(BPS)))

# =============================================================== 场景 C
print("\n========== 场景 C：先设循环点、再替换曲目 ==========")
TARGET_C = int(30.0 * BPS)
jcall("POST", "/api/bgm.loop?index=0&loop=%d" % TARGET_C)
wav_c, n_c = make_wav(40.0)
code, r = jcall("POST", "/api/bgm.replace?index=0", raw=wav_c)
check("上传替换成功", code == 200, str(r)[:60])
code, saved = jcall("POST", "/api/save", {"comment": "loop-first"})
check("保存成功", code == 200 and saved.get("ok") is not False,
      "errors=%s" % saved.get("errors"))
fmt = read_disk_fmt()
tr = fmt.tracks[0]
check("循环点在替换后仍然保留（30 秒）", tr.loop == TARGET_C,
      "实际 %d（%.2f 秒）" % (tr.loop, tr.loop / float(BPS)))

# =============================================================== 场景 D
print("\n========== 场景 D：新音频比原循环点还短，循环点应被安全处理 ==========")
wav_d, n_d = make_wav(3.0)       # 3 秒，比当时的循环点 30 秒短
code, r = jcall("POST", "/api/bgm.replace?index=0", raw=wav_d)
check("上传 3 秒音频", code == 200, str(r)[:60])
code, saved = jcall("POST", "/api/save", {"comment": "tiny"})
check("保存成功", code == 200 and saved.get("ok") is not False,
      "errors=%s" % saved.get("errors"))
fmt = read_disk_fmt()
tr = fmt.tracks[0]
print("   磁盘: loop=%d end=%d（原循环点 30 秒 = %d 字节，已超出新长度）"
      % (tr.loop, tr.end, TARGET_C))
check("循环点被安全归零（不能大于轨道长度）", 0 <= tr.loop <= tr.end,
      "loop=%d end=%d" % (tr.loop, tr.end))
check("轨道长度正确", tr.end == n_d, "实际 %d 期望 %d" % (tr.end, n_d))

# =============================================================== 自洽性
print("\n========== 最终 thbgm.dat 自洽性 ==========")
dat = os.path.join(tmp, "thbgm.dat")
size = os.path.getsize(dat)
fmt = read_disk_fmt()
bad = [(t.name, t.offset + t.end) for t in fmt.tracks if t.offset + t.end > size]
check("所有轨道都在文件范围内", not bad, str(bad[:2]))
badloop = [(t.name, t.loop, t.end) for t in fmt.tracks if t.loop > t.end]
check("所有循环点合法", not badloop, str(badloop[:3]))
pre = [(t.name, t.preload, t.end) for t in fmt.tracks if t.preload <= 0]
check("所有预读长度为正数", not pre, str(pre[:3]))
# 注意：`preload` 是「预读深度」，原版 th12.dat 里 18 条**全部** preload > end
# （例如 th12_00.wav: preload=19700116 / end=17719168）。
# 所以这里**不能**断言 preload <= end —— 那是我的错误假设，
# 真正的不变量是 end 描述数据长度、preload 不小于它的一部分。
over = sum(1 for t in fmt.tracks if t.preload > t.end)
print("   参考：本文件 %d/%d 条 preload > end（与原版一致即为正常）"
      % (over, len(fmt.tracks)))

print("\n" + "=" * 78)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - %s" % f)
print("\n测试目录保留: %s" % tmp)
sys.exit(1 if FAIL else 0)
