# Legacy Wheel Hub

A free control panel for **legacy Logitech force-feedback wheels** (Driving
Force GT and G27) — a modern, open alternative to Logitech Gaming Software.
Set your rotation range and force-feedback strength, remove the center FFB
deadzone, apply a **custom LUT curve in any game**, test the motor, watch every
button and axis live, and have your settings applied automatically the moment
the wheel finishes calibrating.

## 1) Install the wheel drivers first
This app does **not** include any Logitech drivers. On Windows 10/11, install
the community driver package first:

> https://github.com/Mysli0210/Legacy-Logitech-wheels-for-W11

Follow that project's steps, reboot if prompted, then continue below.

## 2) Install Legacy Wheel Hub
1. Download **`LegacyWheelHub_Setup.exe`** from the [Releases](../../releases) page.
2. Run it and follow the installer.
3. Plug in your wheel, open the app, choose your settings, and press **APPLY**.

> Windows SmartScreen may warn because the installer is unsigned — click
> **More info → Run anyway**.

## Features
- **Force Feedback tuning** — overall strength (incl. the 101% center-deadzone
  fix), spring, damper, centering spring
- **Global LUT (FFB post-processing)** — import an Assetto-Corsa-style `.lut`
  curve and apply it to *any* game, even titles without built-in LUT support,
  for more linear force feedback on gear-driven wheels (see below)
- **Per-game profiles** — each profile remembers its wheel settings, its game,
  and its own LUT; selecting a profile applies everything instantly, and shows
  the game's icon
- Steering rotation range (40–900°) with quick presets
- Live telemetry: rotating wheel, steering angle, pedals (clutch/brake/throttle)
- Input Monitor: paddles, buttons, D-pad, face buttons, H-pattern shifter, LED test
- FFB motor test bench (push / spring / sweep / pulse / vibration)
- Auto-load on connect, system-tray minimize
- Light/Dark theme, multi-language (EN / TR / DE)

## Global LUT — how it works
Gear-driven wheels like the G27/DFGT have a force-feedback "deadzone" near
center: small forces don't move the wheel at all. A **LUT** (look-up table)
remaps the game's force so even small inputs are felt, giving a more linear
response.

Some sims (Assetto Corsa, ACC, iRacing) apply a LUT themselves. Legacy Wheel
Hub adds LUT support to games that **don't** have it:

1. Open the **LUT** tab and click **Import LUT** to add a `.lut` file (you can
   generate one for your wheel with WheelCheck + LUT Generator). Keep several
   and pick a different one per profile.
2. Tick **Enable FFB post-processing**.
3. Edit or create a profile and choose the game. Legacy Wheel Hub handles the
   rest automatically so the game's force feedback runs through your LUT.

Do **not** apply a LUT in sims that already have their own (AC, ACC, iRacing) —
that would double up the curve.

> ⚠ **Online games:** Do not use the LUT in online games. Some anti-cheat
> systems may flag it. If you use it online, it's **at your own risk!**

Your settings are stored per-user at `%APPDATA%\Legacy Wheel Hub\settings.json`.

## License
Released under the **GNU General Public License v3.0 (GPL-3.0)**. Full text at
https://www.gnu.org/licenses/gpl-3.0.txt

## Disclaimer
Not affiliated with, endorsed by, or sponsored by Logitech. "Logitech",
"Driving Force" and "G27" are trademarks of Logitech, used here only to
indicate hardware compatibility. The app talks to the wheel via standard USB
HID and Logitech driver registry settings for interoperability; no Logitech
software or files are included or distributed. Use at your own risk.
