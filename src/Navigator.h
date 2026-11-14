#pragma once

#include "AudioEngine.h"
#include "Scene.h"

#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

// Holds every loaded scene plus the two pieces of state that make the
// dashboard interactive: which scene is on screen, and which of its items has
// focus. Both live here rather than in main.cpp so the frame loop stays a
// pump: feed it input, ask it what to draw.
//
// Focus is remembered PER SCENE. The console does the same thing -- paging
// away from a hub page and back returns you to the tile you left, not to the
// top of the list -- and it falls out naturally from keying the map by scene
// name instead of tracking one global focus id.
class Navigator
{
public:
    enum class Direction { Up, Down, Left, Right };

    // Loads every subdirectory of `scenesRoot` that contains a manifest.txt.
    // Returns the number of scenes loaded; 0 means nothing was found, which
    // the caller should treat as fatal (there is nothing to draw).
    size_t LoadAll(ID3D11Device* device, const std::wstring& scenesRoot, const std::wstring& fontPath);

    // Makes `name` the current scene. Returns false (and changes nothing) if
    // no such scene was loaded -- a stale GOTO in a hand-edited manifest
    // shouldn't blank the screen.
    bool SetScene(const std::string& name);

    const Scene* GetCurrentScene() const;
    const std::string& GetCurrentSceneName() const { return m_currentScene; }

    // Any loaded scene by name, or null. For a caller that composes several
    // scenes into one picture itself (the guide) rather than drawing the
    // render plan.
    const Scene* GetScene(const std::string& name) const;

    // Puts every scene's focus back on its default item, as if nothing had
    // been visited. The guide does this each time it opens: on the console
    // it is a new scene every time, not one that remembers.
    void ResetFocus();
    // ...or just one scene's.
    void ResetFocus(const std::string& scene);

    // How many scenes B would walk back through: one per GOTO still open.
    size_t GetHistoryDepth() const { return m_history.size(); }

    // The values a scene's items have been changed to -- a ticked checkbox,
    // a slider's position (see VALUES / WHEN in SceneManifest.h). Only items
    // that HAVE been changed are in it; Scene::Draw falls back to each
    // item's own starting value for the rest. They last as long as the
    // navigator does, so a setting made in the guide is still made the next
    // time it opens.
    const Scene::Variables& GetValues(const std::string& scene) const;

    // True while a press animation is still playing out.
    bool IsPressing() const { return m_press.active; }
    const std::string& GetFocusedItemId() const { return GetFocusedItemId(m_currentScene); }
    // ...and the item a scene that ISN'T current would have focus on.
    const std::string& GetFocusedItemId(const std::string& scene) const;

    // How focused each item of the current scene is right now: 1 fully
    // focused, 0 fully unfocused, anything between mid-morph. Items at 0
    // aren't in the map at all, so this is normally one or two entries.
    // Scene::Draw takes this straight and draws each item at its own weight.
    const Scene::FocusWeights& GetFocusWeights() const { return m_focusWeights; }

    // Jumps a whole section sideways, following the focused item's SHOULDER
    // links -- LB/RB on the hub. Returns false on a screen that doesn't
    // declare them, so the buttons are simply inert there.
    bool PageLeft();
    bool PageRight();

    // Moves focus along the current item's nav link for `dir`. Returns true
    // if focus actually moved, so the caller can play a sound / log.
    bool Move(Direction dir);

    // Runs the focused item's action. Currently only "GOTO:<scene>" exists;
    // returns false for an item with no action, so "A did nothing" is
    // distinguishable from "A navigated".
    bool Activate(const std::string& item = std::string());   // (`item`: press that one, wherever focus is)

    // What A would do on the focused item right now: its action, or the
    // ACTIONIF alternative the app's state selects. Empty for none. For
    // main.cpp, which handles the verbs the navigator doesn't (GUIDE:, BACK).
    // (`item`: that item rather than the focused one -- a shortcut's.)
    std::string FocusedAction(const std::string& item = std::string()) const;

    // The item of the current scene that a controller button presses
    // ("SHORTCUT button item"), or empty.
    std::string ShortcutItem(const std::string& button) const;

    // A piece of the app's state (m_state), 0 if nothing has set it.
    int GetState(const std::string& name) const
    {
        auto held = m_state.find(name);
        return (held != m_state.end()) ? held->second : 0;
    }

    // Drills into `sceneName` exactly as an item's GOTO does -- history
    // pushed, transition played. Public for the one caller that isn't an
    // item: the guide's dash commands (see main.cpp).
    bool PerformGoto(const std::string& sceneName);

    // Returns to the scene an Activate() drilled in from. Only Activate()
    // pushes history -- paging sideways between hub pages is lateral movement,
    // not depth, so B from a hub page shouldn't walk back through every page
    // visited on the way there.
    bool GoBack();

