/*
 * LWH SDK bridge - Legacy Wheel Hub's own LogitechSteeringWheel.dll.
 *
 * Games and programs written with Logitech's Steering Wheel SDK use this DLL
 * for everything wheel-related: reading the wheel, the force feedback
 * effects, the G27's RPM lights, and telling Logitech Gaming Software what
 * they want for the wheel (rotation, strengths) - Content Manager's
 * "Hardware lock" is one of those. Without LGS (or G HUB) there is no such
 * DLL on the computer: Content Manager finds nothing next to its exe, and
 * games built with the SDK's engine plugin (Unity, Unreal) find nothing
 * under the SDK's registration
 *     HKLM\SOFTWARE\Classes\CLSID\{63BD165D-1584-4E75-AB56-08330350545F}\ServerBinary
 * so the SDK in them stays switched off - no force feedback from it at all.
 *
 * This DLL has the SDK's full set of functions with the same names and the
 * same calling convention, and does what they are documented to do:
 *
 *   wheel input   DirectInput: up to four game controllers, axes -32768..32767,
 *                 128 buttons with pressed / triggered / released, POVs.
 *   forces        DirectInput effects with the SDK's shapes: spring, damper,
 *                 constant, side and frontal collision (150 ms, back to back
 *                 ones add up), dirt road (65 ms sine), bumpy road (100 ms
 *                 square), slippery road (negative damper), surface (sine /
 *                 square / triangle, the game's period), soft stop, airborne
 *                 (everything paused, resumed on landing).
 *   RPM lights    the Logitech driver's own escape command for the LEDs.
 *   settings      to Legacy Wheel Hub through the windows LGS had (LWH
 *                 answers them - see LgsRouter in LWH):
 *                   "Logitech Wingman Internal Message Router" - WM_COPYDATA,
 *                     dwData WM_USER+179 set (size 56, version 2, PID,
 *                     which 0 = all, the ten values; wParam = the game's
 *                     window) and WM_USER+178 get (answered the same way to
 *                     a window of ours);
 *                   "LCore_MessageHandler_{C464822E-...}" - the registered
 *                     messages "LGS_Msg_SetOperatingRange" / "...Get..."
 *                     (wParam PID, lParam degrees).
 *                 As the SDK does, the game's preferred settings (its own,
 *                 or the SDK's defaults) are sent when a wheel appears and
 *                 whenever the game changes them.
 *
 * Content Manager hands over Assetto Corsa's window, which is not in its own
 * process: then only the settings part works and the wheel is left alone,
 * exactly as before. No Logitech code is in here; the behaviour follows the
 * SDK's documentation. Build: build.sh (MinGW, x86 and x64).
 */
#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE
#define DIRECTINPUT_VERSION 0x0800
#include <windows.h>
#include <dinput.h>
#include <dbt.h>
#include <string.h>
#include <wchar.h>

#define LGS_CLASS   L"Logitech Wingman Internal Message Router"
#define LCORE_CLASS L"LCore_MessageHandler_{C464822E-04D1-4447-B918-6D5EB33E0E5D}"
#define LGS_GET (WM_USER + 178)
#define LGS_SET (WM_USER + 179)

#define MAXDEV 4
#define COLLISION_MS 150
#define NOMINAL DI_FFNOMINALMAX

typedef unsigned char bool8;            /* C++ bool: one byte */

/* The SDK's own layout (C++ bools are one byte, padded to the next int). */
typedef struct {
    bool8 forceEnable; unsigned char _p0[3];
    int overallGain;
    int springGain;
    int damperGain;
    bool8 defaultSpringEnabled; unsigned char _p1[3];
    int defaultSpringGain;
    bool8 combinePedals; unsigned char _p2[3];
    int wheelRange;
    bool8 gameSettingsEnabled;
    bool8 allowGameSettings;
    unsigned char _p3[2];
} LogiControllerPropertiesData;

/* the SDK's numbers */
enum { DT_NONE = -1, DT_WHEEL, DT_JOYSTICK, DT_GAMEPAD, DT_OTHER };
enum { MF_NONE = -1, MF_LOGITECH, MF_MICROSOFT, MF_OTHER };
enum { F_SPRING, F_CONSTANT, F_DAMPER, F_SIDE, F_FRONTAL, F_DIRT, F_BUMPY,
       F_SLIPPERY, F_SURFACE, F_NUMBER, F_SOFTSTOP, F_AIRBORNE };
#define NFX 11
enum { PT_SINE, PT_SQUARE, PT_TRIANGLE };
#define VID_LOGITECH  0x046D
#define VID_MICROSOFT 0x045E

/* LogiIsModelConnected's models, in the SDK's order */
static const WORD MODEL_PID[] = {
    0xC29B, 0xC29A, 0xC299, 0xCA03, 0xC295, 0xC298, 0xC294, 0xCA04, 0xC291, 0xC293,
    0xC286, 0xC215, 0xC213, 0xC214, 0xC283, 0xC285, 0xC2A8, 0xC2A9, 0xC2AA,
    0xC20A, 0xC218, 0xC219, 0xC211, 0xC216, 0xC21A, 0xC242,
    0xC294 /* G29 (as the SDK checks it) */, 0xC262 /* G920 */ };
static const WORD WHEEL_PID[] = { 0xC29B, 0xC29A, 0xC299, 0xCA03, 0xC295, 0xC298, 0xC294,
    0xCA04, 0xC291, 0xC293, 0xC24F, 0xC260, 0xC261, 0xC262, 0xC266, 0xC267, 0xC26D, 0xC26E };
static const WORD JOY_PID[] = { 0xC286, 0xC215, 0xC213, 0xC214, 0xC283, 0xC285, 0xC2A8 };
static const WORD OTHER_PID[] = { 0xC2A9, 0xC2AA };
static const WORD PAD_PID[] = { 0xC20A, 0xC218, 0xC219, 0xC211, 0xC216, 0xC21A, 0xC242 };

typedef struct {
    LPDIRECTINPUTEFFECT e;
    int playing;
    DICONDITION cond[2];
    DICONSTANTFORCE cf;
    DIPERIODIC per;
    DIENVELOPE env;
    LONG dir[2];
    const GUID *kind;
} Fx;

typedef struct {
    LPDIRECTINPUTDEVICE8W d;
    GUID inst;
    DWORD vid, pid;
    int type;
    int ffaxes;
    int seen;
    WCHAR name[MAX_PATH];
    WCHAR path[MAX_PATH];
    DIJOYSTATE2 cur, prev;
    int actuators;
    int lost;                    /* not acquired at the last read */
    int props_sent;
    int nl_coeff, nl_ready;
    int nl[1024];
    Fx fx[NFX];
    int airborne;
    int before_air[NFX];
    int spring_was, damper_was;  /* paused by the slippery road */
    DWORD side_t, front_t;
    int side_mag, front_mag;
} Dev;

