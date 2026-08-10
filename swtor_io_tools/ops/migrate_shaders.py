# ============================================================================
# MIGRATION BRIDGE -- ShaderNodeHeroEngine (deprecated) -> Atroxa Shader
# native node groups (current)
#
# Converts a material's old custom-Python-node SWTOR shader
# (ShaderNodeHeroEngine, types/node_deprecated.py) to the equivalent
# native ShaderNodeGroup-based "Atroxa Shader" (built by
# ops/shaders_menu.py's build_swtor_shader_material()), in place -- same
# material name/identity, node graph swapped internally.
#
# This file is entirely dependent on the deprecated system: it reads
# ShaderNodeHeroEngine instances that only exist because that system
# does. Once ShaderNodeHeroEngine is gone there is nothing left to
# migrate FROM, so this file is deleted alongside it -- see
# __init__.py's "DEPRECATED SYSTEM" block, which this file has been
# added to.
#
# Deliberately self-contained: does NOT import from ops/import_cha.py.
# import_cha.py is active, in-use code with no relationship to the
# deprecated system -- keeping this file free of any dependency on it
# means it can be deleted the day the deprecated system goes, without
# having to check whether doing so breaks anything else, or touch
# import_cha.py at all. Per Crunch, this holds even where the two files'
# solutions are shaped alike (default map assets, palette writing) --
# duplication is the accepted cost of that isolation.
#
# DELETE THIS FILE alongside:
#   - types/node_deprecated.py
#   - types/node_tree_deprecated.py
#   - ops/shaders_menu_deprecated.py
# and the "DEPRECATED SYSTEM" block in __init__.py, once that system's
# support window ends.
# ============================================================================

# <pep8 compliant>

from pathlib import Path
from typing import List, NamedTuple

import bpy

from ..types.node_deprecated import CREATURE, EYE, GARMENT, HAIRC, SKINB, UBER
from .shaders_menu import build_swtor_shader_material


# ---------------------------------------------------------------------------
# Default map assets -- loaded in place of an optional map that's absent
# on the old node, so the new group doesn't end up with a live-but-empty
# Image Texture node overriding its own (correct, no-op) socket default
# with black. Mirrors import_cha.py's DEFAULT_MAP_ASSETS of the same
# name, deliberately duplicated rather than imported -- see module
# docstring. Paths relative to the resources root, same convention as
# every other dds path in this addon.
#
# Keyed by node_name (not the old node's attr_name), matching how every
# other lookup in this file identifies a map -- see MIGRATE_CONFIGS below.
#
# These are the smaller, actual game-default assets (confirmed working
# by Crunch), not the larger player-customization textures of the same
# apparent purpose -- .mat files reference these specifically.
#
# HACK: DirectionMap's real in-game default is art/defaultassets/flat_n.dds
# (a flat/neutral normal map) -- black.dds is used instead because it's
# smaller and Crunch found no visible difference between the two in
# testing. If that ever stops holding true, swap the path below back to
# "art/defaultassets/flat_n.dds".
DEFAULT_MAP_ASSETS = {
    "complexionMap": "art/defaultassets/white.dds",
    "facepaintMap": "art/defaultassets/default_facepaint.dds",
    "ageMap": "art/defaultassets/default_age.dds",
    "directionMap": "art/defaultassets/black.dds",
}


def _resolve_resource_path(resources_root, relative_path):
    # type: (str, Optional[str]) -> Optional[str]
    """
    Local copy of import_cha.py's resolve_resource_path() -- deliberately
    duplicated rather than imported, see module docstring.

    Resolves a relative asset path against a local resources root folder
    that contains an "art" subfolder directly, mirroring the game's own
    asset layout. Defensively strips a single leading slash/backslash and
    a redundant leading "resources" path segment.

    Returns None if relative_path is falsy, so callers can skip absent
    optional paths without extra checks.
    """
    if not relative_path:
        return None

    normalized = relative_path.replace("\\", "/").lstrip("/")
    parts = normalized.split("/")
    if parts and parts[0].lower() == "resources":
        parts = parts[1:]

    return str(Path(resources_root, *parts))


