# ============================================================================
# DEPRECATED -- Add-menu entry point for ShaderNodeHeroEngine shaders
#
# NODE_MT_add_swtor_shader below adds a ShaderNodeHeroEngine instance (the
# addon's original custom-Python-node shader) to the current material.
# Superseded by NODE_OT_add_swtor_shader_group in shaders_menu.py, which
# builds the equivalent native node-group shader instead -- no addon-side
# Python needed to open, render, or edit once saved.
#
# This shows up in the Shader Editor's Add menu as its own separate
# submenu, "SWTOR Shaders (Deprecated)", so it stays available during the
# transition without cluttering the primary "SWTOR Shaders" menu.
#
# NOTE: despite the "NODE_MT_" prefix (conventionally reserved for Menu
# subclasses in this codebase), NODE_MT_add_swtor_shader is actually an
# Operator -- a naming inconsistency inherited from the original addon
# author, left as-is here rather than renamed, since bl_idname
# ('node.add_swtor_shader') is what other code/keymaps would reference.
#
# DELETE THIS FILE, plus:
#   - types/node_deprecated.py
#   - types/node_tree_deprecated.py
# and the "DEPRECATED SYSTEM" block in __init__.py, once this system's
# support window ends (planned: whenever the next Blender LTS release
# breaks ShaderNodeCustomGroup / this custom node stops working).
# ============================================================================

# BLENDER 4.x-specific code for adding deprecated SWTOR Shaders
# to the Shader Editor's Add menu.

import bpy


# Operator that adds a SWTOR shader to the current material in context.
class NODE_MT_add_swtor_shader(bpy.types.Operator):
    bl_idname = 'node.add_swtor_shader'
    bl_label = 'Add SWTOR Shader'
    bl_description = "Adds this SWTOR Shader Nodegroup to the current Material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.material


    swtor_shader_type : bpy.props.StringProperty()
    
    def execute(self, context):
        mat_nodes = bpy.context.material.node_tree.nodes

        swtor_nodegroup = mat_nodes.new(type="ShaderNodeHeroEngine")

        # Tell the ShaderNodeHeroEngine what SWTOR shader to reproduce
        swtor_nodegroup.derived = self.swtor_shader_type
        
        # Position it to make any leftover Principled Shader
        # easy to select and remove from underneath
        swtor_nodegroup.location = -30.0, 300.0

        # Set reasonable opacity modes
        if self.swtor_shader_type in ["SKINB", "EYE"]:
            swtor_nodegroup.alpha_mode = "OPAQUE"
        else:
            swtor_nodegroup.alpha_mode = "CLIP"

        return {'FINISHED'}


# Deprecated SWTOR Shaders menu -- ShaderNodeHeroEngine versions only.
class NODE_MT_swtor_shaders_menu_deprecated(bpy.types.Menu):
    bl_idname = 'NODE_MT_swtor_shaders_menu_deprecated'
    bl_label = 'SWTOR Shaders (Deprecated)'

    def draw(self, context):
        layout = self.layout

        swtor_shader_names = ["Uber", "Creature", "Garment", "SkinB", "HairC", "Eye"]

        for swtor_shader_name in swtor_shader_names:
            swtor_shader = layout.operator(NODE_MT_add_swtor_shader.bl_idname, text=f"{swtor_shader_name} Shader")
            swtor_shader.swtor_shader_type = swtor_shader_name.upper()


# Function that creates a layout element holding a separator bar plus
# the deprecated SWTOR shaders menu, to be appended to the NODE_MT_add
# menu by __init__.py's registrations.
def swtor_shaders_submenu_element_deprecated(self, context):
    self.layout.separator()
    self.layout.menu(NODE_MT_swtor_shaders_menu_deprecated.bl_idname)
