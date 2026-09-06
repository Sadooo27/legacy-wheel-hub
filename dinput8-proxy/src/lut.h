#pragma once
// GlobalLut: loads an Assetto Corsa style .lut file and remaps DirectInput
// force magnitudes through it. Hot-reloads when the file changes (checked
// at most every 2 seconds). Thread-safe.
//
// LUT file format (same as AC / LUT Generator):
//   ; comment
//   0.00|0.00
//   0.05|0.18
//   ...
//   1.00|1.00
// Separators '|', ',', ';' or whitespace are accepted. If values look like
// 0..100 they are normalized to 0..1 automatically.

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cwctype>
#include <locale.h>
#include <mutex>
#include <optional>
#include <string>
#include <vector>
#include "status.h"

// LUT files always use '.' as the decimal separator. We run inside the game's
// process, and many games call setlocale(LC_ALL, "") at startup. On a system
// whose separator is ',' (Turkish, German, ...) the CRT's "%f" would then stop
// understanding "0.5000": sscanf would return 1 instead of 2, EVERY line would
// be rejected, the point list would stay empty and the LUT would silently never
// load - no error, no log, FFB just runs unmapped. Parsing with a fixed "C"
// locale makes the reader immune to whatever the host process does.
static _locale_t LwhCLocale() {
    static _locale_t loc = _create_locale(LC_NUMERIC, "C");
    return loc;
}

// Read a string value from HKCU\Software\LegacyWheelHub. LWH writes:
//   ActiveLut          = legacy global value (kept for old LWH builds during
//                         the update transition; see LwhRegReadForThisGame)
//   Games\<key>\ActiveLut = per-game value, <key> derived from this game's own
//                         exe path (see LwhGameKey) so switching the profile
//                         shown in the LWH window can never affect a DIFFERENT
//                         game's already-running proxy.
//   LogDir             = folder where the proxy should write proxy.log
//
// Returns std::nullopt if the key/value doesn't exist at all - as opposed to
// an empty string, which is a real, deliberate value (LWH writes "" when a
// profile's post-processing is turned OFF). Callers MUST be able to tell
// "off" (present, empty) apart from "never published" (absent) - collapsing
// both into "" was the exact bug that let an already-running game's disabled
// LUT fall through to whatever a DIFFERENT profile shown in LWH published.
inline std::optional<std::wstring> LwhRegRead(const wchar_t* name, const wchar_t* subkey = nullptr)
{
    std::wstring path = L"Software\\LegacyWheelHub";
    if (subkey && *subkey) { path += L"\\"; path += subkey; }
    HKEY k;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, path.c_str(), 0,
                      KEY_READ, &k) != ERROR_SUCCESS)
        return std::nullopt;
    wchar_t buf[1024];
    DWORD sz = sizeof(buf);
    DWORD type = 0;
    LONG r = RegQueryValueExW(k, name, nullptr, &type, (LPBYTE)buf, &sz);
    RegCloseKey(k);
    if (r != ERROR_SUCCESS || (type != REG_SZ && type != REG_EXPAND_SZ))
        return std::nullopt;
    size_t n = sz / sizeof(wchar_t);
    if (n > 0 && buf[n - 1] == L'\0') --n;  // drop trailing NUL
    return std::wstring(buf, n);   // may legitimately be "" - that's fine
}

// A stable, filesystem/registry-safe key for the game this proxy is loaded
// into (e.g. "C__Games_F1_2012_f1_2012.exe" -> "C__Games_F1_2012_f1_2012_exe").
// Built once from GetModuleFileNameW(NULL, ...), so it's this game's own exe
// path, never whatever profile happens to be showing in the LWH window.
inline const std::wstring& LwhGameKey()
{
    static std::wstring key = [] {
        wchar_t exe[MAX_PATH]{};
        GetModuleFileNameW(nullptr, exe, MAX_PATH);
        std::wstring k;
        for (wchar_t* p = exe; *p; ++p) {
            // Lowercased: Windows paths are case-insensitive, but whatever
            // launched this process (Steam, a shortcut, ...) may report a
            // different case than what the user picked in LWH's file dialog.
            // Case-sensitive keys would silently split into two, bringing
            // the exact per-game bug this key exists to fix right back.
            wchar_t c = towlower(*p);
            k += (iswalnum(c)) ? c : L'_';
        }
        return k;
    }();
    return key;
}

// Per-game read with fallback to the old global key. The fallback triggers
// ONLY when the per-game key/value is absent (an old proxy DLL that hasn't
// been reinstalled since the per-game update, so LWH never wrote one for
// this exe). A present-but-empty per-game value means "this profile's
// setting is deliberately off" and is honored as-is - it must NOT fall
// through to whatever a different, currently-selected profile published to
// the legacy global key.
inline std::wstring LwhRegReadForThisGame(const wchar_t* name)
{
    auto per_game = LwhRegRead(name, (L"Games\\" + LwhGameKey()).c_str());
    if (per_game.has_value()) return *per_game;   // even if "" - that's real
    auto global = LwhRegRead(name);
    return global.value_or(std::wstring());
}

