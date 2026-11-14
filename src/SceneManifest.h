#pragma once

#include <string>
#include <utility>
#include <vector>

// Parses the flat text format emitted by the Python cook script
// (xur_cook.py) that flattens a cracked .xur layout into an
// absolute-coordinate draw list -- see that script's docstring for the
// authoritative format spec. Reproduced here for reference:
//
//   CANVAS w h
//   RECT x y w h shape(0|1) a r g b
//   GRADIENT_RECT x y w h shape(0|1) radial(0|1) angleDegrees stopCount
//     STOP pos a r g b        (repeated stopCount times, indentation optional)
//   IMAGE x y w h filename a r g b
//   TEXT x y w h halign valign pointSize a r g b <text...>
//   TEXTV ...same...                 (turned a quarter turn clockwise about x,y)
//   ITEM id up down left right action viewX
//   STATE normal | STATE focus
//   ENDITEM
//   LAYER fixed | LAYER scroll | LAYER list
//   LIST x y w h / ROW y h           (a list that scrolls up and down -- see HasList)
//   FADEGROUP anchorX
//   FADE offPageAlpha span
//   STATE press                      (a third item state -- see ManifestItem::press)
//   TRACK id x y w h / KEY ... / ENDTRACK / MARK name frame
//                                    (timelines -- see ManifestTrack below)
//   AFTER scene                      (what follows a scene that plays to its end -- see GetAfter)
//   SETS name value                  (app state it sets when it does -- see GetSets)
//   ACTIONIF var value action        (inside an ITEM -- see ManifestItem::alternatives)
//   HIDEIF var value                 (inside an ITEM -- see ManifestItem::hiddenIf)
//   ENTER name value                 (app state it sets when it becomes the current scene -- see GetEnters)
//   SAY name text                    (a text variable's first value; a TEXT "{name}" draws it -- TextVars.h)
//   IF var value ... ENDIF           (draw these only while `var` is `value`)
//   VALUES count initial / WHEN value   (inside an ITEM -- see below)
//   # comment
//
// A TEXT whose string is exactly "{time}" draws the time of day instead.
//
// IF is how one cooked scene holds several mutually exclusive looks that
// only the running app can choose between: which page's legend the guide's
// frame shows, which battery icon. `var` is just a name; whoever draws the
// scene passes its value (Scene::Variables), and an unnamed variable is 0.
//
// An ITEM is a variable too, named by its id: its VALUE. `VALUES 2 1` gives
// it two (0 and 1) and starts it on 1, and `WHEN 1` inside a STATE block is
// `IF <this item> 1` -- so a checkbox's ticked and unticked rows, or a
// slider's eleven fills, are all cooked into the one item and A (or left /
// right) only changes a number. What changes it is the item's action:
// TOGGLE steps to the next value, RADIO:<group> sets this item to 1 and
// every other item of that group to 0, SLIDER moves it with left and right.
//
// ITEM opens a focusable element: everything between it and ENDITEM belongs
// to that element rather than to the scene's static draw list, split into a
// `normal` and a `focus` variant by STATE lines. The renderer draws the
// static list, then one variant per item depending on which item currently
// has focus -- so a screen's two appearances live in one file instead of two.
// `up`/`down`/`left`/`right` name the item focus moves to in that direction
// ("-" = no move, i.e. focus stays put), and `action` is what pressing A
// does: "GOTO:<sceneName>" switches scenes, "-" does nothing yet.
//
// `viewX` is where the camera belongs while this item is focused. It is what
// makes the hub one continuous surface instead of a stack of pages: every
// page's content sits side by side in one very wide scene, each item carries
// the scroll offset that frames its own page, and moving focus off the edge
// of one page onto the next simply slides the camera there. Paging is a
// consequence of ordinary focus movement rather than a separate mechanism.
//
// LAYER decides whether following commands scroll with that camera or are
// pinned to the screen. The dashboard's backdrop, gamertag, page selector and
// button legend stay put while the pages slide underneath them, so they are
// `fixed`; everything else is `scroll` (the default at the top of a file).
//
// FADE/FADEGROUP dim whatever the camera isn't looking at. FADE sets the
// scene-wide floor (`offPageAlpha`, 1.0 = never dim) and the distance over
// which content reaches it; FADEGROUP marks the camera position at which
// following commands are fully opaque -- their page, in other words. A
// command's alpha is then scaled by how far its group sits from the camera,
// so a page brightens as it slides in and dims as it leaves, and the two
// crossfade through each other mid-slide.
//
// This is per-command alpha rather than a translucent panel laid over the
// off-page area, which is what it looks like it could be. A panel has to
// stop somewhere: covering the pages means covering the full height of the
// screen, which also covers the legend and the gamertag that are supposed to
// sit above all of this. Scaling the content's own alpha dims exactly the
// content and nothing else. Commands on the fixed layer are never faded.
//
// `shape`: 0 = draw the full x/y/w/h rect, 1 = mask to the ellipse
// inscribed in it -- how XuiFigure circles/rings (e.g. the connection-test
// progress dots) get drawn as real circles rather than their flat bounding
// box; see xur_cook.py's ellipse detection and QuadRenderer::DrawRect's
// doc comment.
//
// `angleDegrees` (GRADIENT_RECT, linear only): the gradient axis, rotated
// from XUI's un-rotated LEFT-TO-RIGHT default. Nearly every vertical-looking
// gradient in the dashboard's assets is authored as -90 on top of that, and
// a button's focus highlight uses +90 (the same axis, reversed) -- so
// hardcoding top-to-bottom, as this format previously did, renders that
// highlight upside down and any genuinely horizontal gradient wrong.
//
// `halign`/`valign` (TEXT): 0/1/2 = left/center/right and top/center/bottom,
// decoded by the cook script from XUI's TextStyle bits. TEXT's x/y/w/h is the
// text's BOX, not a pen position -- alignment is resolved here, at draw time,
// because it needs the font metrics the atlas actually baked. Without this
// every label drew flush to its box's top-left, which put each menu caption
// hard against the top of its 47px row and left the connect-bar captions
// left-aligned instead of centered under their icons.
// This is intentionally NOT a general-purpose format parser -- it's the
// exact counterpart to what xur_cook.py writes, so it can stay simple
// (whitespace-tokenized, no escaping/quoting support) rather than robust
// against arbitrary input.
struct ManifestColor
{
    float a = 255.0f, r = 255.0f, g = 255.0f, b = 255.0f;
};

