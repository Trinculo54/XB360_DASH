#include "Renderer.h"

using Microsoft::WRL::ComPtr;

bool Renderer::Initialize(HWND hwnd, uint32_t width, uint32_t height)
{
    if (!CreateDeviceAndSwapChain(hwnd, width, height))
    {
        return false;
    }
    return CreateBackbufferRTV();
}

bool Renderer::CreateDeviceAndSwapChain(HWND hwnd, uint32_t width, uint32_t height)
{
    m_width = width;
    m_height = height;

    DXGI_SWAP_CHAIN_DESC scd = {};
    scd.BufferCount = 2;
    scd.BufferDesc.Width = width;
    scd.BufferDesc.Height = height;
    scd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    scd.BufferDesc.RefreshRate.Numerator = 60;
    scd.BufferDesc.RefreshRate.Denominator = 1;
    scd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    scd.OutputWindow = hwnd;
    scd.SampleDesc.Count = 1;
    scd.SampleDesc.Quality = 0;
    scd.Windowed = TRUE;
    scd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    UINT createFlags = 0;
#ifdef _DEBUG
    createFlags |= D3D11_CREATE_DEVICE_DEBUG;
#endif

    D3D_FEATURE_LEVEL requestedLevels[] = { D3D_FEATURE_LEVEL_11_0 };
    D3D_FEATURE_LEVEL obtainedLevel;

    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        nullptr,                    // default adapter
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        createFlags,
        requestedLevels, 1,
        D3D11_SDK_VERSION,
        &scd,
        &m_swapChain,
        &m_device,
        &obtainedLevel,
        &m_context);

    return SUCCEEDED(hr);
}

bool Renderer::CreateBackbufferRTV()
{
    ComPtr<ID3D11Texture2D> backbuffer;
    HRESULT hr = m_swapChain->GetBuffer(0, IID_PPV_ARGS(&backbuffer));
    if (FAILED(hr))
    {
        return false;
    }

    hr = m_device->CreateRenderTargetView(backbuffer.Get(), nullptr, &m_backbufferRTV);
    return SUCCEEDED(hr);
}

void Renderer::ReleaseBackbufferRTV()
{
    m_backbufferRTV.Reset();
}

void Renderer::Resize(uint32_t width, uint32_t height)
{
    if (!m_swapChain || width == 0 || height == 0)
    {
        return;
    }

    // The backbuffer RTV holds a reference to the backbuffer texture, so
    // it must be released before ResizeBuffers will succeed.
    m_context->OMSetRenderTargets(0, nullptr, nullptr);
    ReleaseBackbufferRTV();

    HRESULT hr = m_swapChain->ResizeBuffers(0, width, height, DXGI_FORMAT_UNKNOWN, 0);
    if (FAILED(hr))
    {
        // m_width/m_height deliberately NOT updated on failure: they're
        // used every frame (BeginFrame's viewport, and main.cpp's
        // currentWidth/currentHeight feeding QuadRenderer::
        // SetLetterboxViewport) as ground truth for the actual backbuffer
        // size. If the resize failed, the backbuffer is still whatever
        // size it was before this call -- claiming the new (unresized)
        // width/height here would desync every downstream size-dependent
        // calculation from the real surface, permanently, until the next
        // successful resize (rather than self-correcting on the next
        // frame). CreateBackbufferRTV() below still runs against the
        // pre-existing buffer so rendering keeps working at the old size
        // instead of going black.
        CreateBackbufferRTV();
        return;
    }

    m_width = width;
    m_height = height;
    CreateBackbufferRTV();
}

void Renderer::BeginFrame()
{
    ID3D11RenderTargetView* rtv = m_backbufferRTV.Get();
    m_context->OMSetRenderTargets(1, &rtv, nullptr);

    D3D11_VIEWPORT viewport = {};
    viewport.Width = static_cast<float>(m_width);
    viewport.Height = static_cast<float>(m_height);
    viewport.MinDepth = 0.0f;
    viewport.MaxDepth = 1.0f;
    m_context->RSSetViewports(1, &viewport);

    m_context->ClearRenderTargetView(rtv, m_clearColor.data());
}

void Renderer::EndFrame(bool vsyncOn)
{
    m_swapChain->Present(vsyncOn ? 1 : 0, 0);
}
