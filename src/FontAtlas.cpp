#include "FontAtlas.h"
#include "FileUtil.h"

#define STB_TRUETYPE_IMPLEMENTATION
#include "../third_party/stb_truetype.h"

#include <cmath>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace
{
    // A size counts as "already baked" within this much, in pixels. The
    // manifest's sizes come out of a points-to-pixels conversion and land on
    // values like 18.667, so an exact float compare would miss; anything
    // this close is the same size as far as rasterizing is concerned.
    constexpr float kSizeEpsilon = 0.01f;

    // The codepoint of UTF-8 `text` at `i`, moving `i` past it. A byte that
    // isn't the start of well-formed UTF-8 comes back as itself, one byte on.
    uint32_t NextCodepoint(const std::string& text, size_t& i)
    {
        const unsigned char lead = static_cast<unsigned char>(text[i++]);
        const int extra = lead >= 0xF0 ? 3 : lead >= 0xE0 ? 2 : lead >= 0xC0 ? 1 : 0;
        if (extra == 0 || i + extra > text.size())
        {
            return lead;
        }
        uint32_t codepoint = lead & (0x3F >> extra);
        for (int k = 0; k < extra; ++k)
        {
            const unsigned char next = static_cast<unsigned char>(text[i + k]);
            if ((next & 0xC0) != 0x80)
            {
                return lead;
            }
            codepoint = (codepoint << 6) | (next & 0x3F);
        }
        i += extra;
        return codepoint;
    }
}

int FontAtlas::GlyphIndex(uint32_t codepoint)
{
    int base = 0;
    for (const Range& range : kRanges)
    {
        if (codepoint >= static_cast<uint32_t>(range.first) && codepoint < static_cast<uint32_t>(range.first + range.count))
        {
            return base + static_cast<int>(codepoint) - range.first;
        }
        base += range.count;
    }
    return -1;
}

bool FontAtlas::LoadFromFile(ID3D11Device* device, const std::wstring& ttfPath)
{
    (void)device; // pages are baked later, by EnsureSize
    m_pages.clear();
    m_fontData = FileUtil::ReadWholeFileBinary(ttfPath);
    if (m_fontData.empty())
    {
        return false;
    }

    // Real ascent/descent metrics (baseline to top/bottom of the font's
    // design em) rather than guessed constants -- lets callers convert a
    // top-left text origin (the manifest's TEXT convention) to the baseline
    // LayoutText expects accurately, instead of eyeballing a fudge factor
    // per font. These are size-independent, so they're read once here rather
    // than per baked page.
    stbtt_fontinfo info;
    if (!stbtt_InitFont(&info, m_fontData.data(), 0))
    {
        m_fontData.clear(); // not a font we can read -- report as not loaded
        return false;
    }
    int ascent = 0, descent = 0, lineGap = 0;
    stbtt_GetFontVMetrics(&info, &ascent, &descent, &lineGap);
    // Expressed as a fraction of an em so they scale to any size a caller
    // later asks for. stb reports descent as negative (below the baseline);
    // flip it so callers can treat ascent+descent as the line height.
    //
    // ScaleForMappingEmToPixels, NOT ScaleForPixelHeight: the first divides
    // by unitsPerEm, the second by (ascent - descent), and everything else
    // here -- the manifest's sizes, the cook scripts' measurements, every
    // other font tool -- means the em. These fonts' two figures differ by a
    // third, so mixing them up sits text noticeably wrong in its box on top
    // of drawing it at the wrong size.
    const float emScale = stbtt_ScaleForMappingEmToPixels(&info, 100.0f) / 100.0f;
    m_ascent = ascent * emScale;
    m_descent = -descent * emScale;
    // What EnsureSize has to hand the baker to get an em of `pixelSize`.
    const float heightScale = stbtt_ScaleForPixelHeight(&info, 100.0f) / 100.0f;
    m_bakeHeightScale = (heightScale > 0.0f) ? (emScale / heightScale) : 1.0f;
    return true;
}

