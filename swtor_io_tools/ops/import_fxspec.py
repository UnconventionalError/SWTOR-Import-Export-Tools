# <pep8 compliant>

"""
.fxspec -- Operator layer (standalone File > Import entry point).

Builds real Blender objects from types/fxspec.py's parsed attach
graph: a plain Empty (sphere display) per recognized dummy/anchor
emitter, and a real .gr2 import per model entry, each parented
directly to whatever it resolves to in the graph.

Scope for this pass (see design discussion): standalone import only.
CASTER anchors at world origin, baked directly into whichever entry
sits at the top of a given chain (see _place_entry) -- UNLESS the
active object is an Armature and has the bone a chain entry actually
names, in which case that entry (and everything parented under it)
follows the bone live via a Copy Transforms constraint instead, since
a posable bone genuinely can't be baked once and forgotten. TARGET
follows the exact same rule as CASTER for now -- Crunch's own call,
explicitly flagged as provisional pending a real ability-effect test
case, since there's no natural second scene object to resolve it
against yet.

build_fxspec_graph() is also reused directly by Area Assembler's own
`.fxp` element handling (see ops/import_area.py's own
_process_fxspec_element) via the `caster_object` parameter -- see that
parameter's own docstring below. Standalone import was locked in and
live-tested first (it's the only one of the two entry points that can
exercise the deep attach-chain resolver AND the bone-anchor path with
the mtx_gearfx_sovex_chest_v01_bfs sample), and Case 1 of the area
integration (a `.fxp` element whose own asset_name authors the fx spec
directly, per the fxspec/area integration handoff) is the only case
wired up so far -- a `.fxp` reached indirectly through a `.spn_p` ->
`.dyn` expansion (Case 2) is still deferred, its own future session.

IMPORTANT ARCHITECTURAL NOTE, since it's a real departure from this
add-on's own established convention: ops/import_area.py's own header
explicitly says it deliberately avoids live Blender parenting for
transform inheritance, baking absolute transforms in Python instead.
This module does NOT follow that pattern -- it leans on real Blender
parent-child composition (and a Copy Transforms constraint for the
bone-follow case) on purpose, not as an oversight. The reasons area.py
bakes absolute transforms don't apply here: an area json element ships
an already-fully-resolved `finalPosition` per element (computed once by
SWTOR's own exporter), so there's nothing left to compose. A .fxspec
attach graph has no such thing -- each entry only ever authors a LOCAL
offset relative to a named parent -- and the bone-follow case
specifically needs to track a live (possibly posed) armature bone,
which a one-time bake structurally cannot do. Real Blender parenting is
the right tool for both of those reasons, not just a shortcut.
"""

import json
import math
import os
from pathlib import Path
from typing import Callable, Optional

import bpy
from bpy.types import Context, Operator, OperatorFileListElement
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from mathutils import Euler, Matrix

from ..types import fxspec as fxspec_types
from .import_area import _MeshCache, _job_results_key
from .import_cha import resolve_resource_path
from .import_gr2 import load as ImportGR2_load
from .process_materials import apply_materials_by_name_to_gr2_objects, _summarize_and_report
from ..types.shared import job_results


# ---------------------------------------------------------------------------
# .gr2 import (mirrors ops/import_area.py's own _import_gr2, minus the
# "Merge Multi-Mesh Objects" option -- not carried over in this first
# pass; every returned sub-object from a multi-mesh .gr2 gets parented
# independently instead, same fallback behaviour _MeshCache itself
# already documents for the no-merge case).
# ---------------------------------------------------------------------------

def _import_gr2_objects(operator, context, resources_root, resolved_path, mesh_cache):
    # type: (Operator, Context, str, str, _MeshCache) -> list
    if mesh_cache.has(resolved_path):
        return mesh_cache.instantiate(resolved_path, Path(resolved_path).stem)

    operator.job_results_rich = True
    ImportGR2_load(operator, context, resolved_path)

    object_names = job_results.get("files_objs_names", {}).get(_job_results_key(resolved_path), [])
    imported_objects = [bpy.data.objects[n] for n in object_names]
    mesh_cache.store(resolved_path, imported_objects)
    return imported_objects


# ---------------------------------------------------------------------------
# CASTER / TARGET anchor resolution
# ---------------------------------------------------------------------------

def _find_bone(armature_ob, bone_name):
    # type: (bpy.types.Object, str) -> bool
    """True if `armature_ob`'s data has a bone (pose or edit, same
    names either way) matching `bone_name` case-insensitively -- SWTOR
    bone names are seen in both cases across different exports
    elsewhere in this add-on, so an exact-case match alone would be
    too strict here."""
    if armature_ob is None or armature_ob.type != 'ARMATURE':
        return False
    target = bone_name.strip().lower()
    return any(b.name.lower() == target for b in armature_ob.data.bones)


