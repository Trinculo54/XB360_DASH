"""
Cook Configure Network -- 2004_NetworkDetails.xur, the page Network Settings'
first row opens -- as two scenes, one per tab.

It is not one of settings_pages.py's forms. It is a tab scene ("Basic
Settings" | "Additional Settings") of tall buttons, each several lines of
text: a caption and its value on the first line, then the fields under it.

  Basic Settings        IP Settings      (btn_4Line)  -> 2016_EditIPSettings
                        DNS Settings     (btn_3Line)  -> 2033_DNSConfig
  Additional Settings   PPPoE Settings   (btn_4Line)  -> 2036_PPoESettings
                        Advanced Settings (btn_3Line) -> 2038_ConsoleInformation
                        Restore to Factory Defaults

The layout, the tab header and its two states, the buttons' visuals and where
each button goes (its PressPath) are the file's, cooked by xur_cook.Cooker in
the same shell cook_networkmain.sh puts Network Settings in. What the file
does NOT have is the buttons' text: the class behind them (C4LineBtn) sets
every line from code. So it is filled in here (LINES), and two things about
that are judgment:

  - WHICH strings. They are all the network pack's own (Strings.xus, checked
    below), or the captions of the page the button opens. That "IP Address,
    Subnet Mask, Gateway" are IP Settings' three fields is from how they sit
    together in the string table and from 2018_IPSettings' own list.
  - The VALUES. Nothing here knows the console's network settings. IP and
    DNS say "Automatic", which is how a console comes; the addresses under
    them, which the console would show, are left blank.

Each tab is its own scene, and left / right step between them as a nav link
to the other scene: the dashboard's navigator has no tab scenes, so the
switch is its ordinary scene change rather than the 12-frame slide the tab
scene's timeline has.

It also cooks what Network Settings' other four rows open -- the three
connection tests, which play, and Network Statistics. See "The connection
tests" below.

    python3 network_pages.py --search-dir ../DASH/dash_bin_embedded_assets \\
                             --search-dir ../XZP --out-dir ../assets/scenes
"""
import argparse
import copy
import json
import os
import shutil

import xur_extract
import xur_schema
from settings_build import ROW_TEXT_REST as INK, Builder
from xur_cook import (HALIGN_LEFT, HALIGN_RIGHT, POINT_SIZE_TO_PIXELS, VALIGN_TOP, Cooker, build_image_index,
                      evaluate_state, find_skin_files, load_visual_templates, resolve_image_path, text_alignment)

LAYOUT = '2004_NetworkDetails.xur'
BASIC, ADDITIONAL = '2004_NetworkDetails', '2004_NetworkDetails_Additional'

# Per button: its lines, by the template's DataAssociation -- None is the
# caption (left, line 1), 1..3 the fields under it (left, lines 2..4), and 4
# the value beside the caption (right, line 1). 5..7, the fields' values, are
# the console's live settings and stay empty.
LINES = {
    'btn_IP': {None: 'IP Settings', 4: 'Automatic', 1: 'IP Address', 2: 'Subnet Mask', 3: 'Gateway'},
    'btn_DNS': {None: 'DNS Settings', 4: 'Automatic', 1: 'Primary DNS Server', 2: 'Secondary DNS Server'},
    'btn_PPPoE': {None: 'PPPoE Settings', 1: 'User Name', 2: 'Password', 3: 'Service Name'},
    'btn_Console': {None: 'Advanced Settings', 1: 'Host Name', 2: 'Alternate MAC Address'},
}

# A button that asks first. The question is a message box, which is xam's,
# so it is cooked with the guide (guide_build.py: DIALOGS) and opened there.
ASKS = {'btn_RestoreDefaults': 'GUIDE:GuideRestoreNetwork'}

# tab scene -> (the named frame it rests on, its buttons, the other tab's first button)
TABS = {
    BASIC: ('2To1End', 'Tab1', ['btn_IP', 'btn_DNS'], ('-', f'{ADDITIONAL}:btn_PPPoE')),
    ADDITIONAL: ('1To2End', 'Tab2', ['btn_PPPoE', 'btn_Console', 'btn_RestoreDefaults'], (f'{BASIC}:btn_IP', '-')),
}


def props(node):
    return xur_schema.resolve_properties(node['class'], node['properties']) or {}


