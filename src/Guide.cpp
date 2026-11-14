#include "Guide.h"
#include "TextVars.h"

#include <cstdlib>
#include <string>

namespace
{
    const char* const kFrameScene = "GuideFrame";
    const char* const kMainScene = "GuideMain";
    const char* const kTabScenes[] = { "GuideGames", "GuideHome", "GuideMedia", "GuideSettings" };
    // The same four with nobody signed in -- hud.xex has a layout of each tab
    // for that (Sign In where Friends was, no Profile...). See TabScene.
    const char* const kTabScenesOut[] = { "GuideGamesOut", "GuideHomeOut", "GuideMediaOut", "GuideSettingsOut" };
    const char* const kToastScene = "GuideNotify";
    constexpr int kTabCount = 4;
    constexpr int kDefaultTab = 1;   // GuideMain.xur's DefaultTab=2, zero-based

    // A .xur timeline carries frame numbers and no rate. 60 is the reading
    // the rest of the project uses (see hub_build.py's PRESS_* note), and at
    // 60 the guide's 12-frame tab switch and 19-frame open come out at 0.2s
    // and 0.32s, which is how they feel on a console.
    constexpr float kFramesPerSecond = 60.0f;

    int TabIndex(const std::string& scene)
    {
        for (int i = 0; i < kTabCount; ++i)
        {
            if (scene == kTabScenes[i] || scene == kTabScenesOut[i])
            {
                return i;
            }
        }
        return -1;
    }

    // The named frame tab `tab` rests on: the end of a switch INTO it. Tab 1
    // is only ever arrived at from tab 2; every other from the one before.
    std::string SettledMark(int tab)
    {
        return (tab == 0) ? "2To1End" : std::to_string(tab) + "To" + std::to_string(tab + 1) + "End";
    }
}

void Guide::Clock::Play(float from, float to)
{
    // Just short of `from`, so a sound keyed on the very first frame of the
    // range still counts as newly reached.
    previous = from - 0.001f;
    frame = from;
    end = to;
}

void Guide::Clock::Advance(float deltaSeconds)
{
    previous = frame;
    frame += deltaSeconds * kFramesPerSecond;
    if (frame > end)
    {
        frame = end;
    }
}

bool Guide::Load(ID3D11Device* device, const std::wstring& guideDir, const std::wstring& fontPath)
{
    m_nav.LoadAll(device, guideDir, fontPath);
    m_loaded = m_nav.GetScene(kFrameScene) && m_nav.GetScene(kMainScene)
        && m_nav.GetScene(kTabScenes[kDefaultTab]);
    return m_loaded;
}

float Guide::Mark(const char* scene, const std::string& name) const
{
    const Scene* found = m_nav.GetScene(scene);
    return found ? found->GetManifest().GetMark(name) : 0.0f;
}

void Guide::PlayFrame(const std::string& from, const std::string& to)
{
    m_frame.Play(Mark(kFrameScene, from), Mark(kFrameScene, to));
}

bool Guide::IsDialog(const std::string& scene) const
{
    const Scene* found = m_nav.GetScene(scene);
    return found && found->GetManifest().GetMark("Host.Error") > 0.0f;
}

float Guide::PageRestFrame(const std::string& scene) const
{
    const Scene* found = m_nav.GetScene(scene);
    if (!found)
    {
        return 0.0f;
    }
    const std::string tail = "To" + m_nav.GetFocusedItemId(scene) + "End";
    for (const std::pair<std::string, float>& mark : found->GetManifest().GetMarks())
    {
        if (mark.first.size() > tail.size() && mark.first[0] != '@'
            && mark.first.compare(mark.first.size() - tail.size(), tail.size(), tail) == 0)
        {
            return mark.second;
        }
    }
    return 0.0f;
}

void Guide::PlayTabs(const std::string& from, const std::string& to)
{
    m_tabs.Play(Mark(kMainScene, from), Mark(kMainScene, to));
}

const char* Guide::TabScene(int tab) const
{
    // The signed-out set, while nobody is signed in and it has been cooked.
    return (m_signedOut && m_nav.GetScene(kTabScenesOut[tab])) ? kTabScenesOut[tab] : kTabScenes[tab];
}