def _make_bone_follower(collection, armature_ob, bone_name, cache):
    # type: (bpy.types.Collection, bpy.types.Object, str, dict) -> bpy.types.Object
    """
    Returns a plain Empty that exactly tracks `bone_name` on
    `armature_ob` via a Copy Transforms constraint (no offset of its
    own) -- cached per (armature, bone) so multiple chain entries
    naming the same bone share one follower rather than each getting
    their own redundant constraint chain.

    `cache` is shared across every .fxspec file in one Import
    operator run (passed down from execute(), not created fresh per
    file) -- per Crunch's own "feels a bit redundant" complaint about
    an earlier version's separate CASTER/TARGET anchor, this follower
    has the exact same multi-file duplication risk (unlike that
    anchor, though, a bone follower can't just be baked away -- it
    genuinely needs a live constraint to track a posable bone -- so
    it's kept, just made properly shared).

    Also checks bpy.data.objects for an already-existing follower with
    the expected name and a matching constraint target/subtarget
    BEFORE creating a new one, so re-running the importer in the same
    Blender session (a normal part of testing this) doesn't pile up
    "fxspec_bone_anchor: vfx_chest.001", ".002", etc. either. Falls
    through to creating a fresh one if the name exists but doesn't
    actually match (e.g. a leftover from a different armature that
    happens to share a bone name) rather than silently reusing the
    wrong object.

    Deliberately carries no authored offset itself: the entry that
    actually named this bone (via _fxAttachBone) still gets its own
    _fxAttachPosition/_fxAttachRotation/_fxScale applied normally, by
    being parented to this Empty like any other chain link -- this
    follower's only job is to put a live, correctly-posed anchor point
    in the graph for that parenting to attach to.
    """
    key = (armature_ob.name, bone_name.lower())
    existing = cache.get(key)
    if existing is not None:
        return existing

    # Flat, uncompensated -- see DUMMY_EMPTY_DISPLAY_SIZE's own docstring
    # for why (mirrors the mesh's own scaling: propagate through
    # inherited scale naturally, don't fight it). Applied on BOTH the
    # reuse and fresh-create paths below, not just at creation time --
    # a follower reused from an EARLIER run (e.g. before this fix
    # shipped, or before a preference change) had its stale size
    # refreshed here too.
    display_size = DUMMY_EMPTY_DISPLAY_SIZE

    expected_name = "fxspec_bone_anchor: %s" % bone_name
    existing_ob = bpy.data.objects.get(expected_name)
    if (existing_ob is not None and existing_ob.type == 'EMPTY'
            and len(existing_ob.constraints) == 1
            and existing_ob.constraints[0].type == 'COPY_TRANSFORMS'
            and existing_ob.constraints[0].target == armature_ob
            and existing_ob.constraints[0].subtarget.lower() == bone_name.strip().lower()):
        if collection not in existing_ob.users_collection:
            collection.objects.link(existing_ob)
        existing_ob.empty_display_size = display_size
        cache[key] = existing_ob
        return existing_ob

    follower = bpy.data.objects.new(expected_name, None)
    follower.empty_display_type = 'PLAIN_AXES'
    follower.empty_display_size = display_size
    follower.show_in_front = True
    collection.objects.link(follower)

    constraint = follower.constraints.new('COPY_TRANSFORMS')
    constraint.target = armature_ob
    constraint.subtarget = next(
        b.name for b in armature_ob.data.bones if b.name.lower() == bone_name.strip().lower()
    )

    cache[key] = follower
    return follower


# ---------------------------------------------------------------------------
# Graph -> Blender objects
# ---------------------------------------------------------------------------

DUMMY_EMPTY_DISPLAY_SIZE = 0.004  # A flat, UNCOMPENSATED empty_display_size --
                                  # deliberately not divided by scale_factor or
                                  # any inherited scale. An earlier version tried
                                  # compensating so the RENDERED size stayed
                                  # constant regardless of scale_factor, which
                                  # backfired badly: it fought against the
                                  # correct behaviour instead of matching it. Once
                                  # mesh vertex-baking was removed (scale_factor
                                  # now flows into geometry purely through
                                  # inherited Object.scale, same mechanism as
                                  # everything else), the marker's own size
                                  # should propagate through that SAME inherited
                                  # scale exactly like the mesh it's marking does
                                  # -- flat and uncompensated is what makes that
                                  # happen. 0.004 is scale_factor=10's own
                                  # confirmed-correct fxspec_bone_anchor value,
                                  # carried over unchanged now that there's
                                  # nothing left to compensate for.

# Same rotation types/area.py's own _SWTOR_TO_BLENDER_AXIS_CONVERSION applies
# (a plain +90 degree rotation about X) -- see that module for the
# derivation. Expressed here as a real mathutils.Matrix (this module runs
# inside Blender, unlike types/area.py's own pure-Python 3x3 matrices, which
# have to avoid a bpy/mathutils dependency to stay testable standalone).
_SWTOR_TO_BLENDER_ROOT_MATRIX = Matrix.Rotation(math.radians(90.0), 4, 'X')

