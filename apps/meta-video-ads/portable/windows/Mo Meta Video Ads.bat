@echo off
chcp 65001 >nul
title Meta Video Ads (demo)
set "ROOT=%~dp0"
if "%ROOT:~0,2%"=="\\" (
  echo Khong chay duoc tu thu muc mang. Hay copy thu muc nay ra o dia cua may ^(vi du Desktop^) roi chay lai.
  pause
  exit /b 1
)
cd /d "%~dp0app" || (pause & exit /b 1)
"%~dp0runtime\node.exe" scripts\portable.mjs
if errorlevel 1 pause
