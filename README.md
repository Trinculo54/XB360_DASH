# DashX360Native

A from-scratch, native C++ / Direct3D11 recreation of the Xbox 360 Metro
dashboard, built against real assets extracted from your own dash.xex/
dash.bin (XZP archives, XUS string tables, xttf fonts, and XUR8 compiled UI
layouts). No third-party dashboard-recreation codebase involved -- every
format here was reverse-engineered from scratch and every asset on screen
is pulled from your own extracted files.

This repository is the source only: the app, the cook scripts and the
extraction tools. Nothing extracted from a console is in it -- not the system
update, the unpacked packs, the fonts, nor the cooked `assets\` the app
loads -- so the folders this file names beside `src\` are ones you make from
your own dump and cook yourself, as described below.

## Status

The app renders a real, complete Xbox 360 dashboard screen: a cracked `.xur`
layout (Network Settings) flattened to absolute coordinates by the Python
cook pipeline, drawn through a small D3D11 2D renderer using the
reconstructed `xenonclatin_full.ttf` font and PNGs extracted straight out of
`dash.bin`'s XZP archives.

What's working end to end:

- XZP archive extraction, XUS strings, xttf -> TTF font reconstruction
  (4 fonts), XACT audio, and full XUR8 binary layout parsing (0 parse
  errors across all 363 `.xur` files in dash.bin).
- `xur_cook.py`: flattens a parsed `.xur` layout into a simple
  absolute-coordinate manifest + copied PNGs, implementing the XUI semantics
  listed under "What the cook pipeline models" below.
- `manifest_preview.py`: renders a cooked manifest to a PNG offline, with
  deliberately the same semantics as the D3D11 path. Lets a pipeline change
  be checked without a Windows box or a rebuild -- and any disagreement
  between it and the app is itself a bug worth chasing.
- The native renderer: WIC-based PNG loading, an stb_truetype glyph atlas, a
  small D3D11 quad renderer (solid rects, images, linear/radial gradients,
  aligned text), and a manifest loader that plays a cooked scene back every
  frame. The canvas is letterboxed into the actual window
  (`QuadRenderer::SetLetterboxViewport`) -- uniformly scaled and centered to
  fit rather than stretched to fill -- so a window whose aspect ratio doesn't
  match the scene's authored canvas doesn't distort spacing and text.
- `Renderer::Resize` checks `IDXGISwapChain::ResizeBuffers`'s HRESULT, so a
  failed resize can't silently desync the tracked size (used by the letterbox
  math every frame) from the swapchain's real size. `SetProcessDPIAware()` is
  declared in `WinMain` so Windows doesn't bitmap-stretch the whole window on
  a >100%-scaled display.

## What the cook pipeline models

Each of these was a visible defect while it was missing, and each is derived
from the files rather than eyeballed:

- **Visual templates.** Most objects carry no appearance of their own; a
  `Visual` property (`Visual=BackgroundPanel`, `Visual=Label_TestStatus`)
  names a reusable template defined once in `skin.xur`, and the runtime
  instantiates that template's figures/presenters, binding the referencing
  object's own `Text`/`ImagePath` into the presenter slot. Ignoring `Visual`
  loses the background panel outright and renders every status label in the
  wrong color and size.
- **Template state timelines.** An interactive control's template
  (`btn_single`, `XuiButton`, `legend_A`, ...) has *no* useful static look --
  the whole appearance is a Flash-style keyframe timeline, and the file's
  named frames are the state table into it (`Normal` 5, `Focus` 7, `Press`
  387, `InitFocus` 409, ...), with the runtime doing GoToAndStop as gamepad
  focus moves. `evaluate_state` reads the timeline at a named state's frame;
  `resolve_focus_path` follows the screen's own `DefaultFocus` chain
  (`Main` -> `Menu\navConfigureSettings`) to decide which single control gets
  `Focus` rather than guessing. That is where the menu's green highlight bar,
  its white focused text, its 18pt grey unfocused text, and its 1px row
  hairlines all come from. Cross-check that the evaluation lands on the right
  values: the `InitFocus` transition *starts* `txt_line3` at (51,58,64) and
  ends at the focus white, independently confirming (51,58,64) -- what
  evaluating at `Normal` produces -- is the real unfocused color, and not the
  (101,109,114) sitting on the element as a static property.
- **`Anchor`.** The XUI docking bitmask (1 Left / 2 Top / 4 Right / 8 Bottom /
  16 HCenter / 32 VCenter) that maps a template child's box into an instance's
  differently-sized box. Decoded from self-consistent usage across `skin.xur`
  and confirmed against `DashBkgnd.xur`, whose `Anchor=48` wait cursor sits at
  the exact center of its 1280x720 canvas. Without it a button's full-width
  hairline runs past the button's edge and "fill the control" highlights come
  out the template's width instead of the instance's.
- **`TextStyle`.** Text alignment lives in bits 8-12 (0x0100 Left, 0x0200
  Right, 0x0400 HCenter, 0x1000 VCenter). Pinned by `skin.xur`'s
  self-documenting template names -- `XuiLabelRightJustify` = 0x0210,
  `Label_TabTitle_Centered` = 0x0400, and `Label_BodyCentered_VH` differing
  from `Label_BodyCentered` by exactly 0x1000. Cross-checked geometrically:
  centering `Label_White_Centered` in its own authored box lands each
  connect-bar caption within ~1px of its icon's center (Network 432.3 vs 431,
  Internet 662.9 vs 662), where left alignment is ~60px off. The manifest
  carries the box and the alignment mode; the *renderer* resolves them, since
  that needs the atlas's real glyph advances and the font's ascent/descent.
- **Gradient `Rotation`.** XUI's un-rotated linear gradient axis runs LEFT TO
  RIGHT. Nearly every vertical-looking gradient in these assets gets there via
  `Rotation=-90` (the whole background panel included), and the button focus
  highlight is `+90` -- the same axis reversed. The old hardcoded
  top-to-bottom axis drew that one upside down.
- **`FillType`.** Selects *which* of a figure's (usually all present)
  FillColor / Gradient / TextureFileName fields actually paints: 0 or absent =
  solid FillColor, 2 = linear gradient, 3 = radial gradient, 4 = texture.
  Invisible in the file and glaring on screen, because most figures carry a
  colour and a gradient that look nothing alike. Pinned against a console
  capture: the connection dots (`Full1..5`) hold FillColor (0,138,0) *and* a
  ring-shaped radial gradient, and the console draws solid green discs; the
  selection bar's FillColor (0,138,0) x ColorFactor 235/255 = (0,127,0)
  matches its measured (16,125,16), where its gradient would be a much
  yellower (81,134,15)->(119,186,2).
- **`PointSize` is in points, not pixels.** Every other number in a layout is
  canvas pixels, but text sizes need the usual 96/72 before they mean anything
  to a rasterizer. Measured: the menu captions (PointSize 18) have a cap
  height of 15.5 canvas units and the title (PointSize 22) an ink height of
  28.3 -- both a 4/3 scale to within a couple of percent, and both a third
  larger than PointSize read as pixels.
- **`ColorFactor`.** XUI's per-element color modulation, multiplied into fills
  and text. A no-op almost everywhere (default 0xffffffff), but a button's
  hairlines set it to 235/255 and skipping it renders them too bright.
- **Texture fills.** `FillType=4` + `TextureFileName` means "paint this figure
  with an image" -- how the legend's A/B button glyphs are authored. Treated
  as a solid rect, they vanish.
- **Circular figures.** `_figure_is_ellipse` detects the one shape every
  circular figure in these assets actually uses -- four bezier anchors at the
  box's edge midpoints with non-degenerate control handles -- and flags it for
  the pixel shader's antialiased inscribed-ellipse mask. The curvature test
  matters as much as the anchor test: on a 1px-tall box a corner and the
  adjacent edge midpoint are within any usable tolerance of each other, which
  turned every button's underline hairline into a 1px "ellipse", i.e. a smear
  instead of a line.
- **Property dispatch by property, not class.** XUI's skinning lets a generic
  `XuiControl` carry its own literal `Text`/`ImagePath`/`Fill`, so a class
  whitelist silently drops them -- 635 text-bearing and 98 image-bearing nodes
  across 204 of the 363 files.
- **Runtime-alternate content.** Some screens author several mutually
  exclusive labels for one slot (`InternetText`/`PCText`/`MCXText` at
  identical positions) or two entire alternate detail panels as flat siblings.
  Same-position duplicates are deduped automatically; the panel case has no
  static signal at all and needs `--hide-id` against a real reference.

## Known simplifications

Documented in code, not blocking, but worth knowing about:

- Rotation/Scale/Pivot are parsed from the `.xur` but not applied during
  flattening -- fine for the scenes cooked so far, would misplace content in a
  screen that uses them.
- `XuiFigure` bezier outlines are still approximated as their bounding box
  outside the full-ellipse case, so rounded-rect corners square off. The
  ring-track backgrounds behind the connection dots (`HalfRing1..10`) are
  literal rectangles with a radial-gradient fill faking a ring, so they are
  outside what `_figure_is_ellipse` can (or should) detect. A real bezier
  rasterizer is the fix for both.
- `Stroke` (figure outlines) and `DropShadowColor` aren't emitted. The focused
  menu caption has a real drop shadow on the console that we don't draw.
- The reconstructed `xenonclatin_full.ttf` is close to but not identical to
  the console's face: at matched sizes its advances run ~10% narrow on the
  title and ~3% narrow on body text, so long strings end slightly short. One
  of the other three reconstructed fonts is probably the better match for
  headings.
- Runtime content isn't in the file: the status rows' check icons and their
  Connected/Up values are filled in by the dashboard's live network-test
  code, as is NetworkMain's "Continue" button. The labels ("Service:", ...)
  are static and do render; the three Connected values and their check icons
  appear once the cooked Xbox Live test has run (see "The three connection
  tests"), and with them the "Live Status" block (Service / Xbox stores /
  Accounts / Matchmaking, each "Up").

## The main menu (hub)

The settings screens are static XUI forms, so `xur_cook.py` flattens them
straight out of a `.xur`. The hub is not. `hubscene.xur` is 1 KB and its whole
content is one opaque `ControlPackHubScene`; `HubSceneStandard.xur` is the
same. There are no tiles, no grid, no page list and no navigation graph in any
layout file -- the hub is assembled at runtime by Lua (`HubScene.lub` 36 KB,
`HubChannel.lub` 20 KB, `HubSlot.lub`, `HubRoot.lub`, all standard Lua 5.1
bytecode compiled big-endian; header `1B 4C 75 61 51 00 00 04 04 04 08`).

The five pages are built as ONE scene laid out side by side on a single wide
surface, because that is what they are on the console: `HubSceneStandard.xur`
drives them through a `ControlPackHorizonControl` -- a horizon being a
continuous strip you slide along, not a stack of screens you cut between.
Each tile carries the camera offset that frames its own page, so moving focus
off the right edge of one page onto the left edge of the next slides the
surface there. Paging is a consequence of ordinary focus movement rather than
a separate mechanism, and the neighbouring page peeking in at the screen edge
is simply that page, really there.

The page origins are not evenly spaced -- the gaps are 975.6, 952.4, 952.5,
947.3 -- which is what you would expect from a horizon whose panels are sized
by their own content rather than a fixed-pitch pager, and reproducing them
exactly is what puts each peek back where the console has it.

Everything that isn't the current page is faded. That fade is measured, not
guessed: an off-page tile that reads (26,124,26) when it's current shows as
(62,110,63) at y=200 and (85,136,85) at y=350 when it isn't, and the backdrop
at those heights is (101,101,102) and (140,140,142). Both solve to the same
blend -- content at ~52%. It has to be a fade rather than a wash of some
colour, because the green channel goes DOWN while red and blue go up, which no
single-colour overlay can do.

It is applied as a scale on each command's own alpha (`FADE` / `FADEGROUP`),
NOT by laying a translucent panel over the off-page area. A panel has to stop
somewhere: covering the pages means covering the full height of the screen,
which also covers the legend and the gamertag that are supposed to sit above
all of this. Scaling the content's alpha dims exactly the content -- verified
by rendering the hub with the camera parked 9000 units away and diffing: the
legend row and the gamertag are identical to the pixel.

`XURs\hub_build.py` builds all of that from a declarative table whose
geometry is MEASURED off console and emulator captures. That table is
the one part of this project not derived from the rip, and it is annotated
line by line with where each number came from. Cracking the `.lub` files would
replace the table; nothing else would have to change.

The geometry is measured; most of the CONTENT is not, because the rip does
say what is on the hub, just not where:

- `archive_12\dashhome-offline.xml` is the offline hub's own definition --
  every channel, every slot, the icon each slot wears, which slots are
  disabled, and the promo: slot 2 of home, games and apps is one picture,
  `Offline_Slot_Home.jpg`, 640x398, which is the whole XBOX LIVE panel (the
  sphere, the wordmark and the three glyphs under it). The table's icons and
  `enabled` flags follow it.
- The social page is `archive_03`'s: `HubChannelLayout.xur` places every
  slot, `MeAvatarSlotScene.xur` is the gamertag and the 3x3 grid of latest
  achievements (empty), `UpsellSlotScene.xur` is the XBOX LIVE logo and its
  "Join the fun" link, and that page's tiles are a different visual from the
  others, `LiveTile_Social_Corner`, with a 32-unit icon in the top-right
  corner. Laid out from those files and checked against a capture, five
  measurable features land within 0.7 units. The link is a focus stop of its
  own; A on it, as on that page's Friends, Activity Feed and Avatar Store,
  opens the sign-up page (see "Asking for Xbox Live" below).
- The avatar is the rip's own stand-in, `AvatarSilhouette.png`, which is what
  the captures show where a console would draw the profile's 3D avatar. Only
  the soft shadow it stands on is measured.

Two things about the captures themselves, both found by having a real asset
to hold against them:

- **They are in video levels**: black is 16 and white is 235. The promo JPEG
  reads `16 + v * 219/255` of its own colours at every point sampled. Every
  fill in the table was measured off those captures and so carries that
  already; full-colour assets are put through the same transfer when they are
  cooked (`as_captured`), so they belong with what is around them.
- **The backdrop** was five gradient stops that were right at the top and
  bottom of the screen and up to 20 levels dark through the middle. It is now
  eight, fitted to every row of an emulator capture and a real-hardware one
  (which agree with each other to 1.6 levels) and following them to 1.7. It
  lives in `xur_cook.py`, so the settings screens share it.

Also from the template rather than by eye now: a disabled tile's contents are
at 0.3 opacity (`btn_LiveTile`'s NormalDisable), and a tile's caption rests 4
units off the tile's bottom edge instead of floating in a band above it.

`hub_build.py` measures text at 64x and scales back, so the same cook gives
the same manifest whichever layout engine a machine's Pillow has; a fresh cook
now reproduces the Hub in `assets\scenes`.

One corroboration that the measurements are the real grid: `hubchannellayout
.xur`, one of the few hub files with actual content, declares slots of exactly
174x97 at a 100-unit vertical pitch on an 865x398 canvas -- which is the tile
size, the tile pitch and the content height measured off the captures.

```
cd XURs
python3 hub_build.py --search-dir ..\DASH\dash_bin_embedded_assets --search-dir ..\XZP
python3 nav_check.py ..\assets\scenes --start Hub
```

The legend's buttons are `LegendScene.xur`'s own pictures (`Legend_A.png`,
`Legend_B.png`, `Legend_X.png`, `Legend_Y.png` in the shell pack,
`archive_05`): a disc with its letter drawn in, heavy, with its own shadow.
They were once the settings skin's plain discs with a letter typed over them
in the app's font, which beside a capture of the console read as a different
face a size too big. The caption has its drop shadow too (the layout's style
0x1111; one pixel right and down, half black, as measured for the page
names). The guide's legend is xam's and is a letter over a disc by design;
its letter is bold there (TextStyle bit 2, which only button letters carry),
struck twice three quarters of a pixel apart, the face having one weight.

Tile icons are real files out of the extracted archives, resolved by basename
the same way `xur_cook.py` resolves `common://` references -- `ico_64x_games`,
`ico_64x_SettingsPreferences`, `ico_64x_TurnOff` and so on all exist in the
rip under exactly the names the tiles want.

