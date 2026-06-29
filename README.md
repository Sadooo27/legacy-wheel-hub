# Legacy Wheel Hub
[![Latest release](https://img.shields.io/github/v/release/Sadooo27/legacy-wheel-hub?label=release)](https://github.com/Sadooo27/legacy-wheel-hub/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Sadooo27/legacy-wheel-hub/total)](https://github.com/Sadooo27/legacy-wheel-hub/releases)
[![License: GPL-3.0](https://img.shields.io/github/license/Sadooo27/legacy-wheel-hub)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6)
![Made with Python](https://img.shields.io/badge/Python-3.x-3776AB?logo=python&logoColor=white)

A lightweight control panel for **legacy Logitech force-feedback wheels**
(Driving Force GT and G27) — a free, open alternative to Logitech Gaming
Software. Set rotation range and FFB strength,
test motor effects, monitor every button/axis live, and auto-apply your
settings the moment the wheel finishes calibrating.

Built with Python, PySide6 and QFluentWidgets.

## Screenshots
<details>
  <summary>Click to view screenshots</summary>
<img width="1366" height="860" alt="image1" src="https://github.com/user-attachments/assets/f0b93bdd-8ab3-4b5f-9348-ee10c2bf5010" />
<img width="1366" height="860" alt="image2" src="https://github.com/user-attachments/assets/e7199cff-5ff3-44be-9432-f7eab53b194a" />
<img width="1366" height="860" alt="image3" src="https://github.com/user-attachments/assets/99d9caba-3eca-43b9-99e3-d3b28b393337" />
<img width="1366" height="860" alt="image4" src="https://github.com/user-attachments/assets/caae83fe-a057-45a1-b666-9b29f92f47b2" />

</details>

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
