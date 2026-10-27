# shared.py

import os
from pathlib import Path
from typing import Dict, List, Optional

# Dict for sharing info between this Add-on's Operators
# and functions about imported objects.
# It's defined here to work as a global of sorts by
# importing it to the relevant modules (import_gr2.py
# and import_cha.py, so far).

job_results = {'job_origin'        : "",
               'objs_names'        : [],
               'files_objs_names' : {},}


# The dict is also converted to .json and placed
# in a custom scene stringProperty:
#
# bpy.context.scene.swtor_io_last_job
#
# before exiting this add-on's jobs so that other
# Add-ons can read it. It's the simplest way to
# share info, as Blender Operators don't return
# data other than success or failure.

# NEVER RESET IT WITH A job_results = {}
# That would create a new var with too local a scope.
# Clear specific keys:values instead.

# Info stored so far:

# 'job_origin': <operator's bl_idname>

# 'obj_list': [<list of resulting Blender object names>]
#
# (As many SWTOR .gr2 object files hold multiple
# meshes and Blender only supports single mesh objects,
# it is possible to obtain multiple objects from a single
# .gr2 file import. In such cases, we assume that, for single
# .gr2 file import calls, the first object in the list is
# the main one.
#
# Also, as we have to deal with Blender's anti-name collisions
# .xxx suffixes system (.001, .002, etc.), we store those
# actual bpy.data.objects names instead of the original names
# to spare others the confusion)

# If the Add-on's operators set the job_results_rich parameter
# to True, 'files_and_folders' is filled with:

# 'files_and_objects': {
#       <filepath a>: [<list of resulting Blender object names>]
#       <filepath b>: [<list of resulting Blender object names>]
#       etc.
#       }
# 
# - filepaths are normalized to Unix-style forward slashes.
# - If the files belong to an assets extraction's 'resources' folder,
#   their paths start at its 'art' subdirectory, without an initial
#   forward slash. SWTOR's internal conventions regarding paths are
#   such a horror show that we might as well follow Python's.


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

    return resolve_existing_case(resources_root, parts)


# directory path -> (that directory's mtime_ns, {lowercase name: [real names, sorted]})
_dir_listing_cache = {}


def _dir_listing(dir_path):
    # type: (str) -> Dict[str, List[str]]
    """
    A directory's entries grouped by lowercased name, cached per directory
    and refreshed whenever the directory's mtime changes (so files added
    mid-session are still found). Unreadable/non-directory -> {}.
    """
    try:
        mtime = os.stat(dir_path).st_mtime_ns
    except OSError:
        return {}
    cached = _dir_listing_cache.get(dir_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    listing = {}  # type: Dict[str, List[str]]
    try:
        with os.scandir(dir_path) as entries:
            for entry in entries:
                listing.setdefault(entry.name.lower(), []).append(entry.name)
    except OSError:
        return {}
    for names in listing.values():
        names.sort()
    _dir_listing_cache[dir_path] = (mtime, listing)
    return listing


def _search_existing_case(current, remaining):
    # type: (str, List[str]) -> Optional[str]
    """
    Depth-first walk of `remaining` path components under `current`, trying
    each component's exact spelling first, then any other-case spelling that
    exists on disk (alphabetical order if several). Backtracks, so e.g. a
    "PSD" directory that exists but doesn't contain the file doesn't hide a
    sibling "psd" directory that does. Returns the full path of the first
    complete match, else None.
    """
    if not remaining:
        return current
    part, rest = remaining[0], remaining[1:]
    if part == "..":
        return _search_existing_case(os.path.join(current, part), rest)

    candidates = []
    if os.path.lexists(os.path.join(current, part)):
        candidates.append(part)   # exact case always tried first
    for name in _dir_listing(current).get(part.lower(), []):
        if name not in candidates:
            candidates.append(name)

    for name in candidates:
        found = _search_existing_case(os.path.join(current, name), rest)
        if found is not None:
            return found
    return None


def resolve_existing_case(root, parts):
    # type: (str, List[str]) -> str
    """
    str(Path(root, *parts)), except that if that exact path doesn't exist,
    each component of `parts` is matched case-insensitively against what's
    really on disk and the corrected path returned. SWTOR's own data is
    inconsistent about case (e.g. a .mat saying ...\\texture\\PSD\\... for a
    folder that is really "psd"), which Windows doesn't care about but
    case-sensitive filesystems (Linux) do.

    An exact-case match always wins over a differently-cased one. `root`
    itself is taken as given (it's the user's own configured path). If
    nothing matches, the original (literal) path is returned unchanged, so
    a genuinely missing file fails exactly as it always has.
    """
    literal = str(Path(root, *parts))
    if os.path.exists(literal):
        return literal
    found = _search_existing_case(str(root), [p for p in parts if p not in ("", ".")])
    return str(Path(found)) if found is not None else literal


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
