#pragma once

#include "AudioEngine.h"
#include "Navigator.h"
#include "QuadRenderer.h"

#include <string>

// The Xbox Guide: the blade panel the Guide button opens over whatever the
// dashboard is showing. Cooked by XURs\guide_build.py into assets\guide.
//
// On the console this is three things stacked, and it is kept as three here
// because each runs on its own clock:
//
//   GuideFrame   xam.xex's hudbkgnd.xur -- the dim, the plain centre blade,
//                the clock and the gamerpic. Its timeline is the guide
//                opening and closing (ClosedToHalf, HalfToClosed) and
//                growing to full size for a sub-page (HalfToFull, ...). It
//                also says how visible whatever it hosts is: the
//                AppHostElementId track.
//   GuideMain    hud.xex's GuideMain.xur, hosted inside that -- the side
//                blades and their captions. Its timeline is the tab switches
//                (1To2, 2To1, ...) and tucking the blades away while a
//                sub-page is up (2Open, 2Close).
//   the tabs     one scene per tab holding just that tab's rows, placed by
//                GuideMain's Tab1..Tab4 tracks -- which is what slides a
//                tab's rows in from the side, clipped to the panel, while
//                the previous tab's fade out.
//
// plus any number of full-size sub-pages (Preferences and what hangs off
// it, Recent), which are ordinary scenes reached by GOTO and hosted in place
// of GuideMain once the frame has grown; and message boxes (Turn Off
// Console's), which the frame hosts separately, in its Error state, where
// GuideMain was.
//
// The Navigator inside does focus, rows, GOTO and B exactly as it does for
// the dashboard. This class only decides which frame each timeline is on
// and draws the stack; every frame number it uses is a named MARK read out
// of the cooked manifests, not a constant here.
class Guide
{
public:
    // False if assets\guide isn't there, in which case the Guide button is
    // simply inert.
    bool Load(ID3D11Device* device, const std::wstring& guideDir, const std::wstring& fontPath);

    // True while the guide owns the controller. It goes on being drawn for a
    // few frames after this turns false, while the frame closes.
    bool IsOpen() const { return m_open; }

    void Toggle();                              // the Guide button
    // Opens the guide on `scene`. A tab is the ordinary guide; anything else
    // is the guide as the DASHBOARD opens it -- straight onto one page or
    // message box (a hub tile's "GUIDE:<scene>"), which B then closes.
    void Open(const std::string& scene);
    void Move(Navigator::Direction direction);
    void Activate(const std::string& item = std::string());   // A (`item`: on that item, wherever focus is)
    // A controller button the scene on show has a use for ("SHORTCUT"): the
    // keyboard's X, Y, LB, RB, LT, RT, LS and START. False if it has none.
    bool Shortcut(const std::string& button);
    void Back();                                // B
    // Y, the legend's "Xbox Home": put the guide away and ask for `scene`.
    void Home(const std::string& scene);

    // Advances everything. Returns the dashboard scene a row asked for (its
    // DASH:<scene> action, or Home's) on the one frame the guide lets go for
    // it, and an empty string otherwise.
    std::string Update(float deltaSeconds, AudioEngine& audio);

    // What Gamepad::GetBatteryLevel says, for the controller battery icon in
    // the guide's header: 0..3, or -1 for none (wired, or no controller).
    void SetControllerBattery(int level) { m_battery = level; }

    // Whether the dashboard's sign-in screen has signed the profile out. Set
    // before the guide is opened: it then shows its signed-out tabs (Sign In
    // where Friends was, no Profile...) and no gamer picture.
    void SetSignedOut(bool signedOut) { m_signedOut = signedOut; }

    // Which of the thirteen stock gamer pictures the profile wears, as the
    // cooked scenes' "gamerpic" IF variable. Change Gamer Picture's tiles
    // set it (their action is "PIC:<n>"); the hub draws its corner from it.
    int GetGamerPicture() const { return m_gamerPicture; }

    // Fires the notification popup. It is xam's, as the guide is, and cooked
    // with it (GuideNotify), but it is not part of the guide: it plays over
    // whatever is on screen, guide open or not, and takes no input. Each
    // call shows the NEXT notification the scene was cooked with -- an
    // achievement first -- and firing it while one is up starts over with
    // that next one.
    void Notify();

    void SetOutputSize(ID3D11Device* device, float windowWidth, float windowHeight);
    void Draw(QuadRenderer& renderer, float windowWidth, float windowHeight, float timeSeconds) const;
    // Over everything else, the guide included.
    void DrawNotification(QuadRenderer& renderer, float windowWidth, float windowHeight, float timeSeconds) const;

private:
    // One timeline's position: resting on `frame`, or playing toward `end`.
    struct Clock
    {
        float previous = 0.0f;   // where it stood last Update, for firing keyed sounds once
        float frame = 0.0f;
        float end = 0.0f;
        void Play(float from, float to);
        void Advance(float deltaSeconds);
        bool Done() const { return frame >= end; }
    };

    // What a sub-page transition is in the middle of. The two halves run one
    // after the other, not together: the blades tuck away and only then does
    // the frame grow, and on the way back it shrinks before they return.
    enum class Stage { None, Tucking, Growing, Shrinking, Untucking };

    float Mark(const char* scene, const std::string& name) const;
    // A scene cooked with "MARK Host.Error": a message box.
    bool IsDialog(const std::string& scene) const;
    // Where a page's own timeline rests for the item it has focus on: the
    // end of a switch INTO that item ("<x>To<item>End"), frame 0 failing that.
    float PageRestFrame(const std::string& scene) const;
    void PlayFrame(const std::string& from, const std::string& to);
    void PlayTabs(const std::string& from, const std::string& to);
    void Close();
    void FireSounds(const Clock& clock, const char* scene, AudioEngine& audio) const;
    void DrawMain(QuadRenderer& renderer, float hostAlpha, float timeSeconds, const Scene::Variables& variables) const;

    Navigator m_nav;
    bool m_loaded = false;
    bool m_open = false;
    bool m_visible = false;
    Clock m_frame;               // GuideFrame's timeline
    Clock m_tabs;                // GuideMain's
    Clock m_page;                // the hosted sub-page's own, if it has one ("Recent" is a tab scene too)
    Stage m_stage = Stage::None;
    std::string m_shown;         // the scene m_nav was on last Update
    size_t m_depth = 0;          // ...and how deep its history was, to tell a GOTO from a B
    int m_tab = 1;               // the tab GuideMain is showing (or last showed), 0-based
    std::string m_leaving;       // a tab still fading out, and the row it had focus on
    std::string m_leavingFocus;
    std::string m_dialog;        // the message box the frame last hosted
    std::string m_pendingDash;   // a dashboard scene to hand back once the press has played
    bool m_pendingBack = false;  // a BACK action (a message box's Cancel) waiting on the same
    int m_battery = -1;          // see SetControllerBattery
    bool m_signedOut = false;    // see SetSignedOut
    const char* TabScene(int tab) const;   // tab `tab`'s scene: the signed-in one or the signed-out
    int m_gamerPicture = 0;      // see GetGamerPicture
    Clock m_toast;               // the notification popup's timeline
    int m_toastStage = 0;        // 0 idle, 1 arriving (TransTo), 2 holding (its loop), 3 leaving (TransFrom)
    int m_toastKind = -1;        // which of the cooked notifications it shows; each Notify takes the next
};
