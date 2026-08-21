# <pep8 compliant>

"""
This script imports Star Wars: The Old Republic animations into Blender.

Usage:
Run this script from "File->Import" menu and then load the desired JBA animation file.

https://github.com/SWTOR-Slicers/WikiPedia/wiki/JBA-File-Structure
"""

import math
import os
from typing import Optional, Set

from bpy import app
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, StringProperty
from bpy.types import Context, Object, Operator, OperatorFileListElement
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Quaternion, Vector

from ..types.jba import JointBoneAnimation
from ..utils.binary import ArrayBuffer, DataView
from ..utils.string import path_split, readCString


# Bones authored with a genuine 180°-around-their-own-Y rest orientation
# relative to their parent - the classic 3ds Max Biped root-bone convention
# (Bip01 points "backward" relative to the node above it by design). This is
# a real, correct fact about the bind pose, not a bug in it - but it means
# a bone's own jba rotation channel (which encodes motion relative to that
# flipped rest, same as every other bone) needs the same 180° accounted for
# when composing pose_bone.matrix_basis below, or an unanimated frame comes
# out as a spurious 180° flip instead of the identity it should be.
#
# This is intentionally a short, explicit name list rather than automatic
# per-bone detection (e.g. flagging any bone whose rest is ~180° from its
# parent). Bip01 is the only bone this has ever been observed on; a wrong
# auto-detected match on some other bone with a genuinely-intended large
# rest rotation would silently corrupt that bone's motion with no obvious
# symptom, which is worse than this list occasionally needing a bone added
# to it by hand.
KNOWN_180_FLIP_BONES = {"Bip01"}
BONE_REST_FLIP_CORRECTION = Matrix.Rotation(math.pi, 4, 'Y')


class ImportJBA(Operator):
    """Import from SWTOR JBA file format (.jba)"""
    bl_idname = "import_animation.jba"  # DO NOT CHANGE
    bl_description = "Import and apply an animation to the active SWTOR skeleton in the scene.\n\n• Only compatible with .jba files extracted from SWTOR 32-bit\n   (before Game Update 7.2.1).\n\n• Might apply exaggerated bone translations requiring cleanup\n   (check ZG SWTOR Tools Add-on's Clear Bones Translations tool)."
    bl_label = "Import SWTOR (.jba)"
    bl_options = {'UNDO'}


    # File Browser properties
    
    # This class used to be based on ImportHelper
    # but we now use invoke() to be able to use
    # the Add-on's Preferences settings when
    # called from the Import menu and launching
    # a File Browser.
    
    # filepath is explicitly declared because
    # omitting ImportHelper omits it, too.
    # invoke() handles what to do if it is
    # filled as a param in an external call.
    
    filepath: StringProperty(subtype='FILE_PATH')
    
    if app.version < (2, 82, 0):
        directory = StringProperty(subtype='DIR_PATH')
    else:
        directory: StringProperty(subtype='DIR_PATH')

    filename_ext = ".jba"

    files: CollectionProperty(
        name="File Path",
        description="File path used for importing the JBA file",
        type=OperatorFileListElement,
    )

    filter_glob: StringProperty(
        default="*.jba",
        options={'HIDDEN'},
    )
    
    # Animation importing-related properties
    
    ignore_facial_bones: BoolProperty(
        name="Ignore Facial Transl.",
        description="Ignores the data in the facial bones' translation keyframes\nand only uses their rotation keyframes",
        default=True,
    )

    delete_180: BoolProperty(
        name="Delete 180º rotation",
        description="Keeps the animation data from turning the skeleton 180º by deleting\nthe keyframes assigned to the Bip01 bone and setting its rotation to zero.\n\nSWTOR animations turn characters so that they face away from the camera,\nas normally shown in the gameplay. That is not just a nuisance but a problem\nwhen adding cloth or physics simulations to capes or lekku: the instantaneous\nturn plays havok with them",
        default=False,
    )

    scale_animation: BoolProperty(
        name="Scale Animation",
        description="Scales the bones' translation data by a factor.\nIt must match the scale of the skeleton\nand objects to be animated for good results.\n\nWhenever possible, this setting will try to match\nthe Objects' Scale Factor automatically.\nIt will still allow for setting different values manually",
        default=False,
    )

    scale_factor: FloatProperty(
        name="Scale Factor",
        description="Scales the bones' translation data by a factor.\nIt must match the scale of the skeleton\nand objects to be animated.\n\nWhenever possible, this setting will try to match\nthe Objects Import Settings automatically\n(it still allows for setting different values)",
        default=1.0,
        soft_min=0.1,
        soft_max=2.0,
        precision=2,
    )
    

    def invoke(self, context, event):
        # To be able to set the class' properties to the values in the
        # Add-on's preferences and show them in the File Browser's options,
        # we use an Invoke function instead of directly using ImportHelper in
        # the class definition, to be able to put there the required code.
        
        prefs = context.preferences.addons["swtor_io_tools"].preferences
        
        self.ignore_facial_bones = prefs.jba_ignore_facial_bones
        self.delete_180          = prefs.jba_delete_180
        self.scale_animation     = prefs.gr2_scale_object
        self.scale_factor        = prefs.gr2_scale_factor


        # Handling of filepath in case of being
        # filled as a param in an external call.
        if not self.filepath:
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        else:
            return self.execute(context)        


    def execute(self, context):
        # type: (Context) -> Set[str]

        paths = [os.path.join(self.directory, file.name) for file in self.files if file.name.lower().endswith(self.filename_ext)]

        if not paths:
            paths.append(self.filepath)
            
        # Clear filebrowser-related properties now
        # that they have been read and have no more
        # use so that they don't persist if the class
        # breaks before finishing its execution
        # (it makes debugging difficult).
        self.files.clear()
        self.filepath = ""

        for path in paths:
            if not load(self, context, path):
                return {'CANCELLED'}

        return {'FINISHED'}


