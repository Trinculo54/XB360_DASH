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
import json
import os
import shutil

import xur_extract
from hub_build import CANVAS, TILE_TOP, HubBuilder
from network_pages import child, override
from xur_cook import Cooker, build_image_index, find_skin_files, load_visual_templates

CHANNEL_AT = (207.0, TILE_TOP)
NOTHING = 'No {1} found. Download them from the {2}.'
# scene -> (its name, the text of its empty channel -- with what fills its blanks, if it has any)
LIBRARIES = {
    'Recent': ('recent', 'Your recently played and downloaded items appear here.', ()),
    'MyGames': ('my games', NOTHING, ('games', 'Games Store')),
    'MyApps': ('my apps', NOTHING, ('apps', 'App Store')),
}


def build(name, strings, layout, cooker, image_index, gamertag, font_path):
    title, text, blanks = LIBRARIES[name]
    # Every word is the library pack's own.
    missing = {title, text, *blanks} - set(strings)
    assert not missing, f'not in the library pack\'s strings: {missing}'
    for n, blank in enumerate(blanks, 1):
        text = text.replace('{%d}' % n, blank)

    b = HubBuilder(image_index, font_path)
    b.twist_names = [title]
    b.comment(f'{title}: a library with nothing in it -- emptychannel.xur under the hub\'s furniture.')
    b.comment('See library_build.py.')
    b.emit('CANVAS', *CANVAS)
    b.lines.append('LAYER fixed')
    b.backdrop()
    b.gamertag(gamertag)
    b.twist(title)
    b.legend([('B', 'Back')])

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
             if fn in ('emptychannel.xur', 'FilterChannelLayout.xur')}
    with open(os.path.join(os.path.dirname(found['FilterChannelLayout.xur']), 'strings.strings.json'),
              encoding='utf-8') as f:      # the library pack's table sits beside its layouts
        strings = [t if isinstance(t, str) else t['text'] for t in json.load(f)['strings']]
    index = build_image_index(args.search_dir)
    cooker = Cooker(index, visual_templates=load_visual_templates(find_skin_files(args.search_dir)))
    for name in LIBRARIES:
        b = build(name, strings, found['emptychannel.xur'], cooker, index, args.gamertag, args.font)
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
