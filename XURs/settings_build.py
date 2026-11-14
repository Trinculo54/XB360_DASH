"""
Build the System Settings screen as a cooked scene manifest.

WHY THIS ISN'T PLAIN xur_cook.py
--------------------------------
`SystemScene.xur` is a real layout and `xur_cook.py` flattens it happily --
but what comes out is only the FRAME: two panels and the legend. The eight
rows aren't in it as objects. `lstSettings` is a `XuiCommonList` whose Visual
is the `List_1line_icon_1text` template, and the runtime fills it from data.

The data is still right there in the .xur's string table, though, one
\r\n-separated list per column, and the row's appearance is fully specified
by the skin. So unlike the hub -- where the geometry had to be measured off
captures because the layout is assembled by Lua -- NOTHING here is measured.
Every number below was read out of the rip:

  SystemScene.xur      string table    the 8 labels, their descriptions, their
                                       icons, and the .xur each row opens
                       object tree     lstSettings at (0,1) 440x444 inside a
                                       DashScene at (120,145) 880x480;
                                       metaPanelScene at (470,20) 380x430
  skin.xur             BackgroundPanel     the light list panel's gradient
                       RightPanelShader    the darker description panel's
                       List_1line_icon_1text  row pitch: 450x47
                       btn_1line_icon_1text   one row, in full (below)
                       Label_Head          header, PointSize 22
                       Label_BodyWhite_18  description, PointSize 18

The row template `btn_1line_icon_1text` (450x47) carries its own timeline,
which is the state table -- frame 0 is resting, frame 7 is focused:

  txt_line3        x=50 w=375 h=47, PointSize 18, left/vcentre
  image_presenter  (10,7) 32x32
  GreenHighlight   (0,-1) 450x48, FillColor (0,138,0)
  Top / Bottom     1px rules at y=-1 and y=46, (159,172,178)

Those elements' per-state values come from xur_cook.evaluate_state rather
than from the properties sitting on them, which is the difference between
right and plausible here. The template's named frames are the state table --
KillFocus 0, Normal 5, Focus 7, NormalDisable 369, FocusDisable 371 -- and a
state's value is the last keyframe at or before its frame:

  state          txt_line3        icon ColorFactor  highlight   rules
  Normal         (51,58,64)       (51,58,64)        hidden      shown
  Focus          (235,235,235)    (235,235,235)     opacity 1   hidden
  NormalDisable  (51,58,64) @50%  (51,58,64) @50%   hidden      shown

Note what Normal is NOT: (101,109,114). That value is the static TextColor
property on txt_line3, and it never renders in the resting state -- the
timeline overrides it at frame 4. evaluate_state's own docstring flags this
exact trap, and I walked straight into it on the first pass.

    python3 settings_build.py --search-dir ../DASH/dash_bin_embedded_assets \\
                              --search-dir ../XZP --out-dir ../assets/scenes
"""
import argparse
import os
import shutil

try:
    from PIL import ImageFont
except ImportError:
    ImageFont = None

from xur_cook import (BACKDROP_STOPS, LEGEND_GLYPH_IMAGE, LEGEND_GLYPH_OFFSET, LEGEND_GLYPH_SIZE,
                      LEGEND_GLYPH_LETTER_COLOR, LEGEND_GLYPH_POINT_SIZE, LEGEND_LABEL_COLOR,
                      LEGEND_LABEL_OFFSET, LEGEND_LABEL_POINT_SIZE, LEGEND_Y, POINT_SIZE_TO_PIXELS,
                      HALIGN_LEFT, HALIGN_CENTER, VALIGN_CENTER, VALIGN_TOP,
                      build_image_index, resolve_image_path, _fmt)

SCENE_NAME = 'SystemSettings'

# The shell frame, not the scene's own canvas. SystemScene.xur declares
# 1120x770, but that is the authoring canvas -- the dashboard composites the
# screen into a 1280x720 frame over its own backdrop, which is why NetworkMain
# was cooked with --canvas 1280x720 and --scene-pos too. Matching it matters
# for more than the backdrop: two scenes with different canvas sizes get
# different letterbox scales, so the A-to-settings transition would visibly
# jump the whole frame between them.
CANVAS = (1280.0, 720.0)

