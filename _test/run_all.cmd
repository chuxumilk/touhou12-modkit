@echo off
rem Run all regression suites. ASCII only: cmd.exe reads .cmd in the OEM
rem codepage, so any non-ASCII text here would break parsing.
rem The Chinese game path is passed through an environment variable instead.
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not defined TH12_GAME_DIR set TH12_GAME_DIR=D:\Games\th12
if not defined TH12_DAMAGED_DIR set TH12_DAMAGED_DIR=%TH12_GAME_DIR%

call :run "settings"      verify_settings.py
call :run "damaged"       verify_damaged.py
call :run "workflow"      verify_workflow.py
call :run "save"          verify_save.py
echo.
echo ============ all regression suites finished ============
exit /b 0

:run
echo.
echo ########## %~1 ##########
taskkill /f /im python.exe >nul 2>&1
timeout /t 2 /nobreak >nul
echo {} > "tools\modtool\config.json"
start "" /b python tools\modtool\server.py --port 8799 --no-browser
timeout /t 9 /nobreak >nul
rem No extra args: verify_save.py reads TH12_GAME_DIR itself, which keeps
rem paths with spaces intact.
set PYTHONIOENCODING=utf-8
python _test\%~2
taskkill /f /im python.exe >nul 2>&1
exit /b 0