def child(node, ident):
    return next(c for c in node['children'] if props(c).get('Id') == ident)


def override(node, values):
    node['properties'] = ([p for p in node['properties'] if p['property'] not in values]
                          + [{'property': k, 'value': v} for k, v in values.items()])


def cook(scene_name, path, templates, index, out_dir):
    state, tab_id, buttons, sides = TABS[scene_name]

    def prepare(root):
        tabs = child(root['children'][0], 'Scene_Tabs')
        # The tab scene at rest on this tab: its own timeline's values.
        for target, values in evaluate_state(tabs, state).items():
            override(child(tabs, target), values)
        for tab in ('Tab1', 'Tab2'):
            override(child(tabs, tab), {'Show': tab == tab_id, 'Opacity': 1.0})
        for button in child(tabs, tab_id)['children']:
            lines = LINES.get(props(button).get('Id'))
            if not lines:
                if button['class'] == 'XuiButton' and not props(button).get('Visual'):
                    override(button, {'Visual': 'XuiButton'})   # its class's own visual, which XUI falls back to
                continue
            # A copy of the button's visual with each line that has text
            # made a literal: the Cooker binds an instance's Text to the one
            # presenter that has no DataAssociation, and nothing to the rest.
            p = props(button)
            visual = copy.deepcopy(templates[p['Visual']])
            for presenter in visual['children']:
                which = props(presenter).get('DataAssociation') or None
                if presenter['class'] == 'XuiTextPresenter' and which in lines and which is not None:
                    presenter['class'] = 'XuiText'
                    override(presenter, {'Text': lines[which], 'Show': True})
            templates[f"{p['Visual']}@{p['Id']}"] = visual
            override(button, {'Visual': f"{p['Visual']}@{p['Id']}", 'Text': lines[None]})

    scene_dir = os.path.join(out_dir, scene_name)
    cooker = shell(index, templates)
    cooker.prepare = prepare
    for button in buttons:
        opens = props(next(b for b in walk(path) if props(b).get('Id') == button)).get('PressPath') or ''
        cooker.items[button] = f'GOTO:{os.path.splitext(opens)[0]}' if opens else ASKS.get(button, '-')
        cooker.item_sides[button] = sides
    result = cooker.cook(path, os.path.join(scene_dir, 'manifest.txt'), os.path.join(scene_dir, 'assets'),
                         (1280.0, 720.0))
    print(f"{scene_name}: {result['lines']} lines, items {', '.join(f'{b} {cooker.items[b]}' for b in buttons)}")
    for w in result['warnings']:
        print(f'   warning: {w}')


# ---------------------------------------------------------------------------
# The connection tests, and Network Statistics
# ---------------------------------------------------------------------------
# Network Settings' other four rows name no page: its code runs them. What a
# test looks like is all in NetworkMain.xur -- the Xbox -> Network -> Internet
# -> Xbox Live bar along the bottom with a spinner and a row of lit dots for
# each hop, the status lines, and beside "Internet" the "PC" a media test goes
# to instead -- and in TestingScene.xur, which is the menu a test puts in
# place of Network Settings' own: "Stop Test" while it runs. So a test is
# cooked from those two as a scene that PLAYS: each hop's spinner runs once
# through its own timeline (Network_Progress, 180 frames) with "Testing..."
# beside its status line, then its dots stay lit and the line reads
# "Connected" with the check icon; when the last hop is done the scene ends
# (MARK Scene.End) and the app goes back to Network Settings.
#
# NOTHING IS TESTED. The Xbox Live test passes, and the two media tests fail
# at the PC (FAILS): a test that fails gives way to the page it is left as
# (AFTER; cook_failed), with TestingScene's other four buttons, and from
# there to its suggestions (cook_suggestions). Which test does which, how
# long a hop takes, how long a passed test stays up, that "Test Status" is
# the title meanwhile and that a passed test returns to Network Settings are
# all JUDGMENT. The words are the network pack's own.
MAIN, TESTING, SUGGESTION = 'NetworkMain.xur', 'TestingScene.xur', 'SuggestionScene.xur'
STAGE = 180     # frames a hop is tested for: one pass of its spinner
HOLD = 120      # ...and the finished test stays up this long
WORDS = {'title': 'Test Status', 'testing': 'Testing...', 'done': 'Connected', 'unknown': '---',
         'failed': 'Failed', 'up': 'Up', 'retry': 'Restart Test', 'next': 'Next Suggestion', 'previous': 'Previous Suggestion',
         'count': 'Suggestion %1!d! of %2!d!'}
