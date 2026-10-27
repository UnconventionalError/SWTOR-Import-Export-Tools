# <pep8 compliant>

"""
Stage 2 -- `.hms` terrain materials.

============================================================================
ARCHITECTURE (rewritten -- see conversation for the full history; this
replaces an earlier "blend raw textures together, feed one Uber shader"
approach entirely)
============================================================================
Each heightmap tile gets ONE real material, built as a chain of Mix
Shader nodes blending N complete, UNMODIFIED per-layer shader instances --
not a single shader fed a pre-blended mega-texture. Each layer keeps its
own independent DiffuseMap/RotationMap1/GlossMap and whatever shading
characteristics its own .mat authored; only the SHADER OUTPUTS get
blended, by the same per-vertex splat weights used everywhere else.

This replaced an earlier "blend raw textures together, feed one Uber
shader" approach for a real reason, not just preference: collapsing every
layer's textures into one shared shader instance means every layer is
forced through the same shader parameters -- there's no way for e.g.
glossy wet-looking grass and matte cracked brick to coexist, since a
texture-level blend only ever evaluates ONE shader per pixel. Blending
shader RESULTS instead means each layer's own authored shading survives
all the way through.

Mix Shader only blends TWO shaders at a time (a straight 0-1 factor), so
N layers chain through N-1 Mix Shader nodes using the standard cumulative-
weighted-average trick: layer i's mix factor is w_i / (w_0 + ... + w_i),
not its raw weight -- see _compute_cumulative_fac()'s docstring for the
derivation. This reproduces the exact same N-way weighted result as a
parallel sum would, just computed progressively.

Each per-layer shader instance is now a TEMPLATE-APPENDED node group --
"TEMPLATE GROUP - HeroEngine SWTOR - HMS Uber Shader", hand-built by
Crunch inside "TEMPLATE - HMS Uber Shader" in the same bundled
HeroEngine_Shaders.blend every other shader template lives in -- rather than
assembled node-by-node in Python. See _append_hms_layer_template() and
_build_layer_shader_group(). This moves the per-layer internal layout
(previously ad hoc Python-picked coordinates) into Blender's own node
editor, where it can actually be designed instead of guessed at, and
means the whole class of "leaked placeholder Alpha" bug this module kept
hitting is now confined to one well-understood, deliberate spot
(_clear_placeholder_alpha_link) instead of something a Python rewrite
could reintroduce by accident.

Confirmed against a real sample (ter_ond_jungle_moss_01_full.mat): terrain
layer materials are ordinary "Uber" .mat files -- DiffuseMap/RotationMap1/
GlossMap, nothing splat-aware about the .mat itself. The splat weighting
lives entirely in the heightmap's own VertexData (types/hms.py's
vertex_weights), completely decoupled from how any individual layer's
material is built. That means each layer's real textures can be sourced
by calling this addon's EXISTING by-name material pipeline
(ops/process_materials._build_named_material) exactly as it would be for
any other object -- no new texture-loading or .mat-parsing code needed.

============================================================================
NOT YET VERIFIED IN REAL BLENDER -- written without a local Blender to
test against (see conversation). Specifically unverified:
  - color_attributes.new()'s exact call signature has shifted across
    recent Blender versions (attribute vs. color_attribute APIs) -- used
    the current 4.x form here, worth double-checking against 4.5 LTS.
  - ShaderNodeMixShader's fixed layout (inputs "Fac"/[1]/[2], output
    "Shader") is long-standing, simple, unchanging Blender API, unlike
    the general-purpose Mix node's data-type-dependent socket layout that
    caused real problems earlier -- lower risk than most of the above,
    but still not run against a real Blender.

RESOLVED, confirmed against real node dumps of the actual template (no
longer guesses):
  - The wrapper group's own Shader output is confirmed at index [0],
    named "Shader" -- addressed by name now (group_instance.
    outputs["Shader"]), not positionally.
  - The template's diffuseMap/rotationMap1/glossMap Image Texture nodes
    and its Mapping/UV Map nodes were confirmed to exist, by those exact
    names, nested one level inside the wrapper group -- not the
    material's own top level. _append_hms_layer_template()'s dedup pass
    targets that nested level specifically (the existing
    _append_template_material() in shader_templates.py only walks a
    MATERIAL's top-level groups, which doesn't reach this).

TERRAIN COLOUR MAP: the .hms file carries a baked per-vertex RGB colour
map (types/hms.py vertex_color_map) which the reference viewer multiplies
into every layer's diffuse (diffuse * vColor, then weights summed). It is
low-frequency baked tinting/darkening and a large part of why the real
terrain reads as natural rather than flat. THIS MODULE ONLY BAKES IT: each
tile gets a FLOAT_COLOR point attribute "hms_color_map" holding the RAW
0-1 values (no sRGB conversion). The multiply itself lives in the hand-built
wrapper template ("TEMPLATE GROUP - HeroEngine SWTOR - HMS Uber Shader"):
Color Attribute (hms_color_map) x the diffuse texture colour, feeding the
Uber group's "DiffuseMap Color" input. RAW is deliberate: the Uber group
applies its Gamma (2.1) node AFTER that input, so multiplying before it is
the same display-space multiply the reference viewer does. (An earlier
version inserted those nodes from code; that was reverted in favour of the
template.) APPLY_COLOR_MAP False bakes neutral white instead of the real map.

SPLAT SAMPLING (replaces per-vertex weight attributes as the primary path):
the heightmap's splat grid is (2w-1) x (2d-1) -- one sample every 0.1 m,
twice the vertex resolution -- but the original approach kept only every
second sample (the ones landing on vertices) and let Blender interpolate
those linearly across 0.2 m triangles, which read as blocky, stair-
stepped layer boundaries. Now the full-resolution grid is baked, per
tile, into float RGB images (3 Mix Shader factors per image) sampled with
Linear interpolation through a dedicated "hms_splat_uv" UV layer whose
per-vertex values land exactly on texel centres (see _ensure_splat_uv),
so the terrain blends with the full splat resolution. The old per-vertex
attribute path (_compute_cumulative_fac / _pack_weight_attributes /
_weight_output_socket) is kept as an automatic fallback if the texture
path raises. NOT yet verified in real Blender.

RESOLVED against real runs (from the earlier texture-blend approach,
kept here since the underlying facts -- UvScaling, the orphan-purge
landmine, missing UVs, stale attribute references -- still apply to this
version too):
  - No UV coordinates existed anywhere in this pipeline at all, so every
    Image Texture node fell back to Blender's default Generated
    (bounding-box) coordinates -- one full texture stretched across the
    whole ~12.8m tile with no repeat ("blocky, low-res"). Fixed in
    types/hms.py's parse_heightmap() (Heightmap.uvs, the real tiling
    formula from dat_VertexData.js) and standalone_import_hms.py's
    build_hms_object() (assigns them to the mesh).
  - Per-layer materials built purely to steal their loaded Images (never
    assigned to an object, so zero real users) got swept by a later
    material's own template-append orphan-purge -- "StructRNA of type
    Image has been removed". Fixed with the same use_fake_user
    protection ops/migrate_shaders.py's migrate_material() already uses
    for this exact problem -- see resolve_layer_map_images(), and now
    also _append_hms_layer_template() for the same reason.
  - Creating a SECOND packed color attribute via mesh.color_attributes.
    new() invalidated the first one's cached `.data` reference --
    IndexError "size 0" on a mesh that very much had vertices. Fixed by
    re-fetching every attribute fresh by name after all of a batch's
    .new() calls are done -- see _pack_weight_attributes().

QOL, per Crunch: per-layer shader groups are named "<layer name> -
HeroEngine SWTOR - HMS Uber Shader" (the template's own "TEMPLATE GROUP"
prefix, replaced with the layer's real material name) and deduplicated
by that name, instead of "<tile material name>_layer_<index>" and
rebuilt fresh every tile. Most real areas reuse a handful of ground
textures across dozens or hundreds of tiles, so this turns what used to
be one duplicate group per tile per layer into one shared group total.
Per-layer group instances in the outer chain are also width-matched
(150, Blender's standard Image Texture node width) to the template's own
nodes for visual consistency.
============================================================================
"""

