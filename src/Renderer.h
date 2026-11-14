#pragma once

#include <windows.h>
#include <d3d11.h>
#include <wrl/client.h>
#include <cstdint>
#include <array>

// Owns the D3D11 device/context/swapchain and the single backbuffer
// render-target view. This is intentionally minimal for now: it can
// clear and present a frame. Sprite batching, tile rendering, and text
// come later (see Task #4/#5) and will build on top of GetDevice()/
// GetContext().
class Renderer
{
public:
    bool Initialize(HWND hwnd, uint32_t width, uint32_t height);
    void Resize(uint32_t width, uint32_t height);

    // Clears the backbuffer and binds it as the current render target.
    void BeginFrame();

    // Presents the frame. vsyncOn=true keeps us locked to display refresh,
    // matching the smooth, capped feel of the original dashboard.
    void EndFrame(bool vsyncOn = true);

    void SetClearColor(float r, float g, float b, float a = 1.0f)
    {
        m_clearColor = { r, g, b, a };
    }

    ID3D11Device* GetDevice() const { return m_device.Get(); }
    ID3D11DeviceContext* GetContext() const { return m_context.Get(); }

private:
    bool CreateDeviceAndSwapChain(HWND hwnd, uint32_t width, uint32_t height);
    bool CreateBackbufferRTV();
    void ReleaseBackbufferRTV();

    Microsoft::WRL::ComPtr<ID3D11Device> m_device;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> m_context;
    Microsoft::WRL::ComPtr<IDXGISwapChain> m_swapChain;
    Microsoft::WRL::ComPtr<ID3D11RenderTargetView> m_backbufferRTV;

    uint32_t m_width = 0;
    uint32_t m_height = 0;
    std::array<float, 4> m_clearColor = { 0.0f, 0.0f, 0.0f, 1.0f };
};
