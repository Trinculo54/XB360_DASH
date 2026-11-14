"""
Convert an Xbox 360 `.xtt` / `.xttp` font container into a plain `.ttf`.

THE CONTAINER
-------------
Both extensions are the same format, identified by the magic 'xttf':

    0x000  'xttf'
    0x004  RSA signature block (opaque -- not needed to read the font)
    0x104  >5I  signedBlockSize, fileSize, compTables, uncompTables, version
    0x118  zlib stream, `compTables` bytes -> `uncompTables` bytes

The decompressed blob is an ordinary sfnt: a TrueType table directory whose
tables are stored inline, except for three Xbox-specific ones:

    xglf   the glyf data -- lives in the OUTER file, not the blob (see below)
    xloc   the loca table, with chunk-relative offsets (see below)
    xchk   a SHA-1 per compressed chunk of xglf, for integrity

xglf is not one blob and not one zlib stream. It is a sequence of 4096-byte
chunks, each an independent zlib stream padded with zeros to the next 4096
boundary -- so the console can pull in and verify one chunk at a time
without touching the rest, which is what xchk's per-chunk SHA-1s are for.
(Verified: every chunk's SHA-1 matches, on all three fonts in this rip.)

xloc's offsets are therefore not offsets into one flat glyf blob. Each is:

    high 16 bits = which chunk       low 16 bits = offset within that chunk

which is why they run far past the size of any real glyf table -- the last
glyph of Segoe Xbox Light sits at 0x40D1C, i.e. chunk 4, offset 3356, and
that font's fifth and last chunk is exactly 3356 bytes. Decompress the
chunks, concatenate them, and add each chunk's base to the low half, and the
result is an ordinary long-format loca over an ordinary glyf table.

.xtt vs .xttp
-------------
Identical container; what differs is how complete the font inside it is.
The .xtt carries the full desktop table set -- OS/2, maxp, post, gasp and a
populated name table. The .xttp files carry only what the console's own
rasterizer reads: cmap, head, hhea, hmtx, a stub name, and the three x*
tables. So a .xttp has to have maxp/post synthesized to become a valid
desktop font, where a .xtt converts straight across.

    python3 xtt_convert.py <in.xtt|in.xttp> <out.ttf>
"""
import argparse
import hashlib
import io
import os
import struct
import sys
import zlib

CHUNK = 4096
CHUNK_SHIFT = 16                    # xloc: chunk index in the high 16 bits
CHUNK_MASK = (1 << CHUNK_SHIFT) - 1


def parse_container(data):
    """-> (table_blob, {tag: (offset, length)})."""
    if data[0:4] != b'xttf':
        raise ValueError('not an xttf container (bad magic)')
    _signed, _size, comp, uncomp, _ver = struct.unpack_from('>5I', data, 0x104)
    blob = zlib.decompress(data[0x118:0x118 + comp])
    if len(blob) != uncomp:
        raise ValueError(f'table blob is {len(blob)} bytes, header says {uncomp}')
    count = struct.unpack_from('>H', blob, 4)[0]
    directory = {}
    for i in range(count):
        tag, _cks, off, length = struct.unpack_from('>4sIII', blob, 12 + i * 16)
        directory[tag.decode('latin1')] = (off, length)
    return blob, directory


