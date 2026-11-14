#include "QuadRenderer.h"

#include <cmath>
#include "Shaders.h"

#include <d3dcompiler.h>
#include <cstring>

using Microsoft::WRL::ComPtr;

namespace
{
    // Compiles one entry point from Shaders.h's shared HLSL source at
    // runtime -- avoids an offline-shader-compile build step (see the
    // comment atop Shaders.h). Errors are swallowed into `outBlob == null`;
    // callers treat that as fatal-to-Initialize since a missing shader
    // means nothing can be drawn at all.
    HRESULT CompileShader(const char* entryPoint, const char* target, ID3DBlob** outBlob)
    {
        ComPtr<ID3DBlob> errors;
        UINT flags = D3DCOMPILE_ENABLE_STRICTNESS;
#ifdef _DEBUG
        flags |= D3DCOMPILE_DEBUG | D3DCOMPILE_SKIP_OPTIMIZATION;
#endif
        HRESULT hr = D3DCompile(
            kShaderSource, std::strlen(kShaderSource),
            "QuadRenderer.hlsl", nullptr, nullptr,
            entryPoint, target, flags, 0, outBlob, &errors);
        if (FAILED(hr) && errors)
        {
            OutputDebugStringA(reinterpret_cast<const char*>(errors->GetBufferPointer()));
        }
        return hr;
    }

    void ColorToFloat4(float a, float r, float g, float b, float out[4])
    {
        out[0] = r / 255.0f;
        out[1] = g / 255.0f;
        out[2] = b / 255.0f;
        out[3] = a / 255.0f;
    }
}

