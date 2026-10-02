# 验证脚本

改完代码后跑一遍 `run_all.ps1` 就能确认没有回归。
这些脚本都要指向**真实的 TH12 游戏目录**（里面有 `th12.dat` 或 `th12c.dat`）。

## 一键自检（推荐）

```powershell
$env:TH12_GAME_DIR     = 'D:\Games\th12'
$env:TH12_DAMAGED_DIR  = 'D:\Games\th12-broken'   # 可选：一份归档损坏的副本
.\_test\run_all.ps1
```

它会依次跑完所有套件，最后汇总；**有任何失败就以退出码 1 结束**。
只跑接口层（不启浏览器）：加 `-SkipBrowser`。

目录按这个顺序自动查找，找不到时脚本会提示怎么设：

1. 环境变量 `TH12_GAME_DIR`
2. 仓库里的 `game/[th12] 东方星莲船 (汉化版+日文版)/`
3. 仓库里的 `测试/[th12] 东方星莲船 (汉化版+日文版)/`

## 各套件

| 脚本 | 项数 | 覆盖内容 |
| --- | --- | --- |
| `verify_settings.py` | 31 | 候选目录扫描、`/api/check` 实时校验、指到上一级自动纠正、无效路径提示、设置后各接口能否读、配置持久化、重新扫描 |
| `verify_damaged.py` | 19 | 游戏数据损坏（归档被写坏）时界面仍可用：`/api/state` 不许 500，贴图/对话照常，音乐降级为提示 |
| `verify_workflow.py` | 15 | 真实流程：列贴图 → 导出 PNG → 替换（只进暂存、游戏文件不动）→ 改对话文本 → 清空暂存 |
| `verify_save.py` | 14 | 真正执行一次「保存到游戏」：写完自检、归档完整性、没有残留临时文件；只改音乐室评论并在结束时还原 |
| `verify_restore.py` | 18 | 还原安全性：空/截断/垃圾备份必须被拒绝且游戏文件不动；好备份能还原；硬链接场景 |
| `verify_bgm_replace_loop.py` | 22 | **替换 BGM 后循环点能否设置**：界面拿到的时长/上限、替换+循环点叠加保存、导出 WAV 合法性 |
| `verify_bgm_loop.py` | 16 | 循环点基本流程（上传等长音频） |
| `verify_bgm_loop2.py` | 21 | 换成**更短/更长**的音乐后再设循环点、只改循环点、先设循环点再替换、新音频比循环点还短时的安全处理 |

## 浏览器验收（`run_all.ps1` 会自动起无头 Chrome）

| 脚本 | 覆盖内容 |
| --- | --- |
| `cdp_pick_file.js` | 「选文件」这条路：填 `th12c.dat` → 自动定位目录 → 应用成功；填错文件/不存在的提示 |
| `cdp_one_dir.js` | 「填目录」这条路：粘贴目录 → 实时校验 → 应用 → 版本下拉出现 jp/cn |
| `cdp_encoding.js` | 文档编码判定（UTF-8/带BOM/UTF-16/GBK）、导出确实带 BOM、`boot()` 容错 |
| `cdp_bgm.js` | 替换 BGM 后界面显示新时长、循环点输入框上限、按钮可用性、待保存提示 |
| `cdp_wave.js` | **波形播放器**：波形绘制（取像素验证）、点波形跳转、循环区随循环点移动、拖动 A 标记、播放/停止与播放头、定时器清理、「设为当前位置」 |

手动跑浏览器套件时，先自己起 Chrome（调试端口 9222）：

```bat
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --headless=new --remote-debugging-port=9222 --remote-allow-origins=* ^
  --user-data-dir=%TEMP%\th12-chrome --no-first-run --no-sandbox about:blank
node _test\cdp_bgm.js 8799 9222
```

> 这些脚本都**新开标签页**再操作（脚本内部已处理），
> 否则会读到上一个页面的残留状态，得出错误结论。

## 诊断与工具

| 脚本 | 用途 |
| --- | --- |
| `audit.py` | 静态自查：DOM id 引用、前后端接口对应、死代码、`innerHTML` 拼接、线程锁、静默吞异常 |
| `diagnose_th12c.py` | 判断某份归档是「数据流坏了」还是「条目表长度对不上」 |
| `verify_review_claims.py` | 逐条实测代码审查里的主张（含被推翻的） |
| `verify_patched_bug.py` | 验证「`to_bytes_patched` 会写坏条目」这一主张（实测不成立） |
| `make_fake_game.py` | 造一份结构合法的最小假游戏目录，没有真游戏时也能测 |

## 注意

- **`verify_save.py` 会真的写游戏文件**，请指向副本；它自带备份并在结束时还原。
- `verify_bgm_*.py` 会在**临时目录**里复制一份 `th12.dat` + `thbgm.dat`（约 420 MB）
  再操作，不会碰你的游戏；跑完自动清理。
- 其余脚本只写「暂存」，最后会清空，**不改游戏文件**。
- 假游戏目录、Chrome profile 等临时产物已在 `.gitignore` 里，不会入库。

## 已知的「看着像 bug、其实是预期」的现象

排查时先看这两条，能省不少时间：

1. **`th12c.dat` 有部分条目「长度与归档记录不一致」**
   实测该文件 179 条中有 48 条解出偏短、59 条偏长，但解压从不抛错 ——
   是**条目表 `size` 字段与数据流对不上**，不是数据损坏。
   官方 `thdat` 同样「解出多少算多少」，所以工具不会因此拒绝这份归档，
   只在设置目录时提示一句。用 `diagnose_th12c.py` 可自行复现。

2. **`thbgm.fmt` 里 `preload >= end` 是正常的**
   原版 `th12.dat` 的 18 条**全部**是 `preload > end`
   （例如 `th12_00.wav`: preload=19700116 / end=17719168）。
   `end` 才是数据长度，`preload` 是预读深度，别拿它当长度校验。