## The sign-in screen

`XURs\signin_build.py` cooks what the hub's "Sign In or Out" tile opens, with
the hub's own builder: the sign-in pack's template of three slots
(`SignInTemplate.xur`) holding this console's profile (`ProfileSlotScene.xur`)
and the two actions, Create Profile and Download Profile
(`EmptyProfileSlotScene.xur`), under the title "sign in or out". The slots'
places, the text in them and where it sits, how a focused slot changes (a
profile's text rises 20 and its avatar grows; an action's text and avatar
grow about their own pivots) and the strings are the pack's.

A on the profile puts up its menu, "Sign Out" / "Close" (`ModalMenu2Scene.xur`):
the profile moves over and the two actions shrink and fade, as the template's
`EndModal1` frame has them. That is a scene of its own (`SignInMenu`), reached
by the ordinary scene change rather than the template's slide. A on an action
asks the pack's own question -- "Before creating a new profile, the current
profile must be signed out. Sign out the current profile?" -- as a message
box (`GuideCreateProfile`, `GuideDownloadProfile`).

"Sign Out" signs out, and A on the profile then signs it back in. Each is a
scene that plays for a moment and sets the app's `signedout` as it ends
(`SETS`, see Navigation): signing in runs the profile slot's own "SIGNING IN
. .. ..." and "SIGNED IN" (its timeline's frames), signing out shows the
pack's "Signing out Trinculo54..." in the same place for a second, which is
a guess at where the console says it. While nobody is signed in there is no
"SIGNED IN" on the profile, no gamertag at the top right of the hub or on its
social page, and no gamer picture anywhere; A on the profile signs in rather
than opening its menu (`ACTIONIF`); and Create Profile and Download Profile
skip their question, there being no one to sign out first.
It lasts until the app closes.

The guide follows it. `hud.xex` has a layout of each tab for nobody being
signed in (`HomeTabSignedOut.xur`, `GamesTabSignedOut.xur`,
`SettingsTabSignedOut.xur`; Media has one for both), and they are cooked as
four more tab scenes (`GuideHomeOut` ...) that the guide shows instead, with
no gamer picture in its frame: Home is Xbox Home / Sign In / Create Profile /
Download Profile / Open Tray, Games loses Achievements and Awards, Settings
loses Profile and Account Management. "Sign In" opens the sign-in screen;
Create Profile and Download Profile go where the sign-in screen's do (see
"Create Profile" below). On the hub, the Profile tile
goes to the sign-in screen while signed out rather than to the profile's
page -- a guess. What else a signed-out console changes (the social page's
slot) isn't known and isn't changed.

### Create Profile

Create Profile and Download Profile ask the pack's question first, and Yes
goes on into `createprofile.xex`'s pages (unpacked to
`XZP\createprofile.xex.cp`, cooked by `guide_build.py`: `save_profile`).
A new profile is its name: the keyboard under the pack's "Enter Profile
Name", and then **GuideSaveProfile**, `Congratulations.xur` -- "Save
Profile", the new profile's gamer card beside Join Xbox Live / Customize
Profile / Done, each with its own words in the panel. Download Profile is
**GuideDownloadIntro**, `RecoveryIntro.xur`, whose one button needs Xbox
Live. Two small things in the app carry it: a guide row's `TYPE:<field>`
goes on to the keyboard for that field, and `SAY <field>.then <scene>` is
where the keyboard's Done goes on to instead of closing.

Judgment: that the name starts empty and has no prompt; that Done leaves for
the dashboard's home; that Customize Profile is the profile's edit page.
Nothing is created -- the name shows on that card and nowhere else, and
whoever was signed in still is. Not cooked: choosing where to save the
profile, "Creating profile", joining Xbox Live (`AddXboxLive.xur`).

