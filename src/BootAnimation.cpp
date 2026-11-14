#include "BootAnimation.h"

#include <windows.h>
#include <d3dcompiler.h>

#include <algorithm>
#include <cstring>
#include <fstream>

// See BootAnimation.h: this replays a recording of the title's Direct3D calls.
// The recording's layout is capture.cpp's WriteFile; the commands are below.

namespace
{
enum : uint32_t { kCmdTarget = 1, kCmdClear, kCmdResolve, kCmdDraw, kCmdVsConst, kCmdPsConst, kCmdPresent };
// A draw's words: the command, then these twelve, the render states, the eight
// texture stages, the eight samplers.
enum { kPrim = 1, kIndexed, kStart, kCount, kVs, kPs, kDecl, kStride, kVOffset, kVSize, kIOffset, kISize, kStates };
// The render states, in capture.cpp's order. The numbers in them are the
// Xbox 360's own D3D enums, which are the GPU's: compare 0..7 from NEVER to
// ALWAYS, blend 0 ZERO / 1 ONE / 4.. as Factor has them, cull 0 NONE / 2 CW /
// 6 CCW, colour write a mask of R, G, B, A from bit 0.
enum { kZEnable, kZWrite, kZFunc, kCull, kBlendEnable, kSrcBlend, kDestBlend, kAlphaTest, kAlphaFunc, kAlphaRef, kColorWrite };

// Vertex data at or under this size is a ribbon or a quad, new every frame;
// anything larger is a mesh, the same bytes throughout. (A ribbon is 21,840.)
constexpr uint32_t kStaticOver = 22000;
constexpr uint32_t kDynamicVBSize = 4u << 20, kDynamicIBSize = 64u << 10;

bool ReadAll(const std::wstring& path, std::vector<uint8_t>& out)
{
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f)
    {
        return false;
    }
    out.resize(static_cast<size_t>(f.tellg()));
    f.seekg(0);
    f.read(reinterpret_cast<char*>(out.data()), static_cast<std::streamsize>(out.size()));
    return static_cast<bool>(f);
}

D3D11_BLEND Factor(uint32_t f)
{
    switch (f)
    {
    case 0:  return D3D11_BLEND_ZERO;
    case 1:  return D3D11_BLEND_ONE;
    case 4:  return D3D11_BLEND_SRC_COLOR;
    case 5:  return D3D11_BLEND_INV_SRC_COLOR;
    case 6:  return D3D11_BLEND_SRC_ALPHA;
    case 7:  return D3D11_BLEND_INV_SRC_ALPHA;
    case 8:  return D3D11_BLEND_DEST_COLOR;
    case 9:  return D3D11_BLEND_INV_DEST_COLOR;
    case 10: return D3D11_BLEND_DEST_ALPHA;
    case 11: return D3D11_BLEND_INV_DEST_ALPHA;
    default: return D3D11_BLEND_ONE;
    }
}

// The same factor for the alpha channel, where Direct3D 11 takes no colour factor.
D3D11_BLEND AlphaFactor(uint32_t f)
{
    switch (f)
    {
    case 4: case 6:  return D3D11_BLEND_SRC_ALPHA;
    case 5: case 7:  return D3D11_BLEND_INV_SRC_ALPHA;
    case 8: case 10: return D3D11_BLEND_DEST_ALPHA;
    case 9: case 11: return D3D11_BLEND_INV_DEST_ALPHA;
    default:         return Factor(f);
    }
}
}

BootAnimation::~BootAnimation()
{
    Close();
}

// An Xbox 360 D3DFORMAT word: a depth format, and one whose alpha reads as 1
// whatever was written (X8R8G8B8, which is every surface and every texture the
// title resolves into -- the front buffers included).
static bool IsDepth(uint32_t format) { return (format & 0x3F) == 0x16; }
static bool AlphaIsOne(uint32_t format) { return (format >> 24) == 0x28; }

void BootAnimation::Close()
{
    m_frameSRV.Reset();
    m_samplers.clear();
    m_textures.clear();
    m_surfaces.clear();
    m_layouts.clear();
    m_staticVB.clear();
    m_staticIB.clear();
    m_blends.clear();
    m_depths.clear();
    m_rasters.clear();
    m_frames.clear();
    m_file.clear();
    m_file.shrink_to_fit();
    m_ready = false;
}

