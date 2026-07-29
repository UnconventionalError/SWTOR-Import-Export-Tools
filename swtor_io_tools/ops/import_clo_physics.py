# <pep8 compliant>

"""
"Build Cloth Physics" mode for the .clo importer: sets up Blender's Cloth Physics
modifier directly on the sibling .gr2 mesh, seeded with real per-particle/per-edge
data from the .clo file -- rather than a hand-tuned guess, and rather than building
any bone rig at all (see import_clo_rig.py for that mode). See CLO_PHYSICS_HANDOFF.md
for the full design discussion this module implements; summary of the decisions
that ended up encoded here:

  - Operates on the ACTIVE SELECTED mesh, not a scene-wide filename lookup. Unlike
    Rig mode (which uses _find_sibling_mesh only for an optional advisory check),
    Physics mode's entire job IS mutating a specific mesh object -- if multiple
    objects in the scene share a name pattern, silently picking "the" match by
    filename could touch the wrong duplicate. The user selects the mesh; we operate
    on exactly that object and nothing else.

  - No mesh separation, no new geometry: the Cloth modifier goes straight onto the
    already-imported mesh, scoped by vertex groups. Every value below is read from
    vertex groups the .gr2 importer already created (named after cloth particles --
    see import_gr2.py's `ob.vertex_groups.new(name=bone.name)` and import_clo_rig.py's
    `_find_unweighted_bones`, which relies on the exact same naming convention), so
    "how much does particle N's data apply to mesh vertex V" is just that vertex's
    existing real skin weight to group N -- not re-derived or guessed.

  - Pin group (`invertedSimWeight`, 0 = free / 1 = pinned): projected onto each mesh
    vertex as a weighted average across whichever cloth-particle groups actually
    influence it, normalized by the total matched weight. A vertex with no cloth-
    particle influence at all is left out of the group entirely, which Blender's
    Cloth modifier already reads as weight 0 (fully free) -- the same outcome, no
    special-casing needed.

  - Stiffness group: `springStrength` averaged per-particle across every edge
    touching it (lossy -- flattens structural/shear/bend distinctions, an accepted
    trade-off per the handoff doc), then min-max normalized WITHIN THIS FILE's own
    particles (there's no cross-file absolute scale to normalize against), then
    projected onto vertices the same weighted-average way as the pin group. Assigned
    to BOTH Structural and Bending stiffness slots (not split) -- the .clo data
    doesn't distinguish them either, and the goal here is "good quality, one click,"
    not a from-scratch recreation of the in-game solver.

  - Gravity: `cloth.gravity` is NOT an absolute acceleration -- verified against
    Jedipedia's own clo-render.js, which stores it "pointing up" in file space, then
    flips all 3 axes AND negates before use as the actual downward acceleration; those
    two operations cancel out, so the raw vector is already the correct down-pointing
    acceleration in the sibling .gr2's own (pre-axis-conversion) space. Magnitudes
    seen across real files (2.0-2.5) are architecturally too small to be a literal
    acceleration in any sane unit, and every sample is exactly axis-aligned to
    straight down once the standard 90d-about-X axis conversion is applied -- the
    signature of a per-item DIMENSIONLESS GRAVITY SCALE (mirrors Blender Cloth's own
    Effector Weights > Gravity field, which multiplies whatever the scene's real
    gravity already is). Because it's a ratio, not an absolute unit, the fact that
    SWTOR assets are ~1/10 scale in Blender doesn't require any conversion here --
    unlike position data, a dimensionless multiplier is scale-invariant. This is a
    reasoned hypothesis (no authoritative HeroEngine/APEX doc found confirming the
    field's exact semantics), not a certainty -- flagged in the report message so
    it's easy to notice if a real sim looks unexpectedly floaty/heavy and revisit.
    If a file's gravity vector ever turns out NOT to be axis-aligned to straight
    down post-conversion, the scalar-only approach breaks down (would need a real
    Force Field object instead of just Effector Weights) -- guarded against below.

  - Floating cloth islands: a mesh piece (e.g. a belt loop or buckle) can be
    geometrically disconnected from the main cloth surface -- no shared mesh edges
    -- while still carrying real .clo particle weight. Nothing in Blender's edge-
    derived spring network anchors a piece like that to anything, so it drifts/falls
    independently even with correct .clo data applied. Detected via mesh-topology
    connected-components (real edges only, nothing to do with vertex weights) cross-
    checked against which components contain a rigid vertex at all, and reported by
    name -- NOT auto-fixed. The right fix (typically reweighting to a rigid bone) is
    an artistic judgment call, consistent with this add-on's "give real control"
    convention rather than silently guessing.

  - Colliders: NOT implemented here, and not planned -- a deliberate decision, not
    just a deferral. clo-render.js -- the reference viewer's own simulation code --
    never touches `data.data.colliders` at all despite parsing it, so there's no
    working implementation anywhere to validate position/rotation's coordinate space
    against. More fundamentally though: real Blender collision objects (the
    character's own body mesh, posed correctly, with a Collision modifier) are a
    better fit than reconstructing the game's own low-poly approximate primitives --
    more accurate, and something the artist already has to hand rather than
    something this importer would need to fabricate and keep in sync.
"""