CHECK, CROSS = 'ico_32x_check.png', 'ico_32x_error.png'

# The two media tests FAIL at their second hop: there is no PC to find. (The
# Xbox Live test passes; Network Settings is cooked connected.) What the pack
# says then, each by how its string starts: the problem, its title, and the
# suggestions for it that say everything themselves -- its others point at
# "the following website" without the pack saying which. Which strings go
# with which test is plain from what they say. Their ORDER as suggestions is
# a guess, and for Windows Media Center so is the choice of "can't connect
# to" over "is not connected to", whose one suggestion is a button this
# doesn't have.
FAILS = {
    'TestingScene_PC': ("Can't connect to your PC.", 'Media Sharing Problem',
                        ['Make sure your PC is turned on and connected', 'Make sure that media sharing software',
                         'Make sure the required firewall ports']),
    'TestingScene_MCX': ("Can't connect to your Windows Media Center PC.", 'Windows Media Center Error',
                         ['Make sure your PC is turned on.', 'Make sure the console and PC are on the same network.']),
}


def pack(strings, start):
    """The one string of the pack that starts so."""
    found = [s for s in strings if s.startswith(start)]
    assert len(found) == 1, (start, found)
    return found[0].strip()

# Per hop: its spinner, the row of rings it shows until it is tested, the lit
# dots it shows once it is, and the status line it settles. (The rings and
# the dots are alternates: Network Settings, which is connected, shows dots
# and no rings -- cook_networkmain.sh hides them, against a console capture.)
LIVE = [('Spinner', 'ProgressBar_Off', 'ProgressBar_On', 'txtNetworkStatus'),
        ('Spinner1', 'ProgressBar1_Off', 'ProgressBar1_On', 'txtInternetStatus'),
        ('Spinner2', 'ProgressBar2_Off', 'ProgressBar3_On', 'txtLiveStatus')]
# What only the Xbox Live test shows, which a media test puts its own in place of.
INTERNET = ['InternetImage', 'InternetText', 'txtInternetStatus', 'txtLiveStatus', 'LiveImage', 'ProgressBar2_Off']
# scene -> (its hops, what it shows that Network Settings doesn't, what it hides)
TESTS = {
    'TestingScene_Live': (LIVE, [], []),
    'TestingScene_PC': (LIVE[:1] + [LIVE[1][:3] + ('txtPCStatus',)], ['PCImage', 'txtPCStatus'], INTERNET),
    'TestingScene_MCX': (LIVE[:1] + [LIVE[1][:3] + ('txtMCXStatus',)],
                         ['MCXImage', 'MCXText', 'txtMCXStatus'], INTERNET + ['PCText']),
}
# The lines Network Statistics shows where the Live ones are. Network Settings hides them.
STATS = ['txtLatencyStatus', 'txtPacketLossStatus', 'txtUploadSpeedStatus', 'txtDownloadSpeedStatus',
         'txtSignalStrengthStatus', 'txtLinkSpeedStatus']
STATISTICS = 'NetworkStatistics'
# The test whose result Network Settings shows afterwards, and the lines it settles.
RESULTS = ('TestingScene_Live', [hop[3] for hop in LIVE])
# The rings of a hop's row, which its lit dots sit among in the layout and show without.
RINGS = [f'HalfRing{n}' for n in range(1, 11)]


def shell(index, templates):
    """A Cooker set up as cook_networkmain.sh sets Network Settings up."""
    return Cooker(index, hide_ids=['legend_a', 'legend_b'] + RINGS, visual_templates=templates,
                  place={'txt_Header': (96.0, 56.0)}, scene_pos=(200.0, 114.0), backdrop=True,
                  legend=[('A', 'Select', 96.0), ('B', 'Back', 196.4)])


def keys_of(owner, target):
    """One target's keys in `owner`'s timeline: [(frame, interpolation, {property: value})]."""
    return next(([(kf['frame'], kf['interpolation'], {p['property']: p.get('value') for p in kf['properties']})
                  for kf in tl['keyframes']] for tl in owner.get('timelines') or [] if tl['target'] == target), [])


