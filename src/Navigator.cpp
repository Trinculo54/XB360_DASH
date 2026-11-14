#include "Navigator.h"
#include "FileUtil.h"
#include "TextVars.h"

#include <cstdlib>

#include <windows.h>

#include <cmath>
#include <iterator>
#include <utility>
#include <string>

namespace
{
    const std::string kEmpty;

    // Enumerates immediate subdirectories of `root`. Deliberately not
    // recursive: a scene folder owns an assets/ subfolder, and recursing
    // would try to load that as a scene of its own.
    std::vector<std::wstring> ListSubdirectories(const std::wstring& root)
    {
        std::vector<std::wstring> out;
        WIN32_FIND_DATAW find = {};
        HANDLE handle = FindFirstFileW((root + L"\\*").c_str(), &find);
        if (handle == INVALID_HANDLE_VALUE)
        {
            return out;
        }
        do
        {
            if (!(find.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
            {
                continue;
            }
            std::wstring name = find.cFileName;
            if (name == L"." || name == L"..")
            {
                continue;
            }
            out.push_back(name);
        } while (FindNextFileW(handle, &find));
        FindClose(handle);
        return out;
    }
}

size_t Navigator::LoadAll(ID3D11Device* device, const std::wstring& scenesRoot, const std::wstring& fontPath)
{
    for (const std::wstring& dirName : ListSubdirectories(scenesRoot))
    {
        std::wstring sceneDir = scenesRoot + L"\\" + dirName;
        auto scene = std::make_unique<Scene>();
        if (!scene->Load(device, sceneDir, fontPath))
        {
            continue; // no manifest.txt in there -- not a scene folder
        }
        std::string name = FileUtil::WideToUtf8(dirName);
        m_focusByScene[name] = scene->GetManifest().GetDefaultFocus();
        m_scenes[name] = std::move(scene);
        m_sceneOrder.push_back(name);
    }

    if (!m_sceneOrder.empty() && m_currentScene.empty())
    {
        m_currentScene = m_sceneOrder.front();
    }
    return m_scenes.size();
}

bool Navigator::SetScene(const std::string& name)
{
    if (m_scenes.find(name) == m_scenes.end())
    {
        return false;
    }
    const bool changed = (name != m_currentScene);
    m_currentScene = name;
    for (const std::pair<std::string, int>& enter : m_scenes[name]->GetManifest().GetEnters())
    {
        m_state[enter.first] = enter.second;   // "ENTER name value"
    }
    if (changed)
    {
        m_returnItem.clear(); // an item id only means anything on its own scene
        // Entering a different scene is a cut, not a slide -- animating the
        // camera across would sweep through content that belongs to neither
        // scene. Same reasoning for the tile morph: there is no sensible
        // "shrink" for a tile that isn't even on the new scene.
        SnapCamera();
        SnapFocusWeights();
    }
    return true;
}

void Navigator::SnapFocusWeights()
{
    m_focusWeights.clear();
    const std::string& focused = GetFocusedItemId();
    if (!focused.empty())
    {
        m_focusWeights[focused] = 1.0f;
    }
}

std::vector<Navigator::QueuedSound> Navigator::TakeSoundCues()
{
    std::vector<QueuedSound> out;
    out.swap(m_soundCues);
    return out;
}

void Navigator::QueueSound(SoundCue cue)
{
    QueueSound(cue, m_currentScene);
}

void Navigator::QueueSound(SoundCue cue, const std::string& scene)
{
    m_soundCues.push_back(QueuedSound{ cue, scene });
}

bool Navigator::IsDisabled(const std::string& id) const
{
    const Scene* scene = GetCurrentScene();
    if (!scene || id.empty())
    {
        return false;
    }
    const int index = scene->GetManifest().FindItem(id);
    return (index >= 0) && scene->GetManifest().GetItems()[index].disabled;
}

void Navigator::BeginIntro()
{
    const Scene* scene = GetCurrentScene();
    if (!scene || scene->GetManifest().GetIntroSeconds() <= 0.0f)
    {
        return; // this screen just appears
    }
    m_intro.scene = m_currentScene;
    m_intro.t = 0.0f;
    m_intro.active = true;
}

float Navigator::IntroProgress() const
{
    if (!m_intro.active)
    {
        return 1.0f;
    }
    auto it = m_scenes.find(m_intro.scene);
    if (it == m_scenes.end() || !it->second)
    {
        return 1.0f;
    }
    const SceneManifest& manifest = it->second->GetManifest();
    const float seconds = manifest.GetIntroSeconds();
    if (seconds <= 0.0f)
    {
        return 1.0f;
    }
    // The hold comes first and counts as "not started": progress stays at 0
    // through it, which BuildRenderPlan reads as "draw nothing".
    const float after = m_intro.t - manifest.GetIntroHoldSeconds();
    if (after <= 0.0f)
    {
        return 0.0f;
    }
    const float p = after / seconds;
    return (p >= 1.0f) ? 1.0f : p;
}

void Navigator::PressTimings(float& inSeconds, float& outSeconds) const
{
    inSeconds = 0.0f;
    outSeconds = 0.0f;
    auto it = m_scenes.find(m_press.scene);
    if (it == m_scenes.end() || !it->second)
    {
        return;
    }
    inSeconds = it->second->GetManifest().GetPressInSeconds();
    outSeconds = it->second->GetManifest().GetPressOutSeconds();
}

float Navigator::PressWeight() const
{
    if (!m_press.active)
    {
        return 1.0f;
    }
    float inSeconds = 0.0f, outSeconds = 0.0f;
    PressTimings(inSeconds, outSeconds);
    if (inSeconds <= 0.0f && outSeconds <= 0.0f)
    {
        return 1.0f;
    }
    // Which way the press goes. An item with a STATE press runs UP from 1
    // toward 2 -- Scene::Draw reads anything past 1 as focus morphing into
    // that state -- where one without sinks DOWN toward 0, its resting look.
    // The curve is otherwise the same, so it is computed once as "how far
    // into the press" and pointed in one direction or the other.
    float direction = -1.0f;
    auto pressed = m_scenes.find(m_press.scene);
    if (pressed != m_scenes.end() && pressed->second)
    {
        const SceneManifest& manifest = pressed->second->GetManifest();
        const int index = manifest.FindItem(m_press.item);
        if (index >= 0 && !manifest.GetItems()[index].press.empty())
        {
            direction = 1.0f;
        }
    }

    // Linear both ways, and asymmetric -- 8 frames down, 12 back up, with
    // ease_in/ease_out both 0 on every one of those keyframes. The snap down
    // being faster than the spring back is most of what makes it read as a
    // press rather than a pulse, so it is not smoothed here.
    // 0 untouched, 1 fully pressed. With no way back out declared, a sinking
    // press ends released and a press-state one ends held, until Update
    // retires it.
    float depth = (direction > 0.0f) ? 1.0f : 0.0f;
    if (m_press.t < inSeconds)
    {
        depth = m_press.t / inSeconds;
    }
    else if (outSeconds > 0.0f)
    {
        const float k = (m_press.t - inSeconds) / outSeconds;
        depth = (k >= 1.0f) ? 0.0f : 1.0f - k;
    }
    return 1.0f + direction * depth;
}

float Navigator::TargetScrollX() const
{
    const Scene* scene = GetCurrentScene();
    if (!scene)
    {
        return 0.0f;
    }
    int index = scene->GetManifest().FindItem(GetFocusedItemId());
    return (index < 0) ? 0.0f : scene->GetManifest().GetItems()[index].viewX;
}

float Navigator::ListTarget()
{
    float& target = m_listTargets[m_currentScene];
    const Scene* scene = GetCurrentScene();
    if (!scene || !scene->GetManifest().HasList())
    {
        return target;
    }
    const SceneManifest& manifest = scene->GetManifest();
    const int index = manifest.FindItem(GetFocusedItemId());
    if (index >= 0 && manifest.GetItems()[index].rowH > 0.0f)
    {
        const ManifestItem& row = manifest.GetItems()[index];
        const float top = manifest.GetListY();
        const float bottom = top + manifest.GetListH();
        if (row.rowY - target < top)
        {
            target = row.rowY - top;
        }
        else if (row.rowY + row.rowH - target > bottom)
        {
            target = row.rowY + row.rowH - bottom;
        }
    }
    return target;
}

void Navigator::SnapCamera()
{
    m_scrollX = TargetScrollX();
}

void Navigator::Update(float deltaSeconds)
{
    const float target = TargetScrollX();
    // Frame-rate independent exponential ease. The 1-exp form (rather than a
    // raw lerp by k*dt) is what keeps a long frame from overshooting.
    const float t = 1.0f - std::exp(-kCameraSmoothing * deltaSeconds);
    m_scrollX += (target - m_scrollX) * t;
    if (std::fabs(target - m_scrollX) < 0.05f)
    {
        m_scrollX = target; // settle exactly, so a parked camera stops jittering sub-pixel
    }

    const float listTarget = ListTarget();
    float& frame = m_sceneFrames[m_currentScene];
    if (m_listScene != m_currentScene)
    {
        // A page arrives already scrolled to wherever its focus is.
        m_listScene = m_currentScene;
        m_listScroll = listTarget;
        frame = 0.0f;   // ...and at the start of its timeline, if it has one
    }
    else
    {
        frame += deltaSeconds * 60.0f;   // XUI timelines are in 60ths of a second
    }
    // A scene that runs its course ("MARK Scene.End <frame>" -- a connection
    // test) leaves when its timeline gets there, as B would.
    if (const Scene* playing = GetCurrentScene())
    {
        const float end = playing->GetManifest().GetMark("Scene.End");
        if (end > 0.0f && frame >= end)
        {
            // ...and is remembered as having done so, along with whatever
            // app state it says it sets ("SETS signedout 1").
            m_state[m_currentScene] = 1;
            for (const std::pair<std::string, int>& set : playing->GetManifest().GetSets())
            {
                m_state[set.first] = set.second;
            }
            const std::string after = playing->GetManifest().GetAfter();
            const std::string from = m_currentScene;
            const std::string fromFocus = GetFocusedItemId();
            const float fromScrollX = m_scrollX;
            if (after.empty())
            {
                GoBack();
            }
            else if (SetScene(after))
            {
                // "AFTER <scene>": another scene takes its place (a test that
                // failed, its results). The one that ended is not somewhere to
                // come back to, so it isn't pushed -- and if the one taking
                // over is already behind it (the test was restarted from
                // there), B from it goes where it did before.
                auto behind = std::find(m_history.begin(), m_history.end(), after);
                m_history.erase(behind, m_history.end());
                BeginTransition(from, fromFocus, fromScrollX, /*forward=*/true);
            }
        }
    }
    m_listScroll += (listTarget - m_listScroll) * t;
    if (std::fabs(listTarget - m_listScroll) < 0.05f)
    {
        m_listScroll = listTarget;
    }

    // Ease every tracked item's focus weight toward where it belongs -- 1 for
    // the focused item, 0 for everything else -- rather than driving a single
    // transition between one named pair of tiles. A tile that was only
    // halfway grown when focus moved on again just eases back down from
    // halfway, which is the whole point: nothing ever asserts a tile was at a
    // state it hadn't actually reached (see kFocusBlendSmoothing in the .h).
    // Rate comes from the scene when it names one. An exponential ease is
    // within 2% of settled at k*t = 4, so a scene asking for T seconds wants
    // k = 4/T; a scene that says nothing keeps the hub's default.
    float smoothing = kFocusBlendSmoothing;
    if (const Scene* current = GetCurrentScene())
    {
        const float seconds = current->GetManifest().GetFocusBlendSeconds();
        if (seconds > 0.0f)
        {
            smoothing = 4.0f / seconds;
        }
    }
    const float blendT = 1.0f - std::exp(-smoothing * deltaSeconds);
    const std::string focused = GetFocusedItemId();
    if (!focused.empty())
    {
        m_focusWeights.emplace(focused, 0.0f); // no-op if it's already tracked
    }
    for (auto it = m_focusWeights.begin(); it != m_focusWeights.end(); )
    {
        const float goal = (it->first == focused) ? 1.0f : 0.0f;
        it->second += (goal - it->second) * blendT;
        if (std::fabs(goal - it->second) < 0.01f)
        {
            it->second = goal; // settle exactly rather than creeping forever
        }
        // Drop items that have finished easing back to unfocused, so the map
        // stays at the one or two entries actually in play rather than
        // growing to every tile ever visited. An item at 0 draws identically
        // whether it's listed or not.
        it = (goal == 0.0f && it->second <= 0.0f) ? m_focusWeights.erase(it) : std::next(it);
    }

    if (m_intro.active)
    {
        m_intro.t += deltaSeconds;
        // Not IntroProgress() >= 1: that is also true throughout the hold,
        // where it means "not begun" rather than "finished".
        auto introScene = m_scenes.find(m_intro.scene);
        const float total = (introScene != m_scenes.end() && introScene->second)
            ? introScene->second->GetManifest().GetIntroHoldSeconds()
                + introScene->second->GetManifest().GetIntroSeconds()
            : 0.0f;
        if (m_intro.scene != m_currentScene || m_intro.t >= total)
        {
            m_intro.active = false;
            m_intro.scene.clear();
        }
    }

    // The press runs on its own clock and OVERWRITES the weight the easing
    // above produced for that one item -- it is a scripted movement with a
    // definite shape, not a chase toward a target, and letting the two fight
    // would round its corners off.
    if (m_press.active)
    {
        float inSeconds = 0.0f, outSeconds = 0.0f;
        PressTimings(inSeconds, outSeconds);
        m_press.t += deltaSeconds;
        // A press whose scene is no longer current is NOT abandoned: that is
        // exactly the drill-in case, where it still has to play out on the
        // transition's outgoing layer. It just has no weight to write here,
        // since m_focusWeights belongs to the current scene.
        const bool onCurrentScene = (m_press.scene == m_currentScene);
        const bool overtaken = onCurrentScene && m_press.item != focused;
        if (overtaken || m_press.t >= inSeconds + outSeconds)
        {
            // Finished, or overtaken: moving focus mid-press abandons it
            // rather than leaving a tile stuck part-sunk while the weight
            // easing tries to take it somewhere else.
            const bool completed = !overtaken;
            m_press.active = false;
            m_press.item.clear();
            m_press.scene.clear();
            if (!m_pendingAction.empty())
            {
                // Deliberately after the weights above have settled and
                // before the transition tick below, so the GOTO's own
                // transition starts on this same frame rather than a frame
                // late. A press that got overtaken drops its pending action
                // instead of firing it from wherever focus ended up.
                const std::string action = m_pendingAction;
                m_pendingAction.clear();
                if (completed)
                {
                    PerformGoto(action.substr(5));
                }
            }
        }
        else if (onCurrentScene)
        {
            m_focusWeights[m_press.item] = PressWeight();
        }
    }

    if (m_transition.t < 1.0f)
    {
        m_transition.t += deltaSeconds / kTransitionSeconds;
        if (m_transition.t >= 1.0f)
        {
            m_transition.t = 1.0f;
            m_transition.outgoing.clear(); // done -- stop holding a reference to the old scene
        }
    }
}

void Navigator::BeginTransition(const std::string& from, const std::string& fromFocus,
    float fromScrollX, bool forward)
{
    if (from.empty() || from == m_currentScene)
    {
        return; // didn't actually go anywhere -- nothing to animate
    }
    m_transition.outgoing = from;
    m_transition.outgoingFocus = fromFocus;
    m_transition.outgoingScrollX = fromScrollX;
    m_transition.forward = forward;
    m_transition.t = 0.0f;
}

const Scene::Variables& Navigator::GetValues(const std::string& scene) const
{
    static const Scene::Variables kNone;
    auto it = m_values.find(scene);
    return (it == m_values.end()) ? kNone : it->second;
}

int Navigator::ValueOf(const std::string& scene, const ManifestItem& item) const
{
    const Scene::Variables& values = GetValues(scene);
    auto it = values.find(item.id);
    return (it == values.end()) ? item.initialValue : it->second;
}

std::vector<Navigator::Layer> Navigator::BuildRenderPlan() const
{
    std::vector<Layer> plan = BuildLayers();
    for (Layer& layer : plan)
    {
        // A layer only carries the scene; its values are kept by name.
        for (const auto& entry : m_scenes)
        {
            if (entry.second.get() == layer.scene)
            {
                layer.values = GetValues(entry.first);
                // The current scene's list is wherever it has eased to; a
                // scene on its way out stays where it was left.
                auto scrolled = m_listTargets.find(entry.first);
                layer.listScroll = (entry.first == m_currentScene) ? m_listScroll
                    : (scrolled != m_listTargets.end()) ? scrolled->second : 0.0f;
                // Where its timeline is: running for the current scene, and
                // wherever it had got to for one on its way out.
                auto played = m_sceneFrames.find(entry.first);
                layer.frame = (played != m_sceneFrames.end()) ? played->second : 0.0f;
                // Every scene sees the app's state: whether another scene
                // has run its course ("IF <scene's name> 1" -- Network
                // Settings, of its test), and what such scenes have set
                // ("IF signedout 1").
                for (const std::pair<const std::string, int>& held : m_state)
                {
                    layer.values[held.first] = held.second;
                }
                // Which way the list has more rows -- what its two arrows
                // show (IF listup 1 / IF listdown 1). By where the list is
                // GOING rather than where it has eased to: an arrow changes
                // as the scroll starts, not as it settles.
                const SceneManifest& manifest = layer.scene->GetManifest();
                if (manifest.HasList())
                {
                    const float target = (scrolled != m_listTargets.end()) ? scrolled->second : 0.0f;
                    float end = 0.0f;
                    for (const ManifestItem& item : manifest.GetItems())
                    {
                        end = (item.rowY + item.rowH > end) ? item.rowY + item.rowH : end;
                    }
                    layer.values["listup"] = target > 0.5f;
                    layer.values["listdown"] = end - target > manifest.GetListY() + manifest.GetListH() + 0.5f;
                }
                break;
            }
        }
    }
    return plan;
}

std::vector<Navigator::Layer> Navigator::BuildLayers() const
{
    std::vector<Layer> plan;
    const Scene* current = GetCurrentScene();

    if (m_transition.t >= 1.0f || m_transition.outgoing.empty())
    {
        if (!current)
        {
            return plan;
        }
        if (m_intro.active && m_intro.scene == m_currentScene)
        {
            const float p = IntroProgress();
            if (p <= 0.0f)
            {
                // Still in the black beat between the boot animation and the
                // hub. An empty plan draws nothing at all, and the render
                // target is cleared to black every frame, so this IS the
                // black screen -- no need to draw one.
                return plan;
            }
            // Eased for the MOVEMENT, raw for the fade. Easing the opacity
            // too would hold the screen near black for longer at the start,
            // which is the opposite of the complaint that prompted this --
            // the brightening should be steady while the travel is shaped.
            const float moved = SceneManifest::ApplyEase(
                current->GetManifest().GetIntroEase(), p);
            const float rise = current->GetManifest().GetIntroRiseY() * (1.0f - moved);
            // Two passes over one scene. The scrolling content -- tiles,
            // panels -- rises; the backdrop and chrome stay where they are
            // and only fade, because a full-canvas backdrop shifted up by 90
            // units would show 90 units of nothing along the bottom edge.
            // Fixed first so the risen content lands on top of the backdrop,
            // which is the order Scene::Draw uses within a single pass.
            Layer fixedLayer{ current, m_focusWeights, m_scrollX, 1.0f, p, 0.0f, Scene::Layer::FixedOnly };
            Layer scrollLayer{ current, m_focusWeights, m_scrollX, 1.0f, p, rise, Scene::Layer::ScrollOnly };
            plan.push_back(fixedLayer);
            plan.push_back(scrollLayer);
            return plan;
        }
        plan.push_back(Layer{ current, m_focusWeights, m_scrollX, 1.0f, 1.0f, 0.0f, Scene::Layer::All });
        return plan;
    }

    auto outgoingIt = m_scenes.find(m_transition.outgoing);
    const Scene* outgoing = (outgoingIt == m_scenes.end()) ? nullptr : outgoingIt->second.get();
    if (!outgoing || !current)
    {
        if (current)
        {
            plan.push_back(Layer{ current, m_focusWeights, m_scrollX, 1.0f, 1.0f });
        }
        return plan;
    }

    // Smoothstep rather than the raw ratio: eased at both ends, so the
    // movement starts and stops rather than cutting in and out at speed.
    const float t = m_transition.t;
    const float e = t * t * (3.0f - 2.0f * t);

    // The scene being left keeps the look it had when you left it -- the
    // item you pressed A on stays focused as the page pushes away, and its
    // camera is frozen where it was rather than still easing somewhere.
    Layer outgoingLayer{ outgoing, Scene::FocusWeights(), m_transition.outgoingScrollX, 1.0f, 1.0f };
    if (!m_transition.outgoingFocus.empty())
    {
        // Fully focused, with no press to account for: a GOTO waits for the
        // press to finish before it changes scene at all (m_pendingAction),
        // and the press ends back at weight 1, so by the time a transition
        // exists the tile has already sprung out.
        outgoingLayer.weights[m_transition.outgoingFocus] = 1.0f;
    }
    Layer incomingLayer{ current, m_focusWeights, m_scrollX, 1.0f, 1.0f };

    // Whichever scene is the SHALLOWER of the two is the one that moves in
    // depth; the deeper one only fades. Drilling in, that's the scene being
    // left; coming back, it's the one being returned to -- the same
    // movement, played forwards and then backwards.
    Layer& shallow = m_transition.forward ? outgoingLayer : incomingLayer;
    Layer& deep = m_transition.forward ? incomingLayer : outgoingLayer;

    shallow.scale = m_transition.forward ? (1.0f + (kTransitionZoom - 1.0f) * e)
                                         : (kTransitionZoom - (kTransitionZoom - 1.0f) * e);
    shallow.alpha = m_transition.forward ? (1.0f - e) : e;
    deep.scale = 1.0f;
    deep.alpha = m_transition.forward ? e : (1.0f - e);

    // Deeper scene first: the shallower one is the one coming toward the
    // viewer, so it belongs on top in both directions.
    plan.push_back(deep);
    plan.push_back(shallow);
    return plan;
}

const Scene* Navigator::GetScene(const std::string& name) const
{
    auto it = m_scenes.find(name);
    return (it == m_scenes.end()) ? nullptr : it->second.get();
}

void Navigator::ResetFocus(const std::string& scene)
{
    auto it = m_scenes.find(scene);
    if (it == m_scenes.end())
    {
        return;
    }
    m_focusByScene[scene] = it->second->GetManifest().GetDefaultFocus();
    if (scene == m_currentScene)
    {
        m_returnItem.clear();
        SnapFocusWeights();
    }
}

void Navigator::ResetFocus()
{
    for (const auto& entry : m_scenes)
    {
        m_focusByScene[entry.first] = entry.second->GetManifest().GetDefaultFocus();
    }
    m_returnItem.clear();
    SnapFocusWeights();
}

void Navigator::SetOutputSize(ID3D11Device* device, float windowWidth, float windowHeight, bool allScenes)
{
    if (allScenes)
    {
        const std::string current = m_currentScene;
        for (const std::string& name : m_sceneOrder)
        {
            m_currentScene = name;
            SetOutputSize(device, windowWidth, windowHeight);
        }
        m_currentScene = current;
        return;
    }
    auto it = m_scenes.find(m_currentScene);
    if (it == m_scenes.end() || !it->second)
    {
        return;
    }
    Scene& scene = *it->second;
    const float canvasWidth = scene.GetCanvasWidth();
    const float canvasHeight = scene.GetCanvasHeight();
    if (canvasWidth <= 0.0f || canvasHeight <= 0.0f || windowWidth <= 0.0f || windowHeight <= 0.0f)
    {
        return; // degenerate (minimized, or a scene with no canvas) -- leave it alone
    }
    // Deliberately the same min-of-the-two-ratios as SetLetterboxViewport:
    // if these two ever disagreed, text would be rasterized for a different
    // scale than it's drawn at, which is the exact problem this fixes.
    const float scale = (windowWidth / canvasWidth < windowHeight / canvasHeight)
        ? (windowWidth / canvasWidth)
        : (windowHeight / canvasHeight);
    scene.SetRenderScale(device, scale);
}

const Scene* Navigator::GetCurrentScene() const
{
    auto it = m_scenes.find(m_currentScene);
    return (it == m_scenes.end()) ? nullptr : it->second.get();
}

const std::string& Navigator::GetFocusedItemId(const std::string& scene) const
{
    auto it = m_focusByScene.find(scene);
    return (it == m_focusByScene.end()) ? kEmpty : it->second;
}

bool Navigator::Move(Direction dir)
{
    if (IsAwaitingPress() || m_intro.active)
    {
        // A screen change is already committed (m_pendingAction), or the
        // scene is still arriving -- the console's hub doesn't take input
        // until it has finished sliding in either.
        return false;
    }
    const Scene* scene = GetCurrentScene();
    if (!scene)
    {
        return false;
    }
    const SceneManifest& manifest = scene->GetManifest();
    int index = manifest.FindItem(GetFocusedItemId());
    if (index < 0)
    {
        // Focus was lost (empty scene, or an id that no longer exists after a
        // re-cook) -- recover to the scene's default rather than going inert.
        const std::string& fallback = manifest.GetDefaultFocus();
        if (fallback.empty())
        {
            return false;
        }
        m_focusByScene[m_currentScene] = fallback;
        return true;
    }

    const ManifestItem& item = manifest.GetItems()[index];
    // A spinner is a slider that goes round: past its last value is its first.
    const bool spin = item.action == "SPIN" && item.valueCount > 0;
    if ((spin || item.action == "SLIDER") && (dir == Direction::Left || dir == Direction::Right))
    {
        // Left and right belong to the slider while it has focus: they move
        // its value, not focus. Quietly -- the console's XuiSlider keys a
        // sound on taking focus and none on moving.
        const int value = ValueOf(m_currentScene, item);
        int next = value + ((dir == Direction::Right) ? 1 : -1);
        if (spin) next = (next + item.valueCount) % item.valueCount;
        if (next < 0) next = 0;
        if (next > item.valueCount - 1) next = item.valueCount - 1;
        if (next == value)
        {
            return false;
        }
        m_values[m_currentScene][item.id] = next;
        return true;
    }
    const std::string* target = nullptr;
    switch (dir)
    {
    case Direction::Up:    target = &item.navUp;    break;
    case Direction::Down:  target = &item.navDown;  break;
    case Direction::Left:  target = &item.navLeft;  break;
    case Direction::Right: target = &item.navRight; break;
    }
    // An item that isn't there just now (HIDEIF) is passed through, along its
    // own link the same way.
    for (int hops = 0; hops < 8 && target && !target->empty(); ++hops)
    {
        const int next = manifest.FindItem(*target);
        if (next < 0)
        {
            break;
        }
        const ManifestItem& through = manifest.GetItems()[next];
        bool hidden = false;
        for (const std::pair<std::string, int>& when : through.hiddenIf)
        {
            hidden = hidden || GetState(when.first) == when.second;
        }
        if (!hidden)
        {
            break;
        }
        target = (dir == Direction::Up) ? &through.navUp : (dir == Direction::Down) ? &through.navDown
               : (dir == Direction::Left) ? &through.navLeft : &through.navRight;
    }
    if (!target || target->empty())
    {
        return false; // edge of the list -- the console just stops here too
    }

    // Captured before the move: whether this was a step ALONG the page
    // selector is a fact about both ends, and after FollowLink the near end
    // is gone. Same for the camera position -- comparing it either side is
    // how a page crossing is detected below.
    const bool fromReturn = item.returnPoint;
    const std::string fromScene = m_currentScene;
    const float fromViewX = TargetScrollX();
    if (!FollowLink(*target))
    {
        return false;
    }

    // Re-read the manifest rather than reusing the one above: a nav link may
    // have been a cross-scene "scene:item", in which case `manifest` now
    // describes the screen we just left and every id in it is the wrong id.
    const std::string& landed = GetFocusedItemId();
    bool toReturn = false;
    if (const Scene* landedScene = GetCurrentScene())
    {
        const SceneManifest& landedManifest = landedScene->GetManifest();
        const int landedIndex = landedManifest.FindItem(landed);
        toReturn = (landedIndex >= 0) && landedManifest.GetItems()[landedIndex].returnPoint;
    }
    if (fromReturn && toReturn)
    {
        // Rolling sideways ALONG the selector. Distinct from arriving on it:
        // the whole surface slides a section, which is the sound you hear.
        QueueSound(dir == Direction::Left ? SoundCue::TwistLeft : SoundCue::TwistRight);
    }
    else if (toReturn)
    {
        // Arriving on the selector from a tile -- HubScene_Twist's own cue.
        QueueSound(SoundCue::TwistFocus);
    }
    else
    {
        QueueSound(IsDisabled(landed) ? SoundCue::FocusInactive : SoundCue::Focus);

        // Walking off the end of one page and onto the next slides the whole
        // surface, exactly as rolling along the selector does, so it says the
        // same thing -- the page sound is about the movement, not about which
        // control asked for it. On top of the focus click rather than instead
        // of it: the tile really did take focus, and the console's skin has
        // these as two independent sound objects (the tile's slot_roll_on and
        // VariablesScene's SoundPanelLeft/Right), so both fire.
        //
        // Detected from the camera rather than from any notion of a page
        // index, because "page" isn't a thing the manifest names -- the hub
        // is one wide surface and an item's viewX IS its page. Scene-guarded:
        // a cross-scene link makes the two viewX values measurements of
        // different surfaces, and their difference meaningless.
        if (m_currentScene == fromScene)
        {
            const float toViewX = TargetScrollX();
            if (std::fabs(toViewX - fromViewX) > 0.5f)
            {
                QueueSound(toViewX > fromViewX ? SoundCue::TwistRight : SoundCue::TwistLeft);
            }
        }
    }
    return true;
}

bool Navigator::FollowLink(const std::string& link)
{
    const Scene* scene = GetCurrentScene();
    if (!scene || link.empty())
    {
        return false;
    }
    const SceneManifest& manifest = scene->GetManifest();
    const std::string* target = &link;

    // A nav link may cross scenes ("scene:item"), which is how paging between
    // hub pages keeps working with the same key that moves between tiles: at
    // the left or right edge of a page, left/right hands off to the
    // neighbouring page instead of dead-ending.
    size_t colon = target->find(':');
    if (colon != std::string::npos)
    {
        std::string sceneName = target->substr(0, colon);
        std::string itemId = target->substr(colon + 1);
        if (!SetScene(sceneName))
        {
            return false;
        }
        if (!itemId.empty())
        {
            m_focusByScene[sceneName] = itemId;
            // SetScene already snapped the weights, but it did so against
            // whichever item the incoming scene last had focus on -- this
            // link names a different one, so snap again now that focus is
            // where it's actually going. Otherwise the scene would cut in
            // with its old tile at full focus, easing down, which is the
            // morph a scene change is specifically meant not to do.
            SnapFocusWeights();
        }
        return true;
    }

    if (manifest.FindItem(*target) < 0)
    {
        return false; // dangling link -- leave focus where it is
    }

    // Same-scene focus move -- the ordinary case (arrow key/d-pad between
    // two tiles on the same page) and the one that should morph rather than
    // cut. Nothing to set up here: Update() reads the focused id every frame
    // and eases each tracked item's weight toward it, so the tile losing
    // focus starts easing down from wherever it actually is and the new one
    // eases up from wherever IT actually is. Deliberately no blend reset --
    // that reset was the bug when a move landed mid-morph.
    //
    // The one exception is a RETURN item (the page selector): stepping off
    // one goes back where you came from instead of following the link, so
    // long as you didn't move along the selector in between. See
    // m_returnItem for why the three cases are written out separately.
    std::string destination = *target;
    const int fromIndex = manifest.FindItem(GetFocusedItemId());
    const bool leavingReturn = (fromIndex >= 0) && manifest.GetItems()[fromIndex].returnPoint;
    const bool enteringReturn = manifest.GetItems()[manifest.FindItem(*target)].returnPoint;
    if (leavingReturn && enteringReturn)
    {
        m_returnItem.clear(); // moved ALONG the selector -- the old origin is stale
    }
    else if (enteringReturn)
    {
        m_returnItem = GetFocusedItemId();
    }
    else if (leavingReturn)
    {
        if (!m_returnItem.empty() && manifest.FindItem(m_returnItem) >= 0)
        {
            destination = m_returnItem;
        }
        m_returnItem.clear();
    }

    m_focusByScene[m_currentScene] = destination;
    return true;
}

bool Navigator::PageLeft()
{
    return FollowShoulder(false);
}

bool Navigator::PageRight()
{
    return FollowShoulder(true);
}

bool Navigator::FollowShoulder(bool forward)
{
    if (IsAwaitingPress() || m_intro.active)
    {
        // A screen change is already committed (m_pendingAction), or the
        // scene is still arriving -- the console's hub doesn't take input
        // until it has finished sliding in either.
        return false;
    }
    const Scene* scene = GetCurrentScene();
    if (!scene)
    {
        return false;
    }
    const SceneManifest& manifest = scene->GetManifest();
    const int index = manifest.FindItem(GetFocusedItemId());
    if (index < 0)
    {
        return false;
    }
    const ManifestItem& item = manifest.GetItems()[index];
    // A screen that doesn't declare shoulder links simply doesn't respond to
    // them, rather than falling back to something approximate.
    if (!FollowLink(forward ? item.pageRight : item.pageLeft))
    {
        return false;
    }
    // LB/RB move the surface exactly as far as rolling along the selector
    // does, so they say the same thing -- what you hear should describe the
    // movement, not which button asked for it.
    QueueSound(forward ? SoundCue::TwistRight : SoundCue::TwistLeft);
    return true;
}

bool Navigator::Activate()
{
    if (IsAwaitingPress() || m_intro.active)
    {
        // A screen change is already committed (m_pendingAction), or the
        // scene is still arriving -- the console's hub doesn't take input
        // until it has finished sliding in either.
        return false;
    }
    const Scene* scene = GetCurrentScene();
    if (!scene)
    {
        return false;
    }
    int index = scene->GetManifest().FindItem(GetFocusedItemId());
    if (index < 0)
    {
        return false;
    }
    const ManifestItem& activated = scene->GetManifest().GetItems()[index];
    if (activated.disabled)
    {
        // The console doesn't ignore A on a greyed-out row -- it answers with
        // a flatter, shorter version of the select sound. Nothing else
        // happens, which is the point.
        QueueSound(SoundCue::SelectInactive);
        return false;
    }
    QueueSound(SoundCue::Select);

    // Begin the press, before the GOTO below is even considered. If this
    // activation does drill in, the screen change is held back until the
    // animation has finished (m_pendingAction) -- the console plays the
    // whole press and only then switches, and cutting to the transition
    // early means the fade swallows the spring back so the tile is only
    // ever seen going in.
    if (scene->GetManifest().GetPressInSeconds() > 0.0f
        || scene->GetManifest().GetPressOutSeconds() > 0.0f)
    {
        m_press.item = GetFocusedItemId();
        m_press.scene = m_currentScene;
        m_press.t = 0.0f;
        m_press.active = true;
    }

    const std::string action = ActionOf(activated);
    if (action == "TOGGLE" && activated.valueCount > 0)
    {
        // A checkbox: on to the next value, round and round.
        m_values[m_currentScene][activated.id] =
            (ValueOf(m_currentScene, activated) + 1) % activated.valueCount;
        return true;
    }
    if (action.rfind("RADIO:", 0) == 0)
    {
        // One of a group: this one on, and every other item naming the same
        // group off.
        for (const ManifestItem& other : scene->GetManifest().GetItems())
        {
            if (other.action == action)
            {
                m_values[m_currentScene][other.id] = (other.id == activated.id) ? 1 : 0;
            }
        }
        return true;
    }
    if (action.rfind("SET:", 0) == 0)
    {
        // "SET:name=value": a piece of the app's state, as a scene's SETS
        // gives it -- which characters a keyboard shows, how many buttons of
        // a pass code are in.
        const size_t equals = action.find('=');
        if (equals != std::string::npos)
        {
            m_state[action.substr(4, equals - 4)] = std::atoi(action.c_str() + equals + 1);
        }
        return true;
    }
    if (action.rfind("KEY:", 0) == 0)
    {
        // A key of the on-screen keyboard. What is typed is the text variable
        // "typed"; DONE puts it in the field the keyboard was opened for,
        // which "field" names (main.cpp, on a row that is typed).
        std::map<std::string, std::string>& text = TextVars();
        std::string& typed = text["typed"];
        const std::string key = action.substr(4);
        if (key == "DONE")
        {
            text[text["field"]] = typed;
        }
        else if (key == "BACK")
        {
            // One character: in UTF-8, a lead byte and what continues it.
            while (!typed.empty() && (static_cast<unsigned char>(typed.back()) & 0xC0) == 0x80)
            {
                typed.pop_back();
            }
            if (!typed.empty())
            {
                typed.pop_back();
            }
        }
        // shortcut: one length limit (in bytes) for every field, where the console's is the field's own; give "TYPE:" a limit when a field needs one
        else if (typed.size() < 48)
        {
            typed += (key == "SPACE") ? std::string(" ") : key;
        }
        return true;
    }
    if (action.rfind("GOTO:", 0) == 0)
    {
        if (m_press.active)
        {
            // Hold the screen change until the press has played out; Update
            // fires it the frame the animation ends.
            m_pendingAction = action;
            return true;
        }
        return PerformGoto(action.substr(5));
    }
    return false;
}

std::string Navigator::ActionOf(const ManifestItem& item) const
{
    for (const ManifestItem::Alternative& alternative : item.alternatives)
    {
        auto held = m_state.find(alternative.var);
        if (((held != m_state.end()) ? held->second : 0) == alternative.value)
        {
            return (alternative.action == "-") ? std::string() : alternative.action;
        }
    }
    return item.action;
}

std::string Navigator::FocusedAction() const
{
    const Scene* scene = GetCurrentScene();
    const int index = scene ? scene->GetManifest().FindItem(GetFocusedItemId()) : -1;
    return (index >= 0) ? ActionOf(scene->GetManifest().GetItems()[index]) : std::string();
}

bool Navigator::PerformGoto(const std::string& sceneName)
{
    // Captured BEFORE SetScene, which snaps the camera and the focus
    // weights to the scene being entered -- after it, the state the
    // outgoing scene should be drawn with is already gone.
    const std::string from = m_currentScene;
    const std::string fromFocus = GetFocusedItemId();
    const float fromScrollX = m_scrollX;
    if (!SetScene(sceneName))
    {
        return false;
    }
    m_history.push_back(from);
    BeginTransition(from, fromFocus, fromScrollX, /*forward=*/true);
    return true;
}

bool Navigator::GoBack()
{
    if (IsAwaitingPress() || m_intro.active)
    {
        // A screen change is already committed (m_pendingAction), or the
        // scene is still arriving -- the console's hub doesn't take input
        // until it has finished sliding in either.
        return false;
    }
    while (!m_history.empty())
    {
        std::string target = m_history.back();
        m_history.pop_back();
        const std::string from = m_currentScene;
        const std::string fromFocus = GetFocusedItemId();
        const float fromScrollX = m_scrollX;
        // Skip entries whose scene has since gone missing rather than
        // stopping the walk -- B should keep working after a partial re-cook.
        if (SetScene(target))
        {
            BeginTransition(from, fromFocus, fromScrollX, /*forward=*/false);
            // `from`, not the scene just entered: B belongs to the screen it
            // was pressed on, the same way A does in Activate above.
            QueueSound(SoundCue::Back, from);
            return true;
        }
    }
    return false;
}