from typing import TYPE_CHECKING, Dict, List, Optional, Set

if TYPE_CHECKING:
    from bpy.types import Context, Object, Operator
    from ..types.clo import Cloth


PIN_GROUP_NAME = "CLO_Pin"
STIFFNESS_GROUP_NAME = "CLO_Stiffness"
MODIFIER_NAME = "CLO Physics"
DOWN_ALIGNMENT_THRESHOLD = 0.99  # dot(normalized gravity, -Z) at/above this counts as "straight down".
PIN_THRESHOLD = 0.999  # Same convention as import_clo_rig.py's PIN_THRESHOLD: at/above this counts as rigid.


COINCIDENT_POSITION_EPSILON = 1e-4  # Grid cell size for treating spatially-identical vertices as joined.


def _connected_components(mesh_ob):
    # type: (Object) -> callable
    """
    Union-find over MESH_OB's own real edges, PLUS a "phantom edge" between any two
    vertices that sit at (near enough) the exact same position. Real edges alone
    aren't sufficient: exported meshes like .gr2 routinely split a single visual
    vertex into multiple separate vertex entries wherever there's a UV seam or a
    hard-shaded edge -- each is a distinct (position, normal, uv) buffer entry, with
    no edge directly between the duplicates, even though they sit on top of each
    other and are visually/functionally "joined". Without this, every seam in every
    file is a potential false-positive floating-island report. Coincidence is
    checked via a position grid (COINCIDENT_POSITION_EPSILON cell size) rather than
    an O(n^2) neighbour search -- cheap, and duplicates from a seam split share the
    literal source position, so exact-enough matching is all that's needed here.
    """
    parent = list(range(len(mesh_ob.data.vertices)))

    def find(a):
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for edge in mesh_ob.data.edges:
        union(edge.vertices[0], edge.vertices[1])

    position_buckets = {}
    for vertex in mesh_ob.data.vertices:
        key = (
            round(vertex.co.x / COINCIDENT_POSITION_EPSILON),
            round(vertex.co.y / COINCIDENT_POSITION_EPSILON),
            round(vertex.co.z / COINCIDENT_POSITION_EPSILON),
        )
        position_buckets.setdefault(key, []).append(vertex.index)
    for members in position_buckets.values():
        for other in members[1:]:
            union(members[0], other)

    return find


def _own_group_indices(mesh_ob):
    # type: (Object) -> Set[int]
    """Indices of this add-on's own PIN_GROUP_NAME/STIFFNESS_GROUP_NAME vertex
    groups, if present from an earlier run on this mesh. Needed because the
    floating-island check runs before this run's own groups get rewritten -- on a
    re-import, last run's leftover CLO_Pin/CLO_Stiffness groups are still sitting
    on the mesh at that point, and without this exclusion they get mistaken for a
    real skeleton bone by _has_rigid_bone_fallback (they're not cloth-particle
    names either), which is exactly what caused a second import to "find" a rigid
    fallback that was really just this tool's own prior output."""
    indices = set()
    for name in (PIN_GROUP_NAME, STIFFNESS_GROUP_NAME):
        group = mesh_ob.vertex_groups.get(name)
        if group is not None:
            indices.add(group.index)
    return indices


