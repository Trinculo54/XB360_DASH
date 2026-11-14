"""
Build the dashboard's main menu (the "hub") as cooked scene manifests.

WHY THIS ISN'T xur_cook.py
--------------------------
The settings screens are static XUI forms, so `xur_cook.py` can flatten them
straight out of a `.xur`. The hub is not. `hubscene.xur` is 1 KB and its
entire content is a single opaque `ControlPackHubScene` control; likewise
`HubSceneStandard.xur`. There are no tiles, no grid, no page list and no
navigation graph in any layout file -- the hub is assembled at runtime by
Lua (`HubScene.lub` 36 KB, `HubChannel.lub` 20 KB, `HubSlot.lub`,
`HubRoot.lub`, all standard big-endian Lua 5.1 bytecode).

So the geometry below is MEASURED off console/emulator captures rather than
read out of the rip, and is the one part of this project that is not derived
from the files. Every number is annotated with what it came from. Cracking
the `.lub` files would replace this table with the real thing; the manifests
and the navigation engine wouldn't have to change.

WHAT THE RIP DOES SAY
---------------------
Not the geometry, but nearly everything else, in three places:

  archive_12/dashhome-offline.xml   the offline hub's own definition: every
        channel, every slot in it, the icon each slot wears, which slots are
        disabled, and the promo image (Offline_Slot_Home.jpg). PAGES below
        follows it for icons and `enabled`.
  archive_03 (the social pack)      HubChannelLayout.xur places everything on
        the social page; MeAvatarSlotScene.xur and UpsellSlotScene.xur are
        the profile block and the Xbox Live link on it; its skin's
        LiveTile_Social_Corner is that page's tile, icon in the corner.
        HubChannel.lub names the four icons outright.
  archive_17/skin.xur               btn_LiveTile, for a disabled tile's
        opacity, and btn_Offline, the Live link's button.

The XML also defines a sixth channel, VIDEO ("tv & movies": My Video Apps, the
promo, Browse and Search), which the emulator captures this hub is matched to
do not show and so is not built.

Corroboration that the measurements are the real grid rather than a
coincidence: `hubchannellayout.xur` -- one of the few hub files with actual
content -- declares slots of exactly 174x97 at a 100-unit vertical pitch on
an 865x398 canvas, which is the tile size, the tile pitch and the content
height measured off the captures.

The five pages are built as ONE scene, laid side by side on a single wide
surface, because that is what they are on the console: `HubSceneStandard.xur`
drives the pages through a `ControlPackHorizonControl`, a horizon being a
continuous strip you slide along rather than a stack of screens you cut
between. Each tile carries the camera offset that frames its own page, so
moving focus off the right edge of one page onto the left edge of the next
slides the surface there -- paging is a consequence of ordinary focus
movement, not a separate mechanism, and the neighbouring page peeking in at
the screen edge is simply that page, really there.

    python3 hub_build.py --search-dir ../DASH/dash_bin_embedded_assets \\
                          --search-dir ../XZP --out-dir ../assets/scenes
"""
import argparse
import os
import shutil

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
except ImportError:  # measuring the legend and baking tile shadows both degrade gracefully
    Image = ImageChops = ImageDraw = ImageFilter = ImageFont = None


def as_captured(image):
    """`image` with its levels squeezed the way REFERENCES/ shows them.

    Every capture this hub is matched to is in video levels: black is 16 and
    white is 235. The promo image is the proof -- it is in the rip, so its
    true colours are known, and the capture shows 16 + v * 219/255 of them
    at every point sampled (background 15 -> 28.9, sphere 226 -> 212, its
    highlight 250 -> 230). It is also why the icons, which peak at 235 in
    their PNGs, read 218 on screen.

    The fills and tints in this file were measured off those captures, so
    they carry that squeeze already. A full-colour asset drawn as it comes
    would be the one thing on screen that didn't -- visibly: a panel of 15
    beside tiles measured against a black of 16. So it is put through the
    same transfer here, to belong with everything measured around it."""
    lut = [int(round(16.0 + v * 219.0 / 255.0)) for v in range(256)]
    r, g, b, a = image.convert('RGBA').split()
    return Image.merge('RGBA', (r.point(lut), g.point(lut), b.point(lut), a))

from xur_cook import (BACKDROP_STOPS, LEGEND_GLYPH_IMAGE, LEGEND_GLYPH_OFFSET, LEGEND_GLYPH_SIZE,
                      LEGEND_LABEL_COLOR, LEGEND_LABEL_SHADOW,
                      LEGEND_LABEL_OFFSET, LEGEND_LABEL_POINT_SIZE, LEGEND_Y, POINT_SIZE_TO_PIXELS,
                      HALIGN_LEFT, HALIGN_CENTER, HALIGN_RIGHT, VALIGN_TOP, VALIGN_CENTER, VALIGN_BOTTOM,
                      build_image_index, resolve_image_path, _fmt)

CANVAS = (1280.0, 720.0)   # the VIEWPORT; the surface itself is much wider
SCENE_NAME = 'Hub'

# ---------------------------------------------------------------------------
# Measured geometry
# ---------------------------------------------------------------------------

# Tile geometry. A column page's tiles are NOT a fixed height at a fixed
# pitch -- they DIVIDE a fixed block, so a page with fewer tiles gets taller
# ones. Measured on REFERENCES/, as green runs along scanlines, on every page
# of the five-tab dashboard:
#
#   page      tiles   tile height   pitch    block
#   home        3        130.3      134.0    180.4 .. 578.5
#   games       3        130.6      134.0    ~same
#   apps        3        131.1      134.0    ~same
#   social      4         97.4      100.6    178.0 .. 576.6
#   settings    2 rows   197.9      202.2    178.4 .. 578.5
#
# Every page's tiles end up spanning the same ~399 units, next to a content
# panel that measures 182..578 -- the tiles fill the panel's height, however
# many of them there are. The old constants here were the four-tile case
# hardcoded (97.4 at 100.6, which is also what hubchannellayout.xur declares:
# 174x97 at a 100 pitch), so the three-tile pages -- home, games, apps -- drew
# short tiles that stopped well above the bottom of the panel beside them.
#
# So height and pitch are derived from the tile count now. A block of 178.0
# .. 578.0 with a 4-unit gap reproduces all three measured cases to within
# 0.7 units, and puts the bottom edge exactly where all three captures put
# it (578) -- which is the edge to get right, since it's the one level with
# the content panel beside it:
#
#   n=2:  h 198.00 (measured 197.90)   pitch 202.00 (measured 202.20)
#   n=3:  h 130.67 (measured 130.30)   pitch 134.67 (measured 134.00)
#   n=4:  h  97.00 (measured  97.40)   pitch 101.00 (measured 100.60)
#
# Note the n=2 row: the settings grid's row height falls out of the SAME rule,
# which is decent evidence this is really how the console lays it out rather
# than a coincidence that happens to fit. GRID_H/GRID_PITCH_Y are still the
# directly measured numbers rather than derived ones -- they were measured
# straight off a capture of that page and agree to within half a unit, so
# there's nothing to gain by computing them instead.
TILE_W = 174.5
TILE_TOP = 178.0        # top of the tile block; the content panel starts at 180
TILE_BLOCK_H = 400.0    # ...and it ends at 578, level with the panel's bottom
TILE_GAP = 4.0          # between one tile and the next, at any count


def tile_metrics(count):
    """(height, pitch) for a column page holding `count` tiles."""
    if count <= 0:
        return (0.0, 0.0)
    height = (TILE_BLOCK_H - TILE_GAP * (count - 1)) / count
    return (height, height + TILE_GAP)


GRID_X, GRID_Y = 238.7, 178.0
GRID_W, GRID_H = 197.3, 197.9
GRID_PITCH_X, GRID_PITCH_Y = 201.6, 202.2

# A focused tile grows about its own centre. Measured four ways and they
# agree: the column pages' focused tile is 187.9 wide against an unfocused
# 174.5 (1.0768), and the settings grid's focused tile is 214.7x213.9 against
# 197.3x197.9 (1.088 / 1.081). Its top-left lands where the scale predicts to
# within a unit on every page.
FOCUS_SCALE = 1.077
# The press animation, straight off btn_LiveTile's Press frames (36..55) in
# XURs/xur_json_out/archive_17/skin.json. Its _slotGroup Scale keys are
#
#     frame 35   1.08   (the focused size -- cf. FOCUS_SCALE above, which was
#     frame 43   1.00    measured off console pixels and landed on 1.077)
#     frame 55   1.08
#
# so a press is the tile falling back to its RESTING size and springing out
# again: 8 frames down, 12 up, linear, no easing on any of those keys. The
# renderer needs nothing new for it -- it is the ordinary focus morph run
# backwards and forwards, so it is expressed here as a duration and driven
# through the same 0..1 weight (see Navigator::PressWeight).
#
# Frames -> seconds at 60fps. The skin gives no frame rate; 60 is what makes
# the sibling Focus transition (19..35, also 16 frames) land at 0.27s, which
# is where the focus blend was independently tuned to by eye.
PRESS_IN_SECONDS = 8.0 / 60.0
PRESS_OUT_SECONDS = 12.0 / 60.0

