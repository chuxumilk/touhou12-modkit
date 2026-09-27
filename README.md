# 东方星莲船 (TH12) 魔改工程

《东方星莲船》的**格式库 + 网页版魔改工具**：把 `th12.dat` / `th12c.dat` 里的
**贴图、音乐、对话、音乐室评论以及任意归档文件**提取出来，改完再写回去。

纯 Python 实现，带一个本地 Web UI（`http://127.0.0.1:8765/`），
日文版（`th12.dat`）与汉化版（`th12c.dat`）都支持，改动前自动备份。

Python 3.7+ · Windows · [MIT 许可](LICENSE)

> ⚠️ 本仓库**不含游戏本体**。请自备一份正版《东方星莲船》。

---

## 功能一览

| 模块 | 能做什么 |
| --- | --- |
| **贴图**（`.anm`） | 浏览归档里所有 ANM，**实时预览**每张贴图；支持**按贴图名全局搜索**（如 `etama`、`face02no`）；PNG 导出 / 替换，按原格式（BGRA8888 / RGB565 / A4R4G4B4 / GRAY8）重新编码；可整文件一键导出 ZIP |
| **音乐**（`thbgm.dat`） | 18 首 BGM 列表、在线试听、导出 WAV、上传替换（自动转 16bit/立体声/44100Hz）、设置循环点，最后重建 `thbgm.dat` |
| **对话**（`.msg`） | 按条目展示文本并**标出说话人**（自机 / 敌机 Boss / 旁白），网页内直接编辑；可导出 UTF-8 文档交给翻译，再导入回来并**逐条预览改动**；日文版按 Shift-JIS、汉化版按 GBK |
| **批量导入** | 选一个文件夹，按文件名自动识别：`face02no.png`（按贴图名匹配）、`ascii.anm_0.png`、`etama@bullet@0.png`、`st01_00a.txt`、`stage01.ecl`、`musiccmt.txt`…… 合成图会按各条目的 x/y 自动切分回填 |
| **任意文件** | `.ecl` / `.sht` / `.std` / `.rpy` 等归档内所有文件均可导出备份、原样替换 |
| **音乐室评论** | 直接编辑归档内的 `musiccmt.txt` |
| **日志** | 所有写操作记进网页「日志」页与 `logs/modtool.log`（JSON Lines） |
| **备份 / 还原** | 首次修改前自动生成 `*.modtool.bak`；还原前把当前文件另存为 `*.modtool.prev`；支持单个还原、下载备份、全部还原 |

**工作流是「先暂存、后保存」**：贴图 / 音乐 / 对话 / 文件的改动都只进暂存区，
页面底部出现「有 N 项修改待保存」后，点 **「保存到游戏…」**（可填备注）才真正写盘。

---

## 目录结构

```
东方魔改/
├─ thtk/                    # 纯 Python 格式库
│   ├─ crypto.py            #   ZUN 流密码（加解密）
│   ├─ lzss.py              #   ZUN 自定义 LZSS 压缩
│   ├─ archive.py           #   THA1 归档（th12.dat / th12c.dat）读写
│   ├─ anm.py               #   ANM 贴图解析 / 替换
│   ├─ bgm.py               #   thbgm.dat + thbgm.fmt
│   └─ msg.py               #   .msg 对话
├─ tools/modtool/
│   ├─ server.py            #   Web 后端（本地 HTTP 服务，无第三方 Web 框架）
│   ├─ web/                 #   前端（原生 HTML / CSS / JS）
│   ├─ installer.py         #   安装器（PyInstaller 打成单文件 Setup.exe）
│   ├─ 打包.bat             #   一键打包（exe + 安装器）
│   ├─ 安装说明.txt
│   └─ README.md            #   工具的完整使用说明
├─ _recon/                  # 格式逆向调研脚本与验证记录（可删）
└─ game/                    # 游戏本体，不入库
```

---

## 快速开始

### 方式一：直接用源码（推荐）

需要 **Python 3.7+** 和两个依赖：

```bat
pip install pillow numpy
```

然后双击 `tools\modtool\启动魔改工具.bat`
（也可以 `python tools\modtool\server.py`），浏览器会自动打开 <http://127.0.0.1:8765/>。

### 方式二：用打包好的 exe

`tools\modtool\打包.bat` 会生成：