def _has_rigid_bone_fallback(mesh_ob, ignored_group_indices, vertex_indices):
    # type: (Object, Set[int], List[int]) -> bool
    """
    True if ANY vertex in VERTEX_INDICES also carries real weight to a vertex group
    that ISN'T a cloth particle and ISN'T this add-on's own CLO_Pin/CLO_Stiffness
    group (IGNORED_GROUP_INDICES covers both) -- i.e. a real skeleton bone from the
    .gr2's own rig. That's the difference between "safe to auto-pin" (an Armature
    modifier upstream of Cloth will keep posing it correctly even once Cloth stops
    touching it) and "auto-pin would just freeze it at rest pose forever" (no bone
    to fall back on at all).
    """
    for vertex_index in vertex_indices:
        for group_element in mesh_ob.data.vertices[vertex_index].groups:
            if group_element.group not in ignored_group_indices and group_element.weight > 1e-4:
                return True
    return False


def _floating_cloth_islands(mesh_ob, cloth, group_index_to_particle, cloth_affected_vertices, rigid_vertices):
    # type: (Object, Cloth, Dict[int, int], Set[int], Set[int]) -> List[dict]
    """
    Finds mesh pieces that carry real cloth-particle weight (so the .clo file
    genuinely drives them) but are geometrically disconnected -- by real mesh
    edges/coincident positions, not vertex-group weight -- from every rigid anchor
    (a true .clo pin, or one of this build's forced-pinned out-of-scope vertices).
    Nothing in Blender's edge-derived spring network holds a piece like this to
    anything, so it drifts/falls under simulation independently of the rest of the
    mesh even though its .clo data was applied correctly -- a real, common case
    (e.g. a belt loop or buckle mesh island that's geometrically separate from the
    main cloth surface, but still named/weighted as a cloth particle in the file).

    Returns one dict per floating island: {'vertices', 'particle_names',
    'has_rigid_fallback'}. Detection only -- callers decide what, if anything, to
    do about each island (see build()'s auto-fix handling, which only acts on
    islands with has_rigid_fallback=True).
    """
    find = _connected_components(mesh_ob)

    components = {}  # type: Dict[int, List[int]]
    for vertex_index in cloth_affected_vertices:
        components.setdefault(find(vertex_index), []).append(vertex_index)

    rigid_roots = {find(vertex_index) for vertex_index in rigid_vertices}
    ignored_group_indices = set(group_index_to_particle.keys()) | _own_group_indices(mesh_ob)

    islands = []
    for root, members in components.items():
        if root in rigid_roots:
            continue
        particle_names = set()
        for vertex_index in members:
            for group_element in mesh_ob.data.vertices[vertex_index].groups:
                particle_index = group_index_to_particle.get(group_element.group)
                if particle_index is not None:
                    particle_names.add(cloth.bones[particle_index].name)
        islands.append({
            'vertices': members,
            'particle_names': particle_names,
            'has_rigid_fallback': _has_rigid_bone_fallback(mesh_ob, ignored_group_indices, members),
        })

    return islands


def _average_spring_strength_per_particle(cloth):
    # type: (Cloth) -> Dict[int, float]
    """
    Averages `springStrength` across every edge touching each particle -- the lossy
    per-vertex approximation of a genuinely per-edge property described in the
    handoff doc. Particles with no edges at all (should be rare -- pins mostly) are
    left out of the returned dict entirely.
    """
    totals = {}  # type: Dict[int, float]
    counts = {}  # type: Dict[int, int]
    for edge in cloth.edges:
        for node in (edge.node1, edge.node2):
            totals[node] = totals.get(node, 0.0) + edge.springStrength
            counts[node] = counts.get(node, 0) + 1
    return {node: totals[node] / counts[node] for node in totals}


