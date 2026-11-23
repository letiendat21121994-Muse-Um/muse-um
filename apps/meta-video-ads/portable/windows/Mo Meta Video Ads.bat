@echo off
chcp 65001 >nul
title Meta Video Ads (demo)
cd /d "%~dp0app"
"%~dp0runtime\node.exe" scripts\portable.mjs
if errorlevel 1 pause
