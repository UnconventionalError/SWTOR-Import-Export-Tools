# <pep8 compliant>

"""
Merged SWTOR NPC/Character importer created by Crunch.

Replaces the old import_cha.py (IO Scene GR2) + char_character_assembler.py
(ZG_Tools) split. Reads directly from a local "resources" folder (mirroring
the game's extracted asset layout) instead of copying assets into a
per-character folder first.

Targets Jedipedia.net's json export format exclusively.
No support for older TORC exports with none planned.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bpy
import bmesh
from mathutils import Vector, Matrix

from .import_gr2 import load as ImportGR2_load
from ..types.mat import read_mat_full, read_mat_summary
from ..types.shader_templates import ShaderTemplateError, find_main_shader_group_node, get_or_build_shader_material, has_swtor_shader_group
from ..types.shared import job_results


# ---------------------------------------------------------------------------
# Path resolution (spec §4)
# ---------------------------------------------------------------------------

def resolve_resource_path(resources_root, relative_path):
    # type: (str, Optional[str]) -> Optional[str]
    """
    Resolves a Jedipedia json path (e.g. "/art/dynamic/head/model/x.gr2")
    against a local resources root folder that contains an "art" subfolder
    directly, mirroring the game's own asset layout.

    Defensively strips:
    - a single leading slash or backslash
    - a redundant leading "resources" path segment, in case a path
      includes one (historically seen on the old meta.skeletonModel
      field, before skeleton resolution moved to the .dyc-based lookup
      in import_skeleton() -- kept as a general safety net)

    Returns None if relative_path is falsy, so callers can skip absent
    optional paths without extra checks.
    """
    if not relative_path:
        return None

    # Normalize to forward slashes regardless of which slash style the
    # json used, so splitting is consistent.
    normalized = relative_path.replace("\\", "/").lstrip("/")

    parts = normalized.split("/")
    if parts and parts[0].lower() == "resources":
        parts = parts[1:]

    return str(Path(resources_root, *parts))


def resolve_resource_path_multi(resources_root, legacy_resources_root, relative_path):
    # type: (str, Optional[str], Optional[str]) -> Tuple[Optional[str], str]
    """
    Tries relative_path (see resolve_resource_path() above -- same
    normalization, just checked against two roots instead of one) under
    resources_root first, then legacy_resources_root as a fallback if it
    doesn't exist there.

    Returns (resolved_path, root_used) -- root_used is whichever root
    the file actually turned up under, mirroring types/mat.py's
    resolve_mat_path()'s exact reasoning: callers go on to resolve this
    same material's OTHER assets (its DDS textures) against that same
    root, on the assumption that a material and everything it references
    ship together within the same resources layer. Deliberately reuses
    resolve_resource_path() for the actual path construction rather than
    reimplementing it, and deliberately does NOT switch to reconstructing
    the path from a fixed art/shaders/materials/<name>.mat convention
    (unlike resolve_mat_path()) -- this resolves the *literal* relative
    path a Jedipedia json entry provides, which isn't guaranteed to
    always match that convention.

    If relative_path can't be found under either root (including when
    relative_path itself is falsy), falls back to
    resolve_resource_path(resources_root, relative_path) unchecked and
    root_used=resources_root -- matches this project's pre-legacy-
    support behavior for that failure case, so a missing file never
    silently changes which root subsequent lookups for the same material
    are attempted against.
    """
    for root in (resources_root, legacy_resources_root):
        if not root:
            continue
        candidate = resolve_resource_path(root, relative_path)
        if candidate is not None and Path(candidate).is_file():
            return candidate, root
    return resolve_resource_path(resources_root, relative_path), resources_root


# ---------------------------------------------------------------------------
# JSON parsing (spec §3, §5)
# ---------------------------------------------------------------------------

def read(filepath):
    # type: (str) -> Tuple[Dict[str, Any], Dict[str, List[Dict]], List[Dict]]
    """
    Parses a Jedipedia-formatted NPC/PC json file.

    This is a pure parser: it doesn't touch the resources folder, doesn't
    validate that the file "looks like" a character file, and doesn't
    raise on a missing "meta" entry (current live Jedipedia exports don't
    emit one yet) — callers decide how to handle those cases.

    Returns:
        meta:
            Dict of the parsed "meta" entry's fields (charType, charName,
            nppPath, bodyType, errors). Empty dict if no "meta" entry is
            present. bodyType drives skeleton resolution -- see
            import_skeleton()'s docstring for how.

        slots:
            Dict mapping slotName -> ordered list of raw entry dicts, in
            the order they appeared in the file. A slotName with more
            than one entry represents multiple import variants (spec §5)
            — e.g. two "head" entries for two alternate head sculpts.
            The reserved "skinMats" slotName is excluded here; see
            skin_mats below.

        skin_mats:
            List of {"slot_name": ..., "mat_info": ...} dicts drawn from
            the "skinMats" entry's materialInfo.mats, flattened for easy
            lookup later (spec §6's SkinB heuristic). Empty list if the
            "skinMats" entry is absent or has no mats.
    """
    with open(filepath, encoding="utf-8") as file:
        data: List[Dict[str, Any]] = json.load(file)

    meta: Dict[str, Any] = {}
    slots: Dict[str, List[Dict]] = {}
    skin_mats: List[Dict] = []

    for entry in data:
        slot_name = entry.get("slotName")
        if not slot_name:
            # Forward-compat: entries we don't recognize (yet) are
            # skipped rather than breaking the importer.
            continue

        if slot_name == "meta":
            meta = {
                "charType": entry.get("charType"),
                "charName": entry.get("charName"),
                "nppPath": entry.get("nppPath"),
                "bodyType": entry.get("bodyType"),
                "errors": entry.get("errors", []),
            }
            continue

        if slot_name == "skinMats":
            mats = entry.get("materialInfo", {}).get("mats", [])
            for mat in mats:
                # The source shape spreads matPath (inside "materialInfo"),
                # "ddsPaths" and "otherValues" across three separate keys
                # on the same mat dict. Consolidate them into a single
                # mat_info dict shaped the same way an ordinary slot
                # entry's materialInfo is, so downstream code (§6) can
                # treat skin_mats entries and ordinary slot mat_infos
                # uniformly.
                mat_info = dict(mat.get("materialInfo", {}))
                mat_info["ddsPaths"] = mat.get("ddsPaths", {})
                mat_info["otherValues"] = mat.get("otherValues", {})
                skin_mats.append({
                    "slot_name": mat.get("slotName"),
                    "mat_info": mat_info,
                })
            continue

        slots.setdefault(slot_name, []).append(entry)

    return meta, slots, skin_mats


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
}
DERIVED_CONFIGS["GarmentScrolling"] = DERIVED_CONFIGS["Garment"]

# The subset of DERIVED_CONFIGS' "maps" json_keys that a real .mat file's
# own <input> block genuinely defines as the material's baked identity --
# these are what use_mat_dds_paths overrides from json. Everything else a
# "maps" tuple can list (complexionMap/facepaintMap/ageMap) is per-NPC
# customization data a .mat file was never meant to represent -- its own
# baked-in values for those are just generic art/defaultassets/*
# placeholders (confirmed against real head_human_bms_african_a01c01.mat:
# ComplexionMap/FacepaintMap/AgeMap all resolve to exactly this addon's
# own DEFAULT_MAP_ASSETS fallback paths, verbatim) -- always left
# json-sourced regardless of use_mat_dds_paths, same as every one of
# otherValues' palette/specular/flush/flesh values (also confirmed
# generic/non-NPC-specific in the .mat file's own data).
_MAT_SOURCED_DDS_KEYS = {
    "diffuseMap", "rotationMap", "glossMap", "paletteMap", "paletteMaskMap", "directionMap",
    "animatedTexture1", "animatedTexture2",
}


def _apply_mat_dds_overrides(mat_info, mat_path, mat_name):
    # type: (Dict[str, Any], Optional[Path], str) -> Dict[str, Any]
    """
    Returns a NEW dict (mat_info itself is never mutated -- callers may
    be sharing it, e.g. a skinMats entry reused across several objects)
    with _MAT_SOURCED_DDS_KEYS' ddsPaths entries replaced by whatever the
    real .mat file at mat_path actually defines for them (Tier 2,
    types/mat.py's read_mat_full() -- reads each <input>'s <value> field,
    NOT <variable>, which is stale/incorrect per Crunch).

    A key .mat_full doesn't define at all (e.g. directionMap on a
    non-Creature/HairC material) is left exactly as json had it --
    absence isn't itself a signal to blank out an existing json value.

    Falls back to mat_info completely unchanged if mat_path is None or
    the .mat file can't be parsed -- soft-fail, same as every other
    resolution step in this pipeline; a single material with a broken
    .mat file just reverts to pure json behavior rather than blocking
    the import.
    """
    if mat_path is None:
        return mat_info

    mat_full = read_mat_full(mat_path, mat_name)
    if mat_full is None:
        return mat_info

    merged_dds_paths = dict(mat_info.get("ddsPaths", {}))
    for key in _MAT_SOURCED_DDS_KEYS:
        if key in mat_full["ddsPaths"]:
            merged_dds_paths[key] = mat_full["ddsPaths"][key]

    merged = dict(mat_info)
    merged["ddsPaths"] = merged_dds_paths
    return merged


def get_or_create_material(resources_root, legacy_resources_root, mat_name, derived, mat_info, object_name=None, missing_assets=None, use_mat_dds_paths=True):
    # type: (str, Optional[str], str, str, Dict[str, Any], Optional[str], Optional[List[Tuple[str, str]]], bool) -> Any
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

    object_name is the Blender Object this material is being assigned to
    (see import_variant()'s call site) -- only used for SkinB's Rotation
    Map "head" Invert Alpha auto-detect below. Since materials are
    deduped by name, this only has an effect the first time a given
    SkinB material is actually built; a shared material re-assigned to a
    second, differently-named object afterward keeps whatever Invert
    Alpha value was baked in the first time.

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

    use_mat_dds_paths (default True): sources diffuseMap/rotationMap/
    glossMap/paletteMap/paletteMaskMap/directionMap from the real .mat
    file's own <input> data instead of json's ddsPaths -- see
    _apply_mat_dds_overrides()'s docstring. Exists because Jedipedia's
    NPC json export currently emits stale .dds paths for exactly these
    keys (confirmed by Crunch); the .mat file's own equivalents are not
    affected. complexionMap/facepaintMap/ageMap and every otherValues
    entry (palette/specular/flush/flesh) always come from json regardless
    of this flag -- a .mat file's own values for those are generic
    art/defaultassets/* placeholders / non-NPC-specific defaults, never
    real per-NPC customization data.
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

    # Builds fresh from Atroxa_Shaders.blend (bundled_data/) if no usable
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

    if use_mat_dds_paths:
        # Overrides diffuseMap/rotationMap/glossMap/paletteMap/
        # paletteMaskMap/directionMap with whatever the real .mat file
        # says (see _apply_mat_dds_overrides()'s own docstring for why
        # only these keys, and why <value> not <variable>) -- json
        # remains the source for complexionMap/facepaintMap/ageMap and
        # all of otherValues regardless. Off by default's inverse (the
        # operator-level "Ignore .mat Texture Paths" toggle, see
        # ImportCHA) exists specifically because Jedipedia's NPC export
        # currently emits stale .dds paths for the overridden keys.
        mat_info = _apply_mat_dds_overrides(mat_info, mat_path, mat_name)
        dds_paths = mat_info.get("ddsPaths", {})

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

    for json_key, node_name, required in config["maps"]:
        if required or json_key in dds_paths:
            relative_path = dds_paths[json_key]
            image = _load_or_get_image(resources_root, legacy_resources_root, relative_path)
            if image is None and missing_assets is not None:
                missing_assets.append((mat_name, relative_path))
            image_nodes[node_name].image = image
        else:
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

    # SkinB head materials: auto-detect Invert Alpha from the Rotation
    # Map's own pixel data, matching the original custom node's behavior.
    # A one-shot bake at import time rather than a live callback -- see
    # this function's docstring re: object_name.
    if derived == "SkinB" and object_name and "head" in object_name.lower():
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


# ---------------------------------------------------------------------------
# Material-slot-index -> json-material-data heuristics (spec §6)
# ---------------------------------------------------------------------------

def _find_skin_mat(skin_mats, slot_name):
    for skin_mat in skin_mats:
        if skin_mat.get("slot_name") == slot_name:
            return skin_mat
    return None


def resolve_material_source(entry, index, skin_mats, npc_uses_skin):
    # type: (Dict[str, Any], int, List[Dict], bool) -> Tuple[str, Dict[str, Any]]
    """
    Determines which json material data (and which "derived" shader type)
    governs a given material-slot index on an imported mesh.

    The json only ever fully describes material-slot 0 (the entry's own
    materialInfo) and, for head/creature slots, an additional eyeMatInfo
    block -- it does not say which mesh material-slot indices actually
    exist, or what a 2nd slot on non-head/creature gear represents. This
    heuristic chain is inherited from the original importer, since
    Jedipedia exports don't include "materialSkinIndex" data that would
    make this unambiguous.

    Returns (derived, mat_info).
    """
    slot_name = entry["slotName"]
    mat_info = entry["materialInfo"]
    other_values = mat_info.get("otherValues", {})
    derived = other_values.get("derived")
    derived = "Creature" if derived == "HighQualityCharacter" else derived

    if index == 1:
        if slot_name in ("head", "creature"):
            eye_mat_info = mat_info.get("eyeMatInfo", {})

            # eyeMatInfo self-describes its own type via its own
            # otherValues.derived -- confirmed against real data: a
            # genuine Eye's eyeMatInfo.otherValues.derived is "Eye"
            # (Tatooine/Balmorra heads), while a 2nd material slot that
            # is actually a second Creature material (not an eye at
            # all) has eyeMatInfo.otherValues.derived == 
            # "HighQualityCharacter" (-> Creature) instead, e.g. Malgus.
            # This replaces an earlier, less reliable heuristic (a
            # malformed paletteMap path as the signal) that was written
            # before any real "creature"-slot data was available to
            # check it against, and which only applied to slot_name ==
            # "creature", never "head" -- this direct read applies
            # equally to both, and generalizes correctly to whatever
            # eyeMatInfo actually says.
            eye_other_values = eye_mat_info.get("otherValues", {})
            eye_derived = eye_other_values.get("derived", "Eye")
            eye_derived = "Creature" if eye_derived == "HighQualityCharacter" else eye_derived

            return eye_derived, eye_mat_info

        material_skin_index = other_values.get("materialSkinIndex")
        if material_skin_index is not None:
            if int(material_skin_index) == index:
                derived = "SkinB"
        else:
            # Jedipedia doesn't currently emit materialSkinIndex at all,
            # so this operator-toggle guess is the normal path in
            # practice.
            if npc_uses_skin:
                derived = "SkinB"

    # Whenever the resolved derived is SkinB -- whether natively so on
    # the slot's own primary materialInfo (e.g. "hand", which can BE
    # skin at index 0, no override needed), or via the guess/
    # materialSkinIndex path above at index 1 -- a matching skinMats
    # entry for this slot_name, if one exists, is preferred over the
    # slot's own mat_info. Confirmed bug fix: the original importer's
    # "elif derived == 'SkinB':" branch always did this lookup
    # regardless of why derived ended up being SkinB; an earlier
    # revision of this rewrite only did it for the index-1 guess path,
    # silently using a slot's own (sometimes placeholder/wrong) palette
    # data whenever it was natively SkinB already.
    if derived == "SkinB":
        skin_mat = _find_skin_mat(skin_mats, slot_name)
        if skin_mat is not None:
            return "SkinB", skin_mat["mat_info"]

    return derived, mat_info


# ---------------------------------------------------------------------------
# Variant import (spec §5, §10's import_variant())
# ---------------------------------------------------------------------------

def _job_results_key(filepath):
    """
    Mirrors import_gr2.py's own key-normalization for
    job_results['files_objs_names'], so lookups against it line up. See
    that module's load() for the authoritative version of this logic.
    """
    normalized = filepath.replace("\\", "/")
    if "resources" in normalized:
        return normalized.partition("resources/")[2]
    return normalized


def import_variant(operator, context, resources_root, legacy_resources_root, entry, skin_mats, missing_assets=None, use_mat_dds_paths=True):
    # type: (Any, Any, str, Optional[str], Dict[str, Any], List[Dict], Optional[List[Tuple[str, str]]], bool) -> List[Any]
    """
    Imports every model listed in a single slot entry (spec §5's "one
    variant") and assigns materials to every resulting Blender object --
    a single .gr2 can produce more than one object (multi-mesh files),
    and all of them get materials now, not just the first one (confirmed
    fix; the original importer only handled the first).

    missing_assets and use_mat_dds_paths are passed straight through to
    get_or_create_material() -- see its own docstring for both.

    Returns the list of imported Objects. Empty if entry["models"] is
    empty -- a valid "none" variant (e.g. a bald/clean-shaven option),
    not an error.
    """
    imported_objects = []

    for model_path in entry.get("models", []):
        resolved_path = resolve_resource_path(resources_root, model_path)

        # Rich results are required so we can look up exactly which
        # object(s) this specific import call produced -- a plain
        # bpy.data.objects[name] guess breaks whenever two variants
        # share the same source .gr2 filename (confirmed real case:
        # multiple head sculpts reusing one mesh with different
        # materials).
        operator.job_results_rich = True

        ImportGR2_load(operator, context, resolved_path)

        object_names = job_results.get("files_objs_names", {}).get(
            _job_results_key(resolved_path), []
        )

        for object_name in object_names:
            ob = bpy.data.objects[object_name]
            imported_objects.append(ob)

            for index in range(len(ob.material_slots)):
                derived, mat_info = resolve_material_source(
                    entry, index, skin_mats, operator.npc_uses_skin,
                )
                mat_path = mat_info.get("matPath")
                if not mat_path:
                    continue

                mat_name = Path(mat_path).stem
                material = get_or_create_material(
                    resources_root, legacy_resources_root, mat_name, derived, mat_info,
                    object_name=ob.name, missing_assets=missing_assets, use_mat_dds_paths=use_mat_dds_paths,
                )
                ob.material_slots[index].material = material

    return imported_objects


# ---------------------------------------------------------------------------
# Skeleton import + binding (spec §8)
# ---------------------------------------------------------------------------

def _parse_dyc_skeleton(dyc_path):
    # type: (str) -> Optional[str]
    """
    Extracts the "Skeleton=..." filename from a .dyc file's [SETTINGS]
    section (e.g. "Skeleton=bmnnew_skeleton.gr2" -> "bmnnew_skeleton.gr2").

    .dyc files are INI-like, but this is a hand-rolled line scan rather
    than configparser: other sections (e.g. [MASKS]) contain repeated
    keys ("mask=...") and unlabeled indented sub-entries that aren't
    valid strict INI and would trip configparser's stricter parsing long
    before ever reaching [SETTINGS].

    Returns the skeleton filename, or None if not found.
    """
    current_section = None

    with open(dyc_path, encoding="utf-8", errors="replace") as file:
        for line in file:
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped[1:-1].upper()
                continue

            if current_section == "SETTINGS" and "=" in stripped:
                key, _, value = stripped.partition("=")
                if key.strip().lower() == "skeleton":
                    return value.strip()

    return None


def import_skeleton(operator, context, resources_root, meta):
    # type: (Any, Any, str, Dict[str, Any]) -> Optional[Any]
    """
    Imports the skeleton for this NPC, resolved via meta["bodyType"]
    rather than a direct path in the json.

    This moved server-side (Jedipedia) -> local resolution: Jedipedia
    can't reliably determine the actual skeleton file server-side, but
    it's straightforward locally, in two steps:

        1. "/art/dynamic/spec/<bodyType>.dyc" is read directly (e.g.
           "bmn.dyc" for bodyType "bmn"). This is an INI-like file whose
           [SETTINGS] section has a "Skeleton=<filename>.gr2" line
           giving the actual skeleton filename (e.g.
           "bmnnew_skeleton.gr2") -- confirmed this filename does NOT
           always match "<bodyType>_skeleton.gr2" or similar, hence
           needing to actually read it rather than guessing a pattern.
        2. That filename lives in the same /art/dynamic/spec/ directory
           as the .dyc file itself.

    Tolerant by design: a missing/empty bodyType, a missing .dyc file, a
    .dyc with no [SETTINGS] Skeleton= line, or a skeleton path that
    doesn't resolve to an existing file, is not an error -- it just
    means no skeleton gets imported (spec §8: "should be able to handle
    skeletons but not require them").

    Returns the imported armature Object, or None.
    """
    body_type = meta.get("bodyType")
    if not body_type:
        return None

    dyc_path = resolve_resource_path(resources_root, "/art/dynamic/spec/%s.dyc" % body_type)
    if not dyc_path or not Path(dyc_path).exists():
        operator.report(
            {'WARNING'},
            "Skeleton definition file not found, skipping: %s" % (dyc_path or body_type,),
        )
        return None

    skeleton_filename = _parse_dyc_skeleton(dyc_path)
    if not skeleton_filename:
        operator.report(
            {'WARNING'},
            "No [SETTINGS] Skeleton= entry found in %s, skipping." % (dyc_path,),
        )
        return None

    resolved_path = resolve_resource_path(resources_root, "/art/dynamic/spec/%s" % skeleton_filename)
    if not resolved_path or not Path(resolved_path).exists():
        operator.report(
            {'WARNING'},
            "Skeleton file not found, skipping: %s" % (resolved_path or skeleton_filename,),
        )
        return None

    operator.job_results_rich = True
    ImportGR2_load(operator, context, resolved_path)

    object_names = job_results.get("files_objs_names", {}).get(
        _job_results_key(resolved_path), []
    )
    if not object_names:
        return None

    # A skeleton .gr2 is expected to produce a single armature object;
    # if it somehow produces more, the first is treated as the main one
    # (same "first object is main" assumption used elsewhere in this
    # add-on for multi-object imports).
    skeleton_ob = bpy.data.objects[object_names[0]]
    skeleton_ob.show_in_front = True

    return skeleton_ob


def bind_objects_to_armature(objects, armature, single_armature_only=True):
    # type: (List[Any], Any, bool) -> None
    """
    Binds a set of objects to an armature via Armature modifiers, ported
    unchanged from the original ZG_Tools assembler.

    :param objects: list of Objects to bind
    :param armature: the armature Object
    :param single_armature_only: skip objects that already have an
        Armature modifier, rather than adding a second one
    """
    for obj in objects:
        if obj.type != 'MESH':
            continue

        if single_armature_only and any(mod.type == 'ARMATURE' for mod in obj.modifiers):
            continue

        mod = obj.modifiers.new(name="Armature", type='ARMATURE')
        mod.object = armature

        # Create a parent relationship. Don't use a parent_type of
        # 'ARMATURE' -- the modifier above is already doing that job.
        obj.parent = armature
        obj.matrix_parent_inverse = armature.matrix_world.inverted()
        
        # Enable Preserve Volume on Armature Modifier by Default
        obj.modifiers["Armature"].use_deform_preserve_volume = True

        # Automatic weight assignment needs the operator context
        # (bpy.ops.object.parent_set(type='ARMATURE_AUTO')) and isn't
        # done here; weights are assumed to be assigned manually or via
        # another process.


# ---------------------------------------------------------------------------
# Collection assembly (spec §7)
# ---------------------------------------------------------------------------

def assemble_collections(context, npc_name, slot_objects, skeleton_ob=None, character_name_suffix=None):
    # type: (Any, str, Dict[str, List[Any]], Optional[Any], Optional[str]) -> Any
    """
    Builds the NPC's Collection hierarchy:

        <NPC Collection>              # named from npc_name
         |-- <SlotName>                # one per slot that produced objects
         |    |-- variant object 1     # (capitalized -- "head" -> "Head")
         |    `-- variant object 2
         |-- ...
         |-- <creature slot's object(s)>  # directly in the NPC Collection,
         |                                # no "Creature" sub-collection --
         |                                # it's effectively the whole NPC
         `-- <skeleton object>        # directly in the NPC Collection, if any

    slot_objects: dict mapping slotName -> list of imported Objects for
    that slot. A slot with no objects (an all-empty-models variant, e.g.
    a bald/clean-shaven "none" option -- spec §5) is simply skipped, no
    empty Collection is created for it.

    character_name_suffix: if given, appended to every per-slot
    Collection's name as " - <suffix>" (e.g. "Boot" -> "Boot - Republic
    Officer"). Only meant to be passed when the "Append Character Name
    to Collections" import option is on AND the json's meta.charName is
    actually present -- callers decide that, this function just appends
    whatever string it's given (or nothing, if None). Not applied to the
    NPC root Collection itself, since that's already named from npc_name.

    Every Collection is created fresh via bpy.data.collections.new()
    rather than looked up/reused by name -- seebpy.data.collections is a
    global namespace, not scoped by nesting; reusing an existing
    same-named Collection across two different NPC imports in the same
    file would merge their objects together, which is never wanted here.
    Letting Blender auto-suffix duplicate names (".001", ".002", ...) is
    the safe default.

    Returns the NPC root Collection.
    """
    npc_collection = bpy.data.collections.new(npc_name)
    context.scene.collection.children.link(npc_collection)

    for slot_name, objects in slot_objects.items():
        if not objects:
            continue

        # The "creature" slot is effectively the whole NPC on
        # creature-type imports (e.g. Malgus) -- it doesn't get its own
        # sub-collection, its objects go straight into the NPC root,
        # same as the skeleton.
        target_collection = npc_collection
        if slot_name != "creature":
            collection_name = slot_name.capitalize()
            if character_name_suffix:
                collection_name = "%s - %s" % (collection_name, character_name_suffix)
            target_collection = bpy.data.collections.new(collection_name)
            npc_collection.children.link(target_collection)

        for ob in objects:
            target_collection.objects.link(ob)
            # ImportGR2_load() links newly created objects into whatever
            # collection is active in the view layer at import time --
            # remove that link now that the object has its proper home.
            for existing_collection in list(ob.users_collection):
                if existing_collection is not target_collection:
                    existing_collection.objects.unlink(ob)

    if skeleton_ob is not None:
        npc_collection.objects.link(skeleton_ob)
        for existing_collection in list(skeleton_ob.users_collection):
            if existing_collection is not npc_collection:
                existing_collection.objects.unlink(skeleton_ob)

    return npc_collection


# ---------------------------------------------------------------------------
# Twi'lek eyes UV fix + eye separation (spec §9, ported from ZG_Tools)
# ---------------------------------------------------------------------------

def translate_uv_coordinates(mesh_object, material_slot=None, uv_offset=(0, 0)):
    """
    Offsets an object's polys' UVs, either all of them or only those
    associated with a specific material slot. Motivated by Twi'lek
    heads' off-image-bounds eye UVs producing black bakes.

    Ported unchanged from the original ZG_Tools assembler.
    """
    if mesh_object.type != 'MESH':
        return

    mesh = mesh_object.data
    if not mesh.uv_layers.active:
        return

    uv_layer = mesh.uv_layers.active.data

    for face in mesh.polygons:
        if material_slot is None or face.material_index == material_slot:
            for loop_index in face.loop_indices:
                uv = uv_layer[loop_index].uv
                if uv.y <= 1:
                    return
                uv.x += uv_offset[0]
                uv.y += uv_offset[1]


def duplicate_obj(obj_or_obj_name):
    """
    Creates a full duplicate of an object (mesh data, transforms,
    modifiers, constraints, animation data, custom properties), linked
    into the same Collection(s) as the original.

    Ported unchanged from the original ZG_Tools assembler.
    """
    if isinstance(obj_or_obj_name, str):
        obj = bpy.data.objects[obj_or_obj_name]
    else:
        obj = obj_or_obj_name

    new_data = obj.data.copy()
    new_obj = bpy.data.objects.new(obj.name + ".copy", new_data)

    for col in obj.users_collection:
        col.objects.link(new_obj)

    new_obj.location = obj.location.copy()
    new_obj.rotation_euler = obj.rotation_euler.copy()
    new_obj.rotation_quaternion = obj.rotation_quaternion.copy()
    new_obj.scale = obj.scale.copy()
    new_obj.data = new_data

    for prop in obj.keys():
        if prop != "_RNA_UI":
            new_obj[prop] = obj[prop]

    for mod in obj.modifiers:
        new_mod = new_obj.modifiers.new(name=mod.name, type=mod.type)
        for attr in dir(mod):
            if not attr.startswith("_") and attr not in {"type", "name", "rna_type"}:
                try:
                    setattr(new_mod, attr, getattr(mod, attr))
                except AttributeError:
                    pass

        if mod.type == 'ARMATURE':
            new_mod.object = mod.object

    if obj.animation_data:
        new_obj.animation_data_create()
        new_obj.animation_data.action = obj.animation_data.action
        new_obj.animation_data.action = obj.animation_data.action.copy()

    for constr in obj.constraints:
        new_constr = new_obj.constraints.new(type=constr.type)
        for attr in dir(constr):
            if not attr.startswith("_") and attr not in {"type", "name", "rna_type"}:
                try:
                    setattr(new_constr, attr, getattr(constr, attr))
                except AttributeError:
                    pass

    return new_obj


def separate_obj_by_specific_materials(obj_or_obj_name, material_names, separate=True):
    """
    Separates an object's polys associated with the given material(s)
    into a new object per material. By default, also deletes those
    polys and material slots from the original object.

    :param material_names: a material name, or a list of material names.

    Ported from the original ZG_Tools assembler, with one clarity fix:
    the original accepted a single material name string and tested
    membership via Python's substring-`in` on that string, despite its
    own docstring describing it as "a list of materials names" --
    happened to work for how it was actually called (the exact same
    string on both sides of `in`), but is genuinely a list-membership
    check now, matching the docstring.
    """
    if isinstance(material_names, str):
        material_names = [material_names]

    if isinstance(obj_or_obj_name, str):
        original_obj = bpy.data.objects[obj_or_obj_name]
    else:
        original_obj = obj_or_obj_name

    original_mesh = original_obj.data
    new_objs = []

    mat_indices = [
        i for i, mat in enumerate(original_mesh.materials) if mat.name in material_names
    ]

    if separate:
        for mat_index in mat_indices:
            new_obj = original_obj.copy()
            new_obj.data = original_obj.data.copy()
            bpy.context.collection.objects.link(new_obj)
            new_obj.name = "%s_%s" % (original_obj.name, original_mesh.materials[mat_index].name)

            bm = bmesh.new()
            bm.from_mesh(new_obj.data)
            faces_to_delete = [f for f in bm.faces if f.material_index != mat_index]
            bmesh.ops.delete(bm, geom=faces_to_delete, context='FACES')
            bm.to_mesh(new_obj.data)
            bm.free()

            new_objs.append(new_obj)

    bm = bmesh.new()
    bm.from_mesh(original_mesh)
    faces_to_delete = [f for f in bm.faces if f.material_index in mat_indices]
    bmesh.ops.delete(bm, geom=faces_to_delete, context='FACES')
    bm.to_mesh(original_mesh)
    bm.free()

    for i, slot in enumerate(original_obj.material_slots[:]):
        if slot.material and slot.material.name in material_names:
            original_obj.data.materials.pop(index=i)

    return new_objs


def delete_polygons_on_side(obj_or_obj_name, side='LEFT'):
    """
    Deletes every polygon on the given side of local X == 0.

    Ported unchanged from the original ZG_Tools assembler.
    """
    if isinstance(obj_or_obj_name, str):
        obj = bpy.data.objects[obj_or_obj_name]
    else:
        obj = obj_or_obj_name

    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)

    faces_to_delete = []
    for face in bm.faces:
        center = face.calc_center_median()
        if side == 'LEFT' and center.x < 0:
            faces_to_delete.append(face)
        elif side == 'RIGHT' and center.x >= 0:
            faces_to_delete.append(face)

    bmesh.ops.delete(bm, geom=faces_to_delete, context='FACES')
    bm.to_mesh(mesh)
    bm.free()


def get_highest_and_lowest_vertices(obj_or_obj_name):
    """
    Returns (highest_vertex, lowest_vertex) world-space coordinates by Z.

    Ported unchanged from the original ZG_Tools assembler.
    """
    if isinstance(obj_or_obj_name, str):
        obj = bpy.data.objects[obj_or_obj_name]
    else:
        obj = obj_or_obj_name

    bpy.ops.object.mode_set(mode='OBJECT')

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    global_matrix = obj.matrix_world
    highest_vertex = None
    lowest_vertex = None

    for v in bm.verts:
        global_coord = global_matrix @ v.co
        if highest_vertex is None or global_coord.z > highest_vertex.z:
            highest_vertex = global_coord
        if lowest_vertex is None or global_coord.z < lowest_vertex.z:
            lowest_vertex = global_coord

    bm.free()

    return highest_vertex, lowest_vertex


def set_obj_origin(obj_or_obj_name, new_origin):
    """
    Moves an object's origin to new_origin (world space), preserving its
    visual position by compensating the mesh data's transform.

    Ported unchanged from the original ZG_Tools assembler.
    """
    if isinstance(obj_or_obj_name, str):
        obj = bpy.data.objects[obj_or_obj_name]
    else:
        obj = obj_or_obj_name

    new_origin = Vector(new_origin)

    obj_matrix = obj.matrix_world
    current_origin_world = obj_matrix @ Vector((0, 0, 0))
    delta_world = new_origin - current_origin_world
    delta_object_space = obj_matrix.inverted() @ delta_world

    obj.location += delta_world
    obj.data.transform(Matrix.Translation(-delta_object_space))
    obj.data.update()


def apply_twilek_eyes_uv_fix(slot_objects):
    # type: (Dict[str, List[Any]]) -> None
    """
    Applies translate_uv_coordinates() to every Twi'lek head object's
    Eye material slot (index 1), in place.

    Deviates from the original in one way: the original stopped after
    the first Twi'lek head object found across the whole NPC. Now that
    a single NPC can have multiple head variants (spec §5), this applies
    to every matching head variant instead.
    """
    for head_ob in slot_objects.get("head", []):
        if "head_twilek" in head_ob.name:
            translate_uv_coordinates(head_ob, 1, (0, -2))


def separate_head_eyes(slot_objects, separate_each_eye):
    # type: (Dict[str, List[Any]], bool) -> None
    """
    For every head object with an Eye material in its 2nd material slot,
    splits the eyes out into their own object (or two objects, one per
    eye, if separate_each_eye is set) and appends them into
    slot_objects["head"] so they end up correctly placed by
    assemble_collections().

    Iterates over a snapshot of slot_objects["head"] rather than the
    live list, since new eye objects get appended into that same list
    as this runs.
    """
    for head_ob in list(slot_objects.get("head", [])):
        if len(head_ob.material_slots) <= 1:
            continue
        if "eye" not in head_ob.material_slots[1].name.lower():
            continue

        eyes_ob = duplicate_obj(head_ob)

        # Delete the eyes' polys/material from the head object, and the
        # head's polys/material from the eyes object (the duplicate).
        separate_obj_by_specific_materials(
            head_ob, head_ob.material_slots[1].name, separate=False,
        )
        separate_obj_by_specific_materials(
            eyes_ob, eyes_ob.material_slots[0].name, separate=False,
        )

        if not separate_each_eye:
            eyes_ob.name = "%s.eyes" % head_ob.name
            slot_objects.setdefault("head", []).append(eyes_ob)
            continue

        eyes_ob_right = eyes_ob
        eyes_ob_left = duplicate_obj(eyes_ob)

        delete_polygons_on_side(eyes_ob_right, side='LEFT')
        highest_vertex, lowest_vertex = get_highest_and_lowest_vertices(eyes_ob_right)
        set_obj_origin(
            eyes_ob_right,
            (highest_vertex.x, highest_vertex.y, (highest_vertex.z + lowest_vertex.z) / 2),
        )
        eyes_ob_right.name = "%s.eyes.right" % head_ob.name
        slot_objects.setdefault("head", []).append(eyes_ob_right)

        delete_polygons_on_side(eyes_ob_left, side='RIGHT')
        highest_vertex, lowest_vertex = get_highest_and_lowest_vertices(eyes_ob_left)
        set_obj_origin(
            eyes_ob_left,
            (highest_vertex.x, highest_vertex.y, (highest_vertex.z + lowest_vertex.z) / 2),
        )
        eyes_ob_left.name = "%s.eyes.left" % head_ob.name
        slot_objects.setdefault("head", []).append(eyes_ob_left)


# ---------------------------------------------------------------------------
# Orchestrator + Operator (spec §10)
# ---------------------------------------------------------------------------

def load(operator, context, filepath=""):
    # type: (Any, Any, str) -> bool
    """
    Does the actual work of importing a Jedipedia-formatted NPC/Character
    json file: parse -> import every slot's variants -> optionally import
    a skeleton -> assemble Collections -> optionally bind to skeleton.

    Returns True on success, False on failure (mirrors import_gr2.py's
    own load() return convention).
    """
    # Deferred import -- see ops/process_materials.py's own module
    # docstring re: the import_gr2 <-> process_materials <-> import_cha
    # import cycle; a top-level import here would risk the same cycle.
    from .process_materials import get_resources_dirs
    resources_root, legacy_resources_root = get_resources_dirs()

    if not resources_root or not Path(resources_root).is_dir():
        operator.report(
            {'ERROR'},
            "Set a valid Resources Folder in IO Scene GR2's add-on preferences first.",
        )
        return False

    try:
        meta, slots, skin_mats = read(filepath)
    except (OSError, json.JSONDecodeError) as exc:
        operator.report({'ERROR'}, "Couldn't read %s: %s" % (filepath, exc))
        return False

    if not slots:
        operator.report({'WARNING'}, "No recognizable slot entries found in this file.")
        return False

    npc_name = meta.get("charName") or Path(filepath).stem

    job_results['job_origin'] = operator.bl_idname
    if not operator.job_results_accumulate:
        job_results['objs_names'] = []
        job_results['files_objs_names'] = {}

    # Local to this one load() call, deliberately NOT part of job_results
    # -- that dict is a stable external contract (serialized to
    # bpy.context.scene.swtor_io_last_job for third-party addons, shared/
    # reset across import_gr2.py/import_area.py/import_fxspec.py too), so
    # mixing this NPC-import-specific reporting into it risks a stale
    # list leaking into an unrelated pipeline's own reported output.
    missing_assets = []

    slot_objects = {}
    for slot_name, entries in slots.items():
        objects_for_slot = []
        for entry in entries:
            objects_for_slot.extend(
                import_variant(
                    operator, context, resources_root, legacy_resources_root, entry, skin_mats,
                    missing_assets=missing_assets, use_mat_dds_paths=not operator.ignore_mat_dds_paths,
                )
            )
        slot_objects[slot_name] = objects_for_slot

    skeleton_ob = None
    if operator.import_skeleton:
        skeleton_ob = import_skeleton(operator, context, resources_root, meta)

    if operator.correct_twilek_eyes_uv:
        apply_twilek_eyes_uv_fix(slot_objects)

    if operator.separate_eyes:
        separate_head_eyes(slot_objects, operator.separate_each_eye)

    assemble_collections(
        context,
        npc_name,
        slot_objects,
        skeleton_ob,
        character_name_suffix=meta.get("charName") if operator.append_character_name_to_collections else None,
    )

    if operator.bind_to_skeleton and skeleton_ob is not None:
        all_objects = [ob for objects in slot_objects.values() for ob in objects]
        bind_objects_to_armature(all_objects, skeleton_ob)

    if missing_assets:
        # Soft-fail summary, not a crash: every listed texture was left
        # unassigned on its Image Texture node (see get_or_create_
        # material()'s docstring) rather than aborting the import.
        #
        # operator.report() alone isn't reliably visible in the System
        # Console (only Blender's own Info log/status bar) -- matches
        # ops/migrate_shaders.py's and ops/process_materials.py's own
        # "[SWTOR <module>]"-prefixed print() convention for exactly
        # this reason. The in-UI report stays a short preview (first 5,
        # "+N more") so it doesn't get unreadably long in the status
        # bar; the console gets the full per-asset list instead, since
        # that's where anyone actually tracking down missing files would
        # look anyway.
        preview = ", ".join(f"'{mat_name}': {rel_path}" for mat_name, rel_path in missing_assets[:5])
        remainder = len(missing_assets) - 5
        if remainder > 0:
            preview += f", and {remainder} more"
        operator.report(
            {'WARNING'},
            f"Imported '{npc_name}', but {len(missing_assets)} texture(s) couldn't be found "
            f"under either Resources Directory -- left unassigned: {preview}",
        )

        print(f"[SWTOR import_cha] Imported '{npc_name}', but {len(missing_assets)} texture(s) couldn't be found under either Resources Directory:")
        for mat_name, rel_path in missing_assets:
            print(f"[SWTOR import_cha]   - '{mat_name}': {rel_path}")

    return True


class ImportCHA(bpy.types.Operator):
    """
    Import a Jedipedia.net-formatted NPC/Character json file.

    Reads .gr2, .mat, and .dds assets directly from the Resources Folder
    configured in this add-on's Preferences -- no separate asset
    gathering/copying step is needed.
    """
    bl_idname = "import_mesh.gr2_json"  # DO NOT CHANGE -- external tools call this by name
    bl_label = "Import SWTOR NPC/Character (.json)"
    bl_description = "Import a Jedipedia.net-formatted NPC/Character .json file"
    bl_options = {'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    append_character_name_to_collections: bpy.props.BoolProperty(
        name="Append Name to Collections",
        description=(
            "Appends the character's name to every per-slot Collection, "
            "e.g. \"Boot\" -> \"Boot - Republic Officer\".\n\n"
            "Only takes effect if the json's meta data actually includes "
            "a character name -- has no effect otherwise"
        ),
        default=False,
    )

    npc_uses_skin: bpy.props.BoolProperty(
        name="Gear Uses Skin",
        description=(
            "When importing a non-Creature-type characters, assume that any 2nd "
            "Material Slot in armor or clothes is skin rather than "
            "garment.\n\n"
            "Typical case actually needing this: revealing clothing."
        ),
        default=True,
    )

    ignore_mat_dds_paths: bpy.props.BoolProperty(
        name="Ignore .mat Texture Paths",
        description=(
            "Sources Diffuse/Rotation/Gloss/Palette/PaletteMask/Direction "
            "maps directly from the json's own .dds paths, instead of each "
            "material's real .mat file.\n\n"
            "Off by default -- Jedipedia's NPC export currently emits "
            "stale .dds paths for these specific maps. Enable this once "
            "that's fixed to go back to reading them straight from json.\n\n"
            "Complexion/Facepaint/Age maps and all hue/palette values "
            "always come from json regardless of this setting."
        ),
        default=False,
    )

    import_skeleton: bpy.props.BoolProperty(
        name="Import Skeleton",
        description="Imports the skeleton referenced by the json file's meta data, if present",
        default=False,  # seeded from add-on Preferences in invoke()
    )

    bind_to_skeleton: bpy.props.BoolProperty(
        name="Bind To Skeleton",
        description="Binds all imported objects to the imported skeleton via Armature modifiers",
        default=False,  # seeded from add-on Preferences in invoke()
    )

    correct_twilek_eyes_uv: bpy.props.BoolProperty(
        name="Correct Twi'lek Eyes UV",
        description="Corrects a known UV offset issue on Twi'lek eyes",
        default=True,
    )

    separate_eyes: bpy.props.BoolProperty(
        name="Separate Eyes",
        description="Separates the eyes from the head object into their own object, useful for rigging",
        default=False,
    )

    separate_each_eye: bpy.props.BoolProperty(
        name="Separate Eyes Individually",
        description=(
        "When separating eyes, makes each eye its own object with its own origin.\n\n"
        "Requires \"Separate Eyes\" to be enabled as well."
        ),
        default=False,
    )

    job_results_rich: bpy.props.BoolProperty(
        name="Rich Results Info",
        options={'HIDDEN'},
        default=False,
    )

    job_results_accumulate: bpy.props.BoolProperty(
        name="Accumulate Jobs' Results Info",
        options={'HIDDEN'},
        default=True,
    )

    # Not exposed in the panel, and always False: import_gr2.py's load()
    # reads this attribute unconditionally before it even checks
    # bl_idname to decide where the *other* import settings come from,
    # so it has to exist regardless. Unlike import_collision/
    # name_as_filename/etc. (genuinely decorative for this operator,
    # since those are always sourced from add-on Preferences instead),
    # this one isn't optional to declare.
    enforce_neutral_settings: bpy.props.BoolProperty(
        options={'HIDDEN'},
        default=False,
    )

    def invoke(self, context, event):
        prefs = bpy.context.preferences.addons["swtor_io_tools"].preferences
        self.import_skeleton = prefs.gr2_import_skeleton_default
        self.bind_to_skeleton = prefs.gr2_bind_to_skeleton_default
        self.append_character_name_to_collections = prefs.gr2_append_character_name_to_collections_default
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if load(self, context, filepath=self.filepath):
            return {'FINISHED'}
        return {'CANCELLED'}