def _normalized(values):
    # type: (Dict[int, float]) -> Dict[int, float]
    """Min-max normalizes VALUES to [0, 1]. If every value is identical (including
    the single-particle case), everything maps to 1.0 -- there's nothing to
    distinguish, so treat it as "as stiff as this file has" rather than dividing by
    zero."""
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    spread = hi - lo
    if spread <= 1e-9:
        return {index: 1.0 for index in values}
    return {index: (value - lo) / spread for index, value in values.items()}


def _particle_group_index_map(mesh_ob, cloth):
    # type: (Object, Cloth) -> Dict[int, int]
    """{blender_vertex_group_index: cloth_particle_index} for every cloth particle
    that actually has a matching vertex group on MESH_OB (same naming convention
    import_clo_rig.py's _find_unweighted_bones relies on: groups are named after
    the cloth bone/particle, since import_gr2.py builds them from the same shared
    bone_buffer)."""
    mapping = {}
    for bone in cloth.bones:
        vertex_group = mesh_ob.vertex_groups.get(bone.name)
        if vertex_group is not None:
            mapping[vertex_group.index] = bone.index
    return mapping


def _project_to_vertices(mesh_ob, group_index_to_particle, particle_values):
    # type: (Object, Dict[int, int], Dict[int, float]) -> Dict[int, float]
    """
    For each mesh vertex, weighted-averages PARTICLE_VALUES across whichever
    cloth-particle groups influence it, normalized by the total matched weight (so
    a vertex half-weighted to a cloth particle and half to some unrelated real
    skeleton bone still gets a correct value from just its cloth-relevant half, not
    diluted by the unrelated weight).

    Vertices with zero total cloth-particle influence are left out of the returned
    dict -- callers should leave those vertices unassigned in the resulting Blender
    vertex group, which reads as 0 for the Pin group (correct: no cloth data means
    nothing to pin) and as "no override" for the stiffness group.
    """
    result = {}
    for vertex in mesh_ob.data.vertices:
        weighted_sum = 0.0
        weight_total = 0.0
        for group_element in vertex.groups:
            particle_index = group_index_to_particle.get(group_element.group)
            if particle_index is None:
                continue
            value = particle_values.get(particle_index)
            if value is None:
                continue
            weighted_sum += group_element.weight * value
            weight_total += group_element.weight
        if weight_total > 1e-9:
            result[vertex.index] = weighted_sum / weight_total
    return result


def _write_vertex_group(mesh_ob, name, vertex_values):
    # type: (Object, str, Dict[int, float]) -> 'bpy.types.VertexGroup'
    """(Re)creates NAME as a fresh vertex group on MESH_OB containing exactly
    VERTEX_VALUES -- clears an existing same-named group in place (overwrite, per
    the selected-mesh-only re-import behaviour) rather than leaving stale members
    from a previous run or piling up '.001' duplicates."""
    existing = mesh_ob.vertex_groups.get(name)
    if existing is not None:
        mesh_ob.vertex_groups.remove(existing)
    group = mesh_ob.vertex_groups.new(name=name)
    for vertex_index, value in vertex_values.items():
        group.add([vertex_index], value, 'REPLACE')
    return group