# The hub's entrance, from HostScene's TransTo frames (1..90) in
# XURs/xur_json_out/archive_31/hubhomepage.json. Its Hub target keys
#
#     frame  1   Opacity 0.0   Position (0, 90, 0)
#     frame 90   Opacity 1.0   Position (0,  0, 0)
#
# linear with no easing, so the hub starts 90 units low and fully transparent
# and arrives 89 frames later. Same 60fps reading as PRESS_* above.
#
# The same timeline fires control_kinect at frame 1 -- the sound starts with
# the slide, it does not follow it.
#
# BUT the whole thing is faster than that on real hardware, and these are
# the numbers that match it -- found by playing candidates back against a
# recording of an OG console until they lined up, not derived:
#
#     0.40s  black hold, the buffer after the boot animation
#     0.76s  the slide and fade themselves
#
# which is roughly half of what 89 frames at 60fps would give. So the
# timeline is either not 60fps or the shell cuts TransTo short; the recording
# is the authority here, not the keyframes. (The press animation is still
# read at 60fps -- if that ever looks wrong, this is the reason why.)
INTRO_SECONDS = 0.76
INTRO_RISE_Y = 90.0
INTRO_HOLD_SECONDS = 0.40

# How the rise is shaped over that time. The console's keyframes are linear
# (ease_in and ease_out are 0 on every one), but 90 units at constant
# velocity reads as sluggish no matter how long it takes -- nothing in the
# motion tells the eye it is nearly done.
#
#   'linear'  what the keyframes literally say
#   'in'      slow off the mark, fastest at the end
#   'out'     quickest first, settles into place  <- matches the recording
#   'inout'   smoothstep, eased at both ends
#
# Only the MOVEMENT is shaped; the fade stays linear either way.
INTRO_EASE = 'out' 

# Tile fills, sampled down the middle of a tile away from icons and text.
# Unfocused is nearly flat; focused ramps from a bright top to the unfocused
# green and holds, which is why the last stop repeats.
TILE_NORMAL_STOPS = [(0.0, (255, 25, 120, 25)), (1.0, (255, 29, 128, 29))]
TILE_FOCUS_STOPS = [(0.0, (255, 80, 160, 80)), (0.85, (255, 16, 134, 16)), (1.0, (255, 16, 134, 16))]

# A disabled tile's focus fill. Same shine, turned well down and cut short:
# the highlight is dimmer at the corner and has died back to the resting fill
# by 35% across, where an enabled tile's runs to 85%. That is the whole of
# what marks a disabled tile as selected -- it doesn't grow, brighten its
# icon and label, or cast a shadow (see the focus block in build_hub), which
# is how the console distinguishes "you're on this" from "you can pick this".
TILE_DISABLED_FOCUS_STOPS = [(0.0, (255, 52, 140, 52)), (0.35, (255, 25, 120, 25)),
                             (1.0, (255, 29, 128, 29))]

# A focused tile casts a soft shadow onto the tile below it -- measured as a
# dip to (21,81,21) on that neighbour, against its usual (26,124,26). It is
# the NEIGHBOUR that darkens, not the focused tile's own edge -- an earlier
# cut of this got that backwards twice: first as soft rings spreading
# symmetrically outward on all four sides (a halo around the focused tile,
# not a shadow on anything), then as a single tight border hugging the
# focused tile's own edge (closer to the "tight dark line" read off a
# capture, but still centred on the wrong tile -- a border on the selected
# item, not "a slight shadow on the neighbouring item(s)"). Both looked wrong
# against the real console for the same underlying reason.
#
# Two earlier cuts of this tried to fake the blur out of primitives the
# renderer already had, and both failed for the same reason -- a rectangular
# primitive can only ever produce a shadow with straight edges and square
# corners:
#   - stacked flat RECTs read as visible STEPS, each band a uniform slab with
#     a hard alpha seam against the next.
#   - black-to-transparent GRADIENT_RECT bands fixed the stepping but were
#     still bands: a straight line of shadow off each edge, with no rolloff
#     at the corners, and drawn unconditionally -- so on a page whose focused
#     tile has no right-hand neighbour, the right band just painted a dark
#     stripe onto the empty backdrop.
#
# So the shadow is BAKED here instead, at build time, where Pillow has a real
# gaussian. The silhouette is the focused tile's own rect pulled slightly
# inside its edges, blurred; that is what gives the soft corner rolloff a
# blurred rectangle has and an axis-aligned gradient can't.
#
# It is then MASKED by what it falls on. A shadow is cast by an object onto a
# surface, and the surfaces here are at two different depths: the other tiles
# sit right behind the focused one and take the shadow at full strength,
# while the backdrop is much further back and takes it at
# SHADOW_BACKDROP_MASK -- present, but weak enough that a tile with no
# neighbour on a side doesn't stain the page there. Checked against a console
# capture: the backdrop does darken slightly around a focused tile, it just
# doesn't darken anything like as much as a neighbouring tile does.
#
# Masked as ONE image over the shadow's whole bounding box, rather than a
# separate cropped image per neighbour. Per-neighbour crops leave the ~4-unit
# grout gap between two tiles unshadowed, so bright lines cut through the
# shadow along every seam; and closing those gaps by growing each crop would
# overlap the crops, double-compositing their alpha into a dark stripe down
# the middle of each seam. A single mask can be grown to close the seams --
# union, not sum -- and then clamped to the tile block's outer edge, so the
# grout fills in without anything reaching the backdrop outside it.
#
# The region under the focused tile itself is masked out too: the enlarged
# tile covers it anyway, and masking it means the shadow can be emitted after
# the tile without drawing over it -- which in turn keeps the icon first in
# this state's image list, so the focus morph in Scene.cpp still pairs
# icon-to-icon rather than icon-to-shadow.
#
# SHADOW_ALPHA is anchored to the measurement above: black at ~88/255 over
# (26,124,26) gives (21,81,21), and a blurred edge sits at roughly half the
# silhouette's alpha, so the silhouette runs about double that. Blur radius
# and inset are eyeballed against a capture -- judgment calls, not facts.
SHADOW_ALPHA = 185
SHADOW_BLUR = 13.0     # gaussian radius, canvas units
SHADOW_INSET = 3.0     # silhouette pulled inside the tile's edge before blurring
SHADOW_REACH = SHADOW_BLUR * 3.0   # past here the gaussian is below one alpha step
SHADOW_GROUT = 3.0     # mask growth, to bridge the gap between adjacent tiles
SHADOW_BACKDROP_MASK = 70   # of 255 -- how much of the shadow the backdrop takes
SHADOW_SS = 2          # supersample factor when baking, for a smooth ramp


def bake_shadow(focus_rect, bbox, receivers):
    """Blurred silhouette of `focus_rect` over `bbox`, masked to `receivers`.

    The blur runs over the whole bounding box before masking, never on a
    pre-clipped piece: a blur that couldn't see the entire silhouette would
    read the missing part as transparent and come out too light at the seam.
    """
    fx, fy, fw, fh = focus_rect
    bx, by, bw, bh = bbox
    ss = SHADOW_SS
    size = (int(round(bw * ss)), int(round(bh * ss)))
    px = lambda rect: [(rect[0] - bx) * ss, (rect[1] - by) * ss,
                       (rect[0] + rect[2] - bx) * ss, (rect[1] + rect[3] - by) * ss]

    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle(
        px((fx + SHADOW_INSET, fy + SHADOW_INSET, fw - 2 * SHADOW_INSET, fh - 2 * SHADOW_INSET)),
        fill=(0, 0, 0, SHADOW_ALPHA))
    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR * ss))

    # Starts at the backdrop's weaker level rather than at zero, so the
    # shadow is everywhere -- the tiles just take more of it.
    mask = Image.new('L', size, SHADOW_BACKDROP_MASK)
    draw = ImageDraw.Draw(mask)
    for receiver in receivers:
        draw.rectangle(px(receiver), fill=255)
    draw.rectangle(px(focus_rect), fill=0)   # the tile hides its own shadow
    shadow.putalpha(ImageChops.multiply(shadow.getchannel('A'), mask))
    return shadow


def intersect(a, b):
    """Overlap of two (x, y, w, h) rects, or None if they don't meet."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x1 - x0 <= 0.5 or y1 - y0 <= 0.5:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def grow(rect, by):
    return (rect[0] - by, rect[1] - by, rect[2] + 2 * by, rect[3] + 2 * by)


def bounds(rects):
    """Outer box of a list of rects -- here, the page's whole tile block."""
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (x0, y0, x1 - x0, y1 - y0)

# A tile's contents. Every length here is quoted at the tile's RESTING size
# and multiplied by the tile's scale when it's drawn (see Builder.tile) --
# because a focused tile isn't a bigger box with the same furniture in it,
# it's the whole control scaled up, label and icon included. Leaving these
# fixed is what made a focused tile look subtly wrong: the box grew 7.7% and
# its text stayed put, so the label drifted inward relative to the edges and
# the icon sat small in the middle of it.
TILE_LABEL_POINT_SIZE = 14.0
TILE_LABEL_INSET = 10.0          # from LiveTile's slotDescription, x=10 in a 148-wide tile
# The label sits on the tile's bottom edge, not centred in a band above it:
# LiveTile's slotDescription is anchored 4 units off the bottom and its text
# is bottom-aligned, so the box below ends 4 up and the line rests on it.
# Measured on nine tiles across four pages, a caption's baseline is 8.0 +/- 1
# above its tile's bottom edge, which is that 4 plus the face's descent. (It
# was a 28-high box 34 up with the line centred in it -- baseline 12 up, so
# every caption on the hub sat about four units high.)
TILE_LABEL_BASELINE = 32.0       # label box's top, up from the tile's bottom edge
TILE_LABEL_BOX_H = 28.0          # label box height; the line rests on its bottom
TILE_LABEL_COLOR = {'a': 255, 'r': 235, 'g': 235, 'b': 235}
# Icons take the same tint as the labels rather than full white. The source
# PNGs already peak at (235,235,235); on console the same icons render at
# (218,218,218), and 235 tinted by 235 is 235*235/255 = 216.5 -- which lands
# on the measurement. Tinting by white would leave them at 235, i.e. visibly
# brighter than the console's.
TILE_ICON_TINT = TILE_LABEL_COLOR
# Dimmed items keep the tile colour and dim only label + icon. btn_LiveTile's
# NormalDisable frames put the slot's contents at Opacity 0.3, and that is
# what the captures show: "Browse Games" peaks at (87,155,87) on a (29,127,29)
# tile, where 0.3 of this label colour over that fill gives (91,159,91). (This
# was 105, which reads (113,171,113) -- measurably brighter than the console.)
TILE_LABEL_DISABLED_ALPHA = 77
# Icons draw at a CONSTANT size, not a fraction of the tile. Backed out of
# the captures by dividing each icon's rendered ink by that PNG's own ink
# fraction, so a glyph that doesn't fill its 64x64 canvas isn't mistaken for
# a smaller draw:
#
#   settings grid (tile h 197.9)   Preferences 47.9   Turn Off 48.2   Family 47.6
#   console home  (tile h 130.7)   Recent      46.5
#
# Three very different tile heights, one draw size. The old value of 64 was
# the SOURCE asset's dimension rather than a measurement -- which happened to
# come out right on the four-tile pages, where the h*0.5 clamp below caught
# it at 48.5 anyway, and too big everywhere else. 48 is also exactly 3/4 of
# the 64x64 assets, so they downsample cleanly.
TILE_ICON_MAX = 48.0
TILE_ICON_CENTER_FRACTION = 0.42 # icon centre as a fraction of tile height, measured on both layouts
# The social page's tiles are a different visual, LiveTile_Social_Corner: the
# icon is small and sits in the top-right corner, leaving the middle for the
# count the tile shows when there is one to show ("3 friends online"). From
# the template: a 32x32 presenter, 8 down, in a 40-wide column that ends 8
# short of the tile's right edge -- so its left edge is 40 in from the right.
CORNER_ICON_SIZE = 32.0
CORNER_ICON_FROM_RIGHT = 40.0
CORNER_ICON_TOP = 8.0