bool BootAnimation::Open(ID3D11Device* device, const std::wstring& assetDir, uint32_t, uint32_t)
{
    Close();
    m_finished = false;
    m_device = device;
    if (!LoadTrace(device, assetDir + L"\\trace.bin") || !LoadShaders(device, assetDir))
    {
        Close();
        return false;
    }

    auto buffer = [&](UINT size, UINT bind, bool dynamic, ComPtr<ID3D11Buffer>& out)
    {
        D3D11_BUFFER_DESC d = {};
        d.ByteWidth = size;
        d.BindFlags = bind;
        d.Usage = dynamic ? D3D11_USAGE_DYNAMIC : D3D11_USAGE_DEFAULT;
        d.CPUAccessFlags = dynamic ? D3D11_CPU_ACCESS_WRITE : 0;
        return SUCCEEDED(device->CreateBuffer(&d, nullptr, out.GetAddressOf()));
    };
    bool ok = buffer(kDynamicVBSize, D3D11_BIND_VERTEX_BUFFER, true, m_dynamicVB)
            && buffer(kDynamicIBSize, D3D11_BIND_INDEX_BUFFER, true, m_dynamicIB)
            && buffer(16, D3D11_BIND_VERTEX_BUFFER, false, m_zeroVB)
            && buffer(m_vsRegs * 16, D3D11_BIND_CONSTANT_BUFFER, true, m_cbVs)
            && buffer(m_psRegs * 16, D3D11_BIND_CONSTANT_BUFFER, true, m_cbPs)
            && buffer(16, D3D11_BIND_CONSTANT_BUFFER, true, m_cbShared);
    // (see MakeOpaque)
    static const char kOpaque[] =
        "float4 VS(uint id : SV_VertexID) : SV_Position { return float4(id == 2 ? 3.0 : -1.0, id == 1 ? 3.0 : -1.0, 0.0, 1.0); }\n"
        "float4 PS() : SV_Target { return float4(0.0, 0.0, 0.0, 1.0); }\n";
    ComPtr<ID3DBlob> vsCode, psCode;
    D3D11_BLEND_DESC alphaOnly = {};
    alphaOnly.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALPHA;
    ok = ok && SUCCEEDED(D3DCompile(kOpaque, sizeof(kOpaque) - 1, nullptr, nullptr, nullptr, "VS", "vs_4_0", 0, 0, vsCode.GetAddressOf(), nullptr))
            && SUCCEEDED(D3DCompile(kOpaque, sizeof(kOpaque) - 1, nullptr, nullptr, nullptr, "PS", "ps_4_0", 0, 0, psCode.GetAddressOf(), nullptr))
            && SUCCEEDED(device->CreateVertexShader(vsCode->GetBufferPointer(), vsCode->GetBufferSize(), nullptr, m_opaqueVS.ReleaseAndGetAddressOf()))
            && SUCCEEDED(device->CreatePixelShader(psCode->GetBufferPointer(), psCode->GetBufferSize(), nullptr, m_opaquePS.ReleaseAndGetAddressOf()))
            && SUCCEEDED(device->CreateBlendState(&alphaOnly, m_alphaOnly.ReleaseAndGetAddressOf()));
    if (!ok)
    {
        Close();
        return false;
    }
    m_vbCursor = m_ibCursor = 0;
    m_ready = true;
    return true;
}

