@echo off
REM ==========================================
REM  Build PrintSVC for Windows 7 32-bit
REM ==========================================
REM Prerequisites:
REM   1. Python 3.8.10 32-bit installed
REM      (https://www.python.org/downloads/release/python-3810/)
REM   2. In the Python installer, ensure "Add to PATH" is checked
REM   3. Run this script from the PrintSVC-DS directory
REM ==========================================

setlocal enabledelayedexpansion

echo ==========================================
echo  PrintSVC Build Script
echo  Target: Windows 7 32-bit
echo ==========================================
echo.

REM ---- Check Python version ----
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found. Please install Python 3.8 32-bit.
    echo   Download: https://www.python.org/downloads/release/python-3810/
    pause
    exit /b 1
)

python --version 2>&1 | findstr "3.8" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Python version may not be 3.8. Win7 32-bit best with Python 3.8.
    python --version
)

REM ---- Check architecture (32-bit required) ----
python -c "import struct; exit(8 if struct.calcsize('P') != 4 else 0)" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Python must be 32-bit for Win7 32-bit compatibility.
    echo   Current Python reports 64-bit. Please install 32-bit Python.
    echo   Download: https://www.python.org/downloads/release/python-3810/
    echo   Look for: "Windows x86 executable installer"
    pause
    exit /b 1
)

echo [OK] Python is 32-bit
echo.

REM ---- Install/upgrade dependencies ----
echo [Step 1/5] Installing dependencies...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.

REM ---- Run PyInstaller ----
echo [Step 2/5] Cleaning previous build...
if exist "dist\PrintSVC" rmdir /s /q "dist\PrintSVC"
if exist "build\PrintSVC" rmdir /s /q "build\PrintSVC"
if exist "dist\PrintSVC.exe" del /q "dist\PrintSVC.exe"

echo [Step 3/5] Building executable (this may take several minutes)...
python -m PyInstaller ^
    --name PrintSVC ^
    --onefile ^
    --console ^
    --clean ^
    --noconfirm ^
    --add-data "printsvc.json;." ^
    --hidden-import printsvc ^
    --hidden-import printsvc.ipp ^
    --hidden-import printsvc.server ^
    --hidden-import printsvc.winprint ^
    --hidden-import printsvc.discovery ^
    --hidden-import printsvc.config ^
    --hidden-import printsvc.logger ^
    --hidden-import printsvc.main ^
    --hidden-import printsvc.docrender ^
    --hidden-import win32print ^
    --hidden-import win32ui ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageWin ^
    --hidden-import zeroconf ^
    --hidden-import fitz ^
    --collect-all win32print ^
    --collect-all zeroconf ^
    run.py

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PyInstaller build failed
    pause
    exit /b 1
)

echo [OK] Build completed
echo.

REM ---- Copy config and create helper scripts ----
echo [Step 4/5] Setting up distribution folder...
set DIST_DIR=dist\PrintSVC
mkdir "%DIST_DIR%" 2>nul

copy "printsvc.json" "%DIST_DIR%\printsvc.json" >nul
copy "PRINTSVC_README.txt" "%DIST_DIR%\README.txt" >nul

REM Create startup script
(
echo @echo off
echo echo ==========================================
echo echo  PrintSVC - Network Print Service
echo echo ==========================================
echo echo.
echo start /B /WAIT "" "%%~dp0PrintSVC.exe" --log-file=printsvc.log
echo echo.
echo echo Press any key to exit...
echo pause ^>nul
) > "%DIST_DIR%\start_printsvc.bat"

echo [OK] Distribution files ready
echo.

REM ---- Verify ----
echo [Step 5/5] Verifying build...
if exist "dist\PrintSVC.exe" (
    for %%F in ("dist\PrintSVC.exe") do echo [OK] Built: %%~nF%%~xF (%%~zF bytes)
) else (
    echo [ERROR] PrintSVC.exe not found in dist\ directory
    pause
    exit /b 1
)

echo.
echo ==========================================
echo  Build successful!
echo ==========================================
echo.
echo Distribution folder: %CD%\dist\PrintSVC\
echo.
echo To deploy to Win7 32-bit:
echo   1. Copy the entire dist\PrintSVC folder to the target machine
echo   2. Double-click start_printsvc.bat to run
echo   3. Open browser to http://localhost:631/ for status
echo.

pause
