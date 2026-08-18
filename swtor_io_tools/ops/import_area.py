# <pep8 compliant>

"""
Area Assembler -- Operator layer.

Builds real Blender objects from types/area.py's parsed elements and
resolved transforms: resolves each element's actual mesh reference
(direct .gr2/.mag/.spt, or a .spn_p indirection through spn_table,
possibly expanding into several .dyn visuals), imports it (reusing
mesh data for repeated source files rather than reimporting them every
time), and bakes its final position/rotation/scale directly onto the
resulting object(s).

Deliberately does NOT use Blender's live parenting for transform
inheritance anywhere except the organizational Empty grouping described
below -- every object's transform is set directly from
types/area.py's own already-composed values, matching this addon's
"design before coding" decision to bake absolute transforms in Python
rather than lean on bpy's own matrix_local/matrix_world composition
(which is how the old source did it, and not something we're repeating
here -- see that decision's own writeup).
"""

import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import bpy
from mathutils import Matrix

from .import_gr2 import load as ImportGR2_load
from .import_cha import resolve_resource_path
from .process_materials import apply_materials_by_name_to_gr2_objects, _summarize_and_report
from ..types import area as area_types
from ..types.shared import job_results


# ---------------------------------------------------------------------------
# Scope (handoff §2a)
# ---------------------------------------------------------------------------

# Extensions Area Assembler is meant to eventually do *something* with.
# Anything NOT in this set (.cvr, .trg, .enc, .rgn, .stg, .spn_c, .cam,
# .prt, .wtr, etc.) is genuinely out of scope per §2a -- gameplay/logic
# data with no visual meaning here -- and is skipped silently, not
# warned about; warning on every one of those would be noise, not
# signal (a single real area file can have thousands of them).
IN_SCOPE_EXTENSIONS = {"gr2", "mag", "spt", "spn_p", "hms", "lit", "fxp"}

# Of those, what this pass of Area Assembler actually builds Blender
# objects for via the generic .gr2/.mag/.spt import path. ".lit" and
# ".hms" are handled separately -- ".lit" in _process_element's own
# early branch (see _LightCache), ".hms" in _process_area_element's own
# early branch (see _process_terrain_element) -- neither goes through
# the .gr2 importer at all, but both still count as "buildable" in
# spirit, just not through this specific path. The remainder (.fxp) is
# a real, in-scope format without an importer yet -- skipped with an
# aggregated warning (see _process_element) rather than silently, since
# it SHOULD eventually produce something and a stale bundled-data-style
# silent skip would hide that.
BUILDABLE_EXTENSIONS = {"gr2", "mag", "spt"}

# Object exclusion, confirmed against the old source (not guessed).
# Both identify genuinely different things -- filename vs. material --
# but are folded under one combined "Skip Game Engine Objects" toggle
# (area_skip_game_engine_objects) rather than kept separate, since both are
# fundamentally the same kind of thing to the person running an import:
# non-visual, tooling-only content nobody wants in an assembled scene.
#
# DBO_FILENAME_PREFIX -- design blockout utility objects (blockers,
# markers, etc.), identifiable by their own filename.
#
# MATERIAL_NAME_EXCLUSION -- covers what the old source's own comment
# describes as "Hero Engine utility object types [that] can't be
# determined by their names, but oftentimes their materials' names are
# a good criteria" -- objects like occluder walls, portals, and various
# "util_*_hidden" utility meshes are identified by which MATERIAL they
# use, not by their own filename. This is genuinely the mechanism
# behind e.g. "occluder_wall" objects being excluded -- not DBO-filename
# matching, which would never have caught those.
DBO_FILENAME_PREFIX = "dbo"

MATERIAL_NAME_EXCLUSION = {
    "collision",
    "dbo_universal_superexclusion_test",
    "mote_mote_a01_v01",
    "occluder",
    "occluder_terrain",
    "occluder_wall",
    "portal",
    "util_blue_hidden",
    "util_collision_hidden",
    "util_collision_none",
    "util_green_hidden",
    "util_red_hidden",
    "util_white_hidden",
    "util_yellow_hidden",
    "white_utility_hidden",
}


def _format_duration(seconds):
    # type: (float) -> str
    """
    "H:MM:SS", no leading zero on hours, milliseconds dropped --
    matches the old importer's own end-of-run report style, which
    Crunch specifically wants kept for very long (multi-hour) batch
    imports where a raw seconds count stops being readable at a glance.
    """
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs)


def _has_excluded_material(ob):
    # type: (Any) -> bool
    raw = ob.get("gr2_material_names")
    if not raw:
        return False
    try:
        names = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return any(isinstance(n, str) and n.lower() in MATERIAL_NAME_EXCLUSION for n in names)


def _filter_excluded_objects(objects, skip_game_engine_objects, skip_counts):
    # type: (list, bool, Counter) -> list
    """
    Applied to every object _import_gr2() returns, whether freshly
    imported or reused from mesh_cache -- both need this check every
    single time, not just on a fresh import: a cached mesh's objects
    are recreated fresh from bpy.data.objects.new() on each reuse, so
    filtering has to be re-applied per use, not baked into the cache
    once.

    Both the DBO-filename and utility-material checks share the same
    skip_game_engine_objects toggle (area_skip_game_engine_objects) -- distinct
    mechanisms under the hood, but the same kind of thing from the
    perspective of someone running an import: non-visual, tooling-only
    content nobody wants in the assembled scene.

    Safe to delete any subset of the objects passed in here, including
    all of them: no multi-mesh sub-object relies on another for its own
    position (see _MeshCache.instantiate()'s own note on why), and
    parent-chain rotation/scale composition (compute_world_rotation_scale)
    reads the parsed area json directly, never an actual Blender object
    -- so excluding an element that other elements reference as their
    own parent doesn't affect those children's correctness either.
    """
    if not skip_game_engine_objects:
        return objects

    kept = []
    for ob in objects:
        reason = None
        if ob.name.lower().startswith(DBO_FILENAME_PREFIX):
            reason = "'dbo*'-named object(s) skipped (Skip Game Engine Objects)"
        elif _has_excluded_material(ob):
            reason = "object(s) using a known non-visual utility material skipped (e.g. occluder/portal/collision)"

        if reason is not None:
            skip_counts[reason] += 1
            bpy.data.objects.remove(ob, do_unlink=True)
        else:
            kept.append(ob)
    return kept


# ---------------------------------------------------------------------------
# .mag / .spt resolution (handoff §2a/§4.5)
# ---------------------------------------------------------------------------

