"""
Build the Xbox Guide -- the blade panel the Guide button opens -- as cooked
scenes under assets/guide, for src/Guide.cpp to stack and animate.

The guide is not in dash.xex. Its layouts are hud.xex's `hud` pack and its
templates are huduiskin.xex's `skin` pack, both flash files inside the system
update; XZP/xex_resources.py unpacks them.

WHAT GETS WRITTEN, AND FROM WHAT

  GuideFrame      hudbkgnd.xur (xam.xex), the frame every guide page sits in:
                  the plain centre blade, the clock, the gamerpic. Its
                  timeline is the guide opening and closing.
  GuideMain       GuideMain.xur's Tabscene: the side blades, their captions,
                  the selected tab's strip. Its timeline is the tab switches.
  GuideGames ...  one scene per tab holding only that tab's rows, from the
                  *TabSignedIn layouts. Home is the signed-in-to-Xbox-Live
                  one, the only tab with a Live layout of its own, because
                  that is where the Friends row is. Media's rows are inline
                  in GuideMain, plus MiniMediaPlayer.xur.
  Guide<Page>     the full-size pages rows open (PAGES): Preferences and its
                  children, Recent, the profile pages and three list pages
                  from other packs. Two lists are filled, from data the dump
                  does carry: the stock gamer pictures (TILES) and the game
                  defaults (DEFAULTS).
  GuideFriends    A RECONSTRUCTION (as is GuideMessages, the same page on
                  its second tab): the only scenes here that are not a real
                  layout cooked as found -- see FRIENDS.
  GuideTurnOff    a message box (DIALOGS).
  GuideNotify     the notification popup -- "Achievement unlocked" -- which is
                  not the guide's at all but is xam's too and wears the same
                  skin: notify.xur, one control whose visual carries the whole
                  animation. See cook_notify.

Things only the running guide knows are cooked as alternatives it picks from:
every legend the scenes ask for, the battery icons and the gamer pictures
under IF, and each checkbox / radio button / slider / spinner at every value
it can hold under WHEN.

HOW THE ANIMATION SURVIVES THE COOK

A .xur timeline keys an object's Position / Width / Scale / Opacity / Show at
numbered frames. Each animated object becomes a TRACK block: its commands
cooked once, fully visible, plus one KEY per keyframe saying how to move,
scale and fade them (see ManifestTrack in SceneManifest.h). The file's named
frames become MARKs, and the frames a sound is keyed on become '@' MARKs. So
nothing about the motion is decided here or in the app -- 1To2 slides the
blades the way the file says because the file's keys are what plays.

WHAT IS BAKED TO PNG, because the manifest format cannot say it

  XuiNineGrid     IMAGE has no source rect.
  ring of light   four rotated, clipped radial-gradient arcs.
  a right arrow   IMAGE cannot mirror; the skin's is its left one, Scale -1.
  the popup's pill, ring and quadrant light
                  figures whose gradient brush is itself moved, turned or
                  scaled, and figures mirrored -- none of which a
                  GRADIENT_RECT can say. See paint.

Nothing is measured off a capture except the text shadow offset (see
xur_cook.Cooker.cook_text) and nothing is guessed except DIM below.

    python3 guide_build.py --search-dir ../XZP --search-dir "../NEW_DASH/EXTRACTED XZP"
"""
import argparse
import copy
import json
import math
import os
import shutil
import tempfile

import numpy as np
from PIL import Image, ImageFont, ImageOps

import xur_extract
import xur_schema
from xur_cook import (FILLTYPE_RADIAL_GRADIENT, Cooker, POINT_SIZE_TO_PIXELS, anchor_rect, build_image_index,
                      evaluate_state, find_skin_files, load_visual_templates, resolve_image_path, text_alignment)

HERE = os.path.dirname(os.path.abspath(__file__))

TAB_SCENES = ['GuideGames', 'GuideHome', 'GuideMedia', 'GuideSettings']   # Tab1..Tab4
SETTLED = ['2To1End', '1To2End', '2To3End', '3To4End']                    # the frame each tab rests on
# (HomeTabSignedIn.xur is the same tab for a profile not on Xbox Live: Xbox
# Home, Connect to Xbox Live, Open Tray. Swap it in for the offline guide.)
TAB_CONTENT = ['GamesTabSignedIn.xur', 'HomeTabSignedInLive.xur', None, 'SettingsTabSignedIn.xur']
# The same four tabs with nobody signed in: hud.xex has a layout of each tab
# for that too (and one Media tab for both). They are cooked as four more
# scenes, linked to each other, and Guide.cpp shows that set while the
# dashboard's sign-in screen has signed the profile out.
TAB_SCENES_OUT = [name + 'Out' for name in TAB_SCENES]
TAB_CONTENT_OUT = ['GamesTabSignedOut.xur', 'HomeTabSignedOut.xur', None, 'SettingsTabSignedOut.xur']

# An empty scene the runtime loads another layout into.
HOSTED = {'scnMusic': 'MiniMediaPlayer.xur'}

# Full-size pages a row opens (its PressPath / MainScenePressPath), and the
# scene each is cooked as. A row naming a layout that isn't here does nothing.
PAGES = {
    'Options.xur': 'GuidePreferences',
    'OptionsOnline.xur': 'GuideOnlineStatus',
    'OptionsNotifications.xur': 'GuideNotifications',
    'OptionsVoice.xur': 'GuideVoice',
    'OptionsController.xur': 'GuideVibration',
    'QuickLaunch.xur': 'GuideRecent',
    # gamerprofile.xex's `gp` pack and minimediaplayer.xex's `media` pack --
    # two more flash files, unpacked the same way. The first three are lists
    # the runtime fills, each with a label for when there is nothing to fill
    # it with. (Achievements and Awards open their per-GAME lists; 802_ and
    # 837_AvatarAwards are the grids one game's entries then open into, and
    # with no games there is no way to reach them.)
    '820_GameShowcaseMe.xur': 'GuideAchievements',
    '837_AvatarAwardGamesMe.xur': 'GuideAwards',
    '579_SelectMediaSource.xur': 'GuideMusicSources',
    'GamerCardScene.xur': 'GuideProfile',
    '808_editProfile.xur': 'GuideEditProfile',
    '803_SignInPreferences.xur': 'GuideSignInPreferences',
    '807_EditGamerTile.xur': 'GuideGamerPicture',
    '809_ChangeGamerTile.xur': 'GuideChangeGamerPicture',       # its grid is TILES
    'ChangePersonalTile.xur': 'GuideChangePersonalPicture',     # its grid stays empty: camera pictures
    '830_GamerPreferences.xur': 'GuideGameDefaults',            # its list is DEFAULTS
}

# The gamer pictures every console has: shrdres ships thirteen, named by the
# dashboard's own title id. They are what fills Change Gamer Picture's grid,
# and wherever the profile's picture is drawn all thirteen are cooked under
# IF gamerpic <n>; a tile's action, PIC:<n>, is Guide.cpp's "wear this one".
TILES = [f'64_fffe07d10002{n:04x}0001{n:04x}.png' for n in range(13)]

# Game Defaults. 830_GamerPreferences is a list of categories, each opening
# 832_PreferenceCategory -- up to eight spinners, the runtime showing as many
# as the category has settings. Every caption is in gp's string table
# (GamerProfile_Custom.xus) and is taken from it by number, so the words are
# the console's. What the table cannot say is ORDER -- it is sorted by string
# id -- so categories, settings and values are in the order of the profile
# settings they store (XPROFILE_GAMER_DIFFICULTY ...), and each starts on that
# setting's default. Both are from the XDK's headers, not from this dump.
#
#   category: (its description, [(setting, [values], the default's index)])
DEFAULTS = {
    53: (12, [(59, [92, 94, 93], 1),                 # General: Difficulty: Easy / Normal / Hard
              (58, [90, 91, 89], 1),                 #   Controller Sensitivity: Low / Medium / High
              (60, [99, 95, 105, 106, 100, 101, 103, 102, 96, 98, 97, 104], 0),    # Primary Color
              (61, [99, 95, 105, 106, 100, 101, 103, 102, 96, 98, 97, 104], 0)]),  # Secondary Color
    52: (11, [(66, [1, 0], 0),                       # Action: Y-Axis: Normal / Inverted
              (55, [37, 81], 1),                     #   Auto-Aim: Off / On
              (56, [37, 81], 0),                     #   Auto-Center
              (57, [87, 88], 0)]),                   #   Movement: Left Stick / Right Stick
    54: (13, [(65, [113, 114], 0),                   # Racing: Transmission: Automatic / Manual
              (64, [109, 111, 110], 0),              #   Camera Angle: Back / Front / Inside
              (63, [112, 108], 0),                   #   Brake Control: Trigger / Button
              (62, [112, 107], 0)]),                 #   Accelerator Control
}
DEFAULTS_HEADER = 68        # "%s Defaults"

# What a page's panel on the right says about the focused row: gp strings, by
# row id. The runtime sets it as focus moves (MetaPanelScene); here it is
# part of each row's focused look, so it crossfades with the highlight.
META = {'changeGamerTileButton': 6, 'changePersonalTileButton': 7, 'downloadTilesButton': 26,
        'capturePictureButton': 5}

# RECONSTRUCTION. The Friends page is Guide.Friends.xex's `community` scene,
# and that module is in neither the system update nor the flash: all that
# survives of it is its string tables, in five languages, none of them English
# (XZP/L.Guide.Friends.xex.friends). So this is NOT its layout. It is
# QuickLaunch.xur -- "Recent", the guide's other full-size page that is a tab
# scene -- re-captioned: everything about where things sit and how the tabs
# switch is Recent's, standing in for a layout nobody has.
#
#   what is the module's own   the tab captions (community.xus ids 25, 22, 19)
#                              and the legend (id 52), translated back from
#                              the Dutch and Swedish
#   what is a guess            that there are these three tabs in this order
#                              (the Swedish table has a fourth, Party), the
#                              header, and which icon each tab wears -- they
#                              are shrdres's friend / mail / buddies icons
#   what is left out           the lists. Nobody is on the friends list, and
#                              the module has no "nothing here" text to show
#                              in their place, so each tab is empty.
#
# Cooked twice: GuideFriends, and GuideMessages, the same page opening on its
# second tab, for the Home tab's Messages row. That the row opens this page
# at all is a guess too -- Messages being one of the community scene's tabs
# is the only evidence. (Two scenes because GOTO has no way to name a focus.)
#
# By node Id (labelNoGames is each tab's "nothing here" label).
FRIENDS = {
    'HUDScene': {'LegendA': 'Select', 'LegendB': 'Back'},
    'Header': {'Text': 'Friends'},
    'text_Tab1': {'Text': 'Friends'}, 'icon_Tab1': {'ImagePath': 'sharedres://ico_32x_Friend.png'},
    'text_Tab2': {'Text': 'Messages'}, 'icon_Tab2': {'ImagePath': 'sharedres://ico_32x_Mail.png'},
    'text_Tab3': {'Text': 'Players'}, 'icon_Tab3': {'ImagePath': 'sharedres://ico_32x_Buddies.png'},
    'labelNoGames': {'Text': ''},
}

# The profile, for the gamer card. The gamertag is the one the hub shows; the
# tallies are a profile with nothing unlocked, which is also what the
# Achievements page says. Edit to taste -- nothing else reads them.
PROFILE = {'gamertag': 'Trinculo54', 'games': '0', 'gamerscore': '0', 'achievements': '0'}

# Parts of the profile pages that belong to a profile on Xbox Live: each page
# authors a Live set of rows and an offline set in the same place and the
# runtime shows one. Also fitnessButton, which sits one row past the end of
# either group's own height -- there only once Kinect has exercise data.
#
# And two of Preferences' rows, an inference rather than something a file
# says: Family Timer, whose page (OptionsPlayTimer.xur) has no way to show a
# timer that isn't set, and Word Registration, the Japanese input method's
# dictionary. Both are authored last in the list with a placeholder
# PressPath, which is how a row the code shows only sometimes is authored.
HIDDEN = {'meOnlineButtonGroup', 'onlineTextGroup', 'onlineButtonGroup', 'onlineDatacenterButtonGroup',
          'onlinePersonalCardGroup', 'scnBox_motto', 'labMotto', 'fitnessButton',
          'btnPlayTimer', 'btnWordRegister'}

# The label a list page shows in place of an empty list. Hidden as authored;
# the runtime shows it when the list comes back with nothing.
EMPTY_LABELS = {'labEmpty', 'labelNoGames'}