# Stable warning categories `warn(category, message)` (see
# build_fxspec_graph's own docstring for the callback contract) can
# report. Deliberately plain string constants rather than an enum --
# consumed across a module boundary (ops/import_area.py buckets these
# into its own skip_counts reporting), and a plain string compares/
# hashes/prints without needing the enum type itself imported too.
# Every warn() call site below uses exactly one of these.
WARN_NO_ARMATURE_BONE = "no_armature_bone"                    # bone authored, no matching Armature/bone available
WARN_GR2_NOT_FOUND = "gr2_not_found"                          # model .gr2 missing on disk
WARN_GR2_NO_OBJECTS = "gr2_no_objects"                        # model .gr2 importer produced nothing
WARN_DANGLING_REFERENCE = "dangling_reference"                # attach chain references an unknown entry
WARN_CHAIN_CYCLE = "chain_cycle"                               # attach chain cycles back on itself
WARN_REAL_EMITTER_SKIPPED = "real_emitter_skipped"            # real particle emitter, not built (Phase 1 scope)
WARN_NON_ESSENTIAL_DUMMY_SKIPPED = "non_essential_dummy_skipped"  # dummy/anchor nothing attaches through

# Categories that are an ordinary, by-design consequence of Phase 1
# scope or missing-Armature-in-context -- NOT a sign anything is
# actually wrong with the file being imported. Every .fxspec with real
# particle content trips WARN_REAL_EMITTER_SKIPPED and usually
# WARN_NON_ESSENTIAL_DUMMY_SKIPPED too, on close to every entry --
# confirmed genuinely alarming-LOOKING at a self.report({'WARNING'}, ...)
# level despite being entirely expected. The remaining four categories
# (missing/empty .gr2, dangling reference, cycle) indicate something
# about the specific file actually is off, and stay WARNING-worthy.
WARN_EXPECTED_CATEGORIES = {WARN_NO_ARMATURE_BONE, WARN_REAL_EMITTER_SKIPPED, WARN_NON_ESSENTIAL_DUMMY_SKIPPED}


def _compute_essential_dummy_names(graph):
    # type: (dict) -> set
    """
    Every dummy/anchor entry that at least one MODEL entry actually
    depends on for its own placement -- i.e. walking up from every
    model's own parent_name chain and marking each dummy ancestor along
    the way. Anything NOT in this set is only ever attached-to by real
    (skipped) particle emitters or other now-orphaned dummies -- pure
    plumbing for effects this add-on doesn't build anything for yet,
    per Crunch's own "a lot of empties which feel redundant... not ones
    for particle effects" call. Used to gate the default (off) case of
    the "Import All Empties" option -- see ImportFXSPEC's own property.
    """
    essential = set()

    def _mark_ancestors(name_upper, visited):
        if name_upper in fxspec_types.VIRTUAL_ROOTS or name_upper in visited:
            return
        visited.add(name_upper)
        entry = graph.get(name_upper)
        if entry is None:
            return
        if entry.kind == "emitter" and entry.is_dummy:
            essential.add(name_upper)
        _mark_ancestors(entry.parent_name.upper(), visited)

    for entry in graph.values():
        if entry.kind == "model":
            _mark_ancestors(entry.parent_name.upper(), set())

    return essential


def _compute_fan_out_counts(graph, essential_dummy_names):
    # type: (dict, set) -> dict
    """
    Direct-children count (models + OTHER essential dummies) per
    essential dummy name -- used to decide which essential dummies
    actually need to become a real Empty (a genuine branch point with
    2+ things depending on it) versus which are pure 1:1 pass-throughs
    whose own offset can just be folded directly into their single
    child instead. CONFIRMED against a live test: Crunch's own gear-fx
    file builds a `dum_X` Empty AND a `mesh_X` container Empty AND the
    real imported mesh object for every single arm piece, when the
    `dum_X` anchor has exactly one thing (that one mesh) ever attached
    to it -- "get rid of every single empty we can when meshes can be
    parented to other meshes". A model or a fan-out (2+ children)
    essential dummy is already a real object on its own merit and
    doesn't need this treatment -- only 1:1 pure-dummy pass-throughs
    benefit from being eliminated. See _resolve()'s own "collapsible"
    handling for where this actually gets used.
    """
    counts = {name: 0 for name in essential_dummy_names}
    for entry in graph.values():
        is_relevant_child = entry.kind == "model" or (
            entry.kind == "emitter" and entry.is_dummy and entry.name.upper() in essential_dummy_names
        )
        if is_relevant_child:
            parent_upper = entry.parent_name.upper()
            if parent_upper in counts:
                counts[parent_upper] += 1
    return counts


def _local_matrix(entry):
    # type: (fxspec_types.FxAttachEntry) -> Matrix
    """entry's own local position/rotation/scale as a real 4x4 Matrix,
    ready to compose with a parent's own matrix (or with other
    collapsed entries' matrices -- see _resolve). entry.local_rotation
    is already in Blender's default 'XYZ' Euler convention -- see
    types/fxspec.py's _yaw_pitch_roll_to_xyz_euler_degrees."""
    return (
        Matrix.Translation(entry.local_position)
        @ Euler(tuple(math.radians(d) for d in entry.local_rotation), 'XYZ').to_matrix().to_4x4()
        @ Matrix.Diagonal((entry.local_scale[0], entry.local_scale[1], entry.local_scale[2], 1.0))
    )


