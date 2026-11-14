#include "Scene.h"
#include "FileUtil.h"
#include "TextVars.h"

#include <algorithm>
#include <cmath>
#include <utility>
#include <vector>

namespace
{
    // Pairs up corresponding commands between an item's `normal` and `focus`
    // draw lists purely by position within their own type -- "the Nth
    // gradient rect in normal corresponds to the Nth gradient rect in
    // focus" -- rather than any hardcoded notion of which element is the
    // background, the icon, or the label. This is what lets a hub tile
    // actually MORPH between its two authored states (grow, brighten, its
    // drop-shadow ring fade in) instead of jump-cutting between them:
    // - A paired command gets its geometry (x/y/w/h, and for RECT/IMAGE/TEXT
    //   its color too) linearly interpolated at blend factor `f`.
    // - A GradientRect pair is handled differently: stop counts/colors can
    //   differ between the two authored gradients (the focus ring is
    //   usually a brighter, differently-stopped ramp than the resting
    //   fill), so rather than trying to match stop-for-stop, the RECT the
    //   gradient paints is interpolated once and BOTH whole, unmodified
    //   gradients are drawn at that shared rect, crossfaded by `f`.
    // - A command that exists in only ONE of the two lists (the focus
    //   state's extra drop-shadow RECTs, or a page-title TEXT that's only
    //   present -- via LAYER fixed -- while this tile has focus) has
    //   nothing to morph into, so it's drawn at its own native geometry,
    //   fading in (if focus-only) or out (if normal-only) with `f`.
    void BucketByType(const std::vector<ManifestCommand>& commands,
        std::vector<const ManifestCommand*> buckets[4])
    {
        for (const ManifestCommand& c : commands)
        {
            buckets[static_cast<int>(c.type)].push_back(&c);
        }
    }

    float LerpF(float a, float b, float f) { return a + (b - a) * f; }

    ManifestCommand LerpGeometry(const ManifestCommand& a, const ManifestCommand& b, float f)
    {
        ManifestCommand out = a;
        out.x = LerpF(a.x, b.x, f);
        out.y = LerpF(a.y, b.y, f);
        out.w = LerpF(a.w, b.w, f);
        out.h = LerpF(a.h, b.h, f);
        return out;
    }

    ManifestCommand BlendPaired(const ManifestCommand& a, const ManifestCommand& b, float f)
    {
        ManifestCommand out = LerpGeometry(a, b, f);
        out.color.a = LerpF(a.color.a, b.color.a, f);
        out.color.r = LerpF(a.color.r, b.color.r, f);
        out.color.g = LerpF(a.color.g, b.color.g, f);
        out.color.b = LerpF(a.color.b, b.color.b, f);
        if (out.type == ManifestCommandType::Text)
        {
            // Alignment mode and the string itself aren't meaningfully
            // interpolatable -- swap at the midpoint rather than picking
            // one side for the whole transition, so a rare case where they
            // genuinely differ still reads as "the new state" by the time
            // the morph is more than half done.
            out.pointSize = LerpF(a.pointSize, b.pointSize, f);
            out.text = (f < 0.5f) ? a.text : b.text;
            out.fontFile = (f < 0.5f) ? a.fontFile : b.fontFile;
            out.hAlign = (f < 0.5f) ? a.hAlign : b.hAlign;
            out.vAlign = (f < 0.5f) ? a.vAlign : b.vAlign;
        }
        return out;
    }

    void AppendFading(std::vector<ManifestCommand>& out, const ManifestCommand& c, float alphaScale)
    {
        ManifestCommand faded = c;
        faded.color.a *= alphaScale;
        for (ManifestGradientStop& stop : faded.stops)
        {
            stop.color.a *= alphaScale;
        }
        out.push_back(std::move(faded));
    }