# The page selector ("twist"). HubSceneStandard.xur puts `_Twist` at (153, 97)
# 900x84 with Visual=HubScene_Twist, and skin.xur's HubScene_TwistSelButton /
# HubScene_TwistButton both render their caption at PointSize 24, the
# unselected one at half opacity. The entries do NOT scroll: each page's own
# caption appears at a fixed x, and those x's are the same across all five
# captures, so they're listed rather than computed.
TWIST_Y, TWIST_H = 97.0, 84.0
TWIST_POINT_SIZE = 24.0
TWIST_COLOR = {'a': 255, 'r': 235, 'g': 235, 'b': 235}
TWIST_UNSELECTED_ALPHA = 128
# The selector is a FLOW, not a table of fixed positions: each caption is
# placed after the previous one's advance plus a constant gap. Fitted to the
# seven captions of the console capture (REFERENCES/REAL_HW), using the
# UNSELECTED face's metrics -- the layout has to be independent of which
# entry is selected, or the row would shift sideways as you page:
#
#     gap: mean 45.32   stdev 2.26   over all six measured gaps, but the
#     two widest residuals both sit either side of "tv & movies" -- a caption
#     with an ampersand and two spaces, where the advance estimate is least
#     trustworthy. Fitting only the three captions our own page list shares
#     with the console, in sequence, gives 43.5 and 44.5, so 44.0 is used.
#     predicted vs measured ink start, all seven captions:
#         home 155.0/155  social 277.9/276  games 396.7/394
#         tv & movies 530.6/527  music 733.7/735  apps 855.6/856
#         settings 965.0/965          -- worst error 3.6 units
#
# The old hardcoded table was five x's each read off a DIFFERENT capture
# (those five-tab shots show only the current page's caption), so their
# errors never cancelled: solving the same flow against them gives gaps
# scattered from 36.9 to 45.6, where the console's own row is consistent to
# a couple of units. Computing it also means adding or renaming a page can't
# leave a stale coordinate behind.
TWIST_START_X = 155.0
TWIST_GAP = 44.0

# The page selector is set in Segoe Xbox Light, not the Xbox TC everything
# else uses. Established by measuring, not guessed -- the layouts carry no
# typeface property at all, so the only evidence is the captures.
#
# The test is scale-invariant: for each label, work out what pixel size each
# candidate face would need to produce the ink width measured off
# REFERENCES/XBOX_360_RIP_DASH.png. The face the text is really set in gives
# ONE consistent answer across every label; the wrong one's per-glyph errors
# don't cancel, so its implied size scatters. Across all seven labels of the
# seven-tab twist:
#
#     Xbox TC            implied size  mean 29.73px   spread 2.42   stdev 0.80
#     Segoe Xbox Light   implied size  mean 31.87px   spread 1.82   stdev 0.66
#
# Light is both tighter AND lands on 31.87 ~ 32.0, which is exactly
# PointSize 24 at 96/72. Xbox TC would need 29.73px, i.e. PointSize 22.3 --
# not a size anyone authors. Note this applies to the SELECTED entry too,
# which is only brighter, not a different face.
#
# The same test on the tile labels says the opposite, which is why the hub
# needs both faces rather than a swap: measured across five settings tiles,
# Xbox TC implies 18.53px (spread 1.40) against Light's 20.09 (spread 1.79),
# and 18.53 is the 18.667px that PointSize 14 already gives.
TWIST_FONT = 'SegoeXbox-Light.ttf'

# The selector is focusable in its own right: up from the top tile lands on
# it, left/right steps across the sections quickly, and down drops back into
# the tiles. While it has focus the current section carries a soft circular
# glow that breathes.
#
# NOT MEASURED. No capture in REFERENCES/ has the selector focused, so the
# glow's size, colour and rate are a judgment call -- unlike the captions
# they sit behind, which are pinned to hardware.
# Sized FROM the caption rather than fixed, so it hugs "home" and "settings"
# alike -- a fixed box that suits one is wrong for the other, and the first
# cut of this was a fixed 210 units, roughly 2.4x the width of "games" and
# far too big. Padded out from the caption's own advance by TWIST_GLOW_PAD_X
# a side, which puts it at about 1.3x, matching the reference.
TWIST_GLOW_PAD_X = 16.0
TWIST_GLOW_H = 54.0
# Neutral and faint. It's a lift in the backdrop behind the word, not a lamp.
TWIST_GLOW_STOPS = [(0.0, (55, 235, 235, 235)), (0.5, (24, 235, 235, 235)),
                    (1.0, (0, 235, 235, 235))]
TWIST_GLOW_SECONDS = 1.6
TWIST_GLOW_FLOOR = 0.35
TWIST_ITEM_PREFIX = 'twist_' 

# The signed-in gamertag, top right, with the gamerpic after it. Both
# measured off REFERENCES/Screenshot 2026-08-20 021632.png: the caption's
# bright pixels run out at x=1147.2, then a 10.9-unit gap, then a 32.6-unit
# square of saturated colour at (1158.1, 37.5).
#
# The box used to be 279.4 wide, ending at 1179.4 -- which put the caption
# right where the gamerpic belongs. The old comment said "right-aligned so
# it ends at 1179.4 with the gamerpic immediately after it", but 1179.4 is
# roughly where the GAMERPIC ends (1190.2), not the text, so a right-aligned
# caption was being drawn across it. Nothing looked wrong only because there
# was no gamerpic there to collide with.
GAMERTAG_BOX = (900.0, 30.0, 247.2, 40.0)
GAMERTAG_POINT_SIZE = 18.0
GAMERPIC_RECT = (1158.1, 37.5, 32.6, 32.6)
# The stock gamerpics. shrdres ships thirteen of these, named by the
# dashboard's own title id (fffe07d1) at 32 and 64 pixels; the 64 is used
# even though it draws at ~33 units, because the scene is letterboxed up on
# any window bigger than the canvas and the extra detail is free. All
# thirteen are cooked, each under IF gamerpic <n>: which one the profile
# wears is the app's to say (the guide's Change Gamer Picture page sets it),
# and the first, the Xbox logo, is the one it starts with.
GAMERPIC_IMAGES = [f'64_fffe07d10002{n:04x}0001{n:04x}.png' for n in range(13)]

# The black content panel that fills the right of a column page.
PANEL_RECT = (411.0, 180.0, 639.0, 397.2)
PANEL_COLOR = {'a': 255, 'r': 29, 'g': 29, 'b': 29}
# The panel is focusable, like the tiles beside it -- A on it opens the
# network settings. None of the captures in REFERENCES/ has it focused, so
# unlike the tile geometry this look is a JUDGMENT CALL, not a measurement:
# it lifts to a slightly lighter charcoal with the same top-left shine the
# tiles use, and casts the same drop shadow onto whatever is beside it. It
# does NOT grow -- at 639x397 a 7.7% pop would shove it a long way into the
# next page, and a panel that size reads as selected from the lift alone.
# Flat, but a gradient rather than a RECT so it pairs with the focus fill in
# Scene.cpp's morph (which matches commands by type) and crossfades into it.
#
# The panel itself is not a fill and a logo, it is one picture: slot 2 of the
# HOME, GAMES and APPS channels in dashhome-offline.xml is
# <shallowimg>epix://Offline_Slot_Home.jpg</shallowimg>, 640x398 -- the
# panel's size. Its background is 15, which as_captured puts at the 29 the
# captures show (PANEL_COLOR). So the focus lift is now a wash laid OVER the
# picture rather than a brighter fill under a logo: white at the alphas that
# give the same (66, 38, 33) over 29 the old fill stops had. The resting wash
# is the same three stops at alpha 0, so the two pair and crossfade.
PROMO_IMAGE = 'Offline_Slot_Home.jpg'
PANEL_NORMAL_STOPS = [(0.0, (0, 255, 255, 255)), (0.55, (0, 255, 255, 255)), (1.0, (0, 255, 255, 255))]
PANEL_FOCUS_STOPS = [(0.0, (42, 255, 255, 255)), (0.55, (10, 255, 255, 255)), (1.0, (5, 255, 255, 255))]
# The panel pops out on focus like a tile does, but NOT by the tiles' 7.7% --
# it's 639x397 against a tile's 174x130, so the same percentage would throw
# its edges ~49 units sideways and shove it well into the next page. Matched
# by how far the EDGE moves instead, which is what actually reads as "popping
# out": a focused tile lifts its edges about 6.7 units sideways and 5.0 up
# and down, and 1.025 on the panel gives 8.0 and 5.0. Same movement, on an
# element three times the size.
PANEL_FOCUS_SCALE = 1.025
PANEL_ACTION = 'GOTO:NetworkMain'
PANEL_ITEM_SUFFIX = '_promo'
# Two lines because the manifest's TEXT command draws a single run with no
# wrapping -- the console wraps this itself, and this is where the break lands.
PANEL_CAPTION = ['Connect your Xbox 360 to the Internet to explore games, entertainment,', 'and more']
# Where the caption sits, off REFERENCES/Screenshot 2026-08-20 021632.png: its
# ink starts 9.6 units in from the panel's left edge, and its two baselines
# are 23.5 apart with the second 7.3 above the panel's bottom edge. (It was
# 16 in and 11 units higher, placed by eye before the panel had a picture to
# be measured against.)
PANEL_CAPTION_INSET = 9.0
PANEL_CAPTION_RISE = 50.55      # first line's box top, up from the panel's bottom
PANEL_CAPTION_PITCH = 23.5

