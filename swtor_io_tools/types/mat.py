# <pep8 compliant>

"""
Two-tier reader for SWTOR's .mat shader-definition files (XML).

Tier 1 (read_mat_summary): cheap universal reader used by every .mat
consumer -- the materials-by-name automatic (import_gr2.py 7a) and
manual (ops/process_materials.py 7b) entry points, ops/import_cha.py's
NPC importer (alpha only, see its get_or_create_material()), and
eventually Area Assembler / generic-.gr2 import. Reads only <Derived>
and <AlphaMode>/<AlphaTestValue>, plus the skip-list check. Never
touches map/palette data.

Tier 2 (read_mat_full): full <input>-list adapter, producing a dict
shaped identically to a Jedipedia json entry's materialInfo (matPath +
ddsPaths + otherValues), so it can be handed straight to
ops/import_cha.py's existing get_or_create_material() unchanged. Used by
ops/process_materials.py (where there's no json to lean on at all, so
this drives ddsPaths AND otherValues both) and by ops/import_cha.py's
own use_mat_dds_paths option (where it only ever overrides specific
"identity" ddsPaths keys -- diffuseMap/rotationMap/glossMap/paletteMap/
paletteMaskMap/directionMap and friends -- never otherValues: NPC
import's own palette/complexion/age/facepaint data stays json-sourced
permanently regardless of that option, since a .mat file's own values
for those are generic placeholders, not real per-NPC customization --
confirmed against real data, see handoff notes / chat history).

Carries forward the XML tag/semantic knowledge (not the output code)
from the prior zg_swtor_tools addon's mat_process_named_materials.py,
per the handoff.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Shader-type aliasing (handoff §3)
# ---------------------------------------------------------------------------

# Collapse onto DERIVED_CONFIGS' "Uber" entry (ops/import_cha.py).
UBER_LIKE = {
    "Uber", "UberHueable", "VegetationHighQuality", "Grass", "UberEnvBlend",
    "Glass", "Ice", "UberScrolling", "Cloud", "Waterfall", "Vegetation",
}
# Collapse onto DERIVED_CONFIGS' "Creature" entry. Mirrors import_cha.py's
# own existing "HighQualityCharacter" -> "Creature" handling.
CREATURE_LIKE = {"Creature", "HighQualityCharacter"}
# Collapse onto DERIVED_CONFIGS' "Garment" entry (which already covers
# "GarmentScrolling" internally too -- collapsing here as well just
# keeps every caller's "derived" value uniformly "Garment").
GARMENT_LIKE = {"Garment", "GarmentScrolling"}
# Collapse variant Derived spellings onto DERIVED_CONFIGS' single
# "AnimatedUV" key (ops/import_cha.py) -- no longer a "known but not yet
# built" bucket now that a real template exists.
ANIMATEDUV_LIKE = {
    "AnimatedUV", "AnimatedUVAlphaBlend", "AnimatedVFX", "AnimatedVFXAlphaBlend",
}

# Recognized but not yet built (handoff §3): materials of these collapsed
# types should be skipped cleanly (not mishandled, not force-fit into
# DERIVED_CONFIGS). Real support is additive later, not a rework.
KNOWN_UNBUILT_DERIVED = {"EmissiveOnly"}

_DERIVED_ALIASES = {}
for _name in UBER_LIKE:
    _DERIVED_ALIASES[_name] = "Uber"
for _name in CREATURE_LIKE:
    _DERIVED_ALIASES[_name] = "Creature"
for _name in GARMENT_LIKE:
    _DERIVED_ALIASES[_name] = "Garment"
for _name in ANIMATEDUV_LIKE:
    _DERIVED_ALIASES[_name] = "AnimatedUV"
del _name


def collapse_derived(derived):
    # type: (str) -> str
    """
    Collapses a .mat file's raw <Derived> value onto the canonical
    shader-family name used by ops/import_cha.py's DERIVED_CONFIGS
    ("Uber"/"Creature"/"Garment"/"SkinB"/"Eye"/"HairC"/"AnimatedUV"), or
    onto "EmissiveOnly" for the known-but-not-yet-built bucket (see
    KNOWN_UNBUILT_DERIVED). Anything else passes through unchanged
    -- DERIVED_CONFIGS.get() rejecting it is the caller's signal that
    it's a genuinely unsupported type, not this function's job to guess.
    """
    return _DERIVED_ALIASES.get(derived, derived)


# ---------------------------------------------------------------------------
# Skip-list (handoff §2 Tier 1) -- carried forward from
# zg_swtor_tools' process_mats() as-is (substring checks, not exact/
# prefix matches), confirmed with Crunch: worth tightening later
# (possibly to "default" / "default.0xx" specifically) but not required
# now.
# ---------------------------------------------------------------------------

def should_skip_material(mat_name):
    # type: (str) -> bool
    """
    True if `mat_name` should never get a real shader built: Template
    placeholders, PC/NPC "default" wildcard materials, and the engine's
    collision-hidden material.

    Deliberately NOT applied inside ops/import_cha.py's NPC/Character
    (.json) importer -- confirmed with Crunch, since Jedipedia json data
    is always treated as already-correct there.
    """
    if "Template:" in mat_name:
        return True
    if "default" in mat_name:
        return True
    if mat_name == "util_collision_hidden":
        return True
    return False


# ---------------------------------------------------------------------------
# .mat path resolution (handoff §4.1)
# ---------------------------------------------------------------------------

MATERIALS_SUBPATH = ("art", "shaders", "materials")


def resolve_mat_path(mat_name, primary_resources_dir, legacy_resources_dir=None):
    # type: (str, Optional[str], Optional[str]) -> Tuple[Optional[Path], Optional[str]]
    """
    Resolves a bare material name (e.g. "boot_tall_heavy_bh_a02c06_u",
    no extension) to a real .mat file under
    <resources>/art/shaders/materials/, checking `primary_resources_dir`
    first and `legacy_resources_dir` (if given) as a fallback.

    Returns (mat_path, root_used) -- root_used is whichever of the two
    roots the file actually turned up under, since callers go on to
    resolve this same material's texture map paths against that same
    root. Returns (None, None) if not found in either -- callers should
    collect these as a warning/error list and continue, never raise.
    """
    filename = mat_name + ".mat"
    for root in (primary_resources_dir, legacy_resources_dir):
        if not root:
            continue
        candidate = Path(root, *MATERIALS_SUBPATH, filename)
        if candidate.is_file():
            return candidate, root
    return None, None


# ---------------------------------------------------------------------------
# Tier 1 -- universal reader
# ---------------------------------------------------------------------------

# AlphaMode -> (Alpha Blend, Alpha Test) native-group-node input booleans.
# Confirmed against zg_swtor_tools' process_mats(), the only place the
# full enum was spelled out: "Test" alone maps to Alpha Test; "Full" /
# "MultipassFull" / "Add" all collapse to a Blend equivalent; anything
# else (including "None", the common case) is neither. The native
# Atroxa Shaders system computes alpha compositing entirely inside the
# node group via these two booleans plus Alpha Test Value/Invert Alpha
# -- material.surface_render_method stays "DITHERED" regardless (set
# directly in ops/import_cha.py's get_or_create_material(), and baked
# into every template material in bundled_data/Atroxa_Shaders.blend --
# see types/shader_templates.py), so no other Blender-level alpha
# property needs to vary per AlphaMode.
_ALPHA_TEST_MODES = {"Test"}
_ALPHA_BLEND_MODES = {"Full", "MultipassFull", "Add"}


class MatSummary:
    """
    Tier 1 result: just enough to drive alpha settings and the
    skip-list/shader-family checks. No map/palette data -- see
    read_mat_full() (Tier 2) for that.
    """
    __slots__ = ("derived", "alpha_blend", "alpha_test", "alpha_test_value", "skip")

    def __init__(self, derived, alpha_blend, alpha_test, alpha_test_value, skip):
        self.derived = derived
        self.alpha_blend = alpha_blend
        self.alpha_test = alpha_test
        self.alpha_test_value = alpha_test_value
        self.skip = skip


def read_mat_summary(mat_path, mat_name=None):
    # type: (Path, Optional[str]) -> Optional[MatSummary]
    """
    Tier 1: reads only <Derived>, <AlphaMode>, <AlphaTestValue> from a
    real .mat file at `mat_path`, plus the skip-list check (against
    `mat_name`, or mat_path's own stem if not given).

    Returns None if the file can't be parsed (missing, malformed XML,
    or missing a required element) -- callers treat that the same as
    "no matching .mat file", never as a hard error.
    """
    name = mat_name or Path(mat_path).stem
    try:
        root = ET.parse(mat_path).getroot()
        derived = root.find("Derived").text or ""
        alpha_mode = (root.find("AlphaMode").text or "None").strip()
        alpha_test_value = float(root.find("AlphaTestValue").text or 0.0)
    except (ET.ParseError, OSError, AttributeError, ValueError):
        return None

    return MatSummary(
        derived=collapse_derived(derived),
        alpha_blend=alpha_mode in _ALPHA_BLEND_MODES,
        alpha_test=alpha_mode in _ALPHA_TEST_MODES,
        alpha_test_value=alpha_test_value,
        skip=should_skip_material(name),
    )


# ---------------------------------------------------------------------------
# Tier 2 -- full map/palette-building adapter
# ---------------------------------------------------------------------------

# Only semantics actually consumed by DERIVED_CONFIGS' "maps" tuples
# (ops/import_cha.py) get captured into ddsPaths -- every OTHER texture
# semantic a .mat file's <input> list carries (EmissiveMap, OffsetMap,
# animatedWrinkle*, Rim*, Reflection*, vegetationParams*, etc.) is
# intentionally ignored: none of it is wired into any Atroxa Shaders
# node group. Non-texture semantics are handled differently -- see the
# generic fallback in the parsing loop below, which captures everything
# NOT explicitly matched first under its own exact .mat name.
_TEXTURE_SEMANTIC_TO_DDS_KEY = {
    "DiffuseMap": "diffuseMap",
    "RotationMap1": "rotationMap",
    "GlossMap": "glossMap",
    "PaletteMap": "paletteMap",
    "PaletteMaskMap": "paletteMaskMap",
    "DirectionMap": "directionMap",
    "ComplexionMap": "complexionMap",
    "FacepaintMap": "facepaintMap",
    "AgeMap": "ageMap",
    "AnimatedTexture1": "animatedTexture1",
    "AnimatedTexture2": "animatedTexture2",
    # FresnelGradient: the template's AnimatedUV material carries an
    # unconnected Image Texture node for this (ready for when the shader
    # actually uses it), but nothing consumes it yet -- included here
    # anyway so a .dds path is at least captured/available already.
    "FresnelGradient": "fresnelGradient",
}

_PALETTE_VECTOR_SEMANTICS = ("palette1", "palette2")
_PALETTE_RGBA_SEMANTICS = (
    "palette1Specular", "palette2Specular",
    "palette1MetallicSpecular", "palette2MetallicSpecular",
)

# otherValues defaults -- always all nine keys present, matching a real
# Jedipedia json materialInfo.otherValues' own convention (confirmed
# against real NPC json data), regardless of whether the resolved
# shader type's DERIVED_CONFIGS entry actually reads a given one (e.g.
# Uber never touches any of these -- harmless to include anyway).
_DEFAULT_OTHER_VALUES = {
    "flush": ["0", "0", "0"],
    "fleshBrightness": "0",
    "palette1": ["0", "0", "0", "0"],
    "palette1Specular": ["0", "0", "0"],
    "palette1MetallicSpecular": ["0", "0", "0"],
    "palette2": ["0", "0", "0", "0"],
    "palette2Specular": ["0", "0", "0"],
    "palette2MetallicSpecular": ["0", "0", "0"],
}


def read_mat_full(mat_path, mat_name=None):
    # type: (Path, Optional[str]) -> Optional[Dict[str, Any]]
    """
    Tier 2: parses a .mat file's full <input> list into a dict shaped
    identically to a Jedipedia json entry's materialInfo -- "matPath"
    (resources-root-relative, same convention as the json's own),
    "ddsPaths", "otherValues" -- so it can be handed straight to
    ops/import_cha.py's existing get_or_create_material() unchanged.

    Returns None on the same parse failures read_mat_summary() guards
    against. In practice this is only called after that already
    succeeded, but stays defensive on its own regardless.
    """
    name = mat_name or Path(mat_path).stem
    try:
        root = ET.parse(mat_path).getroot()
        # Confirms the file parses; the raw <Derived> text is carried
        # into otherValues["derived"] below for shape-fidelity with the
        # json path, even though this round's callers already have the
        # (collapsed) derived type from Tier 1 and don't re-read it here.
        derived_text = root.find("Derived").text or ""
    except (ET.ParseError, OSError, AttributeError):
        return None

    other_values = dict(_DEFAULT_OTHER_VALUES)
    other_values["derived"] = derived_text
    dds_paths = {}

    for input_el in root.findall("input"):
        semantic_el = input_el.find("semantic")
        type_el = input_el.find("type")
        value_el = input_el.find("value")
        if semantic_el is None or type_el is None or value_el is None:
            continue

        semantic = (semantic_el.text or "").strip()
        input_type = (type_el.text or "").strip()
        value = value_el.text or ""

        if input_type == "texture":
            dds_key = _TEXTURE_SEMANTIC_TO_DDS_KEY.get(semantic)
            if dds_key is not None:
                # .mat texture values never carry the ".dds" extension
                # (unlike a json's ddsPaths, which always do) -- add it
                # here so downstream loading (import_cha.py's
                # _load_or_get_image()/resolve_resource_path()) sees the
                # exact same shape either way.
                normalized = value.replace("\\", "/").lstrip("/")
                dds_paths[dds_key] = "/" + normalized + ".dds"
            continue

        if semantic in _PALETTE_VECTOR_SEMANTICS or semantic in _PALETTE_RGBA_SEMANTICS:
            other_values[semantic] = [v.strip() for v in value.split(",")]
        elif semantic == "FlushTone":
            # Renamed, not identity-captured -- get_or_create_material()
            # reads this specific key ("flush"), not the .mat's own
            # "FlushTone" semantic name. Takes priority over the generic
            # fallback below, which would otherwise capture it under the
            # wrong key.
            other_values["flush"] = [v.strip() for v in value.split(",")]
        elif semantic == "FleshBrightness":
            # Same renaming reasoning as FlushTone above.
            other_values["fleshBrightness"] = value.strip()
        elif semantic:
            # Generic capture for every other non-texture semantic, under
            # its own exact .mat name (not renamed) -- e.g. AnimatedUV's
            # animTexTint0/animTexUVScrollSpeed0/etc, which DERIVED_CONFIGS
            # entries read directly by these exact names. Comma-split into
            # a list for multi-component values (vector4/uvscale/etc,
            # matching palette's own shape above and a real json
            # materialInfo.otherValues' own convention), else stored as a
            # single stripped string. Values that no DERIVED_CONFIGS entry
            # ever reads just sit here unused, same as every one of
            # _DEFAULT_OTHER_VALUES' own always-present keys already does
            # for shader types that don't need them.
            other_values[semantic] = [v.strip() for v in value.split(",")] if "," in value else value.strip()

    mat_path_relative = "/" + "/".join(MATERIALS_SUBPATH) + "/" + name + ".mat"

    return {
        "matPath": mat_path_relative,
        "ddsPaths": dds_paths,
        "otherValues": other_values,
    }