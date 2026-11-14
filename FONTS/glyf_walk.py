"""Sequentially walk a decompressed glyf-style blob by structurally
parsing each TrueType glyph (rather than trusting any external offset
table), to discover real glyph boundaries independent of whatever the
xloc encoding scheme actually means."""
import struct


def measure_glyph_length(data, pos):
    """Return the number of bytes the glyph starting at `pos` consumes,
    or None if it doesn't look like a well-formed glyph at all."""
    if pos + 10 > len(data):
        return None
    numberOfContours = struct.unpack_from('>h', data, pos)[0]
    p = pos + 10  # skip numberOfContours + bbox (4 x int16)

    if numberOfContours >= 0:
        if pos + 10 + numberOfContours * 2 + 2 > len(data):
            return None
        endPts = struct.unpack_from('>%dH' % numberOfContours, data, p)
        p += numberOfContours * 2
        numPoints = (endPts[-1] + 1) if numberOfContours > 0 else 0
        # sanity: endPts must be non-decreasing and not absurd
        if numberOfContours > 0:
            if any(endPts[i] > endPts[i + 1] for i in range(numberOfContours - 1)):
                return None
            if numPoints > 20000:
                return None

        if p + 2 > len(data):
            return None
        instructionLength = struct.unpack_from('>H', data, p)[0]
        p += 2
        if instructionLength > 10000 or p + instructionLength > len(data):
            return None
        p += instructionLength

        # flags (with repeat-compression)
        flags = []
        while len(flags) < numPoints:
            if p >= len(data):
                return None
            f = data[p]; p += 1
            flags.append(f)
            if f & 0x08:  # REPEAT_FLAG
                if p >= len(data):
                    return None
                repeat = data[p]; p += 1
                flags.extend([f] * repeat)
        if len(flags) != numPoints:
            return None

        # x coordinates
        for f in flags:
            if f & 0x02:      # X_SHORT_VECTOR
                p += 1
            elif not (f & 0x10):  # not X_IS_SAME -> 2-byte delta
                p += 2
        # y coordinates
        for f in flags:
            if f & 0x04:      # Y_SHORT_VECTOR
                p += 1
            elif not (f & 0x20):  # not Y_IS_SAME -> 2-byte delta
                p += 2

        if p > len(data):
            return None
        return p - pos

    else:
        # composite glyph
        MORE_COMPONENTS = 0x0020
        ARG_1_AND_2_ARE_WORDS = 0x0001
        WE_HAVE_A_SCALE = 0x0008
        WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
        WE_HAVE_A_TWO_BY_TWO = 0x0080
        WE_HAVE_INSTRUCTIONS = 0x0100

        more = True
        last_flags = 0
        while more:
            if p + 4 > len(data):
                return None
            flags, glyphIndex = struct.unpack_from('>HH', data, p)
            p += 4
            p += 4 if (flags & ARG_1_AND_2_ARE_WORDS) else 2
            if flags & WE_HAVE_A_SCALE:
                p += 2
            elif flags & WE_HAVE_AN_X_AND_Y_SCALE:
                p += 4
            elif flags & WE_HAVE_A_TWO_BY_TWO:
                p += 8
            last_flags = flags
            more = bool(flags & MORE_COMPONENTS)
            if p > len(data):
                return None
        if last_flags & WE_HAVE_INSTRUCTIONS:
            if p + 2 > len(data):
                return None
            instructionLength = struct.unpack_from('>H', data, p)[0]
            p += 2
            if p + instructionLength > len(data):
                return None
            p += instructionLength
        return p - pos


def walk(data, start=0, max_glyphs=100000):
    """Sequentially measure glyphs from `start` to end of buffer.
    Returns (list_of_(offset,length), stopped_reason)."""
    pos = start
    results = []
    while pos < len(data) and len(results) < max_glyphs:
        # zero-length glyph (common: .notdef / space) -- can't tell from
        # structure alone how many padding bytes (if any) follow, so try
        # 0 first only if the *next* byte doesn't look like a plausible
        # numberOfContours for a real glyph. We handle this by trying to
        # measure at pos; measure_glyph_length naturally returns a valid
        # (possibly 0-content but >=10-byte) length only for non-empty
        # glyph records, since even an empty simple glyph still carries
        # the 10-byte header + 0 contours. There is no true "0-byte"
        # glyph in the glyf table itself (loca handles zero-length via
        # equal consecutive offsets) so every record here is >=10 bytes
        # unless we've hit padding/garbage.
        length = measure_glyph_length(data, pos)
        if length is None or length < 10:
            return results, f'stopped at {pos} (unparseable, {len(data)-pos} bytes left)'
        results.append((pos, length))
        pos += length
        if pos % 2 == 1:  # glyf entries are padded to even byte boundaries
            pos += 1
    return results, 'ok' if pos >= len(data) else f'stopped early at {pos}'
