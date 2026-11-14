#include "BootAnimation.h"

#include <windows.h>
#include <d3dcompiler.h>
#include <cstring>
#include <string>
#include <vector>

namespace {

// One HLSL source for the whole effect. Compiled at runtime the same way
// QuadRenderer does its own, so there is no build step and no .cso to keep in
// sync with the source.
//
// The matrix convention comes from the console: the vertex shader constants
// hold the transform such that clip.x = dot(position, c0), clip.y =
// dot(position, c1) and so on. Four consecutive registers are therefore four
// rows, which is why the matrices are declared row_major -- HLSL's default is
// column-major, and under that default the same bytes would be read as
// columns and the whole scene would come out transposed. Declaring it means
// the baked matrices upload byte for byte with no fixup.
const char* kShaderSource = R"HLSL(

cbuffer MeshCB : register(b0)
{
    row_major float4x4 gWVP;    // rows are the four constant registers
    row_major float4x4 gWorld;
    float4   gBaseColor;  // rgb = albedo, a = emissive strength
    float4   gParams;     // x = rim power, y = rim strength, z = fade, w unused
};

cbuffer PostCB : register(b1)
{
    float4 gTexel;        // xy = 1/size of the source being sampled
    float4 gPost;         // x = blur direction (0 = horizontal), y = glow gain
};

Texture2D    gTex    : register(t0);
Texture2D    gGlow   : register(t1);
SamplerState gSamp   : register(s0);

struct MeshVS { float3 pos : POSITION; float3 nrm : NORMAL; float2 uv : TEXCOORD0; };
struct MeshPS { float4 pos : SV_Position; float3 nrm : TEXCOORD0; float3 wpos : TEXCOORD1; float2 uv : TEXCOORD2; };

MeshPS VSMesh(MeshVS i)
{
    MeshPS o;
    float4 p = float4(i.pos, 1.0);
    o.pos  = mul(gWVP, p);
    o.wpos = mul(gWorld, p).xyz;
    o.nrm  = normalize(mul((float3x3)gWorld, i.nrm));
    o.uv   = i.uv;
    return o;
}

// The original runs five directional lights and two spots through a shader
// with a per-material colour and an alpha gradient. This is the readable
// stand-in: one key, one fill, and a rim term that does the work the spots
// were doing on the silhouette.
float4 PSMesh(MeshPS i) : SV_Target
{
    float3 n = normalize(i.nrm);

    const float3 kKeyDir  = normalize(float3(-0.35, 0.72, -0.60));
    const float3 kFillDir = normalize(float3( 0.55, 0.15,  0.45));
    // The camera barely moves in world space once the orb has settled -- it
    // is the orb that rotates -- so a fixed view direction is close enough
    // for a rim term and avoids threading a camera position through.
    const float3 kView    = float3(0.0, 0.0, -1.0);

    float key  = saturate(dot(n, kKeyDir));
    float fill = saturate(dot(n, kFillDir)) * 0.35;
    float amb  = 0.18 + 0.22 * saturate(n.y * 0.5 + 0.5);

    float rim  = pow(saturate(1.0 - abs(dot(n, kView))), gParams.x) * gParams.y;

    float3 lit = gBaseColor.rgb * (amb + key * 0.85 + fill) + gBaseColor.rgb * rim;
    lit += gBaseColor.rgb * gBaseColor.a;              // emissive lift for the X

    return float4(lit * gParams.z, gParams.z);
}

struct QuadPS { float4 pos : SV_Position; float2 uv : TEXCOORD0; };

// A single oversized triangle rather than a quad: no seam down the diagonal,
// one fewer vertex, and the UVs fall out of the clip coordinates.
QuadPS VSQuad(uint id : SV_VertexID)
{
    QuadPS o;
    float2 uv = float2((id << 1) & 2, id & 2);
    o.uv  = uv;
    o.pos = float4(uv * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    return o;
}

// Nine-tap Gaussian, separable. The original uses two shaders with six
// offset and six weight constants each; this is the same idea with the
// weights inline.
float4 PSBlur(QuadPS i) : SV_Target
{
    float2 dir = (gPost.x < 0.5) ? float2(gTexel.x, 0.0) : float2(0.0, gTexel.y);
    const float w[5] = { 0.2270270270, 0.1945945946, 0.1216216216, 0.0540540541, 0.0162162162 };
    float4 c = gTex.Sample(gSamp, i.uv) * w[0];
    [unroll] for (int k = 1; k < 5; ++k)
    {
        float2 o = dir * (float)k * 1.6;
        c += gTex.Sample(gSamp, i.uv + o) * w[k];
        c += gTex.Sample(gSamp, i.uv - o) * w[k];
    }
    return c;
}

// Blended additively onto the scene target, so this returns the glow term on
// its own -- adding the scene here as well would count it twice.
float4 PSComposite(QuadPS i) : SV_Target
{
    float3 glow = gGlow.Sample(gSamp, i.uv).rgb;
    return float4(glow * gPost.y, 0.0);
}
)HLSL";

struct MeshCB
{
    float wvp[16];
    float world[16];
    float baseColor[4];
    float params[4];
};
struct PostCB
{
    float texel[4];
    float post[4];
};

// Small helper so every compile reports the same way and a shader error is
// visible rather than a silently null pointer.
bool CompileShader(const char* entry, const char* target, ID3DBlob** blob)
{
    Microsoft::WRL::ComPtr<ID3DBlob> errors;
    UINT flags = D3DCOMPILE_OPTIMIZATION_LEVEL3;
#ifdef _DEBUG
    flags = D3DCOMPILE_DEBUG | D3DCOMPILE_SKIP_OPTIMIZATION;
#endif
    HRESULT hr = D3DCompile(kShaderSource, std::strlen(kShaderSource),
                            "BootAnimation.hlsl", nullptr, nullptr,
                            entry, target, flags, 0, blob, errors.GetAddressOf());
    if (FAILED(hr))
    {
        if (errors)
            ::OutputDebugStringA((const char*)errors->GetBufferPointer());
        return false;
    }
    return true;
}

} // namespace

