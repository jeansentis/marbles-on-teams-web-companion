@echo off
cd /d "%~dp0"
title Marbles on Teams — Setup
color 0A

echo.
echo  ============================================
echo   Marbles on Teams  ^|  Companion Setup
echo  ============================================
echo.
echo  This sets up everything you need to run the
echo  Marbles on Teams companion. Only needed once!
echo.
echo  ------------------------------------------
echo  Step 1: Checking for Python...
echo  ------------------------------------------
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Python was not found on this computer.
    echo.
    echo  Please follow these steps:
    echo.
    echo   1. The Python download page will open in your browser.
    echo   2. Click the yellow "Download Python" button.
    echo   3. Run the installer.
    echo   4. IMPORTANT: Check the box that says
    echo      "Add Python to PATH" before clicking Install!
    echo   5. After installing, run this setup.bat again.
    echo.
    pause
    start https://www.python.org/downloads/
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  [OK] Found %PYVER%
echo.
echo  ------------------------------------------
echo  Step 2: Installing required packages...
echo  ------------------------------------------
echo.

pip install requests --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo  [!] Failed to install packages.
    echo      Try running this file as Administrator.
    pause
    exit /b 1
)
echo  [OK] Packages ready.
echo.
echo  ------------------------------------------
echo  Step 3: Creating desktop shortcut...
echo  ------------------------------------------
echo.

set SCRIPT_DIR=%~dp0
set SHORTCUT=%USERPROFILE%\Desktop\Marbles on Teams.lnk

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut('%SHORTCUT%');" ^
  "$s.TargetPath = '%SCRIPT_DIR%run.bat';" ^
  "$s.WorkingDirectory = '%SCRIPT_DIR%';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Description = 'Marbles on Teams Companion';" ^
  "$s.Save()"

if exist "%SHORTCUT%" (
    echo  [OK] Shortcut created on your Desktop.
) else (
    echo  [~] Could not create shortcut — you can run run.bat directly.
)

echo.
echo  ------------------------------------------
echo  All done! Starting the companion now...
echo  ------------------------------------------
echo.

start pythonw companion.py
timeout /t 2 /nobreak >nul

echo  The companion is running. You can close this window.
echo  Next time, just use the shortcut on your Desktop.
echo.
pause