def _resolve_mag_reference(resources_root, mag_relative_path):
    # type: (str, str) -> str or None
    """
    Naive text-scan of a .mag file for its "Mesh=...gr2" line, per
    handoff §4.5 -- deliberately not "improved" beyond what the old
    source did; see that decision's own writeup for why.

    Returns the referenced .gr2's relative path (leading slash
    stripped), or None if the file is missing or has no such line.
    """
    resolved_path = resolve_resource_path(resources_root, mag_relative_path)
    if resolved_path is None or not os.path.isfile(resolved_path):
        return None

    try:
        with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if ".gr2" in line and "Mesh=" in line:
                    referenced = line.split("Mesh=", 1)[1].strip()
                    if referenced.startswith("/") or referenced.startswith("\\"):
                        referenced = referenced[1:]
                    return referenced
    except OSError:
        return None
    return None


def _resolve_import_target(relative_path):
    # type: (str) -> tuple
    """
    Returns (resolved_relative_path, is_speedtree). ".spt" resolves to
    the identically-named ".gr2" -- confirmed in the old source, the
    SpeedTree mesh data lives at the same path with the extension
    swapped, not a separate lookup.
    """
    if relative_path.lower().endswith(".spt"):
        return relative_path[:-len(".spt")] + ".gr2", True
    return relative_path, False


# ---------------------------------------------------------------------------
# Mesh data cache (handoff's confirmed "carry forward the dedup/instancing
# idea, cleanly, not as the old flat dict" decision)
# ---------------------------------------------------------------------------

class _MeshCache:
    """
    Caches already-imported .gr2 mesh data by resolved (absolute,
    on-disk) path, so a source file reused many times across one area
    (a candle, a crate, a piece of trim) only gets parsed/built once.
    Later instances get a cheap bpy.data.objects.new() sharing that
    mesh data instead of a full reimport.

    A stored entry is either a non-empty list of (Mesh, gr2_material_names)
    pairs (a multi-mesh .gr2 produces more than one Object on its first
    import; every later instance reproduces that same multi-object
    shape) or None, meaning the first import of this path produced
    nothing usable -- remembered so later instances don't retry it.

    gr2_material_names is captured alongside its mesh specifically
    because it's an Object-level custom property, not a Mesh-level one
    -- bpy.data.objects.new() does NOT copy custom properties from
    wherever a mesh came from, even when reusing that exact mesh
    data-block, so a fresh copy has to be set explicitly on every new
    instance or it silently ends up missing. CONFIRMED real bug this
    fixes: _filter_excluded_objects()'s utility-material check reads
    this property per-object, so every cache-instantiated repeat of a
    to-be-excluded object (e.g. a reused occluder mesh) was silently
    skipping that check and never getting excluded -- only a path's
    very first, freshly-imported instance ever actually had the
    property to check against.

    scale_factor is baked into each freshly-cached mesh's vertex data
    exactly once, here, not reflected in Object.scale -- matching this
    addon's own .gr2 importer convention (confirmed against
    import_gr2.py's build(): scale_object bakes into geometry via
    transform_apply, Object.scale is reset to its natural value
    afterward). Every later instance sharing this cached mesh data
    automatically gets the correctly pre-scaled geometry for free.
    """
    def __init__(self, scale_factor=1.0):
        self._cache = {}  # resolved_path -> list[(Mesh, material_names_json_or_None)] or None
        self._scale_factor = scale_factor

    def has(self, resolved_path):
        return resolved_path in self._cache

    def store(self, resolved_path, objects):
        # type: (str, list) -> None
        if self._scale_factor != 1.0:
            scale_matrix = Matrix.Scale(self._scale_factor, 4)
            seen_meshes = set()
            for ob in objects:
                if ob.data is not None and id(ob.data) not in seen_meshes:
                    ob.data.transform(scale_matrix)
                    ob.data.update()
                    seen_meshes.add(id(ob.data))
        entries = [(ob.data, ob.get("gr2_material_names")) for ob in objects if ob.data is not None]
        self._cache[resolved_path] = entries or None

    def instantiate(self, resolved_path, name):
        # type: (str, str) -> list
        """
        Builds new Object(s) sharing this path's cached mesh data.
        Returns [] for a known-discard path.

        Deliberately does NOT parent multi-mesh sub-objects to each
        other -- matches observed behavior of a fresh multi-mesh .gr2
        import (confirmed against a real 220-file batch import with no
        multi-mesh positioning issues, meaning build() itself doesn't
        parent its own sub-objects together either). Keeping every
        object here fully independent means _process_element's own
        "if ob.parent is None: _apply_transform(...)" applies uniformly
        to all of them, and removes any risk from later deleting one
        sub-object (e.g. a DBO/utility-material exclusion) leaving
        others orphaned mid-scene with only their original, un-baked
        local transform.
        """
        entries = self._cache.get(resolved_path)
        if not entries:
            return []
        objects = []
        for i, (mesh_data, material_names) in enumerate(entries):
            ob_name = name if i == 0 else mesh_data.name
            new_ob = bpy.data.objects.new(ob_name, mesh_data)
            if material_names is not None:
                new_ob["gr2_material_names"] = material_names
            objects.append(new_ob)
        return objects


# ---------------------------------------------------------------------------
# Lights (handoff's "Create Scene Lights" priority)
# ---------------------------------------------------------------------------

# Confirmed against the old source: .lit file contents are never
# actually read/parsed anywhere -- a .lit-tagged element just marks
# "put a generic placeholder light here". Keeping that same simplicity
# deliberately (Crunch's own call, given real per-light color/intensity
# data isn't available yet, and richer export data enabling real light
# types is planned as separate future work this shouldn't fight with).
DEFAULT_LIGHT_ENERGY = 2.0

# Per-file safeguard against tanking viewport performance with hundreds
# of real lights. The old source attempted this too but never actually
# worked -- its own comment admits "exclude_collection_lights DOESN'T
# WORK!!! Collections have no .exclude method". The correct API is
# LayerCollection.exclude (a view-layer-tree property), not anything on
# Collection itself -- confirmed by the old source's own OTHER, correct
# use of exactly that API elsewhere (ExcludeAfterImport) for a related
# feature, so this isn't a new pattern for this codebase, just a fixed
# version of an attempt that already existed.
LIGHT_COUNT_AUTO_EXCLUDE_THRESHOLD = 100


class _LightCache:
    """
    Holds ONE shared Light data-block for a whole area file, created
    lazily on first use. Every .lit-tagged element in one file reuses
    the SAME light data (matches the old source's own convention) --
    unlike _MeshCache, no per-path keying is needed, since every
    placeholder light is identical regardless of which element spawned
    it; only the per-instance Object (position) differs.
    """
    def __init__(self, area_name, scale_factor):
        self._area_name = area_name
        self._scale_factor = scale_factor
        self._light_data = None

    def get(self):
        if self._light_data is None:
            self._light_data = bpy.data.lights.new(name="%s - Light" % self._area_name, type='POINT')
            # Power scaled by scale_factor: a fixed energy value becomes
            # visually meaningless once the whole scene has been scaled
            # up or down by an order of magnitude (Crunch's own point --
            # "Power 2 will be useless if the scene is 10x larger").
            self._light_data.energy = DEFAULT_LIGHT_ENERGY * self._scale_factor
        return self._light_data

    def object_name(self):
        # type: () -> str
        """
        "light - <area name>" for every light Object this file's lights
        share -- deliberately NOT derived from the spawning element's
        own asset path (unlike mesh objects), so light names stay
        short and consistent, and carry the area name as a suffix to
        stay identifiable once many area files' lights sit in the same
        large scene together. Multiple lights in one file legitimately
        share this exact name; Blender's own ".001"/".002" auto-suffix
        handles the rest, same as any other repeated name in this addon.
        """
        return "light - %s" % self._area_name