static CRITICAL_SECTION g_cs;
static HWND g_game;              /* the game's window */
static int g_inited;
static bool8 g_ignore_x;
static LPDIRECTINPUT8W g_di;
static Dev g_dev[MAXDEV];
static DIJOYSTATE2 g_copy[MAXDEV], g_empty;
static HWND g_note;              /* ours: device arrival / removal */
static HDEVNOTIFY g_hdn;
static volatile LONG g_changed;
static DWORD g_rescan_next, g_rescan_until;
static HWND g_reply;             /* ours, for the answer to a get */
static int g_got;
static int g_pending;            /* wishes without a wheel (Content Manager) not delivered yet */
static DWORD g_retry_at;
static int g_vals[10];
/* what the game prefers; until it says, the SDK's defaults without LGS */
static int g_pref[10] = { 1, 100, 100, 100, 0, 100, 0, 200, 1, 1 };
__attribute__((used)) static const char g_mark[] = "LWH SDK bridge 3";   /* lets LWH know this file is its own */

/* ------------------------------------------------------------------ LWH */

static HWND lgs(void) { return FindWindowW(LGS_CLASS, NULL); }
static HWND lcore(void) {
    HWND w = FindWindowW(LCORE_CLASS, NULL);
    return w ? w : lgs();          /* LWH before 2.0 answered both on one window */
}

/* The window that stands for the game, as the SDK sends it: the one the
   SDK was initialised with (Content Manager passes Assetto Corsa's), else
   the one in front. LWH finds the game's profile through it. */
static HWND game_window(void) {
    if (g_game && IsWindow(g_game)) return g_game;
    return GetForegroundWindow();
}

static LRESULT CALLBACK reply_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_COPYDATA && l) {
        COPYDATASTRUCT *c = (COPYDATASTRUCT *)l;
        if (c->dwData == LGS_GET && c->cbData >= 52 && c->lpData) {
            memcpy(g_vals, (const char *)c->lpData + 12, sizeof g_vals);
            g_got = 1;
            return 1;
        }
    }
    return DefWindowProcW(h, m, w, l);
}

static HINSTANCE self_module(void) {
    HMODULE h = NULL;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCWSTR)(void *)&self_module, &h);
    return h ? h : GetModuleHandleW(NULL);
}

static HWND reply_window(void) {
    if (g_reply && IsWindow(g_reply)) return g_reply;
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof wc);
    wc.lpfnWndProc = reply_proc;
    wc.hInstance = self_module();
    wc.lpszClassName = L"LWH SDK bridge reply";
    RegisterClassW(&wc);
    g_reply = CreateWindowExW(0, wc.lpszClassName, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, NULL, wc.hInstance, NULL);
    return g_reply;
}

static BOOL send_copy(HWND from, DWORD kind, const void *data, DWORD size) {
    HWND w = lgs();
    if (!w) return FALSE;
    COPYDATASTRUCT cds = { kind, size, (void *)data };
    DWORD_PTR res = 0;
    return SendMessageTimeoutW(w, WM_COPYDATA, (WPARAM)from, (LPARAM)&cds,
                               SMTO_ABORTIFHUNG, 2000, &res) != 0 && res != 0;
}

static int any_device(void) {
    for (int i = 0; i < MAXDEV; i++) if (g_dev[i].d) return 1;
    return 0;
}

static BOOL send_set(DWORD pid) {
    int msg[14] = { 56, 2, (int)(pid & 0xFFFF), 0 };
    memcpy(msg + 4, g_pref, sizeof g_pref);
    return send_copy(game_window(), LGS_SET, msg, sizeof msg);
}

static BOOL ask_props(DWORD pid) {
    int ask[13] = { 52, 2, (int)(pid & 0xFFFF) };
    memcpy(ask + 3, g_pref, sizeof g_pref);
    g_got = 0;
    return send_copy(reply_window(), LGS_GET, ask, sizeof ask) && g_got;
}

static BOOL range_msg(const wchar_t *name, DWORD pid, LPARAM deg, DWORD_PTR *res) {
    HWND w = lcore();
    if (!w) return FALSE;
    UINT m = RegisterWindowMessageW(name);
    *res = 0;
    return m && SendMessageTimeoutW(w, m, (WPARAM)(pid & 0xFFFF), deg, SMTO_ABORTIFHUNG, 2000, res) != 0;
}

/* ---------------------------------------------------------- the devices */

static int in_list(const WORD *l, int n, DWORD pid) {
    for (int i = 0; i < n; i++) if (l[i] == pid) return 1;
    return 0;
}
#define IN_LIST(l, p) in_list(l, (int)(sizeof l / sizeof l[0]), p)

static Dev *dev_at(int i) {
    if (i < 0 || i >= MAXDEV || !g_dev[i].d) return NULL;
    return &g_dev[i];
}

static int from_pct(int pct, int lo, int hi, int out_lo, int out_hi) {
    if (pct < lo) pct = lo;
    if (pct > hi) pct = hi;
    return (int)((float)(out_hi - out_lo) * ((float)(pct - lo) / (float)(hi - lo))) + out_lo;
}

static void fx_release(Fx *f) {
    if (f->e) {
        IDirectInputEffect_Stop(f->e);
        IDirectInputEffect_Unload(f->e);
        IDirectInputEffect_Release(f->e);
    }
    f->e = NULL;
    f->playing = 0;
}

static void dev_release(Dev *v) {
    for (int k = 0; k < NFX; k++) fx_release(&v->fx[k]);
    if (v->d) {
        IDirectInputDevice8_Unacquire(v->d);
        IDirectInputDevice8_Release(v->d);
    }
    ZeroMemory(v, sizeof *v);
}

static BOOL CALLBACK axis_cb(LPCDIDEVICEOBJECTINSTANCEW o, LPVOID ctx) {
    Dev *v = (Dev *)ctx;
    DIPROPRANGE r;
    r.diph.dwSize = sizeof r;
    r.diph.dwHeaderSize = sizeof r.diph;
    r.diph.dwHow = DIPH_BYID;
    r.diph.dwObj = o->dwType;
    r.lMin = -32768;
    r.lMax = 32767;
    IDirectInputDevice8_SetProperty(v->d, DIPROP_RANGE, &r.diph);
    if (o->dwFlags & DIDOI_FFACTUATOR) v->ffaxes++;
    return DIENUM_CONTINUE;
}

static void init_state(DIJOYSTATE2 *s) {
    ZeroMemory(s, sizeof *s);
    for (int i = 0; i < 4; i++) s->rgdwPOV[i] = 0xFFFFFFFF;
}

