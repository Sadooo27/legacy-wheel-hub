// Legacy Wheel Hub — dinput8.dll proxy
// Drop next to a game exe. Forwards everything to the real
// %SystemRoot%\System32\dinput8.dll, wrapping IDirectInput8 so that
// ConstantForce (and periodic) FFB magnitudes pass through the global LUT.

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#define DIRECTINPUT_VERSION 0x0800
#include <windows.h>
#include <dinput.h>
#include "proxy.h"

namespace {

HMODULE g_real = nullptr;
HINSTANCE g_self = nullptr;         // our own module handle
wchar_t   g_realCopyPath[MAX_PATH]{}; // temp copy we made (to clean up)

FARPROC g_pDirectInput8Create = nullptr;
FARPROC g_pDllCanUnloadNow    = nullptr;
FARPROC g_pDllGetClassObject  = nullptr;
FARPROC g_pDllRegisterServer  = nullptr;
FARPROC g_pDllUnregisterServer = nullptr;
FARPROC g_pGetdfDIJoystick    = nullptr;

void LoadReal()
{
    if (g_real) return;

    wchar_t sysPath[MAX_PATH];
    GetSystemDirectoryW(sysPath, MAX_PATH);
    wcscat_s(sysPath, L"\\dinput8.dll");

    // IMPORTANT: loading the real DLL by its full System32 path is NOT safe
    // on its own. Because our proxy is *also* named dinput8.dll, the Windows
    // loader keys already-loaded modules by base name and may hand us back
    // OUR OWN module instead of the System32 file. GetProcAddress would then
    // return our own exports, so every forwarded call re-enters itself
    // -> infinite recursion -> EXCEPTION_STACK_OVERFLOW. This is load-order
    // dependent, which is why some games (LFS, CCD 2.0) worked while JDM
    // (UE5, different load order) crashed.
    //
    // Robust fix: copy the real DLL to a uniquely-named temp file and load
    // THAT. A unique base name cannot collide with ours.
    wchar_t tmpDir[MAX_PATH];
    if (GetTempPathW(MAX_PATH, tmpDir) > 0) {
        swprintf_s(g_realCopyPath, L"%slwh_dinput8_real_%lu.dll", tmpDir,
                   GetCurrentProcessId());
        if (CopyFileW(sysPath, g_realCopyPath, FALSE))
            g_real = LoadLibraryW(g_realCopyPath);
        else
            g_realCopyPath[0] = L'\0';
    }

    // Fallback: direct path (fine when no base-name collision occurs).
    if (!g_real) {
        g_realCopyPath[0] = L'\0';
        g_real = LoadLibraryW(sysPath);
    }

    // Belt-and-suspenders: never let g_real be ourselves.
    if (g_real == (HMODULE)g_self) g_real = nullptr;
    if (!g_real) return;

    g_pDirectInput8Create  = GetProcAddress(g_real, "DirectInput8Create");
    g_pDllCanUnloadNow     = GetProcAddress(g_real, "DllCanUnloadNow");
    g_pDllGetClassObject   = GetProcAddress(g_real, "DllGetClassObject");
    g_pDllRegisterServer   = GetProcAddress(g_real, "DllRegisterServer");
    g_pDllUnregisterServer = GetProcAddress(g_real, "DllUnregisterServer");
    g_pGetdfDIJoystick     = GetProcAddress(g_real, "GetdfDIJoystick");
    LwhLog("[LWH] proxy dinput8 loaded, real=%p\n", g_real);
}

}  // namespace

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = hinst;
        DisableThreadLibraryCalls(hinst);
    } else if (reason == DLL_PROCESS_DETACH) {
        StatusBeacon::Get().Remove();
        if (g_real) FreeLibrary(g_real);
        if (g_realCopyPath[0]) DeleteFileW(g_realCopyPath);
    }
    return TRUE;
}

