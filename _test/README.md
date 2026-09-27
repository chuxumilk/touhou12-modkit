# 验证脚本

用来验证「设置游戏目录」和相关功能是否正常。改完 `server.py` / `web/` 之后跑一遍，
避免又把目录设置弄坏。

## 怎么跑

先在一个终端启动工具（用测试端口，别占用 8765）：

```bat
python tools\modtool\server.py --port 8799 --no-browser
```

再在另一个终端依次运行（**每跑一个前先清空 config 并重启服务**，
否则前一个脚本留下的目录会影响后一个）：

```bat
echo {} > tools\modtool\config.json
python _test\verify_settings.py 8799    :: 设置目录：31 项
python _test\verify_damaged.py 8799     :: 数据损坏时降级：19 项
python _test\verify_workflow.py 8799    :: 真实改贴图/对话流程：15 项
```

全部 PASS 会打印「通过 N 项，失败 0 项」并以 0 退出。

## 各脚本干什么

| 脚本 | 覆盖内容 |
| --- | --- |
| `verify_settings.py` | 候选目录扫描、`/api/check` 实时校验、指到上一级自动纠正、无效路径提示、设置后各接口能否读、配置持久化、重新扫描 |
| `verify_damaged.py` | 游戏数据损坏（归档被写坏）时，界面必须仍可用：`/api/state` 不许 500，贴图/对话照常，音乐降级为提示 |
| `verify_workflow.py` | 真实流程：列贴图 → 导出 PNG → 替换（进暂存、游戏文件不动）→ 改对话文本 → 清空暂存 |
| `make_fake_game.py` | 造一份结构合法的最小假游戏目录（`_test/gameA`、`gameB`、`notagame`），没有真游戏时也能测 |

## 注意

- 脚本里的游戏目录路径写死在文件开头的常量里（`GOOD` / `DAMAGED` / `MAIN` …），
  换机器或换目录时要改。
- `verify_workflow.py` 只写「暂存」，最后会清空，**不会改游戏文件**；
  但它会读取真实归档，所以需要一份能打开的 TH12。
