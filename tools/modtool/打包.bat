@echo off
chcp 65001 >nul
title 打包 TH12 魔改工具（exe + 安装器）
cd /d "%~dp0"

echo ==================================================
echo   打包 TH12 魔改工具
echo   产出:
echo     dist\TH12ModTool\            免安装版（整个文件夹）
echo     dist-setup\TH12ModTool-Setup.exe   安装器（单文件，发给别人用）
echo ==================================================
echo.

set "ROOT=%~dp0..\.."
set "WEB=%~dp0web"
set "NAME=TH12ModTool"
set "SETUP=TH12ModTool-Setup"
set "DIST=%~dp0dist"
set "DIST2=%~dp0dist-setup"
set "BUILD=%~dp0build"
set "BUILD2=%~dp0build-setup"

echo [1/4] 打包主程序…
python -m PyInstaller --noconfirm --clean ^
  --name "%NAME%" --distpath "%DIST%" --workpath "%BUILD%" ^
  --specpath "%BUILD%" --add-data "%WEB%;web" --paths "%ROOT%" ^
  --hidden-import thtk --hidden-import thtk.anm --hidden-import thtk.archive ^
  --hidden-import thtk.bgm --hidden-import thtk.crypto --hidden-import thtk.lzss ^
  --hidden-import thtk.msg --hidden-import PIL.Image --hidden-import numpy ^
  --console "%~dp0server.py"
if errorlevel 1 goto fail

echo.
echo [2/4] 复制说明文件…
copy /y "%~dp0安装说明.txt" "%DIST%\%NAME%\" >nul

echo.
echo [3/4] 压缩程序本体 (app.zip)…
if exist "%BUILD%\app.zip" del /q "%BUILD%\app.zip"
python -c "import shutil,sys; shutil.make_archive(r'%BUILD%\app','zip',r'%DIST%\%NAME%')"
if errorlevel 1 goto fail

echo.
echo [4/4] 打包安装器…
python -m PyInstaller --noconfirm --clean --onefile ^
  --name "%SETUP%" --distpath "%DIST2%" --workpath "%BUILD2%" ^
  --specpath "%BUILD2%" --add-data "%BUILD%\app.zip;." ^
  --hidden-import tkinter --windowed "%~dp0installer.py"
if errorlevel 1 goto fail

echo.
echo ==================================================
echo   完成！
echo   安装器: %DIST2%\%SETUP%.exe
echo   免安装: %DIST%\%NAME%\%NAME%.exe
echo ==================================================
pause
exit /b 0

:fail
echo.
echo *** 打包失败，请看上面的错误信息 ***
pause
exit /b 1
