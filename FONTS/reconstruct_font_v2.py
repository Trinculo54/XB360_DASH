import struct, zlib, sys, io

PAGE_SHIFT = 17
PAGE_MASK = (1 << PAGE_SHIFT) - 1


def parse_xttf(data):
    assert data[0:4] == b'xttf'
    signedBlockSize, fileSize, compTables, uncompTables, ver = struct.unpack_from('>5I', data, 0x104)
    tables_blob = zlib.decompress(data[0x118:0x118 + compTables])
    assert len(tables_blob) == uncompTables

    numTables = struct.unpack_from('>H', tables_blob, 4)[0]
    directory = {}
    off = 12
    for i in range(numTables):
        tag, checksum, tOff, tLen = struct.unpack_from('>4sIII', tables_blob, off)
        directory[tag.decode('ascii')] = (tOff, tLen, checksum)
        off += 16
    return tables_blob, directory


def extract_raw(path):
    data = open(path, 'rb').read()
    tables_blob, directory = parse_xttf(data)
    out = {}
    for tag in ['cmap', 'head', 'hhea', 'hmtx', 'name']:
        o, l, _ = directory[tag]
        out[tag] = tables_blob[o:o + l]
    o, l, _ = directory['xloc']
    out['loca_raw'] = tables_blob[o:o + l]
    o, l, _ = directory['xglf']
    out['glyf_raw'] = data[o:o + l]
    return out


def parse_cmap_format4(cmap_bytes):
    """Minimal cmap parser: returns {codepoint: original_glyph_id}. Handles
    the common format 4 (BMP) subtable, which is what Latin fonts use."""
    numTables = struct.unpack_from('>H', cmap_bytes, 2)[0]
    best_offset = None
    for i in range(numTables):
        platformID, encodingID, offset = struct.unpack_from('>HHI', cmap_bytes, 4 + i * 8)
        fmt = struct.unpack_from('>H', cmap_bytes, offset)[0]
        if fmt == 4:
            best_offset = offset
            break
    assert best_offset is not None, "no format-4 cmap subtable found"
    base = best_offset
    fmt, length, language, segX2 = struct.unpack_from('>HHHH', cmap_bytes, base)
    segCount = segX2 // 2
    p = base + 14
    endCodes = struct.unpack_from('>%dH' % segCount, cmap_bytes, p); p += segX2
    p += 2  # reservedPad
    startCodes = struct.unpack_from('>%dH' % segCount, cmap_bytes, p); p += segX2
    idDeltas = struct.unpack_from('>%dh' % segCount, cmap_bytes, p); p += segX2
    idRangeOffsetsPos = p
    idRangeOffsets = struct.unpack_from('>%dH' % segCount, cmap_bytes, p); p += segX2

    result = {}
    for seg in range(segCount):
        start, end, delta, rangeOff = startCodes[seg], endCodes[seg], idDeltas[seg], idRangeOffsets[seg]
        if start == 0xFFFF and end == 0xFFFF:
            continue
        for c in range(start, end + 1):
            if rangeOff == 0:
                gid = (c + delta) & 0xFFFF
            else:
                addr = idRangeOffsetsPos + seg * 2 + rangeOff + (c - start) * 2
                gid = struct.unpack_from('>H', cmap_bytes, addr)[0]
                if gid != 0:
                    gid = (gid + delta) & 0xFFFF
            if gid != 0:
                result[c] = gid
    return result


def build_maxp(num_glyphs):
    return struct.pack('>IHHHHHHHHHHHHHH',
        0x00010000, num_glyphs,
        500, 50, 500, 50, 2, 200, 100, 100, 100, 500, 2000, 50, 10)


def build_post():
    return struct.pack('>IiHHIIIII', 0x00030000, 0, 0, 0, 0, 0, 0, 0, 0)


def force_long_loca(head_bytes):
    head = bytearray(head_bytes)
    struct.pack_into('>H', head, 50, 1)
    return bytes(head)


def build_cmap_format4(cp_to_gid: dict) -> bytes:
    """Build a minimal single-subtable (platform 3, encoding 1 = Windows
    BMP) format-4 cmap from a {codepoint: new_gid} map."""
    codes = sorted(cp_to_gid.keys())
    segments = []
    i = 0
    while i < len(codes):
        j = i
        while j + 1 < len(codes) and codes[j + 1] == codes[j] + 1 and \
                cp_to_gid[codes[j + 1]] == cp_to_gid[codes[j]] + 1:
            j += 1
        segments.append((codes[i], codes[j]))
        i = j + 1
    segments.append((0xFFFF, 0xFFFF))
    segCount = len(segments)

    endCodes, startCodes, idDeltas, idRangeOffsets = [], [], [], []
    for (s, e) in segments:
        if s == 0xFFFF:
            endCodes.append(0xFFFF); startCodes.append(0xFFFF)
            idDeltas.append(1); idRangeOffsets.append(0)
        else:
            startGid = cp_to_gid[s]
            delta = (startGid - s) & 0xFFFF
            if delta > 32767:
                delta -= 65536
            endCodes.append(e); startCodes.append(s)
            idDeltas.append(delta)
            idRangeOffsets.append(0)

    segX2 = segCount * 2
    searchRange = 2 * (1 << (segCount.bit_length() - 1)) if segCount > 0 else 2
    entrySelector = (segCount.bit_length() - 1) if segCount > 0 else 0
    rangeShift = segX2 - searchRange

    body = struct.pack('>HHHH', segX2, searchRange, entrySelector, rangeShift)
    body += struct.pack('>%dH' % segCount, *endCodes)
    body += struct.pack('>H', 0)  # reservedPad
    body += struct.pack('>%dH' % segCount, *startCodes)
    body += struct.pack('>%dh' % segCount, *idDeltas)
    body += struct.pack('>%dH' % segCount, *idRangeOffsets)

    subtable = struct.pack('>HHH', 4, 6 + len(body), 0) + body
    header = struct.pack('>HH', 0, 1) + struct.pack('>HHI', 3, 1, 12)
    return header + subtable