struct ManifestGradientStop
{
    float pos = 0.0f;
    ManifestColor color;
};

enum class ManifestCommandType
{
    Rect,
    GradientRect,
    Image,
    Text,
};

// One focusable element of a scene: a nav graph node plus the two draw lists
// that render it unfocused and focused.
struct ManifestItem
{
    std::string id;
    std::string navUp, navDown, navLeft, navRight;
    // Where the shoulder buttons go from here -- the SHOULDER directive.
    // A whole page sideways rather than one item, which on the hub is what
    // LB/RB do: jump to the next section without stepping through every tile
    // and without having to go up to the page selector first. Empty when the
    // scene doesn't declare them, which is every screen except the hub.
    std::string pageLeft, pageRight;
    // Marks an item you pass THROUGH rather than land on -- the RETURN
    // directive. The page selector is the only one: you flick up to it,
    // move sideways, and come back down, and leaving it should put you back
    // exactly where you came from rather than at the top of the column.
    // See Navigator::m_returnItem for the rule.
    bool returnPoint = false;
    // Marks an item that is present and focusable but does nothing -- the
    // DISABLED directive. Purely so the right SOUND plays: the console
    // answers a disabled item with btn_InactiveFocus / btn_InactiveSelect
    // rather than the ordinary click. The greyed-out LOOK is already baked
    // into the item's normal/focus command lists by the cook scripts, so
    // nothing in the renderer reads this.
    bool disabled = false;
    std::string action;
    // "ACTIONIF var value action": what A does instead, while the app's state
    // `var` holds `value` (Navigator::ActionOf) -- a profile's tile signs in
    // when signed out, and offers to sign out when not. The first that
    // matches wins; "-" is "nothing".
    struct Alternative
    {
        std::string var;
        int value = 0;
        std::string action;
    };
    std::vector<Alternative> alternatives;
    // "HIDEIF var value": not there while the app's state `var` holds `value`.
    // Focus passes through it along its own link (Navigator::Move); what it
    // draws is the manifest's to leave out (IF). The keyboard's fifth row of
    // keys, which only some of its character sets have.
    std::vector<std::pair<std::string, int>> hiddenIf;
    float viewX = 0.0f;      // camera x while this item is focused -- see LAYER/viewX above
    // "ROW y h", for an item that is a row of the scene's scrolling list: where
    // the row sits before any scrolling. The navigator scrolls the list just
    // far enough to keep the focused row inside the LIST window. 0 high: not
    // a list row.
    float rowY = 0.0f;
    float rowH = 0.0f;
    // VALUES: how many values this item can hold and which it starts on.
    // 1 and 0 for an ordinary item, which has nothing to choose between.
    int valueCount = 1;
    int initialValue = 0;
    std::vector<struct ManifestCommand> normal;
    std::vector<struct ManifestCommand> focus;
    // STATE press: what the focused item becomes under A, for a control
    // whose press is its own look rather than a dip back toward `normal`.
    // The guide's rows are the case: XuiButtonGuide's Press frames run the
    // highlight from (0,138,0) to a brighter (28,182,28), which is neither of
    // the other two states. Empty for everything else, which keeps the hub's
    // sink-and-spring press (see Navigator::PressWeight).
    std::vector<struct ManifestCommand> press;
};