def _read_rotation_compressed(dv, pos, base, stride):
    # type: (DataView, int, Vector, Vector) -> Quaternion
    rot_x_raw = dv.getUint16(pos, True)
    rot_x = base.x + (rot_x_raw & 32767) * stride.x
    rot_y = base.y + dv.getUint16(pos + 2, True) * stride.y
    rot_z = base.z + dv.getUint16(pos + 4, True) * stride.z
    pos += 6
    rot_dot = rot_x * rot_x + rot_y * rot_y + rot_z * rot_z
    rot_w = 0.0 if rot_dot > 1.0 else math.sqrt(1.0 - rot_dot)

    if rot_x_raw & 32768:
        rot_w *= -1.0

    return Quaternion((rot_w, rot_x, rot_y, rot_z)).normalized()


def _read_translation_compressed(dv, pos, base, stride):
    # type: (DataView, int, Vector, Vector) -> Vector
    val = dv.getUint32(pos, True)
    pos += 4

    pos_x = base.x + (val >> 21) * stride.x
    pos_y = base.y + ((val >> 10) & 2047) * stride.y
    pos_z = base.z + (val & 1023) * stride.z

    return Vector((pos_x, pos_y, pos_z))


def read(operator, filepath):
    # type: (Operator, str) -> Optional[JointBoneAnimation]
    with open(filepath, 'rb') as file:
        buffer = ArrayBuffer()
        buffer.fromfile(file, os.path.getsize(filepath))

    dv = DataView(buffer)
    pos = 0

    # Cancel import if this is not a BioWare Austin / SWTOR JBA file
    if dv.getUint32(pos, True) != 0:
        operator.report({'ERROR'}, f"\'{filepath}\' is not a valid SWTOR jba file.")
        return None
    pos += 4

    # NOTE: File header
    length = dv.getFloat32(pos, True)
    pos += 4
    fps = dv.getFloat32(pos, True)
    pos += 4
    num_blocks = dv.getUint32(pos, True)
    pos += 4
    pos += 8  # unknown
    num_bones = dv.getUint32(pos, True)
    pos += 4
    pos += 12  # unknown

    # NOTE: Block headers
    num_frames = round(length * fps) + 1
    blocks = [None] * num_blocks
    for i in range(num_blocks):
        start_frame = dv.getUint32(pos, True)
        pos += 4
        block_size = dv.getUint32(pos, True)
        pos += 4
        blocks[i] = JointBoneAnimation.Block(start_frame, block_size)
        if i > 0:
            blocks[i - 1].num_frames = 1 + blocks[i].start_frame - blocks[i - 1].start_frame
        if i + 1 == num_blocks:
            blocks[i].num_frames = num_frames - blocks[i].start_frame
    pos += num_blocks * 4  # unknown

    # NOTE: Bone data
    pos = (pos + 127) & -128
    bones = [None] * num_bones
    for i in range(num_bones):
        translation_stride = Vector([dv.getFloat32(pos + (j * 4), True) for j in range(3)])
        pos += 12
        translation_base = Vector([dv.getFloat32(pos + (j * 4), True) for j in range(3)])
        pos += 12
        rotation_stride = Vector([dv.getFloat32(pos + (j * 4), True) for j in range(3)])
        pos += 12
        rotation_base = Vector([dv.getFloat32(pos + (j * 4), True) for j in range(3)])
        pos += 12
        bone = JointBoneAnimation.Bone(rotation_base, rotation_stride, translation_base, translation_stride)
        bone.rotations = [None] * num_frames
        bone.translations = [None] * num_frames
        bones[i] = bone

    # NOTE: Block data
    pos = (pos + 127) & -128
    for i in range(num_blocks):
        block = blocks[i]
        block_end = pos + block.size

        num_block_bones = dv.getUint32(pos, True)
        pos += 4
        assert(num_block_bones == num_bones)
        pos += 4  # unknown

        # Keyframe layout
        has_translations = [None] * num_block_bones
        for j in range(num_block_bones):
            # num_rotations = dv.getUint32(pos, True)
            pos += 4
            pos += 4  # unknown
            num_translations = dv.getUint32(pos, True)
            pos += 4
            pos += 4  # unknown
            has_translations[j] = num_translations > 0

        # Keyframes
        for j in range(num_bones):
            bone = bones[j]

            # Rotations
            for k in range(block.num_frames):
                bone.rotations[block.start_frame + k] = _read_rotation_compressed(
                    dv, pos, bone.rotation_base, bone.rotation_stride)
                pos += 6

            # Translations
            pos = (pos + 3) & -4
            if has_translations[j]:
                for k in range(block.num_frames):
                    bone.translations[block.start_frame + k] = _read_translation_compressed(
                        dv, pos, bone.translation_base, bone.translation_stride)
                    pos += 4
            else:
                # No translation track for this bone in this block: the
                # stride quantizes a range that has no samples, so the
                # correct value is translation_base on its own (raw = 0),
                # not translation_base plus the stride's maximum quantized
                # span (see build()'s comment on why translation_base is
                # never used as an absolute value on its own either way -
                # this bone will end up with delta == 0, same effect as
                # any other bone whose track happens not to move).
                for k in range(block.num_frames):
                    bone.translations[block.start_frame + k] = bone.translation_base.copy()

        pos = block_end

    # World space
    pos += 4  # unknown
    # fps = dv.getFloat32(pos, True)
    pos += 4
    translation_stride = Vector([dv.getFloat32(pos + (i * 4), True) for i in range(3)])
    pos += 12
    translation_base = Vector([dv.getFloat32(pos + (i * 4), True) for i in range(3)])
    pos += 12
    rotation_stride = Vector([dv.getFloat32(pos + (i * 4), True) for i in range(3)])
    pos += 12
    rotation_base = Vector([dv.getFloat32(pos + (i * 4), True) for i in range(3)])
    pos += 12
    num_rotations = dv.getUint32(pos, True)
    pos += 4
    assert(num_rotations == num_frames)
    pos += 4  # unknown
    # num_translations = dv.getUint32(pos, True)
    pos += 4
    # assert(num_translations == num_faces)
    pos += 4  # unknown

    rotations = [_read_rotation_compressed(dv, pos + (i * 8), rotation_base, rotation_stride)
                 for i in range(num_frames)]
    pos += num_frames * 6

    pos = (pos + 3) & -4

    translations = [_read_translation_compressed(dv, pos + (i * 4), translation_base, translation_stride)
                    for i in range(num_frames)]
    pos += num_frames * 4

    world_space = JointBoneAnimation.WorldSpace(rotations, translations)

    # Bone names
    names_start = pos
    num_names = dv.getUint32(pos, True)
    pos += 4
    pos += 4  # unknown
    # off_indices = dv.getUint32(pos, True)
    pos += 4
    # off_offsets = dv.getUint32(pos, True)
    pos += 4
    off_names = dv.getUint32(pos, True)
    pos += 4
    pos += num_names * 4  # numbers from 0 to num_names - 1
    name_offsets = [dv.getUint32(pos + (i * 4), True) for i in range(num_names)]
    pos += num_names * 4
    bone_names = [readCString(dv, names_start + off_names + name_offsets[i]) for i in range(num_names)]

    for i, name in enumerate(bone_names):
        bones[i].name = name if name != "GOD" else "Bip01"

    return JointBoneAnimation(length, fps, num_frames, bones, world_space)


