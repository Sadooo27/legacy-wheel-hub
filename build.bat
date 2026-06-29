@echo off
setlocal
REM =================================================================
REM  Legacy Wheel Hub - build LegacyWheelHub.exe (PyInstaller)
REM  Uses "python -m PyInstaller" so it works even when the Scripts
REM  folder is not on PATH (that's why "pyinstaller" was not found).
REM =================================================================

REM Pick an interpreter: prefer "python", fall back to the "py" launcher.
set "PY=python"
where python >nul 2>nul || set "PY=py"
echo Using interpreter: %PY%

echo Installing dependencies...
%PY% -m pip install --upgrade pyinstaller hidapi PySide6 PySide6-Fluent-Widgets

echo Building executable...
%PY% -m PyInstaller --noconfirm --onefile --windowed ^
  --name "LegacyWheelHub" ^
  --icon "wheel.ico" ^
  --version-file "version.txt" ^
  --add-data "wheel.png;." ^
  --collect-all qfluentwidgets ^
  --collect-all qframelesswindow ^
  LegacyWheelHub.py

echo.
if exist "dist\LegacyWheelHub.exe" (
  echo SUCCESS. Executable: dist\LegacyWheelHub.exe
) else (
  echo BUILD FAILED - read the messages above for the reason.
)
pause
