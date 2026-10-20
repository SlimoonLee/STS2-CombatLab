@echo off
setlocal
cd /d "%~dp0"

rem Launch the local browser UI at http://127.0.0.1:8765/
py -3 "%~dp0combat_lab_web.py" --open
if errorlevel 1 python "%~dp0combat_lab_web.py" --open
if errorlevel 1 pause
