"""
"Cook" a parsed .xur layout into a flat, absolute-pixel-coordinate render
manifest -- a simple line-oriented text format with no parsing dependency
beyond split()/float(), so the native C++ app doesn't need a JSON library.

This intentionally does the hard, stateful part (walking the scene graph,
resolving each object's full property set against schema defaults, applying
Visual templates, evaluating their state timelines, and accumulating
parent->child position offsets) in Python, where we already have
xur_extract/xur_schema working and tested. The native app's job then shrinks
to: read flat draw commands, load a handful of textures/fonts, draw quads.

WHAT XUI SEMANTICS THIS IMPLEMENTS (each was a visible bug when missing):

  - Visual templates (`Visual=BackgroundPanel`), including the content
    binding that pushes an instance's own Text/ImagePath into the template's
    presenter slots. See load_visual_templates.
  - Visual template STATE TIMELINES (`Visual=btn_single`) -- a button's real
    look is a keyframe animation with named states (Normal/Focus/Press/...),
    so a static cook has to pick a state and evaluate the timeline at that
    state's frame. See evaluate_state and resolve_focus_path.
  - `Anchor` -- the XUI docking bitmask that maps a template child's box into
    the referencing instance's (differently sized) box. See anchor_rect.
  - `TextStyle` -- the horizontal/vertical text alignment bits. Emitted into
    the manifest (rather than resolved here) because alignment needs the real
    font metrics, which only the renderer has. See text_alignment.
  - `ColorFactor` -- the per-element color modulation XUI multiplies into
    fills and text.
  - Gradient `Rotation` -- XUI's default gradient axis is LEFT-TO-RIGHT;
    almost every vertical-looking gradient in these assets gets there via
    Rotation=-90. Emitted as an angle. See cook_figure.
  - Texture fills (`FillType=4` + `TextureFileName`) -- how the A/B button
    glyphs in the legend are actually drawn.
  - Circular `XuiFigure`s -- detected from their bezier anchor layout and
    flagged for the renderer's inscribed-ellipse mask. See _figure_is_ellipse.

Simplifications still made here, deliberately, and each a real gap vs. the
original renderer rather than an oversight:

  - Only Position (plus Anchor-driven layout) accumulates through the parent
    chain. Scale, Rotation and Pivot are read but not applied -- fine for the
    scenes cooked so far, but WILL misplace content in a screen that uses
    them.
  - XuiFigure bezier outlines are approximated as their bounding box, except
    for the full-ellipse case (_figure_is_ellipse). Rounded-rect corners
    still square off.
  - Only the first fill/gradient chain is used; `Stroke` (outlines) isn't
    emitted.
  - Images whose ImagePath is a runtime `{token}` placeholder (e.g.
    `{gamerpic}`) are skipped with a comment line. Plain names and
    `common://` / `sharedres://` references are resolved against the
    caller-supplied search path list (the extracted archive folders).
"""
import sys
import os
import argparse

import xur_extract
import xur_schema


# ---------------------------------------------------------------------------
# XUI enum decoding
# ---------------------------------------------------------------------------

# XuiElement.Anchor -- a docking bitmask describing which edges of the PARENT
# box a child's own box is pinned to. Decoded from usage across skin.xur,
# where it is self-consistent and matches the template names:
#   Anchor=15 (L|T|R|B) on every "fill the whole control" presenter,
#   Anchor=5  (L|R)     on the full-width 1px hairlines at a button's top,
#   Anchor=48 (HC|VC)   on DashBkgnd's WaitCursorControl, whose position+size
#                       put it at the exact center of the 1280x720 canvas.
ANCHOR_LEFT = 1
ANCHOR_TOP = 2
ANCHOR_RIGHT = 4
ANCHOR_BOTTOM = 8
ANCHOR_HCENTER = 16
ANCHOR_VCENTER = 32

# XuiText/XuiTextPresenter.TextStyle -- alignment lives in bits 8..12. Decoded
# from skin.xur's self-documenting template names, which pin every value:
#   Label_TabTitle       = 0x0000   (no bits -> left/top, the default)
#   Label_OOBE_Header    = 0x0100   -> LEFT set explicitly
#   XuiLabelRightJustify = 0x0210   -> 0x0200 = RIGHT
#   Label_TabTitle_Right = 0x0200
#   Label_TabTitle_Centered / Label_OSUICentered = 0x0400 -> HCENTER
#   Label_BodyCentered   = 0x4400,  Label_BodyCentered_VH = 0x5400
#                          -> the "_VH" (vertical+horizontal) variant adds
#                             exactly 0x1000, so 0x1000 = VCENTER.
# Cross-checked geometrically: Label_White_Centered (0x5410 -> HCENTER|VCENTER)
# is used by NetworkMain's three connect-bar captions, and centering each one
# in its own authored box lands it within ~1px of its icon's center
# (Network: text center 432.3 vs icon center 431; Internet: 662.9 vs 662) --
# left alignment would put them ~60px off.
TEXTSTYLE_HALIGN_MASK = 0x0700
TEXTSTYLE_LEFT = 0x0100
TEXTSTYLE_RIGHT = 0x0200
TEXTSTYLE_HCENTER = 0x0400
TEXTSTYLE_VCENTER = 0x1000
TEXTSTYLE_DROPSHADOW = 0x0001   # see Cooker.cook_text for how this was pinned
TEXTSTYLE_VBOTTOM = 0x2000  # not observed in these assets; handled for completeness

HALIGN_LEFT, HALIGN_CENTER, HALIGN_RIGHT = 0, 1, 2
VALIGN_TOP, VALIGN_CENTER, VALIGN_BOTTOM = 0, 1, 2

# XuiFigureFill.FillType selects WHICH of the (usually all-present)
# FillColor / Gradient / TextureFileName fields the runtime actually paints
# with. Getting this wrong is invisible in the file and glaring on screen,
# because most figures carry a FillColor *and* a Gradient that look nothing
# alike. Decoded by matching a console capture of this exact screen against
# the two possible readings of each figure:
#
#   0 (absent)  solid FillColor. `Full1..5`, the connection dots, are this:
#               FillColor (0,138,0) plus a ring-shaped radial gradient. The
#               console draws solid green discs, so FillColor wins. Same for
#               `GreenHighlight`, whose FillColor (0,138,0) x ColorFactor
#               235/255 = (0,127,0) matches the captured selection bar's
#               measured (16,125,16), where its gradient would be a much
#               yellower (81,134,15)->(119,186,2).
#   2           linear gradient (the background panel, the connect-bar strip)
#   3           radial gradient (`HalfRing1..10`, the ring-shaped dot tracks)
#   4           texture: paint with TextureFileName. How the legend's A/B
#               button glyphs exist at all.
FILLTYPE_SOLID = 0
FILLTYPE_LINEAR_GRADIENT = 2
FILLTYPE_RADIAL_GRADIENT = 3
FILLTYPE_TEXTURE = 4

