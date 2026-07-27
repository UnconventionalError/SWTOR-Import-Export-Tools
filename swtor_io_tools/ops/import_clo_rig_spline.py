# <pep8 compliant>

"""
Spline IK / Bendy Bone layer for "Build Rig" mode -- applied on top of a finished
armature from import_clo_rig.py's build(). Deliberately kept separate from that module:
this only needs the FINISHED hierarchy (which unbranched root-to-leaf paths exist) to
decide where to lay a curve and add constraints, none of the Dijkstra/endParticle/pruning
machinery that makes up most of import_clo_rig.py's complexity -- a genuinely separate
concern, and one that shouldn't make that module grow any further.

Not yet implemented. Planned approach: for each unbranched root-to-leaf path in the built
armature, generate a Bezier curve from the chain's own rest positions (so it opens already
in the correct game rest pose), add a Spline IK constraint on the leaf bone targeting it,
and optionally enable Bendy Bone segments for smoother in-between curvature. A branch
point (see import_clo_rig.py's primary/non-primary child split) means multiple curves
sharing a trunk, not one curve per armature.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bpy.types import Context, Object, Operator
    from ..types.clo import Cloth


def apply(operator, context, armature_ob, effective_parent_of, cloth):
    # type: (Operator, Context, Object, dict, Cloth) -> None
    operator.report(
        {'INFO'},
        "Spline IK / Bendy Bone segments aren't implemented yet -- the plain FK rig is unaffected.",
    )
