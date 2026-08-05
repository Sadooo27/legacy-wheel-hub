"""
Legacy Logitech Wheels - Control Hub  (PySide6 + QFluentWidgets edition)
"""
import sys, os, json, math, time, threading, struct

try:
    import hid
except ImportError:
    print("ERROR: hidapi is not installed  ->  pip install hidapi"); sys.exit(1)

try:
    import winreg
except ImportError:
    winreg = None

from PySide6.QtCore import Qt, QTimer, QThread, QRectF, QPointF, Signal, QEvent, QSize, QPropertyAnimation, QEasingCurve, qInstallMessageHandler, QFileInfo
from PySide6.QtGui import QPainter, QColor, QPixmap, QPolygonF, QFont, QPen, QIcon, QAction, QImage, QPainterPath, QFontMetrics
from PySide6.QtWidgets import (QApplication, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
                               QFrame, QSizePolicy, QScrollArea, QSystemTrayIcon, QMenu,
                               QStackedWidget, QButtonGroup, QSplitter, QFileDialog,
                               QLabel)

# QFileIconProvider moved between QtWidgets and QtGui across Qt6/PySide6
# releases; import defensively so a wrong location can never crash startup.
try:
    from PySide6.QtGui import QFileIconProvider
except Exception:
    try:
        from PySide6.QtWidgets import QFileIconProvider
    except Exception:
        QFileIconProvider = None

from qfluentwidgets import (FluentWindow, NavigationItemPosition, setTheme, Theme, setThemeColor,
                            FluentIcon as FIF, PushButton, PrimaryPushButton, Slider, LineEdit,
                            BodyLabel, StrongBodyLabel, TitleLabel, SubtitleLabel, CaptionLabel,
                            CardWidget, CheckBox, InfoBar, InfoBarPosition, isDarkTheme,
                            ComboBox, TransparentToolButton, ToolButton, Pivot, TransparentPushButton)
from qframelesswindow import FramelessWindow, TitleBar

try:
    from qfluentwidgets import MessageBox
except Exception:
    MessageBox = None
try:
    from qfluentwidgets import MessageBoxBase
except Exception:
    MessageBoxBase = None
try:
    from qfluentwidgets import SwitchButton
except Exception:
    SwitchButton = None

VID = 0x046D
def _exe_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _resource_dir():
    # PyInstaller one-file extracts bundled data to _MEIPASS
    return getattr(sys, "_MEIPASS", _exe_dir())

def _data_dir():
    # persistent, writable location for settings (works under Program Files)
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "Legacy Wheel Hub")
    else:
        d = _exe_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d

def _find_wheel():
    for d in (_exe_dir(), _resource_dir(), os.path.dirname(os.path.abspath(__file__))):
        p = os.path.join(d, "wheel.png")
        if os.path.exists(p):
            return p
    return os.path.join(_exe_dir(), "wheel.png")


def _lut_dir():
    """Folder that holds imported .lut files AND the proxy log.

    Lives inside the application folder (next to the exe) so everything is
    self-contained and portable, not under %LOCALAPPDATA%. Falls back to the
    writable data dir only if the exe folder cannot be written to.
    """
    d = os.path.join(_exe_dir(), "luts")
    try:
        os.makedirs(d, exist_ok=True)
        # quick writability probe
        t = os.path.join(d, ".w")
        with open(t, "w") as f:
            f.write("")
        os.remove(t)
        return d
    except Exception:
        d = os.path.join(_data_dir(), "luts")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d


def set_active_lut(path):
    """Tell the dinput8 proxy which LUT to use (and where to log).

    The proxy DLL runs inside game processes and has no idea where LWH is
    installed, so we publish the active LUT's absolute path and the log
    directory in the registry (HKCU\\Software\\LegacyWheelHub). The DLL polls
    these and hot-reloads. Pass path=None/"" to disable (passthrough).
    """
    if winreg is None:
        return
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\LegacyWheelHub")
        winreg.SetValueEx(key, "ActiveLut", 0, winreg.REG_SZ, path or "")
        winreg.SetValueEx(key, "LogDir", 0, winreg.REG_SZ, _lut_dir())
        winreg.CloseKey(key)
    except Exception:
        pass


_ICON_PROVIDER = None
_ICON_CACHE = {}


def exe_icon(path, size=20):
    """Return a QIcon for a game exe (its embedded icon) or None.

    Uses QFileIconProvider, which on Windows returns the exe's real icon.
    Cached by (path, mtime) so repeated preset redraws are cheap.
    """
    global _ICON_PROVIDER
    if not path or not os.path.isfile(path) or QFileIconProvider is None:
        return None
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = 0
    ck = (path, size, mtime)
    if ck in _ICON_CACHE:
        return _ICON_CACHE[ck]
    try:
        if _ICON_PROVIDER is None:
            _ICON_PROVIDER = QFileIconProvider()
        ic = _ICON_PROVIDER.icon(QFileInfo(path))
        if ic.isNull():
            ic = None
    except Exception:
        ic = None
    _ICON_CACHE[ck] = ic
    return ic


def parse_lut_file(path):
    """Parse an AC-style .lut into a list of (input, output) points in 0..1.

    Accepts '|', ',', ';' or whitespace separators, skips comments, and
    auto-normalizes 0..100 scaled files to 0..1. Returns [] on failure.
    """
    pts = []
    maxv = 0.0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in ";#":
                    continue
                for sep in "|,;\t":
                    line = line.replace(sep, " ")
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        x = float(parts[0]); y = float(parts[1])
                    except ValueError:
                        continue
                    pts.append((x, y))
                    maxv = max(maxv, x, y)
    except Exception:
        return []
    if len(pts) < 2:
        return []
    if maxv > 1.5:                     # looks like 0..100 -> normalize
        pts = [(x / 100.0, y / 100.0) for x, y in pts]
    pts.sort(key=lambda p: p[0])
    return pts


# ---- dinput8 proxy installer (per-game DLL placement) -------------------

PROXY_DLL = "dinput8.dll"
PROXY_MARKER = ".lwh_proxy"   # marks a dinput8.dll that WE placed


def _proxy_assets_dir():
    """Folder holding the bundled proxy DLLs: assets/proxy/{x86,x64}/dinput8.dll"""
    for base in (_resource_dir(), _exe_dir(), os.path.dirname(os.path.abspath(__file__))):
        d = os.path.join(base, "assets", "proxy")
        if os.path.isdir(d):
            return d
    return os.path.join(_resource_dir(), "assets", "proxy")