bool QuadRenderer::Initialize(ID3D11Device* device, ID3D11DeviceContext* context)
{
    m_device = device;
    m_context = context;

    // --- Shaders ---
    ComPtr<ID3DBlob> vsBlob, psSolidBlob, psImageBlob, psTextBlob, psGradientBlob;
    if (FAILED(CompileShader("VSMain", "vs_4_0", &vsBlob))) return false;
    if (FAILED(CompileShader("PSSolid", "ps_4_0", &psSolidBlob))) return false;
    if (FAILED(CompileShader("PSImage", "ps_4_0", &psImageBlob))) return false;
    if (FAILED(CompileShader("PSText", "ps_4_0", &psTextBlob))) return false;
    if (FAILED(CompileShader("PSGradient", "ps_4_0", &psGradientBlob))) return false;

    if (FAILED(device->CreateVertexShader(vsBlob->GetBufferPointer(), vsBlob->GetBufferSize(), nullptr, &m_vs))) return false;
    if (FAILED(device->CreatePixelShader(psSolidBlob->GetBufferPointer(), psSolidBlob->GetBufferSize(), nullptr, &m_psSolid))) return false;
    if (FAILED(device->CreatePixelShader(psImageBlob->GetBufferPointer(), psImageBlob->GetBufferSize(), nullptr, &m_psImage))) return false;
    if (FAILED(device->CreatePixelShader(psTextBlob->GetBufferPointer(), psTextBlob->GetBufferSize(), nullptr, &m_psText))) return false;
    if (FAILED(device->CreatePixelShader(psGradientBlob->GetBufferPointer(), psGradientBlob->GetBufferSize(), nullptr, &m_psGradient))) return false;

    // --- Input layout: matches Shaders.h's VSInput (POSITION, TEXCOORD0), both float2 ---
    D3D11_INPUT_ELEMENT_DESC layoutDesc[] = {
        { "POSITION", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0 },
        { "TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 8, D3D11_INPUT_PER_VERTEX_DATA, 0 },
    };
    if (FAILED(device->CreateInputLayout(layoutDesc, 2, vsBlob->GetBufferPointer(), vsBlob->GetBufferSize(), &m_inputLayout))) return false;

    // --- Vertex buffer: 4 verts, dynamic, rewritten via Map/Unmap for every quad drawn ---
    D3D11_BUFFER_DESC vbDesc = {};
    vbDesc.ByteWidth = sizeof(Vertex) * 4;
    vbDesc.Usage = D3D11_USAGE_DYNAMIC;
    vbDesc.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    vbDesc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    if (FAILED(device->CreateBuffer(&vbDesc, nullptr, &m_vertexBuffer))) return false;

    // --- Index buffer: static, shared by every quad (two triangles, CW) ---
    const uint16_t indices[] = { 0, 1, 2, 2, 1, 3 };
    D3D11_BUFFER_DESC ibDesc = {};
    ibDesc.ByteWidth = sizeof(indices);
    ibDesc.Usage = D3D11_USAGE_IMMUTABLE;
    ibDesc.BindFlags = D3D11_BIND_INDEX_BUFFER;
    D3D11_SUBRESOURCE_DATA ibData = {};
    ibData.pSysMem = indices;
    if (FAILED(device->CreateBuffer(&ibDesc, &ibData, &m_indexBuffer))) return false;

    // --- Constant buffers ---
    auto makeDynamicCB = [&](UINT byteWidth, ComPtr<ID3D11Buffer>& outBuf) -> HRESULT
    {
        D3D11_BUFFER_DESC desc = {};
        desc.ByteWidth = byteWidth;
        desc.Usage = D3D11_USAGE_DYNAMIC;
        desc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        return device->CreateBuffer(&desc, nullptr, &outBuf);
    };
    if (FAILED(makeDynamicCB(sizeof(FrameCBData), m_frameCB))) return false;
    if (FAILED(makeDynamicCB(sizeof(DrawCBData), m_drawCB))) return false;
    if (FAILED(makeDynamicCB(sizeof(GradientCBData), m_gradientCB))) return false;

    // --- Blend state: standard straight-alpha over blend ---
    D3D11_BLEND_DESC blendDesc = {};
    blendDesc.RenderTarget[0].BlendEnable = TRUE;
    blendDesc.RenderTarget[0].SrcBlend = D3D11_BLEND_SRC_ALPHA;
    blendDesc.RenderTarget[0].DestBlend = D3D11_BLEND_INV_SRC_ALPHA;
    blendDesc.RenderTarget[0].BlendOp = D3D11_BLEND_OP_ADD;
    blendDesc.RenderTarget[0].SrcBlendAlpha = D3D11_BLEND_ONE;
    blendDesc.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_INV_SRC_ALPHA;
    blendDesc.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
    blendDesc.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    if (FAILED(device->CreateBlendState(&blendDesc, &m_blendState))) return false;

    // --- Sampler: linear filtering, clamp to edge (avoids seams at atlas/image borders) ---
    D3D11_SAMPLER_DESC sampDesc = {};
    sampDesc.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sampDesc.AddressU = D3D11_TEXTURE_ADDRESS_CLAMP;
    sampDesc.AddressV = D3D11_TEXTURE_ADDRESS_CLAMP;
    sampDesc.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sampDesc.ComparisonFunc = D3D11_COMPARISON_NEVER;
    sampDesc.MaxLOD = D3D11_FLOAT32_MAX;
    if (FAILED(device->CreateSamplerState(&sampDesc, &m_samplerState))) return false;

    // --- 1x1 white texture, bound for solid/gradient draws so PSSolid/PSGradient
    //     never need a "no texture" special case (they just don't sample it) ---
    {
        D3D11_TEXTURE2D_DESC texDesc = {};
        texDesc.Width = 1;
        texDesc.Height = 1;
        texDesc.MipLevels = 1;
        texDesc.ArraySize = 1;
        texDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        texDesc.SampleDesc.Count = 1;
        texDesc.Usage = D3D11_USAGE_IMMUTABLE;
        texDesc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        const uint32_t whitePixel = 0xFFFFFFFF;
        D3D11_SUBRESOURCE_DATA initData = {};
        initData.pSysMem = &whitePixel;
        initData.SysMemPitch = 4;
        if (FAILED(device->CreateTexture2D(&texDesc, &initData, &m_whiteTexture))) return false;
        if (FAILED(device->CreateShaderResourceView(m_whiteTexture.Get(), nullptr, &m_whiteSRV))) return false;
    }

    return true;
}

void QuadRenderer::BeginFrame(float screenWidth, float screenHeight)
{
    D3D11_MAPPED_SUBRESOURCE mapped;
    if (SUCCEEDED(m_context->Map(m_frameCB.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
    {
        FrameCBData data;
        data.ScreenSize[0] = screenWidth;
        data.ScreenSize[1] = screenHeight;
        m_canvasWidth = screenWidth;
        m_canvasHeight = screenHeight;
        // Identity by default, so a frame that never mentions transitions
        // draws exactly as it did before SetSceneTransform existed.
        m_sceneScale = 1.0f;
        m_sceneAlpha = 1.0f;
        m_sceneOffsetX = 0.0f;
        m_sceneOffsetY = 0.0f;
        m_clipEnabled = false;
        data._padFrame[0] = data._padFrame[1] = 0.0f;
        std::memcpy(mapped.pData, &data, sizeof(data));
        m_context->Unmap(m_frameCB.Get(), 0);
    }

    // Common per-frame state: shared vertex/index buffers, input layout,
    // sampler, and blend state don't change between draw kinds, so bind
    // them once here rather than redundantly in every Draw* call.
    UINT stride = sizeof(Vertex);
    UINT offset = 0;
    m_context->IASetInputLayout(m_inputLayout.Get());
    m_context->IASetVertexBuffers(0, 1, m_vertexBuffer.GetAddressOf(), &stride, &offset);
    m_context->IASetIndexBuffer(m_indexBuffer.Get(), DXGI_FORMAT_R16_UINT, 0);
    m_context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    m_context->VSSetShader(m_vs.Get(), nullptr, 0);
    m_context->VSSetConstantBuffers(0, 1, m_frameCB.GetAddressOf());
    ID3D11Buffer* psConstantBuffers[] = { m_drawCB.Get(), m_gradientCB.Get() };
    m_context->PSSetConstantBuffers(1, 2, psConstantBuffers);
    m_context->PSSetSamplers(0, 1, m_samplerState.GetAddressOf());

    float blendFactor[4] = { 0, 0, 0, 0 };
    m_context->OMSetBlendState(m_blendState.Get(), blendFactor, 0xFFFFFFFF);
}

void QuadRenderer::SetLetterboxViewport(float windowWidth, float windowHeight, float canvasWidth, float canvasHeight)
{
    if (canvasWidth <= 0.0f || canvasHeight <= 0.0f || windowWidth <= 0.0f || windowHeight <= 0.0f)
    {
        return; // degenerate size (e.g. minimized window) -- leave whatever viewport is already bound
    }

    // The largest scale that fits the whole canvas inside the window on
    // BOTH axes -- using the smaller of the two per-axis ratios is what
    // makes this "fit inside" (letterbox) rather than "cover" (crop).
    float scale = (windowWidth / canvasWidth < windowHeight / canvasHeight)
        ? (windowWidth / canvasWidth)
        : (windowHeight / canvasHeight);

    float scaledW = canvasWidth * scale;
    float scaledH = canvasHeight * scale;

    D3D11_VIEWPORT viewport = {};
    viewport.TopLeftX = (windowWidth - scaledW) / 2.0f;
    viewport.TopLeftY = (windowHeight - scaledH) / 2.0f;
    viewport.Width = scaledW;
    viewport.Height = scaledH;
    viewport.MinDepth = 0.0f;
    viewport.MaxDepth = 1.0f;
    m_context->RSSetViewports(1, &viewport);
}

void QuadRenderer::UpdateVertexBuffer(float x, float y, float w, float h, float u0, float v0, float u1, float v1,
    bool turned)
{
    // Layout: 0=top-left, 1=top-right, 2=bottom-left, 3=bottom-right --
    // matches the {0,1,2,2,1,3} index buffer (two CW triangles covering the quad).
    // Upright, u runs with x and v with y. Turned a quarter clockwise, the
    // texture's left edge is the quad's TOP and its bottom edge the quad's
    // LEFT: u runs with y, and v runs backwards with x.
    Vertex verts[4] = {
        { x,     y,     u0, turned ? v1 : v0 },
        { x + w, y,     turned ? u0 : u1, v0 },
        { x,     y + h, turned ? u1 : u0, v1 },
        { x + w, y + h, u1, turned ? v0 : v1 },
    };

    D3D11_MAPPED_SUBRESOURCE mapped;
    if (SUCCEEDED(m_context->Map(m_vertexBuffer.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
    {
        std::memcpy(mapped.pData, verts, sizeof(verts));
        m_context->Unmap(m_vertexBuffer.Get(), 0);
    }
}

void QuadRenderer::SetSceneTransform(float scale, float alpha, float offsetX, float offsetY)
{
    m_sceneScale = scale;
    m_sceneAlpha = alpha;
    m_sceneOffsetX = offsetX;
    m_sceneOffsetY = offsetY;
}

void QuadRenderer::SetClip(float x, float y, float w, float h)
{
    m_clipEnabled = true;
    m_clipX0 = x;
    m_clipY0 = y;
    m_clipX1 = x + w;
    m_clipY1 = y + h;
}

void QuadRenderer::DrawQuad(float x, float y, float w, float h, float u0, float v0, float u1, float v1,
    ID3D11PixelShader* ps, ID3D11ShaderResourceView* srv,
    float a, float r, float g, float b, bool isEllipse)
{
    // A quarter turn clockwise about the turn origin, first, in the canvas
    // space the caller laid the quad out in: what was the quad's left edge
    // becomes its top, and its bottom edge its left.
    const bool turned = m_turn;
    if (turned)
    {
        const float turnedX = m_turnX - ((y - m_turnY) + h);
        const float turnedY = m_turnY + (x - m_turnX);
        x = turnedX;
        y = turnedY;
        const float swap = w;
        w = h;
        h = swap;
    }

    // The scene transform lands here, once, rather than in each Draw* entry
    // point -- so it applies uniformly to rects, images, gradients and every
    // individual glyph quad, and a caller can't forget it. Scaling about the
    // canvas centre keeps a scene growing toward the viewer.
    if (m_sceneScale != 1.0f)
    {
        const float cx = m_canvasWidth * 0.5f;
        const float cy = m_canvasHeight * 0.5f;
        x = cx + (x - cx) * m_sceneScale;
        y = cy + (y - cy) * m_sceneScale;
        w *= m_sceneScale;
        h *= m_sceneScale;
    }
    // After the scale, so a translating-and-scaling layer moves by the
    // offset it was given rather than by the offset times the scale.
    x += m_sceneOffsetX;
    y += m_sceneOffsetY;
    a *= m_sceneAlpha;

    if (w <= 0.0f || h <= 0.0f || a <= 0.0f)
    {
        return; // nothing visible -- skip the draw call entirely
    }

    if (m_clipEnabled)
    {
        // Trim the quad to the clip rectangle and move its UVs in by the
        // same proportion, so what survives shows exactly the part of the
        // texture (or gradient, or ellipse mask -- all are driven by UV)
        // that it would have shown untrimmed.
        const float x0 = (x > m_clipX0) ? x : m_clipX0;
        const float y0 = (y > m_clipY0) ? y : m_clipY0;
        const float x1 = (x + w < m_clipX1) ? x + w : m_clipX1;
        const float y1 = (y + h < m_clipY1) ? y + h : m_clipY1;
        if (x1 <= x0 || y1 <= y0)
        {
            return;
        }
        // How far in from the quad's own edges the cut lands, 0..1 each way.
        const float left = (x0 - x) / w, right = (x1 - x) / w;
        const float top = (y0 - y) / h, bottom = (y1 - y) / h;
        if (turned)
        {
            // u runs top to bottom, and v right to left (see UpdateVertexBuffer).
            const float du = u1 - u0, dv = v0 - v1;
            u1 = u0 + bottom * du;
            u0 = u0 + top * du;
            v0 = v1 + right * dv;
            v1 = v1 + left * dv;
        }
        else
        {
            const float du = u1 - u0, dv = v1 - v0;
            u1 = u0 + right * du;
            u0 = u0 + left * du;
            v1 = v0 + bottom * dv;
            v0 = v0 + top * dv;
        }
        x = x0; y = y0; w = x1 - x0; h = y1 - y0;
    }

    UpdateVertexBuffer(x, y, w, h, u0, v0, u1, v1, turned);

    D3D11_MAPPED_SUBRESOURCE mapped;
    if (SUCCEEDED(m_context->Map(m_drawCB.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
    {
        DrawCBData data;
        ColorToFloat4(a, r, g, b, data.Tint);
        data.ShapeMode = isEllipse ? 1.0f : 0.0f;
        data._padDraw[0] = data._padDraw[1] = data._padDraw[2] = 0.0f;
        std::memcpy(mapped.pData, &data, sizeof(data));
        m_context->Unmap(m_drawCB.Get(), 0);
    }

    m_context->PSSetShader(ps, nullptr, 0);
    m_context->PSSetShaderResources(0, 1, &srv);
    m_context->DrawIndexed(6, 0, 0);
}

void QuadRenderer::DrawRect(float x, float y, float w, float h, float a, float r, float g, float b, bool isEllipse)
{
    DrawQuad(x, y, w, h, 0, 0, 1, 1, m_psSolid.Get(), m_whiteSRV.Get(), a, r, g, b, isEllipse);
}

void QuadRenderer::DrawImage(float x, float y, float w, float h, ID3D11ShaderResourceView* srv,
    float a, float r, float g, float b, bool isEllipse)
{
    if (!srv)
    {
        return; // missing/failed-to-load texture -- fail soft, don't draw a garbage quad
    }
    DrawQuad(x, y, w, h, 0, 0, 1, 1, m_psImage.Get(), srv, a, r, g, b, isEllipse);
}

void QuadRenderer::DrawGradientRect(float x, float y, float w, float h, bool radial, float angleDegrees, const std::vector<GradientStop>& stops, bool isEllipse)
{
    if (w <= 0.0f || h <= 0.0f || stops.empty())
    {
        return;
    }

    D3D11_MAPPED_SUBRESOURCE mapped;
    if (SUCCEEDED(m_context->Map(m_gradientCB.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
    {
        GradientCBData data = {};
        UINT count = static_cast<UINT>(stops.size() < 8 ? stops.size() : 8);
        for (UINT i = 0; i < count; ++i)
        {
            ColorToFloat4(stops[i].a, stops[i].r, stops[i].g, stops[i].b, data.StopColor[i]);
            data.StopPos[i][0] = stops[i].pos;
        }
        data.GradientMode = radial ? 1u : 0u;
        data.StopCount = count;
        // XUI's un-rotated linear gradient axis runs left-to-right; the
        // fill's own Rotation turns it. Precompute the axis direction here
        // (in the manifest's +y-down pixel space) rather than calling
        // sin/cos per pixel in the shader.
        const float radians = angleDegrees * 3.14159265358979f / 180.0f;
        data.Axis[0] = std::cos(radians);
        data.Axis[1] = -std::sin(radians);
        std::memcpy(mapped.pData, &data, sizeof(data));
        m_context->Unmap(m_gradientCB.Get(), 0);
    }

    // Tint is ignored by PSGradient (see Shaders.h) -- still bind
    // full-white/full-opaque so nothing stale from a prior draw's DrawCB
    // leaks in if that assumption ever changes. ShapeMode IS read by
    // PSGradient though (see DrawQuad/Shaders.h), so isEllipse still flows
    // through here correctly.
    DrawQuad(x, y, w, h, 0, 0, 1, 1, m_psGradient.Get(), m_whiteSRV.Get(), 255, 255, 255, 255, isEllipse);
}

void QuadRenderer::DrawTextAligned(const FontAtlas& font, const std::string& text,
    float x, float y, float w, float h, int hAlign, int vAlign, float pointSize,
    float a, float r, float g, float b, bool quarterTurn)
{
    if (!font.IsLoaded())
    {
        return;
    }
    // Everything below lays the run out upright; DrawQuad turns each glyph
    // about (x, y) on the way through while this is set.
    m_turn = quarterTurn;
    m_turnX = x;
    m_turnY = y;

    // The manifest gives text a BOX plus an alignment mode (decoded by the
    // cook script from XUI's TextStyle bits), not a pen position -- so the
    // pen has to be derived here, where the real baked glyph advances live.
    float penX = x;
    if (hAlign != 0)
    {
        const float textWidth = font.MeasureWidth(text, pointSize);
        penX = (hAlign == 1) ? x + (w - textWidth) * 0.5f : x + (w - textWidth);
    }

    // Vertical: center/bottom position the font's LINE BOX (ascent+descent),
    // not just its ascent -- centering on ascent alone leaves text sitting
    // low, which was very visible on the 47px-tall menu rows.
    const float ascent = font.GetAscent() * pointSize;
    const float descent = font.GetDescent() * pointSize;
    float baselineY = y + ascent;
    if (vAlign == 1)
    {
        baselineY = y + (h - (ascent + descent)) * 0.5f + ascent;
    }
    else if (vAlign == 2)
    {
        baselineY = y + h - descent;
    }

    DrawTextAtBaseline(font, text, penX, baselineY, pointSize, a, r, g, b, nullptr);
    m_turn = false;
}

void QuadRenderer::DrawText(const FontAtlas& font, const std::string& text, float x, float y, float pointSize,
    float a, float r, float g, float b, float* outWidth)
{
    if (!font.IsLoaded())
    {
        return; // font failed to load -- fail soft rather than crash the frame
    }

    // The manifest's TEXT y is the top-left of the text's bounding box;
    // FontAtlas::LayoutText wants a baseline origin. Convert using the
    // font's real ascent metric (see FontAtlas::GetAscent) scaled to this
    // draw's point size, rather than a guessed fudge constant.
    float baselineY = y + font.GetAscent() * pointSize;
    DrawTextAtBaseline(font, text, x, baselineY, pointSize, a, r, g, b, outWidth);
}

void QuadRenderer::DrawTextAtBaseline(const FontAtlas& font, const std::string& text, float x, float baselineY,
    float pointSize, float a, float r, float g, float b, float* outWidth)
{
    if (!font.IsLoaded())
    {
        return;
    }

    // Both calls take the same pointSize deliberately: FontAtlas keeps one
    // page per baked size, so the UVs LayoutText produces only index into
    // the texture GetAtlasSRV hands back for that same size.
    std::vector<FontAtlas::GlyphQuad> quads = font.LayoutText(text, x, baselineY, pointSize, outWidth);
    ID3D11ShaderResourceView* atlasSRV = font.GetAtlasSRV(pointSize);
    for (const FontAtlas::GlyphQuad& q : quads)
    {
        DrawQuad(q.x, q.y, q.w, q.h, q.u0, q.v0, q.u1, q.v1, m_psText.Get(), atlasSRV, a, r, g, b);
    }
}