# Message boxes. These are not layouts of their own: xam builds one from the
# skin's XuiMessageBox<buttons + 1> visual and whatever the caller passes, and
# hosts it in the frame's Error state. `text` then `title` are consecutive in
# hud.xex's Strings.xus, the order every other body/title pair in that table
# is in. The buttons are NOT in hud.xex's table, so they are xam's own stock
# captions ("OK", "Cancel" in XamStrings.xus) -- which two, and which has
# focus, is a guess; the body is a statement rather than a question, which is
# what OK / Cancel answers.
#
# Actions: BACK is B; DASH:@off is Guide.cpp's "the console turns off", which
# for this app means it closes.
DIALOGS = {
    'GuideTurnOff': {
        'title': 'Xbox Guide',
        'text': 'Turning off the console will also turn off all controllers.',
        'buttons': [('OK', 'DASH:@off'), ('Cancel', 'BACK')],
    },
    # Two questions the dashboard's settings pages ask before doing something
    # (their rows open these: "GUIDE:<name>"). The title and the question are
    # the page's own pack's -- the network pack's Strings.xus, and
    # dashCSettingsStrings.xus. "Yes" and "No" are xam's stock captions, and
    # that it is those two, in that order, with Yes focused, is a guess.
    # Nothing is restored or reset: either answer closes the box.
    'GuideRestoreNetwork': {
        'title': 'Restore Factory Defaults',
        'text': 'Are you sure you want to restore your network settings to the factory defaults? '
                'This will erase your current settings.',
        'buttons': [('Yes', 'BACK'), ('No', 'BACK')],
    },
    'GuideResetControls': {
        'title': 'Reset Content Controls',
        'text': 'This will reset your existing content controls to the defaults for your locale. '
                'Are you sure you want to reset content controls?',
        'buttons': [('Yes', 'BACK'), ('No', 'BACK')],
    },
    # ...and two the sign-in screen asks (signin_build.py), from its own pack's
    # strings.xus. Nothing signs out: either answer closes the box.
    'GuideCreateProfile': {
        'title': 'Create Profile',
        'text': 'Before creating a new profile, the current profile must be signed out. '
                'Sign out the current profile?',
        'buttons': [('Yes', 'TYPE:newprofile'), ('No', 'BACK')],       # on to its name: see save_profile
    },
    'GuideDownloadProfile': {
        'title': 'Download Profile',
        'text': 'Before downloading a profile, the current profile must be signed out. '
                'Sign out the current profile?',
        'buttons': [('Yes', 'GOTO:GuideDownloadIntro'), ('No', 'BACK')],
    },
}

# The notifications the popup is cooked showing, each under IF notify <n>
# for Guide.cpp to pick: (icon, text, what fills the text's blanks).
#
# The text is xam's own -- a number is a string of XamStrings.xus, where the
# popups' wordings sit among everything else xam says -- and the icons are
# the 32-pixel ones in xam's pack, beside notify.xur. What is NOT in any file
# is which icon goes with which wording: xam's code pairs them, by
# notification type. They are paired here by what the files are called
# (Party.png with the party wordings, signed_in.png with "signed in"...),
# which is plain for most and a judgment for three: a game invite wears the
# controller, a message wears shrdres's mail icon (xam's pack has none), and
# the two "please reconnect / reinsert" ones wear the device they name.
#
# Two wordings are not in the dump at all and are written from memory of the
# console, as literals: a friend coming online (the friends module supplies
# it, and that module isn't here) and the song now playing (which is just
# the track's name). Everything that fills a blank is made up, bar the
# gamertag; None is the profile's own.
NOTIFY_FRIEND = 'XboxFriend01'
NOTIFICATIONS = [
    ('Achievement.png', 0, ('20', 'Test Achievement')),        # Achievement unlocked / %sG - %s
    ('Achievement.png', 1, ('3', '45')),                       # Achievements unlocked / %s for %sG
    ('AvatarAward.png', 6, ()),                                # Avatar award unlocked
    ('ico_32x_Friend.png', '%s\\nis online', (NOTIFY_FRIEND,)),
    ('ico_32x_Friend.png', 62, (NOTIFY_FRIEND,)),              # %s / wants to be your friend
    ('Party.png', 131, (NOTIFY_FRIEND,)),                      # %s wants you to join / an Xbox Live Party
    ('Party.png', 132, (NOTIFY_FRIEND,)),                      # %s / has joined your Xbox Live Party
    ('Party.png', 134, (NOTIFY_FRIEND,)),                      # %s / has left your Xbox Live Party
    ('Party.png', 133, ()),                                    # Kicked from / Xbox Live Party
    ('ico_32x_controller.png', 63, (NOTIFY_FRIEND,)),          # %s / wants you to play
    ('ico_32x_Mail.png', 136, (NOTIFY_FRIEND,)),               # %s / sent you a message
    ('chat.png', 12, (NOTIFY_FRIEND,)),                        # %s / wants to chat
    ('signed_in.png', 117, None),                              # %s / signed in to Xbox Live
    ('signed_in.png', 118, None),                              # %s / signed in offline
    ('signed_out.png', 119, None),                             # %s / signed out
    ('download_complete.png', 47, ('Test Download',)),         # %s / downloaded
    ('connection_lost.png', 112, ()),                          # Disconnected / from Xbox Live
    ('song_playing.png', '%s', ('Test Song',)),
    ('playtimer.png', 142, ('15 minutes',)),                   # Family Timer / %ls remaining
    ('ico_32x_controller.png', 148, ()),                       # Please reconnect controller
    ('ico_32x_MU.png', 149, ()),                               # Please reinsert memory unit
]
# Where xam puts the popup is not in any file: it is the title's choice, and
# the default is bottom centre. Placed here with the scene's bottom edge on
# the title-safe line (90% down) and centred -- A GUESS, there being no
# capture of a popup to measure.
NOTIFY_BOTTOM = 0.9

# Checkboxes and radio buttons the profile's settings would have ticked when
# the guide first opens. Not in any layout -- these are the console's defaults
# (everything on, online, voice through the headset). A toggles them from
# there: each is cooked at every value it can hold (see emit_items).
CHECKED = {'radbtnOnline', 'chkShow', 'chkSound', 'chkShowMovies', 'chkVibration', 'radbtnPlayHeadset',
           'radbtnEnabled'}     # (auto sign-in: the hub comes up already signed in)

# Classes that are never rows, whatever they carry.
STATIC_CLASSES = {'XuiLabel', 'XuiText', 'XuiImage', 'XuiFigure', 'XuiEdit'}
# Classes whose Visual, left unset, is the template named after the class.
CLASS_VISUALS = {'XuiLabel', 'XuiCheckbox', 'XuiRadioButton', 'XuiSlider'}

# What A does. DASH:<scene> is Guide.cpp's verb for "close the guide and show
# this dashboard scene"; only rows whose destination is actually cooked get one.
ACTIONS = {'btnDashboard': 'DASH:Hub', 'btnSystemSettings': 'DASH:SystemSettings',
           'btnShutdown': 'GOTO:GuideTurnOff',
           # Two more dash commands whose screens the dashboard now has: Family
           # Settings (settings_pages.py), and the signed-out Home tab's Sign
           # In, which is the sign-in screen (signin_build.py).
           'btnFamilySettings': 'DASH:dashSysCslSetPControl', 'btnSignIn': 'DASH:SignIn',
           'btnGamesLibrary': 'DASH:MyGames',       # the Games tab's My Games: library_build.py's
           # The signed-out Home tab's Create Profile and Download Profile: see save_profile.
           'btnCreateNewGamertag': 'TYPE:newprofile', 'btnRecoverGamertag': 'GOTO:GuideDownloadIntro',
           # Rows whose class, not a PressPath, says where they go.
           'btnAchievements': 'GOTO:GuideAchievements', 'btnAvatarAwards': 'GOTO:GuideAwards',
           'btnPlaylist': 'GOTO:GuideMusicSources', 'btnProfile': 'GOTO:GuideProfile',
           'viewGamesButton': 'GOTO:GuideAchievements', 'editProfileButton': 'GOTO:GuideEditProfile',
           'signInPreferencesButton': 'GOTO:GuideSignInPreferences',
           'gamerTileButton': 'GOTO:GuideGamerPicture', 'gamerPreferencesButton': 'GOTO:GuideGameDefaults',
           # The Live Home tab. Its other Live rows (Party, Beacons & Activity,
           # Chat) open scenes of modules that are not in the dump either, and
           # do nothing here.
           'btnFriends': 'GOTO:GuideFriends', 'btnMessages': 'GOTO:GuideMessages'}

# Text the runtime sets, from hud.xex's Strings.xus. TrayNavButton's authored
# "Disc in Tray" is a placeholder; "Open Tray" is the empty-drive string, as
# on the hub's tile. btnPlaylist is authored with no text at all.
TEXT = {'btnDiscInTray': 'Open Tray', 'btnPlaylist': 'Select Music'}

# Images the runtime binds. The gamerpic is the default tile the hub already
# shows beside the gamertag. MusicPlaybackModeButton names a file that isn't
# in any pack and swaps in the mode's icon; in-order is the mode at rest.
IMAGES = {'btnPlaybackMode': 'ico_32x_inorder.png'}

# Normal -> Focus is frames 5 -> 7 on XuiButtonGuide, and Press runs 38 -> 48.
FOCUS_BLEND_SECONDS = 2.0 / 60.0
PRESS_SECONDS = 10.0 / 60.0

# Text shadows are one output pixel at 720p (see xur_cook); the guide's canvas
# is 480 high.
SHADOW_OFFSET = 480.0 / 720.0

# shortcut: the dim behind the guide is drawn by xam code, not by any layout, and
# there is no guide capture to measure -- this level is an estimate, picked so
# the header and legend (which float OUTSIDE the panel, in white) stay readable
# over a bright hub. Replace it with a measured value when a capture exists.
DIM = (178, 0, 0, 0)

# The ring of light is rasterized here at 2x this many times canvas
# resolution -- 6 texels a canvas unit, enough for a 4K window.
BAKE_SCALE = 3

# shortcut: the legend's slots are authored at a fixed pitch, 64 units of
# caption each, and "Game Audio" does not fit in 64 -- so xam must move the
# later buttons along somehow, and nothing extracted says how. A caption
# wider than its slot pushes everything after it right by the overrun plus
# this much.
LEGEND_GAP = 8.0


def parse(path):
    return xur_extract.parse_xur8(open(path, 'rb').read()).root_object


def props_of(node):
    return xur_schema.resolve_properties(node['class'], node['properties']) or {}


def child(node, child_id):
    return next(c for c in node['children'] if props_of(c).get('Id') == child_id)


def override(node, values):
    """Sets properties on a parsed object, the way a keyframe does at runtime."""
    node['properties'] = ([p for p in node['properties'] if p['property'] not in values]
                          + [{'property': k, 'value': v} for k, v in values.items()])


def apply_state(node, state):
    for target, values in evaluate_state(node, state).items():
        override(child(node, target), values)


def keyframes_of(owner, target_id):
    """[(keyframe, {property: value})] for one target of `owner`'s timeline."""
    for tl in owner.get('timelines') or []:
        if tl['target'] == target_id:
            return [(kf, {p['property']: p['values'] if p.get('indexed') else p.get('value')
                          for p in kf['properties']}) for kf in tl['keyframes']]
    return []


def emit_marks(c, owner):
    """The timeline's named frames, and the frames its sound objects are keyed
    on ('@' + the wave's name), as MARKs."""
    for nf in owner.get('named_frames') or []:
        c.emit('MARK', nf['name'], nf['keyframe'])
    for node in owner['children']:
        if node['class'] != 'XuiSoundXAudio':
            continue
        for kf, values in keyframes_of(owner, props_of(node).get('Id')):
            if values.get('File'):
                c.emit('MARK', '@' + os.path.splitext(values['File'].split('/')[-1])[0], kf['frame'])


def emit_keys(c, static, keys, base, scales_with_size, show_if=None):
    """KEY lines for a track whose commands were cooked at `base` (Position,
    Width, Height). XUI places a point p of the object at
        Position + Pivot + (p * size/size0 - Pivot) * Scale
    which is `scale about the box origin, then move` with the Pivot folded
    into the move -- the form the runtime applies."""
    x0, y0 = base['Position'][:2]
    vcentred = text_alignment(static.get('TextStyle', 0) or 0)[1] == 1 and 'Text' in static
    for kf, values in keys:
        v = dict(static)
        v.update(values)
        pivot = v.get('Pivot') or (0.0, 0.0, 0.0)     # keyed too: hudbkgnd moves the blade's with its size
        scale = v.get('Scale') or (1.0, 1.0, 1.0)
        sx, sy = scale[0], scale[1]
        dx = v['Position'][0] - x0 + pivot[0] * (1.0 - sx)
        dy = v['Position'][1] - y0 + pivot[1] * (1.0 - sy)
        if scales_with_size:
            sx *= v['Width'] / base['Width']
            sy *= v['Height'] / base['Height']
        elif vcentred:
            dy += (v['Height'] - base['Height']) / 2.0      # a taller box re-centres its text
        show = v.get('Show', True) and (show_if is None or show_if(v))
        ease = [e - 256 if e > 127 else e for e in (kf['ease_in'], kf['ease_out'])]
        c.emit('KEY', kf['frame'], kf['interpolation'], ease[0], ease[1], kf['ease_scale'],
               1 if show else 0, float(dx), float(dy), float(sx), float(sy), float(v.get('Opacity', 1.0)))


