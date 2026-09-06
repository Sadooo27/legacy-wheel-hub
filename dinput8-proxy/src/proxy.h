#pragma once
// COM wrappers around IDirectInput8(A/W) -> IDirectInputDevice8(A/W)
// -> IDirectInputEffect. Everything is forwarded 1:1 to the real
// implementation; only the type-specific parameters of ConstantForce
// (and optionally Periodic) effects are remapped through GlobalLut.

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#ifndef DIRECTINPUT_VERSION
#define DIRECTINPUT_VERSION 0x0800
#endif
#include <windows.h>
#include <dinput.h>
#include "lut.h"

// ---------------------------------------------------------------------------
// Effect parameter patching
// ---------------------------------------------------------------------------

// Returns the DIEFFECT pointer to forward to the real API. If patching was
// needed, a stack-provided copy (effCopy + cf/per) is filled and returned so
// the game's own memory is never modified.
inline const DIEFFECT* PatchEffectParams(REFGUID guid,
                                         const DIEFFECT* peff,
                                         DWORD dwFlags,
                                         DIEFFECT* effCopy,
                                         DICONSTANTFORCE* cf,
                                         DIPERIODIC* per)
{
    if (!peff || !(dwFlags & DIEP_TYPESPECIFICPARAMS) ||
        !peff->lpvTypeSpecificParams)
        return peff;

    const bool isConst = IsEqualGUID(guid, GUID_ConstantForce);
    const bool isPeriodic = IsEqualGUID(guid, GUID_Sine) ||
                            IsEqualGUID(guid, GUID_Square) ||
                            IsEqualGUID(guid, GUID_Triangle) ||
                            IsEqualGUID(guid, GUID_SawtoothUp) ||
                            IsEqualGUID(guid, GUID_SawtoothDown);
    if (!isConst && !isPeriodic) return peff;

    // Shallow-copy the DIEFFECT header (dwSize may be a DX5-sized struct;
    // copy at most what we know about, preserve original dwSize field).
    const DWORD copyLen =
        peff->dwSize < sizeof(DIEFFECT) ? peff->dwSize : (DWORD)sizeof(DIEFFECT);
    ZeroMemory(effCopy, sizeof(DIEFFECT));
    memcpy(effCopy, peff, copyLen);

    if (isConst && peff->cbTypeSpecificParams >= sizeof(DICONSTANTFORCE)) {
        *cf = *(const DICONSTANTFORCE*)peff->lpvTypeSpecificParams;
        const LONG before = cf->lMagnitude;
        cf->lMagnitude = GlobalLut::Get().Remap(cf->lMagnitude);
        effCopy->lpvTypeSpecificParams = cf;
        effCopy->cbTypeSpecificParams = sizeof(DICONSTANTFORCE);

        static LONG firstLogged = 0;
        if (InterlockedCompareExchange(&firstLogged, 1, 0) == 0)
            LwhLogFile("First constant-force packet: %ld -> %ld "
                       "(FFB pipeline ACTIVE)", before, cf->lMagnitude);
        StatusBeacon::Get().OnConstantForce(before, cf->lMagnitude);
        LwhLog("[LWH] CF %ld -> %ld\n", before, cf->lMagnitude);
        return effCopy;
    }
    if (isPeriodic && peff->cbTypeSpecificParams >= sizeof(DIPERIODIC)) {
        *per = *(const DIPERIODIC*)peff->lpvTypeSpecificParams;
        per->dwMagnitude = GlobalLut::Get().RemapU(per->dwMagnitude);
        effCopy->lpvTypeSpecificParams = per;
        effCopy->cbTypeSpecificParams = sizeof(DIPERIODIC);
        return effCopy;
    }
    return peff;
}

// ---------------------------------------------------------------------------
// IDirectInputEffect wrapper
// ---------------------------------------------------------------------------

class EffectProxy final : public IDirectInputEffect
{
public:
    EffectProxy(IDirectInputEffect* real, REFGUID guid)
        : real_(real), guid_(guid) {}

