#!/usr/bin/env python3
"""
bootanim_assets.py  --  extract / replace the geometry inside bootanim.xex

The Xbox 360 boot animation stores its three meshes inside the .data section of
bootanim.xex, in two custom codecs that the title decodes at startup:

  * index streams   : byte-wise delta encoding of uint16 indices
                      (recompiled function sub_98018798)
  * vertex streams  : arrays of BIG-ENDIAN int16, dequantised as
                          value = i16 / divisor + bias
                      3 int16/vertex for position-only meshes (sub_980188A0)
                      5 int16/vertex for position+uv meshes   (sub_98018BC8)

Both are decoded into a 32-byte runtime vertex:
      float3 position;  float3 normal;  float2 texcoord;
(normal is initialised to (0,0,1) and recomputed by the title)

Guest address -> file offset for this uncompressed XEX:  file = guest - 0x98000000 + 0x2000

Usage:
    python bootanim_assets.py extract bootanim.xex outdir/
    python bootanim_assets.py stats   bootanim.xex
"""
import struct, sys, os

BASE  = 0x98000000
PEOFF = 0x2000

# guest addr, byte length, kind, dequant params   (from BootAnim sub_9801C480)
MESHES = {
    # name : (pos_addr, pos_len, idx_addr, idx_len, pos_div, pos_bias, uv_div, uv_bias)
    "A_sphere": (0x9806AC50, 28104, 0x98071A18, 14780, 32480.5, 0.000391, None,    None),
    "B_xlogo":  (0x98063510, 18050, 0x98067B98,  4004, 33023.2, 0.001058, 65241.4, 0.493143),
    "C_xedges": (0x98068B40,  7620, 0x9806A908,   838, 32516.8, 0.001467, 70222.5, 0.482359),
}
# Draw primitive is D3DPT_TRIANGLESTRIP (5) - the index runs are one long strip.

def s8(b):  return b - 256 if b >= 128 else b

# ---------------------------------------------------------------- index codec
def decode_indices(buf):
    """buf -> list[int] (uint16).  Mirrors sub_98018798."""
    out, i, n = [], 0, len(buf)
    while i < n:
        if not out:
            if buf[0] == 0x80:                       # escape: absolute int16
                out.append((buf[1] | (s8(buf[2]) << 8)) & 0xFFFF); i = 3
            else:
                out.append(s8(buf[0]) & 0xFFFF);      i = 1
        else:
            d = s8(buf[i])
            if d == -128:                            # escape: int16 delta
                out.append((out[-1] + (buf[i+1] | (s8(buf[i+2]) << 8))) & 0xFFFF); i += 3
            else:
                out.append((out[-1] + d) & 0xFFFF);   i += 1
    return out

def encode_indices(idx):
    """list[int] -> bytes.  Inverse of decode_indices; use to inject your own mesh."""
    out = bytearray()
    for k, v in enumerate(idx):
        if k == 0:
            if -128 < v < 128:            # note: 0x80 itself must use the escape
                out.append(v & 0xFF)
            else:
                out += bytes([0x80, v & 0xFF, (v >> 8) & 0xFF])
        else:
            d = v - idx[k-1]
            if -128 < d < 128:
                out.append(d & 0xFF)
            else:
                if not (-32768 <= d <= 32767):
                    raise ValueError("index delta %d out of int16 range" % d)
                out += bytes([0x80, d & 0xFF, (d >> 8) & 0xFF])
    return bytes(out)