No answer just closes a question. And these are judgment, none of
them being in a file: where the
template sits (centred, slots' top at the hub tiles'); what the three slots
hold; the title ("Choose your profile" is the pack's other candidate); the
avatars, which are all the rip's silhouette at the size the action slot gives
its picture (the profile's is a live 3D figure on the console); and the two
actions' icons, picked by name. Not drawn: the ring of light beside "SIGNED
IN" (the pack has no image of it), the Xbox LIVE logo, the gamerscore and the
storage location. The console's start-up version of this screen, with "Skip
Sign-In" (`SkipSigninSlot.xur`), is not cooked.

### Asking for Xbox Live

The same script cooks `SignupLanding`, which the social page's Friends,
Activity Feed, Avatar Store and "Join the fun" open. Each of them asks for
Xbox Live when pressed (`HubChannel.lub` and `UpsellSlotScene.lub`:
`Demand(user, Silver)`), and for a profile with no Xbox Live account the hub
app answers with `signup_landing.xml` (the hub pack, `archive_12`): the
title "Xbox Live" over `common://oobe_bg.jpg`, an explanation, and three
tiles -- Sign Up, Sign In, No Thanks. `TemplateSignupLandingHD.xur` places
them: the explanation's slot 640x398, the tiles 148 square down the right at
x = 670. The explanation is an HTML string in the pack, a 30 "Segoe Light"
heading and two 16px paragraphs in #EBEBEB, filled with "You need an Xbox
Live account to continue", "It's easy and free to sign up for an Xbox Live
account." and "Already have an account? Sign in now.".

Sign In opens the sign-in screen (the XML's own `Provider=Signin`) and No
Thanks goes back (`EcNavBack`). Sign Up does nothing: it launches the Live
sign-up app, which the console downloads from Xbox Live (the pack for that,
`archive_16`, has only its "Connecting to Xbox Live" panel and "Xbox Live
signup is currently unavailable. Please try again later."). Judgment: how
the HTML flows (both sizes taken as XUI point sizes, a body line between its
blocks); that the title stands where a hub page's name does; and that with
nobody signed in these four go to the sign-in screen first. The home page's
"Connect" panel is the XML's `EcConnectToLive` and still opens Network
Settings.

```
cd XURs
python3 signin_build.py --search-dir ..\DASH\dash_bin_embedded_assets --search-dir ..\XZP
```

## Typed text

The on-screen keyboard and pass code entry are xam's, so they are scenes of
the guide (`guide_build.py`: `keyboard`, `passcode`) that a dashboard row
opens over the page it is on. Their layouts are two more flash modules of the
update, unpacked into `XZP` like the guide's own (`vk.xex.vk`,
`signin.xex.signin`; `createprofile.xex.cp`, `updater.xex.updater` and
`deviceselector.xex.media` are unpacked too and not cooked). The console's own
NAND dump was checked against them: its flash file system holds the same
files as the update's `$flash_*`, byte for byte, so it adds nothing.

**GuideKeyboard** is `KeyboardMain.xur` hosting `KeyboardBase.xur`: a title,
a prompt, the edit box, five rows of ten keys with Backspace and Space under
them, and Left / Symbols / Caps and Right / Accents / Done down the sides
(the pack's strings). The characters are in no layout or string table; they
are vk.xex's own tables, read out of its unpacked image: `1234567890` /
`qwertyuiop` / `asdfghjkl-` / `zxcvbnm_@.` for letters (four rows), and five
rows each of symbols and accents. Caps, Symbols and Accents switch sets, and
the button that switched says "Alphabet" to switch back.

**GuidePasscode** is `passcode.xur`: four boxes over a grid of the ten
buttons a code is made of. The layout is a scene for something to host and
says nothing of what does, so the page around it is judgment: the
dashboard's "Create Pass Code" and its prompt where the keyboard has its
title and prompt, the block centred under them, and after four buttons
"Verify Pass Code" / "Please enter your pass code again." for four more.
A cell is pressed by the button drawn in it and by nothing else, as on the
console: the triggers, the bumpers, the d-pad's four directions, X and Y. So
no cell has focus, the d-pad doesn't move about, and A does nothing. The two
codes are not compared and nothing is stored.

What opens them (`settings_pages.py`: `TYPED`, `PRESSES`): Console Name's
"Custom..." ("Console Name" / "Enter a name for this console."), PPPoE's
User Name, Password and Service Name, Advanced Settings' Host Name, and on
Set Pass Code the pass code row and its answer row ("Pass Code Answer").
The titles and prompts are the packs' own. Console Name shows what was typed
as its Current Setting and the answer row as its second line; the PPPoE and
host name fields keep what was typed but have nowhere on their page to show
it, and "Pass Code Not Set" stays as it is.

In the app this is four small things:

- **Text variables** (`src\TextVars.h`): a TEXT whose whole string is
  `{name}` draws what the app holds under `name`, as `{time}` draws the
  clock. `SAY name text` in a manifest gives a name its first value.
- **`KEY:<text>`**: a key's action. It adds its text to `typed`;
  `KEY:BACK` takes a character off, `KEY:SPACE` adds a space, and
  `KEY:DONE` writes `typed` to the field the keyboard was opened for and
  closes it.
- **`SET:name=value`**, **`ENTER name value`** and **`HIDEIF var value`**:
  an action that sets a piece of the app's state, a scene that sets one each
  time it opens, and an item that isn't there while the state holds a value
  (focus passes through it). The keyboard's character set is `kbmode`, set
  by its side buttons; each key is cooked once per caption under `IF kbmode`
  with an `ACTIONIF` per set, and the fifth row is hidden from the letters.
  The pass code's count is `passn`.
- **A typed row** is any item a manifest names with
  `SAY <scene>/<item>.type <field>`. Whatever else A does on it, `main.cpp`
  opens the keyboard on what `<field>` holds, under `<field>.title` and
  `<field>.prompt`.

The controller is read whichever window is in front, so its buttons count
only while this app's window is (`main.cpp`); the keyboard needs no such
test.

The keyboard was checked against a photo of the console's. Every key and
button has its one-unit outline (the visual's `Edge`, a nine-grid the cooker
doesn't draw, so `guide_build.py` draws it); Backspace and Space are each as
wide as five keys, as in the photo (the layout has them narrower and its
code sizes them); only "A Select / B Back" is under it. The edit box is a
real one: a caret (the skin's `XuiCaret`, a green bar), which Left and Right
move, text typed or deleted where the caret is, and a line longer than the
box moved along to keep the caret in view. A field takes as many characters
as `SAY <field>.max` says -- 63, 63 and 39 for the PPPoE fields and 39 for
the host name are the network pack's own error strings, 15 for a profile's
name is judgment -- and 48 otherwise.

The buttons drawn on the keyboard press its keys from anywhere, by the
layout's own pictures: X Backspace, Y Space, LB and RB the cursor, LT
Symbols, RT Accents, the left stick's click Caps, Start Done. In a manifest
that is `SHORTCUT <button> <item>` (the pass code's cells are the same
thing), and a `CARET <state> a r g b` line after a TEXT makes it the edit
box. On a PC keyboard the pad's buttons are: arrows or WASD the d-pad, Enter
A, Backspace B, X and Y themselves, Q and E the bumpers, Z and C the
triggers, F the left stick's click, Tab Start, G the guide.

Not built: the caret's blink, the other languages' character sets, and the
number pads the network pages use for addresses. `nav_check.py` treats a typed row as
opening `GuideKeyboard`.

## The libraries

`XURs\library_build.py` cooks what the hub's Recent, My Games and My Apps
tiles open (`contentlibrary:`, `gameslibrary:` and `applibrary:` in
`dashhome-offline.xml`). A library is a dashboard app of its own, and what it
looks like is taken from two captures of the console (My Games and My Apps):
its name at the top right with "17 of 26" under it, where the hub has the
gamertag, and what it holds from the screen's left edge. The layouts and
every word are the rip's -- the library pack's names ("recent", "my games",
"my apps"), the marketplace pack's "{1} of {2}" and "Launch" -- and the
places are the captures': tiles and cards on a grid from x = 96 and y = 178
(the hub's own block), the title ending at the safe area's right edge,
x = 1184.

**My Apps** is a grid: two rows of `SmallLibrarySlot_Hub.xur` tiles, 197
square at a pitch of 201, filled down each column then across
(`DoubleTallBrowseTemplate.xur`), over "A Launch / B Back / X App Details".
A tile is a plate with either art fitted to it whole or an icon 64 square
and the name along the bottom; the focused one stands 1.077 times larger,
the hub's own focus. It holds the apps a console has without downloading
any, by the pack's names and in name order: Media Center (its app tile,
`AppTile_MCE.jpg`), Picture Viewer, System Music Player and System Video
Player. Which four, and which icon is whose, is by the capture and the
files' names.

**My Games** is a row of cards: "show me / all games" and "sort / titles" at
the top left (`btn_Filter`, 250x64, with its small down arrow), and the
cards across the middle -- `MarketplaceSlot.xur`, 204x398: a white plate,
the box art fitted to 184x280, the title under it and a small controller at
the bottom right -- over "A Launch / B Back". The My Games capture is of an
earlier dashboard, whose card is a little wider and shorter than this one's
and whose title is capitalised, so the cards' gap (4) is judgment, as are
the focused card standing 3% larger and there being no details line for a
game never played.

**A test game.** `GAMES` in the script is what is installed, and it holds
one made-up entry, "Test Game", to see the game libraries with something in
them. Its box art is a stand-in the script draws (a dark card with the
title on it), nothing of any real game's. My Games shows it as a card and
Recent as a tile; that Recent is a grid like My Apps, and lists a game just
installed, is judgment -- there is no capture of it. With `GAMES` empty
those two show the pack's sentence instead ("No games found. Download them
from the Games Store.", "Your recently played and downloaded items appear
here.") in `emptychannel.xur`'s box.

Not built: A on anything (Launch), App Details, the filter headers as focus
stops and their pages (`FilterChannelLayout.xur`), a game's details
(`GameOverviewChannelLayout.xur`), and scrolling -- a row or grid wider than
the screen runs off its right edge. The guide's My Games row opens the same
page.

```
cd XURs
python3 library_build.py --search-dir ..\DASH\dash_bin_embedded_assets --search-dir ..\XZP
```

## The pages under System Settings

`XURs\settings_pages.py` cooks the tree below System Settings: Console
Settings and everything it leads to, Kinect Settings, Storage, Computers, TV,
Xbox Live Vision, Family Settings (which the hub's Family tile opens) and the
four pages Configure Network's buttons open -- 58 pages. It types nothing in.
Every
one of these pages is the same form -- a list down the left, a title, and in
the right panel "Current Setting", the value, and a paragraph -- and each
says what is on it: the list's `ItemsText`, `ItemsImage` and `ItemsNavPath`
properties are its rows' captions, icons and the `.xur` each opens. A list
with captions and paths is a menu; one with captions only is the values a
setting can take. So the tree is walked from its roots along those paths and
each page is cooked as a scene named for its `.xur`, with settings_build's
frame and rows.

```
cd XURs
python3 settings_pages.py --search-dir ..\DASH\dash_bin_embedded_assets --search-dir ..\XZP
```

What it cannot know, and does about it:

- **A list the dashboard's code fills.** Console Settings is one, but its
  `.xur` still names its rows' ids, which are matched by name to the pages
  they open and captioned with each page's own header. That match is a
  judgment, and the console hides some of those rows by region. Three rows
  are not among the ids: Closed Captioning, there from memory of the
  console, and Connected Devices and Console Name, which
  `dashCSettingsStrings` lists in one run with the other captions (where in
  the list they sit is a guess).
- **Radio buttons and checkboxes.** Connected Devices is not a list: it is
  two radio groups (Xbox SmartGlass Apps, Play To) and a checkbox. Family
  Settings, Closed Captioning and Family Timer have radio groups above their
  lists too. Each such control is a row that holds a tick, drawn by the
  skin's own `XuiRadioButton` / `XuiCheckbox` visual at rest and focused,
  unticked and ticked (`xur_cook`'s Cooker cooks it). A group opens on its
  first button and a box unticked. Three guesses on Connected Devices: its
  two headings are white in the skin, which can't be read on the pale panel,
  so they are drawn dark like Xbox Live Vision's captions; its panel is one
  label the code fills, shown here as the focused control's setting ("Play
  To: On") over its paragraph, both from `dashCSettingsStrings`; and nothing
  greys out when a setting is off.
- **Two-line buttons.** Set Pass Code's three (`btn_twoline_Edit`) are a
  caption and, under it, the part that takes focus: what is set. The visual
  draws both; the second line is the code's, and is given the string table's
  words for nothing being set ("Pass Code Not Set", "Pass Code Reset Answer
  Not Set") and the first of its five reset questions (`SET_TO`) -- a
  judgment. The question page lists those five, from the same table. Its
  list is one whose rows are two lines tall (`List_1string_2line`: 72 high,
  the caption wrapping and centred top to bottom in its box), so a list's
  row height is now its visual's (`TALL`). Choosing a question there shows nowhere: that page
  has no "Current Setting", and the button back on Set Pass Code is cooked
  with the first question.
- **Where things sit.** A list's rows run down from the list's own position
  (Family Settings' starts under its radio group), a control of its own is
  where the layout put it, and the right panel's paragraph is set where and
  how its label says (Set Pass Code's fills the panel at 18 points). Up and
  down go top to bottom, wrapping.
- **A list longer than it is tall scrolls.** XUI's list shows whole rows --
  nine, in these 465-high lists of 47-high rows -- and Console Settings has
  twelve. Such a list is cooked with every row where it would sit if the list
  were tall enough, and the app moves them under a window nine rows high (see
  Navigation). Its two arrows are the list visual's own (`XuiList`'s
  `control_ScrollUp` / `control_ScrollDown`, the skin's up and down arrows
  tinted (51,51,51)), pinned to the list's bottom right -- the strip under
  the ninth row, which is what the list's extra height is for -- and each
  shows only while there are more rows that way. Going past the last row
  wraps to the first, as every list here does; whether the console's list
  wraps is not known.
- **Two more such lists, with no ids at all**: Display, and Family Settings'
  Ratings and Content. Their rows here (`DISPLAY`, `RATINGS_AND_CONTENT`) are
  the pages that sit in the pack with no other way in -- HDTV Settings,
  Screen Format, Reference Levels, HDMI Color Space, Display Discovery,
  Screen Saver; Game Ratings, Video Ratings -- chosen and ordered from memory
  of the console and from the file names. Treat both lists as a best guess.
  PAL Settings, 3D TV and TV Size are in the pack too and are still
  unreachable: where they sit isn't known.
- **Option lists the code fills from a string table** -- Time Zone, Locale,
  Language, HDTV Settings (`filled()`). The pages are in the pack with empty
  lists and the captions are in the tables, each kind in one run: 83 time
  zones in xam's `XamStrings` ("GMT+04 Abu Dhabi" ...), 41 countries and the
  languages in `dashCSettingsStrings`. Their ORDER on screen is not in the
  rip: zones are listed west to east, countries by name, languages as stored
  (by language code), resolutions upward. Seven languages are missing --
  Japanese (two), Korean and Chinese (four) -- because the Latin font has no
  glyphs for their names; the app's font atlas now bakes Latin-1, Latin
  Extended-A and Cyrillic and reads text as UTF-8, which the other twelve
  need.
- **The ratings lists** -- Game Ratings, Movie Ratings, TV Ratings -- are
  filled the same way, as a U.S. console has them: the ESRB's levels, the
  MPAA's and the TV Parental Guidelines', each with "All Games" / "All
  Movies" / "All TV Shows". The captions and the paragraph about each
  ("Allow games and game content rated EC or E. ...") are the string
  table's; the icons are the pack's own (`ESRB_E10.png`, `US_TV-PG.png`, an
  open lock for "all"; it has none for the MPAA, so Movie Ratings is text).
  Game and TV rows are the 72-high rating item (`lstRatingView`): icon,
  then caption. The panel shows the focused row's paragraph where the
  layout's own description control is, and the current setting as its icon
  (`imgCurrentSetting`) where there is one. Guesses: the order (everything,
  then the levels from the oldest audience down), that the current setting
  is the icon rather than the caption on TV Ratings (its layout has both in
  the same place), and that the icons keep their shape in their square
  boxes. What TV Ratings' second description box holds isn't known, nor
  which image Movie Ratings' would show.
- **Choosers.** Xbox Live Vision's three settings are one-row lists turned
  with left and right; each is cooked as a row that holds a value, with the
  skin's arrows either side, under its own caption. With no camera attached
  the panel beside them says so, which is the layout's own text for that.
  Family Timer's time limit is the same thing with larger arrows
  (`btn_horizontal_spinner_Arrows`), and the code fills it: here with
  quarter-hour steps from 15 Minutes to 24 Hours. Only the words "Hour",
  "Hours" and "Minutes" are the string table's; the steps are from memory of
  the console's daily limit and the wording of a value is a guess. It does
  not change to hours when Weekly is chosen, or go away for Off.
- **What a setting is set to.** An options page opens with its first option
  as the current one, which is not necessarily the console's default;
  choosing another changes "Current Setting" until the app closes. Nothing
  is applied. The four filled lists open instead on a stand-in (`CURRENT`):
  English and 720p, which are the strings' language and the canvas's size,
  and United States / Pacific time, which are assumed.
- **A list the runtime fills from the world.** Storage lists devices and TV
  lists providers; both layouts carry a label for when there is nothing to
  list ("No storage devices found."), and that is what is cooked, with none
  of the rest of the page -- which is all the console shows then.
- **Rows that are buttons of their own** rather than a list's items (Family
  Timer's, Set Pass Code's, Computers' one) are cooked where the layout puts
  them. One that names no page to open does nothing.
- **Lists that are fields to fill in**, not a setting's values (PPPoE's user
  name and password, Advanced Settings' host name): nothing in a layout says
  which a list is, so those pages are named (`FIELDS`) and their rows do
  nothing: what they open is xam's on-screen keyboard, and its layout is not
  in the rip.
- **Rows that leave.** "Done", "Save and Exit" and "Continue" are known by
  what they say (`LEAVES`): the `BACK` action, B once the press has played.
  Nothing is saved, so Save and Exit only exits.
- **An option that opens a page.** "Manual" on Edit IP Settings and on Edit
  DNS Settings leads to the fields to fill in (`OPENS`: `2018_IPSettings`,
  `2034_DNSSettings`) -- the two pages in the network pack with those
  headers, numbered next after the pages they follow. Nothing in a layout
  says so. Choosing it therefore doesn't change "Current Setting".
- **Rows that ask first.** Family Settings' "Reset to Default Settings" and
  Configure Network's "Restore to Factory Defaults" put up a message box.
  That box is xam's, so the two are cooked with the guide (`DIALOGS` in
  `guide_build.py`: GuideResetControls, GuideRestoreNetwork) and the rows
  open them there (`GUIDE:<scene>`, as the hub's Turn Off tile does). The
  titles and questions are their pages' own packs'; "Yes" / "No" are xam's
  stock captions and a guess. Either answer just closes the box.
- **Pages that are not this form** -- what the connection tests show, and
  the forms you type into. Rows that lead to them do nothing yet.

**Network Settings** itself is still `cook_networkmain.sh`'s, exactly as it
was, but its five rows are now real: `xur_cook.py --item <Id>` cooks a
control as a focusable ITEM, at rest and focused, linked by its own
NavUp/NavDown. Its first row opens Configure Network.

**The three connection tests** (also `network_pages.py`) are cooked from
`NetworkMain.xur` itself -- the Xbox 360 > Network > Internet > Xbox Live bar
with a spinner and a row of dots per hop, the status lines, and the "PC" a
media test goes to in place of the Internet -- and from `TestingScene.xur`,
the menu a test puts in place of Network Settings' own ("Stop Test"). A test
is a scene that PLAYS: each hop's spinner runs once through its own timeline
(the skin's `Network_Progress`, three seconds) with "Testing..." beside its
status line; then the hop's dots stay lit and the line reads "Connected"
with the check icon; two seconds after the last hop the scene ends and the
app goes back to Network Settings. Stop Test, or B, leaves at once.

That is the Xbox Live test, which passes. The two media tests FAIL at their
second hop, the PC: there is none. A test that fails gives way, the moment
the hop is given up on, to the page it is left as (`AFTER <scene>`, see
Navigation): `TestingScene`'s other four buttons -- Configure Network,
Restart Test, See Suggestions, Continue -- with the hops that passed lit and
"Connected", the layout's cross over the one that didn't and "Failed" beside
its line, and in place of "Visit Xbox.com/networkhelp for more help." what
went wrong ("Can't connect to your PC. Visit www.xbox.com/pcsetup for more
help."). See Suggestions opens `SuggestionScene.xur`, once per suggestion:
the problem's title and "Suggestion 1 of 3", the problem, the suggestion,
and Restart Test / Next Suggestion / Previous Suggestion. Restarting from a
suggestion runs the test again and lands back on the failed page, from which
B still goes to Network Settings.

NOTHING IS TESTED. Which test passes and which fails, the timings, "Test
Status" as the title meanwhile, and that a passed test returns to Network
Settings are judgment. So is most of the failure: the pack says what the
problems and suggestions ARE, and the layouts have the buttons and the
cross, but which strings fill the suggestion page's HTML template, their
order, the buttons' captions there, the red status icon, how the HTML is set
(dark text, 18 and 16 points, no bold face) and, for Windows Media Center,
"can't connect to" rather than "is not connected to" are choices made here.
Suggestions that point at "the following website" are left out: the pack
doesn't say which. The words themselves are the network pack's own.

Network Settings' own status lines have no values until a test has run: the
console fills them in from its last test. So once the Xbox Live test has
played to its end -- not if it was stopped -- Network Settings' three lines
read "Connected" with the check icon, until the app closes, and Network
Statistics' first two do. Under them goes NetworkMain's "Live Status" block,
which it otherwise keeps hidden: Service, Xbox stores, Accounts and
Matchmaking, each "Up" with the check icon. The test itself shows the block
too, from the moment its last hop connects. (That "Up" is what all four
say, and that the block appears then, are judgment; "Up" is the pack's
word.) `network_pages.py` adds those to the manifest
`cook_networkmain.sh` writes, after a marker line, under `IF
TestingScene_Live 1`; the script now runs it as its last step. (Cooking
Network Settings by hand, run `network_pages.py` afterwards or the values go.)
The two media tests leave nothing behind: their own line, "PC:", is not on
this page.

**Network Statistics** is the same page with its six statistics lines
(Latency, Packet loss, ...) where the Xbox Live line is, each reading "---",
the pack's word for a figure it hasn't got, and NetworkMain's lone
"Continue" button. That this is how the console makes the page up is a
judgment; nothing is measured.

**Configure Network** (`XURs\network_pages.py`) is `2004_NetworkDetails.xur`:
a tab scene, "Basic Settings" | "Additional Settings", of tall buttons that
each carry several lines -- IP Settings and DNS Settings on the first tab,
PPPoE Settings, Advanced Settings and Restore to Factory Defaults on the
second. The layout, the tab header's two states, the buttons' visuals and the
page each opens are the file's, cooked in the same shell as Network Settings.
The buttons' TEXT is not in the file -- their class sets every line from
code -- so it is filled in from the network pack's own string table (the
script checks every word is there). Which strings sit on which line is a
judgment from how they are grouped in that table; the values are not known,
so IP and DNS read "Automatic", as a console comes, and the addresses under
them are blank. Each tab is a scene of its own and left / right step between
them, as an ordinary scene change rather than the tab scene's 12-frame slide.
Choosing Manual on Edit IP Settings opens its fields page; nothing entered
there, and nothing chosen, changes the button that led there.

```
cd XURs
python3 network_pages.py --search-dir ..\DASH\dash_bin_embedded_assets --search-dir ..\XZP
```

## Navigation

Focus and scene switching live in the manifest, not in code. A focusable
element is an `ITEM` block naming where focus goes in each direction plus what
A does, and carrying two draw lists:

```
ITEM system profile - HubApps:myapps profile GOTO:NetworkMain
STATE normal
  <draw commands for the unfocused tile>
STATE focus
  <draw commands for the enlarged tile, its brighter fill and its shadow>
ENDITEM
```

A nav target may be `item` (same scene) or `scene:item`. Paging sideways keeps
your row rather than dumping you at the top, clamping when the neighbouring
page is shorter.

`viewX` on an ITEM is where the camera belongs while that item is focused, and
`LAYER fixed|scroll` decides whether following commands ride that camera or
stay pinned to the screen -- the backdrop, gamertag, page selector and legend
are fixed; page content scrolls. `Navigator::Update` eases the camera toward
the focused item's `viewX` (exponential, ~0.22s to settle, frame-rate
independent), and snaps rather than slides when the scene itself changes.

A scene may have one list that scrolls up and down. `LIST x y w h` is the
window it shows through; a row's draw commands go under `LAYER list`, at the
place the row has before any scrolling, and its ITEM carries `ROW y h`. The
navigator scrolls the list only as far as it must to keep the focused row in
the window -- focus runs to the bottom row, then the list moves under it --
with the same easing as the camera, and `Scene` draws those commands moved up
by the scroll and clipped to the window. Each scene's scroll is remembered
with its focus, so coming back to a page finds it as it was left. The
navigator also gives such a scene two variables, `listup` and `listdown`: 1
while the list has rows above or below its window (by where it is scrolling
to, not where it has eased to). A scene draws its scroll arrows under
`IF listup 1` / `IF listdown 1`.

A dashboard scene can play a timeline. The navigator counts how long each
scene has been the current one, in frames (60 a second), starting over each
time it is arrived at, and `Scene::Draw` applies that to the scene's `TRACK`s
(the same ones the guide's scenes use). A scene with `MARK Scene.End <frame>`
is left when its count gets there, as B would leave it. Scenes with no
tracks -- all but the connection tests -- are unaffected. A scene that has
played to its end is from then on a variable to every scene, named for it
and holding 1: `IF TestingScene_Live 1`. With `AFTER <scene>` it is not
left but replaced by that scene: it is not somewhere B comes back to, and if
the scene taking over is already behind it in the history, the history is
unwound to there (a test restarted from its own suggestions).

That is the app's state: named numbers the navigator keeps until the app
closes, which every dashboard scene sees as variables. Besides the name of
each scene that has played out, a scene can set others as it ends -- `SETS
signedout 1` -- and an item can do something else while one holds a value:
`ACTIONIF signedout 1 GOTO:SigningIn`, on the line after its `ITEM`. One piece
of that state means something to the app itself: while `signedout` is not 0,
main.cpp gives the scenes no gamer picture.

Items draw unfocused-first in file order, with the focused one LAST. Two
things depend on that: a focused tile grows past its own bounds and would
otherwise be clipped by whichever neighbour came later in the file, and the
hub's focus block dims every other page -- which only works once they are all
already on screen.

`Navigator` owns the current scene and a per-scene focus id (so paging away
and back returns you to the tile you left), plus a history stack that only
`A`-activations push -- `B` should undo drilling in, not walk back through
every page you paged past. `Gamepad` reads XInput through `LoadLibrary` rather
than linking it, so one binary works with `xinput1_4` / `1_3` / `9_1_0` and
runs keyboard-only on a machine with none of them.

Controls: d-pad or left stick / arrow keys or WASD to move, A / Enter / Space
to activate, B / Backspace / Esc to go back, N to fire a test achievement
popup (keyboard only). Held directions auto-repeat after
0.4s at 0.12s intervals.

The Guide button (Home or G on a keyboard) opens the Xbox Guide over whatever
is showing -- see the next section.

Three hub tiles open the guide themselves, because on the console their
command is a guide page rather than a dashboard screen
(`dashhome-offline.xml`: `EcShowGamerCard`, `EcShowOptionsUI`,
`EcShutdownConsole`). Their action is `GUIDE:<scene>`: `main.cpp` lets the
tile's press play and then calls `Guide::Open`, which brings the frame from
closed straight to full size around that page (hudbkgnd's `ClosedToFull`), or
straight to its message-box state for Turn Off (`ClosedToError`), with no
tabs behind it. B closes it again. Profile opens the gamer card page,
Preferences the Preferences page, and Turn Off the confirmation, whose OK
closes the app.

`nav_check.py` walks the whole graph statically, because the runtime is
deliberately forgiving -- a dangling link leaves focus put rather than
blanking the screen, which is right for a shipped app and useless for catching
a typo. It verifies every link and GOTO resolves, that every item is reachable
from the start scene, that nothing is a dead end, and that opposing links are
reciprocal.

## The Xbox Guide

The blade panel the Guide button opens (Home or G on a keyboard). It is not in
`dash.xex` at all: the layouts are `hud.xex`'s `hud` pack and the templates are
`huduiskin.xex`'s `skin` pack, both flash files inside the system update
(`su20076000_00000000 Extracted\$flash_*.xex`). Neither is encrypted, only
LZX-compressed, so `XZP\xex_resources.py` unpacks them with no key. (It reads
the update's loose modules too -- `dash.xex`, `Guide.*.xex`, `*.lex` -- which
use XEX "basic" compression: the image with its runs of zeros cut out. Every
module in the update unpacks except `Xna_TitleLauncher.xex`, the one that is
encrypted.)

```
cd XZP
python3 xex_resources.py "..\EXTRACTED XBOX UPDATE 2.0.17559.0\su20076000_00000000 Extracted\$flash_hud.xex" .
python3 xex_resources.py "..\EXTRACTED XBOX UPDATE 2.0.17559.0\su20076000_00000000 Extracted\$flash_huduiskin.xex" .
python3 xex_resources.py "..\EXTRACTED XBOX UPDATE 2.0.17559.0\su20076000_00000000 Extracted\$flash_gamerprofile.xex" .
python3 xex_resources.py "..\EXTRACTED XBOX UPDATE 2.0.17559.0\su20076000_00000000 Extracted\$flash_minimediaplayer.xex" .
python3 xex_resources.py "..\EXTRACTED XBOX UPDATE 2.0.17559.0\su20076000_00000000 Extracted\$flash_vk.xex" .
python3 xex_resources.py "..\EXTRACTED XBOX UPDATE 2.0.17559.0\su20076000_00000000 Extracted\$flash_signin.xex" .
cd ..\XURs
python3 guide_build.py --search-dir ..\XZP --search-dir "..\NEW_DASH\EXTRACTED XZP"
python3 nav_check.py ..\assets\guide --start GuideHome --start GuideHomeOut --opened-from ..\assets\scenes
```

(`--opened-from`: some of the guide's message boxes are opened only by
dashboard rows. The second `--start`: the signed-out tabs are opened by the
app, not by a row. Without either, `nav_check` reports those unreachable.)

### What is cooked

`guide_build.py` writes `assets\guide\`, and `src\Guide.cpp` stacks it the way
the console does -- three layers, each on its own timeline:

- **GuideFrame** -- `hudbkgnd.xur` (xam.xex): the dim, the plain centre blade,
  the clock, the gamerpic. Its timeline is the guide opening (`ClosedToHalf`,
  19 frames: the blade scales up from a sliver while the content fades in
  behind it), closing, and growing to full size for a sub-page.
- **GuideMain** -- `GuideMain.xur`'s tab scene: the side blades, their turned
  captions, the selected tab's strip, the ring of light. Its timeline is the
  tab switches (`1To2`, `2To1`, ... 12 frames each) and tucking the blades away
  while a sub-page is up.
- **GuideGames / Home / Media / Settings** -- one scene per tab holding only
  that tab's rows, from the `*TabSignedIn.xur` layouts (Media's are inline,
  plus `MiniMediaPlayer.xur`). Home is `HomeTabSignedInLive.xur`, the tab as
  a profile signed in to Xbox Live sees it -- Friends, Party, Messages,
  Beacons & Activity, Chat -- because that is where the Friends row is; the
  profile pages below are still the offline ones, so the guide now disagrees
  with itself about being on Live. `TAB_CONTENT` in `guide_build.py` switches
  it back. GuideMain's `Tab1..Tab4` tracks place them,
  which is what slides a tab's rows in from the side, clipped to the panel,
  while the previous tab's fade out.
- **GuidePreferences** and the pages under it (Online Status, Notifications,
  Voice, Vibration) -- `Options*.xur`, full-size pages hosted in place of
  GuideMain once the frame has grown.
- **GuideRecent** -- `QuickLaunch.xur`, a full-size page that is a tab scene
  itself: three accordion headers with a timeline of their own (`1To2`, ...),
  cooked as tracks the same way. Its lists are empty, so each tab shows its
  own "nothing here" label.
- **GuideFriends** -- **a reconstruction, not a real layout**, and the only
  scene in the guide that is. The Friends page belongs to
  `Guide.Friends.xex`, which is not in the dump (see below); what is cooked
  is `QuickLaunch.xur` again with different captions, because Recent is the
  guide's other full-size tabbed page. Its tab captions (Friends, Messages,
  Players) and legend are the module's own, translated back to English from
  its Dutch and Swedish string tables. See `FRIENDS` in `guide_build.py`.
  **GuideMessages** is the same page cooked to open on its second tab, which
  is where the Home tab's Messages row goes.
- **GuideAchievements**, **GuideAwards**, **GuideMusicSources** -- from two
  more flash files, `gamerprofile.xex` and `minimediaplayer.xex`. Each is a
  list the runtime fills, cooked showing the label it has for when there is
  nothing to fill it with.
- **GuideProfile**, **GuideEditProfile**, **GuideSignInPreferences** -- also
  `gamerprofile.xex`. Each profile page authors a Live set of rows and an
  offline set in the same place; the offline ones are cooked. The gamer card
  is xam's `gamercard.xur`, which likewise has a face for a profile that
  isn't on Live: gamertag, picture, and three tallies, filled from `PROFILE`
  in `guide_build.py`.
- **GuideGamerPicture**, **GuideChangeGamerPicture**,
  **GuideChangePersonalPicture** -- Edit Profile's "Gamer Picture"
  (`807_EditGamerTile.xur`) and the two grids under it. Change Gamer
  Picture's grid is filled with the thirteen stock gamer pictures `shrdres`
  ships, and choosing one works: the guide's header, every gamer card, the
  row that opened the grid and the hub's corner all wear it until the app
  closes. The panel on the right describes the focused row, in the pack's own
  words.
- **GuideGameDefaults** and **GuideGameDefaultsGeneral / Action / Racing** --
  Profile's "Game Defaults" (`830_GamerPreferences.xur`, a list of
  categories) and one `832_PreferenceCategory.xur` per category: four
  spinners each, turned with left and right. Every category, setting and
  value is from `gamerprofile.xex`'s string table (`DEFAULTS` in
  `guide_build.py`).
- **GuideTurnOff** -- "Turn Off Console"'s confirmation. Not a layout: xam
  builds a message box from the skin's `XuiMessageBox3` visual, and the frame
  hosts it in its Error state (`HalfToError`: the blade grows tall under a
  dark title bar while the guide's own scenes are switched off). OK closes
  the app; Cancel is B.

### The notification popup

**Press N** for a notification; each press is the next kind, an achievement
first. It is a test key: on a console these arrive when something happens.

Twenty-one are cooked (`NOTIFICATIONS` in `guide_build.py`), each as the
popup's icon and wording under `IF notify <n>`: achievements, an avatar
award, a friend coming online, a friend request, the party ones (invited,
joined, left, kicked), a game invite, a message, a chat request, signing in
and out, a finished download, the Xbox Live connection dropping, the song now
playing, the Family Timer, and "please reconnect controller" / "reinsert
memory unit".

The popup ("Achievement unlocked / 20G - ...") is xam's, like the guide, and
wears the guide's skin, so it is cooked with it as **GuideNotify** -- but it
is not part of the guide: it plays over whatever is on screen, guide open or
not, and takes no input. `notify.xur` is one control, and that control's
visual, `scr_Notification`, is the whole thing: an orb that pops, a pill that
unrolls from it and overshoots, the ring and its lit quadrant, an icon that
takes turns with the Xbox logo, two lines of text, and a 241-frame timeline
with its sound (`NotifyPopup.xma`) keyed on the first frame. Every child is a
`TRACK`, so the motion is the file's; `Guide::Notify` only plays its three
named ranges in order -- `TransTo`, the hold from `loop`, `TransFrom`.

What is real: the layout, the timeline, the sound, the icons (xam's pack,
beside `notify.xur`) and the wordings, which are xam's own strings
("Achievement unlocked\n%sG - %s", "%s wants you to join\nan Xbox Live
Party", ...). What is not:

- **Which icon goes with which wording.** xam's code pairs them, by
  notification type; here they are paired by what the icon files are called,
  which is plain for most and a judgment for a few (a game invite wears the
  controller, a message the mail icon).
- **Two wordings** are not in the dump and are written from memory of the
  console: "<gamertag> is online", which the friends module supplies, and
  the song now playing, which is just the track's name.
- **Everything that fills a blank** -- the friend's gamertag, the achievement,
  the download, the song -- is made up.
- **Where it sits** is a guess. The title chooses, bottom centre by default,
  and no file or capture says exactly where that is; it is placed with the
  scene's bottom edge on the title-safe line.
- **How long it holds** is a guess. The timeline marks where its hold loops
  back to but not how many times; it goes round once, about six seconds in
  all.
- **Which quadrant is lit** is player one's, top left; the visual has all
  four and the runtime shows the one for the player the popup is for.

The pill's rounded ends, the ring and the quadrant are figures whose gradient
brush is itself moved, turned or scaled, or which are mirrored -- none of
which a `GRADIENT_RECT` can say -- so `paint` in `guide_build.py` renders
those to PNGs in software, from the figures' own fills.

### Timelines, in the manifest

The animation is not re-authored anywhere. Each animated XUI object becomes a
`TRACK` block: its commands cooked once, fully visible, plus one `KEY` per
keyframe in the `.xur` saying how to scale, move and fade them at that frame.
The file's named frames become `MARK`s, and the frames a sound is keyed on
become `MARK @<wave>`. `Scene::Draw` takes a frame number and applies the
tracks; `Guide.cpp` only decides which named range each clock is playing, so
every guide sound also fires on the frame the console keys it to. See
`ManifestTrack` in `SceneManifest.h`.

What else the guide needed from the renderer, all of it general:

- `QuadRenderer::SetClip` -- XUI's ClipChildren, done by trimming each quad and
  its UVs on the CPU.
- `STATE press` -- a third item state for a control whose press is its own
  look. The guide's rows flash their highlight from (0,138,0) to (28,182,28)
  over ten frames before the action fires.
- `{time}` as a TEXT string -- the clock, in the user's locale format.
- `TEXTV` -- text turned a quarter turn clockwise about its box origin, for
  the blade captions. A turned glyph quad is still an axis-aligned quad with
  its texture corners moved round one place, so it is as sharp as any text.
- `IF var value` ... `ENDIF` -- alternatives only the running app can choose
  between. The frame cooks every legend its scenes ask for and Guide.cpp says
  which is up; likewise the four controller battery icons.
- `VALUES` / `WHEN` and the `TOGGLE`, `RADIO:<group>`, `SLIDER` actions -- an
  item that holds a value. A checkbox, a radio button and a slider are each
  cooked once per value they can hold, and A (or left / right) only changes a
  number in the Navigator. That is all the Preferences pages needed to work;
  the values last until the app closes and nothing reads them.
- `SPIN` -- a `SLIDER` that goes round, for spinners.
- A negative track scale is a mirror, gradient included.
- The `gamerpic` variable -- which stock gamer picture the profile wears.
  Wherever a layout shows it, all thirteen are cooked under `IF gamerpic <n>`;
  a tile's action is `PIC:<n>`, which `Guide.cpp` keeps and `main.cpp` passes
  to the dashboard's scenes too, so the hub's corner follows.

Two things the pages needed that are done entirely in the cook: a **filled
list** (`fill_list`: one copy of the item a list's visual carries per entry,
stacked or wrapped into a grid, exactly where the runtime would put them), and
things a row **brings with it** -- drawn over it always (a spinner's caption,
which the layout keeps as a separate label on top of the list) or only while
it has focus (a spinner's arrows; the description panel's text, which
therefore crossfades with the highlight).

`manifest_preview.py` knows all of it: `--frame` (a number or a MARK's name)
and `--var name=value` put a scene in any state offline.

### What was measured, modelled or guessed

- **Text drop shadows** are measured, off
  `REFERENCES\REAL_HW\DIRECT_CONSOLE.png`: +1px right and down at 720p, and
  only on text whose `TextStyle` has bit 0 set -- the tile captions carry a
  shadow colour and show no shadow, and that bit is what they lack (see
  `Cooker.cook_text`). Opt-in via `Cooker.shadow_offset`, so the dashboard
  scenes don't change until they are re-cooked with it.
- **The ease curve** is a model. XUI's `ease` interpolation takes three
  parameters whose formula lives in xam.xex and isn't public; a search of the
  binary for it came up empty. `SceneManifest::EaseTime` reads them as a
  trapezoid velocity profile, which is quadratic ease-in-out / ease-out at the
  values these files actually use.
- **60 frames a second** is assumed for the timelines, as elsewhere here.
- **The dim** behind the guide is drawn by xam code, not by any layout, and
  there is no guide capture to measure it from. Its level is an estimate.
- **Ticked checkboxes and radio buttons** on the sub-pages start on the
  console's defaults, not read from anywhere.
- **The gamer card's tallies** (games, gamerscore, achievements) are 0: a
  profile with nothing unlocked, which is what the Achievements page says
  too. Only the gamertag is known.
- **The legend's spacing when a caption overruns its slot** ("Game Audio" on
  Music Sources) is a guess: the slots are authored 64 units wide and nothing
  extracted says what xam does with a longer caption.
- **Two of Preferences' rows are hidden** -- Family Timer and Word
  Registration -- on inference, not on anything a file says. The Family
  Timer page is in the pack (`OptionsPlayTimer.xur`) but has no way to show a
  timer that isn't set, and Word Registration is the Japanese input method's
  dictionary; both are authored last, with a placeholder `PressPath`, the way
  a row the code shows only sometimes is. Take them out of `HIDDEN` in
  `guide_build.py` to have them back.
- **Game Defaults' order and starting values** are not from the dump. The
  string table has every word but is sorted by string id, so categories,
  settings and values are in the order of the profile settings they store,
  and each spinner starts on that setting's default, both as the XDK's
  headers have them.
- **The picture on the "Change Gamer Picture" row** is an inference from its
  template: `btn_2Line-icon-content` shows its image in the image's own
  colours where every icon row tints it, and the white glyph authored there
  would be invisible on the light blade.
- **The Friends page** is a guess from top to bottom except its words: the
  geometry and tab animation are Recent's, the three tabs and their order
  are inferred from a string table (the Swedish one has a fourth, Party), the
  header and the tab icons are picked, and the lists are left empty because
  the module has no "nothing here" text to show in their place. The English
  is a translation, not Microsoft's wording. That the Messages row opens this
  page on its Messages tab is a guess as well: the only evidence is that
  Messages is one of the page's tab captions.
- **The Turn Off message box's buttons** are a guess. Its body and title are
  consecutive in `hud.xex`'s string table, in the order every other pair
  there is; its buttons are not in that table at all, so they are two of
  xam's stock captions. OK / Cancel is what answers a statement, and OK
  having focus is the API's default.

### What is still missing

- **The real Friends page, and Party, Messages, Beacons, Chat.** Their
  layouts are not in the dump at all. They belong to `Guide.Friends.xex`,
  `Guide.Beacons.xex` and `Guide.ChatAndMessenger.xex`, which the update does
  not carry: only their `L.*.xzp` string packs are here (and for Friends, only
  five non-English locales of those). The console fetches those modules from
  Xbox Live and keeps them on its hard drive. Friends and Messages are stood
  in for by the reconstruction above; Party, Beacons & Activity and Chat do
  nothing. Nor is there the count that four of those rows show at their right
  edge, which the runtime fills in (the Messages envelope is authored, so it
  is there).
- Profile's "Join Xbox Live" and Edit Profile's "Profile Name" (the
  on-screen keyboard) and "Avatar" (the avatar editor) go nowhere. So do
  Account Management, Active Downloads, Redeem Code and the media players,
  whose pages are Live-fetched modules or dashboard apps. (The Settings
  tab's Family Settings does open the dashboard's page now.) Kinect Tuner's
  module does unpack now (`Guide.NuiTroubleshooter.xex`) but is a Lua app and
  isn't cooked.
- On the picture pages: "Download Gamer Pictures" and "Take Personal Picture"
  do nothing, Change Personal Picture's grid is empty (its pictures come from
  a camera), and the gamer card beside the grid does not preview the focused
  tile.
- On Game Defaults: A on a setting should open `831_PreferenceSetting.xur`,
  the same values as a list with a description of the setting; it isn't
  cooked, because a value can't yet be shared between two scenes.
- Nothing chosen in the guide is saved: the gamer picture, the game defaults
  and the Preferences pages all start over when the app does.
- The list pages have nothing to list, so they show their empty labels.
- The controller battery icon is cooked and wired to XInput's battery call,
  but only a wireless pad reports one and that path is untested. There is no
  headset to ask about.
- GuideMain's "Open Tray" and "Connect to Xbox Live" have no drive or
  network behind them.
Two fixes to shared code came out of this: `AccountManagementNavButton`
derives from `GuideDashCommandNavButton`, not `XuiNavButton` as XUIHelper's
XML has it (`SettingsTabSignedIn.xur` only parses that way); and in
`xur_cook.py` a `FillType=3` figure is radial whether or not its gradient also
carries a `Radial` flag, templates recurse into plain `XuiGroup` children and
draw literal `XuiImage` children, and an instance's text binds to the
presenter without a `DataAssociation`. Image names resolve whatever their
case (`sharedres://ico_32x_camera.png` is `ico_32x_camera.PNG` in the pack).

`hub_build.py` cooks the gamer picture as the thirteen `IF gamerpic`
alternatives.
## Layout

```
CMakeLists.txt
src/
  main.cpp            -- entry point, main loop, scene load + draw
  Window.h/.cpp        -- Win32 window creation + message pump
  Renderer.h/.cpp       -- D3D11 device/swapchain/backbuffer + clear/present
  QuadRenderer.h/.cpp    -- 2D quad renderer (compiles Shaders.h at runtime)
  Shaders.h              -- HLSL source (VS + solid/image/text/gradient PS)
  TextureLoader.h/.cpp    -- WIC PNG -> D3D11 texture
  FontAtlas.h/.cpp         -- stb_truetype glyph atlas baking + layout
  SceneManifest.h/.cpp      -- parses a cooked manifest.txt
  Scene.h/.cpp                -- ties a manifest to its textures/font, draws it
  Guide.h/.cpp                 -- the Xbox Guide overlay: its timelines and its stack of scenes
  FileUtil.h/.cpp               -- file I/O + path helpers
third_party/
  stb_truetype.h        -- single-header TTF rasterizer (public domain)
assets/
  fonts/xenonclatin_full.ttf
  scenes/<name>/manifest.txt + assets/*.png   -- one folder per cooked scene
  guide/<tab>/manifest.txt + assets/*.png     -- the Xbox Guide, one folder per tab
```

## Building

The project picked up new source files this session -- if you have an
existing `build\` folder from before, re-run the `cmake -B build` configure
step (not just the build step) so it notices them.

### Visual Studio (recommended on Windows)

```
cmake -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Debug
```

Open `build/DashX360Native.sln` in Visual Studio if you'd rather work
in the IDE from here on. Run `DashX360Native` (F5) -- the Network Settings
screen should appear in the window.

The build step also copies `assets\` next to the built `.exe`
automatically (see the `POST_BUILD` step in `CMakeLists.txt`) -- if you add
or update a cooked scene later, just rebuild and the copy refreshes.

### Command line (MSVC toolchain, e.g. Developer PowerShell)

```
cmake -B build
cmake --build build
```

### Cross-compile sanity build (MinGW-w64, non-Windows host)

This is how the renderer was verified in the session that produced it --
useful if you ever want to confirm the project still builds without a
Windows machine handy. (Note: `WinMain`, not `wWinMain`, is the entry
point -- see the note at the bottom of this section -- so don't pass
`-municode`.)

```
x86_64-w64-mingw32-g++ -std=c++17 -DUNICODE -D_UNICODE -DWIN32_LEAN_AND_MEAN -DNOMINMAX -Isrc -Ithird_party \
  src/main.cpp src/Window.cpp src/Renderer.cpp src/QuadRenderer.cpp src/TextureLoader.cpp src/FontAtlas.cpp src/Scene.cpp src/SceneManifest.cpp src/FileUtil.cpp \
  -ld3d11 -ldxgi -ld3dcompiler -ldxguid -lwindowscodecs -lole32 \
  -o build/DashX360Native.exe
```

Note: the entry point is `WinMain` (ANSI signature), not `wWinMain` --
deliberately, since `wWinMain` needs extra linker flags on both MSVC
(`/ENTRY:wWinMainCRTStartup`) and MinGW (`-municode`) that are easy to
forget and produce a confusing "undefined reference to WinMain" link
error. We don't use the command-line string, so there's no downside.

## Switching which scene loads

Every folder under `assets\scenes\` is loaded at startup and reachable
through the navigation graph. `kStartScene` near the top of `WinMain` in
`src/main.cpp` (currently `L"HubHome"`) picks which one appears first.

## Cooking more scenes

`XURs\cook_networkmain.sh` reproduces the shipped NetworkMain scene exactly;
start from it rather than from the bare command below if you want a screen
that looks like the console rather than like its layout file.

`xur_cook.py` lives in `XURs\` next to `xur_extract.py`/`xur_schema.py`
(the binary-format parser + schema loader it depends on). Any of the other
361 `.xur` layouts in the cracked set can be cooked the same way the two
shipped scenes were:

```
cd XURs
python3 xur_cook.py <path-to.xur> <out-manifest.txt> --assets-dir <out-assets-dir> --search-dir ..\DASH\dash_bin_embedded_assets --search-dir ..\XZP
```

e.g. to add the OOBE language-select screen:

```
python3 xur_cook.py "..\DASH\dash_bin_embedded_assets\archive_07\oobeControllerNoLanguage.xur" ..\assets\scenes\oobeControllerNoLanguage\manifest.txt --assets-dir ..\assets\scenes\oobeControllerNoLanguage\assets --search-dir ..\DASH\dash_bin_embedded_assets --search-dir ..\XZP
```

`--search-dir` should point at every extracted archive directory so
`common://`/`sharedres://` image references resolve -- `dash_bin_embedded_assets`
(the 35 archives from `dash.bin`) and `XZP` (the shared-resource packs)
cover everything seen so far. The same `--search-dir` list is also used to
auto-discover `skin.xur` (by basename, largest copy wins if there's more
than one) for `Visual` template resolution -- pass an explicit `--skin
<path>` only if your skin file lives somewhere those directories don't
cover. Not every layout will render as a complete, populated screen out of
the box -- many are template/state-scaffold layouts populated at runtime
via data binding (hidden by default), which is why `NetworkMain` and
`oobeControllerNoLanguage` were picked as the first two: they were
verified (via a throwaway PIL preview script) to render as genuinely
complete, recognizable screens on their own.

One more thing the cook script handles: some screens author several
mutually-exclusive label variants for one UI slot (e.g. NetworkMain's
`InternetText`/`PCText`/`MCXText`, all at the identical position, meant to
be toggled by runtime logic depending on what's actually detected on the
network). Some variants are explicitly marked `Show=False` in the file and
get culled automatically; others aren't, and `xur_cook.py` now keeps only
the first label claimed at a given sibling position rather than stacking
overlapping text (which is exactly what caused the garbled "PCernet" text
in the very first render of `NetworkMain` -- fixed by this dedup logic).
It can't know *which* variant runtime logic would have picked, so it just
takes the first one authored; if a re-cooked scene ever shows the "wrong"
alternate label, that's the tell to look for.

Some screens go a step further and author two entire alternate *panels* as
flat sibling controls at different (non-colliding) positions, with nothing
in the file marking one as the non-default -- no amount of static analysis
can resolve that; it needs a real screenshot to compare against. For that,
`xur_cook.py` takes a repeatable `--hide-id <Id>` flag that force-suppresses
a specific object (by its XUI `Id`) regardless of its own `Show` value.
`NetworkMain` was actually cooked with this:

```
python3 xur_cook.py "..\DASH\dash_bin_embedded_assets\archive_18\NetworkMain.xur" ..\assets\scenes\NetworkMain\manifest.txt --assets-dir ..\assets\scenes\NetworkMain\assets --search-dir ..\DASH\dash_bin_embedded_assets --search-dir ..\XZP --hide-id txtPacketLossStatus --hide-id txtUploadSpeedStatus --hide-id txtDownloadSpeedStatus --hide-id txtSignalStrengthStatus --hide-id txtLinkSpeedStatus --place-id txt_Header=122,85 --place-id legend_a=567,646 --place-id legend_b=185,646
```

Those five Ids are the "Network Statistics" detail panel (Packet loss/
Upload/Download/Signal strength/Wi-Fi speed), which sits at different
Y-coordinates than but visually interleaves with the default "Live
Status" panel (Service/Xbox stores/Accounts/Matchmaking) -- confirmed
non-default by comparing against a real console photo of this exact
screen. If you re-cook a scene and something looks like two panels
smashed together, this is the tool for it: find the offending Ids in the
parsed JSON (`XURs\xur_json_out\`) and add `--hide-id`.

## Matching the console

A screen's `.xur` is only part of what ends up on a TV. The shell composites
it, and several things the console shows are runtime state the file cannot
express. `XURs\cook_networkmain.sh` is the worked example: it cooks
NetworkMain the way a real console displays it, and every flag in it is
derived from a capture rather than chosen by eye.

What the capture established:

- **The canvas is 1280x720, not the 1120x770 the file is authored on.** In a
  2406px-wide capture the 440-unit menu buttons span 825px, putting the whole
  frame at 1283x722 canvas units. `--canvas 1280x720`.
- **The screen scene sits at (200, 114).** Horizontally that is dead centre;
  vertically it is ~6 units above centre, which centring alone will not
  produce. Cross-checked three ways -- the selection bar's top edge and the
  connect-bar strip's top and bottom -- all agreeing to within half a unit.
  `--scene-pos 200,114`.
- **The dashboard's own background is a vertical grey ramp**, dark at the top,
  brightest ~70% down, easing back off at the bottom. Sampled horizontally it
  is flat to within 2/255 at every height. `--backdrop`.
- **The A/B legend is the shell's, not the screen's.** NetworkMain carries its
  own `legend_a`/`legend_b` using skin.xur's `legend_A`/`legend_B` visuals,
  which put the glyph to the RIGHT of a right-aligned caption. The console
  shows the opposite -- glyph then caption, bottom left -- which is
  `LegendScene.xur`'s `ControlPackLegendScene`. That is a control pack, i.e.
  laid out by dashboard code, so its row y and each item's x are measured:
  glyphs at x=90.5 and 190.9, y=644.7. `--hide-id legend_a --hide-id legend_b`
  plus `--legend A=Select@96 --legend B=Back@196.4`.
- **Runtime-state groups.** The ring tracks behind the connection dots
  (`HalfRing1..10`, including the 30 inside the `Network_Progress` spinner
  visual) are not drawn at all on a connected console -- between two lit dots
  the strip is a clean gradient with no lightening. The third dot group is
  `Show=False` in the file yet lit in the capture, and the "Live Status"
  detail panel is absent even though it is statically visible. None of that is
  recoverable statically, so it is `--hide-id` / `--show-id`.

After all of it, every measurable feature lands within ~2 canvas units of the
capture: selection bar, menu captions, help box, all three dot groups, both
legend glyphs and both legend captions. What still differs is runtime content
(the status check icons and their Connected values) and font weight.

## Previewing a cooked scene without building

`XURs\manifest_preview.py` renders a manifest to a PNG using the same
semantics as `QuadRenderer`/`Shaders.h` -- the same inscribed-ellipse
coverage, the same gradient axis and stop-bracket lerp, the same top-left ->
baseline text conversion and alignment rules:

```
cd XURs
python3 manifest_preview.py ..\assets\scenes\NetworkMain\manifest.txt out.png
```

It needs numpy and Pillow. This is the fastest loop for a cook-script change,
and because it is an independent implementation of the same spec, a
disagreement between it and the running app points at a real bug in one of
them.

## Cook script flags

- `--hide-id <Id>` -- suppress an object (and its subtree) that is statically
  `Show=True` but is runtime-alternate content, e.g. a non-default detail
  panel. Confirm against a real console screenshot first.
- `--place-id <Id>=<x>,<y>` -- move an object to an absolute canvas position,
  ignoring its parent chain (and un-hiding it if `Show=False`). For the
  dashboard chrome the shell positions at runtime -- see the note under
  "Known simplifications".
- `--state <Id>=<StateName>` -- force a control's Visual template to a named
  state (`Normal`, `Focus`, `Press`, `Disabled`, ...) instead of the
  Normal/Focus choice derived from the screen's `DefaultFocus` chain. Useful
  for seeing what a screen looks like with a different row selected.
- `--show-id <Id>` -- force an object to render even though the file marks it
  `Show=False`, for runtime-toggled state the console actually displays.
- `--item <Id>[=<action>]` -- cook a control as a focusable ITEM (its resting
  and focused looks, linked by its own NavUp/NavDown) instead of as part of
  the picture, with an action such as `GOTO:<scene>` on A, or none.

Skin files are found by name under the search dirs: an earlier search dir
wins over a later one, and within one the largest file wins. (It was largest
first across all of them, which let the guide's skin -- another `skin.xur`,
unpacked under `XZP` and 126 bytes bigger than the dashboard's -- take over
the dashboard's templates.)
- `--canvas <W>x<H>` / `--scene-pos <X>,<Y>` -- composite the screen into
  shell space instead of its authoring canvas. See "Matching the console".
- `--backdrop` -- draw the dashboard's grey background behind the scene.
- `--legend <BTN>=<Label>@<X>` / `--legend-y <Y>` -- add items to the shell's
  A/B/X/Y legend row.

## Next steps

1. Crack `HubScene.lub` / `HubChannel.lub` (big-endian Lua 5.1 bytecode) and
   replace `hub_build.py`'s measured geometry table with the real tile grid,
   page list and navigation graph.
2. Apply rotation/scale/anchor/pivot during flattening (`xur_cook.py`) for
   scenes that need it.
3. Transitions. Every hub template carries `TransTo`/`TransFrom` named frames
   and the tile visuals have `InitFocus`/`KillFocus` states; the timeline
   evaluator already reads them, it just freezes on one frame instead of
   playing between two.
4. Batch-cook a larger set of the 363 layouts and wire more of them into the
   nav graph as item actions.
5. The hub's sixth channel. `dashhome-offline.xml` defines VIDEO ("tv &
   movies": My Video Apps, the promo, Browse Videos, Search Videos), and the
   real-hardware capture shows it and a "music" section; the emulator
   captures the hub is matched to show neither, so neither is built.
6. What the hub's tiles still stop short of. Friends, Activity Feed and
   Avatar Store open the sign-up page, as they do for a profile with no Xbox
   Live account; their own pages (the social pack, `archive_03`: "Xbox is
   more fun with friends.") need one, and the sign-up page's Sign Up does
   nothing. Play Game has no disc behind it. The libraries are cooked only
   empty: the "show me"
   filters (`FilterChannelLayout.xur`: All Games, Arcade, Demos ...) and a
   library with things in it are not.
7. The settings pages that are not plain lists: what is under Storage once
   there is a device (about 20 layouts), the rest of the network pages
   (wireless setup, traceroute, the other ways a test can fail), anything
   that is typed as numbers -- an IP address, a DNS server, a MAC address
   (the keyboard and pass code entry are built: see "Typed text") -- the
   layouts of Create Profile (`XZP\createprofile.xex.cp`), the sign-in
   module's own pages (`XZP\signin.xex.signin`), the update screens
   (`XZP\updater.xex.updater`) and the guide's storage picker
   (`XZP\deviceselector.xex.media`), which are unpacked and not cooked, and
   the ratings lists
   of other locales (the tables and
   icons for PEGI, USK, CERO and the rest are in the pack; only the U.S.
   ones are cooked). The Language list
   lacks its seven Japanese, Korean and Chinese entries until a font with
   those glyphs is loaded beside the Latin one.
8. The rest of signing in and out: what a signed-out console shows beyond a
   missing gamertag and picture and the guide's signed-out tabs (the social
   page's slot), the start-up sign-in with Skip Sign-In, creating or
   downloading a profile,
   and the template's own slide into and out of a profile's menu.
9. A real avatar. The social page shows the rip's silhouette stand-in.
