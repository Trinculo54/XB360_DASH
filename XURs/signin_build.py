"""
Cook the sign-in screen -- what the hub's "Sign In or Out" tile opens -- and
the small menu a signed-in profile puts up.

Its layouts are the sign-in pack's (archive_09), and what is taken from them
is exact:

  SignInTemplate.xur         1000x400, its scene 62 down: three slots 320
                             square at x = 0, 335, 670. With a slot's menu up
                             (EndModal1) that slot stands at x = 255 and the
                             other two shrink to 0.6 about (165, 160), at
                             x = 525 and 730, 40% opaque.
  ProfileSlotScene.xur       a profile: "SIGNED IN" in green at (109, 52),
                             the gamer picture at (110, 96), the gamertag at
                             (110, 147), an avatar to their left. Focused,
                             the text rises 20 and the avatar grows from 0.67
                             to 0.74.
  EmptyProfileSlotScene.xur  an action: an icon at (114, 87), a 24-point
                             header under it, a 14-point line under that; an
                             avatar picture and its shadow to the left, 0.9
                             of their size, about their feet at (67, 235).
                             Focused, the text grows to 1.09 about (49, 236)
                             and the avatar to 1.04.
  ModalMenu2Scene.xur        "Sign Out" and "Close", two small tiles 228 to
                             the left of the slot, at y = 45 and 160.
  strings.xus                "sign in or out", "Create Profile" / "Want to
                             make a new profile?", "Download Profile" /
                             "Already have an Xbox Live profile?".

What is NOT in any file, and is judgment here:

  - WHERE the template sits. It is centred, with its slots' top where the
    hub's tiles' is.
  - WHAT is in the slots: this console's one profile, then the two actions.
    That is what the three slots and the strings suggest for one profile.
  - The title. "sign in or out" is set where a hub page's name is, in the
    same face; the pack's other candidate, "Choose your profile", isn't shown.
  - The avatars. The profile's is a live 3D figure and the actions' picture
    isn't named; all three are the rip's silhouette (as on the hub's social
    page), at the size and place the action slot gives its picture.
  - The action slots' icons (ico_32x_Add, ico_32x_Download: by name).
  - Not drawn: the ring of light beside "SIGNED IN" (no image of it in the
    pack), the Xbox LIVE logo (this is not a Live profile), the gamerscore
    and where the profile is stored.

"Sign Out" signs out, and A on the profile signs it in again: each is a scene
that plays for a moment and sets the app's `signedout` as it ends (SIGNING).
While that is 1 there is no "SIGNED IN" here, no gamertag on the hub and no
gamer picture anywhere, and A on the profile signs in rather than opening its
menu (ACTIONIF). It lasts until the app closes; the guide knows nothing of it.

Create Profile and Download Profile ask the pack's own question (a message
box: guide_build.py's DIALOGS) and Yes goes on -- to the keyboard for the new
profile's name and then "Save Profile", or to the Download Profile page
(guide_build.py: save_profile); with nobody signed in there is no question.
The menu is its own scene, reached by the ordinary
scene change rather than the template's slide.

SignupLanding is the page that asking for Xbox Live gets a profile with no
account -- the social page's Friends, Activity Feed, Avatar Store and "Join
the fun" all ask (HubChannel.lub and UpsellSlotScene.lub: Demand(user,
Silver)), and the hub app answers by showing signup_landing.xml (the hub
pack, archive_12). From the rip:

  signup_landing.xml          the page: background common://oobe_bg.jpg, an
                              explanation, then three tiles -- Sign Up
                              (ico_64x_addEditText, the Live sign-up app),
                              Sign In (ico_64x_SignIn, this sign-in screen)
                              and No Thanks (ico_64x_cancel, back).
  TemplateSignupLandingHD.xur 818 wide, as the hub's home page: the
                              explanation's slot 640x398 at (0, 0), the tiles
                              148 square at x = 670, y = 0, 152, 304.
  strings.xus                 the title, "Xbox Live"; the explanation, an HTML
                              string -- a 30 "Segoe Light" heading and two
                              16px paragraphs, #EBEBEB -- filled with "You
                              need an Xbox Live account to continue" and the
                              two lines under it.

Judgment: how that HTML flows (both sizes taken as XUI point sizes, a body
line's gap between its blocks); that the title stands where a hub page's name
does; and that with nobody signed in the same tiles send you to sign in
first. Sign Up does nothing: the sign-up app is downloaded from Xbox Live.

    python3 signin_build.py --search-dir ../DASH/dash_bin_embedded_assets --search-dir ../XZP
"""
import argparse
import os
import shutil

