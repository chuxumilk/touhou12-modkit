# -*- coding: utf-8 -*-
"""通读项目时的静态自查：前后端一致性、死代码、可疑写法。

用法: python _test/audit.py
"""
import io
import os
import re

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(WS)

html = io.open("tools/modtool/web/index.html", encoding="utf-8").read()
js = io.open("tools/modtool/web/app.js", encoding="utf-8").read()
css = io.open("tools/modtool/web/style.css", encoding="utf-8").read()
srv = io.open("tools/modtool/server.py", encoding="utf-8").read()

print("=" * 74)
print("① 前端引用的 DOM id 是否都存在")
html_ids = set(re.findall(r'id="([^"]+)"', html))
js_ids = set(re.findall(r'\$\$?\("#([A-Za-z0-9_\-]+)"\)', js))
missing = sorted(js_ids - html_ids)
print("   JS 引用 %d 个 id，HTML 里有 %d 个" % (len(js_ids), len(html_ids)))
print("   缺失:", missing or "无 ✓")

print("\n② 前后端接口对应关系")
routes = set(re.findall(r'route == "([a-z0-9_.\-]+)"', srv))
called = set(re.findall(r"/api/([a-z0-9_.\-]+)", js))
print("   后端路由 %d 个，前端调用 %d 个" % (len(routes), len(called)))
print("   前端调了但后端没有:", sorted(called - routes) or "无 ✓")
print("   后端有但前端没调:", sorted(routes - called) or "无")

print("\n③ 后端定义了但全文件只出现一次的顶层函数（疑似死代码）")
funcs = re.findall(r"^def ([a-z_][a-z0-9_]*)", srv, re.M)
dead = []
for f in funcs:
    n = len(re.findall(r"\b%s\b" % re.escape(f), srv))
    if n <= 1:
        dead.append(f)
print("   只定义未调用:", dead or "无")
for f in dead:
    line = srv[:srv.index("def %s" % f)].count("\n") + 1
    print("      %s  (第 %d 行)" % (f, line))

print("\n④ 类方法里定义了但没用的顶层类")
for cls in ("Progress", "Logger", "State", "Handler"):
    body = re.search(r"^class %s\b.*?(?=^class |\Z)" % cls, srv, re.M | re.S)
    if not body:
        continue
    methods = re.findall(r"^    def ([a-z_][a-z0-9_]*)", body.group(0), re.M)
    unused = []
    for m in methods:
        if m.startswith("__"):
            continue
        n = len(re.findall(r"[\.\s]%s\(" % re.escape(m), srv))
        if n <= 1:
            unused.append(m)
    if unused:
        print("   %s: %s" % (cls, ", ".join(unused)))

print("\n⑤ innerHTML 拼接（文件名/路径可能含 < > & 等字符）")
risky = []
for i, line in enumerate(js.splitlines(), 1):
    if "innerHTML" in line and ("${" in line or " + " in line):
        risky.append((i, line.strip()[:88]))
print("   共 %d 处" % len(risky))
for i, l in risky[:14]:
    print("      %d: %s" % (i, l))

print("\n⑥ createObjectURL 是否配对 revoke")
made = len(re.findall(r"createObjectURL", js))
revoked = len(re.findall(r"revokeObjectURL", js))
print("   createObjectURL %d 次 / revokeObjectURL %d 次" % (made, revoked))
if made > revoked:
    print("   ⚠ 有 %d 个对象 URL 没被释放" % (made - revoked))

print("\n⑦ 定时器与轮询")
print("   setInterval:", len(re.findall(r"setInterval", js)),
      " clearInterval:", len(re.findall(r"clearInterval", js)))
print("   setTimeout :", len(re.findall(r"setTimeout", js)),
      " clearTimeout :", len(re.findall(r"clearTimeout", js)))

print("\n⑧ 后端裸 except / 静默吞异常")
swallow = []
for i, line in enumerate(srv.splitlines(), 1):
    if re.match(r"\s*except (Exception|OSError|ValueError)?\s*:", line) and \
            i + 1 <= len(srv.splitlines()):
        nxt = srv.splitlines()[i] if i < len(srv.splitlines()) else ""
        if re.match(r"\s*(pass|return None|return \[\]|continue)\s*$", nxt):
            swallow.append((i, line.strip() + " → " + nxt.strip()))
print("   共 %d 处静默吞掉异常" % len(swallow))
for i, l in swallow[:10]:
    print("      %d: %s" % (i, l))

print("\n⑨ 线程安全：直接改 STATE 的容器（没走 STATE.lock）")
# 用缩进追踪：记录当前处于哪些 with 块的缩进层级，
# 只有「不在 with STATE.lock: 作用域内」的赋值才算可疑。
hits = []
lock_indents = []          # 当前生效的 with STATE.lock 块体缩进
for i, line in enumerate(srv.splitlines(), 1):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    indent = len(line) - len(line.lstrip())
    # 离开作用域
    while lock_indents and indent <= lock_indents[-1] and \
            not stripped.startswith("with "):
        lock_indents.pop()
    if re.match(r"with STATE\.lock\s*:", stripped):
        lock_indents.append(indent)
        continue
    if re.search(r"\bSTATE\.(anm_cache|texture_index|archives|pending|batch"
                 r"|bgm_pending|bgm_loop|bgm_replaced|fmt)\s*=", stripped):
        if not lock_indents or indent <= lock_indents[-1]:
            hits.append((i, stripped))
print("   共 %d 处" % len(hits))
for i, l in hits:
    print("      %d: %s" % (i, l))
