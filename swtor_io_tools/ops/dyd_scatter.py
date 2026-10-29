# <pep8 compliant>

"""
Stage 3 -- dynamic detail (DYD) terrain scatter: grass, small foliage,
and rocks painted onto a heightmap tile, built as Geometry Nodes
instancing driven by the per-channel density data types/hms.py parses
(Heightmap.dyd_channels) and the placement parameters types/area_dat.py
resolves (AreaDat.dyd_placement_params).

NOT going for bit-exact reproduction of SWTOR's own seeded-PRNG placement
-- per Crunch, "close enough... a field of grass, not 240 objects stacked
in a 1m square" is the bar. A genuinely procedural Geometry Nodes
distribution meets that more naturally than replicating the exact
algorithm would, and directly gives the density-adjustability Crunch
wants as an end-user control besides.

============================================================================
ARCHITECTURE: ONE reusable "SWTOR HMS - DYD Scatter" node group, added as
a separate Geometry Nodes modifier per (tile, channel) pair, each with
different input values -- NOT one group per channel the way
hms_materials.py's per-layer shader groups had to be. Shader node groups
can't take an Object as an input socket (that's exactly what forced the
per-layer-group workaround there); Geometry Nodes groups CAN, so one
hand-built, shared group covers every billboard AND every mesh channel
on every tile, just fed a different Object + float inputs each time --
much closer to a normal reusable function than the shader case allowed.

Billboard channels get a small "card" object -- crossed double-plane (two
perpendicular quads), the standard low-cost trick for foliage that
doesn't need to track the camera -- rather than true camera-facing
billboarding, which Geometry Nodes has no clean native support for and
which "close enough" doesn't call for anyway.

Mesh channels reference a real .gr2 object for that asset -- imported on
demand via this addon's existing GR2 import path (ops/import_gr2.py's
own _import_gr2()/_MeshCache, reused directly rather than reimplemented;
see _ensure_dyd_mesh_object()) if it isn't already in the scene, same
dedup-by-name-first philosophy as the billboard cards above.

============================================================================
NOT YET VERIFIED IN REAL BLENDER -- more so than usual. This is the
FIRST geometry-nodes code written in this whole effort, and unlike the
shader-node work (which eventually got real node dumps to correct
against), nothing here has been checked against a real Geometry Nodes
tree at all. Specifically unconfirmed:
  - Node type names and socket names/order for Distribute Points on
    Faces, Instance on Points, Rotate Instances, Scale Instances, Random
    Value, Named Attribute, Combine XYZ, Align Euler to Vector.
  - The node-group interface API for Object-type sockets
    (socket_type="NodeSocketObject") -- used the same
    interface.new_socket() pattern hms_materials.py's shader groups use,
    unconfirmed whether Geometry Node trees expose it identically.
  - How a Geometry Nodes MODIFIER's inputs are actually set from Python
    (modifier[socket.identifier] = value, looked up from the group's own
    interface items rather than assumed "Input_N" naming -- safer, but
    still unconfirmed against a real modifier).
  - THE SHAKIEST PIECE: feeding the "Density" input from a per-vertex
    attribute rather than a flat value. The "<identifier>_attribute_name"
    key is CONFIRMED correct (verified via Copy Data Path on a real
    modifier: modifiers["DYD 0"]["Socket_1_attribute_name"]) -- but that
    alone evidently doesn't switch the socket out of flat-value mode
    (every tile scattered nothing, Density sitting at its default 0
    despite the name being set). add_dyd_scatter() now also sets
    "<identifier>_use_attribute" = True, NAMED by analogy to the
    confirmed sibling key, not itself independently verified. If density
    still reads as 0, run list(modifiers["DYD 0"].keys()) in Blender's
    Python console after manually toggling the attribute switch on, and
    use whatever key actually shows up.
  - Whether DYD billboard sprite names resolve as bare .mat names the
    same way terrain textures do (assumed, by naming-convention
    similarity, not confirmed against a real file).
  - The guessed object-naming convention for an already-imported GR2 mesh
    (asset_name's basename, extension stripped) -- partially confirmed
    against ops/import_gr2.py's own bpy.data.objects.new(file_name, ...)
    call, which does derive names the same way, but not traced end to
    end through that importer's own path resolution.
  - _ensure_dyd_mesh_object()'s whole approach, unverified end to end:
    reuses ops/import_gr2.py's own _import_gr2()/_MeshCache and
    ops/import_gr2.py's load() exactly as import_area.py itself calls
    them (same operator-stand-in shape, same bl_idname/
    enforce_neutral_settings/area_merge_multi_mesh_objects attributes,
    confirmed by reading that real call site rather than guessed) -- but
    this is the first time that path has been driven from OUTSIDE
    import_area.py's own operator context, and area_merge_multi_mesh_
    objects=True's "always exactly one object" assumption in particular
    hasn't been checked against a real multi-part mesh asset.
Treat this as a first draft to correct against a real run, same as every
other new node-building module here has needed.

GROUP AS A TEMPLATE: the scatter group can be saved into the bundled
HeroEngine_Shaders.blend as "TEMPLATE - DYD Scatter" and edited there; the code
appends it when present and valid and otherwise builds the identical group
itself (see DYD_USE_GROUP_TEMPLATE / DYD_REQUIRED_GROUP_INPUTS). The code
builder doubles as the spec: export a template from it, don't hand-rebuild.

ATLAS CARDS (per-instance sprite variation): the scatter group now stores
swtor_dyd_atlas_frame (uniform random 0..frames-1) and swtor_dyd_atlas_flip
(50% on every billboard channel) on the INSTANCE domain, mirroring the
reference viewer's packed-random draw, and each card material's "Grass
Atlas UV" group gets Columns/Rows/Image Width/Image Height from the
channel's atlas mode and the real image size. The template's Attribute
nodes read GEOMETRY attributes, which cannot see instance-domain data:
either set them to INSTANCER (DYD_ATLAS_ATTRIBUTE_MODE = "INSTANCE"), or
use "REALIZE" to bake the attributes onto real mesh points instead.

RESOLVED against a real run (paint grid orientation + viewport density):
  - The DYD density grid in the .hms file is X-MAJOR (index = x * depth +
    z), like the color map, not row-major. Found because scatter showed on
    the brick/paver terrain layers; a correlation test of paint vs splat
    layers under every order/flip picked x-major decisively. types/hms.py
    now reorders it on parse.
  - Added a "Viewport Density" input (default DYD_VIEWPORT_DENSITY): an Is
    Viewport switch scales the density multiplier in the viewport only;
    renders always use full density.

RESOLVED against a real run (orientation / density / scale round):
  - CONFIRMED via a real modifier dump: "<identifier>_use_attribute" is
    the real key (Socket_1_use_attribute exists, set True, attribute name
    set, density attributes hold real painted data) -- the "SHAKIEST
    PIECE" note below is settled. Density was never a wiring problem; it
    was UNITS: Distribute Points on Faces' density is points per m^2, and
    we fed it attribute(0-1) x 1.0, i.e. <= ~1 point/m^2, so a tile with
    mean paint 0.05 over ~110 m^2 got a handful of points. The reference
    (dat_VertexData.js buildDydPlacements) tries 1 + trunc(density/divisor)
    candidates per 0.2 m quad (divisor 12 mesh, 1.5 billboard), each
    accepted with probability paint/255 -> points/m^2 =
    candidates / 0.2^2 x paint. add_dyd_scatter() now computes the
    candidates/0.2^2 part from the type-0 density value and passes it as
    "Points Per m2"; "Density Multiplier" stays as the end-user knob.
  - ORIENTATION: tile meshes are built Y-up in LOCAL space (heightmap
    positions used as-is; the object carries the +90 X rotation from
    area.to_blender_space), and Geometry Nodes runs in that local space.
    Cards and GR2 meshes are Blender Z-up, so they lay on their side and
    "yaw about Z" was really a roll. The group now rotates the instance
    geometry -90 X (Z-up -> local Y-up) via a Transform Geometry node,
    and yaw / normal-alignment use local Y.
  - SCALE: now the reference formula: base - (1 - paint) * strength *
    base + variation * noise, with noise weights 1.5 (mesh) / 1/15
    (billboard) and a minimum clamp (0.01 mesh / 0.0001 billboard).
    Variation and the minimum are divided by the GR2 scale-compensation
    factor along with the base (the old code only divided the base).
  - WORLD SCALE (Area Assembler merge): add_dyd_scatter() takes a
    `world_scale` -- the scale the tile and its surroundings were built at
    (1.0 in the standalone harness, Area Assembler's scale_factor when the
    tile mesh is baked at that scale). Points Per m2 is divided by its
    square, Noise Frequency by it, and the scale terms multiplied by it
    (mesh channels: by it over the GR2 import's own baked scale, which is 1
    when both come from the same preference). It also takes an optional
    `max_viewport_instances` cap, which lowers a tile's Viewport Density to
    fit rather than skipping the tile.
  - Billboard cards are now 1.8 units tall x (1.8 x image aspect) wide,
    bottom at the origin -- the reference quad's size -- with aspect from
    the diffuse image (per atlas sub-image for atlas modes).
  - Rotation for mesh channels follows the reference's rotation_mode:
    1 = full random XYZ (bypasses normal alignment), 3 = optional align
    then random yaw, anything else = optional align only.
  - Scale / yaw / XYZ Random Value nodes now use distinct seeds
    (Seed + offset) instead of all sharing one.
  - The node group and billboard cards carry a version stamp; an older
    group is rebuilt in place and older cards are re-meshed in place on
    the next run. Delete previously imported tiles first -- their
    modifiers point at the old socket layout.

RESOLVED against a real run, from the first look at actual scatter output:
  - "DYD 0 has Align To Normal / Random Rotation off, DYD 1+ have them
    on, on every tile" -- not a channel-0 quirk: rotation randomization
    and normal-alignment are mesh-only in the reference algorithm
    (dat_VertexData.js's buildDydPlacements), and values[0]/values[14]
    mean something else entirely for billboards (atlas sub-image picker /
    unused). Reading them as rotation flags for billboard channels was
    reading unrelated data. Billboards now never align to normal and
    always randomize yaw; mesh channels keep the values-driven logic.
  - GR2 mesh scatter ~10x too big -- the addon's "Scale Object"
    preference (addon_prefs.py: gr2_scale_object / gr2_scale_factor)
    scales imported meshes "at the Mesh level" (baked into vertex data),
    which Object Info with transform_space='ORIGINAL' reads as-is, and
    our Base Scale (from the game's raw values, which assume a neutral
    mesh) then multiplied on top. Mesh channels' Base Scale is now
    divided by the live preference value (see
    _gr2_scale_compensation_factor); billboard cards are hand-built at a
    fixed size and unaffected.
  - Density stuck at 0: see the "SHAKIEST PIECE" note above -- fix
    applied, awaiting a real run to confirm.

RESOLVED against a real run, from the mesh-import pass specifically:
  - Every mesh-channel import crashing ("expected str, bytes or
    os.PathLike object, not tuple"). resolve_resource_path_multi()
    returns a (resolved_path, root_used) 2-tuple, same convention as
    resolve_mat_path() elsewhere in this addon -- _ensure_dyd_mesh_
    object() was using the whole tuple as if it were the path itself.
    Fixed by unpacking it properly.
  - That crash was taking down an entire TILE's scatter, not just the
    one bad channel -- add_dyd_scatter()'s per-channel loop body ran
    unguarded, so one exception anywhere in it propagated straight out,
    discarding channels that had already succeeded earlier in the same
    loop. Wrapped each channel's body in its own try/except, matching
    this module's existing warnings-over-hard-blocks convention for
    every other per-channel skip.
  - Grass cards rendering with no texture in-engine despite a correctly
    built material -- get_or_build_billboard_card() named its UV layer
    "hms_dyd_uv"; the Grass template's own UV Map node reads Blender's
    default UV layer name, not a custom one, so it was finding no UV
    data at all. Fixed by naming it "UVMap" instead, matching what
    mesh.uv_layers.new() itself defaults to.

RESOLVED against a real run:
  - The tile mesh vanishing entirely once a scatter modifier was enabled
    -- caused by this module's own bug, not anything shared: the node
    group's final output was only ever the scattered Instances, never
    joined back with the original terrain Geometry input. A Geometry
    Nodes modifier REPLACES an object's evaluated geometry with whatever
    the tree outputs, so the modifier was discarding the terrain mesh
    outright. Fixed with a Join Geometry node combining both before the
    Group Output.
  - "material not usable (error: 'rotationMap')" on every billboard --
    NOT a bug in this module. Root cause was in ops/material_builder.py's
    get_or_create_material(), shared code well beyond DYD: Uber's config
    marks rotationMap required=True unconditionally, and the maps loop
    used `required` alone to skip straight to an unguarded dds_paths[
    json_key] lookup -- a real KeyError for any Uber material (not just
    billboards) that genuinely has no RotationMap1 at all, which real
    flat foliage/billboard .mat files apparently often don't. Fixed
    there directly, matching this pipeline's own established soft-fail
    convention, rather than worked around here.
  - Mesh channels ("not found as an already-imported object") and the
    Align Euler to Vector deprecation warning are NOT bugs introduced by
    this round -- the former is exactly the documented limitation this
    module's docstring already named (it doesn't import GR2 meshes
    itself); the latter is a genuine open question -- unclear what
    Blender's replacement/preferred node is, worth a real answer before
    touching it rather than a guess, and not expected to be why anything
    was actually broken (it's a warning, not an error).
  - "Not seeing any scatter objects at all", even for billboard channels
    whose materials now build fine -- GeometryNodeInstanceOnPoints'
    "Instance" input is a Geometry socket, not an Object socket.
    Confirmed by a real error ("Conversion is not Supported:
    Object > Geometry") -- the group's Object-type "Instance" input was
    wired straight into it with no conversion. Fixed with a
    GeometryNodeObjectInfo node as the actual Object-to-Geometry bridge,
    transform_space='ORIGINAL' so the instance object's own (identity)
    transform doesn't get baked in on top of Instance on Points' own
    per-point Scale/Rotation.
  - STILL "not seeing any scatter objects", even after the fix above,
    even with add_dyd_scatter() completing cleanly and printing a
    success line per channel -- get_or_build_billboard_card() created its
    Object via bpy.data.objects.new() but never linked it to any
    collection. An unlinked object isn't part of the view layer's
    depsgraph evaluation at all, so Object Info had nothing real to
    reference even though every modifier and every input was wired
    correctly. Fixed by linking every billboard card into a dedicated
    "HMS DYD Assets" collection (see _get_or_build_dyd_assets_collection)
    -- hidden from viewport/render so it doesn't clutter the scene, but
    actually present so instancing can see it. This also replaces the
    card objects' use_fake_user=True from the previous round -- a linked
    object already has a real user (the collection), so that workaround
    is no longer needed for these specifically.
============================================================================
"""