    std::vector<ManifestCommand> BlendCommandLists(const std::vector<ManifestCommand>& normal,
        const std::vector<ManifestCommand>& focus, float f)
    {
        std::vector<ManifestCommand> out;

        std::vector<const ManifestCommand*> normalBuckets[4];
        std::vector<const ManifestCommand*> focusBuckets[4];
        BucketByType(normal, normalBuckets);
        BucketByType(focus, focusBuckets);

        for (int type = 0; type < 4; ++type)
        {
            std::vector<const ManifestCommand*>& nb = normalBuckets[type];
            std::vector<const ManifestCommand*>& fb = focusBuckets[type];
            const size_t paired = std::min(nb.size(), fb.size());

            for (size_t i = 0; i < paired; ++i)
            {
                const ManifestCommand& a = *nb[i];
                const ManifestCommand& b = *fb[i];
                if (a.type == ManifestCommandType::GradientRect)
                {
                    ManifestCommand rect = LerpGeometry(a, b, f);
                    ManifestCommand aCmd = rect;
                    aCmd.radial = a.radial; aCmd.angleDegrees = a.angleDegrees;
                    aCmd.isEllipse = a.isEllipse; aCmd.stops = a.stops;
                    ManifestCommand bCmd = rect;
                    bCmd.radial = b.radial; bCmd.angleDegrees = b.angleDegrees;
                    bCmd.isEllipse = b.isEllipse; bCmd.stops = b.stops;
                    // The resting gradient at FULL strength underneath,
                    // the focus one fading in on top -- not both at
                    // complementary alphas. Complementary alphas look like a
                    // crossfade but aren't one: compositing a at (1-f) and
                    // then b at f leaves f*(1-f) of the BACKDROP showing
                    // through, up to a quarter of it at the midpoint. On the
                    // tiles that just washed the green out slightly; on the
                    // near-black promo panel, against a light grey backdrop,
                    // it would visibly go translucent halfway through the
                    // morph. Drawing the bottom layer opaque is both a real
                    // crossfade and correct at f=0 and f=1.
                    out.push_back(std::move(aCmd));
                    AppendFading(out, bCmd, f);
                }
                else
                {
                    out.push_back(BlendPaired(a, b, f));
                }
            }
            for (size_t i = paired; i < nb.size(); ++i) AppendFading(out, *nb[i], 1.0f - f);
            for (size_t i = paired; i < fb.size(); ++i) AppendFading(out, *fb[i], f);
        }

        return out;
    }
}

namespace
{
    // The time of day as the user's own locale writes it, without seconds --
    // "9:41 PM" on a US machine, "21:41" on most others -- which is the same
    // choice the console makes from its own locale setting.
    std::string ClockText()
    {
        wchar_t buffer[64] = {};
        GetTimeFormatEx(LOCALE_NAME_USER_DEFAULT, TIME_NOSECONDS, nullptr, nullptr, buffer, 64);
        return FileUtil::WideToUtf8(buffer);
    }

    // A raised cosine between pulseFloor and 1, one full breath per
    // pulseSeconds. Cosine rather than a triangle so it eases at both ends --
    // a glow that reverses at constant speed reads as a flicker.
    float PulseFactor(const ManifestCommand& cmd, float timeSeconds)
    {
        if (cmd.pulseSeconds <= 0.0f)
        {
            return 1.0f;
        }
        const float phase = 6.28318530718f * timeSeconds / cmd.pulseSeconds;
        const float wave = 0.5f - 0.5f * std::cos(phase);   // 0 at t=0, up to 1
        return cmd.pulseFloor + (1.0f - cmd.pulseFloor) * wave;
    }
}

