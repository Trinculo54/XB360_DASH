"""
Loader for the Xbox 360 XUI class-property schema (the ".xhe"/XML "extension"
files that describe every XuiElement-derived class and its ordered property
list). This schema is NOT embedded in the .xur binaries themselves -- the
binary format only stores small integer indexes ("this class had property
mask 0b101 against its class hierarchy's property definitions"), so without
this schema the DATA section literally cannot be decoded: there's no way to
know how many bits the mask has, what type each bit's value is, or what
order to read them in.

This schema was recovered from the open-source XUIHelper project
(https://github.com/SGCSam/XUIHelper), which ships the real Microsoft class
definitions (reverse-engineered by that project's author from XDK tooling)
as a set of XML files, one class per <XUIClass> element:

    <XUIClass Name="XuiLabel" BaseClassName="XuiTextElement">
      <PropDef Id="123" Name="Text" Type="string" Flags="localize">
        <DefaultVal></DefaultVal>
      </PropDef>
      ...
    </XUIClass>

The files under xur_schema/V8/ are copied verbatim from that project
(XUIHelper.Core/Assets/Extensions/V8/*.xml) -- they are Microsoft's actual
XUI SDK class definitions for builds 12611-17559 (the version range our
dash.xex's XUR8 files fall into: header ToolVersion 0x0E), not something
guessed or reconstructed by us. The 17559.xhe file lists the load order
(RelationalExtensions) used to merge them into one flat class table, which
we replicate here.

Only the read path matters for us (property presence bitmask -> ordered
PropertyDefinition list per class-in-hierarchy), so IgnoreProperties (an
editor/XUI-text-writer-only concern, confirmed unused by the binary XUR
reader in the reference implementation) is intentionally not implemented.
"""
import xml.etree.ElementTree as ET
import os

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xur_schema', 'V8')

# Load order taken directly from 17559.xhe's <RelationalExtensions> list.
LOAD_ORDER = [
    'XuiElements.xml',
    'XuiDataBinding.xml',
    'XamElements.xml',
    '17559CustomElements.xml',
    '17559SceneElements.xml',
    '17559ControlPackElements.xml',
    '17559HUDElements.xml',
]

TYPE_MAP = {
    'bool': 'bool',
    'integer': 'integer',
    'unsigned': 'unsigned',
    'float': 'float',
    'string': 'string',
    'color': 'colour',
    'vector': 'vector',
    'quaternion': 'quaternion',
    'object': 'object',
    'custom': 'custom',
}


class PropDef:
    __slots__ = ('id', 'name', 'type', 'flags', 'parent_class', 'default_raw')

    def __init__(self, id_, name, type_, flags, parent_class, default_raw=None):
        self.id = id_
        self.name = name
        self.type = type_
        self.flags = flags  # set of strings, e.g. {'indexed'}
        self.parent_class = parent_class
        # Raw <DefaultVal> text straight from the schema XML (None when the
        # element is absent entirely). Parsed lazily by _coerce_default, so a
        # malformed/odd default in one class can't break schema load for all
        # the others.
        self.default_raw = default_raw

    @property
    def indexed(self):
        return 'indexed' in self.flags

    def __repr__(self):
        return f'PropDef({self.name!r}, {self.type!r}{", indexed" if self.indexed else ""})'


class XUClass:
    __slots__ = ('name', 'base_class_name', 'prop_defs')

    def __init__(self, name, base_class_name, prop_defs):
        self.name = name
        self.base_class_name = base_class_name
        self.prop_defs = prop_defs  # ordered list[PropDef], in file/bit order

    def __repr__(self):
        return f'XUClass({self.name!r}, base={self.base_class_name!r}, {len(self.prop_defs)} props)'


_classes = {}  # name -> XUClass
_loaded = False


def _parse_file(path):
    tree = ET.parse(path)
    root = tree.getroot()
    for cls_el in root.findall('XUIClass'):
        name = cls_el.get('Name')
        base = cls_el.get('BaseClassName')
        if base in (None, '(null)'):
            base = None
        prop_defs = []
        for pd_el in cls_el.findall('PropDef'):
            # Id is only used by the reference implementation for its
            # writer-side duplicate-detection and editor UI -- a handful of
            # classes (all of the Xam* wrapper classes, which re-declare
            # their base XuiElement's editor-ignored layout properties)
            # simply omit it. It plays no role in decoding the binary
            # property-mask stream (only property TYPE and ORDER matter for
            # that), so default a missing Id to 0 rather than fail here.
            id_attr = pd_el.get('Id')
            pid = int(id_attr) if id_attr is not None else 0
            pname = pd_el.get('Name')
            ptype_raw = pd_el.get('Type')
            ptype = TYPE_MAP.get(ptype_raw, ptype_raw)
            flags_raw = pd_el.get('Flags') or ''
            flags = set(f for f in flags_raw.split('|') if f)
            dv_el = pd_el.find('DefaultVal')
            default_raw = None if dv_el is None else (dv_el.text or '')
            prop_defs.append(PropDef(pid, pname, ptype, flags, name, default_raw))
        if name in _classes:
            raise ValueError(f"duplicate class {name!r} while loading {path}")
        _classes[name] = XUClass(name, base, prop_defs)