def _snap_euler_degrees(euler, epsilon_degrees=1e-4):
    # type: (Euler, float) -> Euler
    """
    Snaps any component of `euler` (radians, as mathutils.Euler always
    stores/returns) within epsilon_degrees of zero to exactly 0.0.

    CONFIRMED BUG this fixes (live test): a matrix's own
    Matrix.decompose() -> Quaternion -> to_euler('XYZ') round-trip
    introduces tiny floating-point noise even for a perfectly clean
    authored rotation -- Crunch's own report was dum_backpack_base
    showing an X rotation of -0.000003 degrees and Y of -0.000004
    degrees (Z was a clean -90) when nothing in the source data implies
    any X/Y component at all. Numerically harmless (well under any
    real tolerance) but confusing to stare at in the N-panel. Only
    exact zero gets snapped -- deliberately not rounding the whole
    value to a coarser precision, which could mask a genuine small
    intentional rotation.
    """
    epsilon = math.radians(epsilon_degrees)
    return Euler(tuple(0.0 if abs(c) < epsilon else c for c in euler), euler.order)


def build_fxspec_graph(operator, context, resources_root, root_node, collection,
                        mesh_cache, scale_factor, armature_ob, bone_follower_cache,
                        import_all_empties, warn, caster_object=None):
    # type: (Operator, Context, str, fxspec_types.FxNode, bpy.types.Collection, _MeshCache, float, Optional[bpy.types.Object], dict, bool, Callable[[str, str], None], Optional[bpy.types.Object]) -> dict
    """
    Builds every buildable entry (dummy-anchor emitters + models) from
    `root_node`'s attach graph into real, correctly-parented Blender
    objects in `collection`. Returns {UPPERCASED entry name: built
    Object} for whichever entries actually produced one.

    `warn(category, message)` -- category is always one of this
    module's own WARN_* constants (see their own definitions, right
    above _compute_essential_dummy_names, for what each one means);
    message is the existing full, human-readable per-entry detail
    string. Standalone import ignores category and just keeps message
    (see ImportFXSPEC.execute()'s own wrapper) -- a single file's
    console dump doesn't need bucketing. Area integration buckets by
    category into its own skip_counts-style aggregate reporting instead
    of dumping every message raw -- see ops/import_area.py's own
    _process_fxspec_element for why (a whole area's worth of .fxspec
    files, several of these categories firing on nearly every entry,
    made the previous single-bucket-of-raw-messages approach genuinely
    hard to read -- looked like a wall of errors even though most of
    it was expected, benign skips).

    `armature_ob` is the CASTER/TARGET bone-lookup source (or None --
    "no armature selected", the standalone default). `bone_follower_cache`
    is shared across every file in one Import operator run -- see
    _make_bone_follower's own docstring for why. `import_all_empties`
    controls whether a dummy/anchor entry gets built even when nothing
    real ultimately depends on it, AND whether 1:1 pass-through dummies
    get collapsed away -- see _resolve's own docstring.

    `caster_object`, when given, is an already-placed Object (e.g. Area
    Assembler's own anchor Empty for a `.fxp` element) that CASTER/TARGET
    should resolve to directly -- ordinary parenting from there, exactly
    like resolving through any other real built object in the graph, with
    NO root-bake (see _resolve's VIRTUAL_ROOTS branch and _place_combined).
    This is the area-integration case: the caller is expected to have
    already applied `scale_factor` onto `caster_object`'s own Object.scale
    itself (Area Assembler's own convention keeps scale_factor OUT of
    mesh_cache's cached vertex data when caster_object is used -- see
    ops/import_area.py's own _process_fxspec_element for why), so ordinary
    Blender parent-child composition carries it down through every graph
    entry's own local offset AND onto whatever raw, unscaled geometry
    `mesh_cache` returns, uniformly. Left as None (the default), CASTER/
    TARGET resolve to world origin instead, and `scale_factor` gets baked
    directly into whichever entry sits at the effective top of a chain
    (the standalone importer's own case -- see _place_combined). Note
    `scale_factor` is STILL used even when `caster_object` is given -- as
    the fallback for a dangling/cyclic reference that can't reach a real
    parent at all (rare, but the existing fallback-to-CASTER-and-bake
    behaviour is still the most sensible thing to do in that edge case,
    even in area context -- see _resolve's dangling/cycle branches, which
    deliberately still return a plain (None, Identity) rather than routing
    through caster_object).

    THREE separate reasons an entry might not get its own Blender
    object, all handled by the single _resolve() closure below:
      1. A real (non-dummy) particle emitter -- Phase 1 scope, per
         Crunch's own call nothing stands in for these yet.
      2. A dummy/anchor that nothing built ever attaches through
         (only feeds real emitters) -- default-off "Import All
         Empties" gate.
      3. A dummy/anchor with exactly ONE thing depending on it (no
         real branching) -- its own offset (and bone-anchor role, if
         it has one) is folded directly into that one child instead of
         getting a separate Empty. CONFIRMED this was needed live:
         Crunch's own gear-fx test produced a `dum_X` Empty, a `mesh_X`
         container Empty, AND the real mesh object for every arm piece
         -- three objects for one visual part. After this and the
         single-object-model container elimination below, that's just
         the one real mesh object, parented directly to whatever the
         collapsed dummy chain's own true anchor is.

    CASTER/TARGET themselves are NOT a real Blender object at all --
    the SWTOR-to-Blender axis conversion and scale_factor are a fixed,
    one-time correction, baked directly into whichever entry ends up
    sitting at the effective top of a given chain (see _resolve's
    "needs_real_object" branch, root-bake case) rather than creating
    a persistent anchor object for it (an earlier version did exactly
    that -- dropped per Crunch's own "feels a bit redundant" call). A
    specific entry that authors a bone name while attaching (directly,
    or via a collapsed chain) to CASTER/TARGET is the one case that
    DOES get a live object -- a bone-follower Empty with a Copy
    Transforms constraint, since a posable bone genuinely can't be
    baked once and forgotten. Per the module docstring, TARGET
    intentionally resolves exactly like CASTER for now.

    `warn` is called with a single string for every non-fatal issue --
    collected by the caller for a single end-of-import report rather
    than raised per entry, per this add-on's own "warnings over hard
    blocks" preference.
    """
    graph = fxspec_types.resolve_attach_graph(root_node)
    essential_dummy_names = _compute_essential_dummy_names(graph)
    fan_out_counts = _compute_fan_out_counts(graph, essential_dummy_names)

    built = {}         # UPPER name -> Object, for entries that got a real object
    resolve_cache = {}  # UPPER name -> (parent_ob_or_None, extra_matrix), memoized
    resolving = set()   # cycle guard -- names currently mid-resolution

    def _place_combined(ob, parent_ob, combined_matrix):
        # type: (bpy.types.Object, Optional[bpy.types.Object], Matrix) -> None
        """
        Places `ob` at `combined_matrix` (already fully composed --
        entry's own local matrix, plus anything folded in from
        collapsed ancestors -- see _resolve) relative to `parent_ob`,
        or bakes the SWTOR-to-Blender root conversion directly into it
        when `parent_ob` is None (this entry's chain resolved straight
        to CASTER/TARGET with no bone).
        """
        if parent_ob is not None:
            ob.parent = parent_ob
            world_matrix = combined_matrix
        else:
            scale_matrix = Matrix.Diagonal((scale_factor, scale_factor, scale_factor, 1.0))
            world_matrix = _SWTOR_TO_BLENDER_ROOT_MATRIX @ scale_matrix @ combined_matrix
            ob.parent = None

        translation, rotation_quat, scale = world_matrix.decompose()
        ob.location = translation
        ob.rotation_euler = _snap_euler_degrees(rotation_quat.to_euler('XYZ'))
        ob.scale = scale

    def _build_real_object(entry, name_upper):
        # type: (fxspec_types.FxAttachEntry, str) -> Optional[bpy.types.Object]
        if name_upper in built:
            return built[name_upper]

        this_local = _local_matrix(entry)
        if entry.attach_bone and armature_ob is not None and _find_bone(armature_ob, entry.attach_bone):
            parent_ob = _make_bone_follower(collection, armature_ob, entry.attach_bone, bone_follower_cache)
            combined = this_local
        else:
            if entry.attach_bone:
                warn(WARN_NO_ARMATURE_BONE,
                     '"%s" authors bone "%s", but no Armature with that bone is selected '
                     "-- anchored at CASTER/TARGET's own origin instead." % (entry.name, entry.attach_bone))
            parent_ob, extra = _resolve(entry.parent_name.upper())
            combined = extra @ this_local

        if entry.kind == "emitter":  # dummy, either a real branch point or import_all_empties-forced
            ob = bpy.data.objects.new(entry.name, None)
            ob.empty_display_type = 'SPHERE'
            ob.empty_display_size = DUMMY_EMPTY_DISPLAY_SIZE
            ob.show_in_front = True
            collection.objects.link(ob)
            _place_combined(ob, parent_ob, combined)
            built[name_upper] = ob
            return ob

        # entry.kind == "model"
        resolved_path = resolve_resource_path(resources_root, entry.resource)
        if resolved_path is None or not os.path.isfile(resolved_path):
            warn(WARN_GR2_NOT_FOUND, '"%s" -- .gr2 not found on disk (%s)' % (entry.name, entry.resource))
            return None

        imported = _import_gr2_objects(operator, context, resources_root, resolved_path, mesh_cache)
        if not imported:
            warn(WARN_GR2_NO_OBJECTS, '"%s" -- .gr2 importer produced no objects (%s)' % (entry.name, entry.resource))
            return None

        if len(imported) == 1:
            # No separate container Empty -- the mesh object itself IS
            # the model's own attach point. Every real sample file's
            # own _fxModelList entries produce exactly one object each,
            # so this is the common case, not a rare shortcut -- see
            # this function's own docstring.
            ob = imported[0]
            _place_combined(ob, parent_ob, combined)
            if collection not in ob.users_collection:
                collection.objects.link(ob)
                for existing in list(ob.users_collection):
                    if existing is not collection:
                        existing.objects.unlink(ob)
            built[name_upper] = ob
            return ob

        # Multi-object .gr2 -- still needs a container: there's no single
        # object to hang the entry's own placement off, and the
        # sub-objects are deliberately kept independent of each other
        # (see _MeshCache/_import_gr2_objects's own docstring).
        container = bpy.data.objects.new(entry.name, None)
        container.empty_display_type = 'PLAIN_AXES'
        container.empty_display_size = DUMMY_EMPTY_DISPLAY_SIZE
        container.show_in_front = True
        collection.objects.link(container)
        _place_combined(container, parent_ob, combined)
        for ob in imported:
            if ob.parent is None:
                ob.parent = container
                # Identity local transform -- per _MeshCache/_import_gr2's
                # own established convention (mirrored from
                # ops/import_area.py), a multi-mesh .gr2's own sub-object
                # shape is already baked into each object's mesh vertex
                # data, not carried in Object.location, so every returned
                # object shares the SAME container placement uniformly.
                ob.location = (0.0, 0.0, 0.0)
                ob.rotation_euler = (0.0, 0.0, 0.0)
                ob.scale = (1.0, 1.0, 1.0)
            if collection not in ob.users_collection:
                collection.objects.link(ob)
                for existing in list(ob.users_collection):
                    if existing is not collection:
                        existing.objects.unlink(ob)
        built[name_upper] = container
        return container

    def _resolve(name_upper):
        # type: (str) -> tuple
        """
        Returns (parent_ob_or_None, extra_matrix) for whatever
        `name_upper` resolves to:
          - VIRTUAL_ROOTS -> (caster_object, Identity) -- ordinary
            parenting to whatever the caller already placed, or a
            plain (None, Identity) -- "world origin, nothing to
            compose yet" -- when caster_object wasn't given (the
            standalone default).
          - An unresolvable/cyclic reference -> ALWAYS a plain
            (None, Identity), matching the previous fallback-to-CASTER
            behaviour, regardless of caster_object -- see this
            function's own module-level docstring on why this one
            case doesn't route through caster_object even in area
            context.
          - An entry needing its own real object (a model; a dummy
            with 2+ things depending on it; or ANY dummy at all when
            import_all_empties is set) -> that Object, with an
            identity extra_matrix (nothing left to fold in -- the
            entry's own offset already went into placing the object
            itself, see _build_real_object).
          - A collapsible dummy (exactly one thing depends on it, not
            import_all_empties) -> whatever ITS OWN _resolve() call
            returns, with this entry's own local matrix folded into
            extra_matrix (and its own bone-anchor role substituted in,
            if it has one) -- nothing gets built here at all.
        """
        if name_upper in fxspec_types.VIRTUAL_ROOTS:
            return caster_object, Matrix.Identity(4)
        if name_upper in resolve_cache:
            return resolve_cache[name_upper]

        entry = graph.get(name_upper)
        if entry is None:
            warn(WARN_DANGLING_REFERENCE,
                 'Attach chain references "%s", which isn\'t in this file -- '
                 "falling back to CASTER for anything attached to it." % name_upper)
            result = (None, Matrix.Identity(4))
            resolve_cache[name_upper] = result
            return result

        if name_upper in resolving:
            warn(WARN_CHAIN_CYCLE,
                 'Attach chain for "%s" cycles back to itself -- '
                 "falling back to CASTER." % entry.name)
            result = (None, Matrix.Identity(4))
            resolve_cache[name_upper] = result
            return result
        resolving.add(name_upper)

        if entry.kind == "emitter" and not entry.is_dummy:
            # Real particle emitter -- not built (Phase 1 scope), and per
            # Crunch's own call, nothing stands in for it either. Nothing
            # is EVER built through one by construction, so there's
            # nothing to fold forward -- opaque skip.
            warn(WARN_REAL_EMITTER_SKIPPED,
                 '"%s" is a real particle emitter (not a recognized dummy/anchor) '
                 "-- skipped, and anything attached to it falls back to its own "
                 "parent instead." % entry.name)
            result = _resolve(entry.parent_name.upper())
            resolving.discard(name_upper)
            resolve_cache[name_upper] = result
            return result

        is_dummy_entry = entry.kind == "emitter" and entry.is_dummy
        is_essential = name_upper in essential_dummy_names

        if is_dummy_entry and not is_essential and not import_all_empties:
            # Nothing built ever attaches through this one either --
            # same opaque-skip reasoning as the real-emitter case above.
            warn(WARN_NON_ESSENTIAL_DUMMY_SKIPPED,
                 '"%s" is a dummy/anchor point, but nothing built ultimately attaches '
                 'through it -- skipped (enable "Import All Empties" to include it '
                 "anyway)." % entry.name)
            result = _resolve(entry.parent_name.upper())
            resolving.discard(name_upper)
            resolve_cache[name_upper] = result
            return result

        needs_real_object = (
            entry.kind == "model"
            or import_all_empties
            or fan_out_counts.get(name_upper, 0) != 1
        )
        if needs_real_object:
            ob = _build_real_object(entry, name_upper)
            result = (ob, Matrix.Identity(4)) if ob is not None else (None, Matrix.Identity(4))
            resolving.discard(name_upper)
            resolve_cache[name_upper] = result
            return result

        # Collapsible essential dummy: exactly one thing depends on it.
        # Fold its own offset (and bone-anchor role, if it has one)
        # forward instead of creating a separate object for it.
        this_local = _local_matrix(entry)
        if entry.attach_bone and armature_ob is not None and _find_bone(armature_ob, entry.attach_bone):
            anchor_ob = _make_bone_follower(collection, armature_ob, entry.attach_bone, bone_follower_cache)
            result = (anchor_ob, this_local)
        else:
            if entry.attach_bone:
                warn(WARN_NO_ARMATURE_BONE,
                     '"%s" authors bone "%s", but no Armature with that bone is selected '
                     "-- anchored at CASTER/TARGET's own origin instead." % (entry.name, entry.attach_bone))
            parent_ob, extra = _resolve(entry.parent_name.upper())
            result = (parent_ob, extra @ this_local)

        resolving.discard(name_upper)
        resolve_cache[name_upper] = result
        return result

    for name_upper in graph:
        _resolve(name_upper)

    return built


