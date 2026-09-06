@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "BOARD_EXIT=%ERRORLEVEL%"
if not "%BOARD_EXIT%"=="0" echo Setup did not finish. Read the error above or open README.md.
pause
exit /b %BOARD_EXIT%
