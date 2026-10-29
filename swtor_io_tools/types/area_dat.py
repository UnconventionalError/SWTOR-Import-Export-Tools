# <pep8 compliant>

"""
Area .dat binary format reader (AREA_DAT_BINARY_FORMAT_). Ported from
Jedipedia's readAreaDat.mjs, cross-checked against area.dat from the
Onderon PvP arena (area id 4611686359685887000) -- the room list
(ext_arena, int_arena, red_interior, blue_interior, blue_train, Default,
skyscene, _everywhere_, red_train) and the section-offset header matched
by hand before this module existed, and now via read_area_dat() itself
(see test_area_dat.py).

No bpy dependency -- plain Python, testable standalone.

Sections read, by which stage of the .hms pipeline needed them:
  Stage 1 (geometry) -- header, rooms, assets (heightmap_asset_id).
  Stage 2 (materials) -- terrain_textures (splat layer id -> real .mat
    name).
  Stage 3 (dynamic detail / terrain scatter -- grass, rocks, small
    foliage) -- dyd_textures and dyd_channels, added once
    types.hms.Heightmap gained its own dyd_channels field. Confirmed
    end-to-end against the real Onderon arena sample: every one of 55
    real DYD channel instances across all 13 real heightmap tiles
    resolves cleanly to a named asset here (billboard sprite textures at
    low ids, real .gr2 mesh paths at higher ones), zero unresolved ids.
    A type-0 channel's 15 raw parameter values are now fully decoded --
    see DydPlacementParams -- confirmed against dat_VertexData.js's own
    placement algorithm (buildDydPlacements), which was already in our
    reference bundle from the very first upload. Type-2 and type-3
    records are parsed but confirmed UNUSED by that same placement code
    (only type-0 feeds it) -- not a gap on our end, the reference viewer
    itself never consumes them either.
"""

from typing import List, NamedTuple, Optional

from .dat_common import assert_magic, read_length_prefixed_string, read_uint64_id
from ..utils.binary import DataView


HMS_PLACEHOLDER_ASSET_NAME = "\\engine\\heightmap.hms"


class AreaDatAsset(NamedTuple):
    id: int
    name: str


class AreaDatTerrainTexture(NamedTuple):
    id: int
    layer: int  # usually 0 -- meaning undetermined, kept for completeness
    name: str


class AreaDatDydTexture(NamedTuple):
    """A billboard sprite texture (bare .mat-style name -- same
    resolve_mat_path()/`_build_named_material()` convention as terrain
    textures, at low ids) or a real .gr2 mesh path (at higher ids),
    keyed by id -- the SAME id space types.hms.Heightmap.dyd_channels'
    own channelId uses directly (confirmed end-to-end: every real
    channelId seen in the Onderon sample matches an id here exactly)."""
    id: int
    name: str


class AreaDatDydMaterialRef(NamedTuple):
    id: int
    unk_float: float
    name: str


class AreaDatDydChannel(object):
    """
    A dynamic-detail channel's display parameters, keyed by `id` (matches
    types.hms.Heightmap.dyd_channels' own channelId: 1-31 billboard,
    32-63 mesh). Variant-typed:
      type 0 -- 15 raw uint32 params. THE one that matters for placement
                -- see DydPlacementParams for the fully decoded meaning,
                confirmed against dat_VertexData.js's own
                buildDydPlacements() algorithm.
      type 2 -- a single int32 param. Confirmed UNUSED by the actual
                placement/render code (grep shows no consumer at all) --
                parsed for completeness, not because anything needs it.
      type 3 -- a mesh reference: a name, 7 floats (meaning undetermined),
                and a list of AreaDatDydMaterialRef entries. Also
                confirmed UNUSED by placement, and never actually seen in
                real data in this area (no type-3 records appeared) --
                ported field-for-field from readAreaDat.mjs regardless,
                including its own genuinely-unknown 35-byte per-material
                tail (its own comment shows the raw hex with a guessed-
                but-unconfirmed breakdown) -- ported as "skip these
                bytes", not interpreted.
    """
    __slots__ = ("id", "type", "values", "value", "name", "floats", "materials")

    def __init__(self, id, type, values=None, value=None, name=None, floats=None, materials=None):
        # type: (int, int, Optional[List[int]], Optional[int], Optional[str], Optional[List[float]], Optional[List[AreaDatDydMaterialRef]]) -> None
        self.id = id
        self.type = type
        self.values = values
        self.value = value
        self.name = name
        self.floats = floats
        self.materials = materials


