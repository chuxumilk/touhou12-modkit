# 代码审查发现（待修）

> 由一次通读 + 两个子代理深审（格式库 / 前端）汇总。
> 每条都标了**验证状态**：查实 = 我实际跑过或读到确切代码；待验证 = 需要进一步确认。

## 🔴 数据安全

### 1. 备份写入不是原子的 —— 还原可能把游戏文件写坏
- **位置**：`tools/modtool/server.py:2350`（`restore_backup` 里的 `_atomic_copy(bak, target)`）
- **问题**：`_atomic_copy` 是 `copyfile` + `os.replace`，**没有 fsync、没有写完自检**。
  正常保存路径（`Archive.save_patched` → `_verify_blob` + fsync）有这道防线，还原没有。
- **后果**：备份文件若损坏或不完整（上次拷贝被中断、磁盘问题），还原会把它**原样写进游戏**，
  用户以为"还原 = 回到安全状态"，实际拿到一个坏文件。
- **建议**：还原统一走 `Archive.save`（它自带 `_verify_blob` + fsync）；
  BGM 的 `thbgm.dat` 还原也加上大小/头部校验。
- **验证状态**：查实（读到代码 + 已知 `_atomic_copy` 实现）。

### 2. BGM 与归档分开写，可能写出不一致的组合
- **位置**：`server.py:1011`（`os.replace(new_dat, BGM_DAT)`）在 `server.py:1066`（`a.save_patched`）之前
- **问题**：先替换 `thbgm.dat`，之后才写归档里的 `thbgm.fmt`。第二步失败（磁盘满/进程被杀/占用）
  就留下**新 PCM + 旧偏移表**。
- **讽刺点**：`server.py:1224` 的 `_apply_bgm()` **本来就是正确写法**
  （两者都写临时文件，最后一起 `os.replace`），但它是死代码，从未被调用。
- **建议**：把 `save_all` 的 BGM 分支改成 `_apply_bgm` 的两阶段提交（先全部写临时文件 → 校验 → 再统一替换）。
- **验证状态**：查实。

### 3. 暂存文件泄漏（此前 55 MB 残留的根因）
- **位置**：`server.py:911-916`
- **问题**：同一条目被改第二次时，写入新 `pending_NNNN.bin`，旧暂存文件被从 `STATE.pending` 剔除
  但**没有删除**。该文件从此无任何引用，`clear_pending()` 也遍历不到。
- **建议**：替换时 `os.remove(old["staged"])`（吞 OSError）；另加一个启动时清理
  「不在 pending 列表里的 pending_*.bin」。
- **验证状态**：查实（代码逻辑 + 之前实测到 24 个孤儿分片）。

## 🟠 功能错误

### 4. 启动时"记住目录"与"实际使用目录"不一致
- **位置**：`server.py:2685`
- **问题**：
  ```python
  if check_game_dir(saved) or find_game_subdir(saved):   # 允许存的是"上一层"
      set_game_dir(saved)                                # 但没有上浮！
  ```
- **实测**（`D:\...\东方魔改 安东星莲船\game`）：
  ```
  check_game_dir(d)   = []          → 不是游戏目录
  find_game_subdir(d) = ...\game\[th12] 东方星莲船 (汉化版+日文版)   → 找到了
  判断放行 → set_game_dir(d) → GAME_DIR 仍是 game\ 那一层
  → available_games() 为空
  ```
- **后果**：启动后顶部显示记住了目录，但版本下拉为空、所有页签都是空的。
- **建议**：判断改为 `resolved = check_game_dir(p) or find_game_subdir(p)`，
  命中时 `set_game_dir(resolved)`。
- **验证状态**：查实（已实测复现）。

## 🟡 死代码 / 不一致

| 项目 | 位置 | 说明 |
| --- | --- | --- |
| `_apply_bgm()` 从未被调用 | `server.py:1224` | 它才是正确的两阶段写入（见问题 2） |
| `State.new_job()` 从未被调用 | `server.py:874` | 作业机制已由 `PROGRESS` 取代 |
| `/api/job` 永远返回 404 | `server.py:2492` | 依赖 `STATE.jobs`，而它永远是空的 |
| `/api/anms` 前端从不调用 | `server.py` GET 路由 | 已被 `/api/archive` 取代 |
| `/api/bgm.apply` 前端从不调用 | `server.py` POST 路由 | 音乐已并入统一保存 |
| `State.progress` | `server.py:1235` 附近 | 嵌套在死函数里 |
| 5 处直接改 `STATE` 缓存未加锁 | 915 / 968 / 1069 / 1070 / 2109 | `with STATE.lock` 包裹即可 |
| 27 处 `except: pass` 静默吞异常 | 全文 | 多数是 OS 层保护（`listdir` 失败等），
但 `server.py:2348`（保留 `.prev` 失败仍继续还原）会掩盖真问题 |

## 🟢 前端自查（`_test/audit.py`）

- JS 引用的 67 个 DOM id **全部存在** ✓
- 前后端接口**完全对应**，前端没有调用不存在的接口 ✓
- `innerHTML` 拼接 4 处（`app.js:394/449/712/1102`）：拼接的是归档条目名 / 时间戳，
  来自游戏文件，理论上含 `<` `&` 会出问题 —— **风险低**，但可以用 `textContent` 更稳
- `createObjectURL` 0 处（无需 revoke，无泄漏）
- `setInterval`/`setTimeout` 与对应的 clear **配对** ✓

## 建议的修复顺序

1. **问题 1**（还原原子的）—— 唯一可能让用户"越还原越坏"的路径
2. **问题 2**（BGM 两阶段写入）—— 顺手把 `_apply_bgm` 复活，同时消掉死代码
3. **问题 3**（暂存泄漏）—— 简单、一劳永逸
4. **问题 4**（启动目录）—— 一行逻辑，消除一个困惑用户很久的现象
5. 死代码清理 + 线程锁 —— 收尾

每步改完都重跑 `_test/run_all.cmd`（四套回归）+ 两个浏览器脚本。
