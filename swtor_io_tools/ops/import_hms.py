# <pep8 compliant>

"""
Native `.hms` terrain import: the heightmap tile geometry, its layered
material (ops/hms_materials.py) and its Geometry Nodes DYD scatter
(ops/dyd_scatter.py), built straight from the game's own `.dat` files.
Replaces the external OBJ-per-heightmap tool; there is deliberately no OBJ
fallback.

Used by Area Assembler (ops/import_area.py) today and written so a
standalone importer can drive the same entry points later: everything here
takes plain arguments (an HmsBatch plus an AreaElement) and nothing reaches
back into import_area.py.

============================================================================
HOW A ROOM-JSON `.hms` ELEMENT FINDS ITS DATA
============================================================================
A room json carries each `.hms` element's own `id` and the heightmap
placeholder `assetId` shared by every `.hms` in that area -- but NO area id.
The tile data lives in the owning room's `.dat` under
resources/world/areas/<area>/, and room names repeat between areas, so the
room name alone is ambiguous. What resolves it is the assetId:

  1. AreaIndex maps every heightmap placeholder asset id to the area folder
     whose area.dat lists it. The index is cached on disk (hms_area_index.json,
     see AreaIndex) and re-validated against the areas folder every batch
     (folder names + area.dat size/mtime), so a new game extraction is
     picked up automatically while an unchanged one costs a few stat calls.
  2. In that area, the room .dat named like the json is opened first (case-
     insensitively -- area.dat says "Default", the file is default.dat), and
     the instance whose id equals the element's id is the confirmation; if
     the stem doesn't match a room, the area's other rooms are searched for
     that instance id.
  3. Anything that can't be resolved is counted under a skip reason and
     reported -- never a hard stop for the rest of the batch.

The index records one entry per AREA (not per room or instance): every
`.hms` instance in an area shares that area's one placeholder asset id, and
the room/instance lookup always happens against the real room .dat.

============================================================================
WORLD SCALE (scale_factor, "S")
============================================================================
Area Assembler builds the world at S times SWTOR's natural size (the GR2
"Scale Object" preference, default 10): positions are multiplied by S and
mesh data is baked at S with object scale left at its natural value. A tile
follows exactly that convention, which means every quantity that depends on
the tile's size in Blender units has to be told about S:
  - tile vertices are baked x S (build_hms_object);
  - world-continuous UVs are divided by S again (hms_materials);
  - scatter density, noise frequency and instance scales are corrected by
    S^2, 1/S and S respectively (dyd_scatter.add_dyd_scatter's world_scale).
"""

import json
import math
import os
import time
import traceback
from array import array
from collections import Counter

import bpy

from ..types import area as area_types
from ..types import area_dat, hms, room_dat
from ..utils.binary import DataView
from . import dyd_scatter, hms_materials


# Set False to leave tiles without materials -- isolates geometry from
# hms_materials.py while debugging (same switch the standalone harness had).
BUILD_TERRAIN_MATERIALS = True

# Default for the Area Assembler operator's per-tile safety cap: if a tile's
# DYD scatter is estimated to evaluate more than this many instances in the
# VIEWPORT (the viewport fraction, DYD_VIEWPORT_DENSITY, already applied), its
# Viewport Density is lowered to fit rather than risking a hang or crash.
# Render density is never touched.
DEFAULT_MAX_VIEWPORT_INSTANCES = 500000

INDEX_FILE_NAME = "hms_area_index.json"
INDEX_FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _load_dat(filepath):
    # type: (str) -> DataView
    with open(filepath, "rb") as f:
        raw = f.read()
    return DataView(array("B", raw))


def _find_layer_collection(layer_collection, collection):
    """The LayerCollection wrapping `collection` anywhere under `layer_collection`."""
    if layer_collection.collection is collection:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, collection)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Area index (heightmap placeholder asset id -> area folder)
# ---------------------------------------------------------------------------

