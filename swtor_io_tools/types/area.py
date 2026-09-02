# <pep8 compliant>

"""
Area Assembler -- data/logic layer.

No bpy dependency in this module (or any function added to it later for
JSON element parsing / transform composition) -- everything here is
plain Python, testable standalone. ops/import_area.py is where Blender
object/collection creation happens.

This file is built incrementally across sessions per the handoff's
suggested order of work; this pass covers only the ".spn_p"/spn_table/
dyn resolution layer (handoff §2b).
"""

import json
import math
import zipfile
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Bundled reference data (spn_table / dyn_data) -- see
# tools/generate_placeable_tables.py for how these are generated.
# ---------------------------------------------------------------------------

class BundledDataError(Exception):
    """
    Raised when bundled_data/ doesn't contain exactly one file matching
    an expected pattern (e.g. "spn_table_*.json") -- either none at all
    (never generated / accidentally deleted) or more than one (a stale
    previous game version's pair left behind after dropping in a new
    one). Both are addon-setup problems, not recoverable per-element,
    so this deliberately isn't a warning-and-continue case like most of
    the rest of Area Assembler's error handling -- there's nothing
    useful to import without this data.
    """


def _bundled_data_dir():
    # type: () -> Path
    """<addon root>/bundled_data -- one level up from this file (types/)."""
    return Path(__file__).resolve().parent.parent / "bundled_data"


def _find_bundled_file(pattern):
    # type: (str) -> Path
    """
    Globs _bundled_data_dir() for `pattern` (e.g. "spn_table_*.json")
    and returns the single match.

    Deliberately doesn't hardcode a game version anywhere -- swapping in
    a freshly-regenerated pair is a pure data change (drop in the new
    spn_table_<version>.json/dyn_data_<version>.zip, delete the old
    pair), never a code edit.
    """
    directory = _bundled_data_dir()
    matches = sorted(directory.glob(pattern))

    if not matches:
        raise BundledDataError(
            "No file matching '%s' found under %s.\n\n"
            "Area Assembler needs exactly one version's worth of bundled "
            "reference data (a matching spn_table_<version>.json and "
            "dyn_data_<version>.zip pair) to be present. Run "
            "tools/generate_placeable_tables.py against a fresh spn/plc/dyn "
            "export and place its output in this folder."
            % (pattern, directory)
        )

    if len(matches) > 1:
        raise BundledDataError(
            "Found multiple files matching '%s' under %s:\n  %s\n\n"
            "Only one version's worth of bundled data (one "
            "spn_table_<version>.json and one dyn_data_<version>.zip) should "
            "exist in the addon at a time. Delete the stale version's files "
            "before continuing -- keeping both leaves it ambiguous which one "
            "Area Assembler should actually use."
            % (pattern, directory, "\n  ".join(m.name for m in matches))
        )

    return matches[0]


_spn_table_cache = None  # type: Optional[dict]


def _load_spn_table():
    # type: () -> dict
    """Lazy-loaded, cached for the addon's lifetime (until Blender/addon reload)."""
    global _spn_table_cache
    if _spn_table_cache is None:
        path = _find_bundled_file("spn_table_*.json")
        with open(path, encoding="utf-8") as f:
            _spn_table_cache = json.load(f)
    return _spn_table_cache


class SpnResolution:
    """
    Result of resolving a ".spn_p" assetName path against spn_table.
    Mirrors tools/generate_placeable_tables.py's own SpnEntry shape,
    minus the "plc"/"reason" fields (debugging-only, not needed at
    runtime).
    """
    __slots__ = ("target_type", "target", "target_ext")

    def __init__(self, target_type, target, target_ext):
        self.target_type = target_type  # "direct" | "dyn" | "unresolved"
        self.target = target            # resource path (direct) or dyn fqn (dyn); None if unresolved
        self.target_ext = target_ext    # "gr2"/"mag"/"fxp"/"fxspec" (target_type == "direct" only)


def resolve_spn_p(spn_p_path):
    # type: (str) -> Optional[SpnResolution]
    """
    Looks up `spn_p_path` (exact backslash form, as it appears in an
    area json element's own assetName field) in the bundled spn_table.

    Returns None if the path isn't in the table at all -- a genuinely
    unrecognized spawner, e.g. something added to the game since the
    bundled data was last regenerated. Distinct from a present entry
    whose own target_type is "unresolved" (a known, already-
    investigated dead end recorded at generation time, such as a .prt
    force-wall reference with no visual target) -- both mean "nothing
    to import for this element", but callers may want to log/report
    them differently: a None here can indicate stale bundled data worth
    regenerating, while an "unresolved" entry doesn't.
    """
    entry = _load_spn_table().get(spn_p_path)
    if entry is None:
        return None
    return SpnResolution(
        target_type=entry["target_type"],
        target=entry.get("target"),
        target_ext=entry.get("target_ext"),
    )