bool BootAnimation::LoadTrace(ID3D11Device* device, const std::wstring& path)
{
    if (!ReadAll(path, m_file) || m_file.size() < 44 || std::memcmp(m_file.data(), "BATRACE2", 8) != 0)
    {
        return false;
    }
    const uint8_t* p = m_file.data() + 8;
    const uint8_t* end = m_file.data() + m_file.size();
    auto u32 = [&]() { uint32_t v = 0; if (p + 4 <= end) { std::memcpy(&v, p, 4); } p += 4; return v; };
    const uint32_t frames = u32();
    m_fps = static_cast<float>(u32()) / 1000.0f;
    m_vsRegs = u32();
    m_psRegs = u32();
    m_stateCount = u32();
    const uint32_t textures = u32(), surfaces = u32(), decls = u32();
    m_poolSize = u32();
    if (m_vsRegs == 0 || m_psRegs == 0 || m_vsRegs > 256 || m_psRegs > 256 || m_stateCount <= kColorWrite || m_fps <= 0.0f)
    {
        return false;
    }
    m_vsConst.assign(m_vsRegs * 4, 0.0f);
    m_psConst.assign(m_psRegs * 4, 0.0f);

    m_textures.resize(textures);
    for (Texture& t : m_textures)
    {
        t.w = u32();
        t.h = u32();
        t.format = u32();
        t.target = u32() != 0;
        if (t.w == 0 || t.h == 0 || t.w > 8192 || t.h > 8192)
        {
            return false;
        }
        D3D11_TEXTURE2D_DESC d = {};
        d.Width = t.w;
        d.Height = t.h;
        d.MipLevels = 1;
        d.ArraySize = 1;
        d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        d.SampleDesc.Count = 1;
        d.BindFlags = D3D11_BIND_SHADER_RESOURCE | (t.target ? D3D11_BIND_RENDER_TARGET : 0);     // (see MakeOpaque)
        std::vector<uint8_t> rgba;
        D3D11_SUBRESOURCE_DATA init = {};
        if (!t.target)
        {
            // As the title wrote them: big-endian ARGB words, so A, R, G, B in memory.
            const size_t bytes = static_cast<size_t>(t.w) * t.h * 4;
            if (p + bytes > end)
            {
                return false;
            }
            rgba.resize(bytes);
            for (size_t i = 0; i < bytes; i += 4)
            {
                rgba[i] = p[i + 1];
                rgba[i + 1] = p[i + 2];
                rgba[i + 2] = p[i + 3];
                rgba[i + 3] = p[i];
            }
            p += bytes;
            init.pSysMem = rgba.data();
            init.SysMemPitch = t.w * 4;
        }
        if (FAILED(device->CreateTexture2D(&d, t.target ? nullptr : &init, t.texture.GetAddressOf()))
            || FAILED(device->CreateShaderResourceView(t.texture.Get(), nullptr, t.srv.GetAddressOf()))
            || (t.target && FAILED(device->CreateRenderTargetView(t.texture.Get(), nullptr, t.rtv.GetAddressOf()))))
        {
            return false;
        }
    }

    // An EDRAM surface is a render target, multisampled as the title asked,
    // with a depth buffer of its own; the title never samples one, it
    // resolves it into a texture first. Its depth surfaces are in the list
    // too and get nothing: the title pairs each with one colour surface and
    // carries no depth from one to another, which is what a depth buffer per
    // colour surface gives.
    m_surfaces.resize(surfaces);
    for (Surface& s : m_surfaces)
    {
        s.w = u32();
        s.h = u32();
        s.format = u32();
        s.samples = u32();
        if (s.w == 0 || s.h == 0 || s.w > 8192 || s.h > 8192)
        {
            return false;
        }
        if (IsDepth(s.format))
        {
            continue;
        }
        UINT levels = 0;
        if (s.samples > 1 && (FAILED(device->CheckMultisampleQualityLevels(DXGI_FORMAT_R8G8B8A8_UNORM, s.samples, &levels)) || levels == 0))
        {
            s.samples = 1;
        }
        D3D11_TEXTURE2D_DESC d = {};
        d.Width = s.w;
        d.Height = s.h;
        d.MipLevels = 1;
        d.ArraySize = 1;
        d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        d.SampleDesc.Count = s.samples;
        d.BindFlags = D3D11_BIND_RENDER_TARGET;
        if (FAILED(device->CreateTexture2D(&d, nullptr, s.color.GetAddressOf()))
            || FAILED(device->CreateRenderTargetView(s.color.Get(), nullptr, s.rtv.GetAddressOf())))
        {
            return false;
        }
        d.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        d.BindFlags = D3D11_BIND_DEPTH_STENCIL;
        if (FAILED(device->CreateTexture2D(&d, nullptr, s.depth.GetAddressOf()))
            || FAILED(device->CreateDepthStencilView(s.depth.Get(), nullptr, s.dsv.GetAddressOf())))
        {
            return false;
        }
    }

    m_decls.resize(decls);
    for (std::vector<uint32_t>& d : m_decls)
    {
        const uint32_t n = u32();
        if (n > 64 || p + n * 4 > end)
        {
            return false;
        }
        d.resize(n);
        for (uint32_t& w : d)
        {
            w = u32();
        }
    }

    if (p + m_poolSize > end)
    {
        return false;
    }
    m_pool = p;
    p += m_poolSize;

    m_frames.resize(frames);
    for (Frame& f : m_frames)
    {
        f.count = u32();
        if (p + static_cast<size_t>(f.count) * 4 > end)
        {
            return false;
        }
        f.words = reinterpret_cast<const uint32_t*>(p);
        p += static_cast<size_t>(f.count) * 4;
    }
    return !m_frames.empty();
}

bool BootAnimation::LoadShaders(ID3D11Device* device, const std::wstring& dir)
{
    for (int i = 0; i < kShaders; ++i)
    {
        wchar_t name[32];
        std::vector<uint8_t> code;
        swprintf_s(name, L"\\shader%02d.vs.cso", i);
        if (ReadAll(dir + name, code))
        {
            if (FAILED(device->CreateVertexShader(code.data(), code.size(), nullptr, m_vs[i].ReleaseAndGetAddressOf())))
            {
                return false;
            }
            m_vsCode[i] = std::move(code);
            continue;
        }
        swprintf_s(name, L"\\shader%02d.ps.cso", i);
        if (!ReadAll(dir + name, code)
            || FAILED(device->CreatePixelShader(code.data(), code.size(), nullptr, m_ps[i].ReleaseAndGetAddressOf())))
        {
            return false;
        }
    }
    return true;
}

