@echo off
chcp 65001 >nul
title Build YT Downloader

echo === YT Downloader — Building .exe ===
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Install/update deps
echo [1/3] Installing dependencies...
python -m pip install --upgrade pip -q
python -m pip install pyinstaller yt-dlp -q

REM Clean old build
if exist "dist\YT Downloader.exe" del "dist\YT Downloader.exe"
if exist "build" rmdir /s /q "build"
if exist "*.spec" del "*.spec"

REM Build
echo [2/3] Building executable (one-file mode)...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "YT Downloader" ^
    --noconfirm ^
    --clean ^
    --add-data "README.txt;." ^
    yt_downloader.py

echo.
echo [3/3] Done!
echo.
echo Output: dist\YT Downloader.exe
echo Size:
if exist "dist\YT Downloader.exe" (
    for %%I in ("dist\YT Downloader.exe") do echo %%~zI bytes
)

echo.
echo NOTE: ffmpeg is NOT bundled. Users need to install it separately:
echo   winget install ffmpeg
echo   or download from https://ffmpeg.org/download.html
echo.
pause
