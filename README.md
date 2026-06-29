# Legacy Wheel Hub

A lightweight control panel for **legacy Logitech force-feedback wheels**
(Driving Force GT and G27) — a free, open alternative to Logitech Gaming
Software. Set rotation range and FFB strength,
test motor effects, monitor every button/axis live, and auto-apply your
settings the moment the wheel finishes calibrating.

Built with Python, PySide6 and QFluentWidgets.

## 1) Install the wheel drivers first
This app does **not** bundle any Logitech drivers. On Windows 10/11, install
the community driver package first:

> https://github.com/Mysli0210/Legacy-Logitech-wheels-for-W11

Follow that project's steps, reboot if prompted, then install/run Legacy Wheel Hub.

## 2) Install Legacy Wheel Hub
- **Easiest:** download `LegacyWheelHub_Setup.exe` from the Releases page and run it.
- **From source:**
  ```
  pip install hidapi PySide6 PySide6-Fluent-Widgets
  python LegacyWheelHub.py
  ```
  Keep `wheel.png` next to `LegacyWheelHub.py`.

## Features
- Force Feedback tuning: overall strength, spring, damper, centering spring
- Steering rotation range (40–900°) with quick presets
- Live telemetry: rotating wheel, steering angle, pedals (clutch/brake/throttle)
- Input Monitor: paddles, buttons, D-pad, face buttons, H-pattern shifter, LED greeting test
- FFB motor test bench (push / spring / sweep / pulse / vibration)
- Per-game profiles, auto-load on connect, system-tray minimize
- Light/Dark theme, multi-language (EN / TR / DE)

## Build the installer yourself
1. Build the EXE (shows as "Legacy Wheel Hub" in Task Manager, with the app icon):
   ```
   build.bat
   ```
   Needs `LegacyWheelHub.py`, `wheel.png`, `wheel.ico`, `version.txt` in the folder.
   Output: `dist\LegacyWheelHub.exe`.
2. Build the setup with **Inno Setup**: open `LegacyWheelHub.iss`, fix the
   `MySrc` / `OutputDir` paths for your machine, and Compile.
   Output: `LegacyWheelHub_Setup.exe`.

## License
Released under the **GNU General Public License v3.0 (GPL-3.0)** (because it uses PySide6-Fluent-Widgets, which is GPL-3.0). See the `LICENSE` file in this repository for the full text, or visit https://www.gnu.org/licenses/gpl-3.0.txt.

## Disclaimer
Not affiliated with, endorsed by, or sponsored by Logitech. "Logitech",
"Driving Force" and "G27" are trademarks of Logitech, used here only to
indicate hardware compatibility. The app talks to the wheel via standard USB
HID and Logitech driver registry settings for interoperability; no Logitech
software or files are included or distributed. Use at your own risk.