# The social page, right of its tiles. All of it is HubChannelLayout.xur's:
# with the Friends tile's top-left as (0, 0), slot 2 at (40, 142) holds
# MeAvatarSlotScene.xur and slot 9 at (195, 50) holds UpsellSlotScene.xur.
#
#   avatarScene (48, -138) in slot 2, and inside it
#       txtGamertag   (128, 29)   331x46, PointSize 21, top-aligned
#       achievements  (130, 108)  btn_Achievements32x32: nine 32x32 pictures
#                                 at a 34 pitch -- the profile's latest
#                                 achievements, EmptyIcon.png where there are
#                                 none (MeSlotScene.lub)
#       XuiAvatar1    (-54, -143) 668x668, Scale 0.84 about its centre
#   gridOffline (329, 38) in slot 9, rows 28 / 11 / text / 8 / 48
#       imgLogo       (12, 0)     common://LiveLogo.png, 28 high
#       btnAction     (0, 47)     312x48, btn_Offline: a 40x40 arrow at
#                                 (1, 4) and its caption at (45, 4)
#
# Checked against REFERENCES/Screenshot 2026-08-20 021723.png, where five
# features can be measured: the gamertag's ink, the grid's first cell, the
# logo, the button's caption and the avatar. Taking the tile column's own
# measured position as the origin and nudging it by ME_NUDGE lands all five
# within 0.7 units.
#
# The avatar is the one thing here that is not a layout's: XuiAvatar draws a
# 3D figure. What the captures show in its place is the rip's own stand-in,
# common's AvatarSilhouette.png -- its ink is 119x341 in the file and
# 92.5x263.7 on screen, the same shape -- fitted to the control's height and
# centred in it, which puts it within a unit of where it was measured.
#
# It stands on a soft round shadow, which is the avatar renderer's and in no
# file: measured as an ellipse 144 wide and 32 high under the feet, darkening
# the backdrop by 21 levels of 182 across its middle half and fading out.
ME_SHADOW = (368.0, 360.0, 72.0, 16.0)      # centre (x under the control's centre), half-width, half-height
ME_SHADOW_STOPS = [(0.0, (29, 0, 0, 0)), (0.5, (28, 0, 0, 0)), (0.7, (23, 0, 0, 0)),
                   (0.85, (13, 0, 0, 0)), (1.0, (0, 0, 0, 0))]
ME_NUDGE = (1.0, 0.6)
ME_AVATAR_CENTRE = (40.0 + 48.0 - 54.0 + 334.0, 142.0 - 138.0 - 143.0 + 334.0)
ME_AVATAR_SIDE = 668.0 * 0.84
ME_AVATAR_ASPECT = 436.0 / 730.0            # AvatarSilhouette.png
ME_GAMERTAG = (40.0 + 48.0 + 128.0, 142.0 - 138.0 + 29.0, 331.0, 46.0)
ME_GAMERTAG_POINT_SIZE = 21.0
ME_GRID = (40.0 + 48.0 + 120.0 + 10.0, 142.0 - 138.0 + 108.0)
ME_GRID_CELL, ME_GRID_PITCH = 32.0, 34.0
ME_LOGO = (195.0 + 329.0 + 12.0, 50.0 + 38.0, 28.0 * 424.0 / 57.0, 28.0)   # LiveLogo.png is 424x57
ME_BUTTON = (195.0 + 329.0, 50.0 + 38.0 + 47.0, 312.0, 48.0)
ME_BUTTON_ICON = (1.0, 4.0, 40.0, 40.0)
ME_BUTTON_TEXT = (45.0, 4.0, 312.0 - 49.0, 40.0)        # the template's 321 of 370, anchored both sides
ME_BUTTON_CAPTION = 'Join the fun'                      # dashStrings.xus
# btn_Offline's focus: a green plate fades in behind the button and the whole
# thing grows to 1.05 x 1.1. The plate is the tiles' own glass, so it takes
# their focus fill; at rest it is the same stops at alpha 0.
ME_BUTTON_FOCUS_SCALE = (1.05, 1.1)

# Where each page's origin sits along the surface. Derived from the captures:
# when page k is centred, page k+1's tile column appears at a measured screen
# x, so the gap to the next origin is that x minus the next page's own column
# offset. The five gaps are NOT equal -- 975.6, 952.4, 952.5, 947.3 -- which
# is what you would expect from a horizon whose panels are sized by their own
# content rather than a fixed-pitch pager, and reproducing them exactly is
# what puts each page's peek back where the console has it.
PAGE_GAPS = [975.6, 952.4, 952.5, 947.3]

# Everything that isn't the current page is faded back into the backdrop.
# Measured rather than guessed: an off-page tile that reads (26,124,26) when
# it's the current page shows as (62,110,63) at y=200 and (85,136,85) at
# y=350 when it isn't -- and the backdrop at those heights is (101,101,102)
# and (140,140,142). Both solve to the same blend, content at ~52% over the
# backdrop, which is a fade rather than a grey wash (note the green channel
# goes DOWN while red and blue go up -- no single-colour overlay does that).
# Reproduced by laying the backdrop's own gradient back over the off-page
# area at the complementary alpha, so the fade matches at every height.
# Off-page content is faded, and the fade is applied to the content's own
# alpha (see FADE/FADEGROUP in SceneManifest.h) rather than by covering it.
# 0.52 is measured, not chosen: a tile that reads (26,124,26) as the current
# page shows as (62,110,63) at y=200 and (85,136,85) at y=350 when it isn't,
# and the backdrop at those heights is (101,101,102) and (140,140,142). Both
# solve to the same blend. It has to be a fade rather than a wash of some
# colour, because the green channel goes DOWN while red and blue go up.
# The span is one page gap, so a neighbour is fully faded at rest and the two
# pages crossfade through each other during a slide.
PAGE_FADE_ALPHA = 0.52
PAGE_FADE_SPAN = 950.0

# ---------------------------------------------------------------------------
# Page content
# ---------------------------------------------------------------------------
#
# Icons and `enabled` are dashhome-offline.xml's, slot for slot (the social
# page's are HubChannel.lub's) -- see the top of this file. Files resolve by
# basename, same as xur_cook does. `corner` is the social page's tile visual
# (see CORNER_ICON_*), and `me` gives a page the profile block and Xbox Live
# link (see ME_*).
#
# Three of these icons were close guesses before the XML was found and are
# now the named files: My Games was ico_64x_games (a folder and a controller;
# the XML says the plain controller), Friends was ico_64x_profiles, and
# Profile was ico_64x_your_profile. My Pins is the same pin as before, from
# its 64-pixel file rather than the 32.

PAGES = [
    dict(name='home', scene='HubHome', column_x=230.6, panel=True,
         legend=[('A', 'Select'), ('X', 'Game Details'), ('Y', 'Eject')],
         tiles=[
             dict(id='playgame', label='Play Game', icon='ico_32x_OpticalMedia.png'),
             dict(id='mypins', label='My Pins', icon='ico_64x_overlay_bookmark.png', enabled=False),
             dict(id='recent', label='Recent', icon='ico_64x_clock.png'),
         ]),
    dict(name='social', scene='HubSocial', column_x=207.0, panel=False, me=True,
         legend=[('A', 'Select')],
         tiles=[
             dict(id='friends', label='Friends', icon='ico_64x_Friend.png', corner=True),
             dict(id='activity', label='Activity Feed', icon='ico_64x_ActivityFeed.png', corner=True),
             dict(id='avatarstore', label='Avatar Store', icon='ico_64x_AvatarMarketplace.png', corner=True),
             dict(id='signin', label='Sign In or Out', icon='ico_64x_SignIn.png', corner=True),
         ]),
    dict(name='games', scene='HubGames', column_x=229.7, panel=True,
         legend=[('A', 'Select')],
         tiles=[
             dict(id='mygames', label='My Games', icon='ico_64x_controller.png'),
             dict(id='browsegames', label='Browse Games', icon='ico_64x_Marketplace.png', enabled=False),
             dict(id='searchgames', label='Search Games', icon='ico_64x_Search.png', enabled=False),
         ]),
    dict(name='apps', scene='HubApps', column_x=229.5, panel=True,
         legend=[('A', 'Select')],
         tiles=[
             dict(id='myapps', label='My Apps', icon='ico_64x_Apps.png'),
             dict(id='browseapps', label='Browse Apps', icon='ico_64x_Marketplace.png', enabled=False),
             dict(id='searchapps', label='Search Apps', icon='ico_64x_Search.png', enabled=False),
         ]),
    dict(name='settings', scene='HubSettings', grid=True, panel=False,
         legend=[('A', 'Select')],
         tiles=[
             dict(id='system', label='System', icon='ico_64x_console.png'),
             dict(id='profile', label='Profile', icon='ico_64x_pic.png'),
             dict(id='preferences', label='Preferences', icon='ico_64x_SettingsPreferences.png'),
             dict(id='kinect', label='Kinect', icon='ico_64x_kinect_device.png', enabled=False),
             dict(id='account', label='Account', icon='ico_64x_AccountManagement.png', enabled=False),
             dict(id='privacy', label='Privacy', icon='ico_64x_privacy.png', enabled=False),
             dict(id='family', label='Family', icon='ico_64x_family.png'),
             dict(id='turnoff', label='Turn Off', icon='ico_64x_TurnOff.png'),
         ]),
]

