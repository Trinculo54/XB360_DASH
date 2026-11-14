"""
Cook the pages UNDER System Settings -- Console Settings and everything it
leads to, Kinect Settings, Family Settings -- as scene manifests.

settings_build.py cooks the System Settings screen itself, from a table typed
out of SystemScene.xur. This does the rest of the tree, and types nothing:
every one of these pages is the same form, and says what is on it.

  the form     a DashScene 880x480 wearing BackgroundPanel, a RightPanelShader
               over its right half, one XuiCommonList at (0, 1) down the left,
               a header label the shell draws as the page's title, and in the
               right panel "Current Setting", the setting's value, and a
               paragraph about the page. settings_build.py already draws that
               frame and that kind of row; its Builder and numbers are used.

  the rows     properties of the list itself, one \\r\\n-separated string per
               column: ItemsText (the captions), ItemsImage (icons, on the
               few pages that have them) and ItemsNavPath (the .xur each row
               opens). A list with captions and paths is a MENU; one with
               captions only is the OPTIONS a setting can take.

So the tree is walked from its roots along ItemsNavPath, and each page found
is cooked as a scene named for its .xur. What that cannot reach:

  - A list the dashboard's code fills. Console Settings is one, but its .xur
    still names its rows' ids (btnDisplay, btnAudio ...), which are matched
    here to the pages they open by name; each row is captioned with the
    header of the page it opens, which is that page's own word for itself.
    That match is A JUDGMENT (see CONSOLE_SETTINGS), and the console hides
    some of these rows by region. Display is another, with no ids to go on
    (see DISPLAY). And four lists of a setting's values -- time zones,
    locales, languages, HDTV modes -- come from string tables: see filled().
  - A list longer than it is tall. Those scroll: see `scrolls` in build().
  - What a setting is set TO. Nothing here knows the console's settings, so
    an options page opens with its first option as the current one -- which
    is not necessarily the console's default -- and choosing another changes
    "Current Setting" until the app closes.
  - Pages that are not this form. Three shapes of them are handled all the
    same: a row that is a button of its own rather than a list's item; a
    CHOOSER -- a one-row list turned with left and right (Xbox Live Vision's
    three, and Family Timer's time limit), cooked as a row that holds a
    value; and a radio button or a checkbox (Connected Devices), cooked as a
    row that holds a tick. Set Pass Code's two-line buttons are drawn by
    their own visual (SET_TO).

    python3 settings_pages.py --search-dir ../DASH/dash_bin_embedded_assets \\
                              --search-dir ../XZP --out-dir ../assets/scenes
"""
import argparse
import copy
import json
import os
import re
import shutil
import tempfile

from PIL import Image

import xur_extract
import xur_schema
from network_pages import override
from settings_build import (CANVAS, FOCUS_BLEND_SECONDS, HEADER_COLOR, HEADER_POINT_SIZE, HEADER_POS,
                            HEADER_SIZE, HIGHLIGHT_COLOR, LEGEND, LEGEND_START_X, LIST_REL, PANEL_H, PANEL_STOPS,
                            PANEL_W, ROW_H, ROW_ICON, ROW_ICON_FOCUS, ROW_ICON_REST, ROW_POINT_SIZE,
                            ROW_TEXT_FOCUS, ROW_TEXT_REST, ROW_TEXT_W, ROW_TEXT_X, RULE_COLOR,
                            SCENE_ORIGIN, SHADER_REL, SHADER_STOPS, Builder)
from xur_cook import (BACKDROP_STOPS, HALIGN_CENTER, HALIGN_LEFT, LEGEND_GLYPH_IMAGE, LEGEND_GLYPH_LETTER_COLOR,
                      LEGEND_GLYPH_OFFSET, LEGEND_GLYPH_POINT_SIZE, LEGEND_GLYPH_SIZE, LEGEND_LABEL_COLOR,
                      LEGEND_LABEL_OFFSET, LEGEND_LABEL_POINT_SIZE, LEGEND_Y, POINT_SIZE_TO_PIXELS, VALIGN_CENTER,
                      VALIGN_TOP, Cooker, anchor_rect, build_image_index, find_skin_files, load_visual_templates,
                      resolve_image_path)

# Where the walk starts: the System Settings rows that open a page of this
# form, and Family Settings, which the hub's Family tile opens
# (dashhome-offline.xml: EcNavToFamilySettings).
ROOTS = ['dashSysCslSet', 'KinectSettings', 'dashSysCslSetPControl',
         # Storage, Computers and TV: the same frame, but their lists are the
         # runtime's (devices, PCs, TV providers). Storage and TV have a
         # label for when there is nothing to list, and that is what shows.
         'DeviceSelector', '742_SelectNetworkDevice', 'LiveTVSettings',
         # What Configure Network's buttons open (network_pages.py cooks that
         # page itself): the pages their PressPaths name.
         '2016_EditIPSettings', '2033_DNSConfig', '2036_PPoESettings', '2038_ConsoleInformation',
         'dashSysLiveVision']

# Lists whose captions are FIELDS to fill in -- a user name, a host name --
# not the values of a setting. Nothing says which a list is; these two are
# told apart by what they say.
FIELDS = {'2036_PPoESettings', '2038_ConsoleInformation', '2018_IPSettings', '2034_DNSSettings'}

# Rows whose job is known without their code, from what they say: they
# leave. (Nothing is saved, so "Save and Exit" only exits.)
LEAVES = {'Done', 'Save and Exit', 'Continue'}

# An option that opens a page of its own rather than just being chosen:
# "Manual" on Edit IP Settings and on Edit DNS Settings leads to the fields
# to fill in. Nothing in a layout says so -- these are the two pages in the
# network pack with those headers and those fields, numbered next after the
# pages they follow.
OPENS = {('2016_EditIPSettings', 'Manual'): '2018_IPSettings', ('2033_DNSConfig', 'Manual'): '2034_DNSSettings'}

# Rows that ask first. The question is a message box, which is xam's, so it
# is cooked with the guide (guide_build.py: DIALOGS) and opened there.
ASKS = {'Reset to Default Settings': 'GUIDE:GuideResetControls'}

