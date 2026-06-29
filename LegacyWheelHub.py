"""
Legacy Logitech Wheels - Control Hub  (PySide6 + QFluentWidgets edition)
"""
import sys, os, json, math, time, threading

try:
    import hid
except ImportError:
    print("HATA: hidapi yok ->  pip install hidapi"); sys.exit(1)

try:
    import winreg
except ImportError:
    winreg = None

from PySide6.QtCore import Qt, QTimer, QThread, QRectF, QPointF, Signal, QEvent, QSize, QPropertyAnimation, QEasingCurve, qInstallMessageHandler
from PySide6.QtGui import QPainter, QColor, QPixmap, QPolygonF, QFont, QPen, QIcon, QAction, QImage
from PySide6.QtWidgets import (QApplication, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
                               QFrame, QSizePolicy, QScrollArea, QSystemTrayIcon, QMenu,
                               QStackedWidget, QButtonGroup, QSplitter)

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

APP_DIR = _exe_dir()
WHEEL_PNG = _find_wheel()
SETTINGS_FILE = os.path.join(_data_dir(), "settings.json")
STEER_CENTER = 8192
ACCENT_FALLBACK = "#ff6a1a"
HUB_VERSION = "v1.0"
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
        "wheel.steering": "Steering", "wheel.rotation": "Rotation Range",
        "wheel.rotation_h": "Maximum steering rotation angle.",
        "ffb.title": "Force Feedback Test",
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
        "about.author": "Author", "about.sec_about": "ABOUT", "about.repo": "GitHub repository", "about.license": "License: GPL-3.0", "about.disclaimer": "Not affiliated with, endorsed by, or sponsored by Logitech. \u201cLogitech\u201d, \u201cDriving Force\u201d and \u201cG27\u201d are trademarks of Logitech, used here only to indicate hardware compatibility.", "about.power_active": "{0} / Active",
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
        "tab.info": "INFO", "wheel.sec_ffb": "FORCE FEEDBACK", "wheel.sec_steer": "STEERING SETTINGS",
        "ffb.reset": "Reset Driver FFB", "ffb.reset_h": "Deletes all driver FFB registry overrides written by this app.",
        "set.title": "SETTINGS", "set.appearance": "APPEARANCE", "set.general": "GENERAL", "set.testing": "TESTING",
        "set.theme": "Theme", "set.language": "Language", "set.tray": "Minimize to system tray",
        "set.tray_h": "When on, the minimize button hides the app to the system tray (show hidden icons).",
        "set.devmode": "Device detection mode",
        "set.devmode_h": "Force a wheel layout without hardware. \u201cAuto\u201d uses real detection.",
        "info.opmode_active": "Native Advanced Mode (Unlocked)", "info.opmode_idle": "Idle / Disconnected",
        "info.active": "Active", "info.standby": "Standby",
    },
    "tr": {
        "nav.home": "Ana Sayfa", "nav.wheel": "Direksiyon Ayarlar\u0131", "nav.ffb": "FFB Testi",
        "nav.input": "Giri\u015f \u0130zleyici", "nav.apply": "Ayarlar\u0131 Uygula",
        "nav.theme": "Tema De\u011fi\u015ftir", "nav.about": "Hakk\u0131nda",
        "conn.connecting": "Ba\u011flan\u0131yor\u2026", "conn.not_connected": "Ba\u011fl\u0131 De\u011fil",
        "home.title": "Canl\u0131 Telemetri", "home.steering": "D\u0130REKS\u0130YON A\u00c7ISI",
        "home.clutch": "DEBR\u0130YAJ", "home.brake": "FREN", "home.throttle": "GAZ",
        "home.center": "Direksiyonu Ortala", "home.na": "Yok",
        "prof.label": "PROF\u0130L", "prof.auto": "Otomatik Y\u00fckle", "prof.add": "Yeni profil",
        "prof.dup": "Profili \u00e7o\u011falt", "prof.ren": "Profili yeniden adland\u0131r",
        "prof.del": "Profili sil", "prof.new_title": "Yeni Profil",
        "prof.new_hint": "Profil ad\u0131", "prof.ren_title": "Profili Yeniden Adland\u0131r",
        "prof.del_title": "Profili Sil", "prof.del_msg": "\u201c{0}\u201d profili silinsin mi? Bu i\u015flem geri al\u0131namaz.",
        "prof.copy_suffix": " Kopya", "dlg.ok": "Tamam", "dlg.cancel": "\u0130ptal", "dlg.delete": "Sil",
        "wheel.title": "Direksiyon Ayarlar\u0131", "wheel.ffb": "Kuvvet Geri Bildirimi",
        "wheel.overall": "Genel Efekt G\u00fcc\u00fc",
        "wheel.overall_h": "\u00c7o\u011fu oyunda merkez FFB \u00f6l\u00fc b\u00f6lgesini gidermek i\u00e7in %101 yap\u0131n. (Oyun yeniden ba\u015flat\u0131lmal\u0131)",
        "wheel.spring": "Yay Efekti", "wheel.spring_h": "S\u00fcr\u00fcc\u00fc tabanl\u0131 yay (\u00f6nerilen: %0).",
        "wheel.damper": "Damper Efekti", "wheel.damper_h": "S\u00fcr\u00fcc\u00fc tabanl\u0131 s\u00f6n\u00fcmleme (\u00f6nerilen: %0).",
        "wheel.center_cb": "FFB oyunlar\u0131nda ortalama yay\u0131n\u0131 etkinle\u015ftir",
        "wheel.center": "Ortalama Yay\u0131", "wheel.center_h": "S\u00fcr\u00fcc\u00fc tabanl\u0131 otomatik ortalama g\u00fcc\u00fc.",
        "wheel.steering": "Direksiyon", "wheel.rotation": "D\u00f6n\u00fc\u015f Aral\u0131\u011f\u0131",
        "wheel.rotation_h": "Maksimum direksiyon d\u00f6n\u00fc\u015f a\u00e7\u0131s\u0131.",
        "ffb.title": "Kuvvet Geri Bildirim Testi",
        "ffb.subtitle": "FFB motorunu do\u011frudan test edin. Hi\u00e7bir oyun direksiyonu kullanm\u0131yorken yap\u0131lmas\u0131 en iyisidir.",
        "ffb.strength": "Test G\u00fcc\u00fc", "ffb.strength_h": "\u0130tme, Yay ve Tarama testlerinde kullan\u0131lan g\u00fc\u00e7.",
        "ffb.push_l": "\u25c0  Sola \u0130t", "ffb.push_r": "Sa\u011fa \u0130t  \u25b6",
        "ffb.spring": "Yay (Merkez)", "ffb.spring_stop": "Yay\u0131 Durdur",
        "ffb.sweep": "Otomatik Tarama", "ffb.sweep_stop": "Taramay\u0131 Durdur",
        "ffb.advanced": "GEL\u0130\u015eM\u0130\u015e MOTOR TESTLER\u0130",
        "ffb.pulse_l": "\u26a1 Sol Darbe", "ffb.pulse_r": "Sa\u011f Darbe \u26a1",
        "ffb.vibe_light": "\u3030 Hafif Titre\u015fim", "ffb.vibe_med": "\u3030 Orta Titre\u015fim",
        "ffb.vibe_fast": "\u3030 H\u0131zl\u0131 G\u00fcr\u00fclt\u00fc", "ffb.vibe_heavy": "\u3030 A\u011f\u0131r Titre\u015fim",
        "ffb.stop": "\u25a0  T\u00dcM KUVVETLER\u0130 DURDUR",
        "input.title": "Giri\u015f \u0130zleyici", "input.led": "\U0001F4A1  LED Kar\u015f\u0131lama Testi",
        "input.wheel": "D\u0130REKS\u0130YON", "input.shifter": "VITES \u00dcN\u0130TES\u0130", "input.gear": "V\u0130TES  (H-D\u00dcZEN\u0130)",
        "input.lpad": "\u25c0 SOL PADDLE", "input.rpad": "SA\u011e PADDLE \u25b6",
        "input.face": "Y\u00dcZ TU\u015eLARI", "input.dpad": "Y\u00d6N TU\u015eU", "input.horn": "KORNA",
        "input.led_nc": "Direksiyon ba\u011fl\u0131 de\u011fil.",
        "input.led_g27": "LED testi yaln\u0131zca G27 i\u00e7indir (DFGT'de RPM LED'i yoktur).",
        "input.led_run": "LED kar\u015f\u0131lama\u2026 (s\u00fcr\u00fcc\u00fc raporu ge\u00e7irirse \u00e7al\u0131\u015f\u0131r)",
        "about.title": "Cihaz Bilgisi", "about.settings": "Ayarlar",
        "about.status": "Durum", "about.connected": "Ba\u011fl\u0131", "about.not_connected": "Ba\u011fl\u0131 De\u011fil",
        "about.model": "Model", "about.hwid": "Donan\u0131m Kimli\u011fi", "about.axis": "Eksen \u00c7\u00f6z\u00fcn\u00fcrl\u00fc\u011f\u00fc",
        "about.ffb": "Kuvvet Geri Bildirimi", "about.language": "Dil", "about.theme": "Tema",
        "about.theme_dark": "Koyu", "about.theme_light": "A\u00e7\u0131k",
        "about.testmode": "Test Cihaz Modu", "about.testmode_h":
            "Donan\u0131m ba\u011fl\u0131 olmadan aktif direksiyon d\u00fczenini de\u011fi\u015ftirin. \u201cOtomatik\u201d ger\u00e7ek alg\u0131lamay\u0131 kullan\u0131r.",
        "about.test_auto": "Otomatik (alg\u0131la)", "about.footer":
            "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "DONANIM TANILAMA", "about.sec_sensor": "SENS\u00d6R & FFB \u00d6ZELL\u0130KLER\u0130",
        "about.sec_sw": "YAZILIM & S\u00dcR\u00dcC\u00dc DURUMU", "about.sec_credits": "KATKIDA BULUNANLAR",
        "about.devmodel": "Cihaz Modeli", "about.interface": "Aray\u00fcz", "about.power": "G\u00fc\u00e7 Durumu",
        "about.tracking": "Takip Sistemi", "about.polling": "Maks. Yoklama H\u0131z\u0131",
        "about.opmode": "\u00c7al\u0131\u015fma Modu", "about.api": "API Ba\u011flant\u0131s\u0131", "about.hub": "Hub S\u00fcr\u00fcm\u00fc",
        "about.author": "Yazar", "about.sec_about": "HAKKINDA", "about.repo": "GitHub deposu", "about.license": "Lisans: GPL-3.0", "about.disclaimer": "Logitech ile herhangi bir ba\u011flant\u0131s\u0131, onay\u0131 veya sponsorlu\u011fu yoktur. \u201cLogitech\u201d, \u201cDriving Force\u201d ve \u201cG27\u201d Logitech\u2019in ticari markalar\u0131d\u0131r; burada yaln\u0131zca donan\u0131m uyumlulu\u011funu belirtmek i\u00e7in kullan\u0131lm\u0131\u015ft\u0131r.", "about.power_active": "{0} / Aktif",
        "about.power_standby": "Beklemede / Ba\u011fl\u0131 De\u011fil",
        "about.opmode_active": "Yerel Geli\u015fmi\u015f Mod (Kilit A\u00e7\u0131k)",
        "about.opmode_idle": "Bo\u015fta / Cihaz Bekleniyor",
        "about.tray": "Sistem Tepsisine K\u00fc\u00e7\u00fclt",
        "about.tray_h": "A\u00e7\u0131kken, k\u00fc\u00e7\u00fcltme tu\u015fu uygulamay\u0131 sistem tepsisine (gizli simgeler) gizler.",
        "tray.show": "G\u00f6ster", "tray.quit": "\u00c7\u0131k\u0131\u015f",
        "apply.ok_title": "Uyguland\u0131", "apply.ok_body": "Ayarlar direksiyona uyguland\u0131.",
        "ui.presets": "HAZIR AYARLAR", "ui.presets_sub": "Oyuna ba\u015flamadan \u00f6nce se\u00e7in.",
        "ui.add_profile": "+  Oyun Profili Ekle", "ui.autoload": "Ba\u011flan\u0131nca otomatik y\u00fckle",
        "ui.telemetry": "CANLI TELEMETR\u0130", "ui.center": "Merkezle",
        "ui.apply": "UYGULA", "conn.connected": "Ba\u011fl\u0131", "conn.test": "Test Modu",
        "tab.wheel": "D\u0130REKS\u0130YON", "tab.ffb": "FFB TEST\u0130", "tab.input": "G\u0130R\u0130\u015e \u0130ZLEY\u0130C\u0130",
        "tab.info": "B\u0130LG\u0130", "wheel.sec_ffb": "KUVVET GER\u0130 B\u0130LD\u0130R\u0130M\u0130", "wheel.sec_steer": "D\u0130REKS\u0130YON AYARLARI",
        "ffb.reset": "S\u00fcr\u00fcc\u00fc FFB S\u0131f\u0131rla", "ffb.reset_h": "Bu uygulaman\u0131n yazd\u0131\u011f\u0131 t\u00fcm s\u00fcr\u00fcc\u00fc FFB registry de\u011ferlerini siler.",
        "set.title": "AYARLAR", "set.appearance": "G\u00d6R\u00dcN\u00dcM", "set.general": "GENEL", "set.testing": "TEST",
        "set.theme": "Tema", "set.language": "Dil", "set.tray": "Sistem tepsisine k\u00fc\u00e7\u00fclt",
        "set.tray_h": "A\u00e7\u0131kken, k\u00fc\u00e7\u00fcltme tu\u015fu uygulamay\u0131 sistem tepsisine (gizli simgeler) gizler.",
        "set.devmode": "Cihaz alg\u0131lama modu",
        "set.devmode_h": "Donan\u0131ms\u0131z bir d\u00fczen zorla. \u201cOtomatik\u201d ger\u00e7ek alg\u0131lamay\u0131 kullan\u0131r.",
        "info.opmode_active": "Yerel Geli\u015fmi\u015f Mod (Kilit A\u00e7\u0131k)", "info.opmode_idle": "Bo\u015fta / Ba\u011fl\u0131 De\u011fil",
        "info.active": "Aktif", "info.standby": "Beklemede",
    },
    "de": {
        "nav.home": "Startseite", "nav.wheel": "Lenkrad-Einstellungen", "nav.ffb": "FFB-Test",
        "nav.input": "Eingangsmonitor", "nav.apply": "Einstellungen anwenden",
        "nav.theme": "Design wechseln", "nav.about": "\u00dcber",
        "conn.connecting": "Verbinden\u2026", "conn.not_connected": "Nicht verbunden",
        "home.title": "Live-Telemetrie", "home.steering": "LENKWINKEL",
        "home.clutch": "KUPPLUNG", "home.brake": "BREMSE", "home.throttle": "GAS",
        "home.center": "Lenkrad zentrieren", "home.na": "N/V",
        "prof.label": "PROFIL", "prof.auto": "Auto-Laden", "prof.add": "Neues Profil",
        "prof.dup": "Profil duplizieren", "prof.ren": "Profil umbenennen",
        "prof.del": "Profil l\u00f6schen", "prof.new_title": "Neues Profil",
        "prof.new_hint": "Profilname", "prof.ren_title": "Profil umbenennen",
        "prof.del_title": "Profil l\u00f6schen", "prof.del_msg": "Profil \u201e{0}\u201c l\u00f6schen? Dies kann nicht r\u00fcckg\u00e4ngig gemacht werden.",
        "prof.copy_suffix": " Kopie", "dlg.ok": "OK", "dlg.cancel": "Abbrechen", "dlg.delete": "L\u00f6schen",
        "wheel.title": "Lenkrad-Einstellungen", "wheel.ffb": "Force Feedback",
        "wheel.overall": "Gesamtst\u00e4rke der Effekte",
        "wheel.overall_h": "Auf 101% setzen, um die zentrale FFB-Totzone in den meisten Spielen zu beheben. (Spiel-Neustart n\u00f6tig)",
        "wheel.spring": "Feder-Effekt", "wheel.spring_h": "Treiberbasierte Feder (empfohlen: 0%).",
        "wheel.damper": "D\u00e4mpfer-Effekt", "wheel.damper_h": "Treiberbasierte D\u00e4mpfung (empfohlen: 0%).",
        "wheel.center_cb": "Zentrierfeder in FFB-Spielen aktivieren",
        "wheel.center": "Zentrierfeder", "wheel.center_h": "Treiberbasierte Auto-Zentrierst\u00e4rke.",
        "wheel.steering": "Lenkung", "wheel.rotation": "Drehbereich",
        "wheel.rotation_h": "Maximaler Lenkdrehwinkel.",
        "ffb.title": "Force-Feedback-Test",
        "ffb.subtitle": "Testen Sie den FFB-Motor direkt. Am besten, wenn kein Spiel das Lenkrad nutzt.",
        "ffb.strength": "Teststärke", "ffb.strength_h": "St\u00e4rke f\u00fcr Druck-, Feder- und Sweep-Tests.",
        "ffb.push_l": "\u25c0  Nach links", "ffb.push_r": "Nach rechts  \u25b6",
        "ffb.spring": "Feder (Mitte)", "ffb.spring_stop": "Feder stoppen",
        "ffb.sweep": "Auto-Sweep", "ffb.sweep_stop": "Sweep stoppen",
        "ffb.advanced": "ERWEITERTE MOTORTESTS",
        "ffb.pulse_l": "\u26a1 Puls links", "ffb.pulse_r": "Puls rechts \u26a1",
        "ffb.vibe_light": "\u3030 Leichte Vibration", "ffb.vibe_med": "\u3030 Mittlere Vibration",
        "ffb.vibe_fast": "\u3030 Schnelles Rumpeln", "ffb.vibe_heavy": "\u3030 Starke Vibration",
        "ffb.stop": "\u25a0  ALLE KR\u00c4FTE STOPPEN",
        "input.title": "Eingangsmonitor", "input.led": "\U0001F4A1  LED-Begr\u00fc\u00dfungstest",
        "input.wheel": "LENKRAD", "input.shifter": "SCHALTEINHEIT", "input.gear": "GANG  (H-SCHALTUNG)",
        "input.lpad": "\u25c0 LINKES PADDLE", "input.rpad": "RECHTES PADDLE \u25b6",
        "input.face": "TASTEN", "input.dpad": "STEUERKREUZ", "input.horn": "HUPE",
        "input.led_nc": "Lenkrad nicht verbunden.",
        "input.led_g27": "LED-Test nur f\u00fcr G27 (DFGT hat keine RPM-LEDs).",
        "input.led_run": "LED-Begr\u00fc\u00dfung\u2026 (funktioniert, wenn der Treiber den Bericht durchl\u00e4sst)",
        "about.title": "Ger\u00e4teinfo", "about.settings": "Einstellungen",
        "about.status": "Status", "about.connected": "Verbunden", "about.not_connected": "Nicht verbunden",
        "about.model": "Modell", "about.hwid": "Hardware-ID", "about.axis": "Achsenaufl\u00f6sung",
        "about.ffb": "Force Feedback", "about.language": "Sprache", "about.theme": "Design",
        "about.theme_dark": "Dunkel", "about.theme_light": "Hell",
        "about.testmode": "Test-Ger\u00e4temodus", "about.testmode_h":
            "Aktives Lenkrad-Layout ohne angeschlossene Hardware umschalten. \u201eAuto\u201c nutzt echte Erkennung.",
        "about.test_auto": "Auto (erkennen)", "about.footer":
            "Legacy Logitech Wheels - Control Hub (PySide6 / Fluent)",
        "about.sec_hw": "HARDWARE-DIAGNOSE", "about.sec_sensor": "SENSOR- & FFB-SPEZIFIKATIONEN",
        "about.sec_sw": "SOFTWARE- & TREIBERSTATUS", "about.sec_credits": "MITWIRKENDE",
        "about.devmodel": "Ger\u00e4temodell", "about.interface": "Schnittstelle", "about.power": "Energiestatus",
        "about.tracking": "Tracking-System", "about.polling": "Max. Abtastrate",
        "about.opmode": "Betriebsmodus", "about.api": "API-Hook", "about.hub": "Hub-Version",
        "about.author": "Autor", "about.sec_about": "\u00dcBER", "about.repo": "GitHub-Repository", "about.license": "Lizenz: GPL-3.0", "about.disclaimer": "Nicht mit Logitech verbunden, von Logitech unterst\u00fctzt oder gesponsert. \u201eLogitech\u201c, \u201eDriving Force\u201c und \u201eG27\u201c sind Marken von Logitech und werden hier nur zur Angabe der Hardware-Kompatibilit\u00e4t verwendet.", "about.power_active": "{0} / Aktiv",
        "about.power_standby": "Standby / Getrennt",
        "about.opmode_active": "Nativer Erweiterter Modus (Entsperrt)",
        "about.opmode_idle": "Leerlauf / Warte auf Ger\u00e4t",
        "about.tray": "In Infobereich minimieren",
        "about.tray_h": "Wenn aktiv, blendet die Minimieren-Taste die App in den Infobereich (ausgeblendete Symbole) aus.",
        "tray.show": "Anzeigen", "tray.quit": "Beenden",
        "apply.ok_title": "Angewendet", "apply.ok_body": "Einstellungen auf das Lenkrad angewendet.",
        "ui.presets": "VOREINSTELLUNGEN", "ui.presets_sub": "Vor dem Spielstart ausw\u00e4hlen.",
        "ui.add_profile": "+  Spielprofil hinzuf\u00fcgen", "ui.autoload": "Beim Verbinden automatisch laden",
        "ui.telemetry": "LIVE-TELEMETRIE", "ui.center": "Zentrieren",
        "ui.apply": "ANWENDEN", "conn.connected": "Verbunden", "conn.test": "Testmodus",
        "tab.wheel": "LENKRAD", "tab.ffb": "FFB-TEST", "tab.input": "EINGABE-MONITOR",
        "tab.info": "INFO", "wheel.sec_ffb": "FORCE FEEDBACK", "wheel.sec_steer": "LENKEINSTELLUNGEN",
        "ffb.reset": "Treiber-FFB zur\u00fccksetzen", "ffb.reset_h": "L\u00f6scht alle von dieser App geschriebenen FFB-Registry-Werte.",
        "set.title": "EINSTELLUNGEN", "set.appearance": "DARSTELLUNG", "set.general": "ALLGEMEIN", "set.testing": "TEST",
        "set.theme": "Design", "set.language": "Sprache", "set.tray": "In den Infobereich minimieren",
        "set.tray_h": "Wenn aktiv, blendet die Minimieren-Taste die App in den Infobereich (ausgeblendete Symbole) aus.",
        "set.devmode": "Ger\u00e4teerkennungsmodus",
        "set.devmode_h": "Layout ohne Hardware erzwingen. \u201eAuto\u201c nutzt echte Erkennung.",
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


def load_settings():
    base = {"theme": "dark", "language": "en", "last_device": None, "auto_load": False,
            "minimize_to_tray": False, "win_w": 1366, "win_h": 720, "last_tab": "wheel",
            "profiles": {"Global": {"angle": 900, "di_gain": 101, "di_spring": 0,
                                    "di_damper": 0, "di_center": 0, "di_persist": False}},
            "selected_profile": "Global"}
    try:
        with open(SETTINGS_FILE) as f:
            v = json.load(f)
        for k in base:
            v.setdefault(k, base[k])
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


def ensure_native_mode():
    try:
        present = [d["product_id"] for d in hid.enumerate(VID)]
        if DEVICE_PROFILES["DFGT"]["pid_native"] in present: return
        if DEVICE_PROFILES["G27"]["pid_native"] in present: return
        if PID_COMPAT not in present: return
        h = hid.device(); h.open(VID, PID_COMPAT)
        h.write([0x00, 0xF8, 0x0A, 0, 0, 0, 0, 0]); time.sleep(0.1)
        h.write([0x00, 0xF8, 0x09, 0x03, 0x01, 0, 0, 0]); time.sleep(0.1)
        h.close(); time.sleep(4)
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
def autocenter_cmd(pct, ramp):
    r = max(0, min(15, int(ramp)))
    return [0xFE, 0x0D, r, r, int(pct * 255 / 100), 0, 0]
def constant_force_cmd(direction, pct):
    pct = max(0, min(100, int(pct))); span = int(pct * 127 / 100)
    val = 0x80 + span if direction == "left" else (0x80 - span if direction == "right" else 0x80)
    return [0x11, 0x00, max(0, min(255, val)), 0, 0, 0, 0]
def stop_forces_cmd(): return [0xF3, 0, 0, 0, 0, 0, 0]


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
    all_pids = ["VID_046D&PID_C29A", "VID_046D&PID_C29B", "VID_046D&PID_C294"]
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
    ffb_write(autocenter_cmd(0, 7)); ffb_write(stop_forces_cmd())


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
    return f"""
    #ControlHub {{ background-color: {bg}; }}
    #Header {{ background-color: {hdr}; border-bottom: 1px solid {sep}; }}
    #vsep, #hsep {{ background-color: {sep}; border: none; }}
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
    f = lbl.font(); f.setBold(True); f.setPointSize(9); lbl.setFont(f)
    lbl.setStyleSheet("letter-spacing:1px;")
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
    f = lbl.font(); f.setBold(True); f.setPointSize(10); lbl.setFont(f)
    lbl.setStyleSheet("letter-spacing:1px;")
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

    def __init__(self, name):
        super().__init__()
        self.name = name; self.selected = False
        self.setFixedHeight(42); self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self); lay.setContentsMargins(18, 0, 12, 0)
        self.lbl = BodyLabel(name)
        self.lbl.setMinimumWidth(0)
        lay.addWidget(self.lbl); lay.addStretch(1)
        self.set_selected(False)

    def contextMenuEvent(self, e):
        self.menu_requested.emit(self.name, e.globalPos())

    def set_selected(self, s):
        self.selected = s
        if s:
            f = self.lbl.font(); f.setBold(True); self.lbl.setFont(f)
            self.lbl.setStyleSheet(f"color:{ACCENT};")
        else:
            f = self.lbl.font(); f.setBold(False); self.lbl.setFont(f)
            self.lbl.setStyleSheet("")
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
        self.btn_ren = self._mini(FIF.EDIT, "Rename profile", self._rename)
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
        self.btn_ren.setToolTip(tr("prof.ren")); self.btn_del.setToolTip(tr("prof.del"))

    def _selected(self):
        return global_settings.get("selected_profile", "Global")

    def reload(self):
        for it in self.items:
            it.setParent(None)
        self.items = []
        sel = self._selected()
        for name in global_settings["profiles"].keys():
            it = PresetItem(name)
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
        n = _name_dialog("New Profile", "", self.window())
        if n and n not in global_settings["profiles"]:
            base = global_settings["profiles"].get(self._selected(), global_settings["profiles"]["Global"])
            global_settings["profiles"][n] = dict(base)
            global_settings["selected_profile"] = n; save_settings()
            self.reload()
            if self.on_select:
                self.on_select(n)

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
        new = _name_dialog("Rename Profile", cur, self.window())
        if new and new != cur and new not in global_settings["profiles"]:
            profs = global_settings["profiles"]
            profs[new] = profs.pop(cur)
            global_settings["selected_profile"] = new; save_settings()
            self.reload()
            if self.on_select:
                self.on_select(new)

    def _delete(self):
        cur = self._selected()
        if cur == "Global":
            return
        if _confirm_dialog("Delete Profile",
                           f"Delete profile \u201c{cur}\u201d? This cannot be undone.", self.window()):
            global_settings["profiles"].pop(cur, None)
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
        f = self.cap.font(); f.setBold(True); self.cap.setFont(f)
        self.cap.setStyleSheet("letter-spacing:1px;")
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
        ffb_write(autocenter_cmd(100, 7))
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
        ffb_write(autocenter_cmd(f, 7))

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
    lay.addLayout(top); lay.addWidget(s); lay.addWidget(hl); lay.addSpacing(8)
    s._nm = nm; s._hint = hl
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
        lay = QVBoxLayout(host); lay.setContentsMargins(2, 4, 12, 8); lay.setSpacing(4)
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
        lay.addSpacing(6)
        self.h_steer = section_header(tr("wheel.sec_steer")); lay.addWidget(self.h_steer); lay.addSpacing(6)
        self.s_rot = _slider_block(lay, tr("wheel.rotation"), 90, 900, prof.get("angle", 900), "\u00b0",
                                   tr("wheel.rotation_h"))
        prow = QHBoxLayout(); prow.setSpacing(8)
        for d in (360, 540, 720, 900):
            b = PushButton(f"{d}\u00b0")
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
        self.s_rot.setValue(prof.get("angle", 900))


class FFBTestTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self); lay.setContentsMargins(2, 4, 12, 8); lay.setSpacing(4)
        self.h_test = section_header(tr("ffb.title")); lay.addWidget(self.h_test); lay.addSpacing(4)
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
        self.h_adv = section_header(tr("ffb.advanced")); lay.addWidget(self.h_adv); lay.addSpacing(4)
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
            InfoBar.warning(tr("conn.not_connected"), tr("input.led_nc"), duration=2500,
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
        ffb_write(stop_forces_cmd()); ffb_write(autocenter_cmd(0, 7))

    def _toggle_spring(self):
        if not self._spring_on and not self._ready(): return
        self._spring_on = not self._spring_on
        if self._spring_on:
            ffb_write(autocenter_cmd(self._str(), 7)); self.btn_spring.setText(tr("ffb.spring_stop"))
        else:
            ffb_write(autocenter_cmd(0, 7)); self.btn_spring.setText(tr("ffb.spring"))

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
                ffb_write([0x11, 0x00, max(0, min(255, 0x80 + cur)), 0, 0, 0, 0]); cur = -cur; time.sleep(period)
            ffb_write(stop_forces_cmd())
        threading.Thread(target=run, daemon=True).start()

    def _reset_driver(self):
        restore_ffb_defaults()
        InfoBar.success("Driver FFB reset", "All app-written FFB registry values were removed.",
                        duration=2500, position=InfoBarPosition.TOP, parent=self.window())


class InputMonitorTab(QWidget):
    def __init__(self):
        super().__init__()
        # No scroll area: the InputMonitor has a fixed footprint, so letting the
        # layout expose its real minimum makes the window refuse to shrink past
        # the point where buttons would clip (instead of showing a scrollbar).
        lay = QVBoxLayout(self); lay.setContentsMargins(2, 4, 12, 8); lay.setSpacing(6)
        self.hdr = section_header(tr("tab.input")); lay.addWidget(self.hdr); lay.addSpacing(2)
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

    def _led(self):
        if dev is None:
            self.led_status.setText(tr("input.led_nc")); return
        if active_profile is not DEVICE_PROFILES["G27"]:
            self.led_status.setText(tr("input.led_g27")); return
        self.led_status.setText(tr("input.led_run"))
        led_greeting()
        QTimer.singleShot(2300, lambda: self.led_status.setText(""))

    def refresh(self):
        self.mon.pressed = decode_buttons(state["raw"]) if state["connected"] else set()
        self.mon.update()


class InfoTab(QWidget):
    def __init__(self):
        super().__init__()
        self.vals = {}; self._bars = []
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background:transparent;")
        outer.addWidget(scroll)
        host = QWidget(); host.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(host); lay.setContentsMargins(2, 6, 12, 8); lay.setSpacing(5)
        scroll.setWidget(host)

        self._secs = []; self._labels = []
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
        self.lic = BodyLabel(tr("about.license")); self.lic.setStyleSheet("color:#8a93a6;")
        lay.addWidget(self.lic)
        self.disc = CaptionLabel(tr("about.disclaimer"))
        self.disc.setWordWrap(True); self.disc.setStyleSheet("color:#8a93a6;")
        lay.addWidget(self.disc)
        self._refresh_link()
        lay.addStretch(1)

    def _refresh_link(self):
        self.link.setText(
            f'<a style="color:#ff8a3d;" '
            f'href="https://github.com/Sadooo27/legacy-wheel-hub">{tr("about.repo")}</a>')

    def _sec(self, lay, key):
        hd = section_header(tr(key)); hd._key = key; self._bars.append(hd._bar); self._secs.append(hd)
        lay.addSpacing(2); lay.addWidget(hd); lay.addSpacing(2)

    def _row(self, lay, key, field):
        row = QHBoxLayout(); row.setContentsMargins(14, 0, 4, 0)
        lbl = BodyLabel(tr(key)); lbl.setStyleSheet("color:#8a93a6;"); lbl._key = key
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


class SettingsTab(QWidget):
    """Reachable via the gear icon to the right of the tab strip."""
    def __init__(self, hub):
        super().__init__()
        self.hub = hub; self._bars = []; self._guard = False
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background:transparent;")
        outer.addWidget(scroll)
        host = QWidget(); host.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(host); lay.setContentsMargins(2, 6, 12, 8); lay.setSpacing(6)
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
        self.tray_hint = CaptionLabel(tr("set.tray_h")); self.tray_hint.setStyleSheet("color:#8a93a6;")
        self.tray_hint.setWordWrap(True); lay.addWidget(self.tray_hint)
        lay.addSpacing(10)

        self.sec_app = self._sec(lay, "set.appearance")
        self.combo_theme = ComboBox(); self.combo_theme.setMinimumWidth(190)
        self.combo_theme.addItem(tr("about.theme_dark"), userData="dark")
        self.combo_theme.addItem(tr("about.theme_light"), userData="light")
        self.combo_theme.setCurrentIndex(0 if isDarkTheme() else 1)
        self.combo_theme.currentIndexChanged.connect(self._on_theme)
        self.row_theme = self._field(lay, "set.theme", self.combo_theme)
        lay.addSpacing(10)

        self.sec_test = self._sec(lay, "set.testing")
        self.combo_test = ComboBox(); self.combo_test.setMinimumWidth(190)
        self.combo_test.addItem(tr("about.test_auto"), userData=None)
        self.combo_test.addItem("DFGT", userData="DFGT")
        self.combo_test.addItem("G27", userData="G27")
        self.combo_test.setCurrentIndex({"DFGT": 1, "G27": 2}.get(test_override, 0))
        self.combo_test.currentIndexChanged.connect(self._on_test)
        self.row_test = self._field(lay, "set.devmode", self.combo_test)
        self.test_hint = CaptionLabel(tr("set.devmode_h")); self.test_hint.setStyleSheet("color:#8a93a6;")
        self.test_hint.setWordWrap(True); lay.addWidget(self.test_hint)
        lay.addStretch(1)

    def _sec(self, lay, key):
        hd = section_header(tr(key)); hd._key = key; self._bars.append(hd._bar)
        lay.addSpacing(2); lay.addWidget(hd); lay.addSpacing(2)
        return hd

    def _field(self, lay, key, ctrl):
        row = QHBoxLayout(); row.setContentsMargins(14, 0, 4, 0)
        lbl = BodyLabel(tr(key)); lbl.setStyleSheet("color:#cfd4de;"); lbl._key = key
        row.addWidget(lbl); row.addStretch(1); row.addWidget(ctrl)
        lay.addLayout(row)
        return lbl

    def _on_theme(self, *_):
        if self._guard: return
        self.hub.set_theme(self.combo_theme.currentData())

    def _on_lang(self, *_):
        if self._guard: return
        set_language(self.combo_lang.currentData()); self.hub.retranslate_all()

    def _on_tray(self, *_):
        global_settings["minimize_to_tray"] = self.cb_tray.isChecked(); save_settings()
        self.hub.update_tray_state()

    def _on_test(self, *_):
        if self._guard: return
        set_test_override(self.combo_test.currentData())

    def restyle(self):
        for b in self._bars:
            b.setStyleSheet(f"background:{ACCENT}; border-radius:1px;")

    def retranslate(self):
        self._guard = True
        for hd in (self.sec_app, self.sec_gen, self.sec_test):
            hd._lbl.setText(tr(hd._key))
        for lbl, key in ((self.row_theme, "set.theme"), (self.row_lang, "set.language"),
                         (self.row_test, "set.devmode")):
            lbl.setText(tr(key))
        self.cb_tray.setText(tr("set.tray")); self.tray_hint.setText(tr("set.tray_h"))
        self.test_hint.setText(tr("set.devmode_h"))
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
        self.ffb_tab = FFBTestTab()
        self.input_tab = InputMonitorTab()
        self.info_tab = InfoTab()
        self.settings_tab = SettingsTab(hub)
        self._pages = [("wheel", "tab.wheel", self.wheel_tab),
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
        sep = QFrame(); sep.setObjectName("hsep"); sep.setFixedHeight(1)
        lay.addWidget(sep)
        lay.addWidget(self.stack, 1)

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
        for page in (self.wheel_tab, self.ffb_tab, self.input_tab, self.info_tab, self.settings_tab):
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
        self.device = CaptionLabel(""); self.device.setStyleSheet("color:#8a93a6;")
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
        actrow = QHBoxLayout(); actrow.setContentsMargins(0, 0, 18, 14); actrow.setSpacing(14)
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
        self.resize(int(global_settings.get("win_w", 1366)), int(global_settings.get("win_h", 720)))
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

    def _on_preset(self, name):
        try: self.wheelset.load_profile(name)
        except Exception: pass

    # ---- palette / theme ----
    def _apply_palette(self):
        self.setStyleSheet(hub_qss())
        self.telemetry.wheel._cache_key = None
        try: self.titleBar.style_buttons()
        except Exception: pass
        self.info_restyle()

    def info_restyle(self):
        for attr in ("info_tab", "settings_tab"):
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
                InfoBar.warning(tr("conn.not_connected"), tr("input.led_nc"), duration=2500,
                                position=InfoBarPosition.TOP, parent=self)
            return
        w = self.wheelset
        gain, spring, damper = w.s_gain.value(), w.s_spring.value(), w.s_damper.value()
        persist = w.cb_center.isChecked()
        di_center = w.s_center.value()
        center = di_center if persist else 0
        angle = w.s_rot.value()
        update_registry_ffb(gain, spring, damper, di_center, persist, angle)
        ffb_write(rotation_cmd(angle))
        ffb_write(autocenter_cmd(center, 7))
        self._applied_angle = angle   # telemetry now matches the new range
        prof = global_settings["profiles"].setdefault(global_settings["selected_profile"], {})
        prof.update({"angle": angle, "di_gain": gain, "di_spring": spring, "di_damper": damper,
                     "di_center": di_center, "di_persist": persist})
        save_settings()
        if not silent:
            InfoBar.success(tr("apply.ok_title"), tr("apply.ok_body"), duration=2000,
                            position=InfoBarPosition.TOP, parent=self)

    def _teardown(self):
        global running
        running = False
        try: ffb_write(autocenter_cmd(0, 7)); ffb_write(stop_forces_cmd())
        except Exception: pass
        try:
            if not self.isMaximized() and self.width() > 200 and self.height() > 200:
                global_settings["win_w"] = self.width(); global_settings["win_h"] = self.height()
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


def main():
    global main_window, CURRENT_LANG, running
    qInstallMessageHandler(_qt_msg_filter)
    saved = global_settings.get("language")
    if saved in LANG:
        CURRENT_LANG = saved
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