def _load_or_get_image(resources_root, relative_path):
    """
    Local copy of import_cha.py's _load_or_get_image() -- deliberately
    duplicated rather than imported, see module docstring.

    Loads a .dds texture from the resources root, reusing an already-
    loaded Blender image with the same filename if one exists. Sets
    alpha_mode/colorspace exactly as import_cha.py's copy does, so a
    default asset loaded via migration looks identical to the same asset
    loaded via import.

    Raises the same way bpy.data.images.load() does (OSError/RuntimeError
    on a missing/unreadable file) -- callers decide how to handle that,
    see _load_default_map_image() below.
    """
    path = _resolve_resource_path(resources_root, relative_path)
    name = Path(path).name
    existing = bpy.data.images.get(name)
    if existing:
        return existing
    image = bpy.data.images.load(path)
    image.alpha_mode = 'CHANNEL_PACKED'
    image.colorspace_settings.name = 'Non-Color'
    return image


def _get_resources_root():
    # type: () -> Optional[str]
    """
    Reads the addon's persistent "Resources Folder" preference -- the
    same one import_cha.py uses, and the same one the user sets once in
    Preferences regardless of which importer/migrator reads it. Returns
    None if unset or not a real directory, letting callers decide how to
    report that rather than raising here.
    """
    prefs = bpy.context.preferences.addons["swtor_io_tools"].preferences
    resources_root = prefs.swtor_resources_dir
    if not resources_root or not Path(resources_root).is_dir():
        return None
    return resources_root


def _load_default_map_image(node_name):
    # type: (str) -> Optional[bpy.types.Image]
    """
    Loads the fallback image for an optional map that's absent on the
    old node, per DEFAULT_MAP_ASSETS above. Reads the resources-root
    preference lazily -- only called at all when a material actually
    turns out to need a default, never up front.

    Returns None (rather than raising) if node_name has no default asset
    registered, the resources root isn't configured, or the file isn't
    actually present under it -- callers treat that as "leave this map
    unassigned" and report it, not as a hard failure.
    """
    relative_path = DEFAULT_MAP_ASSETS.get(node_name)
    if relative_path is None:
        return None

    resources_root = _get_resources_root()
    if resources_root is None:
        return None

    try:
        return _load_or_get_image(resources_root, relative_path)
    except (OSError, RuntimeError):
        return None


# Per-derived-type configuration, keyed by ShaderNodeHeroEngine's own
# `derived` enum values (CREATURE[0] == 'CREATURE', etc. -- these match
# SWTOR_SHADER_GROUPS' string keys in shaders_menu.py directly, so
# old_node.derived can be passed straight through to
# build_swtor_shader_material()).
#
# image_maps: (PointerProperty attr name on ShaderNodeHeroEngine, new
#   group's external Image Texture node name). The node-name half must
#   match SWTOR_SHADER_GROUPS' own node_name values exactly (see
#   shaders_menu.py), since it indexes straight into the image_nodes
#   dict build_swtor_shader_material() hands back below -- unrelated to
#   the OLD node's own internal node names (still '_d'/'_n'/'_s'/'_h'/
#   '_m', see node_deprecated.py's update_*Map() functions), which this
#   config never touches.
# palettes: list of (index, include_metallic_specular).
# flesh: whether Flesh Brightness / Flush Tone apply for this type.
#
# NOTE: HAIRC_MODERN never appears here -- it's a new shader type added
# after the old system was retired, so migration only ever produces one
# of these original six.
MIGRATE_CONFIGS = {
    CREATURE[0]: {
        "image_maps": [
            ("diffuseMap", "diffuseMap"),
            ("rotationMap", "rotationMap1"),
            ("glossMap", "glossMap"),
            ("paletteMaskMap", "paletteMaskMap"),
            ("directionMap", "directionMap"),
        ],
        "palettes": [],
        "flesh": True,
    },
    EYE[0]: {
        "image_maps": [
            ("diffuseMap", "diffuseMap"),
            ("rotationMap", "rotationMap1"),
            ("glossMap", "glossMap"),
            ("paletteMap", "paletteMap"),
            ("paletteMaskMap", "paletteMaskMap"),
        ],
        # Metallic Specular: the old node never actually wired this for
        # Eye (a pre-existing bug, since fixed in eye_group()) -- per
        # Crunch, migrate the old node's leftover value anyway now that
        # it's meaningful, rather than skipping it.
        "palettes": [(1, True)],
        "flesh": False,
    },
    GARMENT[0]: {
        "image_maps": [
            ("diffuseMap", "diffuseMap"),
            ("rotationMap", "rotationMap1"),
            ("glossMap", "glossMap"),
            ("paletteMap", "paletteMap"),
            ("paletteMaskMap", "paletteMaskMap"),
        ],
        "palettes": [(1, True), (2, True)],
        "flesh": False,
    },
    HAIRC[0]: {
        "image_maps": [
            ("diffuseMap", "diffuseMap"),
            ("rotationMap", "rotationMap1"),
            ("glossMap", "glossMap"),
            ("paletteMap", "paletteMap"),
            ("paletteMaskMap", "paletteMaskMap"),
            ("directionMap", "directionMap"),
        ],
        "palettes": [(1, True)],
        "flesh": False,
    },
    SKINB[0]: {
        "image_maps": [
            ("diffuseMap", "diffuseMap"),
            ("rotationMap", "rotationMap1"),
            ("glossMap", "glossMap"),
            ("paletteMap", "paletteMap"),
            ("paletteMaskMap", "paletteMaskMap"),
            ("ageMap", "ageMap"),
            ("complexionMap", "complexionMap"),
            ("facepaintMap", "facepaintMap"),
        ],
        "palettes": [(1, True)],
        "flesh": True,
    },
    UBER[0]: {
        "image_maps": [
            ("diffuseMap", "diffuseMap"),
            ("rotationMap", "rotationMap1"),
            ("glossMap", "glossMap"),
        ],
        "palettes": [],
        "flesh": False,
    },
}