from typing import Dict, List, Optional

import math
import re

import bpy

from ..types import hms
from ..types.mat import read_mat_full, resolve_mat_path
from ..types.shader_templates import SHADER_GROUP_NAMES, _strip_dot_suffix, _template_blend_path
from .process_materials import _build_named_material, get_resources_dirs


HMS_LAYER_TEMPLATE_MATERIAL_NAME = "TEMPLATE - HMS Uber Shader"
HMS_LAYER_TEMPLATE_GROUP_PREFIX = "TEMPLATE GROUP"
UBER_GROUP_NAME = SHADER_GROUP_NAMES["UBER"]


# The three Image Texture node names the "Uber" template material is
# already pre-wired with (see ops/material_builder.py's DERIVED_CONFIGS["Uber"]
# "maps" tuples) -- one per texture semantic this shader type uses. The
# second element of each tuple is the corresponding "HeroEngine SWTOR - Uber
# Shader" group node's own input socket name (confirmed against a real
# node dump of TEMPLATE - Uber Shader -- see conversation).
UBER_MAP_NODES = (
    ("diffuseMap", "DiffuseMap Color"),
    ("rotationMap1", "RotationMap1 Color"),
    ("glossMap", "GlossMap Color"),
)
UBER_MAP_NODE_NAMES = tuple(name for name, _socket in UBER_MAP_NODES)


# ---------------------------------------------------------------------------
# Datablock protection and cleanup (shared with ops/dyd_scatter.py)
#
# Several datablocks made during an import (per-layer materials, the shared
# per-layer wrapper groups, baked splat images, billboard card materials, the
# scatter group) must get use_fake_user=True the moment they are created:
# the import runs orphans_purge() while de-duplicating template copies, and
# anything with zero users is swept away mid-import. Once the import is done
# and each has picked up a real user, that fake user is redundant -- and
# harmful: it keeps the datablock alive after its tiles are deleted and gets
# saved into whatever .blend the user is working in (leftover fake-user
# materials in a "clean" template contaminated testing for a long while).
# protect_datablock() sets the flag AND remembers the datablock;
# release_protected_datablocks() clears it at the end of an import, on those
# datablocks only, and only where a real user exists. When the dedicated hms
# import operator exists, these three helpers belong with it.
# ---------------------------------------------------------------------------

# Per-layer wrapper groups are named "<layer> - HeroEngine SWTOR - HMS Uber
# Shader" (see _build_layer_shader_group).
WRAPPER_GROUP_SUFFIX = " - HeroEngine SWTOR - HMS Uber Shader"

_protected_datablocks = []


def protect_datablock(datablock):
    """use_fake_user=True now, and remember it for release_protected_
    datablocks(). Returns the datablock so it can wrap an assignment."""
    datablock.use_fake_user = True
    _protected_datablocks.append(datablock)
    return datablock


def release_protected_datablocks():
    # type: () -> tuple
    """
    Clears use_fake_user on every datablock protect_datablock() remembered
    that now has at least one OTHER user (a material slot, a Group node, an
    Image Texture node, a modifier ...). One with no other user keeps its
    fake user -- clearing it would orphan it -- and is listed in the printout.
    Datablocks removed during the import (de-duplicated wrapper groups) are
    skipped. Empties the list. Returns (released_count, kept_names).
    """
    released = 0
    kept = []
    seen = set()
    for datablock in _protected_datablocks:
        try:
            pointer = datablock.as_pointer()
            if pointer in seen:
                continue
            seen.add(pointer)
            name = datablock.name
            if not datablock.use_fake_user:
                continue   # already cleared by whoever finished with it
            other_users = datablock.users - 1   # .users counts the fake user
        except ReferenceError:
            continue   # removed during the import
        if other_users > 0:
            datablock.use_fake_user = False
            released += 1
        else:
            kept.append(name)
    _protected_datablocks.clear()

    print("hms cleanup: released the fake user on %d datablock(s) that have a real user now" % released)
    if kept:
        print(
            "hms cleanup: %d datablock(s) have no other user and keep their fake user: %s%s"
            % (len(kept), ", ".join(kept[:8]), " ..." if len(kept) > 8 else "")
        )
    return released, kept


def report_existing_hms_datablocks(extra_group_names=()):
    # type: (tuple) -> int
    """
    Prints what a previous import left in this file (materials, splat images,
    wrapper / scatter groups, card and tile objects, collections) and returns
    the total. Run at the start of an import: anything listed gets REUSED by
    name rather than rebuilt, so results can reflect old code or settings.
    """
    def base_name(name):
        return re.sub(r"\.\d{3}$", "", name)   # Blender's ".001" duplicate suffix

    extra = set(extra_group_names)
    found = [
        ("terrain materials (hms_mat_*)", [m.name for m in bpy.data.materials if m.name.startswith("hms_mat_")]),
        ("splat images (hms_splat_*)", [i.name for i in bpy.data.images if i.name.startswith("hms_splat_")]),
        (
            "wrapper / scatter node groups",
            [
                g.name
                for g in bpy.data.node_groups
                if (base_name(g.name).endswith(WRAPPER_GROUP_SUFFIX) and not g.name.startswith("TEMPLATE"))
                or base_name(g.name) in extra
            ],
        ),
        ("billboard card objects", [o.name for o in bpy.data.objects if o.name.startswith("hms_dyd_card_")]),
        (
            "tile objects",
            [o.name for o in bpy.data.objects if o.name.startswith("hms_") and not o.name.startswith("hms_dyd_card_")],
        ),
        (
            "collections",
            [c.name for c in bpy.data.collections if c.name.startswith("hms_test_") or c.name == "HMS DYD Assets"],
        ),
    ]
    total = sum(len(names) for _kind, names in found)
    if total == 0:
        print("stale check: no hms datablocks from a previous import found (clean file)")
        return 0

    print("=" * 70)
    print("STALE CHECK: this file already holds %d datablock(s) from a previous import:" % total)
    for kind, names in found:
        if names:
            print("  %-34s %3d   e.g. %s" % (kind, len(names), ", ".join(names[:3])))
    print(
        "  These are REUSED by name instead of rebuilt, so this run can reflect old code or settings,\n"
        "  and leftovers saved into a template file will carry over. For a trustworthy run, start from\n"
        "  a clean file (or delete the datablocks above first)."
    )
    print("=" * 70)
    return total