    // IUnknown -----------------------------------------------------------
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override
    {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_IDirectInputEffect) {
            AddRef(); *ppv = this; return S_OK;
        }
        return real_->QueryInterface(riid, ppv);
    }
    ULONG STDMETHODCALLTYPE AddRef() override
    { return InterlockedIncrement(&ref_); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        const ULONG c = InterlockedDecrement(&ref_);
        if (!c) { real_->Release(); delete this; }
        return c;
    }

    // IDirectInputEffect --------------------------------------------------
    HRESULT STDMETHODCALLTYPE Initialize(HINSTANCE h, DWORD v, REFGUID g) override
    { return real_->Initialize(h, v, g); }
    HRESULT STDMETHODCALLTYPE GetEffectGuid(LPGUID p) override
    { return real_->GetEffectGuid(p); }
    HRESULT STDMETHODCALLTYPE GetParameters(LPDIEFFECT p, DWORD f) override
    { return real_->GetParameters(p, f); }

    HRESULT STDMETHODCALLTYPE SetParameters(LPCDIEFFECT peff, DWORD flags) override
    {
        DIEFFECT effCopy; DICONSTANTFORCE cf; DIPERIODIC per;
        const DIEFFECT* use =
            PatchEffectParams(guid_, peff, flags, &effCopy, &cf, &per);
        return real_->SetParameters(use, flags);
    }

    HRESULT STDMETHODCALLTYPE Start(DWORD iters, DWORD flags) override
    { return real_->Start(iters, flags); }
    HRESULT STDMETHODCALLTYPE Stop() override { return real_->Stop(); }
    HRESULT STDMETHODCALLTYPE GetEffectStatus(LPDWORD p) override
    { return real_->GetEffectStatus(p); }
    HRESULT STDMETHODCALLTYPE Download() override { return real_->Download(); }
    HRESULT STDMETHODCALLTYPE Unload() override { return real_->Unload(); }
    HRESULT STDMETHODCALLTYPE Escape(LPDIEFFESCAPE p) override
    { return real_->Escape(p); }

private:
    IDirectInputEffect* real_;
    GUID guid_;
    volatile LONG ref_ = 1;
};

// ---------------------------------------------------------------------------
// A/W charset traits
// ---------------------------------------------------------------------------

template <bool Wide> struct DITraits;

template <> struct DITraits<false>
{
    using IDI     = IDirectInput8A;
    using IDev    = IDirectInputDevice8A;
    using Str     = LPCSTR;
    using EnumDevCB = LPDIENUMDEVICESCALLBACKA;
    using ActFmt  = LPDIACTIONFORMATA;
    using SemCB   = LPDIENUMDEVICESBYSEMANTICSCBA;
    using CfgPrm  = LPDICONFIGUREDEVICESPARAMSA;
    using ObjCB   = LPDIENUMDEVICEOBJECTSCALLBACKA;
    using ObjInst = LPDIDEVICEOBJECTINSTANCEA;
    using DevInst = LPDIDEVICEINSTANCEA;
    using EffCB   = LPDIENUMEFFECTSCALLBACKA;
    using EffInfo = LPDIEFFECTINFOA;
    using ImgHdr  = LPDIDEVICEIMAGEINFOHEADERA;
    static REFIID DiIID()  { return IID_IDirectInput8A; }
    static REFIID DevIID() { return IID_IDirectInputDevice8A; }
};

