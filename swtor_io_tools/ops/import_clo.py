# <pep8 compliant>

"""
This script imports Star Wars: The Old Republic cloth physics files into Blender.

Usage:
Run this script from "File->Import" menu and then load the desired CLO file.

Module layout
-------------
This module holds the registered import operator, the binary .clo reader (shared by
every build mode), the sibling-mesh lookup (also shared -- Physics mode will need
it just as much as Rig mode does to find vertex groups/weights to work from), the
target-armature resolver (``resolve_target_armature``) and the visibility guard
(``temporarily_visible``). The actual build logic lives in separate modules
so this one stays a manageable size:

    import_clo_rig.py         -- "Build Rig": positions a real, posable skeleton from
                                  the cloth bones (this is the mode that's actually
                                  implemented -- see its own module docstring for the
                                  full design notes accumulated building it)
    import_clo_physics.py     -- "Build Cloth Physics": sets up Cloth Physics on the
                                  SELECTED mesh using the .clo's own pin/stiffness/
                                  gravity data (colliders not yet implemented -- see
                                  its own module docstring)
    import_clo_rig_spline.py  -- Spline IK / Bendy Bones layered on top of a built rig
                                  (not yet implemented)



File format
-----------
https://github.com/SWTOR-Slicers/WikiPedia/wiki/CLO-File-Structure

Binary layout ported from Jedipedia's clo_binary-read.js (the actual in-browser
.clo parser), which documents the format far more completely than this add-on's
previous reader did: a fixed 0x10-byte header, then a version-dependent offset
table (32-bit offsets for versions 1-2, 64-bit for version 3 / the 64-bit client),
a fixed-size (0x20 bytes) string table, and five parallel sections: bones,
particles, per-particle radius data, edges, colliders and triangles.


Operator
--------
    bl_idname:  ``import_cloth.clo`` (class ``ImportCLO``)
    Menu:       File > Import > Import SWTOR (.clo)

Modes (``mode`` property)
-------------------------
    'RIG' (default)
        Builds a posable skeleton from the cloth bones. If an armature is targeted
        (see "Choosing the target armature"), the bones are merged into it and
        parented to the matching real skeleton bones (Pelvis, Chest2, Head, ...).
        Otherwise a standalone armature is created.
    'PHYSICS'
        Sets up Cloth Physics on the SELECTED mesh from the .clo's pin, stiffness
        and gravity data. Does not use ``armature_name``.

Choosing the target armature (RIG mode)
---------------------------------------
    1. ``armature_name`` property, if non-empty: the armature object with that name.
       The active object is ignored and nothing needs to be selected. A hidden
       target (object, collection or view-layer hiding) is revealed for the build
       and its visibility restored afterwards. If the name is not an armature
       object in the current scene, the operator reports an error and returns
       {'CANCELLED'}; it never falls back to another target.
    2. Otherwise, the active object, if it is an armature.
    3. Otherwise, a new standalone armature is created.

Operator properties
-------------------
    filepath, directory, files   File(s) to import. Several files in one call are
                                 imported in order, all into the same target.
    mode                         'RIG' or 'PHYSICS' (see above).
    armature_name                Target armature object name (see above).
                                 Not remembered between calls.
    skip_unweighted_bones        RIG: omit bones with no weight on the matching
                                 mesh (the object named after the .clo's base file
                                 name), reparenting their children upward.
    add_master_bone              RIG, standalone armature only: add one parent bone
                                 above the placeholders.
    physics_auto_fix_floating_islands
                                 PHYSICS: force-pin disconnected mesh islands that
                                 have real bone weights to fall back on.
    use_spline_ik, use_bendy_bones, add_curve_hooks, preserve_bone_length
                                 RIG: reserved for import_clo_rig_spline.py; not yet
                                 implemented.

Usage from the UI
-----------------
    Select the target armature (RIG) or mesh (PHYSICS), then File > Import >
    Import SWTOR (.clo), pick the file and set the options.

Usage from another add-on or script
-----------------------------------
    The file browser is skipped when ``armature_name`` and ``filepath`` (or
    ``files``) are both set, whichever execution context is used::

        import bpy

        result = bpy.ops.import_cloth.clo(
            filepath="/path/to/chest_cape.clo",
            mode='RIG',
            armature_name="my_armature",   # Object name, not the armature data name.
            skip_unweighted_bones=True,
        )
        if result != {'FINISHED'}:
            ...  # Error text was sent through the operator's report().

    Notes:
        - ``bpy.ops`` polling applies: call from a context with a 3D Viewport and
          Object mode available (such as an add-on living in the 3D View's Sidebar),
          or wrap the call in ``context.temp_override()`` (e.g. from timers and handlers).
        - Failures are reported with ``report({'ERROR'}, ...)``; from Python these
          surface as a RuntimeError when the operator is run with 'EXEC_DEFAULT'
          and an ERROR report, so wrap the call in try/except if needed.
        - The operator registers an undo step ({'UNDO'}).


"""