def by_id(path, ident):
    return next(n for n in walk(path) if props(n).get('Id') == ident)


def swap_menu(root, header, buttons):
    """Network Settings' menu and header give way to a page's own header and buttons."""
    menu = child(root['children'][0], 'Menu')
    for node in menu['children']:
        override(node, {'Show': props(node).get('Id') == 'txt_Header'})
    override(child(menu, 'txt_Header'), {'Text': header})
    for button in buttons:
        override(button, {'Show': True, 'Visual': 'XuiButton'})     # its class's own visual, which XUI falls back to
        if button not in menu['children']:
            menu['children'].append(button)


def dress_bar(root, shown, hidden):
    """NetworkMain's bar and status lines as a test has them: titled "Test Status", with what
    another test shows hidden, and the statistics, and every spinner and row of lit dots --
    those are added afterwards (Extras), as tracks or as they ended up."""
    bar = child(root['children'][0], 'ConnectBar')
    override(child(bar, 'txtStatusHeader'), {'Text': WORDS['title']})
    for ident in hidden + STATS + ['LiveStatusScene'] + [i for hop in LIVE for i in (hop[0], hop[2])]:
        override(child(bar, ident), {'Show': False})
    for ident in shown:
        override(child(bar, ident), {'Show': True})
    return bar


class Extras:
    """Lines added to a scene after its layout is cooked: values and icons the code sets, and
    the parts that move, each in a TRACK."""
    def __init__(self, index, templates, bar):
        self.piece, self.index, self.lines = Cooker(index, visual_templates=templates), index, []
        self.bar = bar
        self.at = (200.0 + props(bar)['Position'][0], 114.0 + props(bar)['Position'][1])   # scene_pos + ConnectBar
        status = templates['Label_TestStatus']
        self.value = props(child(status, 'Text_1'))
        self.icon = props(next(c for c in status['children'] if c['class'] == 'XuiImagePresenter'))

    def place(self, ident):
        """Where one of ConnectBar's own children is."""
        p = props(child(self.bar, ident))['Position']
        return self.at[0] + p[0], self.at[1] + p[1]

    def cooked(self, node, x, y, hide=()):
        override(node, {'Show': True})
        self.piece.hide_ids = set(hide)
        self.piece.lines = []
        self.piece.walk(node, x, y, 1.0)
        return list(self.piece.lines)

    def said(self, line, text, check=False):
        """What one of ConnectBar's own status lines says."""
        return self.says(*self.place(line), text, check)

    def live_status(self):
        """NetworkMain's "Live Status" block -- Service, Xbox stores, Accounts, Matchmaking --
        as a console that has reached Xbox Live has it: each "Up", with the check icon. It
        sits under the three status lines; Network Settings and the tests keep it hidden."""
        block = copy.deepcopy(child(self.bar, 'LiveStatusScene'))
        block['children'] = [c for c in block['children'] if props(c).get('Id') != 'txt_Header']  # a stray header
        lines = self.cooked(block, *self.at)
        x, y = self.place('LiveStatusScene')
        for node in block['children']:
            p = props(node)
            if p.get('Visual') == 'Label_TestStatus':
                lines += self.says(x + p['Position'][0], y + p['Position'][1], WORDS['up'], True)
        return lines

    def says(self, x, y, text, check=False):
        """A status line's value (Label_TestStatus's Text_1), and its icon: CHECK, or one named."""
        v, c = self.value, self.value['TextColor']
        self.piece.lines = []
        self.piece.emit('TEXT', x + v['Position'][0], y + v['Position'][1], v['Width'], v['Height'],
                        *text_alignment(v['TextStyle']), v['PointSize'] * POINT_SIZE_TO_PIXELS,
                        int(round(c['a'] * v.get('Opacity', 1.0))), c['r'], c['g'], c['b'], text)
        if check:
            resolved = resolve_image_path(CHECK if check is True else check, self.index)
            self.piece.used_images[resolved] = os.path.basename(resolved)
            i = self.icon
            self.piece.emit('IMAGE', x + i['Position'][0], y + i['Position'][1], i['Width'], i['Height'],
                            os.path.basename(resolved), 255, 255, 255, 255)
        return list(self.piece.lines)

    def track(self, ident, lines, keys):
        """`lines` in a TRACK: (frame, interpolation, shown, alpha) keys, hidden before the first."""
        if keys[0][0] > 0:
            keys = [(0, 'none', False, 0.0)] + keys
        self.lines.append(f'TRACK {ident} 0 0 0 0')
        self.lines += [f'KEY {frame} {interp} 0 0 0 {int(bool(shown))} 0 0 1 1 {alpha:.3f}'
                       for frame, interp, shown, alpha in keys]
        self.lines += lines + ['ENDTRACK']

    def after(self, test, lines, also=()):
        """Once `test` has run to its end, these status lines read as it left them, and
        `also` is drawn. (The app gives every scene a variable named for each scene that has
        played out.)"""
        self.lines.append(f'IF {test} 1')
        for line in lines:
            self.lines += self.said(line, WORDS['done'], check=True)
        self.lines += list(also) + ['ENDIF']

    def write(self, scene_dir, marker=None):
        add_lines(scene_dir, self.lines, self.piece.used_images, marker)


