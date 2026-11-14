#include "Window.h"
#include "Renderer.h"
#include "QuadRenderer.h"
#include "Scene.h"
#include "Navigator.h"
#include "Guide.h"
#include "Gamepad.h"
#include "AudioEngine.h"
#include "BootAnimation.h"
#include "FileUtil.h"
#include "TextVars.h"

#include <objbase.h>
#include <string>
#include <vector>

// Entry point: creates the window and D3D11 renderer, loads every cooked
// Xbox 360 dashboard scene under assets\scenes\ (flattened from real
// cracked .xur layouts by the Python cook pipeline), and runs an
// interactive loop -- d-pad/left stick or arrow keys move focus between a
// scene's items, A/Enter follows an item's action, B/Backspace goes back.
//
// The scene graph itself lives in the manifests: each focusable ITEM names
// the item focus moves to in each direction, and a nav link or action may
// name another scene, so paging between hub pages and drilling into a
// settings screen are the same mechanism. Navigator owns that state; this
// file is just the pump.
//
// Using the ANSI `WinMain` signature (rather than `wWinMain`) deliberately:
// it's the entry point both MSVC and MinGW's default CRT startup resolve
// without extra linker flags (wWinMain needs /ENTRY:wWinMainCRTStartup on
// MSVC, or -municode on MinGW). We don't use the command-line string, so
// there's no downside to sidestepping that toolchain-specific footgun.
int WINAPI WinMain(HINSTANCE, HINSTANCE, LPSTR, int)
{
    // Without this, a window on a display with Windows scaling above 100%
    // (very common on modern high-DPI monitors) gets DPI-virtualized: Windows
    // reports a smaller logical size to us, renders our output at that size,
    // then bitmap-stretches the whole window to the real physical pixels --
    // a blur, not the kind of X/Y-anisotropic distortion SetLetterboxViewport
    // fixes, but worth ruling out as a contributor since it's a real gap and
    // costs nothing to close. Must be called before any window is created.
    SetProcessDPIAware();

    // WIC (used by TextureLoader to decode the scene's PNGs) requires COM
    // initialized on the calling thread. Apartment-threaded is the right
    // choice here (single UI/render thread, no cross-thread COM calls).
    HRESULT comHr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    bool comInitialized = SUCCEEDED(comHr);

    Window window;
    Window::CreateParams params;
    params.title = L"DashX360Native";
    params.width = 1280;
    params.height = 720;

    if (!window.Create(params))
    {
        if (comInitialized) CoUninitialize();
        return -1;
    }

    Renderer renderer;
    if (!renderer.Initialize(window.GetHandle(), window.GetWidth(), window.GetHeight()))
    {
        if (comInitialized) CoUninitialize();
        return -1;
    }

    // Xbox 360 dashboard background is a near-black charcoal, not pure
    // black -- close enough placeholder for the area outside the scene's
    // own canvas (see the letterbox note below).
    renderer.SetClearColor(0.03f, 0.03f, 0.035f);

    QuadRenderer quadRenderer;
    if (!quadRenderer.Initialize(renderer.GetDevice(), renderer.GetContext()))
    {
        if (comInitialized) CoUninitialize();
        return -1;
    }

    // Every cooked scene folder (see xur_cook.py) is
    // assets\scenes\<name>\{manifest.txt, assets\*.png}, resolved relative
    // to the .exe's own directory (not the process's current working
    // directory -- see FileUtil::GetExecutableDir) so this works whether
    // launched by double-click, a shortcut, or the VS debugger.
    static const wchar_t* kStartScene = L"Hub";
    static const wchar_t* kFontFile = L"xenonclatin_full.ttf";

    std::wstring exeDir = FileUtil::GetExecutableDir();
    std::wstring scenesRoot = exeDir + L"assets\\scenes";
    std::wstring fontPath = exeDir + L"assets\\fonts\\" + kFontFile;

    Navigator navigator;
    navigator.LoadAll(renderer.GetDevice(), scenesRoot, fontPath);
    navigator.SetScene(FileUtil::WideToUtf8(kStartScene)); // no-op if that scene isn't present
    navigator.SnapCamera();
    // A missing/misplaced assets folder isn't fatal -- the window still
    // opens and clears to the background color, which is a much easier
    // problem to diagnose (and recover from, by fixing the assets\ layout
    // and relaunching) than a hard crash on first run.

    // The Xbox Guide: hud.xex's blade panel, cooked by XURs\guide_build.py
    // into assets\guide. Not one of the dashboard's scenes, because it is not
    // a place the dashboard goes -- it sits ON TOP of wherever the dashboard
    // already is, with its own focus and its own B (see Guide.h). Absent, the
    // Guide button simply does nothing.
    Guide guide;
    guide.Load(renderer.GetDevice(), exeDir + L"assets\\guide", fontPath);

    // UI sound. Fail-soft on purpose, exactly like the scene loader above: a
    // machine with no output device, or a missing assets\sounds folder, gets
    // a silent dashboard rather than no dashboard.
    AudioEngine audio;
    if (!audio.Initialize(exeDir + L"assets\\sounds"))
    {
        OutputDebugStringA(("DashX360Native: sound disabled -- " + audio.GetLastError() + "\n").c_str());
    }
    // The console's boot animation, rendered rather than played back. The
    // geometry and the per-frame transforms in assets\bootanim were extracted
    // from the real bootanim.xex (see tools\bootanim_extract), so this draws
    // the console's own orb with the console's own motion, at whatever
    // resolution the window happens to be -- which is the point: the 78MB of
    // H.264 it replaces was locked to 1080p and cost a decode per frame.
    //
    // Its soundtrack still comes from assets\sounds\boot.wav through
    // AudioEngine, started on the same frame as the first rendered one.
    // Absent or unopenable data, and the dashboard simply comes straight up.
    //
    // Rendered at the window size as it stands when the boot begins. A resize
    // mid-boot keeps the original size for the few seconds it runs rather
    // than reallocating the targets underneath it.
    BootAnimation boot;
    bool booting = boot.Open(renderer.GetDevice(), exeDir + L"assets\\bootanim",
                             window.GetWidth(), window.GetHeight());
    float bootSeconds = 0.0f;
    if (booting)
    {
        audio.Play(SoundCue::Boot);
    }

    // The dashboard's own arrival sound -- the hub scene fires control_kinect
    // on its End/TransTo frames (archive_31/hubhomepage.json), which is the
    // one sound that isn't a reaction to input. It belongs to the dashboard
    // ARRIVING, so it waits for the boot to get out of the way.
    if (!booting)
    {
        audio.Play(SoundCue::FirstStart, navigator.GetCurrentSceneName());
        navigator.BeginIntro();
    }

    Gamepad gamepad;

    // Keyboard mirrors the pad one-for-one so the dashboard is testable on a
    // machine with no controller attached.
    std::vector<Navigator::Direction> pendingMoves;
    bool pendingActivate = false;
    bool pendingBack = false;
    int pendingPage = 0;   // -1 = a section left, +1 = a section right
    bool pendingGuide = false;   // the Guide button: opens the guide, or closes it
    std::string pendingGuideScene;   // a hub tile's "GUIDE:<scene>", waiting on its press
    bool pendingDone = false;        // a row's "BACK", waiting on the same
    // N: the notification popup, each press the next kind (an achievement,
    // a friend coming online, a party invite...). A test key -- on a console
    // these arrive when something happens, not on demand.
    bool pendingNotify = false;
    bool pendingY = false;       // only the guide listens: its legend's "Xbox Home"
    // X, START, LT, RT and LS (the left stick clicked), by those names: only
    // a scene's own shortcuts listen ("SHORTCUT" -- the keyboard's).
    std::vector<const char*> pendingButtons;
    window.OnKeyDown = [&](uint32_t vk)
    {
        switch (vk)
        {
        case VK_UP:    case 'W': pendingMoves.push_back(Navigator::Direction::Up); break;
        case VK_DOWN:  case 'S': pendingMoves.push_back(Navigator::Direction::Down); break;
        case VK_LEFT:  case 'A': pendingMoves.push_back(Navigator::Direction::Left); break;
        case VK_RIGHT: case 'D': pendingMoves.push_back(Navigator::Direction::Right); break;
        case VK_RETURN: case VK_SPACE: pendingActivate = true; break;
        case VK_BACK:   case VK_ESCAPE: pendingBack = true; break;
        // Q/E stand in for the shoulders, same as WASD does for the d-pad,
        // so section-jumping is testable without a controller attached.
        case 'Q': pendingPage = -1; break;
        case 'E': pendingPage = +1; break;
        case VK_HOME: case 'G': pendingGuide = true; break;
        case 'Y': pendingY = true; break;
        // ...and the rest of the pad, for the same reason: X, Tab for Start,
        // Z and C for the triggers, F for the left stick's click.
        case 'X': pendingButtons.push_back("X"); break;
        case VK_TAB: pendingButtons.push_back("START"); break;
        case 'Z': pendingButtons.push_back("LT"); break;
        case 'C': pendingButtons.push_back("RT"); break;
        case 'F': pendingButtons.push_back("LS"); break;
        case 'N': pendingNotify = true; break;
        default: break;
        }
    };

    // Wall clock since launch, for anything that animates on its own rather
    // than in response to input -- the page selector's glow, so far.
    float elapsedSeconds = 0.0f;

    // The boot animation renders at window resolution; these are the canvas
    // units it is drawn in, matching the hub's canvas so both letterbox
    // identically.
    constexpr float kBootCanvasWidth = 1280.0f;
    constexpr float kBootCanvasHeight = 720.0f;

    LARGE_INTEGER frequency = {};
    LARGE_INTEGER previousTicks = {};
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&previousTicks);

    // Tracked separately from Renderer's own idea of the window size so
    // the letterbox math below (which runs every frame, not just on
    // resize) always has the latest value without reaching into Renderer's
    // private state.
    uint32_t currentWidth = window.GetWidth();
    uint32_t currentHeight = window.GetHeight();
    window.OnResize = [&renderer, &currentWidth, &currentHeight](uint32_t w, uint32_t h)
    {
        renderer.Resize(w, h);
        currentWidth = w;
        currentHeight = h;
    };

    bool running = true;
    while (running)
    {
        running = window.PumpMessages();
        if (!running)
        {
            break;
        }

        // Real elapsed time, not a fixed step: the gamepad's held-direction
        // auto-repeat should feel the same whether the app is running at 60fps
        // or dropped a frame.
        LARGE_INTEGER nowTicks = {};
        QueryPerformanceCounter(&nowTicks);
        float deltaSeconds = frequency.QuadPart
            ? static_cast<float>(nowTicks.QuadPart - previousTicks.QuadPart) / static_cast<float>(frequency.QuadPart)
            : 0.0f;
        previousTicks = nowTicks;
        elapsedSeconds += deltaSeconds;

        gamepad.Update(deltaSeconds);
        // The controller is read whichever window is in front (XInput), and
        // the keyboard is not: so its buttons count only while this window is
        // the one in front -- not while it is being used for something else.
        const bool padHere = (GetForegroundWindow() == window.GetHandle());

        if (booting)
        {
            // Any of the buttons that normally mean "yes" or "no" skips it,
            // which is what the console does with Start -- and what anyone
            // who has seen the boot animation twice will want.
            const bool skip = pendingActivate || pendingBack
                || (padHere && (gamepad.WasPressed(Gamepad::A) || gamepad.WasPressed(Gamepad::B)));
            pendingActivate = false;
            pendingBack = false;
            pendingMoves.clear();
            pendingPage = 0;
            pendingGuide = false;
            pendingY = false;
            pendingButtons.clear();

            bootSeconds += deltaSeconds;
            if (skip || !boot.Update(renderer.GetContext(), bootSeconds))
            {
                // Close, rather than just stopping: the render targets and
                // the vertex buffers are a few megabytes the dashboard has no
                // use for.
                boot.Close();
                booting = false;
                audio.StopAll();
                // control_kinect is keyed at frame 1 of the hub's own
                // entrance, not after it -- the sound and the slide start
                // together.
                audio.Play(SoundCue::FirstStart, navigator.GetCurrentSceneName());
                navigator.BeginIntro();
            }
            else
            {
                renderer.BeginFrame();
                // Drawn on the hub's own canvas so the letterbox matches the
                // dashboard's exactly -- the animation is 16:9 like the
                // canvas, so this is a full-bleed blit either way, but going through
                // the same path means the boot and the first dashboard frame
                // occupy precisely the same rectangle on screen.
                quadRenderer.SetLetterboxViewport(
                    static_cast<float>(currentWidth), static_cast<float>(currentHeight),
                    kBootCanvasWidth, kBootCanvasHeight);
                quadRenderer.BeginFrame(kBootCanvasWidth, kBootCanvasHeight);
                quadRenderer.DrawImage(0.0f, 0.0f, kBootCanvasWidth, kBootCanvasHeight,
                    boot.GetFrameSRV());
                renderer.EndFrame(/*vsyncOn=*/true);
                audio.Update();
                continue;
            }
        }

        if (padHere)
        {
            if (gamepad.WasPressed(Gamepad::DPadUp))    pendingMoves.push_back(Navigator::Direction::Up);
            if (gamepad.WasPressed(Gamepad::DPadDown))  pendingMoves.push_back(Navigator::Direction::Down);
            if (gamepad.WasPressed(Gamepad::DPadLeft))  pendingMoves.push_back(Navigator::Direction::Left);
            if (gamepad.WasPressed(Gamepad::DPadRight)) pendingMoves.push_back(Navigator::Direction::Right);
            if (gamepad.WasPressed(Gamepad::A))         pendingActivate = true;
            if (gamepad.WasPressed(Gamepad::B))         pendingBack = true;
            if (gamepad.WasPressed(Gamepad::LeftShoulder))  pendingPage = -1;
            if (gamepad.WasPressed(Gamepad::RightShoulder)) pendingPage = +1;
            if (gamepad.WasPressed(Gamepad::Guide))     pendingGuide = true;
            if (gamepad.WasPressed(Gamepad::Y))         pendingY = true;
            if (gamepad.WasPressed(Gamepad::X))         pendingButtons.push_back("X");
            if (gamepad.WasPressed(Gamepad::Start))     pendingButtons.push_back("START");
            if (gamepad.WasPressed(Gamepad::LeftThumb)) pendingButtons.push_back("LS");
            if (gamepad.WasTriggerPressed(false))       pendingButtons.push_back("LT");
            if (gamepad.WasTriggerPressed(true))        pendingButtons.push_back("RT");
        }

        // Not while the hub is still arriving: it isn't taking input yet, and
        // the guide's dash commands need a dashboard that is.
        if (pendingGuide && !navigator.IsIntroPlaying())
        {
            guide.SetControllerBattery(gamepad.GetBatteryLevel());
            guide.SetSignedOut(navigator.GetState("signedout") != 0);
            guide.Toggle();
        }
        pendingGuide = false;
        if (pendingNotify)
        {
            guide.Notify();
            pendingNotify = false;
        }

        if (guide.IsOpen())
        {
            // Every input belongs to the guide while it is up; the dashboard
            // underneath keeps animating but hears nothing.
            // A button the scene on show has a use of its own for goes to
            // that first ("SHORTCUT": the keyboard's X is Backspace; a pass
            // code is made of the d-pad's directions, not moved about with
            // them). Otherwise Y is the tabs' "Xbox Home", the d-pad and A
            // are themselves, and the rest are nothing.
            for (Navigator::Direction dir : pendingMoves)
            {
                const char* name = (dir == Navigator::Direction::Up) ? "UP" : (dir == Navigator::Direction::Down) ? "DOWN"
                    : (dir == Navigator::Direction::Left) ? "LEFT" : "RIGHT";
                if (!guide.Shortcut(name))
                {
                    guide.Move(dir);
                }
            }
            if (pendingActivate && !guide.Shortcut("A")) guide.Activate();
            if (pendingBack)     guide.Back();
            const bool yTaken = pendingY && guide.Shortcut("Y");
            if (pendingPage != 0)
            {
                guide.Shortcut(pendingPage < 0 ? "LB" : "RB");
            }
            for (const char* button : pendingButtons)
            {
                guide.Shortcut(button);
            }
            if (pendingY && !yTaken) guide.Home(FileUtil::WideToUtf8(kStartScene));
            pendingMoves.clear();
            pendingActivate = false;
            pendingBack = false;
            pendingPage = 0;
        }
        pendingY = false;
        pendingButtons.clear();

        for (Navigator::Direction dir : pendingMoves)
        {
            navigator.Move(dir);
        }
        pendingMoves.clear();
        if (pendingActivate)
        {
            // A hub tile can open the guide straight onto one of its pages:
            // "GUIDE:<scene>". The navigator plays the press as for any item
            // and knows nothing of the verb; the guide opens once it's done.
            const std::string action = navigator.FocusedAction();
            // A row that is typed: a manifest names it with
            // "SAY <scene>/<item>.type <field>". Whatever else A does on it
            // (a list's "Custom..." is also chosen), the guide's keyboard
            // opens for the field (BeginTyping). "GUIDE:TYPE:<field>" is the
            // same for an item that does nothing else.
            std::map<std::string, std::string>& text = TextVars();
            const auto typedRow = text.find(navigator.GetCurrentSceneName() + "/" + navigator.GetFocusedItemId() + ".type");
            navigator.Activate();
            const bool guideTypes = action.rfind("GUIDE:TYPE:", 0) == 0;
            if (navigator.IsIntroPlaying())
            {
            }
            else if (guideTypes || (action.rfind("GUIDE:", 0) != 0 && typedRow != text.end()))
            {
                BeginTyping(guideTypes ? action.substr(11) : typedRow->second);
                pendingGuideScene = "GuideKeyboard";
            }
            else if (action.rfind("GUIDE:", 0) == 0)
            {
                pendingGuideScene = action.substr(6);
            }
            // "BACK": a settings page's Done. B, once the press has played.
            pendingDone = (action == "BACK");
            pendingActivate = false;
        }
        if (pendingBack)
        {
            navigator.GoBack();
            pendingBack = false;
        }
        if (pendingPage != 0)
        {
            if (pendingPage < 0) navigator.PageLeft(); else navigator.PageRight();
            pendingPage = 0;
        }

        navigator.Update(deltaSeconds);

        if (pendingDone && !navigator.IsPressing())
        {
            navigator.GoBack();
            pendingDone = false;
        }
        if (!pendingGuideScene.empty() && !navigator.IsPressing())
        {
            guide.SetControllerBattery(gamepad.GetBatteryLevel());
            guide.SetSignedOut(navigator.GetState("signedout") != 0);
            guide.Open(pendingGuideScene);
            pendingGuideScene.clear();
        }

        // A guide row can ask for a dashboard scene (its DASH:<scene> action,
        // or Y for "Xbox Home"); the guide hands the name over as it lets go.
        // Always by way of the hub, so "Xbox Home" leaves nothing behind it
        // for B to walk back into and anything else sits one B from home.
        const std::string wanted = guide.Update(deltaSeconds, audio);
        if (wanted == "@off")
        {
            // "Turn Off Console", confirmed. The console going off is this
            // app closing.
            break;
        }
        if (!wanted.empty() && wanted != navigator.GetCurrentSceneName())
        {
            while (navigator.GoBack()) {}
            navigator.TakeSoundCues();   // nobody pressed B
            if (wanted != navigator.GetCurrentSceneName())
            {
                navigator.PerformGoto(wanted);
            }
        }
        guide.SetOutputSize(renderer.GetDevice(),
            static_cast<float>(currentWidth), static_cast<float>(currentHeight));

        // Drain whatever those moves earned. Done here, in one place, rather
        // than at each call site above: the navigator is the only thing that
        // knows a move ran along the page selector or landed on a disabled
        // item, and this keeps the input block above about input.
        // Each cue carries the scene it belongs to, so sounds.txt's
        // per-screen overrides resolve against the screen the input happened
        // on rather than whichever one the input navigated to. See
        // Navigator::QueuedSound for why that distinction is the whole ball
        // game for A and B.
        for (const Navigator::QueuedSound& queued : navigator.TakeSoundCues())
        {
            audio.Play(queued.cue, queued.scene);
        }
        audio.Update();

        // Keep the glyph atlas rasterized for the CURRENT window size. Text
        // is positioned in canvas units, but it lands on real pixels at the
        // letterbox scale -- baking at canvas size and letting the GPU
        // stretch it is what made text look soft and pixelated on any window
        // bigger than the canvas. No-ops unless the scale actually moved.
        navigator.SetOutputSize(renderer.GetDevice(),
            static_cast<float>(currentWidth), static_cast<float>(currentHeight));

        renderer.BeginFrame();

        // Normally one layer; two while a scene transition is running, back
        // to front. main.cpp doesn't need to know which -- it just draws
        // each layer under the transform the plan hands it.
        for (const Navigator::Layer& layer : navigator.BuildRenderPlan())
        {
            const Scene* scene_ = layer.scene;
            if (!scene_ || scene_->GetCanvasWidth() <= 0.0f || scene_->GetCanvasHeight() <= 0.0f)
            {
                continue;
            }
            const Scene& scene = *scene_;
            // The scene draws at its own authored canvas resolution (e.g.
            // 1120x770, whatever the source .xur's screen was). VSMain maps
            // that to clip space using FrameCB (set by QuadRenderer::
            // BeginFrame below), and SetLetterboxViewport narrows the
            // D3D11 viewport to the largest UNIFORMLY-scaled, centered
            // rectangle that fits the canvas inside the actual window --
            // matching the window's aspect ratio would be a coincidence,
            // so without this X and Y get stretched by different amounts,
            // visibly distorting spacing and text (confirmed against a
            // real screenshot).
            quadRenderer.SetLetterboxViewport(static_cast<float>(currentWidth), static_cast<float>(currentHeight),
                scene.GetCanvasWidth(), scene.GetCanvasHeight());
            quadRenderer.BeginFrame(scene.GetCanvasWidth(), scene.GetCanvasHeight());
            // BeginFrame resets the transform, so this has to come after it.
            quadRenderer.SetSceneTransform(layer.scale, layer.alpha, 0.0f, layer.offsetY);
            // The scene's own item values, plus the one thing the guide can
            // change that the dashboard shows: the gamer picture.
            Scene::Variables values = layer.values;
            values["gamerpic"] = guide.GetGamerPicture();
            auto signedOut = values.find("signedout");
            if (signedOut != values.end() && signedOut->second != 0)
            {
                values["gamerpic"] = -1;   // nobody signed in: no gamer picture (no IF gamerpic <n> matches)
            }
            scene.Draw(quadRenderer, layer.weights, layer.scrollX, elapsedSeconds, layer.content,
                layer.frame, &values, layer.listScroll);
        }

        // Over all of it, on its own canvas (480 high against the
        // dashboard's 720) and so its own letterbox -- both 16:9, so the two
        // cover the same pixels. Draws nothing while it is closed.
        guide.Draw(quadRenderer, static_cast<float>(currentWidth), static_cast<float>(currentHeight),
            elapsedSeconds);
        guide.DrawNotification(quadRenderer, static_cast<float>(currentWidth), static_cast<float>(currentHeight),
            elapsedSeconds);

        renderer.EndFrame(/*vsyncOn=*/true);
    }

    if (comInitialized)
    {
        CoUninitialize();
    }
    return 0;
}