import os
from array import array
from contextlib import contextmanager
from typing import Iterator, List, Optional, Set, Tuple

from bpy.props import BoolProperty, CollectionProperty, EnumProperty, StringProperty
from bpy.types import Context, Event, Object, Operator, OperatorFileListElement
from bpy_extras.io_utils import ImportHelper

from ..types.clo import Cloth
from ..utils.binary import ArrayBuffer, DataView


class ImportCLO(Operator, ImportHelper):
    """
    Import a SWTOR cloth physics file (.clo) as a skeleton or as Cloth Physics.

    Call as ``bpy.ops.import_cloth.clo(...)``. See the module docstring for the full
    property list and examples.

    RIG mode adds the cloth bones to a target armature chosen by, in order: the
    ``armature_name`` property, the active armature, or a new standalone armature.
    PHYSICS mode configures Cloth Physics on the selected mesh.

    Scripted use (no pre-selection, no file browser)::

        bpy.ops.import_cloth.clo(
            filepath="/path/to/file.clo",
            mode='RIG',
            armature_name="my_armature",
        )

    Returns {'FINISHED'} on success, {'CANCELLED'} on failure (reason reported via
    ``report()``). A hidden target armature is revealed during the build and restored
    afterwards.
    """
    bl_idname = "import_cloth.clo"
    bl_label = "Import SWTOR (.clo)"
    bl_description = (
        "Import SWTOR cloth physics bones and build a positioned skeleton from them.\n\n"
        "Select the character's .gr2 skeleton armature first (as the active object) to "
        "merge the cloth bones straight into it, parented to the matching real bones "
        "(Pelvis, Chest2, Head, ...). With nothing suitable selected, a standalone "
        "armature is created instead.\n\n"
        "Scripts can instead pass 'armature_name' to target an armature without selecting it"
    )
    bl_options = {'UNDO'}

    directory: StringProperty(subtype='DIR_PATH')

    filename_ext = ".clo"

    files: CollectionProperty(
        name="File Path",
        description="File path used for importing the CLO file",
        type=OperatorFileListElement,
    )
    filter_glob: StringProperty(
        default="*.clo",
        options={'HIDDEN'},
    )

    mode: EnumProperty(
        name="Mode",
        items=(
            ('RIG', "Build Rig", "Build a positioned skeleton from the cloth bones"),
            ('PHYSICS', "Build Cloth Physics", "Set up Cloth Physics on the matching mesh using the .clo data"),
        ),
        default='RIG',
    )
    physics_auto_fix_floating_islands: BoolProperty(
        name="Attempt Auto-Fix Floating Vertex Islands",
        description=(
            "For mesh pieces (e.g. belt loops, buckles) that carry real cloth data but "
            "aren't geometrically connected to any pin, force-pin them IF they also have "
            "real skeleton-bone weight to fall back on (so they'll still follow the body "
            "via the Armature modifier). Islands with no bone fallback are left alone and "
            "still reported either way -- this can't guess which bone they should follow"
        ),
        default=False,
    )
    skip_unweighted_bones: BoolProperty(
        name="Skip Unweighted Bones",
        description="Leave out bones with no weight on the matching mesh (reparenting their children up a level)",
        default=True,
    )
    use_spline_ik: BoolProperty(
        name="Add Spline IK",
        description="Not yet implemented -- will drive each unbranched chain from a generated curve",
        default=False,
    )
    use_bendy_bones: BoolProperty(
        name="Use Bendy Bone segments",
        description="Not yet implemented -- smooths chain segments, usable with or without Spline IK",
        default=False,
    )
    add_curve_hooks: BoolProperty(
        name="Add Hook Controls",
        description=(
            "Add an Empty at each spline chain's root and tip, hooked to the curve, for "
            "quick posing without entering the curve's own Edit Mode. Only applies with "
            "Add Spline IK enabled"
        ),
        default=False,
    )
    preserve_bone_length: BoolProperty(
        name="Preserve Bone Length",
        description=(
            "Keep each bone at its original (rest) length instead of stretching to fit "
            "the curve -- prevents over-stretching the chain when control points are "
            "moved far apart. Sets Spline IK's Y Scale Mode to 'Bone Original'. A long-"
            "standing Blender bug (developer.blender.org T77330) means the FIRST bone in "
            "each chain can still show some residual stretch regardless of this setting"
        ),
        default=True,
    )
    add_master_bone: BoolProperty(
        name="Add Master Bone",
        description=(
            "Add one extra convenience bone above the real-bone placeholders, for a single "
            "grab point or constraint. The placeholders stay independently constrainable "
            "either way -- this only adds a parent above them, it doesn't replace them"
        ),
        default=False,
    )
    armature_name: StringProperty(
        name="Target Armature",
        description=(
            "Name of the armature object to merge the cloth bones into. When set, the "
            "active object is ignored and no pre-selection is needed. If the armature "
            "is hidden, it is temporarily revealed and its visibility restored "
            "afterwards. Intended for calls from other add-ons; leave empty to use "
            "the active object"
        ),
        default="",
        options={'SKIP_SAVE'},  # Never remember a stale name between invocations.
    )

    def draw(self, context):
        # type: (Context) -> None
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        box = layout.box()
        box.label(text="Import Mode")
        box.prop(self, "mode", expand=True)

        if self.mode == 'RIG':
            box = layout.box()
            box.label(text="Rig Options")
            box.prop(self, "skip_unweighted_bones")
            box.prop(self, "use_spline_ik")
            if self.use_spline_ik:
                box.prop(self, "add_curve_hooks")
                box.prop(self, "preserve_bone_length")
            box.prop(self, "use_bendy_bones")
            target = context.active_object
            if not self.armature_name and not (target and target.type == 'ARMATURE'):
                box.prop(self, "add_master_bone")
        else:
            box = layout.box()
            box.label(text="Cloth Physics Options")
            box.label(text="Applies to the selected mesh (select it first).", icon='INFO')
            box.label(text="Builds Pin/Stiffness vertex groups + a Cloth modifier.")
            box.prop(self, "physics_auto_fix_floating_islands")

    def invoke(self, context, event):
        # type: (Context, Event) -> Set[str]
        """
        Bypass the file browser for scripted calls.

        When a target armature is supplied by name AND a file path (or file list) is
        already set, there is nothing left to ask the user, so run immediately
        instead of opening ImportHelper's file selector. Otherwise (normal menu use,
        or a name without a path) defer to the standard file browser.
        """
        if self.armature_name and (self.filepath or self.files):
            return self.execute(context)
        return super().invoke(context, event)

    def execute(self, context):
        # type: (Context) -> Set[str]
        paths = [os.path.join(self.directory, file.name) for file in self.files]

        if not paths:
            paths.append(self.filepath)

        for path in paths:
            if not load(self, context, path):
                return {'CANCELLED'}

        return {'FINISHED'}


