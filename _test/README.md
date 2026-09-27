# 验证脚本

用来验证「设置游戏目录」和相关功能是否正常。改完 `server.py` / `web/` 之后跑一遍，
避免又把目录设置弄坏。

## 先准备一份游戏目录

这些脚本要指向**真实的 TH12 游戏目录**（里面有 `th12.dat` 或 `th12c.dat`）。
按优先顺序自动查找：

1. 环境变量 `TH12_GAME_DIR`
2. 仓库里的 `game/[th12] 东方星莲船 (汉化版+日文版)/`
3. 仓库里的 `测试/[th12] 东方星莲船 (汉化版+日文版)/`

都没找到时会直接提示你怎么设：

```bat
set TH12_GAME_DIR=D:\Games\th12
```

`verify_damaged.py` 还需要一份**归档已损坏**的副本（用来验证降级行为），
用 `TH12_DAMAGED_DIR` 指定；不设就跳过那几项。

## 怎么跑

先在一个终端启动工具（用测试端口，别占用 8765）：

```bat
python tools\modtool\server.py --port 8799 --no-browser
```

再在另一个终端运行（**每跑一个前先清空 config 并重启服务**，
否则前一个脚本留下的目录会影响后一个）：

```bat
echo {} > tools\modtool\config.json
python _test\verify_settings.py 8799    :: 设置目录：31 项
python _test\verify_damaged.py 8799     :: 数据损坏时降级：19 项
python _test\verify_workflow.py 8799    :: 真实改贴图/对话流程：15 项
python _test\verify_save.py 8799 "D:\Games\th12"   :: 真实保存：14 项
```

全部 PASS 会打印「通过 N 项，失败 0 项」并以 0 退出。

## 各脚本干什么

| 脚本 | 覆盖内容 |
| --- | --- |
| `verify_settings.py` | 候选目录扫描、`/api/check` 实时校验、指到上一级自动纠正、无效路径提示、设置后各接口能否读、配置持久化、重新扫描 |
| `verify_damaged.py` | 游戏数据损坏（归档被写坏）时，界面必须仍可用：`/api/state` 不许 500，贴图/对话照常，音乐降级为提示 |
| `verify_workflow.py` | 真实流程：列贴图 → 导出 PNG → 替换（进暂存、游戏文件不动）→ 改对话文本 → 清空暂存 |
| `verify_save.py` | 真正执行一次「保存到游戏」：验证写完自检、归档完整性、没有残留临时文件；**只改音乐室评论并在结束时还原** |
| `make_fake_game.py` | 造一份结构合法的最小假游戏目录（`_test/gameA`、`gameB`、`notagame`），没有真游戏时也能测 |

## 浏览器端验收（可选，但最接近真实使用）

上面几个脚本打的是 HTTP 接口；有两个脚本会用无头 Chrome **真实点击界面**，
能抓到接口测试发现不了的前端问题（例如输入框被旧值覆盖的竞态）。

先启动无头 Chrome（调试端口 9222）：

```bat
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --headless=new --remote-debugging-port=9222 --remote-allow-origins=* ^
  --user-data-dir=%TEMP%\th12-chrome --no-first-run --no-sandbox about:blank
```

然后：

```bat
node _test\cdp_pick_file.js  8799 9222 "D:\Games\th12\th12c.dat" "D:\Games\th12"
node _test\cdp_one_dir.js    8799 9222 "D:\Games\th12"
```

- `cdp_pick_file.js`：验证「选文件」这条路（填 `th12c.dat` → 自动定位目录 → 应用成功），
  以及填错文件 / 文件不存在时的提示
- `cdp_one_dir.js`：验证「填目录」这条路（粘贴目录 → 实时校验 → 应用 →
  版本下拉出现 jp/cn）

> 注意：这两个脚本都要**新开标签页**跑（脚本内部已经这么做了），
> 否则会读到上一个页面的残留状态，得出错误结论。

## 注意

- `verify_workflow.py` 只写「暂存」，最后会清空，**不会改游戏文件**。
- `verify_save.py` 会**真的写游戏文件**，请指向副本而不是你的主游戏目录；
  它自带备份并在结束时还原改动。
- 假游戏目录和 Chrome 配置等临时产物已在 `.gitignore` 里，不会入库。
