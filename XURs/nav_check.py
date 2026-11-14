"""
Walk every scene's navigation graph and report anything that would feel broken
with a controller in hand.

The engine is deliberately forgiving at runtime -- a dangling link just leaves
focus where it is rather than blanking the screen -- which is the right
behaviour for a shipped app and exactly the wrong behaviour for catching a
typo in a generated manifest. So this checks statically:

  - every nav link and GOTO names an item/scene that exists
  - every item is reachable from the start scene's default focus
  - no item is a dead end (nothing to press, nowhere to go)
  - left/right/up/down are reciprocal where both sides define them, since a
    non-reciprocal pair is how a menu ends up with a tile you can enter but
    not leave

    python3 nav_check.py ../assets/scenes [--start HubHome]
"""
import argparse
import os
import sys

from manifest_preview import parse_manifest

DIRECTIONS = ('up', 'down', 'left', 'right')
OPPOSITE = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}


def load_scenes(root):
    scenes = {}
    thens = []
    for name in sorted(os.listdir(root)):
        manifest = os.path.join(root, name, 'manifest.txt')
        if not os.path.isfile(manifest):
            continue
        _canvas, _cmds, items, _fade = parse_manifest(manifest)
        scenes[name] = items
        # "AFTER <scene>": where this one goes by itself when it has played
        # out. "ACTIONIF var value GOTO:<scene>": where one of its items goes
        # instead, in some state of the app.
        with open(manifest, encoding='utf-8') as f:
            lines = [line.split() for line in f]
        HANDS_OVER[name] = ([f[1] for f in lines if f[:1] == ['AFTER']]
                            + [f[3][5:] for f in lines if f[:1] == ['ACTIONIF'] and f[3].startswith('GOTO:')]
                            # "TYPE:<field>": on to the guide's keyboard (Guide.cpp)
                            + ['GuideKeyboard' for item in items if item['action'].startswith('TYPE:')])
        # "SAY <field>.then <scene>": where the keyboard's Done goes on to, for that field.
        thens += [f[2] for f in lines if f[:1] == ['SAY'] and len(f) > 2 and f[1].endswith('.then')]
        # "SAY <scene>/<item>.type <field>": a row that is typed, which opens the guide's keyboard (main.cpp).
        if any(f[:1] == ['SAY'] and len(f) > 2 and f[1].endswith('.type') for f in lines):
            TYPED_IN.add(root)
    if 'GuideKeyboard' in scenes:
        HANDS_OVER['GuideKeyboard'] = HANDS_OVER['GuideKeyboard'] + thens
    return scenes


HANDS_OVER = {}
TYPED_IN = set()    # the roots that have a typed row


def resolve(scene, target, scenes):
    """A nav target is either 'item' (same scene) or 'scene:item'. 'scene:'
    with no item means "that scene, on whichever item it last had focus"
    (Navigator::FollowLink) -- the guide's tabs -- which for a static walk is
    any of its items, so its first stands in."""
    if ':' in target:
        s, _, i = target.partition(':')
        if not i and scenes.get(s):
            i = scenes[s][0]['id']
        return s, i
    return scene, target


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scenes_root')
    ap.add_argument('--start', action='append',
                    help='a scene the app itself can open (repeatable: the guide opens on GuideHome, or on '
                         'GuideHomeOut with nobody signed in). Default HubHome')
    ap.add_argument('--opened-from', metavar='DIR',
                    help="another set of scenes whose rows open these: the dashboard's \"GUIDE:<scene>\" rows "
                         "when checking the guide, the guide's \"DASH:<scene>\" rows when checking the dashboard")
    args = ap.parse_args()

    scenes = load_scenes(args.scenes_root)
    opened = {item['action'].split(':', 1)[1] for items in (load_scenes(args.opened_from) if args.opened_from else
                                                             {}).values()
              for item in items if item['action'].startswith(('GUIDE:', 'DASH:'))}
    if args.opened_from in TYPED_IN:
        opened.add('GuideKeyboard')
    if not scenes:
        print('no scenes found'); return 1
    ids = {s: {it['id'] for it in items} for s, items in scenes.items()}
    problems = []

    # --- links resolve, and nothing is a dead end
    for scene, items in scenes.items():
        for it in items:
            outgoing = 0
            for d in DIRECTIONS:
                target = it['nav'][d]
                if not target:
                    continue
                outgoing += 1
                ts, ti = resolve(scene, target, scenes)
                if ts not in scenes:
                    problems.append(f'{scene}/{it["id"]}: {d} -> unknown scene {ts!r}')
                elif ti not in ids[ts]:
                    problems.append(f'{scene}/{it["id"]}: {d} -> unknown item {ts}/{ti}')
            action = it['action']
            if action.startswith('GOTO:') and action[5:] not in scenes:
                problems.append(f'{scene}/{it["id"]}: action -> unknown scene {action[5:]!r}')
            # A scene's ONLY item can't be a dead end: there is nowhere for it
            # to lead, and B is how you leave (the guide's Vibration page is
            # one checkbox).
            if outgoing == 0 and not action and len(items) > 1:
                problems.append(f'{scene}/{it["id"]}: dead end (no nav links, no action)')

    # --- reciprocity. Reported separately from hard problems: paging between
    # pages of different lengths has to clamp, so a link whose return lands on
    # a different row is expected there and only worth eyeballing.
    notes = []
    for scene, items in scenes.items():
        for it in items:
            for d in DIRECTIONS:
                target = it['nav'][d]
                if not target or target.endswith(':'):
                    continue    # 'scene:' has no single return row to be reciprocal with
                ts, ti = resolve(scene, target, scenes)
                if ts not in scenes or ti not in ids[ts]:
                    continue
                back = next((x for x in scenes[ts] if x['id'] == ti), None)
                if back is None:
                    continue
                ret = back['nav'][OPPOSITE[d]]
                if not ret:
                    continue
                rs, ri = resolve(ts, ret, scenes)
                if (rs, ri) != (scene, it['id']):
                    notes.append(
                        f'{scene}/{it["id"]} {d} -> {ts}/{ti}, but its {OPPOSITE[d]} '
                        f'goes to {rs}/{ri}')

    # --- reachability from the start scene
    starts = [s for s in (args.start or ['HubHome']) if s in scenes] or [sorted(scenes)[0]]
    start = ' / '.join(starts)
    seen, stack = set(), [(name, scenes[name][0]['id']) for name in starts if scenes[name]]
    stack += [(name, scenes[name][0]['id']) for name in opened if scenes.get(name)]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        s, i = node
        item = next((x for x in scenes.get(s, []) if x['id'] == i), None)
        if item is None:
            continue
        for d in DIRECTIONS:
            if item['nav'][d]:
                stack.append(resolve(s, item['nav'][d], scenes))
        if item['action'].startswith('GOTO:'):
            target_scene = item['action'][5:]
            if scenes.get(target_scene):
                stack.append((target_scene, scenes[target_scene][0]['id']))
        for target_scene in HANDS_OVER.get(s, []):
            if scenes.get(target_scene):
                stack.append((target_scene, scenes[target_scene][0]['id']))

    total = sum(len(v) for v in scenes.values())
    for scene, items in scenes.items():
        for it in items:
            if (scene, it['id']) not in seen:
                problems.append(f'{scene}/{it["id"]}: unreachable from {start}')

    print(f'{len(scenes)} scenes, {total} items, {len(seen)} reachable from {start}')
    for p in problems:
        print('  PROBLEM:', p)
    for n in notes:
        print('  note:', n)
    print('OK' if not problems else f'{len(problems)} problem(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
