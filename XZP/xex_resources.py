"""
Pull the embedded resource packs (.xzp) out of an unencrypted XEX2 and unpack
them with xzp_extract.py.

    python xex_resources.py <file.xex> <out_dir>

Writes <out_dir>/<module>.<resource>/... e.g. hud.xex.hud/GuideMain.xur.

Container layout is the one documented in THE DECOMP OF DOOM/extracted/
bootanim_notes.md: optional header 0x3FF says how the image is stored, 0x2FF
lists (name, virtual address, size) resources, and the decompressed image is a
memory image so file offset == address - image base.

Handles the two ways the update stores an image: "normal" (LZX -- the flash
files, hud.xex among them) and "basic" (zero runs cut out -- the loose modules
beside them, dash.xex among them). Not encrypted images; none of the ones with
UI in them are.

The LZX decoder follows libmspack's lzxd.c. It is bit-at-a-time and slow, which
is fine for flash-sized modules (hud.xex unpacks in a couple of seconds).
"""
import os
import struct
import sys

from xzp_extract import extract_all


def be32(b, o):
    return struct.unpack_from('>I', b, o)[0]


# ---------------------------------------------------------------- LZX ------

_EXTRA = []
_BASE = []
for _i in range(51):
    _EXTRA.append(min(17, max(0, (_i >> 1) - 1)))
    _BASE.append(sum(1 << e for e in _EXTRA[:-1]))


class _Bits:
    """16-bit little-endian words, consumed most significant bit first."""

    def __init__(self, data):
        self.d = data + b'\0' * 64
        self.pos = 0
        self.buf = 0
        self.n = 0

    def read(self, count):
        while self.n < count:
            self.buf = (self.buf << 16) | self.d[self.pos] | (self.d[self.pos + 1] << 8)
            self.pos += 2
            self.n += 16
        self.n -= count
        v = self.buf >> self.n
        self.buf &= (1 << self.n) - 1
        return v

    def align(self):
        self.buf = self.n = 0


def _tree(lens):
    """Canonical Huffman: (length, code) -> symbol."""
    table = {}
    code = 0
    for length in range(1, 17):
        for sym, l in enumerate(lens):
            if l == length:
                table[(length, code)] = sym
                code += 1
        code <<= 1
    return table


def _sym(bits, table):
    code = 0
    for length in range(1, 17):
        code = (code << 1) | bits.read(1)
        s = table.get((length, code))
        if s is not None:
            return s
    raise ValueError("LZX: bad Huffman code")


def _read_lens(bits, lens, first, last):
    pre = _tree([bits.read(4) for _ in range(20)])
    x = first
    while x < last:
        z = _sym(bits, pre)
        if z == 17:
            run = bits.read(4) + 4
            lens[x:x + run] = [0] * run
        elif z == 18:
            run = bits.read(5) + 20
            lens[x:x + run] = [0] * run
        elif z == 19:
            run = bits.read(1) + 4
            lens[x:x + run] = [(lens[x] - _sym(bits, pre)) % 17] * run
        else:
            run = 1
            lens[x] = (lens[x] - z) % 17
        x += run