# --------------------------------------------------------------- vertex codec
def decode_vertices(buf, div, bias, uv_div=None, uv_bias=None):
    per = 3 if uv_div is None else 5
    nv  = (len(buf) // 2) // per
    raw = struct.unpack(">%dh" % (nv * per), buf[: nv * per * 2])
    pos, uv = [], []
    for i in range(nv):
        b = i * per
        pos.append(tuple(raw[b + k] / div + bias for k in range(3)))
        if per == 5:
            uv.append((raw[b+3] / uv_div + uv_bias, raw[b+4] / uv_div + uv_bias))
    return pos, uv

def encode_vertices(pos, uv, div, bias, uv_div=None, uv_bias=None):
    vals = []
    for i, p in enumerate(pos):
        for c in p:
            vals.append(max(-32768, min(32767, int(round((c - bias) * div)))))
        if uv_div is not None:
            for c in uv[i]:
                vals.append(max(-32768, min(32767, int(round((c - uv_bias) * uv_div)))))
    return struct.pack(">%dh" % len(vals), *vals)

# --------------------------------------------------------------------- driver
def load(path):
    return open(path, "rb").read()

def sub(d, guest, n):
    o = guest - BASE + PEOFF
    return d[o:o+n]

def strip_to_tris(idx):
    tris = []
    for i in range(len(idx) - 2):
        a, b, c = idx[i], idx[i+1], idx[i+2]
        if a == b or b == c or a == c:
            continue
        tris.append((a, b, c) if i % 2 == 0 else (a, c, b))
    return tris

def cmd_extract(xex, outdir):
    d = load(xex); os.makedirs(outdir, exist_ok=True)
    for name, (pa, pl, ia, il, pdiv, pbias, udiv, ubias) in MESHES.items():
        pos, uv = decode_vertices(sub(d, pa, pl), pdiv, pbias, udiv, ubias)
        idx     = decode_indices(sub(d, ia, il))
        tris    = strip_to_tris(idx)
        # raw streams, so you can diff / re-inject
        open(os.path.join(outdir, name + ".verts.bin"), "wb").write(sub(d, pa, pl))
        open(os.path.join(outdir, name + ".idx.bin"),   "wb").write(sub(d, ia, il))
        with open(os.path.join(outdir, name + ".obj"), "w") as f:
            f.write("# %s  %d verts  %d strip indices  %d triangles\n" % (name, len(pos), len(idx), len(tris)))
            for p in pos: f.write("v %.6f %.6f %.6f\n" % p)
            for t in uv:  f.write("vt %.6f %.6f\n" % t)
            for a, b, c in tris:
                if uv: f.write("f %d/%d %d/%d %d/%d\n" % (a+1,a+1,b+1,b+1,c+1,c+1))
                else:  f.write("f %d %d %d\n" % (a+1, b+1, c+1))
        print("%-10s %5d verts  %5d idx  %5d tris -> %s.obj" % (name, len(pos), len(idx), len(tris), name))

def cmd_stats(xex):
    d = load(xex)
    for name, (pa, pl, ia, il, pdiv, pbias, udiv, ubias) in MESHES.items():
        pos, uv = decode_vertices(sub(d, pa, pl), pdiv, pbias, udiv, ubias)
        idx     = decode_indices(sub(d, ia, il))
        xs=[p[0] for p in pos]; ys=[p[1] for p in pos]; zs=[p[2] for p in pos]
        print("%-10s verts=%-5d idx=%-6d maxidx=%-5d bbox=(%.3f..%.3f, %.3f..%.3f, %.3f..%.3f)"
              % (name, len(pos), len(idx), max(idx), min(xs),max(xs), min(ys),max(ys), min(zs),max(zs)))
        # round-trip check
        assert encode_indices(idx) == sub(d, ia, il), name + ": index round-trip mismatch"
        assert encode_vertices(pos, uv, pdiv, pbias, udiv, ubias) == sub(d, pa, pl)[:len(pos)*(6 if udiv is None else 10)], name+": vertex round-trip mismatch"
        print("           round-trip encode/decode: OK")

if __name__ == "__main__":
    if len(sys.argv) < 3: print(__doc__); sys.exit(1)
    if sys.argv[1] == "extract": cmd_extract(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "stats": cmd_stats(sys.argv[2])
    else: print(__doc__)