def decompress_chunks(data, offset, length, expected_hashes=None):
    """The xglf region -> a list of decompressed chunks, in order."""
    region = data[offset:offset + length]
    chunks = []
    for i in range(0, len(region), CHUNK):
        piece = region[i:i + CHUNK]
        if expected_hashes is not None:
            want = expected_hashes[(i // CHUNK) * 20:(i // CHUNK) * 20 + 20]
            if want and hashlib.sha1(piece).digest() != want:
                raise ValueError(f'xchk mismatch on chunk {i // CHUNK}')
        if piece[:2] != b'\x78\x9c':
            continue  # trailing padding, not a chunk
        chunks.append(zlib.decompressobj().decompress(piece))
    return chunks


def build_glyf_and_loca(chunks, raw_offsets):
    """Flatten the chunks and rebase xloc onto the result."""
    bases, total = [], 0
    for c in chunks:
        bases.append(total)
        total += len(c)
    glyf = b''.join(chunks)

    loca = []
    for raw in raw_offsets:
        index = raw >> CHUNK_SHIFT
        if index > len(bases):
            raise ValueError(f'xloc names chunk {index}, only {len(bases)} present')
        # An offset of (n, 0) is the END marker of the last glyph in chunk
        # n-1 as often as it is the start of chunk n's first glyph -- both
        # resolve to the same byte, so no special case is needed.
        base = bases[index] if index < len(bases) else total
        loca.append(base + (raw & CHUNK_MASK))
    if any(loca[i] > loca[i + 1] for i in range(len(loca) - 1)):
        raise ValueError('rebased loca is not monotonic -- chunk decoding is wrong')
    return glyf, loca


def measure_glyph(data, pos, limit):
    """Bytes the glyph at `pos` occupies, or None if it isn't well formed.

    A structural parse, used only to VERIFY the rebased offsets: if the
    chunk decoding were wrong, glyphs would start mid-outline and this would
    fail on nearly all of them.
    """
    if pos == limit:
        return 0                      # empty glyph (space, etc.)
    if pos + 10 > len(data):
        return None
    contours = struct.unpack_from('>h', data, pos)[0]
    p = pos + 10
    if contours < 0:                  # composite
        while True:
            if p + 4 > len(data):
                return None
            flags, _gid = struct.unpack_from('>HH', data, p)
            p += 4
            p += 4 if (flags & 1) else 2
            if flags & 0x8:      p += 2
            elif flags & 0x40:   p += 4
            elif flags & 0x80:   p += 8
            if not (flags & 0x20):
                break
        return p - pos
    if p + contours * 2 + 2 > len(data):
        return None
    ends = struct.unpack_from('>%dH' % contours, data, p) if contours else ()
    p += contours * 2
    points = (ends[-1] + 1) if contours else 0
    if points > 20000:
        return None
    instructions = struct.unpack_from('>H', data, p)[0]
    p += 2 + instructions
    flags, seen = [], 0
    while seen < points:
        if p >= len(data):
            return None
        f = data[p]; p += 1
        flags.append(f); seen += 1
        if f & 8:
            if p >= len(data):
                return None
            repeat = data[p]; p += 1
            flags.extend([f] * repeat); seen += repeat
    for shift, short_bit, same_bit in ((0, 2, 16), (1, 4, 32)):
        for f in flags[:points]:
            if f & short_bit:
                p += 1
            elif not (f & same_bit):
                p += 2
    return p - pos if p <= len(data) else None


def convert(in_path, out_path, verbose=True):
    from fontTools.ttLib.sfnt import SFNTWriter

    data = open(in_path, 'rb').read()
    blob, directory = parse_container(data)

    def table(tag):
        off, length = directory[tag]
        return blob[off:off + length]

    hashes = table('xchk') if 'xchk' in directory else None
    g_off, g_len = directory['xglf']
    chunks = decompress_chunks(data, g_off, g_len, hashes)

    raw = table('xloc')
    raw_offsets = struct.unpack('>%dI' % (len(raw) // 4), raw)
    glyf, loca = build_glyf_and_loca(chunks, raw_offsets)
    num_glyphs = len(loca) - 1

    bad = [g for g in range(num_glyphs)
           if measure_glyph(glyf, loca[g], loca[g + 1]) is None]
    if verbose:
        print(f'  {len(chunks)} chunks -> {len(glyf)} bytes of glyf, {num_glyphs} glyphs')
        print(f'  structurally valid glyphs: {num_glyphs - len(bad)}/{num_glyphs}')
    if len(bad) > num_glyphs // 20:
        raise ValueError(f'{len(bad)} glyphs failed to parse -- decoding is wrong')

    head = bytearray(table('head'))
    struct.pack_into('>h', head, 50, 1)          # indexToLocFormat = long

    out = {
        'head': bytes(head),
        'hhea': table('hhea'),
        'hmtx': table('hmtx'),
        'cmap': table('cmap'),
        'loca': struct.pack('>%dI' % len(loca), *loca),
        'glyf': glyf,
    }
    # A .xtt already has these; a .xttp doesn't, so they get synthesized.
    if 'maxp' in directory:
        maxp = bytearray(table('maxp'))
        struct.pack_into('>H', maxp, 4, num_glyphs)
        out['maxp'] = bytes(maxp)
    else:
        out['maxp'] = struct.pack('>IHHHHHHHHHHHHHH', 0x00010000, num_glyphs,
                                  500, 50, 500, 50, 2, 200, 100, 100, 100, 500, 2000, 50, 10)
    out['post'] = table('post') if 'post' in directory else \
        struct.pack('>IiHHIIIII', 0x00030000, 0, 0, 0, 0, 0, 0, 0, 0)
    for optional in ('OS/2', 'name', 'gasp', 'cvt ', 'fpgm', 'prep'):
        if optional in directory:
            out[optional] = table(optional)

    buf = io.BytesIO()
    writer = SFNTWriter(buf, numTables=len(out), sfntVersion='\x00\x01\x00\x00')
    for tag, payload in out.items():
        writer[tag] = payload
    writer.close()
    with open(out_path, 'wb') as f:
        f.write(buf.getvalue())
    if verbose:
        print(f'  tables: {" ".join(sorted(out))}')
        print(f'  wrote {out_path} ({os.path.getsize(out_path)} bytes)')
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('output')
    args = ap.parse_args()
    print(f'{os.path.basename(args.input)}:')
    convert(args.input, args.output)


if __name__ == '__main__':
    main()
