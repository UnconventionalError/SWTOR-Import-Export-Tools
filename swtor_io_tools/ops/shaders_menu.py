# BLENDER 4.x-specific code for adding SWTOR Shaders
# to the Shader Editor's Add menu.
#
# This is the current system: every shader here is a plain, native
# ShaderNodeGroup instance -- no addon-side Python needed to open, render,
# or edit once saved. The deprecated ShaderNodeHeroEngine custom-node
# equivalent lives in shaders_menu_deprecated.py.

import bpy

from ..types.node_group import direction_map_uv
from ..types.node_tree import creature_group, eye_group, garment_group, hairc_group, hairc_modern_group, skinb_group, uber_group


# Each entry: node_tree builder, ordered list of image maps, and the
# group's own default (Alpha Blend, Alpha Test) values -- matching the
# legacy operator's per-type defaults below.
#
# Each map is a dict:
#   node_name, label, color_socket, alpha_socket -- as before.
#   position: (x, y) for the external Image Texture node, adopted from
#     Koda Shaders' own layout (primary column x=-680; SkinB's Age/
#     Complexion/Facepaint sit in a secondary column at x=-1080).
#   vector_source: None for a normal UV-sampled map, or a dict (see
#     Direction Map below) for one driven by a DirectionMapUV instance.
#   color_reroutes / alpha_reroutes: list of (x, y) reroute positions to
#     route that channel through on its way to the group, adopted
#     verbatim from Koda's own hand-placed reroutes where we have an
#     equivalent map (SkinB's Complexion/Facepaint/Age). Empty list =
#     direct connection, no reroutes -- matches Koda's own choice not to
#     bother routing the maps that sit close to the group (Diffuse/
#     Rotation/Gloss/Palette/PaletteMask).
#
# vector_source, where set (Direction Map), is a dict:
#   rotation_node: name of the already-placed Rotation Map image node
#     this Direction Map's UV is computed from.
#   directionUV_position: (x, y) for the DirectionMapUV group instance.
#   color_reroutes / alpha_reroutes: the detour chain from the Rotation
#     Map's own output to DirectionMapUV's input -- adopted from Koda's
#     HairC data. The segment near the Rotation Map is identical across
#     Creature/HairC/HairC Modern (Rotation always sits at the same row);
#     only the segment approaching DirectionMapUV shifts to match
#     wherever Direction actually sits in each shader.
#
# Image map order in each shader's 'maps' list matches the original
# custom node's fixed UI order for every shader EXCEPT SkinB, which was
# reordered (Diffuse, Complexion, Rotation, Facepaint, Gloss, Age,
# Palette, PaletteMask) to pair each map with its visual counterpart in
# the other column -- see skinb_group()'s interface socket order in
# node_tree.py, which was reordered to match.
#
# TO MANUALLY ADJUST A NODE'S POSITION: each map's 'position' (x, y) is
# the only thing that controls where its external Image Texture node
# lands -- change that tuple directly, nothing else needs to move in
# sync with it. The one exception is the Direction Map detour: since its
# reroute chain is a physical path from the Rotation Map to
# DirectionMapUV, moving Direction's 'directionUV_position' without also
# adjusting 'color_reroutes'/'alpha_reroutes' to match will leave the
# reroute path pointing at the old location. If you only want to nudge
# the whole shader's layout up/down uniformly, adding the same Y offset
# to every map's position (and directionUV_position, and every reroute
# coordinate) keeps everything aligned; shifting one map in isolation is
# safe everywhere else.
#
# NOTE: covers all six original derived types plus HairC Modern (a
# second-palette HairC variant, for newer hair assets). Legacy-migration/
# cleanup is still to come.

# Shared Direction Map detour chain, near-Rotation segment (identical
# across every shader with a Direction Map, since Rotation always sits
# at the same row -- (-680, 0) -- in all of them).
_DIRECTION_COLOR_NEAR = [(-300.0, -40.0), (-300.0, -300.0)]
_DIRECTION_ALPHA_NEAR = [(-320.0, -60.0), (-320.0, -280.0)]