# ---------------------------------------------------------------------------------
# Binary reading (shared by every build mode)

def _read_fixed_string(dv, pos, size=0x20):
    # type: (DataView, int, int) -> str
    """Reads a null-terminated string out of a fixed-size (size-byte) slot."""
    chars = []
    for i in range(size):
        b = dv.getUint8(pos + i)
        if b == 0:
            break
        chars.append(chr(b))
    return ''.join(chars)


def _fix_crlf_if_needed(raw):
    # type: (bytes) -> bytes
    """
    Some .clo files got every 0x0A byte widened to 0x0D 0x0A somewhere in their asset
    pipeline, making the file 26 bytes longer than its own header says it should be.
    When that mismatch is detected, undo it before parsing (ported from
    clo_binary-read.js, which hits this on real files such as
    chest_capetightskin02_bfa_light_jw_mtx09_shoulder.clo).
    """
    if len(raw) < 16:
        return raw
    payload_offset = int.from_bytes(raw[0x8:0xC], 'little')
    payload_length = int.from_bytes(raw[0xC:0x10], 'little')
    if len(raw) - payload_offset - payload_length == 26:
        return raw.replace(b'\x0d\x0a', b'\x0a')
    return raw


def read(operator, filepath):
    # type: (Operator, str) -> Optional[Cloth]
    with open(filepath, 'rb') as file:
        raw = file.read()

    raw = _fix_crlf_if_needed(raw)

    buffer = ArrayBuffer(len(raw))
    buffer[:] = array('B', raw)
    dv = DataView(buffer)

    # NOTE: Header.
    magic = dv.getUint32(0x0, True)
    if magic != 0x42434C4F:  # b'OLCB'
        operator.report({'ERROR'}, f"\'{filepath}\' is not a valid SWTOR .clo file (bad magic).")
        return None

    cloth = Cloth()
    cloth.version = dv.getUint32(0x4, True)
    if not (1 <= cloth.version <= 3):
        operator.report({'ERROR'}, f"\'{filepath}\': unsupported .clo version {cloth.version} (expected 1-3).")
        return None

    payload_offset = dv.getUint32(0x8, True)
    if payload_offset != 0x10:
        operator.report({'ERROR'}, f"\'{filepath}\': unexpected payload offset {payload_offset} (expected 16).")
        return None
    payload_length = dv.getUint32(0xC, True)
    if dv.byteLength - payload_offset != payload_length:
        operator.report({'WARNING'}, f"\'{filepath}\': payload length mismatch; file may be truncated.")

    cloth.gravity = [dv.getFloat32(0x10, True), dv.getFloat32(0x14, True), dv.getFloat32(0x18, True)]
    cloth.boundingSphereRadiusSq = dv.getFloat32(0x24, True)

    # NOTE: Offset table. Versions 1-2 store 32-bit count/offset pairs interleaved;
    # version 3 (64-bit client, 7.2.1+) stores all counts (32-bit) first, then all
    # offsets as 64-bit (we only need the lower 32 bits -- files are always far
    # smaller than 4 GiB).
    offsets = {}
    if cloth.version < 3:
        offsets['stringsCount'] = dv.getUint32(0x38, True)
        offsets['stringsOffset'] = dv.getUint32(0x3C, True)
        offsets['bonesCount'] = dv.getUint32(0x40, True)
        offsets['bonesOffset'] = dv.getUint32(0x44, True)
        offsets['particlesCount'] = dv.getUint32(0x48, True)
        offsets['particlesOffset'] = dv.getUint32(0x4C, True)
        offsets['particleDataCount'] = dv.getUint32(0x54, True)
        offsets['particleDataOffset'] = dv.getUint32(0x58, True)
        offsets['edgesCount'] = dv.getUint32(0x5C, True)
        offsets['edgesOffset'] = dv.getUint32(0x60, True)
        offsets['collidersCount'] = dv.getUint32(0x64, True)
        offsets['collidersOffset'] = dv.getUint32(0x68, True)
        offsets['trianglesCount'] = dv.getUint32(0x74, True)
        offsets['trianglesOffset'] = dv.getUint32(0x78, True)
    else:
        offsets['stringsCount'] = dv.getUint32(0x38, True)
        offsets['bonesCount'] = dv.getUint32(0x3C, True)
        offsets['particlesCount'] = dv.getUint32(0x40, True)
        offsets['particleDataCount'] = dv.getUint32(0x44, True)
        offsets['edgesCount'] = dv.getUint32(0x48, True)
        offsets['collidersCount'] = dv.getUint32(0x4C, True)
        offsets['trianglesCount'] = dv.getUint32(0x54, True)
        offsets['stringsOffset'] = dv.getUint32(0x58, True)
        offsets['bonesOffset'] = dv.getUint32(0x60, True)
        offsets['particlesOffset'] = dv.getUint32(0x68, True)
        offsets['particleDataOffset'] = dv.getUint32(0x78, True)
        offsets['edgesOffset'] = dv.getUint32(0x80, True)
        offsets['collidersOffset'] = dv.getUint32(0x88, True)
        offsets['trianglesOffset'] = dv.getUint32(0x98, True)

    # NOTE: String table (fixed 0x20-byte slots). Bone/particle/collider names and
    # parent-bone references are all indices into this table.
    strings = []
    for i in range(offsets['stringsCount']):
        pos = 0x10 + offsets['stringsOffset'] + i * 0x20
        strings.append(_read_fixed_string(dv, pos))

    def string_at(index):
        return strings[index] if 0 <= index < len(strings) else ''

    # NOTE: Bones. 0x60-byte stride:
    #   float32[4] boneToParentRot (x, y, z, w)
    #   float32[4] boneToParentTrans (x, y, z, w-is-a-copy-of-x)
    #   float32[4] rootToBoneRot (x, y, z, w)      <- inverse-bind rotation
    #   float32[4] rootToBoneTrans (x, y, z, w-is-a-copy-of-x)  <- inverse-bind translation
    #   float32[4] restEdgeDirection (x, y, z, padding)
    #   int32 startParticle, int32 endParticle
    #   int32 nameIndex, int32 parentNameIndex
    for i in range(offsets['bonesCount']):
        pos = 0x10 + offsets['bonesOffset'] + i * 0x60
        bone = Cloth.Bone()
        bone.index = i
        bone.boneToParentRot = [dv.getFloat32(pos + o, True) for o in (0x0, 0x4, 0x8, 0xC)]
        bone.boneToParentTrans = [dv.getFloat32(pos + o, True) for o in (0x10, 0x14, 0x18)]
        bone.rootToBoneRot = [dv.getFloat32(pos + o, True) for o in (0x20, 0x24, 0x28, 0x2C)]
        bone.rootToBoneTrans = [dv.getFloat32(pos + o, True) for o in (0x30, 0x34, 0x38)]
        bone.restEdgeDirection = [dv.getFloat32(pos + o, True) for o in (0x40, 0x44, 0x48)]
        bone.startParticle = dv.getInt32(pos + 0x50, True)
        bone.endParticle = dv.getInt32(pos + 0x54, True)
        bone.name = string_at(dv.getInt32(pos + 0x58, True))
        bone.parent = string_at(dv.getInt32(pos + 0x5C, True))
        cloth.bones.append(bone)

    # NOTE: Particles. 0x24-byte stride.
    for i in range(offsets['particlesCount']):
        pos = 0x10 + offsets['particlesOffset'] + i * 0x24
        particle = Cloth.Particle()
        particle.index = i
        particle.damping = dv.getFloat32(pos, True)
        particle.movementForceFactor = dv.getFloat32(pos + 0x4, True)
        particle.drivenBone = string_at(dv.getInt32(pos + 0x8, True))
        particle.invertedSimWeight = dv.getFloat32(pos + 0xC, True)
        particle.isZeroWeight = dv.getUint8(pos + 0x10)
        particle.isOneWeight = dv.getUint8(pos + 0x11)
        particle.colliderBitflag = dv.getUint32(pos + 0x14, True)
        particle.radius = None
        cloth.particles.append(particle)

    # NOTE: Per-particle radius data is a sparse side-table; stride differs on v3.
    particle_data_stride = 0x28 if cloth.version == 3 else 0x1C
    for i in range(offsets['particleDataCount']):
        pos = 0x10 + offsets['particleDataOffset'] + i * particle_data_stride
        particle_index = dv.getUint32(pos, True)
        radius = dv.getFloat32(pos + 0x8, True)
        if 0 <= particle_index < len(cloth.particles):
            cloth.particles[particle_index].radius = radius

    # NOTE: Edges. Version 1 has no minLength field (0x18-byte stride); 2+ does (0x1C).
    edge_stride = 0x1C if cloth.version > 1 else 0x18
    for i in range(offsets['edgesCount']):
        pos = 0x10 + offsets['edgesOffset'] + i * edge_stride
        edge = Cloth.Edge()
        edge.node1 = dv.getUint32(pos, True)
        edge.node2 = dv.getUint32(pos + 0x4, True)
        edge.restLength = dv.getFloat32(pos + 0x8, True)
        edge.maxLength = dv.getFloat32(pos + 0xC, True)
        edge.springStrength = dv.getFloat32(pos + 0x10, True)
        edge.movement = dv.getUint32(pos + 0x14, True)
        edge.minLength = dv.getFloat32(pos + 0x18, True) if cloth.version > 1 else edge.restLength
        cloth.edges.append(edge)

    # NOTE: Colliders. 0x40-byte stride.
    for i in range(offsets['collidersCount']):
        pos = 0x10 + offsets['collidersOffset'] + i * 0x40
        collider = Cloth.Collider()
        collider.rotation = [dv.getFloat32(pos + o, True) for o in (0x0, 0x4, 0x8, 0xC)]
        collider.position = [dv.getFloat32(pos + o, True) for o in (0x10, 0x14, 0x18)]
        collider.parent = string_at(dv.getUint32(pos + 0x20, True))
        collider.cls = dv.getUint32(pos + 0x28, True)
        collider.radius = dv.getFloat32(pos + 0x2C, True)
        collider.height = dv.getFloat32(pos + 0x30, True)
        collider.friction = dv.getFloat32(pos + 0x34, True)
        cloth.colliders.append(collider)

    # NOTE: Triangles. 0x10-byte stride. Not used for collision against the real mesh --
    # these approximate it for performance, and only matter for future collider display.
    for i in range(offsets['trianglesCount']):
        pos = 0x10 + offsets['trianglesOffset'] + i * 0x10
        triangle = Cloth.Triangle()
        triangle.index1 = dv.getUint32(pos, True)
        triangle.index2 = dv.getUint32(pos + 0x4, True)
        triangle.index3 = dv.getUint32(pos + 0x8, True)
        triangle.colliderBitflag = dv.getUint32(pos + 0xC, True)
        cloth.triangles.append(triangle)

    return cloth


