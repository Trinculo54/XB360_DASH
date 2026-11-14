"""
Extractor for Xbox 360 dashboard .xzp resource packages (XUIZ container)
and the .xus localized-string tables found inside them (XUIS format).

Both formats were reverse-engineered by direct byte inspection (not from
any public spec) against shrdres.xzp from a real Xbox 360 dashboard.

XZP / "XUIZ" container layout:
  offset 0   : magic b'XUIZ'
  offset 4   : uint32 BE  version (seen: 1)
  offset 8   : uint32 BE  total file size (sanity check against actual size)
  offset 12  : uint32 BE  reserved/unknown (seen: 0)
  offset 16  : uint32 BE  size in bytes of the filename table that follows
                          (NOT counting this 22-byte header)
  offset 20  : uint16 BE  unknown (seen: 0x0098 in our sample; not yet
                          understood -- doesn't affect extraction)
  offset 22  : filename table starts here. Each entry:
                 uint32 BE  size    (bytes, of this file's data)
                 uint32 BE  offset  (bytes, into the data blob -- NOT the
                                     file -- see below)
                 uint8      name length, in UTF-16 code units
                 UTF-16BE   name (name_length * 2 bytes) -- may include
                            backslash-separated subfolders, e.g.
                            "de-de\\LiveAll.xus"
              Repeats until the table's declared byte length (header
              offset 16) is consumed.
  data blob  : starts immediately after the filename table (i.e. at
              22 + table_size). Every entry's `offset` field is relative
              to this point, not to the start of the file.

XUS / "XUIS" localized string table layout:
  offset 0  : magic b'XUIS'
  offset 4  : uint16 BE  unknown (seen: 514 / 0x0202, also 513 / 0x0201)
  offset 6  : uint16 BE  unknown (seen: 0)
  offset 8  : uint16 BE  file size (sanity check)
  offset 10 : uint16 BE  string count
  offset 12 : `count` entries follow. Two variants seen in the wild, both
              starting with a NUL-terminated UTF-8 string:
                v1 (e.g. shrdres.xzp): just the NUL-terminated string,
                   back to back, no trailer.
                v3 (e.g. L.dash.xex.videos.xzp): the NUL-terminated
                   string is followed by a 4-byte trailer:
                     uint16 BE  marker  (constant, seen: 0x010a)
                     uint16 BE  string id (a numeric lookup/resource id,
                                referenced elsewhere, e.g. by XUI markup;
                                not sequential -- ids can skip, e.g.
                                11, 13, 15, 16, 17, 19)
              Which variant a given file uses is auto-detected the same
              way parse_xzp() picks a name width: try one interpretation,
              and keep it only if it consumes the table down to exactly
              end-of-file (declared by the fileSize header field).
"""
import struct
import os
import sys
import json


def parse_xzp(data: bytes):
    # Some .xzp files (seen on ones tied to DRM/licensed content, e.g. the
    # video app's) are prefixed with a "BDES" block -- magic + uint32
    # version + a fixed-size high-entropy blob (a signature/integrity
    # check over what follows, not encryption: the XUIZ archive after it
    # is plain and parses normally). Rather than hardcode that block's
    # length, just locate the real XUIZ magic and start there.
    xuiz_offset = data.find(b'XUIZ')
    if xuiz_offset == -1:
        raise ValueError("no XUIZ magic found anywhere in file")
    if xuiz_offset != 0:
        print(f"  note: {xuiz_offset}-byte prefix before XUIZ magic "
              f"(likely a 'BDES' signature block) -- skipping it")
    data = data[xuiz_offset:]

    version, filesize, _reserved, table_size = struct.unpack_from('>IIII', data, 4)
    if filesize != len(data):
        raise ValueError(f"header fileSize {filesize} != actual size {len(data)} "
                          f"-- refusing to trust offsets on a mismatched file")

    HEADER_SIZE = 22
    data_start = HEADER_SIZE + table_size
    table_end = HEADER_SIZE + table_size

    def try_parse(name_width):
        """name_width=2 -> UTF-16BE names (namelen = code units);
        name_width=1 -> single-byte UTF-8 names (namelen = bytes).
        Seen in the wild: version 1 archives use UTF-16BE, version 3
        archives use single-byte -- rather than hardcode that mapping
        (unverified for other versions), try both and keep whichever
        one consumes *exactly* the declared table size. A wrong guess
        reliably drifts and fails that check well before the end.
        """
        pos = HEADER_SIZE
        out = []
        while pos + 9 <= table_end:
            size, off = struct.unpack_from('>II', data, pos)
            namelen = data[pos + 8]
            name_bytes = data[pos + 9:pos + 9 + namelen * name_width]
            if len(name_bytes) < namelen * name_width:
                return None
            try:
                name = name_bytes.decode('utf-16-be' if name_width == 2 else 'utf-8')
            except UnicodeDecodeError:
                return None
            out.append({'name': name, 'size': size, 'offset': off})
            pos += 9 + namelen * name_width
        return out if pos == table_end else None

    entries = try_parse(2) or try_parse(1)
    if entries is None:
        raise ValueError("filename table didn't parse cleanly as UTF-16BE or "
                          "single-byte names -- format may differ further")

    for e in entries:
        e['data'] = data[data_start + e['offset']: data_start + e['offset'] + e['size']]
    return entries


