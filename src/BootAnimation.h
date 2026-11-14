#pragma once

#include <d3d11.h>
#include <wrl/client.h>

#include <cstdint>
#include <string>

#include "BootAnimAssets.h"

// Renders the Xbox 360 boot animation natively instead of playing a video of
// it, and hands back the result as a shader resource view so it can go
// through the same QuadRenderer path the .mp4 did.
//
// The interface deliberately mirrors VideoPlayer -- Open / Update /
// GetFrameSRV / IsFinished / Close -- so swapping one for the other in main
// is a type change and nothing else.
//
// Where the data comes from: assets\bootanim\ holds the meshes and the
// per-frame matrices extracted from bootanim.xex itself (see
// tools\bootanim_extract). Nothing here is re-authored or eyeballed; the
// geometry is the console's geometry and the motion is the console's motion,
// captured by running its own code.
//
// What is *not* the console's: the lighting and the glow. The original uses a
// hand-written pixel shader with five directional and two spot lights, a
// shadow map, and a seven-texture bloom composite with its own response
// curve. That is reproduced here in spirit -- a hemispheric key light plus a
// rim term, and a separable blur composited additively -- not instruction for
// instruction. It reads as the boot animation; it is not a pixel match, and
// the lighting constants are the obvious place to tune it further.
//
// Cost at 1080p is a few hundred microseconds: 11k triangles, four small blur
// passes, one composite. The 78 MB of H.264 it replaces also stops being
// decoded, so this is cheaper than what it replaces on every axis.
class BootAnimation
{
public:
    BootAnimation() = default;
    ~BootAnimation();
    BootAnimation(const BootAnimation&) = delete;
    BootAnimation& operator=(const BootAnimation&) = delete;

    // `assetDir` is the directory holding meshes.bin and frames.bin, i.e.
    // <exe>\assets\bootanim. Returns false if the data or any device object
    // is missing, which the caller should treat as "no boot animation" and
    // skip straight to the dashboard -- the same way a missing video was.
    bool Open(ID3D11Device* device, const std::wstring& assetDir,
              uint32_t width, uint32_t height);

    // Renders the frame belonging at `elapsedSeconds`. Returns false once the
    // animation is over, matching VideoPlayer::Update's contract, so the
    // existing `if (skip || !boot.Update(...))` in main needs no change.
    bool Update(ID3D11DeviceContext* context, float elapsedSeconds);

    // Valid after the first successful Update.
    ID3D11ShaderResourceView* GetFrameSRV() const { return m_sceneSRV.Get(); }

    bool IsOpen()     const { return m_ready; }
    bool IsFinished() const { return m_finished; }

    uint32_t GetWidth()  const { return m_width; }
    uint32_t GetHeight() const { return m_height; }

    float Duration() const { return m_frames.IsLoaded() ? m_frames.Duration() : 0.0f; }

    void Close();

private:
    template <typename T> using ComPtr = Microsoft::WRL::ComPtr<T>;

    struct GpuMesh
    {
        ComPtr<ID3D11Buffer> vb, ib;
        uint32_t indexCount = 0;
    };

    bool CreateShaders(ID3D11Device* device);
    bool CreateTargets(ID3D11Device* device);
    bool CreateMeshes(ID3D11Device* device);
    bool CreateStates(ID3D11Device* device);

    void DrawScene(ID3D11DeviceContext* ctx, const float* mats, bool orbVisible);
    void DrawGlow(ID3D11DeviceContext* ctx);
    void Composite(ID3D11DeviceContext* ctx);

    // Assets
    BootMeshes m_meshData;
    BootFrames m_frames;

    // Geometry: sphere, X inlay, X trim, in the order the title submits them.
    // The fullscreen passes need no buffer -- VSQuad builds its triangle from
    // SV_VertexID.
    GpuMesh m_mesh[3];

    // Full-resolution scene colour + depth, and the quarter-resolution pair
    // the blur ping-pongs between.
    ComPtr<ID3D11Texture2D>          m_sceneTex, m_depthTex, m_glowTex[2];
    ComPtr<ID3D11RenderTargetView>   m_sceneRTV, m_glowRTV[2];
    ComPtr<ID3D11ShaderResourceView> m_sceneSRV, m_glowSRV[2];
    ComPtr<ID3D11DepthStencilView>   m_depthDSV;

    ComPtr<ID3D11VertexShader> m_vsMesh, m_vsQuad;
    ComPtr<ID3D11PixelShader>  m_psMesh, m_psBlur, m_psComposite;
    ComPtr<ID3D11InputLayout>  m_meshLayout;
    ComPtr<ID3D11Buffer>       m_cbMesh, m_cbPost;

    ComPtr<ID3D11SamplerState>      m_sampler;
    ComPtr<ID3D11RasterizerState>   m_rsSolid, m_rsBackdrop;
    ComPtr<ID3D11DepthStencilState> m_dsWrite, m_dsNone;
    ComPtr<ID3D11BlendState>        m_bsOpaque, m_bsAdd;

    uint32_t m_width = 0, m_height = 0;
    uint32_t m_glowW = 0, m_glowH = 0;
    bool     m_ready = false;
    bool     m_finished = false;
};
