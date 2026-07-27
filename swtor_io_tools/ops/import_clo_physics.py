# <pep8 compliant>

"""
"Build Cloth Physics" mode for the .clo importer: sets up Blender's Cloth Physics on the
matching mesh using the .clo's own data -- pins from invertedSimWeight, stiffness vertex
groups averaged from per-edge springStrength, gravity -- rather than building a skeleton
at all (see import_clo_rig.py for that mode).

Not yet implemented. Right tool for mesh-shaped .clo files specifically: dense 2D cloth
regions where no bone hierarchy, however cleverly rooted, can represent material that
genuinely stretches between two independently-moving anchors (verified against a real
shawl's pinned yoke during import_clo_rig.py's development -- see its module docstring
and the chat history for the full reasoning on chain-shaped vs. mesh-shaped files).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bpy.types import Context, Operator
    from ..types.clo import Cloth


def build(operator, context, cloth, filepath):
    # type: (Operator, Context, Cloth, str) -> bool
    operator.report({'ERROR'}, "Build Cloth Physics isn't implemented yet -- switch Mode to Build Rig.")
    return False