# ---------------------------------------------------------------------------------
# Sibling-mesh lookup (shared -- Physics mode will need this just as much as Rig mode
# does, to find the vertex groups/weights to work from)

def _find_sibling_mesh(filepath):
    # type: (str) -> Optional['bpy.types.Object']
    """
    Same convention as Jedipedia's clo_binary.js (gr2Name = filename.replace(/.clo$/, '.gr2'))
    and this add-on's own .gr2 importer's "use file name as object name" option: the mesh
    object sharing this .clo's base filename, if one exists in the scene.
    """
    import bpy
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    mesh_ob = bpy.data.objects.get(base_name)
    return mesh_ob if (mesh_ob is not None and mesh_ob.type == 'MESH') else None


def _find_layer_collection_paths(layer_collection, collection_set, path=()):
    # type: (object, set, tuple) -> List[tuple]
    """
    Depth-first search of the view layer's LayerCollection tree.
    
    (Support function for processing target armatures fed via 'armature_name' param
     that happen to be excluded from the Viewlayer: their Collection is unticked)

    :param layer_collection: LayerCollection to start from (usually the root).
    :param collection_set: Set of ``bpy.types.Collection`` that directly contain the object.
    :param path: LayerCollections already traversed (outermost first); internal.
    :return: One tuple per matching LayerCollection, listing every LayerCollection
        from the first child of the root down to the match (outermost first). Every
        entry in such a path can hide the object, so every entry must be revealed.
    """
    paths = []
    for child in layer_collection.children:
        child_path = path + (child,)
        if child.collection in collection_set:
            paths.append(child_path)
        paths.extend(_find_layer_collection_paths(child, collection_set, child_path))
    return paths


