# Legacy Wheel Hub
[![Toplam İndirme](https://img.shields.io/github/downloads/Sadooo27/legacy-wheel-hub/total?label=Downloads&color=blue)](https://github.com/Sadooo27/legacy-wheel-hub/releases)
[![Son Sürüm](https://img.shields.io/github/v/release/Sadooo27/legacy-wheel-hub?label=Latest%20Release&color=brightgreen)](https://github.com/Sadooo27/legacy-wheel-hub/releases/latest)
[![Son Sürüm İndirme](https://img.shields.io/github/downloads/Sadooo27/legacy-wheel-hub/latest/total?label=Latest%20Downloads&color=orange)](https://github.com/Sadooo27/legacy-wheel-hub/releases/latest)
[![Lisans](https://img.shields.io/github/license/Sadooo27/legacy-wheel-hub?color=lightgrey)](https://github.com/Sadooo27/legacy-wheel-hub/blob/main/LICENSE)

A free control panel for **legacy Logitech force-feedback wheels** (G27, G25,
Driving Force GT, Driving Force Pro, Driving Force, MOMO Racing and MOMO Force) —
a modern, open alternative to Logitech Gaming Software.
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
1. Download **`LegacyWheelHub_Setup_v1.2.0.exe`** from the [Releases](../../releases) page.
2. Run it and follow the installer.
3. Plug in your wheel, open the app, choose your settings, and press **APPLY**.

> Windows SmartScreen may warn because the installer is unsigned — click
> **More info → Run anyway**.

**Portable instead:** download **`LegacyWheelHub_Portable_v1.2.0.zip`**, unzip it
anywhere and run `LegacyWheelHub.exe`. Settings and LUTs stay in the `data` folder
beside the program. Before deleting the folder, run
`LegacyWheelHub.exe --uninstall-cleanup` once to take out what LWH put in game
folders and the registry.

## Features
- **Seven legacy wheels** — G27 and Driving Force GT measured on the hardware; G25,
  Driving Force Pro, Driving Force, MOMO Racing and MOMO Force built from Logitech's own
  driver files and the Linux driver, awaiting a first confirmation on a real wheel. The
  G25 is read from its own HID descriptor, pedals and all 19 buttons included.
- **LUT generator** — measures this wheel (breakaway threshold across angles and
  directions, torque against force, and whether the shape it finds is the wheel's
  own or an artefact of speed) and computes a curve from it, with sliders for the
  centre and the body. Generated LUTs can be reshaped later with **Edit**, from the
  same measurement. Leave the strength at 100% when using one.
- **Force Feedback tuning** — overall strength (incl. the 101% center-deadzone
  fix), spring, damper, centering spring, and centering ramp (how sharply the
  centering force builds up — same strength, very different feel)
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
- Light/Dark theme, multi-language (EN / TR / DE / UK / RU)

## Global LUT — how it works
Gear-driven wheels like these have a force-feedback "deadzone" near
center: small forces don't move the wheel at all. A **LUT** (look-up table)
remaps the game's force so even small inputs are felt, giving a more linear
response.

Some sims (Assetto Corsa, ACC, iRacing) apply a LUT themselves. Legacy Wheel
Hub adds LUT support to games that **don't** have it:

1. Open the **LUT** tab. **Generate LUT** measures your own wheel and computes
   a curve from it; **Import LUT** adds a `.lut` file you already have. Keep several
   and pick a different one per profile.
2. Select the game's profile (or create one and choose the game) and tick
   **Enable FFB post-processing**. Global has no game, so the LUT always goes
   in a game's own profile.
3. Legacy Wheel Hub handles the rest so the game's force feedback runs through
   your LUT: through the Logitech driver where the wheel allows it (no file in
   the game folder), otherwise with a small helper DLL next to the game.

Do **not** apply a LUT in sims that already have their own (AC, ACC, iRacing) —
that would double up the curve.

> ⚠ **Online games:** Do not use the LUT in online games. Some anti-cheat
> systems may flag it. If you use it online, it's **at your own risk!**

Your settings are stored per-user at `%APPDATA%\Legacy Wheel Hub\settings.json`
(the portable version keeps them in its own `data` folder).

## Thanks
Ukrainian and Russian translations by Andrii ([and-ri](https://github.com/and-ri)).

Developed with assistance from [Claude](https://claude.ai).

## License
Released under the **GNU General Public License v3.0 (GPL-3.0)**. Full text at
https://www.gnu.org/licenses/gpl-3.0.txt

## Disclaimer
Not affiliated with, endorsed by, or sponsored by Logitech. "Logitech",
"Driving Force", "G25", "G27" and "MOMO" are trademarks of Logitech, used here only to
indicate hardware compatibility. The app talks to the wheel via standard USB
HID and Logitech driver registry settings for interoperability; no Logitech
software or files are included or distributed. Use at your own risk.