def _find_hero_node(material):
    # type: (bpy.types.Material) -> Optional[bpy.types.ShaderNode]
    """
    Returns the first ShaderNodeHeroEngine node found in `material`'s
    node tree, or None if it has none (already native, or never SWTOR
    shaded at all).
    """
    if not material.use_nodes or material.node_tree is None:
        return None
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeHeroEngine":
            return node
    return None


class MigrationResult(NamedTuple):
    """
    Return value of migrate_material() -- richer than a plain bool so
    callers can report which specific maps, if any, ended up unassigned
    for lack of a default asset, without treating that as a failure.

    converted: True if a ShaderNodeHeroEngine node was found and
        converted; False if the material had nothing to migrate.
    missing_assets: human-readable names (e.g. "AgeMap") of maps that
        were absent on the old node AND couldn't get a default image
        (resources root unset, or the default asset file itself isn't
        present under it). Always empty when converted is False.
    """
    converted: bool
    missing_assets: List[str]


_NO_OP_RESULT = MigrationResult(converted=False, missing_assets=[])


def migrate_material(material):
    # type: (bpy.types.Material) -> MigrationResult
    """
    Converts one material's ShaderNodeHeroEngine node (if present) to the
    equivalent native Atroxa Shader node group, in place -- same material
    identity, node graph swapped internally, old node (and its now-
    orphaned private node_tree) deleted once the new graph is confirmed
    built.

    Always completes the conversion even if a default map asset can't be
    found -- that map is simply left unassigned (same as it would've been
    before DEFAULT_MAP_ASSETS existed), reported via the returned
    MigrationResult.missing_assets rather than raised.
    """
    old_node = _find_hero_node(material)
    if old_node is None:
        return _NO_OP_RESULT

    config = MIGRATE_CONFIGS.get(old_node.derived)
    if config is None:
        raise ValueError(f"Unrecognized ShaderNodeHeroEngine.derived value: {old_node.derived!r}")

    # ShaderNodeHeroEngine exposes no material-graph input sockets at all
    # (only a "Shader" output, see node_tree_deprecated.py's
    # add_output_socket_if_needed() calls) -- so nothing upstream feeds
    # it, and build_swtor_shader_material() can be called immediately.
    # It doesn't clear existing nodes first (see its own docstring), so
    # this adds the new group + image nodes alongside the still-present
    # old node without any name collision (the old node's own image
    # nodes live inside ITS private node_tree, not this material's).
    group_node, image_nodes = build_swtor_shader_material(material, old_node.derived)

    # Images -- already-loaded bpy.data.images datablocks, reassign
    # directly onto the new external Image Texture nodes, no reload.
    # Absent maps get DEFAULT_MAP_ASSETS' real game-default texture
    # instead; if that can't be loaded either, the map is left
    # unassigned and flagged in missing_assets.
    missing_assets = []
    for attr_name, node_name in config["image_maps"]:
        image = getattr(old_node, attr_name)
        if image is None:
            image = _load_default_map_image(node_name)
        if image is not None:
            image_nodes[node_name].image = image
        elif node_name in DEFAULT_MAP_ASSETS:
            missing_assets.append(node_name[0].upper() + node_name[1:])

    # Alpha: the old node's actual alpha_mode/alpha_test_value/
    # alpha_invert are the real, correct values for this specific
    # material -- read and preserve them faithfully, don't default them
    # (unlike import_cha.py's json-driven import, which always forces
    # CLIP -- not applicable here).
    group_node.inputs['Alpha Blend'].default_value = (old_node.alpha_mode == 'BLEND')
    group_node.inputs['Alpha Test'].default_value = (old_node.alpha_mode == 'CLIP')
    group_node.inputs['Alpha Test Value'].default_value = old_node.alpha_test_value
    group_node.inputs['Invert Alpha'].default_value = old_node.alpha_invert

    # Palettes
    for index, include_metallic_specular in config["palettes"]:
        prefix = f"palette{index}"
        socket_prefix = f"Palette{index} "
        group_node.inputs[f'{socket_prefix}Hue'].default_value = getattr(old_node, f"{prefix}_hue")
        group_node.inputs[f'{socket_prefix}Saturation'].default_value = getattr(old_node, f"{prefix}_saturation")
        group_node.inputs[f'{socket_prefix}Brightness'].default_value = getattr(old_node, f"{prefix}_brightness")
        group_node.inputs[f'{socket_prefix}Contrast'].default_value = getattr(old_node, f"{prefix}_contrast")
        group_node.inputs[f'{socket_prefix}Specular'].default_value = getattr(old_node, f"{prefix}_specular")
        if include_metallic_specular:
            group_node.inputs[f'{socket_prefix}Metallic Specular'].default_value = getattr(old_node, f"{prefix}_metallic_specular")

    # Flesh / Flush
    if config["flesh"]:
        group_node.inputs['Flesh Brightness'].default_value = old_node.flesh_brightness
        group_node.inputs['Flush Tone'].default_value = old_node.flush_tone

    # Delete the old node and its now-orphaned private per-instance
    # node_tree (created in ShaderNodeHeroEngine.init() via
    # bpy.data.node_groups.new()) -- otherwise it'd sit as dead data in
    # the file until Blender's own orphan-data purge caught it.
    old_tree = old_node.node_tree
    material.node_tree.nodes.remove(old_node)
    if old_tree is not None and old_tree.users == 0:
        bpy.data.node_groups.remove(old_tree)

    return MigrationResult(converted=True, missing_assets=missing_assets)