def _find_layer_collection(layer_collection, target_collection):
    # type: (Any, Any) -> Any
    """
    Recursively searches the view layer's own LayerCollection tree
    (parallel to, but distinct from, the plain Collection hierarchy)
    for the node wrapping `target_collection`. Needed because .exclude
    only exists on LayerCollection, not Collection -- see the module
    note above on LIGHT_COUNT_AUTO_EXCLUDE_THRESHOLD for why that
    distinction matters here specifically.
    """
    if layer_collection.collection == target_collection:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, target_collection)
        if found is not None:
            return found
    return None


class _LightBudget:
    """
    Tracks the CUMULATIVE light count across an entire multi-file batch
    import, not per file -- created once per execute() call and shared
    across every load() call in that batch, same lifetime as mesh_cache.

    CONFIRMED real gap this fixes: a per-file-only threshold check
    never catches a batch where every individual file stays comfortably
    under the threshold but the SCENE ends up with thousands of real
    lights once every file's contribution is added together (e.g. 220
    files at ~50 lights each). Viewport performance is affected by how
    many lights are active in the scene at once, not by which file any
    one of them came from -- so the budget has to be batch-wide to mean
    anything for the actual use case (importing a whole area's worth of
    files together).

    Every file still gets its own named "<file> - Lights" Collection
    for organizational purposes (unchanged) -- this only decides
    whether each one, individually, ends up excluded from the view
    layer, based on the running total across every file processed so
    far in this batch.
    """
    def __init__(self, threshold):
        self._threshold = threshold
        self._total = 0
        self._collections = []
        self._exceeded = False

    def register(self, context, lights_collection):
        # type: (Any, Any) -> None
        """
        Call once per file, after that file's lights_collection is
        finalized -- pass None if the file created no lights at all
        (nothing to track or exclude).
        """
        if lights_collection is None:
            return

        self._total += len(lights_collection.objects)
        self._collections.append(lights_collection)

        if self._exceeded:
            # Already over budget from an earlier file in this batch --
            # every subsequent file's lights collection gets excluded
            # too, without repeating the full warning every time.
            self._exclude(context, lights_collection, announce=False)
            return

        if self._total > self._threshold:
            self._exceeded = True
            print(
                "Area Assembler: cumulative light count across this batch reached "
                "%d (threshold %d) -- excluding %d Lights collection(s) from the "
                "view layer so far (viewport performance). Re-enable them in the "
                "Outliner if you want them visible; any further files in this "
                "batch will also be excluded automatically."
                % (self._total, self._threshold, len(self._collections))
            )
            for collection in self._collections:
                self._exclude(context, collection, announce=False)

    def _exclude(self, context, collection, announce):
        context.view_layer.update()
        layer_collection = _find_layer_collection(context.view_layer.layer_collection, collection)
        if layer_collection is not None:
            layer_collection.exclude = True
        else:
            # Never silently do nothing -- see this same reasoning on
            # the equivalent lookup failure this addon has hit before.
            print(
                "Area Assembler: couldn't find \"%s\"'s LayerCollection to exclude "
                "it -- left visible. Please report this if it keeps happening."
                % collection.name
            )


def _job_results_key(filepath):
    # type: (str) -> str
    """Mirrors import_gr2.py's own key normalization for job_results['files_objs_names']."""
    normalized = filepath.replace("\\", "/")
    if "resources" in normalized:
        return normalized.partition("resources/")[2]
    return normalized


def _merge_multi_mesh_objects(objects, name):
    # type: (list, str) -> list
    """
    Joins multiple Objects into one via bpy.ops.object.join(), matching
    the old source's "Merge Multi-Mesh Objects" option (confirmed real
    toggle there too, default off -- same default kept here).

    Always renames both the resulting Object AND its mesh data-block to
    `name` (the resolved .gr2's own filename stem) -- CONFIRMED bug this
    fixes: an earlier version only renamed the Object, leaving the mesh
    DATA-block (a separate ID datablock bpy.ops.object.join() doesn't
    touch the name of) still carrying whichever sub-object happened to
    be arbitrarily active before the join, e.g. "Object17" instead of
    the real name.

    Also rebuilds "gr2_material_names" for the merged result --
    CONFIRMED bug this fixes, and the more serious one: that property
    lives on the OBJECT, not the mesh data, and bpy.ops.object.join()
    only keeps the ACTIVE object's own custom properties -- every OTHER
    sub-object's own gr2_material_names is simply lost when that object
    gets deleted by the join. Left unfixed, only whichever few material
    slots happened to belong to the arbitrarily-active sub-object ever
    got real names; every other slot silently kept whatever generic
    Blender default it already had, with nothing left to tell
    apply_materials_by_name_to_gr2_objects() what those slots were
    really supposed to be. Fixed by capturing each sub-object's own
    slot-name mapping BEFORE the join (keyed by the actual material
    ID-datablock, not by slot index -- robust regardless of how
    bpy.ops.object.join() itself reorders or deduplicates identically-
    named slots internally, which isn't something worth relying on),
    then rebuilding the merged object's own gr2_material_names by
    walking its FINAL slot list and looking up each slot's real name
    through that mapping. A slot whose original name can't be recovered
    (came from an object with no gr2_material_names at all, or slot
    genuinely has no material) falls back to that slot's own current
    Blender material name -- matches this same file's own existing 7b
    precedent for the same situation, and is always a safe string
    (never None, which would crash the string/path operations inside
    _build_named_material's own .mat file lookup).

    No parenting anywhere in this path -- confirmed not needed here,
    per this addon's own earlier decision (see _MeshCache.instantiate()'s
    own note on why fresh multi-mesh .gr2 imports don't need it either).
    """
    if len(objects) <= 1:
        return objects

    material_name_by_datablock = {}
    for ob in objects:
        raw = ob.get("gr2_material_names")
        if not raw:
            continue
        try:
            original_names = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for slot, real_name in zip(ob.data.materials, original_names):
            if slot is not None and isinstance(real_name, str) and real_name:
                material_name_by_datablock[slot] = real_name

    bpy.ops.object.select_all(action='DESELECT')
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()

    merged = bpy.context.view_layer.objects.active
    merged.name = name
    merged.data.name = name

    merged_names = [
        material_name_by_datablock.get(slot, slot.name if slot is not None else "")
        for slot in merged.data.materials
    ]
    if merged_names:
        merged["gr2_material_names"] = json.dumps(merged_names)

    return [merged]