def exe_bitness(path):
    """Return 'x86', 'x64' or None by reading the PE IMAGE_FILE_HEADER.Machine."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return None
            f.seek(0x3C)
            (pe_off,) = struct.unpack("<I", f.read(4))
            f.seek(pe_off)
            if f.read(4) != b"PE\x00\x00":
                return None
            (machine,) = struct.unpack("<H", f.read(2))
    except Exception:
        return None
    if machine == 0x014C:
        return "x86"
    if machine == 0x8664:
        return "x64"
    if machine == 0xAA64:
        return "arm64"     # an x64 proxy DLL will NOT load in an ARM64 process
    return None


def resolve_game_exe(exe_path):
    """For UE/launcher games, try to find the real ...-Shipping.exe.

    A launcher usually sits in the game root while the real executable lives in
    <Project>\\Binaries\\Win64\\<Name>-Win64-Shipping.exe. We therefore search
    DOWN into subfolders (the root's Binaries and each immediate subproject's
    Binaries) as well as the selected folder itself. If nothing is found we
    just return the given exe so the user can point us at the right one.
    """
    if not exe_path or not os.path.isfile(exe_path):
        return exe_path
    name = os.path.basename(exe_path).lower()
    if name.endswith("-shipping.exe") or "shipping" in name:
        return exe_path

    root = os.path.dirname(exe_path)
    stem = os.path.splitext(os.path.basename(exe_path))[0].lower()  # e.g. "jdm"

    def _find_in(base):
        for sub in ("Win64", "WinGDK", "Win32"):
            bindir = os.path.join(base, "Binaries", sub)
            if os.path.isdir(bindir):
                try:
                    files = os.listdir(bindir)
                except Exception:
                    continue
                hits = [f for f in files if f.lower().endswith("-shipping.exe")]
                if not hits:
                    hits = [f for f in files
                            if "shipping" in f.lower() and f.lower().endswith(".exe")]
                if hits:
                    # prefer the one whose name matches the launcher/project name
                    # (JDM.exe -> JDM-Win64-Shipping.exe), else the first sorted.
                    pref = [f for f in hits if f.lower().startswith(stem)]
                    return os.path.join(bindir, sorted(pref or hits)[0])
        return None

    # 1) root/Binaries/...
    hit = _find_in(root)
    if hit:
        return hit
    # 2) root/<subproject>/Binaries/...  (the common UE layout).
    #    Skip "Engine" — it only ever holds engine tools (UnrealEditor,
    #    CrashReportClient), never the game itself.
    try:
        subs = sorted(os.listdir(root))
        # try a subfolder matching the launcher name first (JDM\ for JDM.exe)
        subs.sort(key=lambda e: (e.lower() != stem, e.lower()))
        for entry in subs:
            if entry.lower() == "engine":
                continue
            sub = os.path.join(root, entry)
            if os.path.isdir(sub):
                hit = _find_in(sub)
                if hit:
                    return hit
    except Exception:
        pass
    # nothing found -> caller/user handles it manually
    return exe_path


def install_proxy_for(exe_path, overwrite_foreign=False):
    """Copy the correct-bitness dinput8.dll next to the game exe.

    Returns (ok: bool, message_key_or_text). Leaves a marker file so we can
    safely remove only DLLs we placed. If a foreign dinput8.dll already exists
    and overwrite_foreign is False, returns ('foreign', dir) so the caller can
    ask the user.
    """
    real = resolve_game_exe(exe_path)
    if not real or not os.path.isfile(real):
        return (False, "proxy.err_noexe")
    arch = exe_bitness(real)
    if arch not in ("x86", "x64"):
        return (False, "proxy.err_arch")
    src = os.path.join(_proxy_assets_dir(), arch, PROXY_DLL)
    if not os.path.isfile(src):
        return (False, "proxy.err_asset")
    exe_dir = os.path.dirname(real)
    dst = os.path.join(exe_dir, PROXY_DLL)
    marker = os.path.join(exe_dir, PROXY_MARKER)
    if os.path.isfile(dst) and not os.path.isfile(marker) and not overwrite_foreign:
        return ("foreign", exe_dir)
    try:
        with open(src, "rb") as s, open(dst, "wb") as d:
            d.write(s.read())
        with open(marker, "w", encoding="utf-8") as m:
            m.write(f"Legacy Wheel Hub proxy ({arch})\n")
    except Exception:
        return (False, "proxy.err_write")
    return (True, exe_dir)


def install_proxy_ui(exe, parent):
    """install_proxy_for() + user feedback. Shared by the preset editor and the
    LUT tab so the DLL is handled the same way from both."""
    ok, info = install_proxy_for(exe)
    if ok == "foreign":
        if _confirm_dialog(tr("proxy.foreign_title"), tr("proxy.foreign_body"), parent):
            ok, info = install_proxy_for(exe, overwrite_foreign=True)
        else:
            ok = False
    if ok is True:
        _defer_infobar("success", tr("proxy.installed"), tr("proxy.installed_body"),
                       duration=2500, position=InfoBarPosition.TOP, parent=parent)
    elif ok is False and info in ("proxy.err_noexe", "proxy.err_arch",
                                  "proxy.err_asset", "proxy.err_write"):
        _defer_infobar("warning", tr("proxy.installed"), tr(info), duration=3500,
                       position=InfoBarPosition.TOP, parent=parent)


def uninstall_proxy_for(exe_path, force=False):
    """Remove the dinput8.dll we installed next to the game exe (marker-guarded)."""
    real = resolve_game_exe(exe_path)
    if not real:
        return False
    exe_dir = os.path.dirname(real)
    dst = os.path.join(exe_dir, PROXY_DLL)
    marker = os.path.join(exe_dir, PROXY_MARKER)
    if os.path.isfile(dst) and not os.path.isfile(marker) and not force:
        return False           # not ours -> leave it
    stale = dst + ".old"
    try:
        if os.path.isfile(stale):      # leftover from a previous locked removal
            try: os.remove(stale)
            except Exception: pass
        if os.path.isfile(dst):
            try:
                os.remove(dst)
            except PermissionError:
                # The game is running and has the DLL loaded, so Windows won't
                # let us delete it - but it DOES allow a rename. Renaming is
                # enough: the game will never load it again, and the leftover
                # is cleaned up on the next install/uninstall.
                try:
                    os.replace(dst, stale)
                except Exception:
                    return False
                if os.path.isfile(marker):
                    try: os.remove(marker)
                    except Exception: pass
                return "locked"
        if os.path.isfile(marker):
            os.remove(marker)
    except Exception:
        return False
    return True

def _screen_px():
    # primary-screen physical size, queried BEFORE QApplication exists.
    ov = os.environ.get("LWH_SCREEN")          # test override "WxH"
    if ov and "x" in ov:
        try:
            w, h = ov.lower().split("x"); return int(w), int(h)
        except Exception:
            pass
    try:
        if sys.platform == "win32":
            import ctypes
            u = ctypes.windll.user32
            w, h = int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 1920, 1080

APP_DIR = _exe_dir()
WHEEL_PNG = _find_wheel()
SETTINGS_FILE = os.path.join(_data_dir(), "settings.json")
STEER_CENTER = 8192
ACCENT_FALLBACK = "#ff6a1a"
HUB_VERSION = "v1.1.3"
AUTHOR = "Sadooo"


def windows_accent_color():
    if winreg is None:
        return ACCENT_FALLBACK
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM")
        val, _ = winreg.QueryValueEx(k, "AccentColor")
        winreg.CloseKey(k)
        r = val & 0xFF; g = (val >> 8) & 0xFF; b = (val >> 16) & 0xFF
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return ACCENT_FALLBACK


ACCENT = windows_accent_color()

DEVICE_PROFILES = {
    "DFGT": {
        "name": "Logitech Driving Force GT", "pid_native": 0xC29A,
        "registry_pids": ["VID_046D&PID_C29A", "VID_046D&PID_C294"],
        "steer": {"fmt": "lohi", "lo": 4, "hi": 5, "himask": 0x3F, "center": 8192, "half": 8192},
        "throttle": 6, "brake": 7, "clutch": None, "pedal_invert": True,
        "info": {"model": "Logitech Driving Force GT", "hwid": "USB\\VID_046D & PID_C29A",
                 "interface": "USB 2.0 (Full-Speed)", "power": "24V DC",
                 "tracking": "High-Resolution Optical Encoder",
                 "axis": "14-bit (16,384 Steps)", "ffb": "Single-Motor, Gear-Driven",
                 "polling": "~500 Hz", "api": "Direct HID RAW"},
    },
    "G27": {
        "name": "Logitech G27 Racing Wheel", "pid_native": 0xC29B,
        "registry_pids": ["VID_046D&PID_C29B", "VID_046D&PID_C294"],
        "steer": {"fmt": "hilo6", "lo": 3, "hi": 4, "center": 8192, "half": 8192},
        "throttle": 5, "brake": 6, "clutch": 11, "pedal_invert": True,
        "info": {"model": "Logitech G27 Racing Wheel", "hwid": "USB\\VID_046D & PID_C29B",
                 "interface": "USB 2.0 (Full-Speed)", "power": "24V DC",
                 "tracking": "High-Resolution Optical Encoder",
                 "axis": "14-bit (16,384 Steps)", "ffb": "Dual-Motor, Helical Gear",
                 "polling": "~500 Hz", "api": "Direct HID RAW"},
    },
}
PID_COMPAT = 0xC294

# Derived from DEVICE_PROFILES so the two can never drift apart.
ALL_REGISTRY_PIDS = sorted({p for v in DEVICE_PROFILES.values() for p in v["registry_pids"]})
NATIVE_PIDS = sorted({v["pid_native"] for v in DEVICE_PROFILES.values()})

# Autocenter ramp slope used when a profile doesn't specify one. 7 is what
# v1.1.1 hard-coded everywhere, so this keeps old profiles feeling identical.
DEFAULT_RAMP = 7

LANG = {
    "en": {
        "nav.home": "Home", "nav.wheel": "Wheel Settings", "nav.ffb": "FFB Test",
        "nav.input": "Input Monitor", "nav.apply": "Apply Settings",
        "nav.theme": "Toggle Theme", "nav.about": "About",
        "conn.connecting": "Connecting\u2026", "conn.not_connected": "Not Connected",
        "home.title": "Live Telemetry", "home.steering": "STEERING ANGLE",
        "home.clutch": "CLUTCH", "home.brake": "BRAKE", "home.throttle": "THROTTLE",
        "home.center": "Center Wheel", "home.na": "N/A",
        "prof.label": "PROFILE", "prof.auto": "Auto Load", "prof.add": "New profile",
        "prof.dup": "Duplicate profile", "prof.ren": "Rename profile",
        "prof.del": "Delete profile", "prof.new_title": "New Profile",
        "prof.new_hint": "Profile name", "prof.ren_title": "Rename Profile",
        "prof.del_title": "Delete Profile", "prof.del_msg": "Delete profile \u201c{0}\u201d? This cannot be undone.",
        "prof.copy_suffix": " Copy", "dlg.ok": "OK", "dlg.cancel": "Cancel", "dlg.delete": "Delete",
        "wheel.title": "Wheel Settings", "wheel.ffb": "Force Feedback",
        "wheel.overall": "Overall Effects Strength",
        "wheel.overall_h": "Set to 101% to fix the center FFB deadzone in most games. (Requires game restart)",
        "wheel.spring": "Spring Effect", "wheel.spring_h": "Driver-based spring (recommended: 0%).",
        "wheel.damper": "Damper Effect", "wheel.damper_h": "Driver-based damping (recommended: 0%).",
        "wheel.center_cb": "Enable Centering Spring in FFB games",
        "wheel.center": "Centering Spring", "wheel.center_h": "Driver-based autocenter strength.",
        "wheel.ramp": "Centering Ramp", "wheel.ramp_h": "Shapes the centering spring only \u2014 no effect while it is 0. Default 7.",
        "wheel.steering": "Steering", "wheel.rotation": "Rotation Range",
        "wheel.rotation_h": "Maximum steering rotation angle.",
        "ffb.title": "FORCE FEEDBACK TEST",
        "ffb.subtitle": "Test the FFB motor directly. Best done while no game is using the wheel.",
        "ffb.strength": "Test Strength", "ffb.strength_h": "Strength used by Push, Spring and Sweep tests.",
        "ffb.push_l": "Push Left", "ffb.push_r": "Push Right",
        "ffb.spring": "Spring (Center)", "ffb.spring_stop": "Stop Spring",
        "ffb.sweep": "Auto Sweep", "ffb.sweep_stop": "Stop Sweep",
        "ffb.advanced": "ADVANCED MOTOR TESTS",
        "ffb.pulse_l": "Pulse Left", "ffb.pulse_r": "Pulse Right",
        "ffb.vibe_light": "Light Vibe", "ffb.vibe_med": "Medium Vibe",
        "ffb.vibe_fast": "Fast Rumble", "ffb.vibe_heavy": "Heavy Vibe",
        "ffb.stop": "STOP ALL FORCES",
        "input.title": "Input Monitor", "input.led": "LED Greeting Test",
        "input.wheel": "WHEEL", "input.shifter": "SHIFTER UNIT", "input.gear": "GEAR  (H-PATTERN)",
        "input.lpad": "LEFT PADDLE", "input.rpad": "RIGHT PADDLE",
        "input.face": "FACE BUTTONS", "input.dpad": "D-PAD", "input.horn": "HORN",
        "input.led_nc": "Wheel not connected.",
        "input.led_g27": "LED test is G27-only (DFGT has no RPM LEDs).",
        "input.led_run": "LED greeting\u2026 (works if the driver passes the report through)",
        "about.title": "Device Info", "about.settings": "Settings",
        "about.status": "Status", "about.connected": "Connected", "about.not_connected": "Not Connected",
        "about.model": "Model", "about.hwid": "Hardware ID", "about.axis": "Axis Resolution",
        "about.ffb": "Force Feedback", "about.language": "Language", "about.theme": "Theme",
        "about.theme_dark": "Dark", "about.theme_light": "Light",
        "about.testmode": "Test Device Mode", "about.testmode_h":
            "Switch the active wheel layout without hardware connected. \u201cAuto\u201d uses real detection.",
        "about.test_auto": "Auto (detect)", "about.footer":
            "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "HARDWARE DIAGNOSTICS", "about.sec_sensor": "SENSOR & FFB SPECIFICATIONS",
        "about.sec_sw": "SOFTWARE & DRIVER STATUS", "about.sec_credits": "CREDITS",
        "about.devmodel": "Device Model", "about.interface": "Interface", "about.power": "Power Status",
        "about.tracking": "Tracking System", "about.polling": "Max Polling Rate",
        "about.opmode": "Operating Mode", "about.api": "API Hook", "about.hub": "Hub Version",
        "about.author": "Author", "about.sec_about": "ABOUT", "about.repo": "GitHub repository", "about.license": "License: GPL-3.0", "about.disclaimer": "Not affiliated with Logitech. All trademarks belong to their respective owners.", "about.power_active": "{0} / Active",
        "about.power_standby": "Standby / Disconnected",
        "about.opmode_active": "Native Advanced Mode (Unlocked)",
        "about.opmode_idle": "Idle / Awaiting Device",
        "about.tray": "Minimize to Tray",
        "about.tray_h": "When on, the minimize button hides the app to the system tray (show hidden icons).",
        "tray.show": "Show", "tray.quit": "Quit",
        "apply.ok_title": "Applied", "apply.ok_body": "Settings applied to the wheel.",
        "ui.presets": "PRESETS", "ui.presets_sub": "Select before starting the game.",
        "ui.add_profile": "+  Add Game Profile", "ui.autoload": "Auto-load on connect",
        "ui.telemetry": "LIVE TELEMETRY", "ui.center": "Center",
        "ui.apply": "APPLY", "conn.connected": "Connected", "conn.test": "Test Mode",
        "tab.wheel": "WHEEL SETTINGS", "tab.ffb": "FFB TEST", "tab.input": "INPUT MONITOR",
        "tab.lut": "LUT",
        "lut.sec": "FFB POST-PROCESSING", "lut.enable": "Enable FFB post-processing",
        "lut.enable_h": "Pass the game's force feedback through the selected LUT curve in all games (via the dinput8 proxy).",
        "lut.select": "LUT curve", "lut.import": "Import LUT", "lut.delete": "Delete LUT", "lut.none": "(none)",
        "lut.del_title": "Delete LUT", "lut.del_body": "Delete the LUT file \u201c{}\u201d from disk? This cannot be undone.",
        "lut.deleted": "LUT deleted",
        "lut.empty": "No LUT files yet. Click \u201cImport LUT\u201d to add one.",
        "lut.warn": "\u26a0  Do not use in online games. If you do, it\u2019s at your own risk!",
        "lut.global_notice": "LUT is set per game. Create a game profile (\uff0b in Presets), choose its .exe, then pick its LUT here. The Global profile does not apply a LUT.",
        "lut.axis_in": "Input", "lut.axis_out": "Output",
        "lut.imported": "LUT imported", "lut.import_fail": "Could not import LUT",
        "lut.game": "GAME", "lut.exe": "Game executable", "lut.exe_pick": "Choose\u2026",
        "lut.exe_none": "No game selected",
        "prof.edit": "Edit profile", "prof.exe": "Game executable",
        "prof.exe_hint": "For UE launcher games, pick the real ...-Shipping.exe if it isn't found automatically.",
        "prof.logo": "Icon (from an .exe)", "prof.logo_pick": "Choose icon\u2026",
        "prof.exe_pick": "Choose game\u2026", "prof.name_lbl": "Profile name",
        "proxy.installed": "Proxy installed", "proxy.removed": "Proxy removed",
        "proxy.installed_body": "dinput8.dll placed next to the game.",
        "proxy.locked": "Proxy disabled", "proxy.locked_body": "Will be cleaned up when the game closes.",
        "proxy.removed": "Proxy removed", "proxy.removed_body": "dinput8.dll removed from the game folder.",
        "proxy.err_noexe": "Game exe not found.", "proxy.err_arch": "Unsupported architecture.",
        "proxy.err_asset": "Bundled proxy DLL missing (assets/proxy).",
        "proxy.err_write": "Could not write DLL (is the game running? folder writable?).",
        "proxy.foreign_title": "Existing dinput8.dll",
        "proxy.foreign_body": "This folder already has a dinput8.dll not installed by LWH (another mod/wrapper). Overwrite it?",
        "tab.info": "INFO", "wheel.sec_ffb": "FORCE FEEDBACK", "wheel.sec_steer": "STEERING SETTINGS",
        "ffb.reset": "Reset Driver FFB", "ffb.reset_h": "Deletes all driver FFB registry overrides written by this app.",
        "set.title": "SETTINGS", "set.appearance": "APPEARANCE", "set.general": "GENERAL", "set.testing": "TESTING",
        "set.theme": "Theme", "set.language": "Language", "set.tray": "Minimize to system tray",
        "set.tray_h": "When on, the minimize button hides the app to the system tray (show hidden icons).",
        "set.devmode": "Device detection mode",
        "set.devmode_h": "Force a wheel layout without hardware. \u201cAuto\u201d uses real detection.", "set.ui_scale": "Interface scale", "set.ui_scale_h": "Enlarge the whole interface on high-resolution (1440p/4K) screens. Takes effect after restarting the app.", "set.restart_hint": "Restart the app to apply the new scale.",
        "info.opmode_active": "Native Advanced Mode (Unlocked)", "info.opmode_idle": "Idle / Disconnected",
        "info.active": "Active", "info.standby": "Standby",
    },
    "tr": {
        "nav.home": "Ana Sayfa", "nav.wheel": "Direksiyon Ayarları", "nav.ffb": "FFB Testi",
        "nav.input": "Giriş İzleyici", "nav.apply": "Ayarları Uygula",
        "nav.theme": "Tema Değiştir", "nav.about": "Hakkında",
        "conn.connecting": "Bağlanıyor…", "conn.not_connected": "Bağlı Değil",
        "home.title": "Canlı Telemetri", "home.steering": "DİREKSİYON AÇISI",
        "home.clutch": "DEBRİYAJ", "home.brake": "FREN", "home.throttle": "GAZ",
        "home.center": "Direksiyonu Ortala", "home.na": "Yok",
        "prof.label": "PROFİL", "prof.auto": "Otomatik Yükle", "prof.add": "Yeni profil",
        "prof.dup": "Profili çoğalt", "prof.ren": "Profili yeniden adlandır",
        "prof.del": "Profili sil", "prof.new_title": "Yeni Profil",
        "prof.new_hint": "Profil adı", "prof.ren_title": "Profili Yeniden Adlandır",
        "prof.del_title": "Profili Sil", "prof.del_msg": "“{0}” profili silinsin mi? Bu işlem geri alınamaz.",
        "prof.copy_suffix": " Kopya", "dlg.ok": "Tamam", "dlg.cancel": "İptal", "dlg.delete": "Sil",
        "wheel.title": "Direksiyon Ayarları", "wheel.ffb": "Kuvvet Geri Bildirimi",
        "wheel.overall": "Genel Efekt Gücü",
        "wheel.overall_h": "Çoğu oyunda merkez FFB ölü bölgesini gidermek için %101 yapın. (Oyun yeniden başlatılmalı)",
        "wheel.spring": "Yay Efekti", "wheel.spring_h": "Sürücü tabanlı yay (önerilen: %0).",
        "wheel.damper": "Damper Efekti", "wheel.damper_h": "Sürücü tabanlı sönümleme (önerilen: %0).",
        "wheel.center_cb": "FFB oyunlarında ortalama yayını etkinleştir",
        "wheel.center": "Ortalama Yayı", "wheel.center_h": "Sürücü tabanlı otomatik ortalama gücü.",
        "wheel.ramp": "Ortalama Rampası", "wheel.ramp_h": "Yalnızca ortalama yayını şekillendirir — o 0 iken etkisizdir. Varsayılan 7.",
        "wheel.steering": "Direksiyon", "wheel.rotation": "Dönüş Aralığı",
        "wheel.rotation_h": "Maksimum direksiyon dönüş açısı.",
        "ffb.title": "KUVVET GERİ BİLDİRİM TESTİ",
        "ffb.subtitle": "FFB motorunu doğrudan test edin. Hiçbir oyun direksiyonu kullanmıyorken yapılması en iyisidir.",
        "ffb.strength": "Test Gücü", "ffb.strength_h": "İtme, Yay ve Tarama testlerinde kullanılan güç.",
        "ffb.push_l": "Sola İt", "ffb.push_r": "Sağa İt",
        "ffb.spring": "Yay (Merkez)", "ffb.spring_stop": "Yayı Durdur",
        "ffb.sweep": "Otomatik Tarama", "ffb.sweep_stop": "Taramayı Durdur",
        "ffb.advanced": "GELİŞMİŞ MOTOR TESTLERİ",
        "ffb.pulse_l": "Sol Darbe", "ffb.pulse_r": "Sağ Darbe",
        "ffb.vibe_light": "Hafif Titreşim", "ffb.vibe_med": "Orta Titreşim",
        "ffb.vibe_fast": "Hızlı Gürültü", "ffb.vibe_heavy": "Ağır Titreşim",
        "ffb.stop": "TÜM KUVVETLERİ DURDUR",
        "input.title": "Giriş İzleyici", "input.led": "LED Karşılama Testi",
        "input.wheel": "DİREKSİYON", "input.shifter": "VITES ÜNİTESİ", "input.gear": "VİTES  (H-DÜZENİ)",
        "input.lpad": "SOL PADDLE", "input.rpad": "SAĞ PADDLE",
        "input.face": "YÜZ TUŞLARI", "input.dpad": "YÖN TUŞU", "input.horn": "KORNA",
        "input.led_nc": "Direksiyon bağlı değil.",
        "input.led_g27": "LED testi yalnızca G27 içindir (DFGT'de RPM LED'i yoktur).",
        "input.led_run": "LED karşılama… (sürücü raporu geçirirse çalışır)",
        "about.title": "Cihaz Bilgisi", "about.settings": "Ayarlar",
        "about.status": "Durum", "about.connected": "Bağlı", "about.not_connected": "Bağlı Değil",
        "about.model": "Model", "about.hwid": "Donanım Kimliği", "about.axis": "Eksen Çözünürlüğü",
        "about.ffb": "Kuvvet Geri Bildirimi", "about.language": "Dil", "about.theme": "Tema",
        "about.theme_dark": "Koyu", "about.theme_light": "Açık",
        "about.testmode": "Test Cihaz Modu", "about.testmode_h":
            "Donanım bağlı olmadan aktif direksiyon düzenini değiştirin. “Otomatik” gerçek algılamayı kullanır.",
        "about.test_auto": "Otomatik (algıla)", "about.footer":
            "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "DONANIM TANILAMA", "about.sec_sensor": "SENSÖR & FFB ÖZELLİKLERİ",
        "about.sec_sw": "YAZILIM & SÜRÜCÜ DURUMU", "about.sec_credits": "KATKIDA BULUNANLAR",
        "about.devmodel": "Cihaz Modeli", "about.interface": "Arayüz", "about.power": "Güç Durumu",
        "about.tracking": "Takip Sistemi", "about.polling": "Maks. Yoklama Hızı",
        "about.opmode": "Çalışma Modu", "about.api": "API Bağlantısı", "about.hub": "Hub Sürümü",
        "about.author": "Yazar", "about.sec_about": "HAKKINDA", "about.repo": "GitHub deposu", "about.license": "Lisans: GPL-3.0", "about.disclaimer": "Logitech ile bağlantısı yoktur. Tüm ticari markalar sahiplerine aittir.", "about.power_active": "{0} / Aktif",
        "about.power_standby": "Beklemede / Bağlı Değil",
        "about.opmode_active": "Yerel Gelişmiş Mod (Kilit Açık)",
        "about.opmode_idle": "Boşta / Cihaz Bekleniyor",
        "about.tray": "Sistem Tepsisine Küçült",
        "about.tray_h": "Açıkken, küçültme tuşu uygulamayı sistem tepsisine (gizli simgeler) gizler.",
        "tray.show": "Göster", "tray.quit": "Çıkış",
        "apply.ok_title": "Uygulandı", "apply.ok_body": "Ayarlar direksiyona uygulandı.",
        "ui.presets": "HAZIR AYARLAR", "ui.presets_sub": "Oyuna başlamadan önce seçin.",
        "ui.add_profile": "+  Oyun Profili Ekle", "ui.autoload": "Bağlanınca otomatik yükle",
        "ui.telemetry": "CANLI TELEMETRİ", "ui.center": "Merkezle",
        "ui.apply": "UYGULA", "conn.connected": "Bağlı", "conn.test": "Test Modu",
        "tab.lut": "LUT",
        "lut.sec": "FFB SON İŞLEME", "lut.enable": "FFB son işlemeyi etkinleştir",
        "lut.enable_h": "Oyunun force feedback'ini seçili LUT eğrisinden geçirerek tüm oyunlarda uygular (dinput8 proxy ile).",
        "lut.select": "LUT eğrisi", "lut.import": "LUT İçe Aktar", "lut.delete": "LUT Sil", "lut.none": "(yok)",
        "lut.del_title": "LUT Sil", "lut.del_body": "“{}” LUT dosyası diskten silinsin mi? Bu geri alınamaz.",
        "lut.deleted": "LUT silindi",
        "lut.empty": "Henüz LUT dosyası yok. Eklemek için “LUT İçe Aktar”a tıkla.",
        "lut.warn": "⚠  Çevrimiçi oyunlarda kullanmayın. Kullanırsanız risk size aittir!",
        "lut.global_notice": "LUT her oyun için ayrı ayarlanır. Bir oyun profili oluşturup (Presets'te ＋) exe'sini seçin, sonra LUT'unu buradan seçin. Global profil LUT uygulamaz.",
        "lut.axis_in": "Giriş", "lut.axis_out": "Çıkış",
        "lut.imported": "LUT içe aktarıldı", "lut.import_fail": "LUT içe aktarılamadı",
        "lut.game": "OYUN", "lut.exe": "Oyun exe dosyası", "lut.exe_pick": "Seç…",
        "lut.exe_none": "Oyun seçilmedi",
        "prof.edit": "Profili düzenle", "prof.exe": "Oyun exe dosyası",
        "prof.exe_hint": "UE launcher'lı oyunlarda otomatik bulunamazsa gerçek ...-Shipping.exe'yi seçin.",
        "prof.logo": "Simge (bir .exe'den)", "prof.logo_pick": "Simge seç…",
        "prof.exe_pick": "Oyun seç…", "prof.name_lbl": "Profil adı",
        "proxy.installed": "Proxy kuruldu", "proxy.removed": "Proxy kaldırıldı",
        "proxy.installed_body": "dinput8.dll oyunun yanına kopyalandı.",
        "proxy.locked": "Proxy devre dışı", "proxy.locked_body": "Oyun kapandığında temizlenecek.",
        "proxy.removed": "Proxy kaldırıldı", "proxy.removed_body": "dinput8.dll oyun klasöründen kaldırıldı.",
        "proxy.err_noexe": "Oyun exe'si bulunamadı.", "proxy.err_arch": "Desteklenmeyen mimari.",
        "proxy.err_asset": "Gömülü proxy DLL'i yok (assets/proxy).",
        "proxy.err_write": "DLL yazılamadı (oyun açık mı? klasör yazılabilir mi?).",
        "proxy.foreign_title": "Mevcut dinput8.dll",
        "proxy.foreign_body": "Bu klasörde LWH'nin kurmadığı bir dinput8.dll var (başka bir mod/wrapper). Üzerine yazılsın mı?",
        "tab.wheel": "DİREKSİYON", "tab.ffb": "FFB TESTİ", "tab.input": "GİRİŞ İZLEYİCİ",
        "tab.info": "BİLGİ", "wheel.sec_ffb": "KUVVET GERİ BİLDİRİMİ", "wheel.sec_steer": "DİREKSİYON AYARLARI",
        "ffb.reset": "Sürücü FFB Sıfırla", "ffb.reset_h": "Bu uygulamanın yazdığı tüm sürücü FFB registry değerlerini siler.",
        "set.title": "AYARLAR", "set.appearance": "GÖRÜNÜM", "set.general": "GENEL", "set.testing": "TEST",
        "set.theme": "Tema", "set.language": "Dil", "set.tray": "Sistem tepsisine küçült",
        "set.tray_h": "Açıkken, küçültme tuşu uygulamayı sistem tepsisine (gizli simgeler) gizler.",
        "set.devmode": "Cihaz algılama modu",
        "set.devmode_h": "Donanımsız bir düzen zorla. “Otomatik” gerçek algılamayı kullanır.", "set.ui_scale": "Arayüz ölçeği", "set.ui_scale_h": "Yüksek çözünürlüklü (1440p/4K) ekranlarda tüm arayüzü büyütür. Uygulama yeniden başlatılınca etkin olur.", "set.restart_hint": "Yeni ölçeğin uygulanması için uygulamayı yeniden başlatın.",
        "info.opmode_active": "Yerel Gelişmiş Mod (Kilit Açık)", "info.opmode_idle": "Boşta / Bağlı Değil",
        "info.active": "Aktif", "info.standby": "Beklemede",
    },
    "de": {
        "nav.home": "Startseite", "nav.wheel": "Lenkrad-Einstellungen", "nav.ffb": "FFB-Test",
        "nav.input": "Eingangsmonitor", "nav.apply": "Einstellungen anwenden",
        "nav.theme": "Design wechseln", "nav.about": "Über",
        "conn.connecting": "Verbinden…", "conn.not_connected": "Nicht verbunden",
        "home.title": "Live-Telemetrie", "home.steering": "LENKWINKEL",
        "home.clutch": "KUPPLUNG", "home.brake": "BREMSE", "home.throttle": "GAS",
        "home.center": "Lenkrad zentrieren", "home.na": "N/V",
        "prof.label": "PROFIL", "prof.auto": "Auto-Laden", "prof.add": "Neues Profil",
        "prof.dup": "Profil duplizieren", "prof.ren": "Profil umbenennen",
        "prof.del": "Profil löschen", "prof.new_title": "Neues Profil",
        "prof.new_hint": "Profilname", "prof.ren_title": "Profil umbenennen",
        "prof.del_title": "Profil löschen", "prof.del_msg": "Profil „{0}“ löschen? Dies kann nicht rückgängig gemacht werden.",
        "prof.copy_suffix": " Kopie", "dlg.ok": "OK", "dlg.cancel": "Abbrechen", "dlg.delete": "Löschen",
        "wheel.title": "Lenkrad-Einstellungen", "wheel.ffb": "Force Feedback",
        "wheel.overall": "Gesamtstärke der Effekte",
        "wheel.overall_h": "Auf 101% setzen, um die zentrale FFB-Totzone in den meisten Spielen zu beheben. (Spiel-Neustart nötig)",
        "wheel.spring": "Feder-Effekt", "wheel.spring_h": "Treiberbasierte Feder (empfohlen: 0%).",
        "wheel.damper": "Dämpfer-Effekt", "wheel.damper_h": "Treiberbasierte Dämpfung (empfohlen: 0%).",
        "wheel.center_cb": "Zentrierfeder in FFB-Spielen aktivieren",
        "wheel.center": "Zentrierfeder", "wheel.center_h": "Treiberbasierte Auto-Zentrierstärke.",
        "wheel.ramp": "Zentrier-Rampe", "wheel.ramp_h": "Formt nur die Zentrierfeder — ohne Wirkung, wenn sie 0 ist. Standard 7.",
        "wheel.steering": "Lenkung", "wheel.rotation": "Drehbereich",
        "wheel.rotation_h": "Maximaler Lenkdrehwinkel.",
        "ffb.title": "FORCE-FEEDBACK-TEST",
        "ffb.subtitle": "Testen Sie den FFB-Motor direkt. Am besten, wenn kein Spiel das Lenkrad nutzt.",
        "ffb.strength": "Teststärke", "ffb.strength_h": "Stärke für Druck-, Feder- und Sweep-Tests.",
        "ffb.push_l": "Nach links", "ffb.push_r": "Nach rechts",
        "ffb.spring": "Feder (Mitte)", "ffb.spring_stop": "Feder stoppen",
        "ffb.sweep": "Auto-Sweep", "ffb.sweep_stop": "Sweep stoppen",
        "ffb.advanced": "ERWEITERTE MOTORTESTS",
        "ffb.pulse_l": "Puls links", "ffb.pulse_r": "Puls rechts",
        "ffb.vibe_light": "Leichte Vibration", "ffb.vibe_med": "Mittlere Vibration",
        "ffb.vibe_fast": "Schnelles Rumpeln", "ffb.vibe_heavy": "Starke Vibration",
        "ffb.stop": "ALLE KRÄFTE STOPPEN",
        "input.title": "Eingangsmonitor", "input.led": "LED-Begrüßungstest",
        "input.wheel": "LENKRAD", "input.shifter": "SCHALTEINHEIT", "input.gear": "GANG  (H-SCHALTUNG)",
        "input.lpad": "LINKES PADDLE", "input.rpad": "RECHTES PADDLE",
        "input.face": "TASTEN", "input.dpad": "STEUERKREUZ", "input.horn": "HUPE",
        "input.led_nc": "Lenkrad nicht verbunden.",
        "input.led_g27": "LED-Test nur für G27 (DFGT hat keine RPM-LEDs).",
        "input.led_run": "LED-Begrüßung… (funktioniert, wenn der Treiber den Bericht durchlässt)",
        "about.title": "Geräteinfo", "about.settings": "Einstellungen",
        "about.status": "Status", "about.connected": "Verbunden", "about.not_connected": "Nicht verbunden",
        "about.model": "Modell", "about.hwid": "Hardware-ID", "about.axis": "Achsenauflösung",
        "about.ffb": "Force Feedback", "about.language": "Sprache", "about.theme": "Design",
        "about.theme_dark": "Dunkel", "about.theme_light": "Hell",
        "about.testmode": "Test-Gerätemodus", "about.testmode_h":
            "Aktives Lenkrad-Layout ohne angeschlossene Hardware umschalten. „Auto“ nutzt echte Erkennung.",
        "about.test_auto": "Auto (erkennen)", "about.footer":
            "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "HARDWARE-DIAGNOSE", "about.sec_sensor": "SENSOR- & FFB-SPEZIFIKATIONEN",
        "about.sec_sw": "SOFTWARE- & TREIBERSTATUS", "about.sec_credits": "MITWIRKENDE",
        "about.devmodel": "Gerätemodell", "about.interface": "Schnittstelle", "about.power": "Energiestatus",
        "about.tracking": "Tracking-System", "about.polling": "Max. Abtastrate",
        "about.opmode": "Betriebsmodus", "about.api": "API-Hook", "about.hub": "Hub-Version",
        "about.author": "Autor", "about.sec_about": "ÜBER", "about.repo": "GitHub-Repository", "about.license": "Lizenz: GPL-3.0", "about.disclaimer": "Nicht mit Logitech verbunden. Alle Marken gehören ihren jeweiligen Eigentümern.", "about.power_active": "{0} / Aktiv",
        "about.power_standby": "Standby / Getrennt",
        "about.opmode_active": "Nativer Erweiterter Modus (Entsperrt)",
        "about.opmode_idle": "Leerlauf / Warte auf Gerät",
        "about.tray": "In Infobereich minimieren",
        "about.tray_h": "Wenn aktiv, blendet die Minimieren-Taste die App in den Infobereich (ausgeblendete Symbole) aus.",
        "tray.show": "Anzeigen", "tray.quit": "Beenden",
        "apply.ok_title": "Angewendet", "apply.ok_body": "Einstellungen auf das Lenkrad angewendet.",
        "ui.presets": "VOREINSTELLUNGEN", "ui.presets_sub": "Vor dem Spielstart auswählen.",
        "ui.add_profile": "+  Spielprofil hinzufügen", "ui.autoload": "Beim Verbinden automatisch laden",
        "ui.telemetry": "LIVE-TELEMETRIE", "ui.center": "Zentrieren",
        "ui.apply": "ANWENDEN", "conn.connected": "Verbunden", "conn.test": "Testmodus",
        "tab.wheel": "LENKRAD", "tab.ffb": "FFB-TEST", "tab.input": "EINGABE-MONITOR",
        "tab.lut": "LUT",
        "lut.sec": "FFB-NACHBEARBEITUNG", "lut.enable": "FFB-Nachbearbeitung aktivieren",
        "lut.enable_h": "Leitet das Force Feedback des Spiels durch die gewählte LUT-Kurve in allen Spielen (über den dinput8-Proxy).",
        "lut.select": "LUT-Kurve", "lut.import": "LUT importieren", "lut.delete": "LUT löschen", "lut.none": "(keine)",
        "lut.del_title": "LUT löschen", "lut.del_body": "LUT-Datei “{}” von der Festplatte löschen? Das kann nicht rückgängig gemacht werden.",
        "lut.deleted": "LUT gelöscht",
        "lut.empty": "Noch keine LUT-Dateien. Klicke auf „LUT importieren“.",
        "lut.warn": "⚠  Nicht in Online-Spielen verwenden. Wenn doch, auf eigenes Risiko!",
        "lut.global_notice": "LUT wird pro Spiel festgelegt. Erstelle ein Spielprofil (＋ in Presets), wähle seine .exe und dann hier seine LUT. Das Global-Profil wendet keine LUT an.",
        "lut.axis_in": "Eingang", "lut.axis_out": "Ausgang",
        "lut.imported": "LUT importiert", "lut.import_fail": "LUT konnte nicht importiert werden",
        "lut.game": "SPIEL", "lut.exe": "Spiel-Datei", "lut.exe_pick": "Wählen…",
        "lut.exe_none": "Kein Spiel gewählt",
        "prof.edit": "Profil bearbeiten", "prof.exe": "Spiel-Datei",
        "prof.exe_hint": "Bei UE-Launcher-Spielen die echte ...-Shipping.exe wählen, falls nicht automatisch gefunden.",
        "prof.logo": "Symbol (aus einer .exe)", "prof.logo_pick": "Symbol wählen…",
        "prof.exe_pick": "Spiel wählen…", "prof.name_lbl": "Profilname",
        "proxy.installed": "Proxy installiert", "proxy.removed": "Proxy entfernt",
        "proxy.installed_body": "dinput8.dll neben dem Spiel platziert.",
        "proxy.locked": "Proxy deaktiviert", "proxy.locked_body": "Wird beim Schließen des Spiels entfernt.",
        "proxy.removed": "Proxy entfernt", "proxy.removed_body": "dinput8.dll aus dem Spielordner entfernt.",
        "proxy.err_noexe": "Spiel-Exe nicht gefunden.", "proxy.err_arch": "Nicht unterstützte Architektur.",
        "proxy.err_asset": "Gebündelte Proxy-DLL fehlt (assets/proxy).",
        "proxy.err_write": "DLL konnte nicht geschrieben werden (läuft das Spiel? Ordner beschreibbar?).",
        "proxy.foreign_title": "Vorhandene dinput8.dll",
        "proxy.foreign_body": "In diesem Ordner liegt bereits eine nicht von LWH installierte dinput8.dll (anderer Mod/Wrapper). Überschreiben?",
        "tab.info": "INFO", "wheel.sec_ffb": "FORCE FEEDBACK", "wheel.sec_steer": "LENKEINSTELLUNGEN",
        "ffb.reset": "Treiber-FFB zurücksetzen", "ffb.reset_h": "Löscht alle von dieser App geschriebenen FFB-Registry-Werte.",
        "set.title": "EINSTELLUNGEN", "set.appearance": "DARSTELLUNG", "set.general": "ALLGEMEIN", "set.testing": "TEST",
        "set.theme": "Design", "set.language": "Sprache", "set.tray": "In den Infobereich minimieren",
        "set.tray_h": "Wenn aktiv, blendet die Minimieren-Taste die App in den Infobereich (ausgeblendete Symbole) aus.",
        "set.devmode": "Geräteerkennungsmodus",
        "set.devmode_h": "Layout ohne Hardware erzwingen. „Auto“ nutzt echte Erkennung.", "set.ui_scale": "Oberflächenskalierung", "set.ui_scale_h": "Vergrößert die gesamte Oberfläche auf hochauflösenden (1440p/4K) Bildschirmen. Wird nach einem Neustart der App wirksam.", "set.restart_hint": "Starten Sie die App neu, um die neue Skalierung anzuwenden.",
        "info.opmode_active": "Nativer Erweiterter Modus (Entsperrt)", "info.opmode_idle": "Leerlauf / Getrennt",
        "info.active": "Aktiv", "info.standby": "Standby",
    },
}
LANG_ORDER = [("tr", "Türkçe"), ("en", "English"), ("de", "Deutsch")]
CURRENT_LANG = "en"


def tr(key):
    d = LANG.get(CURRENT_LANG) or LANG["en"]
    return d.get(key, LANG["en"].get(key, key))


state = {"steer": STEER_CENTER, "steer_norm": 0.0, "throttle": 0, "brake": 0,
         "clutch": 0, "raw": [0] * 16, "connected": False}
dev = None
dev_lock = threading.Lock()
running = True
active_profile = DEVICE_PROFILES["DFGT"]
test_override = None
main_window = None


PROFILE_DEFAULTS = {"angle": 900, "di_gain": 101, "di_spring": 0, "di_damper": 0,
                    "di_center": 0, "di_ramp": DEFAULT_RAMP, "di_persist": False,
                    "exe_path": "", "logo_exe": "", "lut_file": "", "lut_enabled": False}


def _active_ramp():
    """Ramp of the currently selected profile (used by test/center actions so
    they match what APPLY sends)."""
    try:
        return int(global_settings["profiles"][global_settings["selected_profile"]]
                   .get("di_ramp", DEFAULT_RAMP))
    except Exception:
        return DEFAULT_RAMP


def load_settings():
    base = {"theme": "dark", "language": "en", "last_device": None, "auto_load": False,
            "minimize_to_tray": False, "win_w": 1366, "win_h": 720, "last_tab": "wheel", "ui_scale": 100,
            "profiles": {"Global": dict(PROFILE_DEFAULTS)},
            "selected_profile": "Global"}
    try:
        with open(SETTINGS_FILE) as f:
            v = json.load(f)
        for k in base:
            v.setdefault(k, base[k])
        # Backfill keys added in later versions (e.g. di_ramp) so a settings
        # file written by an older build keeps its old behaviour instead of
        # hitting a missing key.
        try:
            for p in v["profiles"].values():
                for k, dv in PROFILE_DEFAULTS.items():
                    p.setdefault(k, dv)
        except Exception:
            pass
        return v
    except Exception:
        return base


global_settings = load_settings()
CURRENT_LANG = global_settings.get("language", "en")
if CURRENT_LANG not in LANG:
    CURRENT_LANG = "en"


def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(global_settings, f, indent=2)
    except Exception:
        pass


def set_language(code):
    global CURRENT_LANG
    if code not in LANG:
        return
    CURRENT_LANG = code
    global_settings["language"] = code
    save_settings()


def set_test_override(key):
    global active_profile, test_override
    test_override = key
    if key in DEVICE_PROFILES:
        active_profile = DEVICE_PROFILES[key]


def _detect_profile():
    try:
        pids = [d["product_id"] for d in hid.enumerate(VID)]
    except Exception:
        return None, None
    if DEVICE_PROFILES["DFGT"]["pid_native"] in pids:
        return DEVICE_PROFILES["DFGT"], DEVICE_PROFILES["DFGT"]["pid_native"]
    if DEVICE_PROFILES["G27"]["pid_native"] in pids:
        return DEVICE_PROFILES["G27"], DEVICE_PROFILES["G27"]["pid_native"]
    if PID_COMPAT in pids:
        return DEVICE_PROFILES["DFGT"], PID_COMPAT
    return None, None


# Extended command 09 mode bytes (classic Logitech protocol).
# The byte selects which wheel identity the device re-enumerates as:
#   0x00 DF-EX | 0x01 DFP | 0x02 G25 | 0x03 DFGT | 0x04 G27 | 0x05 G29
# Sending the wrong one makes a G27 come back as a DFGT (wrong PID, no LEDs,
# no clutch), so the byte must match the wheel we actually want.
NATIVE_MODE_BYTE = {"DFEX": 0x00, "DFP": 0x01, "G25": 0x02, "DFGT": 0x03, "G27": 0x04}


def _switch_mode(mode_byte):
    h = hid.device(); h.open(VID, PID_COMPAT)
    h.write([0x00, 0xF8, 0x0A, 0, 0, 0, 0, 0]); time.sleep(0.1)      # revert on USB reset
    h.write([0x00, 0xF8, 0x09, mode_byte, 0x01, 0, 0, 0]); time.sleep(0.1)  # switch + detach
    h.close()


def _wait_for_pid(pid, timeout=4.0):
    """Poll until the wheel re-enumerates with `pid` (instead of a blind sleep)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pid in [d["product_id"] for d in hid.enumerate(VID)]:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def ensure_native_mode():
    try:
        devs = hid.enumerate(VID)
        present = [d["product_id"] for d in devs]
        if DEVICE_PROFILES["DFGT"]["pid_native"] in present: return
        if DEVICE_PROFILES["G27"]["pid_native"] in present: return
        if PID_COMPAT not in present: return
        # In compat mode we don't know which wheel this is; use the reported
        # product name as a hint, then try the other one as a fallback.
        name = ""
        for d in devs:
            if d["product_id"] == PID_COMPAT:
                name = (d.get("product_string") or "").lower(); break
        order = ["DFGT", "G27"] if "gt" in name else ["G27", "DFGT"]
        for key in order:
            try:
                _switch_mode(NATIVE_MODE_BYTE[key])
            except Exception:
                continue
            if _wait_for_pid(DEVICE_PROFILES[key]["pid_native"]):
                return
    except Exception:
        pass