def lzx_decompress(data, window_bits, out_len):
    slots = {20: 42, 21: 50}.get(window_bits, window_bits << 1)
    main_n = 256 + (slots << 3)
    bits = _Bits(data)
    out = bytearray()
    main_lens = [0] * (main_n + 64)
    len_lens = [0] * (250 + 64)
    main = length = aligned = None
    r0 = r1 = r2 = 1
    btype = blen = remaining = 0

    if bits.read(1):
        # shortcut: x86 E8 call translation not implemented -- PowerPC images
        # never set it. Port lzxd.c's intel block if this ever fires.
        raise ValueError("LZX: Intel E8 preprocessing flag set")

    while len(out) < out_len:
        frame_end = min(len(out) + 32768, out_len)
        while len(out) < frame_end:
            if remaining == 0:
                if btype == 3 and (blen & 1):
                    bits.pos += 1
                btype = bits.read(3)
                blen = remaining = (bits.read(16) << 8) | bits.read(8)
                if btype in (1, 2):
                    if btype == 2:
                        aligned = _tree([bits.read(3) for _ in range(8)])
                    _read_lens(bits, main_lens, 0, 256)
                    _read_lens(bits, main_lens, 256, main_n)
                    main = _tree(main_lens[:main_n])
                    _read_lens(bits, len_lens, 0, 249)
                    length = _tree(len_lens[:249])
                elif btype == 3:
                    if bits.n == 0:
                        bits.pos += 2
                    bits.align()
                    r0, r1, r2 = struct.unpack_from('<III', bits.d, bits.pos)
                    bits.pos += 12
                else:
                    raise ValueError(f"LZX: bad block type {btype}")

            run = min(remaining, frame_end - len(out))
            remaining -= run
            if btype == 3:
                out += bits.d[bits.pos:bits.pos + run]
                bits.pos += run
                continue

            stop = len(out) + run
            while len(out) < stop:
                m = _sym(bits, main)
                if m < 256:
                    out.append(m)
                    continue
                m -= 256
                mlen = m & 7
                if mlen == 7:
                    mlen += _sym(bits, length)
                mlen += 2
                slot = m >> 3
                if slot > 2:
                    extra = _EXTRA[slot]
                    off = _BASE[slot] - 2
                    if btype == 2 and extra >= 3:
                        off += (bits.read(extra - 3) << 3) + _sym(bits, aligned)
                    elif extra:
                        off += bits.read(extra)
                    r2, r1, r0 = r1, r0, off
                elif slot == 0:
                    off = r0
                elif slot == 1:
                    off = r1
                    r1, r0 = r0, off
                else:
                    off = r2
                    r2, r0 = r0, off
                if off > len(out):
                    raise ValueError("LZX: match before start of output")
                for _ in range(mlen):
                    out.append(out[-off])
            if len(out) != stop:
                raise ValueError("LZX: match overran its block")
        bits.align()
    return bytes(out)


# ---------------------------------------------------------------- XEX ------

def xex_image(d):
    """-> (decompressed memory image, image base, [(name, address, size)])"""
    if d[:4] != b'XEX2':
        raise ValueError("not a XEX2 file")
    pe_off, sec = be32(d, 8), be32(d, 0x10)
    hdr = {be32(d, 0x18 + i * 8): be32(d, 0x1c + i * 8) for i in range(be32(d, 0x14))}
    image_size, base = be32(d, sec + 4), be32(d, sec + 0x110)

    ff = hdr[0x3FF]
    enc, comp = struct.unpack_from('>HH', d, ff + 4)
    if enc:
        raise ValueError("XEX is encrypted -- needs the AES key, not handled here")
    if comp == 2:
        window, block = be32(d, ff + 8), be32(d, ff + 12)
        p, stream = pe_off, bytearray()
        while block:
            nxt, q = be32(d, p), p + 24
            while True:
                n = struct.unpack_from('>H', d, q)[0]
                if not n:
                    break
                stream += d[q + 2:q + 2 + n]
                q += 2 + n
            p, block = p + block, nxt
        image = lzx_decompress(bytes(stream), window.bit_length() - 1, image_size)
    elif comp == 1:
        # "Basic": not compression so much as the image with its runs of
        # zeros cut out. The header lists (data size, zero size) pairs; the
        # data pieces sit back to back in the file from pe_off.
        image, p = bytearray(), pe_off
        for o in range(ff + 8, ff + be32(d, ff), 8):
            data_size, zero_size = be32(d, o), be32(d, o + 4)
            image += d[p:p + data_size] + bytes(zero_size)
            p += data_size
        image = bytes(image)
    elif comp == 0:
        image = d[pe_off:pe_off + image_size]
    else:
        raise ValueError(f"XEX compression type {comp} not handled")

    res = []
    if 0x2FF in hdr:
        r = hdr[0x2FF]
        for o in range(r + 4, r + be32(d, r), 16):
            res.append((d[o:o + 8].rstrip(b'\0').decode('ascii'), be32(d, o + 8), be32(d, o + 12)))
    return image, base, res


def main(xex_path, out_dir):
    image, base, res = xex_image(open(xex_path, 'rb').read())
    # No "is it a PE" check: huduiskin.xex is resources only, its image starts
    # straight at the first pack. The XUIZ magic below is the sanity check.
    module = os.path.basename(xex_path).replace('$flash_', '')
    os.makedirs(out_dir, exist_ok=True)
    for name, addr, size in res:
        blob = image[addr - base:addr - base + size]
        if blob[:4] != b'XUIZ':
            print(f"  skipping resource {name}: not an XUIZ pack")
            continue
        xzp = os.path.join(out_dir, f"{module}.{name}.xzp")
        with open(xzp, 'wb') as f:
            f.write(blob)
        extract_all(xzp, os.path.join(out_dir, f"{module}.{name}"))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python xex_resources.py <file.xex> <out_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