def track(c, owner, node, body=None, scales_with_size=False, base=None, track_id=None, show_if=None):
    """One animated object as a TRACK: `body(node)` cooks it at its base state,
    fully visible, and the object's own keyframes become the KEYs. An object
    the timeline never touches is just cooked as it stands."""
    static = props_of(node)
    keys = keyframes_of(owner, static.get('Id'))
    if not keys:
        if body and static.get('Show', True):
            body(node)
        return
    if base is None:
        base = dict(static)
        base.update(keys[0][1])
    override(node, {'Position': base['Position'], 'Width': base['Width'], 'Height': base['Height'],
                    'Show': True, 'Opacity': 1.0})
    c.emit('TRACK', track_id or static['Id'], base['Position'][0], base['Position'][1],
           base['Width'], base['Height'])
    emit_keys(c, static, keys, base, scales_with_size, show_if)
    if body:
        body(node)
    c.lines.append('ENDTRACK')


def track_sizes(c, owner, node, body):
    """A nine-grid object as one TRACK per size it is ever keyed at. A
    stretched bake is only right at the size it was baked for, so each size
    gets its own, shown on the keys that ARE that size; a span between two
    sizes is drawn with the bake it starts from, stretched."""
    static = props_of(node)
    sizes = {}
    for _kf, values in keyframes_of(owner, static.get('Id')):
        v = dict(static)
        v.update(values)
        sizes.setdefault((v['Width'], v['Height']), {k: v[k] for k in ('Position', 'Width', 'Height')})
    if len(sizes) < 2:
        return track(c, owner, node, body, scales_with_size=True)
    for (w, h), base in sorted(sizes.items()):
        track(c, owner, copy.deepcopy(node), body, scales_with_size=True, base=base,
              track_id=f"{static['Id']}_{int(w)}x{int(h)}",
              show_if=lambda v, w=w, h=h: (v['Width'], v['Height']) == (w, h))


def track_tints(c, owner, node, body):
    """An object whose ColorFactor is keyed (an icon that darkens when its tab
    is the open one) as one TRACK per tint, each shown on the keys that are
    its own. A track can fade and move; it cannot recolour."""
    static = props_of(node)
    tints = []
    for _kf, values in keyframes_of(owner, static.get('Id')):
        if 'ColorFactor' in values and values['ColorFactor'] not in tints:
            tints.append(values['ColorFactor'])
    if len(tints) < 2:
        return track(c, owner, node, body)
    for i, tint in enumerate(tints):
        copy_ = copy.deepcopy(node)
        override(copy_, {'ColorFactor': tint})
        track(c, owner, copy_, body, track_id=f"{static['Id']}_tint{i}",
              show_if=lambda v, tint=tint: v.get('ColorFactor') == tint)


def is_nine_grid(c, p):
    template = c.visual_templates.get(p.get('Visual'))
    return bool(template and template['children'] and template['children'][0]['class'] == 'XuiNineGrid')


# --- things baked to PNG ----------------------------------------------------

def nine_grid(c, assets_dir, grid, x, y, w, h):
    """One XuiNineGrid as a pre-stretched PNG: corners 1:1, edges stretched
    along their own axis, centre both ways."""
    p = props_of(grid)
    src_path = resolve_image_path(p['TextureFileName'], c.image_index)
    src = Image.open(src_path).convert('RGBA')
    w, h = int(round(w)), int(round(h))
    l, t, r, b = (int(p[k]) for k in ('LeftOffset', 'TopOffset', 'RightOffset', 'BottomOffset'))
    cols = [(0, l, 0, l), (l, src.width - r, l, w - r), (src.width - r, src.width, w - r, w)]
    rows = [(0, t, 0, t), (t, src.height - b, t, h - b), (src.height - b, src.height, h - b, h)]
    out = Image.new('RGBA', (w, h))
    for sx0, sx1, dx0, dx1 in cols:
        for sy0, sy1, dy0, dy1 in rows:
            cell = src.crop((sx0, sy0, sx1, sy1)).resize((dx1 - dx0, dy1 - dy0), Image.BILINEAR)
            out.paste(cell, (dx0, dy0))
    name = f'{os.path.splitext(os.path.basename(src_path))[0]}_{w}x{h}.png'
    out.save(os.path.join(assets_dir, name))
    c.emit('IMAGE', x, y, float(w), float(h), name, 255, 255, 255, 255)


def blade(c, assets_dir, node):
    """A HUD_Blade* control: the template's one nine-grid, anchored into the
    instance's box exactly as any other template child is."""
    p = props_of(node)
    template = c.visual_templates[p['Visual']]
    tp = props_of(template)
    grid = template['children'][0]
    gp = props_of(grid)
    x, y, w, h = anchor_rect(gp['Position'], gp['Width'], gp['Height'], tp['Width'], tp['Height'],
                             p['Width'], p['Height'], gp['Anchor'])
    nine_grid(c, assets_dir, grid, p['Position'][0] + x, p['Position'][1] + y, w, h)


def turned_label(c, node):
    """A XuiText turned a quarter turn clockwise about its own origin, which is
    every blade caption (Rotation z=w=0.7071), as a TEXTV: the same box and
    alignment an upright TEXT would have, which the renderer turns."""
    p = props_of(node)
    start = len(c.lines)
    c.cook_text(p['Text'], p, p['Position'][0], p['Position'][1], p['Width'], p['Height'], 1.0)
    c.lines[start:] = ['TEXTV' + line[4:] for line in c.lines[start:]]

def ring_of_light(c, assets_dir, node):
    """The ringOfLight_Group visual: four 12x12 clipped groups, one per
    quadrant, each turned so the same 16x16 radial-gradient figure shows as
    that quadrant's arc. ROL_Off draws all four in grey at 60%; the lit
    copies sit over it, and the runtime shows the one for the signed-in
    player's quadrant -- player 1, light1, top left.

    The figure's fill is a ring (stops 0.59 / 0.65 / 0.75 / 0.81 of the
    radius) with its texture space scaled 0.55 and moved 0.34 about the
    figure's centre: t = |(uv - 0.5) * Scale + Translation| * 2, which puts
    the ring's centre 1.9px past the group's own corner and its radius at
    8 / 0.55 = 14.5px."""
    p = props_of(node)
    template = c.visual_templates[p['Visual']]
    off_group = child(child(template, 'ROL_indent'), 'ROL_Off')
    size = int(p['Width'])
    n = size * BAKE_SCALE * 2
    ys, xs = np.mgrid[0:n, 0:n]
    px = (xs + 0.5) * size / n
    py = (ys + 0.5) * size / n
    out = np.zeros((n, n, 4))

    def paint(light, opacity):
        lp = props_of(light)
        fig = light['children'][0]
        fp = props_of(fig)
        fill = {f['property']: f.get('values', f.get('value')) for f in fp['Fill']}
        grad = {g['property']: g.get('values', g.get('value')) for g in fill['Gradient']}
        qz, qw = lp['Rotation'][2], lp['Rotation'][3]
        angle = 2.0 * math.atan2(qz, qw)
        # canvas -> the group's own space: undo its position, then its turn.
        rx, ry = px - lp['Position'][0], py - lp['Position'][1]
        lx = rx * math.cos(angle) + ry * math.sin(angle)
        ly = -rx * math.sin(angle) + ry * math.cos(angle)
        inside = (lx >= 0) & (lx <= lp['Width']) & (ly >= 0) & (ly <= lp['Height'])     # ClipChildren
        gx = (lx / fp['Width'] - 0.5) * fill['Scale'][0] + fill['Translation'][0]
        gy = (ly / fp['Height'] - 0.5) * fill['Scale'][1] + fill['Translation'][1]
        t = np.hypot(gx, gy) * 2.0
        stops, colors = grad['StopPos'], grad['StopColor']
        rgba = np.zeros((n, n, 4))
        for k, key in enumerate('rgba'):
            rgba[..., k] = np.interp(t, stops, [col[key] for col in colors])
        factor = fp['ColorFactor']
        rgba[..., :3] *= np.array([factor['r'], factor['g'], factor['b']]) / 255.0
        alpha = rgba[..., 3] / 255.0 * opacity * inside
        out[..., :3] = out[..., :3] * (1 - alpha[..., None]) + rgba[..., :3] * alpha[..., None]
        out[..., 3] = out[..., 3] + (1 - out[..., 3]) * alpha

    for light in off_group['children']:
        paint(light, props_of(off_group)['Opacity'])
    paint(child(template, 'light1'), 1.0)

    rgb = np.where(out[..., 3:] > 0, out[..., :3] / np.maximum(out[..., 3:], 1e-6), 0)     # un-premultiply
    img = np.dstack([rgb, out[..., 3] * 255.0]).clip(0, 255).astype(np.uint8)
    Image.fromarray(img).save(os.path.join(assets_dir, 'ring_of_light_p1.png'))
    # The instance is scaled about its pivot.
    scale, pivot = p['Scale'][0], p['Pivot']
    c.emit('IMAGE', p['Position'][0] + pivot[0] * (1 - scale), p['Position'][1] + pivot[1] * (1 - scale),
           size * scale, size * scale, 'ring_of_light_p1.png', 255, 255, 255, 255)


def half_turns(rotation):
    """A Rotation that is a half turn about one axis, as the (x, y) signs it
    leaves a flat object with.
    shortcut: half turns only, which is all scr_Notification uses; make this a
    real 2x2 matrix if a painted visual ever turns by anything else."""
    q = tuple(int(round(v)) for v in (rotation or (0.0, 0.0, 0.0, 1.0)))
    return {(0, 0, 0, 1): (1.0, 1.0), (0, 0, 1, 0): (-1.0, -1.0),
            (0, 1, 0, 0): (-1.0, 1.0), (1, 0, 0, 0): (1.0, -1.0)}[q]


def paint(out, node, x, y):
    """Software-render `node` -- a XuiFigure, or a group of them -- into the
    premultiplied RGBA array `out`, `x` and `y` being each pixel's position
    in the node's PARENT's space.

    For what a GRADIENT_RECT cannot say: an object that is mirrored or turned
    (XUI places a point q of it at Position + Pivot + (q - Pivot) * Scale,
    turned), and a gradient whose brush is. A fill maps the figure's box to
    (u, v) in -0.5..0.5, turns that by the brush's Rotation, scales it,
    translates it, and then a radial fill reads |g| * 2 as its position along
    the stops and a linear one g.x + 0.5 -- which is how one figure can be the
    quarter of a ring (the brush's centre moved to a corner) or the rounded
    end of a pill (moved to an edge). Rectangles only: the bezier outline is
    the box, as everywhere else in the cook."""
    p = props_of(node)
    if not p.get('Show', True):
        return
    fx, fy = half_turns(p.get('Rotation'))
    scale = p.get('Scale') or (1.0, 1.0, 1.0)
    pivot = p.get('Pivot') or (0.0, 0.0, 0.0)
    lx = pivot[0] + (x - p['Position'][0] - pivot[0]) / (scale[0] * fx)
    ly = pivot[1] + (y - p['Position'][1] - pivot[1]) / (scale[1] * fy)
    if node['class'] != 'XuiFigure':
        for kid in node['children']:
            paint(out, kid, lx, ly)
        return
    fill = {f['property']: f.get('values', f.get('value')) for f in p['Fill']}
    grad = {g['property']: g.get('values', g.get('value')) for g in fill.get('Gradient') or []}
    u, v = lx / p['Width'] - 0.5, ly / p['Height'] - 0.5
    turn = math.radians(fill.get('Rotation') or 0.0)
    bs, bt = fill.get('Scale') or (1.0, 1.0, 1.0), fill.get('Translation') or (0.0, 0.0, 0.0)
    gx = (u * math.cos(turn) - v * math.sin(turn)) * bs[0] + bt[0]
    gy = (u * math.sin(turn) + v * math.cos(turn)) * bs[1] + bt[1]
    if fill.get('FillType') == FILLTYPE_RADIAL_GRADIENT:
        t = np.hypot(gx, gy) * 2.0
    else:
        t = gx + 0.5
    r, g, b, a = (np.interp(t, grad['StopPos'], [c[k] for c in grad['StopColor']]) for k in 'rgba')
    alpha = a / 255.0 * p.get('Opacity', 1.0) * ((lx >= 0) & (lx <= p['Width']) & (ly >= 0) & (ly <= p['Height']))
    out[..., :3] = out[..., :3] * (1.0 - alpha[..., None]) + np.dstack([r, g, b]) * alpha[..., None]
    out[..., 3] = out[..., 3] + (1.0 - out[..., 3]) * alpha