def load():
    global _loaded
    if _loaded:
        return
    for fname in LOAD_ORDER:
        _parse_file(os.path.join(SCHEMA_DIR, fname))
    _loaded = True


def get_class(name):
    load()
    return _classes.get(name)


def get_class_hierarchy(derived_class_name):
    """Returns list[XUClass] from root ancestor down to the derived class
    itself (root-first order -- this is the order property masks are read
    in, per DATA8Section.TryReadProperties in the reference implementation).
    Returns None if the class (or any ancestor) isn't known."""
    load()
    hierarchy = []
    cur = _classes.get(derived_class_name)
    if cur is None:
        return None
    hierarchy.append(cur)
    while cur.base_class_name:
        cur = _classes.get(cur.base_class_name)
        if cur is None:
            return None
        hierarchy.append(cur)
    hierarchy.reverse()
    return hierarchy


# ---------------------------------------------------------------------------
# Property resolution (schema defaults + the object's own explicit values)
# ---------------------------------------------------------------------------
#
# A .xur object only stores the properties it actually overrides -- everything
# else falls back to the class's schema default. Consumers (xur_cook.py) want
# one flat "what is this object really" dict, so this is where the two get
# merged. Kept in the schema module rather than the cook script because the
# defaults are schema data, and because the timeline evaluator needs the exact
# same merge.

_UNSET = object()

# Opacity's <DefaultVal> in XuiElements.xml is literally the string "false"
# for a float-typed property -- a typo in the shipped schema, not a value.
# The XUI runtime's real default is fully opaque, and treating "false" as 0.0
# would make every element that doesn't set Opacity explicitly invisible.
_DEFAULT_OVERRIDES = {
    ('XuiElement', 'Opacity'): 1.0,
}


def _coerce_default(pd):
    raw = pd.default_raw
    override = _DEFAULT_OVERRIDES.get((pd.parent_class, pd.name), _UNSET)
    if override is not _UNSET:
        return override
    if raw is None:
        return _UNSET  # no <DefaultVal> element at all -- genuinely unset
    raw = raw.strip()
    t = pd.type
    try:
        if t == 'bool':
            return raw.lower() in ('true', '1')
        if t in ('integer', 'unsigned'):
            return int(raw, 0) if raw else 0
        if t == 'float':
            return float(raw) if raw else 0.0
        if t == 'string':
            return raw
        if t in ('vector', 'quaternion'):
            parts = [float(p) for p in raw.split(',')] if raw else []
            want = 3 if t == 'vector' else 4
            if len(parts) != want:
                return _UNSET
            return tuple(parts)
        if t == 'colour':
            v = int(raw, 16) if raw.lower().startswith('0x') else int(raw)
            return {'a': (v >> 24) & 0xFF, 'r': (v >> 16) & 0xFF,
                    'g': (v >> 8) & 0xFF, 'b': v & 0xFF}
    except ValueError:
        return _UNSET
    # 'object' (Fill/Gradient/Stroke) and 'custom' (Points) have no
    # meaningful textual default -- absent means absent.
    return _UNSET


def resolve_properties(class_name, properties):
    """Flattens one parsed object's property list against its class
    hierarchy's schema defaults -> {name: value}.

    `properties` is the object's own list of {'property', 'type', 'value'} /
    {'property', 'type', 'indexed': True, 'values': [...]} entries as produced
    by xur_extract. Indexed properties resolve to their `values` list.

    Returns None for an unknown class, so callers can skip that subtree with a
    warning rather than crashing -- same contract get_class_hierarchy uses.
    """
    hierarchy = get_class_hierarchy(class_name)
    if hierarchy is None:
        return None

    resolved = {}
    for xu_class in hierarchy:
        for pd in xu_class.prop_defs:
            d = _coerce_default(pd)
            if d is not _UNSET:
                resolved[pd.name] = d

    for p in properties:
        resolved[p['property']] = p['values'] if p.get('indexed') else p.get('value')

    return resolved


if __name__ == '__main__':
    load()
    print(f"Loaded {len(_classes)} classes.")
    for cname in ['XuiLabel', 'XuiImage', 'XuiFigure', 'XuiCanvas', 'XuiGroup']:
        h = get_class_hierarchy(cname)
        if h is None:
            print(f"  {cname}: NOT FOUND")
            continue
        total_props = sum(len(c.prop_defs) for c in h)
        print(f"  {cname}: hierarchy = {[c.name for c in h]}, total {total_props} props")