def add_lines(scene_dir, lines, images, marker=None):
    """Onto the end of a scene's manifest. With a `marker`, onto a manifest something else
    cooked: whatever follows the marker from an earlier run is replaced."""
    path = os.path.join(scene_dir, 'manifest.txt')
    with open(path, encoding='utf-8') as f:
        kept = f.read().split(f'# {marker}\n')[0] if marker else f.read()
    with open(path, 'w', encoding='utf-8') as f:
        f.write(kept + (f'# {marker}\n' if marker else '') + '\n'.join(lines) + '\n')
    for src, rel in images.items():
        shutil.copyfile(src, os.path.join(scene_dir, 'assets', rel))


def cook_test(scene_name, main_path, testing_path, templates, index, out_dir):
    hops, shown, hidden = TESTS[scene_name]

    def prepare(root):
        swap_menu(root, props(by_id(testing_path, 'txt_Header'))['Text'], [by_id(testing_path, 'btnStopTest')])
        dress_bar(root, shown, hidden)

    scene_dir = os.path.join(out_dir, scene_name)
    cooker = shell(index, templates)
    cooker.prepare = prepare
    cooker.items['btnStopTest'] = 'BACK'
    result = cooker.cook(main_path, os.path.join(scene_dir, 'manifest.txt'), os.path.join(scene_dir, 'assets'),
                         (1280.0, 720.0))

    extras = Extras(index, templates, child(by_id(main_path, 'Main'), 'ConnectBar'))
    progress = templates['Network_Progress']
    fails = scene_name in FAILS
    for n, (spinner, rings, lit, line) in enumerate(hops):
        start, end = n * STAGE, (n + 1) * STAGE
        during = [(start, 'none', True, 1.0), (end, 'none', False, 1.0)]
        after = [(end, 'none', True, 1.0)]
        passes = not (fails and n == len(hops) - 1)     # a test that fails, fails at its last hop
        if start:
            # A hop not yet reached: its row of rings.
            extras.track(rings, extras.cooked(copy.deepcopy(child(extras.bar, rings)), *extras.at),
                         [(0, 'none', True, 1.0), (start, 'none', False, 1.0)])
        x, y = extras.place(spinner)
        # The spinner: its rings for as long as the hop is tested, and its
        # five dots lit in turn by the visual's own timeline.
        extras.track(f'{spinner}.rings', [l for part in progress['children']
                                          if (props(part).get('Id') or '').startswith('HalfRing')
                                          for l in extras.cooked(copy.deepcopy(part), x, y)], during)
        for part in progress['children']:
            ident = props(part).get('Id') or ''
            if ident.startswith('Full'):
                dot = copy.deepcopy(part)
                override(dot, {'Opacity': 1.0})
                extras.track(f'{spinner}.{ident}', extras.cooked(dot, x, y),
                             [(start + frame, interp, values.get('Show', True), values.get('Opacity', 1.0))
                              for frame, interp, values in keys_of(progress, ident)])
        extras.track(f'{line}.testing', extras.said(line, WORDS['testing']), during)
        if passes:
            extras.track(lit, extras.cooked(copy.deepcopy(child(extras.bar, lit)), *extras.at, hide=RINGS), after)
            extras.track(f'{line}.done', extras.said(line, WORDS['done'], check=True), after)
    if scene_name == RESULTS[0]:
        # Xbox Live reached: its services, under the three lines.
        extras.track('LiveStatusScene', extras.live_status(), [(len(hops) * STAGE, 'none', True, 1.0)])
    # A test that passes stays up a moment and is left; one that fails gives
    # way at once to the page it is left as (cook_failed).
    ends = len(hops) * STAGE + (0 if fails else HOLD)
    extras.lines.append(f'MARK Scene.End {ends}')
    if fails:
        extras.lines.append(f'AFTER {scene_name}_Failed')
    extras.write(scene_dir)
    print(f"{scene_name}: {result['lines']} lines + {len(extras.lines)}, {len(hops)} hops, "
          f"{'fails' if fails else 'passes'}, ends at frame {ends}")
    for w in result['warnings']:
        print(f'   warning: {w}')