void Guide::Toggle()
{
    if (!m_loaded)
    {
        return;
    }
    if (m_open)
    {
        Close();
        return;
    }
    Open(TabScene(kDefaultTab));
}

void Guide::Open(const std::string& scene)
{
    if (!m_loaded || m_open || !m_nav.GetScene(scene))
    {
        return;
    }
    // A new guide every time, as on the console: back out of any sub-page it
    // was closed on, then `scene` with everything on its first row.
    while (m_nav.GoBack()) {}
    m_nav.TakeSoundCues();
    m_nav.SetScene(scene);
    m_nav.ResetFocus();
    m_shown = m_nav.GetCurrentSceneName();
    m_depth = m_nav.GetHistoryDepth();
    const int tab = TabIndex(scene);
    m_tab = (tab >= 0) ? tab : kDefaultTab;
    m_stage = Stage::None;
    m_leaving.clear();
    m_dialog.clear();
    m_pendingDash.clear();
    m_pendingBack = false;
    m_page = Clock();

    m_open = true;
    m_visible = true;
    if (tab >= 0)
    {
        PlayFrame("ClosedToHalf", "EndClosedToHalf");
        const float settled = Mark(kMainScene, SettledMark(m_tab));
        m_tabs.Play(settled, settled);
        return;
    }
    // Straight onto a page or a message box, with no tabs under it: the
    // frame has its own way in from closed for each, as it has its own way
    // out (see Close). There is nothing in the history, so B closes it.
    // Input waits for the frame, as it does when a row opens a page.
    const bool dialog = IsDialog(scene);
    if (dialog)
    {
        m_dialog = scene;
    }
    else
    {
        const float rest = PageRestFrame(scene);
        m_page.Play(rest, rest);
    }
    PlayFrame(dialog ? "ClosedToError" : "ClosedToFull", dialog ? "EndClosedToError" : "EndClosedToFull");
    const float tucked = Mark(kMainScene, std::to_string(m_tab + 1) + "OpenEnd");
    m_tabs.Play(tucked, tucked);
    m_stage = Stage::Growing;
}

void Guide::Close()
{
    m_open = false;
    // From wherever the frame actually is -- around a message box, at full
    // size around a sub-page, or small. It has its own way out of each.
    const bool full = TabIndex(m_nav.GetCurrentSceneName()) < 0 && m_stage != Stage::Tucking;
    if (IsDialog(m_nav.GetCurrentSceneName()))
    {
        PlayFrame("ErrorToClosed", "EndErrorToClosed");
    }
    else if (full)
    {
        PlayFrame("FullToClosed", "EndFullToClosed");
    }
    else
    {
        PlayFrame("HalfToClosed", "EndHalfToClosed");
    }
    m_stage = Stage::None;
}

void Guide::Move(Navigator::Direction direction)
{
    if (!m_open || m_stage != Stage::None)
    {
        return;
    }
    const std::string from = m_nav.GetCurrentSceneName();
    const std::string fromFocus = m_nav.GetFocusedItemId();
    m_nav.Move(direction);
    const int a = TabIndex(from);
    const int b = TabIndex(m_nav.GetCurrentSceneName());
    if (a >= 0 && b >= 0 && a != b)
    {
        // Left/right stepped a tab. The navigator has already cut to the new
        // tab's rows; what the eye should see is GuideMain's "<a>To<b>"
        // playing, with the old rows fading out under it. The timeline's own
        // BladeSwitch sound replaces the focus click the move queued.
        m_nav.TakeSoundCues();
        m_leaving = from;
        m_leavingFocus = fromFocus;
        m_tab = b;
        m_shown = m_nav.GetCurrentSceneName();
        const std::string name = std::to_string(a + 1) + "To" + std::to_string(b + 1);
        PlayTabs(name, name + "End");
    }
    else if (a < 0 && m_nav.GetCurrentSceneName() == from)
    {
        // A sub-page that is a tab scene itself ("Recent"): its focus stops
        // are its tabs, and focus going from item <x> to item <y> plays the
        // page's own "<x>To<y>" -- if it has one, which is all that marks
        // such a page out. Its timeline's sound stands in for the click.
        const std::string to = m_nav.GetFocusedItemId();
        const Scene* page = m_nav.GetCurrentScene();
        const std::string name = fromFocus + "To" + to;
        const float start = page ? page->GetManifest().GetMark(name, -1.0f) : -1.0f;
        if (to != fromFocus && start >= 0.0f)
        {
            m_nav.TakeSoundCues();
            m_page.Play(start, page->GetManifest().GetMark(name + "End", start));
        }
    }
}