# --- geometry, all from SystemScene.xur's object tree -----------------------
# The 880x480 DashScene panel lands where NetworkMain's does -- same shell,
# same panel, and that position was tuned against a console capture.
SCENE_ORIGIN = (200.0, 114.0)
PANEL_W, PANEL_H = 880.0, 480.0
PANEL_STOPS = [(0.0, (255, 232, 235, 235)), (1.0, (255, 201, 207, 209))]   # BackgroundPanel
SHADER_REL = (440.0, 0.0, 440.0, 480.0)                                     # RightPanelShader
SHADER_STOPS = [(0.0, (255, 151, 157, 161)), (1.0, (255, 105, 110, 115))]

LIST_REL = (0.0, 1.0, 440.0, 444.0)    # lstSettings
META_REL = (470.0, 20.0, 380.0, 430.0) # metaPanelScene, Label_BodyWhite_18
META_POINT_SIZE = 18.0

HEADER_POS = (96.0, 56.0)              # NetworkMain's header, same shell
HEADER_SIZE = (855.0, 47.0)            # (the authored y is a keyframe offset, not a position)
HEADER_POINT_SIZE = 22.0               # Label_Head
HEADER_COLOR = {'a': 255, 'r': 235, 'g': 235, 'b': 235}

# --- the row, from btn_1line_icon_1text ------------------------------------
ROW_H = 47.0
ROW_TEXT_X, ROW_TEXT_W = 50.0, 375.0
ROW_POINT_SIZE = 18.0
ROW_ICON = (10.0, 7.0, 32.0, 32.0)
RULE_COLOR = {'a': 255, 'r': 159, 'g': 172, 'b': 178}
ROW_TEXT_REST = {'a': 255, 'r': 51, 'g': 58, 'b': 64}
ROW_TEXT_FOCUS = {'a': 255, 'r': 235, 'g': 235, 'b': 235}
ROW_ICON_REST = {'a': 255, 'r': 51, 'g': 58, 'b': 64}
ROW_ICON_FOCUS = {'a': 255, 'r': 235, 'g': 235, 'b': 235}
# FLAT, not the Gradient property that also sits on GreenHighlight. The
# figure carries both a FillColor and a two-stop Gradient, and it's FillColor
# that renders: the timeline animates FillColor across every state -- driving
# it to (50,50,50) for the disabled ones -- and never touches the gradient,
# which would be pointless if the gradient were what you saw. The gradient's
# (81,134,15)->(119,186,2) ramp is also visibly yellow against the console's
# flat green. Same class of trap as the text colour: the property that's
# present isn't necessarily the property that draws.
HIGHLIGHT_COLOR = {'a': 255, 'r': 0, 'g': 138, 'b': 0}
# NormalDisable is the resting look at half opacity -- same colours, same
# rules, nothing else changed. So a disabled row is just an alpha, which is
# also the rule the hub's disabled tiles follow.
ROW_DISABLED_ALPHA = 128

# Normal -> Focus is frames 5 -> 7 on btn_1line_icon_1text: two frames.
FOCUS_BLEND_SECONDS = 0.05

LEGEND_START_X = 96.0

