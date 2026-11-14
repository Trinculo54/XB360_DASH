"""
Parser for Xbox 360 compiled XUI layout files (.xur), version 8 ("XUIB",
version field 0x00000008 -- confirmed against every sample seen from
dash.bin's embedded archives, ToolVersion 0x0E).

This is a from-scratch Python port of the binary-format logic in the
open-source XUIHelper project (https://github.com/SGCSam/XUIHelper,
XUIHelper.Core/XUR/V8/*.cs), cross-validated byte-for-byte against a real
sample (archive_19/HD.xur) before trusting it -- see the header/count-header
decode notes below. It is NOT a guess: XUIHelper is a real, working
open-source reader/writer for this exact format, and every structural
constant here (field widths, section tag order, the packed-varint scheme,
the property-mask-per-hierarchy-class scheme) was taken directly from its
source, not reverse-engineered blind. The one thing we DID independently
verify against raw bytes (rather than just trusting the port) is the
header + count-header layout, decoded by hand against HD.xur:

    58 55 49 42                     "XUIB" magic
    00 00 00 08                     version = 8
    00 00 00 00                     flags = 0
    00 0e                           tool version = 0x0E
    00 00 03 a7                     file size = 935 (== len(data), matches)
    00 06                           sections count = 6
    0b 42 0b 15 06 00 00 00 00 00 00 02   count header, 12 packed-varint
                                           fields, decodes to (11, 66, 11,
                                           21, 6, 0, 0, 0, 0, 0, 0, 2) -- all
                                           small plausible counts for an
                                           11-object screen with no timelines
    "STRN" 00 00 00 68 00 00 00 7a  section table entry: tag, offset=104,
                                     size=122 -- offset 104 == 32-byte
                                     header + 6*12-byte section table, i.e.
                                     section data starts exactly where the
                                     table ends. size chains: each section's
                                     offset+size == the next section's
                                     offset, and the last section's
                                     offset+size == the declared file size.
                                     This is about as strong a self-
                                     consistency check as a format can give.

Property decoding (DATA section) requires a class/property SCHEMA -- the
XUR binary only stores a class name + a per-hierarchy-class bitmask of
"which of this class's declared properties (in schema order) are present",
so without knowing each class's ordered property list there's no way to
know how many bits a mask has or what type follows each set bit. That
schema is provided by xur_schema.py (loaded from Microsoft's real XUI SDK
class definitions, via XUIHelper).
"""
import struct
import os
import sys
import json

import xur_schema

MAGIC = b'XUIB'
EXPECTED_VERSION = 8

SECTION_STRN = b'STRN'
SECTION_VECT = b'VECT'
SECTION_QUAT = b'QUAT'
SECTION_CUST = b'CUST'
SECTION_FLOT = b'FLOT'
SECTION_COLR = b'COLR'
SECTION_KEYP = b'KEYP'
SECTION_KEYD = b'KEYD'
SECTION_NAME = b'NAME'
SECTION_DATA = b'DATA'


class ParseError(Exception):
    pass