class AreaIndex(object):
    """
    {heightmap placeholder asset id: [area folder names]} for everything under
    <resources>/world/areas, cached on disk so only the first run (or a new
    extraction) pays for reading the area.dat files.

    Validity, checked once per batch: every area folder's area.dat must match
    its cached size and modified time, and the stored resources path must be
    the current one. Only new or changed areas are re-read, deleted ones are
    dropped. A heightmap asset id that is still missing from a freshly
    validated index triggers ONE full re-read of every area.dat (guarding
    against a changed file whose size and mtime didn't change) before giving
    up on it.

    Cache location: the user's Blender config folder first (survives addon
    updates, never ends up in the addon's own zip/repo), then the addon's
    bundled_data folder if that can't be written. Whether the save worked is
    always reported, via the `notify` callback.
    """

    def __init__(self, resources_root, notify):
        self._areas_dir = os.path.join(resources_root, "world", "areas")
        self._root_key = os.path.normcase(os.path.normpath(os.path.abspath(resources_root)))
        self._notify = notify
        self._entries = {}      # folder -> {"size": int, "mtime_ns": int, "ids": [int]}
        self._by_asset = {}     # asset id -> [folder, ...]
        self._validated = False
        self._full_rescan_done = False

    @property
    def areas_dir_exists(self):
        return os.path.isdir(self._areas_dir)

    # -- cache file ---------------------------------------------------------

    @staticmethod
    def _cache_dirs():
        dirs = []
        try:
            config_dir = bpy.utils.user_resource('CONFIG', path="swtor_io_tools", create=True)
            if config_dir:
                dirs.append(config_dir)
        except Exception as exc:  # noqa: BLE001
            print("HMS area index: couldn't resolve Blender's config folder (%s)" % exc)
        addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dirs.append(os.path.join(addon_root, "bundled_data"))
        return dirs

    def _load_cache(self):
        for directory in self._cache_dirs():
            path = os.path.join(directory, INDEX_FILE_NAME)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version") != INDEX_FORMAT_VERSION:
                    print("HMS area index: ignoring %s (format version %r)" % (path, data.get("version")))
                    continue
                if data.get("resources_root") != self._root_key:
                    print("HMS area index: %s was built for a different Resources folder -- rebuilding" % path)
                    continue
                areas = {}
                for folder, entry in data["areas"].items():
                    areas[folder] = {
                        "size": int(entry["size"]),
                        "mtime_ns": int(entry["mtime_ns"]),
                        "ids": [int(i) for i in entry["hms_asset_ids"]],
                    }
                return areas
            except Exception as exc:  # noqa: BLE001
                print("HMS area index: couldn't use %s (%s)" % (path, exc))
        return {}

    def _save(self):
        payload = {
            "version": INDEX_FORMAT_VERSION,
            "resources_root": self._root_key,
            "areas": {
                folder: {
                    "size": entry["size"],
                    "mtime_ns": entry["mtime_ns"],
                    "hms_asset_ids": [str(i) for i in entry["ids"]],
                }
                for folder, entry in self._entries.items()
            },
        }
        errors = []
        for directory in self._cache_dirs():
            path = os.path.join(directory, INDEX_FILE_NAME)
            tmp_path = path + ".tmp"
            try:
                os.makedirs(directory, exist_ok=True)
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                os.replace(tmp_path, path)
                with open(path, "r", encoding="utf-8") as f:
                    check = json.load(f)
                if len(check.get("areas", {})) != len(payload["areas"]):
                    raise OSError("read-back has %d areas, expected %d" % (len(check.get("areas", {})), len(payload["areas"])))
                message = "HMS area index: saved %d area(s) to %s" % (len(payload["areas"]), path)
                if errors:
                    message += " (earlier location(s) failed: %s)" % "; ".join(errors)
                self._notify("INFO", message)
                return True
            except Exception as exc:  # noqa: BLE001
                errors.append("%s: %s" % (path, exc))
                try:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
        self._notify(
            "WARNING",
            "HMS area index: could NOT be saved (%s). It is kept in memory for this Blender session only, "
            "so the next session will read the areas folder again." % "; ".join(errors),
        )
        return False

    # -- building -----------------------------------------------------------

    def _scan_current(self):
        current = {}
        with os.scandir(self._areas_dir) as entries:
            for entry in entries:
                if not entry.is_dir():
                    continue
                try:
                    stat = os.stat(os.path.join(entry.path, "area.dat"))
                except OSError:
                    continue
                current[entry.name] = (stat.st_size, stat.st_mtime_ns)
        return current

    def _read_heightmap_ids(self, folder):
        path = os.path.join(self._areas_dir, folder, "area.dat")
        try:
            ad = area_dat.read_area_dat(_load_dat(path))
        except Exception as exc:  # noqa: BLE001
            print("HMS area index: couldn't read %s (%s) -- skipped" % (path, exc))
            return None
        wanted = area_dat.HMS_PLACEHOLDER_ASSET_NAME.lower()
        return [asset.id for asset in ad.assets if asset.name.lower() == wanted]

    def _rebuild_lookup(self):
        self._by_asset = {}
        for folder in sorted(self._entries):
            for asset_id in self._entries[folder]["ids"]:
                self._by_asset.setdefault(asset_id, []).append(folder)

    def _refresh(self, cached):
        """Re-reads whatever differs from `cached` (everything, if empty).
        Returns (reread, reused, changed)."""
        current = self._scan_current()
        entries = {}
        reread = 0
        reused = 0
        for folder, (size, mtime_ns) in current.items():
            old = cached.get(folder)
            if old is not None and old["size"] == size and old["mtime_ns"] == mtime_ns:
                entries[folder] = old
                reused += 1
                continue
            reread += 1
            ids = self._read_heightmap_ids(folder)
            if ids is not None:
                entries[folder] = {"size": size, "mtime_ns": mtime_ns, "ids": ids}
        dropped = set(cached) - set(entries)
        self._entries = entries
        self._rebuild_lookup()
        changed = reread > 0 or bool(dropped)
        return reread, reused, changed

    def ensure_current(self):
        """Loads and validates the index, once per batch."""
        if self._validated:
            return
        self._validated = True
        if not self.areas_dir_exists:
            return
        start = time.time()
        cached = self._load_cache()
        reread, reused, changed = self._refresh(cached)
        elapsed = time.time() - start
        self._notify(
            "INFO",
            "HMS area index: %d area(s) (%d read, %d reused from cache) in %.1fs"
            % (len(self._entries), reread, reused, elapsed),
        )
        if changed or not cached:
            if self._entries:
                self._save()

    def candidates(self, asset_id):
        # type: (int) -> list
        """Area folders whose area.dat lists `asset_id` as its heightmap placeholder."""
        self.ensure_current()
        found = self._by_asset.get(asset_id)
        if found:
            return list(found)
        if self.areas_dir_exists and not self._full_rescan_done:
            self._full_rescan_done = True
            print(
                "HMS area index: asset id %d isn't in the index -- re-reading every area.dat once "
                "in case the cache is stale" % asset_id
            )
            start = time.time()
            self._refresh({})
            self._notify(
                "INFO",
                "HMS area index: full re-read of %d area(s) took %.1fs" % (len(self._entries), time.time() - start),
            )
            if self._entries:
                self._save()
            found = self._by_asset.get(asset_id)
        return list(found or [])