def _import_gr2(operator, context, resources_root, resolved_path, mesh_cache):
    # type: (Any, Any, str, str, _MeshCache) -> list
    """
    Returns a list of Objects for `resolved_path` (an absolute, on-disk
    .gr2 path), either freshly imported or instantiated from mesh_cache.

    Naming: a fresh import doesn't need a name passed in here at all --
    build() already names the resulting object(s) from the .gr2
    filename itself, exactly matching a normal standalone .gr2 import
    (confirmed fix -- an earlier version of this function threaded the
    calling element's raw numeric SWTOR id through as the name, which
    only actually got used on the *cached-instance* path below,
    producing objects named like "4611686170648420455" instead of
    their real .gr2-derived name on the second+ instance of a reused
    mesh). The cached-instance path derives its name from the resolved
    path's own filename for exactly the same reason.

    Merging (area_merge_multi_mesh_objects) happens here, once, on a
    FRESH import only, before mesh_cache.store() -- so the cached mesh
    data is already the single merged result, and every later reused
    instance automatically gets the merged shape for free too, same
    reasoning as scale_factor baking happening at this exact point.
    """
    if mesh_cache.has(resolved_path):
        return mesh_cache.instantiate(resolved_path, Path(resolved_path).stem)

    operator.job_results_rich = True
    ImportGR2_load(operator, context, resolved_path)

    object_names = job_results.get("files_objs_names", {}).get(_job_results_key(resolved_path), [])
    imported_objects = [bpy.data.objects[n] for n in object_names]
    if operator.area_merge_multi_mesh_objects and len(imported_objects) > 1:
        imported_objects = _merge_multi_mesh_objects(imported_objects, Path(resolved_path).stem)
    mesh_cache.store(resolved_path, imported_objects)
    return imported_objects


def _reduce_speedtree_lods(objects, is_speedtree, element_name):
    # type: (list, bool, str) -> list
    """
    Carried forward from the old source: a SpeedTree (or tree-named)
    multi-mesh import commonly produces several LOD (level-of-detail)
    objects; keep only the highest-polygon one, unless an object's name
    contains "_lod0" (kept regardless -- treated as the canonical mesh
    alongside the highest-poly pick, matching the old source exactly).

    This is real, previously-working logic being carried forward, not
    something invented for this pass -- flagged in the handoff writeup
    as worth a deliberate confirm given it's a specific name-substring
    heuristic.
    """
    if len(objects) <= 1:
        return objects
    if not (is_speedtree or "tree" in element_name.lower()):
        return objects

    highest = max(objects, key=lambda ob: len(ob.data.polygons) if ob.data else 0)
    kept = [highest]
    for ob in objects:
        if ob is highest:
            continue
        if "_lod" in ob.name.lower() and "_lod0" not in ob.name.lower():
            bpy.data.objects.remove(ob, do_unlink=True)
        else:
            kept.append(ob)
    return kept


# ---------------------------------------------------------------------------
# Transform application
# ---------------------------------------------------------------------------

def _apply_transform(ob, transform):
    # type: (Any, area_types.ResolvedTransform) -> None
    """
    Converts `transform` (still in SWTOR's raw coordinate space -- see
    types/area.py's to_blender_space() docstring for why every upstream
    composition step deliberately stays in that space) into Blender's
    convention exactly once, here, then applies it. Every caller that
    sets an object's final position/rotation/scale should go through
    this function rather than setting those properties directly, so
    that conversion never accidentally gets applied twice (e.g. once to
    a parent Empty and again to its already-converted children).

    rotation_mode is set to 'ZXY' before assigning rotation_euler --
    matching types/area.py's own composition math exactly (see that
    module's note on _mat3_from_euler_zxy_degrees for why 'ZXY', not
    Blender's default 'XYZ' -- confirmed against the old source, not
    guessed). Every object this function touches needs this set
    consistently, since mixing rotation_mode across objects that are
    meant to represent the same underlying rotation convention would
    reintroduce exactly the kind of subtle mismatch this fixes.
    """
    blender_transform = area_types.to_blender_space(transform)
    ob.location = blender_transform.position
    ob.rotation_mode = 'ZXY'
    ob.rotation_euler = tuple(math.radians(d) for d in blender_transform.rotation)
    ob.scale = blender_transform.scale


def _basis_matrix(ob):
    # type: (Any) -> Matrix
    """
    Computes `ob`'s own local-to-world matrix directly from its
    location/rotation_mode/rotation_euler/scale properties, WITHOUT
    reading ob.matrix_world.

    CONFIRMED BUG this fixes: ob.matrix_world isn't guaranteed to
    reflect a transform just assigned via the Python API until a
    depsgraph evaluation actually runs, which doesn't happen
    synchronously -- reading it immediately after _apply_transform() (as
    the previous version of this file did, for parent_empty specifically)
    can return a stale/identity matrix. That produced an incorrect
    matrix_parent_inverse (effectively identity instead of the real
    inverse), which made Blender double-apply the parent Empty's
    transform on top of each child's own already-correct absolute
    transform -- confirmed against real reported numbers (a child's
    local location exactly matched its parent's, and its resulting
    world position exactly matched "parent transform applied twice").

    Only valid for a PARENTLESS object (matrix_world == this basis
    matrix in that case) -- which parent_empty always is, by
    construction, in this file. Uses ob.rotation_euler.to_matrix(),
    which is mathutils' own Euler-to-matrix conversion and correctly
    respects whatever ob.rotation_mode is currently set to -- not a
    hand-rolled formula, so this doesn't carry the same convention risk
    the pure-Python composition in types/area.py does.
    """
    return (
        Matrix.Translation(ob.location)
        @ ob.rotation_euler.to_matrix().to_4x4()
        @ Matrix.Diagonal(ob.scale).to_4x4()
    )


# ---------------------------------------------------------------------------
# Per-element import
# ---------------------------------------------------------------------------