class Reader:
    """Big-endian binary cursor with the XUR "packed uint" varint scheme:
      first byte < 0xF0            -> value = that byte (1 byte total)
      0xF0 <= first byte <= 0xFE   -> value = ((first<<8)&0xF00)|second (2 bytes)
      first byte == 0xFF           -> value = next 4 bytes as uint32 BE (5 bytes)
    """

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def seek(self, pos):
        self.pos = pos

    def u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def i16(self):
        v = struct.unpack_from('>h', self.data, self.pos)[0]
        self.pos += 2
        return v

    def u16(self):
        v = struct.unpack_from('>H', self.data, self.pos)[0]
        self.pos += 2
        return v

    def i32(self):
        v = struct.unpack_from('>i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def u32(self):
        v = struct.unpack_from('>I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self):
        v = struct.unpack_from('>f', self.data, self.pos)[0]
        self.pos += 4
        return v

    def bytes(self, n):
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def packed_uint(self):
        first = self.u8()
        if first == 0xFF:
            return self.u32()
        if first < 0xF0:
            return first
        second = self.u8()
        return ((first << 8) & 0xF00) | second

    def cstr(self):
        end = self.data.index(0, self.pos)
        s = self.data[self.pos:end].decode('utf-8', errors='replace')
        self.pos = end + 1
        return s


def read_header(r: Reader):
    magic = r.bytes(4)
    if magic != MAGIC:
        raise ParseError(f"bad magic {magic!r}, expected {MAGIC!r}")
    version = r.u32()
    if version != EXPECTED_VERSION:
        raise ParseError(f"unsupported version {version} (only v8 implemented)")
    flags = r.u32()
    tool_version = r.i16()
    filesize = r.u32()
    if filesize != len(r.data):
        raise ParseError(f"header fileSize {filesize} != actual size {len(r.data)}")
    sections_count = r.i16()
    return {
        'magic': magic, 'version': version, 'flags': flags,
        'tool_version': tool_version, 'filesize': filesize,
        'sections_count': sections_count,
    }


COUNT_HEADER_FIELDS = [
    'total_objects_count', 'total_unshared_object_properties_count',
    'shared_properties_array_count', 'shared_compound_properties_count',
    'shared_compound_properties_array_count',
    'total_keyframe_property_class_depth', 'total_timeline_property_class_depth',
    'timelines_count', 'keyframe_properties_count', 'keyframe_data_count',
    'named_frames_count', 'objects_with_children_count',
]


def read_count_header(r: Reader):
    return {name: r.packed_uint() for name in COUNT_HEADER_FIELDS}


def read_sections_table(r: Reader, count):
    entries = []
    for _ in range(count):
        tag = r.bytes(4)
        offset = r.u32()
        size = r.u32()
        entries.append({'tag': tag, 'offset': offset, 'size': size})
    return entries


# ---------------------------------------------------------------------------
# Pool sections (STRN/VECT/QUAT/CUST/FLOT/COLR) -- flat lists read start-to-
# end of their byte range, no internal structure beyond fixed-size records
# (except STRN and CUST, which are self-delimited).
# ---------------------------------------------------------------------------

def read_strn(data, entry):
    r = Reader(data, entry['offset'])
    total_len = r.u32()
    count = r.i16()
    strings = ['']  # index 0 is always the empty string (never stored)
    for _ in range(count):
        strings.append(r.cstr())
    if len(strings) - 1 != count:
        raise ParseError(f"STRN count mismatch: declared {count}, got {len(strings) - 1}")
    return strings


def read_vect(data, entry):
    r = Reader(data, entry['offset'])
    end = entry['offset'] + entry['size']
    out = []
    while r.pos < end:
        out.append((r.f32(), r.f32(), r.f32()))
    return out


def read_quat(data, entry):
    r = Reader(data, entry['offset'])
    end = entry['offset'] + entry['size']
    out = []
    while r.pos < end:
        out.append((r.f32(), r.f32(), r.f32(), r.f32()))
    return out


def read_flot(data, entry):
    r = Reader(data, entry['offset'])
    end = entry['offset'] + entry['size']
    out = []
    while r.pos < end:
        out.append(r.f32())
    return out


def read_colr(data, entry):
    r = Reader(data, entry['offset'])
    end = entry['offset'] + entry['size']
    out = []
    while r.pos < end:
        a, rr, g, b = r.u8(), r.u8(), r.u8(), r.u8()
        out.append({'a': a, 'r': rr, 'g': g, 'b': b})
    return out


def read_cust(data, entry):
    """CUST = a list of "figures" (custom vector-graphic outlines used by
    XuiFigure's Points property): bounding box + a list of bezier points,
    each point = (vertex, control1, control2), all XY floats."""
    r = Reader(data, entry['offset'])
    end = entry['offset'] + entry['size']
    figures = []
    while r.pos < end:
        data_length = r.i32()
        start = r.pos
        bbox = (r.f32(), r.f32())
        n_points = r.i32()
        points = []
        for _ in range(n_points):
            vx, vy = r.f32(), r.f32()
            c1x, c1y = r.f32(), r.f32()
            c2x, c2y = r.f32(), r.f32()
            points.append({'point': (vx, vy), 'control1': (c1x, c1y), 'control2': (c2x, c2y)})
        consumed = r.pos - start
        if consumed != data_length:
            raise ParseError(f"CUST figure data length mismatch: declared {data_length}, consumed {consumed}")
        figures.append({'bounding_box': bbox, 'points': points})
    return figures


def read_keyp(data, entry):
    r = Reader(data, entry['offset'])
    end = entry['offset'] + entry['size']
    out = []
    while r.pos < end:
        out.append(r.packed_uint())
    return out


INTERP_NONE = 'none'
INTERP_LINEAR = 'linear'
INTERP_EASE = 'ease'


def read_keyd(data, entry):
    r = Reader(data, entry['offset'])
    end = entry['offset'] + entry['size']
    out = []
    while r.pos < end:
        frame = r.packed_uint()
        flag_byte = r.u8()
        flags = flag_byte & 0x3F
        unknown = flag_byte >> 6  # never seen nonzero; matches upstream's own note that this is unexplained
        ease_in = ease_out = ease_scale = 0
        interp = INTERP_LINEAR
        if flags == 0x0:
            pass
        elif flags == 0x1:
            interp = INTERP_NONE
        elif flags == 0x2:
            ease_in, ease_out, ease_scale = r.u8(), r.u8(), r.u8()
            interp = INTERP_EASE
        elif flags == 0x3:
            pass
        elif flags == 0xA:
            r.packed_uint()  # unknown id, meaning not recovered upstream either
        elif flags == 0xB:
            r.u8()  # unknown byte, meaning not recovered upstream either
        # else: unhandled flag value, matches upstream behaviour of just continuing
        prop_index = r.packed_uint()
        out.append({
            'frame': frame, 'interpolation': interp,
            'ease_in': ease_in, 'ease_out': ease_out, 'ease_scale': ease_scale,
            'property_index': prop_index,
        })
    return out


NAMED_FRAME_COMMANDS = {
    0: 'Stop', 1: 'Play', 2: 'GoTo', 3: 'GoToAndPlay', 4: 'GoToAndStop',
}
_GOTO_COMMANDS = {2, 3, 4}


def read_name(data, entry, strings):
    r = Reader(data, entry['offset'])
    end = entry['offset'] + entry['size']
    out = []
    while r.pos < end:
        string_index = r.packed_uint()
        name = strings[string_index]
        keyframe = r.packed_uint()
        cmd_byte = r.u8()
        cmd = NAMED_FRAME_COMMANDS.get(cmd_byte, cmd_byte)
        entry_out = {'name': name, 'keyframe': keyframe, 'command': cmd}
        if cmd_byte in _GOTO_COMMANDS:
            target_index = r.packed_uint()
            entry_out['target'] = strings[target_index]
        out.append(entry_out)
    return out


# ---------------------------------------------------------------------------
# DATA section: the actual object tree.
# ---------------------------------------------------------------------------

class XurFile:
    def __init__(self, data):
        self.data = data
        self.header = None
        self.count_header = None
        self.sections = {}     # tag(bytes) -> parsed pool / entry dict
        self.strings = ['']
        self.vectors = []
        self.quaternions = []
        self.floats = []
        self.colours = []
        self.figures = []
        self.keyp = []
        self.keyd = []
        self.named_frames = []
        self.root_object = None
        # DATA section runtime state (mirrors XUR8.ReadPropertiesLists /
        # CompoundPropertyDatas -- shared/backreferenced property lists)
        self.read_properties_lists = []
        self.compound_property_datas = []


def parse_xur8(data: bytes) -> XurFile:
    xur = XurFile(data)
    r = Reader(data)
    xur.header = read_header(r)
    xur.count_header = read_count_header(r)
    table = read_sections_table(r, xur.header['sections_count'])

    by_tag = {}
    for e in table:
        by_tag.setdefault(e['tag'], e)  # first entry wins if a tag repeats (never seen)

    if SECTION_STRN in by_tag:
        xur.strings = read_strn(data, by_tag[SECTION_STRN])
    if SECTION_VECT in by_tag:
        xur.vectors = read_vect(data, by_tag[SECTION_VECT])
    if SECTION_QUAT in by_tag:
        xur.quaternions = read_quat(data, by_tag[SECTION_QUAT])
    if SECTION_FLOT in by_tag:
        xur.floats = read_flot(data, by_tag[SECTION_FLOT])
    if SECTION_COLR in by_tag:
        xur.colours = read_colr(data, by_tag[SECTION_COLR])
    if SECTION_CUST in by_tag:
        xur.figures = read_cust(data, by_tag[SECTION_CUST])
    if SECTION_KEYP in by_tag:
        xur.keyp = read_keyp(data, by_tag[SECTION_KEYP])
    if SECTION_KEYD in by_tag:
        xur.keyd = read_keyd(data, by_tag[SECTION_KEYD])
    if SECTION_NAME in by_tag:
        xur.named_frames = read_name(data, by_tag[SECTION_NAME], xur.strings)

    if SECTION_DATA not in by_tag:
        raise ParseError("no DATA section present")

    r2 = Reader(data, by_tag[SECTION_DATA]['offset'])
    xur.root_object = _read_object(xur, r2)
    return xur


def _resolve_custom_offset(xur, offset):
    """CUST/Points property: byte offset 0 is figure 0; otherwise walk the
    figures list accumulating each figure's encoded byte length
    (12 header + 24*point_count, per XUIHelper's CUSTSection) until it
    matches -- this offset is a serialization artifact (how far into a
    flattened CUST byte-stream this figure would start), not an index."""
    if offset == 0:
        if not xur.figures:
            raise ParseError("custom property referenced but no CUST figures present")
        return xur.figures[0]
    acc = 0
    for i in range(len(xur.figures) - 1):
        acc += 0x10 + (len(xur.figures[i]['points']) * 0x18)
        if acc == offset:
            return xur.figures[i + 1]
        elif acc > offset:
            break
    raise ParseError(f"couldn't resolve custom/Points offset {offset:#x} to a CUST figure")


_COMPOUND_CLASS_BY_PROP_NAME = {
    'Fill': 'XuiFigureFill',
    'Gradient': 'XuiFigureFillGradient',
    'Stroke': 'XuiFigureStroke',
}


def _read_property_value(xur, r: Reader, prop_def):
    t = prop_def.type
    if t == 'bool':
        return r.u8() > 0
    if t == 'integer':
        return r.packed_uint()
    if t == 'unsigned':
        return r.packed_uint()
    if t == 'string':
        idx = r.packed_uint()
        if idx >= len(xur.strings):
            raise ParseError(f"string index {idx} out of range ({len(xur.strings)} strings)")
        return xur.strings[idx]
    if t == 'float':
        idx = r.packed_uint()
        if idx >= len(xur.floats):
            raise ParseError(f"float index {idx} out of range ({len(xur.floats)} floats)")
        return xur.floats[idx]
    if t == 'vector':
        idx = r.packed_uint()
        if idx >= len(xur.vectors):
            raise ParseError(f"vector index {idx} out of range ({len(xur.vectors)} vectors)")
        return xur.vectors[idx]
    if t == 'quaternion':
        idx = r.packed_uint()
        if idx >= len(xur.quaternions):
            raise ParseError(f"quaternion index {idx} out of range ({len(xur.quaternions)} quaternions)")
        return xur.quaternions[idx]
    if t == 'colour':
        idx = r.packed_uint()
        if idx >= len(xur.colours):
            raise ParseError(f"colour index {idx} out of range ({len(xur.colours)} colours)")
        return xur.colours[idx]
    if t == 'custom':
        offset = r.packed_uint()
        figure = _resolve_custom_offset(xur, offset)
        return figure
    if t == 'object':
        return _read_object_property(xur, r, prop_def)
    raise ParseError(f"unhandled property type {t!r} for {prop_def.name}")


def _read_object_property(xur, r: Reader, prop_def):
    """'object'-typed properties (Fill/Gradient/Stroke) are indexes into a
    shared compound-property-list backreference table (built up as we go --
    xur.compound_property_datas), OR (if the index is new) an inline
    class+propmask-encoded compound object of a fixed class determined by
    the property's NAME (not stored in the file -- Fill/Gradient/Stroke are
    the only three that exist in this schema)."""
    idx = r.packed_uint()
    if 0 <= idx < len(xur.compound_property_datas):
        return xur.compound_property_datas[idx]

    compound_class_name = _COMPOUND_CLASS_BY_PROP_NAME.get(prop_def.name)
    if compound_class_name is None:
        raise ParseError(f"unhandled compound object property {prop_def.name!r}")
    compound_class = xur_schema.get_class(compound_class_name)
    if compound_class is None:
        raise ParseError(f"schema missing compound class {compound_class_name!r}")

    # NOTE: unlike the top-level/DATA8Section.TryReadProperties path, the
    # reference implementation's compound-object path (TryReadObjectProperty)
    # reads this count but never actually verifies read-count against it --
    # confirmed by tracing a real file: a Gradient sub-object declared count
    # 8 while its class (XuiFigureFillGradient) only has 4 property
    # definitions (so even every bit set could only ever yield 4 entries).
    # This is presumably a from-a-different-counting-scheme value (possibly
    # counting indexed values individually, e.g. one that has 2 stops would
    # count StopColor+StopPos as 2+2=4, plus Radial+NumStops=2, totalling 6..
    # doesn't cleanly explain 8 either) used elsewhere for the file's overall
    # "shared compound properties count" bookkeeping (count_header), not a
    # per-object structural invariant -- so we read and discard it here,
    # exactly like upstream does.
    declared_count = r.packed_uint()
    mask = r.packed_uint()
    props = []
    for i, pd in enumerate(compound_class.prop_defs):
        if mask & (1 << i):
            props.append(_read_property(xur, r, pd))
    xur.compound_property_datas.append(props)
    return props


def _read_property(xur, r: Reader, prop_def):
    if prop_def.indexed:
        index_count = r.u8()
        values = [_read_property_value(xur, r, prop_def) for _ in range(index_count)]
        return {'property': prop_def.name, 'type': prop_def.type, 'indexed': True, 'values': values}
    value = _read_property_value(xur, r, prop_def)
    return {'property': prop_def.name, 'type': prop_def.type, 'value': value}


def _read_properties(xur, r: Reader, class_name):
    hierarchy = xur_schema.get_class_hierarchy(class_name)
    if hierarchy is None:
        raise ParseError(
            f"unknown class {class_name!r} -- not present in the loaded XUI schema "
            f"(xur_schema.py); this class needs to be added from a matching XUIHelper "
            f"extensions XML, or it's a genuinely new/custom class this build introduced")

    declared_count = r.packed_uint()
    props = []
    for xu_class in hierarchy:
        mask = r.packed_uint()
        for i, pd in enumerate(xu_class.prop_defs):
            if mask & (1 << i):
                props.append(_read_property(xur, r, pd))
    if len(props) != declared_count:
        raise ParseError(
            f"{class_name} property count mismatch: declared {declared_count}, read {len(props)}")
    return props


def _read_object(xur, r: Reader):
    string_index = r.packed_uint()
    flags = r.u8()
    if string_index >= len(xur.strings):
        raise ParseError(f"object class-name string index {string_index} out of range")
    class_name = xur.strings[string_index]

    obj = {'class': class_name, 'properties': [], 'children': [], 'named_frames': [], 'timelines': []}

    if flags & 0x1:
        props = _read_properties(xur, r, class_name)
        obj['properties'] = props
        xur.read_properties_lists.append(props)
    elif flags & 0x8:
        shared_index = r.packed_uint()
        if shared_index >= len(xur.read_properties_lists):
            raise ParseError(f"shared properties index {shared_index} out of range "
                              f"({len(xur.read_properties_lists)} lists so far)")
        obj['properties'] = list(xur.read_properties_lists[shared_index])

    if flags & 0x2:
        children_count = r.packed_uint()
        for _ in range(children_count):
            obj['children'].append(_read_object(xur, r))

    if flags & 0x4:
        named_frames_count = r.packed_uint()
        if named_frames_count > 0:
            base_index = r.packed_uint()
            for i in range(named_frames_count):
                idx = base_index + i
                if idx >= len(xur.named_frames):
                    raise ParseError(f"named frame index {idx} out of range")
                obj['named_frames'].append(xur.named_frames[idx])

        if not obj['children']:
            return obj  # matches upstream: no children -> no timeline data follows

        timelines_count = r.packed_uint()
        for _ in range(timelines_count):
            obj['timelines'].append(_read_timeline(xur, r, obj))

    return obj


def _find_child_by_id(obj, name):
    for child in obj['children']:
        for p in child['properties']:
            if p['property'] == 'Id' and p.get('value') == name:
                return child
        found = _find_child_by_id(child, name)
        if found is not None:
            return found
    return None


def _get_property_definition_value(obj, prop_def):
    """Look up the current (non-animated) value of an indexed property on
    an already-parsed object, used as the base to patch individual indices
    into when a timeline only animates some indices of e.g. gradient stops."""
    for p in obj['properties']:
        if p['property'] == prop_def.name:
            return p.get('values')
    return None


def _read_timeline(xur, r: Reader, parent_obj):
    object_name_index = r.packed_uint()
    if object_name_index >= len(xur.strings):
        raise ParseError(f"timeline object name index {object_name_index} out of range")
    object_name = xur.strings[object_name_index]

    element_obj = _find_child_by_id(parent_obj, object_name)
    if element_obj is None:
        raise ParseError(f"timeline references object id {object_name!r} which wasn't found among children")

    hierarchy = xur_schema.get_class_hierarchy(element_obj['class'])
    if hierarchy is None:
        raise ParseError(f"unknown class {element_obj['class']!r} for timeline target")
    hierarchy = list(reversed(hierarchy))  # most-derived first, per upstream's classList.Reverse()

    prop_def_count = r.packed_uint()
    animated_prop_defs = []
    indexed_prop_indexes = []
    for _ in range(prop_def_count):
        packed_byte = r.u8()
        class_depth = packed_byte & 0x7F
        is_indexed = bool(packed_byte & 0x80)
        class_index = r.u8()
        if class_index >= len(hierarchy):
            raise ParseError(f"timeline class index {class_index} out of range")
        class_at_index = hierarchy[class_index]

        prop_index = 0
        for j in range(class_depth):
            prop_index = r.u8()
            if prop_index >= len(class_at_index.prop_defs):
                raise ParseError(f"timeline property index {prop_index} out of range for class {class_at_index.name}")
            this_pd = class_at_index.prop_defs[prop_index]
            if j != class_depth - 1:
                compound_name = _COMPOUND_CLASS_BY_PROP_NAME.get(this_pd.name)
                if compound_name is None:
                    raise ParseError(f"unhandled compound class step {this_pd.name!r} in timeline")
                compound_class = xur_schema.get_class(compound_name)
                if compound_class is None:
                    raise ParseError(f"schema missing compound class {compound_name!r}")
                class_at_index = compound_class

        pd = class_at_index.prop_defs[prop_index]
        animated_prop_defs.append(pd)
        if is_indexed:
            indexed_prop_indexes.append(r.packed_uint())

    keyframes_count = r.packed_uint()
    keyframe_base_index = r.packed_uint()

    keyframes = []
    for kf_i in range(keyframes_count):
        target_data_index = keyframe_base_index + kf_i
        if target_data_index >= len(xur.keyd):
            raise ParseError(f"keyframe data index {target_data_index} out of range")
        keyframe_meta = xur.keyd[target_data_index]

        animated_properties = []
        handled_indexed = 0  # resets every keyframe -- matches upstream's placement inside the keyframe loop
        for pd_i, pd in enumerate(animated_prop_defs):
            target_prop_index = keyframe_meta['property_index'] + pd_i
            if target_prop_index >= len(xur.keyp):
                raise ParseError(f"keyframe property index {target_prop_index} out of range")
            raw_index = xur.keyp[target_prop_index]

            if pd.type == 'string':
                if raw_index >= len(xur.strings):
                    raise ParseError(f"animated string index {raw_index} out of range")
                value = xur.strings[raw_index]
            elif pd.type == 'float':
                if raw_index >= len(xur.floats):
                    raise ParseError(f"animated float index {raw_index} out of range")
                value = xur.floats[raw_index]
            elif pd.type == 'colour':
                if raw_index >= len(xur.colours):
                    raise ParseError(f"animated colour index {raw_index} out of range")
                value = xur.colours[raw_index]
            elif pd.type == 'vector':
                if raw_index >= len(xur.vectors):
                    raise ParseError(f"animated vector index {raw_index} out of range")
                value = xur.vectors[raw_index]
            elif pd.type == 'quaternion':
                if raw_index >= len(xur.quaternions):
                    raise ParseError(f"animated quaternion index {raw_index} out of range")
                value = xur.quaternions[raw_index]
            elif pd.type == 'integer':
                value = raw_index
            elif pd.type == 'unsigned':
                value = raw_index
            elif pd.type == 'bool':
                value = raw_index != 0
            else:
                raise ParseError(f"unhandled animated property type {pd.type!r}")

            if pd.indexed:
                index_to_use = indexed_prop_indexes[handled_indexed]
                # find/patch an existing entry for this property def in this keyframe
                merged = None
                for ap in animated_properties:
                    if ap['property'] == pd.name:
                        merged = ap
                        break
                if merged is not None:
                    while len(merged['values']) <= index_to_use:
                        merged['values'].append(None)
                    merged['values'][index_to_use] = value
                    handled_indexed += 1
                    continue
                base_values = _get_property_definition_value(element_obj, pd)
                if base_values is None:
                    values = []
                else:
                    values = list(base_values)
                while len(values) <= index_to_use:
                    values.append(None)
                values[index_to_use] = value
                animated_properties.append({'property': pd.name, 'type': pd.type, 'indexed': True, 'values': values})
                handled_indexed += 1
            else:
                animated_properties.append({'property': pd.name, 'type': pd.type, 'value': value})

        keyframes.append({
            'frame': keyframe_meta['frame'],
            'interpolation': keyframe_meta['interpolation'],
            'ease_in': keyframe_meta['ease_in'],
            'ease_out': keyframe_meta['ease_out'],
            'ease_scale': keyframe_meta['ease_scale'],
            'properties': animated_properties,
        })

    return {'target': object_name, 'keyframes': keyframes}


# ---------------------------------------------------------------------------
# CLI / JSON export
# ---------------------------------------------------------------------------

def _json_default(o):
    if isinstance(o, bytes):
        return o.decode('latin1')
    raise TypeError(f"not JSON serializable: {o!r}")


def to_jsonable(obj):
    """Recursively convert tuples (vectors/colours/points) into JSON-safe
    structures; dicts/lists pass through as-is since we built them plainly."""
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    return obj


def extract_one(xur_path, out_path=None):
    data = open(xur_path, 'rb').read()
    xur = parse_xur8(data)
    result = {
        'header': {k: (v.decode('ascii') if isinstance(v, bytes) else v) for k, v in xur.header.items()},
        'root': xur.root_object,
    }
    jsonable = to_jsonable(result)
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(jsonable, f, indent=2, ensure_ascii=False)
    return jsonable


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python xur_extract.py <input.xur> [output.json]")
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) > 2 else None
    result = extract_one(sys.argv[1], out)
    if not out:
        print(json.dumps(result, indent=2, ensure_ascii=False))