# Rows that are typed. The on-screen keyboard is xam's too (guide_build.py:
# keyboard), and main.cpp opens it on any item a manifest names with
# "SAY <scene>/<item>.type <field>" -- under the field's title and prompt,
# starting from what the field holds. (page, row) -> (field, title, prompt,
# what it holds at first, whether that means "nothing yet"). The titles and
# prompts are the packs' own (dashCSettingsStrings; the network pack's
# Strings); which row gets which is by what they say.
TYPED = {
    ('dashSysCslSetConsoleName', 'Custom...'): ('consolename', 'Console Name', 'Enter a name for this console.',
                                                'Xbox 360', False),
    ('2036_PPoESettings', 'User Name'): ('pppoeuser', 'User Name', 'Enter your PPPoE user name.', '', False),
    ('2036_PPoESettings', 'Password'): ('pppoepassword', 'Password', 'Enter your PPPoE password.', '', False),
    ('2036_PPoESettings', 'Service Name'): ('pppoeservice', 'Service Name', 'Enter your PPPoE service name.', '', False),
    ('2038_ConsoleInformation', 'Host Name'): ('hostname', 'Host Name', 'Enter a host name.', '', False),
    ('dashSysCslSetPControlPasscode', 'btnHintA'): ('passanswer', 'Pass Code Answer',
                                                    'Please enter an answer to your pass code question.',
                                                    'Pass Code Reset Answer Not Set', True),
}
# ...and the row that takes a pass code, which is xam's as well (guide_build.py: passcode).
PRESSES = {'btnPasscode': 'GUIDE:GuidePasscode'}


def typed(b, scene, item, key):
    """The SAY lines that make `item` a typed row, if (scene, key) is one. Returns its field, or None."""
    if (scene, key) not in TYPED:
        return None
    field, title, prompt, first, unset = TYPED[(scene, key)]
    b.emit('SAY', f'{scene}/{item}.type', field)
    b.emit('SAY', f'{field}.title', title)
    b.emit('SAY', f'{field}.prompt', prompt)
    if first:
        b.emit('SAY', field, first)
    if unset:
        b.emit('SAY', f'{field}.unset', first)
    return field

# Console Settings' rows, which its code fills: the ids are the page's own
# (the string beside its list), each matched by name to the page it opens.
# btnPolicy, between Auto-Downloads and Remote Control, has no page that
# answers to it and is left out; btnInfo's is the one whose header is
# "System Info", whatever its file is called.
CONSOLE_SETTINGS = [
    ('btnDisplay', 'dashSysCslSetDisplay'), ('btnAudio', 'dashSysCslSetAudio'),
    ('btnLanguageLocale', 'dashSysCslSetLangLocale'), ('btnClock', 'dashSysCslSetClock'),
    ('btnStartupShutdown', 'dashSysCslSetStartupShutdown'), ('btnAutoPlay', 'dashSysCslSetMediaAutoLaunch'),
    ('btnAutoDownloads', 'dashSysCslSetAutoDownloads'),
    # Not among the ids, and so from memory of the console alone: Closed
    # Captioning is a Console Settings row there.
    ('btnClosedCaption', 'dashSysCslSetClosedCaption'),
    ('btnRemote', 'dashSysCslSetRemoteC'),
    # Not among the ids either, but dashCSettingsStrings lists "Console Name"
    # and "Connected Devices" in one run with the other nine captions. WHERE
    # in the list is a guess.
    ('btnConnectedDevices', 'dashSysCslSetConsoleDiscoverable'),
    ('btnConsoleName', 'dashSysCslSetConsoleName'),
    ('btnInfo', 'dashSysCslSetPolicyInfo_System'),
]

# Two more lists the code fills, with no ids to go on at all -- only the
# pages themselves, sitting in the pack unreachable. Which pages are whose
# rows, and in what order, is from memory of the console and from the file
# names (dashSysCslSetDisplay*, dashSysCslSetPControl*): A JUDGMENT, more so
# than Console Settings. PAL Settings, 3D TV and TV Size are left out: the
# first is for PAL consoles, and where the other two sit isn't known.
DISPLAY = [('row1', 'dashSysCslSetDisplayHiDef'), ('row2', 'dashSysCslSetDisplayFormat'),
           ('row3', 'dashSysCslSetOutputLevels'), ('row4', 'dashSysCslSetColorSpace'),
           ('row5', 'dashSysCslSetDisplayDiscovery'), ('row6', 'dashSysCslSetScreensaver')]
RATINGS_AND_CONTENT = [('row1', 'dashSysCslSetPControlGame'), ('row2', 'dashSysCslSetPControlVideo')]
CODE_FILLED = {'dashSysCslSet': CONSOLE_SETTINGS, 'dashSysCslSetDisplay': DISPLAY,
               'dashSysCslSetPControlContentSettings': RATINGS_AND_CONTENT}

# What an option list the code fills opens set to (see filled()). Nothing
# here knows the console's settings: this is a U.S. console in English at
# 720p -- the language the strings are in and the canvas's own size; the
# locale and the time zone are stand-ins. Every other options page opens on
# its first option.
CURRENT = {'dashSysCslSetLanguage': 'English', 'dashSysCslSetCountry': 'United States',
           'dashSysCslSetClockTimeZone': 'GMT-08 Pacific (U.S. & Canada)', 'dashSysCslSetDisplayHiDef': '720p'}


def strings(tables, name):
    with open(tables[name], encoding='utf-8') as f:
        return [t if isinstance(t, str) else t['text'] for t in json.load(f)['strings']]


def said(cs):
    """
    What Connected Devices' panel says. That page is not a list: it is two
    radio groups and a checkbox, and its panel is one label the code fills.
    dashCSettingsStrings has, for each of the two settings, "<name>: Off",
    "<name>: On" and a paragraph, and one sentence about the checkbox. That
    the label shows the focused control's setting and then its paragraph is
    A JUDGMENT. By radio group, or the checkbox's id: (the button ticked
    when the setting is on, the off text, the on text, the paragraph).
    """
    smart, play = cs.index('Xbox SmartGlass apps: Off'), cs.index('Play To: Off')
    return {'radgrpCompanion': ('radbtnCompanionOn', cs[smart], cs[smart + 1], cs[smart + 2].replace('\r', '')),
            'radgrpPlayTo': ('radbtnPlayToOn', cs[play], cs[play + 1], cs[play + 3].replace('\r', '')),
            'chkPlayToInterrupt': (None, None, None, cs[play + 2])}