    // One queued sound: the cue, plus the scene it BELONGS TO.
    //
    // The scene has to travel with the cue rather than being read off the
    // navigator when the queue is drained, because the two disagree exactly
    // when it matters. Pressing A on the hub's System tile queues Select and
    // then changes scene, so by drain time "the current scene" is
    // SystemSettings and the press would be answered with the settings
    // list's own select sound -- and pressing A on the settings list's
    // Network Settings row would be answered with the hub's. A button press
    // belongs to the screen it was pressed on.
    struct QueuedSound
    {
        SoundCue cue;
        std::string scene;
    };

    // The sounds this navigator's last batch of moves earned, oldest first,
    // and clears the queue. Navigation decides WHICH cue -- it is the only
    // thing that knows a move landed on a disabled item, or ran along the
    // page selector rather than between two tiles -- but it deliberately
    // doesn't own an AudioEngine: main() pumps the queue into one, so the
    // navigator stays testable and silent-by-default.
    std::vector<QueuedSound> TakeSoundCues();

    // Eases the camera toward the focused item's viewX, and every item's
    // focus weight (see GetFocusWeights) toward where it belongs. Call once
    // per frame with real elapsed time; both use the same exponential-ease
    // form so they take the same wall-clock time regardless of frame rate.
    void Update(float deltaSeconds);

    float GetScrollX() const { return m_scrollX; }

    // One scene to draw, with the transform to draw it under. A frame is
    // normally a single layer at scale 1 / alpha 1; during a scene
    // transition it is two, ordered BACK TO FRONT.
    struct Layer
    {
        const Scene* scene = nullptr;
        Scene::FocusWeights weights;
        float scrollX = 0.0f;
        float scale = 1.0f;
        float alpha = 1.0f;
        float offsetY = 0.0f;                       // canvas units, for the hub's intro rise
        Scene::Layer content = Scene::Layer::All;   // which half of the scene this layer draws
        Scene::Variables values;                    // the scene's item values -- see GetValues
        float listScroll = 0.0f;                    // how far the scene's scrolling list is scrolled
        float frame = 0.0f;                         // where the scene's timeline (its TRACKs) is
    };

    // What to draw this frame. The caller just walks the list, sets each
    // layer's transform on the renderer and calls Scene::Draw -- it doesn't
    // need to know whether a transition is running.
    //
    // The transition itself is deliberately generic: it isn't "the hub going
    // to the network page", it's what EVERY GOTO and every B does, because
    // that's how the console animates every drill-in. Going deeper, the page
    // you're leaving pushes forward toward the viewer and fades out while
    // the new one only fades in; coming back, the deeper page fades out
    // while the page behind it eases back from that same pushed-forward
    // position. In both directions the SHALLOWER scene is the one that
    // moves, which is what sells it as stepping into and out of the surface
    // rather than two pictures crossfading.
    std::vector<Layer> BuildRenderPlan() const;

private:
    // The plan itself; BuildRenderPlan adds each layer's values to it.
    std::vector<Layer> BuildLayers() const;

    // What `item` of `scene` holds right now.
    int ValueOf(const std::string& scene, const ManifestItem& item) const;

    std::unordered_map<std::string, Scene::Variables> m_values;

public:

    // True while a scene transition is still running.
    bool IsTransitioning() const { return m_transition.t < 1.0f; }

    // Plays the current scene's entrance, if it declares one (INTRO). Called
    // once, when the dashboard first appears -- after the boot animation, or
    // at launch when there isn't one.
    void BeginIntro();

    // True while that entrance is running. Input is ignored for its
    // duration, as it is during a press: the console's hub is not
    // interactive until it has finished arriving.
    bool IsIntroPlaying() const { return m_intro.active; }

    // Re-rasterizes the current scene's text for a window of this size, if
    // it changed. The scale is worked out from THAT scene's own canvas, the
    // same way QuadRenderer::SetLetterboxViewport does it, so a scene with a
    // different canvas size gets its own correct scale when it becomes
    // current. Safe and cheap to call every frame.
    //
    // `allScenes` does it for every loaded scene instead of just the current
    // one -- for the guide, which draws several of its scenes at once.
    void SetOutputSize(ID3D11Device* device, float windowWidth, float windowHeight,
        bool allScenes = false);

    // Snaps the camera to the focused item without animating -- used when a
    // scene is first entered, so it doesn't slide in from wherever the last
    // scene's camera happened to be.
    void SnapCamera();

private:
    // Where the camera belongs right now: the focused item's viewX.
    float TargetScrollX() const;