bool Scene::Load(ID3D11Device* device, const std::wstring& sceneDir, const std::wstring& fontPath)
{
    std::wstring manifestPath = sceneDir + L"\\manifest.txt";
    if (!m_manifest.LoadFromFile(manifestPath))
    {
        return false;
    }

    // Font failing to load isn't fatal to the scene -- text commands will
    // just draw nothing (FontAtlas::IsLoaded() is false, and
    // QuadRenderer::DrawText already fails soft on that) rather than the
    // whole screen going blank for a missing/misnamed font file.
    m_font.LoadFromFile(device, fontPath);
    // Extra faces are named by filename only, and resolved next to the
    // default -- so a manifest never has to know where the app keeps fonts.
    size_t slash = fontPath.find_last_of(L"\\/");
    m_fontsDir = (slash == std::wstring::npos) ? std::wstring() : fontPath.substr(0, slash + 1);

    std::wstring assetsDir = sceneDir + L"\\assets\\";
    // Textures are referenced from the static list AND from both variants of
    // every item, so walk all of them -- an image that only appears in an
    // item's focus state (a highlight, a focused-only glyph) would otherwise
    // silently fail to load until the moment it was needed.
    auto loadFrom = [&](const std::vector<ManifestCommand>& commands)
    {
        for (const ManifestCommand& cmd : commands)
        {
            if (cmd.type != ManifestCommandType::Image || cmd.imageFile.empty())
            {
                continue;
            }
            if (m_textures.find(cmd.imageFile) != m_textures.end())
            {
                continue; // the same PNG is referenced by many draw commands (icons, repeated glyphs)
            }
            std::wstring imagePath = assetsDir + FileUtil::Utf8ToWide(cmd.imageFile);
            m_textures[cmd.imageFile] = TextureLoader::LoadFromFile(device, imagePath);
        }
    };
    // Bake a glyph atlas page for each distinct text size this scene draws
    // at, so every run rasterizes at the size it's actually displayed at
    // instead of being resampled from one fixed size (see FontAtlas.h). The
    // scenes here use a handful of sizes between them, so this is a few
    // pages, not one per string.
    auto bakeFrom = [&](const std::vector<ManifestCommand>& commands)
    {
        for (const ManifestCommand& cmd : commands)
        {
            if (cmd.type != ManifestCommandType::Text || cmd.pointSize <= 0.0f)
            {
                continue;
            }
            // Remembered, not just baked: a window resize changes how many
            // real pixels a canvas unit covers, and every page has to be
            // re-rasterized for the new scale (see Scene::SetRenderScale).
            const bool known = std::any_of(m_textSizes.begin(), m_textSizes.end(),
                [&](float size) { return std::fabs(size - cmd.pointSize) < 0.01f; });
            if (!known)
            {
                m_textSizes.push_back(cmd.pointSize);
            }
            // A face named by a FONT directive, loaded once and shared by
            // every run that asks for it. A face that fails to load simply
            // isn't in the map, and FontFor falls back to the default --
            // wrong weight beats no text at all.
            if (!cmd.fontFile.empty() && m_fonts.find(cmd.fontFile) == m_fonts.end())
            {
                FontAtlas& face = m_fonts[cmd.fontFile];
                if (!face.LoadFromFile(device, m_fontsDir + FileUtil::Utf8ToWide(cmd.fontFile)))
                {
                    m_fonts.erase(cmd.fontFile);
                }
            }
        }
    };
    loadFrom(m_manifest.GetCommands());
    bakeFrom(m_manifest.GetCommands());
    for (const ManifestItem& item : m_manifest.GetItems())
    {
        loadFrom(item.normal);
        loadFrom(item.focus);
        loadFrom(item.press);
        bakeFrom(item.normal);
        bakeFrom(item.focus);
        bakeFrom(item.press);
    }
    BakeTextSizes(device);

    return true;
}

void Scene::BakeTextSizes(ID3D11Device* device)
{
    for (float size : m_textSizes)
    {
        m_font.EnsureSize(device, size); // no-op for a size already baked
        for (auto& entry : m_fonts)
        {
            entry.second.EnsureSize(device, size);
        }
    }
}

const FontAtlas& Scene::FontFor(const ManifestCommand& cmd) const
{
    if (!cmd.fontFile.empty())
    {
        auto it = m_fonts.find(cmd.fontFile);
        if (it != m_fonts.end())
        {
            return it->second;
        }
    }
    return m_font;
}

void Scene::SetRenderScale(ID3D11Device* device, float scale)
{
    if (!device || std::fabs(scale - m_font.GetRenderScale()) < 0.005f)
    {
        return; // nothing moved -- don't touch the pages we already have
    }
    m_font.SetRenderScale(device, scale); // drops the now wrong-sized pages
    for (auto& entry : m_fonts)
    {
        entry.second.SetRenderScale(device, scale);
    }
    BakeTextSizes(device);
}