def open_device():
    global dev, active_profile
    prof, pid = _detect_profile()
    if prof is None:
        dev = None; state["connected"] = False; return False
    try:
        d = hid.device(); d.open(VID, pid); d.set_nonblocking(1)
        dev = d; active_profile = prof; state["connected"] = True
        for k, v in DEVICE_PROFILES.items():
            if v is prof and global_settings.get("last_device") != k:
                global_settings["last_device"] = k; save_settings(); break
        return True
    except Exception:
        dev = None; state["connected"] = False; return False


class Poller(QThread):
    def run(self):
        global dev
        while running:
            if dev is None:
                ensure_native_mode(); open_device(); time.sleep(1); continue
            try:
                data = None
                with dev_lock:
                    while True:
                        chunk = dev.read(64)
                        if chunk: data = chunk
                        else: break
                if data and len(data) >= 8:
                    sd = active_profile["steer"]; fmt = sd.get("fmt", "lohi")
                    if fmt == "hilo6":
                        steer = (data[sd["hi"]] << 6) | (data[sd["lo"]] >> 2)
                    elif fmt == "single":
                        steer = data[sd["lo"]]
                    else:
                        steer = data[sd["lo"]] | ((data[sd["hi"]] & sd.get("himask", 0x3F)) << 8)
                    state["steer"] = steer
                    state["steer_norm"] = max(-1.0, min(1.0, (steer - sd["center"]) / sd["half"]))
                    inv = active_profile["pedal_invert"]
                    def _ax(b):
                        if b is None or b >= len(data): return 0
                        return (255 - data[b]) if inv else data[b]
                    state["throttle"] = _ax(active_profile["throttle"])
                    state["brake"] = _ax(active_profile["brake"])
                    state["clutch"] = _ax(active_profile["clutch"])
                    state["raw"] = list(data[:16]); state["connected"] = True
            except Exception:
                state["connected"] = False; dev = None
            time.sleep(0.005)


def decode_buttons_dfgt(raw):
    p = set(); b0 = raw[0]; b1 = raw[1]; b2 = raw[2]
    if not any(raw): return p
    if b0 & 0x10: p.add("sh_x")
    if b0 & 0x20: p.add("sh_square")
    if b0 & 0x40: p.add("sh_circle")
    if b0 & 0x80: p.add("sh_triangle")
    hat = b0 & 0x0F
    hatmap = {0: ["up"], 1: ["up", "right"], 2: ["right"], 3: ["down", "right"],
              4: ["down"], 5: ["down", "left"], 6: ["left"], 7: ["up", "left"]}
    for d in hatmap.get(hat, []): p.add("dpad_" + d)
    if b1 & 0x01: p.add("paddle_right")
    if b1 & 0x02: p.add("paddle_left")
    if b1 & 0x04: p.add("r2")
    if b1 & 0x08: p.add("l2")
    if b1 & 0x10: p.add("select")
    if b1 & 0x20: p.add("start")
    if b1 & 0x40: p.add("r3")
    if b1 & 0x80: p.add("l3")
    if b2 & 0x01: p.add("up")            # gear paddle up
    if b2 & 0x02: p.add("dn")            # gear paddle down
    if b2 & 0x04: p.add("dial_enter")    # dial press (enter)
    if b2 & 0x08: p.add("plus")
    if b2 & 0x10: p.add("dial_right")
    if b2 & 0x20: p.add("dial_left")
    if b2 & 0x40: p.add("minus")
    if b2 & 0x80: p.add("horn")
    b3 = raw[3] if len(raw) > 3 else 0
    if b3 & 0x01: p.add("ps")
    return p


def decode_buttons_g27(raw):
    p = set(); b0 = raw[0]; b1 = raw[1]; b2 = raw[2]
    b3 = raw[3] if len(raw) > 3 else 0; b10 = raw[10] if len(raw) > 10 else 0
    if not any(raw): return p
    hat = b0 & 0x0F
    hatmap = {0: ["up"], 1: ["up", "right"], 2: ["right"], 3: ["down", "right"],
              4: ["down"], 5: ["down", "left"], 6: ["left"], 7: ["up", "left"]}
    for d in hatmap.get(hat, []): p.add("dpad_" + d)
    if b0 & 0x10: p.add("red_1")
    if b0 & 0x20: p.add("red_2")
    if b0 & 0x40: p.add("red_3")
    if b0 & 0x80: p.add("red_4")
    if b1 & 0x01: p.add("paddle_right")
    if b1 & 0x02: p.add("paddle_left")
    if b1 & 0x04: p.add("wheel_rt")
    if b1 & 0x08: p.add("wheel_lt")
    if b1 & 0x10: p.add("gear_1")
    if b1 & 0x20: p.add("gear_2")
    if b1 & 0x40: p.add("gear_3")
    if b1 & 0x80: p.add("gear_4")
    if b2 & 0x01: p.add("gear_5")
    if b2 & 0x02: p.add("gear_6")
    if b2 & 0x04: p.add("gear_r")
    if b2 & 0x08: p.add("sh_triangle")
    if b2 & 0x10: p.add("sh_square")
    if b2 & 0x20: p.add("sh_x")
    if b2 & 0x40: p.add("sh_circle")
    if b2 & 0x80: p.add("wheel_rm")
    if b3 & 0x01: p.add("wheel_lm")
    if b3 & 0x02: p.add("wheel_rb")
    if b10 & 0x01: p.add("wheel_lb")
    return p


def decode_buttons(raw):
    if active_profile is DEVICE_PROFILES["G27"]:
        return decode_buttons_g27(raw)
    return decode_buttons_dfgt(raw)


def rotation_cmd(deg): return [0xF8, 0x81, deg & 0xFF, (deg >> 8) & 0xFF, 0, 0, 0]
def autocenter_cmd(pct, ramp=DEFAULT_RAMP, ramp2=None):
    """Autocenter spring. Bytes 2/3 are the clockwise / counter-clockwise ramp
    slope (0-15); pass ramp2 for an asymmetric feel. Byte 4 is strength."""
    r1 = max(0, min(15, int(ramp)))
    r2 = r1 if ramp2 is None else max(0, min(15, int(ramp2)))
    p = max(0, min(100, int(pct)))
    return [0xFE, 0x0D, r1, r2, int(p * 255 / 100), 0, 0]
# Classic Logitech FFB opcode = (slot mask << 4) | command.
# Slot 0 carries the game's constant force; slots 1-3 are free (spring/damper/
# periodic). Stopping ALL slots (0xF3) would also kill the game's own forces,
# so per-slot stop exists as well.
CMD_DOWNLOAD_PLAY = 0x01
CMD_STOP = 0x03
SLOT0_MASK = 0x10
ALL_SLOTS_MASK = 0xF0


def _slot_mask(slot):
    return (1 << max(0, min(3, int(slot)))) << 4


def stop_slot_cmd(slot=0):
    """Stop one slot only, leaving the other three untouched."""
    return [_slot_mask(slot) | CMD_STOP, 0, 0, 0, 0, 0, 0]


def constant_force_cmd(direction, pct):
    pct = max(0, min(100, int(pct))); span = int(pct * 127 / 100)
    val = 0x80 + span if direction == "left" else (0x80 - span if direction == "right" else 0x80)
    return [SLOT0_MASK | CMD_DOWNLOAD_PLAY, 0x00, max(0, min(255, val)), 0, 0, 0, 0]
def stop_forces_cmd(): return [ALL_SLOTS_MASK | CMD_STOP, 0, 0, 0, 0, 0, 0]


def ffb_write(cmd):
    if dev is None: return
    try:
        with dev_lock: dev.write([0x00] + cmd)
    except Exception: pass