def cook_failed(scene_name, main_path, testing_path, templates, index, out_dir, strings, font):
    """A test that failed, as it is left. TestingScene's other four buttons are for this:
    Configure Network, Restart Test, See Suggestions, Continue. The hops that passed keep
    their lit dots and "Connected"; the one that didn't keeps its rings, with the layout's
    own cross over it (Fail / Fail1 / Fail2) and "Failed" beside its line; and where "Visit
    Xbox.com/networkhelp for more help." was, what went wrong. That the cross on the status
    line is ico_32x_error, and that the problem goes in that box, are JUDGMENT."""
    hops, shown, hidden = TESTS[scene_name]
    actions = {'btnConfigureSettings': f'GOTO:{BASIC}', 'btnRestartTest': f'GOTO:{scene_name}',
               'btnMoreInfo': f'GOTO:{scene_name}_Suggestion1', 'btnDone': 'BACK'}

    def prepare(root):
        swap_menu(root, props(by_id(testing_path, 'txt_Header'))['Text'],
                  [by_id(testing_path, ident) for ident in actions])
        bar = dress_bar(root, shown, hidden + ['txtNetworkHelp'])
        override(child(bar, ['Fail', 'Fail1', 'Fail2'][len(hops) - 1]), {'Show': True})

    scene_dir = os.path.join(out_dir, f'{scene_name}_Failed')
    cooker = shell(index, templates)
    cooker.prepare = prepare
    cooker.items.update(actions)
    result = cooker.cook(main_path, os.path.join(scene_dir, 'manifest.txt'), os.path.join(scene_dir, 'assets'),
                         (1280.0, 720.0))

    extras = Extras(index, templates, child(by_id(main_path, 'Main'), 'ConnectBar'))
    for n, (_spinner, rings, lit, line) in enumerate(hops):
        if n < len(hops) - 1:
            extras.lines += extras.cooked(copy.deepcopy(child(extras.bar, lit)), *extras.at, hide=RINGS)
            extras.lines += extras.said(line, WORDS['done'], check=True)
        else:
            extras.lines += extras.cooked(copy.deepcopy(child(extras.bar, rings)), *extras.at)
            extras.lines += extras.said(line, WORDS['failed'], check=CROSS)
    # The problem, wrapped in the help text's box and centred in it as that text is.
    box = copy.deepcopy(child(extras.bar, 'txtNetworkHelp'))
    p = props(box)
    wrapped = Builder(index, font).wrap(pack(strings, FAILS[scene_name][0]), p['Width'] - 20.0, p['PointSize'])
    line_h = p['PointSize'] * POINT_SIZE_TO_PIXELS * 1.34
    top = p['Position'][1] + (p['Height'] - line_h * len(wrapped)) / 2.0
    for n, text in enumerate(wrapped):
        override(box, {'Text': text, 'Position': (p['Position'][0], top + n * line_h, 0.0), 'Height': line_h})
        extras.lines += extras.cooked(box, *extras.at)
    extras.write(scene_dir)
    print(f"{scene_name}_Failed: {result['lines']} lines + {len(extras.lines)}")
    for w in result['warnings']:
        print(f'   warning: {w}')


