# -*- coding: utf-8 -*-
"""创建 GitHub Release 并上传附件（公开仓库用不上 token，这里用 GITHUB_TOKEN）。

用法::

    set GITHUB_TOKEN=ghp_xxx          # 需要 repo 权限
    python _test/make_release.py --version v1.0.0 ^
        --portable release/TH12ModTool-Portable-v1.0.0.zip ^
        --source   release/th12-modkit-v1.0.0-source.zip

做的事：
  1. 校验 tag 是否已存在（不存在就按当前 HEAD 创建，走 GitHub API，不用本地 git）
  2. 创建 release（正文取 CHANGELOG.md 里对应版本那一节）
  3. 上传附件（已存在同名附件会先删掉再传，方便重复执行）

Windows 上会自动读取系统代理（Git 推送用的那个），否则走直连。
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.environ.get("GH_REPO", "chuxumilk/touhou12-modkit")
API = "https://api.github.com"
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def proxy_opener():
    """优先用环境变量代理，其次读 Windows 系统代理（和 git 用的保持一致）。"""
    proxies = {}
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        if os.environ.get(key):
            proxies = {"https": os.environ[key], "http": os.environ[key]}
            break
    if not proxies:
        try:
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            enable, _ = winreg.QueryValueEx(k, "ProxyEnable")
            server, _ = winreg.QueryValueEx(k, "ProxyServer")
            if enable and server:
                if "://" not in server:
                    server = "http://" + server
                proxies = {"https": server, "http": server}
        except Exception:
            pass
    if proxies:
        print("使用代理: %s" % proxies["https"])
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(proxies) if proxies
        else urllib.request.ProxyHandler({}))


OPENER = None


def api(method, path, payload=None, raw=None, content_type=None, timeout=180):
    """调用 GitHub API；payload 走 JSON，raw 走原始字节。"""
    global OPENER
    if OPENER is None:
        OPENER = proxy_opener()
    url = API + path if path.startswith("/") else path
    data = raw
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "token " + TOKEN)
    req.add_header("User-Agent", "th12-modkit-release")
    req.add_header("Accept", "application/vnd.github+json")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            body = r.read()
            return r.status, (json.loads(body.decode("utf-8"))
                              if body else None)
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", "replace")
        try:
            return ex.code, json.loads(body)
        except Exception:
            return ex.code, {"message": body[:400]}


def changelog_section(version):
    """从 CHANGELOG.md 里抽出对应版本的正文。"""
    path = os.path.join(WS, "CHANGELOG.md")
    if not os.path.isfile(path):
        return ""
    with io.open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    want = version.lstrip("v")
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = want in line
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def ensure_release(version, draft):
    code, rel = api("GET", "/repos/%s/releases/tags/%s" % (REPO, version))
    if code == 200:
        print("release 已存在，复用: %s" % rel["html_url"])
        return rel
    body = changelog_section(version) or ("版本 %s" % version)
    payload = {
        "tag_name": version,
        "name": "TH12 魔改工具 %s" % version,
        "body": body,
        "draft": bool(draft),
        "prerelease": False,
    }
    code, rel = api("POST", "/repos/%s/releases" % REPO, payload)
    if code not in (200, 201):
        raise SystemExit("创建 release 失败: HTTP %s %s"
                         % (code, json.dumps(rel, ensure_ascii=False)[:400]))
    print("已创建 release: %s" % rel["html_url"])
    return rel


def upload(rel, path):
    name = os.path.basename(path)
    # 同名附件先删掉，方便重复执行
    code, assets = api("GET", "/repos/%s/releases/%d/assets"
                       % (REPO, rel["id"]))
    if code == 200:
        for a in assets or []:
            if a["name"] == name:
                api("DELETE", "/repos/%s/releases/assets/%d" % (REPO, a["id"]))
                print("  删除旧附件: %s" % name)
    url = ("https://uploads.github.com/repos/%s/releases/%d/assets?name=%s"
           % (REPO, rel["id"], urllib.parse.quote(name)))
    with open(path, "rb") as f:
        data = f.read()
    print("  上传 %s（%.1f MB）…" % (name, len(data) / 1048576.0))
    code, res = api("POST", url, raw=data,
                    content_type="application/octet-stream", timeout=1200)
    if code not in (200, 201):
        raise SystemExit("上传失败: HTTP %s %s"
                         % (code, json.dumps(res, ensure_ascii=False)[:300]))
    print("  ✓ %s（%s 次下载）" % (res["browser_download_url"],
                                  res.get("download_count", 0)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="例如 v1.0.0")
    ap.add_argument("--portable", help="免安装包 zip")
    ap.add_argument("--source", help="源码 zip")
    ap.add_argument("--draft", action="store_true", help="创建为草稿")
    args = ap.parse_args()

    TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not TOKEN:
        # 退回读取 Windows 凭据管理器里 git 存的 GitHub 令牌
        try:
            import ctypes
            import ctypes.wintypes as wt

            class CREDENTIAL(ctypes.Structure):
                _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD),
                            ("TargetName", wt.LPWSTR), ("Comment", wt.LPWSTR),
                            ("LastWritten", wt.FILETIME),
                            ("CredentialBlobSize", wt.DWORD),
                            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                            ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
                            ("Attributes", ctypes.c_void_p),
                            ("TargetAlias", wt.LPWSTR),
                            ("UserName", wt.LPWSTR)]

            advapi = ctypes.windll.advapi32
            ptr = ctypes.POINTER(CREDENTIAL)()
            if advapi.CredReadW("git:https://github.com", 1, 0,
                                ctypes.byref(ptr)):
                blob = ctypes.string_at(ptr.contents.CredentialBlob,
                                        ptr.contents.CredentialBlobSize)
                advapi.CredFree(ptr)
                TOKEN = "".join(ch for ch in blob.decode("utf-8", "replace")
                                if 33 <= ord(ch) <= 126)
                if TOKEN:
                    print("使用 Windows 凭据管理器里的 GitHub 令牌")
        except Exception:
            TOKEN = ""
    if not TOKEN:
        raise SystemExit(
            "没有可用的 GitHub 令牌。请先设置：\n"
            "  set GITHUB_TOKEN=ghp_xxx   （需要 repo 权限）")

    rel = ensure_release(args.version, args.draft)
    for p in (args.portable, args.source):
        if p:
            if not os.path.isfile(p):
                raise SystemExit("找不到文件: %s" % p)
            upload(rel, p)
    print("\n完成: %s" % rel["html_url"])