def filled(tables):
    """
    The option lists the dashboard's code fills from a string table: the
    pages are in the pack with empty lists, and the captions are in the
    tables, each kind in one run. Which run is whose is plain from what they
    say. Their ORDER on screen is not in the rip:

      time zones   xam's "GMT+04 Abu Dhabi" ... (XamStrings), stored by city;
                   listed here west to east, as a clock list is.
      locales      dashCSettingsStrings' countries, stored by country code;
                   listed here by name. "Other", in the same run, is left out.
      languages    stored by language code, and listed so. Only those the
                   font can draw: the Japanese, Korean and Chinese entries
                   need glyphs xenonclatin doesn't have.
      HDTV         480p / 720p / 1080i / 1080p. (The "D2 (525p / 480p)"
                   strings beside them are a Japanese D-terminal cable's.)

    Three more things come out of the same table: the pass code reset
    questions, Family Timer's time limit (a spinner, not a list) and the
    three ratings lists, whose rows carry an icon and a paragraph. See below.
    """
    cs, xam = strings(tables, 'dashCSettingsStrings'), strings(tables, 'XamStrings')

    def run(first, end):
        start = cs.index(first)
        return [s.strip() for s in cs[start:cs.index(end, start)]]

    def offset(zone):                       # 'GMT-04:30 Caracas' -> -4.5
        hours, _, minutes = zone[3:].split(' ')[0].partition(':')
        return int(hours) + int(minutes or 0) / 60.0 * (-1 if zone[3] == '-' else 1)
    # Family Timer's spinner, the one thing here that is not a list: a time
    # limit. Only the four words are the table's ("Hour", "Hours", "Minute",
    # "Minutes"). The quarter-hour steps up to a day are from memory of the
    # console's DAILY limit, and how the two parts are put together is A
    # GUESS; the weekly limit (in hours) is not cooked.
    hour, hours, minutes = (cs[cs.index(word)] for word in ('Hour', 'Hours', 'Minutes'))
    limits = [' '.join(part for part in (f'{n // 60} {hour if n // 60 == 1 else hours}' if n >= 60 else '',
                                         f'{n % 60} {minutes}' if n % 60 else '') if part)
              for n in range(15, 24 * 60 + 1, 15)]
    # The three ratings lists, as a U.S. console has them: the ESRB's levels,
    # the MPAA's, the TV Parental Guidelines'. Each board's sit together in
    # the table, caption and paragraph ("Allow games and game content rated
    # EC or E. ..."), a level's paragraph being the one whose first sentence
    # ends with it; "All Games" / "All Movies" / "All TV Shows" are each
    # followed by theirs. A row is its caption, its icon (the pack's own, by
    # name: ESRB_E10.png, US_TV-PG.png; the open lock for "all"; the pack has
    # none for the MPAA) and the paragraph, which the panel shows while the
    # row has focus. Their ORDER on screen is a guess: everything, then the
    # levels from the oldest audience down.
    def ratings(anchor, back, size, levels, everything, icon):
        start = cs.index(anchor) - back
        block = [s.replace('\r', '') for s in cs[start:start + size]]
        about = {level: next(s for s in block if s.startswith('Allow')
                             and s.split('\n')[0].split()[-1] == level + '.') for level in levels}
        return ([dict(label=everything, icon=icon(''), nav='', sys=True,
                      says=cs[cs.index(everything) + 1].replace('\r', ''))]
                + [dict(label=level, icon=icon(level), nav='', says=about[level]) for level in levels])
    lists = {
        'dashSysCslSetPControlGame': ratings(
            'E10+', 1, 10, ['M', 'T', 'E10+', 'E', 'EC'], 'All Games',
            lambda level: f"ESRB_{level.replace('+', '')}.png" if level else 'AllowAllGames.png'),
        'dashSysCslSetPControlVideoMovie': ratings(
            'PG-13', 3, 8, ['R', 'PG-13', 'PG', 'G'], 'All Movies', lambda level: ''),
        'dashSysCslSetPControlVideoTV': ratings(
            'TV-14', 0, 8, ['TV-MA', 'TV-14', 'TV-PG', 'TV-G'], 'All TV Shows',
            lambda level: f'US_{level}.png' if level else 'AllowAllTV.png'),
        'dashSysCslSetPControlFamilyTimer': limits,
        # The five pass code reset questions, in the table's order.
        'dashSysCslSetPControlPasscodeHint': run(SET_TO['navHintQ'], 'Enter Reset Answer'),
        'dashSysCslSetClockTimeZone': sorted((s for s in xam if re.match(r'GMT[+-]\d\d(:\d\d)? \S', s)), key=offset),
        'dashSysCslSetCountry': sorted(set(run('United Arab Emirates', 'None')) - {'Other'}),
        'dashSysCslSetLanguage': [s for s in run('Deutsch', 'United Arab Emirates') if max(s) < 'ᄀ'],
        'dashSysCslSetDisplayHiDef': [s for s in ('480p', '720p', '1080i', '1080p') if s in cs],
    }
    # A self-check: the runs are the ones looked at, and each holds its page's current setting.
    assert [len(lists[name]) for name in CURRENT] == [12, 41, 83, 4], {k: len(v) for k, v in lists.items()}
    assert all(CURRENT[name] in lists[name] for name in CURRENT)
    assert limits[:5] == ['15 Minutes', '30 Minutes', '45 Minutes', '1 Hour', '1 Hour 15 Minutes'], limits[:5]
    assert set(SET_TO.values()) <= set(cs), 'a second line is not in dashCSettingsStrings'
    questions = lists['dashSysCslSetPControlPasscodeHint']
    assert len(questions) == 5 and all(q.endswith('?') for q in questions), questions
    return lists