def cook_suggestions(scene_name, path, templates, index, out_dir, strings, font):
    """What See Suggestions opens: SuggestionScene.xur, once per suggestion. Its page is an
    HTML control the code fills from the pack's own template -- a heading row of two cells, a
    bold line, a paragraph -- and three buttons it captions. Here the cells are the problem's
    title and "Suggestion 1 of 3", the bold line is the problem and the paragraph the
    suggestion; the buttons are Restart Test, Next Suggestion (where there is one) and
    Previous Suggestion (likewise). WHICH strings fill the template, the buttons' captions,
    and how the HTML is set (dark, 18 points for the heading, 16 for the rest, no bold face)
    are all JUDGMENT: the layout has none of it."""
    problem, title, suggestions = FAILS[scene_name]
    for n, suggestion in enumerate(suggestions, 1):
        name = f'{scene_name}_Suggestion{n}'
        captions = {'btnRetry': (WORDS['retry'], f'GOTO:{scene_name}'),
                    'btnNext': (WORDS['next'], f'GOTO:{scene_name}_Suggestion{n + 1}' if n < len(suggestions) else None),
                    'btnBack': (WORDS['previous'], 'BACK' if n > 1 else None)}

        def prepare(root):
            scene = root['children'][0]
            # The buttons that apply, each linked to the next that does.
            there = [ident for ident, (_caption, action) in captions.items() if action]
            for ident, (caption, action) in captions.items():
                at = there.index(ident) if action else -1
                override(child(scene, ident), {
                    'Text': caption, 'Visual': 'XuiButton', 'Show': action is not None,
                    'NavUp': there[at - 1] if at > 0 else '',
                    'NavDown': there[at + 1] if 0 <= at < len(there) - 1 else ''})
            for ident in ('legend_x', 'legend_y'):
                override(child(scene, ident), {'Show': False})

        scene_dir = os.path.join(out_dir, name)
        cooker = shell(index, templates)
        cooker.prepare = prepare
        cooker.items.update({ident: action for ident, (_caption, action) in captions.items() if action})
        result = cooker.cook(path, os.path.join(scene_dir, 'manifest.txt'), os.path.join(scene_dir, 'assets'),
                             (1280.0, 720.0))

        html = props(by_id(path, 'html_TF'))
        x, y, w = 200.0 + html['Position'][0], 114.0 + html['Position'][1], 780.0     # the template's table is 780 wide
        b = Builder(index, font)
        line = lambda points: points * POINT_SIZE_TO_PIXELS * 1.34
        b.text(pack(strings, title), x, y, w, line(18.0), 18.0, INK, HALIGN_LEFT, VALIGN_TOP)
        b.text(WORDS['count'].replace('%1!d!', str(n)).replace('%2!d!', str(len(suggestions))), x, y, w, line(18.0),
               18.0, INK, HALIGN_RIGHT, VALIGN_TOP)
        y += line(18.0) * 1.5
        for paragraph in (pack(strings, problem), pack(strings, suggestion).replace('\r\n', '').replace('<br/>', '\n')):
            for text in b.wrap(paragraph, html['Width'], 16.0):
                if text:
                    b.text(text, x, y, html['Width'], line(16.0), 16.0, INK, HALIGN_LEFT, VALIGN_TOP)
                y += line(16.0)
            y += line(16.0) * 0.5
        add_lines(scene_dir, b.lines, {})
        print(f"{name}: {result['lines']} lines + {len(b.lines)}")
        for warning in result['warnings']:
            print(f'   warning: {warning}')