// The title's vertex declaration as an input layout for one of its vertex
// shaders. A declaration is 12-byte elements -- stream and offset, a type,
// then method / usage / usage index -- ended by stream 0xFF. Direct3D 11
// wants every input a shader reads; one the declaration lacks reads zeros,
// from a buffer of them stepped per instance.
ID3D11InputLayout* BootAnimation::Layout(uint32_t decl, uint32_t vs)
{
    const uint32_t key = (decl << 8) | vs;
    auto found = m_layouts.find(key);
    if (found != m_layouts.end())
    {
        return found->second.Get();
    }
    ComPtr<ID3D11InputLayout>& layout = m_layouts[key];
    if (decl >= m_decls.size() || m_vsCode[vs].empty())
    {
        return nullptr;
    }
    static const char* const kNames[] = { "POSITION", "NORMAL", "TEXCOORD", "COLOR" };
    bool have[4] = {};
    std::vector<D3D11_INPUT_ELEMENT_DESC> elements;
    const std::vector<uint32_t>& w = m_decls[decl];
    for (size_t i = 0; i + 2 < w.size(); i += 3)
    {
        if ((w[i] >> 16) == 0xFF)
        {
            break;
        }
        const uint32_t usage = (w[i + 2] >> 16) & 0xFF;
        const int name = (usage == 0) ? 0 : (usage == 3) ? 1 : (usage == 5) ? 2 : (usage == 10) ? 3 : -1;
        if (name < 0)
        {
            continue;
        }
        D3D11_INPUT_ELEMENT_DESC e = {};
        e.SemanticName = kNames[name];
        e.SemanticIndex = (w[i + 2] >> 8) & 0xFF;
        e.AlignedByteOffset = w[i] & 0xFFFF;
        e.InputSlotClass = D3D11_INPUT_PER_VERTEX_DATA;
        switch (w[i + 1])      // the Xbox 360's D3DDECLTYPE values
        {
        case 0x2C83A4: e.Format = DXGI_FORMAT_R32_FLOAT; break;
        case 0x2C23A5: e.Format = DXGI_FORMAT_R32G32_FLOAT; break;
        case 0x2A23B9: e.Format = DXGI_FORMAT_R32G32B32_FLOAT; break;
        default:       e.Format = DXGI_FORMAT_R32G32B32A32_FLOAT; break;
        }
        have[name] = have[name] || e.SemanticIndex == 0;
        elements.push_back(e);
    }
    for (int name = 0; name < 4; ++name)
    {
        if (!have[name])
        {
            D3D11_INPUT_ELEMENT_DESC e = {};
            e.SemanticName = kNames[name];
            e.Format = DXGI_FORMAT_R32G32B32A32_FLOAT;
            e.InputSlot = 1;
            e.InputSlotClass = D3D11_INPUT_PER_INSTANCE_DATA;
            elements.push_back(e);
        }
    }
    m_device->CreateInputLayout(elements.data(), static_cast<UINT>(elements.size()), m_vsCode[vs].data(),
        m_vsCode[vs].size(), layout.GetAddressOf());
    return layout.Get();
}

ID3D11Buffer* BootAnimation::Static(std::unordered_map<uint32_t, ComPtr<ID3D11Buffer>>& cache, uint32_t offset,
    uint32_t size, UINT bind)
{
    auto found = cache.find(offset);
    if (found != cache.end())
    {
        return found->second.Get();
    }
    ComPtr<ID3D11Buffer>& buffer = cache[offset];
    D3D11_BUFFER_DESC d = {};
    d.ByteWidth = size;
    d.Usage = D3D11_USAGE_IMMUTABLE;
    d.BindFlags = bind;
    D3D11_SUBRESOURCE_DATA init = {};
    init.pSysMem = m_pool + offset;
    m_device->CreateBuffer(&d, &init, buffer.GetAddressOf());
    return buffer.Get();
}