def build(operator, context, filepath, jba):
    # type: (Operator, Context, str, JointBoneAnimation) -> bool
    import bpy
    # import os

    ob: Object = context.active_object

    if not ob or ob.type != 'ARMATURE':
        operator.report({'INFO'}, f"Requires object of type Armature to be active, not {ob.type}")
        return False

    # Create armature action
    # anim_name, _ = os.path.splitext(os.path.basename(filepath))
    anim_name = path_split(filepath)[:-4]

    if not ob.animation_data:
        ob.animation_data_create()

    action = bpy.data.actions.new(anim_name)
    ob.animation_data.action = action

    # Enter pose mode
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='POSE')

    # Create armature keyframes
    # scale = 1000 * operator.scale_factor  # This was the original code's calculation, but it seems it needs to be
    # scale = 1000 * (1/operator.scale_factor) to work correctly, so…
    
    # Check if the armature object has import scale custom property data.
    # If not, use scale_animation and scale_factor or the add-on's prefs
    # settings for the .gr2 import.
    # "gr2_scale" was the old name for this property before it was renamed to
    # "import_scale" to disambiguate from the newer gr2_* BWAG-format round-trip
    # properties. Objects imported with older addon versions still carry the old
    # key, so we fall back to it rather than silently recalculating a (possibly
    # different) scale for those objects.
    if 'import_scale' in ob:
        scale = 1000 * (1 / ob['import_scale'])
    elif 'gr2_scale' in ob:  # legacy key, pre-rename
        scale = 1000 * (1 / ob['gr2_scale'])
    else:
        if operator.scale_animation:
            scale = 1000 * (1 / operator.scale_factor)
        else:
            prefs = context.preferences.addons["swtor_io_tools"].preferences
            if prefs.gr2_scale_object:
                scale = 1000 * (1 / prefs.gr2_scale_factor)
            else:
                scale = 1000
        
    morpheme_space = Matrix(((scale, 0, 0, 0), (0, 0, -scale, 0), (0, scale, 0, 0), (0, 0, 0, 1)))
    morpheme_space_inv = morpheme_space.inverted()

    for anim_frame in range(jba.num_frames):
        for anim_bone in jba.bones:
            bone_name = getattr(anim_bone, "name", "")

            if bone_name in ob.pose.bones:
                pose_bone = ob.pose.bones[bone_name]
                mat_rest = pose_bone.bone.matrix_local

                if pose_bone.parent:
                    mat_rest = pose_bone.parent.bone.matrix_local.inverted() @ mat_rest

                if bone_name in KNOWN_180_FLIP_BONES:
                    mat_rest = mat_rest @ BONE_REST_FLIP_CORRECTION

                mat_rot = anim_bone.rotations[anim_frame].to_matrix().to_4x4()

                # anim_bone.translations[frame] is an ABSOLUTE bone-to-parent
                # offset as authored on whatever rig this specific clip was
                # captured against - not necessarily this armature's own
                # offset for the same bone (confirmed on a real
                # shared-across-body-types clip: several static bones were
                # off by 15-35% against the target skeleton's actual
                # proportions, visibly pulling joints out of place). Using
                # it directly is only safe when it happens to match.
                #
                # What's reliable is the DELTA from the file's own
                # translation_base, since that's this bone's actual motion
                # within the clip regardless of which rig authored it. So
                # every bone's translation is built the same way: take this
                # armature's own rest-pose offset and add only that delta on
                # top, converted through morpheme_space like everything
                # else. A bone with no real motion (delta == 0, whether
                # because it never had a per-frame track at all or because
                # it has one that happens to be constant) lands exactly on
                # this armature's own rest translation; a bone that really
                # animates (e.g. Root) keeps that motion, applied relative
                # to this armature's own bind pose instead of the file's.
                #
                # This also covers what ignore_facial_bones needs (ignore
                # this bone's translation animation entirely) as the delta
                # being forced to zero, rather than needing a separate
                # translation composition for that case.
                if operator.ignore_facial_bones and bone_name.lower().startswith("fc_"):
                    delta = Vector((0.0, 0.0, 0.0))
                else:
                    delta = anim_bone.translations[anim_frame] - anim_bone.translation_base

                mat_bone = morpheme_space_inv @ Matrix.Translation(delta) @ mat_rot @ morpheme_space
                mat_bone.translation = mat_rest.to_translation() + mat_bone.translation

                frame = jba.fps * jba.length * anim_frame / jba.num_frames + 1
                pose_bone.matrix_basis = mat_rest.inverted() @ mat_bone
                pose_bone.keyframe_insert(data_path="location", frame=frame)
                if not (operator.delete_180 and bone_name in KNOWN_180_FLIP_BONES):
                    pose_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)

    # delete_180 is a distinct, deliberate feature from the rest-pose
    # correction above (which is always applied and just makes an
    # unanimated Bip01 come out as identity instead of a spurious 180°
    # flip, the same as every other bone). This is for the separate case
    # where a clip's Bip01 rotation is genuinely animated with a real ~180°
    # character-facing turn (SWTOR does this so gameplay animations play
    # with the character facing away from the camera), which some users
    # want stripped out entirely - e.g. it can wreck cloth/physics sims
    # otherwise. When enabled, this discards all of that bone's rotation
    # data and forces it to identity for the whole clip.
    if operator.delete_180:
        for bone_name in KNOWN_180_FLIP_BONES:
            if bone_name in ob.pose.bones:
                ob.pose.bones[bone_name].rotation_quaternion = (1, 0, 0, 0)

    return True


def load(operator, context, filepath=""):
    # type: (Operator, Context, str) -> bool
    from bpy_extras.wm_utils.progress_report import ProgressReport

    with ProgressReport(context.window_manager) as progress:
        progress.enter_substeps(3, f"Importing \'{filepath}\' ...")

        progress.step("Parsing file ...", 1)
        animation = read(operator, filepath)

        if animation:
            progress.step("Done, building ...", 2)

            if build(operator, context, filepath, animation):
                progress.leave_substeps(f"Done, finished importing: \'{filepath}\'")
                return True

        return False