def _process_element(operator, context, resources_root, mesh_cache, light_cache, relative_path, transform, skip_counts):
    # type: (Any, Any, str, _MeshCache, _LightCache, str, area_types.ResolvedTransform, Counter) -> list
    """
    Imports whatever `relative_path` resolves to and bakes `transform`
    onto the resulting top-level object(s) (objects that already have a
    parent -- either from the mesh cache's own multi-mesh handling, or
    however build() itself structures a fresh multi-mesh .gr2 import,
    which isn't confirmed either way -- inherit their transform through
    that parenting instead, rather than getting the same absolute
    transform applied redundantly).

    Returns the list of created objects (possibly empty).
    """
    resolved_target, is_speedtree = _resolve_import_target(relative_path)
    extension = resolved_target.rsplit(".", 1)[-1].lower() if "." in resolved_target else ""

    if extension == "lit":
        # No file resolution needed at all -- a .lit-tagged element is
        # just a marker to place a generic light, not a real asset on
        # disk we read (see _LightCache's own note on why).
        if not operator.area_create_lights:
            skip_counts["'.lit' skipped (Create Scene Lights disabled)"] += 1
            return []
        light_ob = bpy.data.objects.new(light_cache.object_name(), light_cache.get())
        _apply_transform(light_ob, transform)
        return [light_ob]

    if extension == "mag":
        referenced = _resolve_mag_reference(resources_root, resolved_target)
        if referenced is None:
            skip_counts["'.mag' file missing or has no Mesh= reference"] += 1
            return []
        resolved_target, is_speedtree = _resolve_import_target(referenced)
        extension = resolved_target.rsplit(".", 1)[-1].lower() if "." in resolved_target else ""

    if extension not in BUILDABLE_EXTENSIONS:
        if extension in IN_SCOPE_EXTENSIONS:
            skip_counts["'.%s' -- in scope, importer not built yet" % extension] += 1
        # else: genuinely out of scope (§2a) -- not counted, not warned.
        return []

    on_disk_path = resolve_resource_path(resources_root, resolved_target)
    if on_disk_path is None or not os.path.isfile(on_disk_path):
        skip_counts["file not found on disk"] += 1
        return []

    objects = _import_gr2(operator, context, resources_root, on_disk_path, mesh_cache)
    if not objects:
        skip_counts["'.gr2' importer produced no objects"] += 1
        return []

    objects = _filter_excluded_objects(objects, operator.area_skip_game_engine_objects, skip_counts)
    if not objects:
        return []

    # The SpeedTree "tree"-in-name heuristic (see _reduce_speedtree_lods)
    # is checked against the resolved .gr2's own filename, not whatever
    # label the caller happened to have for this element -- an area
    # element's caller-side name is often just its raw numeric SWTOR id,
    # which could never contain "tree" and would silently defeat this
    # check entirely.
    objects = _reduce_speedtree_lods(objects, is_speedtree, Path(resolved_target).stem)

    for ob in objects:
        if ob.parent is None:
            _apply_transform(ob, transform)

    return objects


def _process_terrain_element(operator, context, resources_root, element, transform, scale_factor, skip_counts):
    # type: (Any, Any, str, area_types.AreaElement, area_types.ResolvedTransform, float, Counter) -> list
    """
    ".hms" elements aren't ".gr2" references at all -- confirmed
    against the old source, not guessed: the real geometry lives at
    resources/world/heightmaps/<element's own numeric SWTOR id>.obj,
    looked up by the ELEMENT'S OWN id, not anything in its assetName
    (unlike every other extension this addon handles). Imported through
    Blender's built-in OBJ importer -- bpy.ops.wm.obj_import is the
    only operator name that applies to our 4.5/5.2 LTS target (the
    pre-4.0 import_scene.obj this addon's own old source also supported
    for older Blender versions is irrelevant here).

    Deliberately does NOT reuse mesh_cache -- each heightmap tile is
    presumably unique to its own element id (no evidence of reuse
    across elements, unlike the .gr2 props the cache exists for). Its
    OWN scale_factor baking (below) is still needed though, matching
    the exact same convention _MeshCache.store() uses for .gr2 meshes
    -- CONFIRMED real bug this fixes: since this path never touched
    mesh_cache at all, nothing was ever baking scale_factor into the
    imported terrain geometry, so heightmaps silently ignored the
    scene's override scale entirely while every other object type
    correctly followed it.

    Unlike the old source, which arbitrarily kept only the first
    resulting object and silently abandoned any others as orphaned,
    unlinked leaks if the .obj ever produced more than one -- this
    keeps every resulting object. Not observed to matter in practice
    (heightmaps appear to always be single-mesh), but costs nothing to
    handle correctly rather than replicate a real bug.
    """
    heightmaps_folder = Path(resources_root) / "world" / "heightmaps"
    if not heightmaps_folder.is_dir():
        skip_counts["'.hms' skipped -- no resources/world/heightmaps folder found"] += 1
        return []

    obj_path = heightmaps_folder / ("%s.obj" % element.id)
    if not obj_path.is_file():
        skip_counts["'.hms' heightmap .obj not found on disk"] += 1
        return []

    objects_before = set(bpy.data.objects)
    result = bpy.ops.wm.obj_import(filepath=str(obj_path))
    # NOTE: the old source checked `result == "CANCELLED"` -- comparing
    # a Blender operator's return value (always a set, e.g.
    # {'CANCELLED'}) to a bare string, which can never be True. That
    # check never actually fired in the old source; not replicated here.
    if 'CANCELLED' in result:
        skip_counts["'.hms' Blender's OBJ importer failed to import it"] += 1
        return []

    imported_objects = list(set(bpy.data.objects) - objects_before)
    if not imported_objects:
        skip_counts["'.hms' OBJ import produced no objects"] += 1
        return []

    if scale_factor != 1.0:
        # Matches _MeshCache.store()'s own approach exactly: bake into
        # the mesh vertex data, not Object.scale -- see this addon's
        # confirmed convention (import_gr2.py's build(): scale_object
        # bakes via transform_apply, Object.scale is reset to its
        # natural value afterward). seen_meshes guards against
        # transforming the same mesh data-block twice, same reasoning
        # as _MeshCache.store(), in case a single .obj ever produces
        # multiple objects sharing one mesh (not observed, but cheap
        # to guard against here too).
        scale_matrix = Matrix.Scale(scale_factor, 4)
        seen_meshes = set()
        for ob in imported_objects:
            if ob.data is not None and id(ob.data) not in seen_meshes:
                ob.data.transform(scale_matrix)
                ob.data.update()
                seen_meshes.add(id(ob.data))

    for i, ob in enumerate(imported_objects):
        ob.name = element.id if i == 0 else "%s.%03d" % (element.id, i)
        _apply_transform(ob, transform)

    return imported_objects


