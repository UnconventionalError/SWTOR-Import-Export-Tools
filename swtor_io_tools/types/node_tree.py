# <pep8 compliant>

# This file holds the current, native node-group SWTOR shaders (see
# uber_group(), eye_group(), creature_group(), hairc_group(),
# hairc_modern_group(), garment_group(), skinb_group(), and their shared
# panel helpers below). No addon-side Python is needed to open, render, or
# edit these once saved into a .blend file.
#
# The addon's original custom-Python-node shader system (ShaderNodeHeroEngine
# and its own internal node_tree builders) has moved to node_deprecated.py /
# node_tree_deprecated.py. add_output_socket_if_needed() below is shared by
# both systems.

from os import name
import bpy
from bpy.types import ShaderNodeTree


# Detect Blender version
major, minor, _ = bpy.app.version
blender_version = float(f"{major}.{minor}")


def add_output_socket_if_needed(node_tree):
    # type: (ShaderNodeTree) -> None
    '''
    Checks for the existence of node_tree outputs by different means
    depending on Blender version, and adds one if there is none
    '''
    if blender_version < 4.0:
        if len(node_tree.outputs) == 0:
            node_tree.outputs.new(type='NodeSocketShader', name='Shader')
    else:
        has_output_sockets = False
        for item in node_tree.interface.items_tree:
            if item.item_type == 'SOCKET':
                if item.in_out == 'OUTPUT':
                    has_output_sockets = True
                    break
        if not has_output_sockets:
            node_tree.interface.new_socket('Shader', in_out='OUTPUT', socket_type='NodeSocketShader')


def add_alpha_settings_panel(node_tree):
    # type: (ShaderNodeTree) -> None
    '''
    Adds the shared "Alpha Settings" Panel (Invert Alpha, Alpha Blend,
    Alpha Test, Alpha Test Value) to a shader node group's interface.
    Used by every one of the six SWTOR shader groups -- the panel's
    sockets are meant to be wired straight into that group's own
    SetAlphaMode sub-group instance (IsAlphaModeBlend/IsAlphaModeTest/
    AlphaTestValue/Invert).
    '''
    alpha_panel = node_tree.interface.new_panel("Alpha Settings")

    invert_alpha_socket = node_tree.interface.new_socket(
        'Invert Alpha', in_out='INPUT', socket_type='NodeSocketBool', parent=alpha_panel)
    invert_alpha_socket.default_value = False
    invert_alpha_socket.description = "Set to True for modernized characters' head SkinB materials"

    alpha_blend_socket = node_tree.interface.new_socket(
        'Alpha Blend', in_out='INPUT', socket_type='NodeSocketBool', parent=alpha_panel)
    alpha_blend_socket.default_value = False
    alpha_blend_socket.description = "For Alpha = None, uncheck both this and Alpha Test"

    alpha_test_socket = node_tree.interface.new_socket(
        'Alpha Test', in_out='INPUT', socket_type='NodeSocketBool', parent=alpha_panel)
    alpha_test_socket.default_value = True
    alpha_test_socket.description = "For Alpha = None, uncheck both this and Alpha Blend"

    alpha_test_value_socket = node_tree.interface.new_socket(
        'Alpha Test Value', in_out='INPUT', socket_type='NodeSocketFloat', parent=alpha_panel)
    alpha_test_value_socket.default_value = 0.5
    alpha_test_value_socket.min_value = 0.0
    alpha_test_value_socket.max_value = 1.0


def add_palette_panel(node_tree, panel_name, palette_number, include_metallic_specular=True):
    # type: (ShaderNodeTree, str, int, bool) -> None
    '''
    Adds a "Palette <N> Controls" Panel (Palette<N> Hue, Saturation,
    Brightness, Contrast, Specular, Metallic Specular) to a shader node
    group's interface. Socket naming (e.g. "Palette1 Hue", no space)
    matches Koda Shaders' convention, adopted across the board per
    Crunch -- including single-palette derived types (Eye/HairC/SkinB),
    which still get the "Palette1 " prefix despite only having one
    palette, for consistency with Koda and with Garment's two.

    palette_number is 1 or 2 -- Garment/HairC Modern call this twice, one
    per palette; everything else calls it once with palette_number=1.

    Specular/Metallic Specular have no equivalent in Koda Shaders (which
    uses a real PBR Roughness/Metallic/Specular Tint/IOR model instead,
    not swapped in here -- this is a renaming pass only, not a change to
    the underlying approach) -- prefixed the same way as our own existing
    fields for internal consistency.
    '''
    palette_panel = node_tree.interface.new_panel(panel_name)
    prefix = f"Palette{palette_number} "

    hue_socket = node_tree.interface.new_socket(
        f'{prefix}Hue', in_out='INPUT', socket_type='NodeSocketFloat', parent=palette_panel)
    hue_socket.min_value = 0.0
    hue_socket.max_value = 1.0

    saturation_socket = node_tree.interface.new_socket(
        f'{prefix}Saturation', in_out='INPUT', socket_type='NodeSocketFloat', parent=palette_panel)
    saturation_socket.default_value = 0.5
    saturation_socket.min_value = 0.0
    saturation_socket.max_value = 1.0

    brightness_socket = node_tree.interface.new_socket(
        f'{prefix}Brightness', in_out='INPUT', socket_type='NodeSocketFloat', parent=palette_panel)
    brightness_socket.min_value = -1.0
    brightness_socket.max_value = 1.0

    contrast_socket = node_tree.interface.new_socket(
        f'{prefix}Contrast', in_out='INPUT', socket_type='NodeSocketFloat', parent=palette_panel)
    contrast_socket.default_value = 1.0
    contrast_socket.min_value = 0.0
    contrast_socket.max_value = 3.0

    specular_socket = node_tree.interface.new_socket(
        f'{prefix}Specular', in_out='INPUT', socket_type='NodeSocketColor', parent=palette_panel)
    specular_socket.default_value = [0.0, 0.5, 0.0, 1.0]

    if include_metallic_specular:
        metallic_specular_socket = node_tree.interface.new_socket(
            f'{prefix}Metallic Specular', in_out='INPUT', socket_type='NodeSocketColor', parent=palette_panel)
        metallic_specular_socket.default_value = [0.0, 0.5, 0.0, 1.0]


def add_flesh_flush_panel(node_tree):
    # type: (ShaderNodeTree) -> None
    '''
    Adds the shared "Flesh & Flush Controls" Panel (Flesh Brightness,
    Flush Tone) to a shader node group's interface. Used by Creature and
    SkinB -- the only two derived types whose original custom node
    exposed these on its draw_buttons UI. Order matches that original UI:
    Flesh Brightness, then Flush Tone.
    '''
    flesh_flush_panel = node_tree.interface.new_panel("Flesh & Flush Controls")

    flesh_brightness_socket = node_tree.interface.new_socket(
        'Flesh Brightness', in_out='INPUT', socket_type='NodeSocketFloat', parent=flesh_flush_panel)
    flesh_brightness_socket.min_value = 0.0
    flesh_brightness_socket.max_value = 1.0

    flush_tone_socket = node_tree.interface.new_socket(
        'Flush Tone', in_out='INPUT', socket_type='NodeSocketColor', parent=flesh_flush_panel)
    flush_tone_socket.default_value = [0.0, 0.0, 0.0, 1.0]


def add_passthrough_outputs(node_tree):
    # type: (ShaderNodeTree) -> None
    '''
    Adds passthrough output sockets (Normal, Diffuse Color, Alpha,
    Emission) alongside Shader, so the processed values feeding the
    shader are available outside the group for baking and similar
    workflows. Roughness was considered and dropped -- every one of the
    six shaders uses a hardcoded roughness constant with no
    texture-driven variation, so there's nothing meaningful to expose.

    Only adds the interface sockets; wiring them to each shader's actual
    tap points (the node already feeding the main BSDF/Emission node) is
    done individually per *_group() function, since those tap points
    differ by shader.
    '''
    node_tree.interface.new_socket('Diffuse Color', in_out='OUTPUT', socket_type='NodeSocketColor')
    node_tree.interface.new_socket('Normal', in_out='OUTPUT', socket_type='NodeSocketVector')
    node_tree.interface.new_socket('Alpha', in_out='OUTPUT', socket_type='NodeSocketFloat')
    node_tree.interface.new_socket('Emission', in_out='OUTPUT', socket_type='NodeSocketColor')


