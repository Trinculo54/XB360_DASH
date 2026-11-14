# Compiled XUI layout format (.xur, v8) -- cracked

## What's in this zip

- `xur_schema.py` + `xur_schema/V8/*.xml` -- the class/property schema (which
  properties each XUI class like `XuiLabel`, `XuiImage`, `XuiFigure` has, in
  what order, and what type each one is). This is Microsoft's real XUI SDK
  class definitions for the 12611-17559 build range, sourced from the
  open-source XUIHelper project (github.com/SGCSam/XUIHelper) -- it's not
  guessed, it's the actual schema those binaries were compiled against.
- `xur_extract.py` -- the binary parser itself. Run it as:
    `python3 xur_extract.py path/to/some.xur output.json`
  or import `extract_one(path, out_path)` / `parse_xur8(data)` directly.
- `xur_json_out/` -- all 363 `.xur` files found inside `dash.bin` already
  converted to JSON, mirroring the archive_NN folder structure they came
  from (e.g. `archive_19/HD.xur` -> `xur_json_out/archive_19/HD.json`).

Every single one of the 363 files parses cleanly with zero errors -- that's
a strong signal the format decode is correct, not just "good enough":
mismatched structural assumptions almost always desync the byte stream and
blow up loudly (as two did during testing, both from real timeline/keyframe
bugs that are now fixed) rather than silently produce garbage.

## The format, in a nutshell

A `.xur` v8 file is: a small header, then 6 "pool" sections (STRN = string
table, VECT = 3-float vectors, QUAT = quaternions, FLOT = floats, COLR =
colours, CUST = vector-graphic point lists), then a DATA section holding the
actual UI as a tree of objects. Every property value elsewhere in the file
is just a small integer index into one of these pools -- this is why the
file is so compact (a screen with dozens of controls can be under 1KB).

The DATA section is a recursive tree, and **each JSON file's `root` object
already reflects that tree exactly**:

```json
{
  "class": "XuiCanvas",
  "properties": [ {"property": "Width", "type": "float", "value": 270.0}, ... ],
  "children": [ { "class": "XuiGroup", "properties": [...], "children": [...] }, ... ],
  "named_frames": [...],
  "timelines": [...]
}
```

- `class` is the XUI element type -- `XuiCanvas`/`XuiGroup` are pure
  containers, `XuiFigure` is a vector shape (rounded rects, gradients,
  bezier outlines -- see `Points`/`Fill`/`Stroke`), `XuiImage` displays a
  texture (`ImagePath` property -- these reference the PNGs already
  extracted from `shrdres.xzp` and the embedded archives; some use
  `common://name.png` for shared-resource-pack images, and some use
  runtime placeholder tokens like `{gamerpic}` or `{actionIcon}` that the
  original dashboard fills in at runtime, not literal files), `XuiLabel`/
  `XuiText`/`XuiTextPresenter` show text (`Text` property, `Visual`
  property names a style that maps to a font/formatting preset), and
  `XuiButton`/`XuiNavButton`/`XuiBackButton`/`XuiControl` etc. are
  interactive controls built from the above as children.
- Every element inherits from `XuiElement`, so nearly everything has
  `Width`, `Height`, `Position` ([x,y,z], relative to the PARENT element's
  origin -- this is a scene-graph, so to get a screen-space position you
  walk from root down summing `Position` at each level, same as any UI
  tree), `Scale`, `Rotation` (quaternion), `Opacity`, `Show` (visibility
  bool), and `Anchor`/`Pivot` for alignment -- only the properties that
  differ from their class's default are present at all (that's what the
  presence-bitmask in the binary format is for), so you'll want default
  values for anything not listed (Width/Height default 60/30, Scale
  1,1,1, Opacity effectively 1.0/visible unless stated, etc. -- see
  `xur_schema/V8/XuiElements.xml`'s `<DefaultVal>` tags for the full
  authoritative defaults per property).
- `timelines` hold keyframe animations (position/opacity/etc. changing over
  time) targeting a named child (matched by its `Id` property) -- present
  but not yet wired into anything, since getting static layout on screen
  is the immediate goal.

## Suggested integration path for the native app

1. Parse (or pre-convert, as done here) a `.xur` into this JSON tree.
2. Walk the tree recursively, accumulating a transform (position/scale)
   from parent to child.
3. For each visible (`Show` != false) leaf: `XuiFigure` -> draw a
   quad/path with the given fill colour or gradient; `XuiImage` -> draw a
   textured quad using the already-extracted PNG at `ImagePath` (skip/
   placeholder anything with `common://` prefix or `{token}` syntax for
   now); `XuiLabel`/`XuiText` -> draw the `Text` string using the
   already-cracked TTF fonts.
4. A good first target to actually see on screen: `archive_19/HD.xur` (the
   file used throughout this writeup) is small, self-contained, and
   doesn't reference any runtime-only placeholder assets -- every image
   and font it needs already exists in your extracted assets. Good smoke
   test before tackling a full scene like `hubscene.xur` or `LegendScene`.

## Known gaps (deliberately not blocking "see something on screen")

- Timelines/keyframe animation data is fully parsed and included in the
  JSON, but not documented/wired for playback here -- static frame first.
- `KEYD` section has ~2 unexplained flag values (`0xA`/`0xB`) that the
  upstream reference project itself never fully reverse-engineered either
  (their own source code comments note this); they're read correctly
  enough to not desync the stream, but their semantic meaning is unknown.
  None of the 363 files in this set hit those flag values during testing.