struct ManifestCommand
{
    ManifestCommandType type = ManifestCommandType::Rect;
    float x = 0.0f, y = 0.0f, w = 0.0f, h = 0.0f;

    ManifestColor color;                        // Rect / Image (tint)
    bool radial = false;                        // GradientRect
    float angleDegrees = 0.0f;                  // GradientRect (linear) -- see the format comment above
    bool isEllipse = false;                     // Rect / GradientRect -- see the format comment above
    std::vector<ManifestGradientStop> stops;     // GradientRect

    std::string imageFile;                       // Image: bare filename, resolve against the scene's assets dir

    bool fixedToScreen = false;                  // ignore the scroll offset -- see LAYER above
    // "LAYER list": part of the scene's one scrolling list -- drawn moved up
    // by however far the list is scrolled, and cut off at the LIST window's
    // edges. (Also fixed, as far as the camera's x is concerned.)
    bool inList = false;
    float fadeAnchorX = 0.0f;                    // camera x at which this is fully opaque -- see FADEGROUP

    float pointSize = 0.0f;                      // Text
    int hAlign = 0;                              // Text: 0 left, 1 center, 2 right
    int vAlign = 0;                              // Text: 0 top, 1 center, 2 bottom
    std::string text;                             // Text
    bool quarterTurn = false;                     // Text: TEXTV -- see QuadRenderer::DrawTextAligned
    // Seconds for one full breath of a pulsing command, 0 for a still one.
    // The dashboard's page selector glows in and out while it has focus;
    // rather than special-casing that one element, any command can be marked
    // to oscillate and Scene::Draw scales its alpha by a raised cosine. The
    // trough is pulseFloor, the crest is the command's own authored alpha.
    float pulseSeconds = 0.0f;
    float pulseFloor = 0.0f;

    // Text: which TTF to set this run in, as a bare filename resolved
    // against the same fonts folder as the scene's default. Empty means the
    // default. Set by the FONT directive -- see the format comment above.
    std::string fontFile;

    // Index of the TRACK this command sits inside, -1 for none.
    int track = -1;

    // Drawn only while variable `ifVar` (an index into SceneManifest::
    // GetVars) holds `ifValue`; -1 = always. See IF / WHEN above.
    int ifVar = -1;
    int ifValue = 0;
};

// One animated XUI object, flattened: a TRACK block in the manifest.
//
//   TRACK id x y w h
//     KEY frame none|linear|ease easeIn easeOut easeScale show dx dy sx sy alpha
//     ...draw commands...
//   ENDTRACK
//   MARK name frame
//
// A .xur timeline keys an object's Position / Width / Scale / Opacity / Show
// at numbered frames, and the file's named frames (MARK) are the entry points
// into it -- "1To2" is frame 1, "1To2End" frame 12. The cook script flattens
// each animated object into one of these: the commands inside are that object
// drawn fully visible in its box (x y w h), and every KEY says what to do to
// them at that frame -- scale by (sx, sy) about the box's origin, move by
// (dx, dy), multiply alpha, or hide outright. Scene::Draw takes the frame and
// applies it, so playing an animation is nothing more than advancing a number.
//
// A track with no commands is still useful: it is how a caller reads where an
// object the scene doesn't draw itself (a hosted child scene) should be.
struct ManifestTrackKey
{
    float frame = 0.0f;
    int interp = 0;            // 0 none (hold), 1 linear, 2 ease -- governs the span AFTER this key
    float easeIn = 0.0f, easeOut = 0.0f, easeScale = 0.0f;
    bool show = true;
    float dx = 0.0f, dy = 0.0f, sx = 1.0f, sy = 1.0f, alpha = 1.0f;
};