# One-row lists turned with left and right, by visual: where their two arrows
# sit -- the left one's x and y, the right one's x back from the row's right
# edge and its y -- and how big they are. The visuals' own ScrollLeft /
# ScrollRight: (10, 14) and (418, 15) of 450 for a chooser; (8, 12) and
# (340, 12) of 373, at Scale 1.3 of 16, for the spinner.
SPINNERS = {'XuiListChooser_No_Kill': (10.0, 14.0, 32.0, 15.0, 16.0),
            'btn_horizontal_spinner_Arrows': (8.0, 12.0, 33.0, 12.0, 20.8)}

# A list's rows are 47 high (XuiList's control_ListItem), one line, the
# caption 30 in. In these two list visuals an item is 72 high and its caption
# wraps, centred top to bottom in its box (TextStyle 20481). By list visual:
# (row height, the caption's box (x, y, width, height) in a 440-wide row, the
# icon's box or None).
#   List_1string_2line   btn_1string_2line: txt_line3 (30, 3) 390x65 of 450.
#   lstRatingView        metabtn_2line_PControl: txt_0 (89, 0) 331x72 of 450;
#                        icon1 and sysIcon (10, 3) 64x64.
TALL = {'List_1string_2line': (72.0, (30.0, 3.0, 380.0, 65.0), None),
        'lstRatingView': (72.0, (89.0, 0.0, 321.0, 72.0), (10.0, 3.0, 64.0, 64.0))}

# Where a page says something about its focused row, by the visual of what
# holds it: (how far down in it the text starts, PointSize, alpha).
# metaScene_1line_14pt's Pane_txt is at (0, 40), 14 points, at 60% opacity;
# edit_AutoScroll_Gray's Text is 16 points in argb(153, ...).
SAYS = {'metaScene_1line_14pt': (40.0, 14.0, 153), 'edit_AutoScroll_Gray': (0.0, 16.0, 153)}

# Set Pass Code's three buttons are two lines (btn_twoline_Edit): a caption,
# and under it -- the part that takes focus -- what is set, which the code
# fills. These are dashCSettingsStrings' words for nothing being set, and the
# first of its five reset questions. That they are what a console with no
# pass code shows is A JUDGMENT.
SET_TO = {'btnPasscode': 'Pass Code Not Set', 'navHintQ': 'Favorite fictional character?',
          'btnHintA': 'Pass Code Reset Answer Not Set'}

# What a page says in its right-hand panel when the device it sets up isn't
# there. The layout holds this beside what it shows when the device IS there
# (a camera's picture), in the same place; with no camera, this is the one.
ABSENT = {'NoCameraTextField'}

# A row with no icon is the plain list item (the skin's XuiList -> XuiButton,
# and btn_single the same): its caption starts 30 in, where an icon row's
# starts 50.
PLAIN_TEXT_X = 30.0

# The right-hand panel, from the labels every one of these pages carries:
# "Current Setting" (Label_MetaHead_1line_white, PointSize 22, alpha 250) at
# (470, 20); the value (Label_Body_1line_White, 18) at (480, 60); the
# paragraph (Label_BodyWhite, 16, at 60% opacity) at (470, 100), 380 wide.
META_HEAD = (470.0, 20.0, 380.0, 40.0, 22.0, 250)
META_VALUE = (480.0, 60.0, 370.0, 35.0, 18.0, 255)
META_BODY = (470.0, 100.0, 380.0, 16.0, 153)
WHITE = {'a': 255, 'r': 235, 'g': 235, 'b': 235}
CAPTION_COLOR = {'a': 255, 'r': 15, 'g': 18, 'b': 20}
# The label visuals that are captions on the left of a page. Both are drawn
# in Label_Body_1line's colour. Label_BodyWhite_18 is WHITE in the skin, which
# on the pale left panel cannot be read ("Xbox SmartGlass Apps" over its radio
# group would vanish); what the console does about that is its code's, so the
# dark colour there is A GUESS.
CAPTIONS = ('Label_Body_1line', 'Label_BodyWhite_18')
# ...and those that are the right panel's paragraph: PointSize, alpha.
BODIES = {'Label_BodyWhite': META_BODY[3:], 'Label_BodyWhite_18': (18.0, 255)}
ARROW_TINT = {'r': 51, 'g': 51, 'b': 51}


def props(node):
    return xur_schema.resolve_properties(node['class'], node['properties']) or {}


def paragraph(b, text, x, y, w, points, alpha, first=0):
    """A wrapped paragraph of the right panel, from its line `first` down."""
    line_h = points * POINT_SIZE_TO_PIXELS * 1.34
    for n, line in enumerate(b.wrap(text, w - 10.0, points), first):
        if line:
            b.text(line, x, y + n * line_h, w, line_h, points, WHITE, HALIGN_LEFT, VALIGN_TOP, alpha)


def fitted(image_index, name, x, y, w, h):
    """Where an image goes in a box: as big as fits with its shape kept, centred. The image
    presenters here are SizeMode 16, and the ESRB icons are 43x64 in boxes 64 square; that 16
    means this rather than a stretch is AN ASSUMPTION (a stretched ESRB icon is not one)."""
    iw, ih = Image.open(resolve_image_path(name, image_index)).size
    scale = min(w / iw, h / ih)
    return x + (w - iw * scale) / 2.0, y + (h - ih * scale) / 2.0, iw * scale, ih * scale