def _gravity_scale(cloth, operator):
    # type: (Cloth, Operator) -> Optional[float]
    """
    Reads cloth.gravity as a dimensionless scale on Blender's own scene gravity (see
    module docstring for why) -- returns the magnitude to feed into the Cloth
    modifier's Effector Weights > Gravity field, or None if there's nothing usable
    (zero vector) or the direction doesn't check out as straight down once converted
    (a real file where this hypothesis appears to be wrong -- reported as a warning
    rather than silently misapplied).
    """
    from math import pi as PI
    from mathutils import Matrix, Vector

    raw = Vector(cloth.gravity)
    magnitude = raw.length
    if magnitude <= 1e-9:
        return None

    # Same 90d-about-X axis conversion every .gr2-derived object already carries
    # (see import_gr2.py's `ob.matrix_local = Matrix.Rotation(PI * 0.5, 4, 'X')` and
    # import_clo_rig.py's AXIS_CONVERSION_ROTATION) -- gravity direction needs the
    # same conversion the mesh itself got, to compare against world-space "down".
    axis_conversion = Matrix.Rotation(PI * 0.5, 4, 'X')
    world_direction = (axis_conversion @ raw.to_4d()).to_3d().normalized()

    alignment = world_direction.dot(Vector((0.0, 0.0, -1.0)))
    if alignment < DOWN_ALIGNMENT_THRESHOLD:
        operator.report(
            {'WARNING'},
            f"This file's gravity vector isn't straight down after axis conversion "
            f"(alignment {alignment:.3f}) -- the gravity-scale assumption may not hold "
            "here. Skipping gravity setup; check Effector Weights manually if the sim "
            "looks off.",
        )
        return None

    return magnitude