template <> struct DITraits<true>
{
    using IDI     = IDirectInput8W;
    using IDev    = IDirectInputDevice8W;
    using Str     = LPCWSTR;
    using EnumDevCB = LPDIENUMDEVICESCALLBACKW;
    using ActFmt  = LPDIACTIONFORMATW;
    using SemCB   = LPDIENUMDEVICESBYSEMANTICSCBW;
    using CfgPrm  = LPDICONFIGUREDEVICESPARAMSW;
    using ObjCB   = LPDIENUMDEVICEOBJECTSCALLBACKW;
    using ObjInst = LPDIDEVICEOBJECTINSTANCEW;
    using DevInst = LPDIDEVICEINSTANCEW;
    using EffCB   = LPDIENUMEFFECTSCALLBACKW;
    using EffInfo = LPDIEFFECTINFOW;
    using ImgHdr  = LPDIDEVICEIMAGEINFOHEADERW;
    static REFIID DiIID()  { return IID_IDirectInput8W; }
    static REFIID DevIID() { return IID_IDirectInputDevice8W; }
};

// ---------------------------------------------------------------------------
// IDirectInputDevice8 wrapper
// ---------------------------------------------------------------------------

template <bool W>
class DevProxy final : public DITraits<W>::IDev
{
    using T = DITraits<W>;

public:
    explicit DevProxy(typename T::IDev* real) : real_(real) {}

    // IUnknown -----------------------------------------------------------
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override
    {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == T::DevIID()) {
            AddRef(); *ppv = this; return S_OK;
        }
        return real_->QueryInterface(riid, ppv);
    }
    ULONG STDMETHODCALLTYPE AddRef() override
    { return InterlockedIncrement(&ref_); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        const ULONG c = InterlockedDecrement(&ref_);
        if (!c) { real_->Release(); delete this; }
        return c;
    }

    // The interesting one --------------------------------------------------
    HRESULT STDMETHODCALLTYPE CreateEffect(REFGUID rguid, LPCDIEFFECT peff,
                                           LPDIRECTINPUTEFFECT* ppEff,
                                           LPUNKNOWN punk) override
    {
        DIEFFECT effCopy; DICONSTANTFORCE cf; DIPERIODIC per;
        const DIEFFECT* use = PatchEffectParams(
            rguid, peff, DIEP_TYPESPECIFICPARAMS, &effCopy, &cf, &per);

        IDirectInputEffect* real = nullptr;
        const HRESULT hr = real_->CreateEffect(rguid, use, &real, punk);
        if (ppEff)
            *ppEff = (SUCCEEDED(hr) && real) ? new EffectProxy(real, rguid)
                                             : real;
        return hr;
    }

    // Plain forwards -------------------------------------------------------
#define LWH_FWD(name, params, args) \
    HRESULT STDMETHODCALLTYPE name params override { return real_->name args; }

    LWH_FWD(GetCapabilities, (LPDIDEVCAPS a), (a))
    LWH_FWD(EnumObjects, (typename T::ObjCB a, LPVOID b, DWORD c), (a, b, c))
    LWH_FWD(GetProperty, (REFGUID a, LPDIPROPHEADER b), (a, b))
    LWH_FWD(SetProperty, (REFGUID a, LPCDIPROPHEADER b), (a, b))
    LWH_FWD(Acquire, (), ())
    LWH_FWD(Unacquire, (), ())
    LWH_FWD(GetDeviceState, (DWORD a, LPVOID b), (a, b))
    LWH_FWD(GetDeviceData, (DWORD a, LPDIDEVICEOBJECTDATA b, LPDWORD c, DWORD d), (a, b, c, d))
    LWH_FWD(SetDataFormat, (LPCDIDATAFORMAT a), (a))
    LWH_FWD(SetEventNotification, (HANDLE a), (a))
    LWH_FWD(SetCooperativeLevel, (HWND a, DWORD b), (a, b))
    LWH_FWD(GetObjectInfo, (typename T::ObjInst a, DWORD b, DWORD c), (a, b, c))
    LWH_FWD(GetDeviceInfo, (typename T::DevInst a), (a))
    LWH_FWD(RunControlPanel, (HWND a, DWORD b), (a, b))
    LWH_FWD(Initialize, (HINSTANCE a, DWORD b, REFGUID c), (a, b, c))
    LWH_FWD(EnumEffects, (typename T::EffCB a, LPVOID b, DWORD c), (a, b, c))
    LWH_FWD(GetEffectInfo, (typename T::EffInfo a, REFGUID b), (a, b))
    LWH_FWD(GetForceFeedbackState, (LPDWORD a), (a))
    LWH_FWD(SendForceFeedbackCommand, (DWORD a), (a))
    LWH_FWD(EnumCreatedEffectObjects, (LPDIENUMCREATEDEFFECTOBJECTSCALLBACK a, LPVOID b, DWORD c), (a, b, c))
    LWH_FWD(Escape, (LPDIEFFESCAPE a), (a))
    LWH_FWD(Poll, (), ())
    LWH_FWD(SendDeviceData, (DWORD a, LPCDIDEVICEOBJECTDATA b, LPDWORD c, DWORD d), (a, b, c, d))
    LWH_FWD(EnumEffectsInFile, (typename T::Str a, LPDIENUMEFFECTSINFILECALLBACK b, LPVOID c, DWORD d), (a, b, c, d))
    LWH_FWD(WriteEffectToFile, (typename T::Str a, DWORD b, LPDIFILEEFFECT c, DWORD d), (a, b, c, d))
    LWH_FWD(BuildActionMap, (typename T::ActFmt a, typename T::Str b, DWORD c), (a, b, c))
    LWH_FWD(SetActionMap, (typename T::ActFmt a, typename T::Str b, DWORD c), (a, b, c))
    LWH_FWD(GetImageInfo, (typename T::ImgHdr a), (a))