// Appends to a dynamic buffer, starting it over when it is full.
uint32_t BootAnimation::Transient(ID3D11DeviceContext* ctx, ID3D11Buffer* buffer, uint32_t& cursor, uint32_t capacity,
    const void* data, uint32_t size, uint32_t align)
{
    cursor = (cursor + align - 1) / align * align;
    const bool wrap = cursor + size > capacity;
    if (wrap)
    {
        cursor = 0;
        if (buffer == m_dynamicVB.Get())
        {
            m_placed.clear();
        }
    }
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    if (size <= capacity && SUCCEEDED(ctx->Map(buffer, 0, wrap ? D3D11_MAP_WRITE_DISCARD : D3D11_MAP_WRITE_NO_OVERWRITE, 0, &mapped)))
    {
        std::memcpy(static_cast<uint8_t*>(mapped.pData) + cursor, data, size);
        ctx->Unmap(buffer, 0);
    }
    const uint32_t at = cursor;
    cursor += size;
    return at;
}

void BootAnimation::Draw(ID3D11DeviceContext* ctx, const uint32_t* d)
{
    const uint32_t vs = d[kVs], ps = d[kPs], count = d[kCount], stride = d[kStride];
    const uint32_t vOffset = d[kVOffset], vSize = d[kVSize], iOffset = d[kIOffset], iSize = d[kISize];
    const uint32_t* state = d + kStates;
    const uint32_t* stage = state + m_stateCount;
    if (m_target < 0 || !m_surfaces[m_target].rtv || vs >= kShaders || ps >= kShaders || !m_vs[vs] || !m_ps[ps] || count == 0 || vSize == 0
        || stride == 0 || static_cast<uint64_t>(vOffset) + vSize > m_poolSize || static_cast<uint64_t>(iOffset) + iSize > m_poolSize)
    {
        return;
    }
    ID3D11InputLayout* layout = Layout(d[kDecl], vs);
    if (!layout)
    {
        return;
    }
    const Surface& target = m_surfaces[m_target];

    // Constants: the title's registers as they stand, and the two this
    // replayer adds. Direct3D 9 puts pixel centres on whole numbers and
    // Direct3D 11 on halves, so everything moves half a pixel right and down.
    // The alpha test is a clip in the pixel shader, which keeps a pixel at or
    // over the threshold: GREATER than the reference -- how the title always
    // asks for it -- is the reference and the least bit more.
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    if (SUCCEEDED(ctx->Map(m_cbVs.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
    {
        std::memcpy(mapped.pData, m_vsConst.data(), m_vsConst.size() * sizeof(float));
        ctx->Unmap(m_cbVs.Get(), 0);
    }
    if (SUCCEEDED(ctx->Map(m_cbPs.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
    {
        std::memcpy(mapped.pData, m_psConst.data(), m_psConst.size() * sizeof(float));
        ctx->Unmap(m_cbPs.Get(), 0);
    }
    if (SUCCEEDED(ctx->Map(m_cbShared.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
    {
        const bool test = state[kAlphaTest] != 0 && state[kAlphaFunc] == 4;
        const float shared[4] = { 1.0f / static_cast<float>(target.w), -1.0f / static_cast<float>(target.h),
                                  test ? static_cast<float>(state[kAlphaRef]) / 255.0f + 1e-6f : -1.0f, 0.0f };
        std::memcpy(mapped.pData, shared, sizeof(shared));
        ctx->Unmap(m_cbShared.Get(), 0);
    }
    ID3D11Buffer* vsBuffers[3] = { m_cbVs.Get(), nullptr, m_cbShared.Get() };
    ID3D11Buffer* psBuffers[3] = { nullptr, m_cbPs.Get(), m_cbShared.Get() };
    ctx->VSSetConstantBuffers(0, 3, vsBuffers);
    ctx->PSSetConstantBuffers(0, 3, psBuffers);
    ctx->VSSetShader(m_vs[vs].Get(), nullptr, 0);
    ctx->PSSetShader(m_ps[ps].Get(), nullptr, 0);
    ctx->IASetInputLayout(layout);

    // Textures, each with the sampler the title had on its stage: address U
    // and V in three bits each (the GPU's numbers: 0 wrap, 1 mirror, 2 clamp,
    // 3 mirror once, 6 border), then magnify and minify (0 point, 1 linear).
    // The textures have one level, so there is nothing to say about mips.
    ID3D11ShaderResourceView* views[8] = {};
    ID3D11SamplerState* samplers[8] = {};
    for (int i = 0; i < 8; ++i)
    {
        if (stage[i] >= m_textures.size())
        {
            continue;
        }
        views[i] = m_textures[stage[i]].srv.Get();
        const uint32_t word = stage[8 + i];
        ComPtr<ID3D11SamplerState>& sampler = m_samplers[word];
        if (!sampler)
        {
            auto address = [](uint32_t a)
            {
                return a == 0 ? D3D11_TEXTURE_ADDRESS_WRAP : a == 1 ? D3D11_TEXTURE_ADDRESS_MIRROR
                     : a == 3 ? D3D11_TEXTURE_ADDRESS_MIRROR_ONCE : a >= 6 ? D3D11_TEXTURE_ADDRESS_BORDER : D3D11_TEXTURE_ADDRESS_CLAMP;
            };
            D3D11_SAMPLER_DESC sd = {};
            sd.Filter = static_cast<D3D11_FILTER>((((word >> 8) & 3) ? 0x10 : 0) | (((word >> 6) & 3) ? 0x4 : 0));
            sd.AddressU = address(word & 7);
            sd.AddressV = address((word >> 3) & 7);
            sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
            sd.MaxLOD = D3D11_FLOAT32_MAX;
            m_device->CreateSamplerState(&sd, sampler.GetAddressOf());
        }
        samplers[i] = sampler.Get();
    }
    ctx->PSSetShaderResources(0, 8, views);
    ctx->PSSetSamplers(0, 8, samplers);

    // Render states, made the first time each combination is asked for.
    const uint32_t depthKey = (state[kZEnable] ? 1u : 0u) | (state[kZWrite] ? 2u : 0u) | ((state[kZFunc] & 7u) << 2);
    ComPtr<ID3D11DepthStencilState>& depth = m_depths[depthKey];
    if (!depth)
    {
        static const D3D11_COMPARISON_FUNC kCompare[8] = {
            D3D11_COMPARISON_NEVER, D3D11_COMPARISON_LESS, D3D11_COMPARISON_EQUAL, D3D11_COMPARISON_LESS_EQUAL,
            D3D11_COMPARISON_GREATER, D3D11_COMPARISON_NOT_EQUAL, D3D11_COMPARISON_GREATER_EQUAL, D3D11_COMPARISON_ALWAYS };
        D3D11_DEPTH_STENCIL_DESC dd = {};
        dd.DepthEnable = state[kZEnable] != 0;
        dd.DepthWriteMask = state[kZWrite] ? D3D11_DEPTH_WRITE_MASK_ALL : D3D11_DEPTH_WRITE_MASK_ZERO;
        dd.DepthFunc = kCompare[state[kZFunc] & 7u];
        m_device->CreateDepthStencilState(&dd, depth.GetAddressOf());
    }
    const uint64_t blendKey = (state[kBlendEnable] ? 1ull : 0ull) | (static_cast<uint64_t>(state[kSrcBlend] & 0xFF) << 8)
        | (static_cast<uint64_t>(state[kDestBlend] & 0xFF) << 16) | (static_cast<uint64_t>(state[kColorWrite] & 0xF) << 24);
    ComPtr<ID3D11BlendState>& blend = m_blends[blendKey];
    if (!blend)
    {
        D3D11_BLEND_DESC bd = {};
        D3D11_RENDER_TARGET_BLEND_DESC& rt = bd.RenderTarget[0];
        rt.BlendEnable = state[kBlendEnable] != 0;
        rt.SrcBlend = Factor(state[kSrcBlend]);
        rt.DestBlend = Factor(state[kDestBlend]);
        rt.SrcBlendAlpha = AlphaFactor(state[kSrcBlend]);
        rt.DestBlendAlpha = AlphaFactor(state[kDestBlend]);
        rt.BlendOp = rt.BlendOpAlpha = D3D11_BLEND_OP_ADD;
        rt.RenderTargetWriteMask = static_cast<UINT8>(state[kColorWrite] & 0xF);
        m_device->CreateBlendState(&bd, blend.GetAddressOf());
    }
    // Culling clockwise triangles is culling back faces with counter-clockwise ones in front, and the reverse.
    ComPtr<ID3D11RasterizerState>& raster = m_rasters[state[kCull]];
    if (!raster)
    {
        D3D11_RASTERIZER_DESC rd = {};
        rd.FillMode = D3D11_FILL_SOLID;
        rd.CullMode = state[kCull] == 0 ? D3D11_CULL_NONE : D3D11_CULL_BACK;
        rd.FrontCounterClockwise = state[kCull] == 2;
        rd.DepthClipEnable = TRUE;
        m_device->CreateRasterizerState(&rd, raster.GetAddressOf());
    }
    ctx->OMSetDepthStencilState(depth.Get(), 0);
    ctx->OMSetBlendState(blend.Get(), nullptr, 0xFFFFFFFF);
    ctx->RSSetState(raster.Get());

    // Geometry.
    ID3D11Buffer* buffers[2] = { nullptr, m_zeroVB.Get() };
    UINT strides[2] = { stride, 16 }, offsets[2] = { 0, 0 };
    if (vSize > kStaticOver)
    {
        buffers[0] = Static(m_staticVB, vOffset, vSize, D3D11_BIND_VERTEX_BUFFER);
    }
    else
    {
        auto placed = m_placed.find(vOffset);
        if (placed == m_placed.end())
        {
            const uint32_t at = Transient(ctx, m_dynamicVB.Get(), m_vbCursor, kDynamicVBSize, m_pool + vOffset, vSize, 4);
            placed = m_placed.emplace(vOffset, at).first;
        }
        buffers[0] = m_dynamicVB.Get();
        offsets[0] = placed->second;
    }
    if (!buffers[0])
    {
        return;
    }
    ctx->IASetVertexBuffers(0, 2, buffers, strides, offsets);

    // Primitives are the GPU's numbers: 4 a list, 5 a fan, 6 a strip, 13 a
    // list of quads. Direct3D 11 has no fan and no quad, so those two are
    // drawn as the triangles they are.
    const uint32_t prim = d[kPrim];
    const bool indexed = d[kIndexed] != 0 && iSize != 0;
    if (prim == 5 || prim == 13)
    {
        std::vector<uint16_t> indices;
        if (prim == 5)
        {
            for (uint32_t i = 1; i + 1 < count; ++i)
            {
                indices.insert(indices.end(), { static_cast<uint16_t>(0), static_cast<uint16_t>(i), static_cast<uint16_t>(i + 1) });
            }
        }
        else
        {
            for (uint32_t q = 0; q + 3 < count; q += 4)
            {
                for (uint32_t k : { 0u, 1u, 2u, 0u, 2u, 3u })
                {
                    indices.push_back(static_cast<uint16_t>(q + k));
                }
            }
        }
        if (indices.empty())
        {
            return;
        }
        const uint32_t at = Transient(ctx, m_dynamicIB.Get(), m_ibCursor, kDynamicIBSize, indices.data(),
            static_cast<uint32_t>(indices.size() * 2), 2);
        ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ctx->IASetIndexBuffer(m_dynamicIB.Get(), DXGI_FORMAT_R16_UINT, 0);
        ctx->DrawIndexed(static_cast<UINT>(indices.size()), at / 2, indexed ? 0 : static_cast<INT>(d[kStart]));
        return;
    }
    ctx->IASetPrimitiveTopology(prim == 4 ? D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST : D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    if (indexed)
    {
        ID3D11Buffer* ib = Static(m_staticIB, iOffset, iSize, D3D11_BIND_INDEX_BUFFER);
        if (ib)
        {
            ctx->IASetIndexBuffer(ib, DXGI_FORMAT_R16_UINT, 0);
            ctx->DrawIndexed(count, d[kStart], 0);
        }
    }
    else
    {
        ctx->Draw(count, d[kStart]);
    }
}

// The current surface as the render target, the whole of it.
void BootAnimation::Bind(ID3D11DeviceContext* ctx)
{
    if (m_target < 0)
    {
        ctx->OMSetRenderTargets(0, nullptr, nullptr);
        return;
    }
    const Surface& s = m_surfaces[m_target];
    ctx->OMSetRenderTargets(1, s.rtv.GetAddressOf(), s.dsv.Get());
    const D3D11_VIEWPORT vp = { 0.0f, 0.0f, static_cast<float>(s.w), static_cast<float>(s.h), 0.0f, 1.0f };
    ctx->RSSetViewports(1, &vp);
}

// Writes alpha 1 over a texture and nothing else (see AlphaIsOne): one
// triangle, the alpha channel the only one written.
void BootAnimation::MakeOpaque(ID3D11DeviceContext* ctx, const Texture& t)
{
    const D3D11_VIEWPORT vp = { 0.0f, 0.0f, static_cast<float>(t.w), static_cast<float>(t.h), 0.0f, 1.0f };
    ctx->OMSetRenderTargets(1, t.rtv.GetAddressOf(), nullptr);
    ctx->RSSetViewports(1, &vp);
    ctx->OMSetBlendState(m_alphaOnly.Get(), nullptr, 0xFFFFFFFF);
    ctx->OMSetDepthStencilState(nullptr, 0);
    ctx->RSSetState(nullptr);
    ctx->IASetInputLayout(nullptr);
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ctx->VSSetShader(m_opaqueVS.Get(), nullptr, 0);
    ctx->PSSetShader(m_opaquePS.Get(), nullptr, 0);
    ctx->Draw(3, 0);
}

bool BootAnimation::Update(ID3D11DeviceContext* ctx, float elapsedSeconds)
{
    if (!m_ready)
    {
        return false;
    }
    if (elapsedSeconds >= Duration())
    {
        m_finished = true;
        return false;
    }
    const size_t index = (std::min)(m_frames.size() - 1, static_cast<size_t>((std::max)(0.0f, elapsedSeconds) * m_fps + 0.5f));
    const Frame& frame = m_frames[index];

    // Whatever the app had bound, to put back afterwards.
    ComPtr<ID3D11RenderTargetView> appTarget;
    ComPtr<ID3D11DepthStencilView> appDepth;
    D3D11_VIEWPORT appViewport = {};
    UINT viewports = 1;
    ctx->OMGetRenderTargets(1, appTarget.GetAddressOf(), appDepth.GetAddressOf());
    ctx->RSGetViewports(&viewports, &appViewport);
    ID3D11ShaderResourceView* none[8] = {};
    ctx->PSSetShaderResources(0, 8, none);

    m_placed.clear();
    m_target = -1;
    const uint32_t drawWords = kStates + m_stateCount + 16;
    for (uint32_t i = 0; i < frame.count; )
    {
        const uint32_t* w = frame.words + i;
        const uint32_t left = frame.count - i;
        if (w[0] == kCmdTarget && left >= 2)
        {
            m_target = (w[1] < m_surfaces.size() && m_surfaces[w[1]].rtv) ? static_cast<int>(w[1]) : -1;
            Bind(ctx);
            i += 2;
        }
        else if (w[0] == kCmdClear && left >= 3)
        {
            // Flags: the four colour channels, then depth, then stencil. The colour is ARGB.
            if (m_target >= 0)
            {
                const Surface& s = m_surfaces[m_target];
                if (w[1] & 0xF)
                {
                    const float c[4] = { ((w[2] >> 16) & 0xFF) / 255.0f, ((w[2] >> 8) & 0xFF) / 255.0f, (w[2] & 0xFF) / 255.0f,
                                         ((w[2] >> 24) & 0xFF) / 255.0f };
                    ctx->ClearRenderTargetView(s.rtv.Get(), c);
                }
                if (w[1] & 0x30)
                {
                    ctx->ClearDepthStencilView(s.dsv.Get(), D3D11_CLEAR_DEPTH | D3D11_CLEAR_STENCIL, 1.0f, 0);
                }
            }
            i += 3;
        }
        else if (w[0] == kCmdResolve && left >= 2)
        {
            // What the surface holds becomes the texture -- its samples
            // averaged, if it has them -- which may be bound somewhere: unbind first.
            if (m_target >= 0 && w[1] < m_textures.size())
            {
                const Surface& s = m_surfaces[m_target];
                const Texture& t = m_textures[w[1]];
                if (t.w == s.w && t.h == s.h && t.rtv)
                {
                    ctx->PSSetShaderResources(0, 8, none);
                    if (s.samples > 1)
                    {
                        ctx->ResolveSubresource(t.texture.Get(), 0, s.color.Get(), 0, DXGI_FORMAT_R8G8B8A8_UNORM);
                    }
                    else
                    {
                        ctx->CopyResource(t.texture.Get(), s.color.Get());
                    }
                    if (AlphaIsOne(t.format))
                    {
                        MakeOpaque(ctx, t);
                        Bind(ctx);
                    }
                }
            }
            i += 2;
        }
        else if (w[0] == kCmdPresent && left >= 2)
        {
            if (w[1] < m_textures.size())
            {
                m_frameSRV = m_textures[w[1]].srv;      // one of the title's two front buffers, in turn
            }
            i += 2;
        }
        else if ((w[0] == kCmdVsConst || w[0] == kCmdPsConst) && left >= 3 && left >= 3 + static_cast<uint64_t>(w[2]) * 4)
        {
            std::vector<float>& dst = (w[0] == kCmdVsConst) ? m_vsConst : m_psConst;
            if (static_cast<uint64_t>(w[1]) + w[2] <= dst.size() / 4)
            {
                std::memcpy(dst.data() + w[1] * 4, w + 3, static_cast<size_t>(w[2]) * 16);
            }
            i += 3 + w[2] * 4;
        }
        else if (w[0] == kCmdDraw && left >= drawWords)
        {
            Draw(ctx, w);
            i += drawWords;
        }
        else
        {
            break;      // not a recording this build reads
        }
    }

    ctx->PSSetShaderResources(0, 8, none);
    ctx->OMSetRenderTargets(1, appTarget.GetAddressOf(), appDepth.Get());
    if (viewports)
    {
        ctx->RSSetViewports(1, &appViewport);
    }
    ctx->OMSetBlendState(nullptr, nullptr, 0xFFFFFFFF);
    ctx->OMSetDepthStencilState(nullptr, 0);
    ctx->RSSetState(nullptr);
    return true;
}
