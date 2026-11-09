@echo off
title HA Helper - simpan sandi
cd /d "%~dp0"
set PY=python
where py >nul 2>nul
if not errorlevel 1 set PY=py -3
%PY% jalankan.py --simpan-sandi
echo.
pause