# ---------------------------------------------------------------------------
# Operator (File > Import entry point)
# ---------------------------------------------------------------------------

class ImportFXSPEC(Operator):
    """
    Import a SWTOR .fxspec file standalone (not via an area .json's
    `.fxp` element -- see the module docstring for why that's a
    separate, later step).

    CASTER (and, for now, TARGET too -- see module docstring) anchors
    at world origin, UNLESS the active object is an Armature and a
    chain entry names one of its bones, in which case that entry
    follows the bone live via a Copy Transforms constraint. Select the
    target Armature (e.g. an already-imported NPC/Character) before
    running this if you want gear-cosmetic-style bone anchoring;
    otherwise everything places relative to (0, 0, 0).
    """
    bl_idname = "import_scene.swtor_fxspec"
    bl_label = "Import SWTOR FX Spec (.fxspec)"
    bl_description = "Import a SWTOR .fxspec file (models + dummy/anchor points only -- see tooltip)"
    bl_options = {'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    directory: StringProperty(subtype='DIR_PATH')
    files: CollectionProperty(
        name="File Path",
        description="File path(s) used for importing the .fxspec file(s)",
        type=OperatorFileListElement,
    )
    filename_ext = ".fxspec"
    filter_glob: StringProperty(default="*.fxspec", options={'HIDDEN'})

    fxspec_separate_collection: BoolProperty(
        name="Separate Collection Per File",
        description=(
            "Creates a new Collection named after each .fxspec file's own "
            "displayName, instead of importing directly into the active "
            "Collection"
        ),
        default=True,
    )

    fxspec_process_materials: BoolProperty(
        name="Process Materials",
        description=(
            "Looks up every imported model's materials by name against real "
            ".mat files in the Resources Directory and builds native SWTOR "
            "shaders for them, once, after every selected file has finished "
            "importing -- same behaviour as Area Assembler's own option of "
            "the same name"
        ),
        default=True,
    )

    fxspec_import_all_empties: BoolProperty(
        name="Import All Empties (Caster/Target Anchors)",
        description=(
            "Also imports dummy/anchor-point Empties that nothing built "
            "actually attaches through -- pure plumbing for particle effects "
            "and other subsystems this add-on doesn't build yet. Off by "
            "default: only the anchors an actual model (or another anchor "
            "leading to one) depends on for its own placement get built"
        ),
        default=False,
    )

    # Not exposed in the panel: import_gr2.py's load() reads this
    # attribute unconditionally before it even checks bl_idname, same
    # reasoning as ImportAREA's own copy of this property -- this
    # module wants raw, unscaled, axis-unconverted .gr2 geometry back
    # from import_gr2.py, since scale_factor and the SWTOR-to-Blender
    # axis conversion are both applied exactly once, at the attach
    # graph's own root anchor, and propagate down through ordinary
    # Blender parent-child composition from there -- see
    # build_fxspec_graph's own docstring.
    enforce_neutral_settings: BoolProperty(options={'HIDDEN'}, default=True)

    # Not exposed in the panel: import_gr2.py's load() reads this
    # attribute unconditionally before it even checks bl_idname, same
    # reasoning as ImportAREA's own copy of this property.
    job_results_rich: BoolProperty(options={'HIDDEN'}, default=False)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        prefs = bpy.context.preferences.addons["swtor_io_tools"].preferences
        resources_root = prefs.swtor_resources_dir

        if not resources_root or not Path(resources_root).is_dir():
            self.report({'ERROR'}, "Set a valid Resources Folder in this add-on's preferences first.")
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
        # read -- same reasoning as ImportAREA/ImportGR2's own execute().
        self.files.clear()
        self.filepath = ""

        # The active object at invocation time is the CASTER/TARGET
        # bone-lookup source, per the class docstring -- selected BEFORE
        # running this operator, same convention ImportCLO's rig mode
        # already uses for "the armature you're rigging onto".
        armature_ob = context.active_object
        if armature_ob is not None and armature_ob.type != 'ARMATURE':
            armature_ob = None

        job_results['job_origin'] = self.bl_idname
        job_results['objs_names'] = []
        job_results['files_objs_names'] = {}

        # scale_factor=1.0 deliberately -- unlike Area Assembler, this
        # module doesn't bake scale into mesh vertex data at all. Every
        # mesh's own container Empty lives under a parent chain that
        # ultimately inherits scale_factor from exactly one place (the
        # root bake in _place_entry, or the armature's already-
        # correctly-scaled bone data) -- see build_fxspec_graph's own
        # docstring. Baking it into vertex data HERE as well would
        # double it a second time on top of that inherited chain scale.
        mesh_cache = _MeshCache(scale_factor=1.0)

        # Shared across every file in this run (not created fresh per
        # file) -- see _make_bone_follower's own docstring for why.
        bone_follower_cache = {}

        succeeded = []
        failed = []
        all_warnings = []
        any_actionable_warning = False
        for path in paths:
            try:
                with open(path, 'rb') as f:
                    raw_bytes = f.read()
                root_node = fxspec_types.parse_fxspec(raw_bytes)
            except (fxspec_types.FxSpecParseError, OSError) as exc:
                self.report({'ERROR'}, "%s -- %s" % (path, exc))
                failed.append(path)
                continue

            # File stem, not the .fxspec's own internal displayName field
            # -- CONFIRMED that field is often generic/unrelated to the
            # actual content (mtx_gearfx_sovex_chest_v01_bfs.fxspec's own
            # displayName is "expl_standard_small", presumably copied from
            # whatever template it was authored from) -- the filename
            # Crunch actually picked to test with is far more meaningful
            # for a Collection name than that.
            display_name = Path(path).stem

            if self.fxspec_separate_collection:
                collection = bpy.data.collections.new(display_name)
                context.scene.collection.children.link(collection)
            else:
                collection = context.collection

            file_warnings = []
            file_actionable = []  # mutable cell -- closures can't rebind a plain bool via `nonlocal` cleanly inside a lambda

            def _on_warn(category, message):
                file_warnings.append(message)
                if category not in WARN_EXPECTED_CATEGORIES:
                    file_actionable.append(True)

            built = build_fxspec_graph(
                self, context, resources_root, root_node, collection,
                mesh_cache, scale_factor, armature_ob, bone_follower_cache,
                self.fxspec_import_all_empties, _on_warn,
            )

            if file_actionable:
                any_actionable_warning = True

            if file_warnings:
                print('FX Spec Importer: "%s" -- %d warning(s):' % (path, len(file_warnings)))
                for w in file_warnings:
                    print("  - %s" % w)
                all_warnings.extend("%s: %s" % (display_name, w) for w in file_warnings)

            if built:
                succeeded.append(path)
            else:
                failed.append(path)
                self.report({'WARNING'}, '"%s" produced nothing buildable -- see console.' % display_name)

        bpy.context.scene.swtor_io_last_job = json.dumps(job_results)

        if succeeded and self.fxspec_process_materials:
            results = apply_materials_by_name_to_gr2_objects(job_results['objs_names'])
            _summarize_and_report(self, results)

        if all_warnings and len(paths) == 1:
            # Single-file import: surface the first warning directly
            # rather than making the user check the console for
            # something that's often the whole reason nothing appeared
            # (e.g. Resources Folder missing a referenced .gr2). Only
            # bumped to WARNING when at least one warning is actually
            # actionable (see WARN_EXPECTED_CATEGORIES) -- a .fxspec
            # full of real particle content trips a real-emitter-skipped/
            # non-essential-dummy-skipped warning on nearly every entry,
            # entirely by design, and reporting that at WARNING level
            # looked like something had gone wrong when it hadn't.
            level = {'WARNING'} if any_actionable_warning else {'INFO'}
            self.report(level, "%d warning(s) -- see console for details. First: %s"
                        % (len(all_warnings), all_warnings[0]))
        elif all_warnings:
            level = {'WARNING'} if any_actionable_warning else {'INFO'}
            self.report(level, "%d warning(s) across %d file(s) -- see console for details."
                        % (len(all_warnings), len(paths)))

        if not succeeded:
            self.report({'ERROR'}, "No .fxspec file imported successfully -- see console for details.")
            return {'CANCELLED'}

        return {'FINISHED'}