@echo off
setlocal
REM =================================================================
REM  Legacy Wheel Hub - build (PyInstaller, ONEDIR = fast startup)
REM  Output folder: dist\LegacyWheelHub\  (contains the exe + _internal)
REM  Uses "python -m PyInstaller" so it works even when Scripts is
REM  not on PATH.
REM =================================================================

set "PY=python"
where python >nul 2>nul || set "PY=py"
echo Using interpreter: %PY%

echo Installing dependencies...
%PY% -m pip install --upgrade pyinstaller hidapi PySide6 PySide6-Fluent-Widgets

echo Building application folder (onedir)...
%PY% -m PyInstaller --noconfirm --onedir --windowed ^
  --name "LegacyWheelHub" ^
  --icon "wheel.ico" ^
  --version-file "version.txt" ^
  --add-data "wheel.png;." ^
  --collect-all qfluentwidgets ^
  --collect-all qframelesswindow ^
  LegacyWheelHub.py

echo.
if exist "dist\LegacyWheelHub\LegacyWheelHub.exe" (
  echo SUCCESS. App folder: dist\LegacyWheelHub\
  echo   Main exe: dist\LegacyWheelHub\LegacyWheelHub.exe
  echo   Now compile LegacyWheelHub.iss to make the installer.
) else (
  echo BUILD FAILED - read the messages above for the reason.
)
pause