def _direction_vector_source(directionUV_y):
    # type: (float) -> dict
    """
    Builds the Direction Map vector_source spec for a shader whose
    DirectionMapUV instance (and Direction Map row) sits at Y=
    directionUV_y. The near-Rotation segment is shared (see above); the
    far segment shifts to land on directionUV_y, preserving the same
    relative offsets Koda used for HairC (-80 for Color, -100 for Alpha).
    """
    return {
        'rotation_node': 'rotationMap1',
        'directionUV_position': (-1140.0, directionUV_y),
        'color_reroutes': _DIRECTION_COLOR_NEAR + [(-1160.0, -300.0), (-1160.0, directionUV_y - 80.0)],
        'alpha_reroutes': _DIRECTION_ALPHA_NEAR + [(-1180.0, -280.0), (-1180.0, directionUV_y - 100.0)],
    }


SWTOR_SHADER_GROUPS = {
    "UBER": {
        'builder': uber_group,
        'maps': [
            {'node_name': 'diffuseMap', 'label': '_d DiffuseMap', 'color_socket': 'DiffuseMap Color', 'alpha_socket': 'DiffuseMap Alpha', 'position': (-680.0, 300.0)},
            {'node_name': 'rotationMap1', 'label': '_n RotationMap1', 'color_socket': 'RotationMap1 Color', 'alpha_socket': 'RotationMap1 Alpha', 'position': (-680.0, 0.0)},
            {'node_name': 'glossMap', 'label': '_s GlossMap', 'color_socket': 'GlossMap Color', 'alpha_socket': 'GlossMap Alpha', 'position': (-680.0, -320.0)},
        ],
        'alpha_defaults': (False, True),  # Alpha Blend, Alpha Test (CLIP)
    },
    "EYE": {
        'builder': eye_group,
        'maps': [
            {'node_name': 'diffuseMap', 'label': '_d DiffuseMap', 'color_socket': 'DiffuseMap Color', 'alpha_socket': 'DiffuseMap Alpha', 'position': (-680.0, 300.0)},
            {'node_name': 'rotationMap1', 'label': '_n RotationMap1', 'color_socket': 'RotationMap1 Color', 'alpha_socket': 'RotationMap1 Alpha', 'position': (-680.0, 0.0)},
            {'node_name': 'glossMap', 'label': '_s GlossMap', 'color_socket': 'GlossMap Color', 'alpha_socket': 'GlossMap Alpha', 'position': (-680.0, -320.0)},
            {'node_name': 'paletteMap', 'label': '_h PaletteMap', 'color_socket': 'PaletteMap Color', 'alpha_socket': 'PaletteMap Alpha', 'position': (-680.0, -620.0)},
            {'node_name': 'paletteMaskMap', 'label': '_m PaletteMaskMap', 'color_socket': 'PaletteMaskMap Color', 'alpha_socket': 'PaletteMaskMap Alpha', 'position': (-680.0, -920.0)},
        ],
        'alpha_defaults': (False, False),  # Alpha Blend, Alpha Test (OPAQUE)
    },
    "CREATURE": {
        'builder': creature_group,
        'maps': [
            {'node_name': 'diffuseMap', 'label': '_d DiffuseMap', 'color_socket': 'DiffuseMap Color', 'alpha_socket': 'DiffuseMap Alpha', 'position': (-680.0, 300.0)},
            {'node_name': 'rotationMap1', 'label': '_n RotationMap1', 'color_socket': 'RotationMap1 Color', 'alpha_socket': 'RotationMap1 Alpha', 'position': (-680.0, 0.0)},
            {'node_name': 'glossMap', 'label': '_s GlossMap', 'color_socket': 'GlossMap Color', 'alpha_socket': 'GlossMap Alpha', 'position': (-680.0, -320.0)},
            {'node_name': 'paletteMaskMap', 'label': '_m PaletteMaskMap', 'color_socket': 'PaletteMaskMap Color', 'alpha_socket': 'PaletteMaskMap Alpha', 'position': (-680.0, -620.0)},
            {'node_name': 'directionMap', 'label': 'DirectionMap', 'color_socket': 'DirectionMap Color', 'alpha_socket': 'DirectionMap Alpha', 'position': (-680.0, -920.0), 'vector_source': _direction_vector_source(-920.0)},
        ],
        'alpha_defaults': (False, True),  # Alpha Blend, Alpha Test (CLIP)
    },
    "HAIRC": {
        'builder': hairc_group,
        'maps': [
            {'node_name': 'diffuseMap', 'label': '_d DiffuseMap', 'color_socket': 'DiffuseMap Color', 'alpha_socket': 'DiffuseMap Alpha', 'position': (-680.0, 300.0)},
            {'node_name': 'rotationMap1', 'label': '_n RotationMap1', 'color_socket': 'RotationMap1 Color', 'alpha_socket': 'RotationMap1 Alpha', 'position': (-680.0, 0.0)},
            {'node_name': 'glossMap', 'label': '_s GlossMap', 'color_socket': 'GlossMap Color', 'alpha_socket': 'GlossMap Alpha', 'position': (-680.0, -320.0)},
            {'node_name': 'paletteMap', 'label': '_h PaletteMap', 'color_socket': 'PaletteMap Color', 'alpha_socket': 'PaletteMap Alpha', 'position': (-680.0, -620.0)},
            {'node_name': 'paletteMaskMap', 'label': '_m PaletteMaskMap', 'color_socket': 'PaletteMaskMap Color', 'alpha_socket': 'PaletteMaskMap Alpha', 'position': (-680.0, -920.0)},
            {'node_name': 'directionMap', 'label': 'DirectionMap', 'color_socket': 'DirectionMap Color', 'alpha_socket': 'DirectionMap Alpha', 'position': (-680.0, -1220.0), 'vector_source': _direction_vector_source(-1220.0)},
        ],
        'alpha_defaults': (False, True),  # Alpha Blend, Alpha Test (CLIP)
    },
    "HAIRC_MODERN": {
        'builder': hairc_modern_group,
        'maps': [
            {'node_name': 'diffuseMap', 'label': '_d DiffuseMap', 'color_socket': 'DiffuseMap Color', 'alpha_socket': 'DiffuseMap Alpha', 'position': (-680.0, 300.0)},
            {'node_name': 'rotationMap1', 'label': '_n RotationMap1', 'color_socket': 'RotationMap1 Color', 'alpha_socket': 'RotationMap1 Alpha', 'position': (-680.0, 0.0)},
            {'node_name': 'glossMap', 'label': '_s GlossMap', 'color_socket': 'GlossMap Color', 'alpha_socket': 'GlossMap Alpha', 'position': (-680.0, -320.0)},
            {'node_name': 'paletteMap', 'label': '_h PaletteMap', 'color_socket': 'PaletteMap Color', 'alpha_socket': 'PaletteMap Alpha', 'position': (-680.0, -620.0)},
            {'node_name': 'paletteMaskMap', 'label': '_m PaletteMaskMap', 'color_socket': 'PaletteMaskMap Color', 'alpha_socket': 'PaletteMaskMap Alpha', 'position': (-680.0, -920.0)},
            {'node_name': 'directionMap', 'label': 'DirectionMap', 'color_socket': 'DirectionMap Color', 'alpha_socket': 'DirectionMap Alpha', 'position': (-680.0, -1220.0), 'vector_source': _direction_vector_source(-1220.0)},
        ],
        'alpha_defaults': (False, True),  # Alpha Blend, Alpha Test (CLIP) -- matches HairC
    },
    "GARMENT": {
        'builder': garment_group,
        'maps': [
            {'node_name': 'diffuseMap', 'label': '_d DiffuseMap', 'color_socket': 'DiffuseMap Color', 'alpha_socket': 'DiffuseMap Alpha', 'position': (-680.0, 300.0)},
            {'node_name': 'rotationMap1', 'label': '_n RotationMap1', 'color_socket': 'RotationMap1 Color', 'alpha_socket': 'RotationMap1 Alpha', 'position': (-680.0, 0.0)},
            {'node_name': 'glossMap', 'label': '_s GlossMap', 'color_socket': 'GlossMap Color', 'alpha_socket': 'GlossMap Alpha', 'position': (-680.0, -320.0)},
            {'node_name': 'paletteMap', 'label': '_h PaletteMap', 'color_socket': 'PaletteMap Color', 'alpha_socket': 'PaletteMap Alpha', 'position': (-680.0, -620.0)},
            {'node_name': 'paletteMaskMap', 'label': '_m PaletteMaskMap', 'color_socket': 'PaletteMaskMap Color', 'alpha_socket': 'PaletteMaskMap Alpha', 'position': (-680.0, -920.0)},
        ],
        'alpha_defaults': (False, True),  # Alpha Blend, Alpha Test (CLIP)
    },
    "SKINB": {
        'builder': skinb_group,
        'maps': [
            {'node_name': 'diffuseMap', 'label': '_d DiffuseMap', 'color_socket': 'DiffuseMap Color', 'alpha_socket': 'DiffuseMap Alpha', 'position': (-680.0, 300.0)},
            # Secondary column (x=-1080), matching Koda's own tiering of
            # these three as less-central than the other five. Reroute
            # coordinates adopted verbatim from Koda's SkinB (their
            # Complexion/Facepaint/AgeMap[Wrinkles] sit at these exact
            # rows too, so no Y-shift was needed).
            {'node_name': 'complexionMap', 'label': 'ComplexionMap', 'color_socket': 'ComplexionMap Color', 'alpha_socket': 'ComplexionMap Alpha', 'position': (-1080.0, 300.0),
             'color_reroutes': [(-680.0, 20.0), (-340.0, 20.0)], 'alpha_reroutes': [(-680.0, 0.0), (-340.0, 0.0)]},
            {'node_name': 'rotationMap1', 'label': '_n RotationMap1', 'color_socket': 'RotationMap1 Color', 'alpha_socket': 'RotationMap1 Alpha', 'position': (-680.0, -60.0)},
            {'node_name': 'facepaintMap', 'label': 'FacepaintMap', 'color_socket': 'FacepaintMap Color', 'alpha_socket': 'FacepaintMap Alpha', 'position': (-1080.0, -60.0),
             'color_reroutes': [(-680.0, -340.0), (-340.0, -340.0)], 'alpha_reroutes': [(-680.0, -360.0), (-340.0, -360.0)]},
            {'node_name': 'glossMap', 'label': '_s GlossMap', 'color_socket': 'GlossMap Color', 'alpha_socket': 'GlossMap Alpha', 'position': (-680.0, -420.0)},
            {'node_name': 'ageMap', 'label': 'AgeMap', 'color_socket': 'AgeMap Color', 'alpha_socket': 'AgeMap Alpha', 'position': (-1080.0, -420.0),
             'color_reroutes': [(-680.0, -700.0), (-340.0, -700.0)], 'alpha_reroutes': [(-680.0, -720.0), (-340.0, -720.0)]},
            {'node_name': 'paletteMap', 'label': '_h PaletteMap', 'color_socket': 'PaletteMap Color', 'alpha_socket': 'PaletteMap Alpha', 'position': (-680.0, -740.0)},
            {'node_name': 'paletteMaskMap', 'label': '_m PaletteMaskMap', 'color_socket': 'PaletteMaskMap Color', 'alpha_socket': 'PaletteMaskMap Alpha', 'position': (-680.0, -1060.0)},
        ],
        'alpha_defaults': (False, False),  # Alpha Blend, Alpha Test (OPAQUE)
    },
}