static BOOL CALLBACK device_cb(LPCDIDEVICEINSTANCEW di, LPVOID ctx) {
    (void)ctx;
    for (int i = 0; i < MAXDEV; i++)
        if (g_dev[i].d && IsEqualGUID(&g_dev[i].inst, &di->guidInstance)) {
            g_dev[i].seen = 1;
            return DIENUM_CONTINUE;
        }
    int slot = -1;
    for (int i = 0; i < MAXDEV; i++) if (!g_dev[i].d) { slot = i; break; }
    if (slot < 0) return DIENUM_STOP;
    LPDIRECTINPUTDEVICE8W d = NULL;
    if (FAILED(IDirectInput8_CreateDevice(g_di, &di->guidInstance, &d, NULL)) || !d)
        return DIENUM_CONTINUE;
    DIPROPGUIDANDPATH gp;
    ZeroMemory(&gp, sizeof gp);
    gp.diph.dwSize = sizeof gp;
    gp.diph.dwHeaderSize = sizeof gp.diph;
    gp.diph.dwHow = DIPH_DEVICE;
    IDirectInputDevice8_GetProperty(d, DIPROP_GUIDANDPATH, &gp.diph);
    if (g_ignore_x) {
        WCHAR up[MAX_PATH];
        wcsncpy(up, gp.wszPath, MAX_PATH - 1);
        up[MAX_PATH - 1] = 0;
        _wcsupr(up);
        if (wcsstr(up, L"IG_")) {               /* an XInput controller: the game reads those itself */
            IDirectInputDevice8_Release(d);
            return DIENUM_CONTINUE;
        }
    }
    if (FAILED(IDirectInputDevice8_SetDataFormat(d, &c_dfDIJoystick2))
        || FAILED(IDirectInputDevice8_SetCooperativeLevel(d, GetAncestor(g_game, GA_ROOT),
                                                          DISCL_EXCLUSIVE | DISCL_FOREGROUND))) {
        IDirectInputDevice8_Release(d);
        return DIENUM_CONTINUE;
    }
    Dev *v = &g_dev[slot];
    ZeroMemory(v, sizeof *v);
    v->d = d;
    v->inst = di->guidInstance;
    v->seen = 1;
    v->vid = LOWORD(di->guidProduct.Data1);
    v->pid = HIWORD(di->guidProduct.Data1);
    wcsncpy(v->name, di->tszProductName, MAX_PATH - 1);
    wcsncpy(v->path, gp.wszPath, MAX_PATH - 1);
    v->nl_coeff = 0x7FFFFFFF;
    if (v->vid == VID_LOGITECH && IN_LIST(PAD_PID, v->pid)) v->type = DT_GAMEPAD;
    else if (v->vid == VID_LOGITECH && IN_LIST(JOY_PID, v->pid)) v->type = DT_JOYSTICK;
    else if (v->vid == VID_LOGITECH && IN_LIST(OTHER_PID, v->pid)) v->type = DT_OTHER;
    else if (v->vid == VID_LOGITECH && IN_LIST(WHEEL_PID, v->pid)) v->type = DT_WHEEL;
    else switch (GET_DIDEVICE_TYPE(di->dwDevType)) {
        case DI8DEVTYPE_GAMEPAD: v->type = DT_GAMEPAD; break;
        case DI8DEVTYPE_JOYSTICK: v->type = DT_JOYSTICK; break;
        case DI8DEVTYPE_DRIVING: v->type = DT_WHEEL; break;
        default: v->type = DT_OTHER;
    }
    IDirectInputDevice8_EnumObjects(d, axis_cb, v, DIDFT_AXIS);
    if (v->ffaxes > 2) v->ffaxes = 2;
    if (v->ffaxes) {
        DIPROPDWORD ac;
        ac.diph.dwSize = sizeof ac;
        ac.diph.dwHeaderSize = sizeof ac.diph;
        ac.diph.dwObj = 0;
        ac.diph.dwHow = DIPH_DEVICE;
        ac.dwData = DIPROPAUTOCENTER_OFF;
        IDirectInputDevice8_SetProperty(d, DIPROP_AUTOCENTER, &ac.diph);
    }
    init_state(&v->cur);
    init_state(&v->prev);
    return DIENUM_CONTINUE;
}

static void enumerate(void) {
    if (!g_di) return;
    for (int i = 0; i < MAXDEV; i++) g_dev[i].seen = 0;
    if (FAILED(IDirectInput8_EnumDevices(g_di, DI8DEVCLASS_GAMECTRL, device_cb, NULL, DIEDFL_ATTACHEDONLY)))
        return;
    for (int i = 0; i < MAXDEV; i++)
        if (g_dev[i].d && !g_dev[i].seen) dev_release(&g_dev[i]);
}

static LRESULT CALLBACK note_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_DEVICECHANGE && (w == DBT_DEVICEARRIVAL || w == DBT_DEVICEREMOVECOMPLETE))
        InterlockedExchange(&g_changed, 1);
    return DefWindowProcW(h, m, w, l);
}

static void note_start(void) {
    static const GUID hid = { 0x4D1E55B2, 0xF16F, 0x11CF, { 0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30 } };
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof wc);
    wc.lpfnWndProc = note_proc;
    wc.hInstance = self_module();
    wc.lpszClassName = L"LWH SDK bridge devices";
    RegisterClassW(&wc);
    g_note = CreateWindowExW(0, wc.lpszClassName, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, NULL, wc.hInstance, NULL);
    if (g_note) {
        DEV_BROADCAST_DEVICEINTERFACE_W f;
        ZeroMemory(&f, sizeof f);
        f.dbcc_size = sizeof f;
        f.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE;
        f.dbcc_classguid = hid;
        g_hdn = RegisterDeviceNotificationW(g_note, &f, DEVICE_NOTIFY_WINDOW_HANDLE);
    }
}

static int own_window(HWND w) {
    DWORD pid = 0;
    if (!w || !IsWindow(w)) return 0;
    GetWindowThreadProcessId(w, &pid);
    return pid == GetCurrentProcessId();
}

static void play_leds(LPDIRECTINPUTDEVICE8W d, float rpm, float first, float red) {
    struct { DWORD size, version; float rpm, first, red; } data = { sizeof data, 1, rpm, first, red };
    DIEFFESCAPE e;
    ZeroMemory(&e, sizeof e);
    e.dwSize = sizeof e;
    e.dwCommand = 0;                       /* the driver's LED command */
    e.lpvInBuffer = &data;
    e.cbInBuffer = sizeof data;
    IDirectInputDevice8_Escape(d, &e);
}

static void teardown(void) {
    for (int i = 0; i < MAXDEV; i++)
        if (g_dev[i].d) {
            play_leds(g_dev[i].d, 0.0f, 1.0f, 2.0f);   /* lights off, as the SDK leaves them */
            dev_release(&g_dev[i]);
        }
    if (g_di) IDirectInput8_Release(g_di);
    g_di = NULL;
    if (g_hdn) UnregisterDeviceNotification(g_hdn);
    g_hdn = NULL;
    if (g_note) DestroyWindow(g_note);
    g_note = NULL;
    g_inited = 0;
}

