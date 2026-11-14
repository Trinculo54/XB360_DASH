"""
Full multi-page xttf/xttp1 -> TTF reconstruction.

KEY FIX over build_v4.py: the earlier "PAGE_SHIFT=17" theory was disproven
because it gave WRONG results -- but the real bug was just the shift
constant. With the correct constants:

  - xglf (raw, compressed) is divided into fixed 4096-byte-aligned blocks,
    one per "page". Each block independently zlib-decompresses (with
    trailing zero/garbage padding after the real deflate stream, which is
    simply ignored -- decompressobj() stops consuming once the deflate
    stream ends).
  - xloc (loca) offsets encode (page_number << 16) | byte_offset_within_
    that_page's_own_decompressed_buffer. So PAGE_SHIFT is 16, not 17.
  - Consecutive loca entries always share the same page number UNLESS
    they land exactly on a page boundary (page changes only ever happen
    between glyphs, never mid-glyph), so glyph gid g's outline is simply
    page_buffers[page(offsets[g])][offset[g] & 0xFFFF : offset[g+1] & 0xFFFF].
  - Glyph gid == loca index directly (no separate "walked index" remap
    needed once page/offset are decoded correctly).

Verified against xenonjklatin.xttp1: all 6 pages decompress cleanly, and
independently structurally-walking each page's glyphs lands on exactly
the same byte offsets the loca table already declares -- so this is a
fully cross-validated, self-consistent reconstruction.
"""
import struct, zlib, sys, os
sys.path.insert(0, '/tmp/bootanim_work')
from reconstruct_font_v2 import (parse_xttf, parse_cmap_format4, build_maxp, build_post,
                                   force_long_loca, build_cmap_format4, build_sfnt)

PAGE_SHIFT = 16
PAGE_BLOCK = 4096


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


def decompress_all_pages(glyf_raw):
    n_pages = len(glyf_raw) // PAGE_BLOCK
    assert len(glyf_raw) % PAGE_BLOCK == 0, \
        f"xglf size {len(glyf_raw)} isn't a multiple of {PAGE_BLOCK} -- page assumption may not hold"
    pages = []
    for pg in range(n_pages):
        block = glyf_raw[pg * PAGE_BLOCK:(pg + 1) * PAGE_BLOCK]
        d = zlib.decompressobj()
        out = d.decompress(block) + d.flush()
        pages.append(out)
    return pages


def build_font(xttp_path, out_path, verbose=True):
    raw = extract_raw(xttp_path)
    loca_raw = raw['loca_raw']
    n = len(loca_raw) // 4
    offsets = [struct.unpack_from('>I', loca_raw, i * 4)[0] for i in range(n)]
    num_glyphs_total = n - 1

    pages = decompress_all_pages(raw['glyf_raw'])
    if verbose:
        print(f"{xttp_path}: {len(pages)} pages, {num_glyphs_total} glyphs declared by loca")

    orig_glyph_bytes = {}
    for g in range(num_glyphs_total):
        start_off, end_off = offsets[g], offsets[g + 1]
        start_page, end_page = start_off >> PAGE_SHIFT, end_off >> PAGE_SHIFT
        if start_off == end_off:
            orig_glyph_bytes[g] = b''  # zero-length (space, etc.)
            continue
        page_buf = pages[start_page]
        lo = start_off & 0xFFFF
        if end_page == start_page:
            hi = end_off & 0xFFFF
        elif end_page == start_page + 1 and (end_off & 0xFFFF) == 0:
            # The last glyph on a page has its "end" recorded as byte 0
            # of the *next* page's slot rather than the true end of this
            # page's own decompressed buffer (both addresses are
            # equivalent in the encoder's mind since nothing follows on
            # this page) -- resolve it to this page's actual length.
            hi = len(page_buf)
        else:
            raise ValueError(f"glyph {g} straddles pages {start_page}->{end_page} "
                              f"(offsets {hex(start_off)}..{hex(end_off)}) -- page model doesn't hold")
        if hi > len(page_buf):
            raise ValueError(f"glyph {g} wants bytes up to {hi} but page {start_page} "
                              f"only decompressed to {len(page_buf)} bytes")
        orig_glyph_bytes[g] = page_buf[lo:hi]

    cp_to_orig_gid = parse_cmap_format4(raw['cmap'])
    mapped = {cp: gid for cp, gid in cp_to_orig_gid.items() if gid in orig_glyph_bytes}
    if verbose:
        missing = {cp: gid for cp, gid in cp_to_orig_gid.items() if gid not in orig_glyph_bytes}
        print(f"  cmap: {len(cp_to_orig_gid)} chars, {len(mapped)} resolved to real glyph data, "
              f"{len(missing)} point past declared glyph range")

    # gid 0 is the synthetic/original .notdef; keep the full original gid
    # space (0..num_glyphs_total-1) as the new gid space directly, since
    # loca already IS a dense compact table -- no remapping needed.
    new_glyf = bytearray()
    new_loca = [0]
    for g in range(num_glyphs_total):
        data = orig_glyph_bytes[g]
        new_glyf += data
        if len(new_glyf) % 2 == 1:
            new_glyf += b'\x00'
        new_loca.append(len(new_glyf))
    loca_bytes = struct.pack('>%dI' % len(new_loca), *new_loca)

    tables = {
        'cmap': build_cmap_format4(mapped),
        'head': force_long_loca(raw['head']),
        'hhea': raw['hhea'],
        'hmtx': raw['hmtx'],  # already 1:1 with original gid order, unchanged
        'name': raw['name'],
        'maxp': build_maxp(num_glyphs_total),
        'post': build_post(),
        'loca': loca_bytes,
        'glyf': bytes(new_glyf),
    }
    sfnt_bytes = build_sfnt(tables)
    with open(out_path, 'wb') as f:
        f.write(sfnt_bytes)
    if verbose:
        chars = sorted(mapped.keys())
        print(f"  wrote {out_path}: {num_glyphs_total} glyphs, {len(mapped)} mapped chars, "
              f"range {hex(chars[0])}-{hex(chars[-1])}")
    return mapped


if __name__ == '__main__':
    build_font(sys.argv[1], sys.argv[2])