def painted(c, assets_dir, name, node):
    """`node` painted (see paint) to <name>.png, cropped to its ink -> the
    IMAGE line that draws it where it stands in its parent."""
    p = props_of(node)
    pad = 90.0      # room for what hangs outside the node's own box: the pill's caps, a mirrored figure
    x0, y0 = p['Position'][0] - pad, p['Position'][1] - pad
    w, h = int((p['Width'] + 2 * pad) * BAKE_SCALE), int((p['Height'] + 2 * pad) * BAKE_SCALE)
    ys, xs = np.mgrid[0:h, 0:w]
    out = np.zeros((h, w, 4))
    paint(out, node, x0 + (xs + 0.5) / BAKE_SCALE, y0 + (ys + 0.5) / BAKE_SCALE)
    rows, cols = np.where(out[..., 3] > 0.002)
    top, left, bottom, right = rows.min(), cols.min(), rows.max() + 1, cols.max() + 1
    out = out[top:bottom, left:right]
    rgb = np.where(out[..., 3:] > 0, out[..., :3] / np.maximum(out[..., 3:], 1e-6), 0)     # un-premultiply
    Image.fromarray(np.dstack([rgb, out[..., 3] * 255.0]).clip(0, 255).astype(np.uint8)).save(
        os.path.join(assets_dir, name + '.png'))
    return 'IMAGE ' + ' '.join(f'{v:.3f}' for v in (x0 + left / BAKE_SCALE, y0 + top / BAKE_SCALE,
                                                    (right - left) / float(BAKE_SCALE),
                                                    (bottom - top) / float(BAKE_SCALE))) + f' {name}.png 255 255 255 255'


def break_lines(text, width, points, font_path, wrap=True):
    """(lines, line height) for `text` set at `points` in a box `width` wide:
    explicit breaks always, word wrap if asked."""
    font = ImageFont.truetype(font_path, int(round(points * POINT_SIZE_TO_PIXELS * 4)))
    lines = []
    for para in text.replace('\r', '').split('\n'):
        line = ''
        for word in para.split():
            trial = word if not line else line + ' ' + word
            if line and wrap and font.getlength(trial) / 4.0 > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines, sum(font.getmetrics()) / 4.0


def wrapping(cook_text, font_path):
    """TEXT is one unwrapped run, and the guide's body labels are paragraphs:
    explicit line breaks, and word wrap unless TextStyle carries the
    single-line bit (0x10 -- what tells Label_Body_1line, 0x4010, from
    Label_Body, 0x4000). The console wraps these itself; this is where the
    breaks land, measured with the same face the app draws in."""
    def cook(text, props, x, y, w, h, opacity, point_size=None, color=None):
        single = (props.get('TextStyle', 0) or 0) & 0x10
        if single and '\n' not in text:
            return cook_text(text, props, x, y, w, h, opacity, point_size=point_size, color=color)
        points = point_size if point_size and point_size > 0 else props.get('PointSize') or 12.0
        lines, line_h = break_lines(text, w, points, font_path, wrap=not single)
        adjust = props.get('LineSpacingAdjust', 0) or 0
        line_h += adjust - (1 << 32) if adjust >= (1 << 31) else adjust
        for i, line in enumerate(lines):
            if line:
                cook_text(line, props, x, y + i * line_h, w, line_h, opacity, point_size=point_size, color=color)
    return cook


def slider_visual(ctx, node):
    """A XuiSlider for one value, as template variants the Cooker can use.

    The slider's fill is not a property, it is a timeline: SliderBody carries
    its own, nested inside the template, whose frames ARE the value -- the
    bar's Width and the thumb's Position run from empty to full across one
    span of frames in the resting colours and across a second span in the
    focused green. The runtime parks that timeline on Value. Do the same
    here: one copy of the template per span, with the bar, the thumb and the
    value readout set to where `Value` falls in it. The second copy is named
    '<visual>:Focus', which emit_items picks up for the focused state."""
    p = props_of(node)
    low, value = p.get('RangeMin', 0) or 0, p.get('Value', 0) or 0
    fraction = (value - low) / float((p.get('RangeMax', 100) or 100) - low)
    name = f'XuiSlider@{value}'
    body = child(ctx['templates']['XuiSlider'], 'SliderBody')
    bar, thumb = keyframes_of(body, 'Bar_mid1'), keyframes_of(body, 'Bar_Highlight')
    spans = [i for i in range(len(bar) - 1) if bar[i + 1][1]['Width'] > bar[i][1]['Width']]
    for variant, i in zip((name, name + ':Focus'), spans):
        if variant in ctx['templates']:
            continue
        t = copy.deepcopy(ctx['templates']['XuiSlider'])
        tb = child(t, 'SliderBody')
        (_, b0), (_, b1), (_, t0), (_, t1) = bar[i], bar[i + 1], thumb[i], thumb[i + 1]
        figure = child(tb, 'Bar_mid1')
        override(figure, {'Width': b0['Width'] + (b1['Width'] - b0['Width']) * fraction, 'Anchor': 0})
        fill = next(q for q in figure['properties'] if q['property'] == 'Fill')['value']
        gradient = next(q for q in fill if q['property'] == 'Gradient')['value']
        next(q for q in gradient if q['property'] == 'StopColor')['values'] = b0['StopColor']
        override(child(tb, 'Bar_Highlight'), {'Position': tuple(
            a + (b - a) * fraction for a, b in zip(t0['Position'], t1['Position']))})
        readout = child(t, 'Text_Slider')
        readout['class'] = 'XuiText'      # a literal, not a presenter waiting for data
        override(readout, {'Text': str(int(value))})
        ctx['templates'][variant] = t
    override(node, {'Visual': name})


# --- the scenes -------------------------------------------------------------