def _resolve_uv_scale(name):
    # type: (str) -> tuple
    """
    Reads this layer's own <UvScaling> straight from its .mat file (a
    fresh, cheap parse -- _build_named_material() doesn't hand back the
    parsed mat_info, only the built material). Confirmed real and
    non-default on the one sample seen so far (ter_ond_jungle_moss_01_full:
    "0.5,0.5") -- NOT currently wired to anything by the mainline addon
    (Uber's DERIVED_CONFIGS "maps" tuples don't reference UvScaling at
    all), so this is new consumption of an existing field, not a
    duplicate of something already applied elsewhere. Defaults to (1, 1)
    if absent or unparseable, so a material lacking it just tiles at the
    base heightmap UV density with no extra scale.
    """
    primary_dir, legacy_dir = get_resources_dirs()
    mat_path, _root = resolve_mat_path(name, primary_dir, legacy_dir)
    if mat_path is None:
        return 1.0, 1.0
    mat_info = read_mat_full(mat_path, name)
    if mat_info is None:
        return 1.0, 1.0
    return _uv_scale_from_other_values(mat_info.get("otherValues", {}))


def _uv_scale_from_other_values(other_values):
    # type: (dict) -> tuple
    """(u, v) from a .mat's captured semantics, matching the name
    case-insensitively: real files spell it both "UvScaling" (Onderon terrain
    layers) and "UVScaling" (e.g. ter_ald_killik_grass01, value 2,2), and the
    reader keeps each semantic under its exact spelling."""
    raw = None
    for key, value in other_values.items():
        if isinstance(key, str) and key.lower() == "uvscaling":
            raw = value
            break
    if not raw or len(raw) < 2:
        return 1.0, 1.0
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return 1.0, 1.0


def resolve_layer_map_images(layer_names):
    # type: (List[Optional[str]]) -> Dict[str, Optional[Dict[str, object]]]
    """
    For every distinct real layer name (None entries -- an unused splat
    slot -- are skipped), builds (or reuses, via _build_named_material's
    own by-name dedup in bpy.data.materials) a real Uber material for
    that terrain texture and pulls its already-loaded diffuseMap/
    rotationMap1/glossMap Image datablocks straight off it, plus that
    layer's own UV scale (see _resolve_uv_scale()).

    These per-layer materials are deliberately left sitting in
    bpy.data.materials afterwards rather than being torn down -- cheap
    (dedup means each distinct name only ever gets built once across an
    entire area import, not once per tile that happens to use it), and
    they're directly useful now beyond just being an Image source: see
    add_debug_material_slots().

    Returns {name: {"diffuseMap": Image, "rotationMap1": Image,
    "glossMap": Image, "uv_scale": (float, float), "material": Material}}
    -- "material" is the real per-layer material itself, present even
    when one or more maps came up empty. A layer maps to None only when
    the material couldn't be built AT ALL (not found, skip-listed, or a
    shader type other than Uber that isn't built yet).

    Every gap gets printed -- both the all-or-nothing "material not
    usable" case and the quieter case where the material built fine but
    a SPECIFIC map inside it (diffuse/normal/gloss) came up with no
    image.
    """
    result = {}
    for name in layer_names:
        if name is None or name in result:
            continue
        material, proc_result = _build_named_material(name)
        if material is None:
            print(
                "hms terrain layer %r: material not usable (%s%s)"
                % (name, proc_result.status, ": " + proc_result.detail if proc_result.detail else "")
            )
            result[name] = None
            continue
        # PROTECTION: these per-layer materials aren't assigned to any
        # object of their own (see build_hms_material -- they're
        # referenced from inside a generated node group instead, and
        # separately as debug-only slots), so they have zero real users
        # -- exactly what a LATER material's own template-append can
        # orphan-purge away, globally, per get_or_create_material()'s own
        # documented landmine (confirmed the hard way: every layer failed
        # with "StructRNA of type Image has been removed" once a
        # subsequent material's append triggered a purge). Same fix
        # ops/migrate_shaders.py's migrate_material() already uses for
        # this exact problem -- fake-user-protect the material
        # immediately, which transitively protects everything its node
        # tree still references (its loaded Images included), since
        # purge only removes datablocks with truly zero users anywhere,
        # real or fake. (Released again at the end of the import --
        # release_protected_datablocks().)
        protect_datablock(material)
        image_nodes = {n.name: n for n in material.node_tree.nodes if n.type == "TEX_IMAGE"}
        entry = {
            key: image_nodes[key].image
            for key in UBER_MAP_NODE_NAMES
            if key in image_nodes and image_nodes[key].image is not None
        }
        missing = [key for key in UBER_MAP_NODE_NAMES if key not in entry]
        if missing:
            print("hms terrain layer %r: built, but no image for %s" % (name, ", ".join(missing)))
        entry["uv_scale"] = _resolve_uv_scale(name)
        entry["material"] = material
        result[name] = entry
    return result