# ---------------------------------------------------------------------------
# Batch (settings + caches shared by every .hms element of one import)
# ---------------------------------------------------------------------------

class _AreaRecord(object):
    __slots__ = ("folder", "directory", "dat", "files_by_lower_name", "hms_asset_ids")

    def __init__(self, folder, directory, dat, files_by_lower_name, hms_asset_ids):
        self.folder = folder
        self.directory = directory
        self.dat = dat
        self.files_by_lower_name = files_by_lower_name
        self.hms_asset_ids = hms_asset_ids


class _RoomRecord(object):
    __slots__ = ("name", "dat", "instances_by_id")

    def __init__(self, name, dat):
        self.name = name
        self.dat = dat
        self.instances_by_id = {inst.id: inst for inst in dat.instances}


class HmsBatch(object):
    """
    Everything the `.hms` elements of ONE import (one operator execute(), any
    number of room jsons) share: the two feature switches, the world scale,
    the area index, parsed area.dat / room .dat caches, and the bookkeeping
    for the per-batch setup and teardown (start/finish). Created once by the
    caller and passed down; nothing here is module-level state.

    Setup is lazy: nothing is touched (no stale-datablock report, no cache
    reset, no collection changes) until the first `.hms` element actually
    needs building, so a batch with no terrain behaves exactly as before.
    finish() must be called once at the end, in a `finally`.
    """

    def __init__(self, resources_root, scale_factor, generate_terrain=True, enable_scatter=True,
                 viewport_density=None, max_viewport_instances=DEFAULT_MAX_VIEWPORT_INSTANCES):
        self.resources_root = resources_root
        self.scale_factor = scale_factor
        self.generate_terrain = generate_terrain
        self.enable_scatter = enable_scatter and generate_terrain
        self.viewport_density = viewport_density
        self.max_viewport_instances = max_viewport_instances

        self.notices = []                 # [(level, message)] worth showing in Blender's status bar
        self.stats = Counter()
        self.terrain_seconds = 0.0        # time spent inside process_terrain_element() only
        self._index = None
        self._areas = {}                  # folder -> _AreaRecord or None (unreadable)
        self._rooms = {}                  # (folder, lower room name) -> _RoomRecord or None
        self._started = False
        self._start_time = 0.0

    # -- notices ------------------------------------------------------------

    def notify(self, level, message):
        print(message)
        self.notices.append((level, message))

    @property
    def index(self):
        if self._index is None:
            self._index = AreaIndex(self.resources_root, self.notify)
        return self._index

    # -- per-batch setup / teardown -------------------------------------------

    def _set_assets_collection_excluded(self, context, excluded):
        collection = bpy.data.collections.get(dyd_scatter.DYD_ASSETS_COLLECTION_NAME)
        if collection is None:
            return
        context.view_layer.update()
        layer_collection = _find_layer_collection(context.view_layer.layer_collection, collection)
        if layer_collection is None:
            print("HMS: couldn't find \"%s\"'s LayerCollection to %s it" % (
                collection.name, "exclude" if excluded else "include"))
            return
        layer_collection.exclude = excluded

    def ensure_started(self, context):
        if self._started:
            return
        self._started = True
        self._start_time = time.time()

        # Leftovers from an earlier run are reused by name instead of rebuilt,
        # which silently overrides new code and settings -- say so up front.
        hms_materials.report_existing_hms_datablocks(extra_group_names=(dyd_scatter.DYD_SCATTER_GROUP_NAME,))
        # Both DYD caches point into bpy.data, which may have changed since.
        dyd_scatter.reset_batch_state()
        # The shared assets collection stays excluded between batches; make
        # sure it is includable while new assets are linked into it.
        self._set_assets_collection_excluded(context, False)

        gr2_scale = dyd_scatter._gr2_scale_compensation_factor()
        if abs(gr2_scale - self.scale_factor) > 1e-6:
            print(
                "HMS NOTE: this batch's scale factor (%g) differs from the GR2 import scale used for DYD mesh "
                "channels (%g) -- their instance scale will be off by that ratio." % (self.scale_factor, gr2_scale)
            )

    def finish(self, context):
        """Once, at the very end of the batch (after the materials pass)."""
        if not self._started:
            return
        try:
            # Per-layer materials, wrapper groups, splat images, card materials
            # and the scatter group were given fake users so nothing could
            # purge them mid-run; they have real users now.
            hms_materials.release_protected_datablocks()
        except Exception as exc:  # noqa: BLE001
            print("HMS: releasing protected datablocks failed: %s" % exc)
        try:
            self._set_assets_collection_excluded(context, True)
        except Exception as exc:  # noqa: BLE001
            print("HMS: couldn't exclude the DYD assets collection: %s" % exc)
        try:
            # Drops the DYD mesh cache (and the fake users it put on its meshes).
            dyd_scatter.reset_batch_state()
        except Exception as exc:  # noqa: BLE001
            print("HMS: couldn't reset the DYD caches: %s" % exc)
        print(
            "HMS terrain: %d tile(s) built, %d DYD scatter modifier(s) added (%.1fs spent building tiles)"
            % (self.stats["tiles"], self.stats["scatter_modifiers"], self.terrain_seconds)
        )
        if self.stats["tiles_density_reduced"]:
            print(
                "HMS terrain: %d tile(s) had their scatter Viewport Density lowered to stay under the "
                "%d-instance cap (render density is unchanged; raise each modifier's Viewport Density or the "
                "operator's \"Max DYD Instances Per Tile\" to see more in the viewport)"
                % (self.stats["tiles_density_reduced"], self.max_viewport_instances)
            )

    # -- .dat caches -----------------------------------------------------------

    def _get_area(self, folder):
        if folder in self._areas:
            return self._areas[folder]
        directory = os.path.join(self.resources_root, "world", "areas", folder)
        record = None
        try:
            dat = area_dat.read_area_dat(_load_dat(os.path.join(directory, "area.dat")))
            # area.dat's room list and the filenames on disk don't always agree
            # in case ("Default" vs default.dat) -- harmless on Windows, a silent
            # miss elsewhere -- so look files up case-insensitively.
            files_by_lower_name = {name.lower(): name for name in os.listdir(directory)}
            wanted = area_dat.HMS_PLACEHOLDER_ASSET_NAME.lower()
            hms_ids = {asset.id for asset in dat.assets if asset.name.lower() == wanted}
            record = _AreaRecord(folder, directory, dat, files_by_lower_name, hms_ids)
        except Exception as exc:  # noqa: BLE001
            print("HMS: couldn't read area %s (%s)" % (folder, exc))
        self._areas[folder] = record
        return record

    def _get_room(self, area_record, room_name):
        key = (area_record.folder, room_name.lower())
        if key in self._rooms:
            return self._rooms[key]
        record = None
        actual = area_record.files_by_lower_name.get(room_name.lower() + ".dat")
        if actual is None:
            print("HMS: area %s lists room %r but no matching .dat file is on disk" % (area_record.folder, room_name))
        else:
            try:
                dat = room_dat.read_room_dat(_load_dat(os.path.join(area_record.directory, actual)))
                record = _RoomRecord(room_name, dat)
            except Exception as exc:  # noqa: BLE001
                print("HMS: couldn't read room %s/%s (%s)" % (area_record.folder, actual, exc))
        self._rooms[key] = record
        return record

    # -- resolution ----------------------------------------------------------------

    def resolve(self, asset_id, instance_id, room_hint):
        # type: (int, int, str) -> tuple
        """
        Finds the (_AreaRecord, _RoomRecord, RoomInstance, failure) a json
        `.hms` element refers to. On failure the first three are None and
        `failure` is "no_area" (no area.dat lists this asset id) or
        "no_instance" (area(s) found, but no room .dat holds this instance);
        on success `failure` is None. `room_hint` is the json's own file
        stem (the room's name).
        """
        folders = self.index.candidates(asset_id)
        if not folders:
            return None, None, None, "no_area"
        for folder in folders:
            area_record = self._get_area(folder)
            if area_record is None:
                continue
            rooms = list(area_record.dat.rooms)
            hinted = [r for r in rooms if r.lower() == room_hint.lower()]
            for room_name in hinted + [r for r in rooms if r not in hinted]:
                room_record = self._get_room(area_record, room_name)
                if room_record is None:
                    continue
                instance = room_record.instances_by_id.get(instance_id)
                if instance is not None and instance.asset_id in area_record.hms_asset_ids:
                    return area_record, room_record, instance, None
        return None, None, None, "no_instance"


