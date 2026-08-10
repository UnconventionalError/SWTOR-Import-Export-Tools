# <pep8 compliant>

"""
.mat-by-Name Materials Processing.

Builds real "Atroxa Shaders" native materials for existing Blender
materials purely by matching their *name* against a real .mat file on
disk -- entirely independent of any Jedipedia NPC/Character json data.
Two entry points share this module's pipeline:

- Automatic (7a): apply_materials_by_name_after_import(), called by
  ops/import_gr2.py's load() right after a genuine standalone .gr2
  import (never the NPC/Character (.json) importer's own internal gr2
  loads -- see that module's gating for why).
- Manual (7b): the three swtor.apply_materials_by_name* operators below
  plus their Shift+A submenu entry, appended onto the live (non-
  deprecated) NODE_MT_swtor_shaders_menu, mirroring ops/migrate_shaders.
  py's exact precedent for the deprecated system's own submenu.

Both funnel through _build_named_material(): Tier 1 (types/mat.py's
read_mat_summary -- alpha + skip-list + shader-family aliasing) + Tier 2
(types/mat.py's read_mat_full -- the map/palette adapter) +
ops/import_cha.py's existing get_or_create_material(), unchanged.

Deliberately does lazy (function-body), not top-level, imports from
ops/import_cha.py: import_cha.py imports ops/import_gr2.py's load() at
module level, and this module is imported by import_gr2.py's load() (to
call apply_materials_by_name_after_import()) too -- a top-level import
here would create an import cycle (import_gr2 -> process_materials ->
import_cha -> import_gr2). Deferred imports sidestep it, since by the
time any function below actually runs, every module in the cycle has
already finished loading.
"""

import json
from typing import Any, List, Optional, Tuple

import bpy

from ..types.mat import (
    KNOWN_UNBUILT_DERIVED,
    read_mat_full,
    read_mat_summary,
    resolve_mat_path,
)


# ---------------------------------------------------------------------------
# Shared core (Tier 1 + Tier 2 + get_or_create_material())
# ---------------------------------------------------------------------------

class MaterialProcessResult:
    """
    Per-material outcome, for the operators' self.report() summaries.

    status is one of:
        "built"        -- a native SWTOR shader was (re)built.
        "skipped"      -- deliberately not processed; see `detail`
                          ("already built" or "skip-listed name").
        "not_found"    -- no matching .mat file under either Resources
                          Directory.
        "unbuilt_type" -- resolved to a recognized-but-not-yet-supported
                          shader type (EmissiveOnly/AnimatedUV, or
                          something DERIVED_CONFIGS doesn't cover at
                          all); `detail` names the type.
        "error"        -- the .mat file couldn't be parsed, or
                          get_or_create_material() itself raised;
                          `detail` has the message. Never propagates --
                          batch callers keep going through the rest of
                          their list.
    """
    __slots__ = ("name", "status", "detail")

    def __init__(self, name, status, detail=""):
        self.name = name
        self.status = status
        self.detail = detail


def _get_resources_dirs():
    # type: () -> Tuple[Optional[str], Optional[str]]
    prefs = bpy.context.preferences.addons["swtor_io_tools"].preferences
    return prefs.swtor_resources_dir or None, prefs.swtor_legacy_resources_dir or None


def _build_named_material(mat_name, object_name=None):
    # type: (str, Optional[str]) -> Tuple[Optional[Any], MaterialProcessResult]
    """
    Core Tier 1 + Tier 2 + get_or_create_material() pipeline, shared by
    both entry points below.

    `mat_name` is looked up both on disk (against
    <resources>/art/shaders/materials/<mat_name>.mat) and in
    bpy.data.materials (via get_or_create_material()'s own by-name
    dedup) -- callers are responsible for passing the right name for
    their situation: the *original* SWTOR name (7a, from
    "gr2_material_names") or the material's own current Blender name
    (7b, matching zg_swtor_tools' original precedent and its same
    name-mangling caveat).

    Returns (material, result). material is None unless result.status
    is "built" or "skipped" with detail "already built".
    """
    from .import_cha import _has_swtor_shader_group, get_or_create_material

    existing = bpy.data.materials.get(mat_name)
    if existing is not None and _has_swtor_shader_group(existing):
        return existing, MaterialProcessResult(mat_name, "skipped", "already built")

    primary_dir, legacy_dir = _get_resources_dirs()
    if not primary_dir and not legacy_dir:
        return None, MaterialProcessResult(mat_name, "error", "no Resources Directory configured")

    mat_path, root_used = resolve_mat_path(mat_name, primary_dir, legacy_dir)
    if mat_path is None:
        return None, MaterialProcessResult(mat_name, "not_found")

    summary = read_mat_summary(mat_path, mat_name)
    if summary is None:
        return None, MaterialProcessResult(mat_name, "error", "couldn't parse .mat file")

    if summary.skip:
        return None, MaterialProcessResult(mat_name, "skipped", "skip-listed name")

    if summary.derived in KNOWN_UNBUILT_DERIVED:
        return None, MaterialProcessResult(mat_name, "unbuilt_type", summary.derived)

    mat_info = read_mat_full(mat_path, mat_name)
    if mat_info is None:
        return None, MaterialProcessResult(mat_name, "error", "couldn't parse .mat file")

    try:
        material = get_or_create_material(
            root_used, mat_name, summary.derived, mat_info, object_name=object_name,
        )
    except ValueError as exc:
        # DERIVED_CONFIGS doesn't cover this type at all (distinct from
        # the KNOWN_UNBUILT_DERIVED bucket above, which is caught
        # earlier -- this is a genuinely unrecognized <Derived> value).
        return None, MaterialProcessResult(mat_name, "unbuilt_type", str(exc))
    except Exception as exc:  # noqa: BLE001 -- defensive, mirrors migrate_shaders.py
        return None, MaterialProcessResult(mat_name, "error", str(exc))

    return material, MaterialProcessResult(mat_name, "built")