# Items that drill into a screen the cook pipeline already produces. Anything
# not listed here has no action yet and A does nothing on it.
ACTIONS = {
    # System Settings sits between the hub and the network screen now, the
    # way the console has it -- NetworkMain is reached from that screen's
    # "Network Settings" row rather than straight off the tile.
    'system': 'GOTO:SystemSettings',
    # Three tiles whose command in dashhome-offline.xml is a page of the
    # guide rather than of the dashboard -- EcShowGamerCard, EcShowOptionsUI,
    # EcShutdownConsole. GUIDE:<scene> opens the guide straight onto that
    # page, with no tabs behind it, and B closes it (main.cpp, Guide::Open).
    'profile': 'GUIDE:GuideProfile',
    'preferences': 'GUIDE:GuidePreferences',
    'turnoff': 'GUIDE:GuideTurnOff',
    # EcNavToFamilySettings: a dashboard screen, one of settings_pages.py's.
    'family': 'GOTO:dashSysCslSetPControl',
    # The sign-in screen: signin_build.py's.
    'signin': 'GOTO:SignIn',
    # The three libraries (contentlibrary:, gameslibrary:, applibrary: in
    # dashhome-offline.xml), as they are with nothing in them: library_build.py's.
    'recent': 'GOTO:Recent',
    'mygames': 'GOTO:MyGames',
    'myapps': 'GOTO:MyApps',
    # Xbox Live's. They ask for it (HubChannel.lub: Demand(user, Silver)),
    # and a profile with no account is shown the sign-up page instead:
    # signin_build.py's SignupLanding.
    'friends': 'GOTO:SignupLanding',
    'activity': 'GOTO:SignupLanding',
    'avatarstore': 'GOTO:SignupLanding',
}
# What a tile does instead while nobody is signed in (ACTIONIF signedout 1).
# Profile is the profile's own page; that with no profile it sends you to
# sign in is a guess, and so is that Xbox Live's tiles do the same.
SIGNED_OUT = {'profile': 'GOTO:SignIn', 'friends': 'GOTO:SignIn', 'activity': 'GOTO:SignIn',
              'avatarstore': 'GOTO:SignIn'}