def update_registry_ffb(gain, spring, damper, center, persist, angle):
    if winreg is None: return
    center = int(center)
    persist_on = 1 if (persist and center > 0) else 0
    if not persist_on:
        center = 0
    for pid in active_profile["registry_pids"]:
        path = rf"Software\Logitech\Gaming Software\DriverSettings\{pid}"
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
            winreg.SetValueEx(key, "OverallStrength", 0, winreg.REG_DWORD, int(gain) * 100)
            winreg.SetValueEx(key, "SpringStrength", 0, winreg.REG_DWORD, int(spring) * 100)
            winreg.SetValueEx(key, "DamperStrength", 0, winreg.REG_DWORD, int(damper) * 100)
            winreg.SetValueEx(key, "MapDefault", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ForceEnabled", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "CenteringSpring", 0, winreg.REG_DWORD, center * 100)
            winreg.SetValueEx(key, "PersistentCenteringSpring", 0, winreg.REG_DWORD, persist_on)
            winreg.SetValueEx(key, "Turn", 0, winreg.REG_DWORD, int(angle))
            winreg.CloseKey(key)
        except Exception: pass


def restore_ffb_defaults():
    names = ["OverallStrength", "SpringStrength", "DamperStrength",
             "CenteringSpring", "PersistentCenteringSpring", "MapDefault", "Turn"]
    all_pids = ALL_REGISTRY_PIDS
    if winreg is not None:
        for pid in all_pids:
            path = rf"Software\Logitech\Gaming Software\DriverSettings\{pid}"
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
                for n in names:
                    try: winreg.DeleteValue(key, n)
                    except Exception: pass
                try: winreg.SetValueEx(key, "ForceEnabled", 0, winreg.REG_DWORD, 1)
                except Exception: pass
                winreg.CloseKey(key)
            except Exception: pass
    ffb_write(autocenter_cmd(0)); ffb_write(stop_forces_cmd())


def _led_set(s): ffb_write([0xF8, 0x12, s & 0x1F, 0, 0, 0, 0x01])
def led_greeting():
    def run():
        seq = [0, 1, 3, 7, 15, 31]
        try:
            for _ in range(2):
                for s in seq: _led_set(s); time.sleep(0.07)
                for s in reversed(seq): _led_set(s); time.sleep(0.07)
            _led_set(31); time.sleep(0.35); _led_set(0)
        except Exception: pass
    threading.Thread(target=run, daemon=True).start()


def theme_col(dark, light):
    return QColor(dark if isDarkTheme() else light)


def _lerp(cur, target, factor):
    d = target - cur
    if abs(d) < 0.0008:
        return target
    return cur + d * factor


class WheelWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(280, 280)
        self.angle = 0.0
        self.target = 0.0
        self._src = QPixmap(WHEEL_PNG) if os.path.exists(WHEEL_PNG) else QPixmap()
        self._colored = self._detect_colored(self._src)
        self._cache = None
        self._cache_key = None

    def _detect_colored(self, px):
        # True for a realistic/colour photo (draw as-is); False for a flat
        # near-white silhouette (tint it so it shows on the dark canvas).
        if px.isNull():
            return False
        img = px.toImage().convertToFormat(QImage.Format_ARGB32)
        w, h = img.width(), img.height()
        if w == 0 or h == 0:
            return False
        step = max(1, min(w, h) // 48)
        for y in range(0, h, step):
            for x in range(0, w, step):
                c = img.pixelColor(x, y)
                if c.alpha() < 24:
                    continue
                r, g, b = c.red(), c.green(), c.blue()
                if max(r, g, b) < 210 or (max(r, g, b) - min(r, g, b)) > 28:
                    return True
        return False

    def _tinted(self, px):
        key = (px, isDarkTheme(), self._colored)
        if self._cache_key == key and self._cache is not None:
            return self._cache
        if self._src.isNull():
            self._cache = None; self._cache_key = key; return None
        pm = self._src.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if self._colored:
            # real photo -> no tint, just a crisp scaled copy
            self._cache = pm; self._cache_key = key
            return pm
        col = theme_col("#9aa4b6", "#2b3440")
        out = QPixmap(pm.size()); out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(0, 0, pm)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(out.rect(), col); p.end()
        self._cache = out; self._cache_key = key
        return out

    def animate(self, factor=0.30):
        new = _lerp(self.angle, self.target, factor)
        if new != self.angle:
            self.angle = new; self.update(); return True
        return False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        side = max(1, min(self.width(), self.height()))
        dpr = float(self.devicePixelRatioF()) if hasattr(self, "devicePixelRatioF") else 1.0
        ss = 1.5
        px = max(1, int(side * dpr * ss))
        pm = self._tinted(px)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if pm:
            disp = pm.width() / (dpr * ss)
            p.translate(cx, cy); p.rotate(self.angle)
            p.drawPixmap(QRectF(-disp / 2, -disp / 2, disp, disp), pm, QRectF(pm.rect()))
        else:
            p.setPen(QPen(theme_col("#9aa4b6", "#2b3440"), 14))
            r = side / 2 - 16
            p.drawEllipse(QPointF(cx, cy), r, r)


class PedalBar(QWidget):
    def __init__(self, label, color):
        super().__init__()
        self.value = 0.0; self.disp = 0.0
        self.label = label; self.color = QColor(color)
        self.enabled_axis = True
        self.setMinimumSize(58, 150)

    def animate(self, factor=0.35):
        new = _lerp(self.disp, self.value, factor)
        if new != self.disp:
            self.disp = new; self.update(); return True
        return False

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w = self.width(); h = self.height()
        bw = 46; bx = (w - bw) / 2; by = 18; bh = h - 56
        p.setPen(Qt.NoPen); p.setBrush(theme_col("#333333", "#e2e2e2"))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 8, 8)
        if self.enabled_axis:
            fh = bh * max(0.0, min(1.0, self.disp))
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(bx, by + bh - fh, bw, fh), 8, 8)
        f = QFont(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.setPen(theme_col("#a0a0a0", "#5f5f5f"))
        p.drawText(QRectF(0, 0, w, 16), Qt.AlignCenter, self.label)
        if self.enabled_axis:
            p.setPen(self.color); f.setPointSize(9); p.setFont(f)
            p.drawText(QRectF(0, h - 20, w, 18), Qt.AlignCenter, f"{int(self.disp * 100)}%")
        else:
            p.setPen(theme_col("#666666", "#b0b0b0")); f.setPointSize(9); p.setFont(f)
            p.drawText(QRectF(0, h - 20, w, 18), Qt.AlignCenter, tr("home.na"))


class InputMonitor(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(560, 462)
        self.pressed = set()

    def _key(self, p, x, y, w, h, label, on, r=9, circle=False, shape=None, col=None):
        acc = QColor(ACCENT)
        base = theme_col("#2f2f2f", "#ffffff"); stroke = theme_col("#393939", "#e4e4e4")
        txt = theme_col("#f0f0f0", "#1b1b1b")
        p.setBrush(acc if on else base); p.setPen(QPen(acc if on else stroke, 1.4))
        rect = QRectF(x, y, w, h)
        if circle: p.drawEllipse(rect)
        else: p.drawRoundedRect(rect, r, r)
        if shape:
            self._shape(p, x + w / 2, y + h / 2, shape, col or "#f0f0f0", on)
        elif label:
            p.setPen(QColor("#1c1c1c") if on else txt)
            f = QFont(); f.setBold(True); f.setPointSize(9); p.setFont(f)
            p.drawText(rect, Qt.AlignCenter, label)

    def _shape(self, p, cx, cy, shape, col, on):
        c = QColor("#1c1c1c") if on else QColor(col)
        p.setBrush(c); p.setPen(Qt.NoPen); s = 7
        if shape == "triangle":
            p.drawPolygon(QPolygonF([QPointF(cx, cy - s), QPointF(cx - s, cy + s), QPointF(cx + s, cy + s)]))
        elif shape == "square":
            p.drawRect(QRectF(cx - s, cy - s, 2 * s, 2 * s))
        elif shape == "circle":
            p.drawEllipse(QPointF(cx, cy), s, s)
        elif shape == "x":
            p.setPen(QPen(c, 3))
            p.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
            p.drawLine(QPointF(cx - s, cy + s), QPointF(cx + s, cy - s))
        elif shape in ("arrow_up", "arrow_down", "arrow_left", "arrow_right"):
            a = 5.5
            if shape == "arrow_up":
                pts = [(cx, cy - a), (cx - a, cy + a), (cx + a, cy + a)]
            elif shape == "arrow_down":
                pts = [(cx, cy + a), (cx - a, cy - a), (cx + a, cy - a)]
            elif shape == "arrow_left":
                pts = [(cx - a, cy), (cx + a, cy - a), (cx + a, cy + a)]
            else:
                pts = [(cx + a, cy), (cx - a, cy - a), (cx - a, cy + a)]
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))

    def _section(self, p, x, y, text):
        muted = theme_col("#a0a0a0", "#5f5f5f")
        f = QFont(); f.setBold(True); f.setPointSize(8); p.setFont(f)
        p.setPen(muted); p.drawText(QRectF(x, y, 260, 14), Qt.AlignLeft, text)

    def _face_buttons(self, p, cx, cy, P, d=28, sp=28):
        h = d / 2
        mc = theme_col("#f0f0f0", "#1b1b1b")   # black/white per theme (no color)
        self._key(p, cx - h, cy - sp - h, d, d, "", "sh_triangle" in P, circle=True, shape="triangle", col=mc)
        self._key(p, cx - sp - h, cy - h, d, d, "", "sh_square" in P, circle=True, shape="square", col=mc)
        self._key(p, cx + sp - h, cy - h, d, d, "", "sh_circle" in P, circle=True, shape="circle", col=mc)
        self._key(p, cx - h, cy + sp - h, d, d, "", "sh_x" in P, circle=True, shape="x", col=mc)

    def _dpad(self, p, cx, cy, P, s=28, g=5):
        txt = theme_col("#f0f0f0", "#1b1b1b").name()
        defs = [("dpad_up", "arrow_up", cx, cy - (s + g)),
                ("dpad_down", "arrow_down", cx, cy + (s + g)),
                ("dpad_left", "arrow_left", cx - (s + g), cy),
                ("dpad_right", "arrow_right", cx + (s + g), cy)]
        for key, sh, bx, by in defs:
            self._key(p, bx - s / 2, by - s / 2, s, s, "", key in P, r=7, shape=sh, col=txt)

    def _pill(self, p, x, y, w, h, label, on, fs=9):
        self._key(p, x, y, w, h, label, on, r=h / 2)

    def _gt_center(self, p, cx, cy, r):
        ring = theme_col("#11151f", "#dfe3ea"); inner = theme_col("#05070b", "#10131a")
        edge = theme_col("#2a3142", "#c2c7d2")
        p.setPen(QPen(edge, 2)); p.setBrush(ring)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(Qt.NoPen); p.setBrush(inner)
        p.drawEllipse(QPointF(cx, cy), r - 6, r - 6)
        f = QFont(); f.setBold(True); f.setItalic(True); f.setPointSize(max(1, int(r * 0.62)))
        p.setFont(f); p.setPen(QColor("#f3f4f6"))
        p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, "GT")

    def _dial(self, p, cx, cy, r, on=False):
        base = theme_col("#222a3a", "#eef0f4"); edge = theme_col("#3a4256", "#c8ccd6")
        red = QColor("#e0463c"); tick = theme_col("#5a6276", "#aeb4c2")
        p.setPen(QPen(edge, 2)); p.setBrush(base)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(QPen(red, 3)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r - 5, r - 5)
        p.setPen(QPen(tick, 1.4))
        for i in range(24):
            a = math.radians(i * 15.0)
            r1 = r - 9; r2 = r - 13
            p.drawLine(QPointF(cx + r1 * math.sin(a), cy - r1 * math.cos(a)),
                       QPointF(cx + r2 * math.sin(a), cy - r2 * math.cos(a)))
        cr = r * 0.46
        acc = QColor(ACCENT)
        p.setPen(QPen(theme_col("#4a5266", "#b9bec9"), 1.5))
        p.setBrush(acc if on else theme_col("#2c3446", "#ffffff"))
        p.drawEllipse(QPointF(cx, cy), cr, cr)
        f = QFont(); f.setBold(True); f.setPointSize(max(1, int(cr * 0.95))); p.setFont(f)
        p.setPen(QColor("#1c1c1c") if on else theme_col("#e6e8ee", "#2a2f3a"))
        p.drawText(QRectF(cx - cr, cy - cr, 2 * cr, 2 * cr), Qt.AlignCenter, "\u21B5")
        p.setBrush(red); p.setPen(Qt.NoPen); a = 6
        lx = cx - r - 8; rx = cx + r + 8
        p.drawPolygon(QPolygonF([QPointF(lx + a, cy - a), QPointF(lx - a, cy), QPointF(lx + a, cy + a)]))
        p.drawPolygon(QPolygonF([QPointF(rx - a, cy - a), QPointF(rx + a, cy), QPointF(rx - a, cy + a)]))

    def _paint_g27(self, p, P, chan):
        D = 38
        self._section(p, 20, 8, tr("input.wheel"))
        self._pill(p, 52, 30, 176, 26, tr("input.lpad"), "paddle_left" in P)
        self._pill(p, 332, 30, 176, 26, tr("input.rpad"), "paddle_right" in P)
        lcx, rcx = 137, 423
        for i, k in enumerate(("wheel_lt", "wheel_lm", "wheel_lb")):
            self._key(p, lcx - D / 2, 72 + i * 46, D, D, ["L1", "L2", "L3"][i], k in P, circle=True)
        for i, k in enumerate(("wheel_rt", "wheel_rm", "wheel_rb")):
            self._key(p, rcx - D / 2, 72 + i * 46, D, D, ["R1", "R2", "R3"][i], k in P, circle=True)
        p.setPen(QPen(chan, 1)); p.drawLine(QPointF(30, 226), QPointF(530, 226))
        self._section(p, 20, 240, tr("input.shifter"))
        # face + d-pad clusters centred symmetrically over the 1-2-3-4 row
        # (row centre = 164), pushed down to clear the section label.
        self._face_buttons(p, 92, 326, P, d=36, sp=36)
        self._dpad(p, 236, 326, P, s=34, g=6)
        for i in range(4):
            cxr = 80 + i * 56
            self._key(p, cxr - D / 2, 416, D, D, str(i + 1), f"red_{i+1}" in P, circle=True)
        self._section(p, 330, 240, tr("input.gear"))
        sepc = theme_col("#2f3645", "#d2d6de")
        p.setPen(QPen(sepc, 1)); p.drawLine(QPointF(312, 262), QPointF(312, 438))
        gx = [346, 408, 470]; rx = 516          # R: between 6 and an even step
        gt_c, gb_c = 300, 384; gm = (gt_c + gb_c) // 2
        p.setPen(QPen(chan, 6)); p.setBrush(Qt.NoBrush)
        for x in gx:
            p.drawLine(QPointF(x, gt_c), QPointF(x, gb_c))
        p.drawLine(QPointF(gx[0], gm), QPointF(rx, gm))
        p.drawLine(QPointF(rx, gm), QPointF(rx, gb_c))
        tops = ["1", "3", "5"]; bots = ["2", "4", "6"]
        for i, x in enumerate(gx):
            self._key(p, x - D / 2, gt_c - D / 2, D, D, tops[i], f"gear_{tops[i]}" in P, circle=True)
            self._key(p, x - D / 2, gb_c - D / 2, D, D, bots[i], f"gear_{bots[i]}" in P, circle=True)
        self._key(p, rx - D / 2, gb_c - D / 2, D, D, "R", "gear_r" in P, circle=True)

    def _paint_dfgt(self, p, P, chan):
        self._pill(p, 40, 14, 152, 24, tr("input.lpad"), "paddle_left" in P)
        self._pill(p, 368, 14, 152, 24, tr("input.rpad"), "paddle_right" in P)
        self._pill(p, 92, 58, 60, 26, "L2", "l2" in P)
        self._pill(p, 408, 58, 60, 26, "R2", "r2" in P)
        self._pill(p, 166, 96, 50, 28, "L3", "l3" in P)
        self._key(p, 340, 94, 30, 30, "R3", "r3" in P, circle=True)
        self._dpad(p, 113, 166, P)
        self._gt_center(p, 270, 165, 48)
        self._face_buttons(p, 425, 166, P, d=28, sp=31)
        self._pill(p, 504, 148, 46, 26, "DN", "dn" in P)
        self._pill(p, 504, 186, 46, 26, "UP", "up" in P)
        self._key(p, 98, 238, 30, 30, "+", "plus" in P, circle=True)
        self._key(p, 98, 284, 30, 30, "\u2212", "minus" in P, circle=True)
        self._pill(p, 248, 238, 44, 28, "PS", "ps" in P)
        self._pill(p, 196, 288, 66, 26, "SELECT", "select" in P)
        self._pill(p, 284, 288, 60, 26, "START", "start" in P)
        self._dial(p, 426, 284, 38, "dial_enter" in P)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        P = self.pressed
        chan = theme_col("#393939", "#d0d0d0")
        if active_profile is DEVICE_PROFILES["G27"]:
            self._paint_g27(p, P, chan)
        else:
            self._paint_dfgt(p, P, chan)


def _name_dialog(title, default, parent):
    if MessageBoxBase is not None:
        try:
            class _D(MessageBoxBase):
                def __init__(self, p):
                    super().__init__(p)
                    self.t = SubtitleLabel(title, self)
                    self.edit = LineEdit(self); self.edit.setText(default)
                    self.edit.setClearButtonEnabled(True)
                    self.edit.setPlaceholderText(tr("prof.new_hint"))
                    self.viewLayout.addWidget(self.t)
                    self.viewLayout.addWidget(self.edit)
                    self.yesButton.setText(tr("dlg.ok"))
                    self.cancelButton.setText(tr("dlg.cancel"))
                    self.widget.setMinimumWidth(360)
            d = _D(parent)
            if d.exec():
                return d.edit.text().strip()
            return None
        except Exception:
            pass
    from PySide6.QtWidgets import QInputDialog
    txt, ok = QInputDialog.getText(parent, title, tr("prof.new_hint"), text=default)
    return txt.strip() if ok else None


def _preset_edit_dialog(title, cur_name, cur_exe, cur_logo, parent, allow_rename=True):
    """Combined profile editor: name + game exe + icon source.

    Returns (name, exe_path, logo_exe) or None if cancelled.
    logo_exe defaults to exe_path when the user didn't pick a separate icon.
    """
    if MessageBoxBase is None:
        # minimal fallback: just rename
        n = _name_dialog(title, cur_name, parent) if allow_rename else cur_name
        return (n, cur_exe, cur_logo) if n else None

    exe_filter = "Programs (*.exe);;All files (*.*)"

    class _D(MessageBoxBase):
        def __init__(self, p):
            super().__init__(p)
            self._exe = cur_exe or ""
            self._logo = cur_logo or ""
            self.t = SubtitleLabel(title, self)
            self.viewLayout.addWidget(self.t)

            self.name_lbl = CaptionLabel(tr("prof.name_lbl"))
            self.edit = LineEdit(self); self.edit.setText(cur_name)
            self.edit.setClearButtonEnabled(True)
            self.edit.setEnabled(allow_rename)
            self.viewLayout.addWidget(self.name_lbl)
            self.viewLayout.addWidget(self.edit)

            # game exe row
            self.exe_lbl = CaptionLabel(tr("prof.exe"))
            self.viewLayout.addWidget(self.exe_lbl)
            erow = QHBoxLayout(); erow.setSpacing(8)
            self.exe_field = LineEdit(self); self.exe_field.setReadOnly(True)
            self.exe_field.setText(os.path.basename(self._exe) if self._exe else "")
            self.exe_field.setPlaceholderText(tr("lut.exe_none"))
            self.btn_exe = PushButton(tr("prof.exe_pick"))
            self.btn_exe.clicked.connect(self._pick_exe)
            erow.addWidget(self.exe_field, 1); erow.addWidget(self.btn_exe)
            self.viewLayout.addLayout(erow)
            self.exe_hint = CaptionLabel(tr("prof.exe_hint"))
            self.exe_hint.setWordWrap(True)
            self.viewLayout.addWidget(self.exe_hint)

            # icon row (with preview)
            self.logo_lbl = CaptionLabel(tr("prof.logo"))
            self.viewLayout.addWidget(self.logo_lbl)
            lrow = QHBoxLayout(); lrow.setSpacing(8)
            self.preview = QLabel(); self.preview.setFixedSize(24, 24)
            self.preview.setAlignment(Qt.AlignCenter)
            self.logo_field = LineEdit(self); self.logo_field.setReadOnly(True)
            self.btn_logo = PushButton(tr("prof.logo_pick"))
            self.btn_logo.clicked.connect(self._pick_logo)
            lrow.addWidget(self.preview, 0)
            lrow.addWidget(self.logo_field, 1); lrow.addWidget(self.btn_logo)
            self.viewLayout.addLayout(lrow)

            self.yesButton.setText(tr("dlg.ok"))
            self.cancelButton.setText(tr("dlg.cancel"))
            self.widget.setMinimumWidth(420)
            self._refresh_logo()

        def _pick_exe(self):
            path, _ = QFileDialog.getOpenFileName(self, tr("prof.exe_pick"), "", exe_filter)
            if path:
                self._exe = path
                self.exe_field.setText(os.path.basename(path))
                if not self._logo:                 # default icon follows the game
                    self._refresh_logo()

        def _pick_logo(self):
            path, _ = QFileDialog.getOpenFileName(self, tr("prof.logo_pick"), "", exe_filter)
            if path:
                self._logo = path
                self._refresh_logo()

        def _refresh_logo(self):
            src = self._logo or self._exe
            self.logo_field.setText(os.path.basename(src) if src else "")
            ic = exe_icon(src)
            if ic is not None and not ic.isNull():
                self.preview.setPixmap(ic.pixmap(20, 20))
            else:
                self.preview.clear()

    d = _D(parent)
    if d.exec():
        name = d.edit.text().strip() if allow_rename else cur_name
        if not name:
            return None
        return (name, d._exe, d._logo)
    return None


