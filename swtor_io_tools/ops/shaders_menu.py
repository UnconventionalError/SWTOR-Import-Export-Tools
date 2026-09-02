# BLENDER 4.x-specific code for adding SWTOR Shaders
# to the Shader Editor's Add menu.
#
# This is the current system: every shader here is a native SWTOR shader
# appended from the bundled template .blend (types/shader_templates.py,
# bundled_data/Atroxa_Shaders.blend) -- no addon-side Python needed to
# open, render, or edit once saved. The deprecated ShaderNodeHeroEngine
# custom-node equivalent lives in shaders_menu_deprecated.py.
#
# Previously (through swtor_io_tools 5.1.x) every shader's node graph was
# constructed directly via the Python bpy API -- SWTOR_SHADER_GROUPS'
# per-type 'maps' layout table (external Image Texture node positions,
# reroutes, the DirectionMapUV detour construction) and
# build_swtor_shader_material()/build_reroute_chain() drove that. All of
# that layout now lives in the template .blend itself, editable directly
# in Blender's node editor, and has been removed from here along with
# types/node_group.py and types/node_tree.py (the Python builders that
# used to construct each shader's native node group from scratch) --
# nothing in the addon calls any of it anymore. See git history if any
# of that Python-side construction logic is ever needed for reference.

import bpy

from ..types.shader_templates import SHADER_TEMPLATE_MATERIAL_NAMES, get_or_build_shader_material


# Operator that replaces the current material in context with a native
# SWTOR shader appended from the bundled template (types/shader_templates.py) --
# no custom Python node class, fully portable without the addon once
# saved. Deliberately a full replacement, not an add/merge: the old
# "stack multiple shaders side by side for comparison" behavior (this
# operator used to build directly into whatever nodes context.material
# already had) is retired -- per Crunch, there's little real need to
# preserve a material's prior node graph when explicitly choosing a
# shader type from this menu, and a single consistent replace-and-rename
# mechanism (shared with import_cha.py and migrate_shaders.py) beats
# maintaining a second, merge-capable one for this one caller.
class NODE_OT_add_swtor_shader_group(bpy.types.Operator):
    bl_idname = 'node.add_swtor_shader_group'
    bl_label = 'Add SWTOR Shader (Node Group)'
    bl_description = "Replaces the current Material's node graph with this native SWTOR Shader"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.material

    swtor_shader_type: bpy.props.StringProperty()

    def execute(self, context):
        if self.swtor_shader_type not in SHADER_TEMPLATE_MATERIAL_NAMES:
            self.report({'ERROR'}, f"'{self.swtor_shader_type}' has no bundled shader template")
            return {'CANCELLED'}

        # force=True: this is a deliberate "make this material this
        # shader type now" action, not an idempotent build-once import
        # call -- must replace even if context.material is already
        # native (e.g. switching it from one shader type to another).
        # If context.material currently has zero real users (a purely
        # browsed/pinned material with no object assigned), there's
        # nothing for user_remap() to redirect and the Shader Editor may
        # not automatically follow the replacement -- a known, accepted
        # rough edge for that uncommon case.
        get_or_build_shader_material(self.swtor_shader_type, context.material.name, force=True)

        return {'FINISHED'}


# Actual SWTOR Shaders menu (native node-group versions).
class NODE_MT_swtor_shaders_menu(bpy.types.Menu):
    bl_idname = 'NODE_MT_swtor_shaders_menu'
    bl_label = 'SWTOR Shaders'

    def draw(self, context):
        layout = self.layout

        swtor_shader_group_uber = layout.operator(NODE_OT_add_swtor_shader_group.bl_idname, text="Uber Shader")
        swtor_shader_group_uber.swtor_shader_type = "UBER"
        swtor_shader_group_eye = layout.operator(NODE_OT_add_swtor_shader_group.bl_idname, text="Eye Shader")
        swtor_shader_group_eye.swtor_shader_type = "EYE"
        swtor_shader_group_creature = layout.operator(NODE_OT_add_swtor_shader_group.bl_idname, text="Creature Shader")
        swtor_shader_group_creature.swtor_shader_type = "CREATURE"
        swtor_shader_group_hairc = layout.operator(NODE_OT_add_swtor_shader_group.bl_idname, text="HairC Shader")
        swtor_shader_group_hairc.swtor_shader_type = "HAIRC"
        swtor_shader_group_hairc_modern = layout.operator(NODE_OT_add_swtor_shader_group.bl_idname, text="HairC Modern Shader")
        swtor_shader_group_hairc_modern.swtor_shader_type = "HAIRC_MODERN"
        swtor_shader_group_garment = layout.operator(NODE_OT_add_swtor_shader_group.bl_idname, text="Garment Shader")
        swtor_shader_group_garment.swtor_shader_type = "GARMENT"
        swtor_shader_group_skinb = layout.operator(NODE_OT_add_swtor_shader_group.bl_idname, text="SkinB Shader")
        swtor_shader_group_skinb.swtor_shader_type = "SKINB"


# Function that creates a layout element holding a separator bar plus
# the SWTOR shaders menu, to be appended to the NODE_MT_add menu
# by __init__.py's registrations.
def swtor_shaders_submenu_element(self, context):
    self.layout.separator()
    self.layout.menu(NODE_MT_swtor_shaders_menu.bl_idname)