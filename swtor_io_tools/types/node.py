# <pep8 compliant>

# NODE_OT_ngroup_edit below is shared: it powers double-click-to-edit for
# both the current native ShaderNodeGroup shaders (node_tree.py) and the
# deprecated ShaderNodeHeroEngine custom node (node_deprecated.py), so it
# stays here rather than moving with the rest of the deprecated system.

from typing import Set

from bpy.props import BoolProperty
from bpy.types import Context, Operator


class NODE_OT_ngroup_edit(Operator):
    bl_label = "Edit Group"
    bl_description = "Edit Hero node group"
    bl_idname = "node.ngroup_edit"
    bl_options = {'REGISTER', 'UNDO'}

    exit: BoolProperty(name="Exit", description="", default=False)

    @classmethod
    def poll(cls, context):
        # type: (Context) -> bool
        space = context.space_data

        if space.type != 'NODE_EDITOR':
            return False
        if space.tree_type not in {"CompositorNodeTree", "ShaderNodeTree", "TextureNodeTree"}:
            return False

        return True

    def execute(self, context):
        # type: (Context) -> Set[str]
        space = context.space_data

        if hasattr(context, "node"):
            node = context.node
        else:
            node = getattr(context, "active_node", None)

        valid_node_groups = [
            "ShaderNodeHeroEngine",  # TODO: DEPRECATED -- remove this entry once the ShaderNodeHeroEngine system is dropped
            "CompositorNodeGroup",
            "ShaderNodeGroup",
            "TextureNodeGroup"
        ]

        if node and node.bl_idname in valid_node_groups and not self.exit:
            space.path.append(node_tree=node.node_tree, node=node)
        else:
            space.path.pop()

        return {'FINISHED'}