from hub_build import (CANVAS, FOCUS_SCALE, GAMERPIC_IMAGES, PAGES, TILE_FOCUS_STOPS, TILE_LABEL_COLOR,
                       TILE_NORMAL_STOPS, TILE_TOP, TWIST_FONT, HubBuilder, inflate)
from xur_cook import HALIGN_LEFT, POINT_SIZE_TO_PIXELS, VALIGN_TOP, build_image_index

TITLE = 'sign in or out'
TEMPLATE_AT = ((CANVAS[0] - 1000.0) / 2.0, TILE_TOP - 62.0)
SLOT_TOP = 62.0
SLOT_PIVOT = (165.0, 160.0)
# Per slot: (x, scale, opacity), at rest and with the first slot's menu up.
REST = [(0.0, 1.0, 1.0), (335.0, 1.0, 1.0), (670.0, 1.0, 1.0)]
MODAL = [(255.0, 1.0, 1.0), (525.0, 0.6, 0.4), (730.0, 0.6, 0.4)]

# The avatar picture and its shadow (EmptyProfileSlotScene), and the point
# they both scale about -- their Position + Pivot, which is the same for both.
AVATAR = (-108.555, -151.666, 322.0, 550.0)
SHADOW = (-20.7777, 283.888, 142.222, 31.1111)
FEET = (67.0, 235.0)
AVATAR_IMAGE = ('AvatarSilhouette.png', 436.0, 730.0)   # ...and its own size, to keep its shape in that box
ACTION_AVATAR = (0.9, 1.04)                     # at rest, focused
PROFILE_AVATAR = (0.9, 0.9 * 0.74 / 0.67)       # the same picture, growing as the profile's own avatar does
TEXT_PIVOT, TEXT_FOCUS = (49.0, 236.0), 1.09    # an action's text: Elements' Position + Pivot, and its focus Scale
RISE = 20.0                                     # a focused profile's text

GREEN = {'a': 255, 'r': 118, 'g': 185, 'b': 0}
WHITE = TILE_LABEL_COLOR

# (`out`: what A does with nobody signed in -- there is no one to sign out, so
# no question first: straight on to what the question's Yes leads to.)
ACTIONS = [dict(id='create', icon='ico_32x_Add.png', header='Create Profile',
                says='Want to make a new profile?', action='GUIDE:GuideCreateProfile', out='GUIDE:TYPE:newprofile'),
           dict(id='download', icon='ico_32x_Download.png', header='Download Profile',
                says='Already have an Xbox Live profile?', action='GUIDE:GuideDownloadProfile',
                out='GUIDE:GuideDownloadIntro')]
# The menu: the layout's own captions, icons and places (relative to its slot).
MENU = [dict(id='signout', label='Sign Out', icon='ico_32x_SignOut.png', rect=(-228.0, 45.0, 170.0, 110.0),
             action='GOTO:SigningOut'),
        dict(id='close', label='Close', icon='ico_32x_cancel.png', rect=(-228.0, 160.0, 170.0, 110.0),
             action='BACK')]

# Signing in and out each take a moment, as scenes that play (MARK Scene.End),
# set the app's `signedout` as they end (SETS) and give way to the sign-in
# screen (AFTER). scene -> (what `signedout` becomes, the profile's green line
# as (text, from frame, to frame), the frame it ends on).
#   Signing in is ProfileSlotScene's own: its SignedInGroup shows "SIGNING IN",
#   then with one, two and three dots, at frames 15, 31, 46 and 61, and
#   "SIGNED IN" from 78 to the end of that timeline at 90.
#   Signing out has no such timeline. The pack's "Signing out %s..." is put in
#   the same place for a second: where the console says it isn't known.
SIGNING = {
    'SigningIn': (0, [('SIGNING IN', 15, 31), ('SIGNING IN .', 31, 46), ('SIGNING IN ..', 46, 61),
                      ('SIGNING IN ...', 61, 78), ('SIGNED IN', 78, None)], 90),
    'SigningOut': (1, [('Signing out {gamertag}...', 0, None)], 60),
}