/* LWH not running: start what LGS's registration points to, as the SDK does
   (LWH's LWHGameLink.exe, which starts LWH in the tray) - once per program */
static void start_lgs(void) {
    static int tried;
    if (tried || lgs()) return;
    tried = 1;
    WCHAR path[MAX_PATH + 32];
    DWORD size = MAX_PATH * sizeof(WCHAR), type = 0;
    HKEY k;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"Software\\Logitech\\Gaming Software", 0, KEY_READ, &k) != ERROR_SUCCESS)
        return;
    LONG r = RegQueryValueExW(k, L"Profiler", NULL, &type, (BYTE *)(path + 1), &size);
    RegCloseKey(k);
    if (r != ERROR_SUCCESS || type != REG_SZ || size < 4) return;
    path[0] = L'"';
    path[1 + size / sizeof(WCHAR)] = 0;
    lstrcatW(path, L"\" /noui /force");
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    if (CreateProcessW(NULL, path, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
}

static int do_init(void) {
    if (!g_game) return 0;
    teardown();
    g_inited = 1;
    start_lgs();
    /* the wheel is only touched for a program's own window (not for Content
       Manager, which passes Assetto Corsa's) */
    if (own_window(g_game)) {
        if (SUCCEEDED(DirectInput8Create(GetModuleHandleW(NULL), DIRECTINPUT_VERSION, &IID_IDirectInput8W,
                                         (void **)&g_di, NULL))) {
            note_start();
            enumerate();
        }
    }
    for (int i = 0; i < MAXDEV; i++) g_dev[i].props_sent = 0;
    return 1;
}

/* the program's top-level window that is in front, if any (the SDK's way of
   finding the game when it is not told) */
static HWND front_own(void) {
    HWND fg = GetForegroundWindow();
    return own_window(fg) ? fg : NULL;
}

/* ------------------------------------------------------------- forces */

static int fx_start(Dev *v, Fx *f) {
    if (!f->e) return 0;
    if (FAILED(IDirectInputDevice8_Acquire(v->d))) return 0;
    if (SUCCEEDED(IDirectInputEffect_Start(f->e, 1, 0))) { f->playing = 1; return 1; }
    return 0;
}

static int fx_stop(Dev *v, Fx *f) {
    if (!f->e) { f->playing = 0; return 0; }
    if (!f->playing) return 1;
    IDirectInputDevice8_Acquire(v->d);
    if (SUCCEEDED(IDirectInputEffect_Stop(f->e))) { f->playing = 0; return 1; }
    return 0;
}

static int playing(Dev *v, int k) { return v->fx[k].e && v->fx[k].playing; }

/* fill a DIEFFECT for force k from its stored parameters */
static void fx_desc(Dev *v, int k, DIEFFECT *e, DWORD *axes) {
    Fx *f = &v->fx[k];
    axes[0] = DIJOFS_X;
    axes[1] = DIJOFS_Y;
    ZeroMemory(e, sizeof *e);
    e->dwSize = sizeof *e;
    e->dwFlags = DIEFF_CARTESIAN | DIEFF_OBJECTOFFSETS;
    e->dwDuration = (k == F_SIDE || k == F_FRONTAL) ? COLLISION_MS * 1000 : INFINITE;
    e->dwGain = NOMINAL;
    e->dwTriggerButton = DIEB_NOTRIGGER;
    e->cAxes = (DWORD)v->ffaxes;
    e->rgdwAxes = axes;
    if (!f->dir[0] && !f->dir[1]) f->dir[0] = 1;
    e->rglDirection = f->dir;
    switch (k) {
    case F_SPRING: case F_DAMPER: case F_SLIPPERY: case F_SOFTSTOP:
        e->cbTypeSpecificParams = e->cAxes * sizeof(DICONDITION);
        e->lpvTypeSpecificParams = f->cond;
        break;
    case F_CONSTANT: case F_SIDE:
        e->cbTypeSpecificParams = sizeof(DICONSTANTFORCE);
        e->lpvTypeSpecificParams = &f->cf;
        break;
    default:
        e->cbTypeSpecificParams = sizeof(DIPERIODIC);
        e->lpvTypeSpecificParams = &f->per;
        if (k == F_FRONTAL) {
            f->env.dwSize = sizeof f->env;
            f->env.dwFadeTime = 20000;     /* 20 ms fade at the end */
            e->lpEnvelope = &f->env;
        }
    }
}

static const GUID *fx_kind(int k, const Fx *f) {
    switch (k) {
    case F_SPRING: case F_SOFTSTOP: return &GUID_Spring;
    case F_DAMPER: case F_SLIPPERY: return &GUID_Damper;
    case F_CONSTANT: case F_SIDE: return &GUID_ConstantForce;
    case F_DIRT: return &GUID_Sine;
    case F_FRONTAL: case F_BUMPY: return &GUID_Square;
    default: return f->kind ? f->kind : &GUID_Sine;
    }
}

/* create or update force k with its stored parameters */
static int fx_apply(Dev *v, int k) {
    Fx *f = &v->fx[k];
    DIEFFECT e;
    DWORD axes[2];
    fx_desc(v, k, &e, axes);
    if (!f->e) {
        IDirectInputDevice8_Acquire(v->d);    /* not in front: created now, sent to the wheel on start */
        return SUCCEEDED(IDirectInputDevice8_CreateEffect(v->d, fx_kind(k, f), &e, &f->e, NULL));
    }
    return SUCCEEDED(IDirectInputEffect_SetParameters(f->e, &e, DIEP_DIRECTION | DIEP_TYPESPECIFICPARAMS));
}

static void set_dir(Dev *v, Fx *f) {
    if (v->type == DT_JOYSTICK && v->vid == VID_MICROSOFT) { f->dir[0] = 0; f->dir[1] = 1; }
    else { f->dir[0] = 1; f->dir[1] = 0; }
}

static void cond_y(Fx *f) {      /* the second axis of a joystick: a steady spring */
    f->cond[1].lDeadBand = 0;
    f->cond[1].lOffset = 0;
    f->cond[1].lPositiveCoefficient = f->cond[1].lNegativeCoefficient = from_pct(80, -100, 100, -NOMINAL, NOMINAL);
    f->cond[1].dwPositiveSaturation = f->cond[1].dwNegativeSaturation = (DWORD)from_pct(80, 0, 100, 0, NOMINAL);
}

/* the common checks of the Play functions */
static Dev *ff_dev(int i) {
    Dev *v = dev_at(i);
    return (v && v->ffaxes > 0) ? v : NULL;
}

static int play_spring_params(Dev *v) {
    int ok = fx_apply(v, F_SPRING);
    if (!v->fx[F_SPRING].playing) ok = fx_start(v, &v->fx[F_SPRING]);
    return ok;
}

static int resume(Dev *v, int k) {         /* start a paused force again with what it had */
    if (!v->fx[k].e) return 0;
    if (v->airborne) return 0;
    if ((k == F_SPRING || k == F_DAMPER) && playing(v, F_SLIPPERY)) return 0;
    int ok = fx_apply(v, k);
    if (!v->fx[k].playing) ok = fx_start(v, &v->fx[k]);
    return ok;
}

/* after the game had lost the wheel (another program in front), what was
   playing starts again */
static void restart_playing(Dev *v) {
    for (int k = 0; k < NFX; k++) {
        Fx *f = &v->fx[k];
        if (f->e && f->playing && k != F_SIDE && k != F_FRONTAL) {
            IDirectInputEffect_Stop(f->e);
            IDirectInputEffect_Start(f->e, 1, 0);
        }
    }
}

static void dev_read(Dev *v) {
    HRESULT hr = IDirectInputDevice8_Poll(v->d);
    int ffwheel = v->ffaxes > 0 && v->type == DT_WHEEL;
    if (FAILED(hr)) {
        hr = IDirectInputDevice8_Acquire(v->d);
        if (SUCCEEDED(hr)) {
            if (ffwheel)
                v->actuators = SUCCEEDED(IDirectInputDevice8_SendForceFeedbackCommand(v->d, DISFFC_SETACTUATORSON));
            restart_playing(v);              /* the wheel had been let go: its forces stopped with it */
            v->lost = 0;
        } else {
            v->lost = 1;
            if (hr == (HRESULT)DIERR_UNPLUGGED) InterlockedExchange(&g_changed, 1);
        }
        return;
    }
    if (!v->actuators && ffwheel)
        v->actuators = SUCCEEDED(IDirectInputDevice8_SendForceFeedbackCommand(v->d, DISFFC_SETACTUATORSON));
    v->prev = v->cur;
    hr = IDirectInputDevice8_GetDeviceState(v->d, sizeof(DIJOYSTATE2), &v->cur);
    if (FAILED(hr)) {
        if (hr == DIERR_INPUTLOST) init_state(&v->cur);
        v->lost = 1;
        return;
    }
    if (v->type == DT_GAMEPAD) {
        v->cur.lY = -v->cur.lY - 1;
        v->cur.lRz = -v->cur.lRz - 1;
    }
}

static void gen_nonlinear(Dev *v, int coeff) {
    if (v->nl_ready && coeff == v->nl_coeff) return;
    v->nl_coeff = coeff;
    float c = (float)coeff / 100.0f;
    for (int i = 0; i < 1023; i++) {
        float x = ((float)i - 511.0f) / 511.0f;
        float y = 32767.5f * ((1.0f - c) * x + c * x * x * x) - 0.5f;
        if (y < -32768.0f) y = -32768.0f;
        if (y > 32767.0f) y = 32767.0f;
        v->nl[i] = (int)y;
    }
    v->nl[1023] = v->nl[1022];
    v->nl_ready = 1;
}

/* ------------------------------------------------------------ exports */

#define API      /* exported by exports.def, undecorated, __cdecl */
#define LOCK     EnterCriticalSection(&g_cs)
#define UNLOCK   LeaveCriticalSection(&g_cs)
#define RET(x)   do { int r_ = (x); UNLOCK; return r_ ? 1 : 0; } while (0)

API bool8 LogiSteeringInitialize(bool8 ignoreXInputControllers) {
    LOCK;
    g_ignore_x = ignoreXInputControllers ? 1 : 0;
    HWND w = front_own();
    if (w) g_game = w;
    RET(do_init());
}

API bool8 LogiSteeringInitializeWithWindow(bool8 ignoreXInputControllers, HWND hwnd) {
    LOCK;
    g_ignore_x = ignoreXInputControllers ? 1 : 0;
    g_game = hwnd;
    RET(do_init());
}

API void LogiSteeringShutdown(void) {
    LOCK;
    teardown();
    g_game = NULL;
    if (g_reply) { DestroyWindow(g_reply); g_reply = NULL; }
    UNLOCK;
}

API bool8 LogiUpdate(void) {
    LOCK;
    if (!g_inited) {
        HWND w = front_own();
        if (w) { g_game = w; do_init(); }
        RET(0);
    }
    DWORD now = GetTickCount();
    if (InterlockedExchange(&g_changed, 0)) {
        enumerate();
        /* a device can take a moment to show up: look again for a few seconds */
        g_rescan_until = now + 7000;
        g_rescan_next = now + 1500;
    } else if (g_rescan_until && (LONG)(now - g_rescan_next) >= 0) {
        enumerate();
        g_rescan_next = now + 1500;
        if ((LONG)(now - g_rescan_until) >= 0) g_rescan_until = 0;
    }
    for (int i = 0; i < MAXDEV; i++) {
        Dev *v = &g_dev[i];
        if (!v->d) continue;
        dev_read(v);
        /* a wheel appeared: its wishes go to LWH - again every 2 s until
           LWH is there to take them (it may be starting just now) */
        if (!v->props_sent && (LONG)(now - g_retry_at) >= 0) {
            v->props_sent = send_set(v->pid);
            if (!v->props_sent) g_retry_at = now + 2000;
        }
    }
    if (g_pending && !any_device() && (LONG)(now - g_retry_at) >= 0) {
        g_pending = !send_set(0);
        if (g_pending) g_retry_at = now + 2000;
    }
    RET(1);
}

API DIJOYSTATE2 *LogiGetState(int index) {
    LOCK;
    Dev *v = dev_at(index);
    DIJOYSTATE2 *s = v ? &v->cur : NULL;
    UNLOCK;
    return s;
}

/* the engines' version: the same state, always readable */
API DIJOYSTATE2 *LogiGetStateENGINES(int index) {
    LOCK;
    Dev *v = dev_at(index);
    DIJOYSTATE2 *s;
    if (v) { g_copy[index] = v->cur; s = &g_copy[index]; }
    else { init_state(&g_empty); s = &g_empty; }
    UNLOCK;
    return s;
}

static bool8 copy_text(const WCHAR *src, wchar_t *buf, int size) {
    if (!buf || size <= 0) return 0;
    int n = (int)wcslen(src);
    if (n >= size) n = size - 1;
    memcpy(buf, src, (size_t)n * sizeof(wchar_t));
    buf[n] = 0;
    return 1;
}

API bool8 LogiGetDevicePath(int index, wchar_t *buffer, int bufferSize) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v ? copy_text(v->path, buffer, bufferSize) : 0);
}