def _process_area_element(operator, context, resources_root, mesh_cache, light_cache, element, transform, scale_factor, skip_counts):
    # type: (Any, Any, str, _MeshCache, _LightCache, area_types.AreaElement, area_types.ResolvedTransform, float, Counter) -> list
    """
    Handles one parsed AreaElement with its already-computed final
    transform: resolves ".spn_p" indirection if present (possibly
    expanding into several ".dyn" visuals, each getting its own composed
    transform -- see types/area.py's compose_dyn_visual_transform), then
    imports whatever that resolves to.

    Returns the list of created objects (possibly empty, including an
    organizational Empty for dyn-expanded elements with multiple visuals
    -- see the dyn branch below).
    """
    asset_name = element.asset_name

    if asset_name.lower().endswith(".hms"):
        return _process_terrain_element(operator, context, resources_root, element, transform, scale_factor, skip_counts)

    if not asset_name.lower().endswith(".spn_p"):
        return _process_element(
            operator, context, resources_root, mesh_cache, light_cache,
            asset_name, transform, skip_counts,
        )

    resolution = area_types.resolve_spn_p(asset_name)
    if resolution is None:
        skip_counts["'.spn_p' path not in spn_table (bundled data may be stale)"] += 1
        return []
    if resolution.target_type == "unresolved":
        # A known, already-investigated dead end recorded at bundled-data
        # generation time (see tools/generate_placeable_tables.py) --
        # not worth a warning every time, unlike a genuinely missing entry.
        return []

    if resolution.target_type == "direct":
        return _process_element(
            operator, context, resources_root, mesh_cache, light_cache,
            resolution.target, transform, skip_counts,
        )

    # resolution.target_type == "dyn": expand into synthetic child
    # visuals, each with its own composed transform.
    visuals = area_types.expand_dyn(resolution.target)
    if not visuals:
        return []

    # Organizational Empty grouping every visual this .spn_p spawned --
    # mirrors the old source's own approach (an Empty per dyn-expanded
    # element holding its resulting objects together) rather than each
    # visual floating independently in the collection. Named from the
    # dyn fqn's own last segment (e.g. "wonkavator_spaceport_vertical")
    # rather than the element's raw numeric SWTOR id -- readable in the
    # outliner, matching the same naming fix applied to actual mesh
    # objects (see _import_gr2's docstring).
    #
    # Each visual object below already has its correct absolute world
    # transform baked on directly (via _process_element, same as any
    # other object); parenting it to parent_empty without correcting
    # matrix_parent_inverse would make Blender reinterpret that
    # already-correct transform as being relative to the parent,
    # double-applying it -- confirmed as a real bug (see _basis_matrix's
    # docstring for the root cause and how this now avoids it).
    empty_name = resolution.target.rsplit(".", 1)[-1]
    parent_empty = bpy.data.objects.new(empty_name, None)
    parent_empty.empty_display_type = 'CUBE'
    parent_empty.empty_display_size = 0.1
    _apply_transform(parent_empty, transform)
    parent_empty_matrix = _basis_matrix(parent_empty)

    all_objects = [parent_empty]
    for visual in visuals:
        visual_transform = area_types.compose_dyn_visual_transform(transform, visual)
        objects = _process_element(
            operator, context, resources_root, mesh_cache, light_cache,
            visual.path, visual_transform, skip_counts,
        )
        for ob in objects:
            if ob.parent is None:
                ob.parent = parent_empty
                ob.matrix_parent_inverse = parent_empty_matrix.inverted()
        all_objects.extend(objects)

    return all_objects


# ---------------------------------------------------------------------------
# Orchestrator + Operator
# ---------------------------------------------------------------------------

