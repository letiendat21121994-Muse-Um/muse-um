@echo off
chcp 65001 >nul
title Meta Video Ads (demo)
pushd "%~dp0app" || (pause & exit /b 1)
"%~dp0runtime\node.exe" scripts\portable.mjs
if errorlevel 1 pause