import types as _pytypes
from typing import Optional

import bpy

from ..types import area_dat, hms
from ..types.mat import resolve_mat_path
from ..types.shader_templates import _template_blend_path
from .hms_materials import protect_datablock
from ..types.shared import resolve_resource_path_multi
from .import_gr2 import _import_gr2, _MeshCache
from .process_materials import _build_named_material, apply_materials_by_name_after_import, get_resources_dirs


DYD_SCATTER_GROUP_NAME = "SWTOR HMS - DYD Scatter"
DYD_DENSITY_ATTR_PREFIX = "hms_dyd_density"
DYD_ASSETS_COLLECTION_NAME = "HMS DYD Assets"

# The scatter node group can live as a hand-editable TEMPLATE in the bundled
# HeroEngine_Shaders.blend, like the shader templates. When the group isn't
# already in the file, get_or_build_dyd_scatter_group() appends
# DYD_SCATTER_TEMPLATE_NAME from there (renamed to DYD_SCATTER_GROUP_NAME);
# if it's missing, out of date or doesn't expose the inputs listed in
# DYD_REQUIRED_GROUP_INPUTS, the built-in code builder below is used
# instead, with a printed reason. The template carries its layout version as
# the custom property "hms_dyd_group_version" -- bump it when you change the
# group's inputs, and add any new input to DYD_REQUIRED_GROUP_INPUTS. NOTE:
# values baked into the template's nodes (noise detail / roughness, the tint
# gamma exponent, ...) are then edited in the template, not through this
# module's constants; only the per-modifier inputs the code sets still apply.
DYD_SCATTER_TEMPLATE_NAME = "TEMPLATE - DYD Scatter"
DYD_USE_GROUP_TEMPLATE = True
DYD_REQUIRED_GROUP_INPUTS = (
    "Density",
    "Viewport Density",
    "Points Per m2",
    "Density Multiplier",
    "Instance",
    "Tint Strength",
    "Noise Gain",
    "Base Scale",
    "Scale Variation",
    "Scale Strength",
    "Min Scale",
    "Noise Frequency",
    "Noise Origin",
    "Hue",
    "Hue Variation",
    "Saturation",
    "Saturation Variation",
    "Brightness",
    "Brightness Variation",
    "Write Tint",
    "Align To Normal",
    "Random Yaw",
    "Random Full Rotation",
    "Seed",
    "Atlas Last Frame",
    "Random Flip",
    "Realize Instances",
)

# Bump when the node group's layout changes / when the card geometry
# changes; an existing datablock with an older stamp is rebuilt in place.
DYD_GROUP_VERSION = 6
DYD_CARD_VERSION = 4

# How per-instance Grass attributes (atlas frame / flip, later tint) reach
# the shader. The Grass template's Attribute nodes read them with type
# GEOMETRY, which only sees attributes on a MESH; scattered cards are
# INSTANCES, whose attributes live on the instance domain.
#   "INSTANCE": store on the instance domain (cheap, keeps instancing).
#               REQUIRES the template's Attribute nodes (inside "HeroEngine
#               SWTOR - Grass Atlas UV", and the tint node in "HeroEngine
#               SWTOR - Grass Shader") to be set to Attribute Type
#               INSTANCER.
#   "REALIZE":  also realize the billboard instances, so the attributes
#               land on real mesh points that the template's existing
#               GEOMETRY-type nodes can read. No template change, but much
#               heavier (every card becomes real geometry).
DYD_ATLAS_ATTRIBUTE_MODE = "INSTANCE"

# Sprite-sheet layout safety net. The channel data's first value picks a
# layout (1 = 2x2, 2 = two columns -- dat_VertexData.js), but some sheets
# don't match it: ond_env_flora_jungle_flower_02 is flagged 2x2 yet is a
# two-column sheet whose plants cross the horizontal midline, so 2x2 slices
# every plant in half. With DYD_AUTO_FIX_ATLAS_LAYOUT the diffuse's alpha is
# checked along each cell boundary and any boundary that cuts through more
# than DYD_ATLAS_CUT_FRACTION of the image's width/height in opaque pixels
# is dropped (the finest grid that doesn't slice a sprite is used). Every
# override is printed. Set False to follow the data exactly, or pin a
# specific asset in DYD_ATLAS_LAYOUT_OVERRIDES as {asset name: (cols, rows)}.
DYD_AUTO_FIX_ATLAS_LAYOUT = True
DYD_ATLAS_CUT_FRACTION = 0.015
DYD_ATLAS_LAYOUT_OVERRIDES = {}

