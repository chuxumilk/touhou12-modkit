# 代码审查发现（已验证）

> 由一次通读 + 两个子代理深审（格式库 / 前端）汇总。
> **本文件只记录经过实测验证的结论**。子代理报告里的主张逐条写在
> `verify_review_claims.py` 里实测；不成立的会明确标出来，避免误修。
>
> 复现工具：
> - `python _test/audit.py` —— 静态自查（DOM id / 接口对应 / 死代码 / lock）
> - `python _test/verify_review_claims.py` —— 逐条实测子代理的硬主张
> - `python _test/verify_patched_bug.py` —— 专门验证「to_bytes_patched 写坏条目」
> - `python _test/verify_restore.py` —— 还原安全性（坏备份被拒 / 原子写入）
> - `python _test/diagnose_th12c.py` —— 判断归档是「数据流坏了」还是「条目表长度对不上」

## 验证结论一览

| 主张 | 实测结果 |
| --- | --- |
| `to_bytes_patched` 会把被替换的条目写坏 | ❌ **不成立**（见文末「被推翻的主张」） |
| `unlzss` 对截断流静默返回短数据 | ✅ 成立 → **已修**（库严格 / 归档宽容） |
| `.msg` 导入把「只剩标签」的行当成改成空文本 | ✅ 成立 → **已修** |
| `.msg` 编码失败静默变 `?` 并写入 | ✅ 成立 → **已修** |
| 还原不校验备份、写入无 fsync | ✅ 成立 → **已修** |
| BGM 与 thbgm.fmt 分开写，可能不一致 | ✅ 成立 → **已修**（两阶段提交） |
| 暂存文件泄漏（55 MB 残留的根因） | ✅ 成立 → **已修** |
| 启动时「记住目录」与「实际目录」不一致 | ✅ 成立 → **已修** |
| `_apply_bgm` / `new_job` / `/api/job` 是死代码 | ✅ 成立 → **已删** |
| `archive.read(-1)` 静默返回最后一条 | ✅ 成立（未修） |
| `archive.read(越界)` 抛裸 `IndexError` | ✅ 成立（未修） |
| 非 ASCII 条目名抛裸 `UnicodeEncodeError` | ✅ 成立（未修） |
| `crypto._normalize` 是死代码 | ✅ 成立（未修） |
| `crypto` `block=0` 抛 `ZeroDivisionError` | ✅ 成立（未修） |

---

## 已修复

### ✅ `lzss.unlzss()` 静默返回短数据（提交 `7e761d8`，后续调整）
- **问题**：契约是「恰好 `output_size` 字节」，但遇到结束标记就 `break`，
  `BitReader` 在数据读完后按 0 继续喂（刻意对齐 thtk），两者叠加 →
  损坏/截断的归档被静默解出短数据，当正常内容导出再打包，错误往下游扩散。
- **实测（修复前）**：8000 字节的流截到 50% → 返回 1577 字节，**无任何异常**
- **踩过两个方向的坑**（重要，避免回退）：
  1. 一开始加了「匹配长度不得越过目标大小」→ **过严**。真实归档里就有这种数据
     （`th12c.dat` 的 `th12_0100b.ver`：目标 99 字节、最后一段匹配还要再写 15），
     thtk 的 `while written < output_size` 本来就是自然截断它。已改为「超出丢弃」。
  2. 但「解出长度不足」这条必须保留 —— 那是真正的截断信号。
- **最终分工**：`unlzss()` 默认严格（报错），
  归档读取路径用 `allow_partial=True` 尽力而为，并把不一致记进
  `Archive.partial_entries`。

### ✅ `.msg` 导入会静默清空对话
- **问题**：行正则 `\s?` 允许「标签与正文之间零个空格」、正文 `(.*)` 允许为空，
  于是「`[0.6] [博丽灵梦]`」（正文被删掉）被当成「改成空文本」→ **静默清空该句**，
  且不计入 `unmatched`。
- **踩过的坑**：光把正文改成「必须含非空白」**没用** —— 说话人标签是可选组，
  正则回溯会跳过它，把 `[博丽灵梦] ` 整个当成正文
  （实测 `groups = ('0','6',None,'[博丽灵梦] ')`）。
- **修复**：按顺序试两个**互斥**模式 ——
  `[0.1] [标签] 正文`（标签后必须空白）与 `[0.1] 正文`（否定前瞻排除后接标签的行）。
- **验证（7/7）**：只剩标签 → 报「无法识别」；正常编辑生效；删掉标签仍生效；
  未改动幂等；**未改动写回与原文件逐字节一致**。

### ✅ `.msg` 编码失败静默变 `?`
- **问题**：`set_text` 用 `encode(encoding, "replace")` 把编不出的字符变成 `?`
  并照常写盘；`import_document` 只收集 `bad_chars`、不阻止写入。
- **修复**：`set_text(strict=True)` 默认抛 `MsgError` 并指出具体字符；
  `import_document` 一旦发现 `bad_chars` 就**整体放弃写入**。