#undef LWH_FWD

private:
    typename T::IDev* real_;
    volatile LONG ref_ = 1;
};

// ---------------------------------------------------------------------------
// IDirectInput8 wrapper
// ---------------------------------------------------------------------------

template <bool W>
class DI8Proxy final : public DITraits<W>::IDI
{
    using T = DITraits<W>;

public:
    explicit DI8Proxy(typename T::IDI* real) : real_(real) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override
    {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == T::DiIID()) {
            AddRef(); *ppv = this; return S_OK;
        }
        return real_->QueryInterface(riid, ppv);
    }
    ULONG STDMETHODCALLTYPE AddRef() override
    { return InterlockedIncrement(&ref_); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        const ULONG c = InterlockedDecrement(&ref_);
        if (!c) { real_->Release(); delete this; }
        return c;
    }

    HRESULT STDMETHODCALLTYPE CreateDevice(REFGUID rguid,
                                           typename T::IDev** ppDev,
                                           LPUNKNOWN punk) override
    {
        typename T::IDev* dev = nullptr;
        const HRESULT hr = real_->CreateDevice(rguid, &dev, punk);
        if (ppDev)
            *ppDev = (SUCCEEDED(hr) && dev) ? new DevProxy<W>(dev) : dev;
        return hr;
    }

#define LWH_FWD(name, params, args) \
    HRESULT STDMETHODCALLTYPE name params override { return real_->name args; }

    LWH_FWD(EnumDevices, (DWORD a, typename T::EnumDevCB b, LPVOID c, DWORD d), (a, b, c, d))
    LWH_FWD(GetDeviceStatus, (REFGUID a), (a))
    LWH_FWD(RunControlPanel, (HWND a, DWORD b), (a, b))
    LWH_FWD(Initialize, (HINSTANCE a, DWORD b), (a, b))
    LWH_FWD(FindDevice, (REFGUID a, typename T::Str b, LPGUID c), (a, b, c))
    LWH_FWD(EnumDevicesBySemantics, (typename T::Str a, typename T::ActFmt b, typename T::SemCB c, LPVOID d, DWORD e), (a, b, c, d, e))
    LWH_FWD(ConfigureDevices, (LPDICONFIGUREDEVICESCALLBACK a, typename T::CfgPrm b, DWORD c, LPVOID d), (a, b, c, d))
#undef LWH_FWD

private:
    typename T::IDI* real_;
    volatile LONG ref_ = 1;
};
