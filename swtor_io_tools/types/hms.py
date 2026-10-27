# <pep8 compliant>

"""
`.hms` heightmap VertexData parser -- ported from Jedipedia's
readHeightmap.mjs (itself a port of their viewer's js/viewer/load/
heightmap.js) and cross-checked field-for-field against
dat_VertexData-read.js and dat_heightmap-spec.js. Validated against real
decompressed blobs pulled from the Onderon PvP arena's default.dat via
room_dat.py -- width/depth/vertex-count/triangle-count/local bounds all
matched the ground truth exactly (see test_hms.py).

No bpy dependency -- plain Python, testable standalone. Only the DENSE
version-2 layout is exercised against real data so far (every live-area
sample seen has been version 2, matching readHeightmap.mjs's own note
that this is the only version live areas currently ship); the SPARSE
(version & 8) branch is implemented directly from dat_heightmap-spec.js's
documented layout and its own test fixtures, but hasn't been run against
a real sparse sample -- flagged inline below.

Deliberately LOCAL-space, unlike readHeightmap.mjs's buildTerrainMesh
(which bakes the instance's world matrix into every position because its
own pipeline has no Blender-style Object.matrix_world to lean on). Here,
positions/indices are left in the heightmap's own local grid space and
ops/import_hms.py sets the object's world matrix instead, matching how
every other importer in this addon keeps mesh data local
(compare types/gr2.py -- also local-space, transform applied by the
Object). Smooth vertex normals are likewise NOT computed here (unlike
readHeightmap.mjs's accumulateNormal) -- Blender derives its own normals
from the mesh geometry once the object exists, so porting that step would
be redundant work thrown away at import time.

Also parses the Dynamic Detail (DYD) paint block -- Heightmap.dyd_channels,
a list of (channelId, density-grid) pairs describing grass/rocks/small
foliage scattered on this specific tile (channelId 1-31 = billboard,
32-63 = mesh, per dat_heightmap-spec.js). Confirmed present in real data:
55 real channel instances across the Onderon arena's 13 real tiles,
every one resolving cleanly against area.dat's own dyd_textures/
dyd_channels tables (see types/area_dat.py) to a real named asset. Only
implemented for the dense (non-sparse) layout, matching everything else
in this file -- the spec places it directly after the color map, gated
on hasColorMap being set, which every real sample seen so far has been.
"""

import math
import struct
from typing import Callable, List, Optional

from .dat_common import decompress_property_blob


GRID_SPACING = 0.2  # version-2 grid: 0.2-unit lattice, matches the viewer exactly

# True: tiling UVs are world-unit XZ positions (like the world viewer's
# terrain shader); False: the legacy tile-viewer formula (~2.5x denser).
# Layer UvScaling is applied on top of either by hms_materials.py.
WORLD_SPACE_TERRAIN_UVS = True


class Heightmap(object):
    """Pure parsed data -- everything parse_heightmap() produces, local-space."""

    __slots__ = (
        "width", "depth", "version", "has_holes",
        "positions",         # List[Tuple[float, float, float]], length width*depth
        "uvs",                # List[Tuple[float, float]], length width*depth -- see _uv_at()
        "indices",           # List[int], flat triangle list (holes already removed)
        "min_y", "max_y",
        "local_min", "local_max",
        "layer_names",        # List[Optional[str]]
        "layer_texture_indices",  # List[int], raw texture-table indices (stage 2 input)
        "vertex_weights",     # List[float], len(layer_names) per vertex, normalized
        "layer_alpha",        # List[int], raw splat grid, (2w-1)*(2d-1)*numLayers, u8
        "layer_map_width", "layer_map_depth",
        "vertex_color_map",   # List[Tuple[int, int, int]], length width*depth, 0-255 RGB
        "dyd_channels",        # List[Tuple[int, List[int]]] -- (channelId, density grid 0-255 per vertex, k*width+j order)
    )

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def decompress_heightmap_blob(raw):
    # type: (bytes) -> bytes
    """Thin alias over dat_common's generic property decompressor, named to
    match readHeightmap.mjs's decompressHeightmapBlob for anyone cross-
    referencing the two side by side."""
    return decompress_property_blob(raw)


def _read_uint8(buf, pos):
    return buf[pos] if pos < len(buf) else 0


def _read_int8(buf, pos):
    if pos >= len(buf):
        return -1
    v = buf[pos]
    return v if v < 128 else v - 256


def _check_no_hole(hole_map, depth, k, j):
    # type: (bytes, int, int, int) -> bool
    index = j * depth + k
    byte_index = index >> 3
    bit = 7 - (index & 0x7)
    return (hole_map[byte_index] & (1 << bit)) != 0