# XUI authors text sizes in POINTS while every other number in a layout is in
# canvas pixels, so a PointSize has to be scaled by the usual 96/72 before it
# means anything to a rasterizer. Measured against a console capture of this
# screen: the menu captions (`btn_single`'s txt_line3, PointSize 18) have a
# cap height of 15.5 canvas units, and the title (`Label_Head`, PointSize 22)
# an ink height of 28.3 -- both matching a 4/3 scale to within a couple of
# percent, and both about a third larger than PointSize taken as pixels.
POINT_SIZE_TO_PIXELS = 4.0 / 3.0

# ---------------------------------------------------------------------------
# Dashboard shell chrome
# ---------------------------------------------------------------------------
#
# None of the following lives in a screen's own .xur -- it belongs to the
# shell, which composites the screen scene into a 1280x720 frame. It is here
# because a screen rendered without it doesn't look like the dashboard, and
# every number is measured off a console capture rather than chosen.

# The dash backdrop: a purely vertical grey ramp (sampled horizontally, it is
# flat to within 2/255 at every height), dark at the top, brightest around 72%
# down, easing back off toward the bottom.
#
# Fitted to every row of two captures at once -- the emulator's
# (REFERENCES/Screenshot 2026-08-20 021632.png) and real hardware's
# (REFERENCES/REAL_HW/DIRECT_CONSOLE.png), down a clean strip at the left
# edge. The two agree with each other to 1.6 levels at every row, and these
# eight stops (the most a GRADIENT_RECT takes) follow them to 1.7.
#
# There were five here before, right at both ends but up to 20 levels dark
# through the middle of the screen (154 where the console is 172 at y=420):
# the ramp bulges, and two stops across the bulge cut the corner off it.
#
# What it is on the console: AuraScene.xur's aura_background.jpg under a
# floor texture and two vignettes. All of those are in the rip, but they are
# composited in a perspective scene, and the result is what is measured here.
BACKDROP_STOPS = [
    (0.0000, (255, 73, 73, 75)),
    (0.0833, (255, 81, 81, 83)),
    (0.2778, (255, 104, 104, 107)),
    (0.5556, (255, 166, 166, 168)),
    (0.6111, (255, 177, 177, 178)),
    (0.7222, (255, 185, 185, 186)),
    (0.7778, (255, 179, 179, 179)),
    (1.0000, (255, 142, 141, 143)),
]

# The A/B/X/Y legend row. Its geometry comes from `LegendScene.xur`'s
# `ControlPackLegendScene1`: origin x=96, a 32x32 button glyph, and the
# caption 30 units to its right. That scene is a control pack -- i.e. its
# children are laid out by dashboard code, not by the file -- so the row's y
# and each item's x are measured instead: the captured A glyph sits at
# x=90.5, y=644.7 and the B glyph at x=190.9, both 32x32 including the PNG's
# transparent margin.
#
# NOTE this is NOT the legend a settings screen carries itself. NetworkMain
# has its own `legend_a`/`legend_b` using skin.xur's `legend_A`/`legend_B`
# visuals, which put the glyph on the RIGHT of a right-aligned caption -- the
# opposite of what the console actually displays. Those instances are parked
# off-canvas in the file and the shell's row is what gets drawn, so cook them
# with --hide-id and use --legend instead.
LEGEND_Y = 645.0
LEGEND_GLYPH_SIZE = 32.0
# LegendScene nests the glyph two groups deep: Images at (-10,-4) then
# Button_Image at (+4,+4), so the 32x32 PNG lands 6 units LEFT of the item's
# own origin while the caption sits 30 to its right. Confirmed by the capture:
# with items at x=96 and x=196.4 the discs land at 90.5 and 190.9.
LEGEND_GLYPH_OFFSET = -6.0
LEGEND_LABEL_OFFSET = 30.0
LEGEND_LABEL_POINT_SIZE = 14.0
LEGEND_LABEL_COLOR = {'a': 255, 'r': 255, 'g': 255, 'b': 255}
LEGEND_GLYPH_POINT_SIZE = 14.0
LEGEND_GLYPH_LETTER_COLOR = {'a': 255, 'r': 245, 'g': 245, 'b': 245}
LEGEND_GLYPH_IMAGE = {
    'A': 'A-Button_32.png',
    'B': 'B-Button_32.png',
    'X': 'X-Button_32.png',
    'Y': 'Y-Button_32.png',
}


def text_alignment(text_style):
    """(halign, valign) from a TextStyle bitfield -- see the constants above."""
    h = text_style & TEXTSTYLE_HALIGN_MASK
    if h == TEXTSTYLE_HCENTER:
        halign = HALIGN_CENTER
    elif h == TEXTSTYLE_RIGHT:
        halign = HALIGN_RIGHT
    else:
        halign = HALIGN_LEFT  # 0x0000 (unset) and 0x0100 (explicit LEFT) alike
    if text_style & TEXTSTYLE_VCENTER:
        valign = VALIGN_CENTER
    elif text_style & TEXTSTYLE_VBOTTOM:
        valign = VALIGN_BOTTOM
    else:
        valign = VALIGN_TOP
    return halign, valign


def anchor_rect(child_pos, child_w, child_h, template_w, template_h, instance_w, instance_h, anchor):
    """Maps a Visual template child's box into the referencing INSTANCE's box.

    A template is authored at one size (btn_single is 450x47) and reused by
    instances of another (NetworkMain's nav buttons are 440x47). `Anchor` says
    how to reconcile that: an edge in the mask keeps that edge's inset fixed,
    both opposite edges in the mask stretch the child, and a center bit
    recenters it. Without this every templated child renders at its authored
    template size -- which is why a button's underline hairline used to run
    10px past the button, and why a "fill the control" highlight was the wrong
    width.

    Returns (x, y, w, h) relative to the instance's own origin.
    """
    x, y = child_pos[0], child_pos[1]
    w, h = child_w, child_h

    dw = instance_w - template_w
    dh = instance_h - template_h

    if anchor & ANCHOR_HCENTER:
        x += dw / 2.0
    elif (anchor & ANCHOR_LEFT) and (anchor & ANCHOR_RIGHT):
        w += dw          # both edges pinned -> the child stretches with the box
    elif anchor & ANCHOR_RIGHT:
        x += dw          # only the right edge pinned -> the child slides right
    # ANCHOR_LEFT alone (and no horizontal bits at all) leaves x/w as authored.

    if anchor & ANCHOR_VCENTER:
        y += dh / 2.0
    elif (anchor & ANCHOR_TOP) and (anchor & ANCHOR_BOTTOM):
        h += dh
    elif anchor & ANCHOR_BOTTOM:
        y += dh

    return x, y, w, h


# ---------------------------------------------------------------------------
# Timeline / named-state evaluation
# ---------------------------------------------------------------------------

def named_frames(node):
    """{state name -> keyframe number} for a template/control's named frames."""
    return {nf['name']: nf['keyframe'] for nf in (node.get('named_frames') or [])}


