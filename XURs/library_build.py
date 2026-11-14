"""
Cook the three libraries the hub's tiles open -- Recent, My Games, My Apps --
as they are on a console with nothing in them.

A library is a dashboard app of its own: a hub like the main one, with a name
where the hub's page names are and a channel of tiles under it, which its
code fills from what is on the console. With nothing to fill it with, the
channel is `emptychannel.xur` (the hub pack, archive_31): a pale box the size
of a channel, 865x400, with one line of text in the middle. That layout is
cooked as it stands, by xur_cook's Cooker, under the hub's own furniture
(hub_build's builder), and what is taken from the rip is:

  the layout   emptychannel.xur, and the size of a channel -- which is also
               hubchannellayout.xur's, the hub's social page, whose column of
               tiles at x = 0 is at 207 on screen. So a channel is at
               (207, hub_build.TILE_TOP).
  the names    "recent", "my games", "my apps": the library pack's strings
               (archive_29), lower case as the hub's page names are.
  the text     "Your recently played and downloaded items appear here.", and
               "No {1} found. Download them from the {2}." filled in with the
               pack's own "games" / "Games Store" and "apps" / "App Store".

What is judgment: that an empty library shows only this (a library with
things in it also has "show me" and "sort" tiles, and whether those stay
when there is nothing to show or sort isn't known); which of the pack's two
sentences each library uses; and that the name stands alone, with no other
channel beside it (the stores are Xbox Live's).

    python3 library_build.py --search-dir ../DASH/dash_bin_embedded_assets --search-dir ../XZP
"""
import argparse
import copy
import json
import os
import shutil

import xur_extract
import xur_schema
from hub_build import (CANVAS, FOCUS_SCALE, TILE_TOP, TWIST_COLOR, TWIST_FONT, TWIST_POINT_SIZE,
                       TWIST_UNSELECTED_ALPHA, HubBuilder)
from network_pages import child, override
from xur_cook import (HALIGN_RIGHT, POINT_SIZE_TO_PIXELS, VALIGN_CENTER, Cooker, build_image_index, find_skin_files,
                      load_visual_templates)

CHANNEL_AT = (207.0, TILE_TOP)
NOTHING = 'No {1} found. Download them from the {2}.'
# scene -> (its name, the text of its empty channel -- with what fills its blanks, if it has any)
LIBRARIES = {
    'Recent': ('recent', 'Your recently played and downloaded items appear here.', ()),
    'MyGames': ('my games', NOTHING, ('games', 'Games Store')),
    'MyApps': ('my apps', NOTHING, ('apps', 'App Store')),
}


# What is "installed". A game here is made up -- a test, to see a library with
# something in it: a title, and a stand-in for its box art that this script
# draws (cover). Nothing of any real game's. An empty GAMES gives the empty
# game libraries back.
GAMES = [dict(id='testgame', title='Test Game')]
BOX_ART = (219, 300)        # the size the console's box art is

# The apps a console has without downloading any, by the pack's own names and
# in the order the library keeps (by name): Windows Media Center as its app
# tile (AppTile_MCE.jpg), the three players as a plate with an icon and the
# name. Which four, and which icon is whose, is by the capture below and the
# files' names.
APPS = [dict(id='mediacenter', title='Media Center', art='AppTile_MCE.jpg'),
        dict(id='pictureviewer', title='Picture Viewer', icon='ico_96x_Camera.png'),
        dict(id='musicplayer', title='System Music Player', icon='ico_64x_music.png'),
        dict(id='videoplayer', title='System Video Player', icon='ico_64x_video.png')]

# A library with something in it, as two captures of the console show it (the
# user's): its name at the top right with "17 of 26" under it, in place of the
# gamertag, and what it holds from the screen's left edge. My Apps -- and
# Recent, which is taken to be the same -- is a grid, two rows of tiles filled
# down each column then across, with A Launch / B Back / X App Details; My
# Games is a row of cards (below). The pieces are the rip's:
#
#   SmallLibrarySlot_Hub.xur      a tile, 197 square: a plate, then either art
#                                 fitted to it whole or an icon 64 square at
#                                 (66.5, 58) (MultiImage148_148's two images),
#                                 and the name along the bottom.
#   DoubleTallBrowseTemplate.xur  a column of two, the second 201 down.
#
# Placed from the My Apps capture, which is cropped: its tiles are 197 at a
# pitch of 201 from x = 96 and y = 178 (the hub's own block), its title ends
# at the safe area's right edge, x = 1184, and the focused tile stands about
# 1.08 times larger -- the hub's own focus. Judgment: that art tiles carry no
# name (the capture's don't) and that a game in Recent is such a tile.
SLOT, SLOT_PITCH = 197.0, 201.0
GRID_AT = (96.0, TILE_TOP)
TITLE_BOX = (1184.0 - 500.0, 87.0, 500.0, 40.0)
COUNT_BOX = (TITLE_BOX[0], 127.0, 500.0, 22.0)