# Coherent noise + tint (reference: dat_VertexData.js dydThreeOctaveNoise /
# dydHslToRgb). The reference's raw noise is
#     perlin(p) + 0.5 * perlin(2p) + 0.25 * perlin(4p),   p = frequency * (x, z)
# i.e. three octaves summed with weights 1 / 0.5 / 0.25. Measured by porting
# the reference's (quirky) Perlin: over a 120x120 patch at frequency 1.2 it
# has std ~0.20, 1st/99th percentile about -0.45 / +0.44 and extremes of
# +-0.81. Blender's Noise Texture (3D, detail 2, roughness 0.5, lacunarity 2,
# normalized) is the stand-in; its Fac has std ~0.09 (estimated from an
# improved-Perlin port, assuming Cycles' noise behaves alike), so
# (Fac - 0.5) * 2.2 matches both the reference's spread and its tails. It is
# not bit-identical to the reference Perlin, just similar in character.
# Tune the gain if variation reads too weak / strong.
DYD_NOISE_GAIN = 2.2

# Default for each modifier's "Tint Strength" input: a multiplier on ONLY the
# noise-driven part of the tint (base hue / saturation / brightness are
# unaffected). 1.0 = as the data specifies, 0 = base tint only, > 1 exaggerates.
# "Noise Gain" (above) is likewise a per-modifier input and scales the noise
# for both the scale variation and the tint.
DYD_TINT_STRENGTH = 1.0

# The Grass shader multiplies the stored tint into the Gamma'd (linear)
# diffuse WITHOUT a gamma of its own, while the reference multiplies in
# display space; since (d * t) ** g == d ** g * t ** g, storing
# tint ** DYD_TINT_GAMMA reproduces the display-space multiply (the Grass
# template's Gamma node uses 2.1). Set 1.0 to store the raw tint instead.
DYD_TINT_GAMMA = 2.1

# Reference billboard quad: spans +-0.9 * scale vertically and
# +-0.9 * scale * aspect horizontally, bottom resting on the surface.
DYD_CARD_HEIGHT = 1.8

# Default for each modifier's "Viewport Density" input: the fraction of the
# render density shown while working in the viewport (1.0 = full). Render
# (F12 / render animation) always uses the full density. Adjustable per
# modifier afterwards.
DYD_VIEWPORT_DENSITY = 0.25

# One mesh cache for the whole addon session, matching Area Assembler's own
# per-import-run instance in spirit -- _MeshCache dedupes by resolved path,
# so a pebble/plant mesh reused across dozens of tiles only ever actually
# imports once. Module-level rather than per-call since DYD scatter calls
# happen one tile at a time, not as a single batch with a natural place to
# thread a cache instance through.
_dyd_mesh_cache = _MeshCache()

# asset_name -> the Object that mesh channel scatters (one per asset). Looked up
# BEFORE any by-name search so a prop Area Assembler imported under the same
# name (e.g. a pebble that is also placed as an ordinary room element) can never
# be mistaken for the scatter source -- and so a name that Blender had to
# suffix (".001") because of such a clash still resolves to the right object.
_dyd_mesh_objects = {}


def reset_batch_state():
    """Forget every per-batch cache. Called at the start of each Area
    Assembler batch (and safe for any other caller to use): both caches hold
    references into bpy.data, and a long Blender session can easily delete or
    replace that data between runs, leaving them pointing at nothing."""
    global _dyd_mesh_cache
    _dyd_mesh_cache.release()
    _dyd_mesh_cache = _MeshCache()
    _dyd_mesh_objects.clear()