def evaluate_state(node, state_name):
    """Evaluates `node`'s timelines at the keyframe its named frame
    `state_name` sits on, returning {child Id: {property: value}}.

    This is what makes interactive controls renderable at all. A button's
    Visual (btn_single, XuiButton, legend_A, ...) carries no useful static
    appearance: the *whole* look is a Flash-style keyframe timeline, and the
    file's named frames are the state table into it --

        KillFocus 0, EndKillFocus 4, Normal 5, Focus 7, Press 387,
        InitFocus 409, ...

    -- with the runtime doing GoToAndStop(<state's frame>) as gamepad focus
    moves. So "what does a normal button look like" is answerable statically,
    it just requires evaluating the timeline rather than reading properties.

    Value at a frame = the last keyframe at or before it (a hold, not a
    tween). That is exactly right at a named STATE frame, which is always
    either a keyframe itself or a Stop marker inside a settled state -- e.g.
    btn_single's txt_line3 has keyframes at 0/4/7 and "Normal" is frame 5, so
    Normal's text color is frame 4's value. The interpolation modes on the
    keyframes only matter for the transitions between states, which a static
    cook never renders.

    Sanity check that this picks the right values rather than a plausible-
    looking one: btn_single's InitFocus (409, the "gain focus from cold"
    transition) STARTS its txt_line3 at (51,58,64) and ends at the Focus
    white -- independently confirming (51,58,64), the value evaluating at
    "Normal" gives, is the real unfocused text color, and not the (101,109,114)
    sitting on the element as its static property.
    """
    frames = named_frames(node)
    if state_name not in frames:
        return {}
    frame = frames[state_name]

    out = {}
    for tl in node.get('timelines') or []:
        best = None
        for kf in tl['keyframes']:
            if kf['frame'] <= frame and (best is None or kf['frame'] > best['frame']):
                best = kf
        if best is None:
            continue
        props = {}
        for p in best['properties']:
            props[p['property']] = p['values'] if p.get('indexed') else p.get('value')
        out[tl['target']] = props
    return out


def pick_state(node, focused):
    """Which named state a control's Visual template should be frozen at.

    Only 'Focus' vs 'Normal' are chosen automatically: those are the two a
    settled, non-animating screen can actually be in, and which one applies is
    decided by the screen's own DefaultFocus chain (see resolve_focus_path),
    not guessed. Press/Disabled/etc. are transient or data-driven and are left
    to the caller's --state override.
    """
    frames = named_frames(node)
    for candidate in (('Focus', 'Normal') if focused else ('Normal',)):
        if candidate in frames:
            return candidate
    return None


def resolve_focus_path(root, hide_ids=()):
    """The set of object Ids that are focused when a screen first appears.

    XUI stores this as `DefaultFocus` on a container, holding a
    backslash-separated path to a descendant (NetworkMain's root scene has
    DefaultFocus='Menu\\navConfigureSettings'). Following the chain from the
    top gives the one control that should render in its Focus state -- i.e.
    the green highlight bar in the left-hand menu. Guessing this, rather than
    reading it, would put the highlight on the wrong row.
    """
    focused = set()

    def child_by_id(node, wanted):
        for c in node.get('children') or []:
            p = xur_schema.resolve_properties(c['class'], c['properties']) or {}
            if p.get('Id') == wanted:
                return c, p
        return None, None

    def follow(node, props):
        path = props.get('DefaultFocus')
        if not path:
            return
        cur, cur_props = node, props
        for segment in path.split('\\'):
            if not segment:
                continue
            cur, cur_props = child_by_id(cur, segment)
            if cur is None:
                return  # a dangling DefaultFocus reference -- nothing to mark
        if cur_props.get('Id') in hide_ids:
            return
        focused.add(cur_props.get('Id'))
        follow(cur, cur_props)  # containers chain focus down into sub-containers

    def walk(node):
        props = xur_schema.resolve_properties(node['class'], node['properties']) or {}
        if props.get('DefaultFocus'):
            follow(node, props)
        for c in node.get('children') or []:
            walk(c)

    walk(root)
    return focused


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def build_image_index(search_dirs):
    """Maps bare filename -> first matching path found under any of
    search_dirs (recursive). This is deliberately basename-keyed rather
    than path-keyed: XUI image paths come in three flavours --
    plain "Foo.png" (relative to the .xur's own archive), "common://Foo.png"
    and "sharedres://Foo.png" (shared resource-pack references) -- and in
    every case observed the actual asset just lives somewhere in the full
    set of extracted archives / shrdres.xzp under that same basename, not
    at a path reproducing the scheme+prefix. Confirmed by cross-checking
    every common:// / sharedres:// reference in skin.xur (the densest
    screen in the set) against this index: 100% resolved."""
    index = {}
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for dirpath, _dirnames, filenames in os.walk(d):
            for fn in filenames:
                if fn.lower().endswith('.png') and fn not in index:
                    index[fn] = os.path.join(dirpath, fn)
    return index


def resolve_image_path(image_path, image_index):
    if not image_path:
        return None
    if '{' in image_path and '}' in image_path:
        return None  # runtime-substituted token (e.g. "{gamerpic}") -- not resolvable statically
    basename = image_path.split('/')[-1]
    # (the packs are not consistent about case: sharedres://ico_32x_camera.png is ico_32x_camera.PNG)
    return image_index.get(basename) or next(
        (path for name, path in image_index.items() if name.lower() == basename.lower()), None)


def load_visual_templates(xur_paths):
    """Parses one or more skin/template `.xur` files and returns
    {template_id: <XuiVisual subtree>} for every top-level `XuiVisual`-classed
    object found in any of them.

    Why this exists: a screen's own `.xur` frequently doesn't carry its own
    visible appearance directly. Instead an object sets a `Visual` property
    naming a reusable template (e.g. "BackgroundPanel", "Label_TestStatus",
    "btn_single") defined once in a shared skin file, and the runtime
    instantiates that template's own child figures/text/image presenters,
    binding the referencing instance's own `Text`/`ImagePath` into the
    template's presenter slot(s). Skipping this entirely means every templated
    object -- most of them, including this screen's background panel, its
    whole menu, and every status label's color/size/alignment -- renders with
    none of its real appearance.

    Unlike the earlier version of this function, templates carrying keyframe
    timelines are NOT excluded: they're the interactive controls, and their
    timelines are the state table that says what they look like. See
    evaluate_state.
    """
    templates = {}
    for path in xur_paths:
        if not os.path.isfile(path):
            continue
        data = open(path, 'rb').read()
        xur = xur_extract.parse_xur8(data)

        def walk_for_templates(node):
            if node.get('class') == 'XuiVisual':
                tid = None
                for p in node.get('properties', []):
                    if p.get('property') == 'Id':
                        tid = p.get('value')
                        break
                if tid and tid not in templates:  # first (largest/most-complete) file wins on name clashes
                    templates[tid] = node
            for child in node.get('children', []) or []:
                walk_for_templates(child)

        walk_for_templates(xur.root_object)
    return templates