def load(operator, context, filepath, resources_root, scale_factor, mesh_cache, light_budget):
    # type: (Any, Any, str, str, float, _MeshCache, _LightBudget) -> bool
    """
    Imports ONE area json file. Assumes `resources_root` is already a
    valid directory and bundled_data/ is already confirmed available --
    both validated once by the caller (ImportAREA.execute(), which
    validates for the whole batch up front) rather than repeated here
    per file. A multi-hundred-file batch import with a missing
    bundled_data/ folder should fail once, immediately, with one clear
    message -- not fail (and report) separately for every single file.

    Similarly, job_results setup/reset is the caller's responsibility
    (once per batch), not this function's -- calling this repeatedly
    for a multi-file import must accumulate into the same job_results,
    not wipe it before every file. `mesh_cache` and `light_budget` are
    likewise shared across a whole batch (created once by the caller),
    not per file -- a source .gr2 reused across many area json files in
    the same zone (a common trim piece, say) benefits from the same
    dedup the cache already provides within a single file, and the
    >100-lights safeguard only means anything if it's tracking the
    running total across the whole batch (see _LightBudget's own note
    on why a per-file-only check doesn't catch the actual failure mode).

    Returns True on success, False on failure (mirrors
    import_gr2.py/import_cha.py's own load() return convention).
    """
    start_time = time.time()

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        operator.report({'ERROR'}, "Couldn't read %s: %s" % (filepath, exc))
        return False

    elements = area_types.parse_area_json(raw_data)
    if not elements:
        operator.report({'WARNING'}, "No recognizable elements found in %s." % filepath)
        return False

    transforms, transform_warnings = area_types.compute_final_transforms(elements, scale_factor=scale_factor)

    area_name = Path(filepath).stem

    # Every collection here -- including the root one -- is created
    # lazily, only the first time something actually needs to be linked
    # into it. Confirmed real complaint: previously root_collection/
    # objects_collection were created unconditionally even for a file
    # that produces zero objects (e.g. a pure-logic file with no
    # .gr2/.spn_p content at all), leaving a pointless empty Collection
    # pair behind every time.
    root_collection = None
    objects_collection = None
    empties_collection = None
    lights_collection = None
    heightmaps_collection = None

    def _ensure_root_collection():
        nonlocal root_collection
        if root_collection is None:
            root_collection = bpy.data.collections.new(area_name)
            context.scene.collection.children.link(root_collection)
        return root_collection

    def _ensure_objects_collection():
        nonlocal objects_collection
        if objects_collection is None:
            root = _ensure_root_collection()
            if operator.area_separate_collections:
                objects_collection = bpy.data.collections.new("%s - Objects" % area_name)
                root.children.link(objects_collection)
            else:
                objects_collection = root
        return objects_collection

    def _ensure_empties_collection():
        nonlocal empties_collection
        if empties_collection is None:
            if operator.area_separate_empties_collection:
                root = _ensure_root_collection()
                empties_collection = bpy.data.collections.new("%s - Empties" % area_name)
                root.children.link(empties_collection)
            else:
                empties_collection = _ensure_objects_collection()
        return empties_collection

    def _ensure_lights_collection():
        nonlocal lights_collection
        if lights_collection is None:
            root = _ensure_root_collection()
            lights_collection = bpy.data.collections.new("%s - Lights" % area_name)
            root.children.link(lights_collection)
        return lights_collection

    def _ensure_heightmaps_collection():
        nonlocal heightmaps_collection
        if heightmaps_collection is None:
            root = _ensure_root_collection()
            heightmaps_collection = bpy.data.collections.new("%s - Heightmaps" % area_name)
            root.children.link(heightmaps_collection)
        return heightmaps_collection

    light_cache = _LightCache(area_name, scale_factor)

    skip_counts = Counter()
    crashed_elements = []
    total_objects = []

    for element in elements:
        transform = transforms.get(element.id)
        if transform is None:
            continue

        # One malformed/unexpected element (e.g. a resolved .gr2 the
        # underlying importer chokes on) shouldn't take down an import
        # of several hundred/thousand other, unrelated elements --
        # confirmed real failure mode. Mirrors the old source's own
        # "the .gr2 Importer addon CRASHED ... despite that, keep
        # importing the rest" handling.
        try:
            objects = _process_area_element(
                operator, context, resources_root, mesh_cache, light_cache, element, transform, scale_factor, skip_counts,
            )
        except Exception as exc:
            crashed_elements.append("%s (%s): %s" % (element.id, element.asset_name, exc))
            continue

        for ob in objects:
            # ImportGR2_load() links freshly-created objects into
            # whatever collection is active in the view layer at import
            # time -- move them to this area's proper collection instead,
            # same pattern as import_cha.py's assemble_collections().
            #
            # CONFIRMED BUG (fixed here): the previous version guarded
            # the link() call with "if not ob.users_collection" -- i.e.
            # "does this object have ANY collection membership at all",
            # rather than "is it specifically in [target] collection".
            # A freshly-imported object arrives already linked to
            # context.collection (from ImportGR2_load's own default
            # linking), so that guard was always False for it -- meaning
            # it never got linked to its proper collection at all, and the
            # very next loop then unconditionally unlinked it from
            # context.collection anyway, leaving it linked to NOTHING.
            # Cache-instantiated repeat objects (which start completely
            # unlinked, unlike a fresh import) accidentally avoided this,
            # which is exactly why only ".001"+ duplicates were ever
            # visible and every "first" instance of a given mesh
            # silently vanished.
            #
            # Heightmap ('.hms') objects are routed by ELEMENT, not by
            # ob.type -- unlike Empties/Lights, a heightmap Object's own
            # .type is 'MESH' (same as a regular imported prop), so
            # there's no per-object signal to key off; this element
            # produced terrain, so every object it returned is terrain.
            #
            # Organizational Empties (grouping .dyn-expanded visuals)
            # route to their own collection when area_separate_empties_collection
            # is on, same reasoning as area_separate_collections for
            # regular mesh objects -- kept as a separate toggle since
            # someone may want mesh objects grouped but not care about
            # a dedicated Empties collection, or vice versa. Lights and
            # Heightmaps always get their own collection when any exist
            # (unlike Objects/Empties, not gated behind a
            # grouping-preference toggle) -- but, like Objects/Empties,
            # still created lazily, so a file with none of a given kind
            # doesn't leave a pointless empty collection behind.
            if element.asset_name.lower().endswith(".hms"):
                target_collection = _ensure_heightmaps_collection()
            elif ob.type == 'EMPTY':
                target_collection = _ensure_empties_collection()
            elif ob.type == 'LIGHT':
                target_collection = _ensure_lights_collection()
            else:
                target_collection = _ensure_objects_collection()
            if target_collection not in ob.users_collection:
                target_collection.objects.link(ob)
            for existing in list(ob.users_collection):
                if existing is not target_collection:
                    existing.objects.unlink(ob)
        total_objects.extend(objects)

    # Registered with the batch-wide light_budget (shared across every
    # file in this execute() call), not checked against a per-file
    # threshold here -- see _LightBudget's own note on why a per-file
    # check doesn't catch the actual failure mode this exists for.
    light_budget.register(context, lights_collection)

    if operator.area_hide_collections_after_import and root_collection is not None:
        # Excluding the ROOT collection is enough -- Blender's view-layer
        # exclusion cascades to every child collection (Objects/Empties/
        # Lights/Heightmaps) automatically, same reasoning confirmed
        # already for the light-count safeguard, just applied to the
        # whole area here instead of only its Lights collection. No
        # per-file threshold or cumulative state needed (unlike
        # _LightBudget) -- this is a plain per-file toggle, not
        # something that only matters once a running total crosses a
        # threshold, so it's applied directly here rather than through
        # any shared batch-wide object.
        context.view_layer.update()
        layer_collection = _find_layer_collection(context.view_layer.layer_collection, root_collection)
        if layer_collection is not None:
            layer_collection.exclude = True
        else:
            print(
                "Area Assembler [%s]: couldn't find \"%s\"'s LayerCollection to "
                "exclude it -- left visible. Please report this if it keeps happening."
                % (os.path.basename(filepath), root_collection.name)
            )

    job_results['objs_names'].extend(ob.name for ob in total_objects)

    file_label = os.path.basename(filepath)
    elapsed_time = time.time() - start_time

    if transform_warnings:
        print("Area Assembler [%s]: %d transform warning(s):" % (file_label, len(transform_warnings)))
        for w in transform_warnings[:20]:
            print("  " + w)

    if skip_counts:
        print("Area Assembler [%s]: elements skipped, by reason:" % file_label)
        for reason, count in skip_counts.most_common():
            print("  %5d  %s" % (count, reason))

    if crashed_elements:
        print("Area Assembler [%s]: %d element(s) raised an unexpected error and were skipped:" % (file_label, len(crashed_elements)))
        for line in crashed_elements[:20]:
            print("  " + line)
        if len(crashed_elements) > 20:
            print("  ... and %d more (see full console output)" % (len(crashed_elements) - 20))

    if skip_counts or crashed_elements:
        parts = []
        if skip_counts:
            parts.append("%d element(s) skipped across %d reason(s)" % (sum(skip_counts.values()), len(skip_counts)))
        if crashed_elements:
            parts.append("%d element(s) raised an error" % len(crashed_elements))
        operator.report({'WARNING'}, "[%s] " % file_label + "; ".join(parts) + " -- see console for details.")

    # Matches the old importer's own "FILE:/OBJS:/TIME:" console
    # convention (and import_gr2.py's own per-file timing) -- confirmed
    # missed, added back.
    print("FILE: %s" % filepath)
    print("OBJS: %d" % len(total_objects))
    print("TIME: %s" % _format_duration(elapsed_time))
    print()

    return bool(total_objects)