def parse_scene(path):
    return xur_extract.parse_xur8(open(path, 'rb').read()).root_object['children'][0]


def cover(title, font_path):
    """A stand-in for a game's box art: a dark card with a band across the top and the title in capitals."""
    from PIL import Image, ImageDraw, ImageFont
    art = Image.new('RGBA', BOX_ART, (24, 28, 34, 255))
    draw = ImageDraw.Draw(art)
    draw.rectangle((0, 0, BOX_ART[0] - 1, 39), fill=(70, 110, 160, 255))
    draw.rectangle((0, 40, BOX_ART[0] - 1, BOX_ART[1] - 1), outline=(70, 78, 90, 255))
    font = ImageFont.truetype(font_path, 44)
    for n, word in enumerate(title.upper().split()):
        draw.text(((BOX_ART[0] - draw.textlength(word, font=font)) / 2.0, 110 + n * 54), word, font=font,
                  fill=(235, 235, 235, 255))
    return art


def grow(lines, start, cx, cy, by):
    """Everything drawn from lines[start] on, `by` times its size about (cx, cy): a focused tile or card.
    (The app eases between an item's two states.)"""
    for n in range(start, len(lines)):
        f = lines[n].split(' ')
        if f[0] in ('RECT', 'IMAGE', 'TEXT'):
            px, py, pw, ph = (float(v) for v in f[1:5])
            f[1:5] = ['%.3f' % v for v in (cx + (px - cx) * by, cy + (py - cy) * by, pw * by, ph * by)]
            if f[0] == 'TEXT':
                f[7] = '%.3f' % (float(f[7]) * by)
            lines[n] = ' '.join(f)


def tile(b, cooker, slot_layout, entry, x, y, focused):
    """One thing's tile at (x, y), at rest or focused: the slot's own visual, with its picture put in."""
    button = child(parse_scene(slot_layout), 'SlotButton')
    art = entry.get('art') or (None if entry.get('icon') else f"boxart_{entry['id']}.png")
    override(button, {'Text': '' if art else entry['title']})
    cooker.lines = []
    # (EndFocus, not Focus: in this visual Focus is the frame the change starts on.)
    cooker.state_overrides['SlotButton'] = 'EndFocus' if focused else 'Normal'
    cooker.walk(button, x, y, 1.0)
    lines = cooker.lines
    b.used_images.update(cooker.used_images)
    # The picture goes where the slot's images do: over its plate, under its name.
    mark = len(b.lines)
    if entry.get('icon'):
        b.image(entry['icon'], x + 66.5, y + 58.0, 64.0, 64.0)
    elif entry.get('art'):
        b.image(art, x, y, SLOT, SLOT)
    else:
        b.baked_images.setdefault(art, cover(entry['title'], b.font_path))
        wide = SLOT * BOX_ART[0] / BOX_ART[1]
        b.emit('IMAGE', x + (SLOT - wide) / 2.0, y, wide, SLOT, art, 255, 255, 255, 255)
    picture = b.lines[mark:]
    del b.lines[mark:]
    under = next((i for i, line in enumerate(lines) if line.startswith('TEXT ')), 1)
    b.lines += lines[:under] + picture + lines[under:]
    if focused:
        grow(b.lines, mark, x + SLOT / 2.0, y + SLOT / 2.0, FOCUS_SCALE)


def heading(b, title):
    """A library's name, where the hub has the gamertag."""
    b.font(TWIST_FONT)
    b.text(title, *TITLE_BOX, TWIST_POINT_SIZE, TWIST_COLOR, HALIGN_RIGHT, VALIGN_CENTER)
    b.font()


def count(b, n, of):
    """Which one of how many has focus: "{1} of {2}", the marketplace pack's."""
    b.text(f'{n} of {of}', *COUNT_BOX, 12.0, TWIST_COLOR, HALIGN_RIGHT, VALIGN_CENTER, TWIST_UNSELECTED_ALPHA)


def grid(b, entries, layouts, cooker, legend):
    """Two to a column, down then across."""
    b.legend(legend)
    ids = [e['id'] for e in entries]
    at = lambda i: ids[i] if 0 <= i < len(ids) else '-'
    for i, entry in enumerate(entries):
        col, row = divmod(i, 2)
        b.emit('ITEM', entry['id'], at(i - 1) if row else '-', at(i + 1) if not row else '-', at(i - 2), at(i + 2),
               '-', 0.0)
        for focused in (False, True):
            b.lines.append('STATE focus' if focused else 'STATE normal')
            b.lines.append('LAYER fixed')
            tile(b, cooker, layouts['SmallLibrarySlot_Hub.xur'], entry,
                 GRID_AT[0] + col * SLOT_PITCH, GRID_AT[1] + row * SLOT_PITCH, focused)
            if focused:
                count(b, i + 1, len(entries))
        b.lines.append('ENDITEM')


