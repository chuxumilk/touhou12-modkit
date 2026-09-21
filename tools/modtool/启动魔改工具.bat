@echo off
chcp 65001 >nul
title 东方星莲船 魔改工具
cd /d "%~dp0"
echo 正在启动本地服务，稍后会自动打开浏览器…
echo 关闭此窗口即退出工具。
echo.
python server.py
echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
