#pragma once

#include <memory>
#include <string>

// The UI sounds the dashboard can make. These are EVENTS, not files: which
// wav each one plays (and how loud) is decided by assets\sounds\sounds.txt,
// so retuning the mix is a text edit and a relaunch rather than a rebuild.
//
// The names are the console's, not invented here. Two sources agree on them:
// the extracted wavebank's manifest.json (which carries the XACT cue aliases)
// and the XUR skins themselves, whose XuiSoundXACT/XuiSoundXAudio nodes name
// the cue or file each control plays and the named frame it plays on. What
// the skins say, in archive_17/skin.json:
//
//   HubSlotContainer, btn_LiveTile, SmallLibrarySlot, MarketplaceSlot,
//   PinSlotSquare  (i.e. every hub tile)
//        focus -> slot_roll_on      select -> snd_buttonselect
//        disabled focus -> btn_InactiveFocus
//        disabled select -> btn_InactiveSelect
//   HubScene_Twist, HubScene_TwistButton
//        -> roll_on
//   btn_1line_icon_1text  (the template the settings list is built from)
//        focus -> sharedres://btn_Focus.xma
//        select -> sharedres://btn_Select.xma
//        disabled select -> sharedres://btn_InactiveSelect.xma
//   legend_A / legend_X / legend_Y -> snd_buttonselect, legend_B -> snd_buttonback
//   _HubSceneHomepage/HostScene -> control_kinect, on the scene's own
//        End / TransTo / TransFrom frames
//
// Note the settings list plays FILES out of shrdres.xzp where the hub plays
// XACT cues, and sharedres://btn_Focus.xma is NOT the same wave as the
// vk_focus cue the wavebank aliases to "btn_Focus" -- 0.10s against 0.03s.
// That is why Play takes a scene name.
//
// The shrdres set is the DEFAULT and the hub is the exception, not the other
// way round: every screen in this project except the hub is a menu built
// from those shared control templates (NetworkMain, SystemSettings, the oobe
// scene), so a new menu screen should sound right without anyone remembering
// to add a line for it. sounds.txt spells the carousel out under Hub:.
enum class SoundCue
{
    FirstStart,          // once, when the dashboard comes up
    Focus,               // focus landing on an item
    FocusInactive,       // ...or on a disabled one
    TwistFocus,          // focus landing on the page selector itself
    TwistLeft,           // rolling along the selector
    TwistRight,
    Select,              // A
    SelectInactive,      // A on a disabled item
    Back,                // B
    Boot,                // the boot animation's soundtrack
    GuideOpen,           // the Xbox Guide arriving (hudbkgnd.xur: xam://HUD_open.xma)
    GuideClose,          // ...and leaving (HUD_close.xma)
    GuideBlade,          // stepping a tab sideways (GuideMain.xur: BladeSwitch_N.xma)
    GuideBladeOpen,      // the blades coming back out from behind a sub-page (BladeOpen.xma)
    GuideTabSwitch,      // a guide sub-page's own tabs (QuickLaunch.xur: sharedres://tab_Switch.xma)
    Notify,              // the notification popup arriving (scr_Notification: NotifyPopup.xma)
    Count
};

// Plays short UI sounds, several at once, without blocking the frame loop.
//
// Built on miniaudio (third_party/miniaudio.h -- public domain / MIT-0,
// single header, no DLL to ship), for the same reason FontAtlas is built on
// stb_truetype: it drops into the existing build with nothing to install,
// and it speaks WASAPI on Windows and CoreAudio on macOS from identical
// code, which is one fewer thing to port if this ever goes to a Mac.
//
// The whole miniaudio header is kept out of THIS header on purpose -- it is
// 4MB and would land in every translation unit that so much as mentions a
// sound. Everything it needs lives behind the impl pointer.
class AudioEngine
{
public:
    AudioEngine();
    ~AudioEngine();
    AudioEngine(const AudioEngine&) = delete;
    AudioEngine& operator=(const AudioEngine&) = delete;

    // Starts the output device and loads every wav named by
    // <soundsDir>\sounds.txt, decoded up front so the first press of a
    // button doesn't pay for a file read. Returns false if the device or the
    // cue list couldn't be opened -- which is NOT fatal to the app: Play()
    // then does nothing and the dashboard runs silently, the same way a
    // missing texture leaves an untextured quad rather than killing the
    // frame.
    bool Initialize(const std::wstring& soundsDir);

    // Starts `cue` if one is configured, mixed over whatever is already
    // playing. Silently does nothing for a cue with no entry in sounds.txt,
    // so a partial cue list is a valid cue list.
    //
    // `scene` picks up a per-screen override when sounds.txt has one -- a
    // line reading "CUE SystemSettings:menu_select ..." beats the plain
    // "CUE menu_select ..." while that scene is current. The console needs
    // this: its settings lists are built from a different control template
    // than its hub tiles, and those templates name different waves for the
    // same gesture.
    void Play(SoundCue cue, const std::string& scene = std::string());

    // Releases voices that have finished. Call once a frame; cheap.
    void Update();

    // Cuts every sounding voice immediately. For skipping the boot: its
    // soundtrack is 19 seconds long and would otherwise keep playing over a
    // dashboard the viewer has already asked to get to.
    void StopAll();

    bool IsLoaded() const;

    // Why Initialize returned false, for the log line in main().
    const std::string& GetLastError() const;

private:
    std::unique_ptr<struct AudioEngineImpl> m_impl;
};