# My Games with something in it is not that: it is a row of cards, as a
# capture of the console shows (the user's, of an earlier dashboard) -- "show
# me / all games" and "sort / titles" at the top left, "My Games" and "12 of
# 42" at the top right, the cards across the middle, A Launch / B Back. The
# pieces are the rip's:
#
#   MarketplaceSlot.xur   a card, 204x398: a white plate, the box art fitted
#                         to 184x280 at (10, 10), and under it the title, a
#                         details line and the kind of thing it is as a small
#                         icon at the bottom right.
#   btn_Filter            a header, 250x64 (FilterActionSlot.xur): a small
#                         down arrow, what it filters by at 14 points, and the
#                         filter it is on at 19 under that.
#   strings               "show me", "all games", "sort", "titles" (this
#                         pack); "{1} of {2}" and "Launch" (the marketplace
#                         pack's). The capture's title is "My Games"; this
#                         dashboard's own is "my games", as "my apps" is.
#
# Placed from the capture: its cards stand on a grid from x = 96, the safe
# area's left edge, the headers' text begins 28 right of that (which is the
# header's own inset) and 250 apart, and the title ends where the hub's
# gamertag does. Judgment: the cards 4 apart (the capture's are a little wider
# and shorter than this dashboard's card, so its numbers can't settle it);
# that the focused card stands a little larger, which is all the capture
# shows of focus; the count's size and grey; no details line for a game never
# played; and the box art, which is cover()'s stand-in.
# shortcut: the row doesn't scroll, so a sixth game runs off the right edge; give it a camera when GAMES needs one.
CARDS = {'MyGames'}
CARD, CARD_PITCH, CARD_FOCUS = (204.0, 398.0), 208.0, 1.03
CARDS_AT = (96.0, TILE_TOP)
HEADERS = [((96.0, 96.0), 'show me', 'all games'), ((346.0, 96.0), 'sort', 'titles')]


def header(b, cooker, layout, at, label, value):
    """One filter header. Its two lines are made literals in a copy of the visual: the cooker gives an
    instance's Text to one presenter only."""
    button = child(parse_scene(layout), 'SlotButton')
    visual = copy.deepcopy(cooker.visual_templates['btn_Filter'])
    for presenter, text in (('slotFilterText', label), ('slotDescription', value)):
        literal = child(child(visual, '_slotGroup'), presenter)
        literal['class'] = 'XuiText'
        # Each line rests on the bottom of its box: in the capture the filter's name sits 12 lower than the
        # top of its 46-high box would have it. The cooker reads the style as top-aligned, so the box is
        # cut down to one line at its bottom.
        p = xur_schema.resolve_properties(literal['class'], literal['properties'])
        line = p['PointSize'] * POINT_SIZE_TO_PIXELS * 1.3
        x, y, z = p['Position']
        override(literal, {'Text': text, 'Show': True, 'Position': (x, y + p['Height'] - line, z), 'Height': line})
    cooker.visual_templates[f'btn_Filter@{label}'] = visual
    override(button, {'Visual': f'btn_Filter@{label}', 'Text': ''})
    cooker.lines = []
    cooker.state_overrides['SlotButton'] = 'Normal'
    cooker.walk(button, *at, 1.0)
    b.lines += cooker.lines
    b.used_images.update(cooker.used_images)


def card(b, cooker, layout, game, x, y, focused):
    """One game's card with its top-left at (x, y), at rest or -- about its centre -- a little larger."""
    scene = parse_scene(layout)
    panel = child(scene, 'MetadataPanel')
    override(child(panel, 'Title'), {'Text': game['title']})
    override(child(panel, 'mediaTypeIcon'), {'ImagePath': 'ico_32x_controller.png'})
    for hidden in ('availableOnXboxOne', 'ReleaseYear', 'userRatingGrid', 'XuiHtmlControl1'):
        override(child(panel, hidden), {'Show': False})
    for hidden in ('boxartAndThumbnail', 'VuiBling', 'NoData', 'ctlWait'):   # (the art is put in below)
        override(child(scene, hidden), {'Show': False})
    cooker.lines = []
    cooker.walk(scene, x, y, 1.0)
    b.used_images.update(cooker.used_images)
    tall = 184.0 * BOX_ART[1] / BOX_ART[0]
    name = f"boxart_{game['id']}.png"
    b.baked_images.setdefault(name, cover(game['title'], b.font_path))
    mark = len(b.lines)
    b.lines += cooker.lines
    b.emit('IMAGE', x + 10.0, y + 10.0 + (280.0 - tall) / 2.0, 184.0, tall, name, 255, 255, 255, 255)
    if focused:
        grow(b.lines, mark, x + CARD[0] / 2.0, y + CARD[1] / 2.0, CARD_FOCUS)


