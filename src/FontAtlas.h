#pragma once

#include <windows.h>
#include <d3d11.h>
#include <wrl/client.h>
#include <cstdint>
#include <string>
#include <vector>

// Rasterizes a TTF (one of the already-cracked Xbox 360 fonts, e.g.
// xenonclatin_full.ttf) into single-channel glyph atlas textures using
// stb_truetype (third_party/stb_truetype.h -- public domain, header-only,
// no extra dependency/vcpkg package needed), and exposes enough per-glyph
// data (atlas UV rect, quad offset, advance) to lay out arbitrary text.
//
// ONE ATLAS PAGE PER PIXEL SIZE. The first pass baked ASCII 32..126 once at
// 32px and scaled the quads to whatever size a draw asked for, which is a
// tempting simplification and a bad one: a glyph rasterized at 32px and then
// squashed to 18.7px is a resampled bitmap, not a glyph rendered at 18.7px.
// Stems land between pixels and come out uneven, the coverage ramp at every
// edge is a blur of a blur, and the run reads as slightly soft and mushy
// without ever looking obviously broken -- which is exactly how it got
// described. It mattered here because 18.667px is by far the most common
// text size in the cooked scenes (every hub tile label, most body copy) and
// 32px is among the least common, so nearly all text on screen was being
// scaled to 58% and only the page-selector captions landed at 1:1.
//
// So pages are baked on demand at exact sizes instead: Scene::Load walks its
// manifest, collects the distinct sizes its TEXT commands use, and calls
// EnsureSize for each -- a handful of pages for a whole scene. A draw at a
// size that was never baked (a mid-morph interpolated size, say) falls back
// to the nearest page and scales, i.e. the old behavior, for the few frames
// it is on screen.
class FontAtlas
{
public:
    struct Glyph
    {
        float u0, v0, u1, v1;   // atlas UV rect
        float xoff, yoff;       // offset from pen position to quad top-left, in baked pixels
        float width, height;    // quad size, in baked pixels
        float advance;          // how far to move the pen after this glyph, in baked pixels
    };

    struct GlyphQuad
    {
        float x, y, w, h;       // destination rect, in caller's coordinate space (already scaled)
        float u0, v0, u1, v1;
    };

    // Reads the TTF and works out the size-independent vertical metrics. Does
    // NOT bake anything yet -- call EnsureSize for each size you'll draw at.
    bool LoadFromFile(ID3D11Device* device, const std::wstring& ttfPath);

    // True once a font file has been read successfully. Callers use this to
    // fail soft (draw nothing) rather than crash on a missing font.
    bool IsLoaded() const { return !m_fontData.empty(); }

    // Bakes the glyphs (kRanges) for text drawn at `pointSize` CANVAS units, if it
    // isn't baked already. The page is rasterized at pointSize * the render
    // scale, i.e. at the size the glyphs actually land on screen.
    // Idempotent and cheap to call again with a size already present.
    //
    // `pointSize` is the EM SQUARE, the same thing every other tool means by
    // a font size -- what FreeType sets with FT_Set_Pixel_Sizes and what the
    // cook scripts measure with (Pillow's ImageFont.truetype(size=N)). That
    // is not what stb_truetype's bake entry point means by its pixel_height
    // argument: it takes ascent-to-descent, so handing it the em size draws
    // everything at em/(ascent-descent) of the intended size. For these
    // fonts that is 2048/2724 = 0.752, i.e. every glyph 25% too small and
    // every advance 25% too short -- which the flow layouts in hub_build.py
    // notice, because they position each caption after the previous one's
    // measured advance and that measurement was right. See EnsureSize for
    // the conversion.
    bool EnsureSize(ID3D11Device* device, float pointSize);

    // The factor between a canvas unit and a real output pixel -- what
    // QuadRenderer::SetLetterboxViewport works out when it fits the scene's
    // canvas into the window. Baking has to know it, because a glyph drawn
    // at 18.667 canvas units on a window scaled 1.9x covers ~35 real pixels,
    // and rasterizing it at 18.667 and letting the GPU stretch it is the
    // same resampling mistake as baking one size and scaling to another --
    // just moved downstream, where it shows up as soft, slightly pixelated
    // text on any window that isn't exactly canvas-sized. Changing this
    // drops every baked page (they were all rasterized for the old scale),
    // so the caller must re-EnsureSize afterwards; Scene::SetRenderScale
    // does both together.
    void SetRenderScale(ID3D11Device* device, float scale);
    float GetRenderScale() const { return m_renderScale; }