BootAnimation::~BootAnimation() { Close(); }

void BootAnimation::Close()
{
    for (auto& m : m_mesh) { m.vb.Reset(); m.ib.Reset(); m.indexCount = 0; }
    m_sceneTex.Reset(); m_depthTex.Reset();
    m_sceneRTV.Reset(); m_sceneSRV.Reset(); m_depthDSV.Reset();
    for (int i = 0; i < 2; ++i) { m_glowTex[i].Reset(); m_glowRTV[i].Reset(); m_glowSRV[i].Reset(); }
    m_vsMesh.Reset(); m_vsQuad.Reset();
    m_psMesh.Reset(); m_psBlur.Reset(); m_psComposite.Reset();
    m_meshLayout.Reset(); m_cbMesh.Reset(); m_cbPost.Reset();
    m_sampler.Reset(); m_rsSolid.Reset(); m_rsBackdrop.Reset();
    m_dsWrite.Reset(); m_dsNone.Reset(); m_bsOpaque.Reset(); m_bsAdd.Reset();
    m_ready = false;
    m_finished = false;
}

bool BootAnimation::Open(ID3D11Device* device, const std::wstring& assetDir,
                         uint32_t width, uint32_t height)
{
    Close();
    if (!device || width == 0 || height == 0) return false;

    // The asset loaders take narrow paths; the directory arrives wide to match
    // how main builds it, so convert once here rather than templating them.
    std::string dir;
    if (!assetDir.empty())
    {
        int n = ::WideCharToMultiByte(CP_ACP, 0, assetDir.c_str(), (int)assetDir.size(),
                                      nullptr, 0, nullptr, nullptr);
        if (n <= 0) return false;
        dir.resize((size_t)n);
        ::WideCharToMultiByte(CP_ACP, 0, assetDir.c_str(), (int)assetDir.size(),
                              &dir[0], n, nullptr, nullptr);
    }

    if (!m_meshData.Load(dir + "\\meshes.bin")) return false;
    if (!m_frames.Load(dir + "\\frames.bin"))   return false;
    if (m_meshData.Count() < 3)                 return false;

    m_width  = width;
    m_height = height;
    m_glowW  = width  / 4 ? width  / 4 : 1;
    m_glowH  = height / 4 ? height / 4 : 1;

    if (!CreateShaders(device)) return false;
    if (!CreateTargets(device)) return false;
    if (!CreateMeshes(device))  return false;
    if (!CreateStates(device))  return false;

    m_ready = true;
    m_finished = false;
    return true;
}