def find_skin_files(search_dirs, basename='skin.xur'):
    """All files named `basename` under search_dirs, largest first -- there
    can be more than one (e.g. a small per-sub-app override alongside the
    main dashboard skin), and the larger one is, empirically, the more
    complete template set, so it should supply a name before a smaller
    file's same-named-but-probably-partial definition does.

    Largest first WITHIN a search dir; an earlier search dir comes before a
    later one whatever the sizes, as it does for images. The guide's skin
    (huduiskin.xex, unpacked under XZP) is another skin.xur, bigger than the
    dashboard's and with many of the same names: found by size alone it took
    over btn_single, Label_Head and the rest for every dashboard screen
    cooked with XZP among its search dirs."""
    found = []
    for order, d in enumerate(search_dirs):
        if not os.path.isdir(d):
            continue
        for dirpath, _dirnames, filenames in os.walk(d):
            for fn in filenames:
                if fn == basename:
                    p = os.path.join(dirpath, fn)
                    found.append((order, -os.path.getsize(p), p))
    found.sort()
    return [p for _order, _size, p in found]


# ---------------------------------------------------------------------------
# Figure geometry
# ---------------------------------------------------------------------------

def _figure_is_ellipse(points):
    """True when a XuiFigure's bezier outline is the full ellipse inscribed in
    its own bounding box.

    Every circular figure in these assets (the connection-test dots) is
    authored the same way: four anchor points at the bounding box's EDGE
    MIDPOINTS, with control points bulging outward -- versus a rect or
    rounded-rect, whose anchors sit at or near the box's CORNERS. Testing the
    anchors alone separates the two cleanly and needs no bezier evaluation.

    Deliberately narrow: it does NOT catch the ring-track backgrounds
    (HalfRing1..10), which are literal rectangles with a radial-gradient fill
    faking a ring, nor rounded-rect corners. Those need a real bezier
    rasterizer; this is a scoped test for the shape that is actually a circle.
    """
    if not points:
        return False
    box = points.get('bounding_box')
    pts = points.get('points')
    if not box or not pts or len(pts) != 4:
        return False
    w, h = float(box[0]), float(box[1])
    if w <= 0 or h <= 0:
        return False

    # A rect's bezier handles are degenerate (control1 == control2 == the
    # anchor's own corner); a real curve's are not. Testing this FIRST matters:
    # the anchor test below alone false-positives on very thin boxes, where a
    # corner and the adjacent edge's midpoint are within any usable tolerance
    # of each other -- which silently turned every button's 1px underline
    # hairline (a 319x1 rect) into a 1px-tall "ellipse", i.e. an antialiased
    # smear instead of a crisp line.
    curved = False
    for p in pts:
        px, py = float(p['point'][0]), float(p['point'][1])
        for handle in ('control1', 'control2'):
            c = p.get(handle)
            if c and (abs(float(c[0]) - px) > 0.01 or abs(float(c[1]) - py) > 0.01):
                curved = True
                break
        if curved:
            break
    if not curved:
        return False

    # ...and the anchors sit one per edge midpoint (as opposed to a
    # rounded-rect, whose anchors cluster near the corners).
    expected = [(w / 2.0, 0.0), (w, h / 2.0), (w / 2.0, h), (0.0, h / 2.0)]
    tol = max(0.5, min(w, h) * 0.05)
    unmatched = list(expected)
    for p in pts:
        px, py = float(p['point'][0]), float(p['point'][1])
        hit = next((e for e in unmatched if abs(px - e[0]) <= tol and abs(py - e[1]) <= tol), None)
        if hit is None:
            return False
        unmatched.remove(hit)
    return not unmatched


def _mul_color(color, factor):
    """XUI's ColorFactor modulation: the element's color multiplied by its
    ColorFactor. Default ColorFactor is 0xffffffff, so this is a no-op almost
    everywhere -- but a button's hairlines set it to 235/255, and skipping it
    renders them noticeably brighter than the console does."""
    if not factor:
        return color
    return {k: max(0, min(255, round(color[k] * factor[k] / 255.0))) for k in ('a', 'r', 'g', 'b')}


def _fmt(x):
    if isinstance(x, float):
        return f'{x:.3f}'
    return str(x)


def _apply_opacity(alpha_byte, opacity):
    return max(0, min(255, round(alpha_byte * opacity)))


