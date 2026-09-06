@echo off
setlocal
set "BOARD_UV="
where uv.exe >nul 2>nul
if not errorlevel 1 set "BOARD_UV=uv.exe"
if not defined BOARD_UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "BOARD_UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined BOARD_UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "BOARD_UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined BOARD_UV (
  echo.
  echo First-time setup is needed. Double-click install.bat, then start.bat.
  echo See README.md for help.
  pause
  exit /b 1
)
echo.
echo Opening unfertig...
echo The first start may download Python. Future starts work offline.
"%BOARD_UV%" run --no-project --python 3.12 --script "%~dp0server.py" %*
set "BOARD_EXIT=%ERRORLEVEL%"
if not "%BOARD_EXIT%"=="0" (
  echo.
  echo Could not start. Read the message above or open README.md.
  pause
)
exit /b %BOARD_EXIT%