void Guide::Activate()
{
    if (!m_open || m_stage != Stage::None)
    {
        return;
    }
    // Read before Activate, which only knows GOTO: DASH is the guide's own
    // verb, for a row that leaves the guide for a dashboard scene.
    const std::string action = m_nav.FocusedAction();
    m_nav.Activate();
    if (action.rfind("DASH:", 0) == 0)
    {
        m_pendingDash = action.substr(5);
    }
    else if (action.rfind("TYPE:", 0) == 0)
    {
        // A step that is typed: on to the keyboard, for the field it names.
        BeginTyping(action.substr(5));
        m_nav.PerformGoto("GuideKeyboard");
    }
    else if (action == "KEY:DONE" && m_nav.GetScene(TextVars()[TextVars()["field"] + ".then"]))
    {
        // The keyboard's Done, for a field that goes on to a page of its own
        // ("SAY <field>.then <scene>") rather than back where it was opened.
        m_nav.PerformGoto(TextVars()[TextVars()["field"] + ".then"]);
    }
    else if (action == "BACK" || action == "KEY:DONE")
    {
        // A message box's Cancel, or the keyboard's Done (which the navigator
        // has just stored): B, once the press has played.
        m_pendingBack = true;
    }
    else if (action.rfind("PIC:", 0) == 0)
    {
        // A tile on Change Gamer Picture: the profile wears it from now on,
        // and the page closes, as it does on the console once it has saved.
        m_gamerPicture = std::atoi(action.c_str() + 4);
        m_pendingBack = true;
    }
}

void Guide::Back()
{
    if (!m_open || m_stage != Stage::None)
    {
        return;
    }
    if (!m_nav.GoBack())
    {
        Close();   // nothing deeper to back out of
    }
}

void Guide::Home(const std::string& scene)
{
    // Only the tabs carry "Xbox Home" in their legend; sub-pages don't.
    if (m_open && m_stage == Stage::None && TabIndex(m_nav.GetCurrentSceneName()) >= 0)
    {
        m_pendingDash = scene;
    }
}

void Guide::Notify()
{
    if (m_nav.GetScene(kToastScene))
    {
        // Each firing is the next of the notifications the scene was cooked
        // with (its "Notify.Count" MARK), round and round.
        const int count = static_cast<int>(Mark(kToastScene, "Notify.Count"));
        m_toastKind = (count > 0) ? (m_toastKind + 1) % count : 0;
        m_toast.Play(Mark(kToastScene, "TransTo"), Mark(kToastScene, "EndTransTo"));
        m_toastStage = 1;
    }
}

void Guide::FireSounds(const Clock& clock, const char* scene, AudioEngine& audio) const
{
    const Scene* found = m_nav.GetScene(scene);
    if (!found)
    {
        return;
    }
    // A MARK whose name starts with '@' is a sound key lifted off the
    // timeline -- "@HUD_open" on the frame hudbkgnd.xur's GuideSound names
    // xam://HUD_open.xma. Played the moment the clock reaches it, so every
    // guide sound lands on the frame the console keys it to.
    for (const std::pair<std::string, float>& mark : found->GetManifest().GetMarks())
    {
        if (mark.first.empty() || mark.first[0] != '@'
            || !(clock.previous < mark.second && mark.second <= clock.frame))
        {
            continue;
        }
        if (mark.first == "@HUD_open")        audio.Play(SoundCue::GuideOpen);
        else if (mark.first == "@tab_Switch") audio.Play(SoundCue::GuideTabSwitch);
        else if (mark.first == "@HUD_close")  audio.Play(SoundCue::GuideClose);
        else if (mark.first == "@BladeOpen")  audio.Play(SoundCue::GuideBladeOpen);
        else if (mark.first == "@NotifyPopup") audio.Play(SoundCue::Notify);
        else if (mark.first.rfind("@BladeSwitch_", 0) == 0)
        {
            // BladeSwitch_<N> belongs to tab N; sounds.txt keys the four
            // waves by that tab's scene.
            const int tab = mark.first.back() - '1';
            if (tab >= 0 && tab < kTabCount)
            {
                audio.Play(SoundCue::GuideBlade, TabScene(tab));
            }
        }
    }
}