# ---------------------------------------------------------------------------
# dyn expansion (handoff §2b)
# ---------------------------------------------------------------------------

# Field IDs (SWTOR's own schema) -- duplicated from
# tools/generate_placeable_tables.py rather than imported, since that
# script lives outside the addon package and isn't shipped with it (see
# that script's own module docstring for the same tradeoff).
FIELD_DYN_VISUAL_LIST = "4611686038108070010"
FIELD_DYN_POSITION = "4611686038108070005"
FIELD_DYN_ROTATION = "4611686038108070006"
FIELD_DYN_SCALE = "4611686038108070007"
FIELD_DYN_VISUAL_FQN = "4611686038125770001"
FIELD_DYN_VISUAL_NAME = "4611686039107870012"

# Extensions Area Assembler currently builds Blender objects for, among
# everything a dyn's visual list can contain (handoff §2a's scope, plus
# ".lit" for automatic lights -- confirmed still in scope). ".fxspec" is
# deliberately excluded here even though tools/generate_placeable_tables.py
# captures it in the bundled data -- no importer for it yet.
DYN_VISUAL_EXTENSIONS_IN_SCOPE = {"gr2", "mag", "fxp", "lit"}

_dyn_zip_cache = None  # type: Optional[zipfile.ZipFile]


def _get_dyn_zip():
    # type: () -> zipfile.ZipFile
    """Lazy-opened, kept open for the addon's lifetime (not reopened per lookup)."""
    global _dyn_zip_cache
    if _dyn_zip_cache is None:
        path = _find_bundled_file("dyn_data_*.zip")
        _dyn_zip_cache = zipfile.ZipFile(path)
    return _dyn_zip_cache


class DynVisual:
    """
    One expanded child of a ".dyn" fqn's own visual list, already
    filtered to DYN_VISUAL_EXTENSIONS_IN_SCOPE.
    """
    __slots__ = ("path", "name", "position", "rotation", "scale", "extension")

    def __init__(self, path, name, position, rotation, scale, extension):
        self.path = path            # resource path, e.g. "\art\static\...\x.gr2" --
                                     # deliberately NOT slash-normalized here; import_cha.py's
                                     # resolve_resource_path() already handles either style
        self.name = name            # dynVisualName if present, else derived from path
        self.position = position    # (x, y, z), (0.0, 0.0, 0.0) if the source entry omits it
        self.rotation = rotation    # (x, y, z) degrees, (0.0, 0.0, 0.0) if omitted
        self.scale = scale          # (x, y, z), (1.0, 1.0, 1.0) if omitted
        self.extension = extension  # lowercased, no leading dot


def _vector3_or_default(value, default):
    # type: (object, tuple) -> tuple
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return default
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return default


def ensure_bundled_data_available():
    # type: () -> None
    """
    Forces both bundled_data/ files to load (or raise BundledDataError)
    right now, rather than lazily on first use. Callers (ops/import_area.py)
    should call this once, up front, before starting a potentially
    several-hundred-element import loop -- so a missing/misconfigured
    bundled_data/ folder fails cleanly and immediately instead of as a
    raw traceback partway through (confirmed real failure mode).
    """
    _load_spn_table()
    _get_dyn_zip()