class DydPlacementParams(NamedTuple):
    """
    A type-0 AreaDatDydChannel's 15 raw values, decoded -- confirmed
    against dat_VertexData.js's own buildDydPlacements()/
    dydMeshInstanceMatrix() placement algorithm (already in our
    reference bundle; just not previously read for this). Per-index
    meaning, values[0..14]:
      0  atlas_align   -- billboard: which atlas sub-image to pick
                           (0=fixed, 1/2=random+flip); mesh: whether to
                           align the instance to the terrain normal
      1  density        -- scales how many placement candidates are
                           tried per terrain quad
      2  noise_frequency -- /25, feeds 3-octave Perlin noise sampled at
                           world position, driving scale/tint variation
      3  hue             -- billboard tint base (only meaningful for
      4  hue_variation      billboards; SWTOR formula is /100, then
      5  saturation         combined with noise -- see below)
      6  saturation_variation
      7  brightness
      8  brightness_variation
      9  (reserved, unused)
      10 base_scale_raw  -- base instance scale; mesh and billboard use
                           different formulas to derive an actual scale
                           from this (see base_scale())
      11 scale_variation -- /100
      12 scale_strength  -- /100, how much low paint density shrinks an
                           instance
      13 wind            -- not used in placement; presumably a render-
                           time sway parameter, left as raw units
      14 rotation_mode   -- mesh only: 1 = full random XYZ rotation,
                           3 = normal-aligned + random yaw, else =
                           normal-aligned only (no randomness)

    Deliberately NOT chasing bit-exact reproduction of SWTOR's own
    placement (its own seeded-PRNG per-quad-candidate scatter) -- per
    Crunch, "close enough... a field of grass, not stacked" is the bar,
    which a genuinely procedural Geometry Nodes distribution meets more
    naturally than trying to replicate the exact algorithm would, and
    directly enables the density-adjustability Crunch wants as an
    end-user control besides.
    """
    atlas_align: int
    density: int
    noise_frequency: float
    hue: float
    hue_variation: float
    saturation: float
    saturation_variation: float
    brightness: float
    brightness_variation: float
    base_scale_raw: int
    scale_variation: float
    scale_strength: float
    wind: int
    rotation_mode: int

    @classmethod
    def from_channel(cls, channel):
        # type: (AreaDatDydChannel) -> Optional["DydPlacementParams"]
        """Returns None for anything other than a type-0 channel (or one
        with fewer than 15 values -- shouldn't happen given the fixed-
        size parse, but checked rather than assumed)."""
        if channel.type != 0 or not channel.values or len(channel.values) < 15:
            return None
        v = channel.values
        return cls(
            atlas_align=v[0], density=v[1], noise_frequency=v[2] / 25.0,
            hue=v[3] / 100.0, hue_variation=v[4] / 100.0,
            saturation=v[5] / 100.0, saturation_variation=v[6] / 100.0,
            brightness=v[7] / 100.0, brightness_variation=v[8] / 100.0,
            base_scale_raw=v[10], scale_variation=v[11] / 100.0, scale_strength=v[12] / 100.0,
            wind=v[13], rotation_mode=v[14],
        )

    def base_scale(self, is_mesh):
        # type: (bool) -> float
        """SWTOR derives an actual base scale from base_scale_raw
        differently for mesh vs. billboard channels -- ported formulas,
        confirmed against dat_VertexData.js."""
        if is_mesh:
            return self.base_scale_raw / 25.64 + 0.1
        return (self.base_scale_raw + 32) / 2400.0


class AreaDatSectionOffsets(NamedTuple):
    rooms: int
    assets: int
    paths: int
    schemes: int
    terrain_textures: int  # not yet parsed further -- see module docstring
    dydtex: int
    dydchannel: int
    settings: int
    guid: int