std::string Guide::Update(float deltaSeconds, AudioEngine& audio)
{
    if (m_toastStage != 0)
    {
        // The popup's three ranges, one after the other. scr_Notification
        // marks where its hold goes back to (`loop`) but not how many times:
        // that is the runtime's, per notification. Once round here, which
        // keeps the popup up for about five seconds -- a guess.
        FireSounds(m_toast, kToastScene, audio);
        m_toast.Advance(deltaSeconds);
        if (m_toast.Done())
        {
            ++m_toastStage;
            if (m_toastStage == 2)
            {
                m_toast.Play(Mark(kToastScene, "loop"), Mark(kToastScene, "EndTransTo"));
            }
            else if (m_toastStage == 3)
            {
                m_toast.Play(Mark(kToastScene, "TransFrom"), Mark(kToastScene, "EndTransFrom"));
            }
            else
            {
                m_toastStage = 0;
            }
        }
    }

    if (!m_visible)
    {
        return std::string();
    }

    m_nav.Update(deltaSeconds);
    for (const Navigator::QueuedSound& queued : m_nav.TakeSoundCues())
    {
        audio.Play(queued.cue, queued.scene);
    }

    // A GOTO or a B moved between a tab and a sub-page (tab-to-tab is Move's).
    const std::string now = m_nav.GetCurrentSceneName();
    const size_t depth = m_nav.GetHistoryDepth();
    if (now != m_shown && depth > m_depth)
    {
        // Arrived by GOTO rather than by B. On the console that is a newly
        // created scene, so it starts on its first row however it was left
        // last time -- which matters most for a message box, whose Cancel
        // would otherwise still have focus the next time it came up.
        m_nav.ResetFocus(now);
    }
    m_depth = depth;
    if (now != m_shown)
    {
        const int from = TabIndex(m_shown);
        const int to = TabIndex(now);
        const std::string tab = std::to_string(m_tab + 1);
        // The frame's Error state is entered from, and left for, whichever
        // size it was: HalfToError / ErrorToHalf under a tab, Full... under
        // a sub-page. GuideMain's blades don't tuck for it; the frame simply
        // stops showing what it hosts (AppHostElementId) for the duration.
        if (IsDialog(now))
        {
            const std::string size = (from >= 0) ? "Half" : "Full";
            m_dialog = now;
            PlayFrame(size + "ToError", "End" + size + "ToError");
            m_stage = Stage::Growing;
        }
        else if (IsDialog(m_shown))
        {
            const std::string size = (to >= 0) ? "Half" : "Full";
            PlayFrame("ErrorTo" + size, "EndErrorTo" + size);
            m_stage = Stage::Growing;
        }
        else if (from >= 0 && to < 0)
        {
            // The tab's rows fade out as part of the tuck, still showing the
            // row that was pressed.
            m_leaving = m_shown;
            m_leavingFocus = m_nav.GetFocusedItemId(m_shown);
            PlayTabs(tab + "Open", tab + "OpenEnd");
            m_stage = Stage::Tucking;
        }
        else if (from < 0 && to >= 0)
        {
            PlayFrame("FullToHalf", "EndFullToHalf");
            const float tucked = Mark(kMainScene, tab + "Close");
            m_tabs.Play(tucked, tucked);   // held tucked until the frame is small again
            m_stage = Stage::Shrinking;
        }
        if (to < 0 && !IsDialog(now))
        {
            // Arriving on a sub-page, from either side: park its own
            // timeline where the item it has focus on rests.
            const float rest = PageRestFrame(now);
            m_page.Play(rest, rest);
        }
        m_shown = now;
    }

    FireSounds(m_frame, kFrameScene, audio);
    FireSounds(m_tabs, kMainScene, audio);
    FireSounds(m_page, now.c_str(), audio);
    m_frame.Advance(deltaSeconds);
    m_tabs.Advance(deltaSeconds);
    m_page.Advance(deltaSeconds);

    const std::string tab = std::to_string(m_tab + 1);
    if (m_stage == Stage::Tucking && m_tabs.Done())
    {
        PlayFrame("HalfToFull", "EndHalfToFull");
        m_stage = Stage::Growing;
    }
    else if (m_stage == Stage::Shrinking && m_frame.Done())
    {
        PlayTabs(tab + "Close", tab + "CloseEnd");
        m_stage = Stage::Untucking;
    }
    else if ((m_stage == Stage::Growing && m_frame.Done())
        || (m_stage == Stage::Untucking && m_tabs.Done()))
    {
        m_stage = Stage::None;
    }
    if (m_tabs.Done() && m_stage == Stage::None)
    {
        m_leaving.clear();   // whatever was fading out has finished
    }

    if (m_pendingBack && !m_nav.IsPressing())
    {
        m_pendingBack = false;
        Back();
    }
    std::string dash;
    if (!m_pendingDash.empty() && !m_nav.IsPressing())
    {
        // Held until the row's press had played, the way a GOTO is.
        dash.swap(m_pendingDash);
        Close();
    }
    if (!m_open && m_frame.Done())
    {
        m_visible = false;
    }
    return dash;
}

