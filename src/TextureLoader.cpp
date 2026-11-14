#include "TextureLoader.h"

#include <wincodec.h>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace
{
    // One process-lifetime WIC factory -- cheap to keep around, and every
    // caller needs one, so avoid re-creating it per texture load.
    ComPtr<IWICImagingFactory> GetWicFactory()
    {
        static ComPtr<IWICImagingFactory> factory;
        if (!factory)
        {
            CoCreateInstance(
                CLSID_WICImagingFactory,
                nullptr,
                CLSCTX_INPROC_SERVER,
                IID_PPV_ARGS(&factory));
        }
        return factory;
    }
}

TextureLoader::LoadedTexture TextureLoader::LoadFromFile(ID3D11Device* device, const std::wstring& path)
{
    LoadedTexture result;

    ComPtr<IWICImagingFactory> wic = GetWicFactory();
    if (!wic)
    {
        return result;
    }

    ComPtr<IWICBitmapDecoder> decoder;
    HRESULT hr = wic->CreateDecoderFromFilename(
        path.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &decoder);
    if (FAILED(hr))
    {
        return result;
    }

    ComPtr<IWICBitmapFrameDecode> frame;
    hr = decoder->GetFrame(0, &frame);
    if (FAILED(hr))
    {
        return result;
    }

    UINT width = 0, height = 0;
    frame->GetSize(&width, &height);
    if (width == 0 || height == 0)
    {
        return result;
    }

    // Normalize every source format (indexed, grayscale, no-alpha, etc.)
    // to 32bpp RGBA -- WIC's GUID_WICPixelFormat32bppRGBA byte order
    // (R,G,B,A per pixel) matches DXGI_FORMAT_R8G8B8A8_UNORM directly, so
    // the decoded bytes can go straight into the D3D11 texture with no
    // channel-swizzling step.
    ComPtr<IWICFormatConverter> converter;
    hr = wic->CreateFormatConverter(&converter);
    if (FAILED(hr))
    {
        return result;
    }

    hr = converter->Initialize(
        frame.Get(),
        GUID_WICPixelFormat32bppRGBA,
        WICBitmapDitherTypeNone,
        nullptr,
        0.0,
        WICBitmapPaletteTypeCustom);
    if (FAILED(hr))
    {
        return result;
    }

    const UINT stride = width * 4;
    std::vector<uint8_t> pixels(static_cast<size_t>(stride) * height);
    hr = converter->CopyPixels(nullptr, stride, static_cast<UINT>(pixels.size()), pixels.data());
    if (FAILED(hr))
    {
        return result;
    }

    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = width;
    desc.Height = height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_IMMUTABLE;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;

    D3D11_SUBRESOURCE_DATA initData = {};
    initData.pSysMem = pixels.data();
    initData.SysMemPitch = stride;

    hr = device->CreateTexture2D(&desc, &initData, &result.texture);
    if (FAILED(hr))
    {
        return result;
    }

    hr = device->CreateShaderResourceView(result.texture.Get(), nullptr, &result.srv);
    if (FAILED(hr))
    {
        result.texture.Reset();
        return result;
    }

    result.width = width;
    result.height = height;
    return result;
}