void FontAtlas::SetRenderScale(ID3D11Device* device, float scale)
{
    (void)device;
    if (scale <= 0.0f || std::fabs(scale - m_renderScale) < 0.005f)
    {
        return; // unchanged (or a degenerate window) -- keep the pages we have
    }
    // Every page was rasterized for the old scale, so none of them is the
    // right size any more. Dropping them here rather than keeping both sets
    // is deliberate: a window being dragged to a new size would otherwise
    // accumulate a full set of pages per size it passed through.
    m_renderScale = scale;
    m_pages.clear();
}

bool FontAtlas::EnsureSize(ID3D11Device* device, float pointSize)
{
    if (!IsLoaded() || !device || pointSize <= 0.0f)
    {
        return false;
    }
    // Rasterize at the size these glyphs will really occupy on screen, not
    // at their size in canvas units -- see SetRenderScale.
    const float pixelSize = pointSize * m_renderScale;
    for (const Page& page : m_pages)
    {
        if (std::fabs(page.pixelHeight - pixelSize) < kSizeEpsilon)
        {
            return true; // already have this one
        }
    }

    // stb's packer is intentionally simple and reports failure by returning
    // 0, so rather than guessing one atlas dimension generous enough for
    // every size, start small and grow until everything fits. A 18.7px page
    // is comfortable at 256x256, where a fixed 512x512 for every size would
    // waste 3/4 of the texture on each of the small pages.
    // stb's baker measures its pixel_height from ascent to descent, so ask
    // it for the taller figure that puts the EM at pixelSize (see the header).
    // page.pixelHeight stays the em size: it is what LayoutText divides by to
    // get back to canvas units, and what PageFor matches against.
    const float bakeHeight = pixelSize * m_bakeHeightScale;
    Page page;
    page.pixelHeight = pixelSize;
    page.glyphs.resize(kNumChars);
    // The pack API rather than stbtt_BakeFontBitmap: it takes several ranges,
    // and draws the font's "missing" box once however many codepoints lack a
    // glyph. One pixel of padding and no oversampling, which is what Bake did.
    std::vector<stbtt_packedchar> baked(kNumChars);
    const int rangeCount = static_cast<int>(sizeof(kRanges) / sizeof(kRanges[0]));
    std::vector<stbtt_pack_range> ranges(rangeCount);
    for (int i = 0, first = 0; i < rangeCount; first += kRanges[i].count, ++i)
    {
        ranges[i] = {};
        ranges[i].font_size = bakeHeight;
        ranges[i].first_unicode_codepoint_in_range = kRanges[i].first;
        ranges[i].num_chars = kRanges[i].count;
        ranges[i].chardata_for_range = baked.data() + first;
    }
    std::vector<uint8_t> pixels;
    int dimension = 128;
    int bakeResult = 0;
    while (dimension <= 2048)
    {
        pixels.assign(static_cast<size_t>(dimension) * dimension, 0);
        stbtt_pack_context pack;
        if (stbtt_PackBegin(&pack, pixels.data(), dimension, dimension, 0, 1, nullptr))
        {
            bakeResult = stbtt_PackFontRanges(&pack, m_fontData.data(), 0, ranges.data(), rangeCount);
            stbtt_PackEnd(&pack);
        }
        if (bakeResult > 0)
        {
            break;
        }
        dimension *= 2;
    }
    if (bakeResult <= 0)
    {
        // Even 2048x2048 couldn't hold the glyphs, which would mean an
        // absurd pixelSize. Whatever did fit is still valid, so carry on
        // with it rather than losing all text at this size.
    }

    for (int i = 0; i < kNumChars; ++i)
    {
        const stbtt_packedchar& b = baked[i];
        Glyph& g = page.glyphs[i];
        g.u0 = static_cast<float>(b.x0) / dimension;
        g.v0 = static_cast<float>(b.y0) / dimension;
        g.u1 = static_cast<float>(b.x1) / dimension;
        g.v1 = static_cast<float>(b.y1) / dimension;
        g.xoff = b.xoff;
        g.yoff = b.yoff;
        g.width = static_cast<float>(b.x1 - b.x0);
        g.height = static_cast<float>(b.y1 - b.y0);
        g.advance = b.xadvance;
    }

    // Uploaded as a single-channel (R8) texture -- the pixel shader that
    // draws text samples .r as coverage/alpha and multiplies by the draw's
    // tint color (see PSText in Shaders.h).
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = static_cast<UINT>(dimension);
    desc.Height = static_cast<UINT>(dimension);
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_R8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_IMMUTABLE;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;

    D3D11_SUBRESOURCE_DATA initData = {};
    initData.pSysMem = pixels.data();
    initData.SysMemPitch = static_cast<UINT>(dimension);

    HRESULT hr = device->CreateTexture2D(&desc, &initData, &page.texture);
    if (FAILED(hr))
    {
        return false;
    }
    hr = device->CreateShaderResourceView(page.texture.Get(), nullptr, &page.srv);
    if (FAILED(hr))
    {
        return false;
    }

    m_pages.push_back(std::move(page));
    return true;
}

