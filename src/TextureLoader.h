#pragma once

#include <windows.h>
#include <d3d11.h>
#include <wrl/client.h>
#include <cstdint>
#include <string>

// Decodes an image file (PNG, and anything else WIC understands) into a
// D3D11 texture + shader resource view, using the Windows Imaging
// Component -- a built-in Windows API, so this needs no extra dependency
// (no stb_image/libpng/vcpkg package) to get real PNG assets on screen.
//
// Requires COM to have been initialized on the calling thread (e.g. via
// CoInitializeEx in main) before use.
namespace TextureLoader
{
    struct LoadedTexture
    {
        Microsoft::WRL::ComPtr<ID3D11Texture2D> texture;
        Microsoft::WRL::ComPtr<ID3D11ShaderResourceView> srv;
        uint32_t width = 0;
        uint32_t height = 0;
    };

    // Returns a texture with width/height == 0 (and null srv) on failure --
    // callers should check srv before using it, not treat failure as fatal,
    // since a single missing/corrupt image shouldn't take the whole scene
    // down.
    LoadedTexture LoadFromFile(ID3D11Device* device, const std::wstring& path);
}
