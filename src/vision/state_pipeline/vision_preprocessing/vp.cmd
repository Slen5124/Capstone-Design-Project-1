@echo off
rem Runs the vision_preprocessing CLI with the existing project venv (no package changes).
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m vision_preprocessing.cli %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
