# 东方星莲船 (TH12) 魔改工程

《东方星莲船》的魔改工具与格式库：**贴图、音乐、对话、任意归档文件**的
提取与替换，带网页界面（Web UI）。

## 目录

| 路径 | 说明 |
| --- | --- |
| `thtk/` | 纯 Python 格式库：ZUN 流密码、LZSS、THA1 归档、ANM 贴图、thbgm 音乐、.msg 对话 |
| `tools/modtool/` | 网页版魔改工具（本地 HTTP 服务 + 前端），见其 [README](tools/modtool/README.md) |
| `_recon/` | 格式逆向的调研脚本与验证记录（可删） |
| `game/` | 游戏本体（不入库） |

## 快速开始

```bat
pip install pillow numpy
双击 tools\modtool\启动魔改工具.bat
```

浏览器会自动打开 `http://127.0.0.1:8765/`，然后：

- **贴图**：选 `.anm` → 预览 → 上传 PNG 替换（即时写入）
- **音乐**：替换 WAV / 改循环点 → 点“应用修改到游戏”
- **对话**：直接编辑 `.msg` 文本并保存
- **任意文件**：`.ecl` / `.sht` / `.std` / `.rpy` 等导出、替换
- **备份 / 还原**：第一次修改前自动生成 `*.modtool.bak`

## 支持的游戏文件

- `th12.dat`（日文版）/ `th12c.dat`（汉化版）—— THA1 归档
- `thbgm.dat` + 归档内的 `thbgm.fmt` —— 18 首 BGM
- 归档内的 `.anm` 贴图、`.msg` 对话、`musiccmt.txt`

## 验证情况

- 归档解包：与官方 `thdat` 逐字节一致（日文版 180/180、汉化版 179/179）
- 贴图解码：与官方 `thanm` 导出的 PNG 逐像素一致（4 种像素格式）
- 音乐：空操作重建 `thbgm.dat` 与原文件 MD5 相同
- 对话：解析 → 重建 与原文件逐字节一致（Shift-JIS / GBK）

## 许可

工具代码为原创；格式信息参考了 [thtk (Touhou Toolkit)](https://github.com/thpatch/thtk)
的开源实现与实测验证。游戏资源版权归原作者 ZUN / 上海爱丽丝幻乐团所有。