class Slot:
    """One slot of the template: draws things given in the slot's own units."""

    def __init__(self, b, x, scale=1.0, opacity=1.0):
        self.b, self.scale, self.alpha = b, scale, int(round(255 * opacity))
        self.ox, self.oy = TEMPLATE_AT[0] + x, TEMPLATE_AT[1] + SLOT_TOP

    def at(self, x, y, w=0.0, h=0.0, about=None, by=1.0):
        """A rect of the slot, on the canvas -- after its own group's scale `by` about `about`."""
        if about:
            x, y, w, h = about[0] + (x - about[0]) * by, about[1] + (y - about[1]) * by, w * by, h * by
        px, py = SLOT_PIVOT
        return (self.ox + px + (x - px) * self.scale, self.oy + py + (y - py) * self.scale,
                w * self.scale, h * self.scale)

    def text(self, s, x, y, w, h, points, color, about=None, by=1.0):
        self.b.text(s, *self.at(x, y, w, h, about, by), points * by * self.scale, color, HALIGN_LEFT, VALIGN_TOP,
                    self.alpha)

    def lines(self, s, x, y, w, line_h, points, about=None, by=1.0):
        """Word-wrapped, top down."""
        line, n = '', 0
        for word in s.split() + [None]:
            trial = word if not line else f'{line} {word}'
            if word is None or (line and self.b.measure(trial, points) > w):
                self.text(line, x, y + n * line_h, w, line_h, points, WHITE, about, by)
                line, n = word, n + 1
            else:
                line = trial

    def figure(self, by):
        """The avatar and its shadow. The picture keeps its shape in its box."""
        name, iw, ih = AVATAR_IMAGE
        x, y, w, h = AVATAR
        fit = min(w / iw, h / ih)
        self.b.image('AvatarShadow.png', *self.at(*SHADOW, about=FEET, by=by), self.alpha)
        self.b.image(name, *self.at(x + (w - iw * fit) / 2.0, y + (h - ih * fit) / 2.0, iw * fit, ih * fit,
                                    about=FEET, by=by), self.alpha)

    def status(self, s, rise):
        """The green line over a profile: "SIGNED IN"."""
        self.text(s, 109.0, 52.0 - rise, 250.0, 40.0, 14.0, GREEN)

    def profile(self, gamertag, focused=False, risen=None, signed=True):
        """`signed`: with "SIGNED IN" over it while someone is (the app's `signedout` is 0).
        The gamer picture goes by itself when nobody is: main.cpp gives none then."""
        rise = RISE if (focused if risen is None else risen) else 0.0
        self.figure(PROFILE_AVATAR[focused])
        if signed:
            self.b.emit('IF', 'signedout', 0)
            self.status('SIGNED IN', rise)
            self.b.lines.append('ENDIF')
        for n, image in enumerate(GAMERPIC_IMAGES):
            self.b.emit('IF', 'gamerpic', n)
            self.b.image(image, *self.at(110.0, 96.0 - rise, 32.0, 32.0), self.alpha)
            self.b.lines.append('ENDIF')
        self.text(gamertag, 110.0, 147.0 - rise, 230.0, 48.0, 16.0, WHITE)

    def action(self, action, focused=False):
        by = TEXT_FOCUS if focused else 1.0
        self.figure(ACTION_AVATAR[focused])
        self.b.image(action['icon'], *self.at(114.0, 87.0, 32.0, 32.0, TEXT_PIVOT, by), self.alpha, WHITE)
        # The header's row is 77 high for its two lines; the line under it starts at 118.
        self.lines(action['header'], 114.0, 119.0, 182.0, 38.5, 24.0, TEXT_PIVOT, by)
        self.lines(action['says'], 114.0, 205.0, 182.0, 14.0 * POINT_SIZE_TO_PIXELS * 1.34, 14.0, TEXT_PIVOT, by)


def chrome(image_index, gamertag, font_path, what, title=TITLE):
    b = HubBuilder(image_index, font_path)
    b.twist_names = [title]
    b.comment(what)
    b.comment('See signin_build.py.')
    b.emit('CANVAS', *CANVAS)
    return b