    // The atlas texture holding `pointSize`'s glyphs -- the page baked at
    // that exact size when there is one, else the closest baked page. Null
    // if the font never loaded or no page has been baked.
    ID3D11ShaderResourceView* GetAtlasSRV(float pointSize) const;

    // Lays out `text` with (x, y) as the BASELINE origin (standard font
    // convention: glyphs with descenders, e.g. 'g'/'y', extend below y) at
    // `pointSize` pixels tall, returning one quad per glyph (skipping
    // glyphs with no visible geometry, e.g. space) plus the total advance
    // width via outWidth. Callers working in top-left-origin coordinates
    // (as the cooked manifest's TEXT command does) should add GetAscent()
    // scaled to their point size first -- see QuadRenderer::DrawText.
    //
    // Lays out from the same page GetAtlasSRV(pointSize) returns, so a
    // caller must pass the same pointSize to both or the UVs won't match
    // the texture that ends up bound.
    std::vector<GlyphQuad> LayoutText(const std::string& text, float x, float y, float pointSize, float* outWidth = nullptr) const;

    // Total advance width of `text` at `pointSize`, without building any
    // quads. Needed to resolve the manifest's center/right text alignment,
    // which has to know the run's width BEFORE choosing a pen position.
    float MeasureWidth(const std::string& text, float pointSize) const;

    // Font ascent (baseline to top of typical capital letters), in units of
    // "fraction of point size", where point size means the EM SQUARE (see
    // EnsureSize) -- multiply by a desired pointSize to get a
    // pixel offset. Lets callers convert a top-left text origin to the
    // baseline LayoutText expects without hardcoding a guessed constant.
    float GetAscent() const { return m_ascent; }

    // Font descent (baseline to bottom of typical descenders), as a POSITIVE
    // fraction of point size. GetAscent() + GetDescent() is the font's line
    // height, which is what vertical centering has to center -- centering on
    // the ascent alone sits text visibly low in its box.
    float GetDescent() const { return m_descent; }

private:
    // The codepoints baked: Basic Latin through Latin Extended-A, and
    // Cyrillic -- what the dashboard's own language names ("Español",
    // "Türkçe", "Pусский") need, and all this Latin font has of them. Text
    // is UTF-8. Anything else advances half an em and draws nothing.
    struct Range { int first, count; };
    // ...and two more: the bullet of the guide's lists, and the euro sign, a key of the keyboard.
    static constexpr Range kRanges[] = { { 0x20, 0x160 }, { 0x400, 0x60 }, { 0x2022, 1 }, { 0x20AC, 1 } };
    static constexpr int kNumChars = 0x160 + 0x60 + 2;
    // Where a codepoint's glyph is in Page::glyphs, or -1 if it isn't baked.
    static int GlyphIndex(uint32_t codepoint);

    struct Page
    {
        Microsoft::WRL::ComPtr<ID3D11Texture2D> texture;
        Microsoft::WRL::ComPtr<ID3D11ShaderResourceView> srv;
        std::vector<Glyph> glyphs;  // indexed by GlyphIndex(codepoint)
        float pixelHeight = 0.0f;
    };

    // Nearest baked page for text drawn at `pointSize` canvas units, or
    // null if nothing is baked. Matches against pointSize * m_renderScale,
    // since that's the pixel height a page is rasterized at.
    const Page* PageFor(float pointSize) const;

    std::vector<uint8_t> m_fontData; // kept so pages can be baked after load
    std::vector<Page> m_pages;
    float m_renderScale = 1.0f;      // canvas units -> output pixels
    float m_ascent = 1.0f;   // fraction of em; overwritten with the real metric in LoadFromFile
    float m_descent = 0.25f; // ditto, positive-downward

    // Multiplier from an em size to the "pixel height" stb_truetype's bake
    // wants for that em -- (ascent - descent) / unitsPerEm, ~1.330 for these
    // fonts. Read once from the face rather than assumed, since it is a
    // property of the font's vertical metrics. See EnsureSize.
    float m_bakeHeightScale = 1.0f;
};