class ImportAREA(bpy.types.Operator):
    """
    Import a SWTOR area .json export (Area Assembler).

    Places every recognized element (.gr2/.mag/.spt meshes, resolved
    .spn_p placeables and their .dyn-expanded visuals) at its correct
    final position/rotation/scale, reusing mesh data for repeated
    source files rather than reimporting them each time.

    Supports selecting multiple .json files at once in the file browser
    (each becomes its own named Collection, same as importing them one
    at a time) -- with one deliberate difference from this add-on's
    other multi-file importers (e.g. ImportGR2): a file that fails
    doesn't abort the rest of the batch. Area files are commonly split
    across dozens/hundreds of .json files for one zone, and losing the
    whole batch over one bad file would be a worse outcome than
    reporting that file's failure and importing everything else.
    """
    bl_idname = "import_scene.swtor_area"
    bl_label = "Import SWTOR Area (.json)"
    bl_description = "Import a SWTOR area .json export"
    bl_options = {'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    files: bpy.props.CollectionProperty(
        name="File Path",
        description="File path(s) used for importing the area .json file(s)",
        type=bpy.types.OperatorFileListElement,
    )
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    area_separate_collections: bpy.props.BoolProperty(
        name="Separate Objects Collection",
        description=(
            "Nests all imported objects in a child \"<Area Name> - Objects\" "
            "Collection instead of directly in the area's root Collection"
        ),
        default=True,
    )

    area_process_materials: bpy.props.BoolProperty(
        name="Process Materials",
        description=(
            "Looks up every imported object's materials by name against real "
            ".mat files in the Resources Directory and builds native SWTOR "
            "shaders for them, once, after every selected file has finished "
            "importing (not per file -- a material shared across many area "
            "files only gets built once either way, but running the pass "
            "once at the end keeps that dedup working across the whole batch "
            "rather than resetting per file)"
        ),
        default=True,
    )

    area_create_lights: bpy.props.BoolProperty(
        name="Create Scene Lights",
        description=(
            "Places a generic placeholder Point light for every \".lit\"-tagged "
            "element (real per-light color/intensity data isn't available yet -- "
            "richer light types are planned as separate future work). Lights "
            "beyond a per-file threshold get their Collection auto-excluded from "
            "the view layer to protect viewport performance"
        ),
        default=True,
    )

    area_skip_game_engine_objects: bpy.props.BoolProperty(
        name="Skip Game Engine Objects",
        description=(
            "Don't import non-visual, tooling-only content: design blockout "
            "(DBO) objects such as blockers and markers (identified by their "
            "own filename starting with \"dbo\"), plus objects using known "
            "non-visual game-engine materials such as occluders, portals, and "
            "collision (identified by material name, since these often can't "
            "be recognized by filename alone). Not related to any object "
            "that's genuinely named/labeled \"Utility\" in-game -- this only "
            "targets engine-internal tooling content"
        ),
        default=True,
    )

    area_merge_multi_mesh_objects: bpy.props.BoolProperty(
        name="Merge Multi-Mesh Objects",
        description=(
            "Joins every sub-object a single .gr2 file produces into one "
            "combined object, renamed to that .gr2's own filename, instead of "
            "leaving them as separate objects (often with meaningless default "
            "names like \"Object17\"/\"NGon03\", since a .gr2's own internal "
            "sub-part names don't always reflect anything meaningful)"
        ),
        default=False,
    )

    area_hide_collections_after_import: bpy.props.BoolProperty(
        name="Hide Collections After Importing",
        description=(
            "Excludes each imported area's root Collection from the view layer "
            "once it's finished importing, so its contents (and everything "
            "nested under it -- Objects/Empties/Lights/Heightmaps) don't slow "
            "down the Outliner or viewport on a large batch import. Re-enable "
            "a hidden area's Collection in the Outliner whenever you want to "
            "see it again"
        ),
        default=False,
    )

    area_separate_empties_collection: bpy.props.BoolProperty(
        name="Separate Empties Collection",
        description=(
            "Nests organizational Empties (grouping each .dyn-expanded "
            "placeable's resulting objects) in a child \"<Area Name> - Empties\" "
            "Collection instead of alongside regular mesh objects"
        ),
        default=True,
    )

    area_open_console: bpy.props.BoolProperty(
        name="Open System Console",
        description=(
            "Opens Blender's system console (Windows only) before starting, so "
            "warnings/progress are visible without remembering to open it "
            "manually first. Blender only exposes a TOGGLE for this, not a way "
            "to check whether it's already open -- if it happens to already be "
            "open, this will close it instead. Never auto-closes at the end, "
            "since the point is being able to review the summary afterward"
        ),
        default=True,
    )

    # Always True for this operator -- Area Assembler bakes its own
    # final transforms (position/rotation/scale, scale_factor included)
    # onto every object itself, so its internal .gr2 imports need raw,
    # unscaled geometry rather than each individually re-applying the
    # add-on's own scale settings on top. Unlike ImportCHA (which always
    # leaves this False, sourcing scale from Preferences instead), this
    # is a genuine, deliberate difference between the two importers, not
    # an oversight -- confirmed in design discussion.
    enforce_neutral_settings: bpy.props.BoolProperty(options={'HIDDEN'}, default=True)

    # Not exposed in the panel: import_gr2.py's load() reads this
    # attribute unconditionally before it even checks bl_idname, same
    # reasoning as ImportCHA's own copy of this property.
    job_results_rich: bpy.props.BoolProperty(options={'HIDDEN'}, default=False)

    # Matches ImportGR2's own property of the same name/purpose: lets
    # external code trigger several import_scene.swtor_area calls in a
    # row (not just this operator's own multi-file selection) without
    # each call wiping job_results from the previous one.
    job_results_accumulate: bpy.props.BoolProperty(options={'HIDDEN'}, default=False)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.area_open_console:
            import platform
            if platform.system() == 'Windows':
                bpy.ops.wm.console_toggle()

        prefs = bpy.context.preferences.addons["swtor_io_tools"].preferences
        resources_root = prefs.swtor_resources_dir

        if not resources_root or not Path(resources_root).is_dir():
            self.report({'ERROR'}, "Set a valid Resources Folder in this add-on's preferences first.")
            return {'CANCELLED'}

        # Validated once for the whole batch -- see load()'s own
        # docstring for why this isn't repeated per file.
        try:
            area_types.ensure_bundled_data_available()
        except area_types.BundledDataError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        scale_factor = prefs.gr2_scale_factor if prefs.gr2_scale_object else 1.0

        paths = [
            os.path.join(self.directory, file.name)
            for file in self.files
            if file.name.lower().endswith(self.filename_ext)
        ]
        if not paths:
            paths.append(self.filepath)

        # Clear filebrowser-related properties now that they've been
        # read, same reasoning as ImportGR2's own execute(): keeps them
        # from persisting if this breaks before finishing, which makes
        # debugging harder otherwise.
        self.files.clear()
        self.filepath = ""

        if not self.job_results_accumulate:
            job_results['objs_names'] = []
            job_results['files_objs_names'] = {}
        job_results['job_origin'] = self.bl_idname

        mesh_cache = _MeshCache(scale_factor=scale_factor)
        light_budget = _LightBudget(LIGHT_COUNT_AUTO_EXCLUDE_THRESHOLD)

        batch_start_time = time.time()
        succeeded = []
        failed = []
        for path in paths:
            if load(self, context, path, resources_root, scale_factor, mesh_cache, light_budget):
                succeeded.append(path)
            else:
                failed.append(path)
        batch_elapsed_time = time.time() - batch_start_time

        if len(paths) > 1:
            print("Area Assembler: batch of %d file(s) complete." % len(paths))
            print("TOTAL TIME: %s" % _format_duration(batch_elapsed_time))
            print()

        if succeeded and self.area_process_materials:
            materials_start_time = time.time()
            results = apply_materials_by_name_to_gr2_objects(job_results['objs_names'])
            materials_elapsed_time = time.time() - materials_start_time
            print("Area Assembler: materials processing complete.")
            print("MATERIALS TIME: %s" % _format_duration(materials_elapsed_time))
            print()
            _summarize_and_report(self, results)

        bpy.context.scene.swtor_io_last_job = json.dumps(job_results)

        if failed:
            self.report(
                {'WARNING'},
                "%d of %d file(s) failed to import (%d succeeded) -- see console for per-file details."
                % (len(failed), len(paths), len(succeeded)),
            )

        if not succeeded:
            return {'CANCELLED'}
        return {'FINISHED'}