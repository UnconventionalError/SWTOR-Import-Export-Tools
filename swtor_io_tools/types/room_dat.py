# <pep8 compliant>

"""
Room .dat binary format reader (ROOM_DAT_BINARY_FORMAT_, used since patch
3.3.2). Ported field-for-field from Jedipedia's dat_room_bin-read.js
against real samples (default.dat/ext_arena.dat/etc from the Onderon PvP
arena, area id 4611686359685887000) -- the instance/property loop, the
0xABCD1234 alignment marker, and the file's self-declared resource path
all matched the ground truth exactly.

No bpy dependency -- plain Python, testable standalone. Deliberately does
NOT compose parent-chain transforms or build mathutils matrices here:
this module only reports each instance's own LOCAL scale/rotation/
position plus its ParentInstance id, so the Blender-object-creation layer
(ops/import_hms.py) can apply SWTOR's engine rule that .hms instances
ignore ParentInstance composition entirely (confirmed against Jedipedia's
own test-room-placement.mjs test suite) without this module needing to
know what a ".hms instance" even is.
"""

from typing import Dict, List, Optional

from .dat_common import assert_magic, read_length_prefixed_string, read_utf16_string, read_uint64_id
from ..utils.binary import DataView


# Known property-id hashes relevant to instance placement and to .hms
# specifically. This is NOT an exhaustive property dictionary (room .dat
# carries many more property kinds than area assembly needs) -- just the
# ones this module's own callers currently look for.
PROP_ID_PARENT_INSTANCE = 0x40865e7f
PROP_ID_POSITION = 0x4f77e269
PROP_ID_ROTATION = 0x8c64af1e
PROP_ID_SCALE = 0xb181622a
PROP_ID_VERTEX_DATA = 0xa3ab26ae  # ".hms" instances only

_PROP_TYPE_BOOL = 0
_PROP_TYPE_INT32 = 1
_PROP_TYPE_ENUM = 3
_PROP_TYPE_FLOAT32 = 4
_PROP_TYPE_INT64 = 5
_PROP_TYPE_VEC3 = 6
_PROP_TYPE_VEC4 = 7
_PROP_TYPE_STRING_UTF16 = 8
_PROP_TYPE_COMPRESSED_BINARY = 9


class RoomInstance(object):
    __slots__ = ("id", "asset_id", "properties")

    def __init__(self, instance_id, asset_id, properties):
        # type: (int, int, List[dict]) -> None
        self.id = instance_id
        self.asset_id = asset_id
        self.properties = properties

    def find_property(self, prop_id):
        # type: (int) -> Optional[dict]
        for prop in self.properties:
            if prop["id"] == prop_id:
                return prop
        return None

    @property
    def parent_id(self):
        # type: () -> Optional[int]
        prop = self.find_property(PROP_ID_PARENT_INSTANCE)
        return prop["value"] if prop is not None else None

    @property
    def local_position(self):
        # type: () -> tuple
        prop = self.find_property(PROP_ID_POSITION)
        if prop is None:
            return (0.0, 0.0, 0.0)
        return (prop["x"], prop["y"], prop["z"])

    @property
    def local_rotation_degrees(self):
        # type: () -> tuple
        """XYZ Euler, degrees -- matches SWTOR's own rotX/rotY/rotZ property axes."""
        prop = self.find_property(PROP_ID_ROTATION)
        if prop is None:
            return (0.0, 0.0, 0.0)
        return (prop["x"], prop["y"], prop["z"])

    @property
    def local_scale(self):
        # type: () -> tuple
        prop = self.find_property(PROP_ID_SCALE)
        if prop is None:
            return (1.0, 1.0, 1.0)
        return (prop["x"], prop["y"], prop["z"])

    @property
    def compressed_vertex_data(self):
        # type: () -> Optional[bytes]
        """Raw (still-compressed) bytes of the VertexData property, if present."""
        prop = self.find_property(PROP_ID_VERTEX_DATA)
        return prop["raw_compressed"] if prop is not None else None


class RoomDat(object):
    __slots__ = ("file_name", "instances", "visible")

    def __init__(self, file_name, instances, visible):
        # type: (str, List[RoomInstance], List[str]) -> None
        self.file_name = file_name
        self.instances = instances
        self.visible = visible


