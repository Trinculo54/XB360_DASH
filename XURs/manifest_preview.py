"""Render a cooked manifest.txt to a PNG, offline, with (deliberately) the
same semantics as the native D3D11 path -- so a cook-pipeline change can be
eyeballed without a Windows box or a rebuild.

Mirrors, command for command:
  - QuadRenderer::DrawRect / DrawImage / DrawGradientRect / DrawText
  - Shaders.h's PSSolid / PSImage / PSText / PSGradient, including
    EllipseCoverage's inscribed-ellipse mask, PSGradient's t = uv.y (linear,
    top->bottom) vs. length(uv-0.5)*2 (radial), and its stop-bracket lerp.
  - FontAtlas's top-left -> baseline conversion via the font's real ascent,
    and its per-glyph advance/xoff/yoff layout.

Anything that looks different between this and the app is a bug in one of
them; that's the point.

    python3 manifest_preview.py <manifest.txt> <out.png> [--font <ttf>] [--scale N]
                                [--frame N|MARK] [--var name=value ...]

--frame is where the scene's timeline stands (TRACK / KEY / MARK), as a frame
number or the name of a MARK; --var sets an IF variable or an item's value
(WHEN). Both mirror Scene::Draw's `frame` and `variables`.
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw
from PIL import ImageFont


def parse_manifest(path):
    """-> (canvas, static_cmds, items) where items is a list of
    {id, nav, action, normal, focus} -- see the ITEM/STATE block in
    SceneManifest.h for the format."""
    canvas = (0.0, 0.0)
    cmds = []
    items = []
    sink = cmds
    layer_fixed = False
    font_file = ''
    fade_anchor = 0.0
    top_level_fade_anchor = 0.0
    # 'tracks' and 'marks' ride along in here rather than as more return
    # values: nav_check.py unpacks this function's result too.
    fade = {'off_alpha': 1.0, 'span': 1.0, 'tracks': [], 'marks': {}}
    track = None        # index of the TRACK block being read
    cond = None         # (variable, value) of the IF / WHEN in force
    lines = open(path, encoding='utf-8').read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith('#'):
            continue
        f = line.split()
        op = f[0]
        if op == 'FADE':
            fade['off_alpha'] = float(f[1])
            fade['span'] = max(float(f[2]), 1e-6)
            continue
        if op == 'FADEGROUP':
            fade_anchor = float(f[1])
            if sink is cmds:
                top_level_fade_anchor = fade_anchor
            continue
        if op == 'LAYER':
            layer_fixed = (f[1] == 'fixed')
            continue
        if op == 'ITEM':
            nav = dict(zip(('up', 'down', 'left', 'right'), f[2:6]))
            items.append(dict(id=f[1], nav={k: (v if v != '-' else '') for k, v in nav.items()},
                              action=(f[6] if len(f) > 6 and f[6] != '-' else ''),
                              view_x=float(f[7]) if len(f) > 7 else 0.0,
                              normal=[], focus=[], press=[]))
            fade_anchor = items[-1]['view_x']
            sink = items[-1]['normal']
            continue
        if op == 'TRACK':
            fade['tracks'].append(dict(id=f[1], box=tuple(float(v) for v in f[2:6]), keys=[]))
            track = len(fade['tracks']) - 1
            continue
        if op == 'KEY':
            if track is not None:
                fade['tracks'][track]['keys'].append(dict(
                    frame=float(f[1]), interp=f[2], ease=tuple(float(v) for v in f[3:6]), show=f[6] != '0',
                    dx=float(f[7]), dy=float(f[8]), sx=float(f[9]), sy=float(f[10]), alpha=float(f[11])))
            continue
        if op == 'ENDTRACK':
            track = None
            continue
        if op == 'MARK':
            fade['marks'].setdefault(f[1], float(f[2]))
            continue
        if op == 'IF':
            cond = (f[1], int(f[2]))
            continue
        if op == 'ENDIF':
            cond = None
            continue
        if op == 'VALUES':
            if items:
                items[-1]['initial'] = int(f[2])
            continue
        if op == 'WHEN':
            if items:
                cond = (items[-1]['id'], int(f[1]))
            continue
        if op == 'STATE':
            cond = None
            if items:
                # 'press' is parsed so its commands don't land in 'normal';
                # nothing here draws it (it only exists mid-press).
                sink = items[-1][f[1]] if f[1] in ('focus', 'press') else items[-1]['normal']
            continue
        if op == 'ENDITEM':
            sink = cmds
            fade_anchor = top_level_fade_anchor
            cond = None
            continue
        if op == 'FONT':
            # Sticky, like LAYER -- see SceneManifest.cpp. '-'/'default'
            # goes back to the scene's default face.
            which = f[1] if len(f) > 1 else 'default'
            font_file = '' if which in ('-', 'default') else which
            continue
        if op == 'CANVAS':
            canvas = (float(f[1]), float(f[2]))
        elif op == 'RECT':
            sink.append(dict(fixed=layer_fixed, anchor=fade_anchor, op='RECT', x=float(f[1]), y=float(f[2]), w=float(f[3]), h=float(f[4]),
                             ellipse=int(f[5]) != 0,
                             color=tuple(float(v) for v in f[6:10])))
        elif op == 'IMAGE':
            sink.append(dict(fixed=layer_fixed, anchor=fade_anchor, op='IMAGE', x=float(f[1]), y=float(f[2]), w=float(f[3]), h=float(f[4]),
                             file=f[5], color=tuple(float(v) for v in f[6:10])))
        elif op in ('TEXT', 'TEXTV'):
            sink.append(dict(fixed=layer_fixed, anchor=fade_anchor, op='TEXT', turn=(op == 'TEXTV'),
                             x=float(f[1]), y=float(f[2]), w=float(f[3]), h=float(f[4]),
                             halign=int(f[5]), valign=int(f[6]), size=float(f[7]),
                             color=tuple(float(v) for v in f[8:12]), font=font_file,
                             text=line.split(None, 12)[12] if len(f) > 12 else ''))
        elif op == 'GRADIENT_RECT':
            n = int(f[8])
            stops = []
            for _ in range(n):
                sf = lines[i].strip().split()
                i += 1
                stops.append((float(sf[1]), tuple(float(v) for v in sf[2:6])))
            sink.append(dict(fixed=layer_fixed, anchor=fade_anchor, op='GRAD', x=float(f[1]), y=float(f[2]), w=float(f[3]), h=float(f[4]),
                             ellipse=int(f[5]) != 0, radial=int(f[6]) != 0, angle=float(f[7]),
                             stops=stops))
        else:
            continue
        if op != 'CANVAS':      # everything else in this chain appended a draw command
            sink[-1]['track'] = track
            sink[-1]['cond'] = cond
    return canvas, cmds, items, fade


def ease_time(t, ease_in, ease_out, ease_scale):
    """SceneManifest::EaseTime, line for line."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    a = 0.0 if ease_in <= -100.0 else 1.0 + ease_in / 100.0
    b = 0.0 if ease_out >= 100.0 else 1.0 - ease_out / 100.0
    s = min(max(ease_scale / 100.0, 0.0), 1.0)
    total = s * (a + 1.0) * 0.5 + (1.0 - s) * (b + 1.0) * 0.5
    if total <= 0.0:
        return t
    if t <= s:
        covered = a * t + (1.0 - a) * t * t / (2.0 * s)
    else:
        rest = 1.0 - s
        covered = s * (a + 1.0) * 0.5 + b * (t - s) + (1.0 - b) * (rest * rest - (1.0 - t) ** 2) / (2.0 * rest)
    return covered / total