def uber_group():
    # type: () -> ShaderNodeTree
    """
    Native node-group version of uber(). Unlike uber() (which builds
    directly inside a ShaderNodeHeroEngine's private, per-node node_tree),
    this returns a single shared, get-or-create'd ShaderNodeTree meant to
    be wrapped in an ordinary ShaderNodeGroup node -- no custom Python
    node class involved, so files stay fully portable without the addon
    installed.

    Image Texture nodes are NOT built in here. Instead, this group
    exposes "Diffuse Color", "Gloss Color", "Gloss Alpha", "Rotation
    Color", and "Rotation Alpha" as real group inputs -- the caller wires
    Image Texture nodes into them from outside the group (see
    add_uber_shader() in ops/shaders_menu.py). Alpha-mode
    controls (previously exposed via the ShaderNodeHeroEngine's
    draw_buttons/properties) are likewise promoted to real group inputs,
    passed straight through to the internal SetAlphaMode sub-group.

    Tunable shader constants that were never exposed on the old custom
    node's UI (gamma, specular power base, clamp range, normal map
    strength) are kept as plain internal node defaults, same as before --
    reachable by entering the group, not promoted to the interface.
    """
    from .node_group import (
        normal_and_alpha_from_swizzled_texture,
        set_alpha_mode,
    )

    # Check if node tree already exists.
    # NOTE: naming convention for all future shader groups is
    # "Atroxa SWTOR - <Shader Name> Shader", e.g. "Atroxa SWTOR - Creature Shader".
    GROUP_NAME = 'Atroxa SWTOR - Uber Shader'
    if GROUP_NAME in bpy.data.node_groups:
        return bpy.data.node_groups[GROUP_NAME]

    node_tree = bpy.data.node_groups.new(name=GROUP_NAME, type='ShaderNodeTree')

    # Add interface sockets. Order of the image-map sockets matches the
    # original custom node's fixed UI order: Diffuse, Normal (Rotation),
    # Gloss -- and, where present on other derived types, Palette,
    # PaletteMask, Direction, Age, Complexion, Facepaint after that.
    diffuse_color_socket = node_tree.interface.new_socket('DiffuseMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    diffuse_color_socket.default_value = [1.0, 1.0, 1.0, 1.0]

    # Not consumed internally (Uber's diffuse texture has no meaningful
    # alpha channel), but always connected from outside regardless -- see
    # NODE_OT_add_swtor_shader_group in ops/shaders_menu.py --
    # purely to keep every image node's Color and Alpha uniformly plugged
    # into the group.
    diffuse_alpha_socket = node_tree.interface.new_socket('DiffuseMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    diffuse_alpha_socket.default_value = 1.0
    diffuse_alpha_socket.min_value = 0.0
    diffuse_alpha_socket.max_value = 1.0

    rotation_color_socket = node_tree.interface.new_socket('RotationMap1 Color', in_out='INPUT', socket_type='NodeSocketColor')
    rotation_color_socket.default_value = [0.0, 0.5, 0.0, 1.0]

    rotation_alpha_socket = node_tree.interface.new_socket('RotationMap1 Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    rotation_alpha_socket.default_value = 0.5
    rotation_alpha_socket.min_value = 0.0
    rotation_alpha_socket.max_value = 1.0

    gloss_color_socket = node_tree.interface.new_socket('GlossMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    gloss_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    gloss_alpha_socket = node_tree.interface.new_socket('GlossMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    gloss_alpha_socket.default_value = 0.0
    gloss_alpha_socket.min_value = 0.0
    gloss_alpha_socket.max_value = 1.0

    # Alpha controls, grouped into an "Alpha Settings" Panel (Blender 4.2+
    # node-group interface panels; we target 4.5/5.2 LTS only, so no
    # dropdown-based fallback is needed) in the requested order.
    add_alpha_settings_panel(node_tree)

    # Add output socket
    add_output_socket_if_needed(node_tree)
    add_passthrough_outputs(node_tree)

    # Add and place nodes
    geom1 = node_tree.nodes.new(type='ShaderNodeNewGeometry')
    geom1.location = (-1400.0, 280.0)

    vMath1 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath1.location = (-1160.0, 280.0)
    vMath1.operation = 'REFLECT'

    vMath2 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath2.inputs[1].default_value = [-1.0, -1.0, -1.0]
    vMath2.location = (-920.0, 280.0)
    vMath2.operation = 'MULTIPLY'

    geom2 = node_tree.nodes.new(type='ShaderNodeNewGeometry')
    geom2.location = (-920.0, 80.0)

    vMath3 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath3.location = (-680.0, 280.0)
    vMath3.operation = 'DOT_PRODUCT'

    clamp1 = node_tree.nodes.new(type='ShaderNodeClamp')
    clamp1.inputs['Min'].default_value = 0.0
    clamp1.inputs['Max'].default_value = 1.0
    clamp1.location = (-440.0, 280.0)

    math1 = node_tree.nodes.new(type='ShaderNodeMath')
    math1.location = (-200, 280.0)
    math1.operation = 'POWER'

    vMath4 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath4.location = (40.0, 280.0)
    vMath4.operation = 'MULTIPLY'

    vMath5 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath5.location = (280.0, 280.0)
    vMath5.operation = 'ADD'

    diffBSDF = node_tree.nodes.new(type='ShaderNodeBsdfDiffuse')
    diffBSDF.inputs['Roughness'].default_value = 0.0
    diffBSDF.location = (520.0, 280.0)

    addShader = node_tree.nodes.new(type='ShaderNodeAddShader')
    addShader.location = (780.0, 280.0)

    mixShader = node_tree.nodes.new(type='ShaderNodeMixShader')
    mixShader.location = (1020.0, 280.0)

    grpOut = node_tree.nodes.new(type='NodeGroupOutput')
    grpOut.location = (1260.0, 280.0)

    math2 = node_tree.nodes.new('ShaderNodeMath')
    math2.inputs[0].default_value = 64.0
    math2.inputs[1].default_value = 1.0
    math2.location = (-920.0, -100.0)
    math2.operation = 'SUBTRACT'

    math3 = node_tree.nodes.new(type='ShaderNodeMath')
    math3.location = (-680.0, 0.0)
    math3.operation = 'MULTIPLY'

    math4 = node_tree.nodes.new(type='ShaderNodeMath')
    math4.inputs[1].default_value = 1.0
    math4.location = (-440.0, 0.0)
    math4.operation = 'ADD'

    gamma1 = node_tree.nodes.new(type='ShaderNodeGamma')
    gamma1.inputs['Gamma'].default_value = 2.1
    gamma1.location = (40.0, 0.0)

    emission = node_tree.nodes.new(type='ShaderNodeEmission')
    emission.location = (520.0, 0.0)

    transparentBSDF = node_tree.nodes.new(type='ShaderNodeBsdfTransparent')
    transparentBSDF.inputs['Color'].default_value = [1.0, 1.0, 1.0, 1.0]
    transparentBSDF.location = (780.0, -100.0)

    tangentN = node_tree.nodes.new(type='ShaderNodeGroup')
    tangentN.location = (-680.0, -280.0)
    tangentN.node_tree = normal_and_alpha_from_swizzled_texture()
    tangentN.width = 380.0

    norMap = node_tree.nodes.new(type='ShaderNodeNormalMap')
    norMap.location = (-180.0, -280.0)

    setAlphaMode = node_tree.nodes.new(type='ShaderNodeGroup')
    setAlphaMode.name = "SetAlphaMode"
    setAlphaMode.location = (760.0, 100.0)
    setAlphaMode.node_tree = set_alpha_mode()
    setAlphaMode.width = 180.0

    # Group Input nodes (multiple copies, spread near their consumers,
    # matching the multi-Group-Input style used throughout node_group.py)
    grpIn_diffuse = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_diffuse.location = (-260.0, 0.0)
    grpIn_diffuse.label = "Diffuse"

    grpIn_gloss = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_gloss.location = (-1260.0, 0.0)
    grpIn_gloss.label = "Gloss"

    grpIn_rotation = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_rotation.location = (-1040.0, -280.0)
    grpIn_rotation.label = "Rotation"

    grpIn_alphaMode = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_alphaMode.location = (520.0, -160.0)
    grpIn_alphaMode.label = "Alpha Mode"

    # Add and place reroutes
    nr3 = node_tree.nodes.new(type='NodeReroute')
    nr3.location = (0.0, 300.0)
    nr4 = node_tree.nodes.new(type='NodeReroute')
    nr4.location = (480.0, 180.0)
    nr11 = node_tree.nodes.new(type='NodeReroute')
    nr11.location = (560.0, -20.0)
    nr18 = node_tree.nodes.new(type='NodeReroute')
    nr18.location = (0.0, -280.0)
    nr19 = node_tree.nodes.new(type='NodeReroute')
    nr19.location = (0.0, -220.0)
    nr20 = node_tree.nodes.new(type='NodeReroute')
    nr20.location = (100.0, -220.0)

    # Link nodes together
    node_tree.links.new(geom1.outputs['Incoming'], vMath1.inputs[1])
    node_tree.links.new(vMath1.outputs['Vector'], vMath2.inputs[0])
    node_tree.links.new(vMath2.outputs['Vector'], vMath3.inputs[0])
    node_tree.links.new(geom2.outputs['Incoming'], vMath3.inputs[1])
    node_tree.links.new(vMath3.outputs['Value'], clamp1.inputs['Value'])
    node_tree.links.new(clamp1.outputs['Result'], math1.inputs[0])
    node_tree.links.new(math1.outputs['Value'], vMath4.inputs[1])
    node_tree.links.new(vMath4.outputs['Vector'], vMath5.inputs[1])
    node_tree.links.new(vMath5.outputs['Vector'], nr4.inputs[0])
    node_tree.links.new(nr4.outputs[0], diffBSDF.inputs['Color'])
    node_tree.links.new(diffBSDF.outputs['BSDF'], addShader.inputs[0])
    node_tree.links.new(addShader.outputs['Shader'], mixShader.inputs[1])
    node_tree.links.new(mixShader.outputs['Shader'], grpOut.inputs[0])
    node_tree.links.new(norMap.outputs['Normal'], grpOut.inputs['Normal'])
    node_tree.links.new(vMath5.outputs['Vector'], grpOut.inputs['Diffuse Color'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], grpOut.inputs['Alpha'])
    node_tree.links.new(emission.outputs['Emission'], grpOut.inputs['Emission'])

    node_tree.links.new(input=grpIn_gloss.outputs['GlossMap Color'], output=vMath4.inputs[0])
    node_tree.links.new(input=grpIn_gloss.outputs['GlossMap Alpha'], output=math3.inputs[0])
    node_tree.links.new(input=math2.outputs['Value'], output=math3.inputs[1])
    node_tree.links.new(input=math3.outputs['Value'], output=math4.inputs[0])
    node_tree.links.new(input=math4.outputs['Value'], output=math1.inputs[1])
    node_tree.links.new(input=grpIn_diffuse.outputs['DiffuseMap Color'], output=gamma1.inputs['Color'])
    node_tree.links.new(input=gamma1.outputs['Color'], output=vMath5.inputs[0])
    node_tree.links.new(input=nr4.outputs[0], output=nr11.inputs[0])
    node_tree.links.new(input=nr11.outputs[0], output=emission.inputs['Color'])
    node_tree.links.new(input=emission.outputs['Emission'], output=addShader.inputs[1])
    node_tree.links.new(input=transparentBSDF.outputs['BSDF'], output=mixShader.inputs[2])

    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Color'], tangentN.inputs[0])
    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Alpha'], tangentN.inputs[1])
    node_tree.links.new(tangentN.outputs['Normal'], norMap.inputs['Color'])
    node_tree.links.new(norMap.outputs['Normal'], nr18.inputs[0])
    node_tree.links.new(nr18.outputs[0], nr19.inputs[0])
    node_tree.links.new(nr19.outputs[0], nr3.inputs[0])
    node_tree.links.new(nr19.outputs[0], nr20.inputs[0])
    node_tree.links.new(nr20.outputs[0], diffBSDF.inputs['Normal'])
    node_tree.links.new(nr3.outputs[0], vMath1.inputs[0])

    node_tree.links.new(tangentN.outputs['Alpha'], setAlphaMode.inputs['Alpha'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], mixShader.inputs['Fac'])

    node_tree.links.new(tangentN.outputs['Emission Strength'], emission.inputs['Strength'])

    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Blend'], setAlphaMode.inputs['IsAlphaModeBlend'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test'], setAlphaMode.inputs['IsAlphaModeTest'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test Value'], setAlphaMode.inputs['AlphaTestValue'])
    node_tree.links.new(grpIn_alphaMode.outputs['Invert Alpha'], setAlphaMode.inputs['Invert'])

    # Hide unused outputs on each Group Input node, so only the sockets
    # relevant to that node's location show up
    for grp_in, keep in (
        (grpIn_diffuse, {'DiffuseMap Color'}),
        (grpIn_gloss, {'GlossMap Color', 'GlossMap Alpha'}),
        (grpIn_rotation, {'RotationMap1 Color', 'RotationMap1 Alpha'}),
        (grpIn_alphaMode, {'Alpha Blend', 'Alpha Test', 'Alpha Test Value', 'Invert Alpha'}),
    ):
        for output in grp_in.outputs:
            if output.name not in keep:
                output.hide = True

    # Hide unlinked node sockets, matching uber()'s original tidiness pass
    for node in (vMath2, clamp1, math2, math4, gamma1, transparentBSDF, norMap):
        for socket in node.inputs:
            if not socket.is_linked:
                socket.hide = True
        for socket in node.outputs:
            if not socket.is_linked:
                socket.hide = True

    # Re-expose the tunable constants that were deliberately kept visible
    # on the old custom node's internal tree (not promoted to the group
    # interface, but still reachable/tweakable by entering the group)
    vMath2.inputs[1].hide = False
    clamp1.inputs[1].hide = False
    clamp1.inputs[2].hide = False
    math2.inputs[0].hide = False
    math2.inputs[1].hide = False
    math4.inputs[1].hide = False
    gamma1.inputs[1].hide = False
    transparentBSDF.inputs['Color'].hide = False
    norMap.inputs['Strength'].hide = False

    return node_tree


def eye_group():
    # type: () -> ShaderNodeTree
    """
    Native node-group version of eye(). See uber_group()'s docstring for
    the general pattern this follows.

    Adds the "Palette1 Controls" panel on top of what uber_group()
    established. HuePixel's Metallic Specular input has a real socket but
    was never wired up in the original custom node (confirmed by Crunch
    as a pre-existing bug in the legacy system, not intentional) -- fixed
    here, so Metallic Specular is included and connected like HairC/
    Garment/SkinB.

    Every image map gets both Color and Alpha exposed as group inputs
    even where the original didn't wire one up internally (Diffuse Alpha,
    PaletteMask Alpha), to keep every image node uniformly plugged into
    the group.
    """
    from .node_group import (
        get_phong_specular,
        hue_pixel,
        negative_normal,
        normal_and_alpha_from_swizzled_texture,
        set_alpha_mode,
    )

    GROUP_NAME = 'Atroxa SWTOR - Eye Shader'
    if GROUP_NAME in bpy.data.node_groups:
        return bpy.data.node_groups[GROUP_NAME]

    node_tree = bpy.data.node_groups.new(name=GROUP_NAME, type='ShaderNodeTree')

    # Add interface sockets. Image-map order matches the original custom
    # node's fixed UI order: Diffuse, Rotation (Normal), Gloss, Palette,
    # PaletteMask.
    diffuse_color_socket = node_tree.interface.new_socket('DiffuseMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    diffuse_color_socket.default_value = [1.0, 1.0, 1.0, 1.0]

    # Not consumed internally, always connected from outside regardless --
    # see NODE_OT_add_swtor_shader_group in ops/shaders_menu.py.
    diffuse_alpha_socket = node_tree.interface.new_socket('DiffuseMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    diffuse_alpha_socket.default_value = 1.0
    diffuse_alpha_socket.min_value = 0.0
    diffuse_alpha_socket.max_value = 1.0

    rotation_color_socket = node_tree.interface.new_socket('RotationMap1 Color', in_out='INPUT', socket_type='NodeSocketColor')
    rotation_color_socket.default_value = [0.0, 0.5, 0.0, 1.0]

    rotation_alpha_socket = node_tree.interface.new_socket('RotationMap1 Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    rotation_alpha_socket.default_value = 0.5
    rotation_alpha_socket.min_value = 0.0
    rotation_alpha_socket.max_value = 1.0

    gloss_color_socket = node_tree.interface.new_socket('GlossMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    gloss_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    gloss_alpha_socket = node_tree.interface.new_socket('GlossMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    gloss_alpha_socket.default_value = 0.0
    gloss_alpha_socket.min_value = 0.0
    gloss_alpha_socket.max_value = 1.0

    palette_color_socket = node_tree.interface.new_socket('PaletteMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    palette_alpha_socket = node_tree.interface.new_socket('PaletteMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_alpha_socket.default_value = 0.0
    palette_alpha_socket.min_value = 0.0
    palette_alpha_socket.max_value = 1.0

    palette_mask_color_socket = node_tree.interface.new_socket('PaletteMaskMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_mask_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally (see Diffuse Alpha above).
    palette_mask_alpha_socket = node_tree.interface.new_socket('PaletteMaskMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_mask_alpha_socket.default_value = 1.0
    palette_mask_alpha_socket.min_value = 0.0
    palette_mask_alpha_socket.max_value = 1.0

    # Palette controls -- Metallic Specular fixed (see docstring)
    add_palette_panel(node_tree, "Palette1 Controls", 1, include_metallic_specular=True)

    # Alpha controls
    add_alpha_settings_panel(node_tree)

    # Add output socket
    add_output_socket_if_needed(node_tree)
    add_passthrough_outputs(node_tree)

    # Add and place nodes
    huePixel = node_tree.nodes.new(type='ShaderNodeGroup')
    huePixel.location = (-760.0, 300.0)
    huePixel.name = 'HuePixel'
    huePixel.node_tree = hue_pixel()
    huePixel.width = 180.0

    vMath1 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath1.inputs[1].default_value = [1.2, 1.2, 1.2]
    vMath1.location = (-480.0, 300.0)
    vMath1.operation = 'MULTIPLY'

    vMath2 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath2.location = (-240.0, 300.0)
    vMath2.operation = 'MULTIPLY'

    phongSpec = node_tree.nodes.new(type='ShaderNodeGroup')
    phongSpec.location = (0.0, 300.0)
    phongSpec.node_tree = get_phong_specular()
    phongSpec.inputs['MaxSpecPower'].default_value = 8.0
    phongSpec.width = 180.0

    vMath3 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath3.location = (280.0, 300.0)
    vMath3.operation = 'ADD'

    principled = node_tree.nodes.new(type='ShaderNodeBsdfPrincipled')
    principled.inputs['Coat Weight'].default_value = 0.25
    principled.inputs['IOR'].default_value = 1.41
    principled.inputs['Roughness'].default_value = 1.0
    principled.inputs['Specular IOR Level'].default_value = 0.0
    principled.location = (520.0, 300.0)
    for socket in principled.inputs:
        if socket.name not in ['Base Color', 'Coat Weight', 'IOR', 'Normal', 'Coat Normal']:
            socket.hide = True

    addShader = node_tree.nodes.new(type='ShaderNodeAddShader')
    addShader.location = (860.0, 300.0)

    mixShader1 = node_tree.nodes.new(type='ShaderNodeMixShader')
    mixShader1.location = (1100.0, 300.0)

    grpOut1 = node_tree.nodes.new(type='NodeGroupOutput')
    grpOut1.location = (1340.0, 300.0)

    tangentN = node_tree.nodes.new(type='ShaderNodeGroup')
    tangentN.location = (-1100.0, -100.0)
    tangentN.node_tree = normal_and_alpha_from_swizzled_texture()
    tangentN.width = 240.0

    negativeN = node_tree.nodes.new(type='ShaderNodeGroup')
    negativeN.location = (-760.0, -100.0)
    negativeN.node_tree = negative_normal()
    negativeN.width = 180.0

    negN = node_tree.nodes.new(type='ShaderNodeNormalMap')
    negN.location = (-480.0, 60.0)
    negN.width = 140.0

    N = node_tree.nodes.new(type='ShaderNodeNormalMap')
    N.location = (-240.0, 60.0)
    N.width = 140.0

    emissionShader = node_tree.nodes.new(type='ShaderNodeEmission')
    emissionShader.location = (520.0, 40.0)
    emissionShader.width = 240.0

    transparentBSDF = node_tree.nodes.new(type='ShaderNodeBsdfTransparent')
    transparentBSDF.inputs['Color'].default_value = [1.0, 1.0, 1.0, 1.0]
    transparentBSDF.location = (860.0, -50.0)

    setAlphaMode = node_tree.nodes.new(type='ShaderNodeGroup')
    setAlphaMode.name = "SetAlphaMode"
    setAlphaMode.location = (840.0, 155.0)
    setAlphaMode.node_tree = set_alpha_mode()
    setAlphaMode.width = 180.0

    # Group Input nodes, one per consuming area, each showing only the
    # sockets relevant to that location
    grpIn_diffuse = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_diffuse.location = (-1100.0, 380.0)
    grpIn_diffuse.label = "Diffuse"

    grpIn_gloss = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_gloss.location = (-1100.0, 460.0)
    grpIn_gloss.label = "Gloss"

    grpIn_glossAlpha = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_glossAlpha.location = (-260.0, 140.0)
    grpIn_glossAlpha.label = "Gloss Alpha"

    grpIn_palette = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette.location = (-1100.0, 540.0)
    grpIn_palette.label = "Palette"

    grpIn_paletteMask = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_paletteMask.location = (-1100.0, 620.0)
    grpIn_paletteMask.label = "Palette Mask"

    grpIn_rotation = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_rotation.location = (-1400.0, -100.0)
    grpIn_rotation.label = "Rotation"

    grpIn_palette1 = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette1.location = (-1100.0, 700.0)
    grpIn_palette1.label = "Palette1 Controls"

    grpIn_alphaMode = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_alphaMode.location = (520.0, -160.0)
    grpIn_alphaMode.label = "Alpha Mode"

    # Link nodes together
    node_tree.links.new(grpIn_diffuse.outputs['DiffuseMap Color'], huePixel.inputs['_d DiffuseMap Color'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], huePixel.inputs['_m PaletteMaskMap Color'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Color'], huePixel.inputs['_h PaletteMap Color'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Alpha'], huePixel.inputs['_h PaletteMap Alpha'])
    node_tree.links.new(grpIn_gloss.outputs['GlossMap Color'], huePixel.inputs['_s GlossMap Color'])

    node_tree.links.new(grpIn_palette1.outputs['Palette1 Hue'], huePixel.inputs['Hue'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Saturation'], huePixel.inputs['Saturation'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Brightness'], huePixel.inputs['Brightness'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Contrast'], huePixel.inputs['Contrast'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Specular'], huePixel.inputs['Specular'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Metallic Specular'], huePixel.inputs['Metallic Specular'])

    node_tree.links.new(huePixel.outputs['Diffuse Color'], vMath1.inputs[0])
    node_tree.links.new(huePixel.outputs['Diffuse Color'], vMath3.inputs[0])
    node_tree.links.new(huePixel.outputs['Specular Color'], vMath2.inputs[1])
    node_tree.links.new(vMath1.outputs['Vector'], vMath2.inputs[0])
    node_tree.links.new(vMath2.outputs['Vector'], phongSpec.inputs['Specular Color'])
    node_tree.links.new(phongSpec.outputs['Specular'], vMath3.inputs[1])
    node_tree.links.new(vMath3.outputs['Vector'], principled.inputs['Base Color'])
    node_tree.links.new(vMath3.outputs['Vector'], emissionShader.inputs['Color'])
    node_tree.links.new(principled.outputs['BSDF'], addShader.inputs[0])
    node_tree.links.new(addShader.outputs['Shader'], mixShader1.inputs[1])
    node_tree.links.new(mixShader1.outputs['Shader'], grpOut1.inputs['Shader'])
    node_tree.links.new(N.outputs['Normal'], grpOut1.inputs['Normal'])
    node_tree.links.new(vMath3.outputs['Vector'], grpOut1.inputs['Diffuse Color'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], grpOut1.inputs['Alpha'])
    node_tree.links.new(emissionShader.outputs['Emission'], grpOut1.inputs['Emission'])

    node_tree.links.new(grpIn_glossAlpha.outputs['GlossMap Alpha'], phongSpec.inputs['Specular Alpha'])

    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Color'], tangentN.inputs['_n RotationMap Color'])
    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Alpha'], tangentN.inputs['_n RotationMap Alpha'])
    node_tree.links.new(tangentN.outputs['Normal'], negativeN.inputs['Normal'])
    node_tree.links.new(negativeN.outputs['-Normal'], negN.inputs['Color'])
    node_tree.links.new(negN.outputs['Normal'], phongSpec.inputs['-Normal'])
    node_tree.links.new(tangentN.outputs['Normal'], N.inputs['Color'])
    node_tree.links.new(N.outputs['Normal'], phongSpec.inputs['Normal'])
    node_tree.links.new(N.outputs['Normal'], principled.inputs['Normal'])
    node_tree.links.new(N.outputs['Normal'], principled.inputs['Coat Normal'])

    node_tree.links.new(tangentN.outputs['Alpha'], setAlphaMode.inputs['Alpha'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], mixShader1.inputs['Fac'])
    node_tree.links.new(tangentN.outputs['Emission Strength'], emissionShader.inputs['Strength'])
    node_tree.links.new(emissionShader.outputs['Emission'], addShader.inputs[1])
    node_tree.links.new(transparentBSDF.outputs['BSDF'], mixShader1.inputs[2])

    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Blend'], setAlphaMode.inputs['IsAlphaModeBlend'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test'], setAlphaMode.inputs['IsAlphaModeTest'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test Value'], setAlphaMode.inputs['AlphaTestValue'])
    node_tree.links.new(grpIn_alphaMode.outputs['Invert Alpha'], setAlphaMode.inputs['Invert'])

    # Hide unused outputs on each Group Input node, so only the sockets
    # relevant to that node's location show up
    for grp_in, keep in (
        (grpIn_diffuse, {'DiffuseMap Color'}),
        (grpIn_gloss, {'GlossMap Color'}),
        (grpIn_glossAlpha, {'GlossMap Alpha'}),
        (grpIn_palette, {'PaletteMap Color', 'PaletteMap Alpha'}),
        (grpIn_paletteMask, {'PaletteMaskMap Color'}),
        (grpIn_rotation, {'RotationMap1 Color', 'RotationMap1 Alpha'}),
        (grpIn_palette1, {'Palette1 Hue', 'Palette1 Saturation', 'Palette1 Brightness', 'Palette1 Contrast', 'Palette1 Specular', 'Palette1 Metallic Specular'}),
        (grpIn_alphaMode, {'Alpha Blend', 'Alpha Test', 'Alpha Test Value', 'Invert Alpha'}),
    ):
        for output in grp_in.outputs:
            if output.name not in keep:
                output.hide = True

    return node_tree


def creature_group():
    # type: () -> ShaderNodeTree
    """
    Native node-group version of creature(). See uber_group()'s docstring
    for the general pattern this follows.

    Adds "Flesh & Flush Controls" (Flesh Brightness, Flush Tone) on top of
    the Alpha Settings panel established by uber_group().

    Direction Map is a special case: its Vector input isn't a standard UV
    -- it's driven by GetSpecularLookup(), computed from the decoded
    Rotation Map normal. A round-trip through the group boundary (group
    outputs the vector, external Direction Map node feeds its Color back
    in) was tried first but Blender rejects it as a dependency cycle
    (Group -> external node -> same Group instance), even though nothing
    about the actual data forms a loop. So instead: this group has no
    idea Direction Map exists at all. It only takes "Direction Color" as
    a plain input. The GetSpecularLookup computation needed to drive the
    external Direction Map node's Vector input is built a second time,
    entirely outside this group, by NODE_OT_add_swtor_shader_group in
    ops/shaders_menu.py (from the same external Rotation Map
    node already feeding this group's Rotation Color/Alpha).
    """
    from .node_group import (
        get_flush_color,
        get_phong_specular,
        normal_and_alpha_from_swizzled_texture,
        set_alpha_mode,
    )

    GROUP_NAME = 'Atroxa SWTOR - Creature Shader'
    if GROUP_NAME in bpy.data.node_groups:
        return bpy.data.node_groups[GROUP_NAME]

    node_tree = bpy.data.node_groups.new(name=GROUP_NAME, type='ShaderNodeTree')

    # Add interface sockets. Image-map order matches the original custom
    # node's fixed UI order: Diffuse, Rotation (Normal), Gloss,
    # PaletteMask, Direction.
    diffuse_color_socket = node_tree.interface.new_socket('DiffuseMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    diffuse_color_socket.default_value = [1.0, 1.0, 1.0, 1.0]

    diffuse_alpha_socket = node_tree.interface.new_socket('DiffuseMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    diffuse_alpha_socket.default_value = 1.0
    diffuse_alpha_socket.min_value = 0.0
    diffuse_alpha_socket.max_value = 1.0

    rotation_color_socket = node_tree.interface.new_socket('RotationMap1 Color', in_out='INPUT', socket_type='NodeSocketColor')
    rotation_color_socket.default_value = [0.0, 0.5, 0.0, 1.0]

    rotation_alpha_socket = node_tree.interface.new_socket('RotationMap1 Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    rotation_alpha_socket.default_value = 0.5
    rotation_alpha_socket.min_value = 0.0
    rotation_alpha_socket.max_value = 1.0

    gloss_color_socket = node_tree.interface.new_socket('GlossMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    gloss_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    gloss_alpha_socket = node_tree.interface.new_socket('GlossMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    gloss_alpha_socket.default_value = 0.0
    gloss_alpha_socket.min_value = 0.0
    gloss_alpha_socket.max_value = 1.0

    palette_mask_color_socket = node_tree.interface.new_socket('PaletteMaskMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_mask_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally, always connected from outside regardless --
    # see NODE_OT_add_swtor_shader_group in ops/shaders_menu.py.
    palette_mask_alpha_socket = node_tree.interface.new_socket('PaletteMaskMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_mask_alpha_socket.default_value = 1.0
    palette_mask_alpha_socket.min_value = 0.0
    palette_mask_alpha_socket.max_value = 1.0

    direction_color_socket = node_tree.interface.new_socket('DirectionMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    direction_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally (see Palette Mask Alpha above).
    direction_alpha_socket = node_tree.interface.new_socket('DirectionMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    direction_alpha_socket.default_value = 1.0
    direction_alpha_socket.min_value = 0.0
    direction_alpha_socket.max_value = 1.0

    # Flesh/Flush and Alpha controls
    add_flesh_flush_panel(node_tree)
    add_alpha_settings_panel(node_tree)

    # Add output socket
    add_output_socket_if_needed(node_tree)
    add_passthrough_outputs(node_tree)

    # Add and place nodes
    gamma1 = node_tree.nodes.new(type='ShaderNodeGamma')
    gamma1.inputs['Gamma'].default_value = 2.1
    gamma1.location = (-780.0, 580.0)

    math1 = node_tree.nodes.new(type='ShaderNodeMath')
    math1.inputs[1].default_value = 0.5
    math1.location = (-780.0, 460.0)
    math1.operation = 'GREATER_THAN'

    math2 = node_tree.nodes.new(type='ShaderNodeMath')
    math2.location = (-540.0, 460.0)
    math2.operation = 'MULTIPLY'

    math3 = node_tree.nodes.new(type='ShaderNodeMath')
    math3.inputs[1].default_value = 0.5
    math3.location = (-300.0, 460.0)
    math3.operation = 'SUBTRACT'
    math3.use_clamp = True

    math4 = node_tree.nodes.new(type='ShaderNodeMath')
    math4.inputs[1].default_value = 2.0
    math4.location = (-60.0, 460.0)
    math4.operation = 'MULTIPLY'

    tangentN = node_tree.nodes.new(type='ShaderNodeGroup')
    tangentN.location = (-1400.0, 100.0)
    tangentN.name = "tangentN"
    tangentN.node_tree = normal_and_alpha_from_swizzled_texture()
    tangentN.width = 400.0

    norMap = node_tree.nodes.new(type='ShaderNodeNormalMap')
    norMap.location = (-900.0, 100.0)

    vMath1 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath1.location = (-60.0, 260.0)
    vMath1.operation = 'MULTIPLY'

    mix1 = node_tree.nodes.new(type='ShaderNodeMixRGB')
    mix1.location = (180.0, 260.0)

    vMath2 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath2.location = (420.0, 260.0)
    vMath2.operation = 'ADD'

    vMath3 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath3.location = (660.0, 260.0)
    vMath3.operation = 'ADD'

    diffuseBSDF = node_tree.nodes.new(type='ShaderNodeBsdfDiffuse')
    diffuseBSDF.inputs['Roughness'].default_value = 0.0
    diffuseBSDF.location = (900.0, 260.0)

    mixShader1 = node_tree.nodes.new(type='ShaderNodeMixShader')
    mixShader1.location = (1160.0, 260.0)

    addShader = node_tree.nodes.new(type='ShaderNodeAddShader')
    addShader.location = (1400.0, 260.0)

    mixShader2 = node_tree.nodes.new(type='ShaderNodeMixShader')
    mixShader2.location = (1640.0, 260.0)

    grpOut1 = node_tree.nodes.new(type='NodeGroupOutput')
    grpOut1.location = (1880.0, 260.0)

    phongSpec = node_tree.nodes.new(type='ShaderNodeGroup')
    phongSpec.location = (-300.0, -80.0)
    phongSpec.name = "GetPhongSpecular"
    phongSpec.node_tree = get_phong_specular()
    phongSpec.width = 220.0

    sepXYZ = node_tree.nodes.new(type='ShaderNodeSeparateXYZ')
    sepXYZ.location = (-60.0, -80.0)

    flushColor = node_tree.nodes.new(type='ShaderNodeGroup')
    flushColor.location = (-60.0, -320.0)
    flushColor.name = "GetFlushColor"
    flushColor.node_tree = get_flush_color()
    flushColor.width = 200.0

    vMath4 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath4.location = (180.0, -80.0)
    vMath4.operation = 'MULTIPLY'

    glossyBSDF = node_tree.nodes.new(type='ShaderNodeBsdfGlossy')
    glossyBSDF.distribution = 'BECKMANN'
    glossyBSDF.inputs['Roughness'].default_value = 0.5
    glossyBSDF.location = (900.0, -80.0)

    emission = node_tree.nodes.new(type='ShaderNodeEmission')
    emission.location = (1160.0, -80.0)

    transparentBSDF = node_tree.nodes.new(type='ShaderNodeBsdfTransparent')
    transparentBSDF.inputs['Color'].default_value = [1.0, 1.0, 1.0, 1.0]
    transparentBSDF.location = (1400.0, -100.0)

    setAlphaMode = node_tree.nodes.new(type='ShaderNodeGroup')
    setAlphaMode.name = "SetAlphaMode"
    setAlphaMode.location = (1380.0, 100.0)
    setAlphaMode.node_tree = set_alpha_mode()
    setAlphaMode.width = 180.0

    # Group Input/Output nodes, one per consuming area, each showing only
    # the sockets relevant to that location
    grpIn_diffuse = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_diffuse.location = (-1060.0, 620.0)
    grpIn_diffuse.label = "Diffuse"

    grpIn_rotation = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_rotation.location = (-1700.0, 100.0)
    grpIn_rotation.label = "Rotation"

    grpIn_gloss = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_gloss.location = (-360.0, 60.0)
    grpIn_gloss.label = "Gloss"

    grpIn_paletteMask = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_paletteMask.location = (-360.0, -160.0)
    grpIn_paletteMask.label = "Palette Mask"

    grpIn_direction = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_direction.location = (-360.0, 300.0)
    grpIn_direction.label = "Direction"

    grpIn_fleshFlush = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_fleshFlush.location = (-360.0, -400.0)
    grpIn_fleshFlush.label = "Flesh & Flush"

    grpIn_alphaMode = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_alphaMode.location = (1160.0, -220.0)
    grpIn_alphaMode.label = "Alpha Mode"

    # Link nodes together
    node_tree.links.new(grpIn_diffuse.outputs['DiffuseMap Color'], gamma1.inputs['Color'])
    node_tree.links.new(grpIn_diffuse.outputs['DiffuseMap Alpha'], math1.inputs[0])
    node_tree.links.new(grpIn_diffuse.outputs['DiffuseMap Alpha'], math2.inputs[0])
    node_tree.links.new(math1.outputs['Value'], math2.inputs[1])
    node_tree.links.new(math2.outputs['Value'], math3.inputs[0])
    node_tree.links.new(math3.outputs['Value'], math4.inputs[0])
    node_tree.links.new(math4.outputs['Value'], mixShader1.inputs['Fac'])

    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Color'], tangentN.inputs['_n RotationMap Color'])
    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Alpha'], tangentN.inputs['_n RotationMap Alpha'])
    node_tree.links.new(tangentN.outputs['Normal'], norMap.inputs['Color'])
    node_tree.links.new(tangentN.outputs['Alpha'], setAlphaMode.inputs['Alpha'])
    node_tree.links.new(tangentN.outputs['Emission Strength'], emission.inputs['Strength'])

    node_tree.links.new(norMap.outputs['Normal'], phongSpec.inputs['Normal'])
    node_tree.links.new(norMap.outputs['Normal'], phongSpec.inputs['-Normal'])
    node_tree.links.new(norMap.outputs['Normal'], flushColor.inputs['Normal'])
    node_tree.links.new(norMap.outputs['Normal'], diffuseBSDF.inputs['Normal'])
    node_tree.links.new(norMap.outputs['Normal'], glossyBSDF.inputs['Normal'])

    node_tree.links.new(grpIn_direction.outputs['DirectionMap Color'], vMath1.inputs[0])
    node_tree.links.new(grpIn_gloss.outputs['GlossMap Color'], vMath1.inputs[1])
    node_tree.links.new(grpIn_gloss.outputs['GlossMap Color'], phongSpec.inputs['Specular Color'])
    node_tree.links.new(grpIn_gloss.outputs['GlossMap Alpha'], phongSpec.inputs['Specular Alpha'])
    node_tree.links.new(vMath1.outputs['Vector'], mix1.inputs['Color2'])

    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], sepXYZ.inputs['Vector'])
    node_tree.links.new(sepXYZ.outputs['Y'], vMath4.inputs[0])
    node_tree.links.new(sepXYZ.outputs['Z'], mix1.inputs['Fac'])

    node_tree.links.new(phongSpec.outputs['Specular'], mix1.inputs['Color1'])
    node_tree.links.new(mix1.outputs['Color'], vMath2.inputs[1])
    node_tree.links.new(gamma1.outputs['Color'], vMath2.inputs[0])
    node_tree.links.new(gamma1.outputs['Color'], flushColor.inputs['Diffuse Color'])
    node_tree.links.new(vMath2.outputs['Vector'], vMath3.inputs[0])

    node_tree.links.new(grpIn_fleshFlush.outputs['Flesh Brightness'], flushColor.inputs['Flesh Brightness'])
    node_tree.links.new(grpIn_fleshFlush.outputs['Flush Tone'], flushColor.inputs['Flush Tone'])
    node_tree.links.new(flushColor.outputs['Flush Color'], vMath4.inputs[1])
    node_tree.links.new(vMath4.outputs['Vector'], vMath3.inputs[1])

    node_tree.links.new(vMath3.outputs['Vector'], diffuseBSDF.inputs['Color'])
    node_tree.links.new(vMath3.outputs['Vector'], emission.inputs['Color'])
    node_tree.links.new(vMath3.outputs['Vector'], glossyBSDF.inputs['Color'])

    node_tree.links.new(diffuseBSDF.outputs['BSDF'], mixShader1.inputs[1])
    node_tree.links.new(glossyBSDF.outputs['BSDF'], mixShader1.inputs[2])
    node_tree.links.new(mixShader1.outputs['Shader'], addShader.inputs[0])
    node_tree.links.new(emission.outputs['Emission'], addShader.inputs[1])
    node_tree.links.new(addShader.outputs['Shader'], mixShader2.inputs[1])
    node_tree.links.new(transparentBSDF.outputs['BSDF'], mixShader2.inputs[2])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], mixShader2.inputs['Fac'])
    node_tree.links.new(mixShader2.outputs['Shader'], grpOut1.inputs['Shader'])
    node_tree.links.new(norMap.outputs['Normal'], grpOut1.inputs['Normal'])
    node_tree.links.new(vMath3.outputs['Vector'], grpOut1.inputs['Diffuse Color'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], grpOut1.inputs['Alpha'])
    node_tree.links.new(emission.outputs['Emission'], grpOut1.inputs['Emission'])

    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Blend'], setAlphaMode.inputs['IsAlphaModeBlend'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test'], setAlphaMode.inputs['IsAlphaModeTest'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test Value'], setAlphaMode.inputs['AlphaTestValue'])
    node_tree.links.new(grpIn_alphaMode.outputs['Invert Alpha'], setAlphaMode.inputs['Invert'])

    # Hide unused outputs on each Group Input node, and unused inputs on
    # each Group Output node, so only the sockets relevant to that node's
    # location show up
    for grp_in, keep in (
        (grpIn_diffuse, {'DiffuseMap Color', 'DiffuseMap Alpha'}),
        (grpIn_rotation, {'RotationMap1 Color', 'RotationMap1 Alpha'}),
        (grpIn_gloss, {'GlossMap Color', 'GlossMap Alpha'}),
        (grpIn_paletteMask, {'PaletteMaskMap Color'}),
        (grpIn_direction, {'DirectionMap Color'}),
        (grpIn_fleshFlush, {'Flesh Brightness', 'Flush Tone'}),
        (grpIn_alphaMode, {'Alpha Blend', 'Alpha Test', 'Alpha Test Value', 'Invert Alpha'}),
    ):
        for output in grp_in.outputs:
            if output.name not in keep:
                output.hide = True

    # Hide unlinked node sockets, matching creature()'s original tidiness
    # pass, for the nodes with tunable constants not promoted to the
    # group interface
    for node in (gamma1, math1, math3, math4, norMap, phongSpec, transparentBSDF):
        for socket in node.inputs:
            if not socket.is_linked:
                socket.hide = True
        for socket in node.outputs:
            if not socket.is_linked:
                socket.hide = True

    # Re-expose the tunable constants that were deliberately kept visible
    # on the old custom node's internal tree
    gamma1.inputs[1].hide = False
    math1.inputs[1].hide = False
    math3.inputs[1].hide = False
    math4.inputs[1].hide = False
    norMap.inputs['Strength'].hide = False
    phongSpec.inputs['MaxSpecPower'].hide = False
    transparentBSDF.inputs['Color'].hide = False

    return node_tree


def hairc_group():
    # type: () -> ShaderNodeTree
    """
    Native node-group version of hairc(). See uber_group()'s docstring
    for the general pattern, eye_group()'s for the Palette panel, and
    creature_group()'s for the Direction Map handling.

    HairC drives HuePixel's Hue/Saturation/Brightness/Contrast/Specular/
    Metallic Specular directly (no ChosenPalette sub-group -- that's only
    used by Garment, which has two palettes to switch between), so this
    uses a single "Palette1 Controls" panel with Metallic Specular
    included (unlike Eye, which omits it).

    Like Creature, Direction Map lives entirely outside this group;
    NODE_OT_add_swtor_shader_group builds a DirectionMapUV instance
    outside to drive its Vector input, fed from the same external
    Rotation Map node.
    """
    from .node_group import (
        get_phong_specular,
        hue_pixel,
        normal_and_alpha_from_swizzled_texture,
        set_alpha_mode,
    )

    GROUP_NAME = 'Atroxa SWTOR - HairC Shader'
    if GROUP_NAME in bpy.data.node_groups:
        return bpy.data.node_groups[GROUP_NAME]

    node_tree = bpy.data.node_groups.new(name=GROUP_NAME, type='ShaderNodeTree')

    # Add interface sockets. Image-map order matches the original custom
    # node's fixed UI order: Diffuse, Rotation (Normal), Gloss, Palette,
    # PaletteMask, Direction.
    diffuse_color_socket = node_tree.interface.new_socket('DiffuseMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    diffuse_color_socket.default_value = [1.0, 1.0, 1.0, 1.0]

    # Not consumed internally, always connected from outside regardless --
    # see NODE_OT_add_swtor_shader_group in ops/shaders_menu.py.
    diffuse_alpha_socket = node_tree.interface.new_socket('DiffuseMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    diffuse_alpha_socket.default_value = 1.0
    diffuse_alpha_socket.min_value = 0.0
    diffuse_alpha_socket.max_value = 1.0

    rotation_color_socket = node_tree.interface.new_socket('RotationMap1 Color', in_out='INPUT', socket_type='NodeSocketColor')
    rotation_color_socket.default_value = [0.0, 0.5, 0.0, 1.0]

    rotation_alpha_socket = node_tree.interface.new_socket('RotationMap1 Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    rotation_alpha_socket.default_value = 0.5
    rotation_alpha_socket.min_value = 0.0
    rotation_alpha_socket.max_value = 1.0

    gloss_color_socket = node_tree.interface.new_socket('GlossMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    gloss_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    gloss_alpha_socket = node_tree.interface.new_socket('GlossMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    gloss_alpha_socket.default_value = 0.0
    gloss_alpha_socket.min_value = 0.0
    gloss_alpha_socket.max_value = 1.0

    palette_color_socket = node_tree.interface.new_socket('PaletteMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    palette_alpha_socket = node_tree.interface.new_socket('PaletteMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_alpha_socket.default_value = 0.0
    palette_alpha_socket.min_value = 0.0
    palette_alpha_socket.max_value = 1.0

    palette_mask_color_socket = node_tree.interface.new_socket('PaletteMaskMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_mask_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally (see Diffuse Alpha above).
    palette_mask_alpha_socket = node_tree.interface.new_socket('PaletteMaskMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_mask_alpha_socket.default_value = 1.0
    palette_mask_alpha_socket.min_value = 0.0
    palette_mask_alpha_socket.max_value = 1.0

    direction_color_socket = node_tree.interface.new_socket('DirectionMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    direction_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally (see Diffuse Alpha above).
    direction_alpha_socket = node_tree.interface.new_socket('DirectionMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    direction_alpha_socket.default_value = 1.0
    direction_alpha_socket.min_value = 0.0
    direction_alpha_socket.max_value = 1.0

    # Palette controls -- HairC includes Metallic Specular (unlike Eye)
    add_palette_panel(node_tree, "Palette1 Controls", 1, include_metallic_specular=True)

    # Alpha controls
    add_alpha_settings_panel(node_tree)

    # Add output socket
    add_output_socket_if_needed(node_tree)
    add_passthrough_outputs(node_tree)

    # Add and place nodes
    sepXYZ = node_tree.nodes.new(type='ShaderNodeSeparateXYZ')
    sepXYZ.location = (-780.0, 440.0)
    sepXYZ.outputs['X'].hide = True
    sepXYZ.outputs['Y'].hide = True
    sepXYZ.width = 180.0

    huePixel = node_tree.nodes.new(type='ShaderNodeGroup')
    huePixel.location = (-780.0, 300.0)
    huePixel.name = 'HuePixel'
    huePixel.node_tree = hue_pixel()
    huePixel.width = 180.0

    phongSpec = node_tree.nodes.new(type='ShaderNodeGroup')
    phongSpec.location = (-500.0, 300.0)
    phongSpec.node_tree = get_phong_specular()
    phongSpec.width = 180.0

    mixRGB = node_tree.nodes.new(type='ShaderNodeMixRGB')
    mixRGB.location = (-220.0, 300.0)

    vMath1 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath1.location = (40.0, 300.0)
    vMath1.operation = 'ADD'

    diffuseBSDF = node_tree.nodes.new(type='ShaderNodeBsdfDiffuse')
    diffuseBSDF.location = (300.0, 300.0)

    addShader = node_tree.nodes.new(type='ShaderNodeAddShader')
    addShader.location = (560.0, 300.0)

    mixShader = node_tree.nodes.new(type='ShaderNodeMixShader')
    mixShader.location = (800.0, 300.0)

    grpOut1 = node_tree.nodes.new(type='NodeGroupOutput')
    grpOut1.location = (1040.0, 300.0)

    tangentN = node_tree.nodes.new(type='ShaderNodeGroup')
    tangentN.location = (-1400.0, -60.0)
    tangentN.node_tree = normal_and_alpha_from_swizzled_texture()
    tangentN.width = 400.0

    norMap = node_tree.nodes.new(type='ShaderNodeNormalMap')
    norMap.location = (-900.0, -60.0)

    vMath2 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath2.location = (-500.0, 100.0)
    vMath2.operation = 'MULTIPLY'

    emission = node_tree.nodes.new(type='ShaderNodeEmission')
    emission.location = (300.0, 80.0)

    transparentBSDF = node_tree.nodes.new(type='ShaderNodeBsdfTransparent')
    transparentBSDF.inputs['Color'].default_value = [1.0, 1.0, 1.0, 1.0]
    transparentBSDF.location = (560.0, -60.0)

    setAlphaMode = node_tree.nodes.new(type='ShaderNodeGroup')
    setAlphaMode.name = "SetAlphaMode"
    setAlphaMode.location = (540.0, 140.0)
    setAlphaMode.node_tree = set_alpha_mode()
    setAlphaMode.width = 180.0

    # Group Input nodes, one per consuming area, each showing only the
    # sockets relevant to that location
    grpIn_diffuse = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_diffuse.location = (-1060.0, 340.0)
    grpIn_diffuse.label = "Diffuse"

    grpIn_rotation = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_rotation.location = (-1700.0, -60.0)
    grpIn_rotation.label = "Rotation"

    grpIn_gloss = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_gloss.location = (-1060.0, 260.0)
    grpIn_gloss.label = "Gloss"

    grpIn_glossAlpha = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_glossAlpha.location = (-500.0, 180.0)
    grpIn_glossAlpha.label = "Gloss Alpha"

    grpIn_palette = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette.location = (-1060.0, 180.0)
    grpIn_palette.label = "Palette"

    grpIn_paletteMask = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_paletteMask.location = (-1060.0, 100.0)
    grpIn_paletteMask.label = "Palette Mask"

    grpIn_direction = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_direction.location = (-780.0, 100.0)
    grpIn_direction.label = "Direction"

    grpIn_palette1 = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette1.location = (-1060.0, 20.0)
    grpIn_palette1.label = "Palette1 Controls"

    grpIn_alphaMode = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_alphaMode.location = (300.0, -160.0)
    grpIn_alphaMode.label = "Alpha Mode"

    # Link nodes together
    node_tree.links.new(grpIn_diffuse.outputs['DiffuseMap Color'], huePixel.inputs['_d DiffuseMap Color'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], sepXYZ.inputs['Vector'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], huePixel.inputs['_m PaletteMaskMap Color'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Color'], huePixel.inputs['_h PaletteMap Color'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Alpha'], huePixel.inputs['_h PaletteMap Alpha'])
    node_tree.links.new(grpIn_gloss.outputs['GlossMap Color'], huePixel.inputs['_s GlossMap Color'])

    node_tree.links.new(grpIn_palette1.outputs['Palette1 Hue'], huePixel.inputs['Hue'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Saturation'], huePixel.inputs['Saturation'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Brightness'], huePixel.inputs['Brightness'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Contrast'], huePixel.inputs['Contrast'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Specular'], huePixel.inputs['Specular'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Metallic Specular'], huePixel.inputs['Metallic Specular'])

    node_tree.links.new(sepXYZ.outputs['Z'], mixRGB.inputs['Fac'])
    node_tree.links.new(huePixel.outputs['Diffuse Color'], vMath1.inputs[0])
    node_tree.links.new(huePixel.outputs['Specular Color'], phongSpec.inputs['Specular Color'])
    node_tree.links.new(huePixel.outputs['Specular Color'], vMath2.inputs[0])
    node_tree.links.new(phongSpec.outputs['Specular'], mixRGB.inputs['Color1'])
    node_tree.links.new(mixRGB.outputs['Color'], vMath1.inputs[1])
    node_tree.links.new(vMath1.outputs['Vector'], diffuseBSDF.inputs['Color'])
    node_tree.links.new(vMath1.outputs['Vector'], emission.inputs['Color'])
    node_tree.links.new(diffuseBSDF.outputs['BSDF'], addShader.inputs[0])
    node_tree.links.new(addShader.outputs['Shader'], mixShader.inputs[1])
    node_tree.links.new(mixShader.outputs['Shader'], grpOut1.inputs['Shader'])
    node_tree.links.new(norMap.outputs['Normal'], grpOut1.inputs['Normal'])
    node_tree.links.new(vMath1.outputs['Vector'], grpOut1.inputs['Diffuse Color'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], grpOut1.inputs['Alpha'])
    node_tree.links.new(emission.outputs['Emission'], grpOut1.inputs['Emission'])

    node_tree.links.new(grpIn_glossAlpha.outputs['GlossMap Alpha'], phongSpec.inputs['Specular Alpha'])

    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Color'], tangentN.inputs['_n RotationMap Color'])
    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Alpha'], tangentN.inputs['_n RotationMap Alpha'])
    node_tree.links.new(tangentN.outputs['Normal'], norMap.inputs['Color'])
    node_tree.links.new(norMap.outputs['Normal'], phongSpec.inputs['Normal'])
    node_tree.links.new(norMap.outputs['Normal'], phongSpec.inputs['-Normal'])
    node_tree.links.new(norMap.outputs['Normal'], diffuseBSDF.inputs['Normal'])

    node_tree.links.new(grpIn_direction.outputs['DirectionMap Color'], vMath2.inputs[1])
    node_tree.links.new(vMath2.outputs['Vector'], mixRGB.inputs['Color2'])

    node_tree.links.new(tangentN.outputs['Alpha'], setAlphaMode.inputs['Alpha'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], mixShader.inputs['Fac'])
    node_tree.links.new(tangentN.outputs['Emission Strength'], emission.inputs['Strength'])
    node_tree.links.new(emission.outputs['Emission'], addShader.inputs[1])
    node_tree.links.new(transparentBSDF.outputs['BSDF'], mixShader.inputs[2])

    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Blend'], setAlphaMode.inputs['IsAlphaModeBlend'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test'], setAlphaMode.inputs['IsAlphaModeTest'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test Value'], setAlphaMode.inputs['AlphaTestValue'])
    node_tree.links.new(grpIn_alphaMode.outputs['Invert Alpha'], setAlphaMode.inputs['Invert'])

    # Hide unused outputs on each Group Input node, so only the sockets
    # relevant to that node's location show up
    for grp_in, keep in (
        (grpIn_diffuse, {'DiffuseMap Color'}),
        (grpIn_rotation, {'RotationMap1 Color', 'RotationMap1 Alpha'}),
        (grpIn_gloss, {'GlossMap Color'}),
        (grpIn_glossAlpha, {'GlossMap Alpha'}),
        (grpIn_palette, {'PaletteMap Color', 'PaletteMap Alpha'}),
        (grpIn_paletteMask, {'PaletteMaskMap Color'}),
        (grpIn_direction, {'DirectionMap Color'}),
        (grpIn_palette1, {'Palette1 Hue', 'Palette1 Saturation', 'Palette1 Brightness', 'Palette1 Contrast', 'Palette1 Specular', 'Palette1 Metallic Specular'}),
        (grpIn_alphaMode, {'Alpha Blend', 'Alpha Test', 'Alpha Test Value', 'Invert Alpha'}),
    ):
        for output in grp_in.outputs:
            if output.name not in keep:
                output.hide = True

    # Hide unlinked node sockets, matching hairc()'s original tidiness
    # pass, for the nodes with tunable constants not promoted to the
    # group interface
    for node in (norMap, transparentBSDF):
        for socket in node.inputs:
            if not socket.is_linked:
                socket.hide = True
        for socket in node.outputs:
            if not socket.is_linked:
                socket.hide = True

    # Re-expose the tunable constants that were deliberately kept visible
    # on the old custom node's internal tree
    norMap.inputs['Strength'].hide = False
    transparentBSDF.inputs['Color'].hide = False

    return node_tree


def garment_group():
    # type: () -> ShaderNodeTree
    """
    Native node-group version of garment(). See uber_group()'s docstring
    for the general pattern this follows.

    Garment has no Direction Map (unlike Creature/HairC) and no Flesh/
    Flush controls (unlike Creature/SkinB) -- its combine step is a
    simple ADD of HuePixel's diffuse and GetPhongSpecular's result,
    matching uber_group()'s shape more than eye_group()'s.

    Its distinguishing feature is ChosenPalette: two full palettes
    ("Palette1 Controls", "Palette2 Controls") blended by the Palette
    Mask, rather than one palette driving HuePixel directly like Eye/
    HairC. Both panels use add_palette_panel()'s socket_prefix option so
    their otherwise-identical socket names (Hue, Saturation, ...) don't
    collide on this group's interface.
    """
    from .node_group import (
        chosen_palette,
        get_phong_specular,
        hue_pixel,
        normal_and_alpha_from_swizzled_texture,
        set_alpha_mode,
    )

    GROUP_NAME = 'Atroxa SWTOR - Garment Shader'
    if GROUP_NAME in bpy.data.node_groups:
        return bpy.data.node_groups[GROUP_NAME]

    node_tree = bpy.data.node_groups.new(name=GROUP_NAME, type='ShaderNodeTree')

    # Add interface sockets. Image-map order matches the original custom
    # node's fixed UI order: Diffuse, Rotation (Normal), Gloss, Palette,
    # PaletteMask.
    diffuse_color_socket = node_tree.interface.new_socket('DiffuseMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    diffuse_color_socket.default_value = [1.0, 1.0, 1.0, 1.0]

    # Not consumed internally, always connected from outside regardless --
    # see NODE_OT_add_swtor_shader_group in ops/shaders_menu.py.
    diffuse_alpha_socket = node_tree.interface.new_socket('DiffuseMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    diffuse_alpha_socket.default_value = 1.0
    diffuse_alpha_socket.min_value = 0.0
    diffuse_alpha_socket.max_value = 1.0

    rotation_color_socket = node_tree.interface.new_socket('RotationMap1 Color', in_out='INPUT', socket_type='NodeSocketColor')
    rotation_color_socket.default_value = [0.0, 0.5, 0.0, 1.0]

    rotation_alpha_socket = node_tree.interface.new_socket('RotationMap1 Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    rotation_alpha_socket.default_value = 0.5
    rotation_alpha_socket.min_value = 0.0
    rotation_alpha_socket.max_value = 1.0

    gloss_color_socket = node_tree.interface.new_socket('GlossMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    gloss_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    gloss_alpha_socket = node_tree.interface.new_socket('GlossMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    gloss_alpha_socket.default_value = 0.0
    gloss_alpha_socket.min_value = 0.0
    gloss_alpha_socket.max_value = 1.0

    palette_color_socket = node_tree.interface.new_socket('PaletteMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    palette_alpha_socket = node_tree.interface.new_socket('PaletteMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_alpha_socket.default_value = 0.0
    palette_alpha_socket.min_value = 0.0
    palette_alpha_socket.max_value = 1.0

    palette_mask_color_socket = node_tree.interface.new_socket('PaletteMaskMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_mask_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally (see Diffuse Alpha above).
    palette_mask_alpha_socket = node_tree.interface.new_socket('PaletteMaskMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_mask_alpha_socket.default_value = 1.0
    palette_mask_alpha_socket.min_value = 0.0
    palette_mask_alpha_socket.max_value = 1.0

    # Two full palettes, blended by Palette Mask via ChosenPalette
    add_palette_panel(node_tree, "Palette1 Controls", 1, include_metallic_specular=True)
    add_palette_panel(node_tree, "Palette2 Controls", 2, include_metallic_specular=True)

    # Alpha controls
    add_alpha_settings_panel(node_tree)

    # Add output socket
    add_output_socket_if_needed(node_tree)
    add_passthrough_outputs(node_tree)

    # Add and place nodes
    huePixel = node_tree.nodes.new(type='ShaderNodeGroup')
    huePixel.location = (-500.0, 300.0)
    huePixel.name = 'HuePixel'
    huePixel.node_tree = hue_pixel()
    huePixel.width = 180.0

    phongSpec = node_tree.nodes.new(type='ShaderNodeGroup')
    phongSpec.location = (-220.0, 300.0)
    phongSpec.name = 'GetPhongSpecular'
    phongSpec.node_tree = get_phong_specular()
    phongSpec.width = 220.0

    vMath1 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath1.location = (100.0, 300.0)
    vMath1.operation = 'ADD'

    diffuseBSDF = node_tree.nodes.new(type='ShaderNodeBsdfDiffuse')
    diffuseBSDF.inputs['Roughness'].default_value = 0.0
    diffuseBSDF.location = (340.0, 300.0)

    addShader = node_tree.nodes.new(type='ShaderNodeAddShader')
    addShader.location = (600.0, 300.0)

    mixShader = node_tree.nodes.new(type='ShaderNodeMixShader')
    mixShader.location = (840.0, 300.0)

    grpOut1 = node_tree.nodes.new(type='NodeGroupOutput')
    grpOut1.location = (1080.0, 300.0)

    chosenPalette = node_tree.nodes.new(type='ShaderNodeGroup')
    chosenPalette.location = (-780.0, -40.0)
    chosenPalette.name = 'ChosenPalette'
    chosenPalette.node_tree = chosen_palette()
    chosenPalette.width = 240.0

    emissionShader = node_tree.nodes.new(type='ShaderNodeEmission')
    emissionShader.location = (340.0, 120.0)

    transparentShader = node_tree.nodes.new(type='ShaderNodeBsdfTransparent')
    transparentShader.inputs['Color'].default_value = [1.0, 1.0, 1.0, 1.0]
    transparentShader.location = (600.0, -40.0)

    tangentN = node_tree.nodes.new(type='ShaderNodeGroup')
    tangentN.location = (-1140.0, -300.0)
    tangentN.node_tree = normal_and_alpha_from_swizzled_texture()
    tangentN.width = 280.0

    norMap = node_tree.nodes.new(type='ShaderNodeNormalMap')
    norMap.location = (-780.0, -300.0)
    norMap.width = 180.0

    setAlphaMode = node_tree.nodes.new(type='ShaderNodeGroup')
    setAlphaMode.name = "SetAlphaMode"
    setAlphaMode.location = (580.0, 160.0)
    setAlphaMode.node_tree = set_alpha_mode()
    setAlphaMode.width = 180.0

    # Group Input nodes, one per consuming area, each showing only the
    # sockets relevant to that location
    grpIn_diffuse = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_diffuse.location = (-780.0, 340.0)
    grpIn_diffuse.label = "Diffuse"

    grpIn_rotation = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_rotation.location = (-1440.0, -300.0)
    grpIn_rotation.label = "Rotation"

    grpIn_gloss = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_gloss.location = (-780.0, 260.0)
    grpIn_gloss.label = "Gloss"

    grpIn_glossAlpha = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_glossAlpha.location = (-220.0, 180.0)
    grpIn_glossAlpha.label = "Gloss Alpha"

    grpIn_palette = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette.location = (-780.0, 180.0)
    grpIn_palette.label = "Palette"

    grpIn_paletteMask = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_paletteMask.location = (-1140.0, -40.0)
    grpIn_paletteMask.label = "Palette Mask"

    grpIn_palette1and2 = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette1and2.location = (-1140.0, -240.0)
    grpIn_palette1and2.label = "Palette 1 & 2 Controls"

    grpIn_alphaMode = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_alphaMode.location = (340.0, -160.0)
    grpIn_alphaMode.label = "Alpha Mode"

    # Link nodes together
    node_tree.links.new(grpIn_diffuse.outputs['DiffuseMap Color'], huePixel.inputs['_d DiffuseMap Color'])
    node_tree.links.new(grpIn_gloss.outputs['GlossMap Color'], huePixel.inputs['_s GlossMap Color'])
    node_tree.links.new(grpIn_glossAlpha.outputs['GlossMap Alpha'], phongSpec.inputs['Specular Alpha'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Color'], huePixel.inputs['_h PaletteMap Color'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Alpha'], huePixel.inputs['_h PaletteMap Alpha'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], chosenPalette.inputs['_m PaletteMaskMap Color'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], huePixel.inputs['_m PaletteMaskMap Color'])

    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Hue'], chosenPalette.inputs['Palette1 Hue'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Saturation'], chosenPalette.inputs['Palette1 Saturation'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Brightness'], chosenPalette.inputs['Palette1 Brightness'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Contrast'], chosenPalette.inputs['Palette1 Contrast'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Specular'], chosenPalette.inputs['Palette1 Specular'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Metallic Specular'], chosenPalette.inputs['Palette1 Metallic Specular'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Hue'], chosenPalette.inputs['Palette2 Hue'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Saturation'], chosenPalette.inputs['Palette2 Saturation'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Brightness'], chosenPalette.inputs['Palette2 Brightness'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Contrast'], chosenPalette.inputs['Palette2 Contrast'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Specular'], chosenPalette.inputs['Palette2 Specular'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Metallic Specular'], chosenPalette.inputs['Palette2 Metallic Specular'])

    node_tree.links.new(chosenPalette.outputs['Hue'], huePixel.inputs['Hue'])
    node_tree.links.new(chosenPalette.outputs['Saturation'], huePixel.inputs['Saturation'])
    node_tree.links.new(chosenPalette.outputs['Brightness'], huePixel.inputs['Brightness'])
    node_tree.links.new(chosenPalette.outputs['Contrast'], huePixel.inputs['Contrast'])
    node_tree.links.new(chosenPalette.outputs['Specular'], huePixel.inputs['Specular'])
    node_tree.links.new(chosenPalette.outputs['Metallic Specular'], huePixel.inputs['Metallic Specular'])

    node_tree.links.new(huePixel.outputs['Diffuse Color'], vMath1.inputs[0])
    node_tree.links.new(huePixel.outputs['Specular Color'], phongSpec.inputs['Specular Color'])
    node_tree.links.new(phongSpec.outputs['Specular'], vMath1.inputs[1])
    node_tree.links.new(vMath1.outputs['Vector'], diffuseBSDF.inputs['Color'])
    node_tree.links.new(vMath1.outputs['Vector'], emissionShader.inputs['Color'])
    node_tree.links.new(diffuseBSDF.outputs['BSDF'], addShader.inputs[0])
    node_tree.links.new(addShader.outputs['Shader'], mixShader.inputs[1])
    node_tree.links.new(mixShader.outputs['Shader'], grpOut1.inputs['Shader'])
    node_tree.links.new(norMap.outputs['Normal'], grpOut1.inputs['Normal'])
    node_tree.links.new(vMath1.outputs['Vector'], grpOut1.inputs['Diffuse Color'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], grpOut1.inputs['Alpha'])
    node_tree.links.new(emissionShader.outputs['Emission'], grpOut1.inputs['Emission'])

    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Color'], tangentN.inputs['_n RotationMap Color'])
    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Alpha'], tangentN.inputs['_n RotationMap Alpha'])
    node_tree.links.new(tangentN.outputs['Normal'], norMap.inputs['Color'])
    node_tree.links.new(norMap.outputs['Normal'], phongSpec.inputs['Normal'])
    node_tree.links.new(norMap.outputs['Normal'], phongSpec.inputs['-Normal'])
    node_tree.links.new(norMap.outputs['Normal'], diffuseBSDF.inputs['Normal'])

    node_tree.links.new(tangentN.outputs['Alpha'], setAlphaMode.inputs['Alpha'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], mixShader.inputs['Fac'])
    node_tree.links.new(tangentN.outputs['Emission Strength'], emissionShader.inputs['Strength'])
    node_tree.links.new(emissionShader.outputs['Emission'], addShader.inputs[1])
    node_tree.links.new(transparentShader.outputs['BSDF'], mixShader.inputs[2])

    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Blend'], setAlphaMode.inputs['IsAlphaModeBlend'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test'], setAlphaMode.inputs['IsAlphaModeTest'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test Value'], setAlphaMode.inputs['AlphaTestValue'])
    node_tree.links.new(grpIn_alphaMode.outputs['Invert Alpha'], setAlphaMode.inputs['Invert'])

    # Hide unused outputs on each Group Input node, so only the sockets
    # relevant to that node's location show up
    for grp_in, keep in (
        (grpIn_diffuse, {'DiffuseMap Color'}),
        (grpIn_rotation, {'RotationMap1 Color', 'RotationMap1 Alpha'}),
        (grpIn_gloss, {'GlossMap Color'}),
        (grpIn_glossAlpha, {'GlossMap Alpha'}),
        (grpIn_palette, {'PaletteMap Color', 'PaletteMap Alpha'}),
        (grpIn_paletteMask, {'PaletteMaskMap Color'}),
        (grpIn_palette1and2, {
            'Palette1 Hue', 'Palette1 Saturation', 'Palette1 Brightness', 'Palette1 Contrast',
            'Palette1 Specular', 'Palette1 Metallic Specular',
            'Palette2 Hue', 'Palette2 Saturation', 'Palette2 Brightness', 'Palette2 Contrast',
            'Palette2 Specular', 'Palette2 Metallic Specular',
        }),
        (grpIn_alphaMode, {'Alpha Blend', 'Alpha Test', 'Alpha Test Value', 'Invert Alpha'}),
    ):
        for output in grp_in.outputs:
            if output.name not in keep:
                output.hide = True

    # Hide unlinked node sockets, matching garment()'s original tidiness
    # pass, for the nodes with tunable constants not promoted to the
    # group interface
    for node in (phongSpec, norMap, transparentShader):
        for socket in node.inputs:
            if not socket.is_linked:
                socket.hide = True
        for socket in node.outputs:
            if not socket.is_linked:
                socket.hide = True

    # Re-expose the tunable constants that were deliberately kept visible
    # on the old custom node's internal tree
    phongSpec.inputs['MaxSpecPower'].hide = False
    norMap.inputs['Strength'].hide = False
    transparentShader.inputs['Color'].hide = False

    return node_tree


def skinb_group():
    # type: () -> ShaderNodeTree
    """
    Native node-group version of skinb(). See uber_group()'s docstring
    for the general pattern, eye_group()'s for the Palette panel, and
    creature_group()'s for Flesh & Flush.

    The most complex of the six -- three extra image maps (Age,
    Complexion, Facepaint) on top of the usual five, HueSkinPixel instead
    of HuePixel (includes Metallic Specular, like HairC/Garment), and
    CombineNormals + ExtractAgeNormalAndScarFromSwizzledTexture combining
    the Rotation Map's decoded normal with the Age Map's decoded normal
    and scar mask. No Direction Map, so no external round-trip needed
    here.

    Complexion Color defaults to white (identity for the multiply it
    feeds into) -- matching the original custom node's fallback of a
    plain white image when no Complexion Map was assigned.
    """
    from .node_group import (
        combine_normals,
        extract_age_normal_and_scar_from_swizzled_texture,
        get_flush_color,
        get_phong_specular,
        hue_skin_pixel,
        normal_and_alpha_from_swizzled_texture,
        set_alpha_mode,
    )

    GROUP_NAME = 'Atroxa SWTOR - SkinB Shader'
    if GROUP_NAME in bpy.data.node_groups:
        return bpy.data.node_groups[GROUP_NAME]

    node_tree = bpy.data.node_groups.new(name=GROUP_NAME, type='ShaderNodeTree')

    # Add interface sockets. Image-map order matches the new Koda-aligned
    # two-column layout: Diffuse, Complexion, Rotation, Facepaint, Gloss,
    # Age, Palette, PaletteMask -- each pair sitting alongside its visual
    # counterpart in the other column (see ops/shaders_menu.py's
    # SWTOR_SHADER_GROUPS positions), not the original custom node's UI
    # order.
    diffuse_color_socket = node_tree.interface.new_socket('DiffuseMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    diffuse_color_socket.default_value = [1.0, 1.0, 1.0, 1.0]

    # Not consumed internally, always connected from outside regardless --
    # see NODE_OT_add_swtor_shader_group in ops/shaders_menu.py.
    diffuse_alpha_socket = node_tree.interface.new_socket('DiffuseMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    diffuse_alpha_socket.default_value = 1.0
    diffuse_alpha_socket.min_value = 0.0
    diffuse_alpha_socket.max_value = 1.0

    # Defaults to white -- identity value for the multiply it feeds into,
    # matching the original's white-image fallback when unassigned.
    complexion_color_socket = node_tree.interface.new_socket('ComplexionMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    complexion_color_socket.default_value = [1.0, 1.0, 1.0, 1.0]

    # Not consumed internally (see Diffuse Alpha above).
    complexion_alpha_socket = node_tree.interface.new_socket('ComplexionMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    complexion_alpha_socket.default_value = 1.0
    complexion_alpha_socket.min_value = 0.0
    complexion_alpha_socket.max_value = 1.0

    rotation_color_socket = node_tree.interface.new_socket('RotationMap1 Color', in_out='INPUT', socket_type='NodeSocketColor')
    rotation_color_socket.default_value = [0.0, 0.5, 0.0, 1.0]

    rotation_alpha_socket = node_tree.interface.new_socket('RotationMap1 Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    rotation_alpha_socket.default_value = 0.5
    rotation_alpha_socket.min_value = 0.0
    rotation_alpha_socket.max_value = 1.0

    facepaint_color_socket = node_tree.interface.new_socket('FacepaintMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    facepaint_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    facepaint_alpha_socket = node_tree.interface.new_socket('FacepaintMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    facepaint_alpha_socket.default_value = 0.0
    facepaint_alpha_socket.min_value = 0.0
    facepaint_alpha_socket.max_value = 1.0

    gloss_color_socket = node_tree.interface.new_socket('GlossMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    gloss_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    gloss_alpha_socket = node_tree.interface.new_socket('GlossMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    gloss_alpha_socket.default_value = 0.0
    gloss_alpha_socket.min_value = 0.0
    gloss_alpha_socket.max_value = 1.0

    age_color_socket = node_tree.interface.new_socket('AgeMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    age_color_socket.default_value = [0.0, 0.5, 1.0, 1.0]

    age_alpha_socket = node_tree.interface.new_socket('AgeMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    age_alpha_socket.default_value = 0.5
    age_alpha_socket.min_value = 0.0
    age_alpha_socket.max_value = 1.0

    palette_color_socket = node_tree.interface.new_socket('PaletteMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    palette_alpha_socket = node_tree.interface.new_socket('PaletteMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_alpha_socket.default_value = 0.0
    palette_alpha_socket.min_value = 0.0
    palette_alpha_socket.max_value = 1.0

    palette_mask_color_socket = node_tree.interface.new_socket('PaletteMaskMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_mask_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally (see Diffuse Alpha above).
    palette_mask_alpha_socket = node_tree.interface.new_socket('PaletteMaskMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_mask_alpha_socket.default_value = 1.0
    palette_mask_alpha_socket.min_value = 0.0
    palette_mask_alpha_socket.max_value = 1.0

    # Palette controls -- SkinB includes Metallic Specular (like HairC/Garment)
    add_palette_panel(node_tree, "Palette1 Controls", 1, include_metallic_specular=True)

    # Flesh/Flush and Alpha controls
    add_flesh_flush_panel(node_tree)
    add_alpha_settings_panel(node_tree)

    # Add output socket
    add_output_socket_if_needed(node_tree)
    add_passthrough_outputs(node_tree)

    # Add and place nodes
    huePixel = node_tree.nodes.new(type='ShaderNodeGroup')
    huePixel.location = (-780.0, 460.0)
    huePixel.name = 'HueSkinPixel'
    huePixel.node_tree = hue_skin_pixel()
    huePixel.width = 240.0

    phongSpec = node_tree.nodes.new(type='ShaderNodeGroup')
    phongSpec.location = (-440.0, 460.0)
    phongSpec.node_tree = get_phong_specular()

    vMath1 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath1.location = (-200.0, 460.0)
    vMath1.operation = 'MULTIPLY'

    vMath2 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath2.location = (40.0, 460.0)
    vMath2.operation = 'MULTIPLY'

    mixRGB = node_tree.nodes.new(type='ShaderNodeMixRGB')
    mixRGB.location = (280.0, 460.0)

    vMath3 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath3.location = (520.0, 460.0)
    vMath3.operation = 'ADD'

    vMath4 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath4.location = (760.0, 460.0)
    vMath4.operation = 'ADD'

    diffuseBSDF = node_tree.nodes.new(type='ShaderNodeBsdfDiffuse')
    diffuseBSDF.inputs['Roughness'].default_value = 0.0
    diffuseBSDF.location = (1000.0, 460.0)

    addShader = node_tree.nodes.new(type='ShaderNodeAddShader')
    addShader.location = (1260.0, 460.0)

    mixShader = node_tree.nodes.new(type='ShaderNodeMixShader')
    mixShader.location = (1500.0, 460.0)

    grpOut1 = node_tree.nodes.new(type='NodeGroupOutput')
    grpOut1.location = (1740.0, 460.0)

    gamma1 = node_tree.nodes.new(type='ShaderNodeGamma')
    gamma1.inputs['Gamma'].default_value = 2.1
    gamma1.location = (-440.0, 240.0)

    vMath5 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath5.location = (40.0, 240.0)
    vMath5.operation = 'MULTIPLY'

    flushColor = node_tree.nodes.new(type='ShaderNodeGroup')
    flushColor.location = (520.0, 240.0)
    flushColor.name = 'GetFlushColor'
    flushColor.node_tree = get_flush_color()

    emission = node_tree.nodes.new(type='ShaderNodeEmission')
    emission.location = (1000.0, 240.0)

    transparentBSDF = node_tree.nodes.new(type='ShaderNodeBsdfTransparent')
    transparentBSDF.inputs['Color'].default_value = [1.0, 1.0, 1.0, 1.0]
    transparentBSDF.location = (1260.0, 90.0)

    tangentN = node_tree.nodes.new(type='ShaderNodeGroup')
    tangentN.location = (-1400.0, -60.0)
    tangentN.node_tree = normal_and_alpha_from_swizzled_texture()
    tangentN.width = 240.0

    comNor = node_tree.nodes.new(type='ShaderNodeGroup')
    comNor.location = (-1060.0, -60.0)
    comNor.node_tree = combine_normals()
    comNor.width = 180.0

    ageDarkening = node_tree.nodes.new(type='ShaderNodeMixRGB')
    ageDarkening.inputs['Color2'].default_value = [1.0, 1.0, 1.0, 1.0]
    ageDarkening.label = 'AgeDarkening'
    ageDarkening.location = (-440.0, -60.0)
    ageDarkening.name = 'ageDarkening'

    ageNormal = node_tree.nodes.new(type='ShaderNodeGroup')
    ageNormal.location = (-1060.0, -340.0)
    ageNormal.node_tree = extract_age_normal_and_scar_from_swizzled_texture()
    ageNormal.width = 240.0

    setAlphaMode = node_tree.nodes.new(type='ShaderNodeGroup')
    setAlphaMode.name = "SetAlphaMode"
    setAlphaMode.location = (1240.0, 300.0)
    setAlphaMode.node_tree = set_alpha_mode()
    setAlphaMode.width = 180.0

    # Group Input nodes, one per consuming area, each showing only the
    # sockets relevant to that location
    grpIn_diffuse = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_diffuse.location = (-1060.0, 500.0)
    grpIn_diffuse.label = "Diffuse"

    grpIn_rotation = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_rotation.location = (-1700.0, -60.0)
    grpIn_rotation.label = "Rotation"

    grpIn_gloss = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_gloss.location = (-1060.0, 420.0)
    grpIn_gloss.label = "Gloss"

    grpIn_glossAlpha = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_glossAlpha.location = (-440.0, 380.0)
    grpIn_glossAlpha.label = "Gloss Alpha"

    grpIn_palette = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette.location = (-1060.0, 340.0)
    grpIn_palette.label = "Palette"

    grpIn_paletteMask = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_paletteMask.location = (-1060.0, 260.0)
    grpIn_paletteMask.label = "Palette Mask"

    grpIn_age = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_age.location = (-1400.0, -340.0)
    grpIn_age.label = "Age"

    grpIn_complexion = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_complexion.location = (-440.0, 560.0)
    grpIn_complexion.label = "Complexion"

    grpIn_facepaint = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_facepaint.location = (-440.0, 140.0)
    grpIn_facepaint.label = "Facepaint"

    grpIn_palette1 = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette1.location = (-1060.0, 180.0)
    grpIn_palette1.label = "Palette1 Controls"

    grpIn_fleshFlush = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_fleshFlush.location = (520.0, 100.0)
    grpIn_fleshFlush.label = "Flesh & Flush"

    grpIn_alphaMode = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_alphaMode.location = (1000.0, -80.0)
    grpIn_alphaMode.label = "Alpha Mode"

    # Link nodes together
    node_tree.links.new(grpIn_diffuse.outputs['DiffuseMap Color'], huePixel.inputs['_d DiffuseMap Color'])
    node_tree.links.new(grpIn_gloss.outputs['GlossMap Color'], huePixel.inputs['_s GlossMap Color'])
    node_tree.links.new(grpIn_glossAlpha.outputs['GlossMap Alpha'], phongSpec.inputs['Specular Alpha'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Color'], huePixel.inputs['_h PaletteMap Color'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Alpha'], huePixel.inputs['_h PaletteMap Alpha'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], huePixel.inputs['_m PaletteMaskMap Color'])

    node_tree.links.new(grpIn_palette1.outputs['Palette1 Hue'], huePixel.inputs['Hue'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Saturation'], huePixel.inputs['Saturation'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Brightness'], huePixel.inputs['Brightness'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Contrast'], huePixel.inputs['Contrast'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Specular'], huePixel.inputs['Specular'])
    node_tree.links.new(grpIn_palette1.outputs['Palette1 Metallic Specular'], huePixel.inputs['Metallic Specular'])

    node_tree.links.new(huePixel.outputs['Diffuse Color'], vMath1.inputs[0])
    node_tree.links.new(huePixel.outputs['Diffuse Color'], flushColor.inputs['Diffuse Color'])
    node_tree.links.new(grpIn_complexion.outputs['ComplexionMap Color'], vMath1.inputs[1])
    node_tree.links.new(huePixel.outputs['Specular Color'], phongSpec.inputs['Specular Color'])
    node_tree.links.new(phongSpec.outputs['Specular'], vMath5.inputs[0])

    node_tree.links.new(vMath1.outputs['Vector'], vMath2.inputs[0])
    node_tree.links.new(ageDarkening.outputs['Color'], vMath2.inputs[1])
    node_tree.links.new(ageDarkening.outputs['Color'], vMath5.inputs[1])
    node_tree.links.new(vMath2.outputs['Vector'], mixRGB.inputs['Color1'])
    node_tree.links.new(gamma1.outputs['Color'], mixRGB.inputs['Color2'])
    node_tree.links.new(grpIn_facepaint.outputs['FacepaintMap Color'], gamma1.inputs['Color'])
    node_tree.links.new(grpIn_facepaint.outputs['FacepaintMap Alpha'], mixRGB.inputs['Fac'])
    node_tree.links.new(mixRGB.outputs['Color'], vMath3.inputs[0])
    node_tree.links.new(vMath5.outputs['Vector'], vMath3.inputs[1])
    node_tree.links.new(vMath3.outputs['Vector'], vMath4.inputs[0])
    node_tree.links.new(flushColor.outputs['Flush Color'], vMath4.inputs[1])
    node_tree.links.new(vMath4.outputs['Vector'], diffuseBSDF.inputs['Color'])
    node_tree.links.new(vMath4.outputs['Vector'], emission.inputs['Color'])

    node_tree.links.new(diffuseBSDF.outputs['BSDF'], addShader.inputs[0])
    node_tree.links.new(emission.outputs['Emission'], addShader.inputs[1])
    node_tree.links.new(addShader.outputs['Shader'], mixShader.inputs[1])
    node_tree.links.new(transparentBSDF.outputs['BSDF'], mixShader.inputs[2])
    node_tree.links.new(mixShader.outputs['Shader'], grpOut1.inputs['Shader'])
    node_tree.links.new(comNor.outputs['Normal'], grpOut1.inputs['Normal'])
    node_tree.links.new(vMath4.outputs['Vector'], grpOut1.inputs['Diffuse Color'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], grpOut1.inputs['Alpha'])
    node_tree.links.new(emission.outputs['Emission'], grpOut1.inputs['Emission'])

    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Color'], tangentN.inputs['_n RotationMap Color'])
    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Alpha'], tangentN.inputs['_n RotationMap Alpha'])
    node_tree.links.new(tangentN.outputs['Normal'], comNor.inputs['TexNormal'])
    node_tree.links.new(tangentN.outputs['Alpha'], setAlphaMode.inputs['Alpha'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], mixShader.inputs['Fac'])
    node_tree.links.new(tangentN.outputs['Emission Strength'], emission.inputs['Strength'])

    node_tree.links.new(comNor.outputs['Normal'], phongSpec.inputs['Normal'])
    node_tree.links.new(comNor.outputs['Normal'], phongSpec.inputs['-Normal'])
    node_tree.links.new(comNor.outputs['Normal'], flushColor.inputs['Normal'])
    node_tree.links.new(comNor.outputs['Normal'], diffuseBSDF.inputs['Normal'])

    node_tree.links.new(grpIn_fleshFlush.outputs['Flesh Brightness'], flushColor.inputs['Flesh Brightness'])
    node_tree.links.new(grpIn_fleshFlush.outputs['Flush Tone'], flushColor.inputs['Flush Tone'])
    node_tree.links.new(grpIn_fleshFlush.outputs['Flush Tone'], ageDarkening.inputs['Color1'])

    node_tree.links.new(grpIn_age.outputs['AgeMap Color'], ageNormal.inputs['AgeMap Color'])
    node_tree.links.new(grpIn_age.outputs['AgeMap Alpha'], ageNormal.inputs['AgeMap Alpha'])
    node_tree.links.new(ageNormal.outputs['Normal'], comNor.inputs['AgeNormal'])
    node_tree.links.new(ageNormal.outputs['Scar Mask'], ageDarkening.inputs['Fac'])

    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Blend'], setAlphaMode.inputs['IsAlphaModeBlend'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test'], setAlphaMode.inputs['IsAlphaModeTest'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test Value'], setAlphaMode.inputs['AlphaTestValue'])
    node_tree.links.new(grpIn_alphaMode.outputs['Invert Alpha'], setAlphaMode.inputs['Invert'])

    # Hide unused outputs on each Group Input node, so only the sockets
    # relevant to that node's location show up
    for grp_in, keep in (
        (grpIn_diffuse, {'DiffuseMap Color'}),
        (grpIn_rotation, {'RotationMap1 Color', 'RotationMap1 Alpha'}),
        (grpIn_gloss, {'GlossMap Color'}),
        (grpIn_glossAlpha, {'GlossMap Alpha'}),
        (grpIn_palette, {'PaletteMap Color', 'PaletteMap Alpha'}),
        (grpIn_paletteMask, {'PaletteMaskMap Color'}),
        (grpIn_age, {'AgeMap Color', 'AgeMap Alpha'}),
        (grpIn_complexion, {'ComplexionMap Color'}),
        (grpIn_facepaint, {'FacepaintMap Color', 'FacepaintMap Alpha'}),
        (grpIn_palette1, {'Palette1 Hue', 'Palette1 Saturation', 'Palette1 Brightness', 'Palette1 Contrast', 'Palette1 Specular', 'Palette1 Metallic Specular'}),
        (grpIn_fleshFlush, {'Flesh Brightness', 'Flush Tone'}),
        (grpIn_alphaMode, {'Alpha Blend', 'Alpha Test', 'Alpha Test Value', 'Invert Alpha'}),
    ):
        for output in grp_in.outputs:
            if output.name not in keep:
                output.hide = True

    # NOTE: unlike creature_group()/eye_group()/uber_group(), the
    # original skinb() never explicitly hides/unhides any internal
    # sockets -- gamma1's Gamma, phongSpec's MaxSpecPower, and
    # transparentBSDF's Color are all left at their natural
    # visible-by-default state here too, matching that original exactly.

    return node_tree


def hairc_modern_group():
    # type: () -> ShaderNodeTree
    """
    HairC Modern: a variant of hairc_group() for newer hair assets that
    ship with two palettes instead of one. Identical to hairc_group() in
    every respect except the palette source -- HuePixel is driven by
    ChosenPalette (which blends "Palette1 Controls" and "Palette 2
    Controls" by Palette Mask, exactly like garment_group()) instead of a
    single panel feeding HuePixel directly.

    Palette Mask now feeds three consumers: SeparateXYZ (unchanged, for
    the direction/gloss mix), HuePixel (unchanged), and ChosenPalette
    (new) -- the same texture channel doing double duty as both the
    palette-select mask and the direction/gloss mix mask, matching how
    SWTOR packs these maps.
    """
    from .node_group import (
        chosen_palette,
        get_phong_specular,
        hue_pixel,
        normal_and_alpha_from_swizzled_texture,
        set_alpha_mode,
    )

    GROUP_NAME = 'Atroxa SWTOR - HairC Modern Shader'
    if GROUP_NAME in bpy.data.node_groups:
        return bpy.data.node_groups[GROUP_NAME]

    node_tree = bpy.data.node_groups.new(name=GROUP_NAME, type='ShaderNodeTree')

    # Add interface sockets. Image-map order matches the original custom
    # node's fixed UI order: Diffuse, Rotation (Normal), Gloss, Palette,
    # PaletteMask, Direction.
    diffuse_color_socket = node_tree.interface.new_socket('DiffuseMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    diffuse_color_socket.default_value = [1.0, 1.0, 1.0, 1.0]

    # Not consumed internally, always connected from outside regardless --
    # see NODE_OT_add_swtor_shader_group in ops/shaders_menu.py.
    diffuse_alpha_socket = node_tree.interface.new_socket('DiffuseMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    diffuse_alpha_socket.default_value = 1.0
    diffuse_alpha_socket.min_value = 0.0
    diffuse_alpha_socket.max_value = 1.0

    rotation_color_socket = node_tree.interface.new_socket('RotationMap1 Color', in_out='INPUT', socket_type='NodeSocketColor')
    rotation_color_socket.default_value = [0.0, 0.5, 0.0, 1.0]

    rotation_alpha_socket = node_tree.interface.new_socket('RotationMap1 Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    rotation_alpha_socket.default_value = 0.5
    rotation_alpha_socket.min_value = 0.0
    rotation_alpha_socket.max_value = 1.0

    gloss_color_socket = node_tree.interface.new_socket('GlossMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    gloss_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    gloss_alpha_socket = node_tree.interface.new_socket('GlossMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    gloss_alpha_socket.default_value = 0.0
    gloss_alpha_socket.min_value = 0.0
    gloss_alpha_socket.max_value = 1.0

    palette_color_socket = node_tree.interface.new_socket('PaletteMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    palette_alpha_socket = node_tree.interface.new_socket('PaletteMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_alpha_socket.default_value = 0.0
    palette_alpha_socket.min_value = 0.0
    palette_alpha_socket.max_value = 1.0

    palette_mask_color_socket = node_tree.interface.new_socket('PaletteMaskMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    palette_mask_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally (see Diffuse Alpha above).
    palette_mask_alpha_socket = node_tree.interface.new_socket('PaletteMaskMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    palette_mask_alpha_socket.default_value = 1.0
    palette_mask_alpha_socket.min_value = 0.0
    palette_mask_alpha_socket.max_value = 1.0

    direction_color_socket = node_tree.interface.new_socket('DirectionMap Color', in_out='INPUT', socket_type='NodeSocketColor')
    direction_color_socket.default_value = [0.0, 0.0, 0.0, 1.0]

    # Not consumed internally (see Diffuse Alpha above).
    direction_alpha_socket = node_tree.interface.new_socket('DirectionMap Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    direction_alpha_socket.default_value = 1.0
    direction_alpha_socket.min_value = 0.0
    direction_alpha_socket.max_value = 1.0

    # Two full palettes, blended by Palette Mask via ChosenPalette --
    # same as garment_group()
    add_palette_panel(node_tree, "Palette1 Controls", 1, include_metallic_specular=True)
    add_palette_panel(node_tree, "Palette2 Controls", 2, include_metallic_specular=True)

    # Alpha controls
    add_alpha_settings_panel(node_tree)

    # Add output socket
    add_output_socket_if_needed(node_tree)
    add_passthrough_outputs(node_tree)

    # Add and place nodes
    sepXYZ = node_tree.nodes.new(type='ShaderNodeSeparateXYZ')
    sepXYZ.location = (-780.0, 440.0)
    sepXYZ.outputs['X'].hide = True
    sepXYZ.outputs['Y'].hide = True
    sepXYZ.width = 180.0

    huePixel = node_tree.nodes.new(type='ShaderNodeGroup')
    huePixel.location = (-780.0, 300.0)
    huePixel.name = 'HuePixel'
    huePixel.node_tree = hue_pixel()
    huePixel.width = 180.0

    phongSpec = node_tree.nodes.new(type='ShaderNodeGroup')
    phongSpec.location = (-500.0, 300.0)
    phongSpec.node_tree = get_phong_specular()
    phongSpec.width = 180.0

    mixRGB = node_tree.nodes.new(type='ShaderNodeMixRGB')
    mixRGB.location = (-220.0, 300.0)

    vMath1 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath1.location = (40.0, 300.0)
    vMath1.operation = 'ADD'

    diffuseBSDF = node_tree.nodes.new(type='ShaderNodeBsdfDiffuse')
    diffuseBSDF.location = (300.0, 300.0)

    addShader = node_tree.nodes.new(type='ShaderNodeAddShader')
    addShader.location = (560.0, 300.0)

    mixShader = node_tree.nodes.new(type='ShaderNodeMixShader')
    mixShader.location = (800.0, 300.0)

    grpOut1 = node_tree.nodes.new(type='NodeGroupOutput')
    grpOut1.location = (1040.0, 300.0)

    chosenPalette = node_tree.nodes.new(type='ShaderNodeGroup')
    chosenPalette.location = (-1400.0, 460.0)
    chosenPalette.name = 'ChosenPalette'
    chosenPalette.node_tree = chosen_palette()
    chosenPalette.width = 240.0

    tangentN = node_tree.nodes.new(type='ShaderNodeGroup')
    tangentN.location = (-1400.0, -60.0)
    tangentN.node_tree = normal_and_alpha_from_swizzled_texture()
    tangentN.width = 400.0

    norMap = node_tree.nodes.new(type='ShaderNodeNormalMap')
    norMap.location = (-900.0, -60.0)

    vMath2 = node_tree.nodes.new(type='ShaderNodeVectorMath')
    vMath2.location = (-500.0, 100.0)
    vMath2.operation = 'MULTIPLY'

    emission = node_tree.nodes.new(type='ShaderNodeEmission')
    emission.location = (300.0, 80.0)

    transparentBSDF = node_tree.nodes.new(type='ShaderNodeBsdfTransparent')
    transparentBSDF.inputs['Color'].default_value = [1.0, 1.0, 1.0, 1.0]
    transparentBSDF.location = (560.0, -60.0)

    setAlphaMode = node_tree.nodes.new(type='ShaderNodeGroup')
    setAlphaMode.name = "SetAlphaMode"
    setAlphaMode.location = (540.0, 140.0)
    setAlphaMode.node_tree = set_alpha_mode()
    setAlphaMode.width = 180.0

    # Group Input nodes, one per consuming area, each showing only the
    # sockets relevant to that location
    grpIn_diffuse = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_diffuse.location = (-1060.0, 340.0)
    grpIn_diffuse.label = "Diffuse"

    grpIn_rotation = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_rotation.location = (-1700.0, -60.0)
    grpIn_rotation.label = "Rotation"

    grpIn_gloss = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_gloss.location = (-1060.0, 260.0)
    grpIn_gloss.label = "Gloss"

    grpIn_glossAlpha = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_glossAlpha.location = (-500.0, 180.0)
    grpIn_glossAlpha.label = "Gloss Alpha"

    grpIn_palette = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette.location = (-1060.0, 180.0)
    grpIn_palette.label = "Palette"

    grpIn_paletteMask = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_paletteMask.location = (-1700.0, 460.0)
    grpIn_paletteMask.label = "Palette Mask"

    grpIn_direction = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_direction.location = (-780.0, 100.0)
    grpIn_direction.label = "Direction"

    grpIn_palette1and2 = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_palette1and2.location = (-1700.0, 660.0)
    grpIn_palette1and2.label = "Palette 1 & 2 Controls"

    grpIn_alphaMode = node_tree.nodes.new(type='NodeGroupInput')
    grpIn_alphaMode.location = (300.0, -160.0)
    grpIn_alphaMode.label = "Alpha Mode"

    # Link nodes together
    node_tree.links.new(grpIn_diffuse.outputs['DiffuseMap Color'], huePixel.inputs['_d DiffuseMap Color'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], sepXYZ.inputs['Vector'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], huePixel.inputs['_m PaletteMaskMap Color'])
    node_tree.links.new(grpIn_paletteMask.outputs['PaletteMaskMap Color'], chosenPalette.inputs['_m PaletteMaskMap Color'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Color'], huePixel.inputs['_h PaletteMap Color'])
    node_tree.links.new(grpIn_palette.outputs['PaletteMap Alpha'], huePixel.inputs['_h PaletteMap Alpha'])
    node_tree.links.new(grpIn_gloss.outputs['GlossMap Color'], huePixel.inputs['_s GlossMap Color'])

    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Hue'], chosenPalette.inputs['Palette1 Hue'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Saturation'], chosenPalette.inputs['Palette1 Saturation'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Brightness'], chosenPalette.inputs['Palette1 Brightness'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Contrast'], chosenPalette.inputs['Palette1 Contrast'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Specular'], chosenPalette.inputs['Palette1 Specular'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette1 Metallic Specular'], chosenPalette.inputs['Palette1 Metallic Specular'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Hue'], chosenPalette.inputs['Palette2 Hue'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Saturation'], chosenPalette.inputs['Palette2 Saturation'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Brightness'], chosenPalette.inputs['Palette2 Brightness'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Contrast'], chosenPalette.inputs['Palette2 Contrast'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Specular'], chosenPalette.inputs['Palette2 Specular'])
    node_tree.links.new(grpIn_palette1and2.outputs['Palette2 Metallic Specular'], chosenPalette.inputs['Palette2 Metallic Specular'])

    node_tree.links.new(chosenPalette.outputs['Hue'], huePixel.inputs['Hue'])
    node_tree.links.new(chosenPalette.outputs['Saturation'], huePixel.inputs['Saturation'])
    node_tree.links.new(chosenPalette.outputs['Brightness'], huePixel.inputs['Brightness'])
    node_tree.links.new(chosenPalette.outputs['Contrast'], huePixel.inputs['Contrast'])
    node_tree.links.new(chosenPalette.outputs['Specular'], huePixel.inputs['Specular'])
    node_tree.links.new(chosenPalette.outputs['Metallic Specular'], huePixel.inputs['Metallic Specular'])

    node_tree.links.new(sepXYZ.outputs['Z'], mixRGB.inputs['Fac'])
    node_tree.links.new(huePixel.outputs['Diffuse Color'], vMath1.inputs[0])
    node_tree.links.new(huePixel.outputs['Specular Color'], phongSpec.inputs['Specular Color'])
    node_tree.links.new(huePixel.outputs['Specular Color'], vMath2.inputs[0])
    node_tree.links.new(phongSpec.outputs['Specular'], mixRGB.inputs['Color1'])
    node_tree.links.new(mixRGB.outputs['Color'], vMath1.inputs[1])
    node_tree.links.new(vMath1.outputs['Vector'], diffuseBSDF.inputs['Color'])
    node_tree.links.new(vMath1.outputs['Vector'], emission.inputs['Color'])
    node_tree.links.new(diffuseBSDF.outputs['BSDF'], addShader.inputs[0])
    node_tree.links.new(addShader.outputs['Shader'], mixShader.inputs[1])
    node_tree.links.new(mixShader.outputs['Shader'], grpOut1.inputs['Shader'])
    node_tree.links.new(norMap.outputs['Normal'], grpOut1.inputs['Normal'])
    node_tree.links.new(vMath1.outputs['Vector'], grpOut1.inputs['Diffuse Color'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], grpOut1.inputs['Alpha'])
    node_tree.links.new(emission.outputs['Emission'], grpOut1.inputs['Emission'])

    node_tree.links.new(grpIn_glossAlpha.outputs['GlossMap Alpha'], phongSpec.inputs['Specular Alpha'])

    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Color'], tangentN.inputs['_n RotationMap Color'])
    node_tree.links.new(grpIn_rotation.outputs['RotationMap1 Alpha'], tangentN.inputs['_n RotationMap Alpha'])
    node_tree.links.new(tangentN.outputs['Normal'], norMap.inputs['Color'])
    node_tree.links.new(norMap.outputs['Normal'], phongSpec.inputs['Normal'])
    node_tree.links.new(norMap.outputs['Normal'], phongSpec.inputs['-Normal'])
    node_tree.links.new(norMap.outputs['Normal'], diffuseBSDF.inputs['Normal'])

    node_tree.links.new(grpIn_direction.outputs['DirectionMap Color'], vMath2.inputs[1])
    node_tree.links.new(vMath2.outputs['Vector'], mixRGB.inputs['Color2'])

    node_tree.links.new(tangentN.outputs['Alpha'], setAlphaMode.inputs['Alpha'])
    node_tree.links.new(setAlphaMode.outputs['Alpha'], mixShader.inputs['Fac'])
    node_tree.links.new(tangentN.outputs['Emission Strength'], emission.inputs['Strength'])
    node_tree.links.new(emission.outputs['Emission'], addShader.inputs[1])
    node_tree.links.new(transparentBSDF.outputs['BSDF'], mixShader.inputs[2])

    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Blend'], setAlphaMode.inputs['IsAlphaModeBlend'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test'], setAlphaMode.inputs['IsAlphaModeTest'])
    node_tree.links.new(grpIn_alphaMode.outputs['Alpha Test Value'], setAlphaMode.inputs['AlphaTestValue'])
    node_tree.links.new(grpIn_alphaMode.outputs['Invert Alpha'], setAlphaMode.inputs['Invert'])

    # Hide unused outputs on each Group Input node, so only the sockets
    # relevant to that node's location show up
    for grp_in, keep in (
        (grpIn_diffuse, {'DiffuseMap Color'}),
        (grpIn_rotation, {'RotationMap1 Color', 'RotationMap1 Alpha'}),
        (grpIn_gloss, {'GlossMap Color'}),
        (grpIn_glossAlpha, {'GlossMap Alpha'}),
        (grpIn_palette, {'PaletteMap Color', 'PaletteMap Alpha'}),
        (grpIn_paletteMask, {'PaletteMaskMap Color'}),
        (grpIn_direction, {'DirectionMap Color'}),
        (grpIn_palette1and2, {
            'Palette1 Hue', 'Palette1 Saturation', 'Palette1 Brightness', 'Palette1 Contrast',
            'Palette1 Specular', 'Palette1 Metallic Specular',
            'Palette2 Hue', 'Palette2 Saturation', 'Palette2 Brightness', 'Palette2 Contrast',
            'Palette2 Specular', 'Palette2 Metallic Specular',
        }),
        (grpIn_alphaMode, {'Alpha Blend', 'Alpha Test', 'Alpha Test Value', 'Invert Alpha'}),
    ):
        for output in grp_in.outputs:
            if output.name not in keep:
                output.hide = True

    # Hide unlinked node sockets, matching hairc()'s original tidiness
    # pass, for the nodes with tunable constants not promoted to the
    # group interface
    for node in (norMap, transparentBSDF):
        for socket in node.inputs:
            if not socket.is_linked:
                socket.hide = True
        for socket in node.outputs:
            if not socket.is_linked:
                socket.hide = True

    # Re-expose the tunable constants that were deliberately kept visible
    # on the old custom node's internal tree
    norMap.inputs['Strength'].hide = False
    transparentBSDF.inputs['Color'].hide = False

    return node_tree