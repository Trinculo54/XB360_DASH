#pragma once

#include <windows.h>
#include <d3d11.h>
#include <wrl/client.h>
#include <cstdint>
#include <vector>

#include "FontAtlas.h"

// The whole 2D drawing surface for the app: compiles Shaders.h's HLSL once,
// then exposes one draw call per "kind" of thing the cooked manifests need
// to put on screen (solid rects, images, linear/radial gradients, and text
// glyph quads from a FontAtlas). Every draw is a single quad (two triangles,
// four vertices) -- at the scale of one dashboard screen (hundreds, maybe
// ~1000 draws/frame for text-heavy screens) there's no need for batching;
// simplicity here is worth more than the draw-call count.
//
// Coordinate convention throughout: pixel space, origin top-left, +x right,
// +y down -- matching the manifest format emitted by xur_cook.py. VSMain in
// Shaders.h converts that to D3D clip space.
class QuadRenderer
{
public:
    struct GradientStop
    {
        float pos;         // 0..1
        float a, r, g, b;  // 0..255, matches the manifest/cook-script convention; converted to 0..1 internally
    };

    bool Initialize(ID3D11Device* device, ID3D11DeviceContext* context);

    // Call once per frame, before any Draw* calls, with the CANVAS size
    // (the cooked scene's own authored resolution, e.g. 1120x770 -- NOT
    // the window/backbuffer size) -- updates FrameCB so VSMain maps canvas
    // pixel coordinates to clip space.
    void BeginFrame(float canvasWidth, float canvasHeight);

    // Call once per frame, after Renderer::BeginFrame() (which sets a
    // full-window viewport for the clear) and before any Draw* calls,
    // to letterbox the canvas into the actual window: computes the
    // largest UNIFORM scale that fits canvasWidth x canvasHeight inside
    // windowWidth x windowHeight, and narrows the D3D11 viewport to that
    // scaled-and-centered sub-rectangle. Without this, a window whose
    // aspect ratio doesn't match the canvas's (nearly always true, since
    // window size is user/OS-driven and the canvas is whatever the source
    // .xur screen used) stretches X and Y by DIFFERENT amounts, visibly
    // distorting spacing and text -- confirmed against a real screenshot
    // where a 2559x1360 window (aspect ~1.88) was stretching a 1120x770
    // canvas (aspect ~1.45) wider than tall. The un-covered window area
    // (the letterbox bars) keeps showing Renderer's own clear color.
    void SetLetterboxViewport(float windowWidth, float windowHeight, float canvasWidth, float canvasHeight);

    // A scale and an opacity applied to EVERY subsequent draw, until changed
    // or until the next BeginFrame resets it to (1, 1). The scale is about
    // the canvas's centre, so a scene grows toward the viewer rather than
    // toward its top-left corner.
    //
    // This is what makes a scene transition possible without every Draw*
    // caller knowing about it: Scene::Draw replays a manifest in canvas
    // coordinates and has no idea it's mid-transition, and the whole
    // push-forward-and-fade is two floats set before it runs. Drawing two
    // scenes in one frame is then just two Draw calls with different
    // transforms (see Navigator::BuildRenderPlan).
    //
    // Applied on the CPU in DrawQuad rather than in the vertex shader
    // because every draw already rewrites the vertex buffer with its own
    // rect -- transforming four corners there costs nothing and avoids
    // threading another constant through FrameCB.
    void SetSceneTransform(float scale, float alpha, float offsetX = 0.0f, float offsetY = 0.0f);

    // Confines every subsequent draw to this canvas-space rectangle, until
    // ClearClip or the next BeginFrame -- XUI's ClipChildren. The rectangle
    // is in final canvas coordinates, i.e. it does NOT move with the scene
    // transform: a child scene sliding in under an offset is cut off at its
    // parent's edge, which is the whole point.
    //
    // Done on the CPU in DrawQuad, by trimming the quad and its UVs, rather
    // than with a scissor rect: every draw is one axis-aligned quad, so the
    // trim is exact, and it needs no rasterizer state or pixel-space mapping.
    void SetClip(float x, float y, float w, float h);
    void ClearClip() { m_clipEnabled = false; }

