"""
Legacy Wheel Hub - control panel for Logitech's legacy force-feedback wheels:
G27, G25, Driving Force GT, Driving Force Pro, Driving Force, MOMO Racing and
MOMO Force.

Copyright (C) 2026 Sadooo

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
"""
import sys, os, json, math, time, threading, struct, copy

try:
    import hid
except ImportError:
    print("ERROR: hidapi is not installed  ->  pip install hidapi"); sys.exit(1)

# Two different packages install as "hid" and they are not compatible:
# cython-hidapi (what this app is written against) exposes hid.device() with
# open_path(), while the "hid" package exposes hid.Device(path=...). Installing
# the wrong one leaves a module that imports fine and then fails at the first
# open, so adapt rather than demand a particular package.
if not hasattr(hid, "device") and hasattr(hid, "Device"):
    class _HidCompat:
        """hid.device() shape on top of the other package's hid.Device."""

        def __init__(self):
            self._d = None

        def open(self, vid, pid):
            self._d = hid.Device(vid, pid)

        def open_path(self, path):
            self._d = hid.Device(path=path)

        def write(self, data):
            # cython-hidapi takes a list of ints, the other wants bytes.
            return self._d.write(bytes(data))

        def read(self, size, timeout_ms=0):
            return list(self._d.read(size, timeout_ms or 0))

        def set_nonblocking(self, on):
            try:
                self._d.nonblocking = bool(on)
            except Exception:
                pass

        def close(self):
            if self._d is not None:
                self._d.close()
                self._d = None

    hid.device = _HidCompat

try:
    import winreg
except ImportError:
    winreg = None

from PySide6.QtCore import Qt, QTimer, QThread, QRectF, QPointF, Signal, QEvent, QSize, qInstallMessageHandler, QFileInfo
from PySide6.QtGui import QPainter, QColor, QPixmap, QPolygonF, QFont, QPen, QIcon, QAction, QImage, QPainterPath, QFontMetrics
from PySide6.QtWidgets import (QApplication, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
                               QFrame, QSizePolicy, QScrollArea, QSystemTrayIcon, QMenu,
                               QStackedWidget, QFileDialog,
                               QLabel, QDialog)

# QFileIconProvider moved between QtWidgets and QtGui across Qt6/PySide6
# releases; import defensively so a wrong location can never crash startup.
try:
    from PySide6.QtGui import QFileIconProvider
except Exception:
    try:
        from PySide6.QtWidgets import QFileIconProvider
    except Exception:
        QFileIconProvider = None

from qfluentwidgets import (setTheme, Theme, setThemeColor,
                            FluentIcon as FIF, PushButton, PrimaryPushButton, Slider, LineEdit,
                            BodyLabel, StrongBodyLabel, TitleLabel, SubtitleLabel, CaptionLabel,
                            CheckBox, InfoBar, InfoBarPosition, isDarkTheme,
                            ComboBox, TransparentToolButton, ToolButton, Pivot, TransparentPushButton,
                            SingleDirectionScrollArea)
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

def _is_portable():
    """A built copy with portable.txt beside its exe is the portable version:
    it keeps its settings and LUTs in a data folder next to itself, so the
    whole folder can live on a USB stick or move to another PC (as v2)."""
    return bool(getattr(sys, "frozen", False)) and os.path.isfile(os.path.join(_exe_dir(), "portable.txt"))


PORTABLE = _is_portable()


def _data_dir():
    # persistent, writable location for settings (works under Program Files)
    if PORTABLE:
        d = os.path.join(_exe_dir(), "data")
    elif getattr(sys, "frozen", False):
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
    for d in (_exe_dir(), _resource_dir(), os.path.dirname(os.path.abspath(__file__)),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")):
        p = os.path.join(d, "wheel.png")
        if os.path.exists(p):
            return p
    return os.path.join(_exe_dir(), "wheel.png")


_LUT_DIR_CACHE = None


def _lut_dir():
    """Folder that holds imported .lut files AND the proxy log.

    Lives inside the application folder (next to the exe) so everything is
    self-contained and portable, not under %LOCALAPPDATA%. Falls back to the
    writable data dir only if the exe folder cannot be written to.

    The answer is cached. This is called from the game-watcher thread every
    two seconds and once per profile on every publish; probing by writing a
    file each time meant constant disk writes, and two threads probing the
    same ".w" file at once could make one of them fail and fall back to the
    other folder - so the LUT path written to the registry could flip.
    """
    global _LUT_DIR_CACHE
    if _LUT_DIR_CACHE and os.path.isdir(_LUT_DIR_CACHE):
        return _LUT_DIR_CACHE
    # the portable version keeps them with its settings, in data\luts
    d = os.path.join(_data_dir() if PORTABLE else _exe_dir(), "luts")
    try:
        os.makedirs(d, exist_ok=True)
        # quick writability probe (per-thread name, so probes never collide)
        t = os.path.join(d, ".w%d" % threading.get_ident())
        with open(t, "w") as f:
            f.write("")
        os.remove(t)
    except Exception:
        d = os.path.join(_data_dir(), "luts")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    _LUT_DIR_CACHE = d
    return d


def running_exe_paths():
    """{lowercased full exe path: process start time} for visible processes.

    PROCESS_QUERY_LIMITED_INFORMATION is used on purpose: it is enough to read
    a process's image path and start time, and unlike QUERY_INFORMATION it
    does not need admin rights for same-user processes. Anything we cannot
    open (elevated or protected processes) is simply skipped - a game we can't
    see is no worse than one that isn't running.
    """
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        from ctypes import wintypes
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        count = 1024
        while True:
            arr = (wintypes.DWORD * count)()
            need = wintypes.DWORD()
            if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(need)):
                return {}
            if need.value < ctypes.sizeof(arr):
                break
            count *= 2                      # buffer was full: there may be more
        pids = arr[:need.value // ctypes.sizeof(wintypes.DWORD)]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        out = {}
        buf = ctypes.create_unicode_buffer(32768)
        for pid in pids:
            if not pid:
                continue
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                continue
            try:
                size = wintypes.DWORD(len(buf))
                if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    continue
                path = buf.value
                created = wintypes.FILETIME()
                dummy = wintypes.FILETIME()
                started = 0
                if k32.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(dummy),
                                       ctypes.byref(dummy), ctypes.byref(dummy)):
                    started = (created.dwHighDateTime << 32) | created.dwLowDateTime
                # Keep the most recently started instance of a given exe.
                key = path.lower()
                if started >= out.get(key, -1):
                    out[key] = started
            finally:
                k32.CloseHandle(h)
        return out
    except Exception:
        return {}


def _exe_key(exe):
    """A game's exe as it is compared everywhere: resolved, one separator,
    lower case."""
    if not exe:
        return ""
    return (resolve_game_exe(exe) or exe).replace("/", "\\").lower()


def exe_owner(profiles, exe, skip=None):
    """Name of another profile already bound to this game, or None.

    One game, one profile: the per-game registry key holds one LUT, so two
    profiles on the same exe left it to whichever was written last."""
    key = _exe_key(exe)
    if not key:
        return None
    for name, prof in profiles.items():
        if name != skip and _exe_key((prof or {}).get("exe_path", "")) == key:
            return name
    return None


def match_running_profile(profiles, running=None):
    """Name of the profile whose game is running, or None.

    When several match (a launcher left open, two sims running), the most
    recently started one wins - that is the one the user just switched to.
    """
    running = running_exe_paths() if running is None else running
    if not running:
        return None
    best, best_t = None, -1
    for name, prof in list(profiles.items()):
        exe = (prof or {}).get("exe_path", "")
        if not exe:
            continue
        # Exact path match only, normalised for separator and case. Matching
        # on the file name alone was tried and dropped: plenty of games ship a
        # generic "launcher.exe"/"game.exe", so an unrelated program could
        # silently swap the user's wheel profile. Applying the WRONG profile is
        # worse than applying none, and the exe path comes from the user's own
        # file picker, so it matches what actually runs.
        target = (resolve_game_exe(exe) or exe).replace("/", "\\").lower()
        t = running.get(target)
        if t is not None and t > best_t:
            best, best_t = name, t
    return best


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "LegacyWheelHub"


def _startup_command():
    """Command Windows should run at logon, quoted for paths with spaces.

    Frozen builds point at the exe. Running from source we launch pythonw so
    no console window appears; registering bare sys.executable would start a
    Python prompt instead of the app.
    """
    # --tray marks a logon launch. Coming up as a full window every time you
    # sign in is intrusive for something that just sits there configuring a
    # wheel, so a logon start goes to the tray when the user has enabled it.
    if getattr(sys, "frozen", False):
        return '"%s" --tray' % sys.executable
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    return '"%s" "%s" --tray' % (pyw if os.path.isfile(pyw) else py,
                                 os.path.abspath(__file__))


def startup_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, RUN_VALUE)[0])
    except Exception:
        return False


def set_startup(enable):
    """Add/remove the logon entry. HKCU, so no admin rights are needed."""
    if winreg is None:
        return False
    try:
        if enable:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, RUN_VALUE)
            except FileNotFoundError:
                pass            # already absent - nothing to undo
        return True
    except Exception:
        return False


def _proxy_game_key(exe_path):
    """Registry-safe key for a game's exe path — MUST match the proxy DLL's
    LwhGameKey() (proxy.h) exactly: same exe path in, same key out, so each
    game's proxy only ever sees the value written for its own exe."""
    real = (resolve_game_exe(exe_path) or exe_path or "").lower()
    return "".join(c if c.isalnum() else "_" for c in real)


def set_active_lut(path, exe_path=None):
    """Tell the dinput8 proxy which LUT to use (and where to log).

    The proxy DLL runs inside game processes and has no idea where LWH is
    installed, so we publish the active LUT's absolute path in the registry
    (HKCU\\Software\\LegacyWheelHub). The DLL polls this and hot-reloads.
    Pass path=None/"" to disable (passthrough).

    Writing is scoped per game (under Games\\<exe-derived key>) whenever
    exe_path is given: whichever LUT a DIFFERENT game's proxy is already
    applying must never change just because the LWH window happens to be
    showing another profile. exe_path=None still writes the legacy global
    key too, for a proxy DLL that hasn't been reinstalled since the update.
    """
    if winreg is None:
        return
    try:
        if exe_path:
            # Per-game only. Writing the shared legacy key here as well would
            # let each profile in _publish()'s loop overwrite it in turn, so a
            # proxy polling mid-loop could momentarily read another game's LUT.
            gkey = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\LegacyWheelHub\Games\%s" % _proxy_game_key(exe_path))
            winreg.SetValueEx(gkey, "ActiveLut", 0, winreg.REG_SZ, path or "")
            winreg.SetValueEx(gkey, "LogDir", 0, winreg.REG_SZ, _lut_dir())
            winreg.CloseKey(gkey)
        else:
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


# Text every build of our dinput8.dll carries (UTF-16, from dllmain.cpp and
# the copy it makes of the real DLL). The marker file alone said whose DLL it
# was; another mod installed over ours left the marker behind, and the next
# install or "LUT off" overwrote or deleted that mod's DLL.
_PROXY_SIGNATURES = ("LWH dinput8 proxy".encode("utf-16-le"),
                     "lwh_dinput8_real_".encode("utf-16-le"))


def is_our_proxy(path):
    """Whether the DLL at path is ours, judged by its contents."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return False
    return any(sig in data for sig in _PROXY_SIGNATURES)


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
    if os.path.isfile(dst) and not is_our_proxy(dst) and not overwrite_foreign:
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
    if os.path.isfile(dst) and not is_our_proxy(dst) and not force:
        # not ours -> leave it; a marker left from before no longer applies
        if os.path.isfile(marker):
            try: os.remove(marker)
            except Exception: pass
        return False
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
HUB_VERSION = "v1.2.0"
AUTHOR = "Sadooo"
TRANSLATORS = "Andrii (and-ri)"          # Українська, Русский


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
        "name": "Logitech Driving Force GT", "pid_native": 0xC29A, "verified": True,
        "pedals": 2, "range_cmd": "g25",
        "registry_pids": ["VID_046D&PID_C29A", "VID_046D&PID_C294"],
        "steer": {"fmt": "lohi", "lo": 4, "hi": 5, "himask": 0x3F, "center": 8192, "half": 8192},
        "throttle": 6, "brake": 7, "clutch": None, "pedal_invert": True,
        "info": {"model": "Logitech Driving Force GT", "hwid": "USB\\VID_046D & PID_C29A",
                 "interface": "USB 2.0 (Full-Speed)", "power": "24V DC",
                 "tracking": "High-Resolution Optical Encoder",
                 "axis": "14-bit (16,384 Steps)", "ffb": "Single-Motor, Gear-Driven",
                 "polling": "~500 Hz", "api": "Direct HID RAW"},
    },
    # --- The older wheels ----------------------------------------------------
    #
    # Everything below is taken from two sources that were written without
    # each other and agree wherever they overlap:
    #
    #   Logitech's own driver, from Logitech Gaming Software 5.10:
    #     wmjoyhid.inf  - per wheel, the number of buttons and each button's
    #                     name, in order, plus which axes exist
    #     wmfilter.sys  - the native-mode switch commands, as a table
    #     wmhidlo.sys   - the rotation command templates (DFP separate)
    #   The Linux kernel's Logitech driver (hid-lg.c, hid-lg4ff.c):
    #     corrected report descriptors for DF, DFP, MOMO Force and MOMO
    #     Racing, which give each field's exact position in the report, and
    #     how to tell the wheels apart while they are in compatibility mode
    #
    # The button order in the INF was checked against the one wheel here
    # whose buttons were measured bit by bit: all 21 DFGT buttons sit in the
    # report in exactly the INF's order. The button counts in the INF match
    # the Linux descriptors for every wheel (DFP 14, DF 12, MOMO Racing 10,
    # MOMO Force 8).
    #
    # Pedals: the Linux descriptors give the two pedal bytes; which is the
    # accelerator is the family convention - accelerator first, brake second
    # - which is how both measured wheels (G27, DFGT) report them and agrees
    # with the INF, where the brake is Rz on every wheel.
    #
    # Why the steering is no longer read from the wheel's own descriptor:
    # Linux replaces the DF, DFP and MOMO descriptors because the wheels'
    # own are wrong. Reading the angle through them, as the first version of
    # these profiles did, reads the wrong bits on exactly these wheels.
    #
    # The G25 is read entirely from its own descriptor, which is correct for
    # it: Linux uses it unchanged, and Logitech's filter driver carries
    # replacement descriptors for three older devices and not for this one
    # (checked in wmfilter.sys, LGS 5.10). The descriptor gives each field's
    # position; which field is which pedal comes from wmjoyhid.inf, which
    # names the G25's axes - accelerator Z, brake Rz, clutch on the slider,
    # Y for the driver's combined-pedals axis (see descriptor_pedals). Its 19
    # buttons carry no names there, and Logitech's own software showed them
    # as numbers; they are shown here the same way. A layout taught through
    # the setup wizard still takes precedence where one exists.
    #
    # "range_cmd": G25, G27 and DFGT take 0xF8 0x81; the Driving Force Pro
    # has its own two-step command; MOMO, MOMO Force and Driving Force have
    # no adjustable range and are fixed at 270 degrees.
    "DFP": {
        "name": "Logitech Driving Force Pro", "pid_native": 0xC298,
        "pedals": 2, "range_cmd": "dfp",
        "registry_pids": ["VID_046D&PID_C298", "VID_046D&PID_C294"],
        # 14-bit wheel in bits 0-13, 14 buttons from bit 14, hat in byte 3
        "steer": {"fmt": "lohi", "lo": 0, "hi": 1, "himask": 0x3F, "center": 8192, "half": 8192},
        "throttle": 5, "brake": 6, "clutch": None, "pedal_invert": True,
        "buttons": {"first_bit": 14, "names": [
            "Cross", "Square", "Circle", "Triangle", "R1", "L1", "R2", "L2",
            "Select", "Start", "R3", "L3", "Shift +", "Shift \u2212"]},
        "hat": {"byte": 3, "shift": 4},
        "info": {"model": "Logitech Driving Force Pro", "hwid": "USB\\VID_046D & PID_C298",
                 "interface": "USB 2.0 (Full-Speed)", "power": "24V DC",
                 "tracking": "Optical Encoder",
                 "axis": "14-bit (16,384 Steps)", "ffb": "Single-Motor, Gear-Driven",
                 "polling": "~500 Hz", "api": "Direct HID RAW"},
    },
    "G25": {
        "name": "Logitech G25 Racing Wheel", "pid_native": 0xC299,
        "pedals": 3, "range_cmd": "g25",
        "registry_pids": ["VID_046D&PID_C299", "VID_046D&PID_C294"],
        "steer": None, "throttle": None, "brake": None, "clutch": None,
        "pedal_invert": True, "button_count": 19,
        "info": {"model": "Logitech G25 Racing Wheel", "hwid": "USB\\VID_046D & PID_C299",
                 "interface": "USB 2.0 (Full-Speed)", "power": "24V DC",
                 "tracking": "High-Resolution Optical Encoder",
                 "axis": "14-bit (16,384 Steps)", "ffb": "Dual-Motor, Straight Gear",
                 "polling": "~500 Hz", "api": "Direct HID RAW"},
    },
    "DF": {
        # Also what every multimode wheel reports as while it is still in
        # compatibility mode (C294): the same layout, emulated.
        "name": "Logitech Driving Force", "pid_native": 0xC294,
        "pedals": 2, "range_cmd": None,
        "registry_pids": ["VID_046D&PID_C294"],
        # 10-bit wheel in bits 0-9, 12 buttons from bit 10, hat in byte 4
        "steer": {"fmt": "lohi", "lo": 0, "hi": 1, "himask": 0x03, "center": 512, "half": 512},
        "throttle": 5, "brake": 6, "clutch": None, "pedal_invert": True,
        "buttons": {"first_bit": 10, "names": [
            "Cross", "Square", "Circle", "Triangle", "R1", "L1", "R2", "L2",
            "Select", "Start", "Paddle R", "Paddle L"]},
        "hat": {"byte": 4, "shift": 0},
        "info": {"model": "Logitech Driving Force", "hwid": "USB\\VID_046D & PID_C294",
                 "interface": "USB 1.1 (Full-Speed)", "power": "12V DC",
                 "tracking": "Optical Encoder",
                 "axis": "10-bit (1,024 Steps)", "ffb": "Single-Motor, Gear-Driven",
                 "polling": "~125 Hz", "api": "Direct HID RAW"},
    },
    "MOMO": {
        "name": "Logitech MOMO Racing", "pid_native": 0xCA03,
        "pedals": 2, "range_cmd": None,
        "registry_pids": ["VID_046D&PID_CA03"],
        # 10-bit wheel in bits 0-9, 10 buttons from bit 10, no hat
        "steer": {"fmt": "lohi", "lo": 0, "hi": 1, "himask": 0x03, "center": 512, "half": 512},
        "throttle": 4, "brake": 5, "clutch": None, "pedal_invert": True,
        "buttons": {"first_bit": 10, "names": [
            "Paddle L", "Paddle R", "3", "4", "5", "6", "7", "8",
            "Shift +", "Shift \u2212"]},
        "info": {"model": "Logitech MOMO Racing", "hwid": "USB\\VID_046D & PID_CA03",
                 "interface": "USB 1.1 (Full-Speed)", "power": "12V DC",
                 "tracking": "Optical Encoder",
                 "axis": "10-bit (1,024 Steps)", "ffb": "Single-Motor, Gear-Driven",
                 "polling": "~125 Hz", "api": "Direct HID RAW"},
    },
    "MOMOFORCE": {
        "name": "Logitech MOMO Force", "pid_native": 0xC295,
        "pedals": 2, "range_cmd": None,
        "registry_pids": ["VID_046D&PID_C295"],
        # 10-bit wheel in bits 0-9, 8 buttons from bit 10, no hat
        "steer": {"fmt": "lohi", "lo": 0, "hi": 1, "himask": 0x03, "center": 512, "half": 512},
        "throttle": 4, "brake": 5, "clutch": None, "pedal_invert": True,
        "buttons": {"first_bit": 10, "names": [
            "Paddle L", "Paddle R", "3", "4", "5", "6", "7", "8"]},
        "info": {"model": "Logitech MOMO Force", "hwid": "USB\\VID_046D & PID_C295",
                 "interface": "USB 1.1 (Full-Speed)", "power": "12V DC",
                 "tracking": "Optical Encoder",
                 "axis": "10-bit (1,024 Steps)", "ffb": "Single-Motor, Gear-Driven",
                 "polling": "~125 Hz", "api": "Direct HID RAW"},
    },
    "G27": {
        "name": "Logitech G27 Racing Wheel", "pid_native": 0xC29B, "verified": True,
        "pedals": 3, "range_cmd": "g25",
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
        "input.doc_notice": "Button names and order from Logitech's own driver. Where each button sits on the rim is not recorded there, so they are listed rather than drawn.",
        "input.grid_notice": "Buttons learned on this machine, numbered in the order you pressed them. Where they sit on the wheel is not something the setup can find out.",
        "input.grid_notice_desc": "Buttons numbered as the wheel itself reports them - the same numbers games and Logitech's own software show.",
        "map.step_buttons": "Press every button in turn",
        "map.hint_buttons": "Go round the wheel: face buttons, paddles, then the shifter if it has one. Each press is recorded in order. Next when you are done.",
        "map.title": "Wheel setup",
        "map.next": "Next",
        "map.skip": "Skip",
        "map.restart": "Start over",
        "map.save": "Save",
        "map.yes": "Yes",
        "map.no": "No",
        "map.result": "Done",
        "map.step_intro": "Before you start",
        "map.hint_intro": "Three things affect the results.\n\nPress each pedal to the floor a few times first. The wheel learns their travel as it sees it, so a pedal that has never been pressed fully reports a shortened range.\n\nUse the community driver package for Windows 10/11. If you have replaced it with a different driver, most of this will come back empty.\n\nRun a game for a few seconds. The driver only creates its force-feedback block once a game asks for it, so without that the check below finds nothing.",
        "map.step_steer": "Turn the wheel lock to lock",
        "map.hint_steer": "All the way left, then all the way right. Press Next once you have reached both ends.",
        "map.step_rest": "Leave the pedals alone",
        "map.hint_rest": "Take your feet off, then press Next.",
        "map.step_throttle": "Press the throttle",
        "map.hint_throttle": "All the way down at least once, then Next.",
        "map.step_brake": "Press the brake",
        "map.hint_brake": "All the way down at least once, then Next.",
        "map.step_clutch": "Press the clutch",
        "map.hint_clutch": "All the way down at least once, then Next. Skip if this wheel has none.",
        "map.step_probe": "Checking the driver",
        "map.hint_probe": "Nothing to do here. Reading the force-feedback block and the wheel's pedal table.",
        "map.step_ffb": "Did the wheel push back?",
        "map.hint_ffb": "It was pushed left, then right. Answer No if nothing moved.",
        "map.step_curve": "Press the throttle. Does it stall halfway?",
        "map.hint_curve": "A dip was written into the throttle curve. Either answer undoes it.",
        "map.step_range": "Turn the wheel. Is the travel much shorter?",
        "map.hint_range": "The range was set to 200 degrees. Either answer restores it.",
        "map.live_waiting": "Waiting for the wheel",
        "map.live_steer": "left {0}   right {1}   span {2}",
        "map.live_rest": "Reading the resting position",
        "map.live_pedal": "movement seen: {0}",
        "map.ffb_notsent": "Nothing was sent. The wheel is not open for writing.",
        "map.steer_ok": "Steering reads correctly.",
        "map.steer_none": "No steering movement was seen.",
        "map.verify": "Press each pedal. Only the marked bars should move.",
        "map.nothing": "Nothing moved far enough to identify. Start over and press each pedal to the floor.",
        "map.button": "Set up this wheel…",
        "map.button_h": "Finds where the steering and pedals sit in this wheel's report so telemetry works, and checks which features it supports. Takes a couple of minutes.",
        "map.clear": "Use built-in layout",
        "map.cleared": "Measured layout removed",
        "map.share": "Share this setup",
        "map.share_title": "Help finish support for this wheel",
        "map.share_body": "Everything needed to add your wheel properly. It describes the hardware only — no profiles, no file paths, no game names.",
        "map.share_copied": "Copied to your clipboard. Send it wherever suits you, or close this and send nothing.",
        "map.share_github": "Open a GitHub issue",
        "map.share_mail": "Send by email",
        "map.share_folder": "Show the file",
        "map.share_close": "Close",
        "map.share_saved": "Saved to {0}",
        "map.browser_title": "Open Gmail?",
        "map.browser_body": "This opens a Gmail window in your browser with the report filled in. It is already on your clipboard either way.",
        "map.browser_ok": "Open browser",
        "dev.bar_dismiss": "Hide until next time",
        "dev.bar_unmapped": "This wheel is still in beta. Its layout has not been measured, so telemetry stays empty until you map it.",
        "dev.bar_mapped": "This wheel is still in beta. It has been mapped on this machine; force feedback and rotation are not verified on this model.",
        "dev.beta_action": "Set it up",
        "dev.bar_unverified": "This wheel works from Logitech's own driver data, but nobody has confirmed it on real hardware yet. A short check from you would settle it for everyone with this model.",
        "dev.verify_action": "Check it",
        "map.step_center": "Autocentre spring",
        "map.hint_center": "Turn the wheel away from centre and let go. Does a spring pull it back?",
        "dev.beta_remap": "Map again",
        "map.step_of": "Step {0} of {1}",
        "dev.beta": "Beta",
        "input.beta_notice": "The layout for this wheel has not been mapped yet, so the overlay is not drawn. Buttons and axes still reach your games normally.", "conn.not_connected": "Not Connected", "home.steering": "STEERING ANGLE",
        "home.clutch": "CLUTCH", "home.brake": "BRAKE", "home.throttle": "THROTTLE", "home.na": "N/A", "prof.add": "New profile",
        "prof.dup": "Duplicate profile",
        "prof.del": "Delete profile",
        "prof.new_hint": "Profile name", "prof.ren_title": "Rename Profile",
        "prof.del_title": "Delete Profile", "prof.del_msg": "Delete profile \u201c{0}\u201d? This cannot be undone.",
        "ffb.reset_ok": "Driver FFB reset", "ffb.reset_ok_body": "All app-written FFB registry values were removed.",
        "prof.exe_taken": "Game already has a profile", "prof.exe_taken_body": "This game is already set for the profile “{0}”. One game can belong to one profile, so the game was not changed here.",
        "prof.name_taken": "Name already in use", "prof.name_taken_body": "A profile named \u201c{0}\u201d already exists, so the name was left unchanged.",
        "input.mode_numbers": "Show button numbers", "input.mode_names": "Show button names",
        "ui.autogame": "Auto-switch by game", "ui.autogame_h": "Applies the profile of the running game.", "game.detected": "Game detected", "game.detected_body": "{0} preset applied.",
        "set.startup": "Start with Windows", "set.startup_h": "Launches the app when you sign in. Off by default.",
        "set.updates_sec": "UPDATES", "set.updates": "Check for updates on startup", "set.updates_h": "Checks GitHub once a day. Nothing is installed automatically.",
        "set.check_now": "Check now", "set.checking": "Checking\u2026",
        "upd.available": "Update available", "upd.available_body": "Version {0} is available.", "upd.open": "Download",
        "upd.current": "Up to date", "upd.current_body": "You are running the latest release.",
        "upd.failed": "Update check failed", "upd.failed_body": "Could not reach GitHub. Check your connection and try again.", "dlg.ok": "OK", "dlg.cancel": "Cancel", "dlg.delete": "Delete",
        "wheel.overall": "Overall Effects Strength",
        "wheel.overall_h": "101% removes the center FFB deadzone in most games.",
        "wheel.spring": "Spring Effect", "wheel.spring_h": "How strongly the game's own spring effects reach the wheel - the pull back to the centre many games use. 100% is as the game sends it, 0% removes it.",
        "wheel.damper": "Damper Effect", "wheel.damper_h": "How strongly the game's own damper effects reach the wheel - the weight that resists fast turning. 100% is as the game sends it, 0% removes it.",
        "wheel.center_cb": "Enable Centering Spring in FFB games",
        "wheel.center": "Centering Spring", "wheel.center_h": "Driver-based autocenter strength.",
        "wheel.ramp": "Centering Ramp", "wheel.ramp_h": "Shapes the centering spring. No effect at 0%.", "wheel.rotation": "Rotation Range",
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
        "ffb.stop": "STOP ALL FORCES", "input.led": "LED Greeting Test",
        "input.wheel": "WHEEL", "input.shifter": "SHIFTER UNIT", "input.gear": "GEAR  (H-PATTERN)",
        "input.lpad": "LEFT PADDLE", "input.rpad": "RIGHT PADDLE",
        "input.led_nc": "Wheel not connected.",
        "input.led_g27": "LED test is G27-only (DFGT has no RPM LEDs).",
        "input.led_run": "LED greeting\u2026 (works if the driver passes the report through)", "about.hwid": "Hardware ID", "about.axis": "Axis Resolution",
        "about.ffb": "Force Feedback",
        "about.theme_dark": "Dark", "about.theme_light": "Light", "about.testmode_h":
            "Switch the active wheel layout without hardware connected. \u201cAuto\u201d uses real detection.",
        "about.test_auto": "Auto (detect)", "about.footer":
            "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "HARDWARE DIAGNOSTICS", "about.sec_sensor": "SENSOR & FFB SPECIFICATIONS",
        "about.sec_sw": "SOFTWARE & DRIVER STATUS", "about.sec_credits": "CREDITS",
        "about.devmodel": "Device Model", "about.interface": "Interface", "about.power": "Power Status",
        "about.tracking": "Tracking System", "about.polling": "Max Polling Rate",
        "about.opmode": "Operating Mode", "about.api": "API Hook", "about.hub": "Hub Version",
        "about.author": "Author", "about.sec_about": "ABOUT", "about.repo": "GitHub repository", "about.license": "License: GPL-3.0", "about.portable": "Portable", "about.disclaimer": "Not affiliated with Logitech. All trademarks belong to their respective owners.",
        "tray.show": "Show", "tray.quit": "Quit",
        "apply.ok_title": "Applied", "apply.ok_body": "Settings applied to the wheel.",
        "ui.presets": "PRESETS", "ui.autoload": "Auto-load on connect",
        "ui.telemetry": "LIVE TELEMETRY", "ui.center": "Center",
        "ui.apply": "APPLY", "conn.connected": "Connected", "conn.test": "Test Mode",
        "tab.wheel": "WHEEL", "tab.pedals": "PEDALS", "pedals.gamma": "Gamma", "pedals.deadzone": "Deadzone", "pedals.saturation": "Saturation", "pedals.custom": "Custom", "pedals.reset": "Back to factory", "pedals.note": "Held in the wheel's memory - unplugging it restores the factory table.", "pedals.combine_title": "Combined pedals", "pedals.combine": "Report combined pedals", "pedals.combine_info": "Throttle and brake on one axis, for older games that expect it. Logitech's driver reads this when the wheel starts: Windows asks for permission once, then the wheel restarts.", "pedals.combine_on": "Pedals combined. The wheel restarted.", "pedals.combine_off": "Pedals separate again. The wheel restarted.", "pedals.combine_replug": "Saved. Unplug the wheel and plug it back in to apply it.", "pedals.combine_denied": "Not changed: Windows' permission was not given.", "pedals.combine_failed": "The setting could not be saved.", "lgs.game": "The game", "lgs.set_range": "{0} set the rotation to {1}°.", "lgs.set_ffb": "{0} changed the force feedback settings.", "lgs.allow": "Allow the game to adjust settings", "lgs.allow_h": "Logitech Gaming Software's \"Allow Game To Adjust Settings\", working as it did there: a game built with Logitech's wheel SDK, and Content Manager's hardware lock, may set the rotation, force feedback on/off, strength, spring and damper while it runs. The game's own profile decides whether it may, otherwise Global does. Nothing is saved; when the game ends, this profile's values come back. LWH also keeps the values in force where LGS kept them, so Content Manager sends back what the wheel already has.", "cm.title": "Content Manager hardware lock", "cm.state_ours": "Set up: each car's lock reaches the wheel through LWH.", "cm.state_none": "Not set up.", "cm.state_other": "Another LogitechSteeringWheel.dll is in Content Manager's folder (LGS's own?).", "cm.install": "Set up", "cm.remove": "Remove", "cm.pick": "Find Content Manager.exe", "cm.installed": "Set up. Restart Content Manager, then turn on Hardware lock in its controls settings.", "cm.other": "Content Manager's folder already has another LogitechSteeringWheel.dll; it was left alone.", "cm.error": "Could not copy the file into Content Manager's folder.", "cm.removed": "Removed from Content Manager's folder.", "sdk.title": "Logitech SDK for games", "set.compat": "COMPATIBILITY", "sdk.h": "Only for games built with Logitech's engine plugin (some Unity and Unreal games) that give no force feedback without Logitech Gaming Software or G HUB. While on, LWH's own SDK file is loaded into those games. Keep it off for games with anti-cheat.", "sdk.state_ours": "Registered: games with Logitech's engine plugin use LWH.", "sdk.state_none": "Not registered.", "sdk.state_other": "Logitech's own software is registered.", "sdk.register": "Register", "sdk.remove": "Remove", "sdk.done_on": "Registered. Games that reach Logitech's SDK through their engine now find the wheel through LWH; restart a game that is running.", "sdk.done_off": "The registration was removed.", "sdk.denied": "Not changed: Windows' permission was not given.", "sdk.failed": "The registration could not be changed.", "cm.take_over": "Use LWH's", "sdk.take_over": "Use LWH's", "cm.taken": "Set up. The other file was kept aside and comes back when you remove LWH's. Restart Content Manager.", "sdk.done_take": "Registered. Logitech's registration was kept aside and comes back when you remove LWH's; restart a game that is running.", "keymap.none": "None", "keymap.keys": "Keys", "keymap.macro": "Macro", "keymap.macro_n": "Macro, {0} steps", "keymap.pick": "Pick a button", "keymap.click": "Click, then press the keys", "keymap.press": "Press the keys…", "keymap.record": "Click to record", "keymap.delays": "Record the waits", "keymap.repeat": "Repeat while held", "keymap.remove_step": "Remove", "keymap.add_wait": "Add wait", "keymap.clear": "Clear", "keymap.open": "Key assignment…", "keymap.title": "Key assignment", "keymap.close": "Done", "keymap.help": "Profile “{0}”. A wheel button presses keyboard keys or plays a macro in every game. Press a button on the wheel to pick it. Nothing is sent while Legacy Wheel Hub is in front.", "keymap.gear_n": "Gear {0}", "keymap.shifter_n": "Shifter {0}", "keymap.button_n": "Button {0}", "keymap.recording_n": "Recording - {0} keys (click elsewhere to stop)", "lgs.title": "Game settings", "lgs.set_by": "Set by {0}: {1}", "pedals.unsupported": "This wheel does not hand over its firmware pedal table, so curves set here cannot reach it. The pedals themselves read normally - use the game's own pedal settings for curves.",  "tab.ffb": "FFB TEST", "tab.input": "INPUT MONITOR",
        "tab.lut": "LUT",
        "lut.sec": "FFB POST-PROCESSING", "lut.enable": "Enable FFB post-processing",
        "lut.enable_h": "Pass the game's force feedback through the selected LUT curve.",
        "lut.mode": "Applied via", "lut.mode_proxy": "Helper DLL (dinput8)", "lut.mode_direct": "Driver (no DLL)",
        "lut.mode_proxy_h": "Places a dinput8.dll next to the game. Works on every wheel.",
        "lut.mode_direct_h": "No file in the game folder, and changes are instant. Needs a wheel whose driver exposes the curve.",
        "lut.mode_unsupported": "Not available on this wheel", "lut.mode_unsupported_h": "This wheel's driver does not expose the curve, so the DLL stays in use here.",
        "lut.select": "LUT curve", "lut.import": "Import LUT", "lut.delete": "Delete LUT", "lut.none": "(none)",
        "settings.unreadable_title": "Settings could not be read", "settings.unreadable_body": "settings.json was damaged, so the app started with default settings. The old file was kept as {0} - your profiles are in it.",
        "lut.gen_global_title": "LUT saved", "lut.gen_global_body": "{0} is in the LUT list. Global has no game, so it was not switched on here: select a game's profile and choose it there.",
        "lut.del_title": "Delete LUT", "lut.del_body": "Delete the LUT file \u201c{}\u201d from disk? This cannot be undone.",
        "lut.deleted": "LUT deleted",
        "lut.empty": "No LUT files yet. Click \u201cImport LUT\u201d to add one.",
        "lut.warn": "\u26a0  Places a DLL next to the game \u2014 online use at your own risk.",
        "lut.warn_direct": "No files are added to the game folder.",
        "lut.global_notice": "LUT is set per game. Create a game profile and pick its LUT here.",
        "lut.axis_in": "Input", "lut.axis_out": "Output",
        "lut.imported": "LUT imported", "lut.import_fail": "Could not import LUT",
        "lut.exe_none": "No game selected",
        "prof.edit": "Edit profile", "prof.exe": "Game executable",
        "prof.exe_hint": "For UE launcher games, pick the real ...-Shipping.exe if it isn't found automatically.",
        "prof.logo": "Icon (from an .exe)", "prof.logo_pick": "Choose icon\u2026",
        "prof.exe_pick": "Choose game\u2026", "prof.name_lbl": "Profile name",
        "proxy.installed": "Proxy installed",
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
        "set.tray_h": "Minimize hides the app to the system tray.",
        "set.devmode": "Device detection mode",
        "set.devmode_h": "Force a wheel layout without hardware.", "set.ui_scale": "Interface scale", "set.ui_scale_h": "For 1440p and 4K screens. Applies after a restart.", "set.restart_hint": "Restart the app to apply the new scale.",
        "info.opmode_active": "Native Advanced Mode (Unlocked)", "info.opmode_idle": "Idle / Disconnected",
        "info.active": "Active", "info.standby": "Standby",
        "about.translation": "Translation",
        "lut.replace_title": "Replace the LUT?",
        "lut.replace_body": "There is already a {0}. Replace it with the imported one?",
        "dlg.overwrite": "Overwrite",
        "lut.direct_waiting": "Waiting for a game: the driver's curve is checked when a game starts force feedback. Until then no LUT is applied.",
        "lut.direct_none": "This wheel's driver does not expose the curve, so no LUT is applied. Choose the DLL instead.",
    },
    "tr": {
        "input.doc_notice": "Tuş isimleri ve sırası Logitech'in kendi sürücüsünden. Tuşların direksiyonda nerede durduğu orada kayıtlı değil; bu yüzden çizilmek yerine listeleniyor.",
        "input.grid_notice": "Bu makinede öğrenilen tuşlar, bastığınız sırayla numaralandı. Direksiyonun neresinde olduklarını kurulum bulamaz.",
        "input.grid_notice_desc": "Tuşlar, direksiyonun kendisinin bildirdiği numaralarla - oyunların ve Logitech'in kendi yazılımının gösterdiği numaralar.",
        "map.step_buttons": "Tüm tuşlara sırayla basın",
        "map.hint_buttons": "Direksiyonu turlayın: ön tuşlar, kulakçıklar, varsa vites kolu. Her basış sırayla kaydedilir. Bitince İleri'ye basın.",
        "map.title": "Direksiyon kurulumu",
        "map.next": "İleri",
        "map.skip": "Atla",
        "map.restart": "Baştan başla",
        "map.save": "Kaydet",
        "map.yes": "Evet",
        "map.no": "Hayır",
        "map.result": "Tamamlandı",
        "map.step_intro": "Başlamadan önce",
        "map.hint_intro": "Sonuçları üç şey etkiliyor.\n\nÖnce her pedala birkaç kez sonuna kadar basın. Direksiyon pedal hareketini gördüğü kadarıyla öğreniyor; hiç tam basılmamış bir pedal kısalmış bir aralık bildirir.\n\nWindows 10/11 için topluluk sürücü paketi kurulu olsun. Yerine başka bir sürücü kurduysanız buradaki kontrollerin çoğu boş dönecektir.\n\nBirkaç saniye bir oyun açın. Sürücü kuvvet bloğunu ancak bir oyun istediğinde oluşturuyor; açmazsanız aşağıdaki kontrol hiçbir şey bulamaz.",
        "map.step_steer": "Direksiyonu uçtan uca çevirin",
        "map.hint_steer": "Sonuna kadar sola, sonra sonuna kadar sağa. İki uca da vardığınızda İleri'ye basın.",
        "map.step_rest": "Pedallara dokunmayın",
        "map.hint_rest": "Ayaklarınızı çekin, sonra İleri'ye basın.",
        "map.step_throttle": "Gaza basın",
        "map.hint_throttle": "En az bir kez sonuna kadar, sonra İleri.",
        "map.step_brake": "Frene basın",
        "map.hint_brake": "En az bir kez sonuna kadar, sonra İleri.",
        "map.step_clutch": "Debriyaja basın",
        "map.hint_clutch": "En az bir kez sonuna kadar, sonra İleri. Direksiyonda debriyaj yoksa atlayın.",
        "map.step_probe": "Sürücü kontrol ediliyor",
        "map.hint_probe": "Yapacağınız bir şey yok. Kuvvet bloğu ve direksiyonun pedal tablosu okunuyor.",
        "map.step_ffb": "Direksiyon geri itti mi?",
        "map.hint_ffb": "Önce sola, sonra sağa itildi. Hiçbir şey oynamadıysa Hayır deyin.",
        "map.step_curve": "Gaza basın. Ortada takılıyor mu?",
        "map.hint_curve": "Gaz eğrisine bir çukur yazıldı. Hangi cevabı verirseniz verin geri alınır.",
        "map.step_range": "Direksiyonu çevirin. Hareket belirgin kısaldı mı?",
        "map.hint_range": "Dönüş açısı 200 dereceye alındı. Hangi cevabı verirseniz verin geri alınır.",
        "map.live_waiting": "Direksiyon bekleniyor",
        "map.live_steer": "sol {0}   sağ {1}   aralık {2}",
        "map.live_rest": "Boştaki konum okunuyor",
        "map.live_pedal": "görülen hareket: {0}",
        "map.ffb_notsent": "Hiçbir şey gönderilmedi. Direksiyon yazmaya açık değil.",
        "map.steer_ok": "Direksiyon doğru okunuyor.",
        "map.steer_none": "Direksiyonda hareket görülmedi.",
        "map.verify": "Pedallara tek tek basın. Yalnızca işaretli çubuklar oynamalı.",
        "map.nothing": "Hiçbir şey tanınacak kadar hareket etmedi. Baştan başlayıp her pedala sonuna kadar basın.",
        "map.button": "Bu direksiyonu kur…",
        "map.button_h": "Direksiyonun ve pedalların bu cihazın raporunda nerede olduğunu bulur, telemetri böylece çalışır; ayrıca hangi özelliklerin desteklendiğini kontrol eder. Birkaç dakika sürer.",
        "map.clear": "Yerleşik düzeni kullan",
        "map.cleared": "Ölçülen düzen kaldırıldı",
        "map.share": "Bu kurulumu paylaş",
        "map.share_title": "Bu direksiyonun desteğini tamamlamaya yardım edin",
        "map.share_body": "Direksiyonunuzu düzgün eklemek için gereken her şey. Yalnızca donanımı anlatır — profil, dosya yolu veya oyun adı içermez.",
        "map.share_copied": "Panoya kopyalandı. Size uygun olan yerden gönderin, ya da kapatıp hiçbir şey göndermeyin.",
        "map.share_github": "GitHub'da issue aç",
        "map.share_mail": "E-posta ile gönder",
        "map.share_folder": "Dosyayı göster",
        "map.share_close": "Kapat",
        "map.share_saved": "Şuraya kaydedildi: {0}",
        "map.browser_title": "Gmail açılsın mı?",
        "map.browser_body": "Tarayıcınızda içi doldurulmuş bir Gmail penceresi açar. Rapor her durumda panonuzda.",
        "map.browser_ok": "Tarayıcıyı aç",
        "dev.bar_dismiss": "Bir dahakine kadar gizle",
        "dev.bar_unmapped": "Bu direksiyon hâlâ beta aşamasında. Düzeni ölçülmediği için, siz eşleyene kadar telemetri boş kalır.",
        "dev.bar_mapped": "Bu direksiyon hâlâ beta aşamasında. Bu makinede eşlendi; kuvvet geri bildirimi ve dönüş açısı bu modelde doğrulanmadı.",
        "dev.beta_action": "Kurulumu yap",
        "dev.bar_unverified": "Bu direksiyon Logitech'in kendi sürücü verileriyle çalışıyor, ama henüz kimse gerçek donanımda doğrulamadı. Kısa bir kontrol, bu modeli kullanan herkes için soruyu kapatır.",
        "dev.verify_action": "Kontrol et",
        "map.step_center": "Otomatik merkezleme yayı",
        "map.hint_center": "Direksiyonu merkezden çevirip bırakın. Bir yay onu geri çekiyor mu?",
        "dev.beta_remap": "Yeniden eşle",
        "map.step_of": "Adım {0} / {1}",
        "dev.beta": "Beta",
        "input.beta_notice": "Bu direksiyonun düzeni henüz çıkarılmadı, bu yüzden görsel çizilmiyor. Tuşlar ve eksenler oyunlarınıza normal şekilde ulaşır.", "conn.not_connected": "Bağlı Değil", "home.steering": "DİREKSİYON AÇISI",
        "home.clutch": "DEBRİYAJ", "home.brake": "FREN", "home.throttle": "GAZ", "home.na": "Yok", "prof.add": "Yeni profil",
        "prof.dup": "Profili çoğalt",
        "prof.del": "Profili sil",
        "prof.new_hint": "Profil adı", "prof.ren_title": "Profili Yeniden Adlandır",
        "prof.del_title": "Profili Sil", "prof.del_msg": "“{0}” profili silinsin mi? Bu işlem geri alınamaz.",
        "ffb.reset_ok": "Sürücü FFB sıfırlandı", "ffb.reset_ok_body": "Uygulamanın yazdığı tüm FFB kayıt defteri değerleri kaldırıldı.",
        "prof.name_taken": "Bu isim kullanımda", "prof.name_taken_body": "“{0}” adlı bir profil zaten var, isim değiştirilmedi.",
        "prof.exe_taken": "Bu oyunun zaten bir profili var", "prof.exe_taken_body": "Bu oyun “{0}” profiline bağlı. Bir oyun tek bir profile bağlanabilir, bu yüzden oyun burada değiştirilmedi.",
        "input.mode_numbers": "Buton numaralarını göster", "input.mode_names": "Buton adlarını göster",
        "ui.autogame": "Oyuna göre geç", "ui.autogame_h": "Çalışan oyunun profilini uygular.", "game.detected": "Oyun algılandı", "game.detected_body": "{0} preseti uygulandı.",
        "set.startup": "Windows ile başlat", "set.startup_h": "Oturum açtığınızda uygulamayı başlatır. Varsayılan olarak kapalıdır.",
        "set.updates_sec": "GÜNCELLEMELER", "set.updates": "Açılışta güncellemeleri denetle", "set.updates_h": "Günde bir kez GitHub'ı denetler. Hiçbir şey otomatik kurulmaz.",
        "set.check_now": "Şimdi denetle", "set.checking": "Denetleniyor\u2026",
        "upd.available": "Güncelleme mevcut", "upd.available_body": "{0} sürümü mevcut.", "upd.open": "İndir",
        "upd.current": "Güncel", "upd.current_body": "En son sürümü kullanıyorsunuz.",
        "upd.failed": "Denetim başarısız", "upd.failed_body": "GitHub'a ulaşılamadı. Bağlantınızı kontrol edip tekrar deneyin.", "dlg.ok": "Tamam", "dlg.cancel": "İptal", "dlg.delete": "Sil",
        "wheel.overall": "Genel Efekt Gücü",
        "wheel.overall_h": "%101 çoğu oyunda merkez FFB ölü bölgesini giderir.",
        "wheel.spring": "Yay Efekti", "wheel.spring_h": "Oyunun kendi yay efektlerinin direksiyona ne kadar geçtiği: birçok oyunun merkeze çeken kuvveti. %100 oyunun gönderdiği gibi, %0 tamamen kaldırır.",
        "wheel.damper": "Damper Efekti", "wheel.damper_h": "Oyunun kendi sönüm efektlerinin direksiyona ne kadar geçtiği: hızlı çevirmeye karşı koyan ağırlık. %100 oyunun gönderdiği gibi, %0 tamamen kaldırır.",
        "wheel.center_cb": "FFB oyunlarında ortalama yayını etkinleştir",
        "wheel.center": "Ortalama Yayı", "wheel.center_h": "Sürücü tabanlı otomatik ortalama gücü.",
        "wheel.ramp": "Ortalama Rampası", "wheel.ramp_h": "Merkezleme yayını şekillendirir. %0'da etkisizdir.", "wheel.rotation": "Dönüş Aralığı",
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
        "ffb.stop": "TÜM KUVVETLERİ DURDUR", "input.led": "LED Karşılama Testi",
        "input.wheel": "DİREKSİYON", "input.shifter": "VITES ÜNİTESİ", "input.gear": "VİTES  (H-DÜZENİ)",
        "input.lpad": "SOL PADDLE", "input.rpad": "SAĞ PADDLE",
        "input.led_nc": "Direksiyon bağlı değil.",
        "input.led_g27": "LED testi yalnızca G27 içindir (DFGT'de RPM LED'i yoktur).",
        "input.led_run": "LED karşılama… (sürücü raporu geçirirse çalışır)", "about.hwid": "Donanım Kimliği", "about.axis": "Eksen Çözünürlüğü",
        "about.ffb": "Kuvvet Geri Bildirimi",
        "about.theme_dark": "Koyu", "about.theme_light": "Açık", "about.testmode_h":
            "Donanım bağlı olmadan aktif direksiyon düzenini değiştirin. “Otomatik” gerçek algılamayı kullanır.",
        "about.test_auto": "Otomatik (algıla)", "about.footer":
            "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "DONANIM TANILAMA", "about.sec_sensor": "SENSÖR & FFB ÖZELLİKLERİ",
        "about.sec_sw": "YAZILIM & SÜRÜCÜ DURUMU", "about.sec_credits": "KATKIDA BULUNANLAR",
        "about.devmodel": "Cihaz Modeli", "about.interface": "Arayüz", "about.power": "Güç Durumu",
        "about.tracking": "Takip Sistemi", "about.polling": "Maks. Yoklama Hızı",
        "about.opmode": "Çalışma Modu", "about.api": "API Bağlantısı", "about.hub": "Hub Sürümü",
        "about.author": "Yazar", "about.sec_about": "HAKKINDA", "about.repo": "GitHub deposu", "about.license": "Lisans: GPL-3.0", "about.portable": "Taşınabilir", "about.disclaimer": "Logitech ile bağlantısı yoktur. Tüm ticari markalar sahiplerine aittir.",
        "tray.show": "Göster", "tray.quit": "Çıkış",
        "apply.ok_title": "Uygulandı", "apply.ok_body": "Ayarlar direksiyona uygulandı.",
        "ui.presets": "HAZIR AYARLAR", "ui.autoload": "Bağlanınca yükle",
        "ui.telemetry": "CANLI TELEMETRİ", "ui.center": "Merkezle",
        "ui.apply": "UYGULA", "conn.connected": "Bağlı", "conn.test": "Test Modu",
        "tab.lut": "LUT",
        "lut.sec": "FFB SON İŞLEME", "lut.enable": "FFB son işlemeyi etkinleştir",
        "lut.enable_h": "Oyunun force feedback'ini seçili LUT eğrisinden geçirir.",
        "lut.mode": "Uygulama yöntemi", "lut.mode_proxy": "Yardımcı DLL (dinput8)", "lut.mode_direct": "Sürücü (DLL'siz)",
        "lut.mode_proxy_h": "Oyunun yanına bir dinput8.dll koyar. Tüm direksiyonlarda çalışır.",
        "lut.mode_direct_h": "Oyun klasörüne dosya konmaz, değişiklikler anında. Sürücüsü eğriyi açan direksiyonlar için.",
        "lut.mode_unsupported": "Bu direksiyonda kullanılamaz", "lut.mode_unsupported_h": "Bu direksiyonun sürücüsü eğriyi açmıyor, bu yüzden DLL kullanılmaya devam eder.",
        "lut.select": "LUT eğrisi", "lut.import": "LUT İçe Aktar", "lut.delete": "LUT Sil", "lut.none": "(yok)",
        "settings.unreadable_title": "Ayarlar okunamadı", "settings.unreadable_body": "settings.json bozuk olduğu için uygulama varsayılan ayarlarla açıldı. Eski dosya {0} olarak saklandı, profillerin onun içinde.",
        "lut.gen_global_title": "LUT kaydedildi", "lut.gen_global_body": "{0} LUT listesinde. Global'in oyunu olmadığı için burada açılmadı: bir oyunun profilini seçip LUT'u orada seç.",
        "lut.del_title": "LUT Sil", "lut.del_body": "“{}” LUT dosyası diskten silinsin mi? Bu geri alınamaz.",
        "lut.deleted": "LUT silindi",
        "lut.empty": "Henüz LUT dosyası yok. Eklemek için “LUT İçe Aktar”a tıkla.",
        "lut.warn": "⚠  Oyunun yanına DLL koyar — çevrimiçi kullanım riski size aittir.",
        "lut.warn_direct": "Oyun klasörüne hiçbir dosya eklenmez.",
        "lut.global_notice": "LUT oyun başına ayarlanır. Bir oyun profili oluşturup LUT'unu seçin.",
        "lut.axis_in": "Giriş", "lut.axis_out": "Çıkış",
        "lut.imported": "LUT içe aktarıldı", "lut.import_fail": "LUT içe aktarılamadı",
        "lut.exe_none": "Oyun seçilmedi",
        "prof.edit": "Profili düzenle", "prof.exe": "Oyun exe dosyası",
        "prof.exe_hint": "UE launcher'lı oyunlarda otomatik bulunamazsa gerçek ...-Shipping.exe'yi seçin.",
        "prof.logo": "Simge (bir .exe'den)", "prof.logo_pick": "Simge seç…",
        "prof.exe_pick": "Oyun seç…", "prof.name_lbl": "Profil adı",
        "proxy.installed": "Proxy kuruldu",
        "proxy.installed_body": "dinput8.dll oyunun yanına kopyalandı.",
        "proxy.locked": "Proxy devre dışı", "proxy.locked_body": "Oyun kapandığında temizlenecek.",
        "proxy.removed": "Proxy kaldırıldı", "proxy.removed_body": "dinput8.dll oyun klasöründen kaldırıldı.",
        "proxy.err_noexe": "Oyun exe'si bulunamadı.", "proxy.err_arch": "Desteklenmeyen mimari.",
        "proxy.err_asset": "Gömülü proxy DLL'i yok (assets/proxy).",
        "proxy.err_write": "DLL yazılamadı (oyun açık mı? klasör yazılabilir mi?).",
        "proxy.foreign_title": "Mevcut dinput8.dll",
        "proxy.foreign_body": "Bu klasörde LWH'nin kurmadığı bir dinput8.dll var (başka bir mod/wrapper). Üzerine yazılsın mı?",
        "tab.wheel": "DİREKSİYON", "tab.pedals": "PEDALLAR", "pedals.gamma": "Gama", "pedals.deadzone": "Ölü Bölge", "pedals.saturation": "Doygunluk", "pedals.custom": "Özel", "pedals.reset": "Fabrika Ayarlarına Dön", "pedals.note": "Direksiyonun belleğinde tutulur - fişini çekmek fabrika tablosunu geri yükler.", "pedals.combine_title": "Birleşik pedallar", "pedals.combine": "Pedalları birleştir", "pedals.combine_info": "Gaz ve fren tek eksende; bunu bekleyen eski oyunlar için. Logitech sürücüsü bunu direksiyon başlarken okur: Windows bir kez izin ister, sonra direksiyon yeniden başlar.", "pedals.combine_on": "Pedallar birleştirildi. Direksiyon yeniden başlatıldı.", "pedals.combine_off": "Pedallar yeniden ayrı. Direksiyon yeniden başlatıldı.", "pedals.combine_replug": "Kaydedildi. Uygulamak için direksiyonu çıkarıp yeniden takın.", "pedals.combine_denied": "Değişmedi: Windows izni verilmedi.", "pedals.combine_failed": "Ayar kaydedilemedi.", "lgs.game": "Oyun", "lgs.set_range": "{0} dönüş açısını {1}° yaptı.", "lgs.set_ffb": "{0} FFB ayarlarını değiştirdi.", "lgs.allow": "Oyunun ayarları değiştirmesine izin ver", "lgs.allow_h": "Logitech Gaming Software'deki \"Oyunun ayarları değiştirmesine izin ver\", orada nasıl çalışıyorsa öyle: Logitech'in direksiyon SDK'sıyla yapılmış bir oyun ve Content Manager'ın donanım kilidi, oyun açıkken dönüş açısını, FFB'nin açık/kapalı olmasını, gücü, yayı ve sönümü ayarlayabilir. İzni oyunun kendi profili verir, oyunun profili yoksa Global. Hiçbiri kaydedilmez; oyun kapanınca bu profilin değerleri geri gelir. LWH geçerli değerleri LGS'nin tuttuğu yere de yazar; böylece Content Manager direksiyonda zaten olanı geri gönderir.", "cm.title": "Content Manager donanım kilidi", "cm.state_ours": "Kurulu: her aracın kilidi LWH üzerinden direksiyona gider.", "cm.state_none": "Kurulu değil.", "cm.state_other": "Content Manager klasöründe başka bir LogitechSteeringWheel.dll var (LGS'nin kendisi mi?).", "cm.install": "Kur", "cm.remove": "Kaldır", "cm.pick": "Content Manager.exe'yi bul", "cm.installed": "Kuruldu. Content Manager'ı yeniden başlat, sonra kontrol ayarlarında Hardware lock'u aç.", "cm.other": "Content Manager klasöründe zaten başka bir LogitechSteeringWheel.dll var; ona dokunulmadı.", "cm.error": "Dosya Content Manager klasörüne kopyalanamadı.", "cm.removed": "Content Manager klasöründen kaldırıldı.", "sdk.title": "Oyunlar için Logitech SDK", "set.compat": "UYUMLULUK", "sdk.h": "Yalnızca Logitech'in motor eklentisiyle yapılmış ve Logitech Gaming Software ya da G HUB olmadan kuvvet geri bildirimi vermeyen oyunlar için (bazı Unity ve Unreal oyunları). Açıkken bu oyunlara LWH'nin kendi SDK dosyası yüklenir. Anti-cheat'li oyunlarda kapalı tutun.", "sdk.state_ours": "Kayıtlı: Logitech'in motor eklentisini kullanan oyunlar LWH'yi kullanır.", "sdk.state_none": "Kayıtlı değil.", "sdk.state_other": "Logitech'in kendi yazılımı kayıtlı.", "sdk.register": "Kaydet", "sdk.remove": "Kaldır", "sdk.done_on": "Kaydedildi. Logitech SDK'sına motoru üzerinden ulaşan oyunlar artık direksiyonu LWH üzerinden bulur; açık bir oyunu yeniden başlat.", "sdk.done_off": "Kayıt kaldırıldı.", "sdk.denied": "Değişmedi: Windows izni verilmedi.", "sdk.failed": "Kayıt değiştirilemedi.", "cm.take_over": "LWH'ninkini kullan", "sdk.take_over": "LWH'ninkini kullan", "cm.taken": "Kuruldu. Öteki dosya kenara alındı; LWH'ninkini kaldırınca geri gelir. Content Manager'ı yeniden başlat.", "sdk.done_take": "Kaydedildi. Logitech'in kaydı kenara alındı; LWH'ninkini kaldırınca geri gelir. Açık bir oyunu yeniden başlat.", "keymap.none": "Yok", "keymap.keys": "Tuşlar", "keymap.macro": "Makro", "keymap.macro_n": "Makro, {0} adım", "keymap.pick": "Bir tuş seç", "keymap.click": "Tıkla, sonra tuşlara bas", "keymap.press": "Tuşlara bas…", "keymap.record": "Kaydetmek için tıkla", "keymap.delays": "Beklemeleri kaydet", "keymap.repeat": "Basılıyken tekrarla", "keymap.remove_step": "Kaldır", "keymap.add_wait": "Bekleme ekle", "keymap.clear": "Temizle", "keymap.open": "Tuş ataması…", "keymap.title": "Tuş ataması", "keymap.close": "Tamam", "keymap.help": "“{0}” profili. Bir direksiyon tuşu her oyunda klavye tuşlarına basar ya da makro oynatır. Seçmek için direksiyonda bir tuşa bas. Legacy Wheel Hub öndeyken hiçbir şey gönderilmez.", "keymap.gear_n": "Vites {0}", "keymap.shifter_n": "Vites kolu {0}", "keymap.button_n": "Tuş {0}", "keymap.recording_n": "Kaydediliyor - {0} tuş (bitirmek için başka yere tıkla)", "lgs.title": "Oyun ayarları", "lgs.set_by": "{0} ayarladı: {1}", "pedals.unsupported": "Bu direksiyon firmware pedal tablosunu vermiyor, bu yüzden burada ayarlanan eğriler ona ulaşamaz. Pedallar normal okunuyor - eğri için oyunun kendi pedal ayarlarını kullanın.",  "tab.ffb": "FFB TESTİ", "tab.input": "GİRİŞ İZLEYİCİ",
        "tab.info": "BİLGİ", "wheel.sec_ffb": "KUVVET GERİ BİLDİRİMİ", "wheel.sec_steer": "DİREKSİYON AYARLARI",
        "ffb.reset": "Sürücü FFB Sıfırla", "ffb.reset_h": "Bu uygulamanın yazdığı tüm sürücü FFB registry değerlerini siler.",
        "set.title": "AYARLAR", "set.appearance": "GÖRÜNÜM", "set.general": "GENEL", "set.testing": "TEST",
        "set.theme": "Tema", "set.language": "Dil", "set.tray": "Sistem tepsisine küçült",
        "set.tray_h": "Küçültme, uygulamayı sistem tepsisine gizler.",
        "set.devmode": "Cihaz algılama modu",
        "set.devmode_h": "Donanım olmadan bir direksiyon düzenini zorlar.", "set.ui_scale": "Arayüz ölçeği", "set.ui_scale_h": "1440p ve 4K ekranlar için. Yeniden başlatınca geçerli olur.", "set.restart_hint": "Yeni ölçeğin uygulanması için uygulamayı yeniden başlatın.",
        "info.opmode_active": "Yerel Gelişmiş Mod (Kilit Açık)", "info.opmode_idle": "Boşta / Bağlı Değil",
        "info.active": "Aktif", "info.standby": "Beklemede",
        "about.translation": "Çeviri",
        "lut.replace_title": "LUT değiştirilsin mi?",
        "lut.replace_body": "Zaten bir {0} var. İçe aktarılanla değiştirilsin mi?",
        "dlg.overwrite": "Üzerine yaz",
        "lut.direct_waiting": "Oyun bekleniyor: sürücünün eğrisi bir oyun FFB'yi başlatınca kontrol edilir. O zamana kadar LUT uygulanmaz.",
        "lut.direct_none": "Bu direksiyonun sürücüsü eğriyi açmıyor, bu yüzden LUT uygulanmıyor. Bunun yerine DLL'i seç.",
    },
    "de": {
        "input.doc_notice": "Tastennamen und Reihenfolge aus Logitechs eigenem Treiber. Wo jede Taste am Kranz sitzt, ist dort nicht erfasst, daher werden sie aufgelistet statt gezeichnet.",
        "input.grid_notice": "Auf diesem Rechner erfasste Tasten, in der Reihenfolge deiner Eingabe nummeriert. Wo sie am Lenkrad sitzen, kann die Einrichtung nicht ermitteln.",
        "input.grid_notice_desc": "Tasten so nummeriert, wie das Lenkrad sie selbst meldet - dieselben Nummern wie in Spielen und in Logitechs eigener Software.",
        "map.step_buttons": "Drücke alle Tasten der Reihe nach",
        "map.hint_buttons": "Geh das Lenkrad durch: Tasten, Paddles, dann die Schaltung, falls vorhanden. Jeder Druck wird der Reihe nach erfasst. Weiter, wenn du fertig bist.",
        "map.title": "Lenkrad einrichten",
        "map.next": "Weiter",
        "map.skip": "Überspringen",
        "map.restart": "Neu beginnen",
        "map.save": "Speichern",
        "map.yes": "Ja",
        "map.no": "Nein",
        "map.result": "Fertig",
        "map.step_intro": "Vor dem Start",
        "map.hint_intro": "Drei Dinge beeinflussen das Ergebnis.\n\nTritt jedes Pedal zuerst ein paar Mal ganz durch. Das Lenkrad lernt den Pedalweg aus dem, was es sieht - ein nie voll getretenes Pedal meldet einen verkürzten Bereich.\n\nVerwende das Community-Treiberpaket für Windows 10/11. Mit einem anderen Treiber bleiben die meisten Prüfungen hier leer.\n\nStarte kurz ein Spiel. Der Treiber legt seinen Force-Feedback-Block erst an, wenn ein Spiel ihn anfordert - sonst findet die Prüfung unten nichts.",
        "map.step_steer": "Lenkrad von Anschlag zu Anschlag drehen",
        "map.hint_steer": "Ganz nach links, dann ganz nach rechts. Weiter, sobald beide Anschläge erreicht sind.",
        "map.step_rest": "Pedale in Ruhe lassen",
        "map.hint_rest": "Füße weg, dann Weiter.",
        "map.step_throttle": "Gas geben",
        "map.hint_throttle": "Mindestens einmal ganz durchtreten, dann Weiter.",
        "map.step_brake": "Bremsen",
        "map.hint_brake": "Mindestens einmal ganz durchtreten, dann Weiter.",
        "map.step_clutch": "Kupplung treten",
        "map.hint_clutch": "Mindestens einmal ganz durchtreten, dann Weiter. Überspringen, wenn keine vorhanden.",
        "map.step_probe": "Treiber wird geprüft",
        "map.hint_probe": "Hier ist nichts zu tun. Der Force-Feedback-Block und die Pedaltabelle werden gelesen.",
        "map.step_ffb": "Hat das Lenkrad gegengehalten?",
        "map.hint_ffb": "Es wurde nach links und dann nach rechts gedrückt. Nein, wenn sich nichts bewegt hat.",
        "map.step_curve": "Gas geben. Stockt es auf halbem Weg?",
        "map.hint_curve": "In die Gaskurve wurde eine Delle geschrieben. Beide Antworten machen das rückgängig.",
        "map.step_range": "Lenkrad drehen. Ist der Weg deutlich kürzer?",
        "map.hint_range": "Der Lenkwinkel wurde auf 200 Grad gesetzt. Beide Antworten setzen ihn zurück.",
        "map.live_waiting": "Warte auf das Lenkrad",
        "map.live_steer": "links {0}   rechts {1}   Bereich {2}",
        "map.live_rest": "Ruhestellung wird gelesen",
        "map.live_pedal": "erkannte Bewegung: {0}",
        "map.ffb_notsent": "Es wurde nichts gesendet. Das Lenkrad ist nicht zum Schreiben geöffnet.",
        "map.steer_ok": "Die Lenkung wird korrekt gelesen.",
        "map.steer_none": "Keine Lenkbewegung erkannt.",
        "map.verify": "Tritt die Pedale einzeln. Nur die markierten Balken sollten sich bewegen.",
        "map.nothing": "Nichts hat sich weit genug bewegt. Beginne neu und tritt jedes Pedal bis zum Anschlag.",
        "map.button": "Dieses Lenkrad einrichten…",
        "map.button_h": "Findet, wo Lenkung und Pedale im Bericht dieses Lenkrads liegen, damit die Telemetrie funktioniert, und prüft, welche Funktionen es unterstützt. Dauert ein paar Minuten.",
        "map.clear": "Eingebautes Layout verwenden",
        "map.cleared": "Gemessenes Layout entfernt",
        "map.share": "Diese Einrichtung teilen",
        "map.share_title": "Hilf, die Unterstützung für dieses Lenkrad fertigzustellen",
        "map.share_body": "Alles, was zum sauberen Einbinden nötig ist. Es beschreibt nur die Hardware — keine Profile, Dateipfade oder Spielnamen.",
        "map.share_copied": "In die Zwischenablage kopiert. Schick es, wohin du magst - oder schließe das Fenster und schick nichts.",
        "map.share_github": "GitHub-Issue öffnen",
        "map.share_mail": "Per E-Mail senden",
        "map.share_folder": "Datei anzeigen",
        "map.share_close": "Schließen",
        "map.share_saved": "Gespeichert unter {0}",
        "map.browser_title": "Gmail öffnen?",
        "map.browser_body": "Öffnet ein ausgefülltes Gmail-Fenster im Browser. Der Bericht liegt ohnehin in der Zwischenablage.",
        "map.browser_ok": "Browser öffnen",
        "dev.bar_dismiss": "Bis zum nächsten Mal ausblenden",
        "dev.bar_unmapped": "Dieses Lenkrad ist noch Beta. Sein Layout wurde nicht vermessen, daher bleibt die Telemetrie leer, bis du es zuordnest.",
        "dev.bar_mapped": "Dieses Lenkrad ist noch Beta. Es wurde auf diesem Rechner zugeordnet; Force Feedback und Lenkwinkel sind für dieses Modell nicht verifiziert.",
        "dev.beta_action": "Einrichten",
        "dev.bar_unverified": "Dieses Lenkrad läuft mit Logitechs eigenen Treiberdaten, wurde aber noch an keiner echten Hardware bestätigt. Eine kurze Prüfung von dir klärt das für alle mit diesem Modell.",
        "dev.verify_action": "Prüfen",
        "map.step_center": "Zentrierfeder",
        "map.hint_center": "Lenkrad aus der Mitte drehen und loslassen. Zieht eine Feder es zurück?",
        "dev.beta_remap": "Neu zuordnen",
        "map.step_of": "Schritt {0} von {1}",
        "dev.beta": "Beta",
        "input.beta_notice": "Das Layout dieses Lenkrads ist noch nicht erfasst, daher wird die Grafik nicht gezeichnet. Tasten und Achsen erreichen deine Spiele wie gewohnt.", "conn.not_connected": "Nicht verbunden", "home.steering": "LENKWINKEL",
        "home.clutch": "KUPPLUNG", "home.brake": "BREMSE", "home.throttle": "GAS", "home.na": "N/V", "prof.add": "Neues Profil",
        "prof.dup": "Profil duplizieren",
        "prof.del": "Profil löschen",
        "prof.new_hint": "Profilname", "prof.ren_title": "Profil umbenennen",
        "prof.del_title": "Profil löschen", "prof.del_msg": "Profil „{0}“ löschen? Dies kann nicht rückgängig gemacht werden.",
        "ffb.reset_ok": "Treiber-FFB zurückgesetzt", "ffb.reset_ok_body": "Alle von der App geschriebenen FFB-Registry-Werte wurden entfernt.",
        "prof.name_taken": "Name bereits vergeben", "prof.name_taken_body": "Ein Profil namens „{0}“ existiert bereits, der Name wurde nicht geändert.",
        "prof.exe_taken": "Spiel hat schon ein Profil", "prof.exe_taken_body": "Dieses Spiel gehört bereits zum Profil „{0}“. Ein Spiel kann nur zu einem Profil gehören, daher wurde das Spiel hier nicht geändert.",
        "input.mode_numbers": "Tastennummern anzeigen", "input.mode_names": "Tastennamen anzeigen",
        "ui.autogame": "Auto-Wechsel je Spiel", "ui.autogame_h": "Wendet das Profil des laufenden Spiels an.", "game.detected": "Spiel erkannt", "game.detected_body": "Voreinstellung {0} angewendet.",
        "set.startup": "Mit Windows starten", "set.startup_h": "Startet die App bei der Anmeldung. Standardmäßig aus.",
        "set.updates_sec": "UPDATES", "set.updates": "Beim Start nach Updates suchen", "set.updates_h": "Prüft einmal täglich auf GitHub. Es wird nichts automatisch installiert.",
        "set.check_now": "Jetzt prüfen", "set.checking": "Wird geprüft\u2026",
        "upd.available": "Update verfügbar", "upd.available_body": "Version {0} ist verfügbar.", "upd.open": "Herunterladen",
        "upd.current": "Aktuell", "upd.current_body": "Du verwendest die neueste Version.",
        "upd.failed": "Prüfung fehlgeschlagen", "upd.failed_body": "GitHub war nicht erreichbar. Prüfe deine Verbindung und versuche es erneut.", "dlg.ok": "OK", "dlg.cancel": "Abbrechen", "dlg.delete": "Löschen",
        "wheel.overall": "Gesamtstärke der Effekte",
        "wheel.overall_h": "101% beseitigt in den meisten Spielen die zentrale FFB-Totzone.",
        "wheel.spring": "Feder-Effekt", "wheel.spring_h": "Wie stark die Federeffekte des Spiels am Lenkrad ankommen – der Zug zur Mitte, den viele Spiele nutzen. 100 % wie vom Spiel gesendet, 0 % entfernt ihn.",
        "wheel.damper": "Dämpfer-Effekt", "wheel.damper_h": "Wie stark die Dämpfereffekte des Spiels am Lenkrad ankommen – das Gewicht gegen schnelles Drehen. 100 % wie vom Spiel gesendet, 0 % entfernt es.",
        "wheel.center_cb": "Zentrierfeder in FFB-Spielen aktivieren",
        "wheel.center": "Zentrierfeder", "wheel.center_h": "Treiberbasierte Auto-Zentrierstärke.",
        "wheel.ramp": "Zentrier-Rampe", "wheel.ramp_h": "Formt die Zentrierfeder. Ohne Wirkung bei 0%.", "wheel.rotation": "Drehbereich",
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
        "ffb.stop": "ALLE KRÄFTE STOPPEN", "input.led": "LED-Begrüßungstest",
        "input.wheel": "LENKRAD", "input.shifter": "SCHALTEINHEIT", "input.gear": "GANG  (H-SCHALTUNG)",
        "input.lpad": "LINKES PADDLE", "input.rpad": "RECHTES PADDLE",
        "input.led_nc": "Lenkrad nicht verbunden.",
        "input.led_g27": "LED-Test nur für G27 (DFGT hat keine RPM-LEDs).",
        "input.led_run": "LED-Begrüßung… (funktioniert, wenn der Treiber den Bericht durchlässt)", "about.hwid": "Hardware-ID", "about.axis": "Achsenauflösung",
        "about.ffb": "Force Feedback",
        "about.theme_dark": "Dunkel", "about.theme_light": "Hell", "about.testmode_h":
            "Aktives Lenkrad-Layout ohne angeschlossene Hardware umschalten. „Auto“ nutzt echte Erkennung.",
        "about.test_auto": "Auto (erkennen)", "about.footer":
            "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "HARDWARE-DIAGNOSE", "about.sec_sensor": "SENSOR- & FFB-SPEZIFIKATIONEN",
        "about.sec_sw": "SOFTWARE- & TREIBERSTATUS", "about.sec_credits": "MITWIRKENDE",
        "about.devmodel": "Gerätemodell", "about.interface": "Schnittstelle", "about.power": "Energiestatus",
        "about.tracking": "Tracking-System", "about.polling": "Max. Abtastrate",
        "about.opmode": "Betriebsmodus", "about.api": "API-Hook", "about.hub": "Hub-Version",
        "about.author": "Autor", "about.sec_about": "ÜBER", "about.repo": "GitHub-Repository", "about.license": "Lizenz: GPL-3.0", "about.portable": "Portabel", "about.disclaimer": "Nicht mit Logitech verbunden. Alle Marken gehören ihren jeweiligen Eigentümern.",
        "tray.show": "Anzeigen", "tray.quit": "Beenden",
        "apply.ok_title": "Angewendet", "apply.ok_body": "Einstellungen auf das Lenkrad angewendet.",
        "ui.presets": "VOREINSTELLUNGEN", "ui.autoload": "Beim Verbinden laden",
        "ui.telemetry": "LIVE-TELEMETRIE", "ui.center": "Zentrieren",
        "ui.apply": "ANWENDEN", "conn.connected": "Verbunden", "conn.test": "Testmodus",
        "tab.wheel": "LENKRAD", "tab.pedals": "PEDALE", "pedals.gamma": "Gamma", "pedals.deadzone": "Totzone", "pedals.saturation": "Sättigung", "pedals.custom": "Eigene", "pedals.reset": "Zurücksetzen", "pedals.note": "Im Speicher des Lenkrads gehalten - Abstecken stellt die Werkstabelle wieder her.", "pedals.combine_title": "Kombinierte Pedale", "pedals.combine": "Pedale kombiniert melden", "pedals.combine_info": "Gas und Bremse auf einer Achse, für ältere Spiele, die das erwarten. Logitechs Treiber liest das beim Start des Lenkrads: Windows fragt einmal um Erlaubnis, dann startet das Lenkrad neu.", "pedals.combine_on": "Pedale kombiniert. Das Lenkrad wurde neu gestartet.", "pedals.combine_off": "Pedale wieder getrennt. Das Lenkrad wurde neu gestartet.", "pedals.combine_replug": "Gespeichert. Lenkrad aus- und wieder einstecken, um es anzuwenden.", "pedals.combine_denied": "Nicht geändert: Die Windows-Erlaubnis wurde nicht erteilt.", "pedals.combine_failed": "Die Einstellung konnte nicht gespeichert werden.", "lgs.game": "Das Spiel", "lgs.set_range": "{0} hat den Drehbereich auf {1}° gesetzt.", "lgs.set_ffb": "{0} hat die Force-Feedback-Einstellungen geändert.", "lgs.allow": "Spiel darf Einstellungen anpassen", "lgs.allow_h": "„Spiel darf Einstellungen anpassen“ aus der Logitech Gaming Software, so wie es dort funktionierte: Ein Spiel mit Logitechs Lenkrad-SDK und der Hardware-Lock von Content Manager dürfen, solange sie laufen, den Drehbereich, Force Feedback an/aus, Stärke, Feder und Dämpfer setzen. Ob sie dürfen, entscheidet das Profil des Spiels, sonst Global. Nichts wird gespeichert; endet das Spiel, gelten wieder die Werte dieses Profils. LWH legt die gültigen Werte auch dort ab, wo LGS sie hatte, damit Content Manager zurückschickt, was das Lenkrad schon hat.", "cm.title": "Content-Manager-Hardware-Lock", "cm.state_ours": "Eingerichtet: Der Lenkeinschlag jedes Autos kommt über LWH ans Lenkrad.", "cm.state_none": "Nicht eingerichtet.", "cm.state_other": "Im Content-Manager-Ordner liegt eine andere LogitechSteeringWheel.dll (die von LGS?).", "cm.install": "Einrichten", "cm.remove": "Entfernen", "cm.pick": "Content Manager.exe suchen", "cm.installed": "Eingerichtet. Content Manager neu starten und dann in den Steuerungseinstellungen Hardware lock einschalten.", "cm.other": "Im Content-Manager-Ordner liegt bereits eine andere LogitechSteeringWheel.dll; sie wurde nicht angetastet.", "cm.error": "Die Datei konnte nicht in den Content-Manager-Ordner kopiert werden.", "cm.removed": "Aus dem Content-Manager-Ordner entfernt.", "sdk.title": "Logitech-SDK für Spiele", "set.compat": "KOMPATIBILITÄT", "sdk.h": "Nur für Spiele mit Logitechs Engine-Plugin (einige Unity- und Unreal-Spiele), die ohne Logitech Gaming Software oder G HUB kein Force Feedback geben. Eingeschaltet wird LWHs eigene SDK-Datei in diese Spiele geladen. Bei Spielen mit Anti-Cheat ausgeschaltet lassen.", "sdk.state_ours": "Registriert: Spiele mit Logitechs Engine-Plugin nutzen LWH.", "sdk.state_none": "Nicht registriert.", "sdk.state_other": "Logitechs eigene Software ist registriert.", "sdk.register": "Registrieren", "sdk.remove": "Entfernen", "sdk.done_on": "Registriert. Spiele, die Logitechs SDK über ihre Engine nutzen, finden das Lenkrad jetzt über LWH; ein laufendes Spiel neu starten.", "sdk.done_off": "Die Registrierung wurde entfernt.", "sdk.denied": "Nicht geändert: Die Windows-Erlaubnis wurde nicht erteilt.", "sdk.failed": "Die Registrierung konnte nicht geändert werden.", "cm.take_over": "LWHs verwenden", "sdk.take_over": "LWHs verwenden", "cm.taken": "Eingerichtet. Die andere Datei wurde beiseitegelegt und kommt zurück, wenn LWHs entfernt wird. Content Manager neu starten.", "sdk.done_take": "Registriert. Logitechs Registrierung wurde beiseitegelegt und kommt zurück, wenn LWHs entfernt wird; ein laufendes Spiel neu starten.", "keymap.none": "Keine", "keymap.keys": "Tasten", "keymap.macro": "Makro", "keymap.macro_n": "Makro, {0} Schritte", "keymap.pick": "Taste wählen", "keymap.click": "Klicken, dann Tasten drücken", "keymap.press": "Tasten drücken…", "keymap.record": "Zum Aufnehmen klicken", "keymap.delays": "Pausen aufnehmen", "keymap.repeat": "Wiederholen, solange gedrückt", "keymap.remove_step": "Entfernen", "keymap.add_wait": "Pause hinzufügen", "keymap.clear": "Leeren", "keymap.open": "Tastenbelegung…", "keymap.title": "Tastenbelegung", "keymap.close": "Fertig", "keymap.help": "Profil „{0}“. Eine Lenkradtaste drückt Tastaturtasten oder spielt ein Makro ab, in jedem Spiel. Drücke eine Taste am Lenkrad, um sie zu wählen. Solange Legacy Wheel Hub im Vordergrund ist, wird nichts gesendet.", "keymap.gear_n": "Gang {0}", "keymap.shifter_n": "Schaltknauf {0}", "keymap.button_n": "Taste {0}", "keymap.recording_n": "Aufnahme - {0} Tasten (zum Beenden woanders klicken)", "lgs.title": "Spieleinstellungen", "lgs.set_by": "Von {0} gesetzt: {1}", "pedals.unsupported": "Dieses Lenkrad gibt seine Firmware-Pedaltabelle nicht heraus, daher erreichen hier eingestellte Kurven es nicht. Die Pedale selbst werden normal gelesen - für Kurven die Pedaleinstellungen des Spiels nutzen.",  "tab.ffb": "FFB-TEST", "tab.input": "EINGABE-MONITOR",
        "tab.lut": "LUT",
        "lut.sec": "FFB-NACHBEARBEITUNG", "lut.enable": "FFB-Nachbearbeitung aktivieren",
        "lut.enable_h": "Leitet das Force Feedback des Spiels durch die gewählte LUT-Kurve.",
        "lut.mode": "Angewendet über", "lut.mode_proxy": "Hilfs-DLL (dinput8)", "lut.mode_direct": "Treiber (ohne DLL)",
        "lut.mode_proxy_h": "Legt eine dinput8.dll neben das Spiel. Für alle Lenkräder.",
        "lut.mode_direct_h": "Keine Datei im Spielordner, Änderungen sofort. Erfordert ein Lenkrad, dessen Treiber die Kurve freigibt.",
        "lut.mode_unsupported": "Für dieses Lenkrad nicht verfügbar", "lut.mode_unsupported_h": "Der Treiber dieses Lenkrads gibt die Kurve nicht frei, daher bleibt hier die DLL aktiv.",
        "lut.select": "LUT-Kurve", "lut.import": "LUT importieren", "lut.delete": "LUT löschen", "lut.none": "(keine)",
        "settings.unreadable_title": "Einstellungen nicht lesbar", "settings.unreadable_body": "settings.json war beschädigt, daher startete die App mit Standardeinstellungen. Die alte Datei wurde als {0} aufbewahrt - deine Profile sind darin.",
        "lut.gen_global_title": "LUT gespeichert", "lut.gen_global_body": "{0} steht in der LUT-Liste. Global hat kein Spiel, daher wurde sie hier nicht eingeschaltet: Wähle das Profil eines Spiels und dort die LUT.",
        "lut.del_title": "LUT löschen", "lut.del_body": "LUT-Datei “{}” von der Festplatte löschen? Das kann nicht rückgängig gemacht werden.",
        "lut.deleted": "LUT gelöscht",
        "lut.empty": "Noch keine LUT-Dateien. Klicke auf „LUT importieren“.",
        "lut.warn": "⚠  Legt eine DLL neben das Spiel — Online-Nutzung auf eigenes Risiko.",
        "lut.warn_direct": "Es wird keine Datei im Spielordner abgelegt.",
        "lut.global_notice": "LUT gilt je Spiel. Lege ein Spielprofil an und wähle hier seine LUT.",
        "lut.axis_in": "Eingang", "lut.axis_out": "Ausgang",
        "lut.imported": "LUT importiert", "lut.import_fail": "LUT konnte nicht importiert werden",
        "lut.exe_none": "Kein Spiel gewählt",
        "prof.edit": "Profil bearbeiten", "prof.exe": "Spiel-Datei",
        "prof.exe_hint": "Bei UE-Launcher-Spielen die echte ...-Shipping.exe wählen, falls nicht automatisch gefunden.",
        "prof.logo": "Symbol (aus einer .exe)", "prof.logo_pick": "Symbol wählen…",
        "prof.exe_pick": "Spiel wählen…", "prof.name_lbl": "Profilname",
        "proxy.installed": "Proxy installiert",
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
        "set.tray_h": "Minimieren blendet die App in den Infobereich.",
        "set.devmode": "Geräteerkennungsmodus",
        "set.devmode_h": "Erzwingt ein Lenkrad-Layout ohne Hardware.", "set.ui_scale": "Oberflächenskalierung", "set.ui_scale_h": "Für 1440p- und 4K-Bildschirme. Gilt nach einem Neustart.", "set.restart_hint": "Starten Sie die App neu, um die neue Skalierung anzuwenden.",
        "info.opmode_active": "Nativer Erweiterter Modus (Entsperrt)", "info.opmode_idle": "Leerlauf / Getrennt",
        "info.active": "Aktiv", "info.standby": "Standby",
        "about.translation": "Übersetzung",
        "lut.replace_title": "LUT ersetzen?",
        "lut.replace_body": "Es gibt schon eine {0}. Durch die importierte ersetzen?",
        "dlg.overwrite": "Überschreiben",
        "lut.direct_waiting": "Warten auf ein Spiel: Die Kurve des Treibers wird geprüft, sobald ein Spiel Force Feedback startet. Bis dahin wird keine LUT angewendet.",
        "lut.direct_none": "Der Treiber dieses Lenkrads stellt die Kurve nicht bereit, daher wird keine LUT angewendet. Wähle stattdessen die DLL.",
    },
    "uk": {
        "input.doc_notice": "Назви та порядок кнопок взято з драйвера Logitech. Де саме кожна кнопка розташована на ободі, там не записано, тому вони показані списком, а не на малюнку.",
        "input.grid_notice": "Кнопки, вивчені на цьому комп’ютері, пронумеровані в порядку натискання. Де вони розташовані на кермі, налаштування дізнатися не може.",
        "input.grid_notice_desc": "Кнопки пронумеровано так, як їх повідомляє саме кермо, — ті самі номери, що показують ігри та програма Logitech.",
        "map.step_buttons": "Натисніть усі кнопки по черзі",
        "map.hint_buttons": "Пройдіться по кермі: лицьові кнопки, підрульові перемикачі, потім КПП, якщо вона є. Кожне натискання записується по порядку. Коли закінчите — «Далі».",
        "map.title": "Налаштування керма",
        "map.next": "Далі",
        "map.skip": "Пропустити",
        "map.restart": "Почати знову",
        "map.save": "Зберегти",
        "map.yes": "Так",
        "map.no": "Ні",
        "map.result": "Готово",
        "map.step_intro": "Перш ніж почати",
        "map.hint_intro": "На результат впливають три речі.\n\nСпочатку кілька разів натисніть кожну педаль до упору. Кермо запам’ятовує їхній хід таким, яким його бачило, тому педаль, яку ще жодного разу не натискали повністю, повідомляє вкорочений діапазон.\n\nВикористовуйте пакет драйверів спільноти для Windows 10/11. Якщо ви замінили його іншим драйвером, більша частина перевірки повернеться порожньою.\n\nЗапустіть гру на кілька секунд. Драйвер створює свій блок FFB лише тоді, коли його запитує гра, тож без цього перевірка нижче нічого не знайде.",
        "map.step_steer": "Поверніть кермо від упору до упору",
        "map.hint_steer": "До кінця вліво, потім до кінця вправо. Натисніть «Далі», коли дійдете до обох упорів.",
        "map.step_rest": "Не торкайтеся педалей",
        "map.hint_rest": "Приберіть ноги, потім натисніть «Далі».",
        "map.step_throttle": "Натисніть газ",
        "map.hint_throttle": "Хоча б раз до упору, потім «Далі».",
        "map.step_brake": "Натисніть гальмо",
        "map.hint_brake": "Хоча б раз до упору, потім «Далі».",
        "map.step_clutch": "Натисніть зчеплення",
        "map.hint_clutch": "Хоча б раз до упору, потім «Далі». Пропустіть, якщо в цього керма його немає.",
        "map.step_probe": "Перевірка драйвера",
        "map.hint_probe": "Тут нічого робити не треба. Читаємо блок FFB і таблицю педалей керма.",
        "map.step_ffb": "Кермо штовхнуло у відповідь?",
        "map.hint_ffb": "Його штовхнули вліво, потім вправо. Відповідайте «Ні», якщо нічого не рухалося.",
        "map.step_curve": "Натисніть газ. Він «застрягає» посередині?",
        "map.hint_curve": "У криву газу записано провал. Будь-яка відповідь його скасує.",
        "map.step_range": "Поверніть кермо. Хід став набагато коротшим?",
        "map.hint_range": "Діапазон встановлено на 200 градусів. Будь-яка відповідь його відновить.",
        "map.live_waiting": "Очікування керма",
        "map.live_steer": "вліво {0}   вправо {1}   розмах {2}",
        "map.live_rest": "Зчитуємо положення спокою",
        "map.live_pedal": "помічений рух: {0}",
        "map.ffb_notsent": "Нічого не надіслано. Кермо не відкрите для запису.",
        "map.steer_ok": "Кермо зчитується правильно.",
        "map.steer_none": "Руху керма не помічено.",
        "map.verify": "Натискайте кожну педаль. Рухатися мають лише позначені смуги.",
        "map.nothing": "Нічого не зрушило достатньо, щоб розпізнати. Почніть знову й натисніть кожну педаль до упору.",
        "map.button": "Налаштувати це кермо…",
        "map.button_h": "Знаходить, де в звіті цього керма лежать кермо й педалі, щоб працювала телеметрія, і перевіряє, які функції воно підтримує. Займає кілька хвилин.",
        "map.clear": "Використовувати вбудовану розкладку",
        "map.cleared": "Виміряну розкладку видалено",
        "map.share": "Поділитися цим налаштуванням",
        "map.share_title": "Допоможіть завершити підтримку цього керма",
        "map.share_body": "Усе потрібне, щоб правильно додати ваше кермо. Тут описано лише обладнання — без профілів, шляхів до файлів і назв ігор.",
        "map.share_copied": "Скопійовано в буфер обміну. Надішліть куди зручно або закрийте це вікно й нічого не надсилайте.",
        "map.share_github": "Відкрити issue на GitHub",
        "map.share_mail": "Надіслати поштою",
        "map.share_folder": "Показати файл",
        "map.share_close": "Закрити",
        "map.share_saved": "Збережено в {0}",
        "map.browser_title": "Відкрити Gmail?",
        "map.browser_body": "Відкриється вікно Gmail у браузері із заповненим звітом. У будь-якому разі він уже є у вашому буфері обміну.",
        "map.browser_ok": "Відкрити браузер",
        "dev.bar_dismiss": "Сховати до наступного разу",
        "dev.bar_unmapped": "Це кермо ще в бета-версії. Його розкладку не виміряно, тому телеметрія залишається порожньою, доки ви його не налаштуєте.",
        "dev.bar_mapped": "Це кермо ще в бета-версії. Його налаштовано на цьому комп’ютері; FFB і кут повороту на цій моделі не перевірено.",
        "dev.beta_action": "Налаштувати",
        "dev.bar_unverified": "Це кермо працює за даними драйвера Logitech, але на справжньому обладнанні його ще ніхто не підтвердив. Коротка перевірка від вас вирішить питання для всіх власників цієї моделі.",
        "dev.verify_action": "Перевірити",
        "map.step_center": "Пружина автоцентрування",
        "map.hint_center": "Відведіть кермо від центру й відпустіть. Пружина повертає його назад?",
        "dev.beta_remap": "Налаштувати ще раз",
        "map.step_of": "Крок {0} з {1}",
        "dev.beta": "Бета",
        "input.beta_notice": "Розкладку цього керма ще не виміряно, тому схема не малюється. Кнопки й осі все одно нормально доходять до ваших ігор.",
        "conn.not_connected": "Не підключено",
        "home.steering": "КУТ ПОВОРОТУ",
        "home.clutch": "ЗЧЕП.",
        "home.brake": "ГАЛЬМО",
        "home.throttle": "ГАЗ",
        "home.na": "Н/Д",
        "prof.add": "Новий профіль",
        "prof.dup": "Дублювати профіль",
        "prof.del": "Видалити профіль",
        "prof.new_hint": "Назва профілю",
        "prof.ren_title": "Перейменування профілю",
        "prof.del_title": "Видалення профілю",
        "prof.del_msg": "Видалити профіль “{0}”? Це неможливо скасувати.",
        "ffb.reset_ok": "FFB драйвера скинуто",
        "ffb.reset_ok_body": "Усі значення реєстру FFB, записані цією програмою, видалено.",
        "prof.exe_taken": "Гра вже має профіль",
        "prof.exe_taken_body": "Цю гру вже призначено профілю «{0}». Одна гра може належати лише одному профілю, тому гру тут не змінено.",
        "prof.name_taken": "Назва вже використовується",
        "prof.name_taken_body": "Профіль із назвою “{0}” вже існує, тому назву залишено без змін.",
        "input.mode_numbers": "Показувати номери кнопок",
        "input.mode_names": "Показувати назви кнопок",
        "ui.autogame": "Профіль за грою",
        "ui.autogame_h": "Застосовує профіль запущеної гри.",
        "game.detected": "Гру виявлено",
        "game.detected_body": "Застосовано пресет {0}.",
        "set.startup": "Запускати разом із Windows",
        "set.startup_h": "Запускає програму під час входу в систему. За замовчуванням вимкнено.",
        "set.updates_sec": "ОНОВЛЕННЯ",
        "set.updates": "Перевіряти оновлення під час запуску",
        "set.updates_h": "Перевіряє GitHub раз на день. Нічого не встановлюється автоматично.",
        "set.check_now": "Перевірити зараз",
        "set.checking": "Перевірка…",
        "upd.available": "Доступне оновлення",
        "upd.available_body": "Доступна версія {0}.",
        "upd.open": "Завантажити",
        "upd.current": "Останню версію встановлено",
        "upd.current_body": "У вас найновіша версія.",
        "upd.failed": "Не вдалося перевірити оновлення",
        "upd.failed_body": "Не вдалося з’єднатися з GitHub. Перевірте підключення та спробуйте ще раз.",
        "dlg.ok": "ОК",
        "dlg.cancel": "Скасувати",
        "dlg.delete": "Видалити",
        "wheel.overall": "Загальна сила ефектів",
        "wheel.overall_h": "101% прибирає мертву зону FFB у центрі в більшості ігор.",
        "wheel.spring": "Ефект пружини",
        "wheel.spring_h": "Наскільки сильно власні ефекти пружини гри доходять до керма — тяга до центру, яку використовують багато ігор. 100% — як надсилає гра, 0% — прибирає її повністю.",
        "wheel.damper": "Ефект демпфера",
        "wheel.damper_h": "Наскільки сильно власні ефекти демпфера гри доходять до керма — вага, що опирається швидкому повороту. 100% — як надсилає гра, 0% — прибирає її повністю.",
        "wheel.center_cb": "Увімкнути пружину центрування в іграх із FFB",
        "wheel.center": "Пружина центрування",
        "wheel.center_h": "Сила автоцентрування на рівні драйвера.",
        "wheel.ramp": "Наростання центрування",
        "wheel.ramp_h": "Формує центрувальну пружину. При 0% не діє.",
        "wheel.rotation": "Діапазон повороту",
        "wheel.rotation_h": "Максимальний кут повороту керма.",
        "ffb.title": "ТЕСТ ЗВОРОТНОГО ЗВ’ЯЗКУ",
        "ffb.subtitle": "Перевірте мотор FFB напряму. Бажано робити це, коли жодна гра не використовує кермо.",
        "ffb.strength": "Сила тесту",
        "ffb.strength_h": "Сила, яка використовується в тестах Push, Spring і Sweep.",
        "ffb.push_l": "Штовхнути вліво",
        "ffb.push_r": "Штовхнути вправо",
        "ffb.spring": "Пружина (центр)",
        "ffb.spring_stop": "Зупинити пружину",
        "ffb.sweep": "Автоцикл",
        "ffb.sweep_stop": "Зупинити цикл",
        "ffb.advanced": "РОЗШИРЕНІ ТЕСТИ МОТОРА",
        "ffb.pulse_l": "Імпульс вліво",
        "ffb.pulse_r": "Імпульс вправо",
        "ffb.vibe_light": "Легка вібрація",
        "ffb.vibe_med": "Середня вібрація",
        "ffb.vibe_fast": "Швидке торохтіння",
        "ffb.vibe_heavy": "Сильна вібрація",
        "ffb.stop": "ЗУПИНИТИ ВСІ СИЛИ",
        "input.led": "Тест привітання LED",
        "input.wheel": "КЕРМО",
        "input.shifter": "БЛОК ПЕРЕМИКАННЯ ПЕРЕДАЧ",
        "input.gear": "ПЕРЕДАЧА (H-СХЕМА)",
        "input.lpad": "ЛІВА ПЕЛЮСТКА",
        "input.rpad": "ПРАВА ПЕЛЮСТКА",
        "input.led_nc": "Кермо не підключено.",
        "input.led_g27": "Тест LED доступний лише для G27 (у DFGT немає LED обертів).",
        "input.led_run": "Привітання LED… (працює, якщо драйвер передає цей звіт)",
        "about.hwid": "Апаратний ID",
        "about.axis": "Роздільна здатність осей",
        "about.ffb": "Зворотний зв’язок",
        "about.theme_dark": "Темна",
        "about.theme_light": "Світла",
        "about.testmode_h": "Перемикає розкладку активного керма без підключеного обладнання. “Авто” використовує реальне визначення.",
        "about.test_auto": "Авто (визначення)",
        "about.footer": "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "ДІАГНОСТИКА ОБЛАДНАННЯ",
        "about.sec_sensor": "ХАРАКТЕРИСТИКИ ДАТЧИКІВ ТА FFB",
        "about.sec_sw": "СТАН ПЗ ТА ДРАЙВЕРА",
        "about.sec_credits": "ПОДЯКИ",
        "about.devmodel": "Модель пристрою",
        "about.interface": "Інтерфейс",
        "about.power": "Стан живлення",
        "about.tracking": "Система відстеження",
        "about.polling": "Макс. частота опитування",
        "about.opmode": "Режим роботи",
        "about.api": "API-хук",
        "about.hub": "Версія хаба",
        "about.author": "Автор",
        "about.sec_about": "ПРО ПРОГРАМУ",
        "about.repo": "Репозиторій GitHub",
        "about.license": "Ліцензія: GPL-3.0", "about.portable": "Портативна",
        "about.disclaimer": "Не пов’язано з Logitech. Усі торгові марки належать їхнім власникам.",
        "tray.show": "Показати",
        "tray.quit": "Вийти",
        "apply.ok_title": "Застосовано",
        "apply.ok_body": "Налаштування застосовано до керма.",
        "ui.presets": "ПРЕСЕТИ",
        "ui.autoload": "Авто при підключенні",
        "ui.telemetry": "ТЕЛЕМЕТРІЯ",
        "ui.center": "Центрувати",
        "ui.apply": "ЗАСТОСУВАТИ",
        "conn.connected": "Підключено",
        "conn.test": "Тестовий режим",
        "tab.wheel": "КЕРМО",
        "tab.pedals": "ПЕДАЛІ",
        "pedals.gamma": "Гамма",
        "pedals.deadzone": "Мертва зона",
        "pedals.saturation": "Насичення",
        "pedals.custom": "Власна",
        "pedals.reset": "До заводських",
        "pedals.note": "Зберігається в пам’яті керма — після від’єднання повертається заводська таблиця.", "pedals.combine_title": "Об'єднані педалі", "pedals.combine": "Об'єднати педалі", "pedals.combine_info": "Газ і гальмо на одній осі — для старих ігор, які цього очікують. Драйвер Logitech читає це під час запуску керма: Windows один раз попросить дозвіл, потім кермо перезапуститься.", "pedals.combine_on": "Педалі об'єднано. Кермо перезапущено.", "pedals.combine_off": "Педалі знову окремі. Кермо перезапущено.", "pedals.combine_replug": "Збережено. Від'єднайте кермо й під'єднайте знову, щоб застосувати.", "pedals.combine_denied": "Не змінено: дозвіл Windows не надано.", "pedals.combine_failed": "Не вдалося зберегти налаштування.", "lgs.game": "Гра", "lgs.set_range": "{0} встановила кут повороту {1}°.", "lgs.set_ffb": "{0} змінила налаштування FFB.", "lgs.allow": "Дозволити грі змінювати налаштування", "lgs.allow_h": "«Дозволити грі змінювати налаштування» з Logitech Gaming Software, як це працювало там: гра на SDK керма Logitech і апаратне обмеження Content Manager можуть, поки працюють, встановлювати кут повороту, увімкнення FFB, силу, пружину й демпфер. Чи можна — вирішує профіль гри, інакше Global. Нічого не зберігається; коли гра закривається, повертаються значення цього профілю. LWH також записує чинні значення туди, де їх тримав LGS, щоб Content Manager повертав те, що вже є на кермі.", "cm.title": "Апаратне обмеження Content Manager", "cm.state_ours": "Налаштовано: кут кожного авто передається на кермо через LWH.", "cm.state_none": "Не налаштовано.", "cm.state_other": "У теці Content Manager є інший LogitechSteeringWheel.dll (від LGS?).", "cm.install": "Налаштувати", "cm.remove": "Видалити", "cm.pick": "Знайдіть Content Manager.exe", "cm.installed": "Налаштовано. Перезапустіть Content Manager, потім увімкніть Hardware lock у налаштуваннях керування.", "cm.other": "У теці Content Manager уже є інший LogitechSteeringWheel.dll; його не змінено.", "cm.error": "Не вдалося скопіювати файл у теку Content Manager.", "cm.removed": "Видалено з теки Content Manager.", "sdk.title": "Logitech SDK для ігор", "set.compat": "СУМІСНІСТЬ", "sdk.h": "Лише для ігор із рушієвим плагіном Logitech (деякі ігри на Unity та Unreal), які без Logitech Gaming Software чи G HUB не дають силового відгуку. Коли ввімкнено, у ці ігри завантажується власний файл SDK від LWH. В іграх з античитом тримайте вимкненим.", "sdk.state_ours": "Зареєстровано: ігри з плагіном Logitech для рушія використовують LWH.", "sdk.state_none": "Не зареєстровано.", "sdk.state_other": "Зареєстровано власне ПЗ Logitech.", "sdk.register": "Зареєструвати", "sdk.remove": "Видалити", "sdk.done_on": "Зареєстровано. Ігри, що звертаються до SDK Logitech через свій рушій, тепер знаходять кермо через LWH; перезапустіть запущену гру.", "sdk.done_off": "Реєстрацію видалено.", "sdk.denied": "Не змінено: дозвіл Windows не надано.", "sdk.failed": "Не вдалося змінити реєстрацію.", "cm.take_over": "Використати LWH", "sdk.take_over": "Використати LWH", "cm.taken": "Налаштовано. Інший файл відкладено; він повернеться, коли видалите файл LWH. Перезапустіть Content Manager.", "sdk.done_take": "Зареєстровано. Реєстрацію Logitech відкладено; вона повернеться, коли видалите реєстрацію LWH. Перезапустіть запущену гру.", "keymap.none": "Немає", "keymap.keys": "Клавіші", "keymap.macro": "Макрос", "keymap.macro_n": "Макрос, кроків: {0}", "keymap.pick": "Виберіть кнопку", "keymap.click": "Клацніть, потім натисніть клавіші", "keymap.press": "Натисніть клавіші…", "keymap.record": "Клацніть, щоб записати", "keymap.delays": "Записувати паузи", "keymap.repeat": "Повторювати, поки натиснуто", "keymap.remove_step": "Видалити", "keymap.add_wait": "Додати паузу", "keymap.clear": "Очистити", "keymap.open": "Призначення клавіш…", "keymap.title": "Призначення клавіш", "keymap.close": "Готово", "keymap.help": "Профіль «{0}». Кнопка керма натискає клавіші клавіатури або відтворює макрос у будь-якій грі. Натисніть кнопку на кермі, щоб вибрати її. Поки Legacy Wheel Hub на передньому плані, нічого не надсилається.", "keymap.gear_n": "Передача {0}", "keymap.shifter_n": "Важіль {0}", "keymap.button_n": "Кнопка {0}", "keymap.recording_n": "Запис - {0} клавіш (клацніть деінде, щоб зупинити)", "lgs.title": "Налаштування гри", "lgs.set_by": "Встановлено {0}: {1}",
        "pedals.unsupported": "Це кермо не віддає свою вбудовану таблицю педалей, тому встановлені тут криві до нього не дійдуть. Самі педалі зчитуються нормально — для кривих використовуйте налаштування педалей у грі.",
        "tab.ffb": "ТЕСТ FFB",
        "tab.input": "МОНІТОР ВВОДУ",
        "tab.lut": "LUT",
        "lut.sec": "ПОСТ-ОБРОБКА FFB",
        "lut.enable": "Увімкнути пост-обробку FFB",
        "lut.enable_h": "Пропускати FFB гри через вибрану криву LUT.",
        "lut.mode": "Спосіб застосування",
        "lut.mode_proxy": "Допоміжна DLL (dinput8)",
        "lut.mode_direct": "Драйвер (без DLL)",
        "lut.mode_proxy_h": "Кладе dinput8.dll поруч із грою. Працює на будь-якому кермі.",
        "lut.mode_direct_h": "Жодного файлу в теці гри, зміни діють одразу. Потрібне кермо, драйвер якого відкриває криву.",
        "lut.mode_unsupported": "Недоступно на цьому кермі",
        "lut.mode_unsupported_h": "Драйвер цього керма не відкриває криву, тому тут і надалі використовується DLL.",
        "lut.select": "Крива LUT",
        "lut.import": "Імпортувати LUT",
        "lut.delete": "Видалити LUT",
        "lut.none": "(немає)",
        "settings.unreadable_title": "Не вдалося прочитати налаштування",
        "settings.unreadable_body": "settings.json пошкоджено, тому програма запустилася з типовими налаштуваннями. Старий файл збережено як {0} - ваші профілі в ньому.",
        "lut.gen_global_title": "LUT збережено",
        "lut.gen_global_body": "{0} є в списку LUT. Global не має гри, тому тут його не ввімкнено: виберіть профіль гри й оберіть LUT там.",
        "lut.del_title": "Видалення LUT",
        "lut.del_body": "Видалити файл LUT “{}” з диска? Це неможливо скасувати.",
        "lut.deleted": "LUT видалено",
        "lut.empty": "Файлів LUT ще немає. Натисніть “Імпортувати LUT”, щоб додати.",
        "lut.warn": "⚠  Кладе DLL поруч із грою — онлайн-гра на ваш ризик.",
        "lut.warn_direct": "У папку гри не додається жодних файлів.",
        "lut.global_notice": "LUT задається для кожної гри окремо. Створіть профіль гри й виберіть його LUT тут.",
        "lut.axis_in": "Вхід",
        "lut.axis_out": "Вихід",
        "lut.imported": "LUT імпортовано",
        "lut.import_fail": "Не вдалося імпортувати LUT",
        "lut.exe_none": "Гру не вибрано",
        "prof.edit": "Редагувати профіль",
        "prof.exe": "Виконуваний файл гри",
        "prof.exe_hint": "Для ігор на UE лаунчері виберіть справжній ...-Shipping.exe, якщо його не знайдено автоматично.",
        "prof.logo": "Значок (з .exe)",
        "prof.logo_pick": "Вибрати значок…",
        "prof.exe_pick": "Вибрати гру…",
        "prof.name_lbl": "Назва профілю",
        "proxy.installed": "Проксі встановлено",
        "proxy.installed_body": "dinput8.dll розміщено поруч із грою.",
        "proxy.locked": "Проксі вимкнено",
        "proxy.locked_body": "Буде видалено після закриття гри.",
        "proxy.removed": "Проксі видалено",
        "proxy.removed_body": "dinput8.dll видалено з папки гри.",
        "proxy.err_noexe": "Виконуваний файл гри не знайдено.",
        "proxy.err_arch": "Непідтримувана архітектура.",
        "proxy.err_asset": "Відсутній вбудований файл проксі (assets/proxy).",
        "proxy.err_write": "Не вдалося записати DLL (гра запущена? папка доступна для запису?).",
        "proxy.foreign_title": "Існуючий dinput8.dll",
        "proxy.foreign_body": "У цій папці вже є dinput8.dll, встановлений не цією програмою (інший мод/обгортка). Перезаписати його?",
        "tab.info": "ІНФО",
        "wheel.sec_ffb": "ЗВОРОТНИЙ ЗВ’ЯЗОК",
        "wheel.sec_steer": "НАЛАШТУВАННЯ КЕРМА",
        "ffb.reset": "Скинути FFB драйвера",
        "ffb.reset_h": "Видаляє всі значення реєстру FFB, записані цією програмою.",
        "set.title": "НАЛАШТУВАННЯ",
        "set.appearance": "ЗОВНІШНІЙ ВИГЛЯД",
        "set.general": "ЗАГАЛЬНІ",
        "set.testing": "ТЕСТУВАННЯ",
        "set.theme": "Тема",
        "set.language": "Мова",
        "set.tray": "Згортати в системний трей",
        "set.tray_h": "Згортання ховає програму в системний трей.",
        "set.devmode": "Режим визначення пристрою",
        "set.devmode_h": "Примусово вибрати розкладку керма без обладнання.",
        "set.ui_scale": "Масштаб інтерфейсу",
        "set.ui_scale_h": "Для екранів 1440p і 4K. Діє після перезапуску.",
        "set.restart_hint": "Перезапустіть програму, щоб застосувати новий масштаб.",
        "info.opmode_active": "Нативний розширений режим (розблоковано)",
        "info.opmode_idle": "Очікування / Відключено",
        "info.active": "Активно",
        "info.standby": "Очікування",
        "about.translation": "Переклад",
        "lut.replace_title": "Замінити LUT?",
        "lut.replace_body": "{0} уже існує. Замінити його імпортованим?",
        "dlg.overwrite": "Перезаписати",
        "lut.direct_waiting": "Очікування гри: криву драйвера буде перевірено, коли гра запустить FFB. До того LUT не застосовується.",
        "lut.direct_none": "Драйвер цього керма не відкриває криву, тому LUT не застосовується. Оберіть натомість DLL.",
    },
    "ru": {
        "input.doc_notice": "Названия и порядок кнопок взяты из драйвера Logitech. Где именно каждая кнопка находится на ободе, там не записано, поэтому они показаны списком, а не на рисунке.",
        "input.grid_notice": "Кнопки, изученные на этом компьютере, пронумерованы в порядке нажатия. Где они расположены на руле, настройка узнать не может.",
        "input.grid_notice_desc": "Кнопки пронумерованы так, как их сообщает сам руль, — те же номера, что показывают игры и программа Logitech.",
        "map.step_buttons": "Нажмите все кнопки по очереди",
        "map.hint_buttons": "Пройдитесь по рулю: лицевые кнопки, подрулевые лепестки, затем КПП, если она есть. Каждое нажатие записывается по порядку. Когда закончите — «Далее».",
        "map.title": "Настройка руля",
        "map.next": "Далее",
        "map.skip": "Пропустить",
        "map.restart": "Начать заново",
        "map.save": "Сохранить",
        "map.yes": "Да",
        "map.no": "Нет",
        "map.result": "Готово",
        "map.step_intro": "Прежде чем начать",
        "map.hint_intro": "На результат влияют три вещи.\n\nСначала несколько раз нажмите каждую педаль до упора. Руль запоминает их ход таким, каким его видел, поэтому педаль, которую ни разу не нажимали полностью, сообщает укороченный диапазон.\n\nИспользуйте пакет драйверов сообщества для Windows 10/11. Если вы заменили его другим драйвером, большая часть проверки вернётся пустой.\n\nЗапустите игру на несколько секунд. Драйвер создаёт свой блок FFB только тогда, когда его запрашивает игра, поэтому без этого проверка ниже ничего не найдёт.",
        "map.step_steer": "Поверните руль от упора до упора",
        "map.hint_steer": "До конца влево, затем до конца вправо. Нажмите «Далее», когда дойдёте до обоих упоров.",
        "map.step_rest": "Не трогайте педали",
        "map.hint_rest": "Уберите ноги, затем нажмите «Далее».",
        "map.step_throttle": "Нажмите газ",
        "map.hint_throttle": "Хотя бы раз до упора, затем «Далее».",
        "map.step_brake": "Нажмите тормоз",
        "map.hint_brake": "Хотя бы раз до упора, затем «Далее».",
        "map.step_clutch": "Нажмите сцепление",
        "map.hint_clutch": "Хотя бы раз до упора, затем «Далее». Пропустите, если у этого руля его нет.",
        "map.step_probe": "Проверка драйвера",
        "map.hint_probe": "Здесь ничего делать не нужно. Читаем блок FFB и таблицу педалей руля.",
        "map.step_ffb": "Руль толкнул в ответ?",
        "map.hint_ffb": "Его толкнули влево, затем вправо. Ответьте «Нет», если ничего не двигалось.",
        "map.step_curve": "Нажмите газ. Он «застревает» на середине?",
        "map.hint_curve": "В кривую газа записан провал. Любой ответ его отменит.",
        "map.step_range": "Поверните руль. Ход стал намного короче?",
        "map.hint_range": "Диапазон установлен на 200 градусов. Любой ответ его восстановит.",
        "map.live_waiting": "Ожидание руля",
        "map.live_steer": "влево {0}   вправо {1}   размах {2}",
        "map.live_rest": "Считываем положение покоя",
        "map.live_pedal": "замечено движение: {0}",
        "map.ffb_notsent": "Ничего не отправлено. Руль не открыт для записи.",
        "map.steer_ok": "Руль считывается правильно.",
        "map.steer_none": "Движения руля не замечено.",
        "map.verify": "Нажимайте каждую педаль. Двигаться должны только отмеченные полосы.",
        "map.nothing": "Ничего не сдвинулось достаточно, чтобы распознать. Начните заново и нажмите каждую педаль до упора.",
        "map.button": "Настроить этот руль…",
        "map.button_h": "Находит, где в отчёте этого руля лежат руль и педали, чтобы работала телеметрия, и проверяет, какие функции он поддерживает. Занимает пару минут.",
        "map.clear": "Использовать встроенную раскладку",
        "map.cleared": "Измеренная раскладка удалена",
        "map.share": "Поделиться этой настройкой",
        "map.share_title": "Помогите завершить поддержку этого руля",
        "map.share_body": "Всё нужное, чтобы правильно добавить ваш руль. Здесь описано только оборудование — без профилей, путей к файлам и названий игр.",
        "map.share_copied": "Скопировано в буфер обмена. Отправьте куда удобно или закройте это окно и ничего не отправляйте.",
        "map.share_github": "Открыть issue на GitHub",
        "map.share_mail": "Отправить по почте",
        "map.share_folder": "Показать файл",
        "map.share_close": "Закрыть",
        "map.share_saved": "Сохранено в {0}",
        "map.browser_title": "Открыть Gmail?",
        "map.browser_body": "Откроется окно Gmail в браузере с заполненным отчётом. В любом случае он уже в вашем буфере обмена.",
        "map.browser_ok": "Открыть браузер",
        "dev.bar_dismiss": "Скрыть до следующего раза",
        "dev.bar_unmapped": "Этот руль ещё в бета-версии. Его раскладка не измерена, поэтому телеметрия остаётся пустой, пока вы его не настроите.",
        "dev.bar_mapped": "Этот руль ещё в бета-версии. Он настроен на этом компьютере; FFB и угол поворота на этой модели не проверены.",
        "dev.beta_action": "Настроить",
        "dev.bar_unverified": "Этот руль работает по данным драйвера Logitech, но на настоящем оборудовании его ещё никто не подтвердил. Короткая проверка от вас решит вопрос для всех владельцев этой модели.",
        "dev.verify_action": "Проверить",
        "map.step_center": "Пружина автоцентрирования",
        "map.hint_center": "Отведите руль от центра и отпустите. Пружина возвращает его назад?",
        "dev.beta_remap": "Настроить ещё раз",
        "map.step_of": "Шаг {0} из {1}",
        "dev.beta": "Бета",
        "input.beta_notice": "Раскладка этого руля ещё не измерена, поэтому схема не рисуется. Кнопки и оси всё равно нормально доходят до ваших игр.",
        "conn.not_connected": "Не подключено",
        "home.steering": "УГОЛ ПОВОРОТА",
        "home.clutch": "СЦЕПЛ.",
        "home.brake": "ТОРМОЗ",
        "home.throttle": "ГАЗ",
        "home.na": "Н/Д",
        "prof.add": "Новый профиль",
        "prof.dup": "Дублировать профиль",
        "prof.del": "Удалить профиль",
        "prof.new_hint": "Название профиля",
        "prof.ren_title": "Переименование профиля",
        "prof.del_title": "Удаление профиля",
        "prof.del_msg": "Удалить профиль “{0}”? Это действие нельзя отменить.",
        "ffb.reset_ok": "FFB драйвера сброшен",
        "ffb.reset_ok_body": "Все значения реестра FFB, записанные этой программой, удалены.",
        "prof.exe_taken": "У игры уже есть профиль",
        "prof.exe_taken_body": "Эта игра уже назначена профилю «{0}». Одна игра может принадлежать только одному профилю, поэтому игра здесь не изменена.",
        "prof.name_taken": "Имя уже используется",
        "prof.name_taken_body": "Профиль с именем “{0}” уже существует, поэтому имя оставлено без изменений.",
        "input.mode_numbers": "Показывать номера кнопок",
        "input.mode_names": "Показывать названия кнопок",
        "ui.autogame": "Профиль по игре",
        "ui.autogame_h": "Применяет профиль запущенной игры.",
        "game.detected": "Игра обнаружена",
        "game.detected_body": "Применён пресет {0}.",
        "set.startup": "Запускать вместе с Windows",
        "set.startup_h": "Запускает программу при входе в систему. По умолчанию выключено.",
        "set.updates_sec": "ОБНОВЛЕНИЯ",
        "set.updates": "Проверять обновления при запуске",
        "set.updates_h": "Проверяет GitHub раз в день. Ничего не устанавливается автоматически.",
        "set.check_now": "Проверить сейчас",
        "set.checking": "Проверка…",
        "upd.available": "Доступно обновление",
        "upd.available_body": "Доступна версия {0}.",
        "upd.open": "Скачать",
        "upd.current": "Установлена последняя версия",
        "upd.current_body": "У вас самая новая версия.",
        "upd.failed": "Не удалось проверить обновления",
        "upd.failed_body": "Не удалось связаться с GitHub. Проверьте подключение и попробуйте снова.",
        "dlg.ok": "ОК",
        "dlg.cancel": "Отмена",
        "dlg.delete": "Удалить",
        "wheel.overall": "Общая сила эффектов",
        "wheel.overall_h": "101% убирает мёртвую зону FFB в центре в большинстве игр.",
        "wheel.spring": "Эффект пружины",
        "wheel.spring_h": "Насколько сильно собственные эффекты пружины игры доходят до руля — тяга к центру, которую используют многие игры. 100% — как отправляет игра, 0% — убирает её полностью.",
        "wheel.damper": "Эффект демпфера",
        "wheel.damper_h": "Насколько сильно собственные эффекты демпфера игры доходят до руля — вес, сопротивляющийся быстрому повороту. 100% — как отправляет игра, 0% — убирает его полностью.",
        "wheel.center_cb": "Включить пружину центрирования в играх с FFB",
        "wheel.center": "Пружина центрирования",
        "wheel.center_h": "Сила автоцентрирования на уровне драйвера.",
        "wheel.ramp": "Нарастание центрирования",
        "wheel.ramp_h": "Формирует центрирующую пружину. При 0% не действует.",
        "wheel.rotation": "Диапазон поворота",
        "wheel.rotation_h": "Максимальный угол поворота руля.",
        "ffb.title": "ТЕСТ ОБРАТНОЙ СВЯЗИ",
        "ffb.subtitle": "Проверьте мотор FFB напрямую. Лучше делать это, когда ни одна игра не использует руль.",
        "ffb.strength": "Сила теста",
        "ffb.strength_h": "Сила, используемая в тестах Push, Spring и Sweep.",
        "ffb.push_l": "Толкнуть влево",
        "ffb.push_r": "Толкнуть вправо",
        "ffb.spring": "Пружина (центр)",
        "ffb.spring_stop": "Остановить пружину",
        "ffb.sweep": "Автоцикл",
        "ffb.sweep_stop": "Остановить цикл",
        "ffb.advanced": "РАСШИРЕННЫЕ ТЕСТЫ МОТОРА",
        "ffb.pulse_l": "Импульс влево",
        "ffb.pulse_r": "Импульс вправо",
        "ffb.vibe_light": "Лёгкая вибрация",
        "ffb.vibe_med": "Средняя вибрация",
        "ffb.vibe_fast": "Быстрое дребезжание",
        "ffb.vibe_heavy": "Сильная вибрация",
        "ffb.stop": "ОСТАНОВИТЬ ВСЕ СИЛЫ",
        "input.led": "Тест приветствия LED",
        "input.wheel": "РУЛЬ",
        "input.shifter": "БЛОК ПЕРЕКЛЮЧЕНИЯ ПЕРЕДАЧ",
        "input.gear": "ПЕРЕДАЧА (H-СХЕМА)",
        "input.lpad": "ЛЕВЫЙ ЛЕПЕСТОК",
        "input.rpad": "ПРАВЫЙ ЛЕПЕСТОК",
        "input.led_nc": "Руль не подключён.",
        "input.led_g27": "Тест LED доступен только для G27 (у DFGT нет LED оборотов).",
        "input.led_run": "Приветствие LED… (работает, если драйвер передаёт этот отчёт)",
        "about.hwid": "Аппаратный ID",
        "about.axis": "Разрешение осей",
        "about.ffb": "Обратная связь",
        "about.theme_dark": "Тёмная",
        "about.theme_light": "Светлая",
        "about.testmode_h": "Переключает раскладку активного руля без подключённого оборудования. “Авто” использует реальное определение.",
        "about.test_auto": "Авто (определение)",
        "about.footer": "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "ДИАГНОСТИКА ОБОРУДОВАНИЯ",
        "about.sec_sensor": "ХАРАКТЕРИСТИКИ ДАТЧИКОВ И FFB",
        "about.sec_sw": "СОСТОЯНИЕ ПО И ДРАЙВЕРА",
        "about.sec_credits": "БЛАГОДАРНОСТИ",
        "about.devmodel": "Модель устройства",
        "about.interface": "Интерфейс",
        "about.power": "Состояние питания",
        "about.tracking": "Система слежения",
        "about.polling": "Макс. частота опроса",
        "about.opmode": "Режим работы",
        "about.api": "API-хук",
        "about.hub": "Версия хаба",
        "about.author": "Автор",
        "about.sec_about": "О ПРОГРАММЕ",
        "about.repo": "Репозиторий GitHub",
        "about.license": "Лицензия: GPL-3.0", "about.portable": "Портативная",
        "about.disclaimer": "Не связано с Logitech. Все товарные знаки принадлежат их владельцам.",
        "tray.show": "Показать",
        "tray.quit": "Выход",
        "apply.ok_title": "Применено",
        "apply.ok_body": "Настройки применены к рулю.",
        "ui.presets": "ПРЕСЕТЫ",
        "ui.autoload": "Авто при подключении",
        "ui.telemetry": "ТЕЛЕМЕТРИЯ",
        "ui.center": "Центрировать",
        "ui.apply": "ПРИМЕНИТЬ",
        "conn.connected": "Подключено",
        "conn.test": "Тестовый режим",
        "tab.wheel": "РУЛЬ",
        "tab.pedals": "ПЕДАЛИ",
        "pedals.gamma": "Гамма",
        "pedals.deadzone": "Мёртвая зона",
        "pedals.saturation": "Насыщение",
        "pedals.custom": "Своя",
        "pedals.reset": "К заводским",
        "pedals.note": "Хранится в памяти руля — после отключения возвращается заводская таблица.", "pedals.combine_title": "Объединённые педали", "pedals.combine": "Объединить педали", "pedals.combine_info": "Газ и тормоз на одной оси — для старых игр, которые этого ждут. Драйвер Logitech читает это при запуске руля: Windows один раз запросит разрешение, затем руль перезапустится.", "pedals.combine_on": "Педали объединены. Руль перезапущен.", "pedals.combine_off": "Педали снова раздельные. Руль перезапущен.", "pedals.combine_replug": "Сохранено. Отключите руль и подключите снова, чтобы применить.", "pedals.combine_denied": "Не изменено: разрешение Windows не получено.", "pedals.combine_failed": "Не удалось сохранить настройку.", "lgs.game": "Игра", "lgs.set_range": "{0} установила угол поворота {1}°.", "lgs.set_ffb": "{0} изменила настройки FFB.", "lgs.allow": "Разрешить игре изменять настройки", "lgs.allow_h": "«Разрешить игре изменять настройки» из Logitech Gaming Software, как это работало там: игра на SDK руля Logitech и аппаратное ограничение Content Manager могут, пока запущены, задавать угол поворота, включение FFB, силу, пружину и демпфер. Можно ли — решает профиль игры, иначе Global. Ничего не сохраняется; когда игра закрывается, возвращаются значения этого профиля. LWH также записывает действующие значения туда, где их хранил LGS, чтобы Content Manager возвращал то, что уже стоит на руле.", "cm.title": "Аппаратное ограничение Content Manager", "cm.state_ours": "Настроено: угол каждой машины передаётся на руль через LWH.", "cm.state_none": "Не настроено.", "cm.state_other": "В папке Content Manager есть другой LogitechSteeringWheel.dll (от LGS?).", "cm.install": "Настроить", "cm.remove": "Удалить", "cm.pick": "Найдите Content Manager.exe", "cm.installed": "Настроено. Перезапустите Content Manager, затем включите Hardware lock в настройках управления.", "cm.other": "В папке Content Manager уже есть другой LogitechSteeringWheel.dll; он не тронут.", "cm.error": "Не удалось скопировать файл в папку Content Manager.", "cm.removed": "Удалено из папки Content Manager.", "sdk.title": "Logitech SDK для игр", "set.compat": "СОВМЕСТИМОСТЬ", "sdk.h": "Только для игр с плагином движка Logitech (некоторые игры на Unity и Unreal), которые без Logitech Gaming Software или G HUB не дают обратной связи. Когда включено, в эти игры загружается собственный файл SDK от LWH. В играх с античитом держите выключенным.", "sdk.state_ours": "Зарегистрировано: игры с плагином Logitech для движка используют LWH.", "sdk.state_none": "Не зарегистрировано.", "sdk.state_other": "Зарегистрировано собственное ПО Logitech.", "sdk.register": "Зарегистрировать", "sdk.remove": "Удалить", "sdk.done_on": "Зарегистрировано. Игры, обращающиеся к SDK Logitech через свой движок, теперь находят руль через LWH; перезапустите запущенную игру.", "sdk.done_off": "Регистрация удалена.", "sdk.denied": "Не изменено: разрешение Windows не получено.", "sdk.failed": "Не удалось изменить регистрацию.", "cm.take_over": "Использовать LWH", "sdk.take_over": "Использовать LWH", "cm.taken": "Настроено. Другой файл отложен; он вернётся, когда удалите файл LWH. Перезапустите Content Manager.", "sdk.done_take": "Зарегистрировано. Регистрация Logitech отложена; она вернётся, когда удалите регистрацию LWH. Перезапустите запущенную игру.", "keymap.none": "Нет", "keymap.keys": "Клавиши", "keymap.macro": "Макрос", "keymap.macro_n": "Макрос, шагов: {0}", "keymap.pick": "Выберите кнопку", "keymap.click": "Щёлкните, затем нажмите клавиши", "keymap.press": "Нажмите клавиши…", "keymap.record": "Щёлкните, чтобы записать", "keymap.delays": "Записывать паузы", "keymap.repeat": "Повторять, пока нажата", "keymap.remove_step": "Удалить", "keymap.add_wait": "Добавить паузу", "keymap.clear": "Очистить", "keymap.open": "Назначение клавиш…", "keymap.title": "Назначение клавиш", "keymap.close": "Готово", "keymap.help": "Профиль «{0}». Кнопка руля нажимает клавиши клавиатуры или воспроизводит макрос в любой игре. Нажмите кнопку на руле, чтобы выбрать её. Пока Legacy Wheel Hub на переднем плане, ничего не отправляется.", "keymap.gear_n": "Передача {0}", "keymap.shifter_n": "Рычаг {0}", "keymap.button_n": "Кнопка {0}", "keymap.recording_n": "Запись - {0} клавиш (щёлкните в другом месте, чтобы остановить)", "lgs.title": "Настройки игры", "lgs.set_by": "Установлено {0}: {1}",
        "pedals.unsupported": "Этот руль не отдаёт свою встроенную таблицу педалей, поэтому заданные здесь кривые до него не дойдут. Сами педали считываются нормально — для кривых используйте настройки педалей в игре.",
        "tab.ffb": "ТЕСТ FFB",
        "tab.input": "МОНИТОР ВВОДА",
        "tab.lut": "LUT",
        "lut.sec": "ПОСТ-ОБРАБОТКА FFB",
        "lut.enable": "Включить пост-обработку FFB",
        "lut.enable_h": "Пропускать FFB игры через выбранную кривую LUT.",
        "lut.mode": "Способ применения",
        "lut.mode_proxy": "Вспомогательная DLL (dinput8)",
        "lut.mode_direct": "Драйвер (без DLL)",
        "lut.mode_proxy_h": "Кладёт dinput8.dll рядом с игрой. Работает на любом руле.",
        "lut.mode_direct_h": "Никаких файлов в папке игры, изменения действуют сразу. Нужен руль, драйвер которого открывает кривую.",
        "lut.mode_unsupported": "Недоступно на этом руле",
        "lut.mode_unsupported_h": "Драйвер этого руля не открывает кривую, поэтому здесь по-прежнему используется DLL.",
        "lut.select": "Кривая LUT",
        "lut.import": "Импортировать LUT",
        "lut.delete": "Удалить LUT",
        "lut.none": "(нет)",
        "settings.unreadable_title": "Не удалось прочитать настройки",
        "settings.unreadable_body": "settings.json повреждён, поэтому программа запустилась с настройками по умолчанию. Старый файл сохранён как {0} - ваши профили в нём.",
        "lut.gen_global_title": "LUT сохранён",
        "lut.gen_global_body": "{0} есть в списке LUT. У Global нет игры, поэтому здесь он не включён: выберите профиль игры и выберите LUT там.",
        "lut.del_title": "Удаление LUT",
        "lut.del_body": "Удалить файл LUT “{}” с диска? Это действие нельзя отменить.",
        "lut.deleted": "LUT удалён",
        "lut.empty": "Файлов LUT пока нет. Нажмите “Импортировать LUT”, чтобы добавить.",
        "lut.warn": "⚠  Кладёт DLL рядом с игрой — онлайн-игра на ваш риск.",
        "lut.warn_direct": "В папку игры не добавляется никаких файлов.",
        "lut.global_notice": "LUT задаётся для каждой игры отдельно. Создайте профиль игры и выберите его LUT здесь.",
        "lut.axis_in": "Вход",
        "lut.axis_out": "Выход",
        "lut.imported": "LUT импортирован",
        "lut.import_fail": "Не удалось импортировать LUT",
        "lut.exe_none": "Игра не выбрана",
        "prof.edit": "Редактировать профиль",
        "prof.exe": "Исполняемый файл игры",
        "prof.exe_hint": "Для игр на лаунчере UE выберите настоящий ...-Shipping.exe, если он не найден автоматически.",
        "prof.logo": "Значок (из .exe)",
        "prof.logo_pick": "Выбрать значок…",
        "prof.exe_pick": "Выбрать игру…",
        "prof.name_lbl": "Название профиля",
        "proxy.installed": "Прокси установлен",
        "proxy.installed_body": "dinput8.dll размещён рядом с игрой.",
        "proxy.locked": "Прокси отключён",
        "proxy.locked_body": "Будет удалён после закрытия игры.",
        "proxy.removed": "Прокси удалён",
        "proxy.removed_body": "dinput8.dll удалён из папки игры.",
        "proxy.err_noexe": "Исполняемый файл игры не найден.",
        "proxy.err_arch": "Неподдерживаемая архитектура.",
        "proxy.err_asset": "Отсутствует встроенный файл прокси (assets/proxy).",
        "proxy.err_write": "Не удалось записать DLL (игра запущена? папка доступна для записи?).",
        "proxy.foreign_title": "Существующий dinput8.dll",
        "proxy.foreign_body": "В этой папке уже есть dinput8.dll, установленный не этой программой (другой мод/обёртка). Перезаписать его?",
        "tab.info": "ИНФО",
        "wheel.sec_ffb": "ОБРАТНАЯ СВЯЗЬ",
        "wheel.sec_steer": "НАСТРОЙКИ РУЛЯ",
        "ffb.reset": "Сбросить FFB драйвера",
        "ffb.reset_h": "Удаляет все значения реестра FFB, записанные этой программой.",
        "set.title": "НАСТРОЙКИ",
        "set.appearance": "ВНЕШНИЙ ВИД",
        "set.general": "ОБЩИЕ",
        "set.testing": "ТЕСТИРОВАНИЕ",
        "set.theme": "Тема",
        "set.language": "Язык",
        "set.tray": "Сворачивать в системный трей",
        "set.tray_h": "Сворачивание прячет программу в системный трей.",
        "set.devmode": "Режим определения устройства",
        "set.devmode_h": "Принудительно выбрать раскладку руля без оборудования.",
        "set.ui_scale": "Масштаб интерфейса",
        "set.ui_scale_h": "Для экранов 1440p и 4K. Действует после перезапуска.",
        "set.restart_hint": "Перезапустите программу, чтобы применить новый масштаб.",
        "info.opmode_active": "Нативный расширенный режим (разблокирован)",
        "info.opmode_idle": "Ожидание / Отключено",
        "info.active": "Активно",
        "info.standby": "Ожидание",
        "about.translation": "Перевод",
        "lut.replace_title": "Заменить LUT?",
        "lut.replace_body": "{0} уже существует. Заменить его импортированным?",
        "dlg.overwrite": "Перезаписать",
        "lut.direct_waiting": "Ожидание игры: кривая драйвера проверяется, когда игра запускает FFB. До этого LUT не применяется.",
        "lut.direct_none": "Драйвер этого руля не открывает кривую, поэтому LUT не применяется. Выберите вместо этого DLL.",
    },
}
LANG_ORDER = [("de", "Deutsch"), ("en", "English"), ("tr", "Türkçe"), ("uk", "Українська"), ("ru", "Русский")]
CURRENT_LANG = "en"


def tr(key):
    d = LANG.get(CURRENT_LANG) or LANG["en"]
    return d.get(key, LANG["en"].get(key, key))


state = {"steer": STEER_CENTER, "steer_norm": 0.0, "throttle": 0, "brake": 0,
         "clutch": 0, "raw": [0] * 16, "connected": False}
dev = None
dev_lock = threading.Lock()
running = True
# Set while the LUT generator owns the wheel: the poller drains the HID
# queue every pass, and the measurement needs those reports itself.
lutgen_busy = False
active_profile = DEVICE_PROFILES["DFGT"]
test_override = None       # set from settings once they are loaded
main_window = None


PROFILE_DEFAULTS = {"lut_mode": "proxy", "angle": 900, "di_gain": 101, "di_spring": 100, "di_damper": 100,
                    "di_center": 0, "di_ramp": DEFAULT_RAMP, "di_persist": False,
                    "exe_path": "", "logo_exe": "", "lut_file": "", "lut_enabled": False,
                    "allow_game": True, "keymap": {}}


def _active_ramp():
    """Ramp of the currently selected profile (used by test/center actions so
    they match what APPLY sends)."""
    try:
        return int(global_settings["profiles"][global_settings["selected_profile"]]
                   .get("di_ramp", DEFAULT_RAMP))
    except Exception:
        return DEFAULT_RAMP


def _system_language():
    """Windows UI language, if we ship that language - else English.

    Only consulted on first run: once the user has a settings file their own
    choice wins, including an explicit switch back to English. QLocale is used
    rather than the locale module because it reports the UI language, not the
    number/date formatting region (a Turkish user on an English Windows should
    get English, and vice-versa).
    """
    try:
        from PySide6.QtCore import QLocale
        code = QLocale.system().name().split("_")[0].lower()
        if code in LANG:
            return code
    except Exception:
        pass
    return "en"


# Set when settings.json could not be read and was put aside: the path of the
# copy, so the window can say so instead of quietly starting from scratch.
SETTINGS_RECOVERED = None


def _read_settings_file():
    """settings.json as a dict. None if there is no file yet.

    The file was opened in Windows' ANSI code page, so a BOM (old Notepad) or
    a UTF-8 hand edit with Turkish letters made it unreadable, the app started
    on defaults and wrote those over the file on exit - every profile gone.
    Now UTF-8 (with or without BOM) is read first, the ANSI code page after,
    and a file that still cannot be read is copied aside before anything is
    written over it."""
    global SETTINGS_RECOVERED
    try:
        with open(SETTINGS_FILE, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    import locale
    for enc in ("utf-8-sig", locale.getpreferredencoding(False) or "cp1252"):
        try:
            v = json.loads(raw.decode(enc))
        except Exception:
            continue
        if isinstance(v, dict):
            return v
        break
    keep = SETTINGS_FILE + time.strftime(".unreadable-%Y%m%d-%H%M%S")
    try:
        with open(keep, "wb") as f:
            f.write(raw)
        SETTINGS_RECOVERED = keep
    except Exception:
        SETTINGS_RECOVERED = SETTINGS_FILE
    raise ValueError("settings.json unreadable")


def load_settings():
    base = {"theme": "dark", "language": _system_language(), "last_device": None, "auto_load": False,
            "minimize_to_tray": False, "check_updates": True, "auto_game_profile": True, "last_update_check": 0, "update_seen": "", "button_numbers": False, "test_device": None, "win_w": 1366, "win_h": 720, "last_tab": "wheel", "ui_scale": 100,
            "profiles": {"Global": copy.deepcopy(PROFILE_DEFAULTS)},
            "selected_profile": "Global"}
    try:
        v = _read_settings_file()
        if v is None:
            return base
        for k in base:
            v.setdefault(k, base[k])
        # A hand-edited or damaged file must not leave the app without the
        # Global profile or pointing at a profile that no longer exists -
        # several places index global_settings["profiles"]["Global"] directly.
        if not isinstance(v["profiles"], dict):
            v["profiles"] = {}
        for n in list(v["profiles"]):
            if not isinstance(v["profiles"][n], dict):
                v["profiles"][n] = {}
        v["profiles"].setdefault("Global", copy.deepcopy(PROFILE_DEFAULTS))
        if v.get("selected_profile") not in v["profiles"]:
            v["selected_profile"] = "Global"
        # Backfill keys added in later versions (e.g. di_ramp) so a settings
        # file written by an older build keeps its old behaviour instead of
        # hitting a missing key.
        try:
            for p in v["profiles"].values():
                for k, dv in PROFILE_DEFAULTS.items():
                    p.setdefault(k, copy.deepcopy(dv))
        except Exception:
            pass
        return v
    except Exception:
        return base


global_settings = load_settings()
CURRENT_LANG = global_settings.get("language", "en")
if CURRENT_LANG not in LANG:
    CURRENT_LANG = "en"


REPO_URL = "https://github.com/Sadooo27/legacy-wheel-hub"
RELEASES_API = "https://api.github.com/repos/Sadooo27/legacy-wheel-hub/releases/latest"


def open_releases_page():
    """Open the GitHub releases page in the user's browser."""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(REPO_URL + "/releases/latest"))
    except Exception:
        try:
            import webbrowser
            webbrowser.open(REPO_URL + "/releases/latest")
        except Exception:
            pass


def _ver_tuple(v):
    """'v1.1.4' -> (1, 1, 4, 1); 'v2.0.0-rc1' -> (2, 0, 0, 0).

    The last number says whether it is a final release, so a test build sorts
    below the release it leads up to. Before, the digits of "0-rc1" were run
    together and "v2.0.0-rc1" read as 2.0.1 - newer than 2.0.0 itself.
    Unparsable parts become 0 so a malformed tag can never look newer than a
    real version."""
    s = str(v).strip().lstrip("vV")
    core, _, pre = s.partition("-")
    nums = []
    for part in core.split("."):
        digits = ""
        for c in part:
            if not c.isdigit():
                break
            digits += c
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3]) + (0 if pre else 1,)


def is_test_tag(tag):
    """Test builds are tagged with a suffix (v2.0.0-beta1, v2.0.0-rc1). The
    releases feed lists them next to real releases - GitHub's pre-release
    flag is not in it - so the suffix is what keeps them from being offered
    to everyone as an update."""
    return "-" in str(tag)


RELEASES_FEED = REPO_URL + "/releases.atom"


def _url_open(url, timeout):
    """Open a URL, resolving IPv4 first.

    Python asks for A and AAAA records together and waits for both. On a
    network with no working IPv6 the AAAA query just times out - measured at
    ~12 s here, which is the entire cost of an update check that should take
    a fraction of a second. Browsers hide this by racing both families
    (Happy Eyeballs); urllib does not.

    So: force IPv4, and fall back to the default resolver if that finds
    nothing, which keeps genuinely IPv6-only networks working.
    """
    import http.client
    import socket
    import urllib.request

    class _V4Connection(http.client.HTTPSConnection):
        def connect(self):
            infos = socket.getaddrinfo(self.host, self.port,
                                       socket.AF_INET, socket.SOCK_STREAM)
            last = None
            for af, kind, proto, _c, addr in infos:
                sock = socket.socket(af, kind, proto)
                try:
                    if self.timeout is not None:
                        sock.settimeout(self.timeout)
                    sock.connect(addr)
                    self.sock = sock
                    break
                except OSError as e:
                    sock.close()
                    last = e
            else:
                raise last or OSError("no IPv4 address for %s" % self.host)
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)

    class _V4Handler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_V4Connection, req)

    req = urllib.request.Request(url, headers={
        "User-Agent": "LegacyWheelHub/%s" % HUB_VERSION,
        "Accept": "application/vnd.github+json, application/atom+xml, */*"})
    try:
        return urllib.request.build_opener(_V4Handler()).open(req, timeout=timeout)
    except Exception:
        return urllib.request.urlopen(req, timeout=timeout)


def _fetch_tag_from_api(timeout):
    # /releases/latest already leaves out drafts and pre-releases
    with _url_open(RELEASES_API, timeout) as r:
        tag = json.loads(r.read().decode("utf-8")).get("tag_name")
    return None if not tag or is_test_tag(tag) else tag


def _fetch_tag_from_feed(timeout):
    """Read the newest tag from the releases Atom feed.

    Preferred over the API because api.github.com allows only 60
    unauthenticated requests per hour PER IP: behind a VPN, CGNAT or a company
    gateway that quota is shared with strangers and is often already spent, so
    the API can fail permanently through no fault of the user. The feed is
    served by github.com itself and has no such quota.

    Only the first few KB are read. The whole feed carries the release notes
    of the last ten releases (~20 KB) but the newest tag appears in the first
    <entry> within the first kilobyte - downloading the rest just to throw it
    away is wasted time on a slow or tunnelled connection.
    """
    import re as _re
    pat = _re.compile(r"<id>tag:github\.com[^<]*/([^/<]+)</id>")
    with _url_open(RELEASES_FEED, timeout) as r:
        buf = b""
        for _ in range(16):                     # the feed is ~20 KB; cap at 64
            chunk = r.read(4096)
            if not chunk:
                break
            buf += chunk
    text = buf.decode("utf-8", "replace")
    # Every entry, not only the first: the newest entry can be a test build
    # (skipped) or a v1.2.x fix published after v2 (not the highest). The
    # feed's own <id> comes before the first <entry> and has no tag path.
    tags = [m.group(1) for part in text.split("<entry>")[1:]
            for m in [pat.search(part)] if m]
    tags = [t for t in tags if not is_test_tag(t)]
    return max(tags, key=_ver_tuple) if tags else None


def fetch_latest_version(timeout=6):
    """Latest release tag from GitHub, or None if anything goes wrong.

    Tries the Atom feed first, then the JSON API as a backup.

    Failures are swallowed on purpose: no network, GitHub down, a corporate
    proxy - none of that is the user's problem, and an update check must never
    interrupt someone who just wants to configure their wheel.
    """
    # Feed first: it is the source that actually works for everyone. The API
    # is kept as a fallback for the unlikely case the feed format changes.
    for source in (_fetch_tag_from_feed, _fetch_tag_from_api):
        try:
            tag = source(timeout)
            if tag:
                return tag
        except Exception:
            continue
    return None


_settings_lock = threading.Lock()


def save_settings():
    """Write to a temporary file and swap it in.

    Writing over the file directly means a crash or a power cut mid-write
    leaves a truncated settings.json, and every profile with it.
    """
    # The poller thread saves too (it records the detected wheel), so this can
    # run concurrently with the GUI thread editing profiles. Without the lock,
    # json.dump can walk a dict that another thread is mutating and blow up
    # with "dictionary changed size during iteration" - or write a truncated
    # file. Serialising the dump keeps settings.json always well-formed.
    with _settings_lock:
        # Snapshot first, then write. json.dump walks the live dict while
        # holding the file open, so a GUI-thread edit landing mid-write
        # could raise "dictionary changed size during iteration" or leave
        # a truncated file. deepcopy is used rather than .copy() because a
        # shallow copy still shares the nested "profiles" dict - the very
        # one that grows when a preset is added. The copy itself can hit the
        # same race, so it is retried rather than given up on.
        snapshot = None
        for _ in range(3):
            try:
                snapshot = copy.deepcopy(global_settings)
                break
            except Exception:
                snapshot = None
        try:
            # Temp file then os.replace, which is atomic: the old settings stay
            # intact until the new ones are completely on disk, so an
            # interrupted write can never leave a half-written file behind.
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot if snapshot is not None else global_settings,
                          f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, SETTINGS_FILE)
        except Exception:
            try: os.remove(tmp)
            except Exception: pass


def set_language(code):
    global CURRENT_LANG
    if code not in LANG:
        return
    CURRENT_LANG = code
    global_settings["language"] = code
    save_settings()


# Declared before anything can touch them: set_test_override runs at import
# to restore a saved override, and it clears the reader.
usage_reader = None
open_device_path = ""

def is_beta():
    return bool(active_profile.get("beta"))


def is_unverified(prof=None):
    """Supported, but not yet seen working on real hardware.

    G27 and DFGT were measured on the hardware. The others are built from
    Logitech's own driver files and the Linux driver - good sources that
    agreed wherever they could be compared, but a source is not a test. Kept
    apart from "beta", which meant data was missing: nothing is missing
    here, it is only unconfirmed."""
    return not (prof or active_profile).get("verified")


def layout_unknown(prof=None):
    """True when the steering should be read from the descriptor.

    Either the profile has no measured layout, or the wizard has been run and
    found the steering that way. The second case matters: a built-in layout
    can be wrong for a particular wheel - a revision that moved an axis, or
    simply the wrong profile selected - and before this the wizard could map
    the pedals and leave the steering reading the wrong bytes with no way to
    correct it.
    """
    prof = active_profile if prof is None else prof
    if prof.get("steer") is None:
        return True
    m = saved_map(prof)
    return bool(m and m.get("steer_ok"))


def set_test_override(key):
    global active_profile, test_override, usage_reader
    was = test_override
    test_override = key
    if key in DEVICE_PROFILES:
        active_profile = DEVICE_PROFILES[key]
    elif was is not None:
        # Back to automatic: the wheel actually plugged in takes over again,
        # rather than the test device staying in charge until a replug.
        try:
            prof, _pid = _detect_profile()
        except Exception:
            prof = None
        if prof is not None:
            active_profile = prof
    # The new profile may read its steering a different way, so the old reader
    # is dropped and the poller builds whatever this one needs.
    if usage_reader is not None:
        usage_reader.close()
        usage_reader = None


# Re-apply a forced test device from the last session. Saving the choice
# without restoring it here would leave the dropdown showing an override that
# isn't actually in effect - the setting would look applied but do nothing.
set_test_override(global_settings.get("test_device"))


def _detect_profile():
    try:
        pids = [d["product_id"] for d in hid.enumerate(VID)]
    except Exception:
        return None, None
    # Every profile's own PID, measured ones first so a wheel we understand
    # properly is never mistaken for one we do not. Adding a wheel to
    # DEVICE_PROFILES is enough to have it detected - naming them by hand here
    # was why the wheels added in v1.2 were defined, listed in the selector,
    # and then never found when plugged in.
    order = ([k for k, v in DEVICE_PROFILES.items() if not v.get("beta")] +
             [k for k, v in DEVICE_PROFILES.items() if v.get("beta")])
    for key in order:
        prof = DEVICE_PROFILES[key]
        pid = prof["pid_native"]
        # C294 is both the Driving Force's own identity and the compatibility
        # PID every other wheel falls back to, so it cannot be claimed here.
        if pid != PID_COMPAT and pid in pids:
            return prof, pid
    if PID_COMPAT in pids:
        # A real Driving Force, or a wheel still in compatibility mode: either
        # way the report is the Driving Force layout (compatibility mode is
        # an emulation of it). The DFGT profile used to be assumed here,
        # which reads a different layout.
        return DEVICE_PROFILES["DF"], PID_COMPAT
    return None, None


# Native-mode switch commands, per wheel. Logitech's own driver keeps them as
# a table (wmfilter.sys in Logitech Gaming Software 5.10), and the Linux
# driver sends the same bytes:
#
#   G27, DFGT   0xF8 0x0A (revert on USB reset), then 0xF8 0x09 <mode> 0x01
#   G25         0xF8 0x10
#   DFP         0xF8 0x01
#
# Only G27 and DFGT understand the extended 0x09 command. The first version
# sent it to every wheel, so a G25 or DFP in compatibility mode never left it
# and was then opened with the DFGT profile.
MODE_SWITCH_CMDS = {
    "DFGT": [[0xF8, 0x0A, 0, 0, 0, 0, 0], [0xF8, 0x09, 0x03, 0x01, 0, 0, 0]],
    "G27":  [[0xF8, 0x0A, 0, 0, 0, 0, 0], [0xF8, 0x09, 0x04, 0x01, 0, 0, 0]],
    "G25":  [[0xF8, 0x10, 0, 0, 0, 0, 0]],
    "DFP":  [[0xF8, 0x01, 0, 0, 0, 0, 0]],
}

# Every multimode wheel reports as C294 in compatibility mode. The firmware
# revision tells them apart (the Linux driver's table): masked, it gives the
# real wheel. Order matters - a G27's 0x1230 also fits the G25 test - and a
# C294 that matches none of them is a real Driving Force (the INF lists
# those as revisions 0x2000-0x2200), which has no other mode to go to.
COMPAT_IDENT = [
    ("DFGT", 0xFF00, 0x1300),
    ("G27",  0xFFF0, 0x1230),
    ("G25",  0xFF00, 0x1200),
    ("DFP",  0xF000, 0x1000),
]


def identify_compat_wheel(release):
    """Which wheel a C294 really is, from its firmware revision; None for a
    real Driving Force."""
    for key, mask, value in COMPAT_IDENT:
        if release & mask == value:
            return key
    return None


def _switch_mode(key):
    h = hid.device(); h.open(VID, PID_COMPAT)
    for cmd in MODE_SWITCH_CMDS[key]:
        h.write([0x00] + cmd)
        time.sleep(0.1)
    # The wheel yanks itself off the USB bus on the mode-switch command, so
    # closing the (now dead) handle routinely raises. That is expected, not a
    # failure of the switch - letting it propagate would abort the caller
    # before it ever waits for the wheel to re-enumerate.
    try: h.close()
    except Exception: pass


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
        for prof in DEVICE_PROFILES.values():
            if prof["pid_native"] != PID_COMPAT and prof["pid_native"] in present:
                return
        if PID_COMPAT not in present: return
        info = next(d for d in devs if d["product_id"] == PID_COMPAT)
        release = int(info.get("release_number") or 0)
        if release:
            # The revision says which wheel this is - or that it is a real
            # Driving Force, which stays as it is.
            key = identify_compat_wheel(release)
            order = [key] if key else []
        else:
            # No revision from this hidapi build: fall back to the product
            # name as a hint, then each wheel in turn.
            name = (info.get("product_string") or "").lower()
            hint = next((k for k in MODE_SWITCH_CMDS if k.lower() in name), None)
            if hint is None and "gt" in name:
                hint = "DFGT"
            order = ([hint] if hint else []) + [k for k in MODE_SWITCH_CMDS if k != hint]
        for key in order:
            try:
                _switch_mode(key)
            except Exception:
                continue
            if _wait_for_pid(DEVICE_PROFILES[key]["pid_native"]):
                return
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  Reading an axis without knowing where it sits
# ---------------------------------------------------------------------------
# G27 and DFGT have measured byte offsets. The wheels added in v1.2 do not,
# and inventing them would produce a telemetry readout that looks right and
# is not. Windows already knows the layout: HidP_GetUsageValue pulls a usage
# out of a report using the device's own descriptor, so the steering angle can
# be read correctly on a wheel nobody here has ever seen.
#
# The pedals and buttons come the same way. Which usage is which pedal is not
# something a descriptor states, but for this family Logitech's own driver
# does: its joystick INF (wmjoyhid.inf, Logitech Gaming Software 5.10) names
# the G25's axes - accelerator Z, brake Rz, clutch on the slider, Y the
# combined-pedals axis its driver can synthesise. See descriptor_pedals().
# The buttons are the descriptor's own numbered buttons, which is how
# Logitech's software showed them too: its INF gives the G25 nineteen
# buttons and no names.

_hidp = None
try:
    import ctypes as _ct
    from ctypes import wintypes as _wt
    _hidp = _ct.WinDLL("hid")
    _hidp.HidD_GetPreparsedData.argtypes = [_wt.HANDLE, _ct.POINTER(_ct.c_void_p)]
    _hidp.HidD_FreePreparsedData.argtypes = [_ct.c_void_p]
    _hidp.HidP_GetUsageValue.argtypes = [_ct.c_int, _wt.USHORT, _wt.USHORT,
                                         _wt.USHORT, _ct.POINTER(_wt.ULONG),
                                         _ct.c_void_p, _ct.c_char_p, _wt.ULONG]
except Exception:
    _hidp = None

import ctypes as _ctx


class _HIDP_CAPS(_ctx.Structure):
    """hidpi.h HIDP_CAPS, 64 bytes."""
    _fields_ = [("Usage", _ctx.c_uint16), ("UsagePage", _ctx.c_uint16),
                ("InputReportByteLength", _ctx.c_uint16), ("OutputReportByteLength", _ctx.c_uint16),
                ("FeatureReportByteLength", _ctx.c_uint16), ("Reserved", _ctx.c_uint16 * 17),
                ("NumberLinkCollectionNodes", _ctx.c_uint16),
                ("NumberInputButtonCaps", _ctx.c_uint16), ("NumberInputValueCaps", _ctx.c_uint16),
                ("NumberInputDataIndices", _ctx.c_uint16),
                ("NumberOutputButtonCaps", _ctx.c_uint16), ("NumberOutputValueCaps", _ctx.c_uint16),
                ("NumberOutputDataIndices", _ctx.c_uint16),
                ("NumberFeatureButtonCaps", _ctx.c_uint16), ("NumberFeatureValueCaps", _ctx.c_uint16),
                ("NumberFeatureDataIndices", _ctx.c_uint16)]


class _HIDP_VALUE_CAPS(_ctx.Structure):
    """hidpi.h HIDP_VALUE_CAPS, 72 bytes. The Range/NotRange union is laid out
    as Range: a single usage sits where UsageMin does, its data index where
    DataIndexMin does."""
    _fields_ = [("UsagePage", _ctx.c_uint16), ("ReportID", _ctx.c_uint8), ("IsAlias", _ctx.c_uint8),
                ("BitField", _ctx.c_uint16), ("LinkCollection", _ctx.c_uint16),
                ("LinkUsage", _ctx.c_uint16), ("LinkUsagePage", _ctx.c_uint16),
                ("IsRange", _ctx.c_uint8), ("IsStringRange", _ctx.c_uint8),
                ("IsDesignatorRange", _ctx.c_uint8), ("IsAbsolute", _ctx.c_uint8),
                ("HasNull", _ctx.c_uint8), ("Reserved", _ctx.c_uint8),
                ("BitSize", _ctx.c_uint16), ("ReportCount", _ctx.c_uint16),
                ("Reserved2", _ctx.c_uint16 * 5), ("UnitsExp", _ctx.c_uint32), ("Units", _ctx.c_uint32),
                ("LogicalMin", _ctx.c_int32), ("LogicalMax", _ctx.c_int32),
                ("PhysicalMin", _ctx.c_int32), ("PhysicalMax", _ctx.c_int32),
                ("UsageMin", _ctx.c_uint16), ("UsageMax", _ctx.c_uint16),
                ("StringMin", _ctx.c_uint16), ("StringMax", _ctx.c_uint16),
                ("DesignatorMin", _ctx.c_uint16), ("DesignatorMax", _ctx.c_uint16),
                ("DataIndexMin", _ctx.c_uint16), ("DataIndexMax", _ctx.c_uint16)]


if _hidp is not None:
    try:
        _hidp.HidP_GetCaps.argtypes = [_ct.c_void_p, _ct.POINTER(_HIDP_CAPS)]
        _hidp.HidP_GetValueCaps.argtypes = [_ct.c_int, _ct.POINTER(_HIDP_VALUE_CAPS),
                                            _ct.POINTER(_ct.c_uint16), _ct.c_void_p]
        _hidp.HidP_GetUsages.argtypes = [_ct.c_int, _ct.c_uint16, _ct.c_uint16,
                                         _ct.POINTER(_ct.c_uint16), _ct.POINTER(_ct.c_uint32),
                                         _ct.c_void_p, _ct.c_char_p, _ct.c_uint32]
        _hidp.HidP_MaxUsageListLength.argtypes = [_ct.c_int, _ct.c_uint16, _ct.c_void_p]
        _hidp.HidP_MaxUsageListLength.restype = _ct.c_uint32
    except Exception:
        pass

HID_Y, HID_Z, HID_RZ, HID_SLIDER, HID_HAT = 0x31, 0x32, 0x35, 0x36, 0x39
HID_PAGE_DESKTOP, HID_PAGE_BUTTON = 0x01, 0x09


def descriptor_pedals(values):
    """Which of a wheel's own axes are its pedals.

    values: (usage_page, usage, data_index, logical_min, logical_max) for each
    input value the wheel's descriptor declares.

    Logitech's joystick INF names this family's axes. For the G25: accelerator
    Z, brake Rz, clutch on the slider (DirectInput axes 2, 5 and 6), and Y
    for the combined-pedals axis its driver can synthesise. That naming is
    recognised by its clutch on the slider and then used as it stands, in
    whatever order the report carries it. Z and Rz alone are not enough: a
    descriptor that numbers three pedals Y, Z, Rz would have its brake read
    as the accelerator and lose its clutch.

    Otherwise the pedals are taken in report order, accelerator first - the
    order both wheels measured here, G27 and DFGT, report them in - leaving
    out a Y that is there only as an extra axis.
    """
    cand = sorted((v for v in values if v[0] == HID_PAGE_DESKTOP
                   and v[1] not in (HID_USAGE_X, HID_HAT)), key=lambda v: v[2])
    by = {v[1]: v for v in cand}
    if HID_SLIDER in by and HID_Z in by and HID_RZ in by:
        pick = [by[HID_Z], by[HID_RZ], by[HID_SLIDER]]
    else:
        if len(cand) > 3 and HID_Y in by:
            cand = [v for v in cand if v[1] != HID_Y]
        pick = cand[:3]
    return {k: p for k, p in zip(("throttle", "brake", "clutch"), pick) if p}

HID_USAGE_X = 0x30
HIDP_INPUT = 0
HIDP_STATUS_SUCCESS = 0x00110000


class _UsageReader:
    """Reads a HID usage out of raw input reports for one open device."""

    def __init__(self, path):
        # The poller thread reads through this while the GUI thread may close
        # it; the HID parser calls release the GIL, so freeing the preparsed
        # data mid-call would be a use-after-free. One lock around both.
        self._lock = threading.RLock()
        self.h = None
        self._form = None          # 0 = as received, 1 = report ID prepended
        self.pp = _ct.c_void_p() if _hidp else None
        if not _hidp or not path:
            return
        try:
            k32 = _ct.WinDLL("kernel32", use_last_error=True)
            k32.CreateFileW.argtypes = [_wt.LPCWSTR, _wt.DWORD, _wt.DWORD,
                                        _ct.c_void_p, _wt.DWORD, _wt.DWORD,
                                        _wt.HANDLE]
            k32.CreateFileW.restype = _wt.HANDLE
            self._k32 = k32
            h = k32.CreateFileW(path, 0, 3, None, 3, 0, None)   # query only
            if h == _wt.HANDLE(-1).value:
                return
            if not _hidp.HidD_GetPreparsedData(h, _ct.byref(self.pp)):
                k32.CloseHandle(h); return
            self.h = h
        except Exception:
            self.h = None
        self.values = []           # (page, usage, data index, logical min, max)
        self.pedals = {}           # "throttle"/"brake"/"clutch" -> one of values
        self.button_count = 0
        if self.ok():
            try:
                self._read_layout()
            except Exception:
                self.values, self.pedals, self.button_count = [], {}, 0

    def _read_layout(self):
        """Every input value the descriptor declares, and how many buttons."""
        caps = _HIDP_CAPS()
        if _hidp.HidP_GetCaps(self.pp, _ct.byref(caps)) != HIDP_STATUS_SUCCESS:
            return
        n = caps.NumberInputValueCaps
        if n:
            arr = (_HIDP_VALUE_CAPS * n)()
            ln = _ct.c_uint16(n)
            if _hidp.HidP_GetValueCaps(HIDP_INPUT, arr, _ct.byref(ln), self.pp) == HIDP_STATUS_SUCCESS:
                for c in arr[:ln.value]:
                    if c.IsRange:
                        for k, u in enumerate(range(c.UsageMin, c.UsageMax + 1)):
                            self.values.append((c.UsagePage, u, c.DataIndexMin + k,
                                                c.LogicalMin, c.LogicalMax))
                    else:
                        self.values.append((c.UsagePage, c.UsageMin, c.DataIndexMin,
                                            c.LogicalMin, c.LogicalMax))
        self.pedals = descriptor_pedals(self.values)
        self.button_count = int(_hidp.HidP_MaxUsageListLength(HIDP_INPUT, HID_PAGE_BUTTON, self.pp))

    def ok(self):
        return self.h is not None and bool(self.pp)

    def pedal(self, key, report, invert=True):
        """One pedal as 0..255, pressed = high, from the descriptor."""
        v = self.pedals.get(key)
        if v is None:
            return None
        raw = self.value(v[1], report, page=v[0])
        if raw is None:
            return None
        lo, hi = v[3], v[4]
        if hi <= lo:
            return None
        x = int(round((min(max(raw, lo), hi) - lo) * 255 / (hi - lo)))
        return 255 - x if invert else x

    def buttons(self, report):
        """Numbers of the buttons held down, as the descriptor numbers them."""
        with self._lock:
            return self._buttons(report)

    def _buttons(self, report):
        if not self.ok() or not self.button_count:
            return set()
        raw = bytes(bytearray(report))
        forms = ([raw, b"\x00" + raw] if self._form is None
                 else [b"\x00" + raw if self._form else raw])
        for buf in forms:
            try:
                lst = (_ct.c_uint16 * self.button_count)()
                ln = _ct.c_uint32(self.button_count)
                st = _hidp.HidP_GetUsages(HIDP_INPUT, HID_PAGE_BUTTON, 0, lst, _ct.byref(ln),
                                          self.pp, buf, len(buf))
                if st == HIDP_STATUS_SUCCESS:
                    return {int(u) for u in lst[:ln.value]}
            except Exception:
                pass
        return set()

    def value(self, usage, report, page=0x01):
        """Pull one usage out of a report, whichever shape hidapi handed us.

        HIDP_STATUS_SUCCESS is 0x00110000, not zero: the HID parser returns
        facility-coded statuses, and comparing against 0 made this return None
        every single time.

        The other half of it is the report itself. Windows wants it exactly as
        the descriptor describes, report-ID byte included, and hidapi only
        prepends that byte for devices with numbered reports. So both shapes
        are tried, and whichever answers is remembered rather than retried
        sixty times a second.
        """
        with self._lock:
            return self._value(usage, report, page)

    def _value(self, usage, report, page):
        if not self.ok():
            return None
        raw = bytes(bytearray(report))
        forms = ([raw, b"\x00" + raw] if self._form is None
                 else [b"\x00" + raw if self._form else raw])
        for idx, buf in enumerate(forms):
            try:
                out = _wt.ULONG()
                st = _hidp.HidP_GetUsageValue(HIDP_INPUT, page, 0, usage,
                                              _ct.byref(out), self.pp,
                                              buf, len(buf))
                if st == HIDP_STATUS_SUCCESS:
                    if self._form is None:
                        self._form = idx
                    return out.value
            except Exception:
                pass
        return None

    def close(self):
        with self._lock:
            try:
                if self.pp:
                    _hidp.HidD_FreePreparsedData(self.pp); self.pp = None
                if self.h:
                    self._k32.CloseHandle(self.h); self.h = None
            except Exception:
                pass


def ensure_usage_reader():
    """Build the descriptor reader if this profile needs one and has none."""
    global usage_reader
    if usage_reader is not None or not open_device_path:
        return usage_reader
    r = _UsageReader(open_device_path)
    usage_reader = r if r.ok() else None
    return usage_reader


# ---------------------------------------------------------------------------
#  Learned pedal layouts
# ---------------------------------------------------------------------------
# Which byte of the input report carries which pedal was measured for G27 and
# DFGT and is unknown for everything else. Rather than guess, the wheel can be
# asked: press a pedal, see what moves. What comes out is stored per device
# and takes precedence over the built-in offsets, so a wheel nobody here has
# held reads correctly, and a wheel we do know can still be re-measured if a
# firmware revision ever moves something.

def device_key_for(prof):
    for k, v in DEVICE_PROFILES.items():
        if v is prof:
            return k
    return None


def saved_map(prof):
    return (global_settings.get("device_maps") or {}).get(device_key_for(prof))


def store_map(prof, mapping):
    key = device_key_for(prof)
    if not key:
        return
    global_settings.setdefault("device_maps", {})[key] = mapping
    save_settings()


def clear_map(prof):
    key = device_key_for(prof)
    maps = global_settings.get("device_maps") or {}
    if key in maps:
        del maps[key]
        save_settings()


def learned_buttons(prof):
    """Button bits this wheel taught us, in the order they were pressed."""
    m = saved_map(prof) or {}
    return [tuple(b) for b in (m.get("buttons") or []) if len(b) == 2]


def descriptor_buttons_ready(prof):
    """True when this wheel's buttons are read from its own descriptor:
    its layout is not built in and nothing was taught on this machine."""
    return (layout_unknown(prof) and not learned_buttons(prof)
            and usage_reader is not None and usage_reader.button_count > 0)


def decode_learned(raw, prof):
    """Which of the learned buttons are down right now - or, where none were
    taught, the buttons the wheel's descriptor reports."""
    r = usage_reader                    # the poller may swap it meanwhile
    if descriptor_buttons_ready(prof) and r is not None:
        return r.buttons(raw)
    out = set()
    for idx, (byte, bit) in enumerate(learned_buttons(prof), start=1):
        if byte < len(raw) and raw[byte] & (1 << bit):
            out.add(idx)
    return out


def effective_layout(prof):
    """Byte offsets to read this wheel with: measured here, or built in."""
    m = saved_map(prof)
    if m:
        return (m.get("throttle"), m.get("brake"), m.get("clutch"),
                bool(m.get("pedal_invert", True)))
    return (prof.get("throttle"), prof.get("brake"), prof.get("clutch"),
            bool(prof.get("pedal_invert", True)))


def solve_steer_layout(samples):
    """Work out how the steering is packed, from raw reports and known values.

    The descriptor already gives us the angle through HidP_GetUsageValue, so
    telemetry works on a wheel nobody has measured. What it does not give is
    where the value sits in the report - and that is what a profile entry
    needs. With a sweep recorded as (raw bytes, angle) pairs, the packing can
    be solved rather than guessed: try every byte pair against each of the
    three layouts this family uses, and keep whichever reproduces the angle on
    every single sample. One mismatch disqualifies a candidate, so a wrong
    answer needs every sample in the sweep to agree by coincidence.

    Returns a dict shaped like the "steer" entry in DEVICE_PROFILES, ready to
    be pasted in, or None when nothing fits.
    """
    if len(samples) < 20:
        return None
    n = min(len(r) for r, _ in samples)
    vals = [v for _, v in samples]
    lo_v, hi_v = min(vals), max(vals)
    if hi_v - lo_v < 100:
        return None

    def decode(raw, lo, hi, fmt, mask):
        if fmt == "single":
            return raw[lo]
        if fmt == "hilo6":
            return (raw[hi] << 6) | (raw[lo] >> 2)
        return raw[lo] | ((raw[hi] & mask) << 8)

    best = None
    for lo in range(n):
        # single-byte first: an 8-bit wheel has no second byte to pair with
        if all(decode(r, lo, lo, "single", 0) == v for r, v in samples):
            best = {"fmt": "single", "lo": lo, "hi": lo}
            break
        for hi in range(n):
            if hi == lo:
                continue
            for fmt, mask in (("hilo6", 0), ("lohi", 0xFF),
                              ("lohi", 0x3F), ("lohi", 0x0F)):
                if all(decode(r, lo, hi, fmt, mask) == v for r, v in samples):
                    best = {"fmt": fmt, "lo": lo, "hi": hi}
                    if fmt == "lohi" and mask != 0xFF:
                        best["himask"] = mask
                    break
            if best:
                break
        if best:
            break
    if not best:
        return None
    # Centre and half-span from what was actually swept, not from the format's
    # theoretical maximum - a wheel that stops short still centres correctly.
    best["center"] = (lo_v + hi_v + 1) // 2
    best["half"] = max(1, (hi_v - lo_v + 1) // 2)
    return best


def analyse_pedal_samples(rest, presses):
    """Turn "what moved when I pressed each pedal" into byte offsets.

    presses maps a pedal name to the medians recorded while it was held down.

    The wheels publish a combined pedals axis alongside the individual ones,
    so pressing the throttle moves two bytes at once and a single measurement
    cannot say which is which. Taking all the pedals together settles it: the
    combined axis is the byte that answers to more than one of them, and moves
    opposite ways for throttle and brake. It is ruled out before anything is
    assigned.
    """
    if not rest:
        return None
    n = min(len(rest), 16)
    moved, appears = {}, {}
    for key, got in presses.items():
        if not got:
            continue
        deltas = [(abs(got[i] - rest[i]), i, got[i] - rest[i]) for i in range(n)]
        # Under about 20 counts is noise or pedal spring flutter, not a press.
        moved[key] = sorted([d for d in deltas if d[0] > 20], reverse=True)
        for _, idx, signed in moved[key]:
            appears.setdefault(idx, []).append(signed)
    combined = {i for i, hits in appears.items()
                if len(hits) > 1 and len({h > 0 for h in hits}) > 1}

    out = {"throttle": None, "brake": None, "clutch": None,
           "pedal_invert": True, "combined": sorted(combined)}
    used = set()
    for key in ("throttle", "brake", "clutch"):
        for _, idx, signed in moved.get(key, []):
            if idx in combined or idx in used:
                continue
            out[key] = idx
            used.add(idx)
            out["pedal_invert"] = signed < 0
            break
    return out if any(out[k] is not None for k in ("throttle", "brake", "clutch")) else None


def open_device():
    global dev, active_profile
    prof, pid = _detect_profile()
    if prof is None:
        dev = None; state["connected"] = False; return False
    try:
        d = hid.device(); d.open(VID, pid); d.set_nonblocking(1)
        dev = d; active_profile = prof; state["connected"] = True
        # The 10-bit/14-bit steering span is learned per wheel; a flag left
        # over from a previously connected wheel would scale this one wrong.
        state.pop("_wide", None)
        global usage_reader, open_device_path
        if usage_reader is not None:
            usage_reader.close(); usage_reader = None
        path = ""
        for info in hid.enumerate(VID, pid):
            path = info.get("path") or ""
            if isinstance(path, bytes):
                path = path.decode("utf-8", "ignore")
            break
        # Kept whether or not it is needed right now: switching the forced test
        # device changes the profile without reopening the wheel, and a reader
        # built only at open time was never built at all in that case - the
        # steering simply never moved.
        open_device_path = path
        if prof.get("steer") is None:
            ensure_usage_reader()
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
            if lutgen_busy:
                time.sleep(0.05)
                continue
            if dev is None:
                ensure_native_mode(); open_device(); time.sleep(1); continue
            try:
                # One snapshot per pass: the GUI thread can swap active_profile
                # (test device selector) at any moment, and reading it twice
                # could see a profile with a layout in the check and one
                # without in the use - a TypeError that dropped the wheel.
                prof = active_profile
                data = None
                with dev_lock:
                    while True:
                        chunk = dev.read(64)
                        if chunk: data = chunk
                        else: break
                if data and len(data) >= 4 and layout_unknown(prof):
                    # No measured layout: ask Windows for the steering usage.
                    raw = None
                    ur = ensure_usage_reader()
                    if ur is not None:
                        raw = ur.value(HID_USAGE_X, data)
                    if raw is not None:
                        span = 16383 if raw > 1023 or state.get("_wide") else 1023
                        if raw > 1023: state["_wide"] = True
                        half = (span + 1) // 2
                        state["steer"] = raw
                        state["steer_norm"] = max(-1.0, min(1.0, (raw - half) / half))
                    t_b, b_b, c_b, inv = effective_layout(prof)
                    def _ax0(b):
                        if b is None or b >= len(data): return 0
                        return (255 - data[b]) if inv else data[b]
                    ur = usage_reader
                    if saved_map(prof) is None and ur is not None and ur.pedals:
                        # Nothing taught on this machine: the wheel's own
                        # descriptor says where each pedal is.
                        for key in ("throttle", "brake", "clutch"):
                            v = ur.pedal(key, data, inv)
                            state[key] = v if v is not None else 0
                    else:
                        state["throttle"] = _ax0(t_b)
                        state["brake"] = _ax0(b_b)
                        state["clutch"] = _ax0(c_b)
                    state["raw"] = list(data[:16]); state["connected"] = True
                elif data and len(data) >= report_min_len(prof):
                    sd = prof["steer"]; fmt = sd.get("fmt", "lohi")
                    if fmt == "hilo6":
                        steer = (data[sd["hi"]] << 6) | (data[sd["lo"]] >> 2)
                    elif fmt == "single":
                        steer = data[sd["lo"]]
                    else:
                        steer = data[sd["lo"]] | ((data[sd["hi"]] & sd.get("himask", 0x3F)) << 8)
                    state["steer"] = steer
                    state["steer_norm"] = max(-1.0, min(1.0, (steer - sd["center"]) / sd["half"]))
                    # A layout measured on this machine wins over the built-in
                    # one: it came from this wheel, not another of the model.
                    t_b, b_b, c_b, inv = effective_layout(prof)
                    def _ax(b):
                        if b is None or b >= len(data): return 0
                        return (255 - data[b]) if inv else data[b]
                    state["throttle"] = _ax(t_b)
                    state["brake"] = _ax(b_b)
                    state["clutch"] = _ax(c_b)
                    state["raw"] = list(data[:16]); state["connected"] = True
                if data:
                    keymap_tick(prof, state.get("raw") or [])
            except Exception:
                # Close the handle before dropping it. Just setting dev=None
                # leaks the hidapi handle: Windows keeps the device claimed,
                # so re-plugging the wheel can fail to reopen with "access
                # denied" until the app restarts.
                state["connected"] = False
                try:
                    keymapper.feed(set())          # no key left held by a vanished wheel
                except Exception:
                    pass
                if dev is not None:
                    try:
                        with dev_lock: dev.close()
                    except Exception: pass
                    dev = None
            time.sleep(0.005)


# HID button numbers, read straight off the bit order in decode_buttons_*
# below. In a HID report the buttons occupy consecutive bits (after the 4-bit
# hat nibble) and are numbered in exactly that order, so bit index + 1 is the
# "Button N" a game shows. Keeping this table next to the decoders is
# deliberate: if a bit assignment there ever changes, the numbers here have to
# change with it.
HID_BUTTON_NUMBERS = {
    "DFGT": {"sh_x": 1, "sh_square": 2, "sh_circle": 3, "sh_triangle": 4,
             "paddle_right": 5, "paddle_left": 6, "r2": 7, "l2": 8,
             "select": 9, "start": 10, "r3": 11, "l3": 12,
             "up": 13, "dn": 14, "dial_enter": 15, "plus": 16,
             "dial_right": 17, "dial_left": 18, "minus": 19, "horn": 20, "ps": 21},
    "G27":  {"red_1": 1, "red_2": 2, "red_3": 3, "red_4": 4,
             "paddle_right": 5, "paddle_left": 6, "wheel_rt": 7, "wheel_lt": 8,
             "gear_1": 9, "gear_2": 10, "gear_3": 11, "gear_4": 12,
             "gear_5": 13, "gear_6": 14, "gear_r": 15,
             "sh_triangle": 16, "sh_square": 17, "sh_x": 18, "sh_circle": 19,
             "wheel_rm": 20, "wheel_lm": 21, "wheel_rb": 22, "wheel_lb": 23},
}


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


def report_min_len(prof):
    """How many bytes a report must have for this profile to be read from it.

    The poller used to require 8 for every wheel. Driving Force, MOMO Racing
    and MOMO Force send 7-byte reports (their descriptors say so), so those
    wheels were never read at all - connected, and frozen. The real minimum
    is the last byte of the steering and the two main pedals, plus one; the
    clutch and the buttons check their own bounds, so a report too short for
    them still gives the angle."""
    need = 1
    sd = prof.get("steer") or {}
    for k in ("lo", "hi"):
        if sd.get(k) is not None:
            need = max(need, sd[k] + 1)
    for k in ("throttle", "brake"):
        if prof.get(k) is not None:
            need = max(need, prof[k] + 1)
    return need


def documented_buttons(prof):
    """(byte, bit, name) for each button of a wheel whose layout is known
    from Logitech's driver and the Linux descriptors, in report order."""
    b = prof.get("buttons")
    if not b:
        return []
    return [((b["first_bit"] + i) // 8, (b["first_bit"] + i) % 8, name)
            for i, name in enumerate(b["names"])]


def documented_hat(prof, raw):
    """Hat direction 0-7 (0 = up, clockwise), or None when centred or absent."""
    h = prof.get("hat")
    if not h or not raw or h["byte"] >= len(raw):
        return None
    v = (raw[h["byte"]] >> h["shift"]) & 0x0F
    return v if v < 8 else None


# The G27's buttons the G25 does not have: the lower two on each side of the
# wheel (L2/L3, R2/R3). Everything else the G25 sends in the G27's places.
G27_ONLY = {"wheel_lm", "wheel_rm", "wheel_lb", "wheel_rb"}


def g25_drawn(prof=None):
    """True when the G25 is shown as a G27 without its four lower wheel
    buttons. The G25 sends its buttons in the G27's order - confirmed on a
    real G25 by a G25 owner, and the same as the Linux and SDL drivers read
    it - so its own descriptor is not needed for them. A layout taught in
    the setup wizard still comes first."""
    prof = active_profile if prof is None else prof
    return prof is DEVICE_PROFILES["G25"] and not learned_buttons(prof)


def decode_buttons(raw):
    if active_profile is DEVICE_PROFILES["G27"]:
        return decode_buttons_g27(raw)
    if g25_drawn() and not documented_buttons(active_profile):
        return decode_buttons_g27(raw) - G27_ONLY
    if layout_unknown(active_profile):
        return {str(n) for n in decode_learned(raw, active_profile)}
    doc = documented_buttons(active_profile)
    if doc:
        return {name for byte, bit, name in doc
                if byte < len(raw) and raw[byte] & (1 << bit)}
    return decode_buttons_dfgt(raw)


def rotation_cmd(deg): return [0xF8, 0x81, deg & 0xFF, (deg >> 8) & 0xFF, 0, 0, 0]
def rotation_cmds_dfp(deg):
    """Rotation for the Driving Force Pro, which does not take 0xF8 0x81.

    The DFP sets its range in two parts: a coarse range (0xF8 0x02 = 200
    degrees, 0xF8 0x03 = 900 degrees), then a fine limit (0x81 0x0B) that
    puts the stops inside it. This follows lg4ff_set_range_dfp() in the Linux
    new-lg4ff driver, which is where every published account of the DFP
    protocol comes from. The previous version sent 0xF8 0x00 plus the G25
    rotation opcode and packed the fine-limit bytes in the wrong order, so
    it could never have set the range.

    Unlike everything else in here, it has not been seen working: nobody on
    this side has a DFP. It is offered rather than withheld because the cost
    of it being wrong is nil - a wheel ignores a command it does not
    recognise - while withholding it leaves a control greyed out for no
    reason. It stays marked beta until somebody with the wheel says otherwise.
    """
    deg = max(40, min(900, int(deg)))
    if deg > 200:
        coarse, full_range = 0x03, 900
    else:
        coarse, full_range = 0x02, 200
    cmds = [[0xF8, coarse, 0x00, 0x00, 0x00, 0x00, 0x00]]
    if deg in (200, 900):
        # The coarse range already is the answer: clear any fine limit.
        cmds.append([0x81, 0x0B, 0x00, 0x00, 0x00, 0x00, 0x00])
        return cmds
    start_left = ((full_range - deg + 1) * 2047) // full_range
    start_right = 0xFFF - start_left
    cmds.append([0x81, 0x0B, start_left >> 4, start_right >> 4, 0xFF,
                 ((start_right & 0x0E) << 4) | (start_left & 0x0E), 0xFF])
    return cmds
def send_rotation(deg):
    """Set the wheel's range, by whichever protocol it speaks.

    MOMO, MOMO Force and Driving Force are fixed at 270 degrees and have
    nothing to set, so they get nothing rather than a command that would look
    like it worked.
    """
    kind = active_profile.get("range_cmd")
    if kind == "g25":
        ffb_write(rotation_cmd(deg))
    elif kind == "dfp":
        for c in rotation_cmds_dfp(deg):
            ffb_write(c)
            time.sleep(0.01)


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
    """Send one command to the wheel. Returns whether it left the machine.

    Callers that do not care can still ignore the result, but the mapping
    wizard needs it: "the user felt nothing" and "the command never got sent"
    are different answers, and reporting them as one would have somebody
    conclude their wheel has no force feedback when the handle was simply not
    open.
    """
    if dev is None:
        return False
    try:
        with dev_lock:
            dev.write([0x00] + cmd)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
#  Live force feedback (G27)
# ---------------------------------------------------------------------------
# The Logitech driver keeps a shared-memory block per wheel:
#
#     WmJoyFrcData_VID_046D&PID_C29B
#
# and applies whatever is in it on every force update. Writing it changes the
# force mid-race, which is why gain normally needs a game restart: the driver
# only reads the registry when a game initialises force feedback, then works
# from this block.
#
# Layout, mapped by dumping the block with one setting changed at a time:
#
#     0x04..0x1FC   transfer curve, 127 entries, output 0..127
#     0x208 / 0x214 overall gain, x100 (two copies, both written)
#
# The curve is where strength above the model's threshold actually lives. The
# driver (wmjoyfrc.dll) builds it from the gain g (x100) and a threshold T it
# keeps at 0x204 of the block:
#
#     d = 1 + 3 * (g - T) / (15000 - T)   when g > T, else 1;   p = 1 / d
#     out[i] = 127 * (i/127) ** p + 0.5
#
# T is 10000 on the G25, G27 and DFGT - where this is p = 1 / (6 * gain/100 - 5),
# the formula measured on the G27 at 101%, 125% and 150% - 9500 on the DFP and
# both MOMOs and 9000 on the Driving Force: those bend their curve already at
# 100%. Below T the curve stays linear and the gain field attenuates instead.
#
# Named shared memory is ordinary user-mode territory: no proxy DLL in the
# game, no kernel call, no administrator rights.
#
# Every wheel on this driver: G25, G27, DFGT, DFP and both MOMOs share one
# wmjoyfrc.dll, and the block's layout is the same for all of them (mapped
# from the driver's own code). Measured on the G27; on any other wheel the
# block is checked before anything is written (live_block_verified), and a
# wheel that fails the check keeps the registry path.
WMJOYFRC_MAP = "WmJoyFrcData_VID_%04X&PID_%04X"
LIVE_GAIN_PID = 0xC29B                 # G27, native mode
# 128 entries, not 127: the table runs 0x04..0x200 inclusive. The dumps show
# it plainly - at 101% the tail reads ...124, 125, 126 and 0x200 holds 127,
# continuing the sequence. Leaving that last entry alone meant peak forces (a
# kerb strike, a hard jolt) always went through at full torque no matter what
# the rest of the curve said.
LIVE_CURVE_START, LIVE_CURVE_COUNT, LIVE_MAX_OUT = 0x04, 128, 127
LIVE_GAIN_OFFSETS = (0x208, 0x214)
LIVE_THRESHOLD_OFFSET = 0x204
# The threshold per model, as wmfilter.sys sets it when the block is first
# made; the block's own value is read whenever it is open and wins.
CURVE_THRESHOLDS = {0xC29B: 10000, 0xC299: 10000, 0xC29A: 10000,      # G27, G25, DFGT
                    0xC298: 9500, 0xCA03: 9500, 0xC295: 9500,         # DFP, MOMO Racing, MOMO Force
                    0xC294: 9000, 0xC293: 9000}                       # Driving Force/EX/RX, Formula Force GP
_live_threshold = {}


def curve_threshold(pid=None):
    """The gain (x100) above which this wheel's driver bends its curve."""
    pid = active_profile.get("pid_native") if pid is None else pid
    return _live_threshold.get(pid) or CURVE_THRESHOLDS.get(pid, 10000)


def _note_threshold(pid, value):
    """Keep the threshold read from the block, if it is a sensible one."""
    if 5000 <= int(value) < 15000:
        _live_threshold[pid] = int(value)
_FILE_MAP_READ, _FILE_MAP_WRITE = 0x0004, 0x0002


_live_verified = {}


def _live_block_open(pid, write=False):
    """Open the driver's force-feedback block for a wheel, or return None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenFileMappingW.restype = wintypes.HANDLE
    k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    access = _FILE_MAP_READ | (_FILE_MAP_WRITE if write else 0)
    base = WMJOYFRC_MAP % (VID, pid)
    for name in (base, "Global\\" + base, "Local\\" + base):
        h = k32.OpenFileMappingW(access, False, name)
        if not h:
            continue
        addr = k32.MapViewOfFile(h, access, 0, 0, 0)
        if not addr:
            k32.CloseHandle(h)
            continue
        return k32, h, addr
    return None


def _live_block_snapshot(pid):
    """A readable slice of the driver's block, for checking the layout by eye.

    The verification above answers yes or no. When the answer is no on a wheel
    we have never seen, the useful thing is not the verdict but the bytes: the
    gain fields, the ends of the curve table, and the block's own length.
    """
    opened = None
    try:
        import ctypes
        opened = _live_block_open(pid, write=False)
        if not opened:
            return None
        k32, h, addr = opened
        def u32(off):
            return ctypes.c_uint32.from_address(addr + off).value
        curve = [u32(LIVE_CURVE_START + 4 * i) for i in range(128)]
        out = [f"  gain fields  {[u32(o) for o in LIVE_GAIN_OFFSETS]}",
               f"  curve first  {curve[:8]}",
               f"  curve last   {curve[-8:]}",
               f"  rising       {all(curve[i] <= curve[i+1] for i in range(127))}",
               f"  threshold    {u32(LIVE_THRESHOLD_OFFSET)} (0x204; G27 10000, DFP/MOMO 9500, DF 9000)"]
        return "\n".join(out)
    except Exception:
        return None
    finally:
        if opened:
            try:
                import ctypes
                k32, h, addr = opened
                k32.UnmapViewOfFile(ctypes.c_void_p(addr))
                k32.CloseHandle(h)
            except Exception:
                pass


def live_block_verified(pid):
    """Whether this wheel's block really has the layout we measured on a G27.

    Assuming it does, because the driver is the same one, is not the same as
    knowing. Two things are checked before anything is written: the gain field
    has to hold what the registry says the gain is - which is what proves the
    offset is right rather than merely readable - and the table has to be 128
    entries that never decrease. A wheel that fails either gets nothing
    written to it at all, which is the only safe way to offer this on hardware
    nobody here has.
    """
    if pid in _live_verified:
        return _live_verified[pid]
    ok = False
    opened = None
    try:
        import ctypes
        opened = _live_block_open(pid, write=False)
        if opened:
            k32, h, addr = opened
            # The registry value the driver was last told to use. Matching it
            # is what proves the offset is the gain field and not merely a
            # readable number.
            want = None
            for rp in active_profile.get("registry_pids", []):
                try:
                    key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        rf"Software\Logitech\Gaming Software\DriverSettings\{rp}")
                    want = winreg.QueryValueEx(key, "OverallStrength")[0]
                    winreg.CloseKey(key)
                    break
                except Exception:
                    continue
            got = ctypes.c_uint32.from_address(addr + LIVE_GAIN_OFFSETS[0]).value
            _note_threshold(pid, ctypes.c_uint32.from_address(addr + LIVE_THRESHOLD_OFFSET).value)
            table = [ctypes.c_uint32.from_address(
                addr + LIVE_CURVE_START + 4 * i).value for i in range(128)]
            rising = all(table[i] <= table[i + 1] for i in range(127))
            in_range = all(0 <= v <= 127 for v in table)
            # a LUT at 100% or less leaves the field just past the threshold
            # (lut_gain_field): ours too, not a different layout
            ours = want is not None and got == lut_gain_field(int(want), curve_threshold(pid))[0]
            ok = bool(rising and in_range and table[-1] == 127
                      and (want is None or got == want or ours))
    except Exception:
        ok = False
    finally:
        if opened:
            try:
                import ctypes
                k32, h, addr = opened
                k32.UnmapViewOfFile(ctypes.c_void_p(addr))
                k32.CloseHandle(h)
            except Exception:
                pass
    # Only remember a verdict reached by actually reading the block. The
    # driver creates it when a game first starts force feedback, so "not
    # there yet" at launch must not disable the direct mode for the session.
    if opened:
        _live_verified[pid] = ok
    return ok


def live_gain_supported():
    """Measured on the G27; proven at run time on anything else."""
    pid = active_profile.get("pid_native")
    if pid == LIVE_GAIN_PID:
        return True
    return live_block_verified(pid)


def live_block_state():
    """"ok" when the driver path works on this wheel, "different" when its
    block was read and is not the layout we know, "waiting" while no game has
    opened the block yet - the driver only creates it then."""
    pid = active_profile.get("pid_native")
    if live_gain_supported():
        return "ok"
    return "different" if pid in _live_verified else "waiting"


def live_gain_curve(gain_pct, threshold=None):
    """The transfer curve the driver itself builds for this gain, with this
    wheel's threshold (see the note above LIVE_GAIN_PID): a live change gives
    exactly what the same strength gives after a game restart."""
    t = curve_threshold() if threshold is None else int(threshold)
    g = max(1, int(gain_pct)) * 100
    d = 1.0 + 3.0 * (g - t) / (15000 - t) if g > t else 1.0
    p = 1.0 / d
    n = LIVE_CURVE_COUNT - 1
    return [int(LIVE_MAX_OUT * (i / n) ** p + 0.5) for i in range(LIVE_CURVE_COUNT)]


def lut_to_curve(points, gain_pct):
    """Turn LUT points into the driver's table, with strength applied on top.

    Two stages rather than one replacing the other: the LUT decides the shape
    of the force, strength decides its level. Below 100% the driver's gain
    field attenuates on its own, so the LUT goes in untouched; above it the
    extra force comes from the curve being bent, so the bend is composed onto
    the LUT here - otherwise loading a LUT would quietly cap the wheel at 100%.
    """
    n = LIVE_CURVE_COUNT - 1
    pts = sorted(points)
    out = []
    for i in range(LIVE_CURVE_COUNT):
        x = i / n
        if x <= pts[0][0]:
            y = pts[0][1]
        elif x >= pts[-1][0]:
            y = pts[-1][1]
        else:
            y = pts[-1][1]
            for j in range(1, len(pts)):
                if pts[j][0] >= x:
                    x0, y0 = pts[j - 1]
                    x1, y1 = pts[j]
                    y = y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                    break
        out.append(max(0, min(LIVE_MAX_OUT, round(LIVE_MAX_OUT * y))))
    # 100% is the LUT exactly, on every wheel: that is what a generated LUT
    # was measured for and what "keep it at 100%" promises. The driver's own
    # threshold sits lower on the DFP, MOMOs and Driving Force; using it here
    # would bend the LUT a second time at 100%.
    if int(gain_pct) > 100:
        bend = live_gain_curve(gain_pct, threshold=10000)
        out = [bend[min(LIVE_CURVE_COUNT - 1, v)] for v in out]
    return out


def lut_gain_field(want, threshold):
    """The gain field to write with a LUT in the table, and how much to scale
    the table by.

    The driver uses its table only when the gain is above the wheel's
    threshold (10000 on the G25, G27 and DFGT). At 100% or less it attenuates
    with the gain field and leaves the table alone, so a LUT written there had
    no effect at all - which is why it only ever worked at 101% and up. Below
    the threshold the field is set just past it, so the table is used, and the
    strength is put into the table instead: 100% is the LUT exactly, 80% the
    LUT at 80%.
    """
    t = int(threshold or 10000)
    if want > t:
        return want, 1.0
    return max(t + 1, 10000), max(0.0, min(1.0, want / 10000.0))


def profile_live_lut(prof):
    """LUT points for a profile, but only when it is set to the DLL-free mode.

    Returns None in proxy mode so the two paths can never both shape the same
    force: the proxy already remapped it on the way in, and bending it again
    here would apply the curve twice.
    """
    if prof.get("lut_mode", "proxy") != "direct":
        return None
    if not prof.get("lut_enabled") or not prof.get("lut_file"):
        return None
    pts = parse_lut_file(os.path.join(_lut_dir(), prof["lut_file"]))
    return pts or None


def push_live_ffb(gain_pct, lut_points=None, lut_on=False):
    """Apply gain, and optionally a LUT, to the running game.

    Writes the gain field and the table together: the field alone only
    attenuates below 100%, while everything above it comes from the table's
    shape. Setting one without the other is what makes 101% and 150% feel
    identical.
    """
    if sys.platform != "win32" or not live_gain_supported():
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.OpenFileMappingW.restype = wintypes.HANDLE
        k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                      wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        access = _FILE_MAP_READ | _FILE_MAP_WRITE
        base = WMJOYFRC_MAP % (VID, active_profile.get("pid_native",
                                                        LIVE_GAIN_PID))
        for name in (base, "Global\\" + base, "Local\\" + base):
            h = k32.OpenFileMappingW(access, False, name)
            if not h:
                continue                       # no game has started FFB yet
            addr = k32.MapViewOfFile(h, access, 0, 0, 0)
            if not addr:
                k32.CloseHandle(h)
                continue
            try:
                want = max(0, int(gain_pct)) * 100
                pid = active_profile.get("pid_native", LIVE_GAIN_PID)
                _note_threshold(pid, ctypes.c_uint32.from_address(addr + LIVE_THRESHOLD_OFFSET).value)
                # A LUT through the DLL (lut_on, no points here) is shaped
                # before the driver: 100% has to stay a straight line for it,
                # as it does for a LUT written into the table.
                curve = (lut_to_curve(lut_points, gain_pct) if lut_points
                         else live_gain_curve(gain_pct, threshold=10000) if lut_on
                         else live_gain_curve(gain_pct))
                if lut_points:
                    # The driver reads the table only above its threshold:
                    # at 100% or less a LUT written there is never used.
                    want, scale = lut_gain_field(want, curve_threshold(pid))
                    if scale < 1.0:
                        curve = [max(0, min(LIVE_MAX_OUT, round(v * scale))) for v in curve]
                for off in LIVE_GAIN_OFFSETS:
                    ctypes.c_uint32.from_address(addr + off).value = want
                for i, v in enumerate(curve):
                    ctypes.c_uint32.from_address(
                        addr + LIVE_CURVE_START + 4 * i).value = v
                return ctypes.c_uint32.from_address(
                    addr + LIVE_GAIN_OFFSETS[0]).value == want
            finally:
                k32.UnmapViewOfFile(ctypes.c_void_p(addr))
                k32.CloseHandle(h)
    except Exception:
        pass
    return False


FIXED_RANGE = 270


def wheel_range(angle, prof=None):
    """The rotation the wheel really has: the angle that was set on a wheel
    that takes a range command, its fixed 270 degrees on one that does not
    (Driving Force, MOMO Racing, MOMO Force). The live view drew those at
    450 degrees full lock and the driver was told Turn=900."""
    prof = active_profile if prof is None else prof
    return int(angle) if prof.get("range_cmd") in ("g25", "dfp") else FIXED_RANGE


def num_setting(key, default):
    """A number from the settings file, or `default` when a hand edit left
    something else there (null, text)."""
    try:
        return float(global_settings.get(key, default))
    except (TypeError, ValueError):
        return default


def ui_scale_setting():
    """The interface scale in percent. A hand-edited settings file can hold
    anything here - null, text, 0 - and the Settings tab used to crash on it;
    anything that is not a sensible number counts as 100."""
    try:
        v = int(global_settings.get("ui_scale", 100))
    except (TypeError, ValueError):
        return 100
    return v if 50 <= v <= 400 else 100


def update_registry_ffb(gain, spring, damper, center, persist, angle):
    if winreg is None: return
    angle = wheel_range(angle)
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


# ---------------------------------------------------------------------------
#  Combined pedals (LGS: "Report Combined Pedals")
# ---------------------------------------------------------------------------
# Logitech's filter driver reads this one per wheel when the wheel starts:
# CombinePedals, a DWORD (non-zero = on) under its own service key,
#     HKLM\SYSTEM\CurrentControlSet\Services\<filter>\Parameters\VID_046D&PID_xxxx
# (RtlQueryRegistryValues relative to the services key). The filter is
# wmfilter.sys (service WmFilter) with LGS 5.10, and LGJoyHidFilter.sys
# (service LGJoyHidFilter) with the community driver package for Windows 11,
# which attaches that one to the wheels instead - same value, same layout.
# Every wheel here reads it: G27, G25, DFGT, DFP, Driving Force and both MOMOs.
# It is read only while the wheel starts, so a change takes a restart of the
# wheel's USB device, and the key is in HKLM, so writing it takes
# administrator rights. LGS had a service of its own for that; here the app
# starts a second copy of itself with administrator rights for the one write
# (one Windows prompt), and that copy restarts the wheel.
#
# The key is not opened up to users instead: the driver reads the value
# straight into a DWORD with no type check, and a key that a kernel driver
# reads that way must not be writable by every program the user runs.
_SERVICES = r"SYSTEM\CurrentControlSet\Services" + "\\"
COMBINE_FILTERS = ("LGJoyHidFilter", "WmFilter")      # first found is read
WMFILTER_SERVICE = _SERVICES + "WmFilter"
WMFILTER_PARAMS = WMFILTER_SERVICE + r"\Parameters"
COMBINE_DONE, COMBINE_FAILED, COMBINE_REPLUG = 0, 1, 2


def _reg64():
    return getattr(winreg, "KEY_WOW64_64KEY", 0) if winreg is not None else 0


def combine_pedals_pids(prof=None):
    """The registry names the setting is kept under for this wheel: its own
    and, for the multimode wheels, the compatibility-mode one."""
    prof = prof or active_profile
    return list(prof.get("registry_pids") or [])


def combine_pedals_state(prof=None):
    """True or False as Logitech's driver will read it; None when no filter
    driver that reads it is installed (no service key), so there is nothing
    to set."""
    if winreg is None:
        return None
    pids = combine_pedals_pids(prof)
    if not pids:
        return None
    filters = combine_filters()
    if not filters:
        return None
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             _SERVICES + filters[0] + "\\Parameters\\" + pids[0], 0,
                             winreg.KEY_READ | _reg64())
        try:
            value, kind = winreg.QueryValueEx(key, "CombinePedals")
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False                     # not set: the driver's default, separate
    return kind == winreg.REG_DWORD and int(value) != 0


def combine_filters():
    """Logitech filter services installed here that read CombinePedals."""
    if winreg is None:
        return []
    found = []
    for name in COMBINE_FILTERS:
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _SERVICES + name, 0,
                                           winreg.KEY_READ | _reg64()))
            found.append(name)
        except OSError:
            pass
    return found


def write_combine_pedals(on, pids):
    """The write itself; needs administrator rights. Written for every filter
    that is installed, so whichever one sits on the wheel reads it."""
    filters = combine_filters()
    if not filters:
        raise OSError("no Logitech filter driver")
    for name in filters:
        for pid in pids:
            key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE,
                                     _SERVICES + name + "\\Parameters\\" + pid, 0,
                                     winreg.KEY_SET_VALUE | _reg64())
            try:
                winreg.SetValueEx(key, "CombinePedals", 0, winreg.REG_DWORD, 1 if on else 0)
            finally:
                winreg.CloseKey(key)


def _pnputil(*args):
    import subprocess
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "pnputil.exe")
    r = subprocess.run([exe, *args], capture_output=True, text=True, errors="replace",
                       timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, r.stdout or ""


def wheel_usb_instances(text, pids):
    """The wheel's USB device instances in pnputil's device list. The labels
    are in the Windows language; the instance IDs themselves are not."""
    import re
    want = {p.upper().rsplit("PID_", 1)[-1] for p in pids}
    found = []
    for m in re.finditer(r"USB\\VID_046D&PID_([0-9A-F]{4})\\[^\s]+", text, re.I):
        if m.group(1).upper() in want and m.group(0) not in found:
            found.append(m.group(0))
    return found


def restart_wheel_usb(pids):
    """Restart the wheel's USB device so that Logitech's driver starts again
    and reads its settings. Needs administrator rights and pnputil's
    /restart-device (Windows 10 2004 and later); False when it could not be
    done, and the wheel has to be plugged in again instead."""
    try:
        _, out = _pnputil("/enum-devices", "/connected")
    except Exception:
        return False
    done = False
    for inst in wheel_usb_instances(out, pids):
        try:
            rc, _ = _pnputil("/restart-device", inst)
        except Exception:
            continue
        done = done or rc in (0, 3010)
    return done


def combine_pedals_main(argv):
    """--combine-pedals 1|0 VID_046D&PID_xxxx[,...]: the copy started with
    administrator rights. Only the wheels' own registry names are accepted."""
    try:
        i = argv.index("--combine-pedals")
        on = argv[i + 1] == "1"
        pids = [p for p in argv[i + 2].split(",") if p in ALL_REGISTRY_PIDS]
    except (ValueError, IndexError):
        return COMBINE_FAILED
    if not pids or winreg is None:
        return COMBINE_FAILED
    try:
        write_combine_pedals(on, pids)
    except OSError:
        return COMBINE_FAILED
    done = restart_wheel_usb(pids)
    try:
        install_combine_tasks()              # so the next change asks nothing
    except Exception:
        pass
    return COMBINE_DONE if done else COMBINE_REPLUG


def run_elevated(args, timeout_ms=60000):
    """Start this program again with administrator rights and wait for it.
    Its exit code, or None when the prompt was declined or nothing started."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    if getattr(sys, "frozen", False):
        exe, params = sys.executable, args
    else:
        exe, params = sys.executable, '"%s" %s' % (os.path.abspath(sys.argv[0]), args)
    sei = SEI()
    sei.cbSize = ctypes.sizeof(SEI)
    sei.fMask = 0x00000040 | 0x00000100          # NOCLOSEPROCESS | NOASYNC
    sei.lpVerb, sei.lpFile, sei.lpParameters, sei.nShow = "runas", exe, params, 0
    shell32, k32 = ctypes.windll.shell32, ctypes.windll.kernel32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SEI)]
    if not shell32.ShellExecuteExW(ctypes.byref(sei)) or not sei.hProcess:
        return None
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        k32.WaitForSingleObject(sei.hProcess, timeout_ms)
        code = wintypes.DWORD(COMBINE_FAILED)
        k32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        k32.CloseHandle(sei.hProcess)


# Asked once, not every time. Two scheduled tasks run as SYSTEM and do only
# this - one writes CombinePedals = 1, the other 0, for every wheel under
# whichever Logitech filter is installed, then restarts the wheels that are
# plugged in - with nothing but Windows' own reg.exe and pnputil.exe, from a
# fixed command line. Every user may start them and nobody but an
# administrator may change them, so they hand out no more than this switch,
# and no file a user can write is ever run as SYSTEM (the portable copy and
# the source live in user folders). The installer sets them up; otherwise the
# first change does it, in the copy that already has the administrator's
# permission. Uninstalling removes them. Without them (declined, or a
# Windows that refuses), every change asks as before.
COMBINE_TASKS = {True: "Legacy Wheel Hub - Combined pedals on",
                 False: "Legacy Wheel Hub - Combined pedals off"}
# SYSTEM and Administrators: everything; signed-in users: read and run
COMBINE_TASK_SDDL = "D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;AU)"


def _sys32(name):
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", name)


def _quiet_run(args, timeout=30):
    import subprocess
    r = subprocess.run(args, capture_output=True, text=True, errors="replace",
                       timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def combine_task_script(on):
    """The PowerShell the task runs: fixed, built from the wheels' own
    registry names only."""
    pids = ",".join("'%s'" % p for p in ALL_REGISTRY_PIDS)
    usb = "|".join(sorted({p.rsplit("PID_", 1)[-1] for p in ALL_REGISTRY_PIDS}))
    filters = ",".join("'%s'" % f for f in COMBINE_FILTERS)
    return "\r\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "$sys = Join-Path $env:SystemRoot 'System32'",
        "$reg = Join-Path $sys 'reg.exe'",
        "$pnp = Join-Path $sys 'pnputil.exe'",
        "foreach ($f in @(%s)) {" % filters,
        "  $svc = 'HKLM\\SYSTEM\\CurrentControlSet\\Services\\' + $f",
        "  $key = 'Registry::HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\' + $f",
        "  if (-not (Test-Path -LiteralPath $key)) { continue }",
        "  foreach ($p in @(%s)) {" % pids,
        "    & $reg add ($svc + '\\Parameters\\' + $p) /v CombinePedals /t REG_DWORD /d %d /f /reg:64 | Out-Null"
        % (1 if on else 0),
        "  }",
        "}",
        "$ids = & $pnp /enum-devices /connected | Select-String -AllMatches -Pattern "
        "'USB\\\\VID_046D&PID_(%s)\\\\\\S+' | ForEach-Object { $_.Matches } | "
        "ForEach-Object { $_.Value } | Sort-Object -Unique" % usb,
        "foreach ($i in $ids) { & $pnp /restart-device $i | Out-Null }",
    ])


def combine_task_xml(on):
    import base64
    from xml.sax.saxutils import escape
    enc = base64.b64encode(combine_task_script(on).encode("utf-16-le")).decode("ascii")
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>Legacy Wheel Hub</Author>
    <Description>%s</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="System">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="System">
    <Exec>
      <Command>%%SystemRoot%%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe</Command>
      <Arguments>-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand %s</Arguments>
    </Exec>
  </Actions>
</Task>
""" % (escape("Logitech's \"Report Combined Pedals\" %s for the wheels, then restarts them "
              "(Legacy Wheel Hub's Pedals tab starts this)." % ("on" if on else "off")), enc)


def install_combine_tasks():
    """Register both tasks (needs administrator rights). True when both are
    there afterwards."""
    if sys.platform != "win32":
        return False
    import base64
    ps = _sys32(r"WindowsPowerShell\v1.0\powershell.exe")
    for on, name in COMBINE_TASKS.items():
        xml = combine_task_xml(on).replace("'", "''")
        # 6 = create or update, 5 = service account, with the task's own access rules
        cmd = ("$ErrorActionPreference = 'Stop'\r\n"
               "$svc = New-Object -ComObject Schedule.Service\r\n$svc.Connect()\r\n"
               "$null = $svc.GetFolder('\\').RegisterTask('%s', '%s', 6, 'SYSTEM', $null, 5, '%s')\r\n"
               % (name, xml, COMBINE_TASK_SDDL))
        enc = base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
        try:
            _quiet_run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-EncodedCommand", enc], timeout=60)
        except Exception:
            return False
    return all(combine_task_ready(on) for on in COMBINE_TASKS)


def remove_combine_tasks():
    for name in COMBINE_TASKS.values():
        try:
            _quiet_run([_sys32("schtasks.exe"), "/Delete", "/TN", "\\" + name, "/F"])
        except Exception:
            pass


def combine_task_ready(on):
    if sys.platform != "win32":
        return False
    try:
        rc, _ = _quiet_run([_sys32("schtasks.exe"), "/Query", "/TN", "\\" + COMBINE_TASKS[bool(on)]])
    except Exception:
        return False
    return rc == 0


def _combine_by_task(on, prof):
    """Through the task, with no prompt: COMBINE_DONE / COMBINE_REPLUG once
    the driver's value has changed, None when the task is not there or did
    not do it."""
    if not combine_task_ready(on):
        return None
    try:
        rc, _ = _quiet_run([_sys32("schtasks.exe"), "/Run", "/TN", "\\" + COMBINE_TASKS[bool(on)]])
    except Exception:
        return None
    if rc != 0:
        return None
    end = time.monotonic() + 20
    while time.monotonic() < end:
        if combine_pedals_state(prof) is bool(on):
            time.sleep(3)                    # the wheel restarts after the write
            try:
                new_enough = sys.getwindowsversion().build >= 19041
            except Exception:
                new_enough = True
            return COMBINE_DONE if new_enough else COMBINE_REPLUG
        time.sleep(0.25)
    return None


def combine_tasks_main(argv):
    """--combine-pedals-setup (the installer): register the tasks."""
    return 0 if install_combine_tasks() else 1


def request_combine_pedals(on, prof=None):
    """Turn the driver's combined pedals on or off for the connected wheel.
    COMBINE_DONE, COMBINE_REPLUG, COMBINE_FAILED, or None when Windows'
    permission prompt was declined. Blocks while the prompt is up. Through
    the scheduled task when it is there (no prompt), otherwise one prompt,
    and that copy sets the task up for next time."""
    pids = combine_pedals_pids(prof)
    if combine_pedals_state(prof) is None or not pids:
        return COMBINE_FAILED
    code = _combine_by_task(on, prof)
    if code is not None:
        return code
    code = run_elevated("--combine-pedals %d %s" % (1 if on else 0, ",".join(pids)))
    if code is None:
        return None
    return code if code in (COMBINE_DONE, COMBINE_REPLUG) else COMBINE_FAILED


# ---------------------------------------------------------------------------
#  Games that set the wheel themselves (LGS: "Allow Game To Adjust Settings")
# ---------------------------------------------------------------------------
# Games built with Logitech's Steering Wheel SDK look for hidden windows of
# Logitech Gaming Software by their class names and tell them what they want
# for the wheel - Assetto Corsa's "hardware lock" (the car's own steering lock
# as the wheel's rotation) goes this way, and so does every other game with
# the SDK, whichever version it carries. Without LGS nobody answers and the
# request is lost. LWH opens both windows itself and answers everything the
# SDK sends them (taken from Logitech's SDK 1.x source and its 8.81 DLL):
#
#   "Logitech Wingman Internal Message Router" (every SDK version):
#     WM_COPYDATA, dwData WM_USER+179 = set, WM_USER+178 = get. Set: size,
#     version, PID, which property, then ten ints - forceEnable, overallGain,
#     springGain, damperGain, defaultSpringEnabled, defaultSpringGain,
#     combinePedals, wheelRange, gameSettingsEnabled, allowGameSettings;
#     wParam is the game's window. Get is answered with the same ten values,
#     sent back to that window the same way. The SDK sends the set again
#     whenever the game comes to the front or a device is plugged in.
#     WM_USER+177 and WM_CLOSE: a game that had to start LGS itself closes it
#     again on exit - LWH stays.
#   "LCore_MessageHandler_{C464822E-...}" (SDK 8.x; LGS 8/9's LCore.exe):
#     the registered messages "LGS_Msg_SetOperatingRange" (wParam PID,
#     lParam degrees; nonzero answer = done) and "LGS_Msg_GetOperatingRange"
#     (wParam PID; the answer is the degrees).
#
#   LWH's own LogitechSteeringWheel.dll (sdk-bridge/) - for Content Manager,
#     which loads that DLL by name, and for games that find the SDK through
#     its registration (see sdk_registration) - speaks exactly the same.
#
# What LGS 5.10 does with a set (LWGStore.dll, the handler of WM_USER+179):
#   * it asks the profile of the game that sent it (by the sender window's
#     process): a profile with its own settings decides "Allow Game To Adjust
#     Settings", otherwise the global one does; not allowed -> nothing at all.
#   * allowed: "which" 0 applies all of force feedback on/off, overall,
#     spring and damper strength, combined pedals and the rotation; 1-6 just
#     the one. The centring spring (defaultSpring...) and the two
#     game-settings flags in the message are not taken.
#   * it keeps them while the game runs.
# LWH does the same, with one difference it cannot help: combined pedals take
# Windows' permission prompt and a restart of the wheel here (see
# combine_pedals_state), so a game asking for them is only noted.
#
# LGS also keeps its global values under
#     HKCU\Software\Logitech\Gaming Software\GlobalDeviceSettings\<wheel>
# and Content Manager reads them to send back with each car's rotation - so
# with LGS the force feedback did not change. LWH writes the values in force
# there too (publish_lgs_settings), for the same result.
LGS_ROUTER_CLASS = "Logitech Wingman Internal Message Router"
LGS_LCORE_CLASS = "LCore_MessageHandler_{C464822E-04D1-4447-B918-6D5EB33E0E5D}"
_WM_CLOSE = 0x0010
_WM_COPYDATA = 0x004A
_LGS_GET, _LGS_SET, _LGS_FASTEXIT = 0x0400 + 178, 0x0400 + 179, 0x0400 + 177
LGS_FIELDS = ("forceEnable", "overallGain", "springGain", "damperGain",
              "defaultSpringEnabled", "defaultSpringGain", "combinePedals",
              "wheelRange", "gameSettingsEnabled", "allowGameSettings")
# "which" in a set: 0 = all of them, otherwise one property
_LGS_WHICH = {1: "forceEnable", 2: "overallGain", 3: "springGain", 4: "damperGain",
              5: "combinePedals", 6: "wheelRange"}


def lgs_changes(which, vals):
    """A game's set as profile values, exactly what LGS takes from it (see
    above): ffb_on, di_gain, di_spring, di_damper, combine, angle."""
    v = dict(zip(LGS_FIELDS, vals))

    def clamp(x, lo, hi):
        return max(lo, min(hi, int(x)))
    take = {1: "forceEnable", 2: "overallGain", 3: "springGain", 4: "damperGain",
            5: "combinePedals", 6: "wheelRange"}
    names = list(take.values()) if which == 0 else [take[which]] if which in take else []
    out = {}
    for n in names:
        if n == "forceEnable":
            out["ffb_on"] = bool(v[n])
        elif n == "overallGain":
            out["di_gain"] = clamp(v[n], 0, 150)
        elif n == "springGain":
            out["di_spring"] = clamp(v[n], 0, 100)
        elif n == "damperGain":
            out["di_damper"] = clamp(v[n], 0, 100)
        elif n == "combinePedals":
            out["combine"] = bool(v[n])
        elif n == "wheelRange" and int(v[n]) > 0:
            out["angle"] = clamp(v[n], 40, 900)
    return out


# LGS's names for the wheels whose global settings Content Manager reads
LGS_DEVICE_KEYS = {"G27": "G27", "G25": "G25", "DFGT": "DrivingForceGT"}


def publish_lgs_settings(eff, allow=True, combined=False, prof=None):
    """The values in force where LGS kept its global settings, so a program
    that reads them (Content Manager's hardware lock) sends back what the
    wheel already has. Only for the wheels such programs know."""
    if winreg is None:
        return
    name = LGS_DEVICE_KEYS.get(device_key_for(prof or active_profile) or "")
    if not name:
        return
    vals = {"ForceFeedbackEnable": int(eff.get("ffb_on", True) is not False),
            "OverallGainPercentage": int(eff.get("di_gain", 100)),
            "SpringGainPercentage": int(eff.get("di_spring", 100)),
            "DamperGainPercentage": int(eff.get("di_damper", 100)),
            "PersistentSpringEnable": int(bool(eff.get("di_persist"))),
            "DefaultSpringGainPercentage": int(eff.get("di_center", 0)),
            "CombinedPedalsEnable": int(bool(combined)),
            "OperatingRange": int(eff.get("angle", 900)),
            "GameSettingsEnable": int(bool(allow)),
            "AllowGameSettings": int(bool(allow))}
    try:
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Logitech\Gaming Software\GlobalDeviceSettings" + "\\" + name)
        try:
            for n, x in vals.items():
                winreg.SetValueEx(k, n, 0, winreg.REG_DWORD, x)
        finally:
            winreg.CloseKey(k)
    except OSError:
        pass


def game_allowed(who, profiles, draft_name=None, draft_allow=None):
    """LGS's rule for "Allow Game To Adjust Settings": the profile of the game
    that asked (by its exe), otherwise the global one. draft_* is the profile
    being edited, whose unsaved switch counts."""
    name = exe_owner(profiles, who) if who else None
    name = name or "Global"
    if draft_name is not None and name == draft_name and draft_allow is not None:
        return bool(draft_allow)
    return bool((profiles.get(name) or {}).get("allow_game", True))


def lgs_props(eff, allow, combined=False, pid=0):
    """The ten values a game is told, from the values in force."""
    vals = [int(eff.get("ffb_on", True) is not False), int(eff.get("di_gain", 100)),
            int(eff.get("di_spring", 100)), int(eff.get("di_damper", 100)),
            int(bool(eff.get("di_persist"))), int(eff.get("di_center", 0)), int(bool(combined)),
            int(eff.get("angle", 900)), int(bool(allow)), int(bool(allow))]
    return struct.pack("<3I10i", 52, 2, pid & 0xFFFF, *vals)


class LgsRouter:
    """The window games look for. It lives on the thread that starts it - the
    interface thread, whose event loop already dispatches its messages - so
    the callbacks run there too. A game waits inside SendMessage while one
    runs: they only note the request and return."""

    def __init__(self):
        self.hwnd = None
        self.hwnd_lcore = None    # the second window, for SDK 8.x's range messages
        self._proc = None
        self.get_props = None     # pid -> bytes for a get (see lgs_props)
        self.get_range = None     # pid -> degrees in force
        self.on_set = None        # (changes or None, sender's exe path) -> None
        self.log = []             # the last requests, for the setup check

    def _note(self, text):
        self.log.append(time.strftime("%H:%M:%S ") + text)
        del self.log[:-20]

    def start(self, get_props, get_range, on_set):
        if sys.platform != "win32" or self.hwnd:
            return bool(self.hwnd)
        import ctypes
        from ctypes import wintypes
        user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        # every handle typed: 64-bit handles do not fit ctypes' default int
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        if user32.FindWindowW(LGS_ROUTER_CLASS, None) or user32.FindWindowW(LGS_LCORE_CLASS, None):
            return False             # Logitech's own software is running: it answers
        self.get_props, self.get_range, self.on_set = get_props, get_range, on_set
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = LRESULT
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        set_range = user32.RegisterWindowMessageW("LGS_Msg_SetOperatingRange")
        get_range_msg = user32.RegisterWindowMessageW("LGS_Msg_GetOperatingRange")

        class CDS(ctypes.Structure):
            _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p)]

        user32.GetForegroundWindow.restype = wintypes.HWND

        def exe_of(pid):
            h = k32.OpenProcess(0x1000, False, pid)
            if not h:
                return ""
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(32768)
            ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            k32.CloseHandle(h)
            return buf.value if ok else ""        # the full path: its exit can be watched

        def sender(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            return exe_of(pid.value)

        def front():
            """The game for a request that does not say who sent it: the
            program in front, unless that is LWH itself."""
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
            return exe_of(pid.value) if pid.value and pid.value != os.getpid() else ""

        def proc(hwnd, msg, wp, lp):
            try:
                if msg and msg == set_range:
                    # no sender window in this one: the game is the program in front
                    who = front()
                    self._note("range %d %s" % (lp, who.rsplit("\\", 1)[-1]))
                    if self.on_set and lp > 0:
                        self.on_set({"angle": max(40, min(900, int(lp)))}, who)
                    return 1
                if msg and msg == get_range_msg:
                    return int(self.get_range(wp & 0xFFFF)) if self.get_range else 0
                if msg == _WM_COPYDATA and lp:
                    cds = CDS.from_address(lp)
                    raw = ctypes.string_at(cds.lpData, cds.cbData) if cds.lpData and cds.cbData < 4096 else b""
                    if cds.dwData == _LGS_SET and len(raw) >= 56:
                        _, _, pid, which = struct.unpack_from("<4I", raw)
                        vals = struct.unpack_from("<10i", raw, 16)
                        who = sender(wp)
                        self._note("set %s %d %s" % (who.rsplit("\\", 1)[-1], which, list(vals)))
                        if self.on_set:
                            self.on_set(lgs_changes(which, vals), who)
                        return 1
                    if cds.dwData == _LGS_GET and len(raw) >= 12 and self.get_props:
                        _, _, pid = struct.unpack_from("<3I", raw)
                        self._note("get %s" % sender(wp).rsplit("\\", 1)[-1])
                        payload = self.get_props(pid)
                        buf = ctypes.create_string_buffer(payload, len(payload))
                        back = CDS(_LGS_GET, len(payload), ctypes.cast(buf, ctypes.c_void_p))
                        user32.SendMessageW(wp, _WM_COPYDATA, hwnd, ctypes.addressof(back))
                        return 1
                    return 0
                if msg in (_LGS_FASTEXIT, _WM_CLOSE):
                    # a game that had to start LGS asks it to quit: LWH stays
                    self._note("close request ignored")
                    return 0
            except Exception:
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        self._proc = WNDPROC(proc)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]
        k32.GetModuleHandleW.restype = wintypes.HMODULE
        hinst = k32.GetModuleHandleW(None)
        self._wc = [WNDCLASSW(0, self._proc, 0, 0, hinst, None, None, None, None, c)
                    for c in (LGS_ROUTER_CLASS, LGS_LCORE_CLASS)]
        for wc in self._wc:
            user32.RegisterClassW(ctypes.byref(wc))
        user32.CreateWindowExW.restype = wintypes.HWND
        # ordinary top-level windows that are never shown: games find them
        # with FindWindow, which does not see message-only windows
        self.hwnd = user32.CreateWindowExW(0, LGS_ROUTER_CLASS, "Legacy Wheel Hub", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        self.hwnd_lcore = user32.CreateWindowExW(0, LGS_LCORE_CLASS, "Legacy Wheel Hub", 0, 0, 0, 0, 0,
                                                 None, None, hinst, None)
        return bool(self.hwnd)

    def stop(self):
        if self.hwnd and sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            ctypes.windll.user32.DestroyWindow.argtypes = [wintypes.HWND]
            for h in (self.hwnd, self.hwnd_lcore):
                if h:
                    ctypes.windll.user32.DestroyWindow(h)
            # and the classes with them, so a later start registers its own
            ctypes.windll.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            ctypes.windll.user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
            for c in (LGS_ROUTER_CLASS, LGS_LCORE_CLASS):
                ctypes.windll.user32.UnregisterClassW(c, ctypes.windll.kernel32.GetModuleHandleW(None))
        self.hwnd = self.hwnd_lcore = None


lgs_router = LgsRouter()


# ---- LWH's LogitechSteeringWheel.dll, for Content Manager's hardware lock --
SDK_BRIDGE_DLL = "LogitechSteeringWheel.dll"
SDK_BRIDGE_MARK = b"LWH SDK bridge"


def sdk_bridge_source(arch="x86"):
    """The bundled bridge (assets/sdkbridge/<arch>). Content Manager is a
    32-bit program, so it takes the x86 one."""
    for base in (_resource_dir(), _exe_dir(), os.path.dirname(os.path.abspath(__file__))):
        f = os.path.join(base, "assets", "sdkbridge", arch, SDK_BRIDGE_DLL)
        if os.path.isfile(f):
            return f
    return ""


def is_our_bridge(path):
    try:
        with open(path, "rb") as f:
            return SDK_BRIDGE_MARK in f.read(1 << 20)
    except OSError:
        return False


def find_content_manager():
    """Content Manager's exe while it runs ("Content Manager.exe", or a
    renamed copy such as "Content Manager Safe.exe"), else the one used last."""
    for p in running_exe_paths():
        name = os.path.basename(p)
        if name.endswith(".exe") and "content manager" in name:
            return p
    last = (global_settings.get("sdk_bridge") or [""])[-1]
    return last if last and os.path.isfile(last) else ""


def cm_bridge_state(cm_exe):
    """"ours", "other" (some other LogitechSteeringWheel.dll is there - LGS's
    own, say), or "none"."""
    if not cm_exe:
        return "none"
    f = os.path.join(os.path.dirname(cm_exe), SDK_BRIDGE_DLL)
    if not os.path.isfile(f):
        return "none"
    return "ours" if is_our_bridge(f) else "other"


CM_KEPT_SUFFIX = ".lwh-kept"          # a DLL that was there before LWH's, kept aside


def install_cm_bridge(cm_exe, take_over=False):
    """Put the bridge next to Content Manager. "ok", "other" (a DLL that is
    not ours is there and is left alone - unless take_over: then it is kept
    aside and comes back when ours is removed) or "error"."""
    import shutil
    src = sdk_bridge_source("x86")
    if not cm_exe or not src:
        return "error"
    state = cm_bridge_state(cm_exe)
    dst = os.path.join(os.path.dirname(cm_exe), SDK_BRIDGE_DLL)
    if state == "other":
        if not take_over:
            return "other"
        try:
            os.replace(dst, dst + CM_KEPT_SUFFIX)
        except OSError:
            return "error"
    try:
        tmp = dst + ".lwh-tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except OSError:
        return "error"
    known = [p for p in (global_settings.get("sdk_bridge") or []) if p.lower() != cm_exe.lower()]
    global_settings["sdk_bridge"] = known + [cm_exe]
    save_settings()
    return "ok"


def remove_cm_bridge(cm_exe):
    """Take the bridge out again - only ours; a DLL it replaced comes back."""
    if cm_bridge_state(cm_exe) != "ours":
        return False
    dst = os.path.join(os.path.dirname(cm_exe), SDK_BRIDGE_DLL)
    try:
        os.remove(dst)
        if os.path.isfile(dst + CM_KEPT_SUFFIX):
            os.replace(dst + CM_KEPT_SUFFIX, dst)
    except OSError:
        return False
    global_settings["sdk_bridge"] = [p for p in (global_settings.get("sdk_bridge") or [])
                                     if p.lower() != cm_exe.lower()]
    save_settings()
    return True


def refresh_cm_bridges():
    """A newer LWH brings a newer bridge: replace ours in the Content Manager
    folders it was put in (never a DLL that is not ours)."""
    src = sdk_bridge_source("x86")
    if not src:
        return 0
    try:
        with open(src, "rb") as f:
            new = f.read()
    except OSError:
        return 0
    done = 0
    for cm in list(global_settings.get("sdk_bridge") or []):
        dst = os.path.join(os.path.dirname(cm), SDK_BRIDGE_DLL)
        try:
            if cm_bridge_state(cm) != "ours":
                continue
            with open(dst, "rb") as f:
                if f.read() == new:
                    continue
            tmp = dst + ".lwh-tmp"
            with open(tmp, "wb") as f:
                f.write(new)
            os.replace(tmp, dst)
            done += 1
        except OSError:
            pass
    return done


# ---- the Steering Wheel SDK's registration (games with Logitech's plugin) --
# Two values LGS 8/9 (or G HUB) leave in HKLM, both read by games, both in
# the 64-bit view for 64-bit games and WOW6432Node for 32-bit ones:
#
#   SOFTWARE\Classes\CLSID\{63BD165D-1584-4E75-AB56-08330350545F}\ServerBinary
#     (default value) - the SDK's DLL. Unity and Unreal games use Logitech's
#     SDK through a small DLL of their own (LogitechSteeringWheelEnginesWrapper,
#     Logitech's plugin for those engines) that carries none of the SDK: it
#     loads this file and calls it for everything - the wheel, the forces,
#     the lights, the settings. Without it the SDK in such a game stays off
#     and the wheel gets no force feedback from it at all.
#   Software\Logitech\Gaming Software, value "Profiler" - LGS's program. The
#     SDK (1.x and 8.x, built into a game) reads its file version: without a
#     file of version 5.03 or later, SDK 8.x sends no settings at all and 1.x
#     refuses them. And when LGS's window is not there, the SDK starts it
#     ("/noui /force") so the game's wishes have someone to go to.
#
# LWH puts its own there: the bridge (both sizes) and LWHGameLink.exe (version
# 5.10, the LGS LWH follows; started, it brings LWH up quietly in the tray).
# Where Logitech's own software is registered it is left alone, unless the
# user asks LWH to take over - then Logitech's values are kept under
# HKLM\SOFTWARE\LegacyWheelHub and put back when LWH's are removed.
SDK_SERVER_KEY = r"SOFTWARE\Classes\CLSID\{63BD165D-1584-4E75-AB56-08330350545F}\ServerBinary"
LGS_SW_KEY = r"Software\Logitech\Gaming Software"
LWH_HKLM_KEY = r"SOFTWARE\LegacyWheelHub"
SDK_LINK_EXE = "LWHGameLink.exe"


def _sdk_views():
    """(registry view, bridge arch) for each kind of program this Windows runs."""
    if winreg is None:
        return []
    import platform
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432") or platform.machine() or "").lower()
    if arch.endswith("64"):
        return [(winreg.KEY_WOW64_64KEY, "x64"), (winreg.KEY_WOW64_32KEY, "x86")]
    return [(0, "x86")]


def _reg_get(view, key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | view) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v or "")
    except OSError:
        return ""


def _reg_set(view, key, name, value):
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_SET_VALUE | view) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _reg_del(view, key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_SET_VALUE | view) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def _sdk_server(view):
    return _reg_get(view, SDK_SERVER_KEY, None)


def sdk_link_source():
    """The bundled LWHGameLink.exe."""
    src = sdk_bridge_source("x86")
    if not src:
        return ""
    f = os.path.join(os.path.dirname(os.path.dirname(src)), SDK_LINK_EXE)
    return f if os.path.isfile(f) else ""


def _sdk_is_ours(path):
    """Our bridge or our LWHGameLink - or where one of them was (a moved
    portable copy)."""
    if not path:
        return False
    p = path.replace("/", "\\").lower()
    if p.endswith("\\" + SDK_LINK_EXE.lower()):
        return True
    if os.path.isfile(path):
        return is_our_bridge(path)
    return "\\sdkbridge\\" in p and p.endswith("\\" + SDK_BRIDGE_DLL.lower())


def _sdk_slots():
    """Every value LWH looks after: (view, arch, key, value name, backup name, our file)."""
    out = []
    for view, arch in _sdk_views():
        out.append((view, arch, SDK_SERVER_KEY, None, "PrevServerBinary", sdk_bridge_source(arch)))
        out.append((view, arch, LGS_SW_KEY, "Profiler", "PrevProfiler", sdk_link_source()))
    return out


def sdk_registration_state():
    """"ours" (games find LWH), "other" (Logitech's own software is
    registered - LWH can take over) or "none"."""
    states = []
    for view, _a, key, name, _b, _src in _sdk_slots():
        cur = _reg_get(view, key, name)
        if cur and os.path.isfile(cur):
            states.append("ours" if _sdk_is_ours(cur) else "other")
        else:
            states.append("none")
    if "other" in states:
        return "other"
    return "ours" if states and all(s == "ours" for s in states) else "none"


def write_sdk_registration(on, take_over=False):
    """Register (on) or take out (off) LWH's bridge and game link. Needs
    administrator rights. Logitech's own values stay unless take_over, and
    then come back when LWH's are taken out. True when all went as asked."""
    ok = True
    for view, _arch, key, name, backup, src in _sdk_slots():
        cur = _reg_get(view, key, name)
        mine = _sdk_is_ours(cur)
        theirs = bool(cur) and os.path.isfile(cur) and not mine
        try:
            if on:
                if theirs and not take_over:
                    continue
                if not src:
                    ok = False
                    continue
                if theirs:
                    _reg_set(view, LWH_HKLM_KEY, backup, cur)
                _reg_set(view, key, name, os.path.abspath(src))
            elif mine:
                prev = _reg_get(view, LWH_HKLM_KEY, backup)
                if prev and os.path.isfile(prev):
                    _reg_set(view, key, name, prev)          # Logitech's, back
                elif name is None:
                    winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, key, view, 0)
                else:
                    _reg_del(view, key, name)
                _reg_del(view, LWH_HKLM_KEY, backup)
        except OSError:
            ok = False
    return ok


def sdk_register_main(argv):
    """--sdk-register 1|0 [--take-over]: the copy with administrator rights
    (the installer runs it too; it keeps a "no" the user gave)."""
    try:
        on = argv[argv.index("--sdk-register") + 1] == "1"
    except (ValueError, IndexError):
        return 2
    if on and global_settings.get("sdk_registration") == "off" and "--force" not in argv:
        return 0
    return 0 if write_sdk_registration(on, "--take-over" in argv) else 1


def request_sdk_registration(on, take_over=False):
    """From the app: remember the choice, then Windows asks for permission.
    True when done, None when the prompt was declined."""
    global_settings["sdk_registration"] = "on" if on else "off"
    save_settings()
    code = run_elevated("--sdk-register %d --force%s" % (1 if on else 0, " --take-over" if take_over else ""))
    if code is None:
        return None
    return code == 0


def note_app_command():
    """Where this copy of LWH is, for LWHGameLink to start it when a game
    asks for LGS before LWH runs."""
    if winreg is None:
        return
    if getattr(sys, "frozen", False):
        cmd = '"%s"' % sys.executable
    else:
        py = sys.executable
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        cmd = '"%s" "%s"' % (pyw if os.path.isfile(pyw) else py, os.path.abspath(sys.argv[0]))
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\LegacyWheelHub") as k:
            winreg.SetValueEx(k, "AppCommand", 0, winreg.REG_SZ, cmd)
    except OSError:
        pass


# ---------------------------------------------------------------------------
#  Wheel buttons as keyboard keys and macros (LGS: "Assign Command")
# ---------------------------------------------------------------------------
# A profile's "keymap" maps a button (the names the Inputs page shows) to
#   {"keys": ["ctrl", "shift", "x"]}           held while the button is held
#   {"macro": [["down", "a"], ["wait", 40], ["up", "a"], ...], "repeat": False}
#                                              played once per press, or over
#                                              and over while it is held
# Keys go out with SendInput as scan codes, which DirectInput games read too.
# Nothing is sent while LWH itself is in front, so trying a button on the
# Inputs page does not type into the app.
KEY_VK = {"ctrl": 0xA2, "rctrl": 0xA3, "shift": 0xA0, "rshift": 0xA1, "alt": 0xA4, "altgr": 0xA5,
          "win": 0x5B, "enter": 0x0D, "esc": 0x1B, "space": 0x20, "tab": 0x09, "backspace": 0x08,
          "capslock": 0x14, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24,
          "end": 0x23, "pgup": 0x21, "pgdn": 0x22, "ins": 0x2D, "del": 0x2E, "printscreen": 0x2C,
          "scrolllock": 0x91, "pause": 0x13, "numlock": 0x90, "num*": 0x6A, "num+": 0x6B,
          "num-": 0x6D, "num.": 0x6E, "num/": 0x6F, "numenter": 0x0D,
          "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE,
          ",": 0xBC, ".": 0xBE, "/": 0xBF, "`": 0xC0, "<": 0xE2}
KEY_VK.update({chr(c).lower(): c for c in range(0x41, 0x5B)})
KEY_VK.update({str(d): 0x30 + d for d in range(10)})
KEY_VK.update({"f%d" % i: 0x6F + i for i in range(1, 25)})
KEY_VK.update({"num%d" % d: 0x60 + d for d in range(10)})
_KEY_EXT = {"rctrl", "altgr", "win", "up", "down", "left", "right", "home", "end", "pgup", "pgdn",
            "ins", "del", "printscreen", "num/", "numenter", "numlock"}
_VK_NAME = {}
for _n, _vk in KEY_VK.items():
    if _n != "numenter":
        _VK_NAME.setdefault(_vk, _n)
_VK_NAME.update({0x11: "ctrl", 0x10: "shift", 0x12: "alt"})
_MODS = ("ctrl", "rctrl", "shift", "rshift", "alt", "altgr", "win")
# Qt key codes, for where no native scan code is given (tests, other systems)
_QT_KEY = {0x01000021: "ctrl", 0x01000020: "shift", 0x01000023: "alt", 0x01001103: "altgr",
           0x01000022: "win", 0x01000004: "enter", 0x01000005: "numenter", 0x01000000: "esc",
           0x20: "space", 0x01000001: "tab", 0x01000003: "backspace", 0x01000024: "capslock",
           0x01000013: "up", 0x01000015: "down", 0x01000012: "left", 0x01000014: "right",
           0x01000010: "home", 0x01000011: "end", 0x01000016: "pgup", 0x01000017: "pgdn",
           0x01000006: "ins", 0x01000007: "del", 0x01000009: "printscreen", 0x01000026: "scrolllock",
           0x01000008: "pause", 0x01000025: "numlock", 0x2D: "-", 0x3D: "=", 0x5B: "[", 0x5D: "]",
           0x5C: "\\", 0x3B: ";", 0x27: "'", 0x2C: ",", 0x2E: ".", 0x2F: "/", 0x60: "`", 0x3C: "<"}
_QT_KEY.update({0x41 + i: chr(0x61 + i) for i in range(26)})
_QT_KEY.update({0x30 + d: str(d) for d in range(10)})
_QT_KEY.update({0x01000030 + i: "f%d" % (i + 1) for i in range(24)})
MACRO_MAX_STEPS = 200
MACRO_MAX_WAIT = 10000


def key_from_event(qt_key, native_scan=0):
    """The key name for a key press in the interface: from the scan code on
    Windows (left and right Ctrl, the keypad, and keys whose letter depends
    on the keyboard layout come out right), otherwise from Qt's key code."""
    if native_scan and sys.platform == "win32":
        try:
            import ctypes
            scan = int(native_scan)
            if scan & 0x100:
                scan = 0xE000 | (scan & 0xFF)
            vk = ctypes.windll.user32.MapVirtualKeyW(scan, 3)       # MAPVK_VSC_TO_VK_EX
            if vk == 0x0D and native_scan & 0x100:
                return "numenter"
            if vk in _VK_NAME:
                return _VK_NAME[vk]
        except Exception:
            pass
    return _QT_KEY.get(int(qt_key))


def key_label(name):
    """How a key is written on screen."""
    special = {"ctrl": "Ctrl", "rctrl": "Right Ctrl", "shift": "Shift", "rshift": "Right Shift",
               "alt": "Alt", "altgr": "AltGr", "win": "Win", "enter": "Enter", "numenter": "Num Enter",
               "esc": "Esc", "space": "Space", "tab": "Tab", "backspace": "Backspace",
               "capslock": "Caps Lock", "up": "↑", "down": "↓", "left": "←", "right": "→",
               "home": "Home", "end": "End", "pgup": "Page Up", "pgdn": "Page Down", "ins": "Insert",
               "del": "Delete", "printscreen": "Print Screen", "scrolllock": "Scroll Lock",
               "pause": "Pause", "numlock": "Num Lock"}
    if name in special:
        return special[name]
    if name.startswith("num") and len(name) > 3:
        return "Num " + name[3:]
    return name.upper()


def keys_label(keys):
    return " + ".join(key_label(k) for k in keys)


def clean_keymap(m):
    """A keymap as stored: unknown keys, empty entries and over-long macros
    are dropped, so a hand-edited file can never make the mapper misbehave."""
    out = {}
    if not isinstance(m, dict):
        return out
    for button, act in m.items():
        if not isinstance(button, str) or not isinstance(act, dict):
            continue
        if isinstance(act.get("keys"), list):
            keys = [k for k in act["keys"] if k in KEY_VK][:6]
            if keys:
                out[button] = {"keys": keys}
        elif isinstance(act.get("macro"), list):
            steps = []
            for st in act["macro"][:MACRO_MAX_STEPS]:
                if not isinstance(st, (list, tuple)) or len(st) != 2:
                    continue
                kind, val = st
                if kind in ("down", "up", "tap") and val in KEY_VK:
                    steps.append([kind, val])
                elif kind == "wait" and isinstance(val, (int, float)):
                    steps.append(["wait", max(0, min(MACRO_MAX_WAIT, int(val)))])
            if any(s[0] != "wait" for s in steps):
                out[button] = {"macro": steps, "repeat": bool(act.get("repeat"))}
    return out


class _KeyOut:
    """SendInput, one key at a time, as a scan code."""

    def __init__(self):
        self.ok = sys.platform == "win32"
        self.sent = []            # (name, down) - what the tests look at
        if not self.ok:
            return
        import ctypes
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]
        self._INPUT, self._KEYBDINPUT = INPUT, KEYBDINPUT
        self._u32 = ctypes.windll.user32
        self._u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self._u32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self._ct = ctypes

    def send(self, name, down):
        self.sent.append((name, down))
        del self.sent[:-200]
        vk = KEY_VK.get(name)
        if not self.ok or vk is None:
            return
        scan = self._u32.MapVirtualKeyW(vk, 0)                       # MAPVK_VK_TO_VSC
        flags = 0 if down else 0x0002                                 # KEYEVENTF_KEYUP
        if name in _KEY_EXT:
            flags |= 0x0001                                           # EXTENDEDKEY
        if scan:
            ki = self._KEYBDINPUT(0, scan, flags | 0x0008, 0, 0)      # SCANCODE
        else:
            ki = self._KEYBDINPUT(vk, 0, flags, 0, 0)
        inp = self._INPUT(1)
        inp.u.ki = ki
        self._u32.SendInput(1, self._ct.byref(inp), self._ct.sizeof(self._INPUT))


def lwh_in_front():
    """True when one of this program's windows has the keyboard."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        h = u32.GetForegroundWindow()
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return False


class KeyMapper:
    """Fed the pressed buttons from the input thread; presses keys for the
    buttons that have them. A macro plays on a thread of its own."""

    def __init__(self, out=None):
        self.out = out or _KeyOut()
        self.map = {}
        self.prev = set()
        self.held = {}           # button -> keys it is holding down
        self.stops = {}          # button -> threading.Event of a repeating macro
        self.paused = False
        self.front = lwh_in_front
        self.lock = threading.Lock()

    def active(self):
        return bool(self.map) or bool(self.held)

    def set_map(self, m):
        m = clean_keymap(m)
        with self.lock:
            self._release_all()
            self.map = m
            self.prev = set()

    def _release_all(self):
        for keys in self.held.values():
            for k in reversed(keys):
                self.out.send(k, False)
        self.held = {}
        for ev in self.stops.values():
            ev.set()
        self.stops = {}

    def feed(self, pressed):
        with self.lock:
            new, gone = pressed - self.prev, self.prev - pressed
            self.prev = set(pressed)
            for b in gone:
                keys = self.held.pop(b, None)
                if keys:
                    for k in reversed(keys):
                        self.out.send(k, False)
                ev = self.stops.pop(b, None)
                if ev is not None:
                    ev.set()
            if not new or self.paused or self.front():
                return
            for b in sorted(new):
                act = self.map.get(b)
                if not act:
                    continue
                if "keys" in act:
                    for k in act["keys"]:
                        self.out.send(k, True)
                    self.held[b] = list(act["keys"])
                elif "macro" in act:
                    stop = threading.Event()
                    if act.get("repeat"):
                        self.stops[b] = stop
                    threading.Thread(target=self._play, args=(act["macro"], act.get("repeat"), stop),
                                     daemon=True).start()

    def _play(self, steps, repeat, stop):
        down = []
        try:
            while True:
                for kind, val in steps:
                    if kind == "wait":
                        if stop.wait(val / 1000.0) and repeat:
                            return
                    elif kind == "tap":
                        self.out.send(val, True)
                        self.out.send(val, False)
                    elif kind == "down":
                        self.out.send(val, True)
                        down.append(val)
                    else:
                        self.out.send(val, False)
                        if val in down:
                            down.remove(val)
                if not repeat or stop.is_set():
                    return
                stop.wait(0.01)
                if stop.is_set():
                    return
        finally:
            for k in reversed(down):             # never leave a key pressed
                self.out.send(k, False)


keymapper = KeyMapper()
_HAT_DIRS = {0: ("up",), 1: ("up", "right"), 2: ("right",), 3: ("down", "right"),
             4: ("down",), 5: ("down", "left"), 6: ("left",), 7: ("up", "left")}
_G27_ONLY_KEYS = {"wheel_lm", "wheel_rm", "wheel_lb", "wheel_rb"}


def pressed_names(prof, raw):
    """The buttons held in a report, by the names the Inputs page uses."""
    if not raw or not any(raw):
        return set()
    doc = documented_buttons(prof)
    if doc:
        p = {name for byte, bit, name in doc if byte < len(raw) and raw[byte] & (1 << bit)}
        h = documented_hat(prof, raw) if prof.get("hat") else None
        if h is not None:
            p |= {"dpad_" + d for d in _HAT_DIRS.get(h, ())}
        return p
    if prof is DEVICE_PROFILES.get("G25") and not learned_buttons(prof):
        return decode_buttons_g27(raw) - _G27_ONLY_KEYS
    if layout_unknown(prof):
        return {str(n) for n in decode_learned(raw, prof)}
    if prof is DEVICE_PROFILES.get("G27"):
        return decode_buttons_g27(raw)
    return decode_buttons_dfgt(raw)


def keymap_tick(prof, raw):
    """From the input thread, after each report."""
    if keymapper.active():
        try:
            keymapper.feed(pressed_names(prof, raw))
        except Exception:
            pass


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
        # False = friendly names (L1, START, the PlayStation shapes), True =
        # the HID button numbers games actually report ("Button 20"). Two
        # different vocabularies for the same hardware. Remembered globally
        # (not per profile): whichever vocabulary the user thinks in, they
        # think in it for every wheel.
        self.numbered = bool(global_settings.get("button_numbers", False))

    def _num(self, key):
        """HID button number for `key` on the active wheel, or None."""
        if g25_drawn() and key in G27_ONLY:
            return None
        prof = "G27" if active_profile is DEVICE_PROFILES["G27"] or g25_drawn() else "DFGT"
        return HID_BUTTON_NUMBERS.get(prof, {}).get(key)

    def _lbl(self, key, default):
        """Label to draw: the HID number in numbered mode, else the name."""
        if self.numbered:
            n = self._num(key)
            if n is not None:
                return str(n)
        return default

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
        # In numbered mode the shape IS the label, so draw the number instead
        # of the glyph - otherwise these four would be the only buttons left
        # unnumbered, which is exactly the confusion this mode exists to fix.
        for key, shape, bx, by in (("sh_triangle", "triangle", cx - h, cy - sp - h),
                                   ("sh_square", "square", cx - sp - h, cy - h),
                                   ("sh_circle", "circle", cx + sp - h, cy - h),
                                   ("sh_x", "x", cx - h, cy + sp - h)):
            if self.numbered:
                self._key(p, bx, by, d, d, self._lbl(key, ""), key in P, circle=True)
            else:
                self._key(p, bx, by, d, d, "", key in P, circle=True, shape=shape, col=mc)

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

    def _gt_center(self, p, cx, cy, r, on=False, label=None):
        ring = theme_col("#11151f", "#dfe3ea"); inner = theme_col("#05070b", "#10131a")
        edge = theme_col("#2a3142", "#c2c7d2")
        p.setPen(QPen(edge, 2)); p.setBrush(ring)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(Qt.NoPen); p.setBrush(inner)
        p.drawEllipse(QPointF(cx, cy), r - 6, r - 6)
        if on:                                  # the GT badge IS the horn button
            p.setBrush(QColor(ACCENT)); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), r - 6, r - 6)
        f = QFont(); f.setBold(True); f.setItalic(not label); f.setPointSize(
            max(1, int(r * (0.5 if label else 0.62))))
        p.setFont(f); p.setPen(QColor("#1c1c1c") if on else QColor("#f3f4f6"))
        p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, label or "GT")

    def _dial(self, p, cx, cy, r, on=False, label="\u21B5", side_labels=None):
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
        p.drawText(QRectF(cx - cr, cy - cr, 2 * cr, 2 * cr), Qt.AlignCenter, label)
        p.setBrush(red); p.setPen(Qt.NoPen); a = 6
        lx = cx - r - 8; rx = cx + r + 8
        p.drawPolygon(QPolygonF([QPointF(lx + a, cy - a), QPointF(lx - a, cy), QPointF(lx + a, cy + a)]))
        p.drawPolygon(QPolygonF([QPointF(rx - a, cy - a), QPointF(rx + a, cy), QPointF(rx - a, cy + a)]))
        # Turning the dial reports two more buttons (left/right steps). They
        # have no key of their own to draw, so in numbered mode the numbers go
        # beside the arrows - otherwise those two would be the only inputs the
        # overlay can't account for.
        if side_labels:
            lft, rgt = side_labels
            f2 = QFont(); f2.setBold(True); f2.setPointSize(8); p.setFont(f2)
            p.setPen(theme_col("#e6e8ee", "#2a2f3a"))
            p.drawText(QRectF(lx - 34, cy - 8, 22, 16), Qt.AlignRight | Qt.AlignVCenter, lft)
            p.drawText(QRectF(rx + 12, cy - 8, 22, 16), Qt.AlignLeft | Qt.AlignVCenter, rgt)

    def _paint_g27(self, p, P, chan, g25=False):
        # The G25 is the same drawing with one button on each side of the
        # wheel (L1/R1): the G27 added the two below them.
        D = 38
        self._section(p, 20, 8, tr("input.wheel"))
        self._pill(p, 52, 30, 176, 26, self._lbl("paddle_left", tr("input.lpad")), "paddle_left" in P)
        self._pill(p, 332, 30, 176, 26, self._lbl("paddle_right", tr("input.rpad")), "paddle_right" in P)
        lcx, rcx = 137, 423
        rows = 1 if g25 else 3
        for i, k in enumerate(("wheel_lt", "wheel_lm", "wheel_lb")[:rows]):
            self._key(p, lcx - D / 2, 72 + i * 46, D, D, self._lbl(k, ["L1", "L2", "L3"][i]), k in P, circle=True)
        for i, k in enumerate(("wheel_rt", "wheel_rm", "wheel_rb")[:rows]):
            self._key(p, rcx - D / 2, 72 + i * 46, D, D, self._lbl(k, ["R1", "R2", "R3"][i]), k in P, circle=True)
        p.setPen(QPen(chan, 1)); p.drawLine(QPointF(30, 226), QPointF(530, 226))
        self._section(p, 20, 240, tr("input.shifter"))
        # face + d-pad clusters centred symmetrically over the 1-2-3-4 row
        # (row centre = 164), pushed down to clear the section label.
        self._face_buttons(p, 92, 326, P, d=36, sp=36)
        self._dpad(p, 236, 326, P, s=34, g=6)
        for i in range(4):
            cxr = 80 + i * 56
            self._key(p, cxr - D / 2, 416, D, D, self._lbl(f"red_{i+1}", str(i + 1)), f"red_{i+1}" in P, circle=True)
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
            self._key(p, x - D / 2, gt_c - D / 2, D, D, self._lbl(f"gear_{tops[i]}", tops[i]), f"gear_{tops[i]}" in P, circle=True)
            self._key(p, x - D / 2, gb_c - D / 2, D, D, self._lbl(f"gear_{bots[i]}", bots[i]), f"gear_{bots[i]}" in P, circle=True)
        self._key(p, rx - D / 2, gb_c - D / 2, D, D, self._lbl("gear_r", "R"), "gear_r" in P, circle=True)

    def _paint_dfgt(self, p, P, chan):
        self._pill(p, 40, 14, 152, 24, self._lbl("paddle_left", tr("input.lpad")), "paddle_left" in P)
        self._pill(p, 368, 14, 152, 24, self._lbl("paddle_right", tr("input.rpad")), "paddle_right" in P)
        self._pill(p, 92, 58, 60, 26, self._lbl("l2", "L2"), "l2" in P)
        self._pill(p, 408, 58, 60, 26, self._lbl("r2", "R2"), "r2" in P)
        self._pill(p, 166, 96, 50, 28, self._lbl("l3", "L3"), "l3" in P)
        self._key(p, 340, 94, 30, 30, self._lbl("r3", "R3"), "r3" in P, circle=True)
        self._dpad(p, 113, 166, P)
        self._gt_center(p, 270, 165, 48, "horn" in P,
                        self._lbl("horn", "") if self.numbered else None)
        self._face_buttons(p, 425, 166, P, d=28, sp=31)
        self._pill(p, 504, 148, 46, 26, self._lbl("dn", "DN"), "dn" in P)
        self._pill(p, 504, 186, 46, 26, self._lbl("up", "UP"), "up" in P)
        self._key(p, 98, 238, 30, 30, self._lbl("plus", "+"), "plus" in P, circle=True)
        self._key(p, 98, 284, 30, 30, self._lbl("minus", "\u2212"), "minus" in P, circle=True)
        self._pill(p, 248, 238, 44, 28, self._lbl("ps", "PS"), "ps" in P)
        self._pill(p, 196, 288, 66, 26, self._lbl("select", "SELECT"), "select" in P)
        self._pill(p, 284, 288, 60, 26, self._lbl("start", "START"), "start" in P)
        self._dial(p, 426, 284, 38, "dial_enter" in P,
                   label=self._lbl("dial_enter", "\u21B5"),
                   side_labels=((self._lbl("dial_left", ""), self._lbl("dial_right", ""))
                                if self.numbered else None))

    def _paint_grid(self, p, mapped):
        """A numbered grid for a wheel whose buttons were learned here.

        Where each button physically sits is not something any measurement can
        recover, so there is no picture to draw. What the wizard does know is
        which bit belongs to which press, and in what order - which is enough
        to light up the right cell. Less pretty than the drawn layouts, and
        actually useful, which the "not mapped" notice was not.
        """
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen); p.setBrush(theme_col("#232323", "#ededed"))
        p.drawRoundedRect(QRectF(24, 24, w - 48, h - 48), 12, 12)

        f = QFont(); f.setPointSize(12); f.setBold(True); p.setFont(f)
        p.setPen(theme_col("#d8d8d8", "#333333"))
        p.drawText(QRectF(40, 40, w - 80, 22), Qt.AlignHCenter,
                   active_profile['name'] + (f"  \u2013  {tr('dev.beta')}" if is_beta() else ""))

        f.setPointSize(9); f.setBold(False); p.setFont(f)
        p.setPen(theme_col("#9a9a9a", "#666666"))
        p.drawText(QRectF(48, 66, w - 96, 30), Qt.AlignHCenter | Qt.TextWordWrap,
                   tr("input.grid_notice_desc") if descriptor_buttons_ready(active_profile)
                   else tr("input.grid_notice"))

        n = len(mapped)
        cols = min(8, max(4, int(n ** 0.5) + 1))
        cw, ch = min(58, (w - 110) // cols), 40
        x0 = (w - cols * cw) // 2
        y0 = 108
        down = decode_learned(state.get("raw") or [], active_profile)
        f.setPointSize(10); f.setBold(True); p.setFont(f)
        for i in range(n):
            r, c = divmod(i, cols)
            x, y = x0 + c * cw, y0 + r * ch
            on = (i + 1) in down
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT) if on else theme_col("#2b2f38", "#d8dade"))
            p.drawRoundedRect(QRectF(x + 3, y + 3, cw - 6, ch - 8), 6, 6)
            p.setPen(QColor("#ffffff") if on else theme_col("#8a93a6", "#767b85"))
            p.drawText(QRectF(x + 3, y + 3, cw - 6, ch - 8), Qt.AlignCenter,
                       str(i + 1))

    def _paint_named_grid(self, p, cells):
        """The buttons of a wheel whose layout is documented, by name.

        No drawn picture for these wheels - where a button sits on the rim is
        not in any of the sources - but every button's name and bit are, so
        each cell lights up under its own name rather than a number."""
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen); p.setBrush(theme_col("#232323", "#ededed"))
        p.drawRoundedRect(QRectF(24, 24, w - 48, h - 48), 12, 12)

        f = QFont(); f.setPointSize(12); f.setBold(True); p.setFont(f)
        p.setPen(theme_col("#d8d8d8", "#333333"))
        p.drawText(QRectF(40, 40, w - 80, 22), Qt.AlignHCenter, active_profile["name"])

        f.setPointSize(9); f.setBold(False); p.setFont(f)
        p.setPen(theme_col("#9a9a9a", "#666666"))
        p.drawText(QRectF(48, 66, w - 96, 30), Qt.AlignHCenter | Qt.TextWordWrap,
                   tr("input.doc_notice"))

        raw = state.get("raw") or []
        hat_prof = active_profile.get("hat")
        n = len(cells) + (1 if hat_prof else 0)
        cols = min(7, max(4, int(n ** 0.5) + 2))
        cw, ch = min(96, (w - 110) // cols), 42
        x0 = (w - cols * cw) // 2
        y0 = 108
        f.setPointSize(9); f.setBold(True); p.setFont(f)
        items = [(name, byte < len(raw) and bool(raw[byte] & (1 << bit)))
                 for byte, bit, name in cells]
        if hat_prof:
            d = documented_hat(active_profile, raw)
            arrows = "\u2191\u2197\u2192\u2198\u2193\u2199\u2190\u2196"
            items.append((arrows[d] if d is not None else "D-pad", d is not None))
        for i, (label, on) in enumerate(items):
            r, c = divmod(i, cols)
            x, y = x0 + c * cw, y0 + r * ch
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT) if on else theme_col("#2b2f38", "#d8dade"))
            p.drawRoundedRect(QRectF(x + 3, y + 3, cw - 6, ch - 8), 6, 6)
            p.setPen(QColor("#ffffff") if on else theme_col("#8a93a6", "#767b85"))
            p.drawText(QRectF(x + 3, y + 3, cw - 6, ch - 8), Qt.AlignCenter, label)

    def _paint_unmapped(self, p):
        """Shown instead of a wheel we have never had in front of us.

        Drawing the DFGT layout for a MOMO would light up buttons that are not
        where the picture says they are - worse than showing nothing, because
        it looks authoritative. This says plainly that the model is not mapped.
        """
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen); p.setBrush(theme_col("#232323", "#ededed"))
        p.drawRoundedRect(QRectF(28, 28, w - 56, h - 56), 12, 12)

        f = QFont(); f.setPointSize(13); f.setBold(True); p.setFont(f)
        p.setPen(theme_col("#d8d8d8", "#333333"))
        p.drawText(QRectF(48, h / 2 - 58, w - 96, 26), Qt.AlignCenter,
                   f"{active_profile['name']}  \u2013  {tr('dev.beta')}")

        f.setPointSize(9); f.setBold(False); p.setFont(f)
        p.setPen(theme_col("#9a9a9a", "#666666"))
        p.drawText(QRectF(56, h / 2 - 24, w - 112, 70),
                   Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
                   tr("input.beta_notice"))

        # Something that does move, so the panel is not simply dead: the raw
        # button bits are correct even when their positions are not.
        n = len(self.pressed)
        f.setPointSize(10); f.setBold(True); p.setFont(f)
        p.setPen(theme_col("#ff6a1a", "#d2560f") if n else theme_col("#5a5a5a", "#aaaaaa"))
        p.drawText(QRectF(48, h / 2 + 54, w - 96, 22), Qt.AlignCenter,
                   ", ".join(sorted(self.pressed)) if n else "\u2014")

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        P = self.pressed
        chan = theme_col("#393939", "#d0d0d0")
        doc = documented_buttons(active_profile)
        if doc:
            self._paint_named_grid(p, doc)
        elif g25_drawn():
            self._paint_g27(p, P, chan, g25=True)
        elif layout_unknown(active_profile):
            mapped = learned_buttons(active_profile)
            if mapped:
                self._paint_grid(p, mapped)
            elif descriptor_buttons_ready(active_profile):
                self._paint_grid(p, list(range(usage_reader.button_count)))
            else:
                self._paint_unmapped(p)
        elif active_profile is DEVICE_PROFILES["G27"]:
            self._paint_g27(p, P, chan)
        else:
            self._paint_dfgt(p, P, chan)


CONTACT_EMAIL = "saadettinbozkurt27@gmail.com"


def _wheel_release():
    """The wheel's firmware revision, as hidapi reports it."""
    try:
        for info in hid.enumerate(VID, active_profile.get("pid_native", 0)):
            return int(info.get("release_number") or 0)
    except Exception:
        pass
    return 0


def mapping_report(prof, result, steer=None, checks=None):
    """What someone would need to add this wheel properly, in one block.

    Everything here is about the hardware, not the person using it: the PID
    the wheel reports, its firmware revision, the report length, and which
    byte each pedal turned out to be. No profiles, no file paths, no game
    names - a report you would not mind pasting into a public issue.
    """
    lines = [
        "Legacy Wheel Hub - wheel mapping report",
        f"app         {HUB_VERSION}{' (portable)' if PORTABLE else ''}",
        f"device      {prof.get('name', '?')}  (key {device_key_for(prof)})",
        f"pid         {prof.get('pid_native', 0):#06x}",
        # The firmware revision identifies the hardware exactly, in the same
        # encoding the Linux lg4ff driver uses: 0x1222 is a G25, 0x1238 a G27,
        # 0x1239 the later G27 revision. Two wheels of the same model can
        # differ here, and that is worth knowing before a layout is written
        # down as final.
        f"firmware    {_wheel_release():#06x}",
        f"pedals      {prof.get('pedals', '?')}",
        "support     " + ("measured on hardware" if prof.get("verified")
                          else "from Logitech's driver and Linux, unconfirmed"),
        # Spelled out rather than left as the internal name: "g25" is the
        # protocol, not the model, and seeing it on a G27 report reads like a
        # mistake.
        "rotation    " + {"g25": "0xF8 0x81 (G25/G27/DFGT)",
                           "dfp": "DFP sequence",
                           None: "not adjustable"}.get(prof.get("range_cmd"),
                                                       str(prof.get("range_cmd"))),
    ]
    raw = state.get("raw") or []
    lines.append(f"report      {len(raw)} bytes")
    st = (steer or {})
    lines.append(f"steering    {'reads' if st.get('ok') else 'no reading'}"
                 + (f"  range {st.get('min')}..{st.get('max')}"
                    if st.get("ok") else ""))
    if st.get("bytes"):
        lines.append(f"steer bytes {st['bytes']}")
    sl = st.get("layout")
    if sl:
        # Written the way DEVICE_PROFILES wants it, so adding the wheel is a
        # paste rather than a translation exercise.
        lines.append("steer layout " + repr(sl))
    elif st.get("ok"):
        lines.append("steer layout could not be solved from the sweep")
    if result:
        for k in ("throttle", "brake", "clutch"):
            lines.append(f"{k:<12}{result.get(k)}")
        lines.append(f"invert      {result.get('pedal_invert')}")
        if result.get("combined"):
            lines.append(f"combined    {result['combined']}")
    if raw:
        lines.append("rest bytes  " + " ".join(f"{b:02X}" for b in raw[:16]))
    btns = (result or {}).get("_buttons") or []
    if btns:
        lines.append(f"buttons     {len(btns)} seen, in press order")
        lines.append("  " + ", ".join(f"b{b}.{t}" for b, t in btns))
    if checks:
        lines.append("")
        for k, v in checks.items():
            if k.startswith("dump "):
                continue
            lines.append(f"{k:<13}{v}")
    # The raw blocks last, because they are for reading at the other end
    # rather than by whoever ran the wizard. A yes/no about a layout is worth
    # far less than the bytes it was decided from.
    # A block that can be pasted straight into DEVICE_PROFILES rather than
    # transcribed. Fifteen lines copied by hand is fifteen chances to mistype
    # a byte offset, and the result of one wrong digit is broken telemetry for
    # everybody with that wheel.
    entry = {"pid_native": f"0x{prof.get('pid_native', 0):04X}",
             "pedals": prof.get("pedals"),
             "range_cmd": prof.get("range_cmd")}
    if (steer or {}).get("layout"):
        entry["steer"] = steer["layout"]
    for k in ("throttle", "brake", "clutch"):
        if (result or {}).get(k) is not None:
            entry[k] = result[k]
    if result:
        entry["pedal_invert"] = result.get("pedal_invert")
        if result.get("_buttons"):
            # "byte.bit" rather than nested pairs: json's indent turns every
            # two-element list into four lines, and a wheel with twenty-three
            # buttons then buries the rest of the entry.
            entry["buttons"] = [f"{b}.{t}" for b, t in result["_buttons"]]
    lines.append("")
    lines.append("--- paste-ready ---")
    lines.append(json.dumps(entry, indent=2, sort_keys=True))

    for k, v in (checks or {}).items():
        if k.startswith("dump "):
            lines.append("")
            lines.append(k)
            lines.append(v)
    return "\n".join(lines)


def _quote_capped(text, limit):
    """URL-encode text, shortening the TEXT until the encoded form fits.

    Cutting the encoded string instead can split a %XX escape in half and
    hand the browser a malformed URL.
    """
    from urllib.parse import quote
    q = quote(text)
    while len(q) > limit and text:
        text = text[:max(0, len(text) - max(1, (len(q) - limit) // 3))]
        q = quote(text)
    return q


class PedalMapWizard(QDialog):
    """Finds which byte carries which pedal by watching what moves.

    Reads state["raw"], which the poller already fills, rather than opening a
    second handle to the wheel - two readers on one device is how the handle
    leak used to happen.
    """

    # Steering first: it is the one axis that has to work before telemetry is
    # worth anything, and sweeping the wheel also shows whether the device is
    # reporting at all before the pedal steps start asking questions.
    # Mapping first, then four checks that answer what the report cannot work
    # out on its own: whether the driver exposes its force-feedback block,
    # whether the wheel holds pedal curves, whether force feedback arrives at
    # all, and whether the rotation command does anything. The first is read
    # only; the rest ask one question each.
    # An opening page rather than a first measurement: two things decide
    # whether the results mean anything, and both are easier to fix before
    # starting than to spot afterwards in a report that says "absent".
    STEPS = ("intro", "steer", "rest", "throttle", "brake", "clutch",
             "buttons", "probe", "ffb", "center", "curve", "range")
    # "center": the older wheels may want a different autocentre command
    # (Linux sends the original Driving Force another one), and whether the
    # spring arrives is something only the person holding the wheel can say.
    ASK = ("ffb", "center", "curve", "range")
    SAMPLE_MS = 700          # long enough for a median to mean something
    SWEEP_MS = 4000          # lock to lock, a couple of times over

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("map.title"))
        self.resize(560, 400)
        self.step = 0
        self.live = {"lo": [255] * 16, "hi": [0] * 16,
                     "steer_lo": None, "steer_hi": None, "n": 0}
        self.captured = {}
        self.steer_pairs = []
        self.buttons = []
        self._hat = {}
        self._btn_base = None
        self._btn_skip = set()
        self.checks = {}
        # What the wheel's own descriptor reported during each pedal step:
        # step -> pedal -> (lowest, highest). Compared with the presses at the
        # end, so a report shows whether the descriptor reading is right.
        self.desc_seen = {}
        self._range_before = 0
        self._saved_report = None
        self._saved_range = None
        self._ffb_token = None
        self.result = None
        self.marked = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18); lay.setSpacing(10)

        self.lbl_count = CaptionLabel("")
        lay.addWidget(self.lbl_count)
        self.lbl_title = SubtitleLabel("")
        self.lbl_title.setWordWrap(True)
        lay.addWidget(self.lbl_title)
        self.lbl_sub = CaptionLabel("")
        self.lbl_sub.setWordWrap(True)
        lay.addWidget(self.lbl_sub)

        # What the wheel reports right now, so the extents can be watched being
        # found rather than taken on trust.
        self.lbl_live = StrongBodyLabel("")
        self.lbl_live.setWordWrap(True)
        lay.addWidget(self.lbl_live)

        lay.addStretch(1)
        self.lbl_out = CaptionLabel("")
        self.lbl_out.setWordWrap(True)
        self.lbl_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_out)

        # Always on show. Folding it behind a button made the wizard look like
        # it was hiding something, and the bars are the one part that proves it
        # is reading the wheel at all.
        self.bars = _RawBars()
        lay.addWidget(self.bars)

        # Left is for undoing, right is for going on. The one that moves you
        # forward sits where the eye ends up.
        row = QHBoxLayout()
        self.b_restart = PushButton(tr("map.restart"))
        self.b_restart.clicked.connect(self._restart)
        self.b_restart.setVisible(False)
        self.b_share = PushButton(tr("map.share"))
        self.b_share.clicked.connect(self._share)
        self.b_share.setVisible(False)
        self.b_skip = PushButton(tr("map.skip"))
        self.b_skip.clicked.connect(self._skip)
        self.b_yes = PushButton(tr("map.yes"))
        self.b_yes.clicked.connect(lambda: self._answer(True))
        self.b_no = PushButton(tr("map.no"))
        self.b_no.clicked.connect(lambda: self._answer(False))
        self.b_next = PrimaryPushButton(tr("map.next"))
        self.b_next.clicked.connect(self._next)
        self.b_save = PrimaryPushButton(tr("map.save"))
        self.b_save.clicked.connect(self._save)
        self.b_save.setVisible(False)
        row.addWidget(self.b_restart); row.addWidget(self.b_share)
        row.addStretch(1)
        row.addWidget(self.b_skip)
        row.addWidget(self.b_no); row.addWidget(self.b_yes)
        row.addWidget(self.b_next)
        row.addWidget(self.b_save)
        lay.addLayout(row)

        self.timer = QTimer(self); self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self._show_step()

    # -- flow ------------------------------------------------------------
    def _show_step(self):
        if self.step >= len(self.STEPS):
            return self._finish()
        key = self.STEPS[self.step]
        # Each step samples continuously from the moment it appears. There is
        # no timer: four seconds was not enough to sweep 900 degrees and too
        # long for a pedal, and either way it moved on whether or not the
        # person was ready. The extents are learned as they happen instead,
        # and Next stays disabled until there is enough to work with.
        self.live = {"lo": [255] * 16, "hi": [0] * 16,
                     "steer_lo": None, "steer_hi": None, "n": 0}
        if key == "steer":
            self.steer_pairs = []
        if key == "buttons":
            # Pedals and steering move bits too; they are already mapped, so
            # their bytes are left out rather than turning up as buttons.
            self.buttons = []
            self._hat = {}
            self._btn_base = None
            st = self.captured.get("steer") or {}
            t_b, b_b, c_b, _i = effective_layout(active_profile)
            r = self.result or {}
            self._btn_skip = {x for x in
                              list(st.get("bytes") or []) + [t_b, b_b, c_b] +
                              [r.get(k) for k in ("throttle", "brake", "clutch")]
                              if x is not None}
        self.lbl_count.setText(tr("map.step_of").format(self.step + 1,
                                                        len(self.STEPS)))
        self.lbl_title.setText(tr(f"map.step_{key}"))
        self.lbl_sub.setText(tr(f"map.hint_{key}"))
        self.lbl_live.setText("")
        ask = key in self.ASK
        self.b_next.setVisible(not ask)
        self.b_next.setEnabled(key in ("probe", "intro"))
        for b in (self.b_yes, self.b_no):
            b.setVisible(ask)
        # Skippable on the steering step too: if the reading never arrives
        # there has to be a way past it, or the wizard traps whoever needs it
        # most. The checks are skippable for the same reason.
        self.b_skip.setVisible(key in ("clutch", "steer") or ask)
        self.b_restart.setVisible(self.step > 0)
        if key == "intro":
            pass
        elif key == "probe":
            self._run_probe()
        elif key == "ffb":
            self._start_ffb_test()
        elif key == "center":
            self._start_center_test()
        elif key == "curve":
            self._start_curve_test()
        elif key == "range":
            if active_profile.get("range_cmd") is None:
                # Nothing to try on a wheel with a fixed range.
                self.checks["range"] = "no command"
                self.step += 1
                return self._show_step()
            self._start_range_test()

    def _answer(self, yes):
        # Past the last step there is nothing to answer. The buttons
        # are hidden on the result page so this should not happen,
        # but an index error here would take the window down.
        if self.step >= len(self.STEPS):
            return
        key = self.STEPS[self.step]
        self.checks[key] = "works" if yes else "no effect"
        if key == "range":
            lo, hi = self.live.get("steer_lo"), self.live.get("steer_hi")
            if lo is not None and self._range_before:
                now = hi - lo
                pct = 100 * now / self._range_before
                self.checks["range"] = (
                    f"{self.checks['range']}  (swept {pct:.0f}% of the {self._saved_range or 900}"
                    f"\u00b0 sweep: {now} vs {self._range_before})")
        if key == "ffb":
            # A bare "no effect" is not diagnosable. Whether the command even
            # left the machine, and what the driver settings were at the time,
            # is what separates a wheel without force feedback from one that
            # was muted or held by something else.
            # "NOT SENT, works" is a contradiction: if nothing left the
            # machine, whatever was felt came from somewhere else and the
            # answer says nothing about this wheel.
            self.checks["ffb"] = ("sent, " + self.checks["ffb"]
                                  if getattr(self, "_ffb_sent", False)
                                  else "NOT SENT - answer not meaningful")
            self.checks["ffb ctx"] = self._ffb_context()
        if key == "center":
            self.checks["center"] = ("sent, " + self.checks["center"]
                                     if getattr(self, "_center_sent", False)
                                     else "NOT SENT - answer not meaningful")
        self._end_test(key)
        self.step += 1
        self._show_step()

    def _end_test(self, key):
        """Undo whatever the test did, whether it worked or not."""
        try:
            if key == "curve" and self._saved_report:
                set_feature_report(self.parent_dev(), self._saved_report)
                self._saved_report = None
            elif key == "ffb":
                self._ffb_token = None
                ffb_write(stop_forces_cmd())
            elif key == "center":
                # back to what the user has set, spring included
                if main_window is not None:
                    main_window.apply_settings(silent=True)
                else:
                    ffb_write(autocenter_cmd(0))
            elif key == "range":
                send_rotation(self._saved_range or 900)
                self._saved_range = None
        except Exception:
            pass

    def parent_dev(self):
        try:
            return main_window.settings.pedals_tab.dev
        except Exception:
            return None

    def _hat_note(self):
        names = ("up", "up-right", "right", "down-right", "down", "down-left", "left", "up-left")
        return "; ".join(f"byte {i} low nibble: " + ", ".join(names[d] for d in sorted(ds))
                         for i, ds in sorted(self._hat.items()))

    def _watch_buttons(self, data):
        """Record each bit the first time it goes high, in the order pressed.

        Which bit is which button is written down nowhere - the only way to
        know is to press them and watch. Asking for them in a stated order
        makes the list a mapping rather than a set. Whatever is already high
        when the step opens counts as the resting state, so a wheel with a
        latched switch does not fill the list by itself, and the bytes already
        claimed by the steering and pedals are left out.
        """
        if self._btn_base is None:
            self._btn_base = list(data)
            return
        for i, v in enumerate(data):
            if i in self._btn_skip or i >= len(self._btn_base):
                continue
            base = self._btn_base[i]
            hat = (base & 0x0F) == 0x08 and (v & 0x0F) <= 7
            if hat:
                # A D-pad is a 4-bit hat: 8 at rest, 0-7 for the eight
                # directions. Up is 0 and left reuses the bits of right and
                # down, so learning new bits saw neither - and recorded right
                # and down as two ordinary buttons. Its directions are noted
                # on their own; its four bits are kept out of the buttons.
                self._hat.setdefault(i, set()).add(v & 0x0F)
            newly = v & ~base
            if (base & 0x0F) == 0x08:
                newly &= 0xF0
            for bit in range(8):
                if newly & (1 << bit) and (i, bit) not in self.buttons:
                    self.buttons.append((i, bit))

    def _run_probe(self):
        """Read only: does this wheel expose the two things we write to?

        Neither needs anything from the person running it, and neither can be
        seen from the outside. "The block is there and its gain field agrees
        with the registry" is what decides whether live force feedback and the
        DLL-free LUT can be turned on for a model nobody here has.
        """
        pid = active_profile.get("pid_native", 0)
        _live_verified.pop(pid, None)
        self.checks["live block"] = ("present" if live_block_verified(pid)
                                     else "absent or different")
        rep_ = None
        try:
            dev = self.parent_dev()
            if dev:
                rep_ = get_feature_report(dev, check=False)
        except Exception:
            rep_ = None
        if not rep_:
            self.checks["pedal report"] = "none"
        else:
            ok = pedal_report_valid(rep_)
            self.checks["pedal report"] = (
                f"{len(rep_)} bytes, signature {'ok' if ok else 'different'}")
            # The three blocks as they stand. If the signature failed this is
            # the only way to see why; if it passed it confirms the offsets
            # rather than asking anyone to take them on trust.
            self.checks["dump pedal blocks"] = "\n".join(
                f"  0x{off:02X}  " + " ".join(f"{b:02X}" for b in rep_[off:off + 16])
                for off in (0x21, 0x31, 0x41) if off + 16 <= len(rep_))
            if len(rep_) != PEDAL_REPORT_LEN:
                # Not the G27's table length: the whole report, so one report
                # from this model shows whether it holds pedal curves at all
                # and where.
                self.checks["dump feature report"] = "\n".join(
                    f"  0x{off:02X}  " + " ".join(f"{b:02X}" for b in rep_[off:off + 16])
                    for off in range(0, len(rep_), 16))
        blk = _live_block_snapshot(active_profile.get("pid_native", 0))
        if blk:
            self.checks["dump live block"] = blk
        rd = self._descriptor()
        if rd is not None and rd.values:
            names = {0x30: "X", 0x31: "Y", 0x32: "Z", 0x33: "Rx", 0x34: "Ry", 0x35: "Rz",
                     0x36: "Slider", 0x37: "Dial", 0x38: "Wheel", 0x39: "Hat"}
            def nm(page, u):
                return names.get(u, f"{u:#04x}") if page == 0x01 else f"{page:#06x}:{u:#04x}"
            self.checks["descriptor"] = ", ".join(
                f"{nm(pg, u)} {lo}..{hi}" for pg, u, _i, lo, hi in sorted(rd.values, key=lambda v: v[2]))
            self.checks["desc pedals"] = ", ".join(
                f"{k}={nm(v[0], v[1])}" for k, v in rd.pedals.items()) or "none found"
            self.checks["desc buttons"] = str(rd.button_count)
        self.lbl_live.setText("\n".join(f"{k}: {v}" for k, v in self.checks.items()))

    def _ffb_context(self):
        """Why a force might not arrive, gathered before blaming the wheel."""
        bits = []
        pid = active_profile.get("pid_native", 0)
        bits.append("native" if pid != PID_COMPAT else "compatibility mode")
        try:
            for rp in active_profile.get("registry_pids", []):
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    rf"Software\Logitech\Gaming Software\DriverSettings\{rp}")
                gain = winreg.QueryValueEx(key, "OverallStrength")[0] // 100
                try:
                    en = winreg.QueryValueEx(key, "ForceEnabled")[0]
                except Exception:
                    en = 1
                winreg.CloseKey(key)
                bits.append(f"gain {gain}%")
                if not en:
                    bits.append("force disabled in registry")
                break
        except Exception:
            bits.append("no registry settings")
        return ", ".join(bits)

    def _start_ffb_test(self):
        """A push one way, then the other, so it is felt rather than guessed at.

        constant_force_cmd takes a direction and a percentage; calling it with
        one argument raised a TypeError that the except below swallowed, so
        nothing ever reached the wheel and the test could only ever be
        answered No.
        """
        self._ffb_sent = False
        try:
            self._ffb_sent = ffb_write(constant_force_cmd("left", 70))
            # The second push and the stop only while this test is still on
            # screen: answered within 0.4 s, the push to the right used to
            # land in the middle of the centring test (or after the window
            # had closed) and the stop then cut that test short.
            tok = self._ffb_token = object()
            QTimer.singleShot(400, lambda: tok is self._ffb_token and
                              ffb_write(constant_force_cmd("right", 70)))
            QTimer.singleShot(900, lambda: tok is self._ffb_token and
                              ffb_write(stop_forces_cmd()))
        except Exception as e:
            # Recorded rather than hidden: a failure here is itself an answer.
            self.checks["ffb"] = f"command failed ({type(e).__name__})"
        if not self._ffb_sent:
            self.lbl_live.setText(tr("map.ffb_notsent"))

    def _start_center_test(self):
        """The autocentre spring at a firm setting, to be felt: turn the wheel
        away and let go."""
        self._center_sent = False
        try:
            ffb_write(stop_forces_cmd())
            self._center_sent = ffb_write(autocenter_cmd(60))
        except Exception as e:
            self.checks["center"] = f"command failed ({type(e).__name__})"
        if not self._center_sent:
            self.lbl_live.setText(tr("map.ffb_notsent"))

    def _descriptor(self):
        """The wheel's own descriptor, read for any model - on a wheel with a
        built-in layout it is only reported, never used for reading."""
        try:
            return usage_reader if usage_reader is not None else ensure_usage_reader()
        except Exception:
            return None

    def _track_descriptor(self, raw):
        key = self.STEPS[self.step] if self.step < len(self.STEPS) else None
        if key not in ("rest", "throttle", "brake", "clutch"):
            return
        rd = self._descriptor()
        if rd is None or not rd.pedals:
            return
        seen = self.desc_seen.setdefault(key, {})
        for k in rd.pedals:
            v = rd.pedal(k, raw, True)
            if v is None:
                continue
            lo, hi = seen.get(k, (v, v))
            seen[k] = (min(lo, v), max(hi, v))

    def _descriptor_verdict(self):
        """Did the axis the descriptor rule calls "brake" move when the brake
        was pressed - and the same for the others? This is what confirms the
        reading on a wheel nobody here has."""
        out = []
        for step in ("throttle", "brake", "clutch"):
            seen = self.desc_seen.get(step)
            if not seen or self.captured.get(step) is None:
                continue
            spread = {k: hi - lo for k, (lo, hi) in seen.items()}
            moved = max(spread, key=spread.get)
            if spread[moved] < 64:
                out.append(f"{step}: nothing moved")
            elif moved == step:
                out.append(f"{step} ok")
            else:
                out.append(f"{step} moved the '{moved}' axis")
        return "; ".join(out)

    def _start_curve_test(self):
        """Write an obvious dip into the throttle, to be felt and then undone."""
        self._saved_report = None
        try:
            dev = self.parent_dev()
            rep_ = get_feature_report(dev, check=False) if dev else None
            if not rep_:
                self.checks["curve"] = "no report"
                return
            if not pedal_report_valid(rep_):
                # Not the G27's pedal table: those bytes may mean something
                # else on this wheel, so nothing is written to find out.
                self.checks["curve"] = "pedal table not recognised - not written"
                return
            self._saved_report = rep_
            data = bytearray(rep_)
            off = 0x21                      # throttle block
            data[off + 1:off + 1 + PEDAL_POINTS] = bytes(
                [7, 31, 63, 95, 32, 159, 191, 223])
            set_feature_report(dev, bytes(data))
        except Exception:
            self.checks["curve"] = "write failed"

    def _start_range_test(self):
        """Set a short range and watch how far the wheel actually reports.

        Asking whether the travel felt shorter is a judgement; comparing the
        sweep before and after is a measurement. The question stays, because
        somebody has to turn the wheel for there to be anything to measure,
        but the numbers go in the report either way.
        """
        # back to the range the profile has on the wheel, not a fixed 900
        try:
            self._saved_range = int(main_window.applied_rotation()) if main_window is not None else 900
        except Exception:
            self._saved_range = 900
        st = self.captured.get("steer") or {}
        self._range_before = (st.get("max", 0) or 0) - (st.get("min", 0) or 0)
        self.live["steer_lo"] = self.live["steer_hi"] = None
        try:
            send_rotation(200)
        except Exception:
            self.checks["range"] = "command failed"

    def _enough(self):
        """Whether this step has seen what it needs."""
        key = self.STEPS[self.step]
        if key in self.ASK or key in ("probe", "intro"):
            return True
        if self.live["n"] < 12:
            return False
        if key == "steer":
            lo, hi = self.live["steer_lo"], self.live["steer_hi"]
            if lo is None:
                return False
            # A 10-bit wheel only spans 0..1023, so a flat 2000 could never be
            # reached on a MOMO or a Driving Force - the step they most need.
            # The poller already knows which it is.
            need = 2000 if state.get("_wide") else 200
            return (hi - lo) > need
        if key == "rest":
            return True
        return max(self.live["hi"][i] - self.live["lo"][i]
                   for i in range(16)) > 40

    def _live_text(self):
        key = self.STEPS[self.step]
        if key == "intro":
            return ""
        if key in self.ASK or key == "probe":
            return "\n".join(f"{k}: {v}" for k, v in self.checks.items())
        if key == "steer":
            lo, hi = self.live["steer_lo"], self.live["steer_hi"]
            if lo is None:
                return tr("map.live_waiting")
            return tr("map.live_steer").format(lo, hi, hi - lo)
        if key == "rest":
            return tr("map.live_rest")
        spread = max(self.live["hi"][i] - self.live["lo"][i] for i in range(16))
        return tr("map.live_pedal").format(spread)

    def _next(self):
        # Past the last step there is nothing to answer. The buttons
        # are hidden on the result page so this should not happen,
        # but an index error here would take the window down.
        if self.step >= len(self.STEPS):
            return
        key = self.STEPS[self.step]
        if key in ("probe", "intro", "buttons") or key in self.ASK:
            if key == "buttons":
                self.captured["buttons"] = list(self.buttons)
                if self._hat:
                    self.checks["d-pad"] = self._hat_note()
            self.step += 1
            return self._show_step()
        if key == "steer":
            solved = solve_steer_layout(self.steer_pairs)
            self.captured["steer"] = {
                "ok": self._enough(),
                "layout": solved,
                "min": self.live["steer_lo"], "max": self.live["steer_hi"],
                "bytes": [i for i in range(16)
                          if self.live["hi"][i] - self.live["lo"][i] > 40][:6],
            }
        elif key == "rest":
            self.captured["rest"] = [(self.live["lo"][i] + self.live["hi"][i]) // 2
                                     for i in range(16)]
        else:
            # Whichever end each byte reached that is further from rest: the
            # pedal need not be held down at the instant Next is pressed, only
            # to have been pressed at some point during the step.
            rest = self.captured.get("rest") or [0] * 16
            self.captured[key] = [
                self.live["lo"][i]
                if abs(self.live["lo"][i] - rest[i]) >= abs(self.live["hi"][i] - rest[i])
                else self.live["hi"][i]
                for i in range(16)]
        self.step += 1
        self._show_step()

    def _skip(self):
        # Past the last step there is nothing to answer. The buttons
        # are hidden on the result page so this should not happen,
        # but an index error here would take the window down.
        if self.step >= len(self.STEPS):
            return
        key = self.STEPS[self.step]
        if key in self.ASK:
            # Record that it was passed over rather than leaving a gap the
            # report cannot explain, and undo whatever the test set up.
            self.checks[key] = "skipped"
            self._end_test(key)
        self.captured[key] = None
        self.step += 1
        self._show_step()

    def _abort_test(self):
        """Undo the test on screen, if one is running. Esc, closing the window
        and Start over used to leave it on the wheel: the throttle dip in the
        pedal memory, the 200 degree range, the firm centring spring."""
        if self.step < len(self.STEPS) and self.STEPS[self.step] in self.ASK:
            self._end_test(self.STEPS[self.step])

    def done(self, r):
        # Every way out - Esc, the title bar, Save - comes through here.
        self._abort_test()
        self.timer.stop()
        super().done(r)
        self.deleteLater()

    def _restart(self):
        self._abort_test()
        self.b_next.setVisible(True)
        self.captured.clear(); self.checks.clear(); self.step = 0
        self._saved_report = None; self._saved_range = None
        self.result = None; self.marked = {}
        self.bars.marked = {}
        self.lbl_out.setText("")
        self.b_save.setVisible(False); self.b_share.setVisible(False)
        self._show_step()

    def _finish(self):
        self.result = analyse_pedal_samples(self.captured.get("rest"),
                                            {k: self.captured.get(k) for k in
                                             ("throttle", "brake", "clutch")})
        # Every step button goes, not just the two the result screen knew about
        # before the yes/no pair was added - they were left sitting under the
        # result with nothing left to answer.
        for b in (self.b_next, self.b_skip, self.b_yes, self.b_no):
            b.setVisible(False)
        self.lbl_count.setText(""); self.lbl_live.setText("")
        self.b_restart.setVisible(True)
        self.lbl_title.setText(tr("map.result"))
        if self.result is not None:
            # Carried on the result so every route to the report has it, not
            # just the one that happened to attach it.
            self.result["_buttons"] = self.captured.get("buttons") or []
        verdict = self._descriptor_verdict()
        if verdict:
            self.checks["desc check"] = verdict
        if not self.result:
            self.lbl_sub.setText(tr("map.nothing"))
            return
        r = self.result
        self.bars.marked = {}
        for k, short in (("throttle", "T"), ("brake", "B"), ("clutch", "C")):
            if r.get(k) is not None:
                self.bars.marked[r[k]] = short
        for c in r.get("combined", []):
            self.bars.marked.setdefault(c, "+")
        self.lbl_sub.setText(tr("map.verify"))
        parts = [f"{k}={r[k]}" for k in ("throttle", "brake", "clutch")
                 if r.get(k) is not None]
        st = self.captured.get("steer") or {}
        head = (tr("map.steer_ok") if st.get("ok") else tr("map.steer_none"))
        self.lbl_out.setText(head + "\n" + "  ".join(parts) +
                             f"   invert={r['pedal_invert']}")
        self.b_save.setVisible(True)
        self.b_share.setVisible(True)

    def _write_report_file(self, text):
        """Keep a copy on disk beside the settings.

        Neither of the other two routes suits everybody - a browser may not be
        signed in, and a GitHub account is a thing to have. A file can be
        attached to a message anywhere, and it survives closing the window
        without deciding.
        """
        try:
            path = os.path.join(_data_dir(), "wheel_mapping_report.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path
        except Exception:
            return None

    def _share(self):
        text = mapping_report(active_profile, self.result,
                              self.captured.get("steer"), self.checks)
        try:
            done = global_settings.setdefault("verify_reported", [])
            key = device_key_for(active_profile)
            if key not in done:
                done.append(key)
                save_settings()
        except Exception:
            pass
        try:
            QApplication.clipboard().setText(text)
        except Exception:
            pass
        _ShareDialog(text, self, self._write_report_file(text)).exec()

    def _save(self):
        if self.result:
            st = self.captured.get("steer") or {}
            # The buttons and the solved steering layout are saved too. They
            # were being collected and then dropped: somebody pressed every
            # button on their wheel and the Input Monitor still told them the
            # model was unmapped.
            store_map(active_profile,
                      {k: self.result.get(k) for k in
                       ("throttle", "brake", "clutch")} |
                      {"pedal_invert": self.result["pedal_invert"],
                       "steer_ok": bool(st.get("ok")),
                       "steer_min": st.get("min"), "steer_max": st.get("max"),
                       "steer_layout": st.get("layout"),
                       "buttons": [list(b) for b in
                                   (self.captured.get("buttons") or [])]})
        self.accept()

    # -- live ------------------------------------------------------------
    def _tick(self):
        raw = state.get("raw") or []
        if not raw or self.step >= len(self.STEPS):
            return
        data = list(raw[:16]) + [0] * max(0, 16 - len(raw[:16]))
        self.bars.data = data
        self.bars.update()

        if self.STEPS[self.step] == "buttons":
            self._watch_buttons(data)
        self._track_descriptor(raw)
        lv = self.live
        if self.STEPS[self.step] == "steer":
            sv0 = state.get("steer")
            if sv0 is not None and len(self.steer_pairs) < 400:
                self.steer_pairs.append((list(data), int(sv0)))
        for i, v in enumerate(data):
            if v < lv["lo"][i]: lv["lo"][i] = v
            if v > lv["hi"][i]: lv["hi"][i] = v
        sv = state.get("steer")
        if sv is not None:
            lv["steer_lo"] = sv if lv["steer_lo"] is None else min(lv["steer_lo"], sv)
            lv["steer_hi"] = sv if lv["steer_hi"] is None else max(lv["steer_hi"], sv)
        lv["n"] += 1

        self.lbl_live.setText(self._live_text())
        self.b_next.setEnabled(self._enough())


class _ShareDialog(QDialog):
    """Shows the report before it goes anywhere.

    Sending diagnostics without showing them first is the kind of thing that
    gets found in an open-source repository and remembered. The text is on
    screen, already copied, and the person picks where it goes - or nowhere.
    """

    def __init__(self, text, parent=None, path=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(tr("map.share"))
        self.resize(520, 380)
        self.text = text
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18); lay.setSpacing(10)

        lay.addWidget(SubtitleLabel(tr("map.share_title")))
        sub = CaptionLabel(tr("map.share_body")); sub.setWordWrap(True)
        lay.addWidget(sub)

        box = QLabel(text)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        box.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
        area = QScrollArea(); area.setWidget(box); area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        lay.addWidget(area, 1)

        note = CaptionLabel(tr("map.share_copied")); note.setWordWrap(True)
        lay.addWidget(note)
        if self.path:
            saved = CaptionLabel(tr("map.share_saved").format(self.path))
            saved.setWordWrap(True)
            saved.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(saved)

        row = QHBoxLayout()
        b_gh = PrimaryPushButton(tr("map.share_github"))
        b_gh.clicked.connect(self._github)
        b_mail = PushButton(tr("map.share_mail"))
        b_mail.clicked.connect(self._mail)
        b_file = PushButton(tr("map.share_folder"))
        b_file.setEnabled(bool(self.path))
        b_file.clicked.connect(self._folder)
        row.addWidget(b_file)
        b_close = PushButton(tr("map.share_close"))
        b_close.clicked.connect(self.accept)
        row.addWidget(b_gh); row.addWidget(b_mail)
        row.addStretch(1); row.addWidget(b_close)
        lay.addLayout(row)

    def _open(self, url):
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            try:
                import webbrowser; webbrowser.open(url)
            except Exception:
                pass

    def _folder(self):
        """Open the folder with the file selected, rather than just the folder."""
        if not self.path:
            return
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.path)])
        except Exception:
            self._open("file:///" + os.path.dirname(self.path).replace("\\", "/"))
        self.accept()

    def _github(self):
        from urllib.parse import quote
        title = quote(f"Wheel mapping: {active_profile.get('name', '?')}")
        # The body is pre-filled, but the clipboard copy stands on its own in
        # case the URL gets truncated somewhere along the way.
        body = _quote_capped(self.text, 6000)
        self._open(f"{REPO_URL}/issues/new?title={title}&body={body}")
        self.accept()

    def _mail(self):
        """Compose in Gmail rather than handing this to a mail client.

        A mailto: link opens whatever Windows has registered, which on most
        machines is an Outlook nobody signed into. Gmail's compose URL works
        in the browser the person is already using.
        """
        from urllib.parse import quote
        if not _confirm_dialog(tr("map.browser_title"), tr("map.browser_body"),
                               self, "map.browser_ok"):
            return
        subj = quote(f"LWH wheel mapping: {active_profile.get('name', '?')}")
        body = _quote_capped(self.text, 4000)
        self._open("https://mail.google.com/mail/?view=cm&fs=1"
                   f"&to={CONTACT_EMAIL}&su={subj}&body={body}")
        self.accept()


class _RawBars(QWidget):
    """Every byte of the report, so the wizard is not a black box."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(110)
        self.data = [0] * 16
        self.marked = {}

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), theme_col("#1e2128", "#f2f3f5"))
        n = len(self.data); w = self.width() / n; base = self.height() - 18
        f = QFont(); f.setPointSize(7); p.setFont(f)
        idle = theme_col("#2b2f38", "#d8dade")
        for i, v in enumerate(self.data):
            x = i * w
            h = (base - 6) * (v / 255.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT) if i in self.marked else idle)
            p.drawRect(int(x + 2), int(base - h), int(max(1, w - 4)), int(h))
            p.setPen(QColor(ACCENT) if i in self.marked
                     else theme_col("#8a93a6", "#767b85"))
            p.drawText(int(x), int(base + 2), int(w), 14, Qt.AlignCenter,
                       self.marked.get(i, str(i)))


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


def _confirm_dialog(title, body, parent, ok_key="dlg.delete"):
    """Confirm something. The button says what will happen, not always Delete.

    Every caller shared one label, so a dialog asking whether to overwrite a
    file offered a button marked Delete.
    """
    if MessageBox is not None:
        try:
            m = MessageBox(title, body, parent)
            m.yesButton.setText(tr(ok_key))
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
    # Optional (label_key, callback): renders a real button inside the bar.
    extra = kwargs.pop("_extra", None)

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
        if extra is not None and _LAST_INFOBAR is not None:
            # Best-effort: if this qfluentwidgets build won't take a widget,
            # the notification still shows - it just loses the shortcut.
            try:
                key, cb = extra
                btn = PushButton(tr(key))
                btn.clicked.connect(cb)
                _LAST_INFOBAR.addWidget(btn)
                # The bar is centred when it is created, then addWidget makes
                # it wider WITHOUT moving it - so it grows to the right and
                # ends up visibly off-centre. Re-centre against the parent
                # once the new width is known.
                bar = _LAST_INFOBAR
                par = bar.parentWidget()
                if par is not None:
                    bar.adjustSize()
                    bar.move((par.width() - bar.width()) // 2, bar.y())
            except Exception:
                pass
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
        # Without this the panel ignores its share of a wide window and stays
        # pinned at its minimum while the column beside it takes everything.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
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
        # The list scrolls instead of squeezing: with a dozen profiles the old
        # layout shrank every row to fit the column, until the names were
        # unreadable. The two checkboxes below stay put - they are settings,
        # not list entries, and scrolling them away would hide them.
        self.list_host = QWidget()
        self.listbox = QVBoxLayout(self.list_host)
        self.listbox.setSpacing(2); self.listbox.setContentsMargins(0, 0, 0, 0)
        self.listbox.setAlignment(Qt.AlignTop)

        self.list_scroll = SingleDirectionScrollArea(orient=Qt.Vertical)
        self.list_scroll.setWidget(self.list_host)
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Transparent so the panel keeps its own background in both themes.
        self.list_scroll.setStyleSheet("background: transparent;")
        self.list_host.setStyleSheet("background: transparent;")
        lay.addWidget(self.list_scroll, 1)
        lay.addSpacing(8)

        # Both of these decide when a preset gets applied on its own, so they
        # belong with the preset list rather than buried in Settings.
        self.cb_autogame = CheckBox(tr("ui.autogame"))
        self.cb_autogame.setChecked(bool(global_settings.get("auto_game_profile", True)))
        self.cb_autogame.setToolTip(tr("ui.autogame_h"))
        self.cb_autogame.stateChanged.connect(self._on_autogame)
        lay.addWidget(self.cb_autogame)
        self.cb_auto = CheckBox(tr("ui.autoload"))
        self.cb_auto.setChecked(bool(global_settings.get("auto_load", False)))
        self.cb_auto.stateChanged.connect(self._on_auto)
        lay.addWidget(self.cb_auto)
        self.items = []
        self._pending = []
        self.reload()

    def _mini(self, icon, tip, slot):
        b = TransparentToolButton(icon)
        b.setFixedSize(28, 28); b.setIconSize(QSize(15, 15))
        b.setToolTip(tip); b.clicked.connect(slot)
        return b

    def retranslate(self):
        self.hdr._lbl.setText(tr("ui.presets"))
        self.cb_auto.setText(tr("ui.autoload"))
        self.cb_autogame.setText(tr("ui.autogame"))
        self.cb_autogame.setToolTip(tr("ui.autogame_h"))
        self.btn_add.setToolTip(tr("prof.add")); self.btn_dup.setToolTip(tr("prof.dup"))
        self.btn_ren.setToolTip(tr("prof.edit")); self.btn_del.setToolTip(tr("prof.del"))

    def _selected(self):
        return global_settings.get("selected_profile", "Global")

    # Rows cost a couple of milliseconds each to build, which nobody notices
    # with five profiles and everybody notices with a hundred and fifty - the
    # window took the better part of a second to appear because it was built
    # before anything was shown. They go in a batch at a time instead, so the
    # window is up immediately and the list fills behind it.
    BATCH = 20

    def reload(self):
        for it in self.items:
            it.setParent(None)
        self.items = []
        self._pending = list(global_settings["profiles"].keys())
        sel = self._selected()
        is_global = (sel == "Global")
        self.btn_ren.setEnabled(not is_global)
        self.btn_del.setEnabled(not is_global)
        # The first batch now, so a normal-sized list is complete before the
        # window is even painted and nothing appears to pop in.
        self._fill_batch()
        if self._pending:
            QTimer.singleShot(0, self._fill_batch)

    def _fill_batch(self):
        sel = self._selected()
        for name in self._pending[:self.BATCH]:
            prof = global_settings["profiles"].get(name, {})
            src = prof.get("logo_exe") or prof.get("exe_path") or ""
            it = PresetItem(name, exe_icon(src))
            it.clicked.connect(self.select)
            it.menu_requested.connect(self._context_menu)
            it.set_selected(name == sel)
            self.listbox.addWidget(it)
            self.items.append(it)
        del self._pending[:self.BATCH]
        if self._pending:
            QTimer.singleShot(0, self._fill_batch)

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
        other = exe_owner(global_settings["profiles"], exe)
        if other:
            _defer_infobar("warning", tr("prof.exe_taken"), tr("prof.exe_taken_body").format(other),
                           duration=4000, position=InfoBarPosition.TOP, parent=self.window())
            exe, logo = "", ""
        base = global_settings["profiles"].get(self._selected(), global_settings["profiles"]["Global"])
        # Deep, for the same reason as _duplicate: a shallow copy shared the
        # nested pedal curves with the source profile.
        prof = copy.deepcopy(base)
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
        # Direct mode applies the curve through the driver; a DLL as well
        # would shape the same force twice.
        if not prof.get("lut_enabled") or prof.get("lut_mode", "proxy") == "direct":
            return
        install_proxy_ui(exe, self.window())


    def _duplicate(self):
        cur = self._selected()
        src = global_settings["profiles"].get(cur)
        if src is None:
            return
        n = self._unique(cur + " Copy")
        # Deep: the nested pedal curves were shared between the copy and the
        # original, so editing one changed the other.
        dup = copy.deepcopy(src)
        # The copy gets no game: the game stays with the original, so the two
        # never compete for the same exe (and its LUT).
        dup["exe_path"] = ""
        dup["logo_exe"] = ""
        global_settings["profiles"][n] = dup
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
        elif new and new != cur:
            # Name is taken. The exe/logo edits below still apply to the
            # original profile, but say so - silently keeping the old name
            # looked like the rename had worked.
            _defer_infobar("warning", tr("prof.name_taken"), tr("prof.name_taken_body").format(new),
                           duration=3000, position=InfoBarPosition.TOP, parent=self.window())
        other = exe_owner(profs, exe, skip=cur)
        if other:
            # keep the game this profile had; the other one owns that exe
            _defer_infobar("warning", tr("prof.exe_taken"), tr("prof.exe_taken_body").format(other),
                           duration=4000, position=InfoBarPosition.TOP, parent=self.window())
            exe, logo = old_exe, prof.get("logo_exe", "")
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
        if _confirm_dialog(tr("prof.del_title"), tr("prof.del_msg").format(cur), self.window()):
            self._remove_profile(cur)

    def _remove_profile(self, name):
        """Delete a profile and everything it left behind.

        Shared by the toolbar button and the right-click menu: the menu used
        to pop the profile on its own and leave the game's dinput8.dll in
        place with nothing pointing at it any more.
        """
        gone = global_settings["profiles"].pop(name, None)
        # clean up the game's dinput8.dll if no other profile still uses it
        try:
            exe = (gone or {}).get("exe_path", "")
            if exe:
                # A profile in direct mode needs no DLL, so it must not
                # keep one alive for this exe after the deletion.
                still = any(pp.get("lut_enabled")
                            and pp.get("lut_mode", "proxy") != "direct"
                            and os.path.normcase(pp.get("exe_path", "")) == os.path.normcase(exe)
                            for pp in global_settings["profiles"].values())
                if not still:
                    uninstall_proxy_for(exe)
        except Exception:
            pass
        if global_settings.get("selected_profile") not in global_settings["profiles"]:
            global_settings["selected_profile"] = "Global"
        save_settings()
        self.reload()
        if self.on_select:
            self.on_select(global_settings["selected_profile"])

    def _on_auto(self, *_):
        global_settings["auto_load"] = self.cb_auto.isChecked(); save_settings()

    def _on_autogame(self, *_):
        global_settings["auto_game_profile"] = self.cb_autogame.isChecked(); save_settings()

    def restyle(self):
        for it in self.items:
            it.set_selected(it.selected)

    def _context_menu(self, name, gpos):
        if name == "Global":
            return
        menu = QMenu(self)
        act_ren = menu.addAction(tr("prof.ren_title"))
        act_del = menu.addAction(tr("dlg.delete"))
        chosen = menu.exec(gpos)
        if chosen == act_ren:
            new = _name_dialog(tr("prof.ren_title"), name, self.window())
            if new and new != name and new not in global_settings["profiles"]:
                profs = global_settings["profiles"]
                profs[new] = profs.pop(name)
                if global_settings.get("selected_profile") == name:
                    global_settings["selected_profile"] = new
                save_settings(); self.reload()
                if self.on_select:
                    self.on_select(global_settings["selected_profile"])
            elif new and new != name:
                _defer_infobar("warning", tr("prof.name_taken"), tr("prof.name_taken_body").format(new),
                               duration=3000, position=InfoBarPosition.TOP, parent=self.window())
        elif chosen == act_del:
            if _confirm_dialog(tr("prof.del_title"), tr("prof.del_msg").format(name), self.window()):
                self._remove_profile(name)


# --------------------------------------------------------------------
#  LIVE TELEMETRY column
# --------------------------------------------------------------------
class TelemetryPanel(QWidget):
    def __init__(self):
        super().__init__()
        # 418 was mostly air: the widest thing inside is the wheel graphic at
        # 280, and the panel's own content needed 292. Giving the space back
        # lets the window get narrower without anything in here shrinking -
        # the pedal tab next door needs it more.
        # A range rather than one fixed number: with every spare pixel going
        # to the settings column, a maximised window left this panel at its
        # narrowest while the column beside it grew to two thirds of the
        # screen. Letting it widen a little keeps the three columns in
        # proportion, and the wheel graphic gets the benefit.
        self.setMinimumWidth(344); self.setMaximumWidth(470)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 18, 16, 18); lay.setSpacing(6)
        self.center_btn = PushButton(tr("ui.center")); self.center_btn.setObjectName("centerBtn")
        self.center_btn.clicked.connect(self._center_wheel)
        self.hdr = column_header(tr("ui.telemetry"), self.center_btn)
        lay.addWidget(self.hdr)
        lay.addStretch(1)
        # Scales with the panel instead of sitting at a fixed 248. Maximised,
        # a small wheel marooned in a wide column was most of what looked out
        # of proportion; the graphic is drawn from a pixmap scaled at paint
        # time, so growing it costs nothing and it never gets smaller than it
        # was.
        # Sized from the panel rather than by the layout: centring a widget
        # pins it to its size hint, so an expanding policy alone left the
        # graphic at 248 in a panel half again as wide. Maximised, that small
        # wheel adrift in a wide column was most of what looked wrong.
        self.wheel = WheelWidget()
        self.wheel.setFixedSize(248, 248)
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

    def resizeEvent(self, e):
        super().resizeEvent(e)
        m = self.layout().contentsMargins()
        avail = self.width() - m.left() - m.right()
        size = max(248, min(360, avail))
        if self.wheel.width() != size:
            self.wheel.setFixedSize(size, size)

    def _center_wheel(self):
        if dev is None:
            return
        ffb_write(stop_forces_cmd())
        send_rotation(main_window.applied_rotation())
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
        # Keep the profile's own ramp: omitting it re-sent the default 7 and
        # silently overrode whatever ramp the user had applied.
        ffb_write(autocenter_cmd(f, w.s_ramp.value()))

    def refresh(self):
        n = state["steer_norm"]
        self.wheel.target = main_window.applied_rotation() / 2 * n
        self.wheel.animate()
        self.angle.setText(f"{round(self.wheel.angle)}\u00b0")
        self.steerbar.norm = n; self.steerbar.animate()
        self.throttle.value = state["throttle"] / 255.0
        self.brake.value = state["brake"] / 255.0
        # Whether the wheel has a clutch, not whether we can read it yet.
        has_clutch = active_profile.get("pedals", 2) >= 3
        self.clutch.setVisible(has_clutch)
        self.clutch.enabled_axis = has_clutch
        self.clutch.value = (state["clutch"] / 255.0) if has_clutch else 0.0
        self.throttle.animate(); self.brake.animate(); self.clutch.animate()


# --------------------------------------------------------------------
#  RIGHT column tabs
# --------------------------------------------------------------------
def _restyle_accent(*labels):
    """Re-apply the accent colour to labels after a theme switch.

    Setting a colour once at construction isn't enough: switching theme makes
    qfluentwidgets re-polish every widget, which wipes the per-widget
    stylesheet and leaves these labels plain black/white. Anything drawn in
    the accent colour has to be repainted from the theme handler.
    """
    for lbl in labels:
        try: lbl.setStyleSheet(f"color:{ACCENT};")
        except Exception: pass


def _dim_slider(sl, on):
    """Grey a slider and its labels together.

    Disabling the control alone leaves its name and value at full strength, so
    a row that does nothing still reads as active.
    """
    sl.setEnabled(on)
    muted = theme_col("#8a93a6", "#9aa0a8").name()
    for w, live in ((getattr(sl, "_nm", None), ""),
                    (getattr(sl, "_hint", None), ""),
                    (getattr(sl, "_val", None), f"color:{ACCENT};")):
        if w is not None:
            w.setStyleSheet(live if on else f"color:{muted};")


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
        self.s_spring = _slider_block(lay, tr("wheel.spring"), 0, 100, prof.get("di_spring", 100), "%",
                                      tr("wheel.spring_h"))
        self.s_damper = _slider_block(lay, tr("wheel.damper"), 0, 100, prof.get("di_damper", 100), "%",
                                      tr("wheel.damper_h"))
        self.cb_center = CheckBox(tr("wheel.center_cb"))
        self.cb_center.setChecked(prof.get("di_persist", False))
        if hasattr(self.cb_center, "checkedChanged"):
            self.cb_center.checkedChanged.connect(lambda *_: self._dim_centering())
        else:
            self.cb_center.stateChanged.connect(lambda *_: self._dim_centering())
        lay.addWidget(self.cb_center); lay.addSpacing(4)
        self.s_center = _slider_block(lay, tr("wheel.center"), 0, 100, prof.get("di_center", 0), "%",
                                      tr("wheel.center_h"))
        # Autocenter ramp (0-15): the wheel's own spring slope. Same force with a
        # different ramp gives a very different center character.
        self.s_ramp = _slider_block(lay, tr("wheel.ramp"), 0, 15, prof.get("di_ramp", DEFAULT_RAMP), "",
                                    tr("wheel.ramp_h"))
        lay.addSpacing(6)
        self.h_steer = section_header(tr("wheel.sec_steer")); lay.addWidget(self.h_steer); lay.addSpacing(6)
        self.s_rot = _slider_block(lay, tr("wheel.rotation"), 40, 900, prof.get("angle", 900), "\u00b0",
                                   tr("wheel.rotation_h"))
        prow = QHBoxLayout(); prow.setSpacing(6)
        for d in (270, 360, 540, 720, 900):
            b = PushButton(f"{d}\u00b0")
            b.setMinimumWidth(1)                 # 5 buttons must fit when narrow
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _, x=d: self.s_rot.setValue(x))
            self._rot_presets = getattr(self, "_rot_presets", []) + [b]
            prow.addWidget(b)
        lay.addLayout(prow)
        lay.addSpacing(10)
        # LGS's "Allow Game To Adjust Settings" (see LgsRouter): saved with
        # APPLY like the rest; what a game set shows under it while it runs
        self.cb_game = CheckBox(tr("lgs.allow"))
        self.cb_game.setToolTip(tr("lgs.allow_h"))
        self.cb_game.setChecked(prof.get("allow_game", True))
        lay.addWidget(self.cb_game)
        self.lbl_game = CaptionLabel("")
        self.lbl_game.setWordWrap(True)
        self.lbl_game.setVisible(False)
        lay.addWidget(self.lbl_game)
        lay.addStretch(1)

    def show_game(self, layer, who):
        """What the running game set, under the tick box; nothing when none."""
        if not layer:
            self.lbl_game.setVisible(False)
            return
        name = (who or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0] or tr("lgs.game")
        parts = []
        if "angle" in layer:
            parts.append("%d\u00b0" % layer["angle"])
        if "di_gain" in layer:
            parts.append(tr("wheel.overall") + " %d%%" % layer["di_gain"])
        for k, lk in (("di_spring", "wheel.spring"), ("di_damper", "wheel.damper"), ("di_center", "wheel.center")):
            if k in layer:
                parts.append(tr(lk) + " %d%%" % layer[k])
        self.lbl_game.setText(tr("lgs.set_by").format(name, ", ".join(parts)))
        self.lbl_game.setStyleSheet(f"color:{ACCENT};")
        self.lbl_game.setVisible(True)

    def retranslate(self):
        self.h_ffb._lbl.setText(tr("wheel.sec_ffb")); self.h_steer._lbl.setText(tr("wheel.sec_steer"))
        self.cb_center.setText(tr("wheel.center_cb"))
        self.cb_game.setText(tr("lgs.allow")); self.cb_game.setToolTip(tr("lgs.allow_h"))
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
        _restyle_accent(*(sl._val for sl in (self.s_gain, self.s_spring, self.s_damper,
                                             self.s_center, self.s_ramp, self.s_rot)))

    def load_profile(self, name):
        prof = global_settings["profiles"].get(name, {})
        self.s_gain.setValue(prof.get("di_gain", 101))
        self.s_spring.setValue(prof.get("di_spring", 100))
        self.s_damper.setValue(prof.get("di_damper", 100))
        self.cb_center.setChecked(prof.get("di_persist", False))
        self.s_center.setValue(prof.get("di_center", 0))
        self.s_ramp.setValue(prof.get("di_ramp", DEFAULT_RAMP))
        self.s_rot.setValue(prof.get("angle", 900))
        self.cb_game.setChecked(prof.get("allow_game", True))
        self.refresh_caps()

    def refresh_caps(self):
        """Grey out what this wheel cannot do rather than let it look active.

        MOMO and Driving Force have a fixed range; a slider that moves and
        changes nothing is worse than one plainly unavailable.
        """
        can_rotate = active_profile.get("range_cmd") in ("g25", "dfp")
        _dim_slider(self.s_rot, can_rotate)
        for b in getattr(self, "_rot_presets", []):
            b.setEnabled(can_rotate)
        self._dim_centering()

    def _dim_centering(self):
        # The strength and ramp belong to the centering spring, so they follow
        # its checkbox rather than sitting there at full strength doing
        # nothing.
        on = self.cb_center.isChecked()
        _dim_slider(self.s_center, on)
        _dim_slider(self.s_ramp, on)



# --- Firmware Pedal Curves ---
import ctypes
from ctypes import wintypes

PEDAL_REPORT_LEN = 145
# Which wheels keep a pedal table in their firmware, from their own HID report
# descriptors (vendor page FF00, usage 03, a feature report): the G27 and the
# G25 declare 144 bytes, the Driving Force GT 131 - the length HidD_GetFeature
# takes is one more, for the report id. The Driving Force Pro, Driving Force
# and both MOMOs declare no feature report at all: there is no table to write.
# Sources: the wheels' descriptors as dumped from the hardware (Linux debugfs,
# LTWheelConf; lgff_wheel_adapter; GIMX) and the Linux driver's own copies
# of the older ones (hid-lg.c). Logitech's driver and LGS never touch it.
PEDAL_TABLE_LEN = {0xC29B: 145, 0xC299: 145, 0xC29A: 132}      # G27, G25, DFGT
PEDAL_BLOCKS = (0x21, 0x31, 0x41)                                 # throttle, brake, clutch


def pedal_table_len(prof=None):
    """The feature report this wheel's pedal table comes in, or None when the
    wheel has none."""
    prof = active_profile if prof is None else prof
    return PEDAL_TABLE_LEN.get(prof.get("pid_native"))
PEDAL_POINTS = 8
PEDAL_FULL = 255
PEDALS_DEF = [("clutch",   "home.clutch",   0x41, 11),
          ("brake",    "home.brake",    0x31, 6),
          ("throttle", "home.throttle", 0x21, 5)]
PEDAL_STOCK = [7, 31, 63, 95, 127, 159, 191, 223]
# Where each table point sits along the pedal travel (0 = at rest, 1 = floored).
# The factory table is the identity sampled at its own values, so point i is
# at the travel its byte stands for: byte 7 near full press, 223 near rest.
# The ends of the travel are not in the table - the wheel pins them to byte
# 255 (0%) at rest and byte 0 (100%) floored - so the stock curve is a
# straight line from corner to corner. Drawing the points evenly from edge to
# edge instead made it look as if the pedal never reached 0% or 100%.
PEDAL_X = [1.0 - v / PEDAL_FULL for v in PEDAL_STOCK]


def pedal_curve_nodes(points, reverse=False):
    """(travel, byte) pairs to draw a curve with, including the two fixed ends."""
    full_end, rest_end = (PEDAL_FULL, 0) if reverse else (0, PEDAL_FULL)
    return ([(1.0, full_end)] + [(PEDAL_X[i], v) for i, v in enumerate(points)]
            + [(0.0, rest_end)])

# Loaded at import, so a failure here would take the whole app down with it -
# and pedal curves are one feature among many. If these are not available the
# tab simply reports that it cannot reach the wheel.
try:
    _hid_dll = ctypes.WinDLL("hid")
    _setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
except Exception:
    _hid_dll = _setupapi = _k32 = None

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

class SP_DID(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD),
                ("Reserved", ctypes.POINTER(wintypes.ULONG))]

class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]

# Declaring these needs the libraries to have loaded. On x64 the return
# types matter: without them a handle comes back truncated and every
# later call fails for no visible reason.
if _hid_dll is not None:
    _setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    _setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    _setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.POINTER(SP_DID)]
    _setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    _setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE, ctypes.POINTER(SP_DID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    _setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _hid_dll.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
    _hid_dll.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
    _hid_dll.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    _hid_dll.HidD_GetFeature.restype = wintypes.BOOLEAN
    _hid_dll.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    _hid_dll.HidD_SetFeature.restype = wintypes.BOOLEAN

def open_feature_handle(vid, pid):
    if sys.platform != "win32" or _hid_dll is None: return None
    g = GUID()
    _hid_dll.HidD_GetHidGuid(ctypes.byref(g))
    h = _setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)
    if not h or h == wintypes.HANDLE(-1).value: return None
    idx = 0
    try:
        while True:
            did = SP_DID()
            did.cbSize = ctypes.sizeof(did)
            if not _setupapi.SetupDiEnumDeviceInterfaces(h, None, ctypes.byref(g), idx, ctypes.byref(did)): break
            idx += 1
            need = wintypes.DWORD()
            _setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8
            if not _setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(did), buf, need.value, None, None): continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            dev = _k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
            if dev == wintypes.HANDLE(-1).value: continue
            attr = HIDD_ATTRIBUTES()
            attr.Size = ctypes.sizeof(attr)
            if _hid_dll.HidD_GetAttributes(dev, ctypes.byref(attr)) and attr.VendorID == vid and attr.ProductID == pid:
                return dev
            _k32.CloseHandle(dev)
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(h)
    return None

def pedal_report_valid(report):
    """Whether this really is the pedal table we decoded on a G27.

    The layout was worked out on one wheel: three sixteen-byte blocks, each a
    flag byte of 0x0F followed by eight values that rise. Another model
    answering HidD_GetFeature is not the same as it holding the same thing at
    the same offsets, and writing a curve into whatever happens to be there
    would be a poor way to find out. Anything that fails this gets left alone.
    """
    if not report or len(report) not in PEDAL_TABLE_LEN.values():
        return False
    # one block per pedal the wheel has: the Driving Force GT's shorter
    # report is checked for its throttle and brake
    blocks = PEDAL_BLOCKS[:3 if len(report) == PEDAL_REPORT_LEN else 2]
    for off in blocks:
        if report[off] != 0x0F:
            return False
        pts = list(report[off + 1:off + 1 + PEDAL_POINTS])
        if len(pts) != PEDAL_POINTS:
            return False
        if any(pts[i] > pts[i + 1] for i in range(PEDAL_POINTS - 1)):
            return False
    return True


def feature_report_length(dev):
    """How long this wheel's feature report is, from its own descriptor; 0 if
    it declares none or it cannot be asked. The G27's pedal table is 145."""
    if not dev or _hidp is None:
        return 0
    pp = _ct.c_void_p()
    try:
        if not _hidp.HidD_GetPreparsedData(dev, _ct.byref(pp)):
            return 0
        try:
            caps = _HIDP_CAPS()
            if _hidp.HidP_GetCaps(pp, _ct.byref(caps)) != HIDP_STATUS_SUCCESS:
                return 0
            return int(caps.FeatureReportByteLength)
        finally:
            _hidp.HidD_FreePreparsedData(pp)
    except Exception:
        return 0


def get_feature_report(dev, check=True):
    """The wheel's feature report. Checked: only the G27's 145-byte pedal
    table. Unchecked (the setup check's probe): at the length the wheel's own
    descriptor gives, so a wheel with a different table is still read and
    shown in the report instead of answering "none"."""
    if not dev: return None
    n = ((pedal_table_len() or PEDAL_REPORT_LEN) if check
         else (feature_report_length(dev) or pedal_table_len() or PEDAL_REPORT_LEN))
    buf = ctypes.create_string_buffer(n)
    buf[0] = b"\x00"
    if not _hid_dll.HidD_GetFeature(dev, buf, n): return None
    data = bytes(buf.raw)
    if check and not pedal_report_valid(data):
        return None
    return data

def set_feature_report(dev, data):
    if not dev: return False
    n = len(data)                      # the report as it was read: 145 or 132
    buf = ctypes.create_string_buffer(bytes(data), n)
    return bool(_hid_dll.HidD_SetFeature(dev, buf, n))

# The graph spans the whole byte, 0..PEDAL_FULL. It used to span the factory
# table's own ends (7..223), but a deadzone or saturation writes 0 and 255:
# those were drawn on top of 7 and 223, could not be reached by dragging, and
# a point dragged once silently lost them - while the axis said 0 and 255.
def pedal_to_display(v, reverse=False):
    y = max(0.0, min(1.0, v / PEDAL_FULL))
    return y if reverse else 1.0 - y

def pedal_from_display(y, reverse=False):
    y = max(0.0, min(1.0, y))
    frac = y if reverse else 1.0 - y
    return int(round(PEDAL_FULL * frac))

def pedal_monotonic(points, reverse=False):
    out, prev = [], 0
    for v in points:
        v = max(prev, min(PEDAL_FULL, int(v)))
        out.append(v)
        prev = v
    return out

def pedal_shape(x, g, kind="gamma"):
    x = max(0.0, min(1.0, x))
    if kind == "s": return (x * x * (3 - 2 * x)) ** max(0.05, g)
    return x ** max(0.05, g)

def pedal_build_points(g=1.0, dead=0.0, sat=1.0, kind="gamma"):
    dead = max(0.0, min(0.49, dead))
    sat = max(dead + 0.01, min(1.0, sat))
    out = []
    
    # One model for every curve - gamma, S, deadzone, saturation. Points are
    # placed where they really sit along the travel (PEDAL_X), the same
    # positions the graph draws them at, and the shape runs over the WHOLE
    # travel, byte 255 at rest to byte 0 floored - the two ends the wheel
    # fixes itself. Gamma 1.00 then lands exactly on the factory table.
    #
    # Shaping only between the factory's first and last points (bytes 223 and
    # 7) left a straight stretch from each end to its nearest point whatever
    # the gamma, so a gamma curve visibly kinked before its first point.
    # Even steps, used before that, made a one-step deadzone flatten a quarter
    # of the pedal while its label said 14%; now it is 12.5%.
    #
    # A hair of tolerance on both comparisons, so a ULP never decides whether
    # a point falls inside the deadzone or saturation.
    eps = 1e-9
    pos = PEDAL_X
    for i in range(PEDAL_POINTS):
        x = pos[i]

        if x <= dead + eps:
            y = 0.0
        elif x >= sat - eps:
            y = 1.0
        else:
            y = pedal_shape((x - dead) / (sat - dead), g, kind)
        
        # Always the whole byte range. Squeezing a curve into the factory's
        # 7..223 is what once pinned a deadzone at 223 (12.5%), so the pedal
        # climbed to 12% and held there instead of reading nothing.
        val = max(0, min(255, round(PEDAL_FULL * (1.0 - y))))
        out.append(val)
        
    for i in range(PEDAL_POINTS - 2, -1, -1):
        x = pos[i]
        if dead + eps < x < sat - eps and out[i] >= out[i+1]:
            out[i] = max(0, out[i+1] - 1)
            
    for i in range(1, PEDAL_POINTS):
        x = pos[i]
        if dead + eps < x < sat - eps and out[i] <= out[i-1]:
            out[i] = min(255, out[i-1] + 1)
            

    return out

def pedal_gamma_points(g):
    """Plain gamma curve over the whole travel; 1.00 is the factory table.

    The same model as pedal_build_points, so a gamma curve and one with a
    deadzone or saturation can never disagree about where a point belongs.
    """
    return pedal_build_points(g, 0.0, 1.0, "gamma")

_GAMMA_CANDIDATES = None


def pedal_fit_gamma(points):
    """Gamma whose curve lies closest to a hand-dragged set of points.

    Candidates cover the Gamma slider's own range and are built once: this
    runs on every mouse move while a point is dragged.
    """
    global _GAMMA_CANDIDATES
    if _GAMMA_CANDIDATES is None:
        _GAMMA_CANDIDATES = [(k / 100.0, pedal_gamma_points(k / 100.0))
                             for k in range(30, 301)]
    best, bg = None, 1.0
    for g, cand in _GAMMA_CANDIDATES:
        err = sum((cand[i] - points[i]) ** 2 for i in range(PEDAL_POINTS))
        if best is None or err < best: best, bg = err, g
    return bg


class CurveIcon(QWidget):
    clicked = Signal(str)
    
    def __init__(self, key, kind="linear"):
        super().__init__()
        self.key = key
        self.kind = kind
        self.setFixedSize(36, 24)
        self.active = False
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        if self.active:
            p.setBrush(QColor(ACCENT))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, 4, 4)
            pen_color = QColor("#ffffff")
        else:
            pen_color = QColor(ACCENT)
        
        p.setPen(QPen(pen_color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        
        path = QPainterPath()
        w, h = self.width(), self.height()
        m_x, m_y = 6, 5 
        x0, y0 = m_x, h - m_y
        x1, y1 = w - m_x, m_y
        
        if self.kind == "linear":
            path.moveTo(x0, y0)
            path.lineTo(x1, y1)
        elif self.kind == "soft":
            path.moveTo(x0, y0)
            for i in range(1, 11):
                t = i / 10.0
                path.lineTo(x0 + t*(x1-x0), y0 - (t**1.6)*(y0-y1))
        elif self.kind == "quick":
            path.moveTo(x0, y0)
            for i in range(1, 11):
                t = i / 10.0
                path.lineTo(x0 + t*(x1-x0), y0 - (t**0.6)*(y0-y1))
        elif self.kind == "s":
            path.moveTo(x0, y0)
            for i in range(1, 11):
                t = i / 10.0
                y_t = (t**2 * (3 - 2*t))
                path.lineTo(x0 + t*(x1-x0), y0 - y_t*(y0-y1))
        p.drawPath(path)


class PedalCurveView(QWidget):
    def __init__(self, on_change):
        super().__init__()
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.points = list(PEDAL_STOCK)
        self.live = 0.0
        self.on_change = on_change
        self.drag = None
        self.reverse = False
        self.dead, self.sat = 0.0, 1.0

    def _rect(self): return self.rect().adjusted(24, 12, -12, -20)

    # QRectF, not QRect: QRect.bottom() is top + height - 1, so the top edge
    # of the graph read back as byte 1 instead of 0.
    def _to_pt(self, i, v):
        return self._xy(PEDAL_X[i], v)

    def _xy(self, travel, v):
        r = QRectF(self._rect())
        x = r.left() + r.width() * travel
        y = r.bottom() - r.height() * pedal_to_display(v, self.reverse)
        return QPointF(x, y)

    def _curve(self, points):
        return QPolygonF([self._xy(t, v)
                          for t, v in pedal_curve_nodes(points, self.reverse)])

    def _from_y(self, y):
        r = QRectF(self._rect())
        return pedal_from_display((r.bottom() - y) / max(1.0, r.height()), self.reverse)

    def _nearest(self, pos):
        best, bi = 1e9, None
        for i, v in enumerate(self.points):
            d = (self._to_pt(i, v) - pos).manhattanLength()
            if d < best: best, bi = d, i
        return bi if best < 30 else None

    def mousePressEvent(self, e):
        self.drag = self._nearest(e.position())
        if self.drag is not None: self.mouseMoveEvent(e)

    def limits(self, i):
        if self.reverse:
            lo = self.points[i + 1] if i < PEDAL_POINTS - 1 else 0
            hi = self.points[i - 1] if i > 0 else PEDAL_FULL
        else:
            lo = self.points[i - 1] if i > 0 else 0
            hi = self.points[i + 1] if i < PEDAL_POINTS - 1 else PEDAL_FULL
        return lo, hi

    def mouseMoveEvent(self, e):
        if self.drag is None: return
        lo, hi = self.limits(self.drag)
        self.points[self.drag] = max(lo, min(hi, self._from_y(e.position().y())))
        self.update()
        self.on_change(self.points, from_drag=True)

    def mouseReleaseEvent(self, _): self.drag = None

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = self._rect()
        dark = isDarkTheme()
        bg = QColor("#1e2128") if dark else QColor("#f4f5f8")
        line = QColor("#2b2f38") if dark else QColor("#e0e3e8")
        muted = QColor("#8a93a6") if dark else QColor("#8a93a6")
        text = QColor("#e6e8ee") if dark else QColor("#1b1b1b")
        p.fillRect(self.rect(), bg)
        
        p.setPen(QPen(line, 1))
        for i in range(5):
            y = r.top() + r.height() * i / 4
            p.drawLine(r.left(), int(y), r.right(), int(y))
        for i in range(PEDAL_POINTS):
            x = self._to_pt(i, 0).x()
            p.drawLine(int(x), r.top(), int(x), r.bottom())

        f = QFont(); f.setPointSize(7); p.setFont(f); p.setPen(muted)
        p.drawText(QRectF(0, r.top() - 6, 20, 14), Qt.AlignRight, "255")
        p.drawText(QRectF(0, r.bottom() - 8, 20, 14), Qt.AlignRight, "0")
        p.setPen(QPen(line, 1, Qt.DotLine)); p.drawLine(r.left(), r.bottom(), r.right(), r.top())
        p.setPen(QPen(line, 1, Qt.DashLine)); p.drawPolyline(self._curve(PEDAL_STOCK))

        if self.drag is not None:
            lo, hi = self.limits(self.drag)
            x = self._to_pt(self.drag, 0).x()
            top, bot = self._to_pt(self.drag, hi).y(), self._to_pt(self.drag, lo).y()
            # normalized(): the graph puts 0 at the bottom, so the upper limit has
            # the smaller y and the height came out negative - Qt treats that
            # rectangle as invalid and drew nothing at all.
            p.fillRect(QRectF(x - 9, top, 18, bot - top).normalized(), _accent_rgba(40))

        if self.dead > 0 or self.sat < 1.0:
            p.fillRect(QRectF(r.left(), r.top(), r.width() * self.dead, r.height()), _accent_rgba(22))
            p.fillRect(QRectF(r.left() + r.width() * self.sat, r.top(), r.width() * (1 - self.sat), r.height()), _accent_rgba(22))

        p.setPen(QPen(QColor(ACCENT), 2))
        # Through the fixed ends too, so the curve meets 0% and 100%.
        p.drawPolyline(self._curve(self.points))
        
        p.setBrush(QColor(ACCENT))
        for i, v in enumerate(self.points): p.drawEllipse(self._to_pt(i, v), 4, 4)
        # The two fixed ends (0% at rest, 100% floored). They are not in the
        # table and cannot be dragged, so they are drawn hollow - but they are
        # drawn, or the curve looks cut off at the top.
        ends = pedal_curve_nodes(self.points, self.reverse)
        p.setBrush(bg); p.setPen(QPen(QColor(ACCENT), 2))
        for t, v in (ends[0], ends[-1]):
            p.drawEllipse(self._xy(t, v), 4, 4)

        out = pedal_to_display(self.live * PEDAL_FULL, self.reverse)
        lo_u, hi_u = None, None
        # Search the same segments that are drawn, ends included, so the
        # marker sits on the curve all the way to 0% and 100%.
        nodes = pedal_curve_nodes(self.points, self.reverse)
        for (xa, va), (xb, vb) in zip(nodes, nodes[1:]):
            a_, b_ = pedal_to_display(va, self.reverse), pedal_to_display(vb, self.reverse)
            if min(a_, b_) - 1e-6 <= out <= max(a_, b_) + 1e-6:
                t = 0.0 if abs(b_ - a_) < 1e-6 else (out - a_) / (b_ - a_)
                u0, u1 = (xa, xb) if abs(b_ - a_) >= 1e-6 else (min(xa, xb), max(xa, xb))
                u = xa + (xb - xa) * t
                lo_u = u if lo_u is None else min(lo_u, min(u0, u1))
                hi_u = u if hi_u is None else max(hi_u, max(u0, u1))
        if lo_u is None: lo_u = hi_u = 1.0 if out >= pedal_to_display(nodes[0][1], self.reverse) else 0.0
        
        # On a flat stretch every position gives the same output, so the
        # midpoint is as good a guess as any - but it moves the moment the
        # span changes width. Staying where it already was, when that is still
        # inside the span, keeps it from sliding about.
        prev_u = getattr(self, "_last_u", None)
        u_mid = ((lo_u + hi_u) / 2.0 if prev_u is None or not (lo_u <= prev_u <= hi_u)
                 else prev_u)
        self._last_u = u_mid
        y = r.bottom() - r.height() * out
        x_mid = r.left() + r.width() * u_mid
        p.setPen(QPen(text, 1))
        p.drawLine(int(x_mid), r.top(), int(x_mid), r.bottom())
        p.setBrush(text); p.drawEllipse(QPointF(x_mid, y), 3, 3)

class SinglePedalPage(QFrame):
    def __init__(self, key, label_key, offset, parent_tab):
        super().__init__()
        self._guard = False
        self._applying_preset = False
        self.key, self.label_key, self.offset, self.parent_tab = key, label_key, offset, parent_tab
        self.kind = "gamma"
        self.setObjectName("SinglePedalPage")
        
        self.setMinimumWidth(210)
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(6)
        
        head_lay = QHBoxLayout()
        head_lay.setContentsMargins(0, 0, 0, 0)
        self.head = StrongBodyLabel(tr(self.label_key))
        
        self.btn_reset = TransparentToolButton(FIF.SYNC)
        self.btn_reset.setToolTip(tr("pedals.reset"))
        self.btn_reset.clicked.connect(self.reset_controls)
        
        head_lay.addWidget(self.head)
        head_lay.addStretch(1)
        head_lay.addWidget(self.btn_reset)
        lay.addLayout(head_lay)
        
        self.view = PedalCurveView(self.points_changed)
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.lbl_gamma = CaptionLabel(tr("pedals.gamma"))
        self.val_gamma = CaptionLabel("1.00"); self.val_gamma.setStyleSheet(f"color:{ACCENT}; font-weight:bold;")
        row.addWidget(self.lbl_gamma); row.addStretch(1); row.addWidget(self.val_gamma)
        lay.addLayout(row)

        self.slider = Slider(Qt.Horizontal)
        self.slider.setRange(30, 300); self.slider.setValue(100)
        self.slider.valueChanged.connect(lambda v: self.rebuild(from_preset=False))
        lay.addWidget(self.slider)

        self.s_dead = self._range_row(lay, "pedals.deadzone", 0, 3, 0, step=True)
        self.s_sat = self._range_row(lay, "pedals.saturation", 4, 7, 7, step=True)

        self.preset_bar = QFrame()
        self.preset_bar.setFixedHeight(36)
        
        p_lay = QHBoxLayout(self.preset_bar)
        p_lay.setContentsMargins(6, 0, 10, 0)
        p_lay.setSpacing(6)
        
        p_lay.addStretch(1)
        
        self.icons = []
        preset_defs = [
            ("linear", dict(kind="gamma", g=1.00)),
            ("soft", dict(kind="gamma", g=1.60)),
            ("quick", dict(kind="gamma", g=0.60)),
            ("s", dict(kind="s", g=1.00))
        ]
        
        for kind, kw in preset_defs:
            icon = CurveIcon(kind, kind)
            icon.clicked.connect(lambda k=kind, kwargs=kw: self.apply_preset(k, kwargs))
            p_lay.addWidget(icon)
            self.icons.append(icon)
            
        # A button, not a label: picking a preset used to throw a hand-drawn
        # curve away with no way back to it. This keeps the last one and
        # returns to it when clicked. A minimum width stops the text being
        # squeezed down to "C" when the window is made small.
        self.lbl_custom = TransparentPushButton(tr("pedals.custom"))
        f = self.lbl_custom.font(); f.setPointSize(8); self.lbl_custom.setFont(f)
        # Width measured from the text rather than guessed. A fixed 58px left
        # the last letter under the panel's rounded edge, because it took no
        # account of the button's own padding or of the font the theme uses.
        # The widest translation is used so switching language never clips it.
        self.lbl_custom.setMinimumWidth(
            max(QFontMetrics(f).horizontalAdvance(d.get("pedals.custom", "Custom"))
                for d in LANG.values()) + 26)
        self.lbl_custom.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.lbl_custom.setCursor(Qt.PointingHandCursor)
        self.lbl_custom.clicked.connect(self.restore_custom)
        p_lay.addWidget(self.lbl_custom)
        
        p_lay.addStretch(1)
        lay.addWidget(self.preset_bar)
        
        self.set_custom_mode()

    def _range_row(self, lay, label_key, lo, hi, value, step=False):
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        name = CaptionLabel(tr(label_key)); name._lbl_key = label_key
        fmt = (lambda v: f"{100 * v / (PEDAL_POINTS - 1):.0f}%") if step else (lambda v: f"{v}%")
        val = CaptionLabel(fmt(value)); val.setStyleSheet(f"color:{ACCENT}; font-weight:bold;")
        row.addWidget(name); row.addStretch(1); row.addWidget(val)
        lay.addLayout(row)
        sl = Slider(Qt.Horizontal); sl.setRange(lo, hi); sl.setValue(value); sl.setPageStep(1)
        sl.valueChanged.connect(lambda v, label=val, f=fmt: (label.setText(f(v)), self.rebuild(from_preset=False)))
        lay.addWidget(sl)
        sl.value_label, sl.fmt, sl._name_lbl = val, fmt, name
        return sl

    def remember_custom(self):
        """Keep whatever the user shaped by hand, so a preset does not lose it."""
        self.custom_state = {
            "points": list(self.view.points),
            "g": self.slider.value(),
            "dead": self.s_dead.value(),
            "sat": self.s_sat.value(),
            "kind": self.kind,
        }

    def restore_custom(self):
        st = getattr(self, "custom_state", None)
        if not st:
            return
        self._guard = True
        self.kind = st["kind"]
        self.slider.setValue(st["g"])
        self.s_dead.setValue(st["dead"])
        self.s_sat.setValue(st["sat"])
        self.s_dead.value_label.setText(self.s_dead.fmt(st["dead"]))
        self.s_sat.value_label.setText(self.s_sat.fmt(st["sat"]))
        self._guard = False
        self.view.points = list(st["points"])
        self.view.dead = st["dead"] / (PEDAL_POINTS - 1)
        self.view.sat = st["sat"] / (PEDAL_POINTS - 1)
        self.view.update()
        self.set_custom_mode()
        self.points_changed(self.view.points)

    def set_custom_mode(self):
        for icon in self.icons:
            if icon.active:
                icon.active = False
                icon.update()
        self.lbl_custom.setStyleSheet(f"color: {theme_col('#e6e8ee', '#1b1b1b').name()}; font-weight: bold;")

    def apply_preset(self, key, kw):
        # Save what is on screen first: without this, one click on a preset
        # erased a curve that took a while to shape.
        if not any(i.active for i in self.icons):
            self.remember_custom()
        self._applying_preset = True
        
        for icon in self.icons:
            icon.active = (icon.key == key)
            icon.update()
        self.lbl_custom.setStyleSheet(f"color: {theme_col('#5c6370', '#8a93a6').name()}; font-weight: normal;")
        
        self.kind = kw.get("kind", "gamma")
        self._guard = True
        self.slider.setValue(int(round(kw.get("g", 1.0) * 100)))
        
        # _guard rather than blockSignals, the same way the gamma slider above
        # is handled. These controls position their handle from their own
        # valueChanged signal, so blocking it moved the value and left the
        # handle behind: the fill underneath jumped back to zero while the
        # knob stayed where it had been dragged. rebuild() already returns
        # early while _guard is set, so nothing is re-entered.
        self.s_dead.setValue(0)
        self.s_dead.value_label.setText(self.s_dead.fmt(0))

        self.s_sat.setValue(PEDAL_POINTS - 1)
        self.s_sat.value_label.setText(self.s_sat.fmt(PEDAL_POINTS - 1))
        
        self._guard = False
        self.val_gamma.setText(f"{kw.get('g', 1.0):.2f}")
        
        if self.kind == "s":
            self.slider.setEnabled(False)
            self.lbl_gamma.setStyleSheet(f"color: {theme_col('#8a93a6', '#5c6370').name()};")
            self.val_gamma.setStyleSheet(f"color: {theme_col('#8a93a6', '#5c6370').name()};")
        else:
            self.slider.setEnabled(True)
            self.lbl_gamma.setStyleSheet("")
            self.val_gamma.setStyleSheet(f"color:{ACCENT}; font-weight:bold;")
            
        self.rebuild(from_preset=True)
        self._applying_preset = False

    def rebuild(self, from_preset=False):
        if getattr(self, '_guard', False): return
        if not from_preset:
            self.set_custom_mode()
            if self.kind == "s":
                self.kind = "gamma"
                self.slider.setEnabled(True)
                self.lbl_gamma.setStyleSheet("")
                self.val_gamma.setStyleSheet(f"color:{ACCENT}; font-weight:bold;")
            
        g = self.slider.value() / 100.0; self.val_gamma.setText(f"{g:.2f}")
        dead = self.s_dead.value() / (PEDAL_POINTS - 1)
        sat = self.s_sat.value() / (PEDAL_POINTS - 1)
        
        if self.kind == "gamma" and dead == 0 and sat == 1.0: 
            self.view.points = pedal_gamma_points(g)
        else: 
            self.view.points = pedal_build_points(g, dead, sat, self.kind)
        
        self.view.dead, self.view.sat = dead, sat; self.view.update()
        
        # Anında uygulama iptal edildi: Değişiklikler yalnızca Profile kaydedilir.
        # Donanıma yalnızca "Apply" butonuna basıldığında işlenir.
        data = {
            "points": list(self.view.points),
            "g": self.slider.value(),
            "dead": self.s_dead.value(),
            "sat": self.s_sat.value(),
            "kind": self.kind
        }
        self.parent_tab.on_pedal_data_updated(self.key, data)

    def points_changed(self, points, from_drag=False):
        if from_drag:
            self.set_custom_mode()
            self.remember_custom()
            g = pedal_fit_gamma(points)
            self._guard = True
            self.slider.setValue(int(round(g * 100)))
            self._guard = False
            self.val_gamma.setText(f"{g:.2f}")
            self.kind = "gamma"
            self.slider.setEnabled(True)
            self.lbl_gamma.setStyleSheet("")
            self.val_gamma.setStyleSheet(f"color:{ACCENT}; font-weight:bold;")
            
        data = {
            "points": list(points),
            "g": self.slider.value(),
            "dead": self.s_dead.value(),
            "sat": self.s_sat.value(),
            "kind": self.kind
        }
            
        self.parent_tab.on_pedal_data_updated(self.key, data)

    def reset_controls(self):
        self.apply_preset("linear", dict(kind="gamma", g=1.00))

    def load_data(self, data):
        self._guard = True
        self.kind = data.get("kind", "gamma")
        g = data.get("g", 100)
        self.slider.setValue(g)
        self.val_gamma.setText(f"{g/100.0:.2f}")
        
        dead = data.get("dead", 0)
        self.s_dead.setValue(dead)
        self.s_dead.value_label.setText(self.s_dead.fmt(dead))
        
        sat = data.get("sat", PEDAL_POINTS - 1)
        self.s_sat.setValue(sat)
        self.s_sat.value_label.setText(self.s_sat.fmt(sat))
        
        if self.kind == "s":
            self.slider.setEnabled(False)
            self.lbl_gamma.setStyleSheet(f"color: {theme_col('#8a93a6', '#5c6370').name()};")
            self.val_gamma.setStyleSheet(f"color: {theme_col('#8a93a6', '#5c6370').name()};")
        else:
            self.slider.setEnabled(True)
            self.lbl_gamma.setStyleSheet("")
            self.val_gamma.setStyleSheet(f"color:{ACCENT}; font-weight:bold;")
            
        self.view.dead = dead / (PEDAL_POINTS - 1)
        self.view.sat = sat / (PEDAL_POINTS - 1)
        loaded_points = list(data.get("points", PEDAL_STOCK))
        self.view.points = loaded_points
        self.view.update()
        self._guard = False
        
        self.set_custom_mode()
        
        active_key = None
        if dead == 0 and sat == PEDAL_POINTS - 1:
            if self.kind == "gamma":
                if g == 100: active_key = "linear"
                elif g == 160: active_key = "soft"
                elif g == 60: active_key = "quick"
            elif self.kind == "s":
                if g == 100: active_key = "s"
                
        if active_key:
            for icon in self.icons:
                if icon.key == active_key:
                    icon.active = True
            if active_key != "custom":
                self.lbl_custom.setStyleSheet(f"color: {theme_col('#5c6370', '#8a93a6').name()}; font-weight: normal;")
                
        for icon in self.icons: icon.update()

    def update_live(self, raw):
        """Smooth the reading before it drives the marker.

        A pedal potentiometer wanders a couple of counts even when nothing is
        touching it, and the marker's position is found by searching the curve
        backwards - so those couple of counts were enough to make it jump
        between neighbouring segments and shiver in place. A large change
        still lands immediately; only the small wobble is filtered.
        """
        target = raw / PEDAL_FULL
        prev = getattr(self.view, "live", target)
        self.view.live = (target if abs(target - prev) > 0.05
                          else prev + (target - prev) * 0.25)
        self.view.update()

    def retranslate(self):
        self.head.setText(tr(self.label_key))
        self.btn_reset.setToolTip(tr("pedals.reset"))
        self.lbl_gamma.setText(tr("pedals.gamma"))
        self.lbl_custom.setText(tr("pedals.custom"))
        self.s_dead._name_lbl.setText(tr(self.s_dead._name_lbl._lbl_key))
        self.s_sat._name_lbl.setText(tr(self.s_sat._name_lbl._lbl_key))

    def restyle(self):
        bg = theme_col('#1b1e26', '#f1f2f5').name()
        bd = theme_col('#262b35', '#d9dce3').name()
        self.setStyleSheet(f"#SinglePedalPage {{ background: {bg}; border-radius: 8px; border: 1px solid {bd}; }}")
        
        p_bg = theme_col('#14161a', '#e4e6e9').name()
        self.preset_bar.setStyleSheet(f"QFrame {{ background: {p_bg}; border-radius: 6px; }}")
        
        _restyle_accent(self.val_gamma, self.s_dead.value_label, self.s_sat.value_label)
        
        if any(ic.active for ic in self.icons):
            self.lbl_custom.setStyleSheet(f"color: {theme_col('#5c6370', '#8a93a6').name()}; font-weight: normal;")
        else:
            self.lbl_custom.setStyleSheet(f"color: {theme_col('#e6e8ee', '#1b1b1b').name()}; font-weight: bold;")
            
        self.view.update()

class PedalsTab(QWidget):
    combine_done = Signal(object, bool)

    def __init__(self, hub):
        super().__init__()
        self.hub = hub
        self.dev = None
        self.report = None
        self.last_vid_pid = None
        # Edits land in memory at once but reach the disk only once they stop.
        # Saving on every change meant a fsync and a file swap for every pixel
        # a curve point was dragged and every step of a slider.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(save_settings)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background:transparent;")
        outer.addWidget(scroll)
        host = QWidget(); host.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(host); lay.setContentsMargins(2, 4, 12, 4); lay.setSpacing(8)
        scroll.setWidget(host)

        self.h_sec = section_header(tr("tab.pedals")); lay.addWidget(self.h_sec); lay.addSpacing(6)

        # Shown when this wheel does not hand over the firmware pedal table: the

        # curves below cannot reach it, and editing them without saying so let

        # people press APPLY and believe it had worked.

        self.lbl_unsup = CaptionLabel(tr("pedals.unsupported"))

        self.lbl_unsup.setWordWrap(True)

        self.lbl_unsup.setVisible(False)

        lay.addWidget(self.lbl_unsup)

        self._fw_state = {}     # pid -> True / False, once decided

        self._fw_fail = {}      # pid -> failed reads so far
        
        self.cols_lay = QHBoxLayout(); self.cols_lay.setSpacing(12)
        self.pages = {}
        for key, lbl, off, idx in PEDALS_DEF:
            pg = SinglePedalPage(key, lbl, off, self)
            self.pages[key] = pg
            self.cols_lay.addWidget(pg)
        lay.addLayout(self.cols_lay, 1)

        # Logitech's "Report Combined Pedals": a setting of the wheel's
        # driver, not of a profile, so it applies at once (one Windows prompt,
        # then the wheel restarts - see combine_pedals_state). Every wheel has
        # it, with a pedal table or without.
        self.cb_combine = CheckBox(tr("pedals.combine"))
        self.cb_combine.setToolTip(tr("pedals.combine_info"))
        self.cb_combine.setVisible(False)
        self.cb_combine.clicked.connect(self._on_combine)
        self.combine_done.connect(self._combine_done)
        self._combine_busy, self._combine_at, self._combine_val = False, 0.0, None
        crow = QHBoxLayout(); crow.addWidget(self.cb_combine); crow.addStretch(1)
        lay.addLayout(crow)

        foot = QHBoxLayout()
        self.lbl_note = CaptionLabel(tr("pedals.note"))
        self.lbl_note.setStyleSheet("color:#8a93a6;")
        foot.addWidget(self.lbl_note, 0, Qt.AlignCenter)
        lay.addLayout(foot)
        
        self.restyle()

    def needed_width(self):
        """Width for three pedal columns side by side, with their spacing."""
        cols = sum(p.minimumSizeHint().width() for p in self.pages.values())
        gaps = self.cols_lay.spacing() * max(0, len(self.pages) - 1)
        # Layout margins, the scrollbar, and a little slack. Sized to the exact
        # figure the columns ask for, the Custom button cleared the panel edge
        # by two pixels - close enough that a different font would clip it
        # again.
        margins = 2 + 12 + 16 + 14
        return cols + gaps + margins

    def get_pedal_profile(self, name):
        prof = global_settings["profiles"].get(name, {})
        pedals_data = prof.get("pedals")
        if not pedals_data:
            return {k: {"points": list(PEDAL_STOCK), "g": 100, "dead": 0, "sat": 7, "kind": "gamma"} for k in ["throttle", "brake", "clutch"]}
        
        for pk in ["throttle", "brake", "clutch"]:
            if pk in pedals_data and isinstance(pedals_data[pk], list):
                pedals_data[pk] = {"points": pedals_data[pk], "g": 100, "dead": 0, "sat": 7, "kind": "gamma"}
            elif pk not in pedals_data:
                pedals_data[pk] = {"points": list(PEDAL_STOCK), "g": 100, "dead": 0, "sat": 7, "kind": "gamma"}
        return pedals_data

    def load_profile(self, name):
        pedals_data = self.get_pedal_profile(name)
        for key, _, _, _ in PEDALS_DEF:
            self.pages[key].load_data(pedals_data[key])
        self._sync_clutch()

    def apply_to_wheel(self, profile_name):
        """Send all three tables in one write.

        This used to loop over the pedals calling write_points, and each call
        re-read the report from the wheel first. Any read that came back
        unusable - the signature check rejecting a report caught mid-update,
        say - made that one pedal silently do nothing while the ones before
        and after it worked. With clutch, brake, throttle in that order it was
        always the brake that vanished. One read, one write, no gap for a
        pedal to fall through.
        """
        if self._fw_supported() is False: return
        if not self._open_if_needed(): return
        report = get_feature_report(self.dev)
        if not report: return
        pedals_data = self.get_pedal_profile(profile_name)
        data = bytearray(report)
        for key, _, off, _ in PEDALS_DEF:
            pts = pedal_monotonic(pedals_data[key]["points"])
            data[off + 1:off + 1 + PEDAL_POINTS] = bytes(pts)
        if set_feature_report(self.dev, bytes(data)):
            self.report = bytes(data)

    def on_pedal_data_updated(self, key, data):
        # Ayarlar anında donanıma yazılmaz, yalnızca JSON profil belleğine kaydedilir.
        # Donanıma işlenmesi için ana penceredeki "APPLY" butonuna basılması gerekir.
        prof_name = global_settings.get("selected_profile", "Global")
        prof = global_settings["profiles"].setdefault(prof_name, {})
        if "pedals" not in prof:
            prof["pedals"] = self.get_pedal_profile(prof_name)
        prof["pedals"][key] = data
        # Restarted on every change, so a drag is one save when it ends. The
        # app saves on exit as well, so a pending write is never lost.
        self._save_timer.start()

    def _sync_clutch(self):
        """Show the clutch curve only on a wheel that has a clutch pedal.

        Same rule as the telemetry panel. The column's minimum width is left
        sized for three pedals, so switching to a three-pedal wheel later can
        never clip the page.
        """
        pg = self.pages.get("clutch")
        if pg is not None:
            pg.setVisible(active_profile.get("pedals", 2) >= 3)

    def _open_if_needed(self):
        if dev is None:
            if self.dev:
                _k32.CloseHandle(self.dev)
                self.dev = None
            return False
        
        current_vid_pid = (VID, active_profile.get("pid_native", 0xC29B))
        if self.dev and self.last_vid_pid != current_vid_pid:
            _k32.CloseHandle(self.dev)
            self.dev = None

        if not self.dev:
            self.dev = open_feature_handle(*current_vid_pid)
            self.last_vid_pid = current_vid_pid
        return bool(self.dev)

    def write_points(self, offset, points, reverse=False):
        """Write one pedal's table, reusing the copy we already hold.

        Re-reading before every write was what let a single bad read drop a
        pedal. The buffer we keep is what was last written, so it is already
        current; it is only fetched from the wheel when we have none.
        """
        if not self._open_if_needed(): return False

        if not self.report:
            self.report = get_feature_report(self.dev)
        if not self.report: return False
        
        data = bytearray(self.report)
        data[offset + 1:offset + 1 + PEDAL_POINTS] = bytes(pedal_monotonic(points, reverse))
        if not set_feature_report(self.dev, bytes(data)): return False
        self.report = bytes(data)
        return True

    def refresh(self):
        # Retry on a timer, not on every frame. When the handle cannot be
        # opened - a wheel without this feature report, say - the previous
        # version rescanned every HID device on the machine sixty times a
        # second.
        # The wheel can change under this tab (plug-in, test device selector),
        # so the clutch page follows it here rather than only on profile load.
        self._sync_clutch()
        self._sync_combine()
        self._show_support(self._fw_supported())
        if not self.dev:
            now = time.monotonic()
            if now - getattr(self, "_last_try", 0.0) < 2.0:
                self._preview()
                return
            self._last_try = now
        self._open_if_needed()
        self._preview()

    def _sync_combine(self):
        """The driver's combined-pedals setting, read from the registry every
        couple of seconds; hidden without Logitech's driver or a wheel."""
        conn = bool(state.get("connected"))
        now = time.monotonic()
        if conn and not self._combine_busy and now - self._combine_at > 2.0:
            self._combine_at = now
            self._combine_val = combine_pedals_state(active_profile)
        show = conn and self._combine_val is not None
        self.cb_combine.setVisible(show)
        if show and not self._combine_busy:
            self.cb_combine.blockSignals(True)
            self.cb_combine.setChecked(bool(self._combine_val))
            self.cb_combine.blockSignals(False)
        self.cb_combine.setEnabled(not self._combine_busy)

    def _on_combine(self, checked):
        if self._combine_busy or self._combine_val is None or bool(checked) == bool(self._combine_val):
            return
        self._combine_busy = True
        self.cb_combine.setEnabled(False)
        prof = active_profile

        def work():
            try:
                code = request_combine_pedals(bool(checked), prof)
            except Exception:
                code = COMBINE_FAILED
            self.combine_done.emit(code, bool(checked))
        threading.Thread(target=work, daemon=True).start()

    def _combine_done(self, code, on):
        self._combine_busy, self._combine_at = False, 0.0
        if code is None:
            show, key = InfoBar.info, "pedals.combine_denied"
        elif code == COMBINE_DONE:
            show, key = InfoBar.success, ("pedals.combine_on" if on else "pedals.combine_off")
        elif code == COMBINE_REPLUG:
            show, key = InfoBar.warning, "pedals.combine_replug"
        else:
            show, key = InfoBar.error, "pedals.combine_failed"
        try:
            show(tr("pedals.combine_title"), tr(key), duration=5000,
                 position=InfoBarPosition.TOP, parent=self.window())
        except Exception:
            pass
        self._sync_combine()

    def _preview(self):
        """Each pedal's live position on its page.

        From the learned or built-in byte where there is one. A wheel read
        from its own descriptor has no byte to name - the poller has already
        put its pedals in state, pressed-high - and before this its preview
        stayed empty while the telemetry moved."""
        t_b, b_b, c_b, inv = effective_layout(active_profile)
        raw = state.get("raw") or []
        from_desc = (saved_map(active_profile) is None and layout_unknown(active_profile)
                     and usage_reader is not None and bool(usage_reader.pedals))
        for key, _, _, _ in PEDALS_DEF:
            if not state.get("connected"):
                self.pages[key].update_live(PEDAL_FULL)
                continue
            if from_desc:
                v = state.get(key)
                self.pages[key].update_live(PEDAL_FULL - int(v) if v is not None else PEDAL_FULL)
                continue
            off = {"throttle": t_b, "brake": b_b, "clutch": c_b}[key]
            self.pages[key].update_live(
                raw[off] if off is not None and off < len(raw) else PEDAL_FULL)

    def _fw_supported(self):
        """Does this wheel hand over the firmware pedal table?

        None while that is not known - no wheel, or not decided yet. A single
        failed read decides nothing: the report can be caught mid-update, and
        a wheel that has the table must not be written off for one bad read.
        Three failures in a row do."""
        if not state.get("connected") or dev is None:
            return None
        if pedal_table_len() is None:
            return False             # its descriptor declares no table at all
        pid = active_profile.get("pid_native", 0)
        if pid in self._fw_state:
            return self._fw_state[pid]
        rep_ = None
        if self._open_if_needed():
            try:
                rep_ = get_feature_report(self.dev)
            except Exception:
                rep_ = None
        if rep_:
            self.report = rep_
            self._fw_state[pid] = True
            return True
        n = self._fw_fail.get(pid, 0) + 1
        self._fw_fail[pid] = n
        if n >= 3:
            self._fw_state[pid] = False
            return False
        return None

    def _show_support(self, ok):
        unsupported = ok is False
        self.lbl_unsup.setVisible(unsupported)
        if unsupported:
            self.lbl_unsup.setStyleSheet(f"color:{theme_col('#b26a00', '#e8c48a').name()};")
        # Disabled is not enough on its own: the pages paint themselves and
        # kept their full colour, so they still looked usable. Faded, they
        # read as what they are.
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        for pg in self.pages.values():
            pg.setEnabled(not unsupported)
            if unsupported:
                eff = pg.graphicsEffect()
                if not isinstance(eff, QGraphicsOpacityEffect):
                    eff = QGraphicsOpacityEffect(pg)
                    pg.setGraphicsEffect(eff)
                eff.setOpacity(0.4)
            elif pg.graphicsEffect() is not None:
                pg.setGraphicsEffect(None)

    def retranslate(self):
        self.h_sec._lbl.setText(tr("tab.pedals"))
        for pg in self.pages.values(): pg.retranslate()
        self.lbl_note.setText(tr("pedals.note"))
        self.lbl_unsup.setText(tr("pedals.unsupported"))
        self.cb_combine.setText(tr("pedals.combine"))
        self.cb_combine.setToolTip(tr("pedals.combine_info"))

    def restyle(self):
        self.h_sec._bar.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")
        for pg in self.pages.values(): pg.restyle()
        self.lbl_note.setStyleSheet(f"color: {theme_col('#8a93a6', '#8a93a6').name()};")



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



# ===========================================================================
#  LUT GENERATOR
# ===========================================================================
#  Measures the wheel and computes a LUT for it. Self-contained: its own
#  maths, its own strings, and one adapter class that drives the wheel
#  through this program's device layer, so every model the hub can read is
#  covered - including those whose steering is read from the HID descriptor.
#
#  Developed and tested standalone (lwhlut.py); the maths below is that file
#  unchanged. Run its checks without the GUI:
#
#      python LegacyWheelHub.py --lutgen-selftest
#
#  If this file is ever split into modules, this block moves out as one piece.
# ===========================================================================

import csv as _csv
import random as _random
import statistics as _stats
from typing import Callable, Dict, List, Optional, Sequence, Tuple

csv = _csv
random = _random
statistics = _stats

# ============================================================================
# Constants
# ============================================================================

VID = 0x046D
FORCE_MAX = 127                  # full scale of a constant-force command
RANGE_DEG = 900                  # rotation range set for the measurement

# Measurement procedure, in physical units so it means the same on every
# model. These set how a trial is carried out - how far counts as "moved",
# how long to wait - not the shape of the result.
BREAK_DEG = 2.75                 # travel that counts as breaking loose
BREAK_WINDOW_S = 0.35            # how long a force is held while waiting
ONSET_DEG = 0.22                 # first movement off the resting angle
PULSE_LONG_DEG = 24.7            # sweep distance (the regime WheelCheck uses)
PULSE_SHORT_DEG = 6.6            # probe's slow distance
PULSE_TIMEOUT_S = 0.9
TRAVEL_LIMIT_DEG = 66.0          # a trial never runs further than this
PARK_DEG = 44.0                  # the off-centre positions thresholds are taken at
SETTLE_S = 0.12

# Precision: more repeats give a better estimate, not a different answer.
THRESHOLD_REPEATS = 2            # per angle and direction
SWEEP_LEVELS = 30
SWEEP_REPEATS = 3                # per level, each is a left and a right pulse
REF_REPEATS = 4                  # heating reference, before and after
PROBE_LEVELS = 10
PROBE_REPEATS = 2

# Time per trial, for the duration estimate. Taken from a real G27 run: twelve
# threshold searches took 3.3 minutes and 276 pulses 5.4 minutes, 8.7 in all.
# The first guesses (1.05 s and 1.10 s) promised 7.
T_BREAK_TRIAL = 1.65
T_PULSE = 1.17

# n default: where RasmusP's G27 LUT clears the friction band of a G27 whose
# lowest threshold measured 24/127. The one value not taken from the wheel;
# see the module docstring.
N_DEFAULT = 0.058

# Probe decision, in units of normalised response.
PROBE_S = 0.06                   # slow response this far below the line: real S
PROBE_SPEED = 0.035              # fast this much more curved than slow: speed


# ============================================================================
# Measurement
# ============================================================================

class Cancelled(Exception):
    pass


Row = Tuple[str, float, str, int, float, str]    # kind, arg, direction, force, value, tag


class Measurer:
    """Runs the measurement stages and reports progress.

    progress(stage, done, total, message) is called after every trial - the
    wizard shows it as it happens. cancel is a threading.Event; setting it
    stops the run at the next trial with the wheel released.
    """

    STAGES = ("thresholds", "sweep", "probe")

    def __init__(self, io, progress: Callable = None,
                 cancel: Optional[threading.Event] = None):
        self.io = io
        self.progress = progress or (lambda *a: None)
        self.cancel = cancel or threading.Event()
        self.rows: List[Row] = []
        self.cpd = io.counts_per_deg
        self._gaps: List[float] = []      # time between HID reports while moving

    # -- helpers -------------------------------------------------------------
    def _check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _counts(self, deg):
        return deg * self.cpd

    def recenter(self):
        self.io.relax()
        self.io.autocenter(True)
        time.sleep(0.6)
        self.io.autocenter(False)
        self.io.relax()
        time.sleep(0.15)

    def park(self, offset_deg: float, direction: str) -> Optional[int]:
        self.recenter()
        if not offset_deg:
            return self.io.angle()
        a0 = self.io.angle()
        if a0 is None:
            return None
        self.io.drain()
        self.io.force(60, direction)
        t0 = time.perf_counter()
        target = self._counts(offset_deg)
        while time.perf_counter() - t0 < 1.5:
            now = self.io.read(5)
            if now is not None and abs(now - a0) >= target:
                break
        self.io.relax()
        time.sleep(0.3)
        return self.io.angle()

    def _moves(self, raw: int, direction: str) -> bool:
        self.io.drain()
        a0 = self.io.angle()
        if a0 is None:
            return False
        self.io.drain()
        self.io.force(raw, direction)
        t0 = time.perf_counter()
        moved = False
        need = self._counts(BREAK_DEG)
        while time.perf_counter() - t0 < BREAK_WINDOW_S:
            now = self.io.read(5)
            if now is not None and abs(now - a0) >= need:
                moved = True
                break
        self.io.relax()
        time.sleep(SETTLE_S)
        return moved

    def breakaway(self, direction: str, park_deg: float, park_dir: str) -> Optional[int]:
        """Lowest force that breaks the wheel loose, from a parked position:
        bisection, then a walk to the edge because trials near it are not
        deterministic."""
        def trial(raw):
            self._check()
            self.park(park_deg, park_dir)
            return self._moves(raw, direction)
        if not trial(FORCE_MAX):
            return None
        lo, hi = 0, FORCE_MAX
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if trial(mid):
                hi = mid
            else:
                lo = mid
        v = hi
        while v > 1 and trial(v - 1):
            v -= 1
        return v

    def pulse(self, raw: int, direction: str, distance_deg: float) -> float:
        """Acceleration proxy: time to cover a distance, timed from the onset
        of motion. 0 if it never got there."""
        self._check()
        self.recenter()
        self.io.drain()
        a0 = self.io.angle()
        if a0 is None:
            return 0.0
        onset, dist = self._counts(ONSET_DEG), self._counts(distance_deg)
        limit = self._counts(TRAVEL_LIMIT_DEG)
        self.io.drain()
        self.io.force(raw, direction)
        t0 = time.perf_counter()
        t_on, elapsed, t_prev = None, None, None
        while time.perf_counter() - t0 < PULSE_TIMEOUT_S:
            now = self.io.read(5)
            if now is None:
                continue
            t_now = time.perf_counter()
            if t_prev is not None and t_on is not None:
                self._gaps.append(t_now - t_prev)
            t_prev = t_now
            d = abs(now - a0)
            if t_on is None and d >= onset:
                t_on = time.perf_counter()
            if t_on is not None and d >= dist:
                elapsed = time.perf_counter() - t_on
                break
            if d > limit:
                break
        self.io.relax()
        time.sleep(SETTLE_S)
        if not elapsed:
            return 0.0
        # 2d/t^2 up to a constant; scaled by distance so the two probe
        # distances are comparable
        return (distance_deg / PULSE_LONG_DEG) / (elapsed * elapsed)

    # -- stages --------------------------------------------------------------
    def plan(self) -> Dict[str, int]:
        return {
            "thresholds": 3 * 2 * THRESHOLD_REPEATS,
            "sweep": SWEEP_LEVELS * SWEEP_REPEATS * 2 + 4 * REF_REPEATS,
            "probe": PROBE_LEVELS * 2 * 2 * PROBE_REPEATS,
        }

    @staticmethod
    def estimate_seconds() -> float:
        # a threshold run is about 10 trials; pulses are one trial each
        return (3 * 2 * THRESHOLD_REPEATS * 10 * T_BREAK_TRIAL
                + (SWEEP_LEVELS * SWEEP_REPEATS * 2 + 4 * REF_REPEATS) * T_PULSE
                + PROBE_LEVELS * 2 * 2 * PROBE_REPEATS * T_PULSE)

    def run(self) -> List[Row]:
        plan = self.plan()
        try:
            self._thresholds(plan["thresholds"])
            self._sweep()
            self._probe(plan["probe"])
        finally:
            self.io.close()
            self.add_meta()
        return self.rows

    def add_meta(self):
        """The HID report interval, measured during the pulses. It sets how
        finely a pulse can be timed, which the heating test needs."""
        if self._gaps and not any(r[0] == "meta" for r in self.rows):
            self.rows.append(("meta", 0.0, "", 0, statistics.median(self._gaps), "report_dt"))

    def _thresholds(self, total):
        places = [(0.0, "left"), (PARK_DEG, "right"), (PARK_DEG, "left")]
        # a positive park moves the wheel one way, the trial pushes the other
        done = 0
        for rep in range(THRESHOLD_REPEATS):
            for park_deg, park_dir in places:
                for direction in ("left", "right"):
                    pd = park_dir if park_deg else direction
                    # park on the side the trial pushes away from
                    if park_deg:
                        pd = "right" if direction == "left" else "left"
                    # Messages carry no words: the app shows them as they are,
                    # in whatever language its interface is in.
                    # Where the wheel waits and which way it is pushed. The
                    # label used to show only the push, and the move you
                    # actually see - parking 44° to the opposite side, at a
                    # force of 60 - made "L" look like the wheel going right.
                    side = "" if not park_deg else ("R " if pd == "right" else "L ")
                    arrow = "←" if direction == "left" else "→"
                    where = f"{side}{park_deg:.0f}°  {arrow}"
                    self.progress("thresholds", done, total, f"{where}  ...")
                    v = self.breakaway(direction, park_deg, pd)
                    done += 1
                    if v is not None:
                        self.rows.append(("threshold", park_deg, direction, 0, float(v), str(rep)))
                    self.progress("thresholds", done, total,
                                  f"{where}   =  {v if v is not None else '-'} / {FORCE_MAX}")

    def _sweep(self):
        th = [r[4] for r in self.rows if r[0] == "threshold"]
        if not th:
            raise RuntimeError("No threshold could be measured - the wheel never moved.")
        start = int(max(th))
        levels = sweep_levels(start, SWEEP_LEVELS)
        ref = levels[len(levels) * 2 // 3]
        total = len(levels) * SWEEP_REPEATS * 2 + 4 * REF_REPEATS
        done = 0

        def ref_block(tag):
            nonlocal done
            for rep in range(REF_REPEATS):
                for d in ("left", "right"):
                    self.rows.append((tag, PULSE_LONG_DEG, d, ref,
                                      self.pulse(ref, d, PULSE_LONG_DEG), str(rep)))
                    done += 1
                    self.progress("sweep", done, total, f"ref  {ref} / {FORCE_MAX}")
        ref_block("ref_before")
        # Mixed order, not low to high. Swept upwards, the motor warms as the
        # forces rise and every high force reads weak - a bias in the shape.
        # Shuffled, the same warming is spread over all levels as scatter,
        # which the fit averages out. The seed makes a run repeatable.
        plan = [(f, rep, d) for f in levels for rep in range(SWEEP_REPEATS)
                for d in ("left", "right")]
        random.Random(len(levels) * 1009 + start).shuffle(plan)
        for f, rep, d in plan:
            a = self.pulse(f, d, PULSE_LONG_DEG)
            self.rows.append(("accel", PULSE_LONG_DEG, d, f, a, str(rep)))
            done += 1
            self.progress("sweep", done, total, f"{f} / {FORCE_MAX}")
        ref_block("ref_after")

    def _probe(self, total):
        th = [r[4] for r in self.rows if r[0] == "threshold"]
        start = int(max(th)) + 4
        forces = sorted({int(round(start + (FORCE_MAX - start) * i / (PROBE_LEVELS - 1)))
                         for i in range(PROBE_LEVELS)})
        done = 0
        for f in forces:
            for dist in (PULSE_SHORT_DEG, PULSE_LONG_DEG):
                for rep in range(PROBE_REPEATS):
                    for d in ("left", "right"):
                        self.rows.append(("probe", dist, d, f, self.pulse(f, d, dist), str(rep)))
                        done += 1
                        self.progress("probe", done, total,
                                      f"{f} / {FORCE_MAX}   {dist:.0f}°")


def sweep_levels(start: int, steps: int) -> List[int]:
    """Dense just above the threshold, where the response changes most."""
    start = max(1, int(start))
    span = FORCE_MAX - start
    knee = start + 0.30 * span
    n_low = max(2, steps // 2)
    n_high = max(2, steps - n_low)
    pts = [start + (knee - start) * i / (n_low - 1) for i in range(n_low)]
    pts += [knee + (FORCE_MAX - knee) * i / n_high for i in range(1, n_high + 1)]
    return sorted({int(round(p)) for p in pts} | {FORCE_MAX})


# ============================================================================
# Analysis (pure: rows in, numbers out)
# ============================================================================

def thresholds_of(rows: Sequence[Row]) -> List[float]:
    return [r[4] for r in rows if r[0] == "threshold"]


def drift_of(rows: Sequence[Row]) -> float:
    b = [r[4] for r in rows if r[0] == "ref_before" and r[4] > 0]
    a = [r[4] for r in rows if r[0] == "ref_after" and r[4] > 0]
    return statistics.mean(a) / statistics.mean(b) if a and b else 1.0


def drift_is_real(rows: Sequence[Row]) -> Tuple[bool, float, float]:
    """Is the change in the reference torque bigger than its uncertainty?

    A pulse is timed from one HID report to another, so its time is only
    known to about one report interval. The proxy goes as 1/t^2, so that
    becomes a relative error of 2*dt/(sqrt(12)*t) - and on a simulated wheel
    it produced a 6% "heating" out of nothing: every reference landed on one
    of two times 2.5 ms apart. Each group's mean is therefore given at least
    that uncertainty, and the correction is applied only when the change
    clears three standard errors. The scatter is taken within each group:
    pooled, the change itself counted as noise.
    """
    b = [r[4] for r in rows if r[0] == "ref_before" and r[4] > 0]
    a = [r[4] for r in rows if r[0] == "ref_after" and r[4] > 0]
    if len(a) < 2 or len(b) < 2:
        return False, 1.0, 0.0
    mb, ma = statistics.mean(b), statistics.mean(a)
    drift = ma / mb
    # The noise of one reading, from the scatter WITHIN each group. Before and
    # after used to be pooled into one list, so the drift being tested counted
    # as noise too: a real loss made its own error bar bigger, and with a few
    # pulses missing it could never clear three standard errors.
    rel_b, rel_a = statistics.pstdev(b) / mb, statistics.pstdev(a) / ma
    rel_sd = math.sqrt((len(b) * rel_b ** 2 + len(a) * rel_a ** 2) / (len(a) + len(b)))
    se = rel_sd * math.sqrt(1 / len(a) + 1 / len(b))
    dt = next((r[4] for r in rows if r[0] == "meta" and r[5] == "report_dt"), None)
    if dt:
        # Timing to the nearest report does not average out: every pulse of
        # a group can land on the same side of a step. So the step's
        # uncertainty applies to each group's mean as a whole, not per pulse.
        t = 1.0 / math.sqrt(statistics.mean(a + b))
        se = max(se, (2 * dt / t) * math.sqrt(2 / 12))
    return abs(drift - 1.0) > 3 * se, drift, 3 * se


def torque_points(rows: Sequence[Row]) -> List[Tuple[int, float, float]]:
    """Median torque per force, left and right averaged, heating removed if
    the heating is real (see drift_is_real). The sweep runs low to high, so
    each pulse is scaled by the drift expected at its place in the sequence."""
    correct, drift, _ = drift_is_real(rows)
    acc = [r for r in rows if r[0] == "accel"]
    n = max(1, len(acc) - 1)
    by = {}
    for k, r in enumerate(acc):
        v = r[4]
        if v <= 0:
            continue
        if correct:
            v /= 1.0 + (drift - 1.0) * k / n
        by.setdefault(r[3], {}).setdefault(r[5], {})[r[2]] = v
    out = []
    for f in sorted(by):
        # Left and right averaged only where both arrived: a pulse that
        # failed in one direction left the other alone, and the wheel's
        # left/right difference then bent the curve at that level.
        reps = list(by[f].values())
        pairs = [statistics.mean(d.values()) for d in reps if len(d) == 2]
        if not pairs:
            pairs = [statistics.mean(d.values()) for d in reps]
        se = statistics.pstdev(pairs) / math.sqrt(len(pairs)) if len(pairs) > 1 else 0.0
        out.append((f, statistics.median(pairs), se))
    return out


def monotone_smooth(forces: Sequence[float], vals: Sequence[float],
                    errs: Sequence[float]):
    """The torque curve through the measured levels, smoothed only as much as
    the measurement's own noise justifies - and judged locally, because the
    noise is not the same everywhere.

    At each level a line is fitted to its k nearest neighbours (weighted by
    distance), with k grown for as long as that line still passes within the
    noise of every neighbour it uses. Where the measurement is clean, k stays
    small and the curve follows the points; where it is noisy, k grows and
    the curve is smoothed. A real bend in a clean region survives; a wiggle
    in a noisy one does not.

    History, because both earlier versions failed on real data:
      * A single cubic missed a real G27's points by four times their noise,
        erring in long one-sided runs, and went negative just above the
        threshold.
      * One smoothing width for the whole curve, chosen from the average
        noise, followed the clean lower region well but copied the noise of
        the upper region, where repeats scattered three to five times more:
        the LUT picked up alternating corners between 0.5 and 0.66 of input.

    The result is made non-decreasing (pool-adjacent-violators) and joined by
    a monotone cubic, so it can be inverted.
    """
    F, Y = list(forces), list(vals)
    # A level's noise estimated from its own three repeats is itself very
    # unreliable - it sometimes shows half the real scatter, the level looks
    # cleaner than it is, and the curve follows its noise. Noise changes
    # slowly from level to level, so each estimate is pooled with its
    # neighbours (two either side). Without this the fit rippled after a
    # sharp bend, and the inversion turned the ripple into slope jumps of
    # four times in the LUT.
    raw = list(errs)
    E = []
    for i in range(len(raw)):
        win = raw[max(0, i - 2):i + 3]
        E.append(math.sqrt(sum(e * e for e in win) / len(win)))
    pos = sorted(e for e in E if e > 0)
    floor = 0.5 * pos[len(pos) // 2] if pos else 0.0      # a level can't be known better than
    E = [max(e, floor) for e in E]                         # half the typical noise

    def line_at(f0, k):
        idx = sorted(range(len(F)), key=lambda j: abs(F[j] - f0))[:k]
        h = max(abs(F[j] - f0) for j in idx) * 1.0001 or 1.0
        w = [(1 - (abs(F[j] - f0) / h) ** 3) ** 3 for j in idx]
        sw = sum(w)
        mx = sum(wi * F[j] for wi, j in zip(w, idx)) / sw
        my = sum(wi * Y[j] for wi, j in zip(w, idx)) / sw
        sxx = sum(wi * (F[j] - mx) ** 2 for wi, j in zip(w, idx))
        b = sum(wi * (F[j] - mx) * (Y[j] - my) for wi, j in zip(w, idx)) / sxx if sxx else 0.0
        return idx, (lambda x: my + b * (x - mx))

    # The narrowest feature a sweep can tell apart from a single bad level is
    # one backed by two consistent levels on each side, so no level is ever
    # fitted from fewer than five. With three, a jump between two adjacent
    # levels on a real G27 - measured back to back, the kind of step a single
    # disturbed trial leaves - was copied into the LUT as a sharp corner.
    k_min = min(5, len(F))
    fitted, ks = [], []
    for i, f0 in enumerate(F):
        best_k, best_v = k_min, line_at(f0, k_min)[1](f0)
        for k in range(k_min + 1, len(F) + 1):
            idx, g = line_at(f0, k)
            # normalised misfit of the neighbours this line is built from
            chi = math.sqrt(sum(((Y[j] - g(F[j])) / E[j]) ** 2 for j in idx) / len(idx)) if floor else 0.0
            if floor and chi > 2.0:
                break
            best_k, best_v = k, g(f0)
        fitted.append(best_v)
        ks.append(best_k)

    bl = []
    for v in fitted:
        bl.append([v, 1])
        while len(bl) > 1 and bl[-2][0] / bl[-2][1] > bl[-1][0] / bl[-1][1]:
            a, c = bl.pop()
            bl[-1][0] += a
            bl[-1][1] += c
    best = []
    for v, c in bl:
        best += [v / c] * c
    for i in range(1, len(best)):
        if best[i] <= best[i - 1]:
            best[i] = best[i - 1] + 1e-9 * (abs(best[-1]) + 1)
    f = pchip(F, best)
    lo, hi = F[0], F[-1]
    k_desc = f"{min(ks)}..{max(ks)}"
    return (lambda x: f(min(hi, max(lo, x)))), k_desc


def smooth_fit(forces: Sequence[float], vals: Sequence[float], max_degree: int = 3):
    """Least-squares polynomial, highest degree that stays increasing over the
    measured range. Interpolating noisy points and inverting them turns
    small disagreements into walls; a regression cannot be bent by one bad
    point. A cubic can still carry an S."""
    lo, hi = min(forces), max(forces)
    span = (hi - lo) or 1.0
    us = [(f - lo) / span for f in forces]

    def solve(deg):
        n = deg + 1
        A = [[sum(u ** (i + j) for u in us) for j in range(n)] for i in range(n)]
        b = [sum(v * u ** i for u, v in zip(us, vals)) for i in range(n)]
        for c in range(n):
            p = max(range(c, n), key=lambda r: abs(A[r][c]))
            if abs(A[p][c]) < 1e-15:
                return None
            A[c], A[p] = A[p], A[c]
            b[c], b[p] = b[p], b[c]
            for r in range(c + 1, n):
                k = A[r][c] / A[c][c]
                for cc in range(c, n):
                    A[r][cc] -= k * A[c][cc]
                b[r] -= k * b[c]
        coef = [0.0] * n
        for r in range(n - 1, -1, -1):
            coef[r] = (b[r] - sum(A[r][cc] * coef[cc] for cc in range(r + 1, n))) / A[r][r]
        return coef

    deg = min(max_degree, len(forces) - 1)
    coef = solve(deg)
    if coef is None:
        return None, 0
    raw = lambda F, c=coef: sum(ci * ((F - lo) / span) ** i for i, ci in enumerate(c))
    # If the cubic dips anywhere, flatten only the dip - pool-adjacent-violators
    # on a fine grid - rather than dropping to a lower degree. An earlier
    # version fell back to a straight line whenever the cubic dipped, which
    # silently turned the "S" LUT into a copy of the straight one.
    K = 400
    gx = [lo + span * k / K for k in range(K + 1)]
    gy = [raw(x) for x in gx]
    blocks = []
    for y in gy:
        blocks.append([y, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            v, c = blocks.pop()
            blocks[-1][0] += v
            blocks[-1][1] += c
    my = []
    for v, c in blocks:
        my += [v / c] * c
    # strictly increasing for the interpolant: nudge flat runs by a hair
    for i in range(1, len(my)):
        if my[i] <= my[i - 1]:
            my[i] = my[i - 1] + 1e-9 * (abs(my[-1]) + 1)
    f = pchip(gx, my)
    return (lambda F: f(min(hi, max(lo, F)))), deg


def probe_verdict(rows: Sequence[Row], threshold: float) -> Dict:
    """Is the measured shape the wheel's own, or an artefact of speed?

    Decided from two signatures. Speed losses flatten the top, which lifts the
    normalised lower part ABOVE the straight line; a real S pulls it BELOW,
    something speed losses cannot do. And speed losses grow with speed, so
    the fast measurement comes out clearly more curved than the slow one.
    """
    res = {}
    for r in rows:
        if r[0] == "probe" and r[4] > 0:
            res.setdefault(r[1], {}).setdefault(r[3], []).append(r[4])
    if len(res) < 2:
        return {"verdict": "unknown"}
    short_d, long_d = min(res), max(res)

    def shape(d):
        pts = sorted((f, statistics.median(v)) for f, v in res[d].items())
        top = pts[-1][1] or 1.0
        return [(f, a / top, (f - threshold) / (FORCE_MAX - threshold)) for f, a in pts]
    s, l = shape(short_d), shape(long_d)
    dev_s = statistics.mean(abs(n - x) for _, n, x in s)
    dev_l = statistics.mean(abs(n - x) for _, n, x in l)
    third = max(2, len(s) // 3)
    low = statistics.median([n - x for _, n, x in s[:third]])
    speed = (dev_l - dev_s) >= PROBE_SPEED
    if low < -PROBE_S or not speed:
        verdict, body = "measured", 1.0
    else:
        verdict, body = "speed", 0.0
    return {"verdict": verdict, "body": body, "dev_slow": dev_s, "dev_fast": dev_l,
            "low": low, "speed": speed, "slow": s, "fast": l}


# ============================================================================
# Curve model
# ============================================================================

def pchip(xs: Sequence[float], ys: Sequence[float]):
    """Monotone cubic (Fritsch-Carlson): cannot overshoot, cannot dip."""
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def end(d0, d1, h0, h1):
        v = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if v * d0 <= 0:
            return 0.0
        return 3 * d0 if abs(v) > 3 * abs(d0) else v
    if n > 2:
        m[0], m[-1] = end(d[0], d[1], h[0], h[1]), end(d[-1], d[-2], h[-1], h[-2])
    else:
        m[0] = m[-1] = d[0]

    def f(x):
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        lo, hi = 0, n - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        hh = xs[hi] - xs[lo]
        t = (x - xs[lo]) / hh
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[lo] + (t3 - 2 * t2 + t) * hh * m[lo]
                + (-2 * t3 + 3 * t2) * ys[hi] + (t3 - t2) * hh * m[hi])
    return f


def hermite(x0, y0, m0, x1, y1, m1):
    """Cubic from (x0,y0) leaving at slope m0 to (x1,y1) arriving at m1. The
    end slopes are held to three times the secant, which keeps it monotone
    (Fritsch-Carlson)."""
    h = x1 - x0
    sec = (y1 - y0) / h
    m0, m1 = min(m0, 3 * sec), min(m1, 3 * sec)

    def f(x):
        t = (x - x0) / h
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + t) * h * m0
                + (-2 * t3 + 3 * t2) * y1 + (t3 - t2) * h * m1)
    return f


class CurveModel:
    """Everything the curve needs, from one measurement.

    Build once, then call build(n, body) as often as the sliders move - it
    does no measuring, only arithmetic.
    """

    GRID = 20                     # sub-steps per force unit for the response

    def __init__(self, thresholds: Sequence[float], torque: Sequence[Tuple[int, float, float]]):
        if len(thresholds) < 2:
            raise ValueError("at least two threshold measurements are needed")
        if len(torque) < 4:
            raise ValueError("the torque sweep has too few usable levels")
        # A measured threshold t means force t moved the wheel and t-1 did not,
        # so the true value lies somewhere in (t-1, t]. Each measurement is
        # spread over that interval - not around t, which would claim the
        # wheel might need more than t.
        self.samples = [t - 1 + k / 20 for t in thresholds for k in range(1, 21)]
        self.lo = min(self.samples) / FORCE_MAX
        self.hi = max(self.samples) / FORCE_MAX
        # How well the lowest threshold is known: the scatter between
        # measurements, but never less than half the step they were taken at.
        # A wheel that returned the same number twelve times still only
        # locates its threshold to within that step.
        self.margin = max(statistics.pstdev(thresholds), 0.5) / FORCE_MAX
        # the highest start that cannot move a still wheel by itself
        self.floor = max(0.0, self.lo - self.margin)

        fit, self.fit_k = monotone_smooth([t[0] for t in torque], [t[1] for t in torque],
                                          [t[2] for t in torque])
        f0 = torque[0][0]
        k0 = max(1e-9, fit(f0 + 1) - fit(f0))
        self.A = lambda F: fit(F) if F >= f0 else fit(f0) - k0 * (f0 - F)
        k_lin = (fit(FORCE_MAX) - fit(f0)) / (FORCE_MAX - f0)
        self.A_lin = lambda F: k_lin * F

        self.Fs = [i / self.GRID for i in range(FORCE_MAX * self.GRID + 1)]
        self._R = {"S": self._expected(self.A), "L": self._expected(self.A_lin)}
        self.n_min, self.n_max = self._n_range()

    # expected torque of a wheel whose breakaway point varies like the samples
    def _expected(self, A):
        As = [A(t) for t in self.samples]
        out = []
        for F in self.Fs:
            aF = A(F)
            out.append(sum(max(0.0, aF - at) for t, at in zip(self.samples, As) if F > t)
                       / len(self.samples))
        return out

    def _inverse(self, key, frac):
        R = self._R[key]
        target = frac * R[-1]
        lo, hi = 0, len(R) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if R[mid] >= target and R[mid] > 0:
                hi = mid
            else:
                lo = mid
        r0, r1 = R[lo], R[hi]
        F = self.Fs[lo] + ((target - r0) / (r1 - r0) if r1 > r0 else 1.0) / self.GRID
        return F / FORCE_MAX

    def _above(self, key, n):
        return lambda x: self._inverse(key, (x - n) / (1 - n))

    def _reach(self, f, y, start):
        a, b = start, 1.0
        for _ in range(40):
            m = (a + b) / 2
            if f(m) >= y:
                b = m
            else:
                a = m
        return b

    def _pedestal(self, n):
        aL = self._above("L", n)
        x_hi = self._reach(aL, self.hi, n)
        m_band = (self.hi - self.lo) / max(1e-9, x_hi - n)
        return max(0.0, self.lo - m_band * n)

    def _n_range(self):
        # the lively end: where the start would come within the lowest
        # threshold's uncertainty; the calm end: the threshold itself, where
        # the curve is the wheel with no LUT at all
        # The limit is the lowest threshold minus its uncertainty. On a wheel
        # whose thresholds scatter more than the lowest one is high, that fell
        # below zero and no setting - so no LUT - could ever pass; a start of
        # zero cannot move a still wheel, so the limit stops there.
        a, b = 0.0, self.lo
        for _ in range(40):
            m = (a + b) / 2
            if self._pedestal(m) > self.floor:
                a = m
            else:
                b = m
        return b, self.lo

    def build(self, n: float = N_DEFAULT, body: float = 1.0,
              points: int = 1001) -> List[Tuple[float, float]]:
        """The LUT for centre setting n and body mix (0 straight .. 1 measured)."""
        n = min(self.n_max, max(self.n_min, n))
        body = min(1.0, max(0.0, body))
        aL, aS = self._above("L", n), self._above("S", n)
        x_hi = self._reach(aL, self.hi, n)
        m_band = (self.hi - self.lo) / (x_hi - n)
        ped = max(0.0, self.lo - m_band * n)
        m_pre = (self.lo - ped) / n
        # shared start: below the threshold, then across the spread
        start = hermite(n, self.lo, m_pre, x_hi, self.hi, m_band)

        # bodies, each anchored to leave the spread from the same point
        xs = self._reach(aS, self.hi, n)
        bodies = {
            "L": aL,
            "S": lambda x: aS(xs + (x - x_hi) * (1 - xs) / (1 - x_hi)),
        }

        def body_curve(B):
            # Hand over from the spread to the body over a length that grows
            # with how much the slope has to change, so the bend's sharpness
            # stays about the same whatever the two slopes are. With a fixed
            # length, a narrow spread (a wheel whose threshold barely varies)
            # squeezed the whole change into a few thousandths of input and
            # left a kink.
            w = x_hi - n
            m_b = (B(min(1.0, x_hi + 2 * w)) - B(x_hi + w)) / w
            ratio = max(m_band, m_b) / max(1e-6, min(m_band, m_b))
            x_h = min(0.9, x_hi + w * max(1.0, ratio))
            m_h = (B(min(1.0, x_h + 0.002)) - B(x_h - 0.002)) / 0.004
            join = hermite(x_hi, self.hi, m_band, x_h, B(x_h), m_h)

            def f(x):
                if x < n:
                    return ped + m_pre * x
                if x < x_hi:
                    return start(x)
                if x < x_h:
                    return join(x)
                return B(x)
            return f
        fL, fS = body_curve(bodies["L"]), body_curve(bodies["S"])
        out, prev = [], 0.0
        for i in range(points):
            x = i / (points - 1)
            y = (1 - body) * fL(x) + body * fS(x)
            y = max(prev, min(1.0, max(0.0, y)))
            prev = y
            out.append((x, y))
        out[-1] = (1.0, 1.0)
        return out


def validate(curve: Sequence[Tuple[float, float]], model: CurveModel) -> List[str]:
    """Checks over the WHOLE curve, the start included. An earlier checker
    skipped the first few percent and so missed a steep spot right where the
    wheel breaks loose."""
    issues = []
    ys = [y for _, y in curve]
    if any(ys[i + 1] < ys[i] - 1e-12 for i in range(len(ys) - 1)):
        issues.append("not monotonic")
    if ys[0] > model.floor + 1e-9:
        issues.append(f"start {ys[0]:.3f} could move a still wheel on its own")
    if curve[-1] != (1.0, 1.0):
        issues.append("does not end at full force")
    dx = curve[1][0] - curve[0][0]
    sl = [(ys[i] - ys[i - 1]) / dx for i in range(1, len(ys))]
    for i in range(1, len(sl)):
        big = max(sl[i], sl[i - 1])
        if abs(sl[i] - sl[i - 1]) > 0.5 * big and abs(sl[i] - sl[i - 1]) > 0.5:
            issues.append(f"kink at {curve[i][0]:.3f}: slope {sl[i-1]:.2f} -> {sl[i]:.2f}")
            break
    return issues


# ============================================================================
# Files
# ============================================================================

def save_csv(path: str, rows: Sequence[Row]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "arg", "direction", "force", "value", "tag"])
        w.writerows(rows)


def load_csv(path: str) -> List[Row]:
    """This tool's CSV, or the files lwhlut3 wrote (lwh_measurement.csv and
    lwh_probe.csv), so earlier measurements can be rebuilt without measuring
    again. lwhlut3 stored distances in G27 counts at 900 degrees."""
    out = []
    with open(path, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if "arg" in (rd.fieldnames or []):
            for r in rd:
                out.append((r["kind"], float(r["arg"]), r["direction"], int(float(r["force"])),
                            float(r["value"]), r["tag"]))
            return out
        cpd = 16384 / 900
        for r in rd:
            kind, off = r["kind"], float(r["offset"])
            f_, v, tag = int(float(r["force"])), float(r["value"]), r["repeat"]
            if kind == "deadzone":
                out.append(("threshold", abs(off) / cpd, r["direction"], 0, v, tag))
            elif kind in ("accel", "ref_before", "ref_after"):
                out.append((kind, PULSE_LONG_DEG, r["direction"], f_, v, tag))
            elif kind == "probe":
                d = PULSE_SHORT_DEG if off < 300 else PULSE_LONG_DEG
                out.append(("probe", d, r["direction"], f_, v, tag))
    return out


def write_lut(path: str, curve: Sequence[Tuple[float, float]], meta: Dict[str, str]):
    with open(path, "w", encoding="utf-8") as f:
        for k, v in meta.items():
            f.write(f"; {k}: {v}\n")
        for x, y in curve:
            f.write(f"{x:.4f}|{y:.4f}\n")


def read_lut(path: str) -> List[Tuple[float, float]]:
    pts = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip().replace(",", ".")
            if not line or line.startswith((";", "#")) or "|" not in line:
                continue
            a, b = line.split("|")[:2]
            try:
                pts.append((float(a), float(b)))
            except ValueError:
                pass
    return sorted(pts)


def model_from_rows(rows: Sequence[Row]) -> CurveModel:
    return CurveModel(thresholds_of(rows), torque_points(rows))


def produce(rows: Sequence[Row], prefix: str, n: Optional[float] = None,
            body: Optional[float] = None, quiet: bool = False) -> Dict:
    """Both LUTs (and a mixed one if a body mix is given) from measured rows."""
    model = model_from_rows(rows)
    th = thresholds_of(rows)
    verdict = probe_verdict(rows, statistics.median(th))
    n_use = N_DEFAULT if n is None else n
    n_use = min(model.n_max, max(model.n_min, n_use))
    outputs = {}
    variants = [("duz", 0.0)]
    if verdict.get("verdict") != "speed":
        variants.insert(0, ("S", 1.0))
    if body is not None and 0.0 < body < 1.0 and verdict.get("verdict") != "speed":
        variants.append((f"mix{int(round(body * 100)):02d}", body))
    for tag, b in variants:
        curve = model.build(n_use, b)
        issues = validate(curve, model)
        path = f"{prefix}_{tag}.lut"
        if issues:
            # an invalid LUT is never written; the report says why
            outputs[tag] = {"path": path, "curve": curve, "issues": issues, "written": False}
            continue
        write_lut(path, curve, {
            "LWH LUT Generator": "measured",
            "body": f"{b:.2f} (0 straight, 1 measured)",
            "n": f"{n_use:.4f} (range {model.n_min:.4f}..{model.n_max:.4f})",
            "thresholds": f"{min(th):.0f}..{max(th):.0f} /127 over {len(th)} measurements",
            "probe": verdict.get("verdict", "unknown"),
        })
        outputs[tag] = {"path": path, "curve": curve, "issues": issues, "written": True}
    if not quiet:
        report(model, verdict, outputs, n_use, rows)
    return {"model": model, "verdict": verdict, "outputs": outputs, "n": n_use}


def report(model, verdict, outputs, n, rows):
    th = thresholds_of(rows)
    print(f"\n  thresholds    {min(th):.0f} .. {max(th):.0f} /127   median "
          f"{statistics.median(th):.1f}   over {len(th)} measurements")
    real, d, lim = drift_is_real(rows)
    print(f"  heating       reference torque {100 * (d - 1):+.1f}% over the sweep "
          f"({'corrected' if real else f'within the measurement uncertainty of +-{100*lim:.1f}%, left alone'})")
    print(f"  torque fit    each level smoothed over {model.fit_k} neighbours "
          f"(chosen locally by the measurement's own noise)")
    v = verdict.get("verdict")
    if v == "measured":
        print(f"  probe         the shape is the wheel's own "
              f"(slow {100*verdict['dev_slow']:.1f}% / fast {100*verdict['dev_fast']:.1f}% "
              f"off straight) - the S version is the linearising one")
    elif v == "speed":
        print(f"  probe         the shape comes from speed "
              f"(fast {100*verdict['dev_fast']:.1f}% vs slow {100*verdict['dev_slow']:.1f}%) "
              f"- the straight version is recommended")
    print(f"  centre n      {n:.4f}   (allowed {model.n_min:.4f} .. {model.n_max:.4f})")
    if v == "speed":
        print("\n  No S version: its shape would be the inverse of a speed artefact, not of "
              "the wheel.")
    for tag, o in outputs.items():
        c = o["curve"]
        at = lambda x: next(y for xx, y in c if xx >= x - 1e-9)
        if not o["written"]:
            print(f"\n  {o['path']}   NOT WRITTEN: {'; '.join(o['issues'])}")
            continue
        print(f"\n  {o['path']}   [ok]")
        print("    " + "  ".join(f"y({x:g})={at(x):.3f}" for x in (0, 0.05, 0.1, 0.2, 0.5, 0.8)))


# ============================================================================
# Self-test (no hardware)
# ============================================================================

def synthetic_rows(shape: str = "linear", seed: int = 1) -> List[Row]:
    """Rows as a measurement would produce them, from a known wheel."""
    rnd = random.Random(seed)
    th = [24, 25, 25, 26, 26, 28, 24, 25, 26, 27, 25, 26]
    rows = [("threshold", 0.0, "left", 0, float(t), "0") for t in th]

    def torque(F):
        u = max(0.0, (F - 25) / (FORCE_MAX - 25))
        if shape == "plateau":
            kn = [(0, 0), (0.3, 0.5), (0.75, 0.7), (1, 1)]
            for (u0, g0), (u1, g1) in zip(kn, kn[1:]):
                if u <= u1:
                    return g0 + (g1 - g0) * (u - u0) / (u1 - u0)
        return u
    for tag in ("ref_before",):
        for _ in range(REF_REPEATS * 2):
            rows.append((tag, PULSE_LONG_DEG, "left", 90, 100 * torque(90), "0"))
    for f in sweep_levels(28, SWEEP_LEVELS):
        for rep in range(SWEEP_REPEATS):
            for d in ("left", "right"):
                rows.append(("accel", PULSE_LONG_DEG, d, f,
                             100 * torque(f) * rnd.uniform(0.95, 1.05), str(rep)))
    for _ in range(REF_REPEATS * 2):
        rows.append(("ref_after", PULSE_LONG_DEG, "left", 90, 100 * torque(90), "0"))
    return rows


def selftest() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(f"  {'pass' if cond else 'FAIL'}  {name}")
        ok = ok and cond
    for shape in ("linear", "plateau"):
        rows = synthetic_rows(shape)
        model = model_from_rows(rows)
        check(f"[{shape}] n range computed ({model.n_min:.4f}..{model.n_max:.4f})",
              0 < model.n_min < model.n_max < 0.3)
        worst = []
        for k in range(41):
            n = model.n_min + (model.n_max - model.n_min) * k / 40
            for body in (0.0, 0.5, 1.0):
                c = model.build(n, body)
                worst += [(n, body, i) for i in validate(c, model)]
        check(f"[{shape}] every slider position valid (41 n x 3 body)", not worst)
        if worst:
            print("       first:", worst[0])
        c = model.build(N_DEFAULT, 0.0)
        steep = max((c[i][1] - c[i - 1][1]) / (c[i][0] - c[i - 1][0]) for i in range(1, 300))
        check(f"[{shape}] no steep spot in the start (max slope {steep:.2f})", steep < 3.0)
    same = [r for r in synthetic_rows("linear") if r[0] != "threshold"]
    same += [("threshold", 0.0, "left", 0, 25.0, "0")] * 12
    mz = model_from_rows(same)
    check(f"zero-spread wheel still keeps a margin (n_min {mz.n_min:.4f}, start "
          f"{mz.build(mz.n_min, 0.0)[0][1]*127:.2f}/127 < true threshold 24)",
          mz.n_min > 0 and mz.build(mz.n_min, 0.0)[0][1] * FORCE_MAX < 24.0)
    # a narrow spread (the threshold barely varies) must not leave a kink
    narrow = [r for r in synthetic_rows("plateau") if r[0] != "threshold"]
    narrow += [("threshold", 0.0, "left", 0, 30.0, "0")] * 12
    mn = model_from_rows(narrow)
    bad = [(round(n, 3), b, i) for k in range(21)
           for n in [mn.n_min + (mn.n_max - mn.n_min) * k / 20]
           for b in (0.0, 1.0) for i in validate(mn.build(n, b), mn)]
    check("narrow threshold spread: no kink at any slider position", not bad)
    if bad:
        print("       first:", bad[0])
    # a torque curve whose cubic dips must keep its shape, not turn straight
    sat = [r for r in synthetic_rows("linear") if r[0] not in ("accel",)]
    for f in sweep_levels(28, SWEEP_LEVELS):
        u = (f - 25) / 102
        sat.append(("accel", PULSE_LONG_DEG, "left", f, 100 * (1 - math.exp(-4 * u)), "0"))
    ms = model_from_rows(sat)
    cS, cL = ms.build(N_DEFAULT, 1.0), ms.build(N_DEFAULT, 0.0)
    gap = max(abs(a[1] - b[1]) for a, b in zip(cS, cL))
    check(f"saturating torque keeps its shape (S differs from straight by up to {gap:.3f})",
          gap > 0.05 and not validate(cS, ms))
    # quantised reference times with no heating must not be "corrected"
    q = [r for r in synthetic_rows("linear") if not r[0].startswith("ref_")]
    t1, t2 = 0.0750, 0.0775
    q += [("ref_before", PULSE_LONG_DEG, "left", 90, 1 / t ** 2, "0") for t in (t1,) * 5 + (t2,)]
    q += [("ref_after", PULSE_LONG_DEG, "left", 90, 1 / t ** 2, "0") for t in (t2,) * 5 + (t1,)]
    q.append(("meta", 0.0, "", 0, 0.0025, "report_dt"))
    real, d, lim = drift_is_real(q)
    check(f"timing steps are not mistaken for heating ({100*(d-1):+.1f}% vs +-{100*lim:.1f}%)",
          not real)
    h = [r for r in synthetic_rows("linear") if not r[0].startswith("ref_")]
    h += [("ref_before", PULSE_LONG_DEG, "left", 90, 100 * rnd, "0")
          for rnd in (1.00, 0.99, 1.01, 1.00, 0.995, 1.005, 1.0, 1.0)]
    h += [("ref_after", PULSE_LONG_DEG, "left", 90, 90 * rnd, "0")
          for rnd in (1.00, 0.99, 1.01, 1.00, 0.995, 1.005, 1.0, 1.0)]
    check("real 10% heating is detected", drift_is_real(h)[0])
    # a large loss is still seen with half the pulses missing (pooling the
    # two groups once made that impossible)
    g = [r for r in synthetic_rows("linear") if not r[0].startswith("ref_")]
    g += [("ref_before", PULSE_LONG_DEG, "left", 90, 100 * rnd, "0") for rnd in (1.0, 0.99, 1.01, 1.0)]
    g += [("ref_after", PULSE_LONG_DEG, "left", 90, 60 * rnd, "0") for rnd in (1.0, 0.99, 1.01, 1.0)]
    check("a 40% loss is detected from four pulses each", drift_is_real(g)[0])
    # thresholds that scatter more than the lowest is high still give a LUT
    try:
        sc = [r for r in synthetic_rows("linear") if r[0] != "threshold"]
        sc += [("threshold", a_, d_, 0, float(v), "0")
               for a_, d_, v in ((0, "left", 3), (0, "right", 30), (90, "left", 4),
                                 (90, "right", 28), (-90, "left", 3), (-90, "right", 31))]
        msc = model_from_rows(sc)
        ok_sc = not validate(msc.build(msc.n_min, 1.0), msc)
    except Exception as e:
        ok_sc = False
        print("       ", e)
    check("widely scattered thresholds still give a valid LUT", ok_sc)
    # on clean data the torque curve must follow a sharp real shape
    Fq = list(range(28, 128, 3))
    true = lambda f: min(1, max(0, (f - 26) / 30)) * 0.5 + max(0, (f - 100) / 27) * 0.5
    fit, k = monotone_smooth(Fq, [true(f) for f in Fq], [0.002] * len(Fq))
    err = max(abs(fit(f) - true(f)) for f in range(28, 128))
    check(f"clean data: fit follows a sharp shape (worst {100*err:.1f}%, {k} neighbours)", err < 0.04)
    rnd = random.Random(5)
    fit2, k2 = monotone_smooth(Fq, [true(f) + rnd.gauss(0, 0.03) for f in Fq], [0.03] * len(Fq))
    kmax = lambda d: int(d.split("..")[1])
    check(f"noisy data: smoothed more ({k2} vs {k} neighbours)", kmax(k2) > kmax(k))
    # clean lower half, noisy upper half: the upper half must not wiggle
    mixed_e = [0.002 if f < 78 else 0.02 for f in Fq]
    mixed_y = [true(f) + (rnd.gauss(0, 0.02) if f >= 78 else rnd.gauss(0, 0.002)) for f in Fq]
    fit3, _ = monotone_smooth(Fq, mixed_y, mixed_e)
    sl = [fit3(f + 1) - fit3(f) for f in range(78, 126)]
    flips = sum(1 for i in range(1, len(sl) - 1)
                if (sl[i] - sl[i - 1]) * (sl[i + 1] - sl[i]) < 0 and abs(sl[i] - sl[i - 1]) > 0.004)
    err_low = max(abs(fit3(f) - true(f)) for f in range(28, 75))
    check(f"uneven noise: clean part followed (worst {100*err_low:.1f}%), noisy part not "
          f"wiggling ({flips} slope reversals)", err_low < 0.03 and flips <= 2)
    # the straight version on a linear wheel should look like RasmusP's
    model = model_from_rows(synthetic_rows("linear"))
    c = model.build(N_DEFAULT, 0.0)
    at = lambda x: next(y for xx, y in c if xx >= x - 1e-9)
    check(f"straight body reaches the lowest threshold at n (y(n)={at(N_DEFAULT):.3f}, "
          f"lo={model.lo:.3f})", abs(at(N_DEFAULT) - model.lo) < 0.01)
    # CSV round trip rebuilds the identical curve
    import tempfile
    rows = synthetic_rows("plateau")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.csv")
        save_csv(p, rows)
        a = model_from_rows(rows).build(0.04, 0.7)
        b = model_from_rows(load_csv(p)).build(0.04, 0.7)
    check("CSV round trip rebuilds the identical curve", a == b)
    # the probe tells speed from shape
    lin = [("probe", PULSE_SHORT_DEG, "left", f, (f - 24) * (1 - 0.004 * (f - 24)), "0")
           for f in range(28, 128, 11)]
    lin += [("probe", PULSE_LONG_DEG, "left", f, (f - 24) * (1 - 0.007 * (f - 24)), "0")
            for f in range(28, 128, 11)]
    check("probe: speed-flattened linear wheel -> straight",
          probe_verdict(lin, 24)["verdict"] == "speed")
    real = [("probe", d, "left", f, ((f - 24) / 103) ** 2 * 100, "0")
            for d in (PULSE_SHORT_DEG, PULSE_LONG_DEG) for f in range(28, 128, 11)]
    check("probe: a real S -> measured", probe_verdict(real, 24)["verdict"] == "measured")
    print()
    return 0 if ok else 1






# ---------------------------------------------------------------------------
#  Driving the wheel through the hub
# ---------------------------------------------------------------------------

class WheelIO:
    """The wheel, through this program's own device handle.

    Each read and write takes dev_lock for the moment it needs it, as
    everything else here does - never for the whole measurement, because
    send() takes the same lock and would deadlock behind it. The poller is
    paused instead: it drains the HID queue every pass, and the measurement
    needs those reports to see the wheel move.
    """

    def __init__(self, profile):
        self.prof = profile
        self.last = None
        sd = (profile.get("steer") or {})
        span = 2 * int(sd.get("half") or 0) or (16384 if state.get("_wide") else 1024)
        # Every distance in the measurement is in degrees, so the rotation
        # range has to be known, not assumed. It first read an "angle" the
        # device profiles do not carry and so took 900 for every wheel: on a
        # MOMO or Driving Force, fixed at 270 on a 10-bit axis, each distance
        # came out 3.3 times too short - "moved 2.75 degrees" became three
        # counts, which gear lash alone can produce - and on a wheel the user
        # had set to 540, 1.7 times too long. Now a wheel whose range can be
        # set is put at 900 for the measurement, as the standalone tool did
        # (the user's own range comes back with apply_settings afterwards),
        # and a fixed one is read as the 270 it is.
        rc = profile.get("range_cmd")
        if rc == "g25":
            self._write(rotation_cmd(900))
            time.sleep(0.1)
            self.range_deg = 900
        elif rc == "dfp":
            for cmd in rotation_cmds_dfp(900):
                self._write(cmd)
                time.sleep(0.05)
            self.range_deg = 900
        else:
            self.range_deg = 270
        self.counts_per_deg = span / self.range_deg
        self.name = profile.get("name", "wheel")
        self._wake()

    def _decode(self, data):
        # Driving Force, MOMO Racing and MOMO Force send 7-byte reports; a
        # fixed "< 8" here threw every one of them away, so the generator saw
        # a wheel that never moved.
        if not data or len(data) < report_min_len(self.prof):
            return None
        if layout_unknown(self.prof):
            ur = ensure_usage_reader()
            if ur is None:
                return None
            return ur.value(HID_USAGE_X, data)
        sd = self.prof["steer"]
        fmt = sd.get("fmt", "lohi")
        if fmt == "hilo6":
            return (data[sd["hi"]] << 6) | (data[sd["lo"]] >> 2)
        if fmt == "single":
            return data[sd["lo"]]
        return data[sd["lo"]] | ((data[sd["hi"]] & sd.get("himask", 0x3F)) << 8)

    def _write(self, cmd):
        if dev is None:
            return
        try:
            with dev_lock:
                dev.write([0x00] + list(cmd))
        except Exception:
            pass

    def force(self, raw, direction):
        """The raw 0..127 force, written as it is.

        This first went through constant_force_cmd, which takes a percentage.
        Converting 0..127 to percent and back left only 101 distinct forces:
        27 requests landed on a neighbour's value, right around the
        threshold too (25 and 26 both sent 25, 23 sent 22). Two sweep levels
        that send the same force measure the same torque, which is a flat
        tread in the curve.
        """
        raw = max(0, min(FORCE_MAX, int(raw)))
        val = 0x80 + raw if direction == "left" else 0x80 - raw
        self._write([SLOT0_MASK | CMD_DOWNLOAD_PLAY, 0x00, max(0, min(255, val)), 0, 0, 0, 0])

    def relax(self):
        self._write(stop_forces_cmd())

    def autocenter(self, on):
        self._write(autocenter_cmd(60 if on else 0))

    def read(self, timeout_ms):
        """The next report's steering, waiting up to timeout_ms for it.

        The device is open non-blocking for the poller, so this used to poll
        and sleep a millisecond between tries. On Windows such a sleep can
        last a whole 15.6 ms timer tick, and every pulse was then timed in
        steps of that size: a simulated run kept 15 distinct pulse times out
        of 73, and 19 of 30 torque levels read the same as their neighbour -
        the staircase curve. Passing a timeout makes the driver itself wait
        and return the moment a report arrives, which is also what the
        standalone tool did.
        """
        if dev is None:
            return None
        deadline = time.perf_counter() + timeout_ms / 1000.0
        while True:
            left = int((deadline - time.perf_counter()) * 1000)
            try:
                with dev_lock:
                    data = dev.read(64, max(1, left))
            except Exception:
                return None
            v = self._decode(data) if data else None
            if v is not None:
                self.last = v
                return v
            if time.perf_counter() >= deadline:
                return None

    def drain(self):
        if dev is None:
            return
        for _ in range(500):
            try:
                with dev_lock:
                    data = dev.read(64)
            except Exception:
                return
            if not data:
                return
            v = self._decode(data)
            if v is not None:
                self.last = v

    def angle(self):
        self.drain()
        vals = [v for v in (self.read(25) for _ in range(4)) if v is not None]
        if vals:
            self.last = int(statistics.median(vals))
        return self.last

    def _wake(self):
        """A still wheel reports nothing, so there may be no angle to start
        from. A small push makes it speak."""
        for _ in range(10):
            if self.read(50) is not None:
                return
        for d in ("left", "right"):
            self.force(45, d)
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 0.15:
                self.read(10)
            self.relax()
            if self.last is not None:
                return

    def close(self):
        # zero the effect slot the measurement used, then stop everything:
        # nothing of the measurement is left in the wheel for a game to meet
        self.force(0, "left")
        self.relax()
        self.autocenter(False)


def measurement_problems(rows):
    """Reasons this measurement should not become a LUT, or an empty list.

    The sweep's levels should all read differently: torque rises with force.
    When many neighbouring levels read exactly the same, the pulses were
    timed too coarsely and the curve built on them is a staircase - which a
    first build inside the app produced and saved. That is caught here,
    before anything is offered.
    """
    problems = []
    tp = torque_points(rows)
    if len(tp) >= 6:
        vals = [t[1] for t in tp]
        top = max(vals) or 1.0
        same = sum(1 for a, b in zip(vals, vals[1:]) if abs(b - a) / top < 0.002)
        if same > 0.2 * (len(vals) - 1):
            problems.append(f"{same} of {len(vals) - 1} torque levels read the same as the "
                            f"next one - the timing was too coarse")
    dt = next((r[4] for r in rows if r[0] == "meta" and r[5] == "report_dt"), None)
    if dt and dt > 0.006:
        problems.append(f"reports arrived every {dt * 1000:.0f} ms on average "
                        f"(a G27 sends one every 2 ms)")
    return problems


def read_lut_meta(path):
    """The '; key: value' lines at the top of a .lut file."""
    meta = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.startswith(";"):
                    if "|" in line:
                        break
                    continue
                k, _, v = line[1:].partition(":")
                meta[k.strip()] = v.strip()
    except OSError:
        pass
    return meta


def lut_is_generated(meta):
    return "LWH LUT Generator" in meta


def lut_meta_float(meta, key, default):
    try:
        return float((meta.get(key) or "").split()[0])
    except (ValueError, IndexError):
        return default


def lut_measurement_path(lut_path, meta):
    """The measurement a generated LUT was computed from, if it can be found.

    Newer LUTs name it in their header. The first builds saved one file per
    wheel, named after it, and a second measurement replaced the first - so
    that name is only a fallback, and the user can point to the file."""
    d = os.path.dirname(lut_path)
    name = meta.get("measurement")
    if name:
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return cand
    base = "".join(c for c in (meta.get("wheel") or "") if c.isalnum() or c in "-_")
    if base:
        cand = os.path.join(d, f"{base}_measurement.csv")
        if os.path.exists(cand):
            return cand
    return None


def import_measurement(src_lut, lut_dir):
    """Bring a generated LUT's measurement along when the LUT is imported.

    Import copied only the .lut, so a generated LUT arrived without the .csv
    it was computed from: no prediction, no reshaping, no Verify, and the
    page said the measurement was missing. The file its header names (or the
    old per-wheel name) is copied from beside the source too, when it is
    there and nothing of that name is in the LUT folder yet. Returns the
    copied file's name, or None."""
    try:
        meta = read_lut_meta(src_lut)
        if not lut_is_generated(meta):
            return None
        src = lut_measurement_path(src_lut, meta)
        if not src:
            return None
        dst = os.path.join(lut_dir, os.path.basename(src))
        if os.path.abspath(src) == os.path.abspath(dst) or os.path.exists(dst):
            return None
        with open(src, "rb") as s, open(dst, "wb") as d:
            d.write(s.read())
        return os.path.basename(dst)
    except Exception:
        return None


def estimate_seconds():
    """Roughly how long a measurement takes, for the wizard to show up front."""
    return Measurer.estimate_seconds()


def measure_wheel(profile, progress=None, cancel=None):
    """One full measurement, with this program's polling paused around it."""
    global lutgen_busy
    # Python threads take turns every 5 ms by default. While the interface
    # thread runs, a report the measurement is waiting for can sit that long
    # before its time is taken - which is how a run that takes 8.7 minutes on
    # its own took about 15 inside the app. A short interval for the duration
    # keeps the waits small; the interface barely notices.
    old_switch = sys.getswitchinterval()
    sys.setswitchinterval(0.0005)
    io = WheelIO(profile)
    lutgen_busy = True
    try:
        m = Measurer(io, progress, cancel)
        try:
            return m.run()
        except Cancelled:
            m.add_meta()
            return m.rows
    finally:
        lutgen_busy = False
        sys.setswitchinterval(old_switch)

from PySide6.QtWidgets import QProgressBar

# ---------------------------------------------------------------------------
#  Wizard
# ---------------------------------------------------------------------------

LUTGEN_LANG = {
    "en": {
        "edit_btn": "Edit",
        "edit_title": "Edit LUT",
        "edit_sub": "Reshape {name} from the measurement it was made from. Saving replaces this file.",
        "edit_tip": "Reshape this LUT from its measurement.",
        "edit_tip_imported": "Only LUTs made by the generator can be edited: they carry the measurement they were computed from.",
        "edit_find": "Find this LUT's measurement (.csv)",
        "edit_bad": "This measurement cannot be used: {why}",
        "edit_saved": "{name} updated.",
        "coarse": "This measurement is too coarse to build a LUT on, so nothing was saved: {why}. Close other programs and try again.",
        "heat_fixed": "corrected",
        "heat_noise": "within measurement noise",
        "sec": "LUT GENERATOR",
        "intro": "Measure this wheel and compute a LUT from what it actually does.",
        "btn": "Generate LUT",
        "title": "LUT generator",
        "subtitle": "Measures this wheel and builds a curve from its own behaviour.",
        "what": "What will be measured",
        "desc_thresholds": "The force at which the wheel breaks loose, at three angles and in both directions.",
        "desc_sweep": "How torque grows with force, with a reference before and after to catch motor heating.",
        "desc_probe": "Whether the measured shape belongs to the wheel or only appears at speed.",
        "prep": "Before you start",
        "prep_1": "Close any running game.",
        "prep_2": "Take your hands off the wheel and keep the area clear - it turns by itself, hard.",
        "prep_3": "You can stop at any time; nothing is changed until you save.",
        "duration": "Takes about {min} minutes.",
        "start": "Start measurement",
        "cancel": "Cancel",
        "stop": "Stop",
        "stopping": "Stopping after the current step...",
        "stage_thresholds": "Breakaway thresholds",
        "stage_sweep": "Torque sweep",
        "stage_probe": "Shape test",
        "reading": "Current step",
        "elapsed": "{el} elapsed  ·  about {left} left",
        "failed": "Measurement failed: {err}",
        "stopped": "Stopped. Nothing was saved.",
        "res_title": "Your LUT",
        "res_sub": "Shape it if you like, then save. The limits come from your measurement.",
        "stat_threshold": "Breakaway threshold",
        "stat_heating": "Motor heating",
        "stat_shape": "Response shape",
        "shape_wheel": "Wheel's own",
        "shape_speed": "From speed",
        "measured": "Based on {n} threshold measurements.",
        "verdict_measured": "The wheel's response has a shape of its own; the measured body corrects it.",
        "verdict_speed": "The measured shape comes from speed, not from the wheel, so a straight body is used.",
        "centre": "Centre",
        "centre_calm": "calm",
        "centre_lively": "lively",
        "centre_hint": "Calm keeps the game's centre noise under the threshold - less vibration. "
                       "Lively lets the wheel start moving sooner.",
        "body": "Body",
        "body_straight": "straight",
        "body_measured": "measured",
        "body_hint": "Straight is the RasmusP style. Measured follows what this wheel actually does.",
        "save": "Save and use",
        "discard": "Discard",
        "saved": "Saved as {name} and selected for this profile.",
        "invalid": "This setting cannot be saved: {why}",
        "nowheel": "No wheel connected.",
    },
    "tr": {
        "edit_btn": "Düzenle",
        "edit_title": "LUT'u düzenle",
        "edit_sub": "{name} dosyasını, üretildiği ölçümden yeniden şekillendirin. Kaydetmek bu dosyanın yerine yazar.",
        "edit_tip": "Bu LUT'u ölçümünden yeniden şekillendir.",
        "edit_tip_imported": "Sadece üreticiyle yapılan LUT'lar düzenlenebilir: hesaplandıkları ölçümü taşırlar.",
        "edit_find": "Bu LUT'un ölçümünü bulun (.csv)",
        "edit_bad": "Bu ölçüm kullanılamıyor: {why}",
        "edit_saved": "{name} güncellendi.",
        "coarse": "Bu ölçüm LUT kurmak için fazla kaba, hiçbir şey kaydedilmedi: {why}. Diğer programları kapatıp tekrar deneyin.",
        "heat_fixed": "düzeltildi",
        "heat_noise": "ölçüm belirsizliği içinde",
        "sec": "LUT ÜRETİCİ",
        "intro": "Direksiyonu ölçüp gerçekte yaptığı işe göre bir LUT hesaplar.",
        "btn": "LUT Üret",
        "title": "LUT üretici",
        "subtitle": "Direksiyonu ölçer ve kendi davranışından bir eğri kurar.",
        "what": "Neler ölçülecek",
        "desc_thresholds": "Direksiyonun yerinden koptuğu kuvvet; üç açıda ve iki yönde.",
        "desc_sweep": "Torkun kuvvetle nasıl arttığı; motor ısınmasını yakalamak için önce ve sonra referans ölçümü.",
        "desc_probe": "Ölçülen şeklin direksiyona mı ait olduğu, yoksa sadece hızda mı ortaya çıktığı.",
        "prep": "Başlamadan önce",
        "prep_1": "Açık oyun varsa kapatın.",
        "prep_2": "Ellerinizi direksiyondan çekin ve çevresini boş bırakın - kendi kendine, sert şekilde dönecek.",
        "prep_3": "İstediğiniz an durdurabilirsiniz; kaydetmeden hiçbir şey değişmez.",
        "duration": "Yaklaşık {min} dakika sürer.",
        "start": "Ölçümü başlat",
        "cancel": "Vazgeç",
        "stop": "Durdur",
        "stopping": "Mevcut adım bitince duruyor...",
        "stage_thresholds": "Kopma eşikleri",
        "stage_sweep": "Tork taraması",
        "stage_probe": "Şekil testi",
        "reading": "Şu anki adım",
        "elapsed": "Geçen {el}  ·  kalan yaklaşık {left}",
        "failed": "Ölçüm başarısız: {err}",
        "stopped": "Durduruldu. Hiçbir şey kaydedilmedi.",
        "res_title": "LUT'unuz",
        "res_sub": "İsterseniz şekillendirin, sonra kaydedin. Sınırlar ölçümünüzden geliyor.",
        "stat_threshold": "Kopma eşiği",
        "stat_heating": "Motor ısınması",
        "stat_shape": "Tepki şekli",
        "shape_wheel": "Direksiyonun kendi",
        "shape_speed": "Hızdan",
        "measured": "{n} eşik ölçümüne dayanıyor.",
        "verdict_measured": "Direksiyonun tepkisinin kendine ait bir şekli var; ölçülen gövde onu düzeltiyor.",
        "verdict_speed": "Ölçülen şekil direksiyondan değil hızdan geliyor; düz gövde kullanılıyor.",
        "centre": "Merkez",
        "centre_calm": "sakin",
        "centre_lively": "canlı",
        "centre_hint": "Sakin, oyunun merkezdeki gürültüsünü eşiğin altında tutar - daha az titreşim. "
                       "Canlı, direksiyonun daha erken harekete geçmesini sağlar.",
        "body": "Gövde",
        "body_straight": "düz",
        "body_measured": "ölçülen",
        "body_hint": "Düz, RasmusP tarzıdır. Ölçülen, bu direksiyonun gerçek tepkisini izler.",
        "save": "Kaydet ve kullan",
        "discard": "Vazgeç",
        "saved": "{name} olarak kaydedildi ve bu profile atandı.",
        "invalid": "Bu ayar kaydedilemez: {why}",
        "nowheel": "Direksiyon bağlı değil.",
    },
    "de": {
        "edit_btn": "Bearbeiten",
        "edit_title": "LUT bearbeiten",
        "edit_sub": "{name} aus der Messung neu formen, aus der sie berechnet wurde. Speichern ersetzt diese Datei.",
        "edit_tip": "Diese LUT aus ihrer Messung neu formen.",
        "edit_tip_imported": "Nur LUTs aus dem Generator sind bearbeitbar: sie tragen die Messung, aus der sie berechnet wurden.",
        "edit_find": "Messung dieser LUT suchen (.csv)",
        "edit_bad": "Diese Messung ist nicht verwendbar: {why}",
        "edit_saved": "{name} aktualisiert.",
        "coarse": "Diese Messung ist für eine LUT zu grob, nichts wurde gespeichert: {why}. Andere Programme schließen und erneut versuchen.",
        "heat_fixed": "korrigiert",
        "heat_noise": "innerhalb der Messunsicherheit",
        "sec": "LUT-GENERATOR",
        "intro": "Misst das Lenkrad und berechnet eine LUT aus seinem tatsächlichen Verhalten.",
        "btn": "LUT erzeugen",
        "title": "LUT-Generator",
        "subtitle": "Misst das Lenkrad und baut eine Kurve aus seinem eigenen Verhalten.",
        "what": "Was gemessen wird",
        "desc_thresholds": "Die Kraft, bei der sich das Lenkrad löst - an drei Winkeln, in beide Richtungen.",
        "desc_sweep": "Wie das Drehmoment mit der Kraft wächst, mit Referenz davor und danach gegen Motorerwärmung.",
        "desc_probe": "Ob die gemessene Form zum Lenkrad gehört oder nur bei Geschwindigkeit auftritt.",
        "prep": "Vor dem Start",
        "prep_1": "Laufende Spiele schließen.",
        "prep_2": "Hände vom Lenkrad nehmen und Platz lassen - es dreht sich selbstständig und kräftig.",
        "prep_3": "Abbruch jederzeit möglich; ohne Speichern wird nichts geändert.",
        "duration": "Dauert etwa {min} Minuten.",
        "start": "Messung starten",
        "cancel": "Abbrechen",
        "stop": "Stoppen",
        "stopping": "Stoppt nach dem aktuellen Schritt...",
        "stage_thresholds": "Losbrech-Schwellen",
        "stage_sweep": "Drehmoment-Messung",
        "stage_probe": "Formtest",
        "reading": "Aktueller Schritt",
        "elapsed": "{el} vergangen  ·  noch etwa {left}",
        "failed": "Messung fehlgeschlagen: {err}",
        "stopped": "Gestoppt. Nichts gespeichert.",
        "res_title": "Ihre LUT",
        "res_sub": "Nach Wunsch formen, dann speichern. Die Grenzen stammen aus Ihrer Messung.",
        "stat_threshold": "Losbrech-Schwelle",
        "stat_heating": "Motorerwärmung",
        "stat_shape": "Kennlinienform",
        "shape_wheel": "Vom Lenkrad",
        "shape_speed": "Von der Geschwindigkeit",
        "measured": "Basiert auf {n} Schwellenmessungen.",
        "verdict_measured": "Die Kennlinie des Lenkrads hat eine eigene Form; der gemessene Körper korrigiert sie.",
        "verdict_speed": "Die gemessene Form stammt von der Geschwindigkeit, nicht vom Lenkrad; gerader Körper.",
        "centre": "Mitte",
        "centre_calm": "ruhig",
        "centre_lively": "lebendig",
        "centre_hint": "Ruhig hält das Mittenrauschen des Spiels unter der Schwelle - weniger Vibration. "
                       "Lebendig lässt das Lenkrad früher reagieren.",
        "body": "Körper",
        "body_straight": "gerade",
        "body_measured": "gemessen",
        "body_hint": "Gerade entspricht dem RasmusP-Stil. Gemessen folgt dem, was dieses Lenkrad tut.",
        "save": "Speichern und verwenden",
        "discard": "Verwerfen",
        "saved": "Als {name} gespeichert und diesem Profil zugewiesen.",
        "invalid": "Diese Einstellung kann nicht gespeichert werden: {why}",
        "nowheel": "Kein Lenkrad verbunden.",
    },
    "uk": {
        "edit_btn": "Змінити",
        "edit_title": "Зміна LUT",
        "edit_sub": "Змініть форму {name} на основі вимірювання, з якого її зроблено. Збереження замінить цей файл.",
        "edit_tip": "Змінити форму цього LUT на основі його вимірювання.",
        "edit_tip_imported": "Змінювати можна лише LUT, створені генератором: вони містять вимірювання, з якого їх обчислено.",
        "edit_find": "Знайти вимірювання цього LUT (.csv)",
        "edit_bad": "Це вимірювання не можна використати: {why}",
        "edit_saved": "{name} оновлено.",
        "coarse": "Це вимірювання надто грубе, щоб побудувати на ньому LUT, тому нічого не збережено: {why}. Закрийте інші програми й спробуйте ще раз.",
        "heat_fixed": "виправлено",
        "heat_noise": "у межах шуму вимірювання",
        "sec": "ГЕНЕРАТОР LUT",
        "intro": "Виміряйте це кермо й обчисліть LUT з того, що воно робить насправді.",
        "btn": "Створити LUT",
        "title": "Генератор LUT",
        "subtitle": "Вимірює це кермо й будує криву з його власної поведінки.",
        "what": "Що буде виміряно",
        "desc_thresholds": "Сила, за якої кермо зрушує з місця, під трьома кутами й в обох напрямках.",
        "desc_sweep": "Як зростає крутний момент зі збільшенням сили, з контрольним виміром до й після, щоб помітити нагрівання мотора.",
        "desc_probe": "Чи виміряна форма належить самому керму, чи з’являється лише на швидкості.",
        "prep": "Перш ніж почати",
        "prep_1": "Закрийте запущену гру.",
        "prep_2": "Приберіть руки з керма й звільніть місце навколо — воно крутитиметься саме, і сильно.",
        "prep_3": "Зупинити можна будь-коли; доки ви не збережете, нічого не зміниться.",
        "duration": "Займає приблизно {min} хв.",
        "start": "Почати вимірювання",
        "cancel": "Скасувати",
        "stop": "Зупинити",
        "stopping": "Зупинка після поточного кроку...",
        "stage_thresholds": "Пороги зрушення",
        "stage_sweep": "Прогін крутного моменту",
        "stage_probe": "Перевірка форми",
        "reading": "Поточний крок",
        "elapsed": "минуло {el}  ·  лишилося близько {left}",
        "failed": "Вимірювання не вдалося: {err}",
        "stopped": "Зупинено. Нічого не збережено.",
        "res_title": "Ваш LUT",
        "res_sub": "За бажання змініть форму, потім збережіть. Межі взято з вашого вимірювання.",
        "stat_threshold": "Поріг зрушення",
        "stat_heating": "Нагрівання мотора",
        "stat_shape": "Форма відгуку",
        "shape_wheel": "Власна для керма",
        "shape_speed": "Від швидкості",
        "measured": "На основі {n} вимірювань порогу.",
        "verdict_measured": "Відгук керма має власну форму; виміряне тіло кривої її виправляє.",
        "verdict_speed": "Виміряна форма походить від швидкості, а не від керма, тому використано пряме тіло.",
        "centre": "Центр",
        "centre_calm": "спокійно",
        "centre_lively": "жваво",
        "centre_hint": "Спокійний тримає центральний шум гри нижче порогу - менше вібрації. Живий дає керму рушати раніше.",
        "body": "Тіло",
        "body_straight": "пряме",
        "body_measured": "виміряне",
        "body_hint": "Пряма - стиль RasmusP. Виміряна повторює те, що це кермо робить насправді.",
        "save": "Зберегти й використати",
        "discard": "Відкинути",
        "saved": "Збережено як {name} і вибрано для цього профілю.",
        "invalid": "Це налаштування не можна зберегти: {why}",
        "nowheel": "Кермо не підключено.",
    },
    "ru": {
        "edit_btn": "Изменить",
        "edit_title": "Изменение LUT",
        "edit_sub": "Измените форму {name} на основе измерения, из которого она сделана. Сохранение заменит этот файл.",
        "edit_tip": "Изменить форму этого LUT на основе его измерения.",
        "edit_tip_imported": "Изменять можно только LUT, созданные генератором: они содержат измерение, из которого вычислены.",
        "edit_find": "Найти измерение этого LUT (.csv)",
        "edit_bad": "Это измерение нельзя использовать: {why}",
        "edit_saved": "{name} обновлён.",
        "coarse": "Это измерение слишком грубое, чтобы построить на нём LUT, поэтому ничего не сохранено: {why}. Закройте другие программы и попробуйте ещё раз.",
        "heat_fixed": "исправлено",
        "heat_noise": "в пределах шума измерения",
        "sec": "ГЕНЕРАТОР LUT",
        "intro": "Измерьте этот руль и вычислите LUT из того, что он делает на самом деле.",
        "btn": "Создать LUT",
        "title": "Генератор LUT",
        "subtitle": "Измеряет этот руль и строит кривую из его собственного поведения.",
        "what": "Что будет измерено",
        "desc_thresholds": "Сила, при которой руль трогается с места, под тремя углами и в обоих направлениях.",
        "desc_sweep": "Как растёт крутящий момент с ростом силы, с контрольным замером до и после, чтобы заметить нагрев мотора.",
        "desc_probe": "Принадлежит ли измеренная форма самому рулю или появляется только на скорости.",
        "prep": "Прежде чем начать",
        "prep_1": "Закройте запущенную игру.",
        "prep_2": "Уберите руки с руля и освободите место вокруг — он будет крутиться сам, и сильно.",
        "prep_3": "Остановить можно в любой момент; пока вы не сохраните, ничего не изменится.",
        "duration": "Занимает около {min} мин.",
        "start": "Начать измерение",
        "cancel": "Отмена",
        "stop": "Остановить",
        "stopping": "Остановка после текущего шага...",
        "stage_thresholds": "Пороги трогания",
        "stage_sweep": "Прогон крутящего момента",
        "stage_probe": "Проверка формы",
        "reading": "Текущий шаг",
        "elapsed": "прошло {el}  ·  осталось около {left}",
        "failed": "Измерение не удалось: {err}",
        "stopped": "Остановлено. Ничего не сохранено.",
        "res_title": "Ваш LUT",
        "res_sub": "При желании измените форму, затем сохраните. Пределы взяты из вашего измерения.",
        "stat_threshold": "Порог трогания",
        "stat_heating": "Нагрев мотора",
        "stat_shape": "Форма отклика",
        "shape_wheel": "Собственная у руля",
        "shape_speed": "От скорости",
        "measured": "На основе {n} измерений порога.",
        "verdict_measured": "Отклик руля имеет собственную форму; измеренное тело кривой её исправляет.",
        "verdict_speed": "Измеренная форма происходит от скорости, а не от руля, поэтому использовано прямое тело.",
        "centre": "Центр",
        "centre_calm": "спокойно",
        "centre_lively": "живо",
        "centre_hint": "Спокойный держит центральный шум игры ниже порога - меньше вибрации. Живой даёт рулю трогаться раньше.",
        "body": "Тело",
        "body_straight": "прямое",
        "body_measured": "измеренное",
        "body_hint": "Прямая - стиль RasmusP. Измеренная повторяет то, что этот руль делает на самом деле.",
        "save": "Сохранить и использовать",
        "discard": "Отбросить",
        "saved": "Сохранено как {name} и выбрано для этого профиля.",
        "invalid": "Эту настройку нельзя сохранить: {why}",
        "nowheel": "Руль не подключён.",
    },
}

def gtr(key):
    d = LUTGEN_LANG.get(CURRENT_LANG) or LUTGEN_LANG["en"]
    return d.get(key, LUTGEN_LANG["en"].get(key, key))


class _LutGenThread(QThread):
    """The measurement, off the GUI thread. It owns the wheel while it runs."""
    progressed = Signal(str, int, int, str)
    finished_rows = Signal(object, object)      # rows, error

    def __init__(self, profile, cancel):
        super().__init__()
        self.profile = profile
        self.cancel = cancel

    def run(self):
        try:
            rows = measure_wheel(self.profile, self.progressed.emit, self.cancel)
            self.finished_rows.emit(rows, None)
        except Exception as e:                  # a failure here must not kill the app
            self.finished_rows.emit(None, e)


# Optional fluent pieces: used when the installed qfluentwidgets has them,
# replaced by plain equivalents when it does not, so an older library never
# stops the window from opening.
try:
    from qfluentwidgets import CardWidget as _FluentCard
except Exception:
    _FluentCard = None
try:
    from qfluentwidgets import ProgressBar as _FluentBar
except Exception:
    _FluentBar = None
from PySide6.QtWidgets import QStackedWidget, QFrame as _QFrame


class _Pages(QWidget):
    """Pages shown one at a time, sized by the one showing.

    Not a QStackedWidget: its layout reports the tallest page's height, and
    a window asking it how tall to be gets that answer however the widget
    itself is overridden - the two short pages opened as tall as the result
    page with its chart, half of them empty. Hidden widgets count for
    nothing in a layout, so showing one page and hiding the rest does what
    the stack would not."""
    def __init__(self):
        super().__init__()
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._pages = []
        self._current = -1

    def addWidget(self, page):
        self._pages.append(page)
        self._lay.addWidget(page)
        page.setVisible(False)

    def count(self):
        return len(self._pages)

    def widget(self, i):
        return self._pages[i]

    def currentIndex(self):
        return self._current

    def currentWidget(self):
        return self._pages[self._current] if 0 <= self._current < len(self._pages) else None

    def setCurrentIndex(self, i):
        for k, p in enumerate(self._pages):
            p.setVisible(k == i)
        self._current = i


def _lg_card(accent=False):
    """A rounded, theme-aware panel; with accent, a coloured strip on its left
    edge. The strip is a widget of its own because the fluent card paints its
    own frame and ignores border styles."""
    if _FluentCard is not None:
        c = _FluentCard()
    else:
        c = _QFrame()
        c.setObjectName("lgCard")
        c.setStyleSheet("#lgCard{border:1px solid rgba(128,128,128,0.28);border-radius:8px;}")
    outer = QHBoxLayout(c)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    if accent:
        strip = _QFrame()
        strip.setFixedWidth(3)
        strip.setStyleSheet(f"background:{ACCENT};border-top-left-radius:6px;"
                            "border-bottom-left-radius:6px;")
        outer.addWidget(strip)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    outer.addWidget(inner, 1)
    return c, lay


def _lg_rule():
    line = _QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background:rgba(128,128,128,0.22);")
    return line


class _StepRow(QWidget):
    """One stage of the measurement: a numbered bubble, its name and what it
    does, and where it stands."""
    STYLES = {
        "wait": "border:1px solid rgba(128,128,128,0.55);color:rgba(128,128,128,0.9);",
        "run":  "background:{a};border:1px solid {a};color:white;",
        "done": "background:rgba(46,160,67,0.9);border:1px solid rgba(46,160,67,0.9);color:white;",
    }

    def __init__(self, number, name, desc):
        super().__init__()
        self.number = number
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(12)
        self.bubble = QLabel(str(number))
        self.bubble.setFixedSize(28, 28)
        self.bubble.setAlignment(Qt.AlignCenter)
        h.addWidget(self.bubble, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = StrongBodyLabel(name)
        self.desc = CaptionLabel(desc)
        self.desc.setWordWrap(True)
        col.addWidget(self.name)
        col.addWidget(self.desc)
        h.addLayout(col, 1)
        self.status = CaptionLabel("")
        h.addWidget(self.status, 0, Qt.AlignTop)
        self.set_state("wait")

    def set_state(self, state, text=""):
        self.bubble.setText("✓" if state == "done" else str(self.number))
        self.bubble.setStyleSheet("border-radius:14px;font-weight:600;"
                                  + self.STYLES[state].format(a=ACCENT))
        self.status.setText(text)


class _Stat(QWidget):
    """A small measured value with its label above it."""
    def __init__(self, title):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.title = CaptionLabel(title)
        self.value = SubtitleLabel("-")
        self.note = CaptionLabel("")
        v.addWidget(self.title)
        v.addWidget(self.value)
        v.addWidget(self.note)


class LutGenWizard(QDialog):
    """Measure, then shape and save - in its own window.

    Its own window on purpose: the measurement takes minutes, and a gain or
    rotation change made halfway through would quietly invalidate it.
    """

    STAGES = ("thresholds", "sweep", "probe")

    def __init__(self, parent, lut_tab, edit=None):
        super().__init__(parent)
        self.lut_tab = lut_tab
        # edit: {"path": lut file, "rows": its measurement, "n", "body",
        #        "csv": path of the measurement file}
        self.edit = edit
        self.setWindowTitle(gtr("edit_title") if edit else gtr("title"))
        self.setMinimumWidth(720)
        self.resize(780, 10)
        self.rows = None
        self.model = None
        self.verdict = {}
        self.curve = []
        self.cancel = threading.Event()
        self.thread = None
        self._t0 = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(14)

        # -- header -------------------------------------------------------
        head = QVBoxLayout()
        head.setSpacing(2)
        self.lbl_title = SubtitleLabel(gtr("title"))
        self.lbl_sub = CaptionLabel(gtr("subtitle"))
        self.lbl_sub.setWordWrap(True)
        head.addWidget(self.lbl_title)
        head.addWidget(self.lbl_sub)
        root.addLayout(head)
        root.addWidget(_lg_rule())

        self.stack = _Pages()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_intro())
        self.stack.addWidget(self._build_run())
        self.stack.addWidget(self._build_result())

        # -- footer -------------------------------------------------------
        root.addWidget(_lg_rule())
        foot = QHBoxLayout()
        self.lbl_foot = CaptionLabel("")
        foot.addWidget(self.lbl_foot, 1)
        self.btn_left = PushButton(gtr("cancel"))
        self.btn_right = PrimaryPushButton(gtr("start"))
        self.btn_left.setMinimumWidth(110)
        self.btn_right.setMinimumWidth(150)
        foot.addWidget(self.btn_left)
        foot.addWidget(self.btn_right)
        root.addLayout(foot)
        self.btn_left.clicked.connect(self._on_left)
        self.btn_right.clicked.connect(self._on_right)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.lbl_foot.setText(gtr("duration").format(min=round(estimate_seconds() / 60)))
        if edit:
            try:
                self.model = model_from_rows(edit["rows"])
            except Exception as e:
                self.model = None
                self.lbl_sub.setText(gtr("edit_bad").format(why=e))
            if self.model is not None:
                self._show_result(edit["rows"], edit.get("n"), edit.get("body"))
                return
        self._show_page(0)

    def _show_page(self, index):
        """Show one page and fit the window to it.

        The window follows the page: compact while measuring, taller once
        there is a curve to show."""
        self.stack.setCurrentIndex(index)
        self.stack.updateGeometry()
        self.layout().invalidate()
        self.layout().activate()
        width = max(self.width(), 780)
        lay = self.layout()
        height = lay.heightForWidth(width) if lay.hasHeightForWidth() else self.sizeHint().height()
        self.setMinimumHeight(min(height, self.minimumSizeHint().height()))
        self.resize(width, height)

    # -- pages -------------------------------------------------------------
    def _steps(self, lay):
        rows = []
        for n, key in enumerate(self.STAGES, 1):
            r = _StepRow(n, gtr("stage_" + key), gtr("desc_" + key))
            lay.addWidget(r)
            rows.append(r)
        return rows

    def _build_intro(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        card, c = _lg_card()
        c.addWidget(StrongBodyLabel(gtr("what")))
        self._steps(c)
        v.addWidget(card)

        warn, w = _lg_card(accent=True)
        w.addWidget(StrongBodyLabel(gtr("prep")))
        for key in ("prep_1", "prep_2", "prep_3"):
            lbl = BodyLabel("•  " + gtr(key))
            lbl.setWordWrap(True)
            w.addWidget(lbl)
        v.addWidget(warn)
        return page

    def _build_run(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        card, c = _lg_card()
        self.run_steps = self._steps(c)
        v.addWidget(card)

        pcard, p = _lg_card()
        top = QHBoxLayout()
        self.lbl_stage = StrongBodyLabel("")
        self.lbl_pct = StrongBodyLabel("0%")
        top.addWidget(self.lbl_stage, 1)
        top.addWidget(self.lbl_pct)
        p.addLayout(top)
        self.bar = None
        if _FluentBar is not None:
            try:
                self.bar = _FluentBar(useAni=False)
            except TypeError:
                self.bar = _FluentBar()
        if self.bar is None:
            self.bar = QProgressBar()
            self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFixedHeight(6)
        p.addWidget(self.bar)
        self.lbl_time = CaptionLabel("")
        p.addWidget(self.lbl_time)
        p.addSpacing(4)
        p.addWidget(CaptionLabel(gtr("reading")))
        self.lbl_msg = BodyLabel("...")
        # a font, not a style sheet: a style sheet on a fluent label replaces
        # its theme colour too, and the text came out black on the dark theme
        mono = self.lbl_msg.font()
        mono.setFamilies(["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "monospace"])
        mono.setPixelSize(15)
        self.lbl_msg.setFont(mono)
        p.addWidget(self.lbl_msg)
        v.addWidget(pcard)
        return page

    def _build_result(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        stats, s = _lg_card()
        row = QHBoxLayout()
        row.setSpacing(24)
        self.st_thr = _Stat(gtr("stat_threshold"))
        self.st_heat = _Stat(gtr("stat_heating"))
        self.st_shape = _Stat(gtr("stat_shape"))
        for w in (self.st_thr, self.st_heat, self.st_shape):
            row.addWidget(w, 1)
        s.addLayout(row)
        self.lbl_verdict = CaptionLabel("")
        self.lbl_verdict.setWordWrap(True)
        s.addWidget(self.lbl_verdict)
        v.addWidget(stats)

        ccard, c = _lg_card()
        self.preview = LutCurveWidget()
        self.preview.setMinimumHeight(250)
        c.addWidget(self.preview, 1)
        v.addWidget(ccard, 1)

        scard, sl = _lg_card()
        self.sl_centre, self.lbl_centre = self._slider(sl, "centre", "centre_calm",
                                                       "centre_lively", "centre_hint")
        sl.addSpacing(4)
        self.sl_body, self.lbl_body = self._slider(sl, "body", "body_straight",
                                                   "body_measured", "body_hint")
        v.addWidget(scard)
        self.lbl_issue = CaptionLabel("")
        self.lbl_issue.setWordWrap(True)
        self.lbl_issue.setStyleSheet("color:#e5534b;")
        v.addWidget(self.lbl_issue)
        return page

    def _slider(self, lay, key, lo_key, hi_key, hint_key):
        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel(gtr(key)))
        head.addStretch(1)
        val = CaptionLabel("")
        head.addWidget(val)
        lay.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(10)
        lo = CaptionLabel(gtr(lo_key))
        hi = CaptionLabel(gtr(hi_key))
        lo.setMinimumWidth(60)
        hi.setMinimumWidth(60)
        hi.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sl = Slider(Qt.Horizontal)
        sl.setRange(0, 100)
        row.addWidget(lo)
        row.addWidget(sl, 1)
        row.addWidget(hi)
        lay.addLayout(row)
        hint = CaptionLabel(gtr(hint_key))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        sl.valueChanged.connect(self._redraw)
        return sl, val

    # -- flow --------------------------------------------------------------
    def _on_right(self):
        if self.stack.currentIndex() == 0:
            self._start()
        elif self.stack.currentIndex() == 2:
            self._save()

    def _on_left(self):
        if self.stack.currentIndex() == 1:
            self.cancel.set()
            self.btn_left.setEnabled(False)
            self.lbl_foot.setText(gtr("stopping"))
        else:
            self.reject()

    def _start(self):
        if dev is None:
            InfoBar.warning(gtr("title"), gtr("nowheel"), duration=4000,
                            position=InfoBarPosition.TOP, parent=self)
            return
        # Name the first stage and a real count before anything is measured:
        # the first threshold run takes about a quarter of a minute, and an
        # empty window with "-1 of 100" read as a hang.
        self.run_steps[0].set_state("run", "0 / %d" % (3 * 2 * THRESHOLD_REPEATS))
        self.lbl_stage.setText(gtr("stage_thresholds"))
        self.bar.setValue(0)
        self.lbl_pct.setText("0%")
        self.lbl_msg.setText("...")
        self._show_page(1)
        self.btn_right.setVisible(False)
        self.btn_left.setText(gtr("stop"))
        self.lbl_foot.setText("")
        self._t0 = time.time()
        self._frac = 0.0
        self.timer.start(500)
        # The main window redraws sixty times a second, all of it Python on the
        # interface thread, competing with the measurement for the
        # interpreter. The wheel's state is frozen while the poller is paused
        # anyway, so there is nothing to redraw until it is over.
        self._paused_timer = None
        t = getattr(main_window, "timer", None)
        if t is not None and t.isActive():
            t.stop()
            self._paused_timer = t
        self._measuring = True
        self.thread = _LutGenThread(active_profile, self.cancel)
        self.thread.progressed.connect(self._progress)
        self.thread.finished_rows.connect(self._done)
        self.thread.start()

    def _progress(self, stage, done, total, msg):
        k = self.STAGES.index(stage) if stage in self.STAGES else 0
        for i, r in enumerate(self.run_steps):
            if i < k:
                r.set_state("done", "")
            elif i == k:
                r.set_state("run", f"{done} / {total}")
            else:
                r.set_state("wait", "")
        self.lbl_stage.setText(gtr("stage_" + stage))
        self.lbl_msg.setText(msg)
        # overall progress, each stage weighted by its share of the time
        plan = {"thresholds": 3 * 2 * THRESHOLD_REPEATS * 10 * T_BREAK_TRIAL,
                "sweep": (SWEEP_LEVELS * SWEEP_REPEATS * 2 + 4 * REF_REPEATS) * T_PULSE,
                "probe": PROBE_LEVELS * 2 * 2 * PROBE_REPEATS * T_PULSE}
        tot = sum(plan.values())
        before = sum(plan[s] for s in self.STAGES[:k])
        frac = (before + plan.get(stage, 0) * (done / total if total else 0)) / tot
        self._frac = max(self._frac, min(1.0, frac))
        self.bar.setValue(int(self._frac * 1000))
        self.lbl_pct.setText(f"{int(self._frac * 100)}%")

    def _tick(self):
        el = time.time() - self._t0
        # remaining time from the progress actually made, once there is enough
        # of it to go on; the fixed estimate before that
        if self._frac > 0.05:
            left = el * (1 - self._frac) / self._frac
        else:
            left = max(0.0, estimate_seconds() - el)
        fmt = lambda s: "%d:%02d" % (int(s) // 60, int(s) % 60)
        self.lbl_time.setText(gtr("elapsed").format(el=fmt(el), left=fmt(left)))

    def _done(self, rows, err):
        self._measuring = False
        self.timer.stop()
        if getattr(self, "_paused_timer", None) is not None:
            self._paused_timer.start()
            self._paused_timer = None
        self.btn_left.setEnabled(True)
        # whatever happened, the wheel goes back to the user's own settings
        try:
            if main_window is not None:
                main_window.apply_settings(silent=True)
        except Exception:
            pass
        if err is not None or not rows or self.cancel.is_set():
            InfoBar.error(gtr("title"),
                          gtr("stopped") if self.cancel.is_set() else gtr("failed").format(err=err),
                          duration=6000, position=InfoBarPosition.TOP, parent=self.parent() or self)
            self.reject()
            return
        problems = measurement_problems(rows)
        if problems:
            InfoBar.error(gtr("title"), gtr("coarse").format(why="; ".join(problems)),
                          duration=12000, position=InfoBarPosition.TOP, parent=self.parent() or self)
            self.reject()
            return
        try:
            self.model = model_from_rows(rows)
        except Exception as e:
            InfoBar.error(gtr("title"), gtr("failed").format(err=e), duration=6000,
                          position=InfoBarPosition.TOP, parent=self.parent() or self)
            self.reject()
            return
        for r in self.run_steps:
            r.set_state("done", "")
        self._show_result(rows)

    def _show_result(self, rows, n=None, body=None):
        """The result page, for a fresh measurement or for editing a saved LUT
        (then n and body are the ones it was saved with)."""
        self.rows = rows
        th = thresholds_of(rows)
        real, drift, _ = drift_is_real(rows)
        self.verdict = probe_verdict(rows, statistics.median(th))
        speed = self.verdict.get("verdict") == "speed"
        self.st_thr.value.setText(f"{min(th):.0f} - {max(th):.0f}  / {FORCE_MAX}")
        self.st_heat.value.setText(f"{100 * (drift - 1):+.1f}%")
        self.st_heat.note.setText(gtr("heat_fixed") if real else gtr("heat_noise"))
        self.st_shape.value.setText(gtr("shape_speed") if speed else gtr("shape_wheel"))
        self.lbl_verdict.setText(gtr("verdict_speed") if speed else gtr("verdict_measured"))
        self.sl_body.setValue(int(round(100 * body)) if body is not None else (0 if speed else 100))
        span = self.model.n_max - self.model.n_min
        n0 = min(max(N_DEFAULT if n is None else n, self.model.n_min), self.model.n_max)
        self.sl_centre.setValue(int(round(100 * (self.model.n_max - n0) / span)) if span > 0 else 50)
        self.lbl_title.setText(gtr("edit_title") if self.edit else gtr("res_title"))
        self.lbl_sub.setText(gtr("edit_sub").format(name=os.path.basename(self.edit["path"]))
                             if self.edit else gtr("res_sub"))
        self._show_page(2)
        self.btn_left.setText(gtr("discard"))
        self.btn_right.setText(gtr("save"))
        self.btn_right.setVisible(True)
        self.btn_right.setEnabled(True)
        self.lbl_foot.setText(gtr("measured").format(n=len(th)))
        self._redraw()

    # -- shaping -----------------------------------------------------------
    def _values(self):
        # The slider runs calm (left) to lively (right). A small n is the
        # lively end - the curve starts just under the threshold - so the
        # scale is reversed. It was not, at first, and each label did what
        # the other one said.
        span = self.model.n_max - self.model.n_min
        n = self.model.n_max - span * self.sl_centre.value() / 100.0
        return n, self.sl_body.value() / 100.0

    def _redraw(self):
        if self.model is None:
            return
        n, body = self._values()
        self.curve = self.model.build(n, body)
        self.preview.set_points(self.curve)
        self.lbl_centre.setText(f"n = {n:.3f}")
        self.lbl_body.setText(f"{int(round(body * 100))}%")
        issues = validate(self.curve, self.model)
        self.lbl_issue.setText(gtr("invalid").format(why="; ".join(issues)) if issues else "")
        self.btn_right.setEnabled(not issues)

    # -- saving ------------------------------------------------------------
    def _save(self):
        n, body = self._values()
        base = "".join(c for c in (active_profile.get("name") or "wheel")
                       if c.isalnum() or c in "-_") or "wheel"
        lut_dir = _lut_dir()
        if self.edit:
            path = self.edit["path"]
            name = os.path.basename(path)
            csv_path = self.edit.get("csv") or ""
            # a measurement picked from elsewhere is copied in beside the LUT,
            # so the next edit finds it without asking
            if csv_path and os.path.dirname(os.path.abspath(csv_path)) != os.path.abspath(lut_dir):
                dst = os.path.join(lut_dir, f"{base}_{time.strftime('%Y%m%d-%H%M%S')}.csv")
                try:
                    save_csv(dst, self.rows)
                    csv_path = dst
                except Exception:
                    pass
            wheel = read_lut_meta(path).get("wheel") or active_profile.get("name", "")
        else:
            tag = "S" if body > 0.99 else ("duz" if body < 0.01 else "mix%02d" % round(body * 100))
            name = f"{base}_{tag}.lut"
            k = 2
            while os.path.exists(os.path.join(lut_dir, name)):   # never replace an older LUT
                name = f"{base}_{tag}_{k}.lut"
                k += 1
            path = os.path.join(lut_dir, name)
            # every LUT keeps its own measurement: one shared file per wheel
            # meant a second measurement silently replaced what the first
            # LUT would be reshaped from
            csv_path = os.path.join(lut_dir, f"{base}_{time.strftime('%Y%m%d-%H%M%S')}.csv")
            try:
                save_csv(csv_path, self.rows)
            except Exception:
                csv_path = ""
            wheel = active_profile.get("name", "")
        th = thresholds_of(self.rows)
        meta = {
            "LWH LUT Generator": HUB_VERSION,
            "wheel": wheel,
            "body": f"{body:.2f} (0 straight, 1 measured)",
            "n": f"{n:.4f} (range {self.model.n_min:.4f}..{self.model.n_max:.4f})",
            "thresholds": f"{min(th):.0f}..{max(th):.0f} /127 over {len(th)} measurements",
            "probe": self.verdict.get("verdict", "unknown"),
        }
        if csv_path:
            meta["measurement"] = os.path.basename(csv_path)
        write_lut(path, self.curve, meta)
        try:
            self.lut_tab.adopt_generated(name)
        except Exception:
            pass
        InfoBar.success(gtr("title"), (gtr("edit_saved") if self.edit else gtr("saved")).format(name=name),
                        duration=5000, position=InfoBarPosition.TOP, parent=self.parent() or self)
        self.accept()

    def reject(self):
        # Esc lands here (QDialog's key handler calls reject, not close).
        # While the wheel is being measured it must act as Stop: hiding the
        # window left the measurement driving the wheel for minutes with
        # nothing on screen to stop it, and the generator could be opened
        # a second time on top of it.
        if getattr(self, "_measuring", False):
            if not self.cancel.is_set():
                self._on_left()
            return
        super().reject()

    def closeEvent(self, e):
        self.cancel.set()
        if self.thread is not None and self.thread.isRunning():
            self.thread.wait(3000)
        if getattr(self, "_paused_timer", None) is not None:
            self._paused_timer.start()
            self._paused_timer = None
        super().closeEvent(e)

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

        mrow = QHBoxLayout(); mrow.setSpacing(8)
        self.lbl_mode = StrongBodyLabel(tr("lut.mode"))
        self.combo_mode = ComboBox(); self.combo_mode.setMinimumWidth(200)
        self.combo_mode.addItems([tr("lut.mode_proxy"), tr("lut.mode_direct")])
        self.combo_mode.currentIndexChanged.connect(self._on_mode)
        mrow.addWidget(self.lbl_mode); mrow.addWidget(self.combo_mode)
        mrow.addStretch(1)
        lay.addLayout(mrow)
        self.mode_hint = CaptionLabel("")
        self.mode_hint.setWordWrap(True); self.mode_hint.setMinimumWidth(1)
        lay.addWidget(self.mode_hint)
        lay.addSpacing(8)

        row = QHBoxLayout(); row.setSpacing(8)
        self.lbl_sel = StrongBodyLabel(tr("lut.select"))
        self.combo = ComboBox(); self.combo.setMinimumWidth(200)
        self.combo.currentIndexChanged.connect(self._on_combo)
        self.btn_edit = PushButton(gtr("edit_btn"))
        self.btn_edit.clicked.connect(self._edit_lut)
        self.btn_import = PushButton(tr("lut.import"))
        self.btn_import.clicked.connect(self._import)
        self.btn_delete = ToolButton(FIF.DELETE)
        self.btn_delete.setToolTip(tr("lut.delete"))
        self.btn_delete.clicked.connect(self._delete_lut)
        row.addWidget(self.lbl_sel); row.addWidget(self.combo, 1)
        row.addWidget(self.btn_edit); row.addWidget(self.btn_import); row.addWidget(self.btn_delete)
        lay.addLayout(row); lay.addSpacing(8)

        self.curve = LutCurveWidget()
        lay.addWidget(self.curve, 1)
        self.empty_hint = CaptionLabel(tr("lut.empty"))
        lay.addWidget(self.empty_hint)

        lay.addSpacing(12)
        self.h_gen = section_header(gtr("sec"))
        lay.addWidget(self.h_gen)
        self.gen_hint = CaptionLabel(gtr("intro"))
        self.gen_hint.setWordWrap(True)
        lay.addWidget(self.gen_hint)
        grow = QHBoxLayout()
        self.btn_gen = PrimaryPushButton(gtr("btn"))
        self.btn_gen.clicked.connect(self._open_lutgen)
        grow.addWidget(self.btn_gen)
        grow.addStretch(1)
        lay.addLayout(grow)

        self._refresh_files()

    # ---- data helpers ----
    def _open_lutgen(self):
        LutGenWizard(self.window(), self).exec()

    def adopt_generated(self, fname):
        """Take a curve the generator just wrote: select it, switch it on for
        this profile and publish it, so it is in use without another click."""
        self._refresh_files(keep=fname)
        if global_settings.get("selected_profile", "Global") == "Global":
            # Global has no game. Switching the LUT on here made every profile
            # created afterwards inherit it, and each of their games got the
            # DLL. The file is saved; the person picks it in a game's profile.
            self._refresh_files()
            _defer_infobar("info", tr("lut.gen_global_title"), tr("lut.gen_global_body").format(fname),
                           duration=7000, position=InfoBarPosition.TOP, parent=self.window())
            return
        self.select_file(fname)
        p = self._cur_prof()
        p["lut_file"] = fname
        p["lut_enabled"] = True
        self.cb_enable.setChecked(True)
        save_settings()
        self._draw_current()
        self._publish()

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
        try:
            gen = bool(fn) and lut_is_generated(read_lut_meta(os.path.join(_lut_dir(), fn)))
            self.btn_edit.setEnabled(gen and not getattr(self, "_is_global", False))
            self.btn_edit.setToolTip(gtr("edit_tip") if gen else gtr("edit_tip_imported"))
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
        """Push every profile's LUT choice to the proxy via registry.

        Each game's proxy now reads a key scoped to its OWN exe (see
        set_active_lut/_proxy_game_key), so this must publish every profile
        that has an exe assigned — not just whichever one happens to be
        selected in the LWH window. Otherwise switching which profile is
        shown here would never update a different game's own key, and an
        already-published game could be left stuck on a stale value.
        """
        # One entry per game. Settings from before the one-game-one-profile
        # rule can still bind two profiles to one exe; then the selected
        # profile wins, otherwise the first - never whichever came last.
        profs = global_settings["profiles"]
        sel = global_settings.get("selected_profile")
        chosen = {}
        for name, pp in profs.items():
            key = _exe_key(pp.get("exe_path", ""))
            if key and (key not in chosen or name == sel):
                chosen[key] = pp
        for pp in chosen.values():
            exe = pp.get("exe_path", "")
            fn = pp.get("lut_file", "")
            # Direct mode leaves the proxy in passthrough: a DLL still present
            # for this exe must not apply the curve on top of the driver's.
            on = (bool(pp.get("lut_enabled", False))
                  and pp.get("lut_mode", "proxy") != "direct")
            set_active_lut(os.path.join(_lut_dir(), fn) if (on and fn) else "", exe)
        # Legacy global key: mirror the profile actually shown right now, for
        # any proxy DLL that hasn't been reinstalled since the per-game update.
        prof = self._cur_prof()
        fn = prof.get("lut_file", "")
        # Same rule as the per-game keys above: in direct mode the driver owns
        # the curve, so an old proxy reading this key must stay in passthrough
        # rather than bend the force a second time.
        on = (bool(prof.get("lut_enabled", False))
              and prof.get("lut_mode", "proxy") != "direct")
        set_active_lut(os.path.join(_lut_dir(), fn) if (on and fn) else "")

    # ---- events ----
    def _on_mode(self, *_):
        if getattr(self, "_guard", False):
            return
        prof = self._cur_prof()
        want = "direct" if self.combo_mode.currentIndex() == 1 else "proxy"
        if want == "direct" and not live_gain_supported():
            # The block is verified at run time now rather than assumed from
            # the model, so this refuses only when the wheel really does not
            # expose the curve - not merely because it is not a G27.
            self._guard = True
            self.combo_mode.setCurrentIndex(0)
            self._guard = False
            _defer_infobar("warning", tr("lut.mode_unsupported"),
                           tr("lut.mode_unsupported_h"), duration=4000,
                           position=InfoBarPosition.TOP, parent=self.window())
            return
        prof["lut_mode"] = want
        save_settings()
        # The two paths must never shape the same force at once: switching to
        # the driver removes the DLL this profile had installed, and switching
        # back puts it there again.
        self._sync_proxy()
        self._publish()
        self._sync_mode_hint()

    def refresh(self):
        # Called by the window's timer while this tab is shown: the driver
        # path's state changes when a game starts force feedback, and the
        # hint should follow without reopening the tab.
        self._hint_n = getattr(self, "_hint_n", 0) + 1
        if self._hint_n % 60 == 0 and self.combo_mode.currentIndex() == 1:
            self._sync_mode_hint()

    def _sync_mode_hint(self):
        direct = self.combo_mode.currentIndex() == 1
        block = live_block_state() if direct else "ok"
        if block == "ok":
            self.mode_hint.setText(tr("lut.mode_direct_h") if direct
                                   else tr("lut.mode_proxy_h"))
            self.mode_hint.setStyleSheet("")
        else:
            self.mode_hint.setText(tr("lut.direct_waiting") if block == "waiting"
                                   else tr("lut.direct_none"))
            self.mode_hint.setStyleSheet("color:#e0a030;")
        # The online warning is about the DLL sitting next to the game. In
        # driver mode nothing is placed there, so repeating it would be
        # scaremongering - state the actual situation instead.
        self.warn.setText(tr("lut.warn_direct") if direct else tr("lut.warn"))
        self.warn.setStyleSheet("" if direct else "color:#e0a030;")

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
        # In DLL-free mode the driver applies the curve, so the helper DLL is
        # not just unnecessary - leaving it would shape the force twice.
        if prof.get("lut_enabled") and prof.get("lut_mode", "proxy") != "direct":
            install_proxy_ui(exe, self.window())
            return
        # Don't pull the DLL out from under another profile that still needs it.
        # A profile in direct mode does not need the DLL, so it must not keep
        # one alive for this exe - that would let the proxy and the driver both
        # bend the same force.
        still_needed = any(
            pp is not prof and pp.get("lut_enabled")
            and pp.get("lut_mode", "proxy") != "direct"
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
        # Checked before anything is written: a file that is not a LUT is
        # refused, and one with the name of a LUT already here asks first -
        # the copy used to go straight over it.
        fname = os.path.basename(path)
        if not fname.lower().endswith(".lut"):
            fname += ".lut"
        dst = os.path.join(_lut_dir(), fname)
        if not parse_lut_file(path):
            _defer_infobar("warning", tr("lut.import_fail"), "", duration=2500,
                            position=InfoBarPosition.TOP, parent=self.window())
            return
        same = os.path.abspath(path) == os.path.abspath(dst)
        if not same and os.path.exists(dst) and not _confirm_dialog(
                tr("lut.replace_title"), tr("lut.replace_body").format(fname),
                self.window(), ok_key="dlg.overwrite"):
            return
        try:
            if not same:
                tmp = dst + ".tmp"
                with open(path, "rb") as s, open(tmp, "wb") as d:
                    d.write(s.read())
                os.replace(tmp, dst)
                import_measurement(path, _lut_dir())
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

    def _edit_lut(self):
        """Reshape a generated LUT from the measurement it was made from.

        Only generated LUTs: the centre and body settings recompute the curve
        from the wheel's measured thresholds and torque. An imported file is
        only points - there is nothing to recompute from, and no measured
        threshold to keep the start safely under."""
        fn = self._current_file()
        if not fn:
            return
        path = os.path.join(_lut_dir(), fn)
        meta = read_lut_meta(path)
        if not lut_is_generated(meta):
            return
        mpath = lut_measurement_path(path, meta)
        if mpath is None:
            mpath, _ = QFileDialog.getOpenFileName(
                self.window(), gtr("edit_find"), _lut_dir(), "CSV (*.csv);;All files (*.*)")
            if not mpath:
                return
        try:
            rows = load_csv(mpath)
            problems = measurement_problems(rows)
            if problems or not thresholds_of(rows):
                raise ValueError("; ".join(problems) or "no thresholds in this file")
        except Exception as e:
            _defer_infobar("warning", gtr("edit_title"), gtr("edit_bad").format(why=e),
                           duration=6000, position=InfoBarPosition.TOP, parent=self.window())
            return
        edit = {"path": path, "rows": rows, "csv": mpath,
                "n": lut_meta_float(meta, "n", None), "body": lut_meta_float(meta, "body", None)}
        LutGenWizard(self.window(), self, edit=edit).exec()

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
        self._is_global = is_global
        for w in (self.cb_enable, self.combo, self.btn_import, self.btn_delete, self.btn_edit):
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
        # The stored choice, as it is: a driver-path profile opened on a wheel
        # where that path is not proven used to show the DLL here, though no
        # DLL was installed and no LUT reached the wheel. The hint under it
        # now says what is really happening (A17).
        mode = prof.get("lut_mode", "proxy")
        self._guard = True
        self.combo_mode.setCurrentIndex(1 if mode == "direct" else 0)
        self._guard = False
        self._sync_mode_hint()
        self.combo_mode.setEnabled(not is_global)
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
        self.lbl_mode.setText(tr("lut.mode"))
        self._guard = True
        i = self.combo_mode.currentIndex()
        self.combo_mode.clear()
        self.combo_mode.addItems([tr("lut.mode_proxy"), tr("lut.mode_direct")])
        self.combo_mode.setCurrentIndex(i)
        self._guard = False
        self._sync_mode_hint()
        self.lbl_sel.setText(tr("lut.select"))
        self.btn_import.setText(tr("lut.import"))
        self.btn_edit.setText(gtr("edit_btn"))
        self.btn_delete.setToolTip(tr("lut.delete"))
        self.empty_hint.setText(tr("lut.empty"))
        self.h_gen._lbl.setText(gtr("sec"))
        self.gen_hint.setText(gtr("intro"))
        self.btn_gen.setText(gtr("btn"))
        # rebuild "(none)" label
        cur = self._current_file()
        self._refresh_files(keep=cur)
        self.curve.update()

    def restyle(self):
        self.h_sec._bar.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")
        _restyle_accent(self.global_notice)
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

    def restyle(self):
        # This tab had no restyle at all, so its section bars and the strength
        # value kept the previous theme's colours until the app restarted.
        for hd in (self.h_test, self.h_adv):
            hd._bar.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")
        _restyle_accent(self.s_strength._val)

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

    def _take(self):
        """A new test takes the wheel: anything still running from an
        earlier one (a vibration thread, a pulse's delayed stop) ends instead
        of carrying on underneath - "Stop all forces" included."""
        self._gen = getattr(self, "_gen", 0) + 1
        return self._gen

    def _push(self, d):
        if not self._ready(): return
        self._take()
        ffb_write(constant_force_cmd(d, self._str()))

    def _stop(self):
        self._take()
        self._spring_on = self._sweep_on = False; self._sweep_t.stop()
        self.btn_spring.setText(tr("ffb.spring")); self.btn_sweep.setText(tr("ffb.sweep"))
        ffb_write(stop_forces_cmd()); ffb_write(autocenter_cmd(0))

    def _toggle_spring(self):
        if not self._spring_on and not self._ready(): return
        self._spring_on = not self._spring_on
        if self._spring_on:
            self._take()
            ffb_write(autocenter_cmd(self._str(), _active_ramp())); self.btn_spring.setText(tr("ffb.spring_stop"))
        else:
            ffb_write(autocenter_cmd(0)); self.btn_spring.setText(tr("ffb.spring"))

    def _toggle_sweep(self):
        if not self._sweep_on and not self._ready(): return
        self._sweep_on = not self._sweep_on
        if self._sweep_on:
            self._take()
            self.btn_sweep.setText(tr("ffb.sweep_stop")); self._sweep_t.start(450)
        else:
            self.btn_sweep.setText(tr("ffb.sweep")); self._sweep_t.stop(); ffb_write(stop_forces_cmd())

    def _sweep_step(self):
        ffb_write(constant_force_cmd(self._sweep_dir, self._str()))
        self._sweep_dir = "right" if self._sweep_dir == "left" else "left"

    def _pulse(self, d):
        if not self._ready(): return
        g = self._take()
        ffb_write(constant_force_cmd(d, min(100, self._str() + 40)))
        QTimer.singleShot(150, lambda: g == self._gen and ffb_write(stop_forces_cmd()))

    def _vibe(self, strength, period):
        if not self._ready(): return
        g = self._take()
        def run():
            t0 = time.time(); cur = strength
            while time.time() - t0 < 1.5 and g == self._gen:
                ffb_write([SLOT0_MASK | CMD_DOWNLOAD_PLAY, 0x00, max(0, min(255, 0x80 + cur)), 0, 0, 0, 0]); cur = -cur; time.sleep(period)
            if g == self._gen:
                ffb_write(stop_forces_cmd())
        threading.Thread(target=run, daemon=True).start()

    def _reset_driver(self):
        restore_ffb_defaults()
        _defer_infobar("success", tr("ffb.reset_ok"), tr("ffb.reset_ok_body"),
                        duration=2500, position=InfoBarPosition.TOP, parent=self.window())


# --------------------------------------------------------------------
#  Key assignment (wheel buttons as keyboard keys and macros)
# --------------------------------------------------------------------
_KM_G27 = ["paddle_left", "paddle_right", "wheel_lt", "wheel_rt", "wheel_lm", "wheel_rm", "wheel_lb", "wheel_rb",
           "dpad_up", "dpad_down", "dpad_left", "dpad_right", "sh_triangle", "sh_square", "sh_circle", "sh_x",
           "red_1", "red_2", "red_3", "red_4", "gear_1", "gear_2", "gear_3", "gear_4", "gear_5", "gear_6", "gear_r"]
_KM_DFGT = ["paddle_left", "paddle_right", "up", "dn", "l2", "r2", "l3", "r3", "dpad_up", "dpad_down",
            "dpad_left", "dpad_right", "sh_triangle", "sh_square", "sh_circle", "sh_x", "horn", "plus", "minus",
            "dial_left", "dial_enter", "dial_right", "select", "start", "ps"]
_KM_DPAD = ["dpad_up", "dpad_down", "dpad_left", "dpad_right"]


def keymap_buttons(prof=None):
    """The buttons of this wheel that can be given keys, by the names the
    key mapper sees them under (see pressed_names)."""
    prof = active_profile if prof is None else prof
    doc = documented_buttons(prof)
    if doc:
        return [n for _, _, n in doc] + (_KM_DPAD if prof.get("hat") else [])
    if prof is DEVICE_PROFILES.get("G25") and not learned_buttons(prof):
        return [b for b in _KM_G27 if b not in G27_ONLY]
    if layout_unknown(prof):
        n = len(learned_buttons(prof) or []) or (getattr(usage_reader, "button_count", 0)
                                                  if usage_reader is not None else 0)
        return [str(i) for i in range(1, n + 1)]
    if prof is DEVICE_PROFILES.get("G27"):
        return list(_KM_G27)
    return list(_KM_DFGT)


def keymap_button_label(b):
    fixed = {"paddle_left": tr("input.lpad"), "paddle_right": tr("input.rpad"),
             "wheel_lt": "L1", "wheel_lm": "L2", "wheel_lb": "L3", "wheel_rt": "R1", "wheel_rm": "R2",
             "wheel_rb": "R3", "l2": "L2", "r2": "R2", "l3": "L3", "r3": "R3", "sh_triangle": "△",
             "sh_square": "□", "sh_circle": "○", "sh_x": "✕", "dpad_up": "D-pad ↑",
             "dpad_down": "D-pad ↓", "dpad_left": "D-pad ←", "dpad_right": "D-pad →",
             "up": "UP", "dn": "DN", "horn": "GT", "plus": "+", "minus": "−", "dial_left": "◀",
             "dial_right": "▶", "dial_enter": "↵", "select": "SELECT", "start": "START", "ps": "PS"}
    if b in fixed:
        return fixed[b]
    if b.startswith("gear_"):
        return tr("keymap.gear_n").format(b[5:].upper())
    if b.startswith("red_"):
        return tr("keymap.shifter_n").format(b[4:])
    if b.isdigit():
        return tr("keymap.button_n").format(b)
    return b


def keymap_text(act):
    if not act:
        return tr("keymap.none")
    if "keys" in act:
        return keys_label(act["keys"])
    return tr("keymap.macro_n").format(len(act.get("macro", [])))


def _step_text(st):
    kind, val = st
    if kind == "wait":
        return "⏱  %d ms" % val
    arrow = {"down": "↓", "up": "↑", "tap": "⇅"}.get(kind, "")
    return "%s  %s" % (arrow, key_label(val))


class _KeyCatcher(PushButton):
    """A button that listens to the keyboard once clicked. In combination
    mode it hands over the keys held together when the last one is let go;
    in macro mode every key going down and up, with the time between."""
    combo = Signal(list)
    step = Signal(str, str, int)          # kind, key, ms since the last event

    def __init__(self, macro=False, parent=None):
        super().__init__(parent)
        self.macro = macro
        self.listening = False
        self.keys, self.held, self.last = [], [], 0.0
        self.idle_text = ""
        self.clicked.connect(self.start)
        self.setFocusPolicy(Qt.StrongFocus)

    def start(self):
        self.listening = True
        self.keys, self.held, self.last = [], [], 0.0
        self.setText(tr("keymap.recording_n").format(0) if self.macro else tr("keymap.press"))
        self.grabKeyboard()
        self.setFocus()

    def stop(self):
        if self.listening:
            self.listening = False
            self.releaseKeyboard()
        self.setText(self.idle_text)

    def focusOutEvent(self, e):
        self.stop()
        super().focusOutEvent(e)

    def _name(self, e):
        return key_from_event(int(e.key()), int(e.nativeScanCode()))

    def keyPressEvent(self, e):
        if not self.listening:
            return super().keyPressEvent(e)
        if e.isAutoRepeat():
            return
        n = self._name(e)
        if not n:
            return
        if self.macro:
            now = time.monotonic()
            ms = int((now - self.last) * 1000) if self.last else 0
            self.last = now
            self.step.emit("down", n, ms)
            self.keys.append(n)
            self.setText(tr("keymap.recording_n").format(len(self.keys)))
            return
        if n not in self.keys and len(self.keys) < 6:
            self.keys.append(n)
        if n not in self.held:
            self.held.append(n)
        mods = [k for k in self.keys if k in _MODS] + [k for k in self.keys if k not in _MODS]
        self.setText(keys_label(mods))

    def keyReleaseEvent(self, e):
        if not self.listening:
            return super().keyReleaseEvent(e)
        if e.isAutoRepeat():
            return
        n = self._name(e)
        if not n:
            return
        if self.macro:
            now = time.monotonic()
            ms = int((now - self.last) * 1000) if self.last else 0
            self.last = now
            self.step.emit("up", n, ms)
            return
        if n in self.held:
            self.held.remove(n)
        if not self.held and self.keys:
            mods = [k for k in self.keys if k in _MODS] + [k for k in self.keys if k not in _MODS]
            self.listening = False
            self.releaseKeyboard()
            self.combo.emit(mods)


class KeymapDialog(QDialog):
    """LGS's "Assign Command": a wheel button presses keyboard keys or plays
    a macro, in every game, for the selected profile. Saved at once. Press a
    button on the wheel to pick it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("keymap.title"))
        self.resize(640, 520)
        self.prof_name = global_settings.get("selected_profile", "Global")
        self.buttons = keymap_buttons()
        self._prev = set()
        self._loading = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18); lay.setSpacing(10)
        lay.addWidget(SubtitleLabel(tr("keymap.title")))
        sub = CaptionLabel(tr("keymap.help").format(self.prof_name)); sub.setWordWrap(True)
        lay.addWidget(sub)

        row = QHBoxLayout(); row.setSpacing(14)
        lay.addLayout(row, 1)
        from PySide6.QtWidgets import QListWidget, QSpinBox
        self.lst = QListWidget()
        self.lst.setMinimumWidth(240)
        row.addWidget(self.lst, 1)

        right = QVBoxLayout(); right.setSpacing(8)
        row.addLayout(right, 1)
        self.lbl_btn = StrongBodyLabel("")
        right.addWidget(self.lbl_btn)
        self.mode = ComboBox()
        self.mode.addItems([tr("keymap.none"), tr("keymap.keys"), tr("keymap.macro")])
        self.mode.currentIndexChanged.connect(self._mode_picked)
        right.addWidget(self.mode)

        self.catch = _KeyCatcher(False)
        self.catch.combo.connect(self._got_combo)
        right.addWidget(self.catch)

        self.macro_box = QWidget()
        mb = QVBoxLayout(self.macro_box); mb.setContentsMargins(0, 0, 0, 0); mb.setSpacing(6)
        self.rec = _KeyCatcher(True)
        self._recording = None
        self.rec.clicked.connect(lambda *_: setattr(self, "_recording", []))
        self.rec.step.connect(self._got_step)
        mb.addWidget(self.rec)
        self.cb_delays = CheckBox(tr("keymap.delays")); self.cb_delays.setChecked(True)
        mb.addWidget(self.cb_delays)
        self.cb_repeat = CheckBox(tr("keymap.repeat"))
        self.cb_repeat.clicked.connect(lambda *_: self._save_macro())
        mb.addWidget(self.cb_repeat)
        self.steps = QListWidget()
        mb.addWidget(self.steps, 1)
        wr = QHBoxLayout(); wr.setSpacing(6)
        self.sp_wait = QSpinBox(); self.sp_wait.setRange(0, MACRO_MAX_WAIT); self.sp_wait.setValue(50)
        self.sp_wait.setSuffix(" ms")
        wr.addWidget(self.sp_wait)
        self.b_wait = PushButton(tr("keymap.add_wait")); self.b_wait.clicked.connect(self._add_wait)
        wr.addWidget(self.b_wait)
        self.b_del = PushButton(tr("keymap.remove_step")); self.b_del.clicked.connect(self._del_step)
        wr.addWidget(self.b_del)
        mb.addLayout(wr)
        right.addWidget(self.macro_box, 1)
        self.b_clear = PushButton(tr("keymap.clear")); self.b_clear.clicked.connect(self._clear)
        right.addWidget(self.b_clear)
        right.addStretch(0)

        foot = QHBoxLayout(); foot.addStretch(1)
        b_close = PrimaryPushButton(tr("keymap.close")); b_close.clicked.connect(self.accept)
        foot.addWidget(b_close)
        lay.addLayout(foot)

        self._fill()
        self.lst.currentRowChanged.connect(lambda *_: self._show())
        if self.buttons:
            self.lst.setCurrentRow(0)
        self._show()
        self._t = QTimer(self); self._t.timeout.connect(self._poll); self._t.start(40)

    # ---- data
    def _prof(self):
        return global_settings["profiles"].setdefault(self.prof_name, {})

    def _map(self):
        return clean_keymap(self._prof().get("keymap") or {})

    def _store(self, m):
        m = clean_keymap(m)
        self._prof()["keymap"] = m
        save_settings()
        if global_settings.get("selected_profile") == self.prof_name:
            keymapper.set_map(m)
        self._fill(keep=True)
        self._show()

    def _sel(self):
        i = self.lst.currentRow()
        return self.buttons[i] if 0 <= i < len(self.buttons) else ""

    # ---- view
    def _fill(self, keep=False):
        row = self.lst.currentRow()
        self.lst.blockSignals(True)
        self.lst.clear()
        m = self._map()
        for b in self.buttons:
            act = m.get(b)
            self.lst.addItem(keymap_button_label(b) + ("   —   " + keymap_text(act) if act else ""))
        self.lst.blockSignals(False)
        if keep and row >= 0:
            self.lst.setCurrentRow(row)

    def _show(self):
        b = self._sel()
        act = self._map().get(b) if b else None
        self._loading = True
        self.lbl_btn.setText(keymap_button_label(b) if b else tr("keymap.pick"))
        want = getattr(self, "_mode_override", None)
        idx = want if want is not None else (0 if not act else 1 if "keys" in act else 2)
        self.mode.setCurrentIndex(idx)
        self.mode.setEnabled(bool(b))
        self.catch.setVisible(idx == 1)
        self.catch.idle_text = keys_label(act["keys"]) if act and "keys" in act else tr("keymap.click")
        self.catch.stop()
        self.macro_box.setVisible(idx == 2)
        self.rec.idle_text = tr("keymap.record")
        self.rec.stop()
        self.cb_repeat.setChecked(bool(act and act.get("repeat")))
        self.steps.clear()
        for st in (act.get("macro", []) if act and "macro" in act else []):
            self.steps.addItem(_step_text(st))
        self.b_clear.setEnabled(bool(act))
        self._loading = False

    # ---- editing
    def _mode_picked(self, idx):
        if self._loading:
            return
        b = self._sel()
        if not b:
            return
        if idx == 0:
            m = self._map(); m.pop(b, None)
            self._mode_override = None
            self._store(m)
            return
        self._mode_override = idx
        self._show()
        self._mode_override = None

    def _got_combo(self, keys):
        b = self._sel()
        if b and keys:
            m = self._map(); m[b] = {"keys": keys}
            self._store(m)

    def _macro_steps(self):
        act = self._map().get(self._sel())
        return [list(s) for s in act["macro"]] if act and "macro" in act else []

    def _save_macro(self, steps=None):
        if self._loading:
            return
        b = self._sel()
        if not b:
            return
        steps = self._macro_steps() if steps is None else steps
        m = self._map()
        if any(s[0] != "wait" for s in steps):
            m[b] = {"macro": steps, "repeat": self.cb_repeat.isChecked()}
        else:
            m.pop(b, None)
        self._mode_override = 2
        self._store(m)
        self._mode_override = None

    def _got_step(self, kind, key, ms):
        steps = self._recording if self._recording is not None else []
        if ms and steps:
            steps.append(["wait", max(10, min(MACRO_MAX_WAIT, round(ms / 10) * 10))
                          if self.cb_delays.isChecked() else 30])
        steps.append([kind, key])
        self._recording = steps
        b = self._sel()
        if b and any(s[0] != "wait" for s in steps):
            m = self._map(); m[b] = {"macro": list(steps), "repeat": self.cb_repeat.isChecked()}
            self._prof()["keymap"] = clean_keymap(m)
            self.steps.clear()
            for st in steps:
                self.steps.addItem(_step_text(st))

    def _add_wait(self):
        self._save_macro(self._macro_steps() + [["wait", int(self.sp_wait.value())]])

    def _del_step(self):
        i = self.steps.currentRow()
        st = self._macro_steps()
        if 0 <= i < len(st):
            del st[i]
            self._save_macro(st)

    def _clear(self):
        b = self._sel()
        if b:
            m = self._map(); m.pop(b, None)
            self._store(m)

    def _poll(self):
        """A button pressed on the wheel is the one picked."""
        if self.catch.listening or self.rec.listening:
            return
        if self._recording:
            # a recording just ended: keep it
            self._recording = None
            self._store(self._map())
        raw = (state.get("raw") or []) if state.get("connected") else []
        now = pressed_names(active_profile, raw) if raw else set()
        new = now - self._prev
        self._prev = now
        for b in sorted(new):
            if b in self.buttons:
                self.lst.setCurrentRow(self.buttons.index(b))
                break

    def done(self, r):
        self._t.stop()
        self.catch.stop(); self.rec.stop()
        if self._recording:
            self._recording = None
            self._store(self._map())
        super().done(r)


class InputMonitorTab(QWidget):
    def __init__(self):
        super().__init__()
        # No scroll area: the InputMonitor has a fixed footprint, so letting the
        # layout expose its real minimum makes the window refuse to shrink past
        # the point where buttons would clip (instead of showing a scrollbar).
        lay = QVBoxLayout(self); lay.setContentsMargins(2, 4, 12, 8); lay.setSpacing(6)
        hrow = QHBoxLayout(); hrow.setContentsMargins(0, 0, 0, 0); hrow.setSpacing(8)
        self.hdr = section_header(tr("tab.input"))
        hrow.addWidget(self.hdr); hrow.addStretch(1)
        # Games label these buttons by their HID number ("Button 20"), the
        # wheel labels them by name (L2, START). Neither is wrong, so offer
        # both rather than picking one and leaving the user to translate.
        self.btn_mode = TransparentPushButton(tr("input.mode_numbers"))
        self.btn_mode.clicked.connect(self._toggle_mode)
        hrow.addWidget(self.btn_mode, 0, Qt.AlignVCenter)
        # LGS's "Assign Command": wheel buttons as keyboard keys and macros
        self.btn_keys = PushButton(tr("keymap.open"))
        self.btn_keys.clicked.connect(self._keymap)
        hrow.addWidget(self.btn_keys, 0, Qt.AlignVCenter)
        lay.addLayout(hrow); lay.addSpacing(6)
        self.mon = InputMonitor()
        # After self.mon exists: the label reads the restored mode off it, so
        # syncing any earlier crashes on a missing attribute.
        self._sync_mode_label()
        lay.addWidget(self.mon, 0, Qt.AlignHCenter)
        lay.addSpacing(12)
        self.led = PushButton(tr("input.led")); self.led.clicked.connect(self._led)
        lay.addWidget(self.led)
        self.led_status = CaptionLabel("")
        lay.addWidget(self.led_status)
        lay.addStretch(1)

    def _keymap(self):
        KeymapDialog(self.window()).exec()

    def _toggle_mode(self):
        self.mon.numbered = not self.mon.numbered
        global_settings["button_numbers"] = self.mon.numbered
        save_settings()
        self._sync_mode_label()
        self.mon.update()

    def _sync_mode_label(self):
        # The button says what you'd switch TO, not the current state.
        self.btn_mode.setText(tr("input.mode_names") if self.mon.numbered
                              else tr("input.mode_numbers"))

    def retranslate(self):
        self.hdr._lbl.setText(tr("tab.input")); self.led.setText(tr("input.led"))
        self.btn_keys.setText(tr("keymap.open"))
        self._sync_mode_label()
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
        self._row(lay, "about.translation", "translation")
        lay.addSpacing(8)

        # ABOUT / legal -- version, repo, license, TM notice (localized)
        self.ab_sec = section_header(tr("about.sec_about")); self.ab_sec._key = "about.sec_about"
        self._bars.append(self.ab_sec._bar)
        lay.addSpacing(2); lay.addWidget(self.ab_sec); lay.addSpacing(2)
        self.ver = StrongBodyLabel(self._ver_text())
        lay.addWidget(self.ver)
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

    # Specs we cannot stand behind on a wheel nobody here has held. Model and
    # hardware id come from the PID and are certain; encoder resolution,
    # polling rate and supply voltage were not measured, and a plausible
    # number in a field like this reads as fact.
    _UNVERIFIED = ("tracking", "axis", "polling", "power")

    def refresh(self):
        info = dict(active_profile["info"]); conn = state["connected"]
        if is_beta():
            info["model"] = f"{info['model']}  \u2013  {tr('dev.beta')}"
            for k in self._UNVERIFIED:
                info[k] = "\u2014"
        state_txt = tr("info.active") if conn else tr("info.standby")
        power = state_txt if info["power"] == "\u2014" else f"{info['power']} / {state_txt}"
        opmode = tr("info.opmode_active") if conn else tr("info.opmode_idle")
        if is_beta() and conn:
            opmode = tr("dev.beta")
        data = {"model": info["model"], "hwid": info["hwid"], "interface": info["interface"],
                "power": power, "tracking": info["tracking"], "axis": info["axis"],
                "ffb": info["ffb"], "polling": info["polling"], "opmode": opmode,
                "api": info["api"], "hub": HUB_VERSION, "author": AUTHOR,
                "translation": TRANSLATORS}
        for k, v in data.items():
            if k in self.vals:
                self.vals[k].setText(v)

    @staticmethod
    def _ver_text():
        return f"Legacy Wheel Hub  {HUB_VERSION}" + (f"  ·  {tr('about.portable')}" if PORTABLE else "")

    def retranslate(self):
        for hd in self._secs:
            hd._lbl.setText(tr(hd._key))
        for lbl in self._labels:
            lbl.setText(tr(lbl._key))
        self.ab_sec._lbl.setText(tr("about.sec_about"))
        self.ver.setText(self._ver_text())
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
    sdk_done = Signal(object, bool)
    def __init__(self, hub):
        super().__init__()
        self.hub = hub; self._bars = []; self._labels = []; self._guard = False
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background:transparent;")
        outer.addWidget(scroll)
        host = QWidget(); host.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(host); lay.setContentsMargins(2, 4, 12, 4); lay.setSpacing(4)
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
        lay.addSpacing(2)                       # a checkbox and its hint were touching
        self.tray_hint = CaptionLabel(tr("set.tray_h"))
        self.tray_hint.setWordWrap(True); lay.addWidget(self.tray_hint)
        self.cb_startup = CheckBox(tr("set.startup"))
        # Read the real registry state, not a stored preference: the user may
        # have removed the entry from Task Manager's Startup tab, and the box
        # must reflect what Windows will actually do.
        self.cb_startup.setChecked(startup_enabled())
        self.cb_startup.stateChanged.connect(self._on_startup)
        lay.addWidget(self.cb_startup)
        lay.addSpacing(2)
        self.startup_hint = CaptionLabel(tr("set.startup_h"))
        self.startup_hint.setWordWrap(True); self.startup_hint.setMinimumWidth(1)
        lay.addWidget(self.startup_hint)
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
        cur_scale = ui_scale_setting()
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
        # Built from DEVICE_PROFILES so a wheel added there shows up without a
        # second list to keep in step. Beta ones are labelled: their layouts
        # were never measured, and a selector presenting them as equals would
        # claim more than we know.
        self.combo_test.addItem(tr("about.test_auto"), userData=None)
        self._test_keys = [None]
        for key, prof in DEVICE_PROFILES.items():
            self.combo_test.addItem(
                key + (f"  \u2013 {tr('dev.beta')}" if prof.get("beta") else ""),
                userData=key)
            self._test_keys.append(key)
        self.combo_test.setCurrentIndex(
            self._test_keys.index(test_override) if test_override in self._test_keys else 0)
        self.combo_test.currentIndexChanged.connect(self._on_test)
        self.row_test = self._field(lay, "set.devmode", self.combo_test)
        self.test_hint = CaptionLabel(tr("set.devmode_h"))
        self.test_hint.setWordWrap(True); lay.addWidget(self.test_hint)
        lay.addSpacing(8)

        # Offered for every wheel, not only the ones with no measurements. A
        # G27 owner has no need of it, but a wheel whose firmware moved an
        # axis, or a report that turns out to differ between revisions, is
        # answerable here instead of in a bug report.
        self.btn_map = PushButton(tr("map.button"))
        self.btn_map.clicked.connect(self._open_map)
        # An icon beside the setup button rather than a sentence of its own.
        # "Use built-in layout" read like a second option to choose between,
        # when all it does is throw away what the wizard found.
        # The words say what it does; the icon says it removes something.
        # Neither alone was enough - an unlabelled bin looked like it might
        # delete the profile.
        self.btn_unmap = TransparentPushButton(FIF.DELETE, tr("map.clear"))
        self.btn_unmap.clicked.connect(self._clear_map)
        row_map = QHBoxLayout()
        row_map.addWidget(self.btn_map); row_map.addWidget(self.btn_unmap)
        row_map.addStretch(1)
        lay.addLayout(row_map)
        self.map_hint = CaptionLabel(tr("map.button_h"))
        self.map_hint.setWordWrap(True); lay.addWidget(self.map_hint)
        self._refresh_map_btns()




        self.sec_upd = self._sec(lay, "set.updates_sec")
        self.cb_upd = CheckBox(tr("set.updates"))
        self.cb_upd.setChecked(bool(global_settings.get("check_updates", True)))
        self.cb_upd.stateChanged.connect(self._on_updates)
        lay.addWidget(self.cb_upd)
        lay.addSpacing(2)                       # let the hint breathe
        self.upd_hint = CaptionLabel(tr("set.updates_h"))
        self.upd_hint.setWordWrap(True); self.upd_hint.setMinimumWidth(1)
        lay.addWidget(self.upd_hint)
        lay.addSpacing(8)
        self.btn_upd = PushButton(tr("set.check_now"))
        self.btn_upd.clicked.connect(self._check_updates_now)
        # Same signal the hub uses for its own result handling, so the button
        # is restored no matter how the check ended - success, "up to date",
        # or a network failure.
        try:
            self.hub.update_checked.connect(self._update_finished)
        except Exception:
            pass
        lay.addWidget(self.btn_upd)

        # Logitech's SDK registration, for the few games that need it: off
        # unless turned on here, since it puts LWH's file into those games
        self.sec_compat = self._sec(lay, "set.compat")
        srow = QHBoxLayout(); srow.setContentsMargins(0, 0, 0, 0); srow.setSpacing(8)
        self.cb_sdk = CheckBox(tr("sdk.title"))
        self.cb_sdk.clicked.connect(self._sdk_clicked)
        srow.addWidget(self.cb_sdk); srow.addStretch(1)
        self.btn_sdk_take = PushButton(tr("sdk.take_over"))
        self.btn_sdk_take.clicked.connect(lambda: self._sdk_clicked(True, take=True))
        srow.addWidget(self.btn_sdk_take)
        lay.addLayout(srow)
        lay.addSpacing(2)
        self.lbl_sdk = CaptionLabel("")
        self.lbl_sdk.setWordWrap(True); self.lbl_sdk.setMinimumWidth(1)
        lay.addWidget(self.lbl_sdk)
        self.sdk_done.connect(self._sdk_finished)
        self.refresh_sdk()
        lay.addStretch(1)

    def refresh_sdk(self):
        try:
            st = sdk_registration_state()
        except Exception:
            st = "none"
        self._sdk_state = st
        self.cb_sdk.setChecked(st == "ours")
        self.cb_sdk.setEnabled(st != "other")
        self.btn_sdk_take.setVisible(st == "other")
        self.lbl_sdk.setText((tr("sdk.state_other") + " " if st == "other" else "") + tr("sdk.h"))

    def _sdk_clicked(self, on=None, take=False):
        st = getattr(self, "_sdk_state", "none")
        on = bool(on) if take else st != "ours"
        self.cb_sdk.setChecked(st == "ours")      # follows the setting, not the click
        self._sdk_took = take
        self.cb_sdk.setEnabled(False); self.btn_sdk_take.setEnabled(False)

        def work():
            try:
                res = request_sdk_registration(on, take)
            except Exception:
                res = False
            self.sdk_done.emit(res, on)
        threading.Thread(target=work, daemon=True).start()

    def _sdk_finished(self, res, on):
        self.btn_sdk_take.setEnabled(True)
        if res is None:
            kind, key = "warning", "sdk.denied"
        elif res:
            kind, key = "success", (("sdk.done_take" if getattr(self, "_sdk_took", False) else "sdk.done_on")
                                    if on else "sdk.done_off")
        else:
            kind, key = "error", "sdk.failed"
        _defer_infobar(kind, tr("sdk.title"), tr(key), duration=6000, position=InfoBarPosition.TOP, parent=self.window())
        self.refresh_sdk()

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

    def _on_startup(self, *_):
        want = self.cb_startup.isChecked()
        if not set_startup(want):
            # Writing failed (locked-down policy, roaming profile...). Put the
            # box back so it never claims something Windows won't honour.
            self._guard = True
            self.cb_startup.setChecked(startup_enabled())
            self._guard = False

    def _on_updates(self, *_):
        global_settings["check_updates"] = self.cb_upd.isChecked(); save_settings()

    def _check_updates_now(self):
        # self.hub is the ControlHub we were constructed with. self.window()
        # depends on this widget already being parented into the window tree,
        # which isn't guaranteed - and the old silent `except: pass` turned
        # that into a button that looked dead instead of reporting anything.
        #
        # The request runs off-thread, so without feedback the button just sat
        # there looking unresponsive for however long the network took.
        self.btn_upd.setEnabled(False)
        self.btn_upd.setText(tr("set.checking"))
        self.hub.check_updates(manual=True)

    def _update_finished(self, *_):
        """Restore the button once a result (any result) comes back."""
        try:
            self.btn_upd.setEnabled(True)
            self.btn_upd.setText(tr("set.check_now"))
        except Exception:
            pass

    def _refresh_map_btns(self):
        self.btn_unmap.setVisible(saved_map(active_profile) is not None)

    def _open_map(self):
        dlg = PedalMapWizard(self.window())
        dlg.exec()
        self._refresh_map_btns()

    def _clear_map(self):
        clear_map(active_profile)
        self._refresh_map_btns()
        _defer_infobar("success", tr("map.cleared"), "",
                       duration=2000, position=InfoBarPosition.TOP,
                       parent=self.window())

    def _on_test(self, *_):
        if self._guard: return
        set_test_override(self.combo_test.currentData())
        global_settings["test_device"] = self.combo_test.currentData()
        save_settings()
        # The override changes active_profile, but the capability-driven parts
        # of the UI (clutch bar, LED test, device name) are refreshed by the
        # poller - which never runs while no wheel is connected. Refresh them
        # by hand so switching the test device actually changes the UI.
        w = self.window()
        # The rotation control depends on which wheel is selected.
        try: w.settings.wheel_tab.refresh_caps()
        except Exception: pass
        # A learned layout is stored per wheel, so the "use built-in layout"
        # button and the beta strip both belong to the newly selected one.
        self._refresh_map_btns()
        try: w._refresh_beta_bar()
        except Exception: pass
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
        self.upd_hint.setStyleSheet(f"color:{muted};")
        self.startup_hint.setStyleSheet(f"color:{muted};")

    def retranslate(self):
        self._guard = True
        for hd in (self.sec_app, self.sec_gen, self.sec_test, self.sec_upd, self.sec_compat):
            hd._lbl.setText(tr(hd._key))
        self.cb_sdk.setText(tr("sdk.title")); self.btn_sdk_take.setText(tr("sdk.take_over"))
        self.refresh_sdk()
        for lbl, key in ((self.row_theme, "set.theme"), (self.row_lang, "set.language"),
                         (self.row_test, "set.devmode")):
            lbl.setText(tr(key))
        self.cb_tray.setText(tr("set.tray")); self.tray_hint.setText(tr("set.tray_h"))
        self.cb_startup.setText(tr("set.startup")); self.startup_hint.setText(tr("set.startup_h"))
        self.cb_upd.setText(tr("set.updates")); self.upd_hint.setText(tr("set.updates_h"))
        if self.btn_upd.isEnabled():        # don't clobber "Checking..." mid-flight
            self.btn_upd.setText(tr("set.check_now"))
        self.test_hint.setText(tr("set.devmode_h"))
        self.btn_map.setText(tr("map.button"))
        self.btn_unmap.setText(tr("map.clear"))
        self.map_hint.setText(tr("map.button_h"))
        self.row_scale.setText(tr("set.ui_scale")); self.scale_hint.setText(tr("set.ui_scale_h"))
        ti = self.combo_theme.currentIndex()
        self.combo_theme.clear()
        self.combo_theme.addItem(tr("about.theme_dark"), userData="dark")
        self.combo_theme.addItem(tr("about.theme_light"), userData="light")
        self.combo_theme.setCurrentIndex(ti)
        tt = self.combo_test.currentIndex()
        self.combo_test.clear()
        self.combo_test.addItem(tr("about.test_auto"), userData=None)
        for key, prof in DEVICE_PROFILES.items():
            self.combo_test.addItem(
                key + (f"  \u2013 {tr('dev.beta')}" if prof.get("beta") else ""),
                userData=key)
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
        self.pedals_tab = PedalsTab(hub)
        self._pages = [("wheel", "tab.wheel", self.wheel_tab),
                       ("pedals", "tab.pedals", self.pedals_tab),
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
            # The pedal tab sits in a scroll area, which hides its children's
            # minimum from the column: three pedal columns were being squeezed
            # below their own minimum and the Custom button lost its last
            # letter under the panel edge. Ask the tab what it needs.
            try:
                needed = max(needed, self.pedals_tab.needed_width())
            except Exception:
                pass
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
        """Mark the gear as the current tab without losing its theming.

        QFluentWidgets styles its controls through their stylesheet, so setting
        one here replaces the theme's rules rather than adding to them - and
        the transparent background goes with them. On Windows 11 the window
        material hid it; on Windows 10 the button turned up solid white in the
        dark theme. Restating the background alongside the underline keeps
        both.
        """
        self._gear_on = on
        hover = ("rgba(255,255,255,0.08)" if isDarkTheme()
                 else "rgba(0,0,0,0.06)")
        edge = f"border-bottom:2px solid {ACCENT};" if on else ""
        # Never cleared back to "": removing our stylesheet does not restore
        # the theme's, because that was applied the same way and is gone once
        # overwritten. The button was left unstyled and fell back to Qt's
        # default - solid white, which the dark theme made obvious on Windows
        # 10. So the transparent background is restated every time instead.
        self.gear.setStyleSheet(
            "TransparentToolButton{background:transparent;border:none;"
            + edge + "}"
            f"TransparentToolButton:hover{{background:{hover};}}")

    def restyle(self):
        # Re-run on a theme switch: the hover shade above is chosen from the
        # theme, so it has to be picked again when the theme changes.
        self._gear_active(getattr(self, "_gear_on", False))

    def retranslate(self):
        for key, tkey, _ in self._pages:
            it = self.pivot.widget(key)
            if it is not None:
                try: it.setText(tr(tkey))
                except Exception: pass
        self.gear.setToolTip(tr("set.title"))
        # tab labels changed width -> re-fit the strip so nothing overlaps
        self._fit_tabstrip()
        for page in (self.wheel_tab, self.pedals_tab, self.lut_tab, self.ffb_tab, self.input_tab, self.info_tab, self.settings_tab):
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
    update_checked = Signal(object, bool)
    game_profile_changed = Signal(str)

    def __init__(self):
        super().__init__()
        global active_profile
        self.setObjectName("ControlHub")
        self.setTitleBar(CustomTitleBar(self))
        self.setWindowTitle("Legacy Wheel Hub")
        self._restore_geometry()
        self.update_checked.connect(self._on_update_result)
        self._auto_switch = False
        self.game_profile_changed.connect(self._on_game_profile)
        self._start_game_watcher()
        
        if global_settings.get("check_updates", True):
            try:
                due = time.time() - num_setting("last_update_check", 0) > 86400
            except Exception:
                due = True
            if due:
                QTimer.singleShot(3000, lambda: self.check_updates(manual=False))
                
        if not test_override and global_settings.get("last_device") in DEVICE_PROFILES:
            active_profile = DEVICE_PROFILES[global_settings["last_device"]]

        body = QWidget(self)
        bl = QHBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
        self.presets = PresetsPanel(self._on_preset)
        self.telemetry = TelemetryPanel()
        self.settings = SettingsColumn(self)
        self.wheelset = self.settings.wheel_tab
        self._applied_angle = self.rotation_value()

        # Pinned at 210 here, which overrode the panel's own 204-460 range and
        # left it at its narrowest however wide the window got.
        self.presets.setMinimumWidth(210)

        bl.addWidget(self.presets, 1)
        bl.addWidget(self._vline())
        bl.addWidget(self.telemetry, 1)
        bl.addWidget(self._vline())
        # Shares the extra width instead of handing all of it to one column.
        # The side panels stop at their maxima, so past a point the settings
        # column still takes what is left - which is right, since that is
        # where the content is.
        bl.addWidget(self.settings, 3)

        # A strip that stays put, rather than a notification that slides away
        # after a few seconds. Somebody plugging in a wheel we have never
        # measured needs to know why the telemetry is empty for as long as it
        # is empty, and where to go about it.
        self.beta_bar = QWidget()
        self.beta_bar.setVisible(False)
        bb = QHBoxLayout(self.beta_bar)
        bb.setContentsMargins(16, 7, 16, 7); bb.setSpacing(10)
        self.beta_text = BodyLabel("")
        self.beta_text.setWordWrap(True)
        bb.addWidget(self.beta_text, 1)
        self.beta_btn = PushButton(tr("dev.beta_action"))
        self.beta_btn.clicked.connect(self._open_map_from_bar)
        bb.addWidget(self.beta_btn, 0)
        # Dismissable, but only for this session: the wheel is still in beta
        # next time it is plugged in, and the strip is the only place that
        # says so.
        self.beta_close = TransparentToolButton(FIF.CLOSE)
        self.beta_close.setFixedSize(26, 26)
        self.beta_close.setToolTip(tr("dev.bar_dismiss"))
        self.beta_close.clicked.connect(self._dismiss_beta_bar)
        bb.addWidget(self.beta_close, 0)

        root = QVBoxLayout(self); root.setContentsMargins(0, self.titleBar.height(), 0, 0); root.setSpacing(0)
        root.addWidget(self.beta_bar)
        root.addWidget(body)
        self.titleBar.raise_()

        self.titleBar.apply_btn.clicked.connect(self._user_apply)
        self.titleBar.set_device(active_profile["name"])
        self.settings.select_tab(global_settings.get("last_tab", "wheel"))

        self._app_icon = QIcon(WHEEL_PNG) if os.path.exists(WHEEL_PNG) else QIcon()
        try: self.setWindowIcon(self._app_icon)
        except Exception: pass

        self.tray = None; self._build_tray()
        self._was_connected = False
        self._autoload_pending = False
        self._cal_prev = 0.0
        self._cal_connect_t = 0.0
        self._cal_last_move_t = 0.0
        self._cal_seen_sweep = False

        self._apply_palette()
        self.retranslate_all()
        
        try: self._on_preset(global_settings.get("selected_profile", "Global"), auto_apply=False)
        except Exception: pass
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(16)
        QTimer.singleShot(0, self._apply_min_size)
        # games that set the wheel themselves, and the end of what they set
        self._game_t = QTimer(self); self._game_t.timeout.connect(self._game_still_running)
        self._game_t.start(2000)
        QTimer.singleShot(0, self._start_lgs_window)

    def _apply_min_size(self):
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
        return wheel_range(getattr(self, "_applied_angle", self.rotation_value()))

    def _on_preset(self, name, auto_apply=True):
        try: self.wheelset.load_profile(name)
        except Exception: pass
        try: keymapper.set_map(global_settings["profiles"].get(name, {}).get("keymap") or {})
        except Exception: pass
        try: self.settings.lut_tab.load_profile(name)
        except Exception: pass
        # Yeni Profile Entegrasyonu: Arayüz yüklemesi 
        try: self.settings.pedals_tab.load_profile(name)
        except Exception: pass
        
        if auto_apply:
            try: self.apply_settings(silent=(dev is None or getattr(self, "_auto_switch", False)))
            except Exception: pass

    def _apply_palette(self):
        self.setStyleSheet(hub_qss())
        self.telemetry.wheel._cache_key = None
        try: self.titleBar.style_buttons()
        except Exception: pass
        self.info_restyle()

    def info_restyle(self):
        for attr in ("info_tab", "settings_tab", "lut_tab", "wheel_tab", "ffb_tab", "pedals_tab"):
            try: getattr(self.settings, attr).restyle()
            except Exception: pass
        try: self.presets.restyle()
        except Exception: pass
        try: _restyle_accent(self.titleBar.brand)
        except Exception: pass

    def set_theme(self, code):
        setTheme(Theme.LIGHT if code == "light" else Theme.DARK)
        global_settings["theme"] = code; save_settings()
        self._apply_palette()

    def retranslate_all(self):
        try: self.presets.retranslate()
        except Exception: pass
        try: self.telemetry.retranslate()
        except Exception: pass
        try: self.settings.retranslate()
        except Exception: pass
        self.titleBar.apply_btn.setText(tr("ui.apply"))
        self._refresh_status()

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
        if conn != self._was_connected:
            if conn:
                # A fresh connection is a fresh reason to say it.
                self._beta_dismissed = False
            self._refresh_beta_bar()
        if conn and not self._was_connected and global_settings.get("auto_load", False):
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

    def _open_map_from_bar(self):
        self.settings.select_tab("wheel")
        dlg = PedalMapWizard(self)
        dlg.exec()
        self._refresh_beta_bar()

    def _dismiss_beta_bar(self):
        self._beta_dismissed = True
        self.beta_bar.setVisible(False)

    def _refresh_beta_bar(self):
        # Two kinds of note. Beta: data is missing and telemetry may be empty.
        # Unconfirmed: everything is in place, from Logitech's driver files and
        # Linux, but nobody has run it on the real wheel yet - a request for a
        # report rather than a warning, and gone for good once one is sent.
        conn = bool(state["connected"])
        dismissed = getattr(self, "_beta_dismissed", False)
        reported = device_key_for(active_profile) in global_settings.get("verify_reported", [])
        beta = is_beta()
        unconf = (not beta) and is_unverified() and not reported
        show = conn and not dismissed and (beta or unconf)
        self.beta_bar.setVisible(show)
        if not show:
            return
        if beta:
            mapped = saved_map(active_profile) is not None
            self.beta_text.setText(tr("dev.bar_mapped") if mapped
                                   else tr("dev.bar_unmapped"))
            self.beta_btn.setText(tr("dev.beta_remap") if mapped
                                  else tr("dev.beta_action"))
            bg = "#3a2a14" if isDarkTheme() else "#fdf2e2"
            fg = "#e8c48a" if isDarkTheme() else "#7a4b10"
        else:
            self.beta_text.setText(tr("dev.bar_unverified"))
            self.beta_btn.setText(tr("dev.verify_action"))
            bg = "#1d2a36" if isDarkTheme() else "#e8f1fb"
            fg = "#a9c7e4" if isDarkTheme() else "#1f4a73"
        self.beta_bar.setStyleSheet(
            f"QWidget{{background:{bg};}} BodyLabel{{color:{fg};}}")

    def _check_calibration_settle(self):
        now = time.monotonic()
        n = state.get("steer_norm", 0.0)
        if abs(n - self._cal_prev) > 0.012:          
            self._cal_last_move_t = now
        if abs(n) > 0.45:                            
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

    def apply_settings(self, silent=False):
        w = self.wheelset
        gain, spring, damper = w.s_gain.value(), w.s_spring.value(), w.s_damper.value()
        persist = w.cb_center.isChecked()
        di_center = w.s_center.value()
        di_ramp = w.s_ramp.value()
        center = di_center if persist else 0
        angle = w.s_rot.value()
        prof = global_settings["profiles"].setdefault(global_settings["selected_profile"], {})
        prof.update({"angle": angle, "di_gain": gain, "di_spring": spring, "di_damper": damper,
                     "di_center": di_center, "di_ramp": di_ramp, "di_persist": persist,
                     "allow_game": w.cb_game.isChecked()})
        save_settings()

        if dev is None:
            if not silent:
                _defer_infobar("warning", tr("conn.not_connected"), tr("input.led_nc"), duration=2500,
                                position=InfoBarPosition.TOP, parent=self)
            return
        update_registry_ffb(gain, spring, damper, di_center, persist, angle)
        send_rotation(angle)
        ffb_write(autocenter_cmd(center, di_ramp))
        self._applied_angle = angle  
        push_live_ffb(gain, profile_live_lut(prof),
                      lut_on=bool(prof.get("lut_enabled") and prof.get("lut_file")) and global_settings["selected_profile"] != "Global")
        self._publish_lgs(prof)
        # what a running game set stays over the profile (switching to the
        # game's profile a moment after it asked must not undo it)
        if getattr(self, "_game", None):
            if prof.get("allow_game", True):
                self._push_game()
            else:
                self._game, self._game_who = {}, ""
                self.wheelset.show_game({}, "")
        
        # Yeni Profile Entegrasyonu: Aktif profildeki pedalları donanıma yazma
        try: self.settings.pedals_tab.apply_to_wheel(global_settings["selected_profile"])
        except Exception: pass
        
        if not silent:
            _defer_infobar("success", tr("apply.ok_title"), tr("apply.ok_body"),
                           duration=2000, position=InfoBarPosition.TOP, parent=self)

    def _start_game_watcher(self):
        def loop():
            # Starts as "" rather than None: the first pass with no game
            # running produced "" too, and "" != None fired a profile change
            # on launch - switching to Global and quietly pushing its settings
            # to the wheel a couple of seconds after start.
            last = ""
            while True:
                time.sleep(2.0)
                if lutgen_busy:
                    # The LUT generator is measuring the wheel: a profile
                    # switch now would send it a new range and centring in
                    # the middle of a trial. "last" is left alone, so a game
                    # that started meanwhile is switched to once it is done.
                    continue
                auto = global_settings.get("auto_game_profile", True)
                if not auto:
                    last = ""          
                try:
                    profs = dict(global_settings.get("profiles", {}))
                    name = match_running_profile(profs) if auto else None
                    name = name or ""
                except Exception:
                    name = last
                if auto and name != last:
                    last = name
                    try: self.game_profile_changed.emit(name)
                    except Exception: pass
                try:
                    prof = global_settings["profiles"].get(
                        global_settings.get("selected_profile", ""), {})
                    push_live_ffb(int(self._game_eff(prof).get("di_gain",
                                                               PROFILE_DEFAULTS["di_gain"])),
                                  profile_live_lut(prof),
                                  lut_on=bool(prof.get("lut_enabled") and prof.get("lut_file"))
                                  and global_settings.get("selected_profile") != "Global")
                except Exception:
                    pass
        threading.Thread(target=loop, daemon=True).start()

    # ---- what a game set through the LGS window (see LgsRouter)
    # what a game may set through the LGS window - what LGS itself takes
    _GAME_KEYS = ("angle", "di_gain", "di_spring", "di_damper", "ffb_on", "combine")

    def _game_eff(self, prof=None):
        prof = global_settings["profiles"].get(global_settings.get("selected_profile"), {}) if prof is None else prof
        d = {k: prof.get(k, PROFILE_DEFAULTS.get(k))
             for k in ("angle", "di_gain", "di_spring", "di_damper", "di_center", "di_persist", "di_ramp")}
        layer = getattr(self, "_game", None)
        if layer:
            d.update(layer)
            if d.get("ffb_on") is False:          # the game turned force feedback off
                d["di_gain"] = 0
        return d

    def _publish_lgs(self, prof=None):
        prof = global_settings["profiles"].get(global_settings.get("selected_profile"), {}) if prof is None else prof
        try:
            publish_lgs_settings(self._game_eff(prof), prof.get("allow_game", True),
                                 combine_pedals_state() is True)
        except Exception:
            pass

    def _start_lgs_window(self):
        def props(pid):
            prof = global_settings["profiles"].get(global_settings.get("selected_profile"), {})
            return lgs_props(self._game_eff(prof), prof.get("allow_game", True),
                             combine_pedals_state() is True, pid)

        def rng(pid):
            return int(self._game_eff().get("angle", 900))

        def on_set(changes, who):
            QTimer.singleShot(0, lambda: self.game_set(changes, who))
        try:
            lgs_router.start(props, rng, on_set)
        except Exception:
            pass
        try:
            refresh_cm_bridges()               # a newer bridge for Content Manager, if ours is there
        except Exception:
            pass
        try:
            note_app_command()                 # for LWHGameLink (a game asking before LWH runs)
        except Exception:
            pass

    def game_set(self, changes, who=""):
        if not hasattr(self, "_game"):
            self._game, self._game_who = {}, ""
        if not changes:
            return
        prof = global_settings["profiles"].get(global_settings.get("selected_profile"), {})
        # LGS's rule: the asking game's own profile decides, otherwise Global
        if not game_allowed(who, global_settings.get("profiles") or {}):
            return
        if "combine" in changes:
            # combined pedals take Windows' prompt and a wheel restart here:
            # noted, not done behind the user's back
            want = changes.pop("combine")
            if (combine_pedals_state() is True) != want:
                lgs_router._note("game wants combined pedals %s - not changed" % ("on" if want else "off"))
        eff = self._game_eff(prof)
        ch = {k: v for k, v in changes.items() if k in self._GAME_KEYS and eff.get(k) != v}
        if who:
            self._game_who = who
        if not ch:
            return
        self._game.update(ch)
        self._push_game()
        name = (self._game_who or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0] or tr("lgs.game")
        body = (tr("lgs.set_range").format(name, int(ch["angle"])) if "angle" in ch and active_profile.get("range_cmd")
                else tr("lgs.set_ffb").format(name))
        _defer_infobar("success", tr("lgs.title"), body, duration=3000, position=InfoBarPosition.TOP, parent=self)

    def _push_game(self):
        """The profile with the game's values over it, to the wheel."""
        self.wheelset.show_game(self._game, self._game_who)
        if dev is None:
            return
        prof = global_settings["profiles"].get(global_settings.get("selected_profile"), {})
        d = self._game_eff(prof)
        persist = bool(d.get("di_persist"))
        update_registry_ffb(int(d["di_gain"]), int(d["di_spring"]), int(d["di_damper"]), int(d["di_center"]),
                            persist, int(d["angle"]))
        send_rotation(int(d["angle"]))
        self._applied_angle = int(d["angle"])
        ffb_write(autocenter_cmd(int(d["di_center"]) if persist else 0, int(d.get("di_ramp", DEFAULT_RAMP))))
        push_live_ffb(int(d["di_gain"]), profile_live_lut(prof),
                      lut_on=bool(prof.get("lut_enabled") and prof.get("lut_file"))
                      and global_settings.get("selected_profile") != "Global")
        self._publish_lgs(prof)

    def clear_game(self):
        if not getattr(self, "_game", None):
            return
        self._game, self._game_who = {}, ""
        self.wheelset.show_game({}, "")
        try: self.apply_settings(silent=True)
        except Exception: pass

    def _game_still_running(self):
        """A game that set things and has since closed gives the wheel back."""
        who = getattr(self, "_game_who", "")
        if not getattr(self, "_game", None) or not who:
            return
        running = running_exe_paths()
        if running and who.replace("/", "\\").lower() not in running:
            self.clear_game()

    def _user_apply(self, *_):
        """APPLY pressed: the user's values win over what a game set."""
        self._game, self._game_who = {}, ""
        self.wheelset.show_game({}, "")
        self.apply_settings()

    def _on_game_profile(self, name):
        prev = getattr(self, "_game_seen", "")
        self._game_seen = name
        if prev and prev != name:
            # the game that was running ended: what it set no longer applies
            self.clear_game()
        if lutgen_busy:
            # arrived just as a measurement started: try again after it
            QTimer.singleShot(2000, lambda: self._on_game_profile(name))
            return
        target = name or "Global"
        if target not in global_settings.get("profiles", {}):
            return
        if global_settings.get("selected_profile") == target:
            return
        try:
            self._auto_switch = True
            self.presets.select(target)      
        finally:
            self._auto_switch = False
        if name:
            _defer_infobar("success", tr("game.detected"),
                           tr("game.detected_body").format(name), duration=3000,
                           position=InfoBarPosition.TOP, parent=self)

    def check_updates(self, manual=False):
        def work():
            latest = fetch_latest_version()
            try:
                self.update_checked.emit(latest, manual)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _on_update_result(self, latest, manual):
        try:
            if latest is None:
                if manual:
                    _defer_infobar("warning", tr("upd.failed"), tr("upd.failed_body"),
                                   duration=3500, position=InfoBarPosition.TOP, parent=self)
                return
            global_settings["last_update_check"] = time.time()
            if _ver_tuple(latest) > _ver_tuple(HUB_VERSION):
                if not manual and global_settings.get("update_seen") == latest:
                    save_settings()
                    return
                global_settings["update_seen"] = latest
                self._update_tag = latest
                _defer_infobar("success", tr("upd.available"),
                               tr("upd.available_body").format(latest),
                               duration=20000, position=InfoBarPosition.TOP, parent=self,
                               _extra=("upd.open", open_releases_page))
            elif manual:
                _defer_infobar("success", tr("upd.current"), tr("upd.current_body"),
                               duration=2500, position=InfoBarPosition.TOP, parent=self)
            save_settings()
        except Exception:
            pass

    def _restore_geometry(self):
        try:
            avail = QApplication.primaryScreen().availableGeometry().height()
            self.setMinimumHeight(max(560, min(792, avail - 48)))
        except Exception:
            self.setMinimumHeight(720)
        self.resize(int(num_setting("win_w", 1366)),
                    max(int(num_setting("win_h", 720)), self.minimumHeight()))
        x = global_settings.get("win_x"); y = global_settings.get("win_y")
        placed = False
        try:
            if x is not None and y is not None:
                wx, wy, ww, wh = int(x), int(y), self.width(), self.height()
                for scr in QApplication.screens():
                    g = scr.availableGeometry()
                    ox = min(wx + ww, g.right() + 1) - max(wx, g.left())
                    oy = min(wy + wh, g.bottom() + 1) - max(wy, g.top())
                    if ox >= 200 and oy >= 100:        
                        self.move(wx, wy); placed = True; break
        except Exception:
            placed = False
        if not placed:                                 
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
            keymapper.set_map({})          # no key left held down
            lgs_router.stop()
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
    if "Point size" in msg and "setPointSize" in msg:
        return
    sys.stderr.write(msg + "\n")


_INSTANCE_MUTEX = None


def _acquire_single_instance():
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        # use_last_error: ctypes can itself call Win32 functions between
        # CreateMutexW returning and a separate GetLastError() call, which
        # would overwrite the ERROR_ALREADY_EXISTS we are looking for.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        ERROR_ALREADY_EXISTS = 183
        global _INSTANCE_MUTEX
        _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False,
                                                "LegacyWheelHub_SingleInstance")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            try:
                user32 = ctypes.windll.user32
                hwnd = user32.FindWindowW(None, "Legacy Wheel Hub")
                if hwnd:
                    user32.ShowWindow(hwnd, 9)          
                    user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return False
        return True
    except Exception:
        return True     


def uninstall_cleanup():
    """What the uninstaller asks for (--uninstall-cleanup): take our DLL out
    of every game folder a profile points to, and remove the registry keys
    the DLL reads its LUT from. Settings and LUTs are left where they are on
    purpose - reinstalling brings everything back - and so is a dinput8.dll
    that is not ours. Returns how many game folders were cleaned."""
    done = 0
    for prof in (global_settings.get("profiles") or {}).values():
        exe = (prof or {}).get("exe_path", "")
        if not exe:
            continue
        try:
            if uninstall_proxy_for(exe):
                done += 1
        except Exception:
            pass
    # and the SDK bridge from Content Manager's folder - only ours
    for cm in list(global_settings.get("sdk_bridge") or []):
        try:
            if cm_bridge_state(cm) == "ours":
                dst = os.path.join(os.path.dirname(cm), SDK_BRIDGE_DLL)
                os.remove(dst)
                if os.path.isfile(dst + CM_KEPT_SUFFIX):
                    os.replace(dst + CM_KEPT_SUFFIX, dst)      # the one it replaced
                done += 1
        except Exception:
            pass
    if winreg is not None:
        def drop(root, path):
            try:
                with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
                    while True:
                        try:
                            sub = winreg.EnumKey(k, 0)
                        except OSError:
                            break
                        drop(root, path + "\\" + sub)
                winreg.DeleteKey(root, path)
            except OSError:
                pass
        drop(winreg.HKEY_CURRENT_USER, r"Software\LegacyWheelHub")
    # and the SDK's registration - only where it is ours
    try:
        write_sdk_registration(False)
    except Exception:
        pass
    # and the combined-pedals tasks
    try:
        remove_combine_tasks()
    except Exception:
        pass
    return done


def restore_proxies():
    """Put our DLL back next to games whose profile still uses it - after an
    uninstall and reinstall took it out (see uninstall_cleanup), the LUT
    would otherwise stay off until the switch was toggled. Only where there
    is no dinput8.dll at all: an existing one, ours or another mod's, is
    never touched here."""
    for prof in (global_settings.get("profiles") or {}).values():
        prof = prof or {}
        exe = prof.get("exe_path", "")
        if not exe or not prof.get("lut_enabled") or prof.get("lut_mode", "proxy") == "direct":
            continue
        try:
            real = resolve_game_exe(exe)
            if real and os.path.isfile(real) and not os.path.isfile(
                    os.path.join(os.path.dirname(real), PROXY_DLL)):
                install_proxy_for(exe)
        except Exception:
            pass


def main():
    global main_window, CURRENT_LANG, running
    if "--uninstall-cleanup" in sys.argv:
        uninstall_cleanup()
        return
    if "--sdk-register" in sys.argv:
        # the copy with administrator rights for the SDK's registration (the
        # installer runs it too)
        sys.exit(sdk_register_main(sys.argv))
    if "--combine-pedals-setup" in sys.argv:
        # the installer: the tasks that change combined pedals without asking
        sys.exit(combine_tasks_main(sys.argv))
    if "--combine-pedals" in sys.argv:
        # the copy started with administrator rights for the one driver
        # setting; the app that asked is still open, so no single-instance check
        sys.exit(combine_pedals_main(sys.argv))
    if not _acquire_single_instance():
        return
    qInstallMessageHandler(_qt_msg_filter)
    saved = global_settings.get("language")
    if saved in LANG:
        CURRENT_LANG = saved
    try:
        s = ui_scale_setting() / 100.0
        if s and s != 1.0:
            sw, sh = _screen_px()
            BASE_W, BASE_H = 1230.0, 770.0      
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
    QTimer.singleShot(3000, restore_proxies)
    start_in_tray = ("--tray" in sys.argv
                     and global_settings.get("minimize_to_tray", False)
                     and getattr(main_window, "tray", None) is not None)
    if "--game" in sys.argv:
        # started by LWHGameLink because a game asked for LGS: never over the game
        if getattr(main_window, "tray", None) is not None:
            main_window._hide_to_tray()
        else:
            main_window.showMinimized()
    elif start_in_tray:
        main_window._hide_to_tray()
    else:
        main_window.show()
    if SETTINGS_RECOVERED:
        # Stays until closed: the profiles are in that copy, not lost.
        QTimer.singleShot(1500, lambda: _defer_infobar(
            "warning", tr("settings.unreadable_title"),
            tr("settings.unreadable_body").format(SETTINGS_RECOVERED),
            duration=-1, position=InfoBarPosition.TOP, parent=main_window))
    poller = Poller(); poller.start()
    app.exec()
    running = False
    poller.wait(2000)


if __name__ == "__main__":
    # The LUT generator's maths can be checked without a wheel or a window,
    # which is how it was developed; keep that reachable from the app too.
    if "--lutgen-selftest" in sys.argv:
        sys.exit(selftest())
    main()
