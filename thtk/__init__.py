# -*- coding: utf-8 -*-
"""thtk 风格的工具包公共层。

本包复刻 thtk (Touhou Toolkit) 的分层设计：
    thcrypt  -> crypto.py    流密码（加密 / 解密）
    thlzss   -> lzss.py      ZUN 自定义 LZSS
    thdat    -> archive.py   .dat 归档容器
    thanm    -> anm.py       .anm 贴图
    thbgm    -> bgm.py       thbgm.dat / thbgm.fmt
    thmsg    -> msg.py       .msg 对话
"""

__version__ = "0.2.0"
__all__ = ["crypto", "lzss", "archive", "anm", "bgm", "msg"]