def build(operator, context, cloth, filepath):
    # type: (Operator, Context, Cloth, str) -> bool
    import bpy

    mesh_ob = context.active_object
    if not (mesh_ob and mesh_ob.type == 'MESH'):
        operator.report(
            {'ERROR'},
            ".gr2 mesh missing. Please import the .gr2 object first, select it, then retry.",
        )
        return False

    import os
    expected_name = os.path.splitext(os.path.basename(filepath))[0]
    if mesh_ob.name != expected_name:
        operator.report(
            {'INFO'},
            f"Selected mesh \'{mesh_ob.name}\' doesn't match this file's expected name "
            f"(\'{expected_name}\') -- proceeding anyway since it's the active selection.",
        )

    prev_mode = mesh_ob.mode
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='OBJECT')

    group_index_to_particle = _particle_group_index_map(mesh_ob, cloth)
    if not group_index_to_particle:
        operator.report(
            {'ERROR'},
            f"\'{mesh_ob.name}\' has no vertex groups matching any bone in this .clo file -- "
            "is this really the matching mesh? Nothing to build physics from.",
        )
        return False

    # --- Pin group: invertedSimWeight straight from the particles, no averaging needed
    # at the particle level (unlike stiffness, this is already a per-particle scalar).
    pin_values_by_particle = {
        particle.index: particle.invertedSimWeight for particle in cloth.particles
    }
    pin_vertex_values = _project_to_vertices(mesh_ob, group_index_to_particle, pin_values_by_particle)
    cloth_affected_vertices = set(pin_vertex_values.keys())  # captured before the backfill below

    # Blender's Cloth modifier simulates the WHOLE mesh -- the Pin group only scales
    # how free/pinned each vertex is, it doesn't opt vertices out of the sim. Any
    # vertex this .clo file has zero cloth-particle influence over at all (e.g. a
    # rigidly-skinned body region sharing the same mesh object) isn't part of the
    # cloth system in the first place, so it needs to be forced fully pinned (1.0)
    # here -- leaving it unassigned reads as pin weight 0 (fully free) and it just
    # falls under gravity along with the real cloth, which is exactly the "parts the
    # .clo doesn't reference are breaking" symptom. Only vertices genuinely untouched
    # by any cloth particle get this treatment; anything with even partial matched
    # weight keeps its real, blended invertedSimWeight-derived value from above.
    n_out_of_scope = 0
    for vertex in mesh_ob.data.vertices:
        if vertex.index not in pin_vertex_values:
            pin_vertex_values[vertex.index] = 1.0
            n_out_of_scope += 1

    # Floating-island check (see _floating_cloth_islands docstring): pieces that
    # carry real cloth data but are geometrically disconnected, by real mesh edges/
    # coincident positions, from every rigid anchor -- nothing holds them to
    # anything, so they'll drift/fall independently even though their .clo data is
    # correctly applied.
    rigid_vertices = {v for v, value in pin_vertex_values.items() if value >= PIN_THRESHOLD}
    islands = _floating_cloth_islands(
        mesh_ob, cloth, group_index_to_particle, cloth_affected_vertices, rigid_vertices
    )

    auto_fix = bool(getattr(operator, 'physics_auto_fix_floating_islands', False))
    fixed_islands = []
    remaining_islands = []
    for island in islands:
        if auto_fix and island['has_rigid_fallback']:
            # Safe to force-pin: an Armature modifier upstream of Cloth will keep
            # posing these vertices from their real skeleton-bone weight even once
            # Cloth stops simulating them, so this trades "drifts/falls on its own"
            # for "follows the body like it should", not for "frozen forever".
            for vertex_index in island['vertices']:
                pin_vertex_values[vertex_index] = 1.0
            fixed_islands.append(island)
        else:
            remaining_islands.append(island)

    pin_group = _write_vertex_group(mesh_ob, PIN_GROUP_NAME, pin_vertex_values)

    if fixed_islands:
        fixed_names = sorted({name for island in fixed_islands for name in island['particle_names']})
        fixed_count = sum(len(island['vertices']) for island in fixed_islands)
        operator.report(
            {'INFO'},
            f"Auto-fixed {fixed_count} vertex/vertices across {len(fixed_islands)} floating "
            "island(s) with a real skeleton-bone fallback (force-pinned; will keep following "
            "the body via the Armature modifier). Particles: " + ", ".join(fixed_names),
        )
    if remaining_islands:
        remaining_names = sorted({name for island in remaining_islands for name in island['particle_names']})
        remaining_count = sum(len(island['vertices']) for island in remaining_islands)
        no_fallback_note = "" if auto_fix else " (auto-fix is off)"
        operator.report(
            {'WARNING'},
            f"{remaining_count} vertex/vertices across {len(remaining_islands)} island(s) carry "
            "real cloth data but aren't geometrically connected to any pin or rigid anchor, and "
            f"have no real skeleton-bone fallback to auto-fix with{no_fallback_note} -- they'll "
            "drift/fall independently under simulation. Likely a piece like a belt loop or buckle "
            "that's mesh-separate from the main cloth surface; usually fixed by manually "
            "reweighting it to a rigid bone. Affected particles: " + ", ".join(remaining_names),
        )

    # --- Stiffness group: averaged per-particle from edges, then normalized within
    # this file, then projected the same way.
    raw_stiffness_by_particle = _average_spring_strength_per_particle(cloth)
    normalized_stiffness_by_particle = _normalized(raw_stiffness_by_particle)
    stiffness_vertex_values = _project_to_vertices(
        mesh_ob, group_index_to_particle, normalized_stiffness_by_particle
    )
    stiffness_group = _write_vertex_group(mesh_ob, STIFFNESS_GROUP_NAME, stiffness_vertex_values)

    # --- Cloth modifier: reuse an existing one of the same name if present (overwrite
    # in place, per the selected-mesh-only re-import behaviour) rather than stacking
    # duplicates.
    cloth_modifier = mesh_ob.modifiers.get(MODIFIER_NAME)
    if cloth_modifier is None or cloth_modifier.type != 'CLOTH':
        cloth_modifier = mesh_ob.modifiers.new(name=MODIFIER_NAME, type='CLOTH')

    settings = cloth_modifier.settings
    settings.vertex_group_mass = pin_group.name
    settings.vertex_group_structural_stiffness = stiffness_group.name
    settings.vertex_group_bending = stiffness_group.name

    gravity_scale = _gravity_scale(cloth, operator)
    if gravity_scale is not None:
        settings.effector_weights.gravity = gravity_scale

    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode=prev_mode if prev_mode != 'EDIT' else 'OBJECT')

    n_pinned = sum(1 for v in pin_vertex_values.values() if v >= 0.999)
    operator.report(
        {'INFO'},
        f"Cloth Physics set up on \'{mesh_ob.name}\': {len(pin_vertex_values)} vertices total, "
        f"{n_pinned} fully pinned ({n_out_of_scope} of those forced-pinned as outside this "
        f".clo's own data), gravity scale {'skipped' if gravity_scale is None else round(gravity_scale, 3)}.",
    )
    return True