@contextmanager
def temporarily_visible(context, object_name):
    # type: (Context, str) -> Iterator[None]
    """
    Context manager: guarantee an object is visible and selectable inside the block,
    then put every visibility flag back exactly as it was (even if the block raises).
    
    (Support function for processing target armatures fed via 'armature_name' param
     that happen to be hidden: their eye button is closed)


    Handles every mechanism that makes ``select_set()`` / ``mode_set()`` fail:
        - Object ``hide_viewport`` (monitor icon) and ``hide_select`` (arrow icon)
        - Object ``hide_get()`` (eye icon / H key; per view layer)
        - On every collection containing the object AND each of its ancestors in the
          view layer: ``LayerCollection.exclude`` (checkbox), ``LayerCollection.hide_viewport``
          (eye), ``Collection.hide_viewport`` (monitor) and ``Collection.hide_select``

    It silently does nothing when the name is empty, unknown, or the object is not
    linked into the current scene; ``resolve_target_armature()`` reports those cases.
    Local View is not handled.

    :param context: The current Blender context.
    :param object_name: Name of the object (``bpy.data.objects`` key); may be empty.
    """
    import bpy

    ob = bpy.data.objects.get(object_name) if object_name else None
    if ob is None or ob.name not in context.scene.objects:
        yield
        return

    view_layer = context.view_layer

    # NOTE: Snapshot, then reveal. Collections go first (outermost to innermost),
    # because an object inside an excluded collection is not in the view layer at all
    # and cannot be queried or hidden/shown until the collection is re-included.
    # Must be captured BEFORE un-excluding collections, or it is always True.
    was_in_view_layer = ob.name in view_layer.objects

    layer_collections = []  # type: List[object]
    for path in _find_layer_collection_paths(view_layer.layer_collection, set(ob.users_collection)):
        for layer_collection in path:
            if layer_collection not in layer_collections:
                layer_collections.append(layer_collection)

    collection_states = []
    for lc in layer_collections:
        collection_states.append((lc, lc.exclude, lc.hide_viewport,
                                  lc.collection.hide_viewport, lc.collection.hide_select))
        lc.exclude = False
        lc.hide_viewport = False
        lc.collection.hide_viewport = False
        lc.collection.hide_select = False

    # hide_get() is only meaningful if the object was in the view layer originally; a
    # freshly re-included object reports default visibility, not a remembered state.
    orig_hide_get = ob.hide_get(view_layer=view_layer) if was_in_view_layer else None
    orig_hide_viewport = ob.hide_viewport
    orig_hide_select = ob.hide_select

    ob.hide_viewport = False
    ob.hide_select = False
    if ob.name in view_layer.objects:
        ob.hide_set(False, view_layer=view_layer)

    try:
        yield
    finally:
        # NOTE: Restore in reverse order. Leave Edit/Pose mode first: hiding an
        # object that is still in an interactive mode leaves Blender in a bad state.
        if context.mode != 'OBJECT' and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')

        # Object flags first, while the object is still guaranteed to be in the view layer.
        if orig_hide_get is not None and ob.name in view_layer.objects:
            ob.hide_set(orig_hide_get, view_layer=view_layer)
        ob.hide_select = orig_hide_select
        ob.hide_viewport = orig_hide_viewport

        for lc, exclude, hide_viewport, coll_hide_viewport, coll_hide_select in reversed(collection_states):
            lc.collection.hide_select = coll_hide_select
            lc.collection.hide_viewport = coll_hide_viewport
            lc.hide_viewport = hide_viewport
            lc.exclude = exclude  # Last: excluding removes the object from the view layer.