def _make_missing_layer_placeholder(name):
    # type: (str) -> bpy.types.Material
    """
    A real, visible stand-in for a layer whose material couldn't be built
    at all -- bright magenta Emission, named so the gap is legible by
    itself in the Shading workspace's material slot list rather than
    just missing from it (matching the classic "missing texture" magenta
    convention, and matching this addon's warnings-over-hard-blocks
    convention -- represent the gap, don't hide it).
    """
    mat_name = "MISSING_%s" % name
    existing = bpy.data.materials.get(mat_name)
    if existing is not None:
        return existing
    material = bpy.data.materials.new(mat_name)
    material.use_nodes = True
    protect_datablock(material)
    emission = material.node_tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (1.0, 0.0, 1.0, 1.0)
    output = next((n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is not None:
        material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def add_debug_material_slots(obj, layer_names, layer_images):
    # type: (bpy.types.Object, List[Optional[str]], Dict) -> None
    """
    Appends one EXTRA material slot per distinct real layer name, on top
    of whatever's already in obj.data.materials (the actual rendering
    material -- see build_hms_material()) -- assigned to zero faces, so
    they have no effect on rendering. Purely for inspection: each real
    per-layer .mat, unmodified, named after itself, so a broken or
    missing texture shows up isolated to its own slot in the Shading
    workspace. A layer whose material failed to build entirely still
    gets a slot -- a visible magenta placeholder named MISSING_<name> --
    so the gap is something you can see and click on, not just absence.
    """
    seen = set()
    for name in layer_names:
        if name is None or name in seen:
            continue
        seen.add(name)
        entry = layer_images.get(name)
        material = entry["material"] if entry is not None else _make_missing_layer_placeholder(name)
        obj.data.materials.append(material)


def _clear_placeholder_alpha_link(node_tree, image_nodes, map_node_name):
    # type: (bpy.types.NodeTree, Dict, str) -> None
    """
    Removes whatever link a template's placeholder Image Texture node
    (diffuseMap/rotationMap1/glossMap) has on its Alpha output. Needed
    again here for the same reason it was needed the first time this bug
    showed up (see this module's docstring's RESOLVED-history section):
    an unpopulated Image Texture node's Alpha evaluates to 0, which is
    still pre-wired into the inner HeroEngine Uber group's own "<Map> Alpha"
    input by the template itself -- silently overriding a deliberately-
    chosen default with garbage. Only actually needed for a layer that's
    genuinely missing this map; a layer WITH a real image just gets that
    image's own real alpha data through the template's existing wiring,
    which is correct and wanted (e.g. glossiness commonly packed there).
    """
    placeholder = image_nodes.get(map_node_name)
    if placeholder is None:
        return
    alpha_socket = placeholder.outputs.get("Alpha")
    if alpha_socket is None:
        return
    for link in list(node_tree.links):
        if link.from_socket == alpha_socket:
            node_tree.links.remove(link)


def _append_hms_layer_template():
    # type: () -> bpy.types.NodeTree
    """
    Appends "TEMPLATE - HMS Uber Shader" fresh from the same bundled
    HeroEngine_Shaders.blend every other shader template lives in (confirmed
    -- no separate file), dedupes the "HeroEngine SWTOR - Uber Shader"
    instance nested ONE LEVEL INSIDE its own wrapper group, then discards
    the scratch material and returns just the wrapper's own node tree --
    fresh, unique, not yet associated with any particular layer.

    This can't reuse types/shader_templates.py's own
    _append_template_material() as-is: that function dedupes a
    MATERIAL's top-level groups, but here the material's only top-level
    node IS the wrapper group we want to keep fresh/undeduped (each layer
    needs its own private copy, since it'll hold that layer's own
    images) -- the group we actually want deduped, "HeroEngine SWTOR - Uber
    Shader", is nested one level further in, inside the wrapper's own
    node tree. Same append -> remap -> orphan-purge shape, just aimed one
    level deeper (confirmed against a real node dump of the template --
    see conversation).
    """
    template_path = str(_template_blend_path())
    scratch_name = "__hms_layer_template_scratch__"

    with bpy.data.libraries.load(template_path, link=False) as (data_from, data_to):
        if HMS_LAYER_TEMPLATE_MATERIAL_NAME not in data_from.materials:
            raise ValueError(
                "Template material %r not found in %s -- has it been added "
                "to the bundled .blend yet?" % (HMS_LAYER_TEMPLATE_MATERIAL_NAME, template_path)
            )
        data_to.materials = [HMS_LAYER_TEMPLATE_MATERIAL_NAME]
    scratch_material = data_to.materials[0]
    # Protect through the dedup/purge below -- orphans_purge() is a global
    # sweep, not scoped to this call (same reasoning
    # _append_template_material's own docstring gives for doing this).
    scratch_material.use_fake_user = True

    wrapper_node = next(
        (n for n in scratch_material.node_tree.nodes if n.type == "GROUP" and n.node_tree is not None),
        None,
    )
    if wrapper_node is None:
        raise ValueError(
            "Appended %r has no top-level group node -- expected the HMS "
            "Uber Shader wrapper group." % HMS_LAYER_TEMPLATE_MATERIAL_NAME
        )
    wrapper_tree = wrapper_node.node_tree
    # Protect through the dedup/purge below, and permanently after --
    # this tree is about to be shared/deduplicated across every tile that
    # reuses this layer, same reasoning resolve_layer_map_images() already
    # applies to the per-layer materials. (Released again at the end of the
    # import -- release_protected_datablocks().)
    protect_datablock(wrapper_tree)

    needs_purge = False
    for node in list(wrapper_tree.nodes):
        if node.type != "GROUP" or node.node_tree is None:
            continue
        base_name = _strip_dot_suffix(node.node_tree.name)
        if base_name != UBER_GROUP_NAME:
            continue
        canonical = bpy.data.node_groups.get(base_name)
        if canonical is not None and node.node_tree != canonical:
            node.node_tree = canonical
            needs_purge = True

    scratch_material.use_fake_user = False
    bpy.data.materials.remove(scratch_material)  # only wanted the wrapper tree, not the scaffold material

    if needs_purge:
        bpy.data.orphans_purge(do_recursive=True)

    return wrapper_tree


# TILING EXPERIMENT. Two references disagree on how often the ground
# textures repeat on a tile:
#   * world viewer source (TerrainAntiTile.frag, claims the engine's rule):
#     uv = world XZ * the layer's <UvScaling> (0.5 on the Onderon layers),
#     i.e. one repeat per 2 units.        -> WORLD_SPACE_TERRAIN_UVS=True,
#                                            IGNORE_LAYER_UV_SCALING=False
#   * SWTOR-Terrain-Tools / the old tile viewer: width/2 repeats per tile
#     (~2.5 per unit) and layer UvScaling ignored.  -> WORLD_SPACE_TERRAIN_UVS
#     =False (types/hms.py) and IGNORE_LAYER_UV_SCALING=True (below).
# ~5x apart. Flip these to compare against a trusted screenshot.
# With WORLD_SPACE_TERRAIN_UVS the tiling UVs are also made continuous
# across tiles (_make_tiling_uvs_world_continuous).
IGNORE_LAYER_UV_SCALING = False

# DEBUG: True renders the tile as FLAT ALBEDO -- the same per-pixel layer
# blend, but of each layer's "Diffuse Color" output, through an Emission
# shader (no lighting, specular, normals or gloss). Use it to tell whether
# a colour difference against another tool's bake comes from lighting /
# Uber shading or from the albedo data itself. Leave False for normal use.
DEBUG_FLAT_DIFFUSE = False

# True: bake the tile's real colour map onto the mesh (the template
# multiplies it in). False: bake neutral white instead, so the template's
# multiply becomes a no-op rather than reading a missing attribute (black).
APPLY_COLOR_MAP = True
COLOR_MAP_ATTRIBUTE = "hms_color_map"


def _make_tiling_uvs_world_continuous(obj, world_scale=1.0):
    # type: (bpy.types.Object, float) -> None
    """
    Turns the tile's "hms_uv" tiling UVs (tile-local x / z in world units
    when hms.WORLD_SPACE_TERRAIN_UVS is on) into true WORLD-space ones, so a
    layer's texture lines up across neighbouring tiles instead of restarting
    at each tile's origin -- the world viewer's rule (texCoord = world
    position xz, then x the layer's UvScaling).

    World UV = (world X, -world Y): a tile rotated 90 deg about X maps local
    (x, y, z) to world (x + loc.x, -z + loc.y, y + loc.z), so this equals
    local (x, z) plus the tile's horizontal offset, i.e. the SAME orientation
    as the tile-local UVs it replaces (the V sign keeps the texture, and a
    normal map's green channel, un-mirrored).

    Uses obj.matrix_basis, which is computed from location / rotation /
    scale the moment it is read; obj.matrix_world is NOT updated until the
    depsgraph evaluates, and would still be identity right after the tile
    was created.

    world_scale: the scale the tile was built at (its vertices are baked at
    it and its location already carries it -- Area Assembler's scale_factor;
    1.0 for an unscaled tile, as in the standalone harness). UVs are
    measured in NATURAL world units (a layer's UvScaling means "repeats per
    natural unit"), so the scaled world position is divided by it again.
    """
    if not hms.WORLD_SPACE_TERRAIN_UVS:
        return
    layer = obj.data.uv_layers.get("hms_uv")
    if layer is None:
        print("hms material: tile %r has no hms_uv layer, UVs left as they are" % obj.name)
        return
    if obj.parent is not None:
        print("hms material: tile %r is parented; world-continuous UVs use its own transform only" % obj.name)
    matrix = obj.matrix_basis
    world = [matrix @ vertex.co for vertex in obj.data.vertices]
    for loop in obj.data.loops:
        point = world[loop.vertex_index]
        layer.data[loop.index].uv = (point.x / world_scale, -point.y / world_scale)


def _ensure_color_map_attribute(mesh, heightmap):
    # type: (bpy.types.Mesh, hms.Heightmap) -> None
    """Adds the tile's baked terrain colour map as a FLOAT_COLOR POINT
    attribute of RAW 0-1 values (see this module's docstring for why raw).
    vertex_color_map is already in the mesh's k*width+j vertex order."""
    if COLOR_MAP_ATTRIBUTE in mesh.color_attributes:
        return
    colors = heightmap.vertex_color_map if APPLY_COLOR_MAP else [(255, 255, 255)] * len(mesh.vertices)
    if len(colors) != len(mesh.vertices):
        raise ValueError(
            "colour map has %d entries but the mesh has %d vertices" % (len(colors), len(mesh.vertices))
        )
    attribute = mesh.color_attributes.new(name=COLOR_MAP_ATTRIBUTE, type="FLOAT_COLOR", domain="POINT")
    flat = []
    for r, g, b in colors:
        flat.extend((r / 255.0, g / 255.0, b / 255.0, 1.0))
    attribute.data.foreach_set("color", flat)


def _rgba_socket(sockets, name):
    """The live RGBA socket called `name` -- ShaderNodeMix carries several
    same-named sockets of different types, only one active at a time."""
    candidates = [s for s in sockets if s.name == name and s.type == "RGBA"]
    for s in candidates:
        if s.enabled:
            return s
    return candidates[0]


def _build_layer_shader_group(images, name):
    # type: (Dict, str) -> bpy.types.NodeTree
    """
    Gets one layer's own dedicated wrapper group -- built and populated
    on a cache miss, reused directly on a cache hit -- an appended,
    per-layer copy of the "TEMPLATE - HMS Uber Shader" template, wired to
    this layer's own UNMODIFIED diffuse/normal/gloss textures and its own
    UV scale. Several of these sit side by side in one tile's material
    and get blended by shader (see build_hms_material) rather than only
    ever existing one-per-material.

    Named "<name> - HeroEngine SWTOR - HMS Uber Shader" (the template's
    own "TEMPLATE GROUP" prefix, replaced with the layer's real material
    name -- Crunch's own convention) and deduplicated by that name --
    reused across every tile that happens to use this same terrain
    texture layer, rather than rebuilt per tile. Most real areas reuse a
    handful of ground textures across dozens or hundreds of tiles (e.g.
    ter_ond_jungle_dirt_01 across most of the Onderon arena), so this
    turns what would otherwise be one duplicate wrapper per tile per
    layer into one shared wrapper total.

    A map this layer doesn't have gets its placeholder Alpha link cleared
    (see _clear_placeholder_alpha_link) but is otherwise left alone.
    """
    group_name = "%s - HeroEngine SWTOR - HMS Uber Shader" % name
    existing = bpy.data.node_groups.get(group_name)
    if existing is not None:
        return existing

    wrapper_tree = _append_hms_layer_template()

    image_nodes = {n.name: n for n in wrapper_tree.nodes if n.type == "TEX_IMAGE"}
    # Only ever touching Scale, not vector_type or anything else -- every
    # other setting on these Mapping nodes is however you configured the
    # template, left untouched.
    uv_scale = (1.0, 1.0) if IGNORE_LAYER_UV_SCALING else images.get("uv_scale", (1.0, 1.0))
    for mapping_node in wrapper_tree.nodes:
        if mapping_node.bl_idname == "ShaderNodeMapping":
            mapping_node.inputs["Scale"].default_value = (uv_scale[0], uv_scale[1], 1.0)

    uv_node = next((n for n in wrapper_tree.nodes if n.bl_idname == "ShaderNodeUVMap"), None)
    if uv_node is not None:
        uv_node.uv_map = "hms_uv"

    # The template's Color Attribute node (colour-map multiply) can't be
    # saved pre-populated with the attribute name, same as the UV Map node.
    color_nodes = [n for n in wrapper_tree.nodes if n.bl_idname == "ShaderNodeVertexColor"]
    if color_nodes:
        for color_node in color_nodes:
            color_node.layer_name = COLOR_MAP_ATTRIBUTE
    else:
        print("hms layer %r: template has no Color Attribute node -- terrain colour map will NOT be applied" % name)

    for map_key, _socket_name in UBER_MAP_NODES:
        tex_node = image_nodes.get(map_key)
        if tex_node is None:
            continue
        image = images.get(map_key)
        if image is not None:
            tex_node.image = image
            if map_key == "rotationMap1":
                tex_node.image.colorspace_settings.name = "Non-Color"
        else:
            _clear_placeholder_alpha_link(wrapper_tree, image_nodes, map_key)

    # _strip_dot_suffix in case a stray un-renamed "TEMPLATE GROUP - ..."
    # somehow persisted locally from an earlier run and forced Blender to
    # auto-suffix this fresh append -- shouldn't happen under normal
    # operation (every successful build renames its copy away from that
    # prefix immediately), but cheap insurance either way.
    base_name = _strip_dot_suffix(wrapper_tree.name)
    wrapper_tree.name = base_name.replace(HMS_LAYER_TEMPLATE_GROUP_PREFIX, name)
    return wrapper_tree



def _compute_cumulative_fac(heightmap, resolved_indices):
    # type: (hms.Heightmap, List[int]) -> tuple
    """
    Per-vertex Mix Shader factors for chaining len(resolved_indices)
    per-layer shaders two at a time into an exact weighted blend.

    Mix Shader only blends two shaders at once, so N layers need N-1
    chained Mix Shader nodes, each taking "everything mixed so far" as
    one input and the next layer as the other. The standard trick for
    making that chain reproduce a true N-way weighted sum: layer i's mix
    factor is w_i / (w_0 + ... + w_i) -- the cumulative running total
    INCLUDING itself. By induction, each step's result equals the
    correctly-weighted average of every layer folded in so far (worked
    through explicitly in conversation for 3 layers; holds for any N).

    `resolved_indices` -- the layer_names indices whose material actually
    built (see resolve_layer_map_images) -- decides both the mix order
    and, by only summing over these, renormalizes away any unresolved
    layers' weight share rather than leaving a "missing" gap.

    Returns (flat vertex-major fac list, facs-per-vertex) -- one fac
    value per vertex per entry in resolved_indices[1:] (the first
    resolved layer is the base of the chain and needs no Mix Shader of
    its own, hence N-1 not N).
    """
    num_layers = len(heightmap.layer_names)
    stride = num_layers if num_layers > 0 else 1
    chain_indices = resolved_indices[1:]
    out_stride = len(chain_indices)
    out = [0.0] * (len(heightmap.positions) * out_stride)
    if out_stride == 0:
        return out, out_stride

    for vi in range(len(heightmap.positions)):
        base = vi * stride
        out_base = vi * out_stride
        cumsum = heightmap.vertex_weights[base + resolved_indices[0]]
        for k, i in enumerate(chain_indices):
            w = heightmap.vertex_weights[base + i]
            cumsum += w
            out[out_base + k] = (w / cumsum) if cumsum > 1e-8 else 0.0

    return out, out_stride


def _pack_weight_attributes(mesh, weights, num_layers, name_prefix):
    # type: (bpy.types.Mesh, List[float], int, str) -> List[bpy.types.Attribute]
    """
    Bakes a flat vertex-major weight/fac list (num_layers floats per
    vertex) into ceil(num_layers / 4) POINT-domain FLOAT_COLOR
    attributes, 4 values packed per RGBA attribute, named
    "<name_prefix>_0", "<name_prefix>_1", etc.
    """
    num_attrs = max(1, (num_layers + 3) // 4)
    names = []
    for i in range(num_attrs):
        mesh.color_attributes.new(name="%s_%d" % (name_prefix, i), type="FLOAT_COLOR", domain="POINT")
        # Capture the REAL name immediately (Blender may rename on a
        # collision), before any further .new() call has a chance to
        # invalidate this reference -- see below.
        names.append(mesh.color_attributes[-1].name)

    # Re-fetch fresh references by name AFTER every .new() call in this
    # batch is done, rather than trusting the objects .new() returned as
    # we go. Confirmed the hard way: creating attribute #2 invalidated
    # attribute #1's cached `.data` (IndexError: "size 0" on a mesh that
    # very much has vertices) -- the same stale-StructRNA class of bug
    # this addon's own learnings-and-practices notes already flag
    # elsewhere (Image datablocks swept by orphan purge mid-import is the
    # other documented instance of it). One extra lookup per attribute,
    # once, is cheap insurance against it.
    attrs = [mesh.color_attributes[name] for name in names]

    stride = num_layers if num_layers > 0 else 1
    for vi in range(len(mesh.vertices)):
        for i, attr in enumerate(attrs):
            rgba = [0.0, 0.0, 0.0, 0.0]
            for c in range(4):
                layer_idx = i * 4 + c
                if layer_idx < num_layers:
                    rgba[c] = weights[vi * stride + layer_idx]
            attr.data[vi].color = rgba

    return attrs


def _weight_output_socket(node_tree, weight_attrs, layer_idx, x, y):
    # type: (bpy.types.NodeTree, List[bpy.types.Attribute], int, float, float) -> object
    """A Color Attribute node reading the right packed attribute, plus a
    Separate Color node picking out this layer's own channel."""
    attr_index, channel = divmod(layer_idx, 4)
    attr_node = node_tree.nodes.new("ShaderNodeVertexColor")
    attr_node.layer_name = weight_attrs[attr_index].name
    attr_node.location = (x, y)

    if channel == 3:
        return attr_node.outputs["Alpha"]

    sep_node = node_tree.nodes.new("ShaderNodeSeparateColor")
    sep_node.location = (x + 160, y)
    node_tree.links.new(attr_node.outputs["Color"], sep_node.inputs["Color"])
    return sep_node.outputs[("Red", "Green", "Blue")[channel]]


# Gaussian blur (sigma, in splat texels = 0.1 units each) applied to the
# splat WEIGHTS before they are baked into the factor images. 0 = exact
# authored data (what the engine / world viewer sample, hard-edged blocks
# with a one-texel bilinear ramp). > 0 rounds the stair-steps and softens
# layer boundaries beyond the engine's own look -- an aesthetic choice, not
# fidelity. Blurring a partition of unity keeps it a partition of unity.
SPLAT_BLUR_SIGMA_TEXELS = 0.0

SPLAT_UV_NAME = "hms_splat_uv"
SPLAT_CHANNELS_PER_IMAGE = 3  # RGB; alpha left at 1.0 so no premultiply trap


def _ensure_splat_uv(mesh, heightmap):
    # type: (bpy.types.Mesh, hms.Heightmap) -> None
    """
    Adds the "hms_splat_uv" layer: vertex (j, k) maps to the CENTRE of
    splat texel (2j, 2k) -- u = (2j + 0.5) / (2w - 1), v likewise. Splat
    sample 2j sits exactly on vertex j, and Blender's linear UV
    interpolation across each triangle then reproduces proper bilinear
    sampling of the full-resolution map between vertices. The tile's
    existing UV layer stays the active / active-render one, so the layer
    templates' own default-UV Map nodes keep reading the tiling UVs.
    """
    if SPLAT_UV_NAME in mesh.uv_layers:
        return
    original_active = mesh.uv_layers.active
    original_render = next((layer for layer in mesh.uv_layers if layer.active_render), None)

    layer = mesh.uv_layers.new(name=SPLAT_UV_NAME)
    width = heightmap.width
    map_w = heightmap.layer_map_width
    map_d = heightmap.layer_map_depth
    for loop in mesh.loops:
        vertex = loop.vertex_index
        j = vertex % width
        k = vertex // width
        layer.data[loop.index].uv = ((2 * j + 0.5) / map_w, (2 * k + 0.5) / map_d)

    if original_active is not None:
        mesh.uv_layers.active = mesh.uv_layers[original_active.name]
    if original_render is not None:
        mesh.uv_layers[original_render.name].active_render = True


def _bake_splat_fac_images(heightmap, resolved_indices, name_prefix):
    # type: (hms.Heightmap, List[int], str) -> List[bpy.types.Image]
    """
    Bakes the RAW (optionally blurred) layer weights of every resolved
    layer, per splat TEXEL at full resolution, into float images,
    SPLAT_CHANNELS_PER_IMAGE weights per image (cumulative factors are
    derived per pixel in the material). Non-Color, never premultiplied (alpha is a flat
    1.0). Returns the images in factor order.
    """
    num_layers = len(heightmap.layer_names)
    map_w = heightmap.layer_map_width
    map_d = heightmap.layer_map_depth
    alpha = heightmap.layer_alpha
    num_images = (len(resolved_indices) + SPLAT_CHANNELS_PER_IMAGE - 1) // SPLAT_CHANNELS_PER_IMAGE
    buffers = [[0.0] * (map_w * map_d * 4) for _ in range(num_images)]

    # 1) Normalized per-texel weights (same base-layer fill as types/hms.py's
    #    per-vertex reduction), stored per layer as a flat map_w*map_d list.
    layer_weights = [[0.0] * (map_w * map_d) for _ in range(num_layers)]
    for texel in range(map_w * map_d):
        base = texel * num_layers
        samples = [alpha[base + i] for i in range(num_layers)]
        total = sum(samples)
        if num_layers > 0 and total < 255:
            samples[0] += 255 - total
            total = 255
        if total > 0:
            for i in range(num_layers):
                layer_weights[i][texel] = samples[i] / total
        else:
            layer_weights[0][texel] = 1.0

    # 2) Optional separable Gaussian blur per layer (clamped at the edges).
    if SPLAT_BLUR_SIGMA_TEXELS > 0.0:
        radius = max(1, int(math.ceil(3.0 * SPLAT_BLUR_SIGMA_TEXELS)))
        kernel = [math.exp(-0.5 * (d / SPLAT_BLUR_SIGMA_TEXELS) ** 2) for d in range(-radius, radius + 1)]
        norm = sum(kernel)
        kernel = [k / norm for k in kernel]
        for i in range(num_layers):
            channel = layer_weights[i]
            if not any(channel):
                continue  # layer never present on this tile
            horizontal = [0.0] * (map_w * map_d)
            for row in range(map_d):
                row_base = row * map_w
                for col in range(map_w):
                    acc = 0.0
                    for t, weight in enumerate(kernel):
                        c = min(map_w - 1, max(0, col + t - radius))
                        acc += weight * channel[row_base + c]
                    horizontal[row_base + col] = acc
            blurred = [0.0] * (map_w * map_d)
            for row in range(map_d):
                for col in range(map_w):
                    acc = 0.0
                    for t, weight in enumerate(kernel):
                        r = min(map_d - 1, max(0, row + t - radius))
                        acc += weight * horizontal[r * map_w + col]
                    blurred[row * map_w + col] = acc
            layer_weights[i] = blurred

    # 3) Store the (optionally blurred) RAW weights of every resolved layer,
    #    in chain order, SPLAT_CHANNELS_PER_IMAGE per image. The cumulative
    #    Mix Shader factors are derived PER PIXEL in the material (see
    #    _build_splat_fac_sockets): interpolating the cumulative ratios
    #    instead (the previous approach) is not the same as interpolating
    #    the weights, and between two non-base layers it mixes in a ghost of
    #    the base layer (full->sparse moss at the midpoint came out ~25%
    #    base, 25% full, 50% sparse instead of 50/50).
    for texel in range(map_w * map_d):
        for rank, layer_index in enumerate(resolved_indices):
            image_index, channel_index = divmod(rank, SPLAT_CHANNELS_PER_IMAGE)
            buffers[image_index][texel * 4 + channel_index] = layer_weights[layer_index][texel]
        for buffer in buffers:
            buffer[texel * 4 + 3] = 1.0

    images = []
    for n, buffer in enumerate(buffers):
        image = bpy.data.images.new("%s_%d" % (name_prefix, n), map_w, map_d, alpha=True, float_buffer=True)
        image.colorspace_settings.name = "Non-Color"
        image.pixels.foreach_set(buffer)
        image.update()
        # Orphan-purge protection, same landmine as every other datablock
        # created here; the node that uses it adds a real user shortly.
        protect_datablock(image)
        # Generated images are lost on save/reopen unless packed. Best
        # effort -- a failure here only matters if the .blend is saved.
        try:
            image.pack()
        except Exception as exc:  # noqa: BLE001
            print("hms splat image %r: could not pack (%s) -- will not survive save/reopen" % (image.name, exc))
        images.append(image)
    return images


def _build_splat_fac_sockets(node_tree, mesh, heightmap, resolved_indices, mat_name, x, y):
    # type: (bpy.types.NodeTree, bpy.types.Mesh, hms.Heightmap, List[int], str, float, float) -> List[object]
    """
    Texture-based factor source. One UV Map node ("hms_splat_uv"), then per
    baked image ONE Image Texture node (Linear, clamped at the edges) and
    ONE Separate Color node, whose channels are the interpolated RAW layer
    weights in chain order. From those, per pixel:
        cumulative_k = w_0 + ... + w_k          (Math ADD chain)
        fac_k        = w_k / cumulative_k        (Math DIVIDE; /0 -> 0)
    which, fed to the Mix Shader chain, reproduces exactly the weighted
    average sum(w_i * layer_i) / sum(w_i) at every pixel. Returns one fac
    socket per Mix Shader step (len(resolved_indices) - 1).
    """
    _ensure_splat_uv(mesh, heightmap)
    images = _bake_splat_fac_images(heightmap, resolved_indices, "hms_splat_%s" % mat_name)

    uv_node = node_tree.nodes.new("ShaderNodeUVMap")
    uv_node.uv_map = SPLAT_UV_NAME
    uv_node.location = (x - 400, y)

    weight_sockets = []
    for n, image in enumerate(images):
        tex_node = node_tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        tex_node.interpolation = "Linear"
        tex_node.extension = "EXTEND"
        tex_node.location = (x, y - 300 * n)
        node_tree.links.new(uv_node.outputs["UV"], tex_node.inputs["Vector"])

        sep_node = node_tree.nodes.new("ShaderNodeSeparateColor")
        sep_node.location = (x + 300, y - 300 * n)
        node_tree.links.new(tex_node.outputs["Color"], sep_node.inputs["Color"])
        weight_sockets.extend([sep_node.outputs["Red"], sep_node.outputs["Green"], sep_node.outputs["Blue"]])
    weight_sockets = weight_sockets[:len(resolved_indices)]

    fac_sockets = []
    cumulative = weight_sockets[0]
    for k in range(1, len(weight_sockets)):
        add_node = node_tree.nodes.new("ShaderNodeMath")
        add_node.operation = "ADD"
        add_node.location = (x + 600 + 200 * k, y - 100 * k)
        node_tree.links.new(cumulative, add_node.inputs[0])
        node_tree.links.new(weight_sockets[k], add_node.inputs[1])
        cumulative = add_node.outputs[0]

        div_node = node_tree.nodes.new("ShaderNodeMath")
        div_node.operation = "DIVIDE"
        div_node.location = (x + 600 + 200 * k, y - 100 * k - 160)
        node_tree.links.new(weight_sockets[k], div_node.inputs[0])
        node_tree.links.new(cumulative, div_node.inputs[1])
        fac_sockets.append(div_node.outputs[0])
    return fac_sockets


def _build_mix_shader_chain(node_tree, layer_shaders, fac_sockets, x, y):
    # type: (bpy.types.NodeTree, List[object], List[object], float, float) -> object
    """
    Chains `layer_shaders` (one Shader-type socket per resolved layer, in
    the same order used to compute the factors -- see
    _compute_cumulative_fac / _bake_splat_fac_images) into a single
    blended Shader output via len(layer_shaders) - 1 Mix Shader nodes.
    layer_shaders[0] is the chain's base; each subsequent layer_shaders[k]
    mixes in using fac_sockets[k - 1] as that step's Fac.
    """
    accumulated = layer_shaders[0]
    for k in range(1, len(layer_shaders)):
        mix_node = node_tree.nodes.new("ShaderNodeMixShader")
        mix_node.location = (x, y)
        node_tree.links.new(fac_sockets[k - 1], mix_node.inputs["Fac"])
        node_tree.links.new(accumulated, mix_node.inputs[1])
        node_tree.links.new(layer_shaders[k], mix_node.inputs[2])
        accumulated = mix_node.outputs["Shader"]
        x += 400
    return accumulated


def build_hms_material(obj, heightmap, mat_name, world_scale=1.0):
    # type: (bpy.types.Object, hms.Heightmap, str, float) -> Optional[bpy.types.Material]
    """
    THE ENTRY POINT. Builds (or reuses -- same by-name dedup as every
    other material this addon builds) one material for this heightmap
    tile: a chain of Mix Shader nodes blending one complete, unmodified
    Uber shader instance per real terrain layer (see this module's
    docstring for why -- independent per-layer shading, not a shared
    shader fed pre-blended textures), assigned to `obj` as material slot
    0. Every slot after that is a debug-only slot, one per real layer,
    assigned to zero faces -- see add_debug_material_slots(). Leaves the
    object with only debug slots and no slot 0 if none of this tile's
    layers resolved to a usable material at all.

    world_scale: see _make_tiling_uvs_world_continuous() -- the scale the
    tile mesh was baked at (default 1.0 = natural size).
    """
    layer_images = resolve_layer_map_images(heightmap.layer_names)
    resolved_indices = [
        i for i, name in enumerate(heightmap.layer_names)
        if name is not None and layer_images.get(name) is not None
    ]
    if not resolved_indices:
        print("hms material %r: no layer resolved any usable texture, leaving object unmaterialed" % mat_name)
        add_debug_material_slots(obj, heightmap.layer_names, layer_images)
        return None

    # Tiling UVs: make them continuous across tiles (world-space mode only).
    try:
        _make_tiling_uvs_world_continuous(obj, world_scale)
    except Exception as exc:  # noqa: BLE001 -- never block the import on a cosmetic step
        print("hms material %r: could not make tiling UVs world-continuous (%s)" % (mat_name, exc))

    # Before either material path: every layer wrapper reads this attribute
    # by name, and a missing attribute reads as BLACK -- so it is always
    # baked (real colour map, or neutral white if APPLY_COLOR_MAP is off).
    try:
        _ensure_color_map_attribute(obj.data, heightmap)
    except Exception as exc:  # noqa: BLE001
        print("hms material %r: could not add %s (%s) -- layers using it will render dark" % (
            mat_name, COLOR_MAP_ATTRIBUTE, exc))

    if bpy.data.materials.get(mat_name) is not None:
        print(
            "hms material %r already exists -- REUSING it instead of rebuilding (a leftover from an earlier "
            "import? it may reflect old code or settings; start from a clean file for a trustworthy test)"
            % mat_name
        )
        # The reused material samples its splat factors through the
        # "hms_splat_uv" layer by name, so this object's mesh needs it too.
        try:
            _ensure_splat_uv(obj.data, heightmap)
        except Exception as exc:  # noqa: BLE001
            print("hms material %r: could not add %s to reused-material object (%s)" % (mat_name, SPLAT_UV_NAME, exc))
        obj.data.materials.append(bpy.data.materials[mat_name])
        add_debug_material_slots(obj, heightmap.layer_names, layer_images)
        return bpy.data.materials[mat_name]

    material = bpy.data.materials.new(mat_name)
    # PROTECTION: this material doesn't get a real user until
    # obj.data.materials.append() at the very end of this function -- for
    # the whole loop below, it has zero real users, exactly what a LATER
    # layer's own _append_hms_layer_template() can orphan-purge away the
    # moment it needs to dedupe (confirmed the hard way: "StructRNA of
    # type ShaderNodeTree has been removed" starting from the second
    # layer onward, once that purge actually triggered). Same landmine,
    # same fix, as every previous instance of it in this module -- just a
    # new victim this time (our own in-progress tile material) rather
    # than a per-layer material or a wrapper group.
    material.use_fake_user = True
    material.use_nodes = True
    node_tree = material.node_tree
    # use_nodes=True auto-creates a Principled BSDF + Material Output pair
    # -- keep the Output, drop the default BSDF, we're supplying our own
    # Surface input.
    output_node = next(n for n in node_tree.nodes if n.type == "OUTPUT_MATERIAL")
    for n in list(node_tree.nodes):
        if n.type == "BSDF_PRINCIPLED":
            node_tree.nodes.remove(n)

    layer_shaders = []
    layer_diffuse_colors = []
    x = -1600
    for i in resolved_indices:
        name = heightmap.layer_names[i]
        images = layer_images[name]
        layer_group = _build_layer_shader_group(images, name)
        group_instance = node_tree.nodes.new("ShaderNodeGroup")
        group_instance.node_tree = layer_group
        group_instance.location = (x, 0)
        # Matches the template's own Image Texture node width, per
        # Crunch's readability request -- Blender's standard unresized
        # width (150) unless the template uses something else, in which
        # case this is a one-line change.
        group_instance.width = 150.0
        layer_shaders.append(group_instance.outputs["Shader"])
        layer_diffuse_colors.append(group_instance.outputs["Diffuse Color"])
        x += 400

    if len(layer_shaders) == 1:
        final_shader = layer_shaders[0]
    else:
        fac_sockets = None
        try:
            fac_sockets = _build_splat_fac_sockets(
                node_tree, obj.data, heightmap, resolved_indices, mat_name, -1600, -700
            )
            print("hms material %r: splat sampled from %dx%d texture(s)" % (
                mat_name, heightmap.layer_map_width, heightmap.layer_map_depth))
        except Exception as exc:  # noqa: BLE001 -- soft-fail to the old path, never hard-block
            print("hms material %r: splat texture path failed (%s); falling back to per-vertex weights" % (mat_name, exc))

        if fac_sockets is None:
            fac_weights, out_stride = _compute_cumulative_fac(heightmap, resolved_indices)
            fac_attrs = _pack_weight_attributes(obj.data, fac_weights, out_stride, "hms_fac_%s" % mat_name)
            fac_sockets = [
                _weight_output_socket(node_tree, fac_attrs, k, -1600, -700 - 220 * k)
                for k in range(out_stride)
            ]
        final_shader = _build_mix_shader_chain(node_tree, layer_shaders, fac_sockets, x, 0)

    if DEBUG_FLAT_DIFFUSE:
        try:
            accumulated = layer_diffuse_colors[0]
            for k in range(1, len(layer_diffuse_colors)):
                mix_node = node_tree.nodes.new("ShaderNodeMix")
                mix_node.data_type = "RGBA"
                mix_node.blend_type = "MIX"
                mix_node.location = (x + 400 * k, 400)
                factor = next(sock for sock in mix_node.inputs if sock.name == "Factor" and sock.type == "VALUE")
                node_tree.links.new(fac_sockets[k - 1], factor)
                node_tree.links.new(accumulated, _rgba_socket(mix_node.inputs, "A"))
                node_tree.links.new(layer_diffuse_colors[k], _rgba_socket(mix_node.inputs, "B"))
                accumulated = next(sock for sock in mix_node.outputs if sock.name == "Result" and sock.type == "RGBA")
            emission_node = node_tree.nodes.new("ShaderNodeEmission")
            emission_node.location = (x + 400 * len(layer_diffuse_colors), 400)
            node_tree.links.new(accumulated, emission_node.inputs["Color"])
            final_shader = emission_node.outputs["Emission"]
            print("hms material %r: DEBUG_FLAT_DIFFUSE on (flat albedo, no lighting)" % mat_name)
        except Exception as exc:  # noqa: BLE001 -- debug aid only, keep the normal shader on failure
            print("hms material %r: DEBUG_FLAT_DIFFUSE failed (%s), keeping normal shading" % (mat_name, exc))

    node_tree.links.new(final_shader, output_node.inputs["Surface"])

    obj.data.materials.append(material)
    material.use_fake_user = False  # has a real user now, matches _append_template_material's own convention
    add_debug_material_slots(obj, heightmap.layer_names, layer_images)
    return material
