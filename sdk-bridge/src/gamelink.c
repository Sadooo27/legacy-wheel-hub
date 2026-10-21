/*
 * LWHGameLink.exe - stands where Logitech Gaming Software's own program was
 * registered (HKLM\Software\Logitech\Gaming Software, value "Profiler").
 *
 * Logitech's Steering Wheel SDK reads that value for two things:
 *   - the file's version: below 5.03 (or no file at all, as without LGS) the
 *     SDK 8.x in a game sends no settings at all, and SDK 1.x refuses them;
 *   - starting it ("/noui /force") when the LGS window is not there yet, so
 *     the game's wishes have someone to go to.
 * This program carries version 5.10 (the LGS whose behaviour LWH follows)
 * and, when started, starts Legacy Wheel Hub quietly in the tray if it is
 * not already running. It never brings a window up over the game.
 * Build: build.sh (MinGW).
 */
#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#include <windows.h>

int WINAPI wWinMain(HINSTANCE h, HINSTANCE p, LPWSTR cmd, int show) {
    (void)h; (void)p; (void)cmd; (void)show;
    HANDLE m = OpenMutexW(SYNCHRONIZE, FALSE, L"LegacyWheelHub_SingleInstance");
    if (m) { CloseHandle(m); return 0; }                  /* already running */
    if (FindWindowW(L"Logitech Wingman Internal Message Router", NULL)) return 0;
    /* where LWH is: it notes its own command at every start */
    WCHAR line[2048];
    DWORD size = sizeof line - 64 * sizeof(WCHAR), type = 0;
    HKEY k;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"Software\\LegacyWheelHub", 0, KEY_READ, &k) != ERROR_SUCCESS) return 1;
    LONG r = RegQueryValueExW(k, L"AppCommand", NULL, &type, (BYTE *)line, &size);
    RegCloseKey(k);
    if (r != ERROR_SUCCESS || type != REG_SZ || size < 4) return 1;
    line[size / sizeof(WCHAR)] = 0;
    lstrcatW(line, L" --tray --game");
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_SHOWMINNOACTIVE;
    if (!CreateProcessW(NULL, line, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) return 1;
    CloseHandle(pi.hThread);
    /* the game goes on at once; LWH's window for it appears within seconds */
    WaitForInputIdle(pi.hProcess, 3000);
    CloseHandle(pi.hProcess);
    return 0;
}