namespace {

// Optional visual confirmation: set LWH_PROXY_MSGBOX=1 and a message box
// pops up (from a worker thread, so game init is not blocked) the moment
// the game initializes DirectInput through our proxy.
DWORD WINAPI MsgBoxThread(LPVOID)
{
    MessageBoxW(nullptr,
                L"LWH dinput8 proxy y\u00fcklendi.\n"
                L"Detay: %LOCALAPPDATA%\\LegacyWheelHub\\proxy.log",
                L"Legacy Wheel Hub", MB_OK | MB_ICONINFORMATION | MB_TOPMOST);
    return 0;
}

void OnFirstCreate()
{
    static LONG once = 0;
    if (InterlockedCompareExchange(&once, 1, 0) != 0) return;

    LwhLogFile("Proxy hooked DirectInput8Create (game initialized DI8)");
    StatusBeacon::Get().OnProxyLoaded();
    GlobalLut::Get().Remap(5000);  // force initial LUT load + log line

    char buf[8]{};
    if (GetEnvironmentVariableA("LWH_PROXY_MSGBOX", buf, sizeof(buf)) > 0 &&
        buf[0] == '1') {
        HANDLE h = CreateThread(nullptr, 0, MsgBoxThread, nullptr, 0, nullptr);
        if (h) CloseHandle(h);
    }
}

}  // namespace

extern "C" HRESULT WINAPI ProxyDirectInput8Create(HINSTANCE hinst,
                                                  DWORD dwVersion,
                                                  REFIID riid, LPVOID* ppvOut,
                                                  LPUNKNOWN punkOuter)
{
    LoadReal();
    OnFirstCreate();
    using Fn = HRESULT(WINAPI*)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);
    const auto fn = (Fn)g_pDirectInput8Create;
    // Guard: if resolution failed or (defensively) points back at our own
    // export, do not call it — returning an error is infinitely better than
    // recursing into a stack overflow.
    if (!fn || (void*)fn == (void*)&ProxyDirectInput8Create) return E_FAIL;

    const HRESULT hr = fn(hinst, dwVersion, riid, ppvOut, punkOuter);
    if (FAILED(hr) || !ppvOut || !*ppvOut) return hr;

    if (IsEqualIID(riid, IID_IDirectInput8A))
        *ppvOut = new DI8Proxy<false>((IDirectInput8A*)*ppvOut);
    else if (IsEqualIID(riid, IID_IDirectInput8W))
        *ppvOut = new DI8Proxy<true>((IDirectInput8W*)*ppvOut);
    // Other riids (shouldn't happen for dinput8): pass through unwrapped.
    return hr;
}

extern "C" HRESULT WINAPI ProxyDllCanUnloadNow()
{
    LoadReal();
    using Fn = HRESULT(WINAPI*)();
    const auto fn = (Fn)g_pDllCanUnloadNow;
    return fn ? fn() : S_FALSE;
}

extern "C" HRESULT WINAPI ProxyDllGetClassObject(REFCLSID rclsid, REFIID riid,
                                                 LPVOID* ppv)
{
    // NOTE: objects obtained via CoCreateInstance bypass our wrapper.
    // Sim games use DirectInput8Create, so this is a plain forward.
    LoadReal();
    using Fn = HRESULT(WINAPI*)(REFCLSID, REFIID, LPVOID*);
    const auto fn = (Fn)g_pDllGetClassObject;
    return fn ? fn(rclsid, riid, ppv) : CLASS_E_CLASSNOTAVAILABLE;
}

extern "C" HRESULT WINAPI ProxyDllRegisterServer()
{
    LoadReal();
    using Fn = HRESULT(WINAPI*)();
    const auto fn = (Fn)g_pDllRegisterServer;
    return fn ? fn() : E_FAIL;
}

extern "C" HRESULT WINAPI ProxyDllUnregisterServer()
{
    LoadReal();
    using Fn = HRESULT(WINAPI*)();
    const auto fn = (Fn)g_pDllUnregisterServer;
    return fn ? fn() : E_FAIL;
}

extern "C" LPCDIDATAFORMAT WINAPI ProxyGetdfDIJoystick()
{
    LoadReal();
    using Fn = LPCDIDATAFORMAT(WINAPI*)();
    const auto fn = (Fn)g_pGetdfDIJoystick;
    return fn ? fn() : nullptr;
}
