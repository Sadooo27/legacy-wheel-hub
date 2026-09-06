#pragma once
// StatusBeacon: writes a small heartbeat JSON to
//   %LOCALAPPDATA%\LegacyWheelHub\status\<pid>_<exe>.json
// about once per second while the game is sending FFB. Legacy Wheel Hub
// polls this folder and shows a live "proxy active" indicator per game.
//
// A file whose "ts" is older than ~5 s should be treated as stale by LWH
// (game closed / crashed). The file is best-effort deleted on DLL unload.

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <string>

class StatusBeacon
{
public:
    static StatusBeacon& Get()
    {
        static StatusBeacon inst;
        return inst;
    }

    // Called once when the proxy DLL has hooked DirectInput.
    void OnProxyLoaded() { WriteNow(); }

    // Called for every remapped constant-force packet.
    void OnConstantForce(LONG in, LONG out)
    {
        lastIn_ = in;
        lastOut_ = out;
        ++cfTotal_;
        ++cfWindow_;
        const ULONGLONG now = GetTickCount64();
        if (now - lastWrite_ >= 1000) {
            cfPerSec_ = (DWORD)(cfWindow_ * 1000 / (now - lastWrite_));
            cfWindow_ = 0;
            WriteNow();
        }
    }

    void SetLutState(bool loaded, size_t points)
    {
        lutLoaded_ = loaded;
        lutPoints_ = points;
    }

    // Best-effort cleanup on DLL_PROCESS_DETACH.
    void Remove()
    {
        if (!path_.empty()) DeleteFileW(path_.c_str());
    }

private:
    StatusBeacon()
    {
        wchar_t base[MAX_PATH]{};
        if (GetEnvironmentVariableW(L"LOCALAPPDATA", base, MAX_PATH) == 0)
            return;

        std::wstring dir = std::wstring(base) + L"\\LegacyWheelHub";
        CreateDirectoryW(dir.c_str(), nullptr);
        dir += L"\\status";
        CreateDirectoryW(dir.c_str(), nullptr);

        wchar_t exePath[MAX_PATH]{};
        GetModuleFileNameW(nullptr, exePath, MAX_PATH);
        const wchar_t* name = wcsrchr(exePath, L'\\');
        exeName_ = name ? name + 1 : exePath;

        wchar_t file[MAX_PATH];
        swprintf_s(file, L"%s\\%lu_%s.json", dir.c_str(),
                   GetCurrentProcessId(), exeName_.c_str());
        path_ = file;
    }

    void WriteNow()
    {
        lastWrite_ = GetTickCount64();
        if (path_.empty()) return;

        // exe name as UTF-8 for the JSON payload
        char exeUtf8[MAX_PATH]{};
        WideCharToMultiByte(CP_UTF8, 0, exeName_.c_str(), -1, exeUtf8,
                            sizeof(exeUtf8), nullptr, nullptr);

        char json[512];
        _snprintf_s(json, _TRUNCATE,
                    "{\"exe\":\"%s\",\"pid\":%lu,"
                    "\"lut_loaded\":%s,\"lut_points\":%zu,"
                    "\"cf_total\":%llu,\"cf_per_s\":%lu,"
                    "\"last_in\":%ld,\"last_out\":%ld,"
                    "\"ts\":%llu}",
                    exeUtf8, GetCurrentProcessId(),
                    lutLoaded_ ? "true" : "false", lutPoints_,
                    cfTotal_, cfPerSec_, lastIn_, lastOut_,
                    (unsigned long long)_time64(nullptr));

        // Write atomically enough: temp file + MoveFileEx replace, so LWH
        // never reads a half-written JSON.
        const std::wstring tmp = path_ + L".tmp";
        FILE* f = nullptr;
        if (_wfopen_s(&f, tmp.c_str(), L"wt") == 0 && f) {
            fputs(json, f);
            fclose(f);
            MoveFileExW(tmp.c_str(), path_.c_str(),
                        MOVEFILE_REPLACE_EXISTING);
        }
    }

    std::wstring path_;
    std::wstring exeName_;
    bool lutLoaded_ = false;
    size_t lutPoints_ = 0;
    unsigned long long cfTotal_ = 0;
    DWORD cfWindow_ = 0;
    DWORD cfPerSec_ = 0;
    LONG lastIn_ = 0;
    LONG lastOut_ = 0;
    ULONGLONG lastWrite_ = 0;
};