bool BootAnimation::CreateShaders(ID3D11Device* device)
{
    Microsoft::WRL::ComPtr<ID3DBlob> vsMesh, vsQuad, psMesh, psBlur, psComp;
    if (!CompileShader("VSMesh",      "vs_5_0", vsMesh.GetAddressOf())) return false;
    if (!CompileShader("VSQuad",      "vs_5_0", vsQuad.GetAddressOf())) return false;
    if (!CompileShader("PSMesh",      "ps_5_0", psMesh.GetAddressOf())) return false;
    if (!CompileShader("PSBlur",      "ps_5_0", psBlur.GetAddressOf())) return false;
    if (!CompileShader("PSComposite", "ps_5_0", psComp.GetAddressOf())) return false;

    if (FAILED(device->CreateVertexShader(vsMesh->GetBufferPointer(), vsMesh->GetBufferSize(), nullptr, &m_vsMesh))) return false;
    if (FAILED(device->CreateVertexShader(vsQuad->GetBufferPointer(), vsQuad->GetBufferSize(), nullptr, &m_vsQuad))) return false;
    if (FAILED(device->CreatePixelShader (psMesh->GetBufferPointer(), psMesh->GetBufferSize(), nullptr, &m_psMesh))) return false;
    if (FAILED(device->CreatePixelShader (psBlur->GetBufferPointer(), psBlur->GetBufferSize(), nullptr, &m_psBlur))) return false;
    if (FAILED(device->CreatePixelShader (psComp->GetBufferPointer(), psComp->GetBufferSize(), nullptr, &m_psComposite))) return false;

    const D3D11_INPUT_ELEMENT_DESC layout[] =
    {
        { "POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0,  0, D3D11_INPUT_PER_VERTEX_DATA, 0 },
        { "NORMAL",   0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0 },
        { "TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT,    0, 24, D3D11_INPUT_PER_VERTEX_DATA, 0 },
    };
    if (FAILED(device->CreateInputLayout(layout, 3, vsMesh->GetBufferPointer(),
                                         vsMesh->GetBufferSize(), &m_meshLayout))) return false;

    D3D11_BUFFER_DESC cb = {};
    cb.Usage = D3D11_USAGE_DYNAMIC;
    cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    cb.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    cb.ByteWidth = sizeof(MeshCB);
    if (FAILED(device->CreateBuffer(&cb, nullptr, &m_cbMesh))) return false;
    cb.ByteWidth = sizeof(PostCB);
    if (FAILED(device->CreateBuffer(&cb, nullptr, &m_cbPost))) return false;
    return true;
}

bool BootAnimation::CreateTargets(ID3D11Device* device)
{
    auto makeRT = [&](uint32_t w, uint32_t h, DXGI_FORMAT fmt,
                      ID3D11Texture2D** tex, ID3D11RenderTargetView** rtv,
                      ID3D11ShaderResourceView** srv) -> bool
    {
        D3D11_TEXTURE2D_DESC d = {};
        d.Width = w; d.Height = h; d.MipLevels = 1; d.ArraySize = 1;
        d.Format = fmt; d.SampleDesc.Count = 1;
        d.Usage = D3D11_USAGE_DEFAULT;
        d.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
        if (FAILED(device->CreateTexture2D(&d, nullptr, tex))) return false;
        if (FAILED(device->CreateRenderTargetView(*tex, nullptr, rtv))) return false;
        if (FAILED(device->CreateShaderResourceView(*tex, nullptr, srv))) return false;
        return true;
    };

    // Half-float colour: the glow sums several bright passes and 8-bit clips
    // the highlights into flat white before the composite gets to them.
    if (!makeRT(m_width, m_height, DXGI_FORMAT_R16G16B16A16_FLOAT,
                &m_sceneTex, &m_sceneRTV, &m_sceneSRV)) return false;
    for (int i = 0; i < 2; ++i)
        if (!makeRT(m_glowW, m_glowH, DXGI_FORMAT_R16G16B16A16_FLOAT,
                    &m_glowTex[i], &m_glowRTV[i], &m_glowSRV[i])) return false;

    D3D11_TEXTURE2D_DESC dd = {};
    dd.Width = m_width; dd.Height = m_height; dd.MipLevels = 1; dd.ArraySize = 1;
    dd.Format = DXGI_FORMAT_D32_FLOAT; dd.SampleDesc.Count = 1;
    dd.Usage = D3D11_USAGE_DEFAULT; dd.BindFlags = D3D11_BIND_DEPTH_STENCIL;
    if (FAILED(device->CreateTexture2D(&dd, nullptr, &m_depthTex))) return false;
    if (FAILED(device->CreateDepthStencilView(m_depthTex.Get(), nullptr, &m_depthDSV))) return false;
    return true;
}

bool BootAnimation::CreateMeshes(ID3D11Device* device)
{
    for (size_t i = 0; i < 3 && i < m_meshData.Count(); ++i)
    {
        const BootMeshes::Mesh& src = m_meshData.At(i);
        if (src.vertices.empty() || src.indices.empty()) return false;

        D3D11_BUFFER_DESC bd = {};
        D3D11_SUBRESOURCE_DATA sd = {};

        bd.Usage = D3D11_USAGE_IMMUTABLE;
        bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
        bd.ByteWidth = (UINT)(src.vertices.size() * sizeof(BootMeshes::Vertex));
        sd.pSysMem = src.vertices.data();
        if (FAILED(device->CreateBuffer(&bd, &sd, &m_mesh[i].vb))) return false;

        bd.BindFlags = D3D11_BIND_INDEX_BUFFER;
        bd.ByteWidth = (UINT)(src.indices.size() * sizeof(uint16_t));
        sd.pSysMem = src.indices.data();
        if (FAILED(device->CreateBuffer(&bd, &sd, &m_mesh[i].ib))) return false;

        m_mesh[i].indexCount = (uint32_t)src.indices.size();
    }
    return true;
}

bool BootAnimation::CreateStates(ID3D11Device* device)
{
    D3D11_SAMPLER_DESC sd = {};
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    if (FAILED(device->CreateSamplerState(&sd, &m_sampler))) return false;

    D3D11_RASTERIZER_DESC rd = {};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;     // strips with degenerates, both windings
    rd.DepthClipEnable = TRUE;
    if (FAILED(device->CreateRasterizerState(&rd, &m_rsSolid))) return false;
    rd.DepthClipEnable = FALSE;        // the backdrop sphere encloses the camera
    if (FAILED(device->CreateRasterizerState(&rd, &m_rsBackdrop))) return false;

    D3D11_DEPTH_STENCIL_DESC ds = {};
    ds.DepthEnable = TRUE;
    ds.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
    ds.DepthFunc = D3D11_COMPARISON_LESS_EQUAL;
    if (FAILED(device->CreateDepthStencilState(&ds, &m_dsWrite))) return false;
    ds.DepthEnable = FALSE;
    ds.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
    if (FAILED(device->CreateDepthStencilState(&ds, &m_dsNone))) return false;

    D3D11_BLEND_DESC bd = {};
    bd.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    if (FAILED(device->CreateBlendState(&bd, &m_bsOpaque))) return false;

    bd.RenderTarget[0].BlendEnable = TRUE;
    bd.RenderTarget[0].SrcBlend  = D3D11_BLEND_ONE;
    bd.RenderTarget[0].DestBlend = D3D11_BLEND_ONE;
    bd.RenderTarget[0].BlendOp   = D3D11_BLEND_OP_ADD;
    bd.RenderTarget[0].SrcBlendAlpha  = D3D11_BLEND_ONE;
    bd.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_ONE;
    bd.RenderTarget[0].BlendOpAlpha   = D3D11_BLEND_OP_ADD;
    if (FAILED(device->CreateBlendState(&bd, &m_bsAdd))) return false;
    return true;
}

bool BootAnimation::Update(ID3D11DeviceContext* ctx, float elapsedSeconds)
{
    if (!m_ready || !ctx) return false;
    if (elapsedSeconds >= Duration()) { m_finished = true; return false; }

    const float* mats = m_frames.Matrices(elapsedSeconds);
    if (!mats) { m_finished = true; return false; }

    DrawScene(ctx, mats, m_frames.OrbVisible(elapsedSeconds));
    DrawGlow(ctx);
    Composite(ctx);
    return true;
}

void BootAnimation::DrawScene(ID3D11DeviceContext* ctx, const float* mats, bool orbVisible)
{
    const float black[4] = { 0.0f, 0.0f, 0.0f, 1.0f };
    ctx->ClearRenderTargetView(m_sceneRTV.Get(), black);
    ctx->ClearDepthStencilView(m_depthDSV.Get(), D3D11_CLEAR_DEPTH, 1.0f, 0);

    ID3D11RenderTargetView* rtv = m_sceneRTV.Get();
    ctx->OMSetRenderTargets(1, &rtv, m_depthDSV.Get());

    D3D11_VIEWPORT vp = {};
    vp.Width = (float)m_width; vp.Height = (float)m_height; vp.MaxDepth = 1.0f;
    ctx->RSSetViewports(1, &vp);

    ctx->IASetInputLayout(m_meshLayout.Get());
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    ctx->VSSetShader(m_vsMesh.Get(), nullptr, 0);
    ctx->PSSetShader(m_psMesh.Get(), nullptr, 0);
    ctx->OMSetBlendState(m_bsOpaque.Get(), nullptr, 0xFFFFFFFF);
    ctx->OMSetDepthStencilState(m_dsWrite.Get(), 0);

    ctx->RSSetState(m_rsSolid.Get());

    ID3D11Buffer* cb = m_cbMesh.Get();
    ctx->VSSetConstantBuffers(0, 1, &cb);
    ctx->PSSetConstantBuffers(0, 1, &cb);

    auto submit = [&](int meshIndex, const float* wvp, const float* world,
                      const float rgb[3], float emissive, float rimPow,
                      float rimStrength, float fade)
    {
        if (meshIndex < 0 || meshIndex > 2 || !m_mesh[meshIndex].vb) return;
        D3D11_MAPPED_SUBRESOURCE map;
        if (FAILED(ctx->Map(m_cbMesh.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &map))) return;
        MeshCB* c = (MeshCB*)map.pData;
        std::memcpy(c->wvp,   wvp,   16 * sizeof(float));
        std::memcpy(c->world, world, 16 * sizeof(float));
        c->baseColor[0] = rgb[0]; c->baseColor[1] = rgb[1];
        c->baseColor[2] = rgb[2]; c->baseColor[3] = emissive;
        c->params[0] = rimPow; c->params[1] = rimStrength; c->params[2] = fade; c->params[3] = 0.0f;
        ctx->Unmap(m_cbMesh.Get(), 0);

        UINT stride = sizeof(BootMeshes::Vertex), offset = 0;
        ID3D11Buffer* vb = m_mesh[meshIndex].vb.Get();
        ctx->IASetVertexBuffers(0, 1, &vb, &stride, &offset);
        ctx->IASetIndexBuffer(m_mesh[meshIndex].ib.Get(), DXGI_FORMAT_R16_UINT, 0);
        ctx->DrawIndexed(m_mesh[meshIndex].indexCount, 0, 0);
    };

    // The backdrop is mesh 0 again, drawn at ~160x radius around the camera:
    // the title's own second transform, so its motion tracks the orb's.
    // Whether it is present depends on the frame, and an identity matrix is
    // how the capture records "not submitted".
    const float* backWvp   = mats + 32;
    const float* backWorld = mats + 48;
    static const float kIdent[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
    if (std::memcmp(backWvp, kIdent, sizeof kIdent) != 0)
    {
        const float backColor[3] = { 0.020f, 0.055f, 0.014f };
        ctx->RSSetState(m_rsBackdrop.Get());
        ctx->OMSetDepthStencilState(m_dsNone.Get(), 0);
        submit(0, backWvp, backWorld, backColor, 0.35f, 1.0f, 0.0f, 1.0f);
        ctx->OMSetDepthStencilState(m_dsWrite.Get(), 0);
    }

    if (!orbVisible) return;

    ctx->RSSetState(m_rsSolid.Get());
    const float* wvp   = mats + 0;
    const float* world = mats + 16;

    // Colours read out of the title's own constant pool: the body sits around
    // (80,160,40)/255 and the X inlay and its trim run up through the pale
    // yellow-green the procedural textures are built from.
    const float kBody[3] = { 0.314f, 0.627f, 0.157f };
    const float kInlay[3] = { 0.596f, 0.847f, 0.157f };
    const float kTrim[3] = { 0.937f, 0.980f, 0.443f };

    submit(0, wvp, world, kBody,  0.00f, 2.5f, 0.35f, 1.0f);   // sphere
    submit(1, wvp, world, kInlay, 0.25f, 2.0f, 0.30f, 1.0f);   // X inlay
    submit(2, wvp, world, kTrim,  0.90f, 1.5f, 0.20f, 1.0f);   // X edge trim
}

void BootAnimation::DrawGlow(ID3D11DeviceContext* ctx)
{
    ID3D11ShaderResourceView* nullSRV[2] = { nullptr, nullptr };
    ctx->OMSetRenderTargets(0, nullptr, nullptr);

    D3D11_VIEWPORT vp = {};
    vp.Width = (float)m_glowW; vp.Height = (float)m_glowH; vp.MaxDepth = 1.0f;
    ctx->RSSetViewports(1, &vp);

    ctx->IASetInputLayout(nullptr);
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ctx->VSSetShader(m_vsQuad.Get(), nullptr, 0);
    ctx->PSSetShader(m_psBlur.Get(), nullptr, 0);
    ctx->OMSetDepthStencilState(m_dsNone.Get(), 0);
    ctx->OMSetBlendState(m_bsOpaque.Get(), nullptr, 0xFFFFFFFF);
    ctx->RSSetState(m_rsSolid.Get());

    ID3D11SamplerState* samp = m_sampler.Get();
    ctx->PSSetSamplers(0, 1, &samp);
    ID3D11Buffer* cb = m_cbPost.Get();
    ctx->PSSetConstantBuffers(1, 1, &cb);

    auto pass = [&](ID3D11ShaderResourceView* src, ID3D11RenderTargetView* dst,
                    float srcW, float srcH, float dir)
    {
        ctx->PSSetShaderResources(0, 2, nullSRV);
        ID3D11RenderTargetView* rtv = dst;
        ctx->OMSetRenderTargets(1, &rtv, nullptr);

        D3D11_MAPPED_SUBRESOURCE map;
        if (SUCCEEDED(ctx->Map(m_cbPost.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &map)))
        {
            PostCB* c = (PostCB*)map.pData;
            c->texel[0] = 1.0f / srcW; c->texel[1] = 1.0f / srcH;
            c->texel[2] = c->texel[3] = 0.0f;
            c->post[0] = dir; c->post[1] = 1.0f; c->post[2] = c->post[3] = 0.0f;
            ctx->Unmap(m_cbPost.Get(), 0);
        }
        ctx->PSSetShaderResources(0, 1, &src);
        ctx->Draw(3, 0);
    };

    // Downsample straight into the first glow target by blurring horizontally
    // from the full-resolution scene, then ping-pong for the vertical pass and
    // one more round to widen it. The console does four such passes per glow
    // level; two per axis lands in the same place visually at a quarter of
    // the cost.
    pass(m_sceneSRV.Get(),  m_glowRTV[0].Get(), (float)m_width, (float)m_height, 0.0f);
    pass(m_glowSRV[0].Get(), m_glowRTV[1].Get(), (float)m_glowW, (float)m_glowH, 1.0f);
    pass(m_glowSRV[1].Get(), m_glowRTV[0].Get(), (float)m_glowW, (float)m_glowH, 0.0f);
    pass(m_glowSRV[0].Get(), m_glowRTV[1].Get(), (float)m_glowW, (float)m_glowH, 1.0f);

    ctx->PSSetShaderResources(0, 2, nullSRV);
}

void BootAnimation::Composite(ID3D11DeviceContext* ctx)
{
    // The glow goes back onto the scene target, which is also what gets handed
    // out as the frame SRV -- so the composite has to unbind it as a source
    // before binding it as a target, or the runtime drops the write.
    ID3D11ShaderResourceView* nullSRV[2] = { nullptr, nullptr };
    ctx->PSSetShaderResources(0, 2, nullSRV);

    ID3D11RenderTargetView* rtv = m_sceneRTV.Get();
    ctx->OMSetRenderTargets(1, &rtv, nullptr);

    D3D11_VIEWPORT vp = {};
    vp.Width = (float)m_width; vp.Height = (float)m_height; vp.MaxDepth = 1.0f;
    ctx->RSSetViewports(1, &vp);

    ctx->VSSetShader(m_vsQuad.Get(), nullptr, 0);
    ctx->PSSetShader(m_psComposite.Get(), nullptr, 0);
    ctx->OMSetBlendState(m_bsAdd.Get(), nullptr, 0xFFFFFFFF);
    ctx->OMSetDepthStencilState(m_dsNone.Get(), 0);
    ctx->IASetInputLayout(nullptr);
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);

    D3D11_MAPPED_SUBRESOURCE map;
    if (SUCCEEDED(ctx->Map(m_cbPost.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &map)))
    {
        PostCB* c = (PostCB*)map.pData;
        c->texel[0] = 1.0f / (float)m_width; c->texel[1] = 1.0f / (float)m_height;
        c->texel[2] = c->texel[3] = 0.0f;
        c->post[0] = 0.0f;
        c->post[1] = 0.85f;                       // glow gain
        c->post[2] = c->post[3] = 0.0f;
        ctx->Unmap(m_cbPost.Get(), 0);
    }

    // Only the blurred glow is read; it goes on t1 to match gGlow.
    ID3D11ShaderResourceView* glow = m_glowSRV[1].Get();
    ctx->PSSetShaderResources(1, 1, &glow);
    ID3D11Buffer* cb = m_cbPost.Get();
    ctx->PSSetConstantBuffers(1, 1, &cb);
    ID3D11SamplerState* samp = m_sampler.Get();
    ctx->PSSetSamplers(0, 1, &samp);

    ctx->Draw(3, 0);

    ctx->PSSetShaderResources(0, 2, nullSRV);
    ctx->OMSetRenderTargets(0, nullptr, nullptr);
    ctx->OMSetBlendState(m_bsOpaque.Get(), nullptr, 0xFFFFFFFF);
}