# --- the rows, from the string table ---------------------------------------
# Column order is the .xur's: label, icon, description, and the .xur that row
# opens. `action` is ours -- the scenes this project has actually cooked.
ROWS = [
    dict(id='console', label='Console Settings', icon='ico_32x_console.png',
         action='GOTO:dashSysCslSet',      # the pages under it are settings_pages.py's
         desc='Change your Xbox 360 console settings, including display, audio, '
              'language, and remote control.'),
    dict(id='kinect', label='Kinect Settings', icon='ico_32x_kinect_device.png',
         action='GOTO:KinectSettings',
         desc='Change Kinect sensor settings, and access the Kinect Tuner to configure '
              'your play space and diagnose audio problems.'),
    dict(id='storage', label='Storage', icon='ico_32x_MU.png', action='GOTO:DeviceSelector',
         desc='Move or delete saved games, profiles, and other items on Xbox 360 '
              'storage devices.'),
    # The only row wired up so far: NetworkMain is already cooked, and the
    # .xur names NetworkSelectionMainScene.xur here -- the picker that leads
    # to it -- so this short-circuits one screen for now.
    dict(id='network', label='Network Settings', icon='ico_32x_network.png',
         action='GOTO:NetworkMain',
         desc='Connect to Xbox Live or your home network. Set up wireless connections '
              'and test network settings.'),
    dict(id='computers', label='Computers', icon='ico_32x_PC.png', action='GOTO:742_SelectNetworkDevice',
         desc='Manage your connection to a Windows Media Center PC.\n\n'
              'To connect to a PC running media sharing software, open the video '
              'player, music player, or picture viewer.'),
    dict(id='tv', label='TV', icon='ico_32x_IPTV.png', action='GOTO:LiveTVSettings',
         desc='Change your TV provider.'),
    dict(id='vision', label='Xbox Live Vision', icon='ico_32x_camera.PNG', action='GOTO:dashSysLiveVision',
         desc='Change the settings for your Xbox Live Vision camera.'),
    dict(id='initialsetup', label='Initial Setup', icon='ico_32x_systemSet.png', enabled=False,
         desc='Start the original system setup.\n\n'
              "You can't perform initial setup if content controls are on. If you want "
              'to do this, first turn them off in Settings, Family.'),
]

LEGEND = [('A', 'Select'), ('B', 'Back')]


class Builder:
    def __init__(self, image_index, font_path=None):
        self.image_index = image_index
        self.font_path = font_path
        self._font_cache = {}
        self.lines = []
        self.used_images = {}
        self.warnings = []

    def emit(self, *fields):
        self.lines.append(' '.join(_fmt(f) for f in fields))

    def comment(self, text):
        self.lines.append(f'# {text}')

    def rect(self, x, y, w, h, c):
        self.emit('RECT', x, y, w, h, 0, c['a'], c['r'], c['g'], c['b'])

    def gradient(self, x, y, w, h, stops, angle=-90.0, alpha_scale=1.0):
        self.emit('GRADIENT_RECT', x, y, w, h, 0, 0, angle, len(stops))
        for pos, (a, r, g, b) in stops:
            self.emit('  STOP', pos, int(round(a * alpha_scale)), r, g, b)

    def text(self, s, x, y, w, h, size_points, color, halign=HALIGN_LEFT, valign=VALIGN_CENTER,
             alpha=None):
        a = color['a'] if alpha is None else alpha
        self.lines.append(
            f'TEXT {_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} {halign} {valign} '
            f'{_fmt(size_points * POINT_SIZE_TO_PIXELS)} {a} {color["r"]} {color["g"]} {color["b"]} {s}')

    def image(self, name, x, y, w, h, tint=None, alpha=255):
        resolved = resolve_image_path(name, self.image_index)
        if resolved is None:
            self.warnings.append(f'image {name!r} not found in any search dir')
            self.comment(f'skipped image {name!r} (not found)')
            return
        rel = os.path.basename(resolved)
        self.used_images[resolved] = rel
        t = tint or {'r': 255, 'g': 255, 'b': 255}
        self.emit('IMAGE', x, y, w, h, rel, alpha, t['r'], t['g'], t['b'])

    # -- text measuring, for wrapping the description ------------------------
    def measure(self, s, size_points):
        px = size_points * POINT_SIZE_TO_PIXELS
        if ImageFont is None or not self.font_path or not os.path.isfile(self.font_path):
            return 0.5 * px * len(s)
        key = round(px, 2)
        if key not in self._font_cache:
            # At 64x and scaled back, so the answer doesn't depend on which
            # layout engine this machine's Pillow has (see hub_build.py).
            self._font_cache[key] = ImageFont.truetype(self.font_path, size=int(round(px)) * 64)
        return self._font_cache[key].getlength(s) / 64.0

    def wrap(self, text, width, size_points):
        """Greedy word wrap. The TEXT command draws one unwrapped run, so a
        paragraph has to be broken into lines here -- the console wraps this
        itself, and this is where the breaks land."""
        out = []
        for para in text.split('\n'):
            if not para.strip():
                out.append('')
                continue
            line = ''
            for word in para.split():
                trial = word if not line else line + ' ' + word
                if line and self.measure(trial, size_points) > width:
                    out.append(line)
                    line = word
                else:
                    line = trial
            if line:
                out.append(line)
        return out