# ----------------------------------------------------------------------
# Operators -- all three share the `swtor.migrate_*` category so they
# list together in search (F3), per Crunch: no dedicated menu UI, just
# discoverable/favoritable/reusable-by-other-addons standard operators.
# ----------------------------------------------------------------------

def _migrate_and_report(operator, materials):
    # type: (bpy.types.Operator, List[bpy.types.Material]) -> None
    """
    Shared execute() body for the two batch operators (selected objects /
    whole file) -- runs migrate_material() over `materials` and reports a
    three-bucket summary: converted cleanly, converted but missing a
    default asset (still a successful conversion, just flagged), and
    failed outright (conversion didn't complete). Per-material detail for
    the latter two prints to console; self.report() gets the counts.

    Not shared with the single-material operator, which reports directly
    since there's only ever one material and no console dump is useful
    for that.
    """
    converted = 0
    skipped = 0
    incomplete = []  # (material_name, missing_assets)
    failed = []  # (material_name, error)

    for material in materials:
        try:
            result = migrate_material(material)
        except Exception as err:
            failed.append((material.name, str(err)))
            continue

        if not result.converted:
            skipped += 1
        else:
            converted += 1
            if result.missing_assets:
                incomplete.append((material.name, result.missing_assets))

    for name, missing in incomplete:
        print(f"[SWTOR migrate] '{name}' migrated, but couldn't find default assets for: {', '.join(missing)}")
    for name, err in failed:
        print(f"[SWTOR migrate] Failed to migrate '{name}': {err}")

    msg = f"Migrated {converted} material(s), skipped {skipped} (no deprecated shader found)"
    if incomplete:
        msg += f", {len(incomplete)} missing default assets (see console)"
    if failed:
        msg += f", {len(failed)} failed (see console)"

    if failed:
        operator.report({'ERROR'}, msg)
    elif incomplete:
        operator.report({'WARNING'}, msg)
    else:
        operator.report({'INFO'}, msg)