API bool8 LogiGetFriendlyProductName(int index, wchar_t *buffer, int bufferSize) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v ? copy_text(v->name, buffer, bufferSize) : 0);
}

API bool8 LogiIsConnected(int index) {
    LOCK;
    RET(dev_at(index) != NULL);
}

API bool8 LogiIsDeviceConnected(int index, int deviceType) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v && v->type == deviceType);
}

API bool8 LogiIsManufacturerConnected(int index, int manufacturerName) {
    LOCK;
    Dev *v = dev_at(index);
    int r = 0;
    if (v) {
        if (manufacturerName == MF_LOGITECH) r = v->vid == VID_LOGITECH;
        else if (manufacturerName == MF_MICROSOFT) r = v->vid == VID_MICROSOFT;
        else if (manufacturerName == MF_OTHER) r = v->vid != VID_LOGITECH && v->vid != VID_MICROSOFT;
    }
    RET(r);
}

API bool8 LogiIsModelConnected(int index, int modelName) {
    LOCK;
    Dev *v = dev_at(index);
    int n = (int)(sizeof MODEL_PID / sizeof MODEL_PID[0]);
    RET(v && v->vid == VID_LOGITECH && modelName >= 0 && modelName < n && v->pid == MODEL_PID[modelName]);
}

static int btn(int index, int b, int kind) {
    Dev *v = dev_at(index);
    if (!v || b < 0 || b > 127) return 0;
    int now = (v->cur.rgbButtons[b] & 0x80) != 0, before = (v->prev.rgbButtons[b] & 0x80) != 0;
    return kind == 0 ? now : kind == 1 ? (now && !before) : (!now && before);
}

