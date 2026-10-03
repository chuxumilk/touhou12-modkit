# 格式逆向调研记录

这里是当初摸清 TH12 文件格式时用的探测脚本和**它们的输出记录**。
结论性的东西（THA1 归档结构、ANM 像素格式、thbgm.fmt 字段、.msg 指令流）
已经整理进 [`../tools/modtool/README.md`](../tools/modtool/README.md) 的「格式说明」一节，
本目录主要留作**证据与复现**用途。

## 文件对应关系

| 脚本 | 输出 | 内容 |
| --- | --- | --- |
| `prove_tha1.py` | `tha1_proof.txt` | 手工实现 th_crypt 解密 th12.dat 头部，验证 `THA1` 魔数与条目数（180） |
| `probe_dat.py` | `dat_header.txt` | 归档头部逐字段 dump 与边界自洽性校验 |
| `probe2.py` | `dat_probe2.txt` | 多假设探测：明文名扫描 / 熵与分布 |
| `probe_exe.py` | `exe_probe.txt` | th12.exe 的 PE 结构与关键立即数搜索 |
| `th12_bgm_engine.py` | —（直接打印） | **反汇编证明 BGM 引擎不读 thbgm.fmt 的 loop 字段**，见下节 |
| `verify1.py` / `verify2.py` | `verify1.txt` / `verify2.txt` | **对照验证**：本项目 `thtk` 与官方 thtk 的结果对比（`verify2.py` 是增量落盘版，可中断） |
| `diag.py` | `list.txt` | 用 UnRAR 列出游戏压缩包内容（排查中文路径编码问题） |
| `extract.py` / `extract_core.py` | — | 从压缩包提取 `th12.dat` / `th12.exe` 等（`extract_core.py` 用 ASCII 通配符绕开非 ASCII 路径问题） |

## 怎么跑

脚本路径都按「仓库根目录 + 相对路径」解析，不写死任何本机路径。
需要真实游戏数据时，用环境变量指过去：

```bat
set TH12_GAME_DIR=D:\Games\th12          :: 里面有 th12.dat / th12c.dat
set THDAT=D:\tools\thtk\thdat.exe        :: 官方 thdat.exe（仅 verify1/verify2 需要）
python _recon\verify2.py
```

没有 `game/` 目录时，`prove_tha1.py`、`probe_dat.py`、`probe2.py`、`probe_exe.py`
可以用 `TH12_DAT` / `TH12_EXE` 分别指定要分析的文件。

> 注意：这些脚本的输出**直接写在 `_recon/` 里**（`*.txt` 已入库），
> 重新跑一遍会让这些文件变成 dirty 状态。想保留原始记录的话，
> 跑之前先 `git stash` 或复制一份。

## thbgm.fmt 的 loop 字段在 TH12 里不起作用（重要）

用户报「改循环点没效果」时查出来的结论，推翻了「改 fmt 字段就能改循环点」的
想当然做法。证据链：

1. **写入没问题**。改循环点后重新解档，`th12.dat` / `th12c.dat` 里的
   `thbgm.fmt` 都确实带上了新值，`thbgm.dat` 与 fmt 的 begin_pos/total_len
   也首尾相接（17/17 条相接，最后一条结束位置正好等于文件大小）。
2. **游戏不读它**。`th12.exe` 的 BGM 播放器在 `0x00453940`-`0x00453AD8`：
   ```
   0x004539B5  CreateFileA(".\thbgm.dat")
   0x00453A19  eax = entry[+0x10]   → begin_pos，用来定位文件指针
   0x00453A2F  eax = entry[+0x14]   → unknown，malloc 这么多字节读进内存
   关闭时      free(buf) / Release()
   ```
   另有一处 `0x00466B70  mov eax, [ebx+0x1c]` → `total_len`，
   用来算 DirectSound 缓冲大小。
   fmt 基址 `0x004D0E6C` 在整个 exe 里只有 **9 处**引用，逐条查完都只涉及
   `+0x10` / `+0x14` / `+0x1C`；**`+0x18`（begin_len，即循环点）一次都没被读过**
   （用 `th12_bgm_engine.py` 可复现：按函数边界切出 2057 个函数做数据流跟踪，
   命中 0 处）。
3. **交叉验证（两个独立来源）**：
   - `[th10] 东方风神录` 目录里有一个独立的 `thbgm.fmt`（953 字节
     = 18×0x34 + 17 字节尾串），字段布局与 TH12 完全一致、
     18/18 条 `unknown > total_len`，说明这个格式跨作品稳定，不是我们读错了。
   - [`RUEEE/TH_BGM_Replacer`](https://github.com/RUEEE/TH_BGM_Replacer) 的
     `BGM_def.h` 把 `+0x18` 命名为 `begin_len` 并明确注释为循环点
     （`GetLoopPos() = beginPos + beginLen`），与本项目的读法一致；
     它是靠 XAudio2 的 `LoopBegin`/`LoopLength` 在**自己的播放器里**做循环，
     并不改变游戏引擎的行为。

**字段命名（以 RUEEE 的定义为准，比本工具早期的叫法准确）**：

| 偏移 | 名称 | 含义 | 引擎是否使用 |
| --- | --- | --- | --- |
| 0x10 | `begin_pos` | 数据在 thbgm.dat 里的起点 | ✅ 定位文件指针 |
| 0x14 | `unknown` | PCM 缓冲的分配大小（TH13+ 导出时写成 total_len） | ✅ malloc 大小 |
| 0x18 | `begin_len` | **循环点**（相对 begin_pos 的字节数） | ❌ **不读** |
| 0x1C | `total_len` | 轨道总长度 | ✅ 算 DirectSound 缓冲大小 |

含义：**TH12 是把整块音频读进内存后整块循环播放的**，`unknown` 一律大于
`total_len`（1.03~1.29 倍）也印证了这点。所以循环点只能靠改音频实现。

既然引擎是把整块缓冲循环播放，那只要让**缓冲内容本身**等于要反复听的那一段：

```
循环体 = 源音频[循环点 : 曲尾]
轨道内容 = [循环体][循环体]     长度 = 2 × 循环体
loop 字段 = 循环体长度
```

这样一进游戏就在循环，没有「前奏播一遍」的阶段；`loop` 写在循环体长度处
还是双保险（引擎若改成在 `loop` 处回跳，落点同样是循环体）。实现见
[`../thtk/bgm.py`](../thtk/bgm.py) 的 `splice_loop_pcm`。

> **第一版设计踩过的坑**（保留记录，免得后人再走一遍）：
> 最初把循环点理解成「引子长度」，产出 `[引子][循环体][引子]`。
> 结果循环点设得越靠后引子越长、循环感越弱 —— 用户把循环点设在
> 208.5 秒曲子的 194.5 秒处（93%），引子占 194.5 秒、循环体只剩 14 秒，
> 进游戏要等三分多钟才听得到重复，实测反馈「完全没效果」。
> 结论：**不要试图保留循环点之前的内容**，那会直接毁掉循环感。

三重取证脚本：`th12_loop_decisive.py`（本目录）。

## 关于解包产物

`_recon/x_official/`、`_recon/src/`、`_recon/build-thtk/`、`_recon/testgame*/`
都是解包/编译出来的大文件，**已在 `.gitignore` 里，不入库**。