def evaluate_track(track, frame):
    """SceneManifest::Evaluate: the last key at or before `frame`, tweened
    toward the next if that key says to. Show steps; it is never tweened."""
    keys = track['keys']
    if not keys:
        return dict(show=True, dx=0.0, dy=0.0, sx=1.0, sy=1.0, alpha=1.0)
    at = 0
    while at + 1 < len(keys) and keys[at + 1]['frame'] <= frame:
        at += 1
    a, b, f = keys[at], keys[at], 0.0
    if a['interp'] != 'none' and at + 1 < len(keys) and frame > a['frame']:
        b = keys[at + 1]
        t = (frame - a['frame']) / (b['frame'] - a['frame'])
        f = ease_time(t, *a['ease']) if a['interp'] == 'ease' else t
    out = {k: a[k] + (b[k] - a[k]) * f for k in ('dx', 'dy', 'sx', 'sy', 'alpha')}
    out['show'] = a['show']
    return out


def apply_tracks(cmds, tracks, frame):
    """What Scene::DrawCommands does to a tracked command: scale about the
    track's box origin, move, fade, mirror on a negative scale, or drop."""
    states = [evaluate_track(t, frame) for t in tracks]
    out = []
    for c in cmds:
        if c.get('track') is None:
            out.append(c)
            continue
        s, (bx, by, _bw, _bh) = states[c['track']], tracks[c['track']]['box']
        if not s['show'] or s['alpha'] <= 0.0:
            continue
        c = dict(c, x=bx + (c['x'] - bx) * s['sx'] + s['dx'], y=by + (c['y'] - by) * s['sy'] + s['dy'],
                 w=c['w'] * s['sx'], h=c['h'] * s['sy'])
        if c['w'] < 0:
            c.update(x=c['x'] + c['w'], w=-c['w'])
            if c['op'] == 'GRAD':
                c['angle'] = 180.0 - c['angle']
        if c['h'] < 0:
            c.update(y=c['y'] + c['h'], h=-c['h'])
            if c['op'] == 'GRAD':
                c['angle'] = -c['angle']
        if c['op'] == 'GRAD':
            c['stops'] = [(pos, (col[0] * s['alpha'],) + tuple(col[1:])) for pos, col in c['stops']]
        else:
            c['color'] = (c['color'][0] * s['alpha'],) + tuple(c['color'][1:])
        out.append(c)
    return out