def _read_property(dv, pos):
    # type: (DataView, int) -> tuple
    """Returns (prop_dict, next_pos)."""
    prop_type = dv.getUint8(pos)
    pos += 1
    prop_id = dv.getUint32(pos, True)
    pos += 4
    prop = {"type": prop_type, "id": prop_id}

    if prop_type == _PROP_TYPE_BOOL:
        prop["value"] = dv.getUint8(pos) == 1
        pos += 1
    elif prop_type == _PROP_TYPE_INT32:
        prop["value"] = dv.getUint32(pos, True)
        pos += 4
    elif prop_type == _PROP_TYPE_ENUM:
        prop["value"] = dv.getUint32(pos, True)
        pos += 4
    elif prop_type == _PROP_TYPE_FLOAT32:
        prop["value"] = dv.getFloat32(pos, True)
        pos += 4
    elif prop_type == _PROP_TYPE_INT64:
        prop["value"] = read_uint64_id(dv, pos)
        pos += 8
    elif prop_type == _PROP_TYPE_VEC3:
        prop["x"] = dv.getFloat32(pos, True)
        prop["y"] = dv.getFloat32(pos + 4, True)
        prop["z"] = dv.getFloat32(pos + 8, True)
        pos += 12
    elif prop_type == _PROP_TYPE_VEC4:
        prop["r"] = dv.getFloat32(pos, True)
        prop["g"] = dv.getFloat32(pos + 4, True)
        prop["b"] = dv.getFloat32(pos + 8, True)
        prop["a"] = dv.getFloat32(pos + 12, True)
        pos += 16
    elif prop_type == _PROP_TYPE_STRING_UTF16:
        str_length = dv.getUint32(pos, True)
        pos += 4
        prop["value"] = read_utf16_string(dv, pos, str_length)
        pos += str_length
    elif prop_type == _PROP_TYPE_COMPRESSED_BINARY:
        data_length = dv.getUint32(pos, True)
        pos += 4
        # Keep the compressed bytes as-is (full container: header + payload
        # + trailer for whichever of zlib/gzip/zstd this is) -- decompress
        # lazily via dat_common.decompress_property_blob() only for the
        # properties a caller actually cares about (in practice: just
        # VertexData on .hms instances). Slicing is cheap; decompressing
        # every compressed property on every instance in a room is not.
        prop["raw_compressed"] = bytes(
            dv.buffer[dv.byteOffset + pos: dv.byteOffset + pos + data_length]
        )
        pos += data_length
    else:
        raise ValueError("Unrecognized room .dat property type %d at offset 0x%x" % (prop_type, pos))

    return prop, pos


def read_room_dat(dv):
    # type: (DataView) -> RoomDat
    assert_magic(dv, "ROOM_DAT_BINARY_FORMAT_")

    instances_offset = dv.getUint32(0x1c, True)
    visible_offset = dv.getUint32(0x20, True)

    # Section-version constants, always 3,1,1,1 in every sample seen so
    # far (matches dat_room_bin-read.js's own asserted values). Not
    # currently used for anything -- read and checked purely as an early
    # "we've mis-parsed the header" tripwire, same spirit as the project's
    # existing verify-before-ship practice.
    unk_const_3 = dv.getUint16(0x28, True)
    if unk_const_3 != 3:
        raise ValueError("Expected room .dat section-version constant 3 at 0x28, got %d" % unk_const_3)

    file_name, _ = read_length_prefixed_string(dv, 0x30)

    pos = instances_offset
    num_instances = dv.getUint32(pos, True)
    pos += 4

    instances = []
    for _ in range(num_instances):
        pos += 5  # skip 0xABCD1234 alignment marker (u32) + zero byte

        instance_id = read_uint64_id(dv, pos)
        pos += 8
        asset_id = read_uint64_id(dv, pos)
        pos += 8
        pos += 1  # skip zero byte

        num_props = dv.getUint32(pos, True)
        pos += 4
        prop_length = dv.getUint32(pos, True)
        pos += 4
        prop_end = pos + prop_length
        pos += 1  # skip zero byte

        properties = []
        for _ in range(num_props):
            prop, pos = _read_property(dv, pos)
            properties.append(prop)

        if pos != prop_end:
            raise ValueError(
                "Did not read property section completely for instance %d "
                "(expected to end at 0x%x, ended at 0x%x)" % (instance_id, prop_end, pos)
            )
        pos = prop_end

        instances.append(RoomInstance(instance_id, asset_id, properties))

    pos = visible_offset
    num_visible = dv.getUint32(pos, True)
    pos += 4
    visible = []
    for _ in range(num_visible):
        length = dv.getUint32(pos, True)
        pos += 4
        visible.append(read_utf16_string(dv, pos, length))
        pos += length

    return RoomDat(file_name, instances, visible)


def find_instances_by_asset_id(room_dat, asset_id):
    # type: (RoomDat, int) -> List[RoomInstance]
    return [inst for inst in room_dat.instances if inst.asset_id == asset_id]