class Cooker:
    def __init__(self, image_index, hide_ids=None, visual_templates=None, place=None,
                 state_overrides=None, show_ids=None, scene_pos=None, backdrop=False,
                 legend=None, legend_y=None):
        self.image_index = image_index
        self.lines = []
        self.warnings = []
        self.used_images = {}  # resolved_path -> relative name written into manifest
        self.visual_templates = visual_templates or {}
        self.focused_ids = set()
        # Manual per-Id suppression, for cases neither Show=False nor the
        # same-position dedup can catch -- see --hide-id's help text.
        self.hide_ids = set(hide_ids or ())
        # Manual per-Id repositioning, for chrome the runtime places -- see
        # --place-id's help text.
        self.place = dict(place or {})
        self.placed_ids = set()
        self.state_overrides = dict(state_overrides or {})
        self.items = {}     # --item: {Id: action} -- see walk
        self.item_sides = {}    # {Id: (left, right)} nav targets an item's own NavUp/NavDown can't say
        self.prepare = None     # called with the parsed root before it is walked: for a caller that
                                # fills in what the runtime would (see network_pages.py)
        # Force Show=True on an object the file marks hidden -- see --show-id.
        self.show_ids = set(show_ids or ())
        # Explicit position for the screen's top-level scene, replacing the
        # auto-centering guess -- see --scene-pos.
        self.scene_pos = scene_pos
        self.backdrop = backdrop
        # [(button, label, x)] for the shell legend -- see --legend.
        self.legend = list(legend or [])
        self.legend_y = LEGEND_Y if legend_y is None else legend_y
        # Canvas units per 720p pixel, to draw text drop shadows; None = don't.
        # See cook_text.
        self.shadow_offset = None

    def emit(self, *fields):
        self.lines.append(' '.join(_fmt(f) for f in fields))

    def comment(self, text):
        self.lines.append(f'# {text}')

    # -- draw emitters ----------------------------------------------------

    def cook_figure(self, props, x, y, w, h, opacity, overrides=None):
        fill = props.get('Fill')
        if not fill:
            return
        fill_by_name = {p['property']: p for p in fill}
        overrides = overrides or {}

        color_factor = props.get('ColorFactor')
        is_ellipse = 1 if _figure_is_ellipse(props.get('Points')) else 0

        fill_type = fill_by_name.get('FillType', {}).get('value')
        texture = fill_by_name.get('TextureFileName', {}).get('value')
        if fill_type == FILLTYPE_TEXTURE and texture:
            # A texture-filled figure IS an image draw -- this is how the
            # legend's A/B glyphs are authored (FillType=4 +
            # TextureFileName='sharedres://A-Button_32.png'), and treating it
            # as a solid rect drops them entirely.
            resolved = resolve_image_path(texture, self.image_index)
            if resolved is None:
                self.warnings.append(f"skipped texture fill (unresolvable {texture!r}) at ({x:.0f},{y:.0f})")
                self.comment(f'skipped texture fill {texture!r} (not found in any extracted archive)')
                return
            rel_name = os.path.basename(resolved)
            self.used_images[resolved] = rel_name
            tint = _mul_color({'a': 255, 'r': 255, 'g': 255, 'b': 255}, color_factor)
            self.emit('IMAGE', x, y, w, h, rel_name,
                      _apply_opacity(tint['a'], opacity), tint['r'], tint['g'], tint['b'])
            return

        gradient_prop = fill_by_name.get('Gradient')
        colour_prop = fill_by_name.get('FillColor')

        # FillType decides which field paints; see the constants above. Only
        # fall back to "gradient if there is one" when FillType is absent AND
        # there is no colour to use, so a figure that only ever had a gradient
        # still draws.
        use_gradient = gradient_prop is not None and (
            fill_type in (FILLTYPE_LINEAR_GRADIENT, FILLTYPE_RADIAL_GRADIENT)
            or (fill_type in (None, FILLTYPE_SOLID) and colour_prop is None
                and 'FillColor' not in overrides))

        if not use_gradient:
            # A timeline keyframe can override the fill colour outright -- the
            # focus highlight's green is set this way.
            c = overrides.get('FillColor')
            if c is None and colour_prop is not None:
                c = colour_prop['value']
            if c is None:
                return
            c = _mul_color(c, color_factor)
            self.emit('RECT', x, y, w, h, is_ellipse,
                      _apply_opacity(c['a'], opacity), c['r'], c['g'], c['b'])
            return

        if gradient_prop is not None:
            grad_props = {p['property']: p for p in gradient_prop['value']}
            # FillType 3 IS radial, whether or not the gradient object also
            # carries its own Radial flag -- the guide skin's radio buttons
            # (XuiRadioButton's BtnBkg) are FillType 3 with no flag at all,
            # and came out as squares fading off one edge.
            radial = grad_props.get('Radial', {}).get('value', False) or fill_type == FILLTYPE_RADIAL_GRADIENT
            stop_colors = grad_props.get('StopColor', {}).get('values', [])
            stop_positions = grad_props.get('StopPos', {}).get('values', [])
            n = min(len(stop_colors), len(stop_positions))
            if n == 0:
                return
            # XUI's un-rotated linear gradient axis runs LEFT TO RIGHT. Nearly
            # every vertical-looking gradient in these assets is authored as
            # Rotation=-90 on top of that (the dashboard's whole background
            # panel included), and the button focus highlight uses +90 -- i.e.
            # the same axis, reversed. Dropping the angle renders that
            # highlight upside down and would render any genuinely horizontal
            # gradient wrong outright.
            angle = fill_by_name.get('Rotation', {}).get('value', 0.0) or 0.0
            self.emit('GRADIENT_RECT', x, y, w, h, is_ellipse, 1 if radial else 0, float(angle), n)
            for i in range(n):
                c = _mul_color(stop_colors[i], color_factor)
                self.emit('  STOP', stop_positions[i], _apply_opacity(c['a'], opacity), c['r'], c['g'], c['b'])

    def cook_image(self, props, x, y, w, h, opacity, image_path=None):
        image_path = image_path or props.get('ImagePath')
        if not image_path:
            # Not a bug: a template/placeholder instance whose ImagePath is
            # populated at runtime by data binding.
            return
        resolved = resolve_image_path(image_path, self.image_index)
        if resolved is None:
            self.warnings.append(f"skipped image (unresolvable path {image_path!r}) at ({x:.0f},{y:.0f})")
            self.comment(f'skipped image {image_path!r} (not found in any extracted archive / runtime token)')
            return
        rel_name = os.path.basename(resolved)
        self.used_images[resolved] = rel_name
        tint = props.get('ColorFactor') or {'a': 255, 'r': 255, 'g': 255, 'b': 255}
        self.emit('IMAGE', x, y, w, h, rel_name,
                  _apply_opacity(tint['a'], opacity), tint['r'], tint['g'], tint['b'])

    def cook_text(self, text, props, x, y, w, h, opacity, point_size=None, color=None):
        if not text:
            return
        if point_size is None:
            point_size = props.get('PointSize')
        if point_size is None or point_size <= 0:
            # PointSize's schema default on XuiControl is -1 ("inherit"), and
            # on XuiText 14. Only fall back to a height-derived guess when
            # there is genuinely nothing to inherit.
            point_size = props.get('Height') or 30.0
            point_size = max(8.0, point_size * 0.6)
        point_size *= POINT_SIZE_TO_PIXELS
        c = color or props.get('TextColor') or {'a': 255, 'r': 255, 'g': 255, 'b': 255}
        c = _mul_color(c, props.get('ColorFactor'))
        halign, valign = text_alignment(props.get('TextStyle', 0) or 0)
        # A drop shadow: DropShadowColor drawn one output pixel right and down
        # of the glyphs, on text whose TextStyle has bit 0 set. Both halves
        # are measured off REFERENCES/REAL_HW/DIRECT_CONSOLE.png, a 1280x720
        # capture:
        #   offset  correlating the bright glyph pixels of "Select" and "home"
        #           against the dark pixels beside them peaks at exactly
        #           (+1, +1), the darkest ~42% under the backdrop -- the
        #           authored 50% black less antialiasing.
        #   bit 0   the tile captions ("Open Tray", "Recent") show no dark
        #           pixel at all, and they DO carry a DropShadowColor
        #           (a127 15,15,15); what they lack is the bit. slotDescription
        #           is 0x8004000 where the legend's text is 0x1111 and the
        #           page selector's 0x1411. So the colour alone means nothing.
        # Opt-in via shadow_offset (canvas units per 720p pixel) so scenes
        # cooked before this existed don't change under a re-cook. Emitted
        # even at alpha 0: an ITEM's states are paired command-for-command,
        # and a shadow present in one state and missing in the next would
        # pair it against the wrong line.
        shadow = props.get('DropShadowColor')
        if self.shadow_offset and shadow and (props.get('TextStyle', 0) or 0) & TEXTSTYLE_DROPSHADOW:
            self.lines.append(
                f'TEXT {_fmt(x + self.shadow_offset)} {_fmt(y + self.shadow_offset)} {_fmt(w)} {_fmt(h)} '
                f'{halign} {valign} {_fmt(point_size)} '
                f'{_apply_opacity(shadow["a"], opacity)} {shadow["r"]} {shadow["g"]} {shadow["b"]} {text}')
        # The renderer -- not this script -- resolves alignment, because doing
        # it here needs the exact glyph advances the font atlas will use, and
        # the manifest is supposed to survive a font swap. So the box and the
        # alignment mode travel together into the manifest, and TEXT's x/y is
        # the box origin rather than the pen position.
        self.lines.append(
            f'TEXT {_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} {halign} {valign} {_fmt(point_size)} '
            f'{_apply_opacity(c["a"], opacity)} {c["r"]} {c["g"]} {c["b"]} {text}')

    def _place_text(self, text, props, x, y, w, h, opacity, sibling_text_slots,
                    point_size=None, color=None):
        """Shared by the plain and templated text paths -- the same-position
        duplicate-label dedup (see walk()) has to apply regardless of which
        path placed the text, since sibling controls mix the two and an
        instance's Position (what makes siblings collide) is the same either
        way."""
        slot = (round(x, 1), round(y, 1))
        if sibling_text_slots is not None and slot in sibling_text_slots:
            self.comment(f'skipped duplicate-slot label {text!r} at {slot} '
                         f'(state alternate of an already-placed label; not statically resolvable)')
            return
        if sibling_text_slots is not None:
            sibling_text_slots.add(slot)
        self.cook_text(text, props, x, y, w, h, opacity, point_size=point_size, color=color)

    # -- template instantiation ------------------------------------------

    def walk_template(self, template_node, inst_x, inst_y, inst_w, inst_h, opacity,
                      content_text, content_image, content_point_size,
                      state, sibling_text_slots=None, animated=None, bound=None):
        """Renders a Visual template's children at the referencing instance's
        position and size, with the template's state timeline evaluated at
        `state` and its content presenters bound to the instance's own
        Text/ImagePath.

        `animated` and `bound` are for the recursion into a plain container
        child (see the XuiGroup case below): the keyframe values and the
        "this presenter slot is taken" flags belong to the whole template, not
        to whichever level of it is being walked."""
        tmpl_props = xur_schema.resolve_properties(template_node['class'], template_node['properties']) or {}
        tmpl_w = tmpl_props.get('Width', inst_w)
        tmpl_h = tmpl_props.get('Height', inst_h)

        if animated is None:
            animated = evaluate_state(template_node, state) if state else {}
        if bound is None:
            bound = {'text': False, 'image': False}

        # Which text presenter the instance's Text binds to. DataAssociation
        # marks a presenter as showing some OTHER field of the item's data (a
        # count, a slider's value), so the plain one wins wherever it sits --
        # XuiSlider puts its value presenter first and its label second.
        presenters = [ch for ch in template_node.get('children', []) or [] if ch['class'] == 'XuiTextPresenter']
        primary = next((ch for ch in presenters
                        if not (xur_schema.resolve_properties(ch['class'], ch['properties']) or {}).get('DataAssociation')),
                       presenters[0] if presenters else None)

        for child in template_node.get('children', []) or []:
            props = xur_schema.resolve_properties(child['class'], child['properties'])
            if props is None:
                continue
            # A keyframe's values override the child's static properties --
            # that IS the state (Show on the hairlines, Opacity on the
            # highlight, TextColor on the label).
            child_id = props.get('Id')
            if child_id in self.hide_ids:
                # --hide-id has to reach INTO templates, not just the screen's
                # own tree: the ring tracks behind the connection dots live
                # inside the `Network_Progress` visual used by the three
                # spinners, so suppressing them only at the scene level leaves
                # 30 of them on screen.
                continue
            overrides = animated.get(child_id, {})
            props = dict(props)
            props.update(overrides)

            cls_name = child['class']
            # A content presenter's own Show flag is the template's "there is
            # nothing bound here, don't draw an empty slot" default, not a
            # statement about the instance using it: the legend templates ship
            # their text presenter as Show=False and no keyframe ever turns it
            # on, yet every real legend button obviously renders its caption.
            # So a presenter that actually has content bound to it draws.
            is_bound_presenter = (
                (child is primary and content_text and not bound['text']) or
                (cls_name == 'XuiImagePresenter' and content_image and not bound['image']))
            if not props.get('Show', True) and not is_bound_presenter:
                continue

            pos = props.get('Position', (0.0, 0.0, 0.0))
            cx, cy, cw, ch = anchor_rect(
                pos, props.get('Width', 60.0), props.get('Height', 30.0),
                tmpl_w, tmpl_h, inst_w, inst_h, props.get('Anchor', 0) or 0)
            cx += inst_x
            cy += inst_y
            child_opacity = opacity * props.get('Opacity', 1.0)
            if child_opacity <= 0.004:
                continue

            cls = child['class']
            if cls == 'XuiFigure' and props.get('Fill'):
                self.cook_figure(props, cx, cy, cw, ch, child_opacity, overrides=overrides)
            elif cls == 'XuiText' and props.get('Text'):
                # A literal text child of the template (the 'A'/'B' glyph
                # letters in the legend), as opposed to a bound presenter.
                self._place_text(props['Text'], props, cx, cy, cw, ch, child_opacity, None)
            elif child is primary and content_text and not bound['text']:
                # The template child defines position/color/size/alignment;
                # the STRING comes from the instance that referenced this
                # template -- that's the binding.
                self._place_text(content_text, props, cx, cy, cw, ch, child_opacity,
                                 sibling_text_slots, point_size=content_point_size)
                bound['text'] = True
            elif cls == 'XuiImagePresenter' and content_image and not bound['image']:
                self.cook_image(props, cx, cy, cw, ch, child_opacity, image_path=content_image)
                bound['image'] = True
            elif cls == 'XuiImage' and props.get('ImagePath'):
                # A literal image child, as opposed to a bound presenter: a
                # checkbox's tick, a legend button's coloured disc.
                self.cook_image(props, cx, cy, cw, ch, child_opacity)
            elif cls == 'XuiGroup' and child.get('children'):
                # A plain container (XuiSlider's SliderBody): its children are
                # laid out against ITS box, under the same state and bindings.
                self.walk_template(child, cx, cy, cw, ch, child_opacity,
                                   content_text, content_image, content_point_size,
                                   state, sibling_text_slots, animated, bound)
            # Anything else (XuiSoundXAudio/XuiSoundXACT, or a second
            # presenter with nothing bound to it -- Label_TestStatus's value
            # column has no static content) has nothing to draw.

    # -- scene walk -------------------------------------------------------

    def walk(self, obj, parent_x, parent_y, parent_opacity, sibling_text_slots=None,
             parent_w=None, parent_h=None, auto_center=False):
        props = xur_schema.resolve_properties(obj['class'], obj['properties'])
        if props is None:
            self.warnings.append(f"unknown class {obj['class']!r}, skipping subtree")
            return

        obj_id = props.get('Id')

        if obj_id in self.hide_ids:
            self.comment(f'skipped {obj_id!r} (manually suppressed via --hide-id -- '
                         f'a runtime-alternate panel confirmed non-default against a real console reference)')
            return

        forced_pos = self.place.get(obj_id)
        if not props.get('Show', True) and forced_pos is None and obj_id not in self.show_ids:
            return

        if obj_id in self.items and obj_id not in self.state_overrides:
            # --item: a control that takes focus, as an ITEM -- cooked once at
            # rest and once focused, where it stands, and linked by its own
            # NavUp / NavDown to whichever of those are items too.
            link = lambda key: props.get(key) if props.get(key) in self.items else '-'
            left, right = self.item_sides.get(obj_id, ('-', '-'))
            self.emit('ITEM', obj_id, link('NavUp'), link('NavDown'), left, right, self.items[obj_id], 0.0)
            for state, frame in (('normal', 'Normal'), ('focus', 'Focus')):
                self.lines.append(f'STATE {state}')
                self.state_overrides[obj_id] = frame
                self.walk(obj, parent_x, parent_y, parent_opacity, set(), parent_w, parent_h, auto_center)
            del self.state_overrides[obj_id]
            self.lines.append('ENDITEM')
            return

        explicit_prop_names = {p['property'] for p in obj['properties']}
        width = props.get('Width', 60.0)
        height = props.get('Height', 30.0)

        if forced_pos is not None:
            # Absolute canvas placement, bypassing the parent chain entirely
            # -- see --place-id's help text.
            abs_x, abs_y = forced_pos
            self.placed_ids.add(obj_id)
        else:
            if auto_center and self.scene_pos is not None:
                # An explicit, measured placement for the screen's top-level
                # scene -- see --scene-pos.
                pos = (self.scene_pos[0], self.scene_pos[1], 0.0)
            elif auto_center and 'Position' not in explicit_prop_names and parent_w is not None:
                # The screen's top-level content scene (NetworkMain's 880x480
                # "Main" inside its 1120x770 canvas) is centered by the
                # dashboard shell at runtime rather than carrying its own
                # Position; nothing in the file encodes that, and every screen
                # checked has identical, symmetric margins.
                pos = ((parent_w - width) / 2.0, (parent_h - height) / 2.0, 0.0)
            else:
                pos = props.get('Position', (0.0, 0.0, 0.0))
            abs_x = parent_x + pos[0]
            abs_y = parent_y + pos[1]

        # XUI opacity composites down the tree, so accumulate multiplicatively.
        opacity = parent_opacity * props.get('Opacity', 1.0)
        if opacity <= 0.004:
            return

        # Dispatch on which visual properties this object actually carries,
        # NOT on its class name: XUI's skinning lets a generic XuiControl be
        # given a Visual template while carrying its own literal
        # Text/ImagePath/Fill, and a class whitelist silently drops those --
        # 635 Text-bearing and 98 ImagePath-bearing nodes across 204 of the
        # 363 files sit on classes outside any reasonable whitelist.
        if props.get('Fill'):
            self.cook_figure(props, abs_x, abs_y, width, height, opacity)

        visual_name = props.get('Visual')
        template = self.visual_templates.get(visual_name) if visual_name else None
        if template is not None:
            state = self.state_overrides.get(obj_id)
            if state is None:
                state = pick_state(template, obj_id in self.focused_ids)
            inst_point_size = props.get('PointSize')
            if inst_point_size is not None and inst_point_size <= 0:
                inst_point_size = None  # -1 means "inherit from the template"
            self.walk_template(template, abs_x, abs_y, width, height, opacity,
                               props.get('Text'), props.get('ImagePath'), inst_point_size,
                               state, sibling_text_slots=sibling_text_slots)
        else:
            if visual_name:
                self.warnings.append(f"Visual {visual_name!r} not found in any skin file "
                                     f"(object {obj_id!r}) -- rendering raw instance properties")
            if props.get('ImagePath'):
                self.cook_image(props, abs_x, abs_y, width, height, opacity)
            if props.get('Text'):
                # XUI screens commonly author several mutually-exclusive label
                # variants for one UI slot (InternetText/PCText/MCXText all at
                # the identical Position, toggled at runtime by what's actually
                # detected). Some are explicitly Show=False; not all are.
                self._place_text(props['Text'], props, abs_x, abs_y, width, height,
                                 opacity, sibling_text_slots)

        child_text_slots = set()
        for child in obj['children']:
            self.walk(child, abs_x, abs_y, opacity, sibling_text_slots=child_text_slots)

    def cook_backdrop(self, canvas_w, canvas_h):
        """The dashboard's own background, behind the screen scene. Not in any
        screen's .xur (it is `DashBkgnd.xur`'s full-screen `Aura` image), but
        without it a screen renders against nothing and reads as a floating
        panel rather than the dashboard."""
        self.emit('GRADIENT_RECT', 0.0, 0.0, canvas_w, canvas_h, 0, 0, -90.0, len(BACKDROP_STOPS))
        for pos, (a, r, g, b) in BACKDROP_STOPS:
            self.emit('  STOP', pos, a, r, g, b)

    def cook_legend_item(self, button, label, x):
        """One `<glyph> <caption>` pair of the shell's legend row -- see the
        LEGEND_* constants for where the geometry comes from."""
        image = LEGEND_GLYPH_IMAGE.get(button.upper())
        if image is None:
            self.warnings.append(f"--legend: unknown button {button!r} (expected A/B/X/Y)")
            return
        resolved = resolve_image_path(image, self.image_index)
        if resolved is None:
            self.warnings.append(f"--legend: glyph {image!r} not found in any search dir")
        else:
            self.used_images[resolved] = os.path.basename(resolved)
            self.emit('IMAGE', x + LEGEND_GLYPH_OFFSET, self.legend_y, LEGEND_GLYPH_SIZE, LEGEND_GLYPH_SIZE,
                      os.path.basename(resolved), 255, 255, 255, 255)
        # The 32px button PNGs are plain coloured discs -- the letter is a
        # separate text element on top, exactly as skin.xur's legend_A/legend_B
        # visuals do it (an XuiText 'A'/'B' in (245,245,245) over the figure).
        lc = LEGEND_GLYPH_LETTER_COLOR
        self.lines.append(
            f'TEXT {_fmt(x + LEGEND_GLYPH_OFFSET)} {_fmt(self.legend_y)} '
            f'{_fmt(LEGEND_GLYPH_SIZE)} {_fmt(LEGEND_GLYPH_SIZE)} '
            f'{HALIGN_CENTER} {VALIGN_CENTER} '
            f'{_fmt(LEGEND_GLYPH_POINT_SIZE * POINT_SIZE_TO_PIXELS)} '
            f'{lc["a"]} {lc["r"]} {lc["g"]} {lc["b"]} {button.upper()}')
        if label:
            c = LEGEND_LABEL_COLOR
            self.lines.append(
                f'TEXT {_fmt(x + LEGEND_LABEL_OFFSET)} {_fmt(self.legend_y)} '
                f'{_fmt(512.0)} {_fmt(LEGEND_GLYPH_SIZE)} '
                f'{HALIGN_LEFT} {VALIGN_CENTER} '
                f'{_fmt(LEGEND_LABEL_POINT_SIZE * POINT_SIZE_TO_PIXELS)} '
                f'{c["a"]} {c["r"]} {c["g"]} {c["b"]} {label}')

    def cook(self, xur_path, out_manifest_path, out_assets_dir, canvas_override=None):
        data = open(xur_path, 'rb').read()
        xur = xur_extract.parse_xur8(data)
        root = xur.root_object
        root_props = xur_schema.resolve_properties(root['class'], root['properties'])
        canvas_w = root_props.get('Width', 0.0) if root_props else 0.0
        canvas_h = root_props.get('Height', 0.0) if root_props else 0.0
        if canvas_override:
            canvas_w, canvas_h = canvas_override

        if self.prepare:
            self.prepare(root)
        self.focused_ids = resolve_focus_path(root, self.hide_ids)

        self.comment(f'cooked from {os.path.basename(xur_path)}')
        if self.focused_ids:
            self.comment(f'default focus (from DefaultFocus chain): {sorted(self.focused_ids)}')
        self.emit('CANVAS', canvas_w, canvas_h)
        if self.backdrop:
            self.cook_backdrop(canvas_w, canvas_h)
        root_text_slots = set()
        for child in root.get('children', []) or []:
            self.walk(child, 0.0, 0.0, 1.0, sibling_text_slots=root_text_slots,
                      parent_w=canvas_w, parent_h=canvas_h, auto_center=True)

        for button, label, lx in self.legend:
            self.cook_legend_item(button, label, lx)

        for pid in self.place:
            if pid not in self.placed_ids:
                self.warnings.append(f"--place-id {pid!r} never matched any object in this layout")

        os.makedirs(os.path.dirname(out_manifest_path) or '.', exist_ok=True)
        with open(out_manifest_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self.lines) + '\n')

        if out_assets_dir:
            os.makedirs(out_assets_dir, exist_ok=True)
            import shutil
            for src, rel_name in self.used_images.items():
                shutil.copyfile(src, os.path.join(out_assets_dir, rel_name))

        return {
            'canvas': (canvas_w, canvas_h),
            'lines': len(self.lines),
            'images': list(self.used_images.values()),
            'warnings': self.warnings,
            'focused': sorted(self.focused_ids),
        }


