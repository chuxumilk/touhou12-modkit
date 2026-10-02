# 代码审查发现（已验证）

> 由一次通读 + 两个子代理深审（格式库 / 前端）汇总。
> **本文件只记录经过实测验证的结论**。子代理报告里的主张逐条写在
> `verify_review_claims.py` 里实测；不成立的会明确标出来，避免误修。
>
> 复现工具：
> - `python _test/audit.py` —— 静态自查（DOM id / 接口对应 / 死代码 / lock）
> - `python _test/verify_review_claims.py` —— 逐条实测子代理的硬主张
> - `python _test/verify_patched_bug.py` —— 专门验证「to_bytes_patched 写坏条目」

## 验证结论一览

| 主张 | 实测结果 |
| --- | --- |
| `to_bytes_patched` 会把被替换的条目写坏 | ❌ **不成立**（见文末「被推翻的主张」） |
| `unlzss` 对截断流静默返回短数据 | ✅ 成立 → **已修** |
| `.msg` 导入把「只剩标签」的行当成改成空文本 | ✅ 成立 → **已修** |
| `.msg` 编码失败静默变 `?` 并写入 | ✅ 成立 → **已修** |
| `archive.read(-1)` 静默返回最后一条 | ✅ 成立 |
| `archive.read(越界)` 抛裸 `IndexError` | ✅ 成立 |
| 非 ASCII 条目名抛裸 `UnicodeEncodeError` | ✅ 成立 |
| `crypto._normalize` 是死代码 | ✅ 成立 |
| `crypto` `block=0` 抛 `ZeroDivisionError` | ✅ 成立 |

---

## 已修复

### ✅ `lzss.unlzss()` 静默返回短数据（提交 `7e761d8`）
- **位置**：`thtk/lzss.py`（`unlzss`）
- **问题**：契约是「恰好 `output_size` 字节」，但遇到结束标记就 `break`，
  `BitReader` 在数据读完后按 0 继续喂（这是刻意对齐 thtk 的行为），
  两者叠加 → 损坏/截断的归档被静默解出短数据，当正常内容导出再打包，错误往下游扩散。
- **实测（修复前）**：8000 字节的流截到 50% → 返回 1577 字节，**无任何异常**
- **修复后**：30% / 50% / 90% 截断全部抛 `LzssError`；
  完好数据（可压缩/随机/空/1 字节）往返一致；真实 `th12.dat` + `th12c.dat`
  共 **359 个条目全部正常解压，零误报**。
- **附带**：输出缓冲改为 `bytearray(output_size)` 预分配；匹配越界提前报错；
  新增 `LzssError`（原模块没有任何异常类型）。

### ✅ `.msg` 导入会静默清空对话（本次提交）
- **位置**：`thtk/msg.py` 的 `DOC_LINE_RE` / `import_document`
- **问题**：`\s?` 允许「标签与正文之间零个空格」，于是「`[0.6] [博丽灵梦]`」
  这种正文被删掉的行也能匹配、`new_text=""` → 判为「改成空文本」→ **静默清空该句**，
  且不计入 `unmatched`。
- **踩过的坑（记录下来避免回退）**：单纯把正文改成「必须含非空白」**没用** ——
  说话人标签是可选组，正则回溯会跳过它，把 `[博丽灵梦] ` 整个当成正文
  （实测 `groups = ('0','6',None,'[博丽灵梦] ')`）。
- **修复**：按顺序试两个**互斥**模式 ——
  `[0.1] [标签] 正文`（标签后必须空白）与 `[0.1] 正文`（用否定前瞻排除后接标签的行）；
  两者正文都要求至少一个非空白字符。
- **验证（7/7）**：只剩标签（含/不含空格）→ 报「无法识别」不再清空；
  正常改正文生效；删掉标签仍生效；未改动文档幂等；**未改动写回与原文件逐字节一致**。

### ✅ `.msg` 编码失败静默变 `?`（本次提交）
- **位置**：`thtk/msg.py` 的 `set_text` / `import_document`
- **问题**：`text.encode(encoding, "replace")` 把目标编码表示不了的字符变成 `?`
  并照常写盘；`import_document` 只把坏字符收进 `bad_chars`，**不阻止写入**。
  翻译场景（日文版写简体字）等于悄悄吃掉用户内容。
- **修复**：`set_text(strict=True)` 默认抛 `MsgError` 并指出具体字符；
  `import_document` 发现 `bad_chars` 就**整体放弃写入**并给出可操作提示。
- **验证**：往日文版正文追加「汉」（cp932 编不出）→ 抛错、文件未被改动。

---

## 待修（按优先级）

### 🔴 数据安全

**1. 备份写入不是原子的 —— 还原可能把游戏文件写坏**
- **位置**：`server.py:2350`（`restore_backup` 的 `_atomic_copy(bak, target)`）
- **问题**：`_atomic_copy` = `copyfile` + `os.replace`，**无 fsync、无自检**；
  正常保存路径（`save_patched` → `_verify_blob` + fsync）有这道防线，还原没有。
- **后果**：备份若损坏/不完整，还原会把它原样写进游戏 ——
  用户以为「还原 = 回到安全状态」，实际拿到坏文件。