def row_rect(index):
    """Absolute rect of row `index` -- the list's own origin plus the pitch."""
    lx, ly, lw, _lh = LIST_REL
    return (SCENE_ORIGIN[0] + lx, SCENE_ORIGIN[1] + ly + index * ROW_H, lw, ROW_H)


def build(rows, image_index, font_path):
    b = Builder(image_index, font_path)
    b.comment('System Settings -- rows and their copy come from SystemScene.xur\'s string')
    b.comment('table; row geometry and colours from skin.xur\'s btn_1line_icon_1text.')
    b.comment('Nothing here is measured off a capture. See settings_build.py.')
    b.emit('CANVAS', *CANVAS)
    b.emit('DEFAULTFOCUS', rows[0]['id'])
    # How long focus takes to settle on this screen. The hub's tiles morph
    # over ~0.22s, which is right for something that grows and brightens, but
    # far too slow for a list -- the row you just left sits half-white for a
    # visible beat and reads as popping. The template says what it should be:
    # its named frames put Normal at 5 and Focus at 7, i.e. TWO frames, ~33ms
    # at 60Hz. Rounded up a little so it still reads as a movement.
    b.emit('FOCUSBLEND', FOCUS_BLEND_SECONDS)

    ox, oy = SCENE_ORIGIN
    # --- chrome: the shell backdrop, the two panels, the header, the legend.
    b.lines.append('LAYER fixed')
    # The dash's own grey ramp. It belongs to the shell rather than to any
    # screen -- the same one xur_cook draws behind a scene for --backdrop, and
    # the same one the hub uses -- so without it a screen sits on black.
    b.gradient(0.0, 0.0, CANVAS[0], CANVAS[1], BACKDROP_STOPS)
    b.gradient(ox, oy, PANEL_W, PANEL_H, PANEL_STOPS)
    sx, sy, sw, sh = SHADER_REL
    b.gradient(ox + sx, oy + sy, sw, sh, SHADER_STOPS)
    b.text('System Settings', HEADER_POS[0], HEADER_POS[1], HEADER_SIZE[0], HEADER_SIZE[1],
           HEADER_POINT_SIZE, HEADER_COLOR, HALIGN_LEFT, VALIGN_CENTER)

    # Same flow layout as the hub's legend and NetworkMain's: glyph, letter
    # over it, caption, then advance past the caption. It's the shell's row,
    # laid out by dashboard code, so the constants come from xur_cook.
    x = LEGEND_START_X
    for button, label in LEGEND:
        glyph = LEGEND_GLYPH_IMAGE.get(button.upper())
        if glyph:
            b.image(glyph, x + LEGEND_GLYPH_OFFSET, LEGEND_Y, LEGEND_GLYPH_SIZE, LEGEND_GLYPH_SIZE)
        b.text(button.upper(), x + LEGEND_GLYPH_OFFSET, LEGEND_Y, LEGEND_GLYPH_SIZE, LEGEND_GLYPH_SIZE,
               LEGEND_GLYPH_POINT_SIZE, LEGEND_GLYPH_LETTER_COLOR, HALIGN_CENTER, VALIGN_CENTER)
        b.text(label, x + LEGEND_LABEL_OFFSET, LEGEND_Y, 512.0, LEGEND_GLYPH_SIZE,
               LEGEND_LABEL_POINT_SIZE, LEGEND_LABEL_COLOR, HALIGN_LEFT, VALIGN_CENTER)
        x += LEGEND_LABEL_OFFSET + b.measure(label, LEGEND_LABEL_POINT_SIZE) + 22.0

    # --- the rows.
    mx, my, mw, mh = META_REL
    for i, row in enumerate(rows):
        rx, ry, rw, _rh = row_rect(i)
        enabled = row.get('enabled', True)
        text_alpha = 255 if enabled else ROW_DISABLED_ALPHA
        # The list WRAPS: down off the last row comes back to the first and
        # up off the first goes to the last, which is what the console does.
        # Python's negative indexing gives the up-wrap for free.
        up = rows[i - 1]['id']
        down = rows[(i + 1) % len(rows)]['id']
        b.emit('ITEM', row['id'], up, down, '-', '-', row.get('action', '-'), 0.0)
        if not enabled:
            # Sound only; the dimmed text/icon is already in the state blocks
            # below. See ManifestItem::disabled.
            b.lines.append('DISABLED')

        def body(focused):
            b.lines.append('LAYER fixed')
            if focused:
                # The green fill sits a pixel proud at the top and is a pixel
                # taller than the row, which is what covers the rules the
                # resting row draws -- the template hides Top/Bottom in the
                # focused state for the same reason.
                b.rect(rx, ry - 1.0, rw, ROW_H + 1.0, HIGHLIGHT_COLOR)
            else:
                b.rect(rx, ry - 1.0, rw, 1.0, RULE_COLOR)
                b.rect(rx, ry + ROW_H - 1.0, rw, 1.0, RULE_COLOR)
            ix, iy, iw, ih = ROW_ICON
            b.image(row['icon'], rx + ix, ry + iy, iw, ih,
                    ROW_ICON_FOCUS if focused else ROW_ICON_REST, text_alpha)
            b.text(row['label'], rx + ROW_TEXT_X, ry, ROW_TEXT_W, ROW_H, ROW_POINT_SIZE,
                   ROW_TEXT_FOCUS if focused else ROW_TEXT_REST,
                   HALIGN_LEFT, VALIGN_CENTER, text_alpha)

        b.lines.append('STATE normal')
        body(False)

        b.lines.append('STATE focus')
        body(True)
        # The description belongs to whichever row has focus, so it lives in
        # the focused state rather than in the chrome -- which also means it
        # crossfades with the row as focus moves, for free.
        line_h = ROW_POINT_SIZE * POINT_SIZE_TO_PIXELS * 1.34
        y = oy + my
        for line in b.wrap(row['desc'], mw - 10.0, META_POINT_SIZE):
            if line:
                b.text(line, ox + mx, y, mw, line_h, META_POINT_SIZE, ROW_TEXT_FOCUS,
                       HALIGN_LEFT, VALIGN_TOP)
            y += line_h
        b.lines.append('ENDITEM')

    return b


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--search-dir', action='append', default=[], required=True)
    ap.add_argument('--out-dir', default='../assets/scenes')
    ap.add_argument('--font', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   '..', 'assets', 'fonts', 'xenonclatin_full.ttf'))
    args = ap.parse_args()

    index = build_image_index(args.search_dir)
    b = build(ROWS, index, args.font)
    scene_dir = os.path.join(args.out_dir, SCENE_NAME)
    assets_dir = os.path.join(scene_dir, 'assets')
    os.makedirs(assets_dir, exist_ok=True)
    with open(os.path.join(scene_dir, 'manifest.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(b.lines) + '\n')
    for src, rel in b.used_images.items():
        shutil.copyfile(src, os.path.join(assets_dir, rel))
    print(f'{SCENE_NAME}: {len(b.lines)} lines, {len(b.used_images)} images, {len(ROWS)} rows')
    for w in b.warnings:
        print(f'   warning: {w}')


if __name__ == '__main__':
    main()