- **验证**：往日文版正文追加「汉」（cp932 编不出）→ 抛错、文件未被改动。

### ✅ 还原可能把坏备份写进游戏（提交 `53642a1`）
- **问题**：`restore_backup` 用 `_atomic_copy`（`copyfile` + `os.replace`），
  既无 fsync、也不检查备份本身是否完好。
- **修复**：
  1. `_backup_problem()`：还原前体检 —— 归档要能解析、条目位置在文件范围内、
     抽查最小几条能解出；BGM 要求文件头是 `ZWAV` 且不只是头。
  2. `_durable_copy()`：写临时文件 → fsync → `os.replace`。
  3. 「保存 .prev 失败」不再静默忽略（以前会把用户当前改动无声丢掉）。
- **注意**：**没有**改用 `Archive.save()`。它虽自带自检，但我实测处理一份
  29.9 MB 的归档要 5 分钟以上（纯 Python 逐条重压），
  会让「还原」变成没法等的操作。还原的正确做法是「校验原文件 + 原样拷贝 + fsync」。
- **验证（18/18）**：空/截断/垃圾备份全部被拒且游戏文件未动；
  好备份还原成功、`.prev` 保存的是还原前的坏文件；
  硬链接场景下目标正确还原、另一头保持旧数据（`os.replace` 会断开链接，属预期）。

### ✅ BGM 与 thbgm.fmt 两阶段提交（提交 `5e16420`）
- **问题**：原来先 `os.replace` 掉 `thbgm.dat`，之后才写归档里的 `thbgm.fmt`。
  第二步失败就留下【新音乐 + 旧偏移表】，游戏里音乐错位。
- **修复**：所有内容先写成临时文件并 fsync，全部成功后才统一替换；
  失败则清掉临时文件，游戏文件一个都没动。
- **顺带**：原来的死代码 `_apply_bgm` 就是正确写法，已内联进 `save_all` 并删除
  （-56 行），消除两处实现漂移的隐患。

### ✅ 换目录/保存后内存缓存没清干净（自己引入又自己抓到）
- 重构时只清了 `STATE.anm_cache`，漏了 `STATE.archives` ——
  缓存里的 `Archive` 对象还持有替换前的条目表，于是
  「保存成功、磁盘也对，但接口读回旧内容」。
- 实测复现：磁盘上含测试行 `True` / 接口读回 `False`。
  修复后内存与磁盘一致，并把 `STATE.fmt` 一起失效。

### ✅ 暂存文件泄漏（55 MB 残留的根因）
- **问题**：同一条目改第二次时写入新 `pending_NNNN.bin`，旧文件被移出
  `STATE.pending` 却**没删**，从此无任何引用，`clear_pending()` 也遍历不到。
- **修复**：剔除旧条目时删掉它的暂存文件；新增 `clean_staging_orphans()`，
  启动时清掉遗留的 `pending_*.bin` / `track_*.pcm`。

### ✅ 启动时「记住目录」与「实际使用目录」不一致
- **问题**：`main()` 里 `if check_game_dir(saved) or find_game_subdir(saved)`
  放行了「装着游戏的那一层」，但 `set_game_dir(saved)` 没有上浮。
- **实测（修复前）**：`...\东方魔改 安东星莲船\game` → 判断放行、
  `find_game_subdir` 找到真游戏目录，但 `GAME_DIR` 仍是 `game\` 那一层。
- **修复**：改用 `resolve_game_input` 并显式上浮，启动时打印改用了哪个目录。

### ✅ 死代码清理
- `_apply_bgm()`（56 行，从未被调用）
- `State.new_job()` + `self.jobs` / `self.job_seq` + GET `/api/job`
  （作业机制已被 `PROGRESS` 取代，`STATE.jobs` 永远是空的）
- 保留 `/api/anms`、`/api/bgm.apply`：前端不用了，但属于对外接口，留着兼容
- `audit.py` 的 ⑨ 项现在会追踪 `with STATE.lock:` 的缩进作用域 → 报告 0 处

---

### ✅ 前端：「循环点存不下去」+ 译文编码 + boot 容错（提交 `db14524`）
- **只改循环点无法保存**：`/api/bgm` 的 `pending_count` 只统计替换曲目、
  不含 `bgm_loop`，而按钮状态读它、底部条读 `/api/pending` ——
  两个数据源不一致。只设循环点时两个按钮都 disabled、底部条不刷新，
  界面上没有任何保存入口，必须刷新页面。修法：`setBgmLoop()` 补
  `loadPending()`；新增 `applyBgmButtons()` 统一以 `/api/pending` 为准。
- **译文编码**：导出对话文档加 UTF-8 BOM（编辑器靠它判断编码，
  否则「另存为 ANSI」会写成 GBK）；前端新增 `sniffDocEncoding()`
  在导入前探测编码 —— `File.text()` 是硬性 UTF-8，GBK 字节在浏览器里
  就变成 U+FFFD，后端再容错也救不回来。判为 GBK/UTF-16 时提示并默认不继续。
- **`pickFile()` 取消对话框时永不 resolve** → 现在 focus 兜底，取消也 resolve。
- **`boot()` 容错**：新增 `refreshAll()`，四个刷新各自 try/catch，
  任一失败不再中断其它页签，也不会用笼统的「初始化失败」盖掉具体提示。
- 新增 `_test/cdp_encoding.js`（真实浏览器，13 项）。

---

## 待修

### 🟠 前端（子代理报告，剩余）

- **保存过程中可关掉对话框**，且无并发保护 → 可能同时跑两个 `/api/save`。
  加 `S.saving` 在途标志 + 在途禁用取消与遮罩关闭。
- 搜索结果的过期响应会覆盖界面（加请求序号丢弃过期响应）
- `#btn-export-zip` 在搜索态会导出别的 ANM（`showSearchResult` 没设 `S.exportAnm`）
- 单例 toast 互相覆盖：同一 tick 连发多条只剩最后一条
- 多处 `api()` 没有 catch → 失败静默无提示
- 进度轮询泄漏：`startProgressWatch()` 在 try 内、`stopProgressWatch()` 只在成功路径