struct ManifestTrack
{
    std::string id;
    float x = 0.0f, y = 0.0f, w = 0.0f, h = 0.0f;
    std::vector<ManifestTrackKey> keys;   // ascending frame
};

// A track evaluated at one frame.
struct TrackState
{
    bool visible = true;
    float x = 0.0f, y = 0.0f, w = 0.0f, h = 0.0f;   // the track's box, untransformed
    float dx = 0.0f, dy = 0.0f, sx = 1.0f, sy = 1.0f, alpha = 1.0f;
};

class SceneManifest
{
public:
    // Parses manifest.txt at `path`. Returns false on a missing/unreadable
    // file (canvas stays 0x0, command list stays empty) -- callers should
    // treat that as "nothing to draw this scene", not a fatal error, so one
    // bad/missing scene file doesn't take the whole app down.
    bool LoadFromFile(const std::wstring& path);

    float GetCanvasWidth() const { return m_canvasWidth; }
    float GetCanvasHeight() const { return m_canvasHeight; }
    const std::vector<ManifestCommand>& GetCommands() const { return m_commands; }
    const std::vector<ManifestItem>& GetItems() const { return m_items; }

    // Index of the item with this id, or -1. Linear because a scene has a
    // handful of items, not thousands.
    int FindItem(const std::string& id) const;

    // The id the scene should start focused on -- the first item that
    // declares any navigation or action, or empty if the scene has no items.
    // Which item focus starts on. A DEFAULTFOCUS line names it outright;
    // without one it falls back to the first ITEM declared.
    const std::string& GetDefaultFocus() const { return m_defaultFocus; }

    // How long focus takes to settle on this scene, in seconds -- the
    // FOCUSBLEND directive, 0 if the scene doesn't say. Different screens
    // want very different answers: a hub tile grows and brightens over
    // ~0.22s, where a settings row is two frames on the console. Keeping it
    // per-scene rather than one constant in Navigator is what lets both be
    // right, and it comes from the same place everything else does -- the
    // control's own timeline.
    float GetFocusBlendSeconds() const { return m_focusBlendSeconds; }

    // The press animation, from the PRESS directive: how long the focused
    // item takes to sink from its focused size back to its resting one, and
    // how long it takes to spring out again. Both 0 (the default) means the
    // scene doesn't animate presses at all.
    //
    // It is expressed as a time rather than a scale because the scale is
    // already known: sinking IS this item's focus state morphing back to its
    // normal one, so the press just drives the same 0..1 weight everything
    // else does. See Navigator::PressWeight.
    float GetPressInSeconds() const { return m_pressInSeconds; }
    float GetPressOutSeconds() const { return m_pressOutSeconds; }

    // The scene's entrance, from the INTRO directive: how long it takes, and
    // how far below its resting place the scrolling content starts. 0
    // seconds means the scene simply appears, which is what every screen
    // except the hub does.
    float GetIntroSeconds() const { return m_introSeconds; }
    float GetIntroRiseY() const { return m_introRiseY; }

    // How long the screen stays black before that entrance starts. The
    // console leaves a beat between the boot animation ending and the hub
    // arriving, and it reads as a deliberate buffer rather than a load
    // stall, so it is part of the entrance rather than an accident of
    // timing.
    float GetIntroHoldSeconds() const { return m_introHoldSeconds; }

    // Shape of the entrance's MOVEMENT over time. The console's own
    // keyframes are linear (ease_in and ease_out are both 0 on every one of
    // them), which is where this started, but linear travel over a distance
    // this short reads as sluggish however long it lasts -- constant
    // velocity gives the eye nothing to tell it the movement is nearly over.
    // Named here rather than hardcoded so alternatives are a re-cook instead
    // of a rebuild.
    enum class Ease { Linear, In, Out, InOut };
    Ease GetIntroEase() const { return m_introEase; }

    // Applies `ease` to a 0..1 progress value.
    static float ApplyEase(Ease ease, float p);