API bool8 LogiButtonIsPressed(int index, int buttonNbr) { LOCK; RET(btn(index, buttonNbr, 0)); }
API bool8 LogiButtonTriggered(int index, int buttonNbr) { LOCK; RET(btn(index, buttonNbr, 1)); }
API bool8 LogiButtonReleased(int index, int buttonNbr) { LOCK; RET(btn(index, buttonNbr, 2)); }

API bool8 LogiGenerateNonLinearValues(int index, int nonLinCoeff) {
    LOCK;
    Dev *v = dev_at(index);
    if (v) gen_nonlinear(v, nonLinCoeff < -100 ? -100 : nonLinCoeff > 100 ? 100 : nonLinCoeff);
    RET(v != NULL);
}

API int LogiGetNonLinearValue(int index, int inputValue) {
    LOCK;
    Dev *v = dev_at(index);
    int r = 0;
    if (v) {
        if (!v->nl_ready) gen_nonlinear(v, 0);
        if (inputValue < -32768) r = -32768;
        else if (inputValue > 32767) r = 32767;
        else {
            int i = (int)((511.5f * (float)inputValue) / 32767.0f + 511.5f);
            r = (i >= 0 && i < 1024) ? v->nl[i] : inputValue;
        }
    }
    UNLOCK;
    return r;
}

API bool8 LogiHasForceFeedback(int index) {
    LOCK;
    RET(ff_dev(index) != NULL);
}

API bool8 LogiIsPlaying(int index, int forceType) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v) RET(0);
    if (forceType == F_AIRBORNE) RET(v->airborne);
    if (forceType < 0 || forceType >= NFX || forceType == F_NUMBER) RET(0);
    RET(playing(v, forceType));
}

API bool8 LogiPlaySpringForce(int index, int offsetPercentage, int saturationPercentage, int coefficientPercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v || v->airborne || playing(v, F_SLIPPERY)) RET(0);
    if (v->type == DT_GAMEPAD) RET(1);          /* a moving spring only buzzes a game pad */
    Fx *f = &v->fx[F_SPRING];
    set_dir(v, f);
    f->cond[0].lDeadBand = 0;
    f->cond[0].lOffset = from_pct(offsetPercentage, -100, 100, -NOMINAL, NOMINAL);
    if (v->type == DT_JOYSTICK && v->vid == VID_MICROSOFT) f->cond[0].lOffset = -f->cond[0].lOffset;
    f->cond[0].dwPositiveSaturation = f->cond[0].dwNegativeSaturation = (DWORD)from_pct(saturationPercentage, 0, 100, 0, NOMINAL);
    f->cond[0].lPositiveCoefficient = f->cond[0].lNegativeCoefficient = from_pct(coefficientPercentage, -100, 100, -NOMINAL, NOMINAL);
    cond_y(f);
    RET(play_spring_params(v));
}

API bool8 LogiStopSpringForce(int index) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v && fx_stop(v, &v->fx[F_SPRING]));
}

API bool8 LogiPlayConstantForce(int index, int magnitudePercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v || v->airborne) RET(0);
    if (v->type == DT_GAMEPAD) RET(1);
    Fx *f = &v->fx[F_CONSTANT];
    LONG m = from_pct(magnitudePercentage, -100, 100, -NOMINAL, NOMINAL);
    int ok = 1;
    if (!f->e || f->cf.lMagnitude != m) { f->cf.lMagnitude = m; ok = fx_apply(v, F_CONSTANT); }
    if (!f->playing) ok = fx_start(v, f);
    RET(ok);
}

API bool8 LogiStopConstantForce(int index) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v && fx_stop(v, &v->fx[F_CONSTANT]));
}

API bool8 LogiPlayDamperForce(int index, int coefficientPercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v || v->airborne || playing(v, F_SLIPPERY)) RET(0);
    Fx *f = &v->fx[F_DAMPER];
    set_dir(v, f);
    LONG c = from_pct(coefficientPercentage, -100, 100, -NOMINAL, NOMINAL);
    int ok = 1;
    if (!f->e || f->cond[0].lPositiveCoefficient != c) {
        f->cond[0].lDeadBand = 0;
        f->cond[0].lOffset = 0;
        f->cond[0].lPositiveCoefficient = f->cond[0].lNegativeCoefficient = c;
        f->cond[0].dwPositiveSaturation = f->cond[0].dwNegativeSaturation = NOMINAL;
        cond_y(f);
        ok = fx_apply(v, F_DAMPER);
    }
    if (!f->playing) ok = fx_start(v, f);
    RET(ok);
}

API bool8 LogiStopDamperForce(int index) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v && fx_stop(v, &v->fx[F_DAMPER]));
}

/* the two collisions: 150 ms; a second one within that adds to the first */
static int collision(Dev *v, int k, int pct) {
    DWORD now = GetTickCount();
    DWORD *t = k == F_SIDE ? &v->side_t : &v->front_t;
    int *m = k == F_SIDE ? &v->side_mag : &v->front_mag;
    if (now - *t > COLLISION_MS) { *m = pct; *t = now; }
    else { *m += pct; if (*m > 100) *m = 100; }
    Fx *f = &v->fx[k];
    if (k == F_SIDE) f->cf.lMagnitude = from_pct(*m, -100, 100, -NOMINAL, NOMINAL);
    else {
        f->per.dwMagnitude = (DWORD)from_pct(*m, 0, 100, 0, NOMINAL);
        f->per.dwPeriod = 75000;
        f->per.lOffset = 0;
        f->per.dwPhase = 0;
    }
    fx_apply(v, k);
    return fx_start(v, f);
}

API bool8 LogiPlaySideCollisionForce(int index, int magnitudePercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    RET(v && collision(v, F_SIDE, magnitudePercentage));
}