def _confirm_dialog(title, body, parent):
    if MessageBox is not None:
        try:
            m = MessageBox(title, body, parent)
            m.yesButton.setText(tr("dlg.delete"))
            m.cancelButton.setText(tr("dlg.cancel"))
            return bool(m.exec())
        except Exception:
            pass
    from PySide6.QtWidgets import QMessageBox
    return QMessageBox.question(parent, title, body) == QMessageBox.Yes


_LAST_INFOBAR = None


def _defer_infobar(kind, *args, **kwargs):
    """Show an InfoBar on the next event-loop tick, replacing the previous one.

    Two problems are solved here:
    1. qfluentwidgets' InfoBar plays a slide animation on show; creating it in
       the middle of a signal/layout pass logs 'starting an animation without
       end value'. Deferring one tick lets the layout settle.
    2. Spamming APPLY (or clicking presets fast) used to STACK InfoBars until
       they overflowed the screen, which is exactly what triggers that error.
       We now close the previous InfoBar before showing a new one, so at most
       one is on screen at a time.
    """
    global _LAST_INFOBAR

    def _show():
        global _LAST_INFOBAR
        prev = _LAST_INFOBAR
        if prev is not None:
            try:
                prev.close()
            except Exception:
                pass
            _LAST_INFOBAR = None
        try:
            _LAST_INFOBAR = getattr(InfoBar, kind)(*args, **kwargs)
        except Exception:
            _LAST_INFOBAR = None
    try:
        QTimer.singleShot(0, _show)
    except Exception:
        _show()

# ====================================================================
#  CUSTOM UI  (3-column Control Hub layout, frameless window)
# ====================================================================

PANEL_BG_DARK = "#1b1e26"
PANEL_BG_LIGHT = "#f1f2f5"
HEADER_BG_DARK = "#1f232c"
HEADER_BG_LIGHT = "#ffffff"
SEP_DARK = "#262b35"
SEP_LIGHT = "#d9dce3"


def _accent_rgba(alpha):
    c = QColor(ACCENT); c.setAlpha(alpha); return c


def hub_qss():
    bg = PANEL_BG_DARK if isDarkTheme() else PANEL_BG_LIGHT
    hdr = HEADER_BG_DARK if isDarkTheme() else HEADER_BG_LIGHT
    sep = SEP_DARK if isDarkTheme() else SEP_LIGHT
    muted = "#8a93a6" if isDarkTheme() else "#5c6370"
    return f"""
    #ControlHub {{ background-color: {bg}; }}
    #Header {{ background-color: {hdr}; border-bottom: 1px solid {sep}; }}
    #vsep, #hsep {{ background-color: {sep}; border: none; }}
    #secHeaderLbl, #colHeaderLbl {{ color: {muted}; }}
    #applyBtn {{
        background-color: {ACCENT}; color: #14110c; border: none;
        border-radius: 5px; font-weight: 700; font-size: 13px;
        padding: 7px 22px;
    }}
    #applyBtn:hover {{ background-color: {QColor(ACCENT).lighter(112).name()}; }}
    #applyBtn:pressed {{ background-color: {QColor(ACCENT).darker(112).name()}; }}
    #centerBtn, #presetAdd, #resetBtn {{
        background-color: transparent; color: {QColor(ACCENT).name()};
        border: 1px solid {sep}; border-radius: 5px; font-weight: 600;
        padding: 5px 16px;
    }}
    #centerBtn:hover, #presetAdd:hover, #resetBtn:hover {{
        background-color: {_accent_rgba(26).name(QColor.HexArgb)};
        border: 1px solid {QColor(ACCENT).name()};
    }}
    #themeBtn {{ background: transparent; border: none; border-radius: 6px; }}
    #themeBtn:hover {{ background-color: {sep}; }}
    QToolTip {{ color: #e8eaed; background-color: #2a2f3a; border: 1px solid {sep}; }}
    """


def section_header(text):
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(9)
    bar = QFrame(); bar.setFixedSize(3, 14)
    bar.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")
    lbl = CaptionLabel(text)
    f = lbl.font(); f.setBold(True); f.setPointSize(9)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
    lbl.setFont(f)
    h.addWidget(bar); h.addWidget(lbl); h.addStretch(1)
    w._bar = bar; w._lbl = lbl
    return w


def column_header(text, button=None):
    """Tall accent-bar header used at the top of each column."""
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
    bar = QFrame(); bar.setFixedSize(3, 16)
    bar.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")
    lbl = StrongBodyLabel(text)
    f = lbl.font(); f.setBold(True); f.setPointSize(10)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
    lbl.setFont(f)
    h.addWidget(bar); h.addWidget(lbl); h.addStretch(1)
    if button is not None:
        h.addWidget(button)
    w._bar = bar; w._lbl = lbl
    return w


# --------------------------------------------------------------------
#  Small painted widgets
# --------------------------------------------------------------------
class WheelLogo(QWidget):
    def __init__(self, d=40):
        super().__init__()
        self.setFixedSize(d, d)
        self._src = QPixmap(WHEEL_PNG) if os.path.exists(WHEEL_PNG) else QPixmap()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        d = min(self.width(), self.height())
        if not self._src.isNull():
            pm = self._src.scaled(d, d, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            out = QPixmap(pm.size()); out.fill(Qt.transparent)
            q = QPainter(out); q.drawPixmap(0, 0, pm)
            q.setCompositionMode(QPainter.CompositionMode_SourceIn)
            q.fillRect(out.rect(), theme_col("#e6e6e6", "#3a4150")); q.end()
            p.drawPixmap((self.width() - out.width()) // 2, (self.height() - out.height()) // 2, out)
            return
        col = theme_col("#aab2c0", "#5a6270")
        cx = cy = d / 2.0; R = d / 2.0 - 3
        p.setPen(QPen(col, 2.4)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), R, R)
        p.setBrush(col); p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), 3.4, 3.4)
        p.setPen(QPen(col, 2.4))
        for ang in (90, 210, 330):
            a = math.radians(ang)
            p.drawLine(QPointF(cx, cy), QPointF(cx + R * math.cos(a), cy - R * math.sin(a)))


class SteerBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(26); self.setMinimumWidth(240)
        self.norm = 0.0; self.disp = 0.0

    def animate(self, f=0.30):
        n = _lerp(self.disp, self.norm, f)
        if n != self.disp:
            self.disp = n; self.update(); return True
        return False

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w = self.width(); h = self.height(); cy = h / 2.0
        line = theme_col("#3a4150", "#c7ccd6")
        p.setPen(QPen(line, 2))
        p.drawLine(QPointF(12, cy), QPointF(w - 12, cy))
        for x in (12, w / 2.0, w - 12):
            big = (abs(x - w / 2.0) < 1)
            p.drawLine(QPointF(x, cy - (8 if big else 6)), QPointF(x, cy + (8 if big else 6)))
        span = (w - 24) / 2.0
        mx = w / 2.0 + max(-1.0, min(1.0, self.disp)) * span
        p.setBrush(QColor(ACCENT)); p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(mx, cy), 5.5, 5.5)


# --------------------------------------------------------------------
#  PRESETS column
# --------------------------------------------------------------------
class PresetItem(QWidget):
    clicked = Signal(str)
    menu_requested = Signal(str, object)

    def __init__(self, name, icon=None):
        super().__init__()
        self.name = name; self.selected = False
        self.setFixedHeight(42); self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name)                       # full name on hover
        lay = QHBoxLayout(self); lay.setContentsMargins(18, 0, 12, 0); lay.setSpacing(9)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        self.icon.setAlignment(Qt.AlignCenter)
        self.set_icon(icon)
        lay.addWidget(self.icon, 0, Qt.AlignVCenter)
        self.lbl = BodyLabel(name)
        # let the label take the remaining width and shrink freely so long
        # names get an ellipsis instead of overflowing the fixed-width panel
        self.lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl.setMinimumWidth(0)
        lay.addWidget(self.lbl, 1)
        self.set_selected(False)

    def _elide(self):
        try:
            w = self.lbl.width() - 2
            if w < 12:                              # not laid out yet -> full text
                self.lbl.setText(self.name); return
            fm = QFontMetrics(self.lbl.font())
            self.lbl.setText(fm.elidedText(self.name, Qt.ElideRight, w))
        except Exception:
            self.lbl.setText(self.name)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def set_icon(self, icon):
        if icon is not None and not icon.isNull():
            self.icon.setPixmap(icon.pixmap(16, 16))
            self.icon.setVisible(True)
        else:
            self.icon.clear()
            self.icon.setVisible(False)

    def contextMenuEvent(self, e):
        self.menu_requested.emit(self.name, e.globalPos())

    def set_selected(self, s):
        self.selected = s
        
        f = self.lbl.font()
        family = f.family()
        size = f.pointSize()
        size_str = f"{size}pt" if size > 0 else f"{f.pixelSize()}px"
        
        if s:
            f.setBold(True)
            self.lbl.setFont(f)
            self.lbl.setStyleSheet(f"color: {ACCENT}; font-family: '{family}'; font-size: {size_str}; font-weight: bold;")
        else:
            f.setBold(False)
            self.lbl.setFont(f)
            text_color = "#ffffff" if isDarkTheme() else "#1b1b1b"
            self.lbl.setStyleSheet(f"color: {text_color}; font-family: '{family}'; font-size: {size_str}; font-weight: normal;")
            
        self._elide()                               # bold changes text width
        self.update()

    def mousePressEvent(self, e):
        self.clicked.emit(self.name)

    def paintEvent(self, e):
        if not self.selected:
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(_accent_rgba(28)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(8, 3, self.width() - 16, self.height() - 6), 6, 6)
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(8, 9, 3, self.height() - 18), 1.5, 1.5)


class PresetsPanel(QWidget):
    def __init__(self, on_select):
        super().__init__()
        self.on_select = on_select
        self.setMinimumWidth(204); self.setMaximumWidth(460)
        lay = QVBoxLayout(self); lay.setContentsMargins(18, 18, 18, 16); lay.setSpacing(4)

        # title row
        self.hdr = column_header(tr("ui.presets"))
        lay.addWidget(self.hdr)

        # Moza-style action icons on their own right-aligned row so they never
        # crowd the title when the column is dragged narrow.
        tools = QHBoxLayout(); tools.setContentsMargins(0, 2, 0, 0); tools.setSpacing(6)
        self.btn_add = self._mini(FIF.ADD, "New profile", self._add)
        self.btn_dup = self._mini(FIF.COPY, "Duplicate profile", self._duplicate)
        self.btn_ren = self._mini(FIF.EDIT, "Edit profile", self._rename)
        self.btn_del = self._mini(FIF.DELETE, "Delete profile", self._delete)
        tools.addStretch(1)
        for b in (self.btn_add, self.btn_dup, self.btn_ren, self.btn_del):
            tools.addWidget(b)
        tools.addStretch(1)
        lay.addLayout(tools)
        lay.addSpacing(10)
        self.listbox = QVBoxLayout(); self.listbox.setSpacing(2); self.listbox.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.listbox)
        lay.addStretch(1)

        self.cb_auto = CheckBox(tr("ui.autoload"))
        self.cb_auto.setChecked(bool(global_settings.get("auto_load", False)))
        self.cb_auto.stateChanged.connect(self._on_auto)
        lay.addWidget(self.cb_auto)
        self.items = []
        self.reload()

    def _mini(self, icon, tip, slot):
        b = TransparentToolButton(icon)
        b.setFixedSize(28, 28); b.setIconSize(QSize(15, 15))
        b.setToolTip(tip); b.clicked.connect(slot)
        return b

    def retranslate(self):
        self.hdr._lbl.setText(tr("ui.presets"))
        self.cb_auto.setText(tr("ui.autoload"))
        self.btn_add.setToolTip(tr("prof.add")); self.btn_dup.setToolTip(tr("prof.dup"))
        self.btn_ren.setToolTip(tr("prof.edit")); self.btn_del.setToolTip(tr("prof.del"))

    def _selected(self):
        return global_settings.get("selected_profile", "Global")

    def reload(self):
        for it in self.items:
            it.setParent(None)
        self.items = []
        sel = self._selected()
        for name in global_settings["profiles"].keys():
            prof = global_settings["profiles"].get(name, {})
            src = prof.get("logo_exe") or prof.get("exe_path") or ""
            it = PresetItem(name, exe_icon(src))
            it.clicked.connect(self.select)
            it.menu_requested.connect(self._context_menu)
            it.set_selected(name == sel)
            self.listbox.addWidget(it)
            self.items.append(it)
        is_global = (sel == "Global")
        self.btn_ren.setEnabled(not is_global)
        self.btn_del.setEnabled(not is_global)

    def select(self, name):
        global_settings["selected_profile"] = name; save_settings()
        for it in self.items:
            it.set_selected(it.name == name)
        self.btn_ren.setEnabled(name != "Global")
        self.btn_del.setEnabled(name != "Global")
        if self.on_select:
            self.on_select(name)

    def _unique(self, base):
        n = base; i = 2
        while n in global_settings["profiles"]:
            n = f"{base} {i}"; i += 1
        return n

    def _add(self):
        res = _preset_edit_dialog(tr("prof.add"), "", "", "", self.window(), allow_rename=True)
        if not res:
            return
        n, exe, logo = res
        if not n or n in global_settings["profiles"]:
            return
        base = global_settings["profiles"].get(self._selected(), global_settings["profiles"]["Global"])
        prof = dict(base)
        prof["exe_path"] = exe or ""
        prof["logo_exe"] = logo or ""
        global_settings["profiles"][n] = prof
        global_settings["selected_profile"] = n; save_settings()
        # install the proxy DLL next to the chosen game
        self._install_for_profile(exe)
        self.reload()
        if self.on_select:
            self.on_select(n)

    def _install_for_profile(self, exe):
        """Install the DLL only when this profile actually uses a LUT.

        Picking a game is not a reason to drop a DLL into its folder: the user
        may just want per-game wheel settings or an icon. The helper is only
        needed for FFB post-processing, so it follows the LUT switch instead.
        """
        if not exe:
            return
        prof = global_settings["profiles"].get(self._selected(), {})
        if not prof.get("lut_enabled"):
            return
        install_proxy_ui(exe, self.window())


    def _duplicate(self):
        cur = self._selected()
        src = global_settings["profiles"].get(cur)
        if src is None:
            return
        n = self._unique(cur + " Copy")
        global_settings["profiles"][n] = dict(src)
        global_settings["selected_profile"] = n; save_settings()
        self.reload()
        if self.on_select:
            self.on_select(n)

    def _rename(self):
        cur = self._selected()
        if cur == "Global":
            return
        prof = global_settings["profiles"].get(cur, {})
        res = _preset_edit_dialog(tr("prof.edit"), cur,
                                  prof.get("exe_path", ""), prof.get("logo_exe", ""),
                                  self.window(), allow_rename=True)
        if not res:
            return
        new, exe, logo = res
        profs = global_settings["profiles"]
        old_exe = prof.get("exe_path", "")
        # rename if changed and free
        if new and new != cur and new not in profs:
            profs[new] = profs.pop(cur)
            global_settings["selected_profile"] = new
            cur = new
        # store exe / logo on the (possibly renamed) profile
        p = profs.setdefault(cur, {})
        p["exe_path"] = exe or ""
        p["logo_exe"] = logo or ""
        save_settings()

        # --- keep the dinput8.dll in sync with the chosen game ---
        # If the game changed, remove the DLL from the old game's folder
        # (only if no other profile still points there).
        if old_exe and os.path.normcase(old_exe) != os.path.normcase(exe or ""):
            still_used = any(
                os.path.normcase(pp.get("exe_path", "")) == os.path.normcase(old_exe)
                for nm, pp in profs.items() if nm != cur)
            if not still_used:
                uninstall_proxy_for(old_exe)
        # Install/refresh the DLL next to the newly chosen game.
        self._install_for_profile(exe)

        self.reload()
        if self.on_select:
            self.on_select(global_settings["selected_profile"])

    def _delete(self):
        cur = self._selected()
        if cur == "Global":
            return
        if _confirm_dialog("Delete Profile",
                           f"Delete profile \u201c{cur}\u201d? This cannot be undone.", self.window()):
            gone = global_settings["profiles"].pop(cur, None)
            # clean up the game's dinput8.dll if no other profile still uses it
            try:
                exe = (gone or {}).get("exe_path", "")
                if exe:
                    still = any(pp.get("lut_enabled")
                                and os.path.normcase(pp.get("exe_path", "")) == os.path.normcase(exe)
                                for pp in global_settings["profiles"].values())
                    if not still:
                        uninstall_proxy_for(exe)
            except Exception:
                pass
            global_settings["selected_profile"] = "Global"; save_settings()
            self.reload()
            if self.on_select:
                self.on_select("Global")

    def _on_auto(self, *_):
        global_settings["auto_load"] = self.cb_auto.isChecked(); save_settings()

    def restyle(self):
        for it in self.items:
            it.set_selected(it.selected)

    def _context_menu(self, name, gpos):
        if name == "Global":
            return
        menu = QMenu(self)
        act_ren = menu.addAction("Rename")
        act_del = menu.addAction("Delete")
        chosen = menu.exec(gpos)
        if chosen == act_ren:
            new = _name_dialog("Rename Profile", name, self.window())
            if new and new != name and new not in global_settings["profiles"]:
                profs = global_settings["profiles"]
                profs[new] = profs.pop(name)
                if global_settings.get("selected_profile") == name:
                    global_settings["selected_profile"] = new
                save_settings(); self.reload()
                if self.on_select:
                    self.on_select(global_settings["selected_profile"])
        elif chosen == act_del:
            if _confirm_dialog("Delete Profile",
                               f"Delete profile \u201c{name}\u201d? This cannot be undone.", self.window()):
                global_settings["profiles"].pop(name, None)
                if global_settings.get("selected_profile") == name:
                    global_settings["selected_profile"] = "Global"
                save_settings(); self.reload()
                if self.on_select:
                    self.on_select(global_settings["selected_profile"])