def resolve_target_armature(operator, context):
    # type: (Operator, Context) -> Tuple[Optional[Object], bool]
    """
    Determine which armature the cloth bones should be merged into.

    Resolution order:
        1. ``operator.armature_name``, if set. It must name an armature object that
           is in the current view layer. Hidden objects are fine: ``load()`` reveals
           them for the duration of the build via ``temporarily_visible()``. If the
           name is invalid an error is reported and the lookup fails (there is
           deliberately NO fallback to the active object or to a standalone armature,
           since the caller asked for a specific target).
        2. The active object, if it is an armature.
        3. None, meaning "create a standalone armature".

    :param operator: The running operator (``armature_name`` is read via getattr, so
        operators without the property are handled).
    :param context: The current Blender context.
    :return: ``(armature_or_None, ok)``. ``ok`` is False when an explicitly
        requested armature could not be used; the caller should abort.
    """
    import bpy

    name = getattr(operator, "armature_name", "")
    if name:
        armature_ob = bpy.data.objects.get(name)
        if armature_ob is None or armature_ob.type != 'ARMATURE':
            operator.report({'ERROR'}, f"Target armature \'{name}\' not found, or it is not an armature object.")
            return None, False
        # Still absent from the view layer after temporarily_visible() ran means it is
        # not linked into the current scene, which cannot be worked around.
        if armature_ob.name not in context.view_layer.objects:
            operator.report({'ERROR'}, f"Target armature \'{name}\' is not in the current scene.")
            return None, False
        return armature_ob, True

    active_ob = context.active_object
    if active_ob is not None and active_ob.type == 'ARMATURE':
        return active_ob, True
    return None, True


def load(operator, context, filepath=""):
    # type: (Operator, Context, str) -> bool
    from bpy_extras.wm_utils.progress_report import ProgressReport

    with ProgressReport(context.window_manager) as progress:
        progress.enter_substeps(2, f"Importing \'{filepath}\' ...")

        progress.step("Parsing file ...", 1)
        cloth = read(operator, filepath)

        if cloth is None:
            return False

        progress.step("Building ...", 2)

        # NOTE: Reveal a hidden target armature for the build only; flags are restored
        # afterwards even if the build raises. No-op when armature_name is empty.
        with temporarily_visible(context, getattr(operator, "armature_name", "")):
            if getattr(operator, "mode", 'RIG') == 'PHYSICS':
                from . import import_clo_physics
                built = import_clo_physics.build(operator, context, cloth, filepath)
            else:
                from . import import_clo_rig
                built = import_clo_rig.build(operator, context, cloth, filepath)

        if built:
            progress.leave_substeps(f"Done, finished importing: \'{filepath}\'")
            return True

        return False