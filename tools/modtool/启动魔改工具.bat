@echo off
chcp 65001 >nul
title 东方星莲船 魔改工具
cd /d "%~dp0"

set "GAMEDIR=%~1"
if "%GAMEDIR%"=="" goto default
echo 使用指定的游戏目录：
echo   %GAMEDIR%
echo.
python server.py --game-dir "%GAMEDIR%"
goto end

:default
python server.py

:end
echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