def expand_dyn(dyn_fqn):
    # type: (str) -> List[DynVisual]
    """
    Reads `dyn_fqn`'s entry from the bundled dyn_data zip and returns
    its visual list, filtered to DYN_VISUAL_EXTENSIONS_IN_SCOPE, in
    their original order (callers needing a stable per-visual index for
    naming synthetic child objects can just enumerate() the result).

    Returns [] if `dyn_fqn` isn't present in the bundled data at all.
    This shouldn't happen for anything spn_table pointed at (both files
    are generated from the same pass over the same game version), but
    the caller (§2b's synthetic-Empty-plus-children builder) still
    needs defined behavior for "resolved to nothing usable" -- treated
    the same as SpnResolution's "unresolved": skip cleanly, don't raise.
    Also returns [] if the entry has no visual list field at all, or an
    unexpected shape for it.
    """
    filename = dyn_fqn + ".json"
    try:
        raw = _get_dyn_zip().read(filename)
    except KeyError:
        return []

    try:
        data = json.loads(raw)
    except ValueError:
        return []

    visuals_raw = data.get(FIELD_DYN_VISUAL_LIST)
    if not isinstance(visuals_raw, list):
        return []

    results = []
    for visual in visuals_raw:
        if not isinstance(visual, dict):
            continue

        path = visual.get(FIELD_DYN_VISUAL_FQN)
        if not path or "." not in path:
            continue

        extension = path.rsplit(".", 1)[-1].lower()
        if extension not in DYN_VISUAL_EXTENSIONS_IN_SCOPE:
            continue

        name = visual.get(FIELD_DYN_VISUAL_NAME) or path
        position = _vector3_or_default(visual.get(FIELD_DYN_POSITION), (0.0, 0.0, 0.0))
        rotation = _vector3_or_default(visual.get(FIELD_DYN_ROTATION), (0.0, 0.0, 0.0))
        scale = _vector3_or_default(visual.get(FIELD_DYN_SCALE), (1.0, 1.0, 1.0))

        results.append(DynVisual(
            path=path, name=name,
            position=position, rotation=rotation, scale=scale,
            extension=extension,
        ))

    return results


# ---------------------------------------------------------------------------
# Area json element parsing (handoff §2c)
# ---------------------------------------------------------------------------

class AreaElement:
    """One raw element from an area json, as-parsed (no resolution/composition yet)."""
    __slots__ = ("id", "asset_name", "parent_id", "position", "rotation", "scale", "final_position",
                 "fx_spec_name")

    def __init__(self, id, asset_name, parent_id, position, rotation, scale, final_position,
                 fx_spec_name=None):
        self.id = id
        self.asset_name = asset_name        # e.g. "\art\static\...\x.gr2" or "\spn\...\x.spn_p"
        self.parent_id = parent_id          # "0" for root elements
        self.position = position            # (x, y, z) -- local, relative to parent; NOT used for
                                             # final placement (see final_position) but kept for
                                             # completeness/debugging
        self.rotation = rotation            # (x, y, z) degrees, local/relative to parent
        self.scale = scale                  # (x, y, z), local/relative to parent
        self.final_position = final_position  # (x, y, z) world-space, or None if the source
                                               # element omitted it (not observed in real data
                                               # so far, but not assumed impossible)
        self.fx_spec_name = fx_spec_name    # e.g. "\art\fx\fxspec\mtx\mtx_item_republic_banner.fxspec",
                                             # or None -- only present on elements carrying a sibling
                                             # "fx": {"fxSpecName": ...} object in the room export (seen
                                             # so far on direct ".fxp" elements; see fxspec/area
                                             # integration handoff for the ".spn_p"-indirected case,
                                             # not covered by this field at all -- that path resolves
                                             # through the separately-sourced bundled dyn data instead,
                                             # not this one)


def parse_area_json(data):
    # type: (list) -> List[AreaElement]
    """
    `data` is an already-`json.load()`-ed area export (a flat list of
    element dicts -- see the kor_rebuild test set for real examples).
    Returns one AreaElement per entry, preserving source order.

    Entries missing `id` or `assetName` are silently skipped -- neither
    has been observed absent in real data, but there's nothing
    meaningful to build from an element we can't identify or resolve.
    """
    elements = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        id_ = raw.get("id")
        asset_name = raw.get("assetName")
        if id_ is None or not asset_name:
            continue

        parent_id = raw.get("parent", "0")
        position = _vector3_or_default(raw.get("position"), (0.0, 0.0, 0.0))
        rotation = _vector3_or_default(raw.get("rotation"), (0.0, 0.0, 0.0))
        scale = _vector3_or_default(raw.get("scale"), (1.0, 1.0, 1.0))

        final_position = None
        fp = raw.get("finalPosition")
        if isinstance(fp, dict):
            try:
                final_position = (float(fp.get("0", 0.0)), float(fp.get("1", 0.0)), float(fp.get("2", 0.0)))
            except (TypeError, ValueError):
                final_position = None

        fx_spec_name = None
        fx = raw.get("fx")
        if isinstance(fx, dict):
            fx_spec_name = fx.get("fxSpecName") or None

        elements.append(AreaElement(
            id=id_, asset_name=asset_name, parent_id=parent_id,
            position=position, rotation=rotation, scale=scale,
            final_position=final_position, fx_spec_name=fx_spec_name,
        ))
    return elements