# ---------------------------------------------------------------------------
# 7b -- manual entry point (operates on already-existing materials, by
# their own current Blender name)
# ---------------------------------------------------------------------------

def apply_materials_by_name_to_materials(materials_with_objects):
    # type: (List[Tuple[Any, Optional[str]]]) -> List[MaterialProcessResult]
    """
    (Re)builds each already-existing material in `materials_with_objects`
    in place, looked up by its own current Blender name (material.name)
    -- the same name-mangling caveat zg_swtor_tools' original tool
    accepted (a Blender-renamed-on-collision material, e.g. "foo.001",
    simply won't resolve to a real "foo.mat"; rename it back to match if
    that happens).

    Each pair's second element is only used for SkinB's head Invert
    Alpha auto-detect (see get_or_create_material()'s docstring) -- pass
    a representative object's name, or None.
    """
    results = []
    for material, object_name in materials_with_objects:
        _, result = _build_named_material(material.name, object_name)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# 7a -- automatic entry point (operates on freshly-.gr2-imported objects,
# by each material slot's *original* SWTOR name)
# ---------------------------------------------------------------------------

def apply_materials_by_name_to_gr2_objects(object_names):
    # type: (List[str]) -> List[MaterialProcessResult]
    """
    For every newly-.gr2-imported object in `object_names` that carries
    the "gr2_material_names" round-trip property (see import_gr2.py's
    build()), resolves each material slot's *original* SWTOR material
    name -- not the possibly Blender-renamed-on-collision bpy.data.
    materials name of the placeholder build() created for it -- against
    a real .mat file, and (re)points that slot at the correctly-deduped
    canonical material.

    The explicit slot re-pointing matters: build()'s placeholder
    materials and get_or_create_material()'s own by-name dedup are two
    independent mechanisms that can each land on a differently-named
    material for the same logical SWTOR material if Blender had to
    auto-suffix one of them on a name collision (e.g. a second .gr2
    import reusing the same material name) -- without this, the newly-
    imported object's slot could keep pointing at an unbuilt ".001"
    placeholder while some unrelated, earlier-created material of the
    same base name gets rebuilt instead.
    """
    results = []
    for object_name in object_names:
        ob = bpy.data.objects.get(object_name)
        if ob is None or ob.type != 'MESH':
            continue

        raw = ob.get("gr2_material_names")
        if not raw:
            continue
        try:
            original_names = json.loads(raw)
        except (ValueError, TypeError):
            continue

        for slot_index, mat_name in enumerate(original_names):
            if slot_index >= len(ob.data.materials):
                break
            material, result = _build_named_material(mat_name, ob.name)
            results.append(result)
            if material is not None:
                ob.data.materials[slot_index] = material
    return results


def apply_materials_by_name_after_import(object_names):
    # type: (List[str]) -> None
    """
    7a entry point proper -- called by ops/import_gr2.py's load() right
    after a genuine standalone .gr2 import. Silent by design: no
    self.report(), since there's no useful interactive operator context
    for an automatic, best-effort pass to surface warnings to.
    Materials with no matching .mat file are left alone, no error/print
    at all (handoff §7a) -- only genuine failures print to console.
    """
    results = apply_materials_by_name_to_gr2_objects(object_names)
    for result in results:
        if result.status == "error":
            print(f"[SWTOR materials-by-name] Failed to process '{result.name}': {result.detail}")


# ---------------------------------------------------------------------------
# Reporting (mirrors ops/migrate_shaders.py's _migrate_and_report())
# ---------------------------------------------------------------------------