- `dist\TH12ModTool\TH12ModTool.exe` —— 免安装版（整个文件夹一起拷走）
- `dist-setup\TH12ModTool-Setup.exe` —— 单文件安装器（不需要管理员权限，自带到 `%LOCALAPPDATA%`）

需要额外装 `pyinstaller`；打包产物已在 `.gitignore` 中，不会进仓库。

### 指定游戏目录

工具默认找 `game/[th12] 东方星莲船 (汉化版+日文版)/`，也支持任意目录，四种方式任选：

1. 网页右上角 **⚙ 设置目录** → 填路径或点「浏览…」选文件夹（记进 `tools/modtool/config.json`）
2. 把游戏文件夹**直接拖到 `启动魔改工具.bat`** 上
3. 命令行：`python tools\modtool\server.py --game-dir "D:\我的游戏\th12"`
4. 把游戏文件放进默认目录

游戏目录里至少要有 `th12.dat`（日文版）**或** `th12c.dat`（汉化版）；
想改音乐还需要 `thbgm.dat`（约 400MB）；放上 `th12.exe` / `th12c.exe`
就能在网页里点「▶ 启动游戏」。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--port 8765` | 指定端口 |
| `--no-browser` | 不自动打开浏览器 |
| `--game-dir "路径"` | 指定游戏目录 |

> 修改期间请**先关闭游戏**，否则文件被占用会写入失败（网页会提示）。

---

## 支持的文件

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `th12.dat` / `th12c.dat` | 游戏目录 | THA1 归档，含约 180 个条目（ANM / MSG / ECL / SHT / STD / thbgm.fmt …） |
| `thbgm.dat` | 游戏目录 | `ZWAV` 头 + 连续 16bit 立体声 PCM，18 首 BGM |
| `thbgm.fmt` | 归档内 | 每条 0x34 字节，记录 offset / 预读 / 循环点 / 长度 |
| `*.anm` | 归档内 | TH12 用 `anm_header11_t`（version=7），贴图数据在 `THTX` 块 |
| `*.msg` | 归档内 | 对话文本，type 17 指令用滚动密钥 XOR 混淆 |
| `musiccmt.txt` | 归档内 | 音乐室评论 |

> 工具的参数是按 **TH12** 写的（加密参数、`thbgm.fmt` 结构等），
> 换成 th11 / th13 等其他作品的 `.dat` 无法解析。

---

## 验证情况

`_recon/` 里保留了对照官方 [thtk (Touhou Toolkit)](https://github.com/thpatch/thtk)
的验证脚本与记录：

- **归档解包**：与官方 `thdat` 逐字节一致（日文版 180/180、汉化版 179/179）
- **贴图解码**：与官方 `thanm` 导出的 PNG 逐像素一致（4 种像素格式）
- **音乐**：空操作重建 `thbgm.dat` 与原文件 MD5 相同
- **对话**：解析 → 重建与原文件逐字节一致（Shift-JIS / GBK）

开发环境：Windows 10/11 + Python 3.9（未使用 3.7 之后的语法特性，3.7+ 理论上均可）。

---

## 已知限制

- 音乐只支持**未压缩 PCM WAV** 上传，其他格式请先转换
- 贴图改尺寸后，引用它的精灵显示位置可能需要另行调整
- 重建 `thbgm.dat` 需要约 1 倍文件大小的临时空间，写入需要几秒到几十秒
- 弹幕脚本（`.ecl`）只支持原样替换，没有可视化编辑
- Web UI 面向本机使用（监听 `127.0.0.1`），未做多用户 / 鉴权

---

## 免责声明与许可

- 本项目的**工具代码为原创**，以 [MIT 许可](LICENSE) 发布；格式信息参考了
  [thtk (Touhou Toolkit)](https://github.com/thpatch/thtk) 的开源实现，并逐项实测验证。
- **MIT 许可只覆盖工具代码**。**游戏本体与其资源（贴图、音乐、文本等）版权归原作者
  ZUN / 上海爱丽丝幻乐团所有**，不在本许可范围内；本仓库不包含、也不分发任何游戏资源，
  请使用自己合法持有的游戏文件。
- 请勿将本工具用于传播游戏本体。

---

## 相关文档

- [`tools/modtool/README.md`](tools/modtool/README.md) —— 工具完整说明（含各文件格式的结构笔记）
- [`tools/modtool/安装说明.txt`](tools/modtool/安装说明.txt) —— 安装器用户的使用说明
