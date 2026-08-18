@echo off
cd /d "%~dp0"
if exist "dist\HireMS.exe" (
  start "HireMS" "dist\HireMS.exe"
) else (
  python launcher.py
)