    // `isEllipse`: when true, the quad is masked (with an antialiased edge,
    // computed in the pixel shader from screen-space derivatives) to the
    // ellipse inscribed in the x/y/w/h rect, instead of drawing the full
    // rectangle. This is how XuiFigure's actual authored geometry (circular
    // status dots, ring-shaped progress indicators -- see cook_figure's
    // ellipse detection in xur_cook.py) gets rendered as a real circle
    // rather than its flat bounding box: the cook script can't emit true
    // bezier geometry (see the file-level "Simplifications" comment), but
    // an inscribed-ellipse mask reproduces the visually-important cases
    // (every circular figure seen so far is a full ellipse filling its
    // bounding box, not a partially-rounded rect) without needing a real
    // tessellator.
    void DrawRect(float x, float y, float w, float h, float a, float r, float g, float b, bool isEllipse = false);

    void DrawImage(float x, float y, float w, float h, ID3D11ShaderResourceView* srv,
        float a = 255.0f, float r = 255.0f, float g = 255.0f, float b = 255.0f, bool isEllipse = false);

    // stops must be sorted by pos ascending; at most 8 are used (Shaders.h's
    // MAX_GRADIENT_STOPS) -- extras are silently dropped rather than
    // erroring, since a slightly-truncated gradient is a fine degrade.
    // `isEllipse` -- see DrawRect's doc comment. Also fixes a real bug this
    // masking replaces: PSGradient's radial mode computed a 0..1 distance
    // ratio via `saturate()`, which CLAMPS (rather than discards) anything
    // past the inscribed circle -- so a square's corners silently got
    // painted with whatever color sits at t=1 instead of being excluded,
    // which happens to matter whenever that stop isn't fully transparent.
    // `angleDegrees` rotates the LINEAR gradient axis away from XUI's
    // un-rotated left-to-right default (ignored when `radial`). Almost every
    // vertical gradient in the dashboard's assets is authored as -90 on top
    // of that default, and a button's focus highlight is +90 -- the same
    // axis reversed -- so a hardcoded top-to-bottom axis draws that
    // highlight upside down.
    void DrawGradientRect(float x, float y, float w, float h, bool radial, float angleDegrees, const std::vector<GradientStop>& stops, bool isEllipse = false);

    // Draws `text` inside the box (x, y, w, h), positioned by `hAlign`
    // (0 left / 1 center / 2 right) and `vAlign` (0 top / 1 center /
    // 2 bottom) -- the manifest's TEXT command, whose alignment comes from
    // XUI's TextStyle bits. Alignment is resolved here rather than in the
    // cook script because it needs the atlas's real glyph advances and the
    // font's ascent/descent, so a manifest stays correct across a font swap.
    //
    // `quarterTurn` lays the text out in that box exactly as usual and then
    // turns the whole thing a quarter turn clockwise about the box's origin
    // (x, y) -- the manifest's TEXTV, which is XUI text with Rotation
    // z=w=0.7071, e.g. the guide's blade captions. The box then runs DOWN
    // from the origin and hangs to its left. A turned glyph quad is still an
    // axis-aligned quad, just with its texture corners moved round one
    // place, so this costs nothing and stays as sharp as upright text.
    void DrawTextAligned(const FontAtlas& font, const std::string& text,
        float x, float y, float w, float h, int hAlign, int vAlign, float pointSize,
        float a, float r, float g, float b, bool quarterTurn = false);

    // Draws `text` with the given FontAtlas, starting at (x, y) using the
    // same top-left pixel convention as everything else -- internally
    // converts to FontAtlas::LayoutText's baseline-relative placement (see
    // the .cpp for the exact offset) so callers never have to think about
    // baseline vs. top-left themselves.
    void DrawText(const FontAtlas& font, const std::string& text, float x, float y, float pointSize,
        float a, float r, float g, float b, float* outWidth = nullptr);