# ---------------------------------------------------------------------------
# Transform composition (handoff §2c)
# ---------------------------------------------------------------------------

# Pure-Python 3x3 matrix math -- deliberately not using mathutils, which
# is only importable inside Blender and doesn't build standalone in a
# plain Python environment (tried; fails on an Eigen/Python-C-API
# mismatch in the pip package, not worth fighting). Kept to exactly
# what this module needs: composing rotation+scale up a parent chain
# and decomposing the result back into (Euler degrees, scale).
#
# Translation is handled completely separately from this matrix math,
# not as a simplification but because it's mathematically exact for
# affine TRS matrices: in a 4x4 matrix [[R@S, T], [0, 1]], the product
# of two such matrices has an upper-left 3x3 block equal to the plain
# product of the two input 3x3 (R@S) blocks -- translation never enters
# that block. Since finalPosition already supplies each element's
# absolute world position directly (per §2c), the parent chain only
# ever needs to compose rotation+scale, never position -- so 3x3 is not
# an approximation of the full 4x4 case, it's the exact relevant subset.
#
# ASSUMED CONVENTION: this module targets Blender's 'ZXY' Euler mode,
# not the default 'XYZ' -- confirmed against the old source, see the
# note just above _mat3_from_euler_zxy_degrees for the full rationale
# and the authoritative source for the exact matrix formula used.