class Scene:
    """One cooked scene being written: a Cooker plus where it lands."""

    def __init__(self, name, ctx, comment):
        self.dir = os.path.join(ctx['out_dir'], name)
        self.assets = os.path.join(self.dir, 'assets')
        # Rewritten whole, so a PNG an earlier cook baked and this one no
        # longer needs doesn't linger. Only ever this scene's own folder.
        if os.path.isdir(self.dir):
            shutil.rmtree(self.dir)
        os.makedirs(self.assets)
        self.name = name
        self.ctx = ctx
        self.c = Cooker(ctx['index'], visual_templates=ctx['templates'])
        self.c.shadow_offset = SHADOW_OFFSET
        self.c.bold_offset = 0.75 * SHADOW_OFFSET       # three quarters of a 720p pixel: see Cooker.cook_text
        self.c.cook_text = wrapping(self.c.cook_text, ctx['font'])
        self.c.comment(f'{name} -- {comment} See guide_build.py.')
        # GuideMain is authored 852x480, a hair narrower than 16:9. Widened to
        # exactly 16:9 so the overlay letterboxes onto the same pixels as the
        # 1280x720 dashboard under it -- otherwise the dim leaves a 1px column
        # undimmed down each side.
        self.canvas = (ctx['canvas_h'] * 16.0 / 9.0, ctx['canvas_h'])
        self.c.emit('CANVAS', *self.canvas)

    def write(self):
        with open(os.path.join(self.dir, 'manifest.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(self.c.lines) + '\n')
        for src, rel in self.c.used_images.items():
            shutil.copyfile(src, os.path.join(self.assets, rel))
        print(f'{self.name}: {len(self.c.lines)} lines')
        for w in self.c.warnings:
            print(f'   warning: {w}')


def cook_frame(ctx):
    s = Scene('GuideFrame', ctx, 'hudbkgnd.xur: the dim, the plain centre blade, the clock, the gamerpic.')
    c = s.c
    root = copy.deepcopy(ctx['bkgnd'])['children'][0]       # HUDRootScene
    emit_marks(c, root)

    # The dim follows the centre blade's own opacity: there when the guide is.
    centre = child(root, 'Blade_Center')
    c.emit('TRACK', 'Dim', 0.0, 0.0, *s.canvas)
    still = {'Position': (0.0, 0.0, 0.0), 'Width': 1.0, 'Height': 1.0}
    emit_keys(c, still, [(kf, {'Show': v['Show'], 'Opacity': v['Opacity']})
                         for kf, v in keyframes_of(root, 'Blade_Center')], still, False)
    c.emit('RECT', 0.0, 0.0, *s.canvas, 0, *DIM)
    c.lines.append('ENDTRACK')

    # A message box's title bar: under the blade in the tree, so only the
    # part of it that stands proud of the blade's top edge shows.
    track(c, root, child(root, 'ErrorShader'), lambda n: c.walk(n, 0.0, 0.0, 1.0), scales_with_size=True)

    # Three sizes: the small guide's, the full-size one a sub-page sits in,
    # and the tall narrow one a message box does.
    track_sizes(c, root, centre, lambda n: blade(c, s.assets, n))

    # The legend row. Its captions are the hosted scene's (LegendA/B/X/Y on
    # its HUDScene), so every distinct set the guide's scenes ask for is
    # cooked here under IF legend <n>, and each of those scenes carries its
    # <n> as a "Legend.Id" MARK for Guide.cpp to pass back in. On its own
    # track, it slides and fades exactly when hudbkgnd says.
    row = child(root, 'Legend')
    parked = dict(props_of(row))
    parked.update(evaluate_state(root, 'EndClosedToHalf')['Legend'])

    def legends(node):
        for n, captions in enumerate(ctx['legends'], 1):
            c.emit('IF', 'legend', n)
            variant = copy.deepcopy(node)
            shift = 0.0
            for letter, caption in zip('ABXY', captions):
                button, text = child(variant, letter + 'Button'), child(variant, letter + 'Text')
                for part in (button, text):
                    x, y, z = props_of(part)['Position']
                    override(part, {'Show': bool(caption), 'Position': (x + shift, y, z)})
                override(text, {'Text': caption})
                tp = props_of(text)
                font = ImageFont.truetype(ctx['font'], int(round(tp['PointSize'] * POINT_SIZE_TO_PIXELS * 4)))
                shift += max(0.0, font.getlength(caption) / 4.0 + LEGEND_GAP - tp['Width'])   # see LEGEND_GAP
            c.walk(variant, 0.0, 0.0, 1.0)
            c.lines.append('ENDIF')
    track(c, root, row, legends, base=parked)

    track(c, root, child(root, 'GamerPic'), lambda n: gamer_picture(c, n, 0.0, 0.0))

    clock = child(root, 'DateTimeTextId')
    override(clock, {'Text': '{time}'})
    half = dict(props_of(clock))
    half.update(evaluate_state(root, 'EndClosedToHalf')['DateTimeTextId'])
    track(c, root, clock, lambda n: c.walk(n, 0.0, 0.0, 1.0), base=half)

    # Not drawn here: how visible whatever the frame HOSTS is -- the guide's
    # own scenes in one, a message box in the other. Guide.cpp reads them.
    track(c, root, child(root, 'AppHostElementId'))
    track(c, root, child(root, 'ErrorHUD'))
    s.write()


def legend(c, ctx, hud_props):
    """Which of the frame's cooked legends a hosted scene wants, as its
    "Legend.Id" MARK (see cook_frame). 0 -- no MARK needed -- for none."""
    captions = tuple(hud_props.get('Legend' + letter) or '' for letter in 'ABXY')
    if any(captions):
        if captions not in ctx['legends']:
            ctx['legends'].append(captions)
        c.emit('MARK', 'Legend.Id', ctx['legends'].index(captions) + 1)


def layout_path(ctx, name):
    """A layout by file name: hud.xex's pack first, then any pack searched."""
    path = os.path.join(ctx['hud_dir'], name)
    return path if os.path.isfile(path) else find_skin_files(ctx['search_dirs'], name)[0]


def cook_main(ctx):
    s = Scene('GuideMain', ctx, "GuideMain.xur's Tabscene: side blades, captions, the selected tab's strip.")
    c = s.c
    hud = copy.deepcopy(ctx['guide'])['children'][0]        # HUDScene
    tabs = child(hud, 'Tabscene')
    emit_marks(c, tabs)

    past_tabs = False
    for node in tabs['children']:
        p = props_of(node)
        if node['class'] == 'XuiSoundXAudio':
            continue
        if node['class'] == 'XuiScene':
            # A tab. Nothing to draw -- its rows are their own scene -- but
            # its track is what places them, relative to where it rests.
            index = int(p['Id'][3:]) - 1
            rest = dict(p)
            rest.update(evaluate_state(tabs, SETTLED[index])[p['Id']])
            track(c, tabs, node, scales_with_size=True, base=rest)
            ctx['tab_boxes'][index] = rest
            past_tabs = True
            continue
        if past_tabs and 'LAYER fixed' not in c.lines:
            # Everything after the tabs in the tree draws OVER their rows:
            # a tab sliding in from the left passes under the selected strip.
            c.lines.append('LAYER fixed')
        if is_nine_grid(c, p):
            track_sizes(c, tabs, node, lambda n: blade(c, s.assets, n))
        elif node['class'] == 'XuiText' and abs((p.get('Rotation') or (0, 0, 0, 1))[2]) > 0.1:
            # Height is keyed on the selected captions but, top-aligned and
            # turned, it only moves the far edge of the box. Bake the tallest.
            tallest = max([v.get('Height', p['Height']) for _kf, v in keyframes_of(tabs, p['Id'])] + [p['Height']])
            first = dict(p)
            first.update(keyframes_of(tabs, p['Id'])[0][1])
            first['Height'] = tallest
            track(c, tabs, node, lambda n: turned_label(c, n), base=first)
        else:
            track(c, tabs, node, lambda n: c.walk(n, 0.0, 0.0, 1.0))

    legend(c, ctx, props_of(hud))
    for node in hud['children']:
        p = props_of(node)
        if node is tabs:
            continue
        if p.get('Visual') == 'ringOfLight_Group':
            ring_of_light(c, s.assets, node)
        elif p.get('Id') == 'imgControllerBattery':
            # Four images, each hosting a one-image layout that is that
            # level's icon (Controller_OneFourth.xur ... _Full.xur); the
            # runtime shows the one for the controller's charge and none for
            # a wired pad. XInput reports the same four levels, so they are
            # cooked under IF battery 1..4 for Guide.cpp to choose from.
            for level, kid in enumerate(sorted(node['children'], key=lambda k: (
                    'OneFourth', 'Half', 'ThreeFourths', 'Full').index(props_of(k)['ImagePath'][11:-4])), 1):
                icon = parse(layout_path(ctx, props_of(kid)['ImagePath']))['children'][0]['children'][0]
                c.emit('IF', 'battery', level)
                c.walk(icon, p['Position'][0], p['Position'][1], 1.0)
                c.lines.append('ENDIF')
        else:
            c.walk(node, 0.0, 0.0, 1.0)
    s.write()


def collect_rows(node, ox, oy, inherited, ctx, rows, entries, statics=None):
    """Every focusable row under `node`, depth first, as (node, x, y, nav).
    A row with no link of its own in some direction falls back to its
    container's, which is how XUI routes a d-pad press nothing handled.
    Whatever isn't a row or a container of rows is cooked into `statics` as
    it is passed, if a Cooker is given -- a page's labels and panels."""
    for kid in node['children']:
        p = props_of(kid)
        nav = {d: p.get('Nav' + d.capitalize()) or inherited.get(d) for d in ('up', 'down', 'left', 'right')}
        if (p.get('Visual') or '').startswith('Loading_') or p.get('Id') in HIDDEN:
            continue                                # a spinner, up only while a list loads; or see HIDDEN
        if kid['class'] == 'XuiGamerCard' and statics:
            gamer_card(statics, ctx, ox + p['Position'][0], oy + p['Position'][1])
            continue
        if p.get('Id') in EMPTY_LABELS:
            override(kid, {'Show': True})
            p = props_of(kid)
        if not p.get('Show', True):
            # Hidden, but still a step in the chain its neighbours link
            # through (Voice's Kinect checkbox sits between the sliders and
            # the radio buttons): a link to it carries on along its own.
            entries[p.get('Id')] = nav
            continue
        if p.get('Id') in IMAGES:
            override(kid, {'ImagePath': IMAGES[p['Id']]})
        if p.get('Id') in TEXT:
            override(kid, {'Text': TEXT[p['Id']]})
        if not p.get('Visual') and kid['class'] in CLASS_VISUALS:
            override(kid, {'Visual': kid['class']})
        if kid['class'] == 'XuiSlider':
            slider_visual(ctx, kid)
        p = props_of(kid)
        # (a legend_* button is the page's own A/B legend, not somewhere focus goes)
        if (kid['class'] not in STATIC_CLASSES and p.get('Visual') and not p['Visual'].startswith('legend_')
                and (p.get('Text') or p.get('ImagePath'))):
            rows.append((kid, ox, oy, nav, props_of(node).get('Id')))
            continue
        inner = kid
        if p.get('Id') in HOSTED:
            inner = parse(layout_path(ctx, HOSTED[p['Id']]))['children'][0]
            hp = props_of(inner)
            before = len(rows)
            collect_rows(inner, ox + p['Position'][0] + hp['Position'][0], oy + p['Position'][1] + hp['Position'][1],
                         nav, ctx, rows, entries)
        elif kid['children'] and kid['class'] not in STATIC_CLASSES:
            before = len(rows)
            collect_rows(kid, ox + p['Position'][0], oy + p['Position'][1], nav, ctx, rows, entries, statics)
        else:
            if statics:
                statics.walk(kid, ox, oy, 1.0)
            continue
        if len(rows) > before:
            # A link that names the container lands on the first row inside it.
            default = props_of(inner).get('DefaultFocus')
            ids = [props_of(r[0])['Id'] for r in rows[before:]]
            entries[p['Id']] = default if default in ids else ids[0]


def gamer_card(c, ctx, x, y):
    """A XuiGamerCard: xam's gamercard.xur, hosted. The card has two faces
    and its own timeline flips between them -- one for a profile on Xbox Live
    (zone, reputation stars, membership bar) and offlineBaseScene, for one
    that isn't: gamertag, picture, and three tallies. PROFILE fills the
    latter. The extended panel under it is off on every page that hosts one
    here (ShowExtendedPanel=false)."""
    card = parse(find_skin_files(ctx['search_dirs'], 'gamercard.xur')[0])['children'][0]
    face = child(card, 'offlineBaseScene')
    override(face, {'Show': True})
    bound = {'gamerTagText': PROFILE['gamertag'], 'titlesPlayedText': PROFILE['games'],
             'credText': PROFILE['gamerscore'], 'achievementsText': PROFILE['achievements']}
    for kid in face['children']:
        p = props_of(kid)
        if p.get('Id') in bound:
            override(kid, {'Text': bound[p['Id']]})
        elif p.get('Text'):
            override(kid, {'Text': p['Text'].strip()})      # authored with a tail of blank lines
        if kid['class'] in CLASS_VISUALS and not p.get('Visual'):
            override(kid, {'Visual': kid['class']})
    # (the face is a bare scene: walking its children is walking it)
    x, y = x + props_of(face)['Position'][0], y + props_of(face)['Position'][1]
    for kid in face['children']:
        if props_of(kid).get('Id') == 'gamerTileImage':
            gamer_picture(c, kid, x, y)
        else:
            c.walk(kid, x, y, 1.0)


def gamer_picture(c, node, x, y):
    """The profile's gamer picture, wherever a layout shows it: every stock
    one (TILES) under IF gamerpic <n>, for Guide.cpp to say which is worn."""
    for n, tile in enumerate(TILES):
        c.emit('IF', 'gamerpic', n)
        override(node, {'ImagePath': tile})
        c.walk(node, x, y, 1.0)
        c.lines.append('ENDIF')


def fill_list(ctx, node, entries):
    """Fill a XuiList the way the runtime does: one copy of the item its
    visual carries per entry -- stacked, or wrapped into rows when the item
    is laid out as a grid (Layout=4). `entries` are {property: value}, Id
    included. The copies become the list's children, linked to each other,
    so collect_rows finds them as it finds any row."""
    p = props_of(node)
    visual = ctx['templates'][p['Visual']]
    ip = props_of(next(k for k in visual['children'] if k['class'] == 'XuiListItem'))
    grid = ip.get('Layout') == 4
    # A stacked item is anchored to both sides: it is as wide as its list.
    width = ip['Width'] if grid else p['Width'] - (props_of(visual)['Width'] - ip['Width'])
    columns = int(p['Width'] // width) if grid else 1
    ids = [e['Id'] for e in entries]
    node['children'] = []
    for i, entry in enumerate(entries):
        def at(j, same_row=False):
            ok = 0 <= j < len(ids) and (not same_row or j // columns == i // columns)
            return ids[j] if ok else ''
        kid = {'class': 'XuiButton', 'properties': [], 'children': []}
        override(kid, {'Visual': ip['Visual'], 'Width': width, 'Height': ip['Height'],
                       'Position': ((i % columns) * width, (i // columns) * ip['Height'], 0.0),
                       'NavUp': at(i - columns), 'NavDown': at(i + columns),
                       'NavLeft': at(i - 1, True), 'NavRight': at(i + 1, True), **entry})
        node['children'].append(kid)


def mirrored(ctx, name):
    """The image `name` flipped left to right, as an image of its own."""
    flipped = 'mirrored_' + name
    if flipped not in ctx['index']:
        ctx['index'][flipped] = os.path.join(ctx['baked'], flipped)
        ImageOps.mirror(Image.open(ctx['index'][name])).save(ctx['index'][flipped])
    return flipped


def spinner(ctx, node, label, choices, initial):
    """A spinner -- a XuiList one item high that left and right turn through
    its values -- as a row holding a value: the item its visual carries, the
    two arrows that visual shows while it has focus, and its caption, which
    the layout keeps as a separate label ON TOP of the list (so it has to be
    drawn with the row, after the highlight, not with the page under it).

    Each arrow is a XuiScrollEnd whose own visual is one image; the right
    one's is the left one's with Scale -1 about its far edge, which the
    Cooker does not apply -- so the arrows are cooked here, as plain images
    where that visual puts them."""
    p = props_of(node)
    x, y = p['Position'][:2]
    arrows = []
    for kid in ctx['templates'][p['Visual']]['children']:
        if kid['class'] == 'XuiScrollEnd':
            kp = props_of(kid)
            ip = props_of(next(k for k in ctx['templates'][kp['Visual']]['children'] if k['class'] == 'XuiImage'))
            flip = (ip.get('Scale') or (1.0,))[0] < 0
            arrow = {'class': 'XuiImage', 'properties': [], 'children': []}
            override(arrow, {'Position': kp['Position'], 'Width': ip['Width'], 'Height': ip['Height'],
                             'ColorFactor': ip['ColorFactor'],
                             'ImagePath': mirrored(ctx, ip['ImagePath']) if flip else ip['ImagePath']})
            arrows.append((arrow, x, y))
        elif kid['class'] == 'XuiListItem':
            item = props_of(kid)['Visual']
    override(label, {'Show': True, 'Visual': props_of(label).get('Visual') or 'XuiLabel'})
    override(node, {'Show': True, 'Visual': item, 'Text': choices[initial]})
    node['choices'], node['initial'] = choices, initial
    node['with'], node['focus_with'] = [(label, 0.0, 0.0)], arrows


def cook_tab(n, ctx, scenes=TAB_SCENES, content=TAB_CONTENT):
    s = Scene(scenes[n], ctx, f'the rows of guide tab {n + 1}'
                              f"{'' if scenes is TAB_SCENES else ', with nobody signed in'}.")
    c = s.c
    c.emit('FOCUSBLEND', FOCUS_BLEND_SECONDS)
    c.emit('PRESS', PRESS_SECONDS, 0.0)

    box = ctx['tab_boxes'][n]
    ox, oy = box['Position'][:2]
    if content[n]:
        parent = parse(layout_path(ctx, content[n]))['children'][0]
        hosted = props_of(parent)['Position']
        ox, oy = ox + hosted[0], oy + hosted[1]
    else:
        parent = child(child(copy.deepcopy(ctx['guide'])['children'][0], 'Tabscene'), f'Tab{n + 1}')
    rows, entries = [], {}
    collect_rows(parent, ox, oy, {}, ctx, rows, entries)

    # Left/right with nowhere to go inside the tab step a tab instead.
    # "Scene:" with no item keeps that tab's own focus.
    tab_left = scenes[n - 1] + ':' if n > 0 else '-'
    tab_right = scenes[n + 1] + ':' if n + 1 < len(scenes) else '-'

    emit_items(s, parent, rows, entries, tab_left, tab_right)


def edge(c, row, x, y):
    """A control's Edge, which the cooker (it has no nine-grids) leaves out: edge.png with no centre and
    one-unit offsets, which is a one-unit outline in the grid's ColorFactor. Every key of the keyboard."""
    p = props_of(row)
    tint = props_of(c.visual_templates[p['Visual'].split(':')[0].split('@')[0]]['children'][0])['ColorFactor']
    px, py, w, h = x + p['Position'][0], y + p['Position'][1], p['Width'], p['Height']
    for rx, ry, rw, rh in ((px, py, w, 1.0), (px, py + h - 1.0, w, 1.0),
                           (px, py + 1.0, 1.0, h - 2.0), (px + w - 1.0, py + 1.0, 1.0, h - 2.0)):
        c.emit('RECT', rx, ry, rw, rh, 0, tint['a'], tint['r'], tint['g'], tint['b'])


def emit_items(s, parent, rows, entries, left, right, actions=None):
    """The ITEM blocks for a scene's rows. `left` / `right` are where those
    directions go from a row with no link of its own; `actions` are this
    scene's own, by row id, ahead of the global table."""
    c = s.c
    ids = [props_of(r[0])['Id'] for r in rows]

    def link(target, direction, fallback='-'):
        # A link is a path whose last step is the control; a container stands
        # for the row inside it, and a hidden control for wherever its own
        # link in the same direction leads (a hidden row at the END of a
        # chain, like the DEBUG ones, leads nowhere).
        for _ in range(8):
            target = (target or '').split('\\')[-1]
            target = entries.get(target, target)
            if not isinstance(target, dict):
                break
            target = target.get(direction)
        return target if target in ids else fallback

    if rows:
        c.emit('DEFAULTFOCUS', link(props_of(parent).get('DefaultFocus'), 'down', ids[0]))
    for row, x, y, nav, group in rows:
        rp = props_of(row)
        rid = rp['Id']
        page = PAGES.get(rp.get('PressPath') or rp.get('MainScenePressPath'))
        action = ((actions or {}).get(rid) or ACTIONS.get(rid)
                  or (f'GOTO:{page}' if page and page != s.name else '-'))

        # A control that HOLDS something is cooked once per value it can hold
        # (WHEN <value> inside each state) and given the action that changes
        # it: a checkbox toggles, a radio button takes over its group, a
        # slider is moved with left and right, and so is a spinner (see
        # spinner), which goes round where a slider stops. [None] is the
        # ordinary row, which has nothing to hold.
        values, initial, low = [None], 0, 0
        if row['class'] == 'XuiCheckbox':
            action, values, initial = 'TOGGLE', [0, 1], int(rid in CHECKED)
        elif row['class'] == 'XuiRadioButton':
            action, values, initial = f'RADIO:{group}', [0, 1], int(rid in CHECKED)
        elif row['class'] == 'XuiSlider':
            low = rp.get('RangeMin', 0) or 0
            action, values = 'SLIDER', list(range(int((rp.get('RangeMax', 100) or 100) - low) + 1))
            initial = int((rp.get('Value', 0) or 0) - low)
        elif row.get('choices'):
            action, values, initial = 'SPIN', list(range(len(row['choices']))), row['initial']
        # A key of the keyboard: (a variable of the app's state, its caption
        # under each value of it -- None where the key isn't there). Cooked
        # once per caption, under IF.
        modes = row.get('modes')
        if modes:
            values = list(range(len(modes[1])))

        c.emit('ITEM', rid, link(nav['up'], 'up'), link(nav['down'], 'down'), link(nav['left'], 'left', left),
               link(nav['right'], 'right', right), action, 0.0)
        c.lines += row.get('lines', [])             # its ACTIONIF / HIDEIF lines
        if len(values) > 1 and not modes:
            c.emit('VALUES', len(values), initial)
        frames = {nf['name'] for nf in c.visual_templates[rp['Visual']].get('named_frames') or []}
        for state in ('normal', 'focus', 'press'):
            c.lines.append(f'STATE {state}')
            for value in values:
                if modes:
                    if modes[1][value] is None:
                        continue
                    c.emit('IF', modes[0], value)
                    override(row, {'Text': modes[1][value]})
                elif value is not None:
                    c.emit('WHEN', value)
                if row['class'] == 'XuiSlider':
                    override(row, {'Value': low + value})
                    slider_visual(s.ctx, row)
                if row.get('choices'):
                    override(row, {'Text': row['choices'][value]})
                if row.get('edge'):
                    edge(c, row, x, y)
                visual = props_of(row)['Visual'].split(':')[0]
                check = 'Check' if value == 1 and row['class'] in ('XuiCheckbox', 'XuiRadioButton') else ''
                # EndPress, not Press: Press is the frame the flash STARTS on,
                # which is the focused look again. A control with no press of
                # its own (a slider, a ticked box) just stays focused.
                frame = {'normal': 'Normal' + check, 'focus': 'Focus' + check,
                         'press': 'EndPress' if 'EndPress' in frames and not check else 'Focus' + check}[state]
                if row.get('unfocused') and state == 'focus':
                    frame = 'Normal'            # pressed by its button, never focused: a pass code's cells
                c.state_overrides[rid] = frame
                # A control whose state lives in a nested timeline has a
                # template variant per state instead (see slider_visual).
                variant = f'{visual}:{frame}'
                override(row, {'Visual': variant if variant in c.visual_templates else visual})
                c.walk(row, x, y, 1.0)
                # What a row brings with it: drawn over it always ('with'),
                # or only while it has focus ('focus_with').
                for extra, ex, ey in row.get('with', []) + (row.get('focus_with', []) if state != 'normal' else []):
                    if extra.get('gamerpic'):
                        gamer_picture(c, extra, ex, ey)
                    else:
                        c.walk(extra, ex, ey, 1.0)
                if modes:
                    c.lines.append('ENDIF')
        c.lines.append('ENDITEM')
    s.write()
    print(f'   rows: {", ".join(ids)}')


def cook_page(layout, ctx, name=None, prepare=None, also=None):
    """A full-size guide page: everything in the layout that isn't a row is
    cooked where it stands, plus the legend where the frame parks it at full
    size. The frame itself (blade, clock, gamerpic) is GuideFrame's.

    `prepare(hud)` is for a page the runtime fills in before showing it: it
    gets the parsed layout to do the same to, and returns the actions of any
    rows it made ({row id: action}). `also(c)` cooks whatever else is the
    page's own, after everything that isn't a row and before the rows."""
    s = Scene(name or PAGES[layout], ctx, f'{layout}, a full-size guide page.')
    c = s.c
    c.emit('FOCUSBLEND', FOCUS_BLEND_SECONDS)
    c.emit('PRESS', PRESS_SECONDS, 0.0)
    hud = parse(layout_path(ctx, layout))['children'][0]
    actions = prepare(hud) if prepare else None
    rows, entries = [], {}
    collect_rows(hud, 0.0, 0.0, {}, ctx, rows, entries, statics=c)
    # The panel that describes the focused row (see META).
    panel = next((k for k in hud['children'] if props_of(k).get('Id') == 'metaPanelScene'), None)
    for row in (r[0] for r in rows):
        if panel and props_of(row)['Id'] in ctx['meta']:
            text = copy.deepcopy(child(panel, 'Text1'))
            override(text, {'Text': ctx['meta'][props_of(row)['Id']]})
            row['focus_with'] = [(text,) + tuple(props_of(panel)['Position'][:2])]
    if also:
        also(c)
    legend(c, ctx, props_of(hud))
    emit_items(s, hud, rows, entries, '-', '-', actions)


# The on-screen keyboard (vk.xex's `vk` pack). KeyboardMain.xur is the page,
# and its KeyboardPlaceholder hosts KeyboardBase.xur: a prompt, the edit box,
# five rows of ten keys with Backspace and Space under them, three buttons
# down each side. The captions of those are the pack's strings. The
# characters are in no layout or string table: they are vk.xex's own tables,
# fifty to a set, read out of its unpacked image (at 0x1a70, 0x1af8 and
# 0x1c08) -- letters, symbols and accents, the letters' set holding its last
# two as 0xBD and 0xBE, Windows' key codes for '-' and '.', and nothing for
# its fifth row.
#
# Which set shows is the app's `kbmode`: 0 letters, 1 capitals, 2 symbols,
# 3 accents, 4 capital accents. A key is cooked once per caption (IF kbmode),
# types its caption (KEY:, with an ACTIONIF per mode) and isn't there in the
# modes that have nothing for it (HIDEIF). What is typed is the app's text
# "typed", which the edit box shows; the title and the prompt are those of
# the row that opened the keyboard (main.cpp; settings_pages.py's TYPED).
KEYBOARD = ['1234567890' 'qwertyuiop' 'asdfghjkl-' 'zxcvbnm_@.',
            '1234567890' ',;:\'"!?¡¿%' '[]{}`$£«»#' '<>()€¥¤~^\\' '|=*/+-@_&.',
            '1234567890' 'àáâãäåñèéê' 'ëþçýìíîïùú' 'ûòóôõðø×œæ' 'ßÿºª.üµš_ö']
# The buttons either side, top to bottom, and what each is beside: (id, the
# pack's caption, action, [ACTIONIF kbmode value action ...]).
#
# Each button, and Backspace and Space, wears the picture of the controller
# button that presses it from anywhere (KEYBOARD_BUTTONS, by the layout's own
# ImagePath): cooked as SHORTCUT lines. Every key and button has a one-unit
# outline (edge). The edit box is a TEXT with a CARET: the skin's XuiCaret is
# a bar 3 wide, (0, 138, 0) at 0.65, and where it stands is the app's
# `kbcaret`, which Left and Right move. A photo of the console's keyboard (the
# user's) shows only A Select / B Back under it, so the layout's own X and Y
# legends are left off.
KEYBOARD_BUTTONS = {'btn_x.png': 'X', 'btn_y.png': 'Y', 'lb.png': 'LB', 'rb.png': 'RB', 'lt.png': 'LT',
                    'rt.png': 'RT', 'caps.png': 'LS', 'done.png': 'START'}
KEYBOARD_SIDES = [
    [('Key.Left', 'Left', 'KEY:LEFT', []),
     ('Key.Prev', ['Symbols', 'Symbols', 'Alphabet', 'Symbols', 'Symbols'], 'SET:kbmode=2', [(2, 'SET:kbmode=0')]),
     ('Key.Caps', 'Caps', 'SET:kbmode=1', [(1, 'SET:kbmode=0'), (2, '-'), (3, 'SET:kbmode=4'), (4, 'SET:kbmode=3')])],
    [('Key.Right', 'Right', 'KEY:RIGHT', []),
     ('Key.Next', ['Accents', 'Accents', 'Accents', 'Alphabet', 'Alphabet'], 'SET:kbmode=3',
      [(3, 'SET:kbmode=0'), (4, 'SET:kbmode=0')]),
     ('Key.OK', 'Done', 'KEY:DONE', [])],
]


def capital(ch):
    """A key's capital, where it has one of its own that the app's font pages hold."""
    up = ch.upper()
    return up if len(up) == 1 and ord(up) < 0x180 else ch


def keyboard(ctx):
    shortcuts = []

    def prepare(hud):
        override(hud, {'LegendX': '', 'LegendY': ''})
        override(child(hud, 'Text_Header'), {'Text': '{kbtitle}'})
        base = parse(layout_path(ctx, 'KeyboardBase.xur'))['children'][0]
        holder = child(hud, 'KeyboardPlaceholder')
        holder['children'] = base['children']
        override(holder, {'DefaultFocus': props_of(base)['DefaultFocus'].split('\\')[-1]})
        override(child(base, 'FormattedText'), {'Show': False})     # a second edit box, for text being converted
        override(child(base, 'XuiText1'), {'Text': '{kbprompt}'})
        override(child(base, 'XuiEdit1'), {'Text': '{typed}'})
        keys = child(base, 'KeysGroup')
        letters, symbols, accents = KEYBOARD
        sets = [letters, ''.join(map(capital, letters)), symbols, accents, ''.join(map(capital, accents))]
        actions = {'Key.BS': 'KEY:BACK', 'Key.Spc': 'KEY:SPACE'}
        sides = [[key[0] for key in side] for side in KEYBOARD_SIDES]
        for key in keys['children']:
            kid = props_of(key).get('Id') or ''
            if kid in ('Key.JComma', 'Key.JPeriod'):
                override(key, {'Show': False})                      # the Japanese sets' own
            if key['class'] == 'XuiButton':
                key['edge'] = True
                button = KEYBOARD_BUTTONS.get((props_of(key).get('ImagePath') or '').lower())
                if button and props_of(key).get('Show', True):
                    shortcuts.append((button, kid))
            if kid.count('_') != 1 or not kid[4:].replace('_', '').isdigit():
                continue
            r, col = (int(n) for n in kid[4:].split('_'))
            texts = [s[r * 10 + col] if r * 10 + col < len(s) else None for s in sets]
            # Left and right are the keys beside it, then the button its row is beside.
            beside = min(r // 2, 2)
            override(key, {'Text': next(t for t in texts if t),
                           'NavLeft': f'Key.{r}_{col - 1}' if col else sides[0][beside],
                           'NavRight': f'Key.{r}_{col + 1}' if col < 9 else sides[1][beside]})
            actions[kid] = f'KEY:{texts[0]}' if texts[0] else '-'
            if len(set(texts)) > 1:
                key['modes'] = ('kbmode', texts)
                key['lines'] = ([f'ACTIONIF kbmode {m} KEY:{t}' for m, t in enumerate(texts) if m and t]
                                + [f'HIDEIF kbmode {m}' for m, t in enumerate(texts) if t is None])
        # The buttons: a row of keys to one side, the bottom row's two for the lowest.
        inward = (('NavRight', ['Key.0_0', 'Key.2_0', 'Key.BS']), ('NavLeft', ['Key.0_9', 'Key.2_9', 'Key.Spc']))
        for side, (direction, targets) in zip(KEYBOARD_SIDES, inward):
            for (kid, caption, action, others), target in zip(side, targets):
                key = child(keys, kid)
                override(key, {'Text': caption if isinstance(caption, str) else caption[0], direction: target})
                if not isinstance(caption, str):
                    key['modes'] = ('kbmode', caption)
                key['lines'] = [f'ACTIONIF kbmode {m} {a}' for m, a in others]
                actions[kid] = action
        # Backspace and Space: in the photo each is as wide as five keys, side by side under the ten (the
        # layout has them 134 wide, and its code evidently sizes them).
        first = props_of(child(keys, 'Key.0_0'))['Position'][0]
        pitch = props_of(child(keys, 'Key.0_1'))['Position'][0] - first
        wide = 5 * pitch - (pitch - props_of(child(keys, 'Key.0_0'))['Width'])
        for kid, x, left, right in (('Key.BS', first, 'Key.Caps', 'Key.Spc'), ('Key.Spc', first + 5 * pitch, 'Key.BS', 'Key.OK')):
            key = child(keys, kid)
            _x, y, z = props_of(key)['Position']
            override(key, {'Position': (x, y, z), 'Width': wide, 'NavLeft': left, 'NavRight': right})
        return actions

    def also(c):
        c.emit('ENTER', 'kbmode', 0)
        c.emit('ENTER', 'kbcaret', 9999)        # past the end is the end: it opens after what is there
        for button, kid in shortcuts:
            c.emit('SHORTCUT', button, kid)
        caret = props_of(ctx['templates']['XuiCaret']['children'][0])
        fill = {f['property']: f['value'] for f in caret['Fill']}['FillColor']
        box = next(i for i, line in enumerate(c.lines) if line.endswith(' {typed}'))
        c.lines.insert(box + 1, f"CARET kbcaret {int(round(255 * caret['Opacity']))} {fill['r']} {fill['g']} {fill['b']}")
    cook_page('KeyboardMain.xur', ctx, 'GuideKeyboard', prepare, also)


# Pass code entry (signin.xex's passcode.xur): four boxes that fill as a code
# goes in, over a grid of the ten buttons a code is made of. The layout is a
# scene for something to host and says nothing of what does, so the page
# around it is judgment: the dashboard's own title and prompt for making a
# code and for entering it again (dashCSettingsStrings), where the keyboard
# has its title and prompt, and the layout's block centred under them.
#
# How far in the code is, is the app's `passn`: 0-3 buttons of the code, 4-7
# of the second time through. A cell counts one (SET:, an ACTIONIF per count)
# and the eighth press leaves. The two codes are not compared.
#
# A cell is pressed by the button drawn in it and nothing else (SHORTCUT, by
# the layout's own pictures: PASSCODE_BUTTONS) -- the triggers, the bumpers,
# the d-pad's four directions, X and Y. So nothing has focus to show, the
# d-pad does not move about, and A does nothing (a SHORTCUT to no item).
PASSCODE_BUTTONS = {'lt_32x.png': 'LT', 'lb_32x.png': 'LB', 'rb_32x.png': 'RB', 'rt_32x.png': 'RT',
                    'left_d.png': 'LEFT', 'up_d.png': 'UP', 'down_d.png': 'DOWN', 'right_d.png': 'RIGHT',
                    'x-button_full.png': 'X', 'y-button_full.png': 'Y'}
PASSCODE = [('Create Pass Code', 'Use X, Y, triggers, bumpers, and the D-pad to create your 4-button pass code.'),
            ('Verify Pass Code', 'Please enter your pass code again.')]
PASSCODE_AT = (291.0, 140.0)                    # the block: 270 wide, centred on the 852
PASSCODE_TITLE = (156.0, 36.0, 540.0, 26.0)     # KeyboardMain.xur's Text_Header
PASSCODE_PROMPT = (156.0, 75.0, 540.0, 40.0)


def passcode(ctx):
    kept = {}

    def prepare(hud):
        override(hud, {'LegendB': 'Back'})
        for kid in hud['children']:
            x, y, z = props_of(kid)['Position']
            override(kid, {'Position': (x + PASSCODE_AT[0], y + PASSCODE_AT[1], z)})
        kept['label'] = copy.deepcopy(child(hud, 'txtEnterPasscode'))
        kept['boxes'] = [child(hud, f'Passcode{n}') for n in range(4)]
        for node in [child(hud, name) for name in ('txtEnterPasscode', 'txtIncorrectPasscode', 'navBack')] + kept['boxes']:
            override(node, {'Show': False})             # the boxes and the words are cooked per count (also)
        scene = child(hud, 'PasscodeGridScene')
        grid = child(scene, 'Grid')
        gx, gy = props_of(scene)['Position'][:2]
        order = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0]          # as the grid has them: five to a row
        cell_id = lambda r, col: f'btn{order[r * 5 + col % 5]}'
        actions = {}
        for i, digit in enumerate(order):
            r, col = divmod(i, 5)
            cell = child(grid, f'btn{digit}')
            cell['class'] = 'XuiButton'                 # a label in the layout: the buttons press it, not A
            override(cell, {'NavLeft': cell_id(r, col - 1), 'NavRight': cell_id(r, col + 1),
                            'NavUp': cell_id(1 - r, col), 'NavDown': cell_id(1 - r, col)})
            picture = child(scene, f'Img{i + 1}')       # the button that is this digit, drawn with its cell
            cell['with'] = [(copy.deepcopy(picture), gx, gy)]
            cell['unfocused'] = True
            kept.setdefault('shortcuts', []).append(
                (PASSCODE_BUTTONS[props_of(picture)['ImagePath'].split('/')[-1].lower()], f'btn{digit}'))
            override(picture, {'Show': False})
            cell['lines'] = [f'ACTIONIF passn {n} SET:passn={n + 1}' for n in range(1, 7)] + ['ACTIONIF passn 7 BACK']
            actions[f'btn{digit}'] = 'SET:passn=1'
        override(hud, {'DefaultFocus': 'btn1'})
        return actions

    def also(c):
        c.emit('ENTER', 'passn', 0)
        for button, cell in kept['shortcuts']:
            c.emit('SHORTCUT', button, cell)
        c.emit('SHORTCUT', 'A', '-')
        for n in range(8):
            c.emit('IF', 'passn', n)
            for text, (x, y, w, h), visual in zip(PASSCODE[n // 4], (PASSCODE_TITLE, PASSCODE_PROMPT),
                                                  ('Label_Head', 'Label_Body')):
                label = copy.deepcopy(kept['label'])
                override(label, {'Text': text, 'Position': (x, y, 0.0), 'Width': w, 'Height': h, 'Visual': visual})
                c.walk(label, 0.0, 0.0, 1.0)
            for k, box in enumerate(kept['boxes']):
                override(box, {'Show': True})
                c.state_overrides[props_of(box)['Id']] = 'NormalCheck' if k < n % 4 else 'Normal'
                c.walk(box, 0.0, 0.0, 1.0)
            c.lines.append('ENDIF')

    cook_page('passcode.xur', ctx, 'GuidePasscode', prepare, also)


# Create Profile and Download Profile (createprofile.xex's `cp` pack). A new
# profile is its name, typed -- the pack's "Enter Profile Name" over the
# keyboard (the action TYPE:newprofile) -- and then Congratulations.xur, "Save
# Profile": the profile's gamer card beside Join Xbox Live / Customize
# Profile / Done, each with its own words in the panel, which the keyboard's
# Done goes on to ("SAY newprofile.then"). Download Profile is
# RecoveryIntro.xur, whose one button needs Xbox Live.
#
# Judgment: that the name starts empty and has no prompt; that Done leaves
# for the dashboard's home; that Customize Profile is the profile's edit
# page. Nothing is created: the name shows on the card and nowhere else, and
# whoever was signed in still is. Not cooked: choosing where to save the
# profile, "Creating profile", and joining Xbox Live (AddXboxLive.xur).
def save_profile(ctx):
    def prepare(hud):
        override(child(hud, 'gamerTagText'), {'Text': '{newprofile}'})
        for number in ('GSTextnumber', 'titlesPlayednumber', 'achievementsnumber'):
            override(child(hud, number), {'Text': '0'})
        # The panel's words are the focused button's (the layout's own timeline shows one of three).
        for button in ('doneButton', 'editProfileButton', 'getLiveButton'):
            panel = child(hud, button[:-len('Button')] + 'MetaPanel')
            said = copy.deepcopy(panel)
            override(said, {'Show': True, 'Opacity': 1.0})
            child(hud, button)['focus_with'] = [(said, 0.0, 0.0)]
            override(panel, {'Show': False})
        return {'doneButton': 'DASH:Hub', 'getLiveButton': '-'}

    def also(c):
        c.emit('SAY', 'newprofile.title', 'Enter Profile Name')
        c.emit('SAY', 'newprofile.max', 15)     # a gamertag's length: judgment
        c.emit('SAY', 'newprofile.then', 'GuideSaveProfile')
    cook_page('Congratulations.xur', ctx, 'GuideSaveProfile', prepare, also)


def download_intro(ctx):
    def prepare(hud):
        override(child(hud, 'metaText'), {'Text': "Download your profile if you:\r\n\r\n• Want to set up your account "
                                                  "on this console.\r\n\r\n• Deleted your profile.\r\n\r\n• Are "
                                                  "on someone else's console."})     # CreateProfile_Custom's
        return {'continueButton': '-'}
    cook_page('RecoveryIntro.xur', ctx, 'GuideDownloadIntro', prepare)


def gamer_picture_rows(ctx):
    """807_EditGamerTile. Its first two rows wear btn_2Line-icon-content, the
    two-line button whose image is shown in its own colours rather than
    tinted with the row's state like an icon -- because what the runtime
    puts there is the picture the row changes, and the white glyph authored
    in its place is only what shows when there is none. So: the gamer
    picture on the first row. (There is no personal picture, so the second
    keeps its glyph.)"""
    def prepare(hud):
        row = child(hud, 'changeGamerTileButton')
        p = props_of(row)
        slot = props_of(next(k for k in ctx['templates'][p['Visual']]['children']
                             if k['class'] == 'XuiImagePresenter'))
        picture = {'class': 'XuiImage', 'properties': [], 'children': [], 'gamerpic': True}
        override(picture, {'Position': slot['Position'], 'Width': slot['Width'], 'Height': slot['Height']})
        override(row, {'ImagePath': ''})
        row['with'] = [(picture,) + tuple(p['Position'][:2])]
    return prepare


def gamer_tiles(ctx):
    """809_ChangeGamerTile's grid: the stock pictures, each wearing itself."""
    def prepare(hud):
        fill_list(ctx, child(hud, 'gamerTileList'), [{'Id': f'tile{n}', 'ImagePath': t} for n, t in enumerate(TILES)])
        return {f'tile{n}': f'PIC:{n}' for n in range(len(TILES))}
    return prepare


def game_defaults(ctx):
    """830_GamerPreferences' list: DEFAULTS' categories, each row described
    in the panel beside it and opening its own page of spinners."""
    gp = ctx['gp']

    def prepare(hud):
        fill_list(ctx, child(hud, 'preferenceList'), [{'Id': gp[k], 'Text': gp[k]} for k in DEFAULTS])
        ctx['meta'].update({gp[k]: gp[DEFAULTS[k][0]] for k in DEFAULTS})
        return {gp[k]: f'GOTO:GuideGameDefaults{gp[k]}' for k in DEFAULTS}
    return prepare


def game_defaults_category(ctx, category):
    """832_PreferenceCategory for one of DEFAULTS' categories: its header,
    and as many of the layout's eight spinners as it has settings."""
    gp = ctx['gp']

    def prepare(hud):
        override(child(hud, 'headerText'), {'Text': gp[DEFAULTS_HEADER].replace('%s', gp[category])})
        for i, (setting, values, initial) in enumerate(DEFAULTS[category][1], 1):
            label = child(hud, f'setting{i}Label')
            hud['children'].remove(label)
            override(label, {'Text': gp[setting]})
            spinner(ctx, child(hud, f'setting{i}Value'), label, [gp[v] for v in values], initial)
    return prepare


def dress(node, table):
    """Apply {Id: {property: value}} over a tree."""
    if props_of(node).get('Id') in table:
        override(node, table[props_of(node)['Id']])
    for kid in node['children']:
        dress(kid, table)


def cook_recent(layout, ctx, name=None, dressing=None, focus=1):
    """QuickLaunch.xur, "Recent": a full-size page that is itself a tab scene.
    Its three headers are an accordion -- the open tab's is wide and carries
    its caption, the other two shrink to their icons -- and its timeline is
    the switches between them (1To2, 2To1, 2To3, 3To2), cooked as tracks
    exactly as GuideMain's are.

    Each tab is a list the runtime fills. With nothing played or downloaded
    it shows the tab's own "nothing here" label instead, which is what is
    cooked. The tabs themselves are the page's only focus stops: one ITEM
    per tab, drawing nothing, named for its number -- Guide.cpp plays
    "<from>To<to>" when focus moves between two items so named."""
    s = Scene(name or PAGES[layout], ctx, f'{layout}, a full-size guide page with tabs of its own.' if not name else
              f'A RECONSTRUCTION, not a real layout: {layout} re-captioned (see FRIENDS in guide_build.py).')
    c = s.c
    hud = parse(layout_path(ctx, layout))['children'][0]
    dress(hud, dressing or {})
    tabs = child(hud, 'Tabscene')
    emit_marks(c, tabs)

    def walk(node):
        c.walk(node, 0.0, 0.0, 1.0)

    for node in tabs['children']:
        p = props_of(node)
        if node['class'] == 'XuiSoundXAudio':
            continue
        if node['class'] == 'XuiScene':
            for kid in node['children']:
                kp = props_of(kid)
                if kp.get('Id') == 'labelNoGames':
                    override(kid, {'Show': True})       # the empty list's stand-in
                elif (kp.get('Visual') or '').startswith('Loading_'):
                    override(kid, {'Show': False})      # the spinner while the list loads
            track(c, tabs, node, walk)
        elif is_nine_grid(c, p):
            track_sizes(c, tabs, node, lambda n: blade(c, s.assets, n))
        else:
            track_tints(c, tabs, node, walk)
    legend(c, ctx, props_of(hud))
    for node in hud['children']:
        if node is not tabs:
            walk(node)

    count = props_of(tabs)['TabCount']
    c.emit('DEFAULTFOCUS', focus)       # the tab the page opens on
    for k in range(1, count + 1):
        c.emit('ITEM', k, '-', '-', k - 1 if k > 1 else '-', k + 1 if k < count else '-', '-', 0.0)
        c.lines.append('ENDITEM')
    s.write()


def cook_notify(ctx):
    """The notification popup: notify.xur is a 460x87 scene holding one
    control, and that control's visual, scr_Notification, is everything -- a
    pill that unrolls from an orb, the orb's ring and logo, an icon the logo
    takes turns with, two lines of text, and the 241-frame timeline that
    brings it on (TransTo), holds it (the frames from `loop`, round and
    round) and takes it off again (TransFrom), with its sound keyed on the
    first frame.

    Each child of the visual is a TRACK, as in the guide's own scenes, so the
    motion is the file's. Three kinds of body:

      painted    the pill (bgCurve1, round), the orb's ring (bg) and the
                 quadrant light (Indicator1) -- see paint. A mirror that is
                 the object's own and never keyed is painted in; a Scale the
                 timeline keys is left to the track.
      as found   the dark disc and the green burst behind the logo: plain
                 radial fills, which are GRADIENT_RECTs.
      filled in  the icon and the text, which the runtime binds: once per
                 entry of NOTIFICATIONS, each under IF notify <n>.

    The four Indicators are the ring of light's quadrants, one per player;
    the timeline shows all four and the runtime keeps the one for the player
    the popup is for. Player one's, top left, is cooked -- as it is on the
    guide's own ring."""
    s = Scene('GuideNotify', ctx, 'notify.xur: the notification popup, here an achievement.')
    c = s.c
    scene = parse(layout_path(ctx, 'notify.xur'))['children'][0]
    control = props_of(scene['children'][0])
    visual = copy.deepcopy(ctx['templates'][control['Visual']])
    vp = props_of(visual)
    emit_marks(c, visual)

    def fit(node, parent_w, parent_h, to_w, to_h):
        """A child anchored into a parent that is not the size it was authored for."""
        q = props_of(node)
        x, y, w, h = anchor_rect(q['Position'], q['Width'], q['Height'], parent_w, parent_h, to_w, to_h,
                                 q.get('Anchor', 0) or 0)
        override(node, {'Position': (x, y, q['Position'][2]), 'Width': w, 'Height': h})
        return q['Width'], q['Height']

    # The control is wider than its visual (460 against 370): the pill and
    # the text box are anchored to both sides and take up the difference.
    for kid in visual['children']:
        was = fit(kid, vp['Width'], vp['Height'], control['Width'], control['Height'])
        if props_of(kid).get('Id') == 'bgCurve1':
            for part in kid['children']:
                fit(part, was[0], was[1], props_of(kid)['Width'], props_of(kid)['Height'])

    # Onto the screen: every Position the timeline keys is in the control's
    # space, so they move with it.
    ox = (s.canvas[0] - props_of(scene)['Width']) / 2.0
    oy = ctx['canvas_h'] * NOTIFY_BOTTOM - props_of(scene)['Height']
    shift = lambda pos: (pos[0] + ox, pos[1] + oy, pos[2])
    for kid in visual['children']:
        override(kid, {'Position': shift(props_of(kid)['Position'])})
    for tl in visual['timelines']:
        for kf in tl['keyframes']:
            for prop in kf['properties']:
                if prop['property'] == 'Position':
                    prop['value'] = shift(prop['value'])

    c.emit('MARK', 'Notify.Count', len(NOTIFICATIONS))

    def wording(text, blanks):
        text = ctx['xam'][text] if isinstance(text, int) else text
        blanks = (PROFILE['gamertag'],) if blanks is None else blanks
        # (the table spells its line break out, and has C's wide-string blank)
        return (text.replace('%ls', '%s') % blanks).replace('\\n', '\n')

    def each(node, values):
        """`node` once per notification, each under its IF."""
        for n, value in enumerate(values):
            c.emit('IF', 'notify', n)
            override(node, value)
            c.walk(node, 0.0, 0.0, 1.0)
            c.lines.append('ENDIF')

    for kid in visual['children']:
        q = props_of(kid)
        ident = q.get('Id')
        keys = keyframes_of(visual, ident)
        if kid['class'] == 'XuiSoundXAudio' or ident in ('Indicator2', 'Indicator3', 'Indicator4'):
            continue
        if keys and 'Position' in keys[0][1]:
            override(kid, {'Position': keys[0][1]['Position']})     # where its track is cooked from
        if kid['class'] in ('XuiImage', 'XuiImagePresenter'):
            kid['class'] = 'XuiImage'
            if q.get('ImagePath'):
                track(c, visual, kid, lambda n: c.walk(n, 0.0, 0.0, 1.0))
            else:
                track(c, visual, kid, lambda n: each(n, [{'ImagePath': icon} for icon, _t, _b in NOTIFICATIONS]))
        elif kid['class'] == 'XuiTextPresenter':
            # One line or two, centred as a block in a box that is
            # single-line by its style: a wording's own line break is what
            # makes it two.
            _lines, line_h = break_lines('x', q['Width'], q['PointSize'], ctx['font'], wrap=False)
            adjust = q.get('LineSpacingAdjust', 0) or 0
            line_h += adjust - (1 << 32) if adjust >= (1 << 31) else adjust
            kid['class'] = 'XuiText'
            texts = []
            for _icon, text, blanks in NOTIFICATIONS:
                text = wording(text, blanks)
                block = line_h * (text.count('\n') + 1)
                texts.append({'Text': text, 'Height': block,
                              'Position': (q['Position'][0], q['Position'][1] + (q['Height'] - block) / 2.0, 0.0)})
            track(c, visual, kid, lambda n: each(n, texts))
        elif ident in ('logoback', 'explosion'):
            track(c, visual, kid, lambda n: c.walk(n, 0.0, 0.0, 1.0))
        else:
            override(kid, {'Show': True, 'Opacity': 1.0})
            if any('Scale' in values for _kf, values in keys):
                override(kid, {'Scale': (1.0, 1.0, 1.0)})                   # the track's to apply
                line = painted(c, s.assets, 'notify_' + ident, kid)
            else:
                line = painted(c, s.assets, 'notify_' + ident, kid)         # its own mirror painted in...
                override(kid, {'Scale': (1.0, 1.0, 1.0), 'Rotation': (0.0, 0.0, 0.0, 1.0)})    # ...and so spent
            track(c, visual, kid, lambda n, line=line: c.lines.append(line))
    s.write()


def cook_dialog(name, spec, ctx):
    """A message box (see DIALOGS): the skin's visual for that many buttons,
    filled in, where hudbkgnd's ErrorHostElement puts it. 'MARK Host.Error'
    is how Guide.cpp knows to host it in the frame's Error state rather than
    as a full-size page."""
    s = Scene(name, ctx, 'a message box, hosted in the frame\'s Error state.')
    c = s.c
    c.emit('FOCUSBLEND', FOCUS_BLEND_SECONDS)
    c.emit('PRESS', PRESS_SECONDS, 0.0)
    c.emit('MARK', 'Host.Error', 1)
    buttons = spec['buttons']
    box = copy.deepcopy(ctx['templates'][f'XuiMessageBox{len(buttons) + 1}'])['children'][0]
    host = props_of(child(child(ctx['bkgnd']['children'][0], 'ErrorHUD'), 'ErrorHostElement'))

    title = child(box, 'Title')
    title['class'] = 'XuiText'          # a literal, not a presenter waiting for data
    override(title, {'Text': spec['title']})
    # The body is a read-only multiline edit; break it to its own width here.
    body = child(box, 'MessageText')
    points = props_of(ctx['templates'][props_of(body)['Visual']]['children'][0])['PointSize']
    override(body, {'Text': '\n'.join(break_lines(spec['text'], props_of(body)['Width'], points, ctx['font'])[0])})
    override(child(box, 'btnA'), {'Text': 'Select'})    # XamStrings.xus
    override(child(box, 'btnB'), {'Text': 'Back'})
    actions = {}
    for i, (caption, action) in enumerate(buttons):
        override(child(box, f'Button{i}'), {'Text': caption, 'Visual': 'XuiButton'})
        actions[f'Button{i}'] = action
    override(box, {'DefaultFocus': 'Button0'})

    rows, entries = [], {}
    collect_rows(box, host['Position'][0], host['Position'][1], {}, ctx, rows, entries, statics=c)
    emit_items(s, box, rows, entries, '-', '-', actions)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--search-dir', action='append', default=[], required=True,
                    help='extracted packs to find PNGs and hudbkgnd.xur in (repeatable)')
    ap.add_argument('--hud-dir', default=os.path.join(HERE, '..', 'XZP', 'hud.xex.hud'))
    ap.add_argument('--skin', default=os.path.join(HERE, '..', 'XZP', 'huduiskin.xex.skin', 'skin.xur'),
                    help="the GUIDE's skin.xur -- not the dashboard's, whose templates share names")
    ap.add_argument('--out-dir', default=os.path.join(HERE, '..', 'assets', 'guide'))
    ap.add_argument('--font', default=os.path.join(HERE, '..', 'assets', 'fonts', 'xenonclatin_full.ttf'))
    args = ap.parse_args()

    guide = parse(os.path.join(args.hud_dir, 'GuideMain.xur'))
    index = build_image_index(args.search_dir)
    # A bare file name in the guide's skin is a file of xam's own skin pack,
    # and one of them is a different picture in shrdres: scroll.png, the list
    # arrow, points left in the skin (which is what the skin's Left / Right /
    # Up visuals leave alone, mirror and turn) and up in shrdres.
    for d in args.search_dir:
        index.update(build_image_index([os.path.join(d, 'skin')]))
    ctx = {
        'index': index,
        'baked': tempfile.mkdtemp(),    # see mirrored
        'templates': load_visual_templates([args.skin]),
        'guide': guide,
        'bkgnd': parse(find_skin_files(args.search_dir, 'hudbkgnd.xur')[0]),
        'hud_dir': args.hud_dir,
        'search_dirs': args.search_dir,
        'legends': [],
        'font': args.font,
        'out_dir': args.out_dir,
        'canvas_h': props_of(guide)['Height'],
        'tab_boxes': {},
    }
    # gamerprofile.xex's own strings, beside its layouts (xus -> json: XZP/xzp_extract.py).
    with open(os.path.join(os.path.dirname(layout_path(ctx, 'GamerCardScene.xur')),
                           'GamerProfile_Custom.strings.json'), encoding='utf-8') as f:
        ctx['gp'] = [t if isinstance(t, str) else t['text'] for t in json.load(f)['strings']]
    ctx['meta'] = {row: ctx['gp'][n] for row, n in META.items()}
    # xam's own strings ride in the skin's module (huduiskin.xex's `xam` pack).
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(args.skin))), 'huduiskin.xex.xam',
                           'XamStrings.strings.json'), encoding='utf-8') as f:
        ctx['xam'] = [t if isinstance(t, str) else t['text'] for t in json.load(f)['strings']]
    cook_main(ctx)
    for n in range(len(TAB_SCENES)):
        cook_tab(n, ctx)
        cook_tab(n, ctx, TAB_SCENES_OUT, TAB_CONTENT_OUT)
    filled = {'807_EditGamerTile.xur': gamer_picture_rows(ctx), '809_ChangeGamerTile.xur': gamer_tiles(ctx),
              '830_GamerPreferences.xur': game_defaults(ctx)}
    for layout in PAGES:
        if layout == 'QuickLaunch.xur':
            cook_recent(layout, ctx)
        else:
            cook_page(layout, ctx, prepare=filled.get(layout))
    for category in DEFAULTS:
        cook_page('832_PreferenceCategory.xur', ctx, 'GuideGameDefaults' + ctx['gp'][category],
                  game_defaults_category(ctx, category))
    cook_recent('QuickLaunch.xur', ctx, 'GuideFriends', FRIENDS)
    cook_recent('QuickLaunch.xur', ctx, 'GuideMessages', FRIENDS, focus=2)
    for name, spec in DIALOGS.items():
        cook_dialog(name, spec, ctx)
    keyboard(ctx)
    passcode(ctx)
    save_profile(ctx)
    download_intro(ctx)
    cook_notify(ctx)
    cook_frame(ctx)     # last: it cooks every legend the scenes above asked for
    shutil.rmtree(ctx['baked'])


if __name__ == '__main__':
    main()
