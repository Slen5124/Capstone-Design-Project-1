@echo off
setlocal
chcp 65001 >nul
if not "%~1"=="" goto direct
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_auto_preprocess.ps1" -Status -KeepOpen
exit /b %errorlevel%
:direct
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_auto_preprocess.ps1" -Status %*
exit /b %errorlevel%