def build_hmtx(orig_hmtx: bytes, orig_to_new_order: list) -> bytes:
    n_orig = len(orig_hmtx) // 4
    entries = [struct.unpack_from('>Hh', orig_hmtx, i * 4) for i in range(n_orig)]
    out = b''.join(struct.pack('>Hh', *entries[g]) for g in orig_to_new_order)
    return out


def build_sfnt(tables: dict) -> bytes:
    from fontTools.ttLib.sfnt import SFNTWriter
    buf = io.BytesIO()
    writer = SFNTWriter(buf, numTables=len(tables), sfntVersion='\x00\x01\x00\x00')
    for tag, data in tables.items():
        writer[tag] = data
    writer.close()
    return buf.getvalue()


def build_page_font(xttp_path, page_number, out_path):
    raw = extract_raw(xttp_path)
    loca_raw = raw['loca_raw']
    n = len(loca_raw) // 4
    offsets = struct.unpack('>%dI' % n, loca_raw)
    pages = [o >> PAGE_SHIFT for o in offsets]

    # original glyph g's outline spans [offsets[g], offsets[g+1]) when
    # both share the same page (glyphs never straddle a page boundary).
    glyf_raw = raw['glyf_raw']
    orig_glyph_bytes = {}
    for g in range(n - 1):
        if pages[g] == page_number and pages[g + 1] == page_number:
            start = offsets[g] & PAGE_MASK
            end = offsets[g + 1] & PAGE_MASK
            orig_glyph_bytes[g] = glyf_raw[start:end]
        elif pages[g] == page_number and offsets[g] == offsets[g + 1]:
            orig_glyph_bytes[g] = b''  # zero-length glyph on this page

    cp_to_orig_gid = parse_cmap_format4(raw['cmap'])
    # keep only characters whose glyph actually lives on this page
    cp_to_orig_gid = {cp: gid for cp, gid in cp_to_orig_gid.items() if gid in orig_glyph_bytes}

    # new compact glyph order: gid 0 = synthetic empty .notdef, then every
    # original glyph id that's present on this page, sorted.
    present_orig_gids = sorted(orig_glyph_bytes.keys())
    new_order = [None] + present_orig_gids   # index -> orig gid ('None' = notdef)
    orig_to_new = {orig: i for i, orig in enumerate(new_order) if orig is not None}

    new_glyf = bytearray()
    # loca needs numGlyphs+1 entries. new_order[0] is the synthetic,
    # zero-length notdef, so it occupies loca[0]=0 *and* loca[1]=0 (its
    # own start/end pair) before the real glyphs' cumulative offsets begin.
    new_loca = [0, 0]
    for orig in new_order[1:]:
        new_glyf += orig_glyph_bytes[orig]
        new_loca.append(len(new_glyf))
    loca_bytes = struct.pack('>%dI' % len(new_loca), *new_loca)

    cp_to_new_gid = {cp: orig_to_new[gid] for cp, gid in cp_to_orig_gid.items()}

    # hmtx for notdef: reuse original glyph 0's metrics (safe default)
    n_orig_hmtx = len(raw['hmtx']) // 4
    notdef_metric = struct.unpack_from('>Hh', raw['hmtx'], 0)
    entries = [struct.unpack_from('>Hh', raw['hmtx'], i * 4) for i in range(n_orig_hmtx)]
    hmtx_out = struct.pack('>Hh', *notdef_metric)
    for orig in present_orig_gids:
        hmtx_out += struct.pack('>Hh', *entries[orig])

    tables = {
        'cmap': build_cmap_format4(cp_to_new_gid),
        'head': force_long_loca(raw['head']),
        'hhea': raw['hhea'],
        'hmtx': hmtx_out,
        'name': raw['name'],
        'maxp': build_maxp(len(new_order)),
        'post': build_post(),
        'loca': loca_bytes,
        'glyf': bytes(new_glyf),
    }
    sfnt_bytes = build_sfnt(tables)
    with open(out_path, 'wb') as f:
        f.write(sfnt_bytes)
    print(f'Page {page_number}: {len(present_orig_gids)} glyphs, '
          f'{len(cp_to_new_gid)} mapped characters, wrote {out_path} ({len(sfnt_bytes)} bytes)')
    return cp_to_new_gid


if __name__ == '__main__':
    build_page_font(sys.argv[1], int(sys.argv[2]), sys.argv[3])
