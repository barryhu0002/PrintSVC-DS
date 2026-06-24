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

REM Allow overriding the Python interpreter when PATH order is wrong.
if defined PYTHON_EXE (
    set "PYTHON_CMD=%PYTHON_EXE%"
) else if exist "C:\Users\cheng\AppData\Local\Programs\Python\Python38-32\python.exe" (
    set "PYTHON_CMD=C:\Users\cheng\AppData\Local\Programs\Python\Python38-32\python.exe"
) else (
    set "PYTHON_CMD=python"
)

echo ==========================================
echo  PrintSVC Build Script
echo  Target: Windows 7 32-bit
echo ==========================================
echo.

REM ---- Check Python version ----
"%PYTHON_CMD%" --version 2>nul | findstr "3.8" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Python version may not be 3.8. Win7 32-bit best with Python 3.8.
    "%PYTHON_CMD%" --version
)

REM ---- Check architecture (32-bit required) ----
"%PYTHON_CMD%" -c "import struct; exit(8 if struct.calcsize('P') != 4 else 0)" 2>nul
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
"%PYTHON_CMD%" -m pip install --upgrade pip -q
"%PYTHON_CMD%" -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.

REM ---- Locate pywin32 runtime DLLs ----
for /f "usebackq delims=" %%P in (`"%PYTHON_CMD%" -c "import os, site; paths = site.getsitepackages() + [site.getusersitepackages()]; matches = [os.path.join(p, 'pywin32_system32') for p in paths if os.path.exists(os.path.join(p, 'pywin32_system32', 'pythoncom38.dll'))]; print(matches[0] if matches else '')"`) do set PYWIN32_SYSTEM32=%%P
if not exist "%PYWIN32_SYSTEM32%\pythoncom38.dll" (
    echo [ERROR] pythoncom38.dll not found in %PYWIN32_SYSTEM32%
    pause
    exit /b 1
)
if not exist "%PYWIN32_SYSTEM32%\pywintypes38.dll" (
    echo [ERROR] pywintypes38.dll not found in %PYWIN32_SYSTEM32%
    pause
    exit /b 1
)

REM ---- Run PyInstaller ----
echo [Step 2/5] Cleaning previous build...
if exist "dist\PrintSVC" rmdir /s /q "dist\PrintSVC"
if exist "build\PrintSVC" rmdir /s /q "build\PrintSVC"
if exist "dist\PrintSVC.exe" del /q "dist\PrintSVC.exe"

echo [Step 3/5] Building executable (this may take several minutes)...
"%PYTHON_CMD%" -m PyInstaller ^
    --name PrintSVC ^
    --onefile ^
    --console ^
    --clean ^
    --noconfirm ^
    --add-data "printsvc.json;." ^
    --add-binary "%PYWIN32_SYSTEM32%\pythoncom38.dll;." ^
    --add-binary "%PYWIN32_SYSTEM32%\pywintypes38.dll;." ^
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