API bool8 LogiPlayFrontalCollisionForce(int index, int magnitudePercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    RET(v && collision(v, F_FRONTAL, magnitudePercentage));
}

static int periodic(Dev *v, int k, int pct, DWORD period_us) {
    Fx *f = &v->fx[k];
    DWORD m = (DWORD)from_pct(pct, -100, 100, -NOMINAL, NOMINAL);
    int ok = 1;
    if (!f->e || f->per.dwMagnitude != m || f->per.dwPeriod != period_us) {
        f->per.dwMagnitude = m;
        f->per.dwPeriod = period_us;
        f->per.lOffset = 0;
        f->per.dwPhase = 0;
        ok = fx_apply(v, k);
    }
    if (!f->playing) ok = fx_start(v, f);
    return ok;
}

API bool8 LogiPlayDirtRoadEffect(int index, int magnitudePercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v || v->airborne) RET(0);
    RET(periodic(v, F_DIRT, magnitudePercentage, 65000));
}

API bool8 LogiStopDirtRoadEffect(int index) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v && fx_stop(v, &v->fx[F_DIRT]));
}

API bool8 LogiPlayBumpyRoadEffect(int index, int magnitudePercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v || v->airborne) RET(0);
    RET(periodic(v, F_BUMPY, magnitudePercentage, 100000));
}

API bool8 LogiStopBumpyRoadEffect(int index) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v && fx_stop(v, &v->fx[F_BUMPY]));
}

API bool8 LogiPlaySlipperyRoadEffect(int index, int magnitudePercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v || v->airborne) RET(0);
    if (playing(v, F_SPRING)) { fx_stop(v, &v->fx[F_SPRING]); v->spring_was = 1; }
    if (playing(v, F_DAMPER)) { fx_stop(v, &v->fx[F_DAMPER]); v->damper_was = 1; }
    Fx *f = &v->fx[F_SLIPPERY];
    set_dir(v, f);
    LONG c = -from_pct(magnitudePercentage, 0, 100, 0, NOMINAL);
    int ok = 1;
    if (!f->e || f->cond[0].lPositiveCoefficient != c) {
        f->cond[0].lDeadBand = 0;
        f->cond[0].lOffset = 0;
        f->cond[0].lPositiveCoefficient = f->cond[0].lNegativeCoefficient = c;
        f->cond[0].dwPositiveSaturation = f->cond[0].dwNegativeSaturation = NOMINAL;
        cond_y(f);
        ok = fx_apply(v, F_SLIPPERY);
    }
    if (!f->playing) ok = fx_start(v, f);
    RET(ok);
}

API bool8 LogiStopSlipperyRoadEffect(int index) {
    LOCK;
    Dev *v = dev_at(index);
    if (!v || !fx_stop(v, &v->fx[F_SLIPPERY])) RET(0);
    if (v->spring_was) { resume(v, F_SPRING); v->spring_was = 0; }
    if (v->damper_was) { resume(v, F_DAMPER); v->damper_was = 0; }
    RET(1);
}

API bool8 LogiPlaySurfaceEffect(int index, int type, int magnitudePercentage, int period) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v || v->airborne) RET(0);
    const GUID *kind = type == PT_SQUARE ? &GUID_Square : type == PT_TRIANGLE ? &GUID_Triangle : &GUID_Sine;
    Fx *f = &v->fx[F_SURFACE];
    if (f->e && f->kind != kind) fx_release(f);       /* another wave: a new effect */
    f->kind = kind;
    RET(periodic(v, F_SURFACE, magnitudePercentage, period > 0 ? (DWORD)period * 1000 : 0));
}

API bool8 LogiStopSurfaceEffect(int index) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v && fx_stop(v, &v->fx[F_SURFACE]));
}

static const int AIR_PAUSED[] = { F_SPRING, F_CONSTANT, F_DAMPER, F_DIRT, F_BUMPY, F_SLIPPERY, F_SURFACE };

API bool8 LogiPlayCarAirborne(int index) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v) RET(0);
    if (!v->airborne) {
        v->airborne = 1;
        for (unsigned i = 0; i < sizeof AIR_PAUSED / sizeof AIR_PAUSED[0]; i++) {
            int k = AIR_PAUSED[i];
            if (playing(v, k)) {
                fx_stop(v, &v->fx[k]);      /* a slippery road keeps its paused spring/damper */
                v->before_air[k] = 1;
            }
        }
    }
    RET(1);
}

API bool8 LogiStopCarAirborne(int index) {
    LOCK;
    Dev *v = dev_at(index);
    if (!v) RET(0);
    v->airborne = 0;
    for (unsigned i = 0; i < sizeof AIR_PAUSED / sizeof AIR_PAUSED[0]; i++) {
        int k = AIR_PAUSED[i];
        if (v->before_air[k]) resume(v, k);
        v->before_air[k] = 0;
    }
    RET(1);
}

API bool8 LogiPlaySoftstopForce(int index, int usableRangePercentage) {
    LOCK;
    Dev *v = ff_dev(index);
    if (!v) RET(0);
    Fx *f = &v->fx[F_SOFTSTOP];
    LONG band = from_pct(usableRangePercentage, 0, 100, 0, NOMINAL);
    int ok = 1;
    if (!f->e || f->cond[0].lDeadBand != band) {
        f->dir[0] = 1; f->dir[1] = 0;
        f->cond[0].lDeadBand = band;
        f->cond[0].lOffset = 0;
        f->cond[0].lPositiveCoefficient = f->cond[0].lNegativeCoefficient = NOMINAL;
        f->cond[0].dwPositiveSaturation = f->cond[0].dwNegativeSaturation = NOMINAL;
        cond_y(f);
        ok = fx_apply(v, F_SOFTSTOP);
    }
    if (!f->playing) ok = fx_start(v, f);
    RET(ok);
}

API bool8 LogiStopSoftstopForce(int index) {
    LOCK;
    Dev *v = dev_at(index);
    RET(v && fx_stop(v, &v->fx[F_SOFTSTOP]));
}

API bool8 LogiSetPreferredControllerProperties(LogiControllerPropertiesData p) {
    LOCK;
    int vals[10] = { p.forceEnable ? 1 : 0, p.overallGain, p.springGain, p.damperGain,
                     p.defaultSpringEnabled ? 1 : 0, p.defaultSpringGain, p.combinePedals ? 1 : 0,
                     p.wheelRange, p.gameSettingsEnabled ? 1 : 0, p.allowGameSettings ? 1 : 0 };
    memcpy(g_pref, vals, sizeof vals);
    /* to every wheel the game has; without one (Content Manager) as the game's */
    int any = 0;
    for (int i = 0; i < MAXDEV; i++)
        if (g_dev[i].d) { any = 1; g_dev[i].props_sent = send_set(g_dev[i].pid); }
    if (!any) g_pending = !send_set(0);
    g_retry_at = GetTickCount() + 2000;
    RET(1);                                       /* the SDK only notes the wish */
}