void Guide::SetOutputSize(ID3D11Device* device, float windowWidth, float windowHeight)
{
    if (m_visible || m_toastStage != 0)
    {
        m_nav.SetOutputSize(device, windowWidth, windowHeight, /*allScenes=*/true);
    }
}

void Guide::DrawNotification(QuadRenderer& renderer, float windowWidth, float windowHeight, float timeSeconds) const
{
    const Scene* toast = (m_toastStage != 0) ? m_nav.GetScene(kToastScene) : nullptr;
    if (!toast || toast->GetCanvasWidth() <= 0.0f || toast->GetCanvasHeight() <= 0.0f)
    {
        return;
    }
    renderer.SetLetterboxViewport(windowWidth, windowHeight, toast->GetCanvasWidth(), toast->GetCanvasHeight());
    renderer.BeginFrame(toast->GetCanvasWidth(), toast->GetCanvasHeight());
    Scene::Variables variables;
    variables["notify"] = m_toastKind;   // which notification: its icon and wording are cooked under IF
    toast->Draw(renderer, Scene::FocusWeights(), 0.0f, timeSeconds, Scene::Layer::All, m_toast.frame, &variables);
}

void Guide::DrawMain(QuadRenderer& renderer, float hostAlpha, float timeSeconds, const Scene::Variables& variables) const
{
    const Scene* main = m_nav.GetScene(kMainScene);
    if (!main)
    {
        return;
    }
    // Under the rows: the side blades, their captions, the centre blade.
    renderer.SetSceneTransform(1.0f, hostAlpha);
    main->Draw(renderer, Scene::FocusWeights(), 0.0f, timeSeconds, Scene::Layer::ScrollOnly, m_tabs.frame, &variables);

    for (int i = 0; i < kTabCount; ++i)
    {
        const std::string name = TabScene(i);
        const Scene* rows = m_nav.GetScene(name);
        TrackState tab;
        if (!rows || !main->GetManifest().EvaluateTrack("Tab" + std::to_string(i + 1), m_tabs.frame, tab)
            || !tab.visible || tab.alpha <= 0.0f)
        {
            continue;
        }
        // The current tab's rows at the navigator's own weights; one still
        // fading out keeps the row it had focus on lit.
        Scene::FocusWeights weights;
        if (name == m_nav.GetCurrentSceneName())
        {
            weights = m_nav.GetFocusWeights();
        }
        else if (name == m_leaving && !m_leavingFocus.empty())
        {
            weights[m_leavingFocus] = 1.0f;
        }
        // The tab's Width only ever narrows what shows (ClipChildren); the
        // rows themselves slide, they don't squash.
        renderer.SetSceneTransform(1.0f, hostAlpha * tab.alpha, tab.dx, tab.dy);
        renderer.SetClip(tab.x + tab.dx, tab.y + tab.dy, tab.w * tab.sx, tab.h * tab.sy);
        rows->Draw(renderer, weights, 0.0f, timeSeconds, Scene::Layer::All, 0.0f, &m_nav.GetValues(name));
        renderer.ClearClip();
    }

    // Over them: the selected tab's strip and caption, the header.
    renderer.SetSceneTransform(1.0f, hostAlpha);
    main->Draw(renderer, Scene::FocusWeights(), 0.0f, timeSeconds, Scene::Layer::FixedOnly, m_tabs.frame, &variables);
}