def read_page(path):
    """What a settings page's .xur says is on it (see the top of this file)."""
    scene = xur_extract.parse_xur8(open(path, 'rb').read()).root_object['children'][0]
    page = dict(title='', head='', desc='', rows=[], panel=False, empty=None, captions=[], absent=None,
                body=META_BODY, list_at=LIST_REL[:2], row_h=ROW_H, wraps=None, icon_at=None, says_at=None,
                current_image=None)
    lists, buttons = [], []

    def walk(node, shown=True, x=0.0, y=0.0, group=None):
        p = props(node)
        shown = shown and p.get('Show', True)
        if node is not scene:
            x, y = x + p['Position'][0], y + p['Position'][1]
        text = (p.get('Text') or '').replace('\r', '').strip()
        if node['class'] in ('XuiCommonList', 'XuiList') and p.get('Visual') in SPINNERS:
            # A chooser: one row, its values turned through with left and
            # right. With none of its own, the code fills it: see collect().
            values = [t.strip() for t in (p.get('ItemsText') or '').replace('\r', '').split('\n') if t.strip()]
            if shown:
                buttons.append(dict(label='', icon='', nav='', values=values, arrows=SPINNERS[p['Visual']],
                                    rect=(x, y, p['Width'], p['Height'])))
        elif node['class'] in ('XuiCommonList', 'XuiList'):
            lists.append(p)
            page['list_at'] = (x, y)
            page['list_looks'] = (p.get('Visual') or 'XuiList', p['Width'], p['Height'])
            page['row_h'], page['wraps'], page['icon_at'] = TALL.get(p.get('Visual'), (ROW_H, None, None))
        elif (p.get('Visual') in CAPTIONS and text and shown
              and (x < PANEL_W / 2.0 or p['Visual'] not in BODIES)):    # in the right panel, a paragraph
            # A caption over a chooser, or over a radio group.
            page['captions'].append((text, x, y, p['Width'], p['Height']))
        elif p.get('Id') in ABSENT:
            page['absent'] = (text, x, y, p['Width'])
        elif p.get('Visual') == 'RightPanelShader':
            page['panel'] = True
        elif p.get('Visual') in SAYS and p.get('Id') != 'metaDescription':
            # (TV Ratings has a second, metaDescription, beside its icon: what goes in it isn't known.)
            down, points, alpha = SAYS[p['Visual']]
            page['says_at'] = (x, y + down, p['Width'], points, alpha)
        elif p.get('Id') == 'imgCurrentSetting':
            page['current_image'] = (x, y, p['Width'], p['Height'])     # the current setting, as its icon
        elif p.get('Visual') == 'Label_Head':
            page['title'] = text
        elif p.get('Visual') == 'Label_MetaHead_1line_white':
            page['head'] = text
        elif p.get('Visual') in BODIES and x >= PANEL_W / 2.0 and len(text) >= len(page['desc']):
            # The right panel's paragraph, and where and how it is set.
            page['desc'], page['body'] = text, (x, y, p['Width']) + BODIES[p['Visual']]
        elif p.get('Id') == 'txt_EmptyList' and shown:
            # What a page whose list is the runtime's shows when there is
            # nothing to list: "No storage devices found."
            page['empty'] = (text, p['Position'][0], p['Position'][1], p['Width'], p['Height'])
        elif node['class'] in ('XuiRadioButton', 'XuiCheckbox') and shown and text:
            # A control that holds a tick: a row of its own, drawn by its
            # class's visual (which XUI falls back to when it names none).
            if not p.get('Visual'):
                override(node, {'Visual': node['class']})
            buttons.append(dict(id=p['Id'], label=text, icon='', nav='', rect=(x, y, p['Width'], p['Height']),
                                node=node, at=(x - p['Position'][0], y - p['Position'][1]), group=group))
        elif (node['class'] in ('XuiNavButton', 'XuiButton') and shown and text
              and not (p.get('Visual') or '').startswith('legend_')):
            # A row that is a button of its own rather than a list's item.
            buttons.append(dict(label=text, icon='', nav=os.path.splitext(p.get('PressPath') or '')[0],
                                rect=(x, y, p['Width'], p['Height'])))      # ...so it sits where it was put
            if p.get('Id') in SET_TO:
                # Two lines, the second what is set: drawn by its own visual.
                buttons[-1].update(id=p['Id'], node=node, at=(x - p['Position'][0], y - p['Position'][1]))
        for kid in node['children']:
            walk(kid, shown, x, y, p.get('Id') if node['class'] == 'XuiRadioGroup' else None)
    walk(scene)

    # How many rows the list shows at once: XUI's list lays out whole items.
    page['fits'] = int(lists[0]['Height'] // page['row_h']) if len(lists) == 1 else None
    if len(lists) == 1:
        column = lambda key: [t.strip() for t in (lists[0].get(key) or '').replace('\r', '').split('\n')]
        captions, icons, paths = column('ItemsText'), column('ItemsImage'), column('ItemsNavPath')
        for i, caption in enumerate(captions):
            if caption:
                page['rows'].append(dict(label=caption, icon=icons[i] if i < len(icons) else '',
                                         nav=os.path.splitext(paths[i])[0] if i < len(paths) else ''))
    # Captions with nowhere to go are a setting's values -- when they are a
    # list's. A button with nowhere to go just isn't wired.
    page['options'] = bool(page['rows']) and not any(row['nav'] for row in page['rows'])
    page['rows'] += buttons
    return page


def collect(files, options):
    """Every page reachable from ROOTS, by scene name. `options`: see filled()."""
    pages, queue = {}, list(ROOTS)
    while queue:
        name = queue.pop(0)
        if name in pages or name.lower() not in files:
            continue
        page = read_page(files[name.lower()])
        # What the code fills on this page goes into its spinner, if it has
        # an empty one, and otherwise into its list. A spinner left empty is
        # dropped.
        spinner = next((row for row in page['rows'] if row.get('values') == []), None)
        if spinner:
            spinner['values'] = options.get(name, [])
        else:
            page['rows'] += [caption if isinstance(caption, dict) else dict(label=caption, icon='', nav='')
                             for caption in options.get(name, [])]
        page['rows'] = [row for row in page['rows'] if row.get('values') != []]
        page['options'] = (page['options'] and name not in FIELDS) or (name in options and not spinner)
        for row in page['rows']:
            row['nav'] = OPENS.get((name, row['label']), row['nav'])
        for ident, target in CODE_FILLED.get(name, []):
            if target.lower() in files:
                page['rows'].append(dict(id=ident, label=read_page(files[target.lower()])['title'], icon='',
                                         nav=target))
        pages[name] = page
        queue += [row['nav'] for row in page['rows'] if row['nav']]
    return pages


def build(name, page, pages, image_index, font_path, cooker, panel):
    """`cooker` draws a tick row's visual; `panel` is said()."""
    b = Builder(image_index, font_path)
    b.comment(f'{name}.xur -- a settings page: its title, rows, links and description are its own.')
    b.comment('See settings_pages.py.')
    b.emit('CANVAS', *CANVAS)
    rows = page['rows']
    for i, row in enumerate(rows):
        row.setdefault('id', f'row{i + 1}')
    # Where each row sits: a list's rows run down from the list's own place,
    # and a row that is a control of its own is where the layout put it. Up
    # and down go by that order, top to bottom (which is what the layouts'
    # own NavUp / NavDown chains say, where they say anything).
    ox, oy = SCENE_ORIGIN
    lx, ly = page['list_at']
    k = 0
    for row in rows:
        if row.get('rect'):
            row['place'] = (ox + row['rect'][0], oy + row['rect'][1], row['rect'][2])
        else:
            row['place'] = (ox + lx, oy + ly + k * page['row_h'], LIST_REL[2])
            k += 1
    rows.sort(key=lambda row: row['place'][1])
    options = page.get('options', False)
    # The option that is the current setting, which is where the page opens.
    current = next((i for i, row in enumerate(rows) if options and row['label'] == CURRENT.get(name)), 0)
    if rows:
        b.emit('DEFAULTFOCUS', rows[current]['id'])
    b.emit('FOCUSBLEND', FOCUS_BLEND_SECONDS)
    # A list with more rows than it shows SCROLLS: its rows are cooked where
    # they would sit if it were tall enough (LAYER list, ROW), and the app
    # moves them under a window (LIST) the height of the rows that fit. The
    # window starts a unit above the first row, where its highlight does.
    scrolls = page['fits'] is not None and sum(1 for row in rows if not row.get('rect')) > page['fits']
    if scrolls:
        b.emit('LIST', ox + lx, oy + ly - 1.0, LIST_REL[2], page['fits'] * page['row_h'] + 1.0)

    b.lines.append('LAYER fixed')
    arrows = []
    if scrolls:
        # The list's own two arrows, from its visual (XuiList's, when it
        # names none or one the skin lacks): control_ScrollUp and
        # control_ScrollDown, pinned to the list's bottom right -- which is
        # the strip under the last row that fits. Each is its visual's image
        # (scr_ScrollEndUp / Down: updefault / downdefault, tinted
        # (51,51,51) in ScrollMore) and shows only while there are more rows
        # that way, which the app says (listup / listdown).
        looks, lw, lh = page['list_looks']
        template = cooker.visual_templates.get(looks) or cooker.visual_templates['XuiList']
        tp = props(template)
        for kid in template['children']:
            kp = props(kid)
            more = {'control_ScrollUp': 'listup', 'control_ScrollDown': 'listdown'}.get(kp.get('Id'))
            if more:
                ax, ay, aw, ah = anchor_rect(kp['Position'], kp['Width'], kp['Height'], tp['Width'], tp['Height'],
                                             lw, lh, kp.get('Anchor', 0))
                image = next(props(c)['ImagePath'] for c in cooker.visual_templates[kp['Visual']]['children']
                             if c['class'] == 'XuiImage')
                arrows.append((more, image, ox + lx + ax, oy + ly + ay, aw, ah))
        assert len(arrows) == 2, f'{name}: its list ({looks}) has no scroll arrows'
    b.gradient(0.0, 0.0, CANVAS[0], CANVAS[1], BACKDROP_STOPS)
    b.gradient(ox, oy, PANEL_W, PANEL_H, PANEL_STOPS)
    # A page with nothing to list shows its "nothing here" label and none of
    # the rest: no right-hand panel, no "Current Setting".
    nothing = page['empty'] and not rows
    if page['panel'] and not nothing:
        sx, sy, sw, sh = SHADER_REL
        b.gradient(ox + sx, oy + sy, sw, sh, SHADER_STOPS)
    b.text(page['title'], HEADER_POS[0], HEADER_POS[1], HEADER_SIZE[0], HEADER_SIZE[1],
           HEADER_POINT_SIZE, HEADER_COLOR, HALIGN_LEFT, VALIGN_CENTER)
    if nothing:
        # XuiLabelCenterJustify: PointSize 18, (51,58,64), centred both ways.
        text, ex, ey, ew, eh = page['empty']
        lines = b.wrap(text, ew, ROW_POINT_SIZE)
        line_h = ROW_POINT_SIZE * POINT_SIZE_TO_PIXELS * 1.34
        top = oy + ey + (eh - line_h * len(lines)) / 2.0
        for i, line in enumerate(lines):
            b.text(line, ox + ex, top + i * line_h, ew, line_h, ROW_POINT_SIZE, ROW_TEXT_REST,
                   HALIGN_CENTER, VALIGN_CENTER)
        page = dict(page, head='', desc='')
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

    for more, image, ax, ay, aw, ah in arrows:
        b.emit('IF', more, 1)
        b.image(image, ax, ay, aw, ah, ARROW_TINT)
        b.lines.append('ENDIF')

    # Captions of the page's own, over its choosers: Label_Body_1line,
    # PointSize 18, (15,18,20), top-left.
    for text, cx, cy, cw, ch in page['captions']:
        b.text(text, ox + cx, oy + cy, cw, ch, ROW_POINT_SIZE, CAPTION_COLOR, HALIGN_LEFT, VALIGN_TOP)
    if page['absent']:
        # PointSize 18, white, in place of everything else in the panel.
        text, ax, ay, aw = page['absent']
        line_h = ROW_POINT_SIZE * POINT_SIZE_TO_PIXELS * 1.34
        for i, line in enumerate(b.wrap(text, aw - 10.0, ROW_POINT_SIZE)):
            b.text(line, ox + ax, oy + ay + i * line_h, aw, line_h, ROW_POINT_SIZE, WHITE, HALIGN_LEFT, VALIGN_TOP)
        page = dict(page, head='', desc='')

    # The right-hand panel.
    hx, hy, hw, hh, hpt, ha = META_HEAD
    if page['head']:
        b.text(page['head'], ox + hx, oy + hy, hw, hh, hpt, WHITE, HALIGN_LEFT, VALIGN_CENTER, ha)
    if options and page['head']:
        # Under "Current Setting", on a page that has it (the pass code
        # question page fills its panel with a paragraph instead).
        # The value is whichever option is the current one: each row is an
        # item holding 0 or 1 (RADIO), and its caption is cooked here under
        # IF <row> 1 -- the item's value is the scene's variable of that name.
        # A page with an image for it (imgCurrentSetting) shows the option's
        # icon there instead, when the option has one.
        vx, vy, vw, vh, vpt, va = META_VALUE
        for row in (r for r in rows if not r.get('rect')):
            b.emit('IF', row['id'], 1)
            if row['icon'] and page['current_image']:
                cx, cy, cw, ch = page['current_image']
                b.image(row['icon'], *fitted(image_index, row['icon'], ox + cx, oy + cy, cw, ch))
            else:
                # (a typed option's value is what was typed: "Custom...")
                shown = '{%s}' % TYPED[(name, row['label'])][0] if (name, row['label']) in TYPED else row['label']
                b.text(shown, ox + vx, oy + vy, vw, vh, vpt, WHITE, HALIGN_LEFT, VALIGN_CENTER, va)
            b.lines.append('ENDIF')
    bx, by, bw, bpt, ba = page['body']
    paragraph(b, page['desc'], ox + bx, oy + by, bw, bpt, ba)

    groups = set()
    for i, row in enumerate(rows):
        rx, ry, rw = row['place']
        if row.get('node'):
            # A control drawn by its own visual, which xur_cook's Cooker
            # cooks at rest and focused. A radio button or a checkbox is
            # cooked unticked and ticked too: it holds 0 or 1 (WHEN). A group
            # opens on its first button and a box unticked, as an options
            # page opens on its first option. A two-line button (SET_TO) is
            # given its second line first.
            group = row.get('group')
            ticks = ('', 'Check') if row['node']['class'] in ('XuiRadioButton', 'XuiCheckbox') else ('',)
            action = f"GOTO:{row['nav']}" if row['nav'] in pages else '-'
            if len(ticks) > 1:
                action = f'RADIO:{group}' if group else 'TOGGLE'
            action = PRESSES.get(row['id'], action)
            b.emit('ITEM', row['id'], rows[i - 1]['id'], rows[(i + 1) % len(rows)]['id'], '-', '-', action, 0.0)
            field = typed(b, name, row['id'], row['id'])
            if len(ticks) > 1:
                b.emit('VALUES', 2, int(group is not None and group not in groups))
                groups.add(group)
            if row['id'] in SET_TO:
                # The Cooker gives an instance's Text to the visual's caption
                # and nothing to its other presenters, so the second line is
                # made a literal in a copy of the visual (as network_pages does).
                look = props(row['node'])['Visual']
                visual = copy.deepcopy(cooker.visual_templates[look])
                for presenter in visual['children']:
                    if presenter['class'] == 'XuiTextPresenter' and props(presenter).get('DataAssociation') == 1:
                        presenter['class'] = 'XuiText'
                        override(presenter, {'Text': '{%s}' % field if field else SET_TO[row['id']], 'Show': True})
                cooker.visual_templates[f"{look}@{row['id']}"] = visual
                override(row['node'], {'Visual': f"{look}@{row['id']}"})
            for state in ('Normal', 'Focus'):
                b.lines.append(f'STATE {state.lower()}')
                b.lines.append('LAYER fixed')
                for value, tick in enumerate(ticks):
                    if len(ticks) > 1:
                        b.emit('WHEN', value)
                    cooker.lines = []
                    cooker.state_overrides[row['id']] = state + tick
                    cooker.walk(row['node'], ox + row['at'][0], oy + row['at'][1], 1.0)
                    b.lines += cooker.lines
                if len(ticks) > 1:
                    b.lines.append('ENDIF')
                if state == 'Focus' and (group or row['id']) in panel:
                    # What the panel says while this control has focus: its
                    # setting, then the paragraph about it (see said()).
                    on, off_text, on_text, about = panel[group or row['id']]
                    bx, by, bw, bpt, ba = page['body']
                    line_h = bpt * POINT_SIZE_TO_PIXELS * 1.34
                    first = 0
                    if on:
                        for value, status in enumerate((off_text, on_text)):
                            b.emit('IF', on, value)
                            b.text(status, ox + bx, oy + by, bw, line_h, bpt, WHITE, HALIGN_LEFT, VALIGN_TOP, ba)
                        b.lines.append('ENDIF')
                        first = 2
                    paragraph(b, about, ox + bx, oy + by, bw, bpt, ba, first)
            b.used_images.update(cooker.used_images)
            b.lines.append('ENDITEM')
            continue
        if row.get('values'):
            # A chooser or a spinner (SPINNERS): the value centred in the
            # row between its two arrows, which are the skin's up arrow
            # turned a quarter turn each way and tinted (51,51,51). Left and
            # right go round its values (SPIN).
            ax, ay, zx, zy, size = row['arrows']
            b.emit('ITEM', row['id'], rows[i - 1]['id'], rows[(i + 1) % len(rows)]['id'], '-', '-', 'SPIN', 0.0)
            b.emit('VALUES', len(row['values']), 0)
            for focused in (False, True):
                b.lines.append('STATE focus' if focused else 'STATE normal')
                b.lines.append('LAYER fixed')
                if focused:
                    b.rect(rx, ry - 1.0, rw, ROW_H + 1.0, HIGHLIGHT_COLOR)
                else:
                    b.rect(rx, ry - 1.0, rw, 1.0, RULE_COLOR)
                    b.rect(rx, ry + ROW_H - 1.0, rw, 1.0, RULE_COLOR)
                b.image('arrow_left.png', rx + ax, ry + ay, size, size, ARROW_TINT)
                b.image('arrow_right.png', rx + rw - zx, ry + zy, size, size, ARROW_TINT)
                for n, value in enumerate(row['values']):
                    b.emit('WHEN', n)
                    b.text(value, rx, ry, rw, ROW_H, ROW_POINT_SIZE, ROW_TEXT_FOCUS if focused else ROW_TEXT_REST,
                           HALIGN_CENTER, VALIGN_CENTER)
            b.lines.append('ENDITEM')
            continue
        if row['label'] in LEAVES:
            action = 'BACK'
        elif row['label'] in ASKS:
            action = ASKS[row['label']]
        elif row['nav'] in pages:
            action = f"GOTO:{row['nav']}"       # (an option that opens a page: OPENS)
        elif options and not row.get('rect'):
            action = 'RADIO:options'
        else:
            action = '-'
        b.emit('ITEM', row['id'], rows[i - 1]['id'], rows[(i + 1) % len(rows)]['id'], '-', '-', action, 0.0)
        typed(b, name, row['id'], row['label'])
        if action == 'RADIO:options':
            b.emit('VALUES', 2, 1 if i == current else 0)
        listed = scrolls and not row.get('rect')
        rh = ROW_H if row.get('rect') else page['row_h']
        if listed:
            b.emit('ROW', ry - 1.0, rh + 1.0)
        for focused in (False, True):
            b.lines.append('STATE focus' if focused else 'STATE normal')
            b.lines.append('LAYER list' if listed else 'LAYER fixed')
            if focused:
                b.rect(rx, ry - 1.0, rw, rh + 1.0, HIGHLIGHT_COLOR)
            else:
                b.rect(rx, ry - 1.0, rw, 1.0, RULE_COLOR)
                b.rect(rx, ry + rh - 1.0, rw, 1.0, RULE_COLOR)
            if focused and row.get('says') and page['says_at']:
                # What the panel says about this row while it has focus.
                sx, sy, sw, spt, sa = page['says_at']
                paragraph(b, row['says'], ox + sx, oy + sy, sw, spt, sa)
            colour = ROW_TEXT_FOCUS if focused else ROW_TEXT_REST
            if page['wraps'] and not row.get('rect'):
                # A two-line item (TALL): its icon, if it has one -- a
                # rating's as it is, a system one (sysIcon) tinted as a
                # row's icon is -- and its caption, wrapped and centred top
                # to bottom in its box.
                if row['icon']:
                    tint = (ROW_ICON_FOCUS if focused else ROW_ICON_REST) if row.get('sys') else None
                    ix, iy, iw, ih = page['icon_at']
                    b.image(row['icon'], *fitted(image_index, row['icon'], rx + ix, ry + iy, iw, ih), tint)
                tx, ty, tw, th = page['wraps']
                lines = b.wrap(row['label'], tw, ROW_POINT_SIZE)
                line_h = ROW_POINT_SIZE * POINT_SIZE_TO_PIXELS * 1.34
                for n, line in enumerate(lines):
                    b.text(line, rx + tx, ry + ty + (th - line_h * len(lines)) / 2.0 + n * line_h, tw, line_h,
                           ROW_POINT_SIZE, colour, HALIGN_LEFT, VALIGN_CENTER)
                continue
            if row['icon']:
                ix, iy, iw, ih = ROW_ICON
                b.image(row['icon'], rx + ix, ry + iy, iw, ih, ROW_ICON_FOCUS if focused else ROW_ICON_REST)
            b.text(row['label'], rx + (ROW_TEXT_X if row['icon'] else PLAIN_TEXT_X), ry, ROW_TEXT_W, rh,
                   ROW_POINT_SIZE, colour, HALIGN_LEFT, VALIGN_CENTER)
        b.lines.append('ENDITEM')
    return b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--search-dir', action='append', default=[], required=True)
    ap.add_argument('--out-dir', default='../assets/scenes')
    ap.add_argument('--font', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   '..', 'assets', 'fonts', 'xenonclatin_full.ttf'))
    args = ap.parse_args()

    files, tables = {}, {}
    for d in args.search_dir:
        for dirpath, _dirnames, filenames in os.walk(d):
            for fn in filenames:
                if fn.lower().endswith('.xur'):
                    files.setdefault(os.path.splitext(fn)[0].lower(), os.path.join(dirpath, fn))
                elif fn.endswith('.strings.json'):
                    tables.setdefault(fn.split('.')[0], os.path.join(dirpath, fn))
    index = build_image_index(args.search_dir)
    # A chooser's arrows: the skin's one up arrow, which its ScrollEnd
    # visuals turn a quarter turn each way and IMAGE cannot.
    turned = tempfile.mkdtemp()
    for name, angle in (('arrow_left.png', 90), ('arrow_right.png', -90)):
        index[name] = os.path.join(turned, name)
        Image.open(index['updefault.png']).rotate(angle, expand=True).save(index[name])
    pages = collect(files, filled(tables))
    # A self-check on the walk: every link cooked must land on a page cooked.
    assert all(row['nav'] in pages or row['nav'].lower() not in files or not row['nav']
               for page in pages.values() for row in page['rows']), 'a reachable page was not collected'
    cooker = Cooker(index, visual_templates=load_visual_templates(find_skin_files(args.search_dir)))
    panel = said(strings(tables, 'dashCSettingsStrings'))
    for name, page in pages.items():
        b = build(name, page, pages, index, args.font, cooker, panel)
        scene_dir = os.path.join(args.out_dir, name)
        os.makedirs(os.path.join(scene_dir, 'assets'), exist_ok=True)
        with open(os.path.join(scene_dir, 'manifest.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(b.lines) + '\n')
        for src, rel in b.used_images.items():
            shutil.copyfile(src, os.path.join(scene_dir, 'assets', rel))
        kind = 'menu' if any(r['nav'] for r in page['rows']) else ('options' if page['rows'] else 'no rows')
        print(f"{name}: {page['title']!r}, {len(page['rows'])} rows ({kind})")
        for w in b.warnings:
            print(f'   warning: {w}')
    for w in cooker.warnings:
        print(f'   warning: {w}')
    print(f'{len(pages)} pages')
    shutil.rmtree(turned)


if __name__ == '__main__':
    main()
