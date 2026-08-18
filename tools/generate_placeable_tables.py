"""
Maintenance script -- NOT part of the shipped addon.

Regenerates swtor_io_tools' bundled_data/ Area Assembler reference files
from raw spn/plc/dyn node-export zips (e.g. the "7.9.1a" set). Run this
locally whenever SWTOR updates and fresh exports are available; the
addon itself only ever reads the two output files below, never the raw
inputs.

Resolution chain (spn -> plc -> target), confirmed against real 7.9.1a
data:

    area json element's assetName (a ".spn_p"-suffixed backslash path)
        -> spn fqn (dotted form of that same path, minus ".spn_p")
        -> spn zip entry, type == "placeable"
        -> its embedded "plc.xxx" fqn reference
        -> plc zip entry
        -> its own single target field: either
             - a direct ".gr2"/".mag"/".fxp"/".fxspec" resource path, or
             - a "dyn.xxx" fqn, itself resolved later (at import time,
               unchanged from the original design) against dyn_data.zip
               to expand into synthetic child elements.

Field IDs (SWTOR's own internal schema, not ours) used below were
confirmed stable between the addon's existing dyn.zip (older export)
and this round's dyn_7_9_1a.zip -- same numeric IDs, just a leaner
list-based serialization instead of the old {"value": {...}} wrapper.

Outputs (game version stamped into both filenames -- see --version below,
e.g. "7_9_1a" -- so it's obvious at a glance how stale a given addon
release's bundled data is without having to open either file):

    bundled_data/spn_table_<version>.json
        One entry per resolved (or explicitly unresolved) ".spn_p"
        path, keyed exactly as it appears in an area json's assetName
        (backslash form) so runtime lookup needs zero normalization
        beyond what §2c/§2b already called for. See SpnEntry below for
        the value shape.

    bundled_data/dyn_data_<version>.zip
        The plc zip is NOT re-shipped -- once its one relevant field
        per entry is resolved into spn_table_<version>.json, it has no
        further runtime use (mirrors the original workflow, which never
        shipped a plc.zip either). The dyn zip IS still needed at
        runtime (each entry's full visual list is read at import time,
        per element, not resolved ahead of time here) -- repacked with
        real compression, which alone accounts for most of the size win
        (source zips are 0%-compressed/STORED).

Only ONE version's worth of bundled_data/ files should exist in the
addon at a time -- this script doesn't clean up a previous version's
output; delete the old spn_table_*.json/dyn_data_*.zip pair by hand
when replacing it, and update whatever in types/area.py hardcodes the
current filenames.
"""