    // Alpha multiplier for a command at the given camera position -- 1 for
    // anything on the fixed layer or in a scene that never set FADE.
    float FadeFactor(const ManifestCommand& command, float scrollX) const;

    // Every TRACK evaluated at `frame`, indexed as ManifestCommand::track is.
    std::vector<TrackState> EvaluateTracks(float frame) const;

    // One track by id. Returns false (and leaves `out` hidden) if the scene
    // has no such track.
    bool EvaluateTrack(const std::string& id, float frame, TrackState& out) const;

    // The frame a MARK names, or `fallback` if the scene doesn't declare it.
    float GetMark(const std::string& name, float fallback = 0.0f) const;
    const std::vector<std::pair<std::string, float>>& GetMarks() const { return m_marks; }

    // "AFTER <scene>": the scene that takes this one's place when it has
    // played to its "Scene.End" mark. Empty: it is simply left, as B would.
    const std::string& GetAfter() const { return m_after; }

    // "SETS name value": app state this scene sets when it has played to its
    // "Scene.End" mark -- signing out is a scene that sets "signedout" to 1.
    // Every scene sees the app's state as variables (IF name value).
    const std::vector<std::pair<std::string, int>>& GetSets() const { return m_sets; }

    // "ENTER name value": app state this scene sets each time it becomes the
    // current one -- a keyboard opens on its letters, a pass code on no
    // buttons pressed.
    const std::vector<std::pair<std::string, int>>& GetEnters() const { return m_enters; }

    // Every variable an IF or WHEN in this scene names, indexed as
    // ManifestCommand::ifVar is.
    const std::vector<std::string>& GetVars() const { return m_vars; }

    // "LIST x y w h": the window a scrolling list shows through, in canvas
    // units -- XUI's list control, whose items run on past its own bottom
    // edge and scroll into it. One per scene. No height: no such list.
    bool HasList() const { return m_listH > 0.0f; }
    float GetListX() const { return m_listX; }
    float GetListY() const { return m_listY; }
    float GetListW() const { return m_listW; }
    float GetListH() const { return m_listH; }

    // XUI's "ease" interpolation: progress 0..1 -> eased progress 0..1.
    //
    // shortcut: this is a MODEL, not the console's formula -- that lives in
    // xam.xex's XUI runtime and isn't documented anywhere public. easeIn and
    // easeOut (-100..100) are read as how much slower than cruising speed the
    // span starts and ends (-100 in / +100 out, the values nearly every key
    // in the guide uses, mean "from rest" / "to rest"), and easeScale (0..100)
    // as where between the two ends top speed is reached. That reduces to the
    // textbook curves at the values actually authored: 50 is quadratic
    // ease-in-out, 0 is quadratic ease-out. Replace if the real one turns up.
    static float EaseTime(float t, float easeIn, float easeOut, float easeScale);

private:
    static TrackState Evaluate(const ManifestTrack& track, float frame);

    int VarIndex(const std::string& name);   // finds or adds

    std::vector<std::string> m_vars;
    float m_listX = 0.0f, m_listY = 0.0f, m_listW = 0.0f, m_listH = 0.0f;   // see HasList
    std::vector<ManifestTrack> m_tracks;
    std::vector<std::pair<std::string, float>> m_marks;
    std::string m_after;    // see GetAfter
    std::vector<std::pair<std::string, int>> m_sets;   // see GetSets
    std::vector<std::pair<std::string, int>> m_enters; // see GetEnters
    float m_canvasWidth = 0.0f;
    float m_canvasHeight = 0.0f;
    std::vector<ManifestCommand> m_commands;
    std::vector<ManifestItem> m_items;
    std::string m_defaultFocus;
    float m_focusBlendSeconds = 0.0f;  // FOCUSBLEND; 0 = use the caller's default
    float m_pressInSeconds = 0.0f;     // PRESS; 0 = this scene doesn't animate presses
    float m_pressOutSeconds = 0.0f;
    float m_introSeconds = 0.0f;       // INTRO; 0 = this scene has no entrance
    float m_introRiseY = 0.0f;
    float m_introHoldSeconds = 0.0f;   // black before the entrance begins
    Ease m_introEase = Ease::Linear;
    float m_offPageAlpha = 1.0f;   // FADE -- 1.0 means this scene never dims
    float m_fadeSpan = 1.0f;
};