def _parse_place(arg):
    """'Id=x,y' -> (Id, (x, y))"""
    name, _, coords = arg.partition('=')
    xs, _, ys = coords.partition(',')
    return name, (float(xs), float(ys))


def _parse_legend(arg):
    """'A=Select@96' -> ('A', 'Select', 96.0)"""
    button, _, rest = arg.partition('=')
    label, _, x = rest.partition('@')
    return button, label, float(x) if x else 96.0


def _parse_state(arg):
    """'Id=StateName' -> (Id, StateName)"""
    name, _, state = arg.partition('=')
    return name, state


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('xur_path')
    ap.add_argument('out_manifest')
    ap.add_argument('--assets-dir', default=None, help='directory to copy referenced images into')
    ap.add_argument('--search-dir', action='append', default=[], required=True,
                    help='directory to recursively index PNGs from, by basename (repeatable)')
    ap.add_argument('--hide-id', action='append', default=[],
                    help="manually suppress an object (and its subtree) by its XUI Id, even though it's "
                         "statically Show=True -- for runtime-alternate content (e.g. a non-default menu "
                         "detail panel) that no static signal in the file distinguishes from the default "
                         "one; confirm against a real console screenshot before using (repeatable)")
    ap.add_argument('--place-id', action='append', default=[], metavar='ID=X,Y',
                    help="move an object to an absolute canvas position, ignoring its parent chain (and "
                         "un-hiding it if it is Show=False). For dashboard CHROME that the shell positions "
                         "at runtime rather than the screen file: every settings-style screen parks its "
                         "title label and its A/B legend buttons far off-canvas (NetworkMain's txt_Header "
                         "at y=-424.8, its legend_a at y=+1030.3; dashSysCslSet.xur does the identical "
                         "thing at y=-467.8 / +1139, and marks its title Show=False outright), because the "
                         "real placement lives in the shell's own LegendScene.xur. Nothing in the screen "
                         "file can recover it, so it is a flag rather than a rule (repeatable)")
    ap.add_argument('--state', action='append', default=[], metavar='ID=STATE',
                    help="force a control's Visual template to a specific named state (Normal, Focus, "
                         "Press, Disabled, ...) instead of the Normal/Focus choice derived from the "
                         "screen's DefaultFocus chain (repeatable)")
    ap.add_argument('--show-id', action='append', default=[], metavar='ID',
                    help="force an object to render even though the file marks it Show=False -- for "
                         "runtime-toggled state the console actually displays. NetworkMain's third "
                         "connection-progress group (ProgressBar3_On) is the case this exists for: it is "
                         "Show=False in the file, but a console capture of a connected machine shows all "
                         "three dot groups lit (repeatable)")
    ap.add_argument('--canvas', default=None, metavar='WxH',
                    help='override the canvas size the scene is centered in. Screens are authored on a '
                         '1120x770 canvas, but the console composites them into 1280x720 -- measured off a '
                         'capture of NetworkMain, whose 440-wide menu buttons span 825px of a 2406px-wide '
                         'capture, putting the whole image at 1283x722 canvas units')
    ap.add_argument('--scene-pos', default=None, metavar='X,Y',
                    help="place the screen's top-level scene explicitly instead of centering it in the "
                         "canvas. Measured from a console capture: NetworkMain's 880x480 panel sits at "
                         "(200, 114) of 1280x720 -- horizontally centered, but ~6 units above center "
                         "vertically, which centering alone does not produce")
    ap.add_argument('--backdrop', action='store_true',
                    help="draw the dashboard's own grey background behind the scene (DashBkgnd.xur's "
                         'full-screen aura, sampled from a console capture) instead of leaving it empty')
    ap.add_argument('--legend', action='append', default=[], metavar='BTN=LABEL@X',
                    help="add one item to the shell's A/B/X/Y legend row, e.g. 'A=Select@96'. The row "
                         'belongs to LegendScene.xur, not to any screen, and is laid out by dashboard '
                         'code, so each item takes a measured x (repeatable)')
    ap.add_argument('--legend-y', type=float, default=None,
                    help=f'y of the legend row (default {LEGEND_Y}, measured from a console capture)')
    ap.add_argument('--item', action='append', default=[], metavar='ID[=ACTION]',
                    help="cook a control as a focusable ITEM -- its resting and focused looks, linked to "
                         "the other items by its own NavUp/NavDown -- with ACTION (e.g. GOTO:<scene>) on A, "
                         "or none. Without this a screen is a picture of itself (repeatable)")
    ap.add_argument('--skin', action='append', default=[],
                    help='explicit path to a skin/template .xur (e.g. skin.xur) to load Visual templates '
                         'from, in addition to whatever is auto-discovered under --search-dir by that '
                         'basename (repeatable)')
    args = ap.parse_args()

    canvas_override = None
    if args.canvas:
        cw, _, chh = args.canvas.lower().partition('x')
        canvas_override = (float(cw), float(chh))

    index = build_image_index(args.search_dir)
    skin_paths = find_skin_files(args.search_dir) + args.skin
    templates = load_visual_templates(skin_paths)
    print(f"Loaded {len(templates)} visual templates from {len(skin_paths)} skin file(s): "
          f"{', '.join(skin_paths) if skin_paths else '(none found)'}")

    scene_pos = None
    if args.scene_pos:
        sx, _, sy = args.scene_pos.partition(',')
        scene_pos = (float(sx), float(sy))

    cooker = Cooker(index, hide_ids=args.hide_id, visual_templates=templates,
                    place=dict(_parse_place(p) for p in args.place_id),
                    state_overrides=dict(_parse_state(s) for s in args.state),
                    show_ids=args.show_id, scene_pos=scene_pos, backdrop=args.backdrop,
                    legend=[_parse_legend(l) for l in args.legend], legend_y=args.legend_y)
    cooker.items = {i.partition('=')[0]: i.partition('=')[2] or '-' for i in args.item}
    result = cooker.cook(args.xur_path, args.out_manifest, args.assets_dir, canvas_override)
    print(f"Canvas: {result['canvas']}, {result['lines']} manifest lines, {len(result['images'])} images used")
    if result['focused']:
        print(f"Focused (DefaultFocus chain): {', '.join(result['focused'])}")
    for w in result['warnings']:
        print(f"  warning: {w}")
