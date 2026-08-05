==============================================================
  Legacy Wheel Hub  v1.1.3
  A free control panel for legacy Logitech force-feedback wheels
  (Driving Force GT and G27).
==============================================================

-------------------------------------------------------------
 IMPORTANT - INSTALL THE WHEEL DRIVERS FIRST
-------------------------------------------------------------
This app does NOT include any Logitech drivers. Before using it
on Windows 10/11, install the community driver package from:

    https://github.com/Mysli0210/Legacy-Logitech-wheels-for-W11

Follow that project's instructions, reboot if asked, then open
Legacy Wheel Hub and plug in your wheel.

-------------------------------------------------------------
 USING THE APP
-------------------------------------------------------------
- Set the rotation range and Force Feedback strength, then press APPLY.
- Tip: set "Overall Effects Strength" to 101% to remove the center
  FFB deadzone in most games (restart the game afterwards).
- FFB TEST: try the motor directly while no game is using the wheel.
- INPUT MONITOR: check every button and axis live.
- Enable "Auto-load on connect" to apply your settings automatically
  once the wheel finishes its power-on calibration.
- PROFILES: selecting a profile applies its settings instantly. You can
  assign a game .exe and a LUT to each profile.
- CENTERING RAMP: controls how sharply the centering force builds up
  around center. Higher = tighter center, lower = softer. Default is 7.
  Same strength with a different ramp feels very different - try it.

-------------------------------------------------------------
 GLOBAL LUT (FFB post-processing)
-------------------------------------------------------------
Gear-driven wheels (G27/DFGT) have a force-feedback deadzone near
center. A LUT curve remaps the game's force so small inputs are felt,
for a more linear response - even in games without built-in LUT support.

  1. LUT tab -> "Import LUT" to add a .lut file (generate one for your
     wheel with WheelCheck + LUT Generator). You can keep several and
     choose a different one per profile.
  2. Tick "Enable FFB post-processing".
  3. Edit/create a profile and choose the game's .exe. The app copies a
     small dinput8.dll helper next to that game so its force feedback is
     routed through your LUT.

Do NOT use a LUT in sims that already have their own (Assetto Corsa,
ACC, iRacing) - it would double the curve.

  !! ONLINE GAMES: Do not use the LUT / dinput8.dll helper in online
     games. Some anti-cheat systems may flag third-party DLLs next to
     the game. If you use it online, it is AT YOUR OWN RISK.

Your settings are stored per-user at:
    %APPDATA%\Legacy Wheel Hub\settings.json
Imported LUTs and the helper log live in the app's own "luts" folder.

-------------------------------------------------------------
 LICENSE
-------------------------------------------------------------
Legacy Wheel Hub is released under the GNU General Public License
v3.0 (GPL-3.0). See the project page for the full license text.

-------------------------------------------------------------
 DISCLAIMER
-------------------------------------------------------------
This project is NOT affiliated with, endorsed by, or sponsored by
Logitech. "Logitech", "Driving Force" and "G27" are trademarks of
Logitech, used here only to indicate hardware compatibility. The
app communicates with the wheel through standard USB HID and the
Logitech driver's registry settings for interoperability; no
Logitech software or files are included or distributed.

Use at your own risk.