# ---------------------------------------------------------------------------
# Tile building
# ---------------------------------------------------------------------------

def hms_transform_for_element(element, scale_factor):
    # type: (area_types.AreaElement, float) -> area_types.ResolvedTransform
    """
    The tile's transform in SWTOR's raw space (not yet converted to Blender's
    -- see apply_hms_transform), built from the element's OWN local position,
    rotation and scale, deliberately skipping any parent-chain composition.
    That is SWTOR's own engine rule for `.hms` specifically (confirmed against
    Jedipedia's test-room-placement.mjs): heightmaps are exempt from
    ParentInstance composition and always use their authored transform.
    Position is multiplied by the world scale like every other object's.
    """
    if element.parent_id not in (None, "0", 0, ""):
        # Not observed in the Onderon arena sample (every .hms there is a
        # root), and per the rule above a parented heightmap STILL ignores its
        # parent, so this is a "did we expect this" note, not an error.
        print(
            "NOTE: .hms instance %s has a non-root parent (%r); per the confirmed engine rule its OWN "
            "transform is used unparented (this is correct, not a bug)." % (element.id, element.parent_id)
        )
    return area_types.ResolvedTransform(
        position=tuple(c * scale_factor for c in element.position),
        rotation=tuple(element.rotation),
        scale=tuple(element.scale),
    )