class SWTOR_OT_migrate_shader(bpy.types.Operator):
    """Converts this material's deprecated SWTOR shader node to the native Atroxa Shader node group, in place"""
    bl_idname = 'swtor.migrate_shader'
    bl_label = 'Migrate Atroxa Shader (Current Material)'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.material is not None

    def execute(self, context):
        material = context.material
        result = migrate_material(material)
        if not result.converted:
            self.report({'WARNING'}, f"'{material.name}' has no deprecated SWTOR shader node to migrate")
        elif result.missing_assets:
            self.report(
                {'WARNING'},
                f"Migrated '{material.name}', but couldn't find default assets for: {', '.join(result.missing_assets)}",
            )
        else:
            self.report({'INFO'}, f"Migrated '{material.name}' to the native Atroxa Shader")
        return {'FINISHED'}


class SWTOR_OT_migrate_shaders_selected(bpy.types.Operator):
    """Converts every deprecated SWTOR shader node found on materials across all selected objects to native Atroxa Shader node groups, in place"""
    bl_idname = 'swtor.migrate_shaders_selected'
    bl_label = 'Migrate Atroxa Shaders (Selected Objects)'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        seen = set()
        materials = []
        for ob in context.selected_objects:
            for slot in ob.material_slots:
                material = slot.material
                if material is None or material.name in seen:
                    continue
                seen.add(material.name)
                materials.append(material)

        _migrate_and_report(self, materials)
        return {'FINISHED'}


# ----------------------------------------------------------------------
# Shift+A discoverability -- appended (not authored) onto the existing
# "SWTOR Shaders (Deprecated)" submenu (NODE_MT_swtor_shaders_menu_
# deprecated, defined in shaders_menu_deprecated.py) via Blender's
# Menu.append() mechanism, the same technique __init__.py already uses
# to hook NODE_MT_add itself. This deliberately avoids editing
# shaders_menu_deprecated.py at all -- that file, and everything else in
# the deprecated system, stays untouched; only __init__.py (already an
# accepted exception for registration) needs to import this function and
# call NODE_MT_swtor_shaders_menu_deprecated.append(...)/.remove(...) in
# register()/unregister().
#
# A plain F3 Menu Search (Blender's default since 2.90) only finds items
# already present in a menu -- these operators weren't reachable that
# way until this entry existed. bpy.ops.swtor.migrate_*() direct calls
# and Developer-Extras Operator Search worked before this and still do.
def swtor_migrate_submenu_element(self, context):
    # type: (bpy.types.Menu, bpy.types.Context) -> None
    layout = self.layout
    layout.separator()
    layout.operator(SWTOR_OT_migrate_shader.bl_idname, text="Migrate This Material")
    layout.operator(SWTOR_OT_migrate_shaders_selected.bl_idname, text="Migrate Selected Objects' Materials")
    layout.operator(SWTOR_OT_migrate_shaders_file.bl_idname, text="Migrate All Materials in File")


class SWTOR_OT_migrate_shaders_file(bpy.types.Operator):
    """Converts every deprecated SWTOR shader node found on any material in this .blend to native Atroxa Shader node groups, in place"""
    bl_idname = 'swtor.migrate_shaders_file'
    bl_label = 'Migrate Atroxa Shaders (Whole File)'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _migrate_and_report(self, list(bpy.data.materials))
        return {'FINISHED'}