    // Moves focus to `link`, which may be "item" or "scene:item". Shared by
    // the d-pad and the shoulders so cross-scene links, dangling-link
    // rejection and the morph all behave identically for both.
    bool FollowLink(const std::string& link);
    bool FollowShoulder(bool forward);

    // Ends any in-progress tile morph immediately: the focused item jumps to
    // weight 1 and every other item drops out. Used alongside SnapCamera
    // whenever the scene itself changes -- morphing a tile from the OLD
    // scene into a state that belongs to the NEW one makes no sense, the
    // same reasoning that makes a scene change a camera cut rather than a
    // slide.
    void SnapFocusWeights();

    std::unordered_map<std::string, std::unique_ptr<Scene>> m_scenes;
    std::vector<std::string> m_sceneOrder;   // load order, for a stable default
    std::unordered_map<std::string, std::string> m_focusByScene;

    // Where focus should go when it leaves a RETURN item (see
    // ManifestItem::returnPoint). Set when focus steps ONTO a return item
    // from an ordinary one, cleared when it moves BETWEEN two return items,
    // and consumed when it steps back off onto an ordinary one.
    //
    // That middle rule is what makes the two cases the console distinguishes
    // fall out of one mechanism: going up to the selector and straight back
    // down returns you to the tile you left, because nothing cleared it;
    // going up, along to another section and then down lands on that
    // section's own entry tile, because moving along the selector threw the
    // memory away. Cleared on a scene change too -- an item id is only
    // meaningful within its own scene.
    std::string m_returnItem;

    // The press animation: A held down on a tile sinks it back to its
    // resting size, then springs it out again, so the button reads as having
    // been clicked rather than the screen simply changing.
    //
    // Modelled as the item's own focus weight running 1 -> 0 -> 1, not as a
    // separate scale. The console's skin says that is literally what it is:
    // btn_LiveTile's Press frames (36..55 in
    // XURs/xur_json_out/archive_17/skin.json) key _slotGroup's Scale at
    // 1.08 -> 1.00 -> 1.08, and 1.08 is the focused size while 1.00 is the
    // resting one -- hub_build.py's independently measured FOCUS_SCALE is
    // 1.077. Everything else in those frames falls out of the same morph:
    // the shadow weakening (0.4 -> 0.1 opacity) and the corner highlight
    // fading (0.44 -> 0) are what our normal state already looks like.
    //
    // Which means no renderer support is needed at all -- the blend that
    // draws a half-focused tile every time you move is the blend that draws
    // a pressed one.
    struct PressAnim
    {
        std::string item;   // what is being pressed
        std::string scene;  // ...and on which screen
        float t = 0.0f;     // seconds since the press began
        bool active = false;
    };
    PressAnim m_press;

    // The scene entrance: content rising into place while everything fades
    // up from nothing.
    //
    // Straight off HostScene's TransTo frames (1..90) in
    // XURs/xur_json_out/archive_31/hubhomepage.json, whose Hub target keys
    //
    //     frame  1   Opacity 0.0   Position (0, 90, 0)
    //     frame 90   Opacity 1.0   Position (0,  0, 0)
    //
    // linear, no easing -- so the hub starts 90 units low and fully
    // transparent and arrives 89 frames later. The same timeline fires
    // control_kinect at frame 1, which is why FirstStart plays as this
    // begins rather than when it ends.
    //
    // One deviation, deliberate: the console also holds its AnchorLayer at
    // zero opacity until frame 50 and fades it in over the last 40, so the
    // chrome arrives after the tiles. Our fixed layer bundles the BACKDROP
    // in with the chrome (the console keeps it in a separate scene,
    // archive_26/DashBkgnd), and delaying the backdrop would open the
    // animation on black. Both halves fade together until the backdrop gets
    // a layer of its own.
    struct IntroAnim
    {
        std::string scene;
        float t = 0.0f;
        bool active = false;
    };
    IntroAnim m_intro;

    // A GOTO that has been asked for but is waiting for the press to finish.
    //
    // The console plays the whole press -- sink AND spring back -- and only
    // then changes screen. Switching at the moment A is read instead means
    // the transition's fade eats the spring, so you see the tile go in and
    // never come out. Empty when nothing is pending.
    std::string m_pendingAction;

    // True while a press is playing that a screen change is waiting on. Input
    // is ignored for that spell: letting focus move would abandon the press
    // and then fire the pending GOTO from a tile that no longer has focus.
    bool IsAwaitingPress() const { return !m_pendingAction.empty(); }

    // 0 at the start of the entrance, 1 once it has arrived.
    float IntroProgress() const;

    // Where the press is in its curve right now, as a focus weight: 1 at
    // rest, falling to 0 at the bottom of the press, back to 1 on release.
    float PressWeight() const;

