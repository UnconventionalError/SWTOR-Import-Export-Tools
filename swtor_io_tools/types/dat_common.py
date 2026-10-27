# <pep8 compliant>

"""
Shared low-level readers for SWTOR's binary `.dat` formats (room .dat,
area .dat -- both start "<u32 magic-length><magic-string>..." and share
the same length-prefixed-string and 64-bit-id conventions).

No bpy dependency -- plain Python, testable standalone, ported field-for-
field against Jedipedia's dat_room_bin-read.js / readAreaDat.mjs (ground
truth references, cross-validated against real .dat samples).

STANDALONE-NOTE: this module's job (shared binary-format primitives) is
permanent, reusable code -- not a standalone-only shim. It has no reason
to change at merge time.
"""

import gzip
import zlib
from typing import Tuple

from ..utils.binary import DataView


ZSTD_MAGIC = bytes((0x28, 0xB5, 0x2F, 0xFD))


def assert_magic(dv, expected_magic):
    # type: (DataView, str) -> None
    """
    Every `.dat` file of this family opens with a u32 magic-length
    followed by that many bytes of an ASCII magic string (itself
    null-terminated, so magic-length == len(expected_magic) + 1).
    Raises ValueError with a message naming what was actually found,
    matching the ground-truth readers' own assert() behaviour.
    """
    magic_length = dv.getUint32(0, True)
    expected_length = len(expected_magic) + 1
    if magic_length != expected_length:
        raise ValueError(
            "Expected magic to be %d bytes long but it was %d!" % (expected_length, magic_length)
        )
    magic = read_fixed_string(dv, 0x4, magic_length).rstrip("\x00")
    if magic != expected_magic:
        raise ValueError("Expected magic to be %r but it was %r!" % (expected_magic, magic))


def read_fixed_string(dv, pos, length):
    # type: (DataView, int, int) -> str
    """
    Reads exactly `length` bytes starting at `pos` as a Latin-1/ASCII
    string (SWTOR .dat strings are plain single-byte-per-char, unlike
    .gr2's occasional UTF-16 properties). Does NOT stop at a null byte
    -- callers that know the block is null-terminated within `length`
    should .rstrip("\\x00") the result themselves (see assert_magic).
    """
    raw = bytes(dv.buffer[dv.byteOffset + pos: dv.byteOffset + pos + length])
    return raw.decode("latin-1")


def read_length_prefixed_string(dv, pos):
    # type: (DataView, int) -> Tuple[str, int]
    """
    The pervasive "<u32 length><that many bytes, usually null-terminated>"
    string convention used throughout both AREA_DAT_BINARY_FORMAT_ and
    ROOM_DAT_BINARY_FORMAT_ for room names, asset paths, filenames, etc.

    Returns (string, next_pos) -- next_pos is pos + 4 + length, i.e. right
    after this whole field, so callers can chain reads without
    recomputing offsets.
    """
    length = dv.getUint32(pos, True)
    pos += 4
    s = read_fixed_string(dv, pos, length).rstrip("\x00")
    pos += length
    return s, pos


def read_utf16_string(dv, pos, byte_length):
    # type: (DataView, int, int) -> str
    """
    Reads `byte_length` bytes starting at `pos` as null-terminated
    UTF-16LE (room .dat string properties, envScheme, .visible room
    names, etc. are all stored this way, distinct from the single-byte
    length-prefixed strings used for room/asset names in the file
    headers -- see read_length_prefixed_string above).
    """
    raw = bytes(dv.buffer[dv.byteOffset + pos: dv.byteOffset + pos + byte_length])
    return raw.decode("utf-16-le").rstrip("\x00")


def read_uint64_id(dv, pos):
    # type: (DataView, int) -> int
    """
    SWTOR's 64-bit instance/asset ids are stored as two consecutive
    little-endian uint32s (lo, then hi) -- matching uint64(idLo, idHi)
    in the JS references. Python ints are arbitrary precision, so this
    just combines them directly instead of building a decimal string
    the way the JS reader does for display purposes.
    """
    lo = dv.getUint32(pos, True)
    hi = dv.getUint32(pos + 4, True)
    return lo | (hi << 32)


def decompress_property_blob(raw):
    # type: (bytes) -> bytes
    """
    Decompresses a room .dat "compressed binary data" (property type 9)
    payload. Live data has been observed as zlib; gzip and zstd are
    handled too since both dat_room_bin-read.js and readHeightmap.mjs
    account for them (zstd starting client patch 7.6.0). Raises
    ValueError on an unrecognized magic rather than guessing.

    NOTE: unlike dat_room_bin-read.js's own inline handling (which reads
    the u32 dataLength prefix itself before sniffing the magic), the
    caller here is expected to have already sliced out just the
    compressed bytes (i.e. pass `raw` starting at the compression magic,
    not at the dataLength field) -- see room_dat.py's property reader.
    """
    if len(raw) < 2:
        raise ValueError("compressed blob too short (%d bytes)" % len(raw))
    if raw[:4] == ZSTD_MAGIC:
        # zstd support depends on the Python build (native since CPython
        # 3.14 / via `zlib` on Node 22.15+; not universal on older
        # Blender-bundled Pythons). Import lazily so the zlib/gzip path
        # -- everything actually observed in our own samples so far --
        # never pays for an import that might not exist.
        try:
            import zstandard
        except ImportError as exc:
            raise ValueError(
                "zstd-compressed heightmap blob encountered, but the "
                "'zstandard' package isn't available in this Python. "
                "(Not yet seen in real data -- zlib/gzip cover every "
                "sample tested so far.)"
            ) from exc
        return zstandard.ZstdDecompressor().decompress(raw)
    if raw[0] == 0x1F and raw[1] == 0x8B:
        return gzip.decompress(raw)
    if (raw[0] & 0x0F) == 8 and ((raw[0] << 8) + raw[1]) % 31 == 0:
        # Standard zlib header sniff (matches readHeightmap.mjs exactly):
        # CM nibble == 8 (deflate) and the 16-bit header is a multiple of 31.
        return zlib.decompress(raw)
    raise ValueError(
        "unknown compression magic 0x%02x%02x" % (raw[0], raw[1])
    )