import json
import re
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Known field IDs (SWTOR's own schema)
# ---------------------------------------------------------------------------

FIELD_SPN_TYPE = "4611686019263330339"          # "placeable" / "creature" / ...
FIELD_SPN_LOOKUP_LIST = "4611686029875809984"   # list of dicts, one holds the plc ref
FIELD_SPN_PLC_REF = "4611686335039500012"       # the "plc.xxx" fqn, inside that list
FIELD_PLC_TARGET = "4611686019112067712"        # direct path OR "dyn.xxx" fqn

# Fallback only -- used if the structured lookup above ever fails on some
# entry shape we haven't seen; kept because the original
# placeables-list-generator.py relied on this and it's a reasonable
# safety net, not because structured access has shown any weakness in
# validation (100% agreement across sampling).
_PLC_TEXT_RE = re.compile(r'"(plc\.[a-zA-Z0-9_.]+)"')

_DIRECT_EXTENSIONS = (".gr2", ".mag", ".fxp", ".fxspec")


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

@dataclass
class SpnEntry:
    plc: Optional[str] = None              # the intermediate plc fqn, kept for
                                            # debugging/future use even though
                                            # the runtime resolver doesn't need it
    target_type: str = "unresolved"        # "direct" | "dyn" | "unresolved"
    target: Optional[str] = None           # direct resource path, or dyn fqn
    target_ext: Optional[str] = None       # "gr2" / "mag" / "fxp" (target_type == "direct" only)
    reason: Optional[str] = None           # set only when target_type == "unresolved"


def _spn_path_key(fqn):
    # type: (str) -> str
    """
    "spn.location.korriban.wonkavator...airlock" ->
    "\\spn\\location\\korriban\\wonkavator...airlock.spn_p"

    Matches the literal backslash-path form area json's assetName field
    actually uses -- confirmed against real kor_rebuild test data.
    """
    return "\\" + fqn.replace(".", "\\") + ".spn_p"


def _find_plc_ref(spn_data, raw_text):
    # type: (dict, str) -> Optional[str]
    lookup_list = spn_data.get(FIELD_SPN_LOOKUP_LIST)
    if isinstance(lookup_list, list):
        for item in lookup_list:
            if isinstance(item, dict):
                ref = item.get(FIELD_SPN_PLC_REF)
                if isinstance(ref, str) and ref.startswith("plc."):
                    return ref
    # Fallback: raw text scan (see _PLC_TEXT_RE's docstring above).
    match = _PLC_TEXT_RE.search(raw_text)
    return match.group(1) if match else None


def _classify_target(value):
    # type: (str) -> tuple
    """Returns (target_type, target, target_ext)."""
    if value.startswith("dyn."):
        return "dyn", value, None
    lower = value.lower()
    for ext in _DIRECT_EXTENSIONS:
        if lower.endswith(ext):
            normalized = value.replace("\\", "/")
            if not normalized.startswith("/"):
                normalized = "/" + normalized
            return "direct", normalized, ext.lstrip(".")
    # A target field we don't recognize -- kept as "unresolved" with the
    # raw value in `reason` rather than silently dropped, so a future
    # regeneration (or a human skimming the json) can see exactly what
    # showed up without re-deriving it from the raw zips.
    return "unresolved", None, None


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------

def generate_spn_table(spn_zip_path, plc_zip_path):
    # type: (str, str) -> dict
    spn_zip = zipfile.ZipFile(spn_zip_path)
    plc_zip = zipfile.ZipFile(plc_zip_path)
    plc_names = set(plc_zip.namelist())

    table = {}
    counts = {"placeable": 0, "resolved_direct": 0, "resolved_dyn": 0, "unresolved": 0}

    for name in spn_zip.namelist():
        raw = spn_zip.read(name)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue

        if data.get(FIELD_SPN_TYPE) != "placeable":
            continue
        counts["placeable"] += 1

        fqn = data.get("_metadata", {}).get("fqn")
        if not fqn:
            continue
        key = _spn_path_key(fqn)

        text = raw.decode("utf-8", errors="replace")
        plc_fqn = _find_plc_ref(data, text)
        if plc_fqn is None:
            table[key] = asdict(SpnEntry(target_type="unresolved", reason="no plc reference found"))
            counts["unresolved"] += 1
            continue

        plc_filename = plc_fqn + ".json"
        if plc_filename not in plc_names:
            table[key] = asdict(SpnEntry(plc=plc_fqn, target_type="unresolved", reason="plc file missing"))
            counts["unresolved"] += 1
            continue

        plc_data = json.loads(plc_zip.read(plc_filename))
        target_value = plc_data.get(FIELD_PLC_TARGET)
        if not isinstance(target_value, str) or not target_value:
            table[key] = asdict(SpnEntry(plc=plc_fqn, target_type="unresolved", reason="plc has no target field"))
            counts["unresolved"] += 1
            continue

        target_type, target, target_ext = _classify_target(target_value)
        entry = SpnEntry(plc=plc_fqn, target_type=target_type, target=target, target_ext=target_ext)
        if target_type == "unresolved":
            entry.reason = "unrecognized plc target value: %r" % target_value
            counts["unresolved"] += 1
        elif target_type == "direct":
            counts["resolved_direct"] += 1
        else:
            counts["resolved_dyn"] += 1
        table[key] = asdict(entry)

    return table, counts


def repack_dyn_zip(src_zip_path, dst_zip_path):
    # type: (str, str) -> None
    """
    Straight repack with real DEFLATE compression -- source entries are
    all 0%-compressed (STORED). No field-level stripping in this first
    pass; see the module docstring for why that's a separate, later
    decision once we've seen the real size this alone gets us to.
    """
    src = zipfile.ZipFile(src_zip_path)
    Path(dst_zip_path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spn_zip", help="raw spn node-export zip, e.g. spn_7_9_1a.zip")
    parser.add_argument("plc_zip", help="raw plc node-export zip, e.g. plc_7_9_1a.zip")
    parser.add_argument("dyn_zip", help="raw dyn node-export zip, e.g. dyn_7_9_1a.zip")
    parser.add_argument("out_dir", help="output directory, typically the addon's bundled_data/")
    parser.add_argument(
        "--version", required=True,
        help="SWTOR game version these exports were pulled from, stamped into both "
             "output filenames, e.g. 7_9_1a (matches the source zips' own naming style)",
    )
    args = parser.parse_args()

    table, counts = generate_spn_table(args.spn_zip, args.plc_zip)
    print("spn table:", counts)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    spn_table_path = out_dir / ("spn_table_%s.json" % args.version)
    with open(spn_table_path, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=None, separators=(",", ":"), sort_keys=True)
    print("wrote", spn_table_path, spn_table_path.stat().st_size, "bytes")

    dyn_out_path = out_dir / ("dyn_data_%s.zip" % args.version)
    repack_dyn_zip(args.dyn_zip, dyn_out_path)
    print("wrote", dyn_out_path, dyn_out_path.stat().st_size, "bytes")