class HubBuilder:
    def __init__(self, image_index, font_path=None):
        self.font_path = font_path
        self._font_cache = {}
        self.image_index = image_index
        self.lines = []
        self.used_images = {}
        # Shadow crops are generated here rather than copied from the rip --
        # baked_images is filename -> PIL image, written out alongside the
        # copied ones. shadow_names dedups them by relative geometry.
        self.baked_images = {}
        self.shadow_names = {}
        self.twist_names = []       # page captions, in selector order
        self.warnings = []

    def emit(self, *fields):
        self.lines.append(' '.join(_fmt(f) for f in fields))

    def comment(self, text):
        self.lines.append(f'# {text}')

    def font(self, name=None):
        """Set the face for the TEXT commands that follow (sticky)."""
        self.lines.append(f'FONT {name or "default"}')

    def measure(self, s, size_points, font_name=None):
        """Advance width of `s` at `size_points`, in canvas units.

        The legend is a flow layout -- each item starts after the previous
        one's caption -- so laying it out needs the same glyph advances the
        renderer will use. Falls back to a crude per-character estimate if
        Pillow or the font isn't available, which only costs a slightly wrong
        gap between legend items.
        """
        px = size_points * POINT_SIZE_TO_PIXELS
        path = self.font_path
        if font_name:
            path = os.path.join(os.path.dirname(os.path.abspath(self.font_path)), font_name)
        if ImageFont is None or not path or not os.path.isfile(path):
            return 0.5 * px * len(s)
        key = (round(px, 2), font_name or '')
        if key not in self._font_cache:
            # Measured at 64x and scaled back. Pillow's basic layout hints
            # every advance to a whole pixel at the size asked for; its Raqm
            # layout keeps the fraction. Which one a machine has is a build
            # detail, and a caption a unit off shows in the twist -- so
            # measure fine enough that both give the same answer.
            self._font_cache[key] = ImageFont.truetype(path, size=int(round(px)) * 64)
        return self._font_cache[key].getlength(s) / 64.0

    def rect(self, x, y, w, h, c):
        self.emit('RECT', x, y, w, h, 0, c['a'], c['r'], c['g'], c['b'])

    def gradient(self, x, y, w, h, stops, angle=-90.0):
        self.emit('GRADIENT_RECT', x, y, w, h, 0, 0, angle, len(stops))
        for pos, (a, r, g, b) in stops:
            self.emit('  STOP', pos, a, r, g, b)

    def text(self, s, x, y, w, h, size_points, color, halign=HALIGN_LEFT, valign=VALIGN_CENTER, alpha=None):
        a = color['a'] if alpha is None else alpha
        self.lines.append(
            f'TEXT {_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} {halign} {valign} '
            f'{_fmt(size_points * POINT_SIZE_TO_PIXELS)} {a} {color["r"]} {color["g"]} {color["b"]} {s}')

    def image(self, name, x, y, w, h, alpha=255, tint=None):
        resolved = resolve_image_path(name, self.image_index)
        if resolved is None:
            self.warnings.append(f'icon {name!r} not found in any search dir')
            self.comment(f'skipped icon {name!r} (not found)')
            return
        rel = os.path.basename(resolved)
        self.used_images[resolved] = rel
        t = tint or {'r': 255, 'g': 255, 'b': 255}
        self.emit('IMAGE', x, y, w, h, rel, alpha, t['r'], t['g'], t['b'])

    def captured(self, name, x, y, w, h, alpha=255):
        """IMAGE for a full-colour asset, baked through as_captured first."""
        resolved = resolve_image_path(name, self.image_index)
        if resolved is None or Image is None:
            self.warnings.append(f'image {name!r} not found in any search dir (or no Pillow to bake it)')
            self.comment(f'skipped image {name!r}')
            return
        baked = 'captured_' + os.path.splitext(os.path.basename(resolved))[0] + '.png'
        if baked not in self.baked_images:
            self.baked_images[baked] = as_captured(Image.open(resolved))
        self.emit('IMAGE', x, y, w, h, baked, alpha, 255, 255, 255)

    # -- pieces ----------------------------------------------------------

    def backdrop(self):
        self.gradient(0.0, 0.0, CANVAS[0], CANVAS[1], BACKDROP_STOPS)

    def twist_slots(self):
        """(name, x, width) per caption, in the same flow twist() uses -- so
        anything that must line up with a caption asks here instead of
        repeating the walk."""
        out = []
        x = TWIST_START_X
        for name in self.twist_names:
            w = self.measure(name, TWIST_POINT_SIZE, TWIST_FONT)
            out.append((name, x, w))
            x += w + TWIST_GAP
        return out

    def twist_glow(self, selected):
        """The breathing halo behind the focused caption."""
        for name, x, _w in self.twist_slots():
            if name != selected:
                continue
            # Measured in the SELECTED face, not the flow face. twist_slots
            # deliberately walks with Light's advance so the captions don't
            # shuffle sideways as focus moves (see TWIST_GAP), but the ink
            # actually under the glow is the wider Xbox TC copy. Sizing and
            # centring the halo off Light's narrower advance pulled it left
            # of the caption by an amount that grew with the word -- measured
            # -1.7px on "home" up to -6.0px on "settings".
            w = self.measure(name, TWIST_POINT_SIZE)
            cx, cy = x + w / 2.0, TWIST_Y + TWIST_H / 2.0
            glow_w = w + 2.0 * TWIST_GLOW_PAD_X
            self.lines.append(f'PULSE {_fmt(TWIST_GLOW_SECONDS)} {_fmt(TWIST_GLOW_FLOOR)}')
            # Radial AND ellipse-masked, so the quad's corners don't paint a
            # faint square around it -- see DrawGradientRect's isEllipse note.
            self.emit('GRADIENT_RECT', cx - glow_w / 2.0, cy - TWIST_GLOW_H / 2.0,
                      glow_w, TWIST_GLOW_H, 1, 1, 0.0, len(TWIST_GLOW_STOPS))
            for pos, (a, r, g, bb) in TWIST_GLOW_STOPS:
                self.emit('  STOP', pos, a, r, g, bb)
            self.lines.append('PULSE -')
            return

    def twist(self, selected, glow=False):
        """The whole page-selector row, with `selected` picked out.

        The selected entry is a different FACE, not merely a brighter one.
        Measured on console hardware (REFERENCES/REAL_HW/DIRECT_CONSOLE.png),
        with the threshold taken at each label's OWN contrast so a dim label
        isn't penalised for being dim:

            home (selected)     ink width 80   median stem 3.00   cap rows 24
            social/games/apps                  median stem 1.00-2.00

            Xbox TC @32px       ink width 78   median stem 3.00   cap rows 24
            Segoe Xbox Light    ink width 72   median stem 2.00   cap rows 25

        So it is Xbox TC at the SAME PointSize 24 -- the size doesn't change
        and neither does the position, only the face.

        Drawn here, per focused item, rather than once as fixed chrome with
        the selected entry over-drawn on top. That was the old arrangement
        and it can't survive two faces: Light is narrower than TC, so a dim
        Light copy underneath pokes out to the left of every glyph and the
        selected label picks up a visible doubled edge.
        """
        if glow:
            self.twist_glow(selected)
        for name, x, _w in self.twist_slots():
            chosen = (name == selected)
            self.font(None if chosen else TWIST_FONT)
            self.text(name, x, TWIST_Y, 300.0, TWIST_H, TWIST_POINT_SIZE, TWIST_COLOR,
                      HALIGN_LEFT, VALIGN_CENTER,
                      TWIST_COLOR['a'] if chosen else TWIST_UNSELECTED_ALPHA)
        self.font()

    def gamertag(self, name):
        x, y, w, h = GAMERTAG_BOX
        # Only while someone is signed in (the app's `signedout`, which the
        # sign-in screen's scenes set: signin_build.py). The picture needs no
        # such test: with nobody signed in the app gives no `gamerpic`.
        self.emit('IF', 'signedout', 0)
        self.text(name, x, y, w, h, GAMERTAG_POINT_SIZE, TWIST_COLOR, HALIGN_RIGHT, VALIGN_CENTER)
        self.lines.append('ENDIF')
        for n, image in enumerate(GAMERPIC_IMAGES):
            self.emit('IF', 'gamerpic', n)
            self.image(image, *GAMERPIC_RECT)
            self.lines.append('ENDIF')

    def legend(self, items, start_x=96.0):
        x = start_x
        for button, label in items:
            image = LEGEND_GLYPH_IMAGE.get(button.upper())
            if image:
                self.image(image, x + LEGEND_GLYPH_OFFSET, LEGEND_Y, LEGEND_GLYPH_SIZE, LEGEND_GLYPH_SIZE)
            self.text(label, x + LEGEND_LABEL_OFFSET + 1.0, LEGEND_Y + 1.0, 512.0, LEGEND_GLYPH_SIZE,
                      LEGEND_LABEL_POINT_SIZE, LEGEND_LABEL_SHADOW, HALIGN_LEFT, VALIGN_CENTER)
            self.text(label, x + LEGEND_LABEL_OFFSET, LEGEND_Y, 512.0, LEGEND_GLYPH_SIZE,
                      LEGEND_LABEL_POINT_SIZE, LEGEND_LABEL_COLOR, HALIGN_LEFT, VALIGN_CENTER)
            # Advance past the caption. Measured on the captures: the A item
            # occupies 100.4 units for "Select", which is glyph + offset +
            # caption width + a ~22-unit gap.
            x += LEGEND_LABEL_OFFSET + self.measure(label, LEGEND_LABEL_POINT_SIZE) + 22.0
        return x

    def content_panel(self, origin=0.0, stops=None, scale=1.0):
        """The promo panel. Returns the rect it drew at, for the shadow.

        `scale` grows it about its own centre exactly as tile() does, logo
        and caption included -- it is one control scaling up, not a bigger
        box with the same furniture in it.
        """
        x, y, w, h = inflate((PANEL_RECT[0] + origin, PANEL_RECT[1], PANEL_RECT[2], PANEL_RECT[3]), scale)
        self.captured(PROMO_IMAGE, x, y, w, h)
        # The focus wash (see PROMO_IMAGE). Emitted at rest too, transparent:
        # Scene.cpp's morph pairs commands by type, so a gradient present in
        # one state and absent in the other would pop rather than fade.
        self.gradient(x, y, w, h, PANEL_NORMAL_STOPS if stops is None else stops, angle=-45.0)
        inset = PANEL_CAPTION_INSET * scale
        for i, line in enumerate(PANEL_CAPTION):
            self.text(line, x + inset, y + h - (PANEL_CAPTION_RISE - i * PANEL_CAPTION_PITCH) * scale,
                      w - 2.0 * inset, 24.0 * scale, 14.0 * scale, TILE_LABEL_COLOR,
                      HALIGN_LEFT, VALIGN_CENTER)
        return (x, y, w, h)

    def me_slot(self, origin, column_x, gamertag, focused=False):
        """The social page's profile block and Xbox Live link (see ME_*).

        One focus stop -- the link -- carrying the rest as furniture, so it
        all fades with its page the way an ITEM's commands do. Both states
        emit the same commands in the same order, for the morph to pair."""
        ox, oy = origin + column_x + ME_NUDGE[0], TILE_TOP + ME_NUDGE[1]

        # Upsell first, then the avatar slot over it: the layout's own order.
        lx, ly, lw, lh = ME_LOGO
        self.captured('LiveLogo.png', ox + lx, oy + ly, lw, lh)
        bx, by, bw, bh = ME_BUTTON
        bx, by = bx + ox, by + oy
        sx, sy = ME_BUTTON_FOCUS_SCALE if focused else (1.0, 1.0)
        cx, cy = bx + bw / 2.0, by + bh / 2.0
        place = lambda rx, ry, rw, rh: (cx + (rx - bw / 2.0) * sx, cy + (ry - bh / 2.0) * sy, rw * sx, rh * sy)
        plate = TILE_FOCUS_STOPS if focused else [(pos, (0,) + c[1:]) for pos, c in TILE_FOCUS_STOPS]
        self.gradient(*place(0.0, 0.0, bw, bh), plate, angle=-45.0)
        self.image('ico_40x_btnSmallArrow.png', *place(*ME_BUTTON_ICON), 255, TILE_ICON_TINT)
        tx, ty, tw, th = place(*ME_BUTTON_TEXT)
        self.text(ME_BUTTON_CAPTION, tx, ty, tw, th, TILE_LABEL_POINT_SIZE * sx, TILE_LABEL_COLOR,
                  HALIGN_LEFT, VALIGN_CENTER)

        sx0, sy0, srx, sry = ME_SHADOW
        self.emit('GRADIENT_RECT', ox + sx0 - srx, oy + sy0 - sry, 2.0 * srx, 2.0 * sry, 1, 1, 0.0,
                  len(ME_SHADOW_STOPS))
        for pos, (a, r, g, bb) in ME_SHADOW_STOPS:
            self.emit('  STOP', pos, a, r, g, bb)
        side = ME_AVATAR_SIDE
        self.image('AvatarSilhouette.png', ox + ME_AVATAR_CENTRE[0] - side * ME_AVATAR_ASPECT / 2.0,
                   oy + ME_AVATAR_CENTRE[1] - side / 2.0, side * ME_AVATAR_ASPECT, side)
        gx, gy, gw, gh = ME_GAMERTAG
        self.emit('IF', 'signedout', 0)     # as the gamertag top right: see gamertag()
        self.text(gamertag, ox + gx, oy + gy, gw, gh, ME_GAMERTAG_POINT_SIZE, TILE_LABEL_COLOR,
                  HALIGN_LEFT, VALIGN_TOP)
        self.lines.append('ENDIF')
        for i in range(9):
            self.captured('EmptyIcon.png', ox + ME_GRID[0] + (i % 3) * ME_GRID_PITCH,
                          oy + ME_GRID[1] + (i // 3) * ME_GRID_PITCH, ME_GRID_CELL, ME_GRID_CELL)

    def drop_shadow(self, focus_rect, neighbour_rects, block):
        """Emit a focused element's drop shadow, masked to its neighbours.

        `block` is the outer box of the page's tiles: the mask is grown to
        bridge the grout between adjacent tiles and then clamped to this, so
        seams fill in without the growth reaching past the block's edge.

        Call this AFTER the element itself, so its own icon stays first in
        this state's image list (see the SHADOW_ALPHA note); the shadow is
        masked out under the caster, so drawing after it costs nothing.
        """
        if Image is None:
            self.warnings.append('Pillow not available -- focused tiles get no drop shadow')
            return
        fx, fy, fw, fh = focus_rect
        bbox = grow(focus_rect, SHADOW_REACH)
        receivers = []
        for neighbour in neighbour_rects:
            caught = intersect(bbox, neighbour)
            if caught is None:
                continue  # too far from the focused tile to catch any of it
            receivers.append(intersect(grow(caught, SHADOW_GROUT), block))
        # A tile with no neighbours at all still casts onto the backdrop, so
        # this is not an early-out -- an empty receiver list just means the
        # whole mask stays at the backdrop's weaker level.
        receivers = [r for r in receivers if r]

        # Keyed on the receivers' offsets from the caster rather than their
        # absolute positions, so all eight tiles of a regular grid share one
        # PNG per distinct arrangement of neighbours around them.
        key = (round(fw, 1), round(fh, 1),
               tuple(sorted((round(r[0] - fx, 1), round(r[1] - fy, 1), round(r[2], 1), round(r[3], 1))
                            for r in receivers)))
        name = self.shadow_names.get(key)
        if name is None:
            name = f'shadow{len(self.shadow_names)}.png'
            self.shadow_names[key] = name
            self.baked_images[name] = bake_shadow(focus_rect, bbox, receivers)
        self.emit('IMAGE', bbox[0], bbox[1], bbox[2], bbox[3], name, 255, 255, 255, 255)

    def tile(self, rect, tile, stops, label_alpha, icon_alpha, angle=-90.0, scale=1.0):
        """One tile. `scale` is the tile's size relative to its resting size.

        Everything inside scales with it, because a focused tile is the whole
        control scaled up rather than a bigger box holding the same-sized
        furniture. The morph in Scene.cpp interpolates the label's point size
        and the icon's rect between the two states along with the tile's own
        geometry, so this grows continuously rather than snapping at the end.
        """
        x, y, w, h = rect
        self.gradient(x, y, w, h, stops, angle=angle)
        icon = tile.get('icon')
        if icon and tile.get('corner'):
            self.image(icon, x + w - CORNER_ICON_FROM_RIGHT * scale, y + CORNER_ICON_TOP * scale,
                       CORNER_ICON_SIZE * scale, CORNER_ICON_SIZE * scale, icon_alpha, TILE_ICON_TINT)
        elif icon:
            # Still bounded by half the tile's height, which is what keeps the
            # icon off the label on the short column tiles -- that bound is
            # already proportional to the tile, so only the cap needs scaling.
            size = min(TILE_ICON_MAX * scale, h * 0.5)
            self.image(icon, x + (w - size) / 2.0, y + h * TILE_ICON_CENTER_FRACTION - size / 2.0,
                       size, size, icon_alpha, TILE_ICON_TINT)
        inset = TILE_LABEL_INSET * scale
        self.text(tile['label'], x + inset, y + h - TILE_LABEL_BASELINE * scale,
                  w - 2 * inset, TILE_LABEL_BOX_H * scale,
                  TILE_LABEL_POINT_SIZE * scale, TILE_LABEL_COLOR,
                  HALIGN_LEFT, VALIGN_BOTTOM, label_alpha)


def tile_rects(page):
    """Absolute rects for a page's tiles, in declaration order."""
    if page.get('grid'):
        out = []
        for i in range(len(page['tiles'])):
            col, row = i % 4, i // 4
            out.append((GRID_X + col * GRID_PITCH_X, GRID_Y + row * GRID_PITCH_Y, GRID_W, GRID_H))
        return out
    height, pitch = tile_metrics(len(page['tiles']))
    return [(page['column_x'], TILE_TOP + i * pitch, TILE_W, height)
            for i in range(len(page['tiles']))]


def inflate(rect, scale):
    """Grow a rect about its own centre -- how a focused tile pops."""
    x, y, w, h = rect
    nw, nh = w * scale, h * scale
    return (x - (nw - w) / 2.0, y - (nh - h) / 2.0, nw, nh)


def has_side(page):
    """Whether something focusable sits right of the page's tile column: the
    promo panel, or on the social page the Xbox Live link. Either way it is
    the item named <page> + PANEL_ITEM_SUFFIX."""
    return bool(page.get('panel') or page.get('me'))


def row_of(page, index):
    """Which row an item sits in -- the grid page packs four per row."""
    return index // 4 if page.get('grid') else index


def entry_index(page, row, from_left):
    """Which item you land on when paging INTO `page` at `row`.

    Paging sideways keeps your row rather than dumping you at the top, which
    is what the console does and what makes holding left/right feel like
    sliding one surface instead of jumping between menus. Entering a grid page
    lands on its leftmost or rightmost column depending on which way you came;
    a row past the end of a shorter page clamps to its last row.
    """
    tiles = page['tiles']
    if page.get('grid'):
        rows = (len(tiles) + 3) // 4
        row = min(row, rows - 1)
        col = 0 if from_left else 3
        return min(row * 4 + col, len(tiles) - 1)
    return min(row, len(tiles) - 1)


def page_origins():
    """Absolute x of each page's origin along the surface."""
    out, x = [0.0], 0.0
    for gap in PAGE_GAPS:
        x += gap
        out.append(x)
    return out


def nav_for(page, index, pages, page_index):
    """The four nav links for one tile.

    Up/down walk the column (or the grid row above/below). Left/right move
    within a grid row, and at a row's edge they continue onto the neighbouring
    page -- which is an ordinary same-scene link now that every page lives on
    one surface. The camera follows because the target item carries a
    different viewX, so there is no paging code anywhere: sliding between
    pages and stepping between tiles are the same operation.
    """
    tiles = page['tiles']
    prev_page = pages[page_index - 1] if page_index > 0 else None
    next_page = pages[page_index + 1] if page_index + 1 < len(pages) else None
    row = row_of(page, index)

    def page_link(other, from_left):
        if other is None:
            return '-'
        # Arriving from the RIGHT means arriving at that page's right-hand
        # edge, and on a page with a promo panel that's the panel, not a
        # tile in the column past it.
        if not from_left and has_side(other):
            return other['name'] + PANEL_ITEM_SUFFIX
        return other['tiles'][entry_index(other, row, from_left)]['id']

    if page.get('grid'):
        col, row = index % 4, index // 4
        rows = (len(tiles) + 3) // 4
        up = tiles[index - 4]['id'] if row > 0 else '-'
        down = tiles[index + 4]['id'] if row + 1 < rows and index + 4 < len(tiles) else '-'
        left = tiles[index - 1]['id'] if col > 0 else page_link(prev_page, from_left=False)
        right = tiles[index + 1]['id'] if col < 3 and index + 1 < len(tiles) \
            else page_link(next_page, from_left=True)
        return up, down, left, right

    up = tiles[index - 1]['id'] if index > 0 else '-'
    down = tiles[index + 1]['id'] if index + 1 < len(tiles) else '-'
    # Right off the column lands on this page's own panel when it has one --
    # the panel sits between the column and the next page, so stepping
    # straight past it to the next page would skip the thing in between.
    right = (page['name'] + PANEL_ITEM_SUFFIX) if has_side(page) \
        else page_link(next_page, from_left=True)
    return up, down, page_link(prev_page, from_left=False), right


def page_surfaces(page, origin):
    """Everything on `page` a shadow can fall on, in absolute coordinates.

    The tiles AND the promo panel: they sit side by side, so a focused tile
    at the right of the column casts onto the panel, and a focused panel
    casts back onto the column. Returns (surfaces, block) -- the second being
    their outer box, which drop_shadow clamps its grown mask to.
    """
    surfaces = [(x + origin, y, w, h) for x, y, w, h in tile_rects(page)]
    if page.get('panel'):
        surfaces.append((PANEL_RECT[0] + origin, PANEL_RECT[1], PANEL_RECT[2], PANEL_RECT[3]))
    return surfaces, bounds(surfaces)


def panel_nav(page, pages, page_index):
    """The panel's own four links: back into its column, on to the next page.

    Right always lands on the next page's TILE COLUMN, never on that page's
    own panel. A panel sits at the right-hand edge of its page, so the first
    thing past it is the next page's leftmost element -- its column. Handing
    off panel-to-panel instead skips a whole column going forwards while
    coming back still walks it, which reads as the column being unreachable
    in one direction: My Games -> Xbox Live -> Xbox Live, with My Apps only
    findable by turning round.

    Up leaves the page for the selector, exactly as it does from the top
    tile of the column -- the panel is the full height of the grid, so there
    is nothing above it either, and it would otherwise be the one place on
    the hub the selector can't be reached from.
    """
    next_page = pages[page_index + 1] if page_index + 1 < len(pages) else None
    left = page['tiles'][0]['id']
    right = '-' if next_page is None \
        else next_page['tiles'][entry_index(next_page, 0, True)]['id']
    return TWIST_ITEM_PREFIX + page['name'], '-', left, right


def page_entry(page):
    """The tile focus lands on when arriving at `page` from the selector."""
    return page['tiles'][entry_index(page, 0, True)]['id']


def shoulder_links(pages, page_index):
    """(left, right) for LB/RB from anywhere on page `page_index`.

    A whole section sideways, landing on that section's entry tile. Both ends
    stop rather than wrapping, matching the d-pad -- the selector is the
    thing you use to get across quickly, and it wraps.
    """
    left = page_entry(pages[page_index - 1]) if page_index > 0 else '-'
    right = page_entry(pages[page_index + 1]) if page_index + 1 < len(pages) else '-'
    return left, right


def build_hub(pages, image_index, gamertag, font_path):
    """The whole hub as one scene: fixed chrome, then every page's content
    laid out along the surface, then the focusable tiles."""
    b = HubBuilder(image_index, font_path)
    b.twist_names = [p['name'] for p in pages]
    b.comment('the dashboard hub -- all five pages on one continuous surface.')
    b.comment('Tile geometry is measured from console/emulator captures, not read from a .xur:')
    b.comment('the hub is assembled at runtime by HubScene.lub. See hub_build.py.')
    b.emit('CANVAS', *CANVAS)
    # Focus starts on the first page's first tile. Stated outright because
    # the alternative -- "whatever ITEM is declared first" -- made the start
    # position depend on emission order, and the promo panels are emitted
    # before the tiles so that an unfocused panel draws beneath them.
    b.emit('DEFAULTFOCUS', pages[0]['tiles'][0]['id'])
    b.emit('FADE', PAGE_FADE_ALPHA, PAGE_FADE_SPAN)
    b.emit('PRESS', _fmt(PRESS_IN_SECONDS), _fmt(PRESS_OUT_SECONDS))
    b.emit('INTRO', _fmt(INTRO_SECONDS), _fmt(INTRO_RISE_Y), _fmt(INTRO_HOLD_SECONDS), INTRO_EASE)
    origins = page_origins()

    # --- chrome that stays put while the surface slides underneath it.
    b.lines.append('LAYER fixed')
    b.backdrop()
    b.gamertag(gamertag)
    # Every page's name, all dim. The focused tile re-draws its own page's
    # name at full brightness on top, which is how the selector highlights
    # the current page without the renderer needing to know what a page is.
    # The page selector is NOT drawn here any more -- each focused item
    # draws the whole row itself, so the selected entry's face can differ
    # from the others' without a dim copy showing through underneath.
    # See HubBuilder.twist.
    # "A Select" is on every page; page-specific extras are drawn by the
    # focused tile, since the legend describes whatever has focus.
    legend_end = b.legend([('A', 'Select')])

    # --- the promo panel. Focusable, like the tiles beside it: A on it
    # opens the network settings, and because it's an ITEM its commands
    # take the item's own viewX as their fade anchor, so it dims and
    # brightens with the page it belongs to without needing a FADEGROUP.
    b.lines.append('LAYER scroll')
    for page_index, (page, origin) in enumerate(zip(pages, origins)):
        if not has_side(page):
            continue
        up, down, left, right = panel_nav(page, pages, page_index)
        # The social page's "Join the fun" asks for Xbox Live as its tiles do
        # (UpsellSlotScene.lub), so it goes where Friends goes.
        b.emit('ITEM', page['name'] + PANEL_ITEM_SUFFIX, up, down, left, right,
               ACTIONS['friends'] if page.get('me') else PANEL_ACTION, origin)
        if page.get('me'):
            b.emit('ACTIONIF', 'signedout', 1, SIGNED_OUT['friends'])
        b.emit('SHOULDER', *shoulder_links(pages, page_index))
        b.lines.append('STATE normal')
        b.lines.append('LAYER scroll')
        if page.get('me'):
            # The social page's side: the Xbox Live link is the focus stop
            # (the same "connect" the promo panels are), the profile block
            # beside it is drawn with it. Nothing lifts, so no shadow.
            b.me_slot(origin, page['column_x'], gamertag)
            b.lines.append('STATE focus')
            b.lines.append('LAYER scroll')
            b.me_slot(origin, page['column_x'], gamertag, focused=True)
        else:
            b.content_panel(origin)
            b.lines.append('STATE focus')
            b.lines.append('LAYER scroll')
            focus_rect = b.content_panel(origin, PANEL_FOCUS_STOPS, scale=PANEL_FOCUS_SCALE)
            # Same shadow the tiles cast, from the same code -- it falls on the
            # tile column beside it at full strength and on the backdrop around
            # it at the weaker backdrop level, exactly as a tile's does.
            surfaces, block = page_surfaces(page, origin)
            b.drop_shadow(focus_rect, [r for r in surfaces if r[0] < PANEL_RECT[0] + origin], block)
        b.lines.append('LAYER fixed')
        b.twist(page['name'])
        b.lines.append('ENDITEM')

    # --- focusable tiles.
    for page_index, (page, origin) in enumerate(zip(pages, origins)):
        rects = tile_rects(page)
        for i, (tile, rect) in enumerate(zip(page['tiles'], rects)):
            up, down, left, right = nav_for(page, i, pages, page_index)
            action = ACTIONS.get(tile['id'], '-')
            x, y, w, h = rect
            rect = (x + origin, y, w, h)
            if up == '-':
                # Nothing above this tile on its own page, so up leaves the
                # grid entirely and lands on the selector -- which is how you
                # reach it, there being no button that jumps there.
                up = TWIST_ITEM_PREFIX + page['name']
            b.emit('ITEM', tile['id'], up, down, left, right, action, origin)
            if tile['id'] in SIGNED_OUT:
                b.emit('ACTIONIF', 'signedout', 1, SIGNED_OUT[tile['id']])
            b.emit('SHOULDER', *shoulder_links(pages, page_index))

            enabled = tile.get('enabled', True)
            if not enabled:
                # Only the SOUND reads this -- the greyed-out look is baked
                # into the state blocks below. See ManifestItem::disabled.
                b.lines.append('DISABLED')
            label_alpha = TILE_LABEL_COLOR['a'] if enabled else TILE_LABEL_DISABLED_ALPHA
            icon_alpha = 255 if enabled else TILE_LABEL_DISABLED_ALPHA

            b.lines.append('STATE normal')
            b.lines.append('LAYER scroll')
            b.tile(rect, tile, TILE_NORMAL_STOPS, label_alpha, icon_alpha)

            b.lines.append('STATE focus')
            b.lines.append('LAYER scroll')
            # This block draws after every unfocused item (see Scene::Draw),
            # so the enlarged tile lands on top of its neighbours. It fades
            # with its own page automatically -- the item's viewX is the
            # default fade anchor for its commands -- so while it is sliding
            # in from off-screen it dims along with everything else on that
            # page instead of floating bright over the current one.
            #
            # angle=-45 rotates the fill so its bright stop sits in the
            # tile's top-left corner rather than running straight down from
            # the top edge -- matches the console's shine, which is diagonal,
            # not vertical.
            if not enabled:
                # A disabled tile doesn't pop out on the console -- it stays
                # exactly where it is, keeps its dimmed icon and label, and
                # the only thing that marks it as selected is a smaller,
                # weaker version of the shine. Nothing here changes size or
                # brightness, so the morph into this state is purely the
                # gradient crossfading from the resting fill to this one.
                # No shadow either: nothing has lifted off the page.
                b.tile(rect, tile, TILE_DISABLED_FOCUS_STOPS, label_alpha, icon_alpha, angle=-45.0)
                b.lines.append('LAYER fixed')
                b.twist(page['name'])
                extras = [item for item in page['legend'] if item[0].upper() != 'A']
                if extras:
                    b.legend(extras, start_x=legend_end)
                b.lines.append('ENDITEM')
                continue

            focus_rect = inflate(rect, FOCUS_SCALE)
            b.tile(focus_rect, tile, TILE_FOCUS_STOPS, 255, 255, angle=-45.0, scale=FOCUS_SCALE)
            # The shadow is cast onto the OTHER tiles on this page and
            # nothing else, so it is clipped to them -- a tile at the edge of
            # its page has no neighbour on that side and casts nothing there,
            # rather than staining the backdrop. Neighbours from other pages
            # are not considered: pages sit ~950 units apart, far outside any
            # shadow's reach.
            surfaces, block = page_surfaces(page, origin)
            b.drop_shadow(focus_rect, [r for j, r in enumerate(surfaces) if j != i], block)
            # Chrome that depends on which tile has focus: this page's name in
            # the selector, and any legend buttons beyond "A Select". Fixed
            # layer, so it is never dimmed and never dims anything.
            b.lines.append('LAYER fixed')
            b.twist(page['name'])
            extras = [item for item in page['legend'] if item[0].upper() != 'A']
            if extras:
                b.legend(extras, start_x=legend_end)
            b.lines.append('ENDITEM')

    # --- the page selector, as focusable items in its own right.
    # One per page, sharing that page's viewX so selecting one slides the
    # surface to it. Left/right WRAP here even though the tiles don't: this
    # is the row you use to get across the hub quickly, and running off the
    # end of it into nothing would defeat that.
    for page_index, (page, origin) in enumerate(zip(pages, origins)):
        name = page['name']
        prev_name = pages[page_index - 1]['name']
        next_name = pages[(page_index + 1) % len(pages)]['name']
        b.emit('ITEM', TWIST_ITEM_PREFIX + name, '-', page_entry(page),
               TWIST_ITEM_PREFIX + prev_name, TWIST_ITEM_PREFIX + next_name, '-', origin)
        # The shoulders do the same thing from up here as they do below.
        b.emit('SHOULDER', TWIST_ITEM_PREFIX + prev_name, TWIST_ITEM_PREFIX + next_name)
        # You pass through the selector rather than landing on it: coming
        # back down returns you to whatever you flicked up from, and the
        # `down` link above is only the fallback for when you moved along the
        # row first and the origin no longer applies. See ManifestItem::
        # returnPoint / Navigator::m_returnItem.
        b.lines.append('RETURN')
        # Resting, the selector is just the row every tile already draws --
        # so an unfocused twist item contributes nothing, and the row on
        # screen is the focused TILE's copy of it.
        b.lines.append('STATE normal')
        b.lines.append('LAYER fixed')
        b.lines.append('STATE focus')
        b.lines.append('LAYER fixed')
        b.twist(name, glow=True)
        b.lines.append('ENDITEM')

    return b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--search-dir', action='append', default=[], required=True,
                    help='directory to recursively index PNGs from, by basename (repeatable)')
    ap.add_argument('--out-dir', default='../assets/scenes',
                    help='where to write each <scene>/manifest.txt + assets/')
    ap.add_argument('--font', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   '..', 'assets', 'fonts', 'xenonclatin_full.ttf'),
                    help='TTF used to measure the legend flow layout (must match the app\'s font)')
    ap.add_argument('--gamertag', default='Trinculo54',
                    help='gamertag shown top-right (runtime data, not in any layout file)')
    args = ap.parse_args()

    index = build_image_index(args.search_dir)
    # ...which only looks for PNGs, and the promo panel is the rip's one JPEG.
    for d in args.search_dir:
        for dirpath, _dirnames, filenames in os.walk(d):
            for fn in filenames:
                if fn.lower().endswith('.jpg'):
                    index.setdefault(fn, os.path.join(dirpath, fn))
    b = build_hub(PAGES, index, args.gamertag, args.font)
    scene_dir = os.path.join(args.out_dir, SCENE_NAME)
    assets_dir = os.path.join(scene_dir, 'assets')
    os.makedirs(assets_dir, exist_ok=True)
    with open(os.path.join(scene_dir, 'manifest.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(b.lines) + '\n')
    for src, rel in b.used_images.items():
        shutil.copyfile(src, os.path.join(assets_dir, rel))
    for name, image in b.baked_images.items():
        image.save(os.path.join(assets_dir, name))
    tiles = sum(len(p['tiles']) for p in PAGES)
    print(f'{SCENE_NAME}: {len(b.lines)} lines, {len(b.used_images)} images '
          f'+ {len(b.baked_images)} baked, {tiles} items, '
          f'{len(PAGES)} pages spanning {page_origins()[-1] + CANVAS[0]:.0f} canvas units')
    for w in b.warnings:
        print(f'   warning: {w}')


if __name__ == '__main__':
    main()