# Builds a native SWTOR shader (group node + external Image Texture
# nodes, wired and positioned + Material Output) into `material`. Adds
# to whatever nodes are already there -- does NOT clear existing nodes
# first (matching the original interactive operator's behavior, which
# supports adding multiple shader groups side by side for comparison).
# Callers needing a clean slate (e.g. import_cha.py's get_or_create_
# material(), which rebuilds a material from scratch) should clear the
# material's nodes themselves before calling this.
#
# Sets the group's own Alpha Blend/Alpha Test input defaults from
# SWTOR_SHADER_GROUPS' per-type alpha_defaults.
#
# Returns (group_node, image_nodes) -- image_nodes is a dict of
# {node_name: ShaderNodeTexImage} so the caller can assign actual images
# and any other per-instance overrides (palette values, alpha mode, etc.)
# afterward via group_node.inputs[...].default_value. Used by both
# NODE_OT_add_swtor_shader_group below (interactive Add-menu use) and
# ops/import_cha.py's get_or_create_material() (automated NPC import) --
# one shared source of truth for the node/wiring setup.
def build_swtor_shader_material(material, shader_type):
    shader_def = SWTOR_SHADER_GROUPS.get(shader_type)
    if shader_def is None:
        raise ValueError(f"Unrecognized SWTOR shader type: {shader_type!r}")

    material.use_nodes = True
    node_tree = material.node_tree
    mat_nodes = node_tree.nodes

    group_node = mat_nodes.new(type="ShaderNodeGroup")
    group_node.node_tree = shader_def['builder']()
    group_node.location = (0.0, 300.0)
    group_node.width = 400.0
    group_node.label = group_node.node_tree.name
    group_node.name = group_node.node_tree.name

    # Maps whose own direct group connection is instead handled by another
    # map's vector_source detour (see below) -- skip wiring these normally.
    detoured_rotation_nodes = {
        m['vector_source']['rotation_node']
        for m in shader_def['maps'] if m.get('vector_source')
    }

    def build_reroute_chain(start_socket, positions, final_input=None):
        # type: (bpy.types.NodeSocket, list, bpy.types.NodeSocket) -> bpy.types.NodeSocket
        """
        Creates a chain of reroutes at each (x, y) in `positions`, wiring
        start_socket -> reroutes[0] -> reroutes[1] -> ... in sequence.
        If final_input is given, also links the last reroute straight into
        it. Returns the last reroute's output socket either way, so the
        caller can keep chaining or branching from it.
        """
        current_output = start_socket
        for x, y in positions:
            reroute = mat_nodes.new(type='NodeReroute')
            reroute.location = (x, y)
            node_tree.links.new(current_output, reroute.inputs[0])
            current_output = reroute.outputs[0]
        if final_input is not None:
            node_tree.links.new(current_output, final_input)
        return current_output

    # Image Texture nodes, external to the group, feeding its inputs, at
    # their Koda-adopted positions (see SWTOR_SHADER_GROUPS above).
    image_nodes = {}
    for m in shader_def['maps']:
        image_node = mat_nodes.new(type='ShaderNodeTexImage')
        image_node.label = m['label']
        image_node.location = m['position']
        # node_name IS the real camelCase Blender node name directly --
        # no shorthand/translation involved (see SWTOR_SHADER_GROUPS).
        image_node.name = m['node_name']
        image_node.width = 340.0
        image_nodes[m['node_name']] = image_node

        vector_source = m.get('vector_source')
        if vector_source:
            rotation_node = mat_nodes[vector_source['rotation_node']]

            directionUV = mat_nodes.new(type='ShaderNodeGroup')
            directionUV.location = vector_source['directionUV_position']
            directionUV.node_tree = direction_map_uv()
            directionUV.width = 400.0
            directionUV.label = directionUV.node_tree.name
            directionUV.name = directionUV.node_tree.name

            # Branch reroute: fans out to the main group's Rotation input
            # AND continues the detour chain toward DirectionMapUV.
            color_positions = vector_source['color_reroutes']
            branch_out = build_reroute_chain(rotation_node.outputs['Color'], color_positions[:1])
            node_tree.links.new(branch_out, group_node.inputs['RotationMap1 Color'])
            final_out = build_reroute_chain(branch_out, color_positions[1:])
            node_tree.links.new(final_out, directionUV.inputs['_n RotationMap1 Color'])

            alpha_positions = vector_source['alpha_reroutes']
            branch_out = build_reroute_chain(rotation_node.outputs['Alpha'], alpha_positions[:1])
            node_tree.links.new(branch_out, group_node.inputs['RotationMap1 Alpha'])
            final_out = build_reroute_chain(branch_out, alpha_positions[1:])
            node_tree.links.new(final_out, directionUV.inputs['_n RotationMap1 Alpha'])

            node_tree.links.new(directionUV.outputs['Vector'], image_node.inputs['Vector'])

        if m['node_name'] in detoured_rotation_nodes:
            continue  # wired above via the detour's branch reroute instead

        color_reroutes = m.get('color_reroutes')
        if color_reroutes:
            build_reroute_chain(image_node.outputs['Color'], color_reroutes, group_node.inputs[m['color_socket']])
        else:
            node_tree.links.new(image_node.outputs['Color'], group_node.inputs[m['color_socket']])

        alpha_reroutes = m.get('alpha_reroutes')
        if alpha_reroutes:
            build_reroute_chain(image_node.outputs['Alpha'], alpha_reroutes, group_node.inputs[m['alpha_socket']])
        else:
            node_tree.links.new(image_node.outputs['Alpha'], group_node.inputs[m['alpha_socket']])

    # Material Output -- reuse one already named "Material Output" if
    # present, matching the original operator's behavior, else create one.
    # Position/width always applied either way, to match Koda's layout.
    output = mat_nodes.get("Material Output")
    if output is None:
        output = mat_nodes.new(type='ShaderNodeOutputMaterial')
    output.location = (460.0, 300.0)
    output.width = 140.0
    node_tree.links.new(group_node.outputs['Shader'], output.inputs['Surface'])

    # Alpha mode: per-type defaults, set both at the material level and
    # as the group node's own input defaults. Callers needing different
    # behavior (e.g. import_cha.py's CLIP-always for JSON-driven import)
    # can override group_node.inputs['Alpha Blend'/'Alpha Test'] after
    # this returns.
    material.surface_render_method = "DITHERED"
    material.use_transparency_overlap = False
    alpha_blend, alpha_test = shader_def['alpha_defaults']
    group_node.inputs['Alpha Blend'].default_value = alpha_blend
    group_node.inputs['Alpha Test'].default_value = alpha_test

    return group_node, image_nodes


# Operator that adds a SWTOR shader, built as a native node group (no
# custom Python node class -- fully portable without the addon once
# saved), to the current material in context.
class NODE_OT_add_swtor_shader_group(bpy.types.Operator):
    bl_idname = 'node.add_swtor_shader_group'
    bl_label = 'Add SWTOR Shader (Node Group)'
    bl_description = "Adds this SWTOR Shader as a native node group to the current Material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.material

    swtor_shader_type: bpy.props.StringProperty()

    def execute(self, context):
        if self.swtor_shader_type not in SWTOR_SHADER_GROUPS:
            self.report({'ERROR'}, f"'{self.swtor_shader_type}' shader is not yet ported to the node-group pattern")
            return {'CANCELLED'}

        build_swtor_shader_material(context.material, self.swtor_shader_type)

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