def _blend(dst, x0, y0, src_rgb, src_a):
    """Straight-alpha 'over', matching the app's standard src-alpha /
    inv-src-alpha blend state. src_rgb is HxWx3 float 0..255, src_a is HxW
    float 0..1; clipped against dst's bounds."""
    h, w = src_a.shape
    H, W, _ = dst.shape
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x0 + w), min(H, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    cw, ch = dx1 - dx0, dy1 - dy0
    a = src_a[sy0:sy0 + ch, sx0:sx0 + cw][..., None]
    s = src_rgb[sy0:sy0 + ch, sx0:sx0 + cw]
    d = dst[dy0:dy1, dx0:dx1]
    dst[dy0:dy1, dx0:dx1] = s * a + d * (1.0 - a)


def _uv(w, h):
    """Pixel-center uv grid across a w x h quad, as the rasterizer would
    interpolate it."""
    u = (np.arange(w) + 0.5) / max(w, 1)
    v = (np.arange(h) + 0.5) / max(h, 1)
    return np.meshgrid(u, v)


def _ellipse_coverage(uu, vv):
    """Shaders.h EllipseCoverage, with fwidth approximated by the quad's own
    pixel step (the app computes it from screen-space derivatives; at 1:1
    canvas scale that is the same thing)."""
    cx = (uu - 0.5) * 2.0
    cy = (vv - 0.5) * 2.0
    dist = np.sqrt(cx * cx + cy * cy)
    h, w = uu.shape
    aa = max(2.0 / max(w, 1), 2.0 / max(h, 1), 1e-4)
    t = np.clip((dist - (1.0 - aa)) / (2.0 * aa), 0.0, 1.0)
    return 1.0 - (t * t * (3.0 - 2.0 * t))


def _grad_color(t, stops):
    """PSGradient's stop lookup: clamp below the first stop, bracket-lerp
    between neighbours, fall through to the last stop's color above it."""
    out = np.empty(t.shape + (4,), dtype=np.float64)
    out[:] = stops[-1][1]
    out[t <= stops[0][0]] = stops[0][1]
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        m = (t >= p0) & (t <= p1)
        if not m.any():
            continue
        span = max(p1 - p0, 1e-4)
        lt = ((t[m] - p0) / span)[:, None]
        out[m] = np.array(c0) * (1 - lt) + np.array(c1) * lt
    return out


class Preview:
    def __init__(self, canvas, assets_dir, font_path):
        w, h = int(round(canvas[0])), int(round(canvas[1]))
        self.W, self.H = w, h
        self.buf = np.zeros((h, w, 3), dtype=np.float64)
        self.assets_dir = assets_dir
        self.font_path = font_path
        self._font_cache = {}
        self._img_cache = {}
        self.missing = []

    # --- text ---------------------------------------------------------
    def _font_path_for(self, name):
        if not name:
            return self.font_path
        return os.path.join(os.path.dirname(os.path.abspath(self.font_path)), name)

    def _font(self, size, name=''):
        key = int(round(size * 4))
        key = (key, name)
        if key not in self._font_cache:
            path = self._font_path_for(name)
            if not os.path.isfile(path):
                path = self.font_path       # missing face -> default, as Scene::FontFor does
            self._font_cache[key] = ImageFont.truetype(path, size=size)
        return self._font_cache[key]

    def draw_text(self, cmd):
        a, r, g, b = cmd['color']
        if a <= 0:
            return
        font = self._font(cmd['size'], cmd.get('font', ''))
        asc, desc = font.getmetrics()
        text = cmd['text']
        bbox = font.getbbox(text)
        if bbox is None:
            return

        # QuadRenderer::DrawTextAligned: the manifest's x/y/w/h is the text's
        # BOX, and halign/valign say where the run sits inside it. Advance
        # width (not ink bbox) is what gets aligned, matching the renderer,
        # which aligns using FontAtlas advances.
        adv = font.getlength(text)
        pen_x = cmd['x']
        if cmd['halign'] == 1:
            pen_x += (cmd['w'] - adv) / 2.0
        elif cmd['halign'] == 2:
            pen_x += cmd['w'] - adv
        base_y = cmd['y'] + asc
        if cmd['valign'] == 1:
            base_y = cmd['y'] + (cmd['h'] - (asc + desc)) / 2.0 + asc
        elif cmd['valign'] == 2:
            base_y = cmd['y'] + cmd['h'] - desc

        pad = 4
        tw = max(1, bbox[2] - bbox[0] + 2 * pad)
        th = max(1, bbox[3] - bbox[1] + 2 * pad)
        tile = Image.new('L', (tw, th), 0)
        ImageDraw.Draw(tile).text((pad - bbox[0], pad - bbox[1]), text, fill=255, font=font)
        alpha = np.asarray(tile, dtype=np.float64) / 255.0 * (a / 255.0)
        rgb = np.empty(alpha.shape + (3,))
        rgb[:] = (r, g, b)
        # PIL's default text anchor is "la" (left / ascender-top), and getbbox
        # is relative to that same origin -- so the origin we want is the
        # baseline shifted up by the ascent, not the baseline itself.
        x0 = int(round(pen_x + bbox[0] - pad))
        y0 = int(round(base_y - asc + bbox[1] - pad))
        if cmd.get('turn'):
            # TEXTV: laid out upright, then turned a quarter clockwise about
            # the box origin -- QuadRenderer::DrawTextAligned's quarterTurn.
            x0, y0 = int(round(cmd['x'] - ((y0 - cmd['y']) + th))), int(round(cmd['y'] + (x0 - cmd['x'])))
            alpha = np.rot90(alpha, k=-1)
            rgb = np.empty(alpha.shape + (3,))
            rgb[:] = (r, g, b)
        _blend(self.buf, x0, y0, rgb, alpha)

    # --- shapes -------------------------------------------------------
    def draw_rect(self, cmd):
        a, r, g, b = cmd['color']
        w, h = int(round(cmd['w'])), int(round(cmd['h']))
        if w <= 0 or h <= 0 or a <= 0:
            return
        alpha = np.full((h, w), a / 255.0)
        if cmd['ellipse']:
            alpha = alpha * _ellipse_coverage(*_uv(w, h))
        rgb = np.empty((h, w, 3))
        rgb[:] = (r, g, b)
        _blend(self.buf, int(round(cmd['x'])), int(round(cmd['y'])), rgb, alpha)

    def draw_grad(self, cmd):
        w, h = int(round(cmd['w'])), int(round(cmd['h']))
        if w <= 0 or h <= 0 or not cmd['stops']:
            return
        uu, vv = _uv(w, h)
        if cmd['radial']:
            t = np.clip(np.sqrt((uu - 0.5) ** 2 + (vv - 0.5) ** 2) * 2.0, 0, 1)
        else:
            # PSGradient's linear axis: XUI's un-rotated axis is left-to-right,
            # rotated by the fill's own Rotation (degrees, clockwise in the
            # +y-down pixel space the manifest uses).
            th = np.deg2rad(cmd.get('angle', 0.0) or 0.0)
            ax, ay = np.cos(th), -np.sin(th)
            t = np.clip((uu - 0.5) * ax + (vv - 0.5) * ay + 0.5, 0, 1)
        c = _grad_color(t, cmd['stops'])
        alpha = c[..., 0] / 255.0
        if cmd['ellipse']:
            alpha = alpha * _ellipse_coverage(uu, vv)
        _blend(self.buf, int(round(cmd['x'])), int(round(cmd['y'])), c[..., 1:4], alpha)

    def draw_image(self, cmd):
        path = os.path.join(self.assets_dir, cmd['file'])
        if path not in self._img_cache:
            self._img_cache[path] = Image.open(path).convert('RGBA') if os.path.isfile(path) else None
        im = self._img_cache[path]
        if im is None:
            self.missing.append(cmd['file'])
            return
        w, h = int(round(cmd['w'])), int(round(cmd['h']))
        if w <= 0 or h <= 0:
            return
        im = im.resize((w, h), Image.LANCZOS)
        arr = np.asarray(im, dtype=np.float64)
        a, r, g, b = cmd['color']
        alpha = arr[..., 3] / 255.0 * (a / 255.0)
        rgb = arr[..., 0:3] * np.array([r, g, b]) / 255.0
        _blend(self.buf, int(round(cmd['x'])), int(round(cmd['y'])), rgb, alpha)

    def run(self, cmds):
        for c in cmds:
            {'RECT': self.draw_rect, 'GRAD': self.draw_grad,
             'IMAGE': self.draw_image, 'TEXT': self.draw_text}[c['op']](c)
        return Image.fromarray(np.clip(self.buf, 0, 255).astype(np.uint8))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('manifest')
    ap.add_argument('out_png')
    ap.add_argument('--font', default=None)
    ap.add_argument('--assets-dir', default=None)
    ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--scroll', type=float, default=None,
                    help='camera x along the scrolling layer (default: the focused item\'s viewX). '
                         'Set it explicitly to render a frame mid-slide.')
    ap.add_argument('--frame', default='0',
                    help='where the timeline stands: a frame number, or the name of a MARK')
    ap.add_argument('--var', action='append', default=[], metavar='NAME=VALUE',
                    help='an IF variable, or an item id for its WHEN value (repeatable)')
    ap.add_argument('--focus', default=None,
                    help='id of the ITEM to render in its focus state (default: the first item, '
                         'matching what the app shows when the scene is entered). Pass "" for none.')
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    font = args.font or os.path.join(here, '..', 'assets', 'fonts', 'xenonclatin_full.ttf')
    assets = args.assets_dir or os.path.join(os.path.dirname(os.path.abspath(args.manifest)), 'assets')

    canvas, cmds, items, fade = parse_manifest(args.manifest)
    frame = fade['marks'][args.frame] if args.frame in fade['marks'] else float(args.frame)
    # A variable nobody set is its item's own starting value, or 0.
    variables = {it['id']: it.get('initial', 0) for it in items}
    variables.update((name, int(value)) for name, _, value in (v.partition('=') for v in args.var))

    def chosen(commands):
        return [c for c in commands if c['cond'] is None or variables.get(c['cond'][0], 0) == c['cond'][1]]

    focus = args.focus
    if focus is None:
        focus = items[0]['id'] if items else ''
    # Unfocused items in file order, then the focused one LAST -- same
    # ordering rule as Scene::Draw, and for the same two reasons: a focused
    # tile grows past its bounds, and the hub's focus block dims every other
    # page, which only works once they are all on screen.
    focused_item = next((it for it in items if it['id'] == focus and it['focus']), None)
    for it in items:
        if it is focused_item:
            continue
        cmds = cmds + it['normal']
    if focused_item:
        cmds = cmds + focused_item['focus']
    cmds = apply_tracks(chosen(cmds), fade['tracks'], frame)

    # The camera: normally parked on the focused item, but --scroll can put it
    # anywhere, which is how a mid-slide frame gets rendered.
    scroll = args.scroll
    if scroll is None:
        focused = next((it for it in items if it['id'] == focus), None)
        scroll = focused['view_x'] if focused else 0.0
    # Camera offset, then the fade -- Scene::DrawCommands does exactly this.
    def faded(c):
        if c.get('fixed') or fade['off_alpha'] >= 1.0:
            k = 1.0
        else:
            t = min(abs(c.get('anchor', 0.0) - scroll) / fade['span'], 1.0)
            k = 1.0 + (fade['off_alpha'] - 1.0) * t
        out = dict(c, x=c['x'] - (0.0 if c.get('fixed') else scroll))
        if k < 1.0:
            if out['op'] == 'GRAD':
                out['stops'] = [(pos, (col[0] * k,) + tuple(col[1:])) for pos, col in out['stops']]
            else:
                col = out['color']
                out['color'] = (col[0] * k,) + tuple(col[1:])
        return out
    cmds = [faded(c) for c in cmds]

    p = Preview(canvas, assets, font)
    img = p.run(cmds)
    if args.scale != 1.0:
        img = img.resize((int(img.width * args.scale), int(img.height * args.scale)), Image.LANCZOS)
    img.save(args.out_png)
    print(f'{args.out_png}: {img.width}x{img.height}, {len(cmds)} draws'
          + (f', MISSING IMAGES: {sorted(set(p.missing))}' if p.missing else ''))


if __name__ == '__main__':
    main()