### 🟡 健壮性（子代理报告，部分已验证）

| 项目 | 位置 | 状态 |
| --- | --- | --- |
| `read(-1)` 静默返回最后一条 | `archive.py:226` | 已验证 |
| `read(越界)` 抛裸 `IndexError` | 同上 | 已验证 |
| 非 ASCII 条目名抛裸 `UnicodeEncodeError` | `archive.py:85` | 已验证 |
| `crypto._normalize` 死代码 | `crypto.py:31` | 已验证 |
| `crypto` `block=0` → `ZeroDivisionError` | `crypto.py:63` | 已验证 |
| `msg` 的 `extra` 有无符号不一致 → 裸 `struct.error` | `msg.py:274/307` | 待验证 |
| `anm.replace_texture` 不校验数据长度 | `anm.py:263` | 待验证 |
| `bgm.read_data_header` 不校验魔数 | `bgm.py:140` | 待验证 |
| `audioop` 已从 CPython 3.13 移除 | `bgm.py` | 待验证（当前环境 3.9） |
| 4 个异常类无共同基类 | `thtk/__init__.py` | 设计问题 |

### 测试脚本自身的问题（子代理报告）

- `run_all.cmd` 的退出码永远是 0（不检查 `%ERRORLEVEL%`），
  且 `taskkill /f /im python.exe` 会杀掉机器上**所有** Python 进程
- `cdp_one_dir.js` 没有任何断言，永远 exit 0
- `verify_workflow.py` 把「归档 180 条」这种本地环境的数字写死当断言
- `_test/README.md` 里各套件的项数与实际不符

---

## 被推翻的主张（不要按它改）

### ❌ `to_bytes_patched()` 会把被替换的条目写坏
- **主张**：`server.py` 保存 BGM 时把 `thbgm.fmt` 写成压缩流，
  游戏读到压缩字节，归档被写坏。
- **为什么看似成立**：`read()` 用 `zsize == size` 判「未压缩」，
  而 patch 会把替换条目的 `size` 设成新数据长度 —— 看似生产端与消费端语义相反。
- **实测否证**（`verify_patched_bug.py`）：
  1. 用一份**完好**的 `th12.dat`，把压缩存储的条目原样替换后写回，
     重新载入：`size`/`zsize` 自洽，**读回数据与原数据一致**。
  2. 专门针对 `thbgm.fmt`（真实 `size=953 zsize=461`）：写回后
     `size=953 zsize=953`，读回 953 字节，**与原始数据逐字节一致**。
  3. 写回后的归档 180 个条目全部可读。
- **真正的原因**：`lzss.compress_if_smaller()` 返回的 `size` **始终是原始长度**
  （`(data, size, size)` 或 `(z, size, len(z))`），所以 `compress=False` 时
  `size == zsize == len(data)` 且数据未压缩，`read()` 直接返回 —— 两端自洽。
- **副作用（非 bug）**：替换条目时默认不压缩，所以归档会比原来大
  （未压缩替换一个压缩条目）。这是**刻意**的取舍（纯 Python LZSS 压缩很慢）。

### ⚠️ 关于 `Archive.save()` 的性能
子代理提到「`to_bytes()` 会重压每一条，179KB 约 1.9 秒，84MB 全量不可接受」——
这是**真的**，也是 `to_bytes_patched` 存在的理由。我实测 `Archive.save()`
处理一份 29.9 MB 的归档**超过 5 分钟**（超时未完成）。
所以：**还原路径不要无脑改用 `Archive.save()`**。

### ⚠️ 关于 `th12c.dat` 的「长度不符」
实测该归档 179 条中有 48 条解出偏短、59 条偏长，但**解压从不抛错** →
是**条目表 `size` 字段与数据流不一致**，不是数据流损坏。
官方 `thdat` 同样「解出多少算多少」。所以不应因此拒绝整份归档，
只在界面上提示「这些文件导出后可能不完整」。
用 `diagnose_th12c.py` 可以自行复现这个判断。