def apply_hms_transform(obj, transform):
    # type: (bpy.types.Object, area_types.ResolvedTransform) -> None
    """
    Applies `transform` the same way Area Assembler places every object (the
    one SWTOR-to-Blender axis conversion, ZXY rotation order) -- identical to
    ops/import_area.py's _apply_transform(), duplicated here only so this
    module doesn't depend on import_area.py.
    """
    blender_transform = area_types.to_blender_space(transform)
    obj.location = blender_transform.position
    obj.rotation_mode = 'ZXY'
    obj.rotation_euler = tuple(math.radians(d) for d in blender_transform.rotation)
    obj.scale = blender_transform.scale


def build_hms_object(heightmap, resolved_transform, name, collection, world_scale=1.0):
    # type: (hms.Heightmap, area_types.ResolvedTransform, str, bpy.types.Collection, float) -> bpy.types.Object
    """
    Builds one Blender mesh object from an already-parsed heightmap and an
    already-built raw-space transform. Geometry only: positions (baked at
    `world_scale`, object scale stays natural -- this addon's convention),
    triangulated faces, per-vertex UVs, smooth shading. Normals are left to
    Blender (see types/hms.py's docstring for why porting the JS's manual
    normal accumulation would be wasted work). Materials and scatter are
    added by the caller.
    """
    mesh = bpy.data.meshes.new(name)
    if world_scale != 1.0:
        positions = [(x * world_scale, y * world_scale, z * world_scale) for x, y, z in heightmap.positions]
    else:
        positions = heightmap.positions
    faces = [
        (heightmap.indices[i], heightmap.indices[i + 1], heightmap.indices[i + 2])
        for i in range(0, len(heightmap.indices), 3)
    ]
    mesh.from_pydata(positions, [], faces)
    mesh.update()

    # UVs. Without a UV layer every Image Texture node in the terrain
    # material falls back to Blender's default "Generated" (bounding-box)
    # coordinates and stretches each layer's texture across the whole tile
    # as one unrepeated image. heightmap.uvs is per-VERTEX; Blender UV
    # layers are per-LOOP, so each loop reads its own vertex's UV.
    uv_layer = mesh.uv_layers.new(name="hms_uv")
    for loop in mesh.loops:
        uv_layer.data[loop.index].uv = heightmap.uvs[loop.vertex_index]
    mesh.validate()

    # Shade smooth, set directly on the polygons rather than through
    # bpy.ops.object.shade_smooth(): the operator needs the object to be
    # active/selected/in the view layer, which isn't guaranteed mid-batch.
    for polygon in mesh.polygons:
        polygon.use_smooth = True

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    apply_hms_transform(obj, resolved_transform)
    return obj