// Directory where proxy.log should go: LogDir from registry, else the legacy
// %LOCALAPPDATA%\LegacyWheelHub folder.
inline std::wstring LwhLogDir()
{
    std::wstring d = LwhRegReadForThisGame(L"LogDir");
    if (!d.empty()) return d;
    wchar_t base[MAX_PATH]{};
    if (GetEnvironmentVariableW(L"LOCALAPPDATA", base, MAX_PATH) > 0) {
        std::wstring dir = std::wstring(base) + L"\\LegacyWheelHub";
        CreateDirectoryW(dir.c_str(), nullptr);
        return dir;
    }
    return std::wstring();
}

// High-frequency debug output (per-packet). Only via OutputDebugString and
// only when LWH_PROXY_DEBUG=1 — never touches the disk.
inline void LwhLog(const char* fmt, ...)
{
    static int enabled = -1;
    if (enabled < 0) {
        char buf[8]{};
        enabled = GetEnvironmentVariableA("LWH_PROXY_DEBUG", buf, sizeof(buf)) > 0 ? 1 : 0;
    }
    if (!enabled) return;
    char line[512];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(line, _TRUNCATE, fmt, ap);
    va_end(ap);
    OutputDebugStringA(line);
}

// Low-frequency event logging (load, LUT reload, first FFB packet).
// Always appended to %LOCALAPPDATA%\LegacyWheelHub\proxy.log so it is
// verifiable without any tooling.
inline void LwhLogFile(const char* fmt, ...)
{
    // Log directory is published by LWH (app folder), falling back to
    // %LOCALAPPDATA%. Resolved per-call — this is low-frequency logging.
    std::wstring dir = LwhLogDir();
    if (dir.empty()) return;
    CreateDirectoryW(dir.c_str(), nullptr);
    std::wstring path = dir + L"\\proxy.log";

    char line[512];
    va_list ap;
    va_start(ap, fmt);
    const int n = _vsnprintf_s(line, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n <= 0) return;

    SYSTEMTIME st;
    GetLocalTime(&st);
    wchar_t exePath[MAX_PATH]{};
    GetModuleFileNameW(nullptr, exePath, MAX_PATH);
    const wchar_t* exe = wcsrchr(exePath, L'\\');
    char exeUtf8[MAX_PATH]{};
    WideCharToMultiByte(CP_UTF8, 0, exe ? exe + 1 : exePath, -1, exeUtf8,
                        sizeof(exeUtf8), nullptr, nullptr);

    FILE* f = nullptr;
    if (_wfopen_s(&f, path.c_str(), L"at") == 0 && f) {
        fprintf(f, "[%02u:%02u:%02u] [%s pid=%lu] %s\n", st.wHour, st.wMinute,
                st.wSecond, exeUtf8, GetCurrentProcessId(), line);
        fclose(f);
    }
    OutputDebugStringA(line);
}

class GlobalLut
{
public:
    static GlobalLut& Get()
    {
        static GlobalLut inst;
        return inst;
    }

    // Remap a signed DirectInput magnitude (-10000..10000).
    LONG Remap(LONG mag)
    {
        MaybeReload();
        std::lock_guard<std::mutex> lk(mu_);
        if (!loaded_ || pts_.size() < 2) return mag;  // passthrough

        const float sign = (mag < 0) ? -1.0f : 1.0f;
        float x = std::fabs((float)mag) / 10000.0f;
        if (x > 1.0f) x = 1.0f;
        const float y = Interp(x);
        LONG out = (LONG)std::lround(sign * y * 10000.0f);
        if (out > 10000) out = 10000;
        if (out < -10000) out = -10000;
        return out;
    }

    // Remap an unsigned magnitude (0..10000), e.g. DIPERIODIC::dwMagnitude.
    DWORD RemapU(DWORD mag)
    {
        LONG v = Remap((LONG)std::min<DWORD>(mag, 10000));
        return (DWORD)std::max<LONG>(v, 0);
    }

private:
    GlobalLut()
    {
        MaybeReload(/*force=*/true);
    }