void Guide::Draw(QuadRenderer& renderer, float windowWidth, float windowHeight, float timeSeconds) const
{
    const Scene* frame = m_nav.GetScene(kFrameScene);
    if (!m_visible || !frame || frame->GetCanvasWidth() <= 0.0f || frame->GetCanvasHeight() <= 0.0f)
    {
        return;
    }
    // Every guide scene is cooked on the same canvas, so one viewport does
    // for the whole stack.
    renderer.SetLetterboxViewport(windowWidth, windowHeight, frame->GetCanvasWidth(), frame->GetCanvasHeight());
    renderer.BeginFrame(frame->GetCanvasWidth(), frame->GetCanvasHeight());

    // GuideMain stays hosted until its blades have finished tucking away;
    // only then is the sub-page the thing in the frame.
    const std::string& current = m_nav.GetCurrentSceneName();
    const bool onPage = TabIndex(current) < 0 && !IsDialog(current) && m_stage != Stage::Tucking;

    // The things only the running guide knows, as the cooked scenes' IF
    // variables: whose legend the frame's legend row is showing (each hosted
    // scene says, with a "Legend.Id" MARK), the controller's battery (1..4
    // for the four icons, 0 for none), and which gamer picture is worn.
    Scene::Variables variables;
    const Scene* hosted = m_nav.GetScene(onPage ? current : std::string(kMainScene));
    variables["legend"] = hosted ? static_cast<int>(hosted->GetManifest().GetMark("Legend.Id")) : 0;
    variables["battery"] = m_battery + 1;
    variables["gamerpic"] = m_signedOut ? -1 : m_gamerPicture;   // -1: none of the cooked pictures

    frame->Draw(renderer, Scene::FocusWeights(), 0.0f, timeSeconds, Scene::Layer::All, m_frame.frame, &variables);

    // The frame hosts two things, each with its own visibility track: the
    // guide's own scenes, and a message box.
    TrackState host;
    if (frame->GetManifest().EvaluateTrack("AppHostElementId", m_frame.frame, host)
        && host.visible && host.alpha > 0.0f)
    {
        if (!onPage)
        {
            DrawMain(renderer, host.alpha, timeSeconds, variables);
        }
        else
        {
            for (const Navigator::Layer& layer : m_nav.BuildRenderPlan())
            {
                if (layer.scene)
                {
                    // The navigator's page-to-page crossfade, without its
                    // push toward the viewer: the guide's pages FadeOut /
                    // FadeIn in place. Only the page that is current has a
                    // timeline of its own that is anywhere but at rest.
                    renderer.SetSceneTransform(1.0f, layer.alpha * host.alpha);
                    // A page's own item values, plus the picture on its gamer card.
                    Scene::Variables values = layer.values;
                    values["gamerpic"] = m_signedOut ? -1 : m_gamerPicture;
                    layer.scene->Draw(renderer, layer.weights, 0.0f, timeSeconds, Scene::Layer::All,
                        (layer.scene == m_nav.GetCurrentScene()) ? m_page.frame : 0.0f, &values);
                }
            }
        }
    }

    TrackState error;
    const Scene* box = m_dialog.empty() ? nullptr : m_nav.GetScene(m_dialog);
    if (box && frame->GetManifest().EvaluateTrack("ErrorHUD", m_frame.frame, error)
        && error.visible && error.alpha > 0.0f)
    {
        // Still drawn for the few frames it takes to fade after B, by which
        // time it is no longer the navigator's current scene.
        Scene::FocusWeights weights;
        if (m_dialog == current)
        {
            weights = m_nav.GetFocusWeights();
        }
        else
        {
            weights[m_nav.GetFocusedItemId(m_dialog)] = 1.0f;
        }
        renderer.SetSceneTransform(1.0f, error.alpha);
        box->Draw(renderer, weights, 0.0f, timeSeconds, Scene::Layer::All, 0.0f, &m_nav.GetValues(m_dialog));
    }
}