def parse_heightmap(buf, terrain_texture_name=None):
    # type: (bytes, Optional[Callable[[int], Optional[str]]]) -> Heightmap
    """
    Parses a DECOMPRESSED heightmap VertexData payload (run
    decompress_heightmap_blob() on the raw room_dat.RoomInstance.
    compressed_vertex_data bytes first).

    `terrain_texture_name`: optional callback resolving a splat-layer
    texture-table index to a name (stage 2's area_dat.py terrain-texture
    table). Left unresolved (None per layer) if omitted -- geometry
    parsing doesn't need it, only stage 2's material building does.
    """
    if terrain_texture_name is None:
        terrain_texture_name = lambda index: None  # noqa: E731

    pos = 0
    version = buf[pos]
    pos += 1
    sparse = (version & 8) != 0

    width = int.from_bytes(buf[pos:pos + 4], "little")
    pos += 4
    depth = int.from_bytes(buf[pos:pos + 4], "little")
    pos += 4

    if sparse:
        # Three unknown floats, version-9 only. Not seen in any real sample
        # yet -- ported directly from dat_heightmap-spec.js's documented
        # layout, untested against live data. Flag loudly if this is ever
        # hit so it gets a real test sample instead of silent trust.
        pos += 12

    has_holes = buf[pos] == 1
    pos += 1
    hole_map = None
    if has_holes:
        hole_bytes = (width * depth + 7) >> 3
        hole_map = buf[pos:pos + hole_bytes]
        pos += hole_bytes

    # --- positions (local space) ---
    x_min = -GRID_SPACING * math.ceil(0.5 * (width - 1))
    x_max = x_min + GRID_SPACING * (width - 1)
    z_min = -GRID_SPACING * math.ceil(0.5 * (depth - 1))
    z_max = z_min + GRID_SPACING * (depth - 1)

    xs = [0.0] * (width * depth)
    ys = [0.0] * (width * depth)
    zs = [0.0] * (width * depth)
    for k in range(depth):
        for j in range(width):
            i = k * width + j
            xs[i] = x_min + GRID_SPACING * j
            zs[i] = z_min + GRID_SPACING * k

    min_y = float("inf")
    max_y = float("-inf")

    def set_y(idx, y):
        ys[idx] = y
        nonlocal min_y, max_y
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y

    def idx_from_pos(x, y):
        return x + y * width

    if sparse:
        # SPARSE (version 8/9) height layout, per dat_heightmap-spec.js:
        # every sample is a full f32, row-major, no split border/interior
        # encoding. Untested against real data (see module docstring).
        for k in range(depth):
            for j in range(width):
                y = struct.unpack_from("<f", buf, pos)[0]
                pos += 4
                set_y(idx_from_pos(j, k), y)
    else:
        # Border rows/columns: full f32. Order matches readHeightmap.mjs
        # exactly -- top/bottom row first (all j), then left/right column
        # (j=1..depth-2), THEN interior.
        for j in range(width):
            y = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
            set_y(idx_from_pos(j, 0), y)
            y = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
            set_y(idx_from_pos(j, depth - 1), y)
        for j in range(1, depth - 1):
            y = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
            set_y(idx_from_pos(0, j), y)
            y = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
            set_y(idx_from_pos(width - 1, j), y)
        # Interior: u16 fixed-point, /512.
        for j in range(1, width - 1):
            for k in range(1, depth - 1):
                raw_u16 = buf[pos] | (buf[pos + 1] << 8)
                pos += 2
                set_y(idx_from_pos(j, k), raw_u16 / 512.0)

    if not math.isfinite(min_y):
        min_y = 0.0
        max_y = 0.0

    positions = [(xs[i], ys[i], zs[i]) for i in range(width * depth)]

    # Per-vertex texture UVs, tiled across the tile -- ported verbatim
    # from dat_VertexData.js's own comment "matching heightmap.js": NOT
    # a simple 0-1 UV. Deliberately built here, in the pure-data parser,
    # rather than left for the Blender-side object-building code to
    # improvise -- this is real format knowledge (the tiling density the
    # engine itself renders at), not a Blender concern.
    uvs = [(0.0, 0.0)] * (width * depth)
    if WORLD_SPACE_TERRAIN_UVS:
        # What the world viewer's TerrainAntiTile shader does (confirmed in
        # its source: texCoord = worldPosition.xz, then x layer UvScaling):
        # UV = position in world units, so a layer with UvScaling 0.5
        # repeats every 2 units. The legacy formula below runs at ~2.5 UV
        # per unit (j/2 per 0.2-unit lattice step) BEFORE UvScaling, i.e.
        # ~2.5x denser tiling than the engine-matching world-space rule.
        for i in range(width * depth):
            uvs[i] = (xs[i], zs[i])
    else:
        for k in range(depth):
            for j in range(width):
                i = k * width + j
                uvs[i] = (
                    (j / (width - 1)) * (width / 2) if width > 1 else 0.0,
                    (k / (depth - 1)) * (depth / 2) if depth > 1 else 0.0,
                )

    # --- dense layer weights (splat) + color map ---
    num_textures = _read_uint8(buf, pos)
    pos += 1
    layer_names = []
    layer_texture_indices = []
    for _ in range(num_textures):
        tex_index = _read_int8(buf, pos)
        pos += 1
        layer_texture_indices.append(tex_index)
        layer_names.append(terrain_texture_name(tex_index))

    layer_map_width = width * 2 - 1
    layer_map_depth = depth * 2 - 1
    layer_map_offset = pos
    layer_alpha_len = layer_map_width * layer_map_depth * num_textures
    layer_alpha = list(buf[layer_map_offset:layer_map_offset + layer_alpha_len])
    # Pad in case the buffer ran short (mirrors readUint8Safe's zero-fill).
    if len(layer_alpha) < layer_alpha_len:
        layer_alpha.extend([0] * (layer_alpha_len - len(layer_alpha)))
    pos = layer_map_offset + layer_alpha_len

    color_map = [(255, 255, 255)] * (width * depth)
    color_map_flag = _read_uint8(buf, pos)
    pos += 1
    dyd_channels = []  # List[Tuple[int, List[int]]] -- (channelId, density grid 0-255, k*width+j order; file is x-major, reordered on parse)
    if color_map_flag != 0 and pos + width * depth * 3 <= len(buf):
        cp = pos
        for x in range(width):
            for z in range(depth):
                target = z * width + x
                color_map[target] = (buf[cp], buf[cp + 1], buf[cp + 2])
                cp += 3
        pos = cp

        # --- Dynamic Detail (DYD) paint block -- billboards/meshes (grass,
        # rocks, small foliage) painted directly onto this tile. Confirmed
        # present in real data: all but one of 13 real Onderon arena tiles
        # carry at least one channel (up to 9 seen). Per the dense-layout
        # spec, this only exists directly after a real color map -- never
        # reached if color_map_flag was 0. channelId 1-31 = billboard,
        # 32-63 = mesh; resolving either to a real asset needs area.dat's
        # own dydtex/dydchannel tables (see types/area_dat.py).
        if pos < len(buf):
            dyd_count = buf[pos]
            pos += 1
            grid_size = width * depth
            for _ in range(dyd_count):
                if pos + 1 + grid_size > len(buf):
                    break  # truncated/malformed tail -- stop rather than read garbage
                channel_id = buf[pos]
                pos += 1
                # The file stores each channel's density grid X-MAJOR
                # (index = x * depth + z), exactly like the color map
                # above -- NOT row-major as this was first assumed.
                # CONFIRMED on a real tile (65x44, 8 channels) by
                # correlating paint against the terrain splat layers
                # under every row/column order and flip: x-major won by
                # a wide margin on every heavily painted channel
                # (alignment 0.3-0.7 vs 0.04-0.13 for the old reading).
                # Reordered here into this module's usual k*width+j
                # vertex order, so every consumer (mesh attribute bake,
                # reference-style quad lookups) can index it like
                # positions/uvs/vertex_weights.
                raw_density = buf[pos:pos + grid_size]
                density = [raw_density[j * depth + k] for k in range(depth) for j in range(width)]
                pos += grid_size
                dyd_channels.append((channel_id, density))

    # --- reduce splat to per-vertex normalized weights (ensureBaseTerrainCoverage) ---
    stride = max(1, num_textures)
    vertex_weights = [0.0] * (width * depth * stride)
    for k in range(depth):
        for j in range(width):
            vi = k * width + j
            sx = min(layer_map_width - 1, j * 2)
            sz = min(layer_map_depth - 1, k * 2)
            src = (sz * layer_map_width + sx) * num_textures
            total = 0
            samples = []
            for i in range(num_textures):
                a = layer_alpha[src + i]
                samples.append(a)
                total += a
            if num_textures > 0 and total < 255:
                samples[0] += 255 - total
                total = 255
            if total > 0:
                for i in range(num_textures):
                    vertex_weights[vi * stride + i] = samples[i] / total
            elif stride > 0:
                vertex_weights[vi * stride] = 1.0

    # --- triangulation, honoring holes ---
    indices = []
    for k in range(depth - 1):
        for j in range(width - 1):
            if not has_holes or (
                _check_no_hole(hole_map, depth, k, j + 1) and _check_no_hole(hole_map, depth, k + 1, j)
            ):
                if not has_holes or _check_no_hole(hole_map, depth, k, j):
                    indices.append(k * width + j)
                    indices.append((k + 1) * width + j)
                    indices.append(k * width + j + 1)
                if not has_holes or _check_no_hole(hole_map, depth, k + 1, j + 1):
                    indices.append(k * width + j + 1)
                    indices.append((k + 1) * width + j)
                    indices.append((k + 1) * width + j + 1)

    return Heightmap(
        width=width, depth=depth, version=version, has_holes=has_holes,
        positions=positions, uvs=uvs, indices=indices,
        min_y=min_y, max_y=max_y,
        local_min=(x_min, min_y, z_min), local_max=(x_max, max_y, z_max),
        layer_names=layer_names, layer_texture_indices=layer_texture_indices,
        vertex_weights=vertex_weights,
        layer_alpha=layer_alpha, layer_map_width=layer_map_width, layer_map_depth=layer_map_depth,
        vertex_color_map=color_map, dyd_channels=dyd_channels,
    )