    // Where the active LUT lives right now. Priority:
    //   1. LWH_LUT_PATH env var (manual/testing override)
    //   2. registry HKCU\Software\LegacyWheelHub\Games\<this exe>\ActiveLut
    //      (set per-game by LWH; falls back to the old global ActiveLut for
    //      a proxy DLL that hasn't been reinstalled since the update yet)
    // Reading a value scoped to THIS game's own exe means switching which
    // profile is shown in the LWH window can never change what a different,
    // already-running game applies — each game only ever sees its own key.
    std::wstring ResolvePath()
    {
        wchar_t buf[MAX_PATH]{};
        if (GetEnvironmentVariableW(L"LWH_LUT_PATH", buf, MAX_PATH) > 0)
            return std::wstring(buf);
        return LwhRegReadForThisGame(L"ActiveLut");
    }

    void MaybeReload(bool force = false)
    {
        const ULONGLONG now = GetTickCount64();
        if (!force && now - lastCheck_ < 2000) return;
        lastCheck_ = now;

        // Re-resolve every check so switching presets in LWH (which rewrites
        // ActiveLut) takes effect within ~2 s without restarting the game.
        std::wstring want = ResolvePath();
        if (want != path_) {
            path_ = want;
            force = true;                 // path changed -> must reload
            ZeroMemory(&lastWrite_, sizeof(lastWrite_));
        }

        if (path_.empty()) {              // no active LUT -> passthrough
            std::lock_guard<std::mutex> lk(mu_);
            if (loaded_) LwhLogFile("No active LUT -> passthrough mode");
            loaded_ = false;
            StatusBeacon::Get().SetLutState(false, 0);
            return;
        }

        WIN32_FILE_ATTRIBUTE_DATA fad{};
        if (!GetFileAttributesExW(path_.c_str(), GetFileExInfoStandard, &fad)) {
            std::lock_guard<std::mutex> lk(mu_);
            if (loaded_)
                LwhLogFile("LUT file missing -> passthrough mode");
            loaded_ = false;  // file removed -> passthrough (LUT disabled)
            StatusBeacon::Get().SetLutState(false, 0);
            return;
        }
        if (!force &&
            fad.ftLastWriteTime.dwLowDateTime == lastWrite_.dwLowDateTime &&
            fad.ftLastWriteTime.dwHighDateTime == lastWrite_.dwHighDateTime)
            return;

        LoadFromFile();
        lastWrite_ = fad.ftLastWriteTime;
    }

    void LoadFromFile()
    {
        FILE* f = nullptr;
        if (_wfopen_s(&f, path_.c_str(), L"rt") != 0 || !f) return;

        std::vector<std::pair<float, float>> pts;
        char line[256];
        float maxVal = 0.0f;
        while (fgets(line, sizeof(line), f)) {
            if (line[0] == ';' || line[0] == '#' || line[0] == '\r' || line[0] == '\n')
                continue;
            for (char* p = line; *p; ++p)
                if (*p == '|' || *p == ',' || *p == ';' || *p == '\t') *p = ' ';
            float x = 0, y = 0;
            if (_sscanf_s_l(line, "%f %f", LwhCLocale(), &x, &y) == 2) {
                pts.emplace_back(x, y);
                maxVal = std::max({maxVal, x, y});
            }
        }
        fclose(f);

        if (pts.size() < 2) return;
        if (maxVal > 1.5f) {  // looks like 0..100 scale
            for (auto& p : pts) { p.first /= 100.0f; p.second /= 100.0f; }
        }
        std::sort(pts.begin(), pts.end(),
                  [](auto& a, auto& b) { return a.first < b.first; });

        std::lock_guard<std::mutex> lk(mu_);
        pts_ = std::move(pts);
        loaded_ = true;
        StatusBeacon::Get().SetLutState(true, pts_.size());
        LwhLogFile("LUT loaded: %zu points (in 0.50 -> out %.3f)",
                   pts_.size(), Interp(0.5f));
    }

    // Linear interpolation, clamped at both ends. mu_ must be held.
    float Interp(float x) const
    {
        if (x <= pts_.front().first) {
            // Below first point: interpolate from (0,0) if the curve
            // does not start at 0 input.
            if (pts_.front().first > 0.0f)
                return pts_.front().second * (x / pts_.front().first);
            return pts_.front().second;
        }
        if (x >= pts_.back().first) return pts_.back().second;
        for (size_t i = 1; i < pts_.size(); ++i) {
            if (x <= pts_[i].first) {
                const float x0 = pts_[i - 1].first, y0 = pts_[i - 1].second;
                const float x1 = pts_[i].first,     y1 = pts_[i].second;
                const float t = (x1 - x0) > 1e-9f ? (x - x0) / (x1 - x0) : 0.0f;
                return y0 + t * (y1 - y0);
            }
        }
        return pts_.back().second;
    }

    std::mutex mu_;
    std::vector<std::pair<float, float>> pts_;
    bool loaded_ = false;
    std::wstring path_;
    FILETIME lastWrite_{};
    ULONGLONG lastCheck_ = 0;
};
