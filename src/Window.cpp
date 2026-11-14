#include "Window.h"

namespace
{
    constexpr wchar_t kWindowClassName[] = L"DashX360NativeWindowClass";
}

Window::~Window()
{
    if (m_hwnd)
    {
        // Drop the GWLP_USERDATA pointer so a stray message during
        // teardown can't dereference a dangling `this`.
        SetWindowLongPtr(m_hwnd, GWLP_USERDATA, 0);
        DestroyWindow(m_hwnd);
        m_hwnd = nullptr;
    }
}

bool Window::Create(const CreateParams& params)
{
    HINSTANCE hInstance = GetModuleHandle(nullptr);

    WNDCLASSEX wc = {};
    wc.cbSize = sizeof(WNDCLASSEX);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = &Window::WndProcThunk;
    wc.hInstance = hInstance;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hbrBackground = nullptr; // D3D owns painting the client area.
    wc.lpszClassName = kWindowClassName;

    // RegisterClassEx fails harmlessly if we've already registered this
    // class (e.g. a second Window instance in the same process); only
    // bail out on an error other than "already exists".
    if (!RegisterClassEx(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS)
    {
        return false;
    }

    RECT rect = { 0, 0, static_cast<LONG>(params.width), static_cast<LONG>(params.height) };
    DWORD style = WS_OVERLAPPEDWINDOW;
    AdjustWindowRect(&rect, style, FALSE);

    m_hwnd = CreateWindowEx(
        0,
        kWindowClassName,
        params.title,
        style,
        CW_USEDEFAULT, CW_USEDEFAULT,
        rect.right - rect.left, rect.bottom - rect.top,
        nullptr, nullptr, hInstance, this);

    if (!m_hwnd)
    {
        return false;
    }

    m_width = params.width;
    m_height = params.height;

    ShowWindow(m_hwnd, SW_SHOW);
    UpdateWindow(m_hwnd);
    return true;
}

bool Window::PumpMessages()
{
    MSG msg;
    while (PeekMessage(&msg, nullptr, 0, 0, PM_REMOVE))
    {
        if (msg.message == WM_QUIT)
        {
            m_quit = true;
        }
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }
    return !m_quit;
}

LRESULT CALLBACK Window::WndProcThunk(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam)
{
    Window* self = nullptr;

    if (msg == WM_NCCREATE)
    {
        auto* createStruct = reinterpret_cast<CREATESTRUCT*>(lParam);
        self = reinterpret_cast<Window*>(createStruct->lpCreateParams);
        SetWindowLongPtr(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
    }
    else
    {
        self = reinterpret_cast<Window*>(GetWindowLongPtr(hwnd, GWLP_USERDATA));
    }

    if (self)
    {
        return self->HandleMessage(hwnd, msg, wParam, lParam);
    }
    return DefWindowProc(hwnd, msg, wParam, lParam);
}

LRESULT Window::HandleMessage(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam)
{
    switch (msg)
    {
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;

    case WM_SIZE:
    {
        uint32_t width = LOWORD(lParam);
        uint32_t height = HIWORD(lParam);
        // Ignore the spurious 0x0 sent on minimize; nothing downstream
        // (the swapchain resize logic) wants a zero-sized buffer.
        if (width > 0 && height > 0 && (width != m_width || height != m_height))
        {
            m_width = width;
            m_height = height;
            if (OnResize)
            {
                OnResize(width, height);
            }
        }
        return 0;
    }

    case WM_KEYDOWN:
        if (OnKeyDown)
        {
            OnKeyDown(static_cast<uint32_t>(wParam));
        }
        if (wParam == VK_ESCAPE)
        {
            PostQuitMessage(0);
        }
        return 0;

    default:
        return DefWindowProc(hwnd, msg, wParam, lParam);
    }
}