def cook_statistics(main_path, templates, index, out_dir):
    """Network Statistics: Network Settings with its statistics lines where the Xbox Live one
    is, each reading "---" (the pack's word for a figure it hasn't got), and one button,
    "Continue" -- NetworkMain's own btnDoneExclusive, the one it has for standing alone. That
    this is how the page is made up is A JUDGMENT; nothing is measured."""
    def prepare(root):
        main = root['children'][0]
        swap_menu(root, props(child(child(main, 'Menu'), 'navTestStatistics'))['Text'],
                  [child(child(main, 'Menu'), 'btnDoneExclusive')])
        bar = child(main, 'ConnectBar')
        for ident in ['txtLiveStatus', 'LiveStatusScene', 'Spinner', 'Spinner1', 'Spinner2']:
            override(child(bar, ident), {'Show': False})
        override(child(bar, 'ProgressBar3_On'), {'Show': True})     # as cook_networkmain.sh shows it

    scene_dir = os.path.join(out_dir, STATISTICS)
    cooker = shell(index, templates)
    cooker.prepare = prepare
    cooker.items['btnDoneExclusive'] = 'BACK'
    result = cooker.cook(main_path, os.path.join(scene_dir, 'manifest.txt'), os.path.join(scene_dir, 'assets'),
                         (1280.0, 720.0))
    extras = Extras(index, templates, child(by_id(main_path, 'Main'), 'ConnectBar'))
    for line in STATS:
        extras.lines += extras.said(line, WORDS['unknown'])
    extras.after(RESULTS[0], RESULTS[1][:2])    # its Network and Internet lines, as Network Settings'
    extras.write(scene_dir)
    print(f"{STATISTICS}: {result['lines']} lines + {len(extras.lines)}")
    for w in result['warnings']:
        print(f'   warning: {w}')


def add_results(main_path, templates, index, out_dir):
    """Network Settings after a test. Its page is cook_networkmain.sh's, whose status lines
    have no values: the console fills those in from its last test. So once the Xbox Live
    test here has run to its end, its three lines -- which are Network Settings' three --
    read "Connected" with the check icon, added to that manifest after a marker. (The two
    media tests leave nothing: their own line, "PC:", isn't on this page.) Run after
    cook_networkmain.sh, which rewrites the manifest without them."""
    extras = Extras(index, templates, child(by_id(main_path, 'Main'), 'ConnectBar'))
    extras.after(*RESULTS, also=extras.live_status())
    extras.write(os.path.join(out_dir, 'NetworkMain'), marker='after a test (network_pages.py)')
    print(f"NetworkMain: + {len(extras.lines)} lines, shown once {RESULTS[0]} has finished")


def walk(path):
    """Every object in a layout, for looking one up by Id."""
    stack = [xur_extract.parse_xur8(open(path, 'rb').read()).root_object]
    while stack:
        node = stack.pop()
        yield node
        stack += node['children']


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--search-dir', action='append', default=[], required=True)
    ap.add_argument('--out-dir', default='../assets/scenes')
    ap.add_argument('--font', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   '..', 'assets', 'fonts', 'xenonclatin_full.ttf'))
    args = ap.parse_args()

    path = next(os.path.join(dp, fn) for d in args.search_dir for dp, _dn, fns in os.walk(d)
                for fn in fns if fn == LAYOUT)
    # Every word put on a button is the network pack's own, or a row of the
    # page that button opens ("User Name", "Password": 2036_PPoESettings).
    with open(os.path.join(os.path.dirname(path), 'Strings.strings.json'), encoding='utf-8') as f:
        strings = [t if isinstance(t, str) else t['text'] for t in json.load(f)['strings']]
    missing = ({text for lines in LINES.values() for text in lines.values()} | set(WORDS.values())) \
        - {s.strip() for s in strings} - {'User Name', 'Password'}
    assert not missing, f'not in the network pack\'s strings: {missing}'

    index = build_image_index(args.search_dir)
    for scene_name in TABS:
        cook(scene_name, path, load_visual_templates(find_skin_files(args.search_dir)), index, args.out_dir)
    main_path, testing_path, suggestion_path = (os.path.join(os.path.dirname(path), name)
                                                for name in (MAIN, TESTING, SUGGESTION))
    for scene_name in TESTS:
        cook_test(scene_name, main_path, testing_path, load_visual_templates(find_skin_files(args.search_dir)),
                  index, args.out_dir)
        if scene_name in FAILS:
            cook_failed(scene_name, main_path, testing_path, load_visual_templates(find_skin_files(args.search_dir)),
                        index, args.out_dir, strings, args.font)
            cook_suggestions(scene_name, suggestion_path, load_visual_templates(find_skin_files(args.search_dir)),
                             index, args.out_dir, strings, args.font)
    cook_statistics(main_path, load_visual_templates(find_skin_files(args.search_dir)), index, args.out_dir)
    add_results(main_path, load_visual_templates(find_skin_files(args.search_dir)), index, args.out_dir)


if __name__ == '__main__':
    main()