def process_terrain_element(context, batch, element, area_name, skip_counts):
    # type: (Any, HmsBatch, area_types.AreaElement, str, Counter) -> list
    """Times _build_terrain_element() into batch.terrain_seconds."""
    start = time.time()
    try:
        return _build_terrain_element(context, batch, element, area_name, skip_counts)
    finally:
        batch.terrain_seconds += time.time() - start


def _build_terrain_element(context, batch, element, area_name, skip_counts):
    # type: (Any, HmsBatch, area_types.AreaElement, str, Counter) -> list
    """
    Builds the tile for one json `.hms` element (geometry, material, DYD
    scatter) and returns [object] -- linked to the active collection, like a
    freshly imported .gr2, for the caller to move into its heightmaps
    collection. Returns [] after counting the reason in `skip_counts` for
    anything that can't be built; a failure in the optional parts (material,
    scatter) keeps the tile and only counts the reason.

    `area_name` is the json's own file stem, used only as the room hint.
    """
    if not batch.generate_terrain:
        skip_counts["'.hms' skipped -- Generate Terrain is off (expected)"] += 1
        return []

    if not batch.index.areas_dir_exists:
        skip_counts["'.hms' skipped -- no resources/world/areas folder found (check the Resources folder)"] += 1
        return []

    try:
        instance_id = int(element.id)
        asset_id = int(element.asset_id) if element.asset_id is not None else None
    except (TypeError, ValueError):
        asset_id = None
        instance_id = None
    if instance_id is None or asset_id is None:
        skip_counts["'.hms' json element has no usable id/assetId -- can't tell which area it belongs to"] += 1
        return []

    batch.ensure_started(context)

    area_record, room_record, instance, failure = batch.resolve(asset_id, instance_id, area_name)
    if area_record is None:
        if failure == "no_area":
            skip_counts["'.hms' no area.dat lists this heightmap's asset id (extraction missing or incomplete?)"] += 1
        else:
            skip_counts["'.hms' area found, but none of its room .dat files has this instance"] += 1
        return []

    raw_blob = instance.compressed_vertex_data
    if raw_blob is None:
        skip_counts["'.hms' instance has the heightmap asset id but no VertexData property"] += 1
        return []

    name = "hms_%s_%d" % (room_record.name, instance.id)
    try:
        decompressed = hms.decompress_heightmap_blob(raw_blob)
        heightmap = hms.parse_heightmap(decompressed, area_record.dat.terrain_texture_name)
        transform = hms_transform_for_element(element, batch.scale_factor)
        obj = build_hms_object(heightmap, transform, name, context.collection, world_scale=batch.scale_factor)
    except Exception as exc:  # noqa: BLE001
        print("HMS: instance %d (%s) failed: %s" % (instance.id, name, exc))
        traceback.print_exc()
        skip_counts["'.hms' heightmap failed to parse/build (see console)"] += 1
        return []

    batch.stats["tiles"] += 1

    if BUILD_TERRAIN_MATERIALS:
        try:
            hms_materials.build_hms_material(obj, heightmap, "hms_mat_%s" % name, world_scale=batch.scale_factor)
        except Exception as exc:  # noqa: BLE001
            print("HMS: material for %s failed: %s" % (name, exc))
            traceback.print_exc()
            skip_counts["'.hms' terrain material failed to build (tile kept without it, see console)"] += 1

    if batch.enable_scatter and heightmap.dyd_channels:
        try:
            scatter_info = {}
            added = dyd_scatter.add_dyd_scatter(
                obj, heightmap, area_record.dat,
                viewport_density=batch.viewport_density,
                world_scale=batch.scale_factor,
                max_viewport_instances=batch.max_viewport_instances,
                info=scatter_info,
            )
            batch.stats["scatter_modifiers"] += added
            if "viewport_density_reduced" in scatter_info:
                batch.stats["tiles_density_reduced"] += 1
            if added == 0:
                print(
                    "HMS: %s: %d dyd channel(s) present but none scattered (see per-channel messages above)"
                    % (name, len(heightmap.dyd_channels))
                )
                skip_counts["'.hms' DYD scatter added no modifiers (see console)"] += 1
        except Exception as exc:  # noqa: BLE001
            print("HMS: DYD scatter for %s failed: %s" % (name, exc))
            traceback.print_exc()
            skip_counts["'.hms' DYD scatter failed (see console)"] += 1

    return [obj]