void Scene::Draw(QuadRenderer& renderer, const FocusWeights& weights, float scrollX, float timeSeconds,
    Layer layer, float frame, const Variables* variables, float listScroll) const
{
    m_trackStates = m_manifest.EvaluateTracks(frame);
    m_listScroll = listScroll;

    const std::vector<std::string>& names = m_manifest.GetVars();
    m_varValues.assign(names.size(), 0);
    for (size_t i = 0; i < names.size(); ++i)
    {
        const int item = m_manifest.FindItem(names[i]);
        if (item >= 0)
        {
            m_varValues[i] = m_manifest.GetItems()[item].initialValue;
        }
        if (variables)
        {
            auto it = variables->find(names[i]);
            if (it != variables->end())
            {
                m_varValues[i] = it->second;
            }
        }
    }

    // `layer` filters EVERYTHING below -- the scene's static commands, the
    // items' own content, and the page chrome -- so that Layer::ScrollOnly
    // plus Layer::FixedOnly together draw exactly what Layer::All does, and
    // a caller can put the two halves under different transforms.
    const bool wantScroll = (layer != Layer::FixedOnly);
    const bool wantFixed = (layer != Layer::ScrollOnly);

    DrawCommands(renderer, m_manifest.GetCommands(), scrollX, timeSeconds, layer);

    // Fully unfocused items first, in file order, then everything with any
    // focus weight at all in ASCENDING weight order. Three things depend on
    // that ordering: a focused tile grows past its own bounds and would
    // otherwise be clipped by whichever neighbour happens to come later in
    // the file; the hub's focus block opens by dimming every other page,
    // which only works if everything it is dimming has already been drawn;
    // and mid-morph, the tile gaining focus is the one that should end up on
    // top -- which sorting by weight gives for free, however many tiles
    // happen to be in flight.
    std::vector<std::pair<float, const ManifestItem*>> active;
    for (const ManifestItem& item : m_manifest.GetItems())
    {
        auto it = weights.find(item.id);
        const float weight = (it == weights.end()) ? 0.0f : it->second;
        if (weight <= 0.0f)
        {
            DrawCommands(renderer, item.normal, scrollX, timeSeconds, layer);
        }
        else
        {
            active.emplace_back(weight, &item);
        }
    }

    std::stable_sort(active.begin(), active.end(),
        [](const std::pair<float, const ManifestItem*>& a, const std::pair<float, const ManifestItem*>& b)
        {
            return a.first < b.first;
        });

    // The tiles themselves, morphing -- but WITHOUT their LAYER fixed
    // commands, which are page chrome rather than part of the tile.
    for (const std::pair<float, const ManifestItem*>& entry : active)
    {
        if (!wantScroll)
        {
            break;
        }
        const ManifestItem& item = *entry.second;
        if (item.focus.empty())
        {
            // Nothing authored to morph into. Still scroll-only: if this
            // item turns out to be the top one its chrome is drawn below,
            // and if it isn't, its chrome isn't the page's to draw.
            DrawCommands(renderer, item.normal, scrollX, timeSeconds, Layer::ScrollOnly);
        }
        else if (entry.first > 1.0f && !item.press.empty())
        {
            // Past fully focused: a press in flight, morphing focus -> press
            // by however far past 1 the weight has run.
            DrawCommands(renderer,
                BlendCommandLists(Visible(item.focus), Visible(item.press), std::min(entry.first - 1.0f, 1.0f)),
                scrollX, timeSeconds, Layer::ScrollOnly);
        }
        else if (entry.first >= 1.0f)
        {
            DrawCommands(renderer, item.focus, scrollX, timeSeconds, Layer::ScrollOnly); // settled
        }
        else
        {
            DrawItemBlended(renderer, item, entry.first, scrollX, timeSeconds, Layer::ScrollOnly);
        }
    }

    // Page chrome: only from the item with the HIGHEST weight, at full
    // strength, never blended.
    //
    // Crossfading it the way a tile crossfades was wrong in a way that only
    // showed once the page selector had two faces in it. Moving focus to a
    // neighbouring PAGE leaves both items mid-morph for ~0.2s, and both were
    // drawing the whole selector row: the tile being left drew "home" in
    // Xbox TC (it was the selected page) while the tile being entered drew
    // the same caption in Segoe Xbox Light (now unselected). Two faces, same
    // box, different advances and different baselines -- which reads exactly
    // like a ghosted copy sitting a few pixels down and to the left.
    //
    // Chrome isn't the tile's, it's the page's: it should switch when focus
    // commits, not dissolve between two states. Drawing only the top item's,
    // unfaded, also stops the identical-twist case (moving between two tiles
    // on the SAME page) from compositing two half-alpha copies and dipping
    // in brightness for the duration of the morph.
    //
    // Every OTHER item still easing keeps its resting fixed commands. On a
    // page that does not scroll the tiles themselves are LAYER fixed (My
    // Apps, the sign-in pages), and with nothing drawn here a tile vanished
    // from the moment focus moved until its weight ran down to 0. The hub's
    // items have no fixed commands at rest, so this adds nothing there.
    if (!active.empty() && wantFixed)
    {
        for (size_t i = 0; i + 1 < active.size(); ++i)
        {
            DrawCommands(renderer, active[i].second->normal, scrollX, timeSeconds, Layer::FixedOnly);
        }
        const ManifestItem& top = *active.back().second;
        DrawCommands(renderer, top.focus.empty() ? top.normal : top.focus, scrollX, timeSeconds, Layer::FixedOnly);
    }
}

