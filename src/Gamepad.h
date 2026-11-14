#pragma once

#include <windows.h>
#include <cstdint>

// Reads an Xbox controller through XInput, resolved at RUNTIME via
// LoadLibrary rather than linked against.
//
// Two reasons, both practical: the XInput import library isn't uniformly
// available across the toolchains this project builds under (MSVC ships
// xinput.lib; MinGW's is spelled differently and isn't always installed),
// and the DLL itself is versioned -- xinput1_4 on Windows 8+, xinput1_3
// alongside the old DirectX redist, xinput9_1_0 as the in-box fallback.
// Binding late lets one binary use whichever is present, and lets a machine
// with none of them run the app fine with keyboard only, instead of failing
// to start with a missing-DLL error.
class Gamepad
{
public:
    // Reported as a bitmask so the caller can test several at once; values
    // deliberately match XINPUT_GAMEPAD_* so no translation table is needed.
    enum Button : uint16_t
    {
        DPadUp    = 0x0001,
        DPadDown  = 0x0002,
        DPadLeft  = 0x0004,
        DPadRight = 0x0008,
        Start     = 0x0010,
        Back      = 0x0020,
        LeftThumb = 0x0040,   // the left stick, clicked
        LeftShoulder  = 0x0100,
        RightShoulder = 0x0200,
        Guide     = 0x0400,   // only ever set via XInputGetStateEx -- see Gamepad.cpp
        A         = 0x1000,
        B         = 0x2000,
        X         = 0x4000,
        Y         = 0x8000,
    };

    Gamepad();
    ~Gamepad();

    bool IsAvailable() const { return m_getState != nullptr; }

    // Polls controller 0 and updates the edge/repeat state. Call once per
    // frame, before the WasPressed queries. `deltaSeconds` drives the
    // held-direction auto-repeat.
    void Update(float deltaSeconds);

    // True on the frame a button went down, and again on each auto-repeat
    // tick while a DIRECTION is held (menus need held-direction scrolling;
    // A/B/X/Y deliberately don't repeat).
    bool WasPressed(Button button) const;

    // The triggers are analogue and have no button bit: true on the frame one
    // is pulled past XInput's own threshold.
    bool WasTriggerPressed(bool right) const { return (m_triggersPressed & (right ? 2 : 1)) != 0; }

    // Controller 0's battery: 0 (empty) to 3 (full), or -1 when there is
    // nothing to report -- no controller, a wired one, or an XInput too old
    // to say (9_1_0 has no battery call).
    int GetBatteryLevel() const;

private:
    // The left stick is folded into the d-pad bits so callers only deal with
    // one set of directions. Deadzone matches XInput's documented default.
    static constexpr int16_t kStickDeadzone = 7849;
    static constexpr float kRepeatDelay = 0.40f;    // before a held direction starts repeating
    static constexpr float kRepeatInterval = 0.12f; // between repeats after that

    using XInputGetStateFn = DWORD(WINAPI*)(DWORD, void*);
    using XInputGetBatteryFn = DWORD(WINAPI*)(DWORD, BYTE, void*);

    HMODULE m_module = nullptr;
    XInputGetStateFn m_getState = nullptr;
    XInputGetBatteryFn m_getBattery = nullptr;

    uint16_t m_buttons = 0;      // current frame, d-pad bits include the stick
    uint16_t m_pressed = 0;      // went down (or repeated) this frame
    uint16_t m_heldDirections = 0;
    uint8_t m_triggers = 0;         // bit 0 left, bit 1 right: past the threshold
    uint8_t m_triggersPressed = 0;  // ...and went past it this frame
    float m_repeatTimer = 0.0f;
    bool m_repeating = false;
};
