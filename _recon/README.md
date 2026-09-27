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

## 关于解包产物

`_recon/x_official/`、`_recon/src/`、`_recon/build-thtk/`、`_recon/testgame*/`
都是解包/编译出来的大文件，**已在 `.gitignore` 里，不入库**。