class AreaDat(object):
    __slots__ = ("offsets", "rooms", "assets", "terrain_textures", "dyd_textures", "dyd_channels")

    def __init__(self, offsets, rooms, assets, terrain_textures, dyd_textures, dyd_channels):
        # type: (AreaDatSectionOffsets, List[str], List[AreaDatAsset], List[AreaDatTerrainTexture], List[AreaDatDydTexture], List[AreaDatDydChannel]) -> None
        self.offsets = offsets
        self.rooms = rooms
        self.assets = assets
        self.terrain_textures = terrain_textures
        self.dyd_textures = dyd_textures
        self.dyd_channels = dyd_channels

    def dyd_channel(self, channel_id, channel_type=None):
        # type: (int, Optional[int]) -> Optional[AreaDatDydChannel]
        """Resolves a types.hms.Heightmap.dyd_channels channelId (1-31
        billboard, 32-63 mesh) to its display parameters.

        A channelId can have SEVERAL records of different types (e.g. a
        type 2 colour record and a type 0 placement record under the same
        id, in either order -- seen on Korriban, Onderon and Dantooine
        areas). With `channel_type` given, only a record of that type
        matches; without it the first record with the id is returned."""
        for channel in self.dyd_channels:
            if channel.id == channel_id and (channel_type is None or channel.type == channel_type):
                return channel
        return None

    def dyd_placement_params(self, channel_id):
        # type: (int) -> Optional[DydPlacementParams]
        """Resolves a channelId straight to its decoded type-0 params, or
        None if this id has no type-0 record."""
        channel = self.dyd_channel(channel_id, 0)
        return DydPlacementParams.from_channel(channel) if channel is not None else None

    def dyd_asset_name(self, channel_id):
        # type: (int) -> Optional[str]
        """Resolves a channelId to its real asset name (a bare .mat-style
        name for a billboard sprite, or a .gr2 mesh path)."""
        for tex in self.dyd_textures:
            if tex.id == channel_id:
                return tex.name or None
        return None

    @staticmethod
    def is_dyd_mesh_channel(channel_id):
        # type: (int) -> bool
        """channelId 32-63 = mesh, 1-31 = billboard, per
        dat_heightmap-spec.js's DydChannel struct comment (channel 0 is
        documented as "base" -- treated as billboard-like here, matching
        every real channel-0 sample seen, which resolved to a billboard
        sprite name, not a mesh path)."""
        return channel_id >= 32

    def find_asset_id(self, name):
        # type: (str) -> int
        for asset in self.assets:
            if asset.name == name:
                return asset.id
        raise KeyError("No asset named %r in this area's asset table" % name)

    @property
    def heightmap_asset_id(self):
        # type: () -> int
        """
        The shared placeholder asset id every `.hms` instance's `assetId`
        property resolves to (confirmed: every `.hms` instance across
        every room in the Onderon arena sample shares this one id --
        matches downloadHeightmaps.js's own `assets.find(name ===
        '\\engine\\heightmap.hms')` lookup rather than a hardcoded
        constant, so this works for any area, not just the one tested).
        """
        return self.find_asset_id(HMS_PLACEHOLDER_ASSET_NAME)

    def terrain_texture_name(self, tex_id):
        # type: (int) -> Optional[str]
        """
        Resolves a types.hms.Heightmap.layer_texture_indices entry to a
        real terrain texture (bare .mat) name. Confirmed against real
        data to be an id-keyed lookup, NOT an array-position index --
        dat_VertexData.js's own resolver builds `terrainIdToName[t.id]`,
        and cross-checking against a real area.dat + real heightmap tiles
        confirmed it: every texIndex value seen in the Onderon arena's
        heightmap samples (1-16) falls exactly within this area's
        terrain-texture id range, nothing suggesting a position-based
        scheme instead. -1 (an unused splat layer slot) and any id not
        in this area's table both resolve to None.
        """
        for tex in self.terrain_textures:
            if tex.id == tex_id:
                return tex.name
        return None