def _mat3_identity():
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _mat3_multiply(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def _mat3_rotation_x(degrees):
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def _mat3_rotation_y(degrees):
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def _mat3_rotation_z(degrees):
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def _mat3_scale(sx, sy, sz):
    return ((sx, 0.0, 0.0), (0.0, sy, 0.0), (0.0, 0.0, sz))


# CONVENTION: SWTOR's raw rotation values need Blender's 'ZXY' Euler
# mode to come out correctly, NOT the default 'XYZ' -- confirmed
# directly from the old source (`blender_object.rotation_mode = 'ZXY'`,
# same raw x/y/z values, no reordering), not guessed. That old code let
# Blender's own native matrix system do the actual composition (real
# bpy parenting); this module reimplements that composition in pure
# Python instead (see the note above on why), so it needs the exact
# same underlying matrix convention Blender itself uses for 'ZXY' mode.
#
# That convention -- confirmed via Blender core developer Brecht Van
# Lommel (https://devtalk.blender.org/t/euler-angles-convention-in-blender-extrinsic-vs-intrinsic/28177):
# for a given mode string, the matrix product order is the REVERSE of
# the mode's letter order (e.g. mode 'XYZ' -> matrix = Rz @ Ry @ Rx).
# Applying that same rule to 'ZXY' gives matrix = Ry @ Rx @ Rz.
#
# ops/import_area.py sets ob.rotation_mode = 'ZXY' to match, on every
# object this module computes a rotation for.

def _mat3_from_euler_zxy_degrees(rx, ry, rz):
    return _mat3_multiply(_mat3_rotation_y(ry), _mat3_multiply(_mat3_rotation_x(rx), _mat3_rotation_z(rz)))


def _mat3_from_rotation_scale(rotation_degrees, scale):
    rx, ry, rz = rotation_degrees
    sx, sy, sz = scale
    return _mat3_multiply(_mat3_from_euler_zxy_degrees(rx, ry, rz), _mat3_scale(sx, sy, sz))


def _vec3_length(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _mat3_columns(m):
    return ((m[0][0], m[1][0], m[2][0]), (m[0][1], m[1][1], m[2][1]), (m[0][2], m[1][2], m[2][2]))


def decompose_rotation_scale(m):
    # type: (tuple) -> tuple
    """
    Splits a composed 3x3 (rotation @ scale) matrix back into
    (rotation_degrees, scale) -- exact inverse of _mat3_from_rotation_scale
    (verified via round-trip testing), targeting the same 'ZXY'
    convention (see the note above). Not exact for a matrix containing
    shear, which a chain combining non-uniform scale with rotation can
    in principle produce -- the same limitation Blender's own
    matrix_local.decompose() has, not something addressed here, and not
    observed to matter in the kor_rebuild test data.

    Derived by hand from M = Ry(y) @ Rx(x) @ Rz(z):
      M[1][2] = -sin(x)
      M[1][0] = cos(x)*sin(z),  M[1][1] = cos(x)*cos(z)
      M[0][2] = sin(y)*cos(x),  M[2][2] = cos(y)*cos(x)
    """
    cols = _mat3_columns(m)
    scale = tuple(_vec3_length(c) for c in cols)
    norm_cols = tuple(
        (c[0] / s, c[1] / s, c[2] / s) if s > 1e-10 else (0.0, 0.0, 0.0)
        for c, s in zip(cols, scale)
    )
    r = (
        (norm_cols[0][0], norm_cols[1][0], norm_cols[2][0]),
        (norm_cols[0][1], norm_cols[1][1], norm_cols[2][1]),
        (norm_cols[0][2], norm_cols[1][2], norm_cols[2][2]),
    )
    sx = max(-1.0, min(1.0, -r[1][2]))
    x = math.asin(sx)
    cx = math.cos(x)
    if abs(cx) > 1e-6:
        z = math.atan2(r[1][0], r[1][1])
        y = math.atan2(r[0][2], r[2][2])
    else:
        # Gimbal lock (rx ~ +/-90): y and z become coupled/ambiguous;
        # picking z=0 and folding everything into y for this degenerate case.
        z = 0.0
        if r[1][2] < 0:  # x = +90
            y = math.atan2(r[0][1], r[0][0])
        else:  # x = -90
            y = math.atan2(-r[0][1], r[0][0])
    return (math.degrees(x), math.degrees(y), math.degrees(z)), scale


def compute_world_rotation_scale(elements):
    # type: (List[AreaElement]) -> tuple
    """
    Returns ({element.id: (rotation_degrees, scale)}, warnings) composed
    through each element's own parent chain (root = parent_id == "0"),
    using only local rotation+scale -- see the module note above on why
    position/translation plays no part in this.

    `warnings` is a list of human-readable strings for conditions worth
    surfacing to the person running the import (via operator.report in
    ops/import_area.py) without aborting the whole import over one bad
    element -- currently just cyclic parent chains, which haven't been
    observed in real data but would otherwise recurse forever.
    """
    by_id = {e.id: e for e in elements}
    cache = {}
    visiting = set()
    warnings = []

    def resolve(element_id):
        if element_id in cache:
            return cache[element_id]
        if element_id in visiting:
            warnings.append(
                "Cyclic parent chain detected at element id %r; treated as "
                "unparented (identity rotation/scale) to avoid an infinite loop."
                % (element_id,)
            )
            return _mat3_identity()

        element = by_id.get(element_id)
        if element is None:
            # Dangling parent reference (points at an id not present in
            # this file). Shouldn't happen in real data; treated as root.
            return _mat3_identity()

        visiting.add(element_id)
        local = _mat3_from_rotation_scale(element.rotation, element.scale)
        if element.parent_id in (None, "0"):
            world = local
        else:
            world = _mat3_multiply(resolve(element.parent_id), local)
        visiting.discard(element_id)

        cache[element_id] = world
        return world

    result = {}
    for element in elements:
        result[element.id] = decompose_rotation_scale(resolve(element.id))
    return result, warnings


class ResolvedTransform:
    """Final, ready-to-apply per-element transform."""
    __slots__ = ("position", "rotation", "scale")

    def __init__(self, position, rotation, scale):
        self.position = position  # (x, y, z), world-space, scale_factor already applied
        self.rotation = rotation  # (x, y, z) degrees, world-space (composed through parent chain)
        self.scale = scale        # (x, y, z), world-space (composed through parent chain), scale_factor already applied


def compute_final_transforms(elements, scale_factor=1.0):
    # type: (List[AreaElement], float) -> tuple
    """
    Returns ({element.id: ResolvedTransform}, warnings).

    Position comes directly from each element's own finalPosition
    (already absolute/world-space per the game's own export -- handoff
    §2c), times scale_factor. An element with no finalPosition at all
    (not observed in real data, but not assumed impossible) falls back
    to (0, 0, 0) with a warning rather than raising, consistent with
    "warnings over hard blocks".

    Rotation/scale are composed through the parent chain (see
    compute_world_rotation_scale). scale_factor is deliberately NOT
    applied to the returned scale here -- matching this addon's own
    .gr2 importer convention (confirmed against import_gr2.py's
    build()): scale_factor gets baked directly into imported mesh
    geometry (Object.scale left at its "natural" value), not reflected
    as a multiplied Object.scale property. ops/import_area.py is
    responsible for baking scale_factor into each freshly-imported
    mesh's vertex data once, matching that convention -- see its
    _import_gr2()/_MeshCache.
    """
    rotation_scale, warnings = compute_world_rotation_scale(elements)
    results = {}
    for element in elements:
        rotation_degrees, scale = rotation_scale[element.id]

        if element.final_position is not None:
            position = element.final_position
        else:
            warnings.append(
                "Element id %r (%s) has no finalPosition; using (0, 0, 0)."
                % (element.id, element.asset_name)
            )
            position = (0.0, 0.0, 0.0)

        position = tuple(p * scale_factor for p in position)

        results[element.id] = ResolvedTransform(position=position, rotation=rotation_degrees, scale=scale)
    return results, warnings


def _mat3_apply_to_vector(m, v):
    # type: (tuple, tuple) -> tuple
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


# SWTOR's own raw coordinate convention doesn't match Blender's Z-up
# convention. The .gr2 importer's build() corrects for this on every
# single object it creates via an unconditional
# "ob.matrix_local = Matrix.Rotation(PI * 0.5, 4, 'X')" -- when called
# with apply_axis_conversion=False (which Area Assembler's
# enforce_neutral_settings=True forces), that correction is left as a
# real, unbaked object rotation rather than applied to the mesh data.
# So every mesh Area Assembler imports already carries this rotation,
# and needs it *composed with*, not overwritten by, this module's own
# computed transforms -- see to_blender_space() below, applied exactly
# once per object at the point of actually setting it (not to
# intermediate composition steps like compute_world_rotation_scale or
# compose_dyn_visual_transform, which stay entirely in SWTOR's own raw
# coordinate space, matching how area json's own position/rotation
# values are expressed).
_SWTOR_TO_BLENDER_AXIS_CONVERSION = _mat3_rotation_x(90.0)


def to_blender_space(transform):
    # type: (ResolvedTransform) -> ResolvedTransform
    """
    Converts a fully-composed, still-SWTOR-raw-space ResolvedTransform
    into Blender's coordinate convention. Callers (ops/import_area.py)
    should call this exactly once per object, immediately before
    setting its location/rotation_euler/scale -- not earlier, since
    every composition step upstream of this (parent chains, dyn visual
    offsets) is only valid within a single consistent coordinate space.
    """
    rotation_scale = _mat3_from_rotation_scale(transform.rotation, transform.scale)
    converted_rotation_scale = _mat3_multiply(_SWTOR_TO_BLENDER_AXIS_CONVERSION, rotation_scale)
    rotation_degrees, scale = decompose_rotation_scale(converted_rotation_scale)

    position = _mat3_apply_to_vector(_SWTOR_TO_BLENDER_AXIS_CONVERSION, transform.position)

    return ResolvedTransform(position=position, rotation=rotation_degrees, scale=scale)


def compose_dyn_visual_transform(parent_transform, visual):
    # type: (ResolvedTransform, DynVisual) -> ResolvedTransform
    """
    Combines an already-resolved element's final transform (its `.spn_p`
    resolved to a "dyn" target -- see compute_final_transforms) with one
    of that dyn fqn's own DynVisual entries (see expand_dyn), producing
    that visual's own absolute world transform.

    Unlike area json elements, a dyn's visuals don't carry their own
    authoritative finalPosition -- their position/rotation/scale are
    purely local offsets from whatever placed the dyn (the resolved
    `.spn_p` element), so this needs genuine parent-child composition of
    all three components together, not just the rotation/scale-only
    shortcut compute_world_rotation_scale relies on (that shortcut is
    valid there specifically because finalPosition sidesteps needing
    position in the chain at all -- not the case here).
    """
    parent_rotation_scale = _mat3_from_rotation_scale(parent_transform.rotation, parent_transform.scale)
    visual_rotation_scale = _mat3_from_rotation_scale(visual.rotation, visual.scale)

    world_rotation_scale = _mat3_multiply(parent_rotation_scale, visual_rotation_scale)
    rotation_degrees, scale = decompose_rotation_scale(world_rotation_scale)

    # world_position = parent_position + parent_rotation_scale @ visual_local_position
    offset = tuple(
        sum(parent_rotation_scale[i][k] * visual.position[k] for k in range(3))
        for i in range(3)
    )
    position = tuple(p + o for p, o in zip(parent_transform.position, offset))

    return ResolvedTransform(position=position, rotation=rotation_degrees, scale=scale)