def furniture(b, gamertag, background=None):
    b.lines.append('LAYER fixed')
    if background:
        b.captured(background, 0.0, 0.0, *CANVAS)
    else:
        b.backdrop()
    b.gamertag(gamertag)
    b.twist(b.twist_names[0])
    b.legend([('A', 'Select'), ('B', 'Back')])


def build_signin(image_index, gamertag, font_path):
    b = chrome(image_index, gamertag, font_path, 'the sign-in screen: a profile and two actions, in SignInTemplate.xur\'s slots.')
    b.emit('DEFAULTFOCUS', 'profile')
    furniture(b, gamertag)
    ids = ['profile'] + [a['id'] for a in ACTIONS]
    for n, (x, scale, opacity) in enumerate(REST):
        left, right = (ids[n - 1] if n else '-'), (ids[n + 1] if n + 1 < len(ids) else '-')
        action = 'GOTO:SignInMenu' if n == 0 else ACTIONS[n - 1]['action']
        b.emit('ITEM', ids[n], '-', '-', left, right, action, 0.0)
        # With nobody signed in, A on the profile signs it in, and there is
        # no one to sign out before an action (ACTIONS' `out`).
        b.emit('ACTIONIF', 'signedout', 1, 'GOTO:SigningIn' if n == 0 else ACTIONS[n - 1]['out'])
        for focused in (False, True):
            b.lines.append('STATE focus' if focused else 'STATE normal')
            b.lines.append('LAYER fixed')
            slot = Slot(b, x, scale, opacity)
            if n == 0:
                slot.profile(gamertag, focused)
            else:
                slot.action(ACTIONS[n - 1], focused)
        b.lines.append('ENDITEM')
    return b


def build_menu(image_index, gamertag, font_path):
    b = chrome(image_index, gamertag, font_path, 'the sign-in screen with the profile\'s menu up (SignInTemplate.xur: EndModal1).')
    b.emit('DEFAULTFOCUS', MENU[0]['id'])
    furniture(b, gamertag)
    # The profile, moved over and as focus left it (EndFocusToModal: the
    # avatar still large, the text back down); the actions small and faint.
    Slot(b, *MODAL[0]).profile(gamertag, focused=True, risen=False)
    for action, place in zip(ACTIONS, MODAL[1:]):
        Slot(b, *place).action(action)
    slot = Slot(b, *MODAL[0])
    for n, tile in enumerate(MENU):
        other = MENU[n - 1]['id']
        b.emit('ITEM', tile['id'], other, other, '-', '-', tile['action'], 0.0)
        rect = slot.at(*tile['rect'])
        b.lines.append('STATE normal')
        b.lines.append('LAYER fixed')
        b.tile(rect, tile, TILE_NORMAL_STOPS, 255, 255)
        b.lines.append('STATE focus')
        b.lines.append('LAYER fixed')
        b.tile(inflate(rect, FOCUS_SCALE), tile, TILE_FOCUS_STOPS, 255, 255, angle=-45.0, scale=FOCUS_SCALE)
        b.lines.append('ENDITEM')
    return b


def build_signing(name):
    def build(image_index, gamertag, font_path):
        becomes, steps, ends = SIGNING[name]
        b = chrome(image_index, gamertag, font_path, f'the sign-in screen while its profile is {name[:7].lower()} '
                                                     f'{name[7:].lower()}: it plays, then sets signedout to {becomes}.')
        furniture(b, gamertag)
        slot = Slot(b, *REST[0])
        slot.profile(gamertag, focused=True, signed=False)      # as it was when A was pressed on it
        for action, place in zip(ACTIONS, REST[1:]):
            Slot(b, *place).action(action)
        for n, (text, start, end) in enumerate(steps):
            b.lines.append(f'TRACK status{n} 0 0 0 0')
            for frame, shown in ([(0, 0)] if start else []) + [(start, 1)] + ([(end, 0)] if end else []):
                b.lines.append(f'KEY {frame} none 0 0 0 {shown} 0 0 1 1 1')
            slot.status(text.format(gamertag=gamertag), RISE)
            b.lines.append('ENDTRACK')
        b.emit('MARK', 'Scene.End', ends)
        b.emit('SETS', 'signedout', becomes)
        b.emit('AFTER', 'SignIn')
        return b
    return build


