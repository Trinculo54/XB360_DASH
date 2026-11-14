#include "Gamepad.h"

namespace
{
    // Newest first: a machine with 1_4 should use it rather than falling back
    // to the in-box 9_1_0, which reports a reduced feature set.
    const wchar_t* kXInputDlls[] = { L"xinput1_4.dll", L"xinput1_3.dll", L"xinput9_1_0.dll" };

    // Mirror of XINPUT_STATE / XINPUT_GAMEPAD. Declared locally so this file
    // doesn't need <xinput.h>, which isn't present in every toolchain's SDK
    // and would defeat the point of binding the DLL late.
    struct GamepadState
    {
        uint16_t wButtons;
        uint8_t bLeftTrigger;
        uint8_t bRightTrigger;
        int16_t sThumbLX;
        int16_t sThumbLY;
        int16_t sThumbRX;
        int16_t sThumbRY;
    };
    struct InputState
    {
        DWORD dwPacketNumber;
        GamepadState Gamepad;
        DWORD reserved;   // XInputGetStateEx's struct is one DWORD longer
    };

    constexpr uint16_t kDirectionMask =
        Gamepad::DPadUp | Gamepad::DPadDown | Gamepad::DPadLeft | Gamepad::DPadRight;
}

Gamepad::Gamepad()
{
    for (const wchar_t* dll : kXInputDlls)
    {
        m_module = LoadLibraryW(dll);
        if (m_module)
        {
            // Ordinal 100 is the unnamed XInputGetStateEx: same call, but it
            // reports the Guide button, which the documented one masks out.
            // 1_4 and 1_3 export it; 9_1_0 doesn't, and falls back.
            FARPROC getState = GetProcAddress(m_module, MAKEINTRESOURCEA(100));
            if (!getState)
            {
                getState = GetProcAddress(m_module, "XInputGetState");
            }
            m_getState = reinterpret_cast<XInputGetStateFn>(reinterpret_cast<void*>(getState));
            if (m_getState)
            {
                m_getBattery = reinterpret_cast<XInputGetBatteryFn>(
                    reinterpret_cast<void*>(GetProcAddress(m_module, "XInputGetBatteryInformation")));
                break;
            }
            FreeLibrary(m_module);
            m_module = nullptr;
        }
    }
}

Gamepad::~Gamepad()
{
    if (m_module)
    {
        FreeLibrary(m_module);
    }
}

void Gamepad::Update(float deltaSeconds)
{
    m_pressed = 0;
    if (!m_getState)
    {
        return;
    }

    InputState state = {};
    if (m_getState(0, &state) != ERROR_SUCCESS)
    {
        // No controller on port 0. Clear held state so unplugging mid-hold
        // doesn't leave a direction stuck repeating forever.
        m_buttons = 0;
        m_heldDirections = 0;
        m_repeating = false;
        return;
    }

    uint16_t buttons = state.Gamepad.wButtons;

    // Fold the left stick into the d-pad bits. Both axes can pass the
    // deadzone at once on a diagonal; that's fine, the caller resolves
    // directions in a fixed order.
    if (state.Gamepad.sThumbLY > kStickDeadzone)  buttons |= DPadUp;
    if (state.Gamepad.sThumbLY < -kStickDeadzone) buttons |= DPadDown;
    if (state.Gamepad.sThumbLX < -kStickDeadzone) buttons |= DPadLeft;
    if (state.Gamepad.sThumbLX > kStickDeadzone)  buttons |= DPadRight;

    m_pressed = static_cast<uint16_t>(buttons & ~m_buttons);
    m_buttons = buttons;

    // Auto-repeat, directions only. A newly pressed direction restarts the
    // timer at the long initial delay; holding past it repeats at the short
    // interval, which is what makes scrolling a long list feel right.
    const uint16_t directions = static_cast<uint16_t>(buttons & kDirectionMask);
    if (directions != m_heldDirections)
    {
        m_heldDirections = directions;
        m_repeatTimer = kRepeatDelay;
        m_repeating = false;
    }
    else if (directions != 0)
    {
        m_repeatTimer -= deltaSeconds;
        if (m_repeatTimer <= 0.0f)
        {
            m_pressed |= directions;
            m_repeatTimer = kRepeatInterval;
            m_repeating = true;
        }
    }
}

int Gamepad::GetBatteryLevel() const
{
    // Mirror of XINPUT_BATTERY_INFORMATION, for the same reason as above.
    struct BatteryInfo
    {
        BYTE type;    // 0 disconnected, 1 wired, 2 alkaline, 3 NiMH, 0xFF unknown
        BYTE level;   // 0 empty, 1 low, 2 medium, 3 full
    };
    BatteryInfo info = {};
    if (!m_getBattery || m_getBattery(0, /*BATTERY_DEVTYPE_GAMEPAD*/ 0, &info) != ERROR_SUCCESS)
    {
        return -1;
    }
    return (info.type == 2 || info.type == 3) ? static_cast<int>(info.level) : -1;
}

bool Gamepad::WasPressed(Button button) const
{
    return (m_pressed & static_cast<uint16_t>(button)) != 0;
}
