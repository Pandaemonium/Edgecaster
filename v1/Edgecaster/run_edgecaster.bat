@echo off
REM Change to the directory this .bat file lives in
cd /d "%~dp0"

REM If you use the 'py' launcher (usually best on Windows):
py -m edgecaster.main

REM Keep the window open after the game exits/crashes
echo.
echo [Press any key to close this window]
pause >nul