    // Shared tail of both text paths: emits the glyph quads for a run whose
    // pen position and BASELINE are already resolved.
    void DrawTextAtBaseline(const FontAtlas& font, const std::string& text, float x, float baselineY,
        float pointSize, float a, float r, float g, float b, float* outWidth = nullptr);

private:
    struct Vertex
    {
        float x, y;
        float u, v;
    };

    struct FrameCBData
    {
        float ScreenSize[2];
        float _padFrame[2];
    };

    struct DrawCBData
    {
        float Tint[4];
        float ShapeMode;   // 0 = full rect, 1 = ellipse inscribed in the quad (see DrawRect's doc comment)
        float _padDraw[3]; // keep the cbuffer a multiple of 16 bytes
    };

    struct GradientCBData
    {
        float StopColor[8][4];
        float StopPos[8][4];
        uint32_t GradientMode;
        uint32_t StopCount;
        float Axis[2];      // linear gradient direction (cos, -sin) of the fill's Rotation
    };

    void DrawQuad(float x, float y, float w, float h, float u0, float v0, float u1, float v1,
        ID3D11PixelShader* ps, ID3D11ShaderResourceView* srv,
        float a, float r, float g, float b, bool isEllipse = false);

    void UpdateVertexBuffer(float x, float y, float w, float h, float u0, float v0, float u1, float v1,
        bool turned);

    Microsoft::WRL::ComPtr<ID3D11Device> m_device;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> m_context;

    // Canvas size from the last BeginFrame -- needed to scale about the
    // canvas centre -- and the scene transform set by SetSceneTransform.
    float m_canvasWidth = 0.0f;
    float m_canvasHeight = 0.0f;
    float m_sceneScale = 1.0f;
    float m_sceneAlpha = 1.0f;
    // Canvas units added to every quad's position. Used by the hub's intro,
    // which starts its content 90 units low and rises into place.
    float m_sceneOffsetX = 0.0f;
    float m_sceneOffsetY = 0.0f;

    bool m_turn = false;          // quads are being turned about (m_turnX, m_turnY) -- see DrawTextAligned
    float m_turnX = 0.0f, m_turnY = 0.0f;

    bool m_clipEnabled = false;   // see SetClip
    float m_clipX0 = 0.0f, m_clipY0 = 0.0f, m_clipX1 = 0.0f, m_clipY1 = 0.0f;

    Microsoft::WRL::ComPtr<ID3D11VertexShader> m_vs;
    Microsoft::WRL::ComPtr<ID3D11PixelShader> m_psSolid;
    Microsoft::WRL::ComPtr<ID3D11PixelShader> m_psImage;
    Microsoft::WRL::ComPtr<ID3D11PixelShader> m_psText;
    Microsoft::WRL::ComPtr<ID3D11PixelShader> m_psGradient;
    Microsoft::WRL::ComPtr<ID3D11InputLayout> m_inputLayout;

    Microsoft::WRL::ComPtr<ID3D11Buffer> m_vertexBuffer; // dynamic, 4 verts, rewritten per draw
    Microsoft::WRL::ComPtr<ID3D11Buffer> m_indexBuffer;   // static, {0,1,2,2,1,3}

    Microsoft::WRL::ComPtr<ID3D11Buffer> m_frameCB;
    Microsoft::WRL::ComPtr<ID3D11Buffer> m_drawCB;
    Microsoft::WRL::ComPtr<ID3D11Buffer> m_gradientCB;

    Microsoft::WRL::ComPtr<ID3D11BlendState> m_blendState;
    Microsoft::WRL::ComPtr<ID3D11SamplerState> m_samplerState;

    // A single 1x1 white pixel, bound whenever a draw has no real texture
    // (solid rects, gradients) -- keeps PSSolid/PSGradient free of a
    // separate no-texture code path even though they don't sample Tex.
    Microsoft::WRL::ComPtr<ID3D11Texture2D> m_whiteTexture;
    Microsoft::WRL::ComPtr<ID3D11ShaderResourceView> m_whiteSRV;
};