# The sign-up landing page (see the top of this file). An 818-wide channel is
# where the hub's home page, also 818, has its own.
LANDING_AT = (PAGES[0]['column_x'], TILE_TOP)
LANDING_SAYS = [(30.0, TWIST_FONT, 'You need an Xbox Live account to continue'),
                (16.0, None, "It's easy and free to sign up for an Xbox Live account."),
                (16.0, None, 'Already have an account? Sign in now.')]
LANDING_TILES = [dict(id='signup', label='Sign Up', icon='ico_64x_addEditText.png', action='-'),
                 dict(id='signin', label='Sign In', icon='ico_64x_SignIn.png', action='GOTO:SignIn'),
                 dict(id='nothanks', label='No Thanks', icon='ico_64x_cancel.png', action='BACK')]


def build_landing(image_index, gamertag, font_path):
    b = chrome(image_index, gamertag, font_path, 'what asking for Xbox Live gets a profile with no account: '
               'signup_landing.xml in TemplateSignupLandingHD.xur\'s slots.', title='Xbox Live')
    b.emit('DEFAULTFOCUS', LANDING_TILES[0]['id'])
    furniture(b, gamertag, background='oobe_bg.jpg')
    x, y = LANDING_AT
    gap = 16.0 * POINT_SIZE_TO_PIXELS * 1.34            # between the HTML's blocks: a body line
    for points, font, says in LANDING_SAYS:
        line_h, line = points * POINT_SIZE_TO_PIXELS * 1.34, ''
        b.font(font)
        for word in says.split() + [None]:              # word-wrapped to the slot's 640, top down
            trial = word if not line else f'{line} {word}'
            if word is None or (line and b.measure(trial, points, font) > 640.0):
                b.text(line, x, y, 640.0, line_h, points, WHITE, HALIGN_LEFT, VALIGN_TOP)
                line, y = word, y + line_h
            else:
                line = trial
        y += gap
    b.font()
    ids = [t['id'] for t in LANDING_TILES]
    for n, tile in enumerate(LANDING_TILES):
        b.emit('ITEM', tile['id'], ids[n - 1] if n else '-', ids[n + 1] if n + 1 < len(ids) else '-', '-', '-',
               tile['action'], 0.0)
        rect = (LANDING_AT[0] + 670.0, LANDING_AT[1] + 152.0 * n, 148.0, 148.0)
        b.lines.append('STATE normal')
        b.lines.append('LAYER fixed')
        b.tile(rect, tile, TILE_NORMAL_STOPS, 255, 255)
        b.lines.append('STATE focus')
        b.lines.append('LAYER fixed')
        b.tile(inflate(rect, FOCUS_SCALE), tile, TILE_FOCUS_STOPS, 255, 255, angle=-45.0, scale=FOCUS_SCALE)
        b.lines.append('ENDITEM')
    return b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--search-dir', action='append', default=[], required=True)
    ap.add_argument('--out-dir', default='../assets/scenes')
    ap.add_argument('--font', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   '..', 'assets', 'fonts', 'xenonclatin_full.ttf'))
    ap.add_argument('--gamertag', default='Trinculo54', help='as hub_build.py\'s: the profile is the hub\'s')
    args = ap.parse_args()

    index = build_image_index(args.search_dir)
    # ...which only looks for PNGs, and the landing page's background is a JPEG (as hub_build.py's promo is).
    for d in args.search_dir:
        for dirpath, _dirnames, filenames in os.walk(d):
            for fn in filenames:
                if fn.lower().endswith('.jpg'):
                    index.setdefault(fn, os.path.join(dirpath, fn))
    for name, build in ([('SignIn', build_signin), ('SignInMenu', build_menu), ('SignupLanding', build_landing)]
                        + [(n, build_signing(n)) for n in SIGNING]):
        b = build(index, args.gamertag, args.font)
        assets_dir = os.path.join(args.out_dir, name, 'assets')
        os.makedirs(assets_dir, exist_ok=True)
        with open(os.path.join(args.out_dir, name, 'manifest.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(b.lines) + '\n')
        for src, rel in b.used_images.items():
            shutil.copyfile(src, os.path.join(assets_dir, rel))
        for baked, image in b.baked_images.items():
            image.save(os.path.join(assets_dir, baked))
        print(f'{name}: {len(b.lines)} lines, {len(b.used_images)} images')
        for w in b.warnings:
            print(f'   warning: {w}')


if __name__ == '__main__':
    main()