def parse_xus(blob: bytes):
    """Returns (strings, fmt) where fmt is 'v1' or 'v3'.
    v1: strings is a plain list[str].
    v3: strings is a list[{'text': str, 'id': int}] (id = the numeric
        string id from each entry's 4-byte trailer).
    """
    assert blob[0:4] == b'XUIS', "not a .xus/XUIS string table"
    _f1, _f2, filesize, count = struct.unpack_from('>HHHH', blob, 4)
    if filesize != len(blob):
        raise ValueError(f"XUIS header fileSize {filesize} != actual size {len(blob)}")

    def try_parse(trailer_len):
        """trailer_len=0 -> v1 (no trailer); trailer_len=4 -> v3
        (2-byte marker + 2-byte BE string id after each NUL). Keep
        whichever interpretation lands exactly on end-of-file."""
        pos = 12
        out = []
        for _ in range(count):
            end = blob.find(b'\x00', pos)
            if end == -1:
                return None
            try:
                s = blob[pos:end].decode('utf-8')
            except UnicodeDecodeError:
                return None
            pos = end + 1
            if trailer_len:
                if pos + trailer_len > len(blob):
                    return None
                _marker, sid = struct.unpack_from('>HH', blob, pos)
                pos += trailer_len
                out.append({'text': s, 'id': sid})
            else:
                out.append(s)
        return out if pos == len(blob) else None

    strings = try_parse(0)
    if strings is not None:
        return strings, 'v1'

    strings = try_parse(4)
    if strings is not None:
        return strings, 'v3'

    raise ValueError("string table didn't parse cleanly as v1 (no trailer) or "
                      "v3 (marker+id trailer) -- format may differ further")


def extract_all(xzp_path: str, out_dir: str):
    data = open(xzp_path, 'rb').read()
    entries = parse_xzp(data)
    os.makedirs(out_dir, exist_ok=True)

    for e in entries:
        out_path = os.path.join(out_dir, e['name'].replace('\\', os.sep))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'wb') as f:
            f.write(e['data'])

        if e['name'].lower().endswith('.xus'):
            try:
                strings, fmt = parse_xus(e['data'])
                json_path = out_path[:-4] + '.strings.json'
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump({'format': fmt, 'strings': strings}, f,
                              ensure_ascii=False, indent=2)
            except Exception as ex:
                print(f"  warning: couldn't decode strings from {e['name']}: {ex}")

    print(f"Extracted {len(entries)} files from {xzp_path} -> {out_dir}")
    return entries


def extract_folder(in_dir: str, out_dir: str, recursive: bool = True):
    """Find every .xzp file under in_dir and extract each one to
    out_dir/<xzp filename without extension>/, mirroring that package's
    own internal file/folder structure directly inside that folder.
    """
    if recursive:
        xzp_paths = []
        for root, _dirs, files in os.walk(in_dir):
            for fname in files:
                if fname.lower().endswith('.xzp'):
                    xzp_paths.append(os.path.join(root, fname))
    else:
        xzp_paths = [
            os.path.join(in_dir, fname)
            for fname in os.listdir(in_dir)
            if fname.lower().endswith('.xzp')
        ]
    xzp_paths.sort()

    if not xzp_paths:
        print(f"No .xzp files found under {in_dir}")
        return

    print(f"Found {len(xzp_paths)} .xzp file(s) under {in_dir}")
    failures = []
    for xzp_path in xzp_paths:
        xzp_name = os.path.splitext(os.path.basename(xzp_path))[0]
        target_dir = os.path.join(out_dir, xzp_name)
        print(f"\n[{xzp_path}]")
        try:
            extract_all(xzp_path, target_dir)
        except Exception as ex:
            print(f"  ERROR: failed to extract {xzp_path}: {ex}")
            failures.append(xzp_path)

    print(f"\nDone: {len(xzp_paths) - len(failures)}/{len(xzp_paths)} succeeded.")
    if failures:
        print("Failed files:")
        for f in failures:
            print(f"  {f}")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python xzp_extract.py <input.xzp | input_folder> <output_dir>")
        print()
        print("  If <input> is a single .xzp file, it is extracted directly into")
        print("  <output_dir>.")
        print()
        print("  If <input> is a folder, every .xzp file found inside it (recursively)")
        print("  is extracted to <output_dir>/<xzp filename>/")
        sys.exit(1)

    src, out_dir = sys.argv[1], sys.argv[2]
    if os.path.isdir(src):
        extract_folder(src, out_dir)
    elif os.path.isfile(src):
        xzp_name = os.path.splitext(os.path.basename(src))[0]
        extract_all(src, os.path.join(out_dir, xzp_name))
    else:
        print(f"Error: {src} is not a file or directory")
        sys.exit(1)