def cards(b, games, layouts, cooker):
    b.legend([('A', 'Launch'), ('B', 'Back')])
    for at, label, value in HEADERS:
        header(b, cooker, layouts['FilterActionSlot.xur'], at, label, value)
    ids = [g['id'] for g in games]
    for i, game in enumerate(games):
        b.emit('ITEM', game['id'], '-', '-', ids[i - 1] if i else '-', ids[i + 1] if i + 1 < len(ids) else '-', '-', 0.0)
        for focused in (False, True):
            b.lines.append('STATE focus' if focused else 'STATE normal')
            b.lines.append('LAYER fixed')
            card(b, cooker, layouts['MarketplaceSlot.xur'], game, CARDS_AT[0] + i * CARD_PITCH, CARDS_AT[1], focused)
            if focused:
                count(b, i + 1, len(games))
        b.lines.append('ENDITEM')


def build(name, strings, layouts, cooker, image_index, gamertag, font_path):
    title, text, blanks = LIBRARIES[name]
    entries = APPS if name == 'MyApps' else GAMES
    # Every word is the library pack's own.
    missing = {title, text, *blanks, *(a['title'] for a in APPS)} - set(strings)
    assert not missing, f'not in the library pack\'s strings: {missing}'
    for n, blank in enumerate(blanks, 1):
        text = text.replace('{%d}' % n, blank)

    b = HubBuilder(image_index, font_path)
    b.comment(f'{title}: a library with {len(entries) or "nothing"} in it.')
    b.comment('See library_build.py.')
    b.emit('CANVAS', *CANVAS)
    if entries:
        b.emit('DEFAULTFOCUS', entries[0]['id'])
    b.lines.append('LAYER fixed')
    b.backdrop()
    heading(b, title)
    if entries and name in CARDS:
        cards(b, entries, layouts, cooker)
    elif entries:
        grid(b, entries, layouts, cooker, [('A', 'Launch'), ('B', 'Back')] + ([('X', 'App Details')] if name == 'MyApps' else []))
    if entries:
        return b

    # Nothing in it: the pack's sentence in emptychannel.xur's box, where the hub has a channel.
    b.legend([('B', 'Back')])
    layout = layouts['emptychannel.xur']
    channel = xur_extract.parse_xur8(open(layout, 'rb').read()).root_object['children'][0]
    override(child(channel, 'HelpText'), {'Text': text})
    cooker.lines = []
    cooker.walk(channel, *CHANNEL_AT, 1.0)
    b.lines += cooker.lines
    b.used_images.update(cooker.used_images)
    return b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--search-dir', action='append', default=[], required=True)
    ap.add_argument('--out-dir', default='../assets/scenes')
    ap.add_argument('--font', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   '..', 'assets', 'fonts', 'xenonclatin_full.ttf'))
    ap.add_argument('--gamertag', default='Trinculo54', help='as hub_build.py\'s')
    args = ap.parse_args()

    found = {fn: os.path.join(dp, fn) for d in args.search_dir for dp, _dn, fns in os.walk(d) for fn in fns
             if fn in ('emptychannel.xur', 'FilterChannelLayout.xur', 'SmallLibrarySlot_Hub.xur',
                       'MarketplaceSlot.xur', 'FilterActionSlot.xur')}
    with open(os.path.join(os.path.dirname(found['FilterChannelLayout.xur']), 'strings.strings.json'),
              encoding='utf-8') as f:      # the library pack's table sits beside its layouts
        strings = [t if isinstance(t, str) else t['text'] for t in json.load(f)['strings']]
    index = build_image_index(args.search_dir)
    # ...which only looks for PNGs, and Media Center's app tile is a JPEG (as hub_build.py's promo is).
    for d in args.search_dir:
        for dp, _dn, fns in os.walk(d):
            for fn in fns:
                if fn.lower().endswith('.jpg'):
                    index.setdefault(fn, os.path.join(dp, fn))
    cooker = Cooker(index, visual_templates=load_visual_templates(find_skin_files(args.search_dir)))
    for name in LIBRARIES:
        b = build(name, strings, found, cooker, index, args.gamertag, args.font)
        assets_dir = os.path.join(args.out_dir, name, 'assets')
        os.makedirs(assets_dir, exist_ok=True)
        with open(os.path.join(args.out_dir, name, 'manifest.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(b.lines) + '\n')
        for src, rel in b.used_images.items():
            shutil.copyfile(src, os.path.join(assets_dir, rel))
        for baked, image in b.baked_images.items():
            image.save(os.path.join(assets_dir, baked))
        print(f'{name}: {len(b.lines)} lines')
    for w in cooker.warnings:
        print(f'   warning: {w}')


if __name__ == '__main__':
    main()