# --------------------------------------------------------------------
#  LIVE TELEMETRY column
# --------------------------------------------------------------------
class TelemetryPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(418)
        lay = QVBoxLayout(self); lay.setContentsMargins(22, 18, 22, 18); lay.setSpacing(6)
        self.center_btn = PushButton(tr("ui.center")); self.center_btn.setObjectName("centerBtn")
        self.center_btn.clicked.connect(self._center_wheel)
        self.hdr = column_header(tr("ui.telemetry"), self.center_btn)
        lay.addWidget(self.hdr)
        lay.addStretch(1)
        self.wheel = WheelWidget(); self.wheel.setFixedSize(248, 248)
        lay.addWidget(self.wheel, 0, Qt.AlignHCenter)
        self.angle = TitleLabel("0\u00b0"); self.angle.setAlignment(Qt.AlignHCenter)
        fnt = self.angle.font(); fnt.setPointSize(36); fnt.setBold(True); self.angle.setFont(fnt)
        lay.addWidget(self.angle)
        
        self.cap = CaptionLabel(tr("home.steering")); self.cap.setAlignment(Qt.AlignHCenter)
        f = self.cap.font(); f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        self.cap.setFont(f)
        lay.addWidget(self.cap, 0, Qt.AlignHCenter)
        
        lay.addSpacing(10)
        self.steerbar = SteerBar()
        lay.addWidget(self.steerbar)
        lay.addSpacing(16)
        prow = QHBoxLayout(); prow.setSpacing(26); prow.setAlignment(Qt.AlignHCenter)
        self.clutch = PedalBar(tr("home.clutch"), "#60a5fa")
        self.brake = PedalBar(tr("home.brake"), "#ff6b6b")
        self.throttle = PedalBar(tr("home.throttle"), "#5dc98a")
        for wdg in (self.clutch, self.brake, self.throttle):
            wdg.setFixedSize(60, 150)
            prow.addWidget(wdg)
        lay.addLayout(prow)
        lay.addStretch(1)
        # center logic
        self._center_state = {"active": False, "near": 0, "deadline": 0.0}
        self._center_timer = QTimer(self); self._center_timer.timeout.connect(self._center_check)

    def retranslate(self):
        self.hdr._lbl.setText(tr("ui.telemetry"))
        self.center_btn.setText(tr("ui.center"))
        self.cap.setText(tr("home.steering"))
        self.clutch.label = tr("home.clutch"); self.clutch.update()
        self.brake.label = tr("home.brake"); self.brake.update()
        self.throttle.label = tr("home.throttle"); self.throttle.update()

    def _center_wheel(self):
        if dev is None:
            return
        ffb_write(stop_forces_cmd())
        ffb_write(rotation_cmd(main_window.applied_rotation()))
        ffb_write(autocenter_cmd(100))
        self._center_state = {"active": True, "near": 0, "deadline": time.time() + 4.0}
        self._center_timer.start(50)

    def _center_check(self):
        st = self._center_state
        if not st["active"]:
            self._center_timer.stop(); return
        if time.time() >= st["deadline"]:
            self._center_finish(); return
        if abs(state["steer_norm"]) < 0.018:
            st["near"] += 1
        else:
            st["near"] = 0
        if st["near"] >= 3:
            self._center_finish()

    def _center_finish(self):
        self._center_state["active"] = False; self._center_timer.stop()
        w = main_window.wheelset
        f = w.s_center.value() if w.cb_center.isChecked() else 0
        ffb_write(autocenter_cmd(f))

    def refresh(self):
        n = state["steer_norm"]
        self.wheel.target = main_window.applied_rotation() / 2 * n
        self.wheel.animate()
        self.angle.setText(f"{round(self.wheel.angle)}\u00b0")
        self.steerbar.norm = n; self.steerbar.animate()
        self.throttle.value = state["throttle"] / 255.0
        self.brake.value = state["brake"] / 255.0
        has_clutch = active_profile.get("clutch") is not None
        self.clutch.setVisible(has_clutch)
        self.clutch.enabled_axis = has_clutch
        self.clutch.value = (state["clutch"] / 255.0) if has_clutch else 0.0
        self.throttle.animate(); self.brake.animate(); self.clutch.animate()


# --------------------------------------------------------------------
#  RIGHT column tabs
# --------------------------------------------------------------------
def _slider_block(lay, name, lo, hi, val, suffix, hint):
    top = QHBoxLayout()
    nm = StrongBodyLabel(name)
    vl = StrongBodyLabel(f"{val}{suffix}"); vl.setStyleSheet(f"color:{ACCENT};")
    top.addWidget(nm); top.addStretch(1); top.addWidget(vl)
    s = Slider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(val)
    s.valueChanged.connect(lambda v: vl.setText(f"{v}{suffix}"))
    hl = CaptionLabel(hint)
    hl.setWordWrap(True)          # long hints must wrap, not widen the column
    hl.setMinimumWidth(1)         # never impose a minimum on the layout
    lay.addLayout(top); lay.addWidget(s); lay.addWidget(hl); lay.addSpacing(10)
    s._nm = nm; s._hint = hl; s._val = vl
    return s


class WheelSettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        prof = global_settings["profiles"].get(global_settings["selected_profile"], {})
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background:transparent;")
        outer.addWidget(scroll)
        host = QWidget(); host.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(host); lay.setContentsMargins(2, 4, 12, 4); lay.setSpacing(2)
        scroll.setWidget(host)

        self.h_ffb = section_header(tr("wheel.sec_ffb")); lay.addWidget(self.h_ffb); lay.addSpacing(6)
        self.s_gain = _slider_block(lay, tr("wheel.overall"), 0, 150, prof.get("di_gain", 101), "%",
                                    tr("wheel.overall_h"))
        self.s_spring = _slider_block(lay, tr("wheel.spring"), 0, 100, prof.get("di_spring", 0), "%",
                                      tr("wheel.spring_h"))
        self.s_damper = _slider_block(lay, tr("wheel.damper"), 0, 100, prof.get("di_damper", 0), "%",
                                      tr("wheel.damper_h"))
        self.cb_center = CheckBox(tr("wheel.center_cb"))
        self.cb_center.setChecked(prof.get("di_persist", False))
        lay.addWidget(self.cb_center); lay.addSpacing(4)
        self.s_center = _slider_block(lay, tr("wheel.center"), 0, 100, prof.get("di_center", 0), "%",
                                      tr("wheel.center_h"))
        # Autocenter ramp (0-15): the wheel's own spring slope. Same force with a
        # different ramp gives a very different center character.
        self.s_ramp = _slider_block(lay, tr("wheel.ramp"), 0, 15, prof.get("di_ramp", DEFAULT_RAMP), "",
                                    tr("wheel.ramp_h"))
        lay.addSpacing(6)
        self.h_steer = section_header(tr("wheel.sec_steer")); lay.addWidget(self.h_steer); lay.addSpacing(6)
        self.s_rot = _slider_block(lay, tr("wheel.rotation"), 90, 900, prof.get("angle", 900), "\u00b0",
                                   tr("wheel.rotation_h"))
        prow = QHBoxLayout(); prow.setSpacing(6)
        for d in (270, 360, 540, 720, 900):
            b = PushButton(f"{d}\u00b0")
            b.setMinimumWidth(1)                 # 5 buttons must fit when narrow
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _, x=d: self.s_rot.setValue(x))
            prow.addWidget(b)
        lay.addLayout(prow)
        lay.addStretch(1)

    def retranslate(self):
        self.h_ffb._lbl.setText(tr("wheel.sec_ffb")); self.h_steer._lbl.setText(tr("wheel.sec_steer"))
        self.cb_center.setText(tr("wheel.center_cb"))
        for s, nk, hk in ((self.s_gain, "wheel.overall", "wheel.overall_h"),
                          (self.s_spring, "wheel.spring", "wheel.spring_h"),
                          (self.s_damper, "wheel.damper", "wheel.damper_h"),
                          (self.s_center, "wheel.center", "wheel.center_h"),
                          (self.s_ramp, "wheel.ramp", "wheel.ramp_h"),
                          (self.s_rot, "wheel.rotation", "wheel.rotation_h")):
            s._nm.setText(tr(nk)); s._hint.setText(tr(hk))

    def restyle(self):
        for hd in (self.h_ffb, self.h_steer):
            hd._bar.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")

    def load_profile(self, name):
        prof = global_settings["profiles"].get(name, {})
        self.s_gain.setValue(prof.get("di_gain", 101))
        self.s_spring.setValue(prof.get("di_spring", 0))
        self.s_damper.setValue(prof.get("di_damper", 0))
        self.cb_center.setChecked(prof.get("di_persist", False))
        self.s_center.setValue(prof.get("di_center", 0))
        self.s_ramp.setValue(prof.get("di_ramp", DEFAULT_RAMP))
        self.s_rot.setValue(prof.get("angle", 900))


class LutCurveWidget(QWidget):
    """Content-Manager-style plot of a .lut curve, theme-aware."""
    def __init__(self):
        super().__init__()
        self.points = []            # list of (in, out) in 0..1
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_points(self, pts):
        self.points = pts or []
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = isDarkTheme()
        grid = QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 22)
        axis = QColor(255, 255, 255, 90) if dark else QColor(0, 0, 0, 90)
        txt = theme_col("#8a90a0", "#7a808c")
        ref = QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 40)

        m_l, m_b, m_t, m_r = 34, 26, 10, 12
        w = self.width(); h = self.height()
        gx = m_l; gy = m_t
        gw = max(1, w - m_l - m_r); gh = max(1, h - m_t - m_b)

        def X(v): return gx + v * gw
        def Y(v): return gy + (1.0 - v) * gh

        # grid every 10%
        p.setPen(QPen(grid, 1))
        f = QFont(); f.setPointSize(7); p.setFont(f)
        for i in range(0, 11):
            t = i / 10.0
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(X(t), Y(0)), QPointF(X(t), Y(1)))
            p.drawLine(QPointF(X(0), Y(t)), QPointF(X(1), Y(t)))
            p.setPen(QPen(txt, 1))
            if i % 2 == 0:
                p.drawText(QRectF(X(t) - 14, Y(0) + 4, 28, 14),
                           Qt.AlignHCenter, str(i * 10))
                p.drawText(QRectF(0, Y(t) - 7, m_l - 6, 14),
                           Qt.AlignRight | Qt.AlignVCenter, str(i * 10))

        # axes
        p.setPen(QPen(axis, 1))
        p.drawLine(QPointF(X(0), Y(0)), QPointF(X(1), Y(0)))
        p.drawLine(QPointF(X(0), Y(0)), QPointF(X(0), Y(1)))

        # 1:1 reference (dashed)
        pen = QPen(ref, 1); pen.setStyle(Qt.DashLine); p.setPen(pen)
        p.drawLine(QPointF(X(0), Y(0)), QPointF(X(1), Y(1)))

        # axis captions
        p.setPen(QPen(txt, 1))
        p.drawText(QRectF(gx, h - 14, gw, 14), Qt.AlignHCenter, tr("lut.axis_in"))
        p.save(); p.translate(10, gy + gh / 2); p.rotate(-90)
        p.drawText(QRectF(-40, -8, 80, 14), Qt.AlignHCenter, tr("lut.axis_out"))
        p.restore()

        # curve
        if len(self.points) >= 2:
            path = QPainterPath()
            first = True
            for xi, yi in self.points:
                xi = min(max(xi, 0.0), 1.0); yi = min(max(yi, 0.0), 1.0)
                pt = QPointF(X(xi), Y(yi))
                if first:
                    path.moveTo(pt); first = False
                else:
                    path.lineTo(pt)
            p.setPen(QPen(QColor(ACCENT), 2))
            p.drawPath(path)


class LutTab(QWidget):
    """LUT selector + live curve preview. Choices are stored per-preset."""
    def __init__(self, hub):
        super().__init__()
        self.hub = hub
        self._loading = False
        lay = QVBoxLayout(self); lay.setContentsMargins(2, 4, 12, 8); lay.setSpacing(4)

        self.h_sec = section_header(tr("lut.sec")); lay.addWidget(self.h_sec); lay.addSpacing(6)
        self.cb_enable = CheckBox(tr("lut.enable"))
        self.cb_enable.stateChanged.connect(self._on_enable)
        lay.addWidget(self.cb_enable)
        self.enable_hint = CaptionLabel(tr("lut.enable_h"))
        self.enable_hint.setWordWrap(True)
        lay.addWidget(self.enable_hint)
        # online-play risk warning (own-risk)
        self.warn = CaptionLabel(tr("lut.warn"))
        self.warn.setWordWrap(True)
        f = self.warn.font(); f.setBold(True); self.warn.setFont(f)
        self.warn.setStyleSheet("color:#e0a030;")
        lay.addWidget(self.warn)
        # shown only for the Global profile: LUT is per-game, make a profile
        self.global_notice = CaptionLabel(tr("lut.global_notice"))
        self.global_notice.setWordWrap(True)
        gf = self.global_notice.font(); gf.setBold(True); self.global_notice.setFont(gf)
        self.global_notice.setStyleSheet(f"color:{ACCENT};")
        self.global_notice.setVisible(False)
        lay.addWidget(self.global_notice)
        lay.addSpacing(8)

        row = QHBoxLayout(); row.setSpacing(8)
        self.lbl_sel = StrongBodyLabel(tr("lut.select"))
        self.combo = ComboBox(); self.combo.setMinimumWidth(200)
        self.combo.currentIndexChanged.connect(self._on_combo)
        self.btn_import = PushButton(tr("lut.import"))
        self.btn_import.clicked.connect(self._import)
        self.btn_delete = ToolButton(FIF.DELETE)
        self.btn_delete.setToolTip(tr("lut.delete"))
        self.btn_delete.clicked.connect(self._delete_lut)
        row.addWidget(self.lbl_sel); row.addWidget(self.combo, 1)
        row.addWidget(self.btn_import); row.addWidget(self.btn_delete)
        lay.addLayout(row); lay.addSpacing(8)

        self.curve = LutCurveWidget()
        lay.addWidget(self.curve, 1)
        self.empty_hint = CaptionLabel(tr("lut.empty"))
        lay.addWidget(self.empty_hint)

        self._refresh_files()

    # ---- data helpers ----
    def _cur_prof(self):
        return global_settings["profiles"].setdefault(
            global_settings.get("selected_profile", "Global"), {})

    def _refresh_files(self, keep=None):
        """Rebuild the combo from .lut files in the LUT folder."""
        self._loading = True
        self.combo.clear()
        self.combo.addItem(tr("lut.none"))
        files = []
        try:
            for fn in sorted(os.listdir(_lut_dir())):
                if fn.lower().endswith(".lut"):
                    files.append(fn)
        except Exception:
            pass
        for fn in files:
            self.combo.addItem(fn)
        self._files = files
        self._loading = False
        if keep is not None:
            self.select_file(keep)

    def select_file(self, fname):
        self._loading = True
        idx = 0
        if fname and fname in getattr(self, "_files", []):
            idx = self.combo.findText(fname)
            if idx < 0:
                idx = 0
        self.combo.setCurrentIndex(max(0, idx))
        self._loading = False
        self._draw_current()

    def _current_file(self):
        if self.combo.currentIndex() <= 0:
            return ""
        return self.combo.currentText()

    def _draw_current(self):
        fn = self._current_file()
        try:
            self.btn_delete.setEnabled(bool(fn))
        except Exception:
            pass
        if fn:
            pts = parse_lut_file(os.path.join(_lut_dir(), fn))
            self.curve.set_points(pts)
            self.empty_hint.setVisible(not pts)
        else:
            self.curve.set_points([])
            self.empty_hint.setVisible(not getattr(self, "_files", []))

    def _publish(self):
        """Push the active preset's LUT choice to the proxy via registry."""
        prof = self._cur_prof()
        fn = prof.get("lut_file", "")
        on = bool(prof.get("lut_enabled", False))
        if on and fn:
            set_active_lut(os.path.join(_lut_dir(), fn))
        else:
            set_active_lut("")

    # ---- events ----
    def _on_enable(self, *_):
        if self._loading:
            return
        self._cur_prof()["lut_enabled"] = self.cb_enable.isChecked()
        save_settings(); self._publish()
        self._sync_proxy()

    def _sync_proxy(self):
        """The helper DLL exists only to apply the LUT, so it follows this
        switch: installed when post-processing is on, removed when it's off."""
        prof = self._cur_prof()
        exe = prof.get("exe_path", "")
        if not exe:
            return
        if prof.get("lut_enabled"):
            install_proxy_ui(exe, self.window())
            return
        # Don't pull the DLL out from under another profile that still needs it.
        still_needed = any(
            pp is not prof and pp.get("lut_enabled")
            and os.path.normcase(pp.get("exe_path", "")) == os.path.normcase(exe)
            for pp in global_settings["profiles"].values())
        if not still_needed:
            res = uninstall_proxy_for(exe)
            if res == "locked":
                # Game is running: the DLL was renamed, not deleted. Say so
                # instead of claiming a clean removal.
                _defer_infobar("warning", tr("proxy.locked"), tr("proxy.locked_body"),
                               duration=3000, position=InfoBarPosition.TOP, parent=self.window())
            elif res:
                _defer_infobar("success", tr("proxy.removed"), tr("proxy.removed_body"),
                               duration=2500, position=InfoBarPosition.TOP, parent=self.window())

    def _on_combo(self, *_):
        if self._loading:
            return
        self._cur_prof()["lut_file"] = self._current_file()
        save_settings(); self._draw_current(); self._publish()

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self.window(), tr("lut.import"), "", "LUT files (*.lut);;All files (*.*)")
        if not path:
            return
        try:
            dst = os.path.join(_lut_dir(), os.path.basename(path))
            if os.path.abspath(path) != os.path.abspath(dst):
                with open(path, "rb") as s, open(dst, "wb") as d:
                    d.write(s.read())
            fname = os.path.basename(path)
            self._refresh_files(keep=fname)
            self._cur_prof()["lut_file"] = fname
            self._cur_prof()["lut_enabled"] = True
            self.cb_enable.setChecked(True)
            save_settings(); self._publish()
            _defer_infobar("success", tr("lut.imported"), fname, duration=2000,
                            position=InfoBarPosition.TOP, parent=self.window())
        except Exception:
            _defer_infobar("warning", tr("lut.import_fail"), "", duration=2500,
                            position=InfoBarPosition.TOP, parent=self.window())

    def _delete_lut(self):
        fn = self._current_file()
        if not fn:
            return
        if not _confirm_dialog(tr("lut.del_title"), tr("lut.del_body").format(fn),
                               self.window()):
            return
        try:
            os.remove(os.path.join(_lut_dir(), fn))
        except Exception:
            pass
        # Deleting a LUT file just clears the reference - it must NOT be treated
        # as the user turning post-processing off, or a profile that still has
        # post-processing on (e.g. pointing at a different LUT, or about to get a
        # new one) would lose its DLL. Only the profiles that actually used this
        # file lose their file; the enabled flag is left untouched, and the
        # checkbox is synced under the loading guard so it doesn't fire
        # _on_enable / _sync_proxy.
        cur = self._cur_prof()
        self._refresh_files()
        # After deleting, fall back to the first remaining LUT, or None if the
        # folder is now empty.
        new_file = self._files[0] if getattr(self, "_files", []) else ""
        self.select_file(new_file)
        # Repoint every profile that used the deleted file to the new selection
        # (post-processing stays on; only the file reference changes).
        for pp in global_settings["profiles"].values():
            if pp.get("lut_file") == fn:
                pp["lut_file"] = new_file
        cur["lut_file"] = new_file
        save_settings()
        self._loading = True
        self.cb_enable.setChecked(bool(cur.get("lut_enabled")))
        self._loading = False
        self._draw_current()
        self._publish()
        _defer_infobar("success", tr("lut.deleted"), fn, duration=2000,
                        position=InfoBarPosition.TOP, parent=self.window())

    # ---- preset switching / i18n / theme ----
    def _apply_mode(self, is_global):
        """Global profile: LUT is per-game, so disable the controls and show a
        notice telling the user to create a game profile instead."""
        for w in (self.cb_enable, self.combo, self.btn_import, self.btn_delete):
            try: w.setEnabled(not is_global)
            except Exception: pass
        self.global_notice.setVisible(is_global)
        self.enable_hint.setVisible(not is_global)
        self.warn.setVisible(not is_global)

    def load_profile(self, name):
        is_global = (name == "Global")
        prof = global_settings["profiles"].get(name, {})
        self._loading = True
        self.cb_enable.setChecked(bool(prof.get("lut_enabled", False)) and not is_global)
        self._loading = False
        self._apply_mode(is_global)
        self.select_file(prof.get("lut_file", "") if not is_global else "")
        if is_global:
            set_active_lut("")          # the Global profile never applies a LUT
        else:
            self._publish()

    def retranslate(self):
        self.h_sec._lbl.setText(tr("lut.sec"))
        self.cb_enable.setText(tr("lut.enable"))
        self.enable_hint.setText(tr("lut.enable_h"))
        self.warn.setText(tr("lut.warn"))
        self.global_notice.setText(tr("lut.global_notice"))
        self.lbl_sel.setText(tr("lut.select"))
        self.btn_import.setText(tr("lut.import"))
        self.btn_delete.setToolTip(tr("lut.delete"))
        self.empty_hint.setText(tr("lut.empty"))
        # rebuild "(none)" label
        cur = self._current_file()
        self._refresh_files(keep=cur)
        self.curve.update()

    def restyle(self):
        self.h_sec._bar.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")
        self.curve.update()


class FFBTestTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self); lay.setContentsMargins(2, 4, 12, 8); lay.setSpacing(4)
        self.h_test = section_header(tr("ffb.title")); lay.addWidget(self.h_test); lay.addSpacing(6)
        self.sub = CaptionLabel(tr("ffb.subtitle"))
        lay.addWidget(self.sub); lay.addSpacing(6)
        self.s_strength = _slider_block(lay, tr("ffb.strength"), 0, 100, 60, "%", tr("ffb.strength_h"))
        self._spring_on = False; self._sweep_on = False; self._sweep_dir = "left"
        self._sweep_t = QTimer(self); self._sweep_t.timeout.connect(self._sweep_step)
        g = QGridLayout(); g.setSpacing(8)
        self.bL = PushButton(tr("ffb.push_l")); self.bL.clicked.connect(lambda: self._push("left"))
        self.bR = PushButton(tr("ffb.push_r")); self.bR.clicked.connect(lambda: self._push("right"))
        self.btn_spring = PushButton(tr("ffb.spring")); self.btn_spring.clicked.connect(self._toggle_spring)
        self.btn_sweep = PushButton(tr("ffb.sweep")); self.btn_sweep.clicked.connect(self._toggle_sweep)
        g.addWidget(self.bL, 0, 0); g.addWidget(self.bR, 0, 1)
        g.addWidget(self.btn_spring, 1, 0); g.addWidget(self.btn_sweep, 1, 1)
        lay.addLayout(g); lay.addSpacing(8)
        lay.addSpacing(6)
        self.h_adv = section_header(tr("ffb.advanced")); lay.addWidget(self.h_adv); lay.addSpacing(6)
        g2 = QGridLayout(); g2.setSpacing(8)
        specs = [("ffb.pulse_l", lambda: self._pulse("left")), ("ffb.pulse_r", lambda: self._pulse("right")),
                 ("ffb.vibe_light", lambda: self._vibe(40, 0.040)), ("ffb.vibe_med", lambda: self._vibe(70, 0.030)),
                 ("ffb.vibe_fast", lambda: self._vibe(90, 0.012)), ("ffb.vibe_heavy", lambda: self._vibe(110, 0.020))]
        self._adv_btns = []
        for i, (tk, cb) in enumerate(specs):
            b = PushButton(tr(tk)); b._key = tk; b.clicked.connect(cb); g2.addWidget(b, i // 2, i % 2)
            self._adv_btns.append(b)
        lay.addLayout(g2); lay.addSpacing(10)
        self.stop = PrimaryPushButton(tr("ffb.stop")); self.stop.clicked.connect(self._stop)
        lay.addWidget(self.stop); lay.addSpacing(6)
        self.reset = PushButton(tr("ffb.reset")); self.reset.setObjectName("resetBtn")
        self.reset.clicked.connect(self._reset_driver)
        lay.addWidget(self.reset)
        self.reset_hint = CaptionLabel(tr("ffb.reset_h"))
        lay.addWidget(self.reset_hint)
        lay.addStretch(1)

    def retranslate(self):
        self.h_test._lbl.setText(tr("ffb.title")); self.h_adv._lbl.setText(tr("ffb.advanced"))
        self.sub.setText(tr("ffb.subtitle"))
        self.s_strength._nm.setText(tr("ffb.strength")); self.s_strength._hint.setText(tr("ffb.strength_h"))
        self.bL.setText(tr("ffb.push_l")); self.bR.setText(tr("ffb.push_r"))
        self.btn_spring.setText(tr("ffb.spring_stop") if self._spring_on else tr("ffb.spring"))
        self.btn_sweep.setText(tr("ffb.sweep_stop") if self._sweep_on else tr("ffb.sweep"))
        for b in self._adv_btns:
            b.setText(tr(b._key))
        self.stop.setText(tr("ffb.stop")); self.reset.setText(tr("ffb.reset"))
        self.reset_hint.setText(tr("ffb.reset_h"))

    def _ready(self):
        if dev is None:
            _defer_infobar("warning", tr("conn.not_connected"), tr("input.led_nc"), duration=2500,
                            position=InfoBarPosition.TOP, parent=self.window())
            return False
        return True

    def _str(self): return self.s_strength.value()
    def _push(self, d):
        if not self._ready(): return
        ffb_write(constant_force_cmd(d, self._str()))

    def _stop(self):
        self._spring_on = self._sweep_on = False; self._sweep_t.stop()
        self.btn_spring.setText(tr("ffb.spring")); self.btn_sweep.setText(tr("ffb.sweep"))
        ffb_write(stop_forces_cmd()); ffb_write(autocenter_cmd(0))

    def _toggle_spring(self):
        if not self._spring_on and not self._ready(): return
        self._spring_on = not self._spring_on
        if self._spring_on:
            ffb_write(autocenter_cmd(self._str(), _active_ramp())); self.btn_spring.setText(tr("ffb.spring_stop"))
        else:
            ffb_write(autocenter_cmd(0)); self.btn_spring.setText(tr("ffb.spring"))

    def _toggle_sweep(self):
        if not self._sweep_on and not self._ready(): return
        self._sweep_on = not self._sweep_on
        if self._sweep_on:
            self.btn_sweep.setText(tr("ffb.sweep_stop")); self._sweep_t.start(450)
        else:
            self.btn_sweep.setText(tr("ffb.sweep")); self._sweep_t.stop(); ffb_write(stop_forces_cmd())

    def _sweep_step(self):
        ffb_write(constant_force_cmd(self._sweep_dir, self._str()))
        self._sweep_dir = "right" if self._sweep_dir == "left" else "left"

    def _pulse(self, d):
        if not self._ready(): return
        ffb_write(constant_force_cmd(d, min(100, self._str() + 40)))
        QTimer.singleShot(150, lambda: ffb_write(stop_forces_cmd()))

    def _vibe(self, strength, period):
        if not self._ready(): return
        def run():
            t0 = time.time(); cur = strength
            while time.time() - t0 < 1.5:
                ffb_write([SLOT0_MASK | CMD_DOWNLOAD_PLAY, 0x00, max(0, min(255, 0x80 + cur)), 0, 0, 0, 0]); cur = -cur; time.sleep(period)
            ffb_write(stop_forces_cmd())
        threading.Thread(target=run, daemon=True).start()

    def _reset_driver(self):
        restore_ffb_defaults()
        _defer_infobar("success", "Driver FFB reset", "All app-written FFB registry values were removed.",
                        duration=2500, position=InfoBarPosition.TOP, parent=self.window())


class InputMonitorTab(QWidget):
    def __init__(self):
        super().__init__()
        # No scroll area: the InputMonitor has a fixed footprint, so letting the
        # layout expose its real minimum makes the window refuse to shrink past
        # the point where buttons would clip (instead of showing a scrollbar).
        lay = QVBoxLayout(self); lay.setContentsMargins(2, 4, 12, 8); lay.setSpacing(6)
        self.hdr = section_header(tr("tab.input")); lay.addWidget(self.hdr); lay.addSpacing(6)
        self.mon = InputMonitor()
        lay.addWidget(self.mon, 0, Qt.AlignHCenter)
        lay.addSpacing(12)
        self.led = PushButton(tr("input.led")); self.led.clicked.connect(self._led)
        lay.addWidget(self.led)
        self.led_status = CaptionLabel("")
        lay.addWidget(self.led_status)
        lay.addStretch(1)

    def retranslate(self):
        self.hdr._lbl.setText(tr("tab.input")); self.led.setText(tr("input.led"))
        self.mon.update()

    def _sync_caps(self):
        """DFGT has no RPM LEDs - hide the test instead of showing a button
        that can only ever answer "G27 only"."""
        has_leds = active_profile is DEVICE_PROFILES["G27"]
        for w in (self.led, self.led_status):
            try: w.setVisible(has_leds)
            except Exception: pass

    def _led(self):
        if dev is None:
            self.led_status.setText(tr("input.led_nc")); return
        if active_profile is not DEVICE_PROFILES["G27"]:
            self.led_status.setText(tr("input.led_g27")); return
        self.led_status.setText(tr("input.led_run"))
        led_greeting()
        QTimer.singleShot(2300, lambda: self.led_status.setText(""))

    def refresh(self):
        self._sync_caps()
        self.mon.pressed = decode_buttons(state["raw"]) if state["connected"] else set()
        self.mon.update()


class InfoTab(QWidget):
    def __init__(self):
        super().__init__()
        self.vals = {}; self._bars = []; self._labels = []
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background:transparent;")
        outer.addWidget(scroll)
        host = QWidget(); host.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(host); lay.setContentsMargins(2, 6, 12, 8); lay.setSpacing(5)
        scroll.setWidget(host)

        self._secs = []
        self._sec(lay, "about.sec_hw")
        self._row(lay, "about.devmodel", "model")
        self._row(lay, "about.hwid", "hwid")
        self._row(lay, "about.interface", "interface")
        self._row(lay, "about.power", "power")
        lay.addSpacing(8)
        self._sec(lay, "about.sec_sensor")
        self._row(lay, "about.tracking", "tracking")
        self._row(lay, "about.axis", "axis")
        self._row(lay, "about.ffb", "ffb")
        self._row(lay, "about.polling", "polling")
        lay.addSpacing(8)
        self._sec(lay, "about.sec_sw")
        self._row(lay, "about.opmode", "opmode")
        self._row(lay, "about.api", "api")
        self._row(lay, "about.hub", "hub")
        lay.addSpacing(8)
        self._sec(lay, "about.sec_credits")
        self._row(lay, "about.author", "author")
        lay.addSpacing(8)

        # ABOUT / legal -- version, repo, license, TM notice (localized)
        self.ab_sec = section_header(tr("about.sec_about")); self.ab_sec._key = "about.sec_about"
        self._bars.append(self.ab_sec._bar)
        lay.addSpacing(2); lay.addWidget(self.ab_sec); lay.addSpacing(2)
        ver = StrongBodyLabel(f"Legacy Wheel Hub  {HUB_VERSION}")
        lay.addWidget(ver)
        self.link = BodyLabel()
        self.link.setOpenExternalLinks(True)
        self.link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lay.addWidget(self.link)
        self.lic = BodyLabel(tr("about.license"))
        lay.addWidget(self.lic)
        self.disc = CaptionLabel(tr("about.disclaimer"))
        self.disc.setWordWrap(True)
        lay.addWidget(self.disc)
        self._refresh_link()
        lay.addStretch(1)

    def _refresh_link(self):
        self.link.setText(
            f'<a style="color:#ff8a3d;" '
            f'href="https://github.com/Sadooo27/legacy-wheel-hub">{tr("about.repo")}</a>')

    def _sec(self, lay, key):
        hd = section_header(tr(key)); hd._key = key; self._bars.append(hd._bar); self._secs.append(hd)
        if lay.count():          # no gap above the FIRST header: it must line
            lay.addSpacing(8)    # up with the other tabs' first header
        lay.addWidget(hd); lay.addSpacing(4)

    def _row(self, lay, key, field):
        row = QHBoxLayout(); row.setContentsMargins(14, 0, 4, 0)
        lbl = BodyLabel(tr(key)); lbl._key = key
        val = StrongBodyLabel("-")
        row.addWidget(lbl); row.addStretch(1); row.addWidget(val)
        lay.addLayout(row)
        self.vals[field] = val; self._labels.append(lbl)

    def refresh(self):
        info = active_profile["info"]; conn = state["connected"]
        power = f"{info['power']} / {tr('info.active')}" if conn else f"{info['power']} / {tr('info.standby')}"
        opmode = tr("info.opmode_active") if conn else tr("info.opmode_idle")
        data = {"model": info["model"], "hwid": info["hwid"], "interface": info["interface"],
                "power": power, "tracking": info["tracking"], "axis": info["axis"],
                "ffb": info["ffb"], "polling": info["polling"], "opmode": opmode,
                "api": info["api"], "hub": HUB_VERSION, "author": AUTHOR}
        for k, v in data.items():
            if k in self.vals:
                self.vals[k].setText(v)

    def retranslate(self):
        for hd in self._secs:
            hd._lbl.setText(tr(hd._key))
        for lbl in self._labels:
            lbl.setText(tr(lbl._key))
        self.ab_sec._lbl.setText(tr("about.sec_about"))
        self.lic.setText(tr("about.license"))
        self.disc.setText(tr("about.disclaimer"))
        self._refresh_link()
        self.refresh()

    def restyle(self):
        for b in self._bars:
            b.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")
        muted = "#8a93a6" if isDarkTheme() else "#5c6370"
        for lbl in self._labels:
            lbl.setStyleSheet(f"color:{muted};")
        self.lic.setStyleSheet(f"color:{muted};")
        self.disc.setStyleSheet(f"color:{muted};")


class SettingsTab(QWidget):
    """Reachable via the gear icon to the right of the tab strip."""
    def __init__(self, hub):
        super().__init__()
        self.hub = hub; self._bars = []; self._labels = []; self._guard = False
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background:transparent;")
        outer.addWidget(scroll)
        host = QWidget(); host.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(host); lay.setContentsMargins(2, 4, 12, 4); lay.setSpacing(2)
        scroll.setWidget(host)

        self.sec_gen = self._sec(lay, "set.general")
        self.combo_lang = ComboBox(); self.combo_lang.setMinimumWidth(190)
        for code, label in LANG_ORDER:
            self.combo_lang.addItem(label, userData=code)
        self.combo_lang.setCurrentIndex(next((i for i, (c, _) in enumerate(LANG_ORDER) if c == CURRENT_LANG), 0))
        self.combo_lang.currentIndexChanged.connect(self._on_lang)
        self.row_lang = self._field(lay, "set.language", self.combo_lang)
        self.cb_tray = CheckBox(tr("set.tray"))
        self.cb_tray.setChecked(bool(global_settings.get("minimize_to_tray", False)))
        self.cb_tray.stateChanged.connect(self._on_tray)
        lay.addWidget(self.cb_tray)
        self.tray_hint = CaptionLabel(tr("set.tray_h"))
        self.tray_hint.setWordWrap(True); lay.addWidget(self.tray_hint)
        lay.addSpacing(10)

        self.sec_app = self._sec(lay, "set.appearance")
        self.combo_theme = ComboBox(); self.combo_theme.setMinimumWidth(190)
        self.combo_theme.addItem(tr("about.theme_dark"), userData="dark")
        self.combo_theme.addItem(tr("about.theme_light"), userData="light")
        self.combo_theme.setCurrentIndex(0 if isDarkTheme() else 1)
        self.combo_theme.currentIndexChanged.connect(self._on_theme)
        self.row_theme = self._field(lay, "set.theme", self.combo_theme)
        self.combo_scale = ComboBox(); self.combo_scale.setMinimumWidth(190)
        self._scale_vals = [100, 125, 150, 175, 200, 250]
        for v in self._scale_vals:
            self.combo_scale.addItem(f"{v}%", userData=v)
        cur_scale = int(global_settings.get("ui_scale", 100))
        if cur_scale not in self._scale_vals:
            self._scale_vals.append(cur_scale); self.combo_scale.addItem(f"{cur_scale}%", userData=cur_scale)
        self.combo_scale.setCurrentIndex(self._scale_vals.index(cur_scale))
        self.combo_scale.currentIndexChanged.connect(self._on_scale)
        self.row_scale = self._field(lay, "set.ui_scale", self.combo_scale)
        self.scale_hint = CaptionLabel(tr("set.ui_scale_h"))
        self.scale_hint.setWordWrap(True); lay.addWidget(self.scale_hint)
        lay.addSpacing(10)

        self.sec_test = self._sec(lay, "set.testing")
        self.combo_test = ComboBox(); self.combo_test.setMinimumWidth(190)
        self.combo_test.addItem(tr("about.test_auto"), userData=None)
        self.combo_test.addItem("DFGT", userData="DFGT")
        self.combo_test.addItem("G27", userData="G27")
        self.combo_test.setCurrentIndex({"DFGT": 1, "G27": 2}.get(test_override, 0))
        self.combo_test.currentIndexChanged.connect(self._on_test)
        self.row_test = self._field(lay, "set.devmode", self.combo_test)
        self.test_hint = CaptionLabel(tr("set.devmode_h"))
        self.test_hint.setWordWrap(True); lay.addWidget(self.test_hint)
        lay.addStretch(1)

    def _sec(self, lay, key):
        hd = section_header(tr(key)); hd._key = key; self._bars.append(hd._bar)
        if lay.count():          # no gap above the FIRST header: it must line
            lay.addSpacing(8)    # up with the other tabs' first header
        lay.addWidget(hd); lay.addSpacing(4)
        return hd

    def _field(self, lay, key, ctrl):
        row = QHBoxLayout(); row.setContentsMargins(14, 0, 4, 0)
        lbl = BodyLabel(tr(key)); lbl._key = key
        self._labels.append(lbl)
        row.addWidget(lbl); row.addStretch(1); row.addWidget(ctrl)
        lay.addLayout(row)
        return lbl

    def _on_theme(self, *_):
        if self._guard: return
        self.hub.set_theme(self.combo_theme.currentData())

    def _on_scale(self, *_):
        if self._guard: return
        global_settings["ui_scale"] = int(self.combo_scale.currentData()); save_settings()
        try:
            _defer_infobar("success", tr("set.ui_scale"), tr("set.restart_hint"), duration=4000,
                            position=InfoBarPosition.TOP, parent=self.window())
        except Exception: pass

    def _on_lang(self, *_):
        if self._guard: return
        set_language(self.combo_lang.currentData()); self.hub.retranslate_all()

    def _on_tray(self, *_):
        global_settings["minimize_to_tray"] = self.cb_tray.isChecked(); save_settings()
        self.hub.update_tray_state()

    def _on_test(self, *_):
        if self._guard: return
        set_test_override(self.combo_test.currentData())
        # The override changes active_profile, but the capability-driven parts
        # of the UI (clutch bar, LED test, device name) are refreshed by the
        # poller - which never runs while no wheel is connected. Refresh them
        # by hand so switching the test device actually changes the UI.
        w = self.window()
        for path in ("telemetry", "settings.input_tab"):
            try:
                o = w
                for p in path.split("."): o = getattr(o, p)
                o.refresh()
            except Exception:
                pass
        for owner in (w, getattr(w, "titleBar", None)):
            try:
                owner.set_device(active_profile["name"]); break
            except Exception:
                pass

    def restyle(self):
        for b in self._bars:
            b.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")
        lbl_color = "#cfd4de" if isDarkTheme() else "#1b1b1b"
        muted = "#8a93a6" if isDarkTheme() else "#5c6370"
        for lbl in self._labels:
            lbl.setStyleSheet(f"color:{lbl_color};")
        self.tray_hint.setStyleSheet(f"color:{muted};")
        self.scale_hint.setStyleSheet(f"color:{muted};")
        self.test_hint.setStyleSheet(f"color:{muted};")

    def retranslate(self):
        self._guard = True
        for hd in (self.sec_app, self.sec_gen, self.sec_test):
            hd._lbl.setText(tr(hd._key))
        for lbl, key in ((self.row_theme, "set.theme"), (self.row_lang, "set.language"),
                         (self.row_test, "set.devmode")):
            lbl.setText(tr(key))
        self.cb_tray.setText(tr("set.tray")); self.tray_hint.setText(tr("set.tray_h"))
        self.test_hint.setText(tr("set.devmode_h"))
        self.row_scale.setText(tr("set.ui_scale")); self.scale_hint.setText(tr("set.ui_scale_h"))
        ti = self.combo_theme.currentIndex()
        self.combo_theme.clear()
        self.combo_theme.addItem(tr("about.theme_dark"), userData="dark")
        self.combo_theme.addItem(tr("about.theme_light"), userData="light")
        self.combo_theme.setCurrentIndex(ti)
        tt = self.combo_test.currentIndex()
        self.combo_test.clear()
        self.combo_test.addItem(tr("about.test_auto"), userData=None)
        self.combo_test.addItem("DFGT", userData="DFGT")
        self.combo_test.addItem("G27", userData="G27")
        self.combo_test.setCurrentIndex(tt)
        self.combo_lang.setCurrentIndex(next((i for i, (c, _) in enumerate(LANG_ORDER) if c == CURRENT_LANG), 0))
        self._guard = False


class SettingsColumn(QWidget):
    """Right column: Pivot tab strip (+ gear) + stacked pages."""
    def __init__(self, hub):
        super().__init__()
        self.hub = hub
        self.setMinimumWidth(584)
        lay = QVBoxLayout(self); lay.setContentsMargins(24, 16, 18, 16); lay.setSpacing(10)

        toprow = QHBoxLayout(); toprow.setContentsMargins(0, 0, 0, 0); toprow.setSpacing(8)
        self.pivot = Pivot(self)
        self.stack = QStackedWidget(self)
        self.wheel_tab = WheelSettingsTab()
        self.lut_tab = LutTab(hub)
        self.ffb_tab = FFBTestTab()
        self.input_tab = InputMonitorTab()
        self.info_tab = InfoTab()
        self.settings_tab = SettingsTab(hub)
        self._pages = [("wheel", "tab.wheel", self.wheel_tab),
                       ("lut", "tab.lut", self.lut_tab),
                       ("ffb", "tab.ffb", self.ffb_tab),
                       ("input", "tab.input", self.input_tab),
                       ("info", "tab.info", self.info_tab)]
        for key, tkey, page in self._pages:
            self.stack.addWidget(page)
            self.pivot.addItem(routeKey=key, text=tr(tkey),
                               onClick=lambda checked=False, c=page, k=key: self._goto(k, c))
        self.stack.addWidget(self.settings_tab)
        self.pivot.setCurrentItem("wheel")
        self.stack.setCurrentWidget(self.wheel_tab)

        self.gear = TransparentToolButton(FIF.SETTING, self)
        self.gear.setFixedSize(32, 32); self.gear.setIconSize(QSize(18, 18))
        self.gear.setToolTip(tr("set.title")); self.gear.clicked.connect(self._open_settings)

        gsep = QFrame(); gsep.setObjectName("vsep"); gsep.setFixedSize(1, 22)
        gsep.setFrameShape(QFrame.NoFrame)
        toprow.addWidget(self.pivot); toprow.addStretch(1)
        toprow.addWidget(gsep, 0, Qt.AlignVCenter); toprow.addSpacing(4)
        toprow.addWidget(self.gear, 0, Qt.AlignVCenter)
        lay.addLayout(toprow)
        # Ensure the whole tab strip always fits: force the pivot's minimum
        # width to the width it needs to show every tab, and widen the column
        # minimum to fit pivot + separator + gear + margins. Prevents the tabs
        # from overlapping the settings gear when the window is made narrow.
        self._fit_tabstrip()
        sep = QFrame(); sep.setObjectName("hsep"); sep.setFixedHeight(1)
        lay.addWidget(sep)
        lay.addWidget(self.stack, 1)

    def _fit_tabstrip(self):
        """Size the column so the pivot tab strip + gear always fit."""
        try:
            self.pivot.adjustSize()
            pw = self.pivot.sizeHint().width()
            if pw <= 0:
                pw = self.pivot.minimumSizeHint().width()
            self.pivot.setMinimumWidth(pw)
            # pivot + spacing + separator + gear + column L/R margins + padding
            needed = pw + 8 + 1 + 4 + 32 + (24 + 18) + 24
            self.setMinimumWidth(max(584, needed))
        except Exception:
            pass

    def select_tab(self, key):
        for k, _, page in self._pages:
            if k == key:
                self._goto(k, page); return

    def _goto(self, key, page):
        self.pivot.setCurrentItem(key)
        self.stack.setCurrentWidget(page)
        self._gear_active(False)

    def _open_settings(self):
        self.stack.setCurrentWidget(self.settings_tab)
        # no pivot tab should look active while the settings panel is shown
        self.pivot._currentRouteKey = None
        for it in self.pivot.items.values():
            try: it.setSelected(False)
            except Exception: pass
        self.pivot.update()
        self._gear_active(True)

    def _gear_active(self, on):
        self.gear.setStyleSheet(
            f"TransparentToolButton{{border-bottom:2px solid {ACCENT};}}" if on else "")

    def retranslate(self):
        for key, tkey, _ in self._pages:
            it = self.pivot.widget(key)
            if it is not None:
                try: it.setText(tr(tkey))
                except Exception: pass
        self.gear.setToolTip(tr("set.title"))
        # tab labels changed width -> re-fit the strip so nothing overlaps
        self._fit_tabstrip()
        for page in (self.wheel_tab, self.lut_tab, self.ffb_tab, self.input_tab, self.info_tab, self.settings_tab):
            if hasattr(page, "retranslate"):
                try: page.retranslate()
                except Exception: pass


# --------------------------------------------------------------------
#  Custom header (title bar)
# --------------------------------------------------------------------
class CustomTitleBar(TitleBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("Header")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(78)
        # strip the default layout
        while self.hBoxLayout.count():
            it = self.hBoxLayout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        for b in (self.minBtn, self.maxBtn, self.closeBtn):
            b.setParent(self)

        root = self.hBoxLayout
        root.setContentsMargins(18, 0, 0, 0); root.setSpacing(0)

        # left cluster: logo + titles
        left = QWidget(); lh = QHBoxLayout(left); lh.setContentsMargins(6, 0, 0, 0); lh.setSpacing(13)
        self.logo = WheelLogo(46)
        tcol = QVBoxLayout(); tcol.setContentsMargins(0, 0, 0, 0); tcol.setSpacing(2)
        self.brand = StrongBodyLabel("Legacy Wheel Hub"); self.brand.setStyleSheet(f"color:{ACCENT};")
        bf = self.brand.font(); bf.setBold(True); bf.setPointSize(15); bf.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
        self.brand.setFont(bf)
        self.device = CaptionLabel("")
        df = self.device.font(); df.setPointSize(9); self.device.setFont(df)
        tcol.addStretch(1); tcol.addWidget(self.brand); tcol.addWidget(self.device); tcol.addStretch(1)
        lh.addWidget(self.logo, 0, Qt.AlignVCenter); lh.addLayout(tcol)
        root.addWidget(left, 0, Qt.AlignVCenter)
        root.addStretch(1)

        # right area: window buttons on top (min / max / close), actions below
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        winrow = QHBoxLayout(); winrow.setContentsMargins(0, 0, 0, 0); winrow.setSpacing(0)
        winrow.addStretch(1)
        winrow.addWidget(self.minBtn); winrow.addWidget(self.maxBtn); winrow.addWidget(self.closeBtn)
        rv.addLayout(winrow)
        rv.addStretch(1)
        actrow = QHBoxLayout(); actrow.setContentsMargins(0, 0, 18, 6); actrow.setSpacing(14)
        self.status = StrongBodyLabel("\u25cf  Not Connected")
        self.status.setStyleSheet("color:#ff6b6b;")
        self.apply_btn = PushButton("APPLY"); self.apply_btn.setObjectName("applyBtn")
        actrow.addStretch(1)
        actrow.addWidget(self.status, 0, Qt.AlignVCenter)
        actrow.addWidget(self.apply_btn, 0, Qt.AlignVCenter)
        rv.addLayout(actrow)
        root.addWidget(right, 0)
        self.style_buttons()

    def style_buttons(self):
        dark = isDarkTheme()
        nc = QColor("#e6e6e6") if dark else QColor("#1a1a1a")
        hc = QColor("#ffffff") if dark else QColor("#000000")
        for b in (self.minBtn, self.maxBtn):
            b.setNormalColor(nc); b.setHoverColor(hc)
        self.closeBtn.setNormalColor(nc)
        muted = "#8a93a6" if dark else "#5c6370"
        self.device.setStyleSheet(f"color:{muted};")

    def set_status(self, text, color):
        self.status.setText(text); self.status.setStyleSheet(f"color:{color};")

    def set_device(self, name):
        self.device.setText(name)


# --------------------------------------------------------------------
#  Main window
# --------------------------------------------------------------------
class ControlHub(FramelessWindow):
    def __init__(self):
        super().__init__()
        global active_profile
        self.setObjectName("ControlHub")
        self.setTitleBar(CustomTitleBar(self))
        self.setWindowTitle("Legacy Wheel Hub")
        self._restore_geometry()
        if global_settings.get("last_device") in DEVICE_PROFILES:
            active_profile = DEVICE_PROFILES[global_settings["last_device"]]

        body = QWidget(self)
        bl = QHBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
        self.presets = PresetsPanel(self._on_preset)
        self.telemetry = TelemetryPanel()
        self.settings = SettingsColumn(self)
        self.wheelset = self.settings.wheel_tab   # alias used by apply/center logic
        # rotation range currently believed to be ON THE WHEEL. The live
        # telemetry scales by THIS, not the slider, so dragging the rotation
        # slider doesn't desync the on-screen wheel until APPLY is pressed.
        self._applied_angle = self.rotation_value()

        # fixed always-visible PRESETS sidebar
        self.presets.setFixedWidth(210)

        bl.addWidget(self.presets)
        bl.addWidget(self._vline())
        bl.addWidget(self.telemetry)
        bl.addWidget(self._vline())
        bl.addWidget(self.settings, 1)

        root = QVBoxLayout(self); root.setContentsMargins(0, self.titleBar.height(), 0, 0); root.setSpacing(0)
        root.addWidget(body)
        self.titleBar.raise_()

        self.titleBar.apply_btn.clicked.connect(self.apply_settings)
        self.titleBar.set_device(active_profile["name"])
        self.settings.select_tab(global_settings.get("last_tab", "wheel"))

        self._app_icon = QIcon(WHEEL_PNG) if os.path.exists(WHEEL_PNG) else QIcon()
        try: self.setWindowIcon(self._app_icon)
        except Exception: pass

        self.tray = None; self._build_tray()
        self._was_connected = False
        # auto-load is deferred until the wheel finishes its power-on
        # calibration sweep, detected by the steering axis settling.
        self._autoload_pending = False
        self._cal_prev = 0.0
        self._cal_connect_t = 0.0
        self._cal_last_move_t = 0.0
        self._cal_seen_sweep = False

        self._apply_palette()
        self.retranslate_all()
        # sync LUT tab + publish active LUT for the currently selected preset
        try: self._on_preset(global_settings.get("selected_profile", "Global"), auto_apply=False)
        except Exception: pass
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(16)
        QTimer.singleShot(0, self._apply_min_size)

    def _apply_min_size(self):
        # Lock the window's hard minimum to the layout's real minimum (driven by
        # the fixed-size INPUT MONITOR tab). This guarantees the input buttons
        # always fit and no scrollbar can ever appear.
        m = self.layout().minimumSize()
        self.setMinimumSize(m.width(), m.height())

    def _vline(self):
        f = QFrame(); f.setObjectName("vsep"); f.setFixedWidth(1)
        f.setFrameShape(QFrame.NoFrame)
        return f

    def rotation_value(self):
        try: return self.wheelset.s_rot.value()
        except Exception: return 900

    def applied_rotation(self):
        # range actually applied to the wheel (updated on APPLY), used by the
        # live telemetry so the on-screen angle matches the physical wheel.
        return getattr(self, "_applied_angle", self.rotation_value())

    def _on_preset(self, name, auto_apply=True):
        try: self.wheelset.load_profile(name)
        except Exception: pass
        try: self.settings.lut_tab.load_profile(name)
        except Exception: pass
        # Selecting a preset applies it immediately, exactly as if APPLY was
        # pressed (same wheel writes + same notification). Skipped for the
        # one-time startup sync so launching doesn't fire a warning.
        if auto_apply:
            try: self.apply_settings()
            except Exception: pass

    # ---- palette / theme ----
    def _apply_palette(self):
        self.setStyleSheet(hub_qss())
        self.telemetry.wheel._cache_key = None
        try: self.titleBar.style_buttons()
        except Exception: pass
        self.info_restyle()

    def info_restyle(self):
        for attr in ("info_tab", "settings_tab", "lut_tab", "wheel_tab"):
            try: getattr(self.settings, attr).restyle()
            except Exception: pass
        try: self.presets.restyle()
        except Exception: pass

    def set_theme(self, code):
        setTheme(Theme.LIGHT if code == "light" else Theme.DARK)
        global_settings["theme"] = code; save_settings()
        self._apply_palette()

    # ---- i18n ----
    def retranslate_all(self):
        try: self.presets.retranslate()
        except Exception: pass
        try: self.telemetry.retranslate()
        except Exception: pass
        try: self.settings.retranslate()
        except Exception: pass
        self.titleBar.apply_btn.setText(tr("ui.apply"))
        self._refresh_status()

    # ---- system tray ----
    def _build_tray(self):
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            icon = self._app_icon if not self._app_icon.isNull() else self.windowIcon()
            self.tray = QSystemTrayIcon(icon, self)
            self.tray.setToolTip("Legacy Wheel Hub")
            menu = QMenu()
            self.act_show = QAction(tr("tray.show"), self); self.act_show.triggered.connect(self._restore_from_tray)
            self.act_quit = QAction(tr("tray.quit"), self); self.act_quit.triggered.connect(self._quit_app)
            menu.addAction(self.act_show); menu.addSeparator(); menu.addAction(self.act_quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._tray_activated)
        except Exception:
            self.tray = None

    def update_tray_state(self):
        if not global_settings.get("minimize_to_tray", False):
            if self.tray is not None and not self.isVisible():
                self._restore_from_tray()
            if self.tray is not None:
                self.tray.hide()

    def _hide_to_tray(self):
        if self.tray is None: return
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.tray.show(); self.hide()

    def _restore_from_tray(self):
        if self.tray is not None: self.tray.hide()
        self.showNormal(); self.raise_(); self.activateWindow()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def _quit_app(self):
        if self.tray is not None: self.tray.hide()
        self._teardown()
        QApplication.quit()

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange and self.isMinimized() \
                and global_settings.get("minimize_to_tray", False) and self.tray is not None:
            QTimer.singleShot(0, self._hide_to_tray)
        super().changeEvent(e)

    # ---- live loop ----
    def _refresh_status(self):
        if state["connected"]:
            self.titleBar.set_device(active_profile["name"])
            self.titleBar.set_status("\u25cf  " + tr("conn.connected"), "#5dc98a")
        elif test_override:
            self.titleBar.set_device(active_profile["name"])
            self.titleBar.set_status("\u25cf  " + tr("conn.test"), "#a0a0a0")
        else:
            self.titleBar.set_status("\u25cf  " + tr("conn.not_connected"), "#ff6b6b")

    def _tick(self):
        cur = self.settings.stack.currentWidget()
        if hasattr(cur, "refresh"):
            try: cur.refresh()
            except Exception: pass
        self.telemetry.refresh()
        conn = bool(state["connected"])
        if conn and not self._was_connected and global_settings.get("auto_load", False):
            # arm deferred auto-load; the actual apply waits for the wheel to
            # finish its power-on calibration sweep (detected below).
            self._autoload_pending = True
            now = time.monotonic()
            self._cal_connect_t = now
            self._cal_last_move_t = now
            self._cal_prev = state.get("steer_norm", 0.0)
            self._cal_seen_sweep = False
        if not conn:
            self._autoload_pending = False
        if self._autoload_pending:
            self._check_calibration_settle()
        self._was_connected = conn
        self._refresh_status()

    def _check_calibration_settle(self):
        # On power-up the wheel auto-calibrates by driving itself lock-to-lock,
        # then rests. We must NOT send any FFB command during that sweep.
        # There is a short gap between "connected" and the sweep physically
        # starting, so we can't just wait for stillness (the pre-sweep pause
        # would look "done"). Instead we wait until we have actually SEEN the
        # sweep (the axis swung far) and it has since settled. If no sweep
        # appears within a long window, the wheel was already calibrated
        # (e.g. a hot re-plug) and we apply anyway.
        now = time.monotonic()
        n = state.get("steer_norm", 0.0)
        if abs(n - self._cal_prev) > 0.012:          # axis is moving
            self._cal_last_move_t = now
        if abs(n) > 0.45:                            # swung far -> calibration sweep
            self._cal_seen_sweep = True
        self._cal_prev = n
        settled = (now - self._cal_last_move_t) >= 0.7
        since_connect = now - self._cal_connect_t
        ready = (self._cal_seen_sweep and settled) or \
                (not self._cal_seen_sweep and since_connect >= 5.0 and settled)
        if ready and dev is not None:
            self._autoload_pending = False
            try: self.apply_settings(silent=True)
            except Exception: pass

    # ---- apply (USER's FFB flow, verbatim) ----
    def apply_settings(self, silent=False):
        if dev is None:
            if not silent:
                _defer_infobar("warning", tr("conn.not_connected"), tr("input.led_nc"), duration=2500,
                                position=InfoBarPosition.TOP, parent=self)
            return
        w = self.wheelset
        gain, spring, damper = w.s_gain.value(), w.s_spring.value(), w.s_damper.value()
        persist = w.cb_center.isChecked()
        di_center = w.s_center.value()
        di_ramp = w.s_ramp.value()
        center = di_center if persist else 0
        angle = w.s_rot.value()
        update_registry_ffb(gain, spring, damper, di_center, persist, angle)
        ffb_write(rotation_cmd(angle))
        ffb_write(autocenter_cmd(center, di_ramp))
        self._applied_angle = angle   # telemetry now matches the new range
        prof = global_settings["profiles"].setdefault(global_settings["selected_profile"], {})
        prof.update({"angle": angle, "di_gain": gain, "di_spring": spring, "di_damper": damper,
                     "di_center": di_center, "di_ramp": di_ramp, "di_persist": persist})
        save_settings()
        if not silent:
            _defer_infobar("success", tr("apply.ok_title"), tr("apply.ok_body"), duration=2000,
                            position=InfoBarPosition.TOP, parent=self)

    def _restore_geometry(self):
        # One rule instead of per-tab tinkering: the window may not shrink
        # below what the tallest tab needs, so no tab ever gets a scrollbar.
        # Clamped to the screen so it still fits on small/720p displays (there
        # scrolling is unavoidable - the content is simply taller).
        try:
            avail = QApplication.primaryScreen().availableGeometry().height()
            self.setMinimumHeight(max(560, min(792, avail - 48)))
        except Exception:
            self.setMinimumHeight(720)
        self.resize(int(global_settings.get("win_w", 1366)),
                    max(int(global_settings.get("win_h", 720)), self.minimumHeight()))
        x = global_settings.get("win_x"); y = global_settings.get("win_y")
        placed = False
        try:
            if x is not None and y is not None:
                wx, wy, ww, wh = int(x), int(y), self.width(), self.height()
                for scr in QApplication.screens():
                    g = scr.availableGeometry()
                    ox = min(wx + ww, g.right() + 1) - max(wx, g.left())
                    oy = min(wy + wh, g.bottom() + 1) - max(wy, g.top())
                    if ox >= 200 and oy >= 100:        # a usable chunk is visible
                        self.move(wx, wy); placed = True; break
        except Exception:
            placed = False
        if not placed:                                 # first run / off-screen -> center
            try:
                g = QApplication.primaryScreen().availableGeometry()
                self.move(g.x() + (g.width() - self.width()) // 2,
                          g.y() + (g.height() - self.height()) // 2)
            except Exception:
                pass

    def _teardown(self):
        global running
        running = False
        try: ffb_write(autocenter_cmd(0)); ffb_write(stop_forces_cmd())
        except Exception: pass
        try:
            if not self.isMaximized() and self.width() > 200 and self.height() > 200:
                global_settings["win_w"] = self.width(); global_settings["win_h"] = self.height()
                global_settings["win_x"] = self.x(); global_settings["win_y"] = self.y()
            global_settings["last_tab"] = self.settings.pivot.currentRouteKey() or global_settings.get("last_tab", "wheel")
        except Exception: pass
        save_settings()

    def closeEvent(self, e):
        self._teardown(); super().closeEvent(e)


def _qt_msg_filter(mode, ctx, msg):
    # qfluentwidgets emits a harmless "QFont::setPointSize: Point size <= 0"
    # on some Windows font setups; drop only that line, pass everything else.
    if "Point size" in msg and "setPointSize" in msg:
        return
    sys.stderr.write(msg + "\n")


_INSTANCE_MUTEX = None


def _acquire_single_instance():
    """Return True if this is the only running instance.

    Uses a named Windows mutex to detect a second launch. If one is already
    running, tries to surface its window (restoring it from the tray) and
    returns False so this process exits instead of fighting over the wheel.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        global _INSTANCE_MUTEX
        # session-local named mutex; kept alive for the whole process
        _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False,
                                                "LegacyWheelHub_SingleInstance")
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            try:
                user32 = ctypes.windll.user32
                hwnd = user32.FindWindowW(None, "Legacy Wheel Hub")
                if hwnd:
                    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return False
        return True
    except Exception:
        return True     # never block startup if the check itself fails


def main():
    global main_window, CURRENT_LANG, running
    if not _acquire_single_instance():
        return
    qInstallMessageHandler(_qt_msg_filter)
    saved = global_settings.get("language")
    if saved in LANG:
        CURRENT_LANG = saved
    # --- UI scaling (must be set BEFORE QApplication) ---
    try:
        s = int(global_settings.get("ui_scale", 100)) / 100.0
        if s and s != 1.0:
            # the scale multiplies the window's minimum size too, so cap it to
            # what the physical screen can actually show (otherwise the window
            # overflows the screen and the layout collapses / overlaps).
            sw, sh = _screen_px()
            BASE_W, BASE_H = 1230.0, 770.0      # app's natural minimum (logical px)
            max_s = min((sw * 0.96) / BASE_W, (sh * 0.92) / BASE_H)
            eff = max(1.0, min(s, max_s))
            if abs(eff - 1.0) > 0.01:
                os.environ["QT_SCALE_FACTOR"] = f"{eff:.4f}"
    except Exception:
        pass
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    app = QApplication(sys.argv)
    if os.path.exists(WHEEL_PNG):
        app.setWindowIcon(QIcon(WHEEL_PNG))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LegacyWheelHub.app")
        except Exception:
            pass
    setTheme(Theme.LIGHT if global_settings.get("theme") == "light" else Theme.DARK)
    setThemeColor(QColor(ACCENT))
    main_window = ControlHub()
    main_window.show()
    poller = Poller(); poller.start()
    app.exec()
    running = False
    poller.wait(2000)


if __name__ == "__main__":
    main()