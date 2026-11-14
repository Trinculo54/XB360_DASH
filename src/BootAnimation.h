#pragma once

#include <d3d11.h>
#include <wrl/client.h>

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

// The Xbox 360 boot animation, drawn live by replaying the title's own
// Direct3D calls.
//
// Nothing here knows what the animation is. assets\bootanim\trace.bin is a
// recording of everything bootanim.xex hands to its D3D, frame by frame --
// every draw with its vertices, its shaders' constants, its textures, its
// render states, every render-target switch, clear and resolve -- made by
// running the title's own code (tools\bootanim_extract\capture.cpp). The
// shaders are the title's own too, translated to HLSL and compiled
// (tools\bootanim_extract\shaders.py -> shaderNN.vs.cso / .ps.cso). This class
// issues those calls again on Direct3D 11: the ribbons, the sphere, the
// wordmark, the floor's reflection, the glow chain and the flash are whatever
// the title drew, lit however it lit them.
//
// What is this file's rather than the title's is the mapping of one API onto
// the other, all of it in BootAnimation.cpp where each choice is noted: the
// render states' and samplers' numbers, Direct3D 9's half-pixel rule, EDRAM
// surfaces as render targets (multisampled where the title's are) with a
// resolve as a resolve, and the console's tiled rendering of the scene as one
// surface the size of the picture.
//
// The interface is the one the old renderer had, and VideoPlayer before it:
// Open / Update / GetFrameSRV / IsFinished / Close.
class BootAnimation
{
public:
    BootAnimation() = default;
    ~BootAnimation();
    BootAnimation(const BootAnimation&) = delete;
    BootAnimation& operator=(const BootAnimation&) = delete;

    // `assetDir` holds trace.bin and the compiled shaders, i.e.
    // <exe>\assets\bootanim. False if anything is missing, which the caller
    // treats as "no boot animation". width/height are not used: the title
    // renders 1280x720 and that is what is replayed.
    bool Open(ID3D11Device* device, const std::wstring& assetDir, uint32_t width, uint32_t height);

    // Renders the frame belonging at `elapsedSeconds`. False once it is over.
    bool Update(ID3D11DeviceContext* context, float elapsedSeconds);

    // The front buffer the title last swapped to. Valid after the first successful Update.
    ID3D11ShaderResourceView* GetFrameSRV() const { return m_frameSRV.Get(); }

    bool IsOpen()     const { return m_ready; }
    bool IsFinished() const { return m_finished; }

    uint32_t GetWidth()  const { return 1280; }
    uint32_t GetHeight() const { return 720; }

    float Duration() const { return m_fps > 0.0f ? (float)m_frames.size() / m_fps : 0.0f; }

    void Close();

private:
    template <typename T> using ComPtr = Microsoft::WRL::ComPtr<T>;

    struct Texture
    {
        uint32_t w = 0, h = 0, format = 0;          // format: the Xbox 360's D3DFORMAT word
        bool target = false;                        // something is resolved into it
        ComPtr<ID3D11Texture2D> texture;
        ComPtr<ID3D11ShaderResourceView> srv;
        ComPtr<ID3D11RenderTargetView> rtv;         // targets only
    };
    struct Surface
    {
        uint32_t w = 0, h = 0, format = 0, samples = 1;
        ComPtr<ID3D11Texture2D> color, depth;
        ComPtr<ID3D11RenderTargetView> rtv;
        ComPtr<ID3D11DepthStencilView> dsv;
    };
    struct Frame { const uint32_t* words = nullptr; uint32_t count = 0; };

    bool LoadTrace(ID3D11Device* device, const std::wstring& path);
    bool LoadShaders(ID3D11Device* device, const std::wstring& dir);
    void Draw(ID3D11DeviceContext* ctx, const uint32_t* d);
    void Bind(ID3D11DeviceContext* ctx);
    void MakeOpaque(ID3D11DeviceContext* ctx, const Texture& t);
    ID3D11InputLayout* Layout(uint32_t decl, uint32_t vs);
    ID3D11Buffer* Static(std::unordered_map<uint32_t, ComPtr<ID3D11Buffer>>& cache, uint32_t offset, uint32_t size, UINT bind);
    uint32_t Transient(ID3D11DeviceContext* ctx, ID3D11Buffer* buffer, uint32_t& cursor, uint32_t capacity,
                       const void* data, uint32_t size, uint32_t align);

    ComPtr<ID3D11Device> m_device;

    // The recording, whole, and where its parts are in it.
    std::vector<uint8_t> m_file;
    const uint8_t* m_pool = nullptr;
    uint32_t m_poolSize = 0;
    uint32_t m_vsRegs = 0, m_psRegs = 0, m_stateCount = 0;
    float m_fps = 0.0f;
    std::vector<Frame> m_frames;
    std::vector<Texture> m_textures;
    std::vector<Surface> m_surfaces;
    std::vector<std::vector<uint32_t>> m_decls;     // each a declaration's words, as the title gave them
    int m_target = -1;

    // The title's 17 shaders, by its own numbering; each is a vertex or a pixel shader.
    static constexpr int kShaders = 17;
    ComPtr<ID3D11VertexShader> m_vs[kShaders];
    ComPtr<ID3D11PixelShader>  m_ps[kShaders];
    std::vector<uint8_t>       m_vsCode[kShaders];
    std::unordered_map<uint32_t, ComPtr<ID3D11InputLayout>> m_layouts;

    // Geometry: what is the same bytes every frame is uploaded once, the rest
    // (a ribbon, a quad) goes through a buffer that is refilled as it goes.
    std::unordered_map<uint32_t, ComPtr<ID3D11Buffer>> m_staticVB, m_staticIB;
    std::unordered_map<uint32_t, uint32_t> m_placed;        // pool offset -> where in m_dynamicVB, this frame
    ComPtr<ID3D11Buffer> m_dynamicVB, m_dynamicIB, m_zeroVB;
    uint32_t m_vbCursor = 0, m_ibCursor = 0;

    std::vector<float> m_vsConst, m_psConst;
    ComPtr<ID3D11Buffer> m_cbVs, m_cbPs, m_cbShared;
    std::unordered_map<uint32_t, ComPtr<ID3D11SamplerState>> m_samplers;
    std::unordered_map<uint64_t, ComPtr<ID3D11BlendState>> m_blends;
    std::unordered_map<uint32_t, ComPtr<ID3D11DepthStencilState>> m_depths;
    std::unordered_map<uint32_t, ComPtr<ID3D11RasterizerState>> m_rasters;

    // MakeOpaque's: one triangle that writes alpha 1 and nothing else.
    ComPtr<ID3D11VertexShader> m_opaqueVS;
    ComPtr<ID3D11PixelShader>  m_opaquePS;
    ComPtr<ID3D11BlendState>   m_alphaOnly;

    ComPtr<ID3D11ShaderResourceView> m_frameSRV;
    bool m_ready = false;
    bool m_finished = false;
};