void Scene::DrawItemBlended(QuadRenderer& renderer, const ManifestItem& item, float f, float scrollX,
    float timeSeconds, Layer layer) const
{
    f = std::clamp(f, 0.0f, 1.0f);
    if (f <= 0.0f)
    {
        DrawCommands(renderer, item.normal, scrollX, timeSeconds, layer);
        return;
    }
    DrawCommands(renderer, BlendCommandLists(Visible(item.normal), Visible(item.focus), f), scrollX, timeSeconds, layer);
}

std::vector<ManifestCommand> Scene::Visible(const std::vector<ManifestCommand>& list) const
{
    std::vector<ManifestCommand> out;
    out.reserve(list.size());
    for (const ManifestCommand& cmd : list)
    {
        if (cmd.ifVar < 0 || m_varValues[cmd.ifVar] == cmd.ifValue)
        {
            out.push_back(cmd);
        }
    }
    return out;
}

void Scene::DrawCommands(QuadRenderer& renderer, const std::vector<ManifestCommand>& commands,
    float scrollX, float timeSeconds, Layer layer) const
{
    bool clipped = false;
    for (const ManifestCommand& cmd : commands)
    {
        if (clipped)
        {
            renderer.ClearClip();   // the last command was the list's; this one may not be
            clipped = false;
        }
        if ((layer == Layer::ScrollOnly && cmd.fixedToScreen) ||
            (layer == Layer::FixedOnly && !cmd.fixedToScreen))
        {
            continue;
        }
        // The camera is applied here rather than in the vertex shader because
        // every draw is already a separate quad with its own position -- one
        // subtraction per command is cheaper than threading another constant
        // buffer field through, and it keeps text alignment working unchanged
        // (x is the alignment box's origin either way).
        if (cmd.ifVar >= 0 && m_varValues[cmd.ifVar] != cmd.ifValue)
        {
            continue;   // one of the scene's alternatives, and not the one in force
        }
        float x = cmd.fixedToScreen ? cmd.x : cmd.x - scrollX;
        float y = cmd.y;
        float w = cmd.w;
        float h = cmd.h;
        if (cmd.inList && m_manifest.HasList())
        {
            // A row of the scrolling list: moved up by the scroll, and cut
            // off where the list's own window ends.
            y -= m_listScroll;
            renderer.SetClip(m_manifest.GetListX(), m_manifest.GetListY(), m_manifest.GetListW(), m_manifest.GetListH());
            clipped = true;
        }

        // A tracked command is drawn where its track puts it this frame:
        // scaled about the track's box origin, moved, faded, or not at all.
        float trackAlpha = 1.0f;
        float gradientAngle = cmd.angleDegrees;
        if (cmd.track >= 0 && static_cast<size_t>(cmd.track) < m_trackStates.size())
        {
            const TrackState& track = m_trackStates[cmd.track];
            if (!track.visible || track.alpha <= 0.0f)
            {
                continue;
            }
            x = track.x + (x - track.x) * track.sx + track.dx;
            y = track.y + (y - track.y) * track.sy + track.dy;
            w *= track.sx;
            h *= track.sy;
            trackAlpha = track.alpha;
            // A negative scale is a mirror: the box lands on the far side of
            // the track's origin, and a gradient in it runs the other way.
            // (The guide's "Recent" page keys its tab shadows like this, to
            // throw one shadow figure to either side of the open tab.)
            if (w < 0.0f)
            {
                x += w;
                w = -w;
                gradientAngle = 180.0f - gradientAngle;
            }
            if (h < 0.0f)
            {
                y += h;
                h = -h;
                gradientAngle = -gradientAngle;
            }
        }

        // ...and the fade is applied the same way, as a scale on whatever
        // alpha the command already carries. Dimming the content itself
        // rather than covering it means a dimmed page stays behind the
        // legend and the gamertag instead of under a panel that would have
        // to cover them too.
        const float fade = m_manifest.FadeFactor(cmd, scrollX) * PulseFactor(cmd, timeSeconds) * trackAlpha;
        const float a = cmd.color.a * fade;
        switch (cmd.type)
        {
        case ManifestCommandType::Rect:
            renderer.DrawRect(x, y, w, h, a, cmd.color.r, cmd.color.g, cmd.color.b, cmd.isEllipse);
            break;

        case ManifestCommandType::Image:
        {
            auto it = m_textures.find(cmd.imageFile);
            ID3D11ShaderResourceView* srv = (it != m_textures.end()) ? it->second.srv.Get() : nullptr;
            renderer.DrawImage(x, y, w, h, srv, a, cmd.color.r, cmd.color.g, cmd.color.b);
            break;
        }

        case ManifestCommandType::GradientRect:
        {
            std::vector<QuadRenderer::GradientStop> stops;
            stops.reserve(cmd.stops.size());
            for (const ManifestGradientStop& s : cmd.stops)
            {
                stops.push_back({ s.pos, s.color.a * fade, s.color.r, s.color.g, s.color.b });
            }
            renderer.DrawGradientRect(x, y, w, h, cmd.radial, gradientAngle, stops, cmd.isEllipse);
            break;
        }

        case ManifestCommandType::Text:
        {
            // Live text, which no cook script can know: "{time}" is the
            // guide's clock, and any other "{name}" is what the app holds
            // under that name (TextVars) -- what has been typed, a field.
            std::string live;
            const std::string* text = &cmd.text;
            if (cmd.text == "{time}")
            {
                live = ClockText();
                text = &live;
            }
            else if (cmd.text.size() > 2 && cmd.text.front() == '{' && cmd.text.back() == '}')
            {
                live = TextVars()[cmd.text.substr(1, cmd.text.size() - 2)];
                text = &live;
            }
            if (cmd.caretVar >= 0)
            {
                // An edit box ("CARET"): one line, cut off at the box's sides,
                // with a caret bar where the state variable says (a byte
                // offset; past the end is the end), and the line moved along
                // so that the caret is never out of the box.
                const float kCaretWidth = 3.0f;
                const FontAtlas& font = FontFor(cmd);
                const size_t at = (std::min)(text->size(),
                    static_cast<size_t>((std::max)(m_varValues[cmd.caretVar], 0)));
                const float before = font.MeasureWidth(text->substr(0, at), cmd.pointSize);
                const float shift = std::max(0.0f, before + kCaretWidth - w);
                renderer.SetClip(x, y, w, h);
                renderer.DrawTextAligned(font, *text, x - shift, y, w + shift + 4096.0f, h, 0, cmd.vAlign,
                    cmd.pointSize, a, cmd.color.r, cmd.color.g, cmd.color.b, false);
                renderer.DrawRect(x + before - shift, y + 1.0f, kCaretWidth, h - 2.0f,
                    (cmd.color.a > 0.0f) ? a * cmd.caretColor.a / cmd.color.a : a,
                    cmd.caretColor.r, cmd.caretColor.g, cmd.caretColor.b);
                renderer.ClearClip();
                break;
            }
            renderer.DrawTextAligned(FontFor(cmd), *text,
                x, y, w, h, cmd.hAlign, cmd.vAlign, cmd.pointSize,
                a, cmd.color.r, cmd.color.g, cmd.color.b, cmd.quarterTurn);
            break;
        }
        }
    }
    if (clipped)
    {
        renderer.ClearClip();
    }
}