def read_area_dat(dv):
    # type: (DataView) -> AreaDat
    assert_magic(dv, "AREA_DAT_BINARY_FORMAT_")

    offsets = AreaDatSectionOffsets(
        rooms=dv.getUint32(0x1c, True),
        assets=dv.getUint32(0x20, True),
        paths=dv.getUint32(0x24, True),
        schemes=dv.getUint32(0x28, True),
        terrain_textures=dv.getUint32(0x2c, True),
        dydtex=dv.getUint32(0x30, True),
        dydchannel=dv.getUint32(0x34, True),
        settings=dv.getUint32(0x38, True),
        guid=dv.getUint32(0x3c, True),
    )

    # ------ ROOMS ------
    pos = offsets.rooms
    num_rooms = dv.getUint32(pos, True)
    pos += 4
    rooms = []
    for _ in range(num_rooms):
        name, pos = read_length_prefixed_string(dv, pos)
        rooms.append(name)

    # ------ ASSETS ------
    pos = offsets.assets
    num_assets = dv.getUint32(pos, True)
    pos += 4
    assets = []
    for _ in range(num_assets):
        asset_id = read_uint64_id(dv, pos)
        pos += 8
        name, pos = read_length_prefixed_string(dv, pos)
        assets.append(AreaDatAsset(asset_id, name))

    if pos != offsets.paths:
        raise ValueError(
            "Did not fully read assets section; expected to end at 0x%x but was at 0x%x"
            % (offsets.paths, pos)
        )

    # ------ TERRAIN TEXTURES ------
    # id, layer (usually 0), name -- keyed by `id`, NOT array position (see
    # dat_VertexData.js: `terrainIdToName[t.id] = t.name`). Parsed here
    # (Stage 2) purely to resolve types.hms.Heightmap.layer_texture_indices
    # to real names -- see AreaDat.terrain_texture_name_by_id().
    pos = offsets.terrain_textures
    num_terrain_textures = dv.getUint32(pos, True)
    pos += 4
    terrain_textures = []
    for _ in range(num_terrain_textures):
        tex_id = dv.getUint32(pos, True)
        pos += 4
        layer = dv.getUint32(pos, True)
        pos += 4
        name, pos = read_length_prefixed_string(dv, pos)
        terrain_textures.append(AreaDatTerrainTexture(tex_id, layer, name))

    if pos != offsets.dydtex:
        raise ValueError(
            "Did not fully read terrain texture section; expected to end at 0x%x but was at 0x%x"
            % (offsets.dydtex, pos)
        )

    # ------ DYNAMIC DETAIL TEXTURES ------
    pos = offsets.dydtex
    num_dyd_textures = dv.getUint32(pos, True)
    pos += 4
    dyd_textures = []
    for _ in range(num_dyd_textures):
        tex_id = dv.getUint32(pos, True)
        pos += 4
        name, pos = read_length_prefixed_string(dv, pos)
        dyd_textures.append(AreaDatDydTexture(tex_id, name))

    if pos != offsets.dydchannel:
        raise ValueError(
            "Did not fully read dyd texture section; expected to end at 0x%x but was at 0x%x"
            % (offsets.dydchannel, pos)
        )

    # ------ DYNAMIC DETAIL CHANNELS ------
    # Variant-typed records -- see AreaDatDydChannel's own docstring for
    # what's confirmed vs. ported-but-unverified about each type.
    pos = offsets.dydchannel
    num_dyd_channel_params = dv.getUint32(pos, True)
    pos += 4
    dyd_channels = []
    for _ in range(num_dyd_channel_params):
        channel_type = dv.getUint32(pos, True)
        pos += 4
        channel_id = dv.getUint32(pos, True)
        pos += 4

        if channel_type == 0:
            values = []
            for _j in range(15):
                values.append(dv.getUint32(pos, True))
                pos += 4
            dyd_channels.append(AreaDatDydChannel(channel_id, channel_type, values=values))

        elif channel_type == 2:
            value = dv.getInt32(pos, True)
            pos += 4
            dyd_channels.append(AreaDatDydChannel(channel_id, channel_type, value=value))

        elif channel_type == 3:
            name, pos = read_length_prefixed_string(dv, pos)

            floats = []
            for _j in range(7):
                floats.append(dv.getFloat32(pos, True))
                pos += 4

            num_materials = dv.getUint32(pos, True)
            pos += 4
            materials = []
            for _j in range(num_materials):
                mat_id = dv.getUint32(pos, True)
                pos += 4
                unk_float = dv.getFloat32(pos, True)
                pos += 4
                mat_name, pos = read_length_prefixed_string(dv, pos)
                pos += 0x23  # unknown tail, per material entry -- see AreaDatDydChannel's docstring
                materials.append(AreaDatDydMaterialRef(mat_id, unk_float, mat_name))

            dyd_channels.append(
                AreaDatDydChannel(channel_id, channel_type, name=name, floats=floats, materials=materials)
            )

        else:
            # Matches readAreaDat.mjs's own behavior: log and keep going
            # with just id/type recorded, rather than raise -- an unknown
            # variant here shouldn't take down the whole area parse over
            # what might just be a channel type this area happens to use
            # that the reference source hadn't seen yet either.
            print("area_dat: unknown DYD channel param type %d for channel %d" % (channel_type, channel_id))
            dyd_channels.append(AreaDatDydChannel(channel_id, channel_type))

    if pos != offsets.settings:
        raise ValueError(
            "Did not fully read dyd channel param section; expected to end at 0x%x but was at 0x%x"
            % (offsets.settings, pos)
        )

    return AreaDat(offsets, rooms, assets, terrain_textures, dyd_textures, dyd_channels)
