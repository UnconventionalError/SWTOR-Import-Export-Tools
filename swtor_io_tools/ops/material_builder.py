# <pep8 compliant>

"""
Shader/material building shared by every importer that builds native
HeroEngine SWTOR materials.

Holds DERIVED_CONFIGS (one entry per supported .mat <Derived> shader type),
get_or_create_material() and its map/palette/animated-UV helpers, plus the
image loading and default-map fallback code they depend on. Used by:

- ops/import_cha.py (the NPC/Character importer)
- ops/process_materials.py (.mat-by-name processing), which hms_materials.py
  also reaches through its _build_named_material()

Resource path resolution lives in types/shared.py, not here. This module
imports nothing from ops/import_cha.py, ops/import_gr2.py or
ops/process_materials.py, so it can be imported normally (top-level) from
any of them without creating an import cycle.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bpy

from ..types.mat import read_mat_summary
from ..types.shader_templates import ShaderTemplateError, find_main_shader_group_node, get_or_build_shader_material, has_swtor_shader_group
from ..types.shared import resolve_resource_path_multi


# ---------------------------------------------------------------------------
# Material building (spec §6)
# ---------------------------------------------------------------------------

def _load_or_get_image(resources_root, legacy_resources_root, relative_path):
    """
    Loads a .dds texture, trying resources_root then legacy_resources_root
    (see resolve_resource_path_multi()) -- per-ASSET fallback, not a
    single root decided once for the whole material: a real material has
    turned up whose own .mat file resolves fine under resources_root, but
    whose textures (under a completely different resources subtree --
    player_character/head/texture/ vs the .mat's own shaders/materials/)
    only exist under legacy_resources_root. So the two can't be assumed
    to travel together the way ops/process_materials.py's .mat-file-only
    entry points assumed for their own root_used.

    Reuses an already-loaded Blender image with the same filename if one
    exists (mirrors the dedup-by-basename convention the original
    importer used for images -- distinct from the new dedup-by-.mat-
    filename convention used for materials themselves, see
    get_or_create_material()).

    Sets alpha_mode/colorspace exactly as the old custom node's per-map
    update_*Map callbacks did for every SWTOR texture (CHANNEL_PACKED
    alpha, Non-Color colorspace) -- since assigning an image directly to
    an external Image Texture node's .image no longer goes through those
    callbacks, this has to happen here instead.
    """
    path, _root_used = resolve_resource_path_multi(resources_root, legacy_resources_root, relative_path)
    if not path:
        return None
    name = Path(path).name
    existing = bpy.data.images.get(name)
    if existing:
        return existing
    try:
        image = bpy.data.images.load(path)
    except RuntimeError:
        # Genuinely missing from both roots (Path.is_file() failed under
        # both in resolve_resource_path_multi() above) -- soft-fail:
        # callers leave the Image Texture node unassigned, same as an
        # optional map simply being absent, rather than letting this
        # abort the entire import over one missing file. Reported by
        # get_or_create_material()'s caller instead of raised here.
        return None
    image.alpha_mode = 'CHANNEL_PACKED'
    image.colorspace_settings.name = 'Non-Color'
    return image


def is_head_object_name(object_name):
    # type: (Optional[str]) -> bool
    """
    The caller-side policy for get_or_create_material()'s
    detect_skinb_invert_alpha: True if `object_name` (the Blender Object a
    material is being assigned to) looks like a head. A name heuristic, not
    a guarantee -- kept here, next to the pixel check it gates, so every
    caller uses the same one.
    """
    return bool(object_name) and "head" in object_name.lower()


def _is_first_pixel_white(image):
    """
    True if `image`'s first pixel's Red channel is pure white (1.0),
    False if pure black (0.0). Raises ValueError otherwise -- SWTOR's
    Rotation Map opacity-channel background is expected to be exactly
    one or the other by design, never something in between.

    Used to auto-detect SWTOR's "modernized" head SkinB materials, which
    use an inverted opacity convention for eyelash detail (black mask on
    white background) versus the older convention (white mask on black
    background). A fresh, non-deprecated copy of the original custom
    node's is_first_pixel_white(), used here as a one-shot check at
    import time rather than a live-updating callback (see
    get_or_create_material()'s use of it).
    """
    first_red = image.pixels[0]
    if first_red == 1.0:
        return True
    elif first_red == 0.0:
        return False
    else:
        raise ValueError(
            f"Rotation Map '{image.name}': first pixel's Red channel is "
            f"neither pure black nor pure white (value={first_red})"
        )


def _get_white_fallback_image():
    """
    Matches the old custom node's Complexion Map fallback: a plain white
    4x4 image, used so SkinB's diffuse*complexion multiply is a no-op
    when no Complexion Map is present in the json data -- a solid black
    fallback (what an empty Image Texture node would otherwise output)
    would multiply the whole diffuse color to black instead.
    """
    existing = bpy.data.images.get('white.dds')
    if existing:
        return existing
    image = bpy.data.images.new('white.dds', 4, 4)
    image.generated_color = [1.0, 1.0, 1.0, 1.0]
    return image


# Default map assets (spec §6 extension, per Crunch): auto-populated into
# an optional map's Image Texture node when a material's json data doesn't
# specify that ddsPaths key at all. Keyed by the same json_key used in
# DERIVED_CONFIGS' "maps" tuples, so this isn't SkinB-specific -- any
# derived type's optional map can get a default here (e.g. Creature's
# directionMap) just by adding an entry, no other code changes needed.
#d
# Paths are relative to the resources root, same convention as every
# other ddsPaths entry -- resolve_resource_path() normalizes slashes and
# strips a redundant leading "resources" segment regardless.
DEFAULT_MAP_ASSETS = {
    "complexionMap": "art/defaultassets/white.dds",
    "facepaintMap": "art/defaultassets/default_facepaint.dds",
    "ageMap": "art/defaultassets/default_age.dds",
}


def _load_default_map_image(resources_root, legacy_resources_root, json_key):
    """
    Loads the fallback image for an optional map key that's absent from
    a material's json ddsPaths, per DEFAULT_MAP_ASSETS above. Tries
    resources_root then legacy_resources_root, same as _load_or_get_image().

    Returns None -- same as the map simply being absent -- if json_key
    has no default asset registered, or if its default asset file isn't
    actually present under either root (e.g. Crunch hasn't dropped the
    defaultassets folder in yet); callers fall back to whatever behavior
    they had before this existed.
    """
    relative_path = DEFAULT_MAP_ASSETS.get(json_key)
    if relative_path is None:
        return None
    try:
        return _load_or_get_image(resources_root, legacy_resources_root, relative_path)
    except (OSError, RuntimeError):
        return None


def _set_palette(group_node, other_values, index, include_metallic_specular):
    """
    Sets a SWTOR shader group node's Palette<index> input socket
    defaults (e.g. "Palette1 Hue") -- Koda Shaders' naming convention,
    adopted across the board per Crunch, including single-palette
    derived types (Eye/HairC/SkinB), which still get the "Palette1 "
    prefix despite only having one palette.
    """
    prefix = "palette%d" % index
    socket_prefix = f"Palette{index} "

    other_palette = other_values[prefix]
    group_node.inputs[f'{socket_prefix}Hue'].default_value = float(other_palette[0])
    group_node.inputs[f'{socket_prefix}Saturation'].default_value = float(other_palette[1])
    group_node.inputs[f'{socket_prefix}Brightness'].default_value = float(other_palette[2])
    group_node.inputs[f'{socket_prefix}Contrast'].default_value = float(other_palette[3])

    specular = other_values[prefix + "Specular"]
    group_node.inputs[f'{socket_prefix}Specular'].default_value = [
        float(specular[0]), float(specular[1]), float(specular[2]), 1.0,
    ]

    if include_metallic_specular:
        metallic_specular = other_values[prefix + "MetallicSpecular"]
        group_node.inputs[f'{socket_prefix}Metallic Specular'].default_value = [
            float(metallic_specular[0]),
            float(metallic_specular[1]),
            float(metallic_specular[2]),
            1.0,
        ]


def _set_animated_uv_inputs(new_mat, group_node, other_values):
    """
    Wires AnimatedUV's otherValues onto both of its group nodes -- the
    main shader group (tint inputs) AND the sibling 'TransformAllUVs'
    group (per-layer UV scroll/rotation), looked up by its known fixed
    Blender node name (a SECOND top-level group node with real inputs to
    set is specific to this shader type; every other DERIVED_CONFIGS
    entry only has one, or a sibling detour group -- DirectionMapUV --
    that nothing here ever sets inputs on).

    Two real ambiguities resolved with a specific, deliberately-flagged
    choice rather than a silent guess -- confirm visually once a
    textured AnimatedUV material is actually rendered, adjust here if
    wrong:
      - animTexTint0/animTexTint2 are the .mat's own vector4 (RGBA)
        values, but feed a NodeSocketVector (3-component) input on the
        shader group -- the alpha component is dropped, only RGB used.
      - animTexUVScrollSpeed{n}/animTexUVRotationPivot{n} sockets on
        TransformAllUVs are 2-dimensional Vector sockets (confirmed via
        real testing -- NOT 3-component; an earlier version of this
        function wrongly assumed 3D and padded with Z=0, which raises
        "sequences of dimension 0 should contain 2 items, not 3" and
        crashes this whole function on its very first statement, silently
        preventing every value after it -- scroll, pivot, rotation speed,
        AND all three tints -- from ever being set). Matches the .mat's
        own genuinely-2-component "uvscale" data (U, V) exactly -- no
        padding needed at all.

    Also note: the .mat's own semantic is "animTexRotationPivot{n}", but
    TransformAllUVs' socket is named "animTexUVRotationPivot{n}" (extra
    "UV") -- a deliberate explicit mapping below, not a typo.

    Untouched, left at the template's own defaults, since neither has any
    .mat correspondence at all: TransformAllUVs' 'Animation Offset', and
    the main group's "User Controls" panel (Roughness/Emission Strength/
    Backface Culling) -- the latter's own panel name signals these are
    meant to be hand-tuned by whoever's editing the material in Blender,
    not driven by SWTOR's own material data.

    animFresnelHue0/animFresnelHue1/BloomMaterialParams are captured into
    otherValues (types/mat.py's generic Tier 2 fallback) but have no
    corresponding socket on either group yet -- same as FresnelGradient's
    own Image Texture node (present, unconnected) -- nothing to do with
    any of them here until that part of the shader is actually built.
    """
    transform_uvs_node = new_mat.node_tree.nodes.get("TransformAllUVs")
    if transform_uvs_node is None:
        raise ShaderTemplateError(
            f"'{new_mat.name}' is missing its 'TransformAllUVs' node -- "
            f"the template .blend may be corrupted or out of date."
        )

    for i in range(3):
        scroll = other_values.get(f"animTexUVScrollSpeed{i}")
        if scroll is not None:
            transform_uvs_node.inputs[f'animTexUVScrollSpeed{i}'].default_value = [float(scroll[0]), float(scroll[1])]

        pivot = other_values.get(f"animTexRotationPivot{i}")
        if pivot is not None:
            transform_uvs_node.inputs[f'animTexUVRotationPivot{i}'].default_value = [float(pivot[0]), float(pivot[1])]

        rotation_speed = other_values.get(f"animTexRotationSpeed{i}")
        if rotation_speed is not None:
            transform_uvs_node.inputs[f'animTexRotationSpeed{i}'].default_value = float(rotation_speed)

    tint0 = other_values.get("animTexTint0")
    if tint0 is not None:
        group_node.inputs['animTexTint0'].default_value = [float(tint0[0]), float(tint0[1]), float(tint0[2])]

    tint1 = other_values.get("animTexTint1")
    if tint1 is not None:
        group_node.inputs['animTexTint1'].default_value = float(tint1)

    tint2 = other_values.get("animTexTint2")
    if tint2 is not None:
        group_node.inputs['animTexTint2'].default_value = [float(tint2[0]), float(tint2[1]), float(tint2[2])]


# Per-derived-type configuration, replacing the original's per-branch node
# wiring (largely copy-pasted across ~700 lines) with a single data-driven
class UnrecognizedDerivedTypeError(Exception):
    """
    Raised by get_or_create_material() specifically when `derived` isn't
    a key DERIVED_CONFIGS covers at all (distinct from KNOWN_UNBUILT_
    DERIVED, types/mat.py -- that bucket is caught earlier, before this
    function is ever called).

    A DISTINCT exception type on purpose, not a plain ValueError: Blender
    itself raises plain ValueError for plenty of unrelated failures (e.g.
    assigning a wrongly-sized sequence to a node socket's default_value)
    -- catching bare ValueError to mean "unrecognized shader type" would
    silently mislabel a real bug as "not yet supported" instead of
    surfacing it as an actual error (confirmed real case: a 3-item list
    assigned to a 2-dimensional Vector socket in
    _set_animated_uv_inputs() got reported as "recognized but
    not-yet-supported shader type" instead of the genuine bug it was).
    ops/process_materials.py's _build_named_material() catches this
    specifically, ahead of its own broader except Exception.
    """


# function. Each "maps" tuple is (json ddsPaths key, external image node
# name, required); required maps are read unconditionally -- same as the
# original, a missing required key raises -- optional maps are only read
# if present. Node names match SWTOR_SHADER_GROUPS in shaders_menu.py.
# Each "palettes" tuple is (palette index, include a metallic_specular
# value too?).
DERIVED_CONFIGS = {
    "Creature": {
        "shader_derived": "CREATURE",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
            ("rotationMap", "rotationMap1", True),
            ("glossMap", "glossMap", True),
            ("paletteMaskMap", "paletteMaskMap", True),
            ("directionMap", "directionMap", False),
        ],
        "palettes": [],
        "flesh": True,
    },
    "Eye": {
        "shader_derived": "EYE",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
            ("rotationMap", "rotationMap1", True),
            ("glossMap", "glossMap", True),
            ("paletteMap", "paletteMap", True),
            ("paletteMaskMap", "paletteMaskMap", True),
        ],
        # Metallic Specular was never wired up in the old custom node (a
        # pre-existing bug, confirmed by Crunch, not intentional) -- now
        # fixed in eye_group(), so read it from json like every other
        # single-palette derived type.
        "palettes": [(1, True)],
        "flesh": False,
    },
    "Garment": {
        "shader_derived": "GARMENT",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
            ("rotationMap", "rotationMap1", True),
            ("glossMap", "glossMap", True),
            ("paletteMap", "paletteMap", True),
            ("paletteMaskMap", "paletteMaskMap", True),
        ],
        "palettes": [(1, True), (2, True)],
        "flesh": False,
    },
    "HairC": {
        "shader_derived": "HAIRC",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
            ("rotationMap", "rotationMap1", True),
            ("glossMap", "glossMap", True),
            ("paletteMap", "paletteMap", True),
            ("paletteMaskMap", "paletteMaskMap", True),
            ("directionMap", "directionMap", True),
        ],
        "palettes": [(1, True)],
        "flesh": False,
    },
    "SkinB": {
        "shader_derived": "SKINB",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
            ("complexionMap", "complexionMap", False),
            ("rotationMap", "rotationMap1", True),
            ("facepaintMap", "facepaintMap", False),
            ("glossMap", "glossMap", True),
            ("ageMap", "ageMap", False),
            ("paletteMap", "paletteMap", True),
            ("paletteMaskMap", "paletteMaskMap", True),
        ],
        "palettes": [(1, True)],
        "flesh": True,
    },
    "Uber": {
        "shader_derived": "UBER",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
            ("rotationMap", "rotationMap1", True),
            ("glossMap", "glossMap", True),
        ],
        "palettes": [],
        "flesh": False,
    },
    "Grass": {
        "shader_derived": "GRASS",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
            # NOT required, unlike Uber's -- real grass/foliage .mat files
            # never define a RotationMap1 at all (confirmed with Crunch),
            # and the template's own group (confirmed via real node dump)
            # still exposes RotationMap1 Color/Alpha sockets regardless --
            # left at the template's own default when absent, same as any
            # other optional map.
            ("rotationMap", "rotationMap1", False),
            ("glossMap", "glossMap", True),
        ],
        "palettes": [],
        "flesh": False,
    },
    "AnimatedUV": {
        "shader_derived": "ANIMATEDUV",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
            ("animatedTexture1", "animatedTexture1", True),
            ("animatedTexture2", "animatedTexture2", True),
            # fresnelGradient exists as a node in the template (unconnected,
            # per Crunch, ready for when the shader actually uses it) but
            # deliberately left out of "maps" -- nothing to assign it yet.
        ],
        "palettes": [],
        "flesh": False,
        # Distinct from every other type: two group nodes, and its
        # otherValues shape (UV scroll/rotation/tint, not palette/flesh)
        # doesn't fit _set_palette()'s or the flesh/flush block's shape at
        # all -- see _set_animated_uv_inputs()'s own docstring.
        "animated_uv": True,
    },
    # The next three templates (confirmed via real node dump) are minimal:
    # one diffuseMap Image Texture node feeding DiffuseMap Color/Alpha, and
    # no Alpha Blend/Alpha Test/Alpha Clip Threshold sockets at all, so
    # get_or_create_material()'s socket-existence guards skip the alpha
    # block automatically. Vegetation and NoShadeTexFogged also expose a
    # Roughness socket, EmissiveOnly doesn't -- all three are left at the
    # template's own defaults, nothing in a .mat drives them.
    "NoShadeTexFogged": {
        "shader_derived": "NOSHADETEXFOGGED",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
        ],
        "palettes": [],
        "flesh": False,
    },
    "Vegetation": {
        "shader_derived": "VEGETATION",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
        ],
        "palettes": [],
        "flesh": False,
    },
    "EmissiveOnly": {
        "shader_derived": "EMISSIVEONLY",
        "maps": [
            ("diffuseMap", "diffuseMap", True),
        ],
        "palettes": [],
        "flesh": False,
    },
}
DERIVED_CONFIGS["GarmentScrolling"] = DERIVED_CONFIGS["Garment"]

_absent_required_maps_noted = set()


def _assign_material_maps(maps, dds_paths, image_nodes, resources_root, legacy_resources_root,
                          mat_name, missing_assets, strict_required_maps):
    """
    Assigns each of a derived type's "maps" tuples (json_key, node name,
    required) its Image Texture node's image.

    A map present in `dds_paths` is loaded from there. One that is absent is
    given its DEFAULT_MAP_ASSETS fallback if it has one, else the node is left
    at the template's own default -- EXCEPT a `required` map when
    `strict_required_maps` is True, which raises KeyError (the original
    behaviour, kept for the json-driven NPC pipeline).

    strict_required_maps=False is for materials sourced straight from real
    .mat files (ops/process_materials.py): environment materials routinely
    omit a map their derived type lists as required (grass/foliage with no
    GlossMap, props with no RotationMap1, ...). Raising there made the whole
    material fail -- after its shader group had already been created -- so
    those materials/billboards were reported "not usable" or, depending on
    whether a purge happened to sweep the half-built leftover, silently used
    half-built. Missing maps are now simply left at the template default.
    """
    for json_key, node_name, required in maps:
        if json_key in dds_paths or (required and strict_required_maps):
            relative_path = dds_paths[json_key]
            image = _load_or_get_image(resources_root, legacy_resources_root, relative_path)
            if image is None and missing_assets is not None:
                missing_assets.append((mat_name, relative_path))
            image_nodes[node_name].image = image
        else:
            if required and (mat_name, json_key) not in _absent_required_maps_noted:
                _absent_required_maps_noted.add((mat_name, json_key))
                print("material %r: its .mat has no %s -- left at the shader template's default" % (mat_name, json_key))
            default_image = _load_default_map_image(resources_root, legacy_resources_root, json_key)
            if default_image is not None:
                image_nodes[node_name].image = default_image
            elif node_name == 'complexionMap':
                # Last-resort fallback, kept from the original custom
                # node's behavior: Complexion Color feeds a multiply
                # against diffuse, so an empty Image Texture node's
                # black default would render the whole material black
                # instead of leaving diffuse unchanged. Only reached if
                # DEFAULT_MAP_ASSETS' own white.dds isn't present under
                # resources_root either.
                image_nodes[node_name].image = _get_white_fallback_image()


def get_or_create_material(resources_root, legacy_resources_root, mat_name, derived, mat_info, missing_assets=None, strict_required_maps=True, detect_skinb_invert_alpha=False):
    # type: (str, Optional[str], str, str, Dict[str, Any], Optional[List[Tuple[str, str]]], bool, bool) -> Any
    """
    Returns a Blender material for the given governing .mat data, (re)
    building its node graph only if it doesn't already have one.

    mat_name is the .mat file's own basename, extension stripped (spec
    §6) -- e.g. "hair_twilek_non_a08_v02" -- replacing the original's
    "{idx} {slotName}{derived}" naming. Lookup is by name, so identical
    .mat files reused across slots or separate NPC imports collapse into
    one shared material automatically.

    Dedup is NOT a bare bpy.data.materials.get(mat_name) existence check:
    import_gr2.py's own low-level .gr2 reader unconditionally creates a
    default-BSDF placeholder material for every material name embedded
    *inside* the .gr2 file itself (see its build()'s "NOTE: Create
    Materials" step) -- entirely independent of this json-driven code.
    SWTOR .gr2 files can embed a material name that happens to exactly
    match a real .mat file's basename (confirmed real case:
    "boot_dancer01_light_ge_a21dancer01_f"), which would collide with
    our own naming/dedup convention. So existence alone isn't a signal
    that a material was actually already built by this function -- only
    the presence of our own SWTOR shader group node is (has_swtor_
    shader_group(), types/shader_templates.py). If a same-named material
    exists but wasn't built by us, get_or_build_shader_material() below
    replaces it in place (every existing user redirected onto the newly
    built material, then the placeholder deleted) rather than ever
    creating a second, differently-suffixed material for the same
    logical .mat.

    detect_skinb_invert_alpha (default False): for a SkinB material, auto-
    detects Invert Alpha from the Rotation Map's own pixel data (see
    below) -- the caller decides whether this material is a head (see
    is_head_object_name()), this function has no knowledge of Blender
    Objects. Since materials are deduped by name, this only has an effect
    the first time a given SkinB material is actually built; a shared
    material re-assigned to a second, differently-named object afterward
    keeps whatever Invert Alpha value was baked in the first time.

    legacy_resources_root is an optional second resources root (e.g.
    Crunch's "Legacy Resources Directory" -- same internal layout as a
    normal resources folder, just a different top-level location), tried
    as a fallback for EVERY asset this material references individually
    -- its own .mat file, and each .dds texture -- not a single root
    decided once from wherever the .mat file happens to resolve. A
    material's .mat file and its textures live under different resources
    subtrees (art/shaders/materials/ vs e.g.
    art/dynamic/player_character/head/texture/), so one resolving under
    resources_root is no guarantee the other does too (confirmed real
    case). May be None/falsy if no legacy directory is configured, in
    which case behavior is unchanged from before legacy support existed.

    missing_assets, if given, is a list this function appends
    (mat_name, relative_path) onto for every map that's explicitly
    present in this material's own ddsPaths/required-by-config, but whose
    file couldn't be found under EITHER root -- soft-fail (the Image
    Texture node is simply left unassigned, same as an optional map
    that's absent from json entirely), never raised, so one missing
    texture never aborts an otherwise-fine import. Callers that want this
    reported collect it and summarize at the end (see load()); passing
    None (the default) silently drops this reporting without changing
    the soft-fail behavior itself.

    strict_required_maps (default True): False tolerates a "required" map
    that is absent from ddsPaths (see _assign_material_maps()); used by
    ops/process_materials.py for .mat-sourced materials.

    mat_info is used exactly as given: any json-vs-.mat merging of
    ddsPaths is the NPC importer's own concern and happens before this
    call (ops/import_cha.py's _apply_mat_dds_overrides()).
    """
    existing = bpy.data.materials.get(mat_name)
    if existing is not None and has_swtor_shader_group(existing):
        return existing

    # "HighQualityCharacter" is an older/alternate name occasionally seen
    # for what is otherwise a Creature material.
    derived = "Creature" if derived == "HighQualityCharacter" else derived

    config = DERIVED_CONFIGS.get(derived)
    if config is None:
        raise UnrecognizedDerivedTypeError("Unrecognized derived material type: %r" % (derived,))

    other_values = mat_info.get("otherValues", {})
    dds_paths = mat_info.get("ddsPaths", {})

    # Builds fresh from HeroEngine_Shaders.blend (bundled_data/) if no usable
    # material exists yet under this name -- or, if a same-named material
    # exists but isn't one of ours (see docstring above), replaces it.
    # Either way, comes back fully wired (native group + external Image
    # Texture nodes + reroutes/detours, all pre-built in the template --
    # see types/shader_templates.py), so there's no node-clearing/
    # building step needed here anymore.
    #
    # Deliberately called BEFORE any .dds image loading below, not after
    # -- this call may internally orphan-purge (types/shader_templates.py's
    # _append_template_material(), whenever it has to dedupe a colliding
    # node group), and that purge is global: it sweeps any currently
    # zero-real-user datablock in the file, Images included. Holding an
    # Image reference across this call is unsafe (see migrate_shaders.py's
    # migrate_material(), which hit exactly this with old_images) -- safe
    # here only because nothing is loaded yet at this point. If image
    # loading is ever reordered to happen earlier (e.g. pre-fetched before
    # the material is built), it would need the same use_fake_user
    # protection migrate_material() uses.
    new_mat = get_or_build_shader_material(config["shader_derived"], mat_name)
    group_node = find_main_shader_group_node(new_mat, config["shader_derived"])
    image_nodes = {
        node.name: node for node in new_mat.node_tree.nodes
        if node.type == 'TEX_IMAGE'
    }

    # Alpha / transparency: sourced from the real .mat file's own
    # <AlphaMode>/<AlphaTestValue> (Tier 1, types/mat.py) when it can be
    # resolved, replacing this function's previous hardcoded CLIP-always
    # behavior. Jedipedia json data doesn't carry alpha settings at all,
    # so this is the only source of truth for it -- confirmed with
    # Crunch. Falls back to the old hardcoded values (CLIP, 0.5) if the
    # .mat file can't be found/parsed under EITHER resources_root or
    # legacy_resources_root, so a missing file never blocks an import.
    #
    # Deliberately does NOT run Tier 1's skip-list check here -- json
    # data is always treated as already-correct (confirmed with Crunch),
    # unlike ops/process_materials.py's materials-by-name entry points,
    # which do apply it.
    new_mat.show_transparent_back = False
    new_mat.surface_render_method = "DITHERED"

    # Only the .mat file's OWN lookup needs root_used specifically (to
    # know which root to actually read the XML from) -- everything else
    # below (every .dds lookup) tries BOTH roots per-asset via
    # _load_or_get_image()/_load_default_map_image(), rather than
    # assuming a material's textures live under whichever root its .mat
    # file happened to resolve under. Confirmed real case: a material
    # whose .mat resolves fine under resources_root, but whose actual
    # textures (a completely different resources subtree --
    # player_character/head/texture/ vs the .mat's own
    # shaders/materials/) only exist under legacy_resources_root.
    mat_path, _root_used = resolve_resource_path_multi(resources_root, legacy_resources_root, mat_info.get("matPath"))
    mat_summary = read_mat_summary(mat_path, mat_name) if mat_path is not None else None

    if mat_summary is not None:
        alpha_blend = mat_summary.alpha_blend
        alpha_test = mat_summary.alpha_test
        alpha_test_value = mat_summary.alpha_test_value
    else:
        alpha_blend = False
        alpha_test = True
        alpha_test_value = 0.5

    new_mat.alpha_threshold = alpha_test_value
    # Guarded by socket existence, not by shader type: AnimatedUV's own
    # group (confirmed via real node dump) has no Alpha Blend/Alpha Test/
    # Alpha Test Value sockets at all -- it's a fixed-blend-mode shader by
    # design (its "User Controls" panel exposes Roughness/Emission
    # Strength/Backface Culling instead), so there's nothing to set here.
    # Checking existence rather than hardcoding "skip for AnimatedUV"
    # keeps this correct automatically for any future shader type that
    # also doesn't expose these, without needing another special case.
    if 'Alpha Blend' in group_node.inputs:
        group_node.inputs['Alpha Blend'].default_value = alpha_blend
        group_node.inputs['Alpha Test'].default_value = alpha_test
        group_node.inputs['Alpha Test Value'].default_value = alpha_test_value
    # Grass's group (confirmed via real node dump) has a differently-shaped
    # alpha interface -- one "Alpha Clip Threshold" socket instead of
    # Uber's four -- rather than a variant of the same one. Guarded by
    # socket existence, same convention as the block above, so this is a
    # no-op for every other shader type.
    if 'Alpha Clip Threshold' in group_node.inputs:
        group_node.inputs['Alpha Clip Threshold'].default_value = alpha_test_value

    _assign_material_maps(
        config["maps"], dds_paths, image_nodes, resources_root, legacy_resources_root,
        mat_name, missing_assets, strict_required_maps,
    )

    # SkinB head materials: auto-detect Invert Alpha from the Rotation
    # Map's own pixel data, matching the original custom node's behavior.
    # A one-shot bake at import time rather than a live callback -- see
    # this function's docstring re: detect_skinb_invert_alpha.
    if derived == "SkinB" and detect_skinb_invert_alpha:
        rotation_image = image_nodes['rotationMap1'].image
        if rotation_image is not None:
            group_node.inputs['Invert Alpha'].default_value = _is_first_pixel_white(rotation_image)

    # Every derived type feeds "Palette<N> Hue"/"Saturation"/... sockets
    # (Koda Shaders' convention) -- Garment is the only one with two.
    for palette_index, include_metallic_specular in config["palettes"]:
        _set_palette(group_node, other_values, palette_index, include_metallic_specular)

    if config["flesh"]:
        group_node.inputs['Flesh Brightness'].default_value = float(other_values["fleshBrightness"])
        flush = other_values["flush"]
        group_node.inputs['Flush Tone'].default_value = [float(flush[0]), float(flush[1]), float(flush[2]), 1.0]

    if config.get("animated_uv"):
        _set_animated_uv_inputs(new_mat, group_node, other_values)

    return new_mat