- **修法**：归档走 `Archive.save()`（自带自检 + fsync）；BGM 加头部校验 + fsync。

**2. BGM 与 thbgm.fmt 分开写，可能写出不一致的组合**
- **位置**：`server.py:1011`（先 `os.replace(new_dat, BGM_DAT)`）
  与 `server.py:1066`（之后才 `a.save_patched`）
- **问题**：第二步失败就留下**新 PCM + 旧偏移表**，游戏里音乐错位。
- **讽刺点**：`server.py:1224` 的 `_apply_bgm()` **本来就是正确的两阶段提交**
  （两者都写临时文件、最后一起 replace），但它是死代码、从未被调用。
- **修法**：把 BGM 分支改成两阶段，并删掉 `_apply_bgm` 避免两处实现漂移。

**3. 暂存文件泄漏（此前 55 MB 残留的根因）**
- **位置**：`server.py:911-916`
- **问题**：同一条目改第二次时，写新 `pending_NNNN.bin`，旧文件被移出
  `STATE.pending` 却**没删**，从此无引用，`clear_pending()` 也遍历不到。
- **修法**：替换时 `os.remove(old["staged"])`；启动时清理孤儿分片。

### 🟠 功能

**4. 启动时「记住目录」与「实际使用目录」不一致**
- **位置**：`server.py:2685`
- **问题**：`if check_game_dir(saved) or find_game_subdir(saved)` 允许存的是
  「上一层」，但 `set_game_dir(saved)` **没有上浮**。
- **实测**：`D:\...\东方魔改 安东星莲船\game` → 判断放行、
  `find_game_subdir` 找到真游戏目录，但 `GAME_DIR` 仍是 `game\` 那一层 →
  `available_games()` 为空。表现：启动后顶部显示了目录，但版本下拉是空的。
- **修法**：命中时 `set_game_dir(resolved)`。

### 🟡 健壮性 / 一致性（子代理报告，部分已验证）

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
| 5 处直接改 `STATE` 缓存未加锁 | `server.py` 915/968/1069/1070/2109 | 非高危（CPython 下原子），但写法不一致 |

### 前端（子代理报告，摘录高优先级）

- **只改「循环点」后无法保存**：`/api/bgm` 的 `pending_count` 只算替换曲目、
  不含 `bgm_loop`，而 `setBgmLoop()` 不调 `loadPending()` →
  两个按钮都 disabled、底部条不出现，必须刷新页面才能存。
- **保存过程中可关掉对话框**，且无并发保护 → 可能同时跑两个 `/api/save`
  （固定临时名 `BGM_DAT+".new"`、`dst+".tmp"` 会相互踩）。
- **对话文档导入硬按 UTF-8 解码**：`File.text()` 是硬性 UTF-8，
  GBK 保存的译文在进后端前就变成 U+FFFD（后端本来用 `utf-8-sig`，
  但导出侧从不写 BOM，容错形同虚设）—— 主推的翻译工作流会被毁。
- `boot()` 里任一接口失败会中止后续初始化，并用笼统的「初始化失败」
  盖掉具体的音乐数据提示。

---

## 被推翻的主张（不要按它改）

### ❌ `to_bytes_patched()` 会把被替换的条目写坏
- **主张**：`server.py:1248` 保存 BGM 时把 `thbgm.fmt` 写成压缩流，
  游戏读到压缩字节，归档被写坏。
- **为什么看似成立**：`read()` 用 `zsize == size` 判「未压缩」，
  而 patch 会把替换条目的 `size` 设成新数据长度 —— 看起来生产端与消费端语义相反。
- **实测否证**（`verify_patched_bug.py`）：
  1. 用一份**完好**的 `th12.dat`，把压缩存储的条目（`ascii.anm`）原样替换后写回，
     重新载入：`size`/`zsize` 自洽，**读回数据与原数据一致**。
  2. 专门针对 `thbgm.fmt`（真实 `size=953 zsize=461`）：写回后
     `size=953 zsize=953`，读回 953 字节，**与原始数据逐字节一致**。
  3. 写回后的归档 180 个条目全部可读。
- **真正的原因**：`lzss.compress_if_smaller()` 返回的 `size` **始终是原始长度**
  （`(data, size, size)` 或 `(z, size, len(z))`），所以 `compress=False` 时
  `size == zsize == len(data)` 且数据未压缩，`read()` 直接返回 —— 两端自洽。
- **副作用（非 bug）**：替换条目时默认不压缩，所以归档会比原来大
  （未压缩替换一个压缩条目）。这是**刻意**的取舍（纯 Python LZSS 压缩很慢），
  不是数据损坏。

### ⚠️ 降级为「性能问题」的观察
子代理报告里提到「`to_bytes()` 会重压每一条，179KB 约 1.9 秒，
84MB 全量不可接受」—— 这是**真的**，也是 `to_bytes_patched` 存在的理由。
我实测 `Archive.save()` 处理一份 29.9 MB 的归档**超过 5 分钟**（超时未完成），
所以：**还原路径不要无脑改用 `Archive.save()`**，
那会让"还原"变成一个需要等好几分钟的操作。还原应当用
「校验 + 原样拷贝 + fsync」，而不是重新序列化。
