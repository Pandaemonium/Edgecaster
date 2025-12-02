@echo off
cd /d "%~dp0"
cd "v1\Edgecaster"

py -m edgecaster.main
if %errorlevel% neq 0 (
    echo.
    echo The game crashed with errorlevel %errorlevel%.
    echo Press any key to close this window...
    pause >nul
)
