#pragma once

#include "SceneManifest.h"
#include "TextureLoader.h"
#include "FontAtlas.h"
#include "QuadRenderer.h"

#include <unordered_map>
#include <string>

// Ties SceneManifest (the parsed draw list) together with the actual GPU
// resources it references (PNG textures, a font atlas) and knows how to
// replay itself through a QuadRenderer every frame. This is the piece
// main.cpp actually talks to -- it doesn't need to know about manifest
// parsing or texture caching itself.
class Scene
{
public:
    // sceneDir is a folder containing manifest.txt and an assets/
    // subfolder (exactly what xur_cook.py writes per cooked scene, e.g.
    // Scenes/NetworkMain/). fontPath is the TTF used for every TEXT
    // command in this scene. Returns false if manifest.txt couldn't be
    // read; a missing/failed individual texture or the font is not fatal
    // (see TextureLoader/FontAtlas's own fail-soft behavior) -- the scene
    // still loads and simply draws blank/untextured quads for that asset.
    bool Load(ID3D11Device* device, const std::wstring& sceneDir, const std::wstring& fontPath);

    // Tells the font atlas how many output pixels a canvas unit covers, and
    // re-bakes this scene's text sizes for it. Cheap and idempotent when the
    // scale hasn't changed, so it's fine to call every frame; call it before
    // Draw whenever the window size might have changed.
    void SetRenderScale(ID3D11Device* device, float scale);

    float GetCanvasWidth() const { return m_manifest.GetCanvasWidth(); }
    float GetCanvasHeight() const { return m_manifest.GetCanvasHeight(); }

    // How focused each item is, keyed by item id: 1 fully focused, 0 fully
    // unfocused, anything between mid-morph. An id absent from the map is
    // simply at 0, so an empty map draws the whole scene unfocused.
    using FocusWeights = std::unordered_map<std::string, float>;

    // Draws the scene's static commands, then every focusable item at its
    // own weight from `weights` -- an item at 0 in its normal variant, one
    // at 1 in its focus variant, and anything between as a morph between
    // the two (see DrawItemBlended). Items are drawn in ascending weight
    // order, so the most-focused tile ends up on top.
    //
    // Taking a weight per item rather than "the focused id + a blend"
    // is what lets several tiles be mid-morph at once, which is the normal
    // case when a direction is pressed again before the last move settled.
    //
    // `scrollX` slides the scrolling layer left by that many canvas units --
    // the hub is one wide surface and this is the camera along it. Commands
    // marked LAYER fixed (backdrop, gamertag, page selector, legend) ignore
    // it and stay pinned to the screen.
    // `timeSeconds` drives PULSE commands (see ManifestCommand::pulseSeconds);
    // it only needs to advance steadily, not to mean anything in particular.
    // Which part of the scene to draw. The hub's intro needs the split from
    // outside: its scrolling content rises into place while the backdrop and
    // the chrome stay put and only fade, so those two groups are drawn as
    // two passes under different transforms.
    enum class Layer { All, ScrollOnly, FixedOnly };

    // What the scene's IF / WHEN variables hold, by name: an item's value by
    // its id, anything else by whatever name the manifest gave it. A name
    // that is absent is the item's own starting value, or 0.
    using Variables = std::unordered_map<std::string, int>;

    // `frame` is where the scene's timeline stands, for a scene that has
    // TRACK blocks (see ManifestTrack); every tracked command is drawn as
    // its track says for that frame. Scenes without tracks ignore it.
    // `variables` chooses between the scene's IF / WHEN alternatives.
    // `listScroll` is how far the scene's scrolling list (LIST / LAYER list)
    // is scrolled, in canvas units.
    void Draw(QuadRenderer& renderer, const FocusWeights& weights = FocusWeights(),
        float scrollX = 0.0f, float timeSeconds = 0.0f, Layer layer = Layer::All,
        float frame = 0.0f, const Variables* variables = nullptr, float listScroll = 0.0f) const;

    const SceneManifest& GetManifest() const { return m_manifest; }

private:
    void DrawCommands(QuadRenderer& renderer, const std::vector<ManifestCommand>& commands,
        float scrollX, float timeSeconds, Layer layer = Layer::All) const;

    // Draws one item mid-morph at blend factor `f` (0 = item.normal, 1 =
    // item.focus, values between interpolated -- see BlendCommandLists in
    // Scene.cpp for how commands from the two lists are paired up).
    void DrawItemBlended(QuadRenderer& renderer, const ManifestItem& item, float f, float scrollX,
        float timeSeconds, Layer layer = Layer::All) const;

    // Bakes a glyph atlas page for every distinct text size in m_textSizes,
    // in every loaded face.
    void BakeTextSizes(ID3D11Device* device);

    // The atlas a command's text should be set in: the face named by its
    // FONT directive if that one loaded, else the scene's default.
    const FontAtlas& FontFor(const ManifestCommand& cmd) const;

    SceneManifest m_manifest;
    FontAtlas m_font;                                  // the scene's default face
    // Extra faces named by FONT directives, keyed by filename. Kept
    // separate from m_font so a scene that never mentions FONT costs
    // exactly what it did before.
    std::unordered_map<std::string, FontAtlas> m_fonts;
    std::wstring m_fontsDir;                           // where to look for those
    std::vector<float> m_textSizes; // distinct TEXT point sizes, for re-baking on resize
    // The manifest's tracks at the frame the current Draw was asked for.
    // Scratch state for that one call, hence mutable on a const draw path.
    mutable std::vector<TrackState> m_trackStates;
    // ...and its variables' values, indexed as ManifestCommand::ifVar is.
    mutable std::vector<int> m_varValues;
    mutable float m_listScroll = 0.0f;                 // ...and how far its list is scrolled

    // The commands of `list` whose IF / WHEN holds right now. Blending pairs
    // two lists command for command, so the ones not being drawn have to be
    // out of the way first.
    std::vector<ManifestCommand> Visible(const std::vector<ManifestCommand>& list) const;
    std::unordered_map<std::string, TextureLoader::LoadedTexture> m_textures; // keyed by manifest filename
};