    // in/out durations for the scene the press belongs to, 0 if that scene
    // doesn't animate presses (or has gone away).
    void PressTimings(float& inSeconds, float& outSeconds) const;

    // Queued by the move/activate/back paths, drained by TakeSoundCues.
    std::vector<QueuedSound> m_soundCues;

    // Queues `cue` against the scene that is current RIGHT NOW, which is the
    // screen the input happened on. Call it before any SetScene the same
    // input triggers; where that isn't possible, name the scene explicitly.
    void QueueSound(SoundCue cue);
    void QueueSound(SoundCue cue, const std::string& scene);

    // True when `id` names an item on the current scene carrying DISABLED.
    bool IsDisabled(const std::string& id) const;
    std::vector<std::string> m_history;
    std::string m_currentScene;

    // ~0.22s to settle, which is about where the console's page slide lands:
    // fast enough that holding a direction still feels responsive, slow
    // enough that the pages read as one surface moving rather than a cut.
    static constexpr float kCameraSmoothing = 14.0f;
    float m_scrollX = 0.0f;

    // A scene's scrolling list (LIST / ROW in its manifest). Where each
    // scene's list should be scrolled to is remembered by scene, as focus
    // is, and moves only as far as it must to keep the focused row inside
    // the window -- so going down a long list the focus runs to the bottom
    // row and then the list moves under it, and coming back up it runs to
    // the top row first. m_listScroll is the current scene's, easing there.
    float ListTarget();
    std::unordered_map<std::string, float> m_listTargets;
    // How long each scene has been the current one, in frames (60 a second):
    // what plays a scene's TRACKs. It starts over each time the scene is
    // arrived at, and a scene with a "Scene.End" mark is left when it gets
    // there -- see Update.
    std::unordered_map<std::string, float> m_sceneFrames;
    // The app's state, by name: 1 for each scene that has played to its
    // Scene.End at least once (not one left early), and whatever such scenes
    // set as they end ("SETS signedout 1"). Every scene sees it as variables
    // (BuildRenderPlan), and an item's action can depend on it (ActionOf).
    // It lasts until the app closes.
    std::unordered_map<std::string, int> m_state;
    std::string ActionOf(const ManifestItem& item) const;
    float m_listScroll = 0.0f;
    std::string m_listScene;

    // The tile-selection morph: on the real console, moving focus doesn't
    // jump-cut between a tile's unfocused and focused looks -- one tile
    // shrinks back down while the other grows and brightens, at the same
    // time. kFocusBlendSmoothing is a little faster than the camera's
    // kCameraSmoothing (~0.17s vs ~0.22s) because tile-to-tile feedback
    // should read as immediate even while a slower page slide is still
    // catching up alongside it.
    //
    // Modelled as a WEIGHT PER ITEM, all easing every frame, rather than as
    // one "previous item + one blend factor". The single-blend version was
    // only correct while at most one tile was moving: pressing a direction
    // again mid-morph overwrote the leaving tile with the one that had just
    // been focused and reset the blend to 0, which asserts that tile was
    // fully focused when it was really only part-grown -- so it snapped up
    // to full size to start shrinking from, and the abandoned tile snapped
    // straight back down. That popped a wrongly-sized tile and its shine on
    // screen for a frame, which is exactly what you see flicking between two
    // items quickly. Per-item weights have no such assumption: whatever a
    // tile's weight is when focus changes again, it just starts easing the
    // other way from there, and any number can be in flight at once.
    static constexpr float kFocusBlendSmoothing = 18.0f;
    Scene::FocusWeights m_focusWeights;

    // A scene transition in flight. `t` runs 0 -> 1 over kTransitionSeconds
    // and sits at 1 when nothing is happening.
    struct Transition
    {
        std::string outgoing;      // the scene being left
        std::string outgoingFocus; // ...and the item that had focus on it
        float outgoingScrollX = 0.0f; // frozen: a leaving page shouldn't still be sliding
        float t = 1.0f;
        bool forward = true;       // true = drilling in (Activate), false = coming back (B)
    };

    // Starts one, from state captured BEFORE the scene actually changed.
    void BeginTransition(const std::string& from, const std::string& fromFocus,
        float fromScrollX, bool forward);

    // A fixed duration rather than the exponential ease the camera and the
    // focus weights use: those chase a target that can move again at any
    // moment, where this has a definite start and end and wants to feel like
    // one deliberate movement.
    static constexpr float kTransitionSeconds = 0.28f;

    // How far the shallower scene pushes toward the viewer. Small on
    // purpose -- it reads as depth, not as a zoom.
    static constexpr float kTransitionZoom = 1.12f;

    Transition m_transition;
};
