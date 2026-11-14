#pragma once

#include <windows.h>
#include <cstdint>
#include <functional>

// Thin Win32 window wrapper. Owns the HWND, pumps messages, and reports
// resize/close/input events back to whoever creates it via callbacks so
// Renderer/App code doesn't need to know anything about Win32 directly.
class Window
{
public:
    struct CreateParams
    {
        const wchar_t* title = L"DashX360";
        uint32_t width = 1280;
        uint32_t height = 720;
    };

    Window() = default;
    ~Window();

    Window(const Window&) = delete;
    Window& operator=(const Window&) = delete;

    bool Create(const CreateParams& params);

    // Pumps all pending Win32 messages. Returns false once a WM_QUIT has
    // been received (i.e. the app should exit its main loop).
    bool PumpMessages();

    HWND GetHandle() const { return m_hwnd; }
    uint32_t GetWidth() const { return m_width; }
    uint32_t GetHeight() const { return m_height; }

    // Called with (width, height) whenever the client area changes size.
    std::function<void(uint32_t, uint32_t)> OnResize;

    // Called with (virtualKeyCode) on key-down.
    std::function<void(uint32_t)> OnKeyDown;

private:
    static LRESULT CALLBACK WndProcThunk(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam);
    LRESULT HandleMessage(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam);

    HWND m_hwnd = nullptr;
    uint32_t m_width = 0;
    uint32_t m_height = 0;
    bool m_quit = false;
};