def _summarize_and_report(operator, results):
    # type: (bpy.types.Operator, List[MaterialProcessResult]) -> None
    built = sum(1 for r in results if r.status == "built")
    already_built = sum(1 for r in results if r.status == "skipped" and r.detail == "already built")
    skip_listed = sum(1 for r in results if r.status == "skipped" and r.detail == "skip-listed name")
    not_found = [r.name for r in results if r.status == "not_found"]
    unbuilt = [(r.name, r.detail) for r in results if r.status == "unbuilt_type"]
    errors = [(r.name, r.detail) for r in results if r.status == "error"]

    for name in not_found:
        print(f"[SWTOR materials-by-name] No matching .mat file found for '{name}'")
    for name, derived in unbuilt:
        print(f"[SWTOR materials-by-name] '{name}' is a recognized but not-yet-supported shader type ({derived})")
    for name, err in errors:
        print(f"[SWTOR materials-by-name] Failed to process '{name}': {err}")

    msg = f"Built {built} material(s), {already_built + skip_listed} already/deliberately skipped"
    if not_found:
        msg += f", {len(not_found)} with no matching .mat file (see console)"
    if unbuilt:
        msg += f", {len(unbuilt)} of a not-yet-supported type (see console)"
    if errors:
        msg += f", {len(errors)} failed (see console)"

    if errors:
        operator.report({'ERROR'}, msg)
    elif not_found or unbuilt:
        operator.report({'WARNING'}, msg)
    else:
        operator.report({'INFO'}, msg)


# ---------------------------------------------------------------------------
# 7b operators -- same three-operator scope split as ops/migrate_shaders.
# py's precedent (Current Material / Selected Objects / Whole File),
# appended onto the live NODE_MT_swtor_shaders_menu (not the deprecated
# one) via the same Shift+A submenu-append technique.
# ---------------------------------------------------------------------------

class SWTOR_OT_apply_materials_by_name(bpy.types.Operator):
    """Looks up this material's name against a real .mat file in the Resources Directory and builds/rebuilds its native SWTOR shader"""
    bl_idname = 'swtor.apply_materials_by_name'
    bl_label = 'Apply Material By Name (Current Material)'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.material is not None

    def execute(self, context):
        material = context.material
        object_name = context.active_object.name if context.active_object else None
        result = apply_materials_by_name_to_materials([(material, object_name)])[0]

        if result.status == "built":
            self.report({'INFO'}, f"Built native SWTOR shader for '{material.name}'")
        elif result.status == "skipped":
            if result.detail == "skip-listed name":
                self.report({'INFO'}, f"Skipped: '{material.name}' is not a valid .mat file name")
            else:
                self.report({'INFO'}, f"'{material.name}' already has a native SWTOR shader")
        elif result.status == "not_found":
            self.report({'WARNING'}, f"No matching .mat file found for '{material.name}'")
        elif result.status == "unbuilt_type":
            self.report({'WARNING'}, f"'{material.name}' is a recognized but not-yet-supported shader type ({result.detail})")
        else:
            self.report({'ERROR'}, f"Failed to process '{material.name}': {result.detail}")

        return {'FINISHED'}


class SWTOR_OT_apply_materials_by_name_selected(bpy.types.Operator):
    """Looks up every material name across all selected objects against real .mat files in the Resources Directory and builds/rebuilds their native SWTOR shaders"""
    bl_idname = 'swtor.apply_materials_by_name_selected'
    bl_label = 'Apply Materials By Name (Selected Objects)'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        seen = set()
        materials_with_objects = []
        for ob in context.selected_objects:
            for slot in ob.material_slots:
                material = slot.material
                if material is None or material.name in seen:
                    continue
                seen.add(material.name)
                materials_with_objects.append((material, ob.name))

        results = apply_materials_by_name_to_materials(materials_with_objects)
        _summarize_and_report(self, results)
        return {'FINISHED'}


class SWTOR_OT_apply_materials_by_name_file(bpy.types.Operator):
    """Looks up every material name in this .blend against real .mat files in the Resources Directory and builds/rebuilds their native SWTOR shaders"""
    bl_idname = 'swtor.apply_materials_by_name_file'
    bl_label = 'Apply Materials By Name (Whole File)'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        materials_with_objects = [(material, None) for material in bpy.data.materials]
        results = apply_materials_by_name_to_materials(materials_with_objects)
        _summarize_and_report(self, results)
        return {'FINISHED'}


# Shift+A discoverability -- appended (not authored) onto the existing,
# live NODE_MT_swtor_shaders_menu (ops/shaders_menu.py), below a
# separator from its seven "Add [X] Shader" entries. Mirrors ops/
# migrate_shaders.py's swtor_migrate_submenu_element() exactly, except
# targeting the live menu instead of the deprecated one -- this feature
# isn't deprecated-adjacent, so, unlike migrate_shaders.py, this module
# imports Tier 1/Tier 2 (and, lazily, get_or_create_material()) directly
# rather than keeping a local duplicate for isolation.
def swtor_process_materials_submenu_element(self, context):
    # type: (bpy.types.Menu, bpy.types.Context) -> None
    layout = self.layout
    layout.separator()
    layout.operator(SWTOR_OT_apply_materials_by_name.bl_idname, text="Apply Material By Name (Current Material)")
    layout.operator(SWTOR_OT_apply_materials_by_name_selected.bl_idname, text="Apply Materials By Name (Selected Objects)")
    layout.operator(SWTOR_OT_apply_materials_by_name_file.bl_idname, text="Apply Materials By Name (Whole File)")