const FontAtlas::Page* FontAtlas::PageFor(float pointSize) const
{
    const float wanted = pointSize * m_renderScale;
    const Page* best = nullptr;
    float bestDistance = 0.0f;
    for (const Page& page : m_pages)
    {
        const float distance = std::fabs(page.pixelHeight - wanted);
        if (distance < kSizeEpsilon)
        {
            return &page; // baked at exactly this size -- no resampling
        }
        if (!best || distance < bestDistance)
        {
            best = &page;
            bestDistance = distance;
        }
    }
    return best;
}

ID3D11ShaderResourceView* FontAtlas::GetAtlasSRV(float pointSize) const
{
    const Page* page = PageFor(pointSize);
    return page ? page->srv.Get() : nullptr;
}

std::vector<FontAtlas::GlyphQuad> FontAtlas::LayoutText(const std::string& text, float x, float y, float pointSize, float* outWidth) const
{
    std::vector<GlyphQuad> quads;
    quads.reserve(text.size());

    const Page* page = PageFor(pointSize);
    if (!page)
    {
        if (outWidth)
        {
            *outWidth = 0.0f;
        }
        return quads;
    }

    // Baked pixels -> canvas units. With a page baked at exactly
    // pointSize * renderScale this is 1/renderScale, which is what puts a
    // glyph rasterized for a 1.9x window back at its correct CANVAS size --
    // the quad is still positioned and sized in canvas units, it's just
    // sampling a texture with 1.9x the detail. The general form is kept so
    // the nearest-page fallback still lands at the right size.
    const float scale = pointSize / page->pixelHeight;
    float penX = x;

    for (size_t i = 0; i < text.size();)
    {
        const int index = GlyphIndex(NextCodepoint(text, i));
        if (index < 0)
        {
            // A codepoint that isn't baked -- advance by a rough
            // space-width guess rather than dropping the character
            // silently and desyncing later glyph positions.
            penX += pointSize * 0.5f;
            continue;
        }

        const Glyph& g = page->glyphs[index];
        if (g.width > 0 && g.height > 0)
        {
            GlyphQuad q;
            q.x = penX + g.xoff * scale;
            q.y = y + g.yoff * scale;
            q.w = g.width * scale;
            q.h = g.height * scale;
            q.u0 = g.u0;
            q.v0 = g.v0;
            q.u1 = g.u1;
            q.v1 = g.v1;
            quads.push_back(q);
        }
        penX += g.advance * scale;
    }

    if (outWidth)
    {
        *outWidth = penX - x;
    }
    return quads;
}

float FontAtlas::MeasureWidth(const std::string& text, float pointSize) const
{
    // Deliberately mirrors LayoutText's pen advance exactly, including its
    // choice of page and its fallback for codepoints outside the baked
    // ranges -- a measurement that disagreed with the layout would misalign
    // centered/right-aligned text by exactly the amount it disagreed.
    const Page* page = PageFor(pointSize);
    if (!page)
    {
        return 0.0f;
    }
    const float scale = pointSize / page->pixelHeight;
    float width = 0.0f;
    for (size_t i = 0; i < text.size();)
    {
        const int index = GlyphIndex(NextCodepoint(text, i));
        width += (index < 0) ? pointSize * 0.5f : page->glyphs[index].advance * scale;
    }
    return width;
}