API bool8 LogiGetCurrentControllerProperties(int index, LogiControllerPropertiesData *p) {
    LOCK;
    if (!p) RET(0);
    Dev *v = dev_at(index);
    int ok = ask_props(v ? v->pid : 0);
    const int *s = ok ? g_vals : g_pref;          /* without an answer: the defaults, and false */
    ZeroMemory(p, sizeof *p);
    p->forceEnable = s[0] != 0;
    p->overallGain = s[1];
    p->springGain = s[2];
    p->damperGain = s[3];
    p->defaultSpringEnabled = s[4] != 0;
    p->defaultSpringGain = s[5];
    p->combinePedals = s[6] != 0;
    p->wheelRange = s[7];
    p->gameSettingsEnabled = s[8] != 0;
    p->allowGameSettings = s[9] != 0;
    RET(ok);
}

API int LogiGetShifterMode(int index) {
    LOCK;
    Dev *v = dev_at(index);
    int r = 0;
    if (v && v->vid == VID_LOGITECH && v->pid == 0xC299) {        /* G25: ask the driver */
        struct { DWORD size, version; int gated; } in = { 12, 1, -1 };
        int out = -1;
        DIEFFESCAPE e;
        ZeroMemory(&e, sizeof e);
        e.dwSize = sizeof e;
        e.dwCommand = 1;
        e.lpvInBuffer = &in;
        e.cbInBuffer = sizeof in;
        e.lpvOutBuffer = &out;
        e.cbOutBuffer = sizeof out;
        r = (SUCCEEDED(IDirectInputDevice8_Escape(v->d, &e)) && e.cbOutBuffer == sizeof out) ? out : -1;
    } else if (v && v->vid == VID_LOGITECH && v->pid == 0xC29B) {
        r = 1;                                                       /* G27: always gated */
    }
    UNLOCK;
    return r;
}

static DWORD pid_of(LPDIRECTINPUTDEVICE8W d) {
    DIPROPDWORD p;
    p.diph.dwSize = sizeof p;
    p.diph.dwHeaderSize = sizeof p.diph;
    p.diph.dwObj = 0;
    p.diph.dwHow = DIPH_DEVICE;
    p.dwData = 0;
    if (!d || FAILED(IDirectInputDevice8_GetProperty(d, DIPROP_VIDPID, &p.diph))) return 0;
    return HIWORD(p.dwData);
}

static bool8 set_range(DWORD pid, int range) {
    DWORD_PTR res = 0;
    if (range <= 0) return 0;
    return range_msg(L"LGS_Msg_SetOperatingRange", pid, range, &res) && res != 0;
}

static bool8 get_range(DWORD pid, int *range) {
    DWORD_PTR res = 0;
    int r = range_msg(L"LGS_Msg_GetOperatingRange", pid, 0, &res) ? (int)res : 0;
    if (range) *range = r;
    return r > 0;
}

API bool8 LogiSetOperatingRange(int index, int range) {
    LOCK;
    Dev *v = dev_at(index);
    RET(set_range(v ? v->pid : 0, range));
}

API bool8 LogiGetOperatingRange(int index, int *range) {
    LOCK;
    Dev *v = dev_at(index);
    RET(get_range(v ? v->pid : 0, range));
}

API bool8 LogiSetOperatingRangeDInput(LPDIRECTINPUTDEVICE8W device, int range) {
    LOCK;
    RET(set_range(pid_of(device), range));
}

API bool8 LogiGetOperatingRangeDInput(LPDIRECTINPUTDEVICE8W device, int *range) {
    LOCK;
    RET(get_range(pid_of(device), range));
}

API bool8 LogiPlayLeds(int index, float currentRPM, float rpmFirstLedTurnsOn, float rpmRedLine) {
    LOCK;
    Dev *v = dev_at(index);
    if (v) play_leds(v->d, currentRPM, rpmFirstLedTurnsOn, rpmRedLine);
    RET(v != NULL);
}

API bool8 LogiPlayLedsDInput(LPDIRECTINPUTDEVICE8W device, float currentRPM, float rpmFirstLedTurnsOn, float rpmRedLine) {
    if (!device) return 0;
    LOCK;
    play_leds(device, currentRPM, rpmFirstLedTurnsOn, rpmRedLine);
    RET(1);
}

/* the engines' wrapper has these two of its own; here for programs that
   load this DLL directly and look for them */
API bool8 LogiSteeringGetSdkVersion(int *majorNum, int *minorNum, int *buildNum) {
    if (majorNum) *majorNum = 8;
    if (minorNum) *minorNum = 81;
    if (buildNum) *buildNum = 15;
    return 1;
}

API bool8 LogiSteeringInit(bool8 ignoreXInputControllers) {
    return LogiSteeringInitialize(ignoreXInputControllers);
}

/* regsvr32: the SDK's registration, pointing at this file - what LGS's own
   DLL does. LWH normally does this itself (sdk_registration). */
#define SDK_KEY L"SOFTWARE\\Classes\\CLSID\\{63BD165D-1584-4E75-AB56-08330350545F}\\ServerBinary"
API HRESULT DllRegisterServer(void) {
    WCHAR path[MAX_PATH];
    DWORD n = GetModuleFileNameW(self_module(), path, MAX_PATH);
    if (!n || n >= MAX_PATH) return E_FAIL;
    HKEY k;
    if (RegCreateKeyExW(HKEY_LOCAL_MACHINE, SDK_KEY, 0, NULL, 0, KEY_SET_VALUE, NULL, &k, NULL) != ERROR_SUCCESS)
        return E_ACCESSDENIED;
    LONG r = RegSetValueExW(k, NULL, 0, REG_SZ, (const BYTE *)path, (n + 1) * sizeof(WCHAR));
    RegCloseKey(k);
    return r == ERROR_SUCCESS ? S_OK : E_FAIL;
}

API HRESULT DllUnregisterServer(void) {
    WCHAR path[MAX_PATH], cur[MAX_PATH];
    DWORD size = sizeof cur, type = 0;
    if (!GetModuleFileNameW(self_module(), path, MAX_PATH)) return E_FAIL;
    HKEY k;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, SDK_KEY, 0, KEY_READ, &k) != ERROR_SUCCESS) return S_OK;
    LONG r = RegQueryValueExW(k, NULL, NULL, &type, (BYTE *)cur, &size);
    RegCloseKey(k);
    if (r == ERROR_SUCCESS && type == REG_SZ && !lstrcmpiW(cur, path))  /* only when it is this file */
        RegDeleteKeyW(HKEY_LOCAL_MACHINE, SDK_KEY);
    return S_OK;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    (void)h; (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        InitializeCriticalSection(&g_cs);
        DisableThreadLibraryCalls(h);
    }
    return TRUE;
}