def _get_or_build_dyd_assets_collection():
    # type: () -> bpy.types.Collection
    """
    A dedicated collection for instance-source objects (billboard cards;
    could hold reparented mesh-channel objects too eventually) -- linked
    into the scene so Geometry Nodes' Object Info node can actually
    reference and evaluate them, but hidden from viewport/render so they
    don't clutter the scene as if they were real placed objects
    themselves. Confirmed necessary the hard way: an Object created via
    bpy.data.objects.new() but never linked to any collection isn't part
    of the view layer's depsgraph evaluation at all, which is exactly why
    every scattered instance was invisible despite add_dyd_scatter()
    completing without error -- the modifiers were correctly built and
    wired, but Object Info had nothing real to reference.
    """
    collection = bpy.data.collections.get(DYD_ASSETS_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(DYD_ASSETS_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(collection)
        collection.hide_render = True
        collection.hide_viewport = True
    return collection


# ---------------------------------------------------------------------------
# Billboard card
# ---------------------------------------------------------------------------

def _billboard_atlas_layout(atlas_mode):
    # type: (int) -> tuple
    """(columns, rows) of the sprite sheet for values[0]: 0 = whole sheet,
    1 = 2x2 quadrants, 2 = left/right halves (dat_VertexData.js)."""
    columns = 2 if atlas_mode in (1, 2) else 1
    rows = 2 if atlas_mode == 1 else 1
    return columns, rows


def _boundary_cuts(alpha, columns, rows):
    # type: (object, int, int) -> tuple
    """Opaque pixel counts along the vertical / horizontal cell boundary
    lines of a columns x rows grid (max over the lines and the pixel
    either side of each). `alpha` is a height x width array."""
    height, width = alpha.shape
    opaque = alpha >= 0.5
    cut_x = 0
    for k in range(1, columns):
        x = (width * k) // columns
        cut_x = max(cut_x, int(opaque[:, x].sum()), int(opaque[:, max(0, x - 1)].sum()))
    cut_y = 0
    for k in range(1, rows):
        y = (height * k) // rows
        cut_y = max(cut_y, int(opaque[y, :].sum()), int(opaque[max(0, y - 1), :].sum()))
    return cut_x, cut_y


def _effective_atlas_layout(name, atlas_mode, material):
    # type: (str, int, bpy.types.Material) -> tuple
    """(columns, rows) actually used for a card: the data's layout, unless
    pinned in DYD_ATLAS_LAYOUT_OVERRIDES, or (DYD_AUTO_FIX_ATLAS_LAYOUT)
    reduced so no cell boundary slices through a sprite."""
    columns, rows = _billboard_atlas_layout(atlas_mode)
    if name in DYD_ATLAS_LAYOUT_OVERRIDES:
        pinned = DYD_ATLAS_LAYOUT_OVERRIDES[name]
        print("dyd card %r: layout pinned to %dx%d by DYD_ATLAS_LAYOUT_OVERRIDES" % (name, pinned[0], pinned[1]))
        return pinned
    if not DYD_AUTO_FIX_ATLAS_LAYOUT or (columns, rows) == (1, 1):
        return columns, rows
    image = _diffuse_image(material)
    if image is None or image.size[0] <= 0 or image.size[1] <= 0:
        return columns, rows
    try:
        import numpy as np
        width, height = image.size
        buffer = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        alpha = buffer.reshape(height, width, 4)[..., 3]
        cut_x, cut_y = _boundary_cuts(alpha, columns, rows)
    except Exception as exc:  # noqa: BLE001 -- never block the import on a heuristic
        print("dyd card %r: could not check atlas layout against the image (%s), using data layout" % (name, exc))
        return columns, rows

    new_columns, new_rows = columns, rows
    if columns > 1 and cut_x > DYD_ATLAS_CUT_FRACTION * height:
        new_columns = 1
    if rows > 1 and cut_y > DYD_ATLAS_CUT_FRACTION * width:
        new_rows = 1
    if (new_columns, new_rows) != (columns, rows):
        details = []
        if columns > 1:
            details.append(
                "column split %s (%d opaque px on the vertical midline)"
                % ("dropped" if new_columns != columns else "kept", cut_x)
            )
        if rows > 1:
            details.append(
                "row split %s (%d opaque px on the horizontal midline)"
                % ("dropped" if new_rows != rows else "kept", cut_y)
            )
        print(
            "dyd card %r: layout auto-corrected %dx%d -> %dx%d: %s. "
            "Set DYD_AUTO_FIX_ATLAS_LAYOUT = False to follow the data."
            % (name, columns, rows, new_columns, new_rows, ", ".join(details))
        )
    return new_columns, new_rows


def _diffuse_image(material):
    # type: (bpy.types.Material) -> Optional[bpy.types.Image]
    """The material's diffuse image: the node named "diffuseMap", else the
    first image texture node that has an image."""
    tree = material.node_tree if material is not None else None
    if tree is None:
        return None
    image = getattr(tree.nodes.get("diffuseMap"), "image", None)
    if image is None:
        for candidate in tree.nodes:
            if candidate.type == "TEX_IMAGE" and candidate.image is not None:
                return candidate.image
    return image


def _card_aspect_from_material(material, columns, rows):
    # type: (bpy.types.Material, int, int) -> float
    """Width / height of ONE sprite frame, from the material's diffuse
    image. Falls back to 1.0 (square) with a printed reason."""
    image = _diffuse_image(material)
    if image is None:
        print("dyd card: no diffuse image found on %r, using square aspect" % material.name)
        return 1.0
    width, height = image.size
    if width <= 0 or height <= 0:
        print("dyd card: image %r reports size %dx%d, using square aspect" % (image.name, width, height))
        return 1.0
    return (width / float(columns)) / (height / float(rows))


def _configure_atlas_group(material, columns, rows):
    # type: (bpy.types.Material, int, int) -> bool
    """
    Sets Columns / Rows / Image Width / Image Height on the card
    material's "HeroEngine SWTOR - Grass Atlas UV" group node, so the
    template's sprite-sheet maths (a port of the reference viewer's atlas
    rect: column = frame % columns, row = frame // columns, same insets and
    flip) addresses the right frame sizes. The frame and flip themselves
    come per instance from the scatter group's swtor_dyd_atlas_frame /
    swtor_dyd_atlas_flip attributes. Returns False (with a printed reason)
    if the group isn't there.
    """
    tree = material.node_tree if material is not None else None
    atlas_node = None
    if tree is not None:
        atlas_node = next(
            (n for n in tree.nodes if n.type == "GROUP" and n.node_tree and "Grass Atlas UV" in n.node_tree.name),
            None,
        )
    if atlas_node is None:
        print("dyd card: no Grass Atlas UV group on material %r, atlas not configured" % material.name)
        return False
    image = _diffuse_image(material)
    width, height = (image.size if image is not None else (0, 0))
    values = {"Columns": columns, "Rows": rows}
    if width > 0 and height > 0:
        values["Image Width"] = int(width)
        values["Image Height"] = int(height)
    for socket_name, value in values.items():
        if socket_name in atlas_node.inputs:
            atlas_node.inputs[socket_name].default_value = value
        else:
            print("dyd card: Atlas UV group has no %r input on %r" % (socket_name, material.name))
    return True


def _fill_card_mesh(mesh, aspect):
    # type: (bpy.types.Mesh, float) -> None
    """Two perpendicular quads, Z-up, bottom at z=0, DYD_CARD_HEIGHT tall
    and DYD_CARD_HEIGHT * aspect wide (the reference quad size at scale
    1). The scatter group rotates instances into the tile's Y-up local
    space, and applies the per-point scale."""
    height = DYD_CARD_HEIGHT
    half = height * aspect * 0.5
    verts = [
        (-half, 0.0, 0.0), (half, 0.0, 0.0), (half, 0.0, height), (-half, 0.0, height),
        (0.0, -half, 0.0), (0.0, half, 0.0), (0.0, half, height), (0.0, -half, height),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for polygon in mesh.polygons:
        polygon.use_smooth = True

    # Layer must be named "UVMap": the Grass template's UV Map node reads
    # Blender's default layer name (confirmed on a real run).
    uv_layer = mesh.uv_layers.new(name="UVMap")
    # Whole-sheet 0-1 UVs per quad; atlas sub-frame selection belongs to
    # the Grass Atlas UV group, driven by swtor_dyd_atlas_frame (not yet
    # written by the scatter group).
    quad_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for loop in mesh.loops:
        uv_layer.data[loop.index].uv = quad_uvs[loop.index % 4]


def get_or_build_billboard_card(name, atlas_mode=0):
    # type: (str, int) -> Optional[bpy.types.Object]
    """
    A crossed-double-plane card object for one billboard sprite name,
    sharing a real material resolved the same way terrain layer textures
    are. Deduplicated by name. A card built by an older version of this
    module (no/old version stamp) is re-meshed in place, keeping its
    object, collection link and material.

    Returns None if the material can't be resolved/built at all -- caller
    should skip this channel rather than scatter an untextured card.
    """
    obj_name = "hms_dyd_card_%s" % name
    existing = bpy.data.objects.get(obj_name)
    if existing is not None and existing.data.get("hms_dyd_card_version") == DYD_CARD_VERSION:
        return existing

    material = None
    if existing is not None and existing.data.materials:
        material = existing.data.materials[0]
    if material is None:
        material, proc_result = _build_named_material(name)
        if material is None:
            print(
                "dyd billboard %r: material not usable (%s%s)"
                % (name, proc_result.status, ": " + proc_result.detail if proc_result.detail else "")
            )
            return None
        protect_datablock(material)

    columns, rows = _effective_atlas_layout(name, atlas_mode, material)
    aspect = _card_aspect_from_material(material, columns, rows)
    print("dyd card %r: atlas %dx%d, aspect %.3f" % (name, columns, rows, aspect))
    _configure_atlas_group(material, columns, rows)

    if existing is not None:
        mesh = existing.data
        mesh.clear_geometry()
    else:
        mesh = bpy.data.meshes.new(obj_name)
    _fill_card_mesh(mesh, aspect)
    mesh["hms_dyd_card_version"] = DYD_CARD_VERSION
    mesh["hms_dyd_atlas_columns"] = columns
    mesh["hms_dyd_atlas_rows"] = rows
    if not mesh.materials:
        mesh.materials.append(material)

    if existing is not None:
        return existing
    obj = bpy.data.objects.new(obj_name, mesh)
    _get_or_build_dyd_assets_collection().objects.link(obj)
    return obj


def _gr2_scale_compensation_factor():
    # type: () -> float
    """
    The scale ops/import_gr2.py's "Scale Object" preference bakes into
    imported mesh data (addon_prefs.py: gr2_scale_object gates whether
    it's applied at all, gr2_scale_factor is the multiplier, default 10)
    -- 1.0 when that preference is off, so this is a no-op for anyone not
    using it. Read live from preferences rather than hardcoded so it
    stays correct if the preference is changed. gr2_scale_factor's own
    min is 1.0, so dividing by this can't hit zero.

    Lives here, not on DydPlacementParams.base_scale(), because that's
    deliberately a pure-data method with no bpy dependency (same split
    as every other types/ module in this addon).
    """
    prefs = bpy.context.preferences.addons["swtor_io_tools"].preferences
    return prefs.gr2_scale_factor if prefs.gr2_scale_object else 1.0


def _make_gr2_import_operator_stand_in():
    # type: () -> object
    """
    A lightweight duck-typed stand-in for the bpy.types.Operator
    ops/import_gr2.py's load()/_import_gr2() normally expect -- same
    pattern ops/import_area.py already uses to call this same import path
    programmatically (not through the File > Import menu), reused rather
    than invented fresh.

    The two attributes that matter: bl_idname set to anything other than
    'IMPORT_MESH_OT_gr2' (confirmed in import_gr2.py's own load() --
    that exact string is what selects "invoked from the File > Import UI
    itself" behavior; anything else takes the "called from other code"
    branch, which pulls scale/axis-conversion from addon preferences
    instead) and enforce_neutral_settings=False (so that branch is
    actually reached at all -- checked unconditionally before the
    bl_idname comparison). area_merge_multi_mesh_objects=True matters
    too: a .gr2 that imports as several sub-objects would break this
    module's single-object-per-asset-name lookup, so this forces a
    single merged object per import, matching what add_dyd_scatter's
    bpy.data.objects.get(single name) already assumes.
    """
    operator = _pytypes.SimpleNamespace()
    operator.bl_idname = "swtor.dyd_scatter_standalone_gr2_import"
    operator.enforce_neutral_settings = False
    operator.area_merge_multi_mesh_objects = True
    return operator


def _ensure_dyd_mesh_object(asset_name):
    # type: (str) -> Optional[bpy.types.Object]
    """
    Resolves a mesh-channel DYD asset (a real .gr2 path, e.g.
    "\\art\\static\\area\\...\\ond_env_pebble_01.gr2") to a real imported
    Blender object -- importing it via this addon's existing GR2
    importer if it isn't already in the scene, rather than only ever
    checking for one that happens to already exist (the previous
    behavior, which silently skipped every mesh channel since nothing
    ever imported them). Deduplicated two ways: by the expected object
    name (an already-imported object short-circuits immediately, no
    importer call at all) and, on an actual import, by _dyd_mesh_cache's
    own resolved-path dedup (shared with Area Assembler's own mesh
    import path) -- a pebble/plant mesh reused across many tiles only
    ever really imports once either way.

    Freshly-imported objects get relinked into the HMS DYD Assets
    collection (unlinked from wherever ops/import_gr2.py's own
    bpy.context.collection.objects.link() put them) and get real
    materials built via apply_materials_by_name_after_import() --
    neither of which the plain import path does on its own when called
    this way (both are normally only triggered by the File > Import
    operator's own invoke-time behavior).

    Returns None (with a printed reason) if the asset can't be resolved
    to an on-disk file or the import produces nothing usable.
    """
    registered = _dyd_mesh_objects.get(asset_name)
    if registered is not None:
        try:
            if bpy.data.objects.get(registered.name) is registered:
                return registered
        except ReferenceError:
            pass
        _dyd_mesh_objects.pop(asset_name, None)

    expected_name = asset_name.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
    # Only the shared assets collection is searched -- NOT every object in the
    # file. An ordinary prop Area Assembler placed under the same name is a
    # different object (different transform, parents, collections), and
    # scattering it would silently move or duplicate it.
    assets_collection = _get_or_build_dyd_assets_collection()
    existing = assets_collection.objects.get(expected_name)
    if existing is not None:
        _dyd_mesh_objects[asset_name] = existing
        return existing

    primary_dir, legacy_dir = get_resources_dirs()
    # BUG FIX: resolve_resource_path_multi() returns (resolved_path,
    # root_used) -- a 2-tuple, same convention as resolve_mat_path()
    # elsewhere in this addon -- not a bare path. Confirmed the hard way:
    # the unpacked-as-a-whole tuple reached open() three frames later
    # ("expected str, bytes or os.PathLike object, not tuple").
    resolved_path, _root_used = resolve_resource_path_multi(primary_dir, legacy_dir, asset_name)
    if resolved_path is None:
        print("dyd mesh asset %r: could not resolve to an on-disk file" % asset_name)
        return None

    operator = _make_gr2_import_operator_stand_in()
    objects = _import_gr2(operator, bpy.context, primary_dir, resolved_path, _dyd_mesh_cache)
    if not objects:
        print("dyd mesh asset %r: import produced no objects" % asset_name)
        return None

    for obj in objects:
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        assets_collection.objects.link(obj)

    apply_materials_by_name_after_import([obj.name for obj in objects])

    # area_merge_multi_mesh_objects=True should guarantee exactly one,
    # but prefer one of THIS import's own objects whose name matches what
    # the rest of this module expects, else the first -- never a global
    # by-name lookup (see the scoped lookup above for why).
    chosen = next((obj for obj in objects if obj.name == expected_name), objects[0])
    _dyd_mesh_objects[asset_name] = chosen
    return chosen



# ---------------------------------------------------------------------------
# The reusable scatter node group
# ---------------------------------------------------------------------------

def _active_socket(sockets, name, sock_type=None):
    """Picks the live socket called `name` (optionally of `sock_type`,
    e.g. 'VALUE' / 'VECTOR') from a node's inputs/outputs. Needed because
    nodes like Random Value and Switch can carry several same-named
    sockets of different types, only one of which is active."""
    candidates = [s for s in sockets if s.name == name]
    for s in candidates:
        if s.enabled and (sock_type is None or s.type == sock_type):
            return s
    for s in candidates:
        if sock_type is None or s.type == sock_type:
            return s
    raise KeyError("no %r socket (type %r)" % (name, sock_type))


def _append_scatter_template():
    # type: () -> Optional[bpy.types.NodeTree]
    """
    Appends DYD_SCATTER_TEMPLATE_NAME from the bundled HeroEngine_Shaders.blend
    and renames it to DYD_SCATTER_GROUP_NAME. Returns None (with a printed
    reason where there is one) if the template isn't there, is older than
    DYD_GROUP_VERSION, or lacks any input in DYD_REQUIRED_GROUP_INPUTS --
    the caller then falls back to the code builder.
    """
    try:
        template_path = str(_template_blend_path())
    except Exception as exc:  # noqa: BLE001
        print("dyd scatter template: bundled blend unavailable (%s)" % exc)
        return None
    with bpy.data.libraries.load(template_path, link=False) as (data_from, data_to):
        if DYD_SCATTER_TEMPLATE_NAME not in data_from.node_groups:
            return None   # not added to the bundled blend yet: nothing to report
        data_to.node_groups = [DYD_SCATTER_TEMPLATE_NAME]
    group = data_to.node_groups[0] if data_to.node_groups else None
    if group is None:
        return None

    input_names = {
        item.name for item in group.interface.items_tree if item.item_type == "SOCKET" and item.in_out == "INPUT"
    }
    missing = [name for name in DYD_REQUIRED_GROUP_INPUTS if name not in input_names]
    version = group.get("hms_dyd_group_version") or 0
    if missing or version < DYD_GROUP_VERSION:
        print(
            "dyd scatter template %r is out of date (version %r, need >= %d; missing inputs: %s) -- "
            "using the built-in group instead"
            % (DYD_SCATTER_TEMPLATE_NAME, version, DYD_GROUP_VERSION, ", ".join(missing) or "none")
        )
        group.use_fake_user = False
        bpy.data.node_groups.remove(group)
        return None

    group.name = DYD_SCATTER_GROUP_NAME
    protect_datablock(group)   # same orphan-purge protection as the built group
    print("dyd scatter group: loaded from template %r (version %d)" % (DYD_SCATTER_TEMPLATE_NAME, version))
    return group


def get_or_build_dyd_scatter_group():
    # type: () -> bpy.types.NodeTree
    """
    Builds (once; reused after) the shared Geometry Nodes group every
    (tile, channel) scatter modifier instances. Rebuilt in place if an
    older version is found. Works in the tile's LOCAL space, which is
    Y-up (see this module's docstring) -- instance geometry is rotated
    -90 X so Z-up cards/meshes stand up along local Y.
    """
    existing = bpy.data.node_groups.get(DYD_SCATTER_GROUP_NAME)
    if existing is not None:
        if (existing.get("hms_dyd_group_version") or 0) >= DYD_GROUP_VERSION:
            return existing
        print("dyd scatter group: older version found, rebuilding in place")
        existing.nodes.clear()
        existing.interface.clear()
        group = existing
    else:
        if DYD_USE_GROUP_TEMPLATE:
            try:
                from_template = _append_scatter_template()
            except Exception as exc:  # noqa: BLE001 -- never block the import on the template
                print("dyd scatter template could not be loaded (%s) -- using the built-in group" % exc)
                from_template = None
            if from_template is not None:
                return from_template
        group = bpy.data.node_groups.new(DYD_SCATTER_GROUP_NAME, "GeometryNodeTree")
    protect_datablock(group)
    group["hms_dyd_group_version"] = DYD_GROUP_VERSION
    # Groups made from Python don't get the "Modifier" usage flag, and the
    # Geometry Nodes modifier's dropdown only lists groups that have it --
    # harmless for the scatter modifiers (assigned directly) but it hides an
    # exported template from the UI.
    if hasattr(group, "is_modifier"):
        group.is_modifier = True
    iface = group.interface

    def add_float(name, default, min_value=None):
        sock = iface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
        sock.default_value = default
        if min_value is not None:
            sock.min_value = min_value
        return sock

    iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    iface.new_socket("Density", in_out="INPUT", socket_type="NodeSocketFloat")  # painted 0-1, fed by attribute
    iface.new_socket("Instance", in_out="INPUT", socket_type="NodeSocketObject")
    add_float("Density Multiplier", 1.0, 0.0)   # end-user knob
    viewport_density = add_float("Viewport Density", DYD_VIEWPORT_DENSITY, 0.0)  # viewport-only fraction of the above
    viewport_density.description = "Fraction of the density shown in the viewport. Final renders always use the full density."
    add_float("Points Per m2", 25.0, 0.0)       # candidates / 0.2^2, set per channel
    add_float("Base Scale", 1.0, 0.0)
    add_float("Scale Variation", 0.0, 0.0)      # already weighted (1.5 mesh / 1/15 billboard)
    add_float("Scale Strength", 0.0, 0.0)
    add_float("Min Scale", 0.0001, 0.0)
    add_float("Noise Frequency", 1.2, 0.0)       # channel noise frequency (values[2] / 25)
    noise_gain = add_float("Noise Gain", DYD_NOISE_GAIN, 0.0)
    noise_gain.description = "Strength of the coherent noise that drives scale variation and tint (default matches the reference's spread)."
    tint_strength = add_float("Tint Strength", DYD_TINT_STRENGTH, 0.0)
    tint_strength.description = "Scales only the noise-driven tint variation. 0 = base tint only, 1 = as authored, >1 exaggerates."
    iface.new_socket("Noise Origin", in_out="INPUT", socket_type="NodeSocketVector")  # tile world offset, see add_dyd_scatter
    add_float("Hue", 0.0)
    add_float("Hue Variation", 0.0)
    add_float("Saturation", 0.0)
    add_float("Saturation Variation", 0.0)
    add_float("Brightness", 1.0)
    add_float("Brightness Variation", 0.0)
    iface.new_socket("Write Tint", in_out="INPUT", socket_type="NodeSocketBool")
    iface.new_socket("Align To Normal", in_out="INPUT", socket_type="NodeSocketBool")
    iface.new_socket("Random Yaw", in_out="INPUT", socket_type="NodeSocketBool")
    iface.new_socket("Random Full Rotation", in_out="INPUT", socket_type="NodeSocketBool")
    iface.new_socket("Seed", in_out="INPUT", socket_type="NodeSocketInt")
    last_frame = iface.new_socket("Atlas Last Frame", in_out="INPUT", socket_type="NodeSocketInt")
    last_frame.default_value = 0
    last_frame.min_value = 0   # frame count - 1; frames are drawn uniformly from 0..this
    iface.new_socket("Random Flip", in_out="INPUT", socket_type="NodeSocketBool")
    iface.new_socket("Realize Instances", in_out="INPUT", socket_type="NodeSocketBool")
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    nodes = group.nodes
    links = group.links

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-1800, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (1900, 0)

    def gin(name):
        return group_in.outputs[name]

    def math_node(operation, location, a=None, b=None):
        node = nodes.new("ShaderNodeMath")
        node.operation = operation
        node.location = location
        for index, value in ((0, a), (1, b)):
            if value is None:
                continue
            if isinstance(value, (int, float)):
                node.inputs[index].default_value = float(value)
            else:
                links.new(value, node.inputs[index])
        return node.outputs[0]

    def random_float(lo, hi, seed_offset, location):
        """Random Value (float) seeded with group Seed + seed_offset, so
        each call decorrelates from the others."""
        seed = math_node("ADD", (location[0] - 300, location[1]), gin("Seed"), float(seed_offset))
        node = nodes.new("FunctionNodeRandomValue")
        node.data_type = "FLOAT"
        node.location = location
        _active_socket(node.inputs, "Min", "VALUE").default_value = lo
        _active_socket(node.inputs, "Max", "VALUE").default_value = hi
        links.new(seed, node.inputs["Seed"])
        return _active_socket(node.outputs, "Value", "VALUE")

    def random_int(lo, hi, seed_offset, location):
        """Random Value (integer, inclusive range) seeded with group Seed +
        seed_offset. `hi` is a number or a node output (e.g. a group input)."""
        seed = math_node("ADD", (location[0] - 300, location[1]), gin("Seed"), float(seed_offset))
        node = nodes.new("FunctionNodeRandomValue")
        node.data_type = "INT"
        node.location = location
        _active_socket(node.inputs, "Min", "INT").default_value = lo
        if isinstance(hi, (int, float)):
            _active_socket(node.inputs, "Max", "INT").default_value = int(hi)
        else:
            links.new(hi, _active_socket(node.inputs, "Max", "INT"))
        links.new(seed, node.inputs["Seed"])
        return _active_socket(node.outputs, "Value", "INT")

    def switch_node(input_type, switch_socket, false_value, true_socket, location):
        node = nodes.new("GeometryNodeSwitch")
        node.input_type = input_type
        node.location = location
        links.new(switch_socket, node.inputs["Switch"])
        sock_type = {"FLOAT": "VALUE", "VECTOR": "VECTOR", "GEOMETRY": "GEOMETRY", "INT": "INT"}[input_type]
        false_sock = _active_socket(node.inputs, "False", sock_type)
        true_sock = _active_socket(node.inputs, "True", sock_type)
        if isinstance(false_value, (int, float)):
            false_sock.default_value = float(false_value)
        else:
            links.new(false_value, false_sock)
        links.new(true_socket, true_sock)
        return _active_socket(node.outputs, "Output", sock_type)

    # --- density: paint (0-1) x multiplier x points-per-m2 ----------------
    paint = gin("Density")
    # Viewport override: in the viewport the multiplier is scaled by
    # "Viewport Density"; at render time (Is Viewport = False) it is not.
    is_viewport = nodes.new("GeometryNodeIsViewport")
    is_viewport.location = (-1900, 450)
    viewport_mult = math_node("MULTIPLY", (-1700, 450), gin("Density Multiplier"), gin("Viewport Density"))
    effective_mult = switch_node(
        "FLOAT", is_viewport.outputs["Is Viewport"], gin("Density Multiplier"), viewport_mult, (-1500, 450),
    )
    density_a = math_node("MULTIPLY", (-1300, 250), paint, effective_mult)
    density_field = math_node("MULTIPLY", (-1100, 250), density_a, gin("Points Per m2"))

    distribute = nodes.new("GeometryNodeDistributePointsOnFaces")
    distribute.distribute_method = "RANDOM"
    distribute.location = (-1000, 0)
    links.new(gin("Geometry"), distribute.inputs["Mesh"])
    links.new(density_field, distribute.inputs["Density"])
    links.new(gin("Seed"), distribute.inputs["Seed"])

    # --- scale: base - (1-paint)*strength*base + variation*noise ----------
    one_minus_paint = math_node("SUBTRACT", (-1500, -250), 1.0, paint)
    strength_term = math_node("MULTIPLY", (-1300, -250), one_minus_paint, gin("Scale Strength"))
    keep = math_node("SUBTRACT", (-1100, -250), 1.0, strength_term)
    shrunk_base = math_node("MULTIPLY", (-900, -250), keep, gin("Base Scale"))

    # Coherent 3-octave noise at frequency * world (x, z), shared by the scale
    # variation and the tint. `Noise Origin` carries the tile's world offset
    # so the noise is continuous across tile borders.
    position = nodes.new("GeometryNodeInputPosition")
    position.location = (-1900, -1700)
    position_split = nodes.new("ShaderNodeSeparateXYZ")
    position_split.location = (-1700, -1700)
    links.new(position.outputs["Position"], position_split.inputs["Vector"])
    origin_split = nodes.new("ShaderNodeSeparateXYZ")
    origin_split.location = (-1700, -1900)
    links.new(gin("Noise Origin"), origin_split.inputs["Vector"])
    world_x = math_node("ADD", (-1500, -1700), position_split.outputs["X"], origin_split.outputs["X"])
    world_z = math_node("ADD", (-1500, -1850), position_split.outputs["Z"], origin_split.outputs["Z"])
    noise_x = math_node("MULTIPLY", (-1300, -1700), world_x, gin("Noise Frequency"))
    noise_z = math_node("MULTIPLY", (-1300, -1850), world_z, gin("Noise Frequency"))
    noise_vector = nodes.new("ShaderNodeCombineXYZ")
    noise_vector.location = (-1100, -1750)
    noise_vector.inputs["Y"].default_value = 0.5   # mid-cell slice (the reference samples a fixed y plane)
    links.new(noise_x, noise_vector.inputs["X"])
    links.new(noise_z, noise_vector.inputs["Z"])
    noise_texture = nodes.new("ShaderNodeTexNoise")
    noise_texture.location = (-900, -1750)
    noise_texture.noise_dimensions = "3D"
    if hasattr(noise_texture, "noise_type"):
        noise_texture.noise_type = "FBM"
    if hasattr(noise_texture, "normalize"):
        noise_texture.normalize = True
    noise_texture.inputs["Scale"].default_value = 1.0       # frequency is applied to the vector already
    noise_texture.inputs["Detail"].default_value = 2.0      # three octaves
    noise_texture.inputs["Roughness"].default_value = 0.5   # octave weights 1 / 0.5 / 0.25
    noise_texture.inputs["Lacunarity"].default_value = 2.0  # octave frequencies 1 / 2 / 4
    noise_texture.inputs["Distortion"].default_value = 0.0
    links.new(noise_vector.outputs["Vector"], noise_texture.inputs["Vector"])
    centred = math_node("SUBTRACT", (-700, -1750), noise_texture.outputs["Fac"], 0.5)
    raw_noise = math_node("MULTIPLY", (-500, -1750), centred, gin("Noise Gain"))

    noise_scale = raw_noise
    variation = math_node("MULTIPLY", (-900, -450), noise_scale, gin("Scale Variation"))
    total_scale = math_node("ADD", (-700, -350), shrunk_base, variation)

    scale_clamp = nodes.new("ShaderNodeClamp")
    scale_clamp.location = (-500, -350)
    scale_clamp.inputs["Max"].default_value = 1000.0
    links.new(total_scale, scale_clamp.inputs["Value"])
    links.new(gin("Min Scale"), scale_clamp.inputs["Min"])

    # --- rotation, in the tile's Y-up local space -------------------------
    # Yaw is about local Y; full-random mode randomizes all three axes.
    yaw = random_float(0.0, 6.283185307, 2, (-1100, -700))
    roll_x = random_float(0.0, 6.283185307, 3, (-1100, -900))
    roll_z = random_float(0.0, 6.283185307, 4, (-1100, -1100))
    euler_x = switch_node("FLOAT", gin("Random Full Rotation"), 0.0, roll_x, (-800, -900))
    euler_y = switch_node("FLOAT", gin("Random Yaw"), 0.0, yaw, (-800, -700))
    euler_z = switch_node("FLOAT", gin("Random Full Rotation"), 0.0, roll_z, (-800, -1100))

    combine_rot = nodes.new("ShaderNodeCombineXYZ")
    combine_rot.location = (-550, -900)
    links.new(euler_x, combine_rot.inputs["X"])
    links.new(euler_y, combine_rot.inputs["Y"])
    links.new(euler_z, combine_rot.inputs["Z"])

    align_rotation = nodes.new("FunctionNodeAlignEulerToVector")
    align_rotation.axis = "Y"
    align_rotation.location = (-300, -900)
    links.new(distribute.outputs["Normal"], align_rotation.inputs["Vector"])
    links.new(combine_rot.outputs["Vector"], align_rotation.inputs["Rotation"])
    if "Factor" in align_rotation.inputs:
        align_rotation.inputs["Factor"].default_value = 1.0

    final_rotation = switch_node(
        "VECTOR", gin("Align To Normal"), combine_rot.outputs["Vector"],
        align_rotation.outputs["Rotation"], (0, -800),
    )

    # --- instance geometry: Object Info -> rotate Z-up asset to Y-up ------
    object_info = nodes.new("GeometryNodeObjectInfo")
    object_info.transform_space = "ORIGINAL"
    object_info.location = (-300, 250)
    links.new(gin("Instance"), object_info.inputs["Object"])

    # -90 degrees about X maps Blender Z-up asset geometry onto the tile's
    # local +Y (up) axis; the tile object's own +90 X then stands it up in
    # world space.
    to_local_up = nodes.new("GeometryNodeTransform")
    to_local_up.location = (-50, 250)
    to_local_up.inputs["Rotation"].default_value = (-1.5707963267948966, 0.0, 0.0)
    links.new(object_info.outputs["Geometry"], to_local_up.inputs["Geometry"])

    instance = nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (300, 0)
    links.new(distribute.outputs["Points"], instance.inputs["Points"])
    links.new(to_local_up.outputs["Geometry"], instance.inputs["Instance"])
    links.new(final_rotation, instance.inputs["Rotation"])
    links.new(scale_clamp.outputs["Result"], instance.inputs["Scale"])

    # --- per-instance Grass attributes (reference: dat_VertexData.js) ------
    # atlas frame: uniform random pick from 0..Atlas Last Frame (the
    # reference draws a random byte and takes it modulo the frame count --
    # equivalent; the template's Atlas UV group takes a modulo as well).
    # flip: 50% random on every billboard channel (even whole-sheet ones).
    frame_value = random_int(0, gin("Atlas Last Frame"), 5, (100, -1300))
    flip_bit = random_int(0, 1, 6, (100, -1500))
    flip_value = math_node("MULTIPLY", (400, -1500), flip_bit, gin("Random Flip"))

    def store_instance_attribute(name, value_socket, geometry_socket, location, data_type="INT"):
        node = nodes.new("GeometryNodeStoreNamedAttribute")
        node.data_type = data_type
        node.domain = "INSTANCE"
        node.location = location
        node.inputs["Name"].default_value = name
        links.new(geometry_socket, node.inputs["Geometry"])
        links.new(value_socket, _active_socket(node.inputs, "Value", {"INT": "INT", "FLOAT_COLOR": "RGBA"}[data_type]))
        return node.outputs["Geometry"]

    with_frame = store_instance_attribute(
        "swtor_dyd_atlas_frame", frame_value, instance.outputs["Instances"], (600, 0)
    )
    with_atlas = store_instance_attribute("swtor_dyd_atlas_flip", flip_value, with_frame, (850, 0))

    # Tint (billboards only): HSL = base + variation * noise, standard HSL ->
    # RGB (Blender's Combine Color in HSL mode is the textbook conversion the
    # reference uses), channels clamped, then ** DYD_TINT_GAMMA (see the
    # constant) and floored just above the Grass shader's "no tint" guard
    # (a tint shorter than 0.0001 reads as "missing" and becomes white).
    tint_noise = math_node("MULTIPLY", (-500, -2000), raw_noise, gin("Tint Strength"))

    def tint_term(base_name, variation_name, location, clamp):
        scaled = math_node("MULTIPLY", (location[0], location[1]), tint_noise, gin(variation_name))
        total = math_node("ADD", (location[0] + 200, location[1]), gin(base_name), scaled)
        if clamp:
            total.node.use_clamp = True
        return total

    hue_total = tint_term("Hue", "Hue Variation", (-300, -2100), False)
    hue_wrapped = math_node("FRACT", (-100, -2100), hue_total)   # reference wraps hue once; fract is equivalent here
    saturation = tint_term("Saturation", "Saturation Variation", (-300, -2250), True)
    lightness = tint_term("Brightness", "Brightness Variation", (-300, -2400), True)

    combine_hsl = nodes.new("FunctionNodeCombineColor")
    combine_hsl.mode = "HSL"
    combine_hsl.location = (100, -2250)
    links.new(hue_wrapped, combine_hsl.inputs[0])
    links.new(saturation, combine_hsl.inputs[1])
    links.new(lightness, combine_hsl.inputs[2])
    combine_hsl.inputs[3].default_value = 1.0
    split_rgb = nodes.new("FunctionNodeSeparateColor")
    split_rgb.mode = "RGB"
    split_rgb.location = (300, -2250)
    links.new(combine_hsl.outputs[0], split_rgb.inputs[0])
    tint_channels = []
    for channel in range(3):
        powered = math_node("POWER", (500, -2100 - 150 * channel), split_rgb.outputs[channel], DYD_TINT_GAMMA)
        floored = math_node("MAXIMUM", (700, -2100 - 150 * channel), powered, 0.0002)
        floored.node.use_clamp = True
        tint_channels.append(floored)
    combine_rgb = nodes.new("FunctionNodeCombineColor")
    combine_rgb.mode = "RGB"
    combine_rgb.location = (900, -2250)
    for channel, socket in enumerate(tint_channels):
        links.new(socket, combine_rgb.inputs[channel])
    combine_rgb.inputs[3].default_value = 1.0
    with_tint = store_instance_attribute(
        "swtor_dyd_tint", combine_rgb.outputs[0], with_atlas, (1100, 0), data_type="FLOAT_COLOR"
    )
    with_attributes = switch_node("GEOMETRY", gin("Write Tint"), with_atlas, with_tint, (1250, 100))

    # Optional: realize so the attributes land on real mesh points (see
    # DYD_ATLAS_ATTRIBUTE_MODE). Lazy -- unevaluated when the flag is off.
    realize = nodes.new("GeometryNodeRealizeInstances")
    realize.location = (1100, -200)
    links.new(with_attributes, realize.inputs["Geometry"])
    instances_out = switch_node(
        "GEOMETRY", gin("Realize Instances"), with_attributes, realize.outputs["Geometry"], (1350, 0)
    )

    # A Geometry Nodes modifier REPLACES the evaluated geometry, so join
    # the original terrain back in.
    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (1600, 0)
    links.new(gin("Geometry"), join.inputs["Geometry"])
    links.new(instances_out, join.inputs["Geometry"])

    links.new(join.outputs["Geometry"], group_out.inputs["Geometry"])

    return group


# ---------------------------------------------------------------------------
# Per-tile wiring
# ---------------------------------------------------------------------------

def _bake_density_attribute(mesh, density, channel_id):
    # type: (bpy.types.Mesh, list, int) -> str
    """Bakes a raw 0-255 density grid (Heightmap.dyd_channels' own
    per-vertex values, same vertex order as everything else built on this
    mesh) into a POINT-domain FLOAT attribute, 0-1 normalized. Returns the
    attribute's real name (Blender may rename on a collision, though one
    shouldn't occur -- channel_id is unique per tile)."""
    attr_name = "%s_%d" % (DYD_DENSITY_ATTR_PREFIX, channel_id)
    mesh.attributes.new(name=attr_name, type="FLOAT", domain="POINT")
    attr = mesh.attributes[attr_name]  # re-fetch fresh, see hms_materials.py's own note on why
    for vi, value in enumerate(density):
        attr.data[vi].value = value / 255.0
    return attr.name


def _candidate_cap(params, is_mesh):
    # type: (Any, bool) -> int
    """Candidates tried per 0.2 m quad at full paint (see add_dyd_scatter)."""
    divisor = 12.0 if is_mesh else 1.5
    return 1 + int(max(0, params.density) / divisor)


def estimate_scatter_instances(heightmap, area, viewport_density=None):
    # type: (hms.Heightmap, area_dat.AreaDat, Optional[float]) -> float
    """
    Expected number of instances this tile's scatter modifiers will evaluate
    in the VIEWPORT (the same per-channel figure add_dyd_scatter() prints as
    "~N pts", times the viewport fraction). Independent of the world scale:
    the density correction in add_dyd_scatter() exists precisely to keep the
    count the same however large the world is built. Channels that can't
    resolve an asset name or placement params are skipped here exactly as
    they are there.
    """
    if viewport_density is None:
        viewport_density = DYD_VIEWPORT_DENSITY
    total = 0.0
    for channel_id, density in heightmap.dyd_channels:
        if area.dyd_asset_name(channel_id) is None:
            continue
        params = area.dyd_placement_params(channel_id)
        if params is None:
            continue
        candidates = _candidate_cap(params, area.is_dyd_mesh_channel(channel_id))
        total += candidates * sum(density) / 255.0
    return total * viewport_density


def add_dyd_scatter(obj, heightmap, area, resources_root_check=True, viewport_density=None,
                    world_scale=1.0, max_viewport_instances=0, info=None):
    # type: (bpy.types.Object, hms.Heightmap, area_dat.AreaDat, bool, Optional[float], float, int, Optional[dict]) -> int
    """
    THE ENTRY POINT. Adds one Geometry Nodes modifier per real DYD
    channel on this tile's heightmap, each instancing the shared "SWTOR
    HMS - DYD Scatter" group with that channel's own density attribute,
    resolved instance object (billboard card or real mesh), and decoded
    placement parameters. Channels that can't resolve a real asset or
    placement params are skipped, with a printed reason -- matching this
    addon's warnings-over-hard-blocks convention.

    world_scale is the scale the SURROUNDING WORLD was built at -- 1.0 when
    the tile (and everything around it) is at SWTOR's natural size, as in
    the standalone harness, or Area Assembler's scale_factor when the tile
    mesh was baked at that scale. It rescales every quantity that is
    measured in Blender units so the scatter looks the same at any scale:
    Points Per m2 by 1/S^2 (the surface is S^2 larger), Noise Frequency by
    1/S (noise is sampled in the tile's scaled coordinates; Noise Origin is
    already in those units because it comes from the scaled object
    location), and the scale terms by S (billboard cards are unitless) --
    with GR2 mesh channels' terms by S divided by the scale their own GR2
    import baked in, which is exactly 1 when both come from the same
    preference.

    max_viewport_instances > 0 is a safety cap on the tile's estimated
    VIEWPORT instance count (estimate_scatter_instances()). Over it, the tile
    is still scattered, but with its "Viewport Density" lowered just far
    enough to land on the cap -- a sparse preview in the viewport, full
    density at render time (Density Multiplier is untouched). 0 disables the
    check. If `info` is a dict, the density change is recorded in it as
    info["viewport_density_reduced"] = (from, to, estimated_instances).

    Returns how many modifiers were actually added.
    """
    if not heightmap.dyd_channels:
        return 0

    if world_scale <= 0.0:
        raise ValueError("world_scale must be positive, got %r" % (world_scale,))

    if viewport_density is None:
        viewport_density = DYD_VIEWPORT_DENSITY

    if max_viewport_instances > 0:
        estimated = estimate_scatter_instances(heightmap, area, viewport_density)
        if estimated > max_viewport_instances:
            reduced = viewport_density * max_viewport_instances / estimated
            print(
                "dyd scatter on %r: estimated ~%d viewport instances is over the cap of %d -> Viewport Density "
                "lowered %.4g -> %.4g (render density unchanged)"
                % (obj.name, estimated, max_viewport_instances, viewport_density, reduced)
            )
            if info is not None:
                info["viewport_density_reduced"] = (viewport_density, reduced, estimated)
            viewport_density = reduced
    scatter_group = get_or_build_dyd_scatter_group()
    added = 0

    for channel_id, density in heightmap.dyd_channels:
        try:
            asset_name = area.dyd_asset_name(channel_id)
            if asset_name is None:
                print("dyd channel %d: no asset name in area.dat, skipping" % channel_id)
                continue

            params = area.dyd_placement_params(channel_id)
            if params is None:
                # Say WHY: a channel id can carry records of several types (type 2 = a colour int,
                # type 3 = a preset), and only type 0 holds placement data.
                record_types = sorted({c.type for c in area.dyd_channels if c.id == channel_id})
                if record_types:
                    print(
                        "dyd channel %d (%r): no type-0 placement record (only type %s in area.dat), skipping"
                        % (channel_id, asset_name, "/".join(str(t) for t in record_types))
                    )
                else:
                    print("dyd channel %d (%r): no channel record in area.dat, skipping" % (channel_id, asset_name))
                continue

            is_mesh = area.is_dyd_mesh_channel(channel_id)
            if is_mesh:
                instance_obj = _ensure_dyd_mesh_object(asset_name)
                if instance_obj is None:
                    print(
                        "dyd channel %d: mesh asset %r could not be imported, skipping" % (channel_id, asset_name)
                    )
                    continue
            else:
                instance_obj = get_or_build_billboard_card(asset_name, params.atlas_align)
                if instance_obj is None:
                    continue

            attr_name = _bake_density_attribute(obj.data, density, channel_id)

            modifier = obj.modifiers.new(name="DYD %d" % channel_id, type="NODES")
            modifier.node_group = scatter_group

            # Inputs are looked up by NAME -> real identifier from the
            # group's own interface (identifiers are Socket_N).
            input_by_name = {
                item.name: item for item in scatter_group.interface.items_tree if item.in_out == "INPUT"
            }

            # Density, per dat_VertexData.js: each 0.2 m quad tries
            # 1 + trunc(density / divisor) candidates (divisor 12 for
            # meshes, 1.5 for billboards), each accepted with probability
            # paint/255. Points per m^2 at full paint is therefore
            # candidates / 0.2^2; the painted attribute (0-1) scales it
            # inside the group.
            #
            # world_scale: that is per NATURAL m^2; a tile built S times larger
            # has S^2 the surface, so the per-unit^2 figure is divided by S^2
            # to keep the same total count.
            candidate_cap = _candidate_cap(params, is_mesh)
            points_per_m2 = candidate_cap / (hms.GRID_SPACING ** 2) / (world_scale ** 2)
            # Expected total, as a sanity check against the viewport:
            # each vertex's paint spreads over ~one quad's worth of area.
            expected_points = candidate_cap * sum(density) / 255.0

            # Rotation policy (dat_VertexData.js dydMeshInstanceMatrix):
            # meshes: rotation_mode 1 = full random XYZ (bypasses normal
            # alignment); 3 = optional align + random yaw; else optional
            # align only. Alignment itself is atlas_align == 1.
            # Billboards: upright, random yaw, never aligned.
            if is_mesh:
                rotation_mode = params.rotation_mode
                random_full = rotation_mode == 1
                random_yaw = rotation_mode in (1, 3)
                align_to_normal = params.atlas_align == 1 and rotation_mode != 1
            else:
                random_full = False
                random_yaw = True
                align_to_normal = False

            # Scale. Every ADDITIVE scale term (base, variation, minimum) is
            # multiplied by `scale_multiplier`; the paint-strength term
            # multiplies base so it follows automatically.
            #  - Billboard cards are hand-built at natural size, so they need
            #    the world's scale: S.
            #  - Imported GR2 meshes already carry the addon's "Scale Object"
            #    preference baked into their vertex data, so they need the
            #    world's scale divided by that: S / S_gr2. In Area Assembler
            #    both come from the same preference (-> 1.0, the baked mesh
            #    is already the right size); in the standalone harness the
            #    world is unscaled (world_scale 1.0), so this is the old
            #    "divide by the GR2 scale" compensation.
            scale_multiplier = world_scale / (_gr2_scale_compensation_factor() if is_mesh else 1.0)
            effective_base_scale = params.base_scale(is_mesh) * scale_multiplier
            noise_weight = 1.5 if is_mesh else 1.0 / 15.0
            scale_variation = params.scale_variation * noise_weight * scale_multiplier
            min_scale = (0.01 if is_mesh else 0.0001) * scale_multiplier

            modifier[input_by_name["Density"].identifier + "_attribute_name"] = attr_name
            modifier[input_by_name["Density"].identifier + "_use_attribute"] = True  # confirmed real key
            modifier[input_by_name["Instance"].identifier] = instance_obj
            modifier[input_by_name["Density Multiplier"].identifier] = 1.0
            modifier[input_by_name["Viewport Density"].identifier] = viewport_density
            modifier[input_by_name["Points Per m2"].identifier] = points_per_m2
            modifier[input_by_name["Base Scale"].identifier] = effective_base_scale
            modifier[input_by_name["Scale Variation"].identifier] = scale_variation
            modifier[input_by_name["Scale Strength"].identifier] = params.scale_strength
            modifier[input_by_name["Min Scale"].identifier] = min_scale
            modifier[input_by_name["Align To Normal"].identifier] = align_to_normal
            modifier[input_by_name["Random Yaw"].identifier] = random_yaw
            modifier[input_by_name["Random Full Rotation"].identifier] = random_full
            modifier[input_by_name["Seed"].identifier] = channel_id

            # Atlas variation (billboards only): frames per the sheet layout
            # the card was built with, 50% random flip. Meshes get neither.
            if is_mesh:
                atlas_last_frame, random_flip, realize_flag = 0, False, False
            else:
                fallback_columns, fallback_rows = _billboard_atlas_layout(params.atlas_align)
                columns = int(instance_obj.data.get("hms_dyd_atlas_columns", fallback_columns))
                rows = int(instance_obj.data.get("hms_dyd_atlas_rows", fallback_rows))
                atlas_last_frame = columns * rows - 1
                random_flip = True
                realize_flag = DYD_ATLAS_ATTRIBUTE_MODE == "REALIZE"
            modifier[input_by_name["Atlas Last Frame"].identifier] = atlas_last_frame
            modifier[input_by_name["Random Flip"].identifier] = random_flip
            modifier[input_by_name["Realize Instances"].identifier] = realize_flag

            # Coherent noise + tint. The noise origin is the tile's horizontal
            # world offset in the SWTOR-style (x, z) the reference samples:
            # tiles are rotated 90 deg about X with scale 1, so world (X, Y) =
            # (x + loc.x, -z + loc.y) and the matching horizontal coordinates
            # are x + loc.x and z - loc.y. Adjacent tiles then share one
            # continuous noise field.
            rotation = obj.rotation_euler
            if (abs(rotation.x - 1.5707963) > 1e-3 or abs(rotation.y) > 1e-3 or abs(rotation.z) > 1e-3
                    or max(abs(c - 1.0) for c in obj.scale) > 1e-3):
                print(
                    "dyd channel %d: tile %r is not rotated 90 deg X / scale 1, so its noise origin is approximate"
                    % (channel_id, obj.name)
                )
            # Frequency is per unit of the coordinates the group samples (the
            # tile's own, scaled, local position + Noise Origin), so it is
            # divided by the world scale to give the same noise pattern.
            modifier[input_by_name["Noise Frequency"].identifier] = params.noise_frequency / world_scale
            modifier[input_by_name["Noise Gain"].identifier] = DYD_NOISE_GAIN
            modifier[input_by_name["Tint Strength"].identifier] = DYD_TINT_STRENGTH
            modifier[input_by_name["Noise Origin"].identifier] = (obj.location.x, 0.0, -obj.location.y)
            modifier[input_by_name["Hue"].identifier] = params.hue
            modifier[input_by_name["Hue Variation"].identifier] = params.hue_variation
            modifier[input_by_name["Saturation"].identifier] = params.saturation
            modifier[input_by_name["Saturation Variation"].identifier] = params.saturation_variation
            modifier[input_by_name["Brightness"].identifier] = params.brightness
            modifier[input_by_name["Brightness Variation"].identifier] = params.brightness_variation
            modifier[input_by_name["Write Tint"].identifier] = not is_mesh

            # Printed on success too, not just failure -- the previous
            # version only ever printed warnings, which made it
            # impossible to tell a genuinely-silent success apart from
            # every channel quietly failing (exactly what happened a
            # couple of rounds back: billboard channels were building
            # materially fine but failing at the Object>Geometry step,
            # and the log gave no way to tell).
            print(
                "dyd channel %d (%r): scattered onto %r (~%d pts, %.0f/m2 at full paint, base_scale=%.4f, "
                "align=%s, yaw=%s, full_rot=%s)"
                % (
                    channel_id, asset_name, obj.name, expected_points, points_per_m2,
                    effective_base_scale, align_to_normal, random_yaw, random_full,
                )
            )
            added += 1
        except Exception as exc:
            # BUG FIX: this whole per-channel body used to run unguarded,
            # so one bad channel (confirmed the hard way: a
            # resolve_resource_path_multi() tuple-unpacking bug) took
            # down the ENTIRE tile's scatter -- including channels that
            # had already succeeded earlier in this same loop, since the
            # exception propagated straight out of add_dyd_scatter() to
            # its caller. Matches this module's own existing warnings-
            # over-hard-blocks convention (see every other per-channel
            # skip above) -- a bad channel should cost that one channel,
            # not everything else on the same tile.
            print("dyd channel %d: failed: %s" % (channel_id, exc))

    return added
