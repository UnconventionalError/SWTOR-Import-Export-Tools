# <pep8 compliant>

"""
.fxspec -- data/logic layer.

No bpy dependency in this module -- everything here is plain Python,
testable standalone, matching types/area.py's own split (ops/import_fxspec.py
is where Blender object/collection creation happens, once that lands).

Scope for this pass (see fxspec-importer-handoff.md and the design
discussion that followed it):

  - Parse the UTF-16LE marshal-node XML into a plain nested structure,
    porting fxspec-read.js's own parser exactly (same encoding/BOM/
    trailing-null handling, same element shape).
  - Resolve the named attachment graph across _fxEmitterList (dummy/
    anchor entries only) and _fxModelList together, in one pass --
    NOT the two-phase split the handoff doc originally sketched, since
    real data shows models routinely attach through dummy emitters
    (confirmed against czk_item_planter.fxspec and, much more deeply,
    mtx_gearfx_sovex_chest_v01_bfs.fxspec's multi-level chains).
  - _fxList entries are NOT recursive containers -- confirmed against
    fxspec-render.js's own header comment ("_fxList entries are nested
    timing groups: they render nothing but other nodes reference by
    name") and by inspecting real examples: every <e> under _fxList is
    shaped like _fxMasterGroup (name + timer/state fields only), never
    containing its own _fxEmitterList/_fxModelList/_fxLightList. Other
    elements reference a group by name via _fxOwnerName/_fxStartFxName/
    _fxStopFxName purely for start/stop sequencing -- irrelevant to a
    static Blender scene, so this module parses _fxList but never walks
    into it as a placement source.
  - _fxAttachRelative is parsed and then discarded by the shipped
    client per fxspec-docs.js ("Parsed and then discarded by the
    shipped client, which always snaps -- 5,686 emitters author true
    and none of them behave differently") -- so the resolver below
    never branches on it, matching real client behaviour rather than
    the authored-but-inert field.
  - A node's resolved pos/rot/scale is a LOCAL offset relative to
    whatever it attaches to, not a flattened world-space value --
    confirmed against fxspec-render.js's resolveAttach(), whose CASTER/
    TARGET special-casing only exists because those aren't real scene-
    graph nodes; attaching to a named locator otherwise is nothing more
    than "my local offset is _fxAttachPosition/_fxAttachRotation/
    _fxScale, and my parent is that locator", with world placement
    coming from an actual parent-child chain elsewhere in the renderer.
    That maps directly onto Blender's own object parenting, so this
    resolver returns local offsets + a parent reference rather than
    composed matrices -- ops/import_fxspec.py can parent Empties/
    objects directly and let Blender do the composition, the same way
    a bone attachment can be left to a Copy Transform constraint
    instead of being baked.
"""

import math as _math
import re
from typing import Dict, List, Optional
from xml.etree import ElementTree

_DEG2RAD = _math.pi / 180.0


# ---------------------------------------------------------------------------
# Known dummy/anchor .prt names (handoff §"Real _fxEmitterList entry")
# ---------------------------------------------------------------------------

# Read off fxspec.js's own UI-dimming logic (the browser greys these out
# specifically because they carry no visible particles -- pure attachment
# points). Not a canonical source per the handoff's own open question --
# Crunch flagged wanting to revisit this list once real data is visible in
# Blender, so keep this easy to extend rather than baking the assumption in
# deeper than a single set literal.
DUMMY_EMITTER_RESOURCE_NAMES = {
    "fxtemplate_dummy_point.prt",
    "fxtemplate_dummy_rot314.prt",
    "fxtemplate_dummy_negrot314.prt",
    "fxtemplate_jfoote_randrot_120.prt",
}

# The two virtual roots a chain can terminate at. Neither is a real graph
# node -- see resolveAttach()'s own comment on why CASTER/TARGET need
# special-casing instead of just being named locators.
VIRTUAL_ROOTS = {"CASTER", "TARGET"}

# Values meaning "no attachment named" across _fxStartLoc/_fxAttachTo --
# ported from fxspec-render.js's own checks (anchorActor/anchorModel).
_NO_ATTACHMENT_VALUES = {"", "DEFAULT", "NOTHING"}


class FxSpecParseError(Exception):
    """
    Raised when a .fxspec file doesn't match the expected
    <nodeWClasses><classes><class>_FxSpec</class></classes><marshalData>...
    shape -- ported 1:1 from fxspec-read.js's own assert() calls, which
    is the closest thing to a spec for what "valid" means here (no
    separate format documentation exists beyond that reader).
    """


# ---------------------------------------------------------------------------
# Parsing (ports fxspec-read.js)
# ---------------------------------------------------------------------------

def _decode_fxspec_text(raw_bytes):
    # type: (bytes) -> str
    """
    UTF-16LE detection matches fxspec-read.js exactly: the second byte
    of a leading '<' is 0 in UTF-16LE with no BOM, or a FF FE BOM leads.
    Every real .fxspec sample seen so far is UTF-16LE with no BOM (raw
    bytes start `3C 00 6E 00` -- '<' then 'n'), but the FF FE case is
    kept since fxspec-read.js explicitly guards for it.

    Trailing NUL and a leading BOM character (if decoding did produce
    one) are stripped the same way the reference parser does -- some
    shipped files have a trailing zero byte that would otherwise break
    XML parsing.
    """
    is_utf16 = len(raw_bytes) >= 2 and (
        raw_bytes[1] == 0 or (raw_bytes[0] == 0xFF and raw_bytes[1] == 0xFE)
    )
    text = raw_bytes.decode("utf-16-le" if is_utf16 else "utf-8")
    if text and text[0] == "\ufeff":
        text = text[1:]
    if text.endswith("\x00"):
        text = text[:-1]
    return text


def _strip_element_whitespace(elem):
    # type: (ElementTree.Element) -> None
    """
    Ports fxspecStripElementWhitespace(): ElementTree already discards
    inter-element whitespace-only text by default when there's no
    mixed content, UNLESS an element has both child elements and stray
    whitespace text alongside them (pretty-printed input) -- so this
    walks the tree and blanks `.text`/`.tail` on any element that has
    child elements, leaving leaf text (a tag's own value, e.g.
    <f name="_fxAttachBone">leftweapon</f>) untouched. Matches the
    reference comment's own reasoning: indentation is not content.
    """
    if len(elem) > 0:
        if elem.text is not None and not elem.text.strip():
            elem.text = None
        for child in elem:
            if child.tail is not None and not child.tail.strip():
                child.tail = None
            _strip_element_whitespace(child)


class FxNode:
    """
    One parsed <f>/<e> element's content, in the shape
    parseContent() in fxspec-read.js produces: either a leaf string,
    or a list of (key, FxNode) pairs for a container. `key` is the
    field's own `name` attribute for <f>, or the paired <k> element's
    text (falling back to a stringified index) for <e> entries inside
    a list -- ported for shape-fidelity with the reference tool even
    though this addon doesn't currently use keyed <e> lists (only seen
    in _fx3DSoundList per the reference reader's own comment, which is
    out of scope here).
    """
    __slots__ = ("text", "children")

    def __init__(self, text=None, children=None):
        self.text = text                # type: Optional[str]
        self.children = children        # type: Optional[List[tuple]]

    def get(self, name):
        # type: (str) -> Optional["FxNode"]
        """First child FxNode whose key == `name`, or None."""
        if not self.children:
            return None
        for key, value in self.children:
            if key == name:
                return value
        return None

    def get_text(self, name, default=""):
        # type: (str, str) -> str
        """Convenience for a leaf field read as plain text."""
        child = self.get(name)
        if child is None or child.text is None:
            return default
        return child.text

    def entries(self):
        # type: () -> List["FxNode"]
        """
        All direct <e> children's own FxNodes, in document order --
        for reading a list field like _fxEmitterList/_fxModelList.
        Every list field is present even when empty (handoff doc's own
        note: "presence of the tag tells you nothing"), so this simply
        returns [] for an empty list rather than distinguishing that
        from a missing field.
        """
        if not self.children:
            return []
        return [value for key, value in self.children if key.isdigit() or key == "e"]


def _parse_content(elem):
    # type: (ElementTree.Element) -> FxNode
    """Ports parseContent() from fxspec-read.js."""
    kids = list(elem)
    if len(kids) == 0:
        return FxNode(text=(elem.text or "").strip() if elem.text else "")
    if len(kids) == 0:
        pass  # unreachable, kept for structural parity with the JS branch

    # Leaf text case: exactly one child in the ORIGINAL sense means "just a
    # text node" in the JS DOM model, which ElementTree represents as
    # elem.text with no child elements at all (already handled above).
    # ElementTree never produces a text-only Element with a length here.

    out = []
    for index, child in enumerate(kids):
        tag = child.tag
        if tag == "e":
            key = str(index)
            out.append((key, _parse_content(child)))
        elif tag == "f":
            name = child.attrib.get("name", "")
            out.append((name, _parse_content(child)))
        elif tag == "k":
            # Paired <k>/<e> lists (seen only in _fx3DSoundList per the
            # reference reader) -- out of scope; the <k> itself carries no
            # placement data we need, so it's dropped rather than paired.
            continue
        else:
            out.append(("unknown tagName " + tag, _parse_content(child)))
    return FxNode(children=out)


def parse_fxspec(raw_bytes):
    # type: (bytes) -> FxNode
    """
    Parses the raw bytes of a .fxspec file (as read straight off disk,
    no decoding done by the caller) into the root <node>'s own FxNode
    -- i.e. the return value's .get("_fxEmitterList") etc. work
    directly, matching marshalData.firstChild in the reference reader.

    Raises FxSpecParseError on anything that doesn't match the
    <nodeWClasses><classes><class>_FxSpec</class>...<marshalData><node>
    shape fxspec-read.js itself asserts on.
    """
    text = _decode_fxspec_text(raw_bytes)
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        raise FxSpecParseError("Could not parse .fxspec XML: %s" % e)

    _strip_element_whitespace(root)

    if root.tag != "nodeWClasses":
        raise FxSpecParseError("Expected a <nodeWClasses> root node, got <%s>" % root.tag)
    if len(root) != 2 or root[0].tag != "classes":
        raise FxSpecParseError("Expected a <classes> node under <nodeWClasses>")
    classes_elem = root[0]
    if len(classes_elem) == 0 or classes_elem[0].tag != "class" or (classes_elem[0].text or "").strip() != "_FxSpec":
        raise FxSpecParseError("Expected a <class>_FxSpec</class>")
    if root[1].tag != "marshalData":
        raise FxSpecParseError("Expected a <marshalData> node under <nodeWClasses>")
    marshal_data = root[1]
    if len(marshal_data) != 1 or marshal_data[0].tag != "node":
        raise FxSpecParseError("Expected a single <node> under <marshalData>")

    return _parse_content(marshal_data[0])


# ---------------------------------------------------------------------------
# Attachment graph resolution
# ---------------------------------------------------------------------------

class FxAttachEntry:
    """
    One resolved _fxEmitterList or _fxModelList entry, reduced to what
    ops/import_fxspec.py needs to place it in Blender: its own name,
    what it's parented to (a name, or one of VIRTUAL_ROOTS), and its
    LOCAL offset relative to that parent -- see this module's own
    docstring for why local-offset-plus-parent-reference is the right
    shape here rather than a flattened world transform.
    """
    __slots__ = (
        "name", "kind", "resource", "is_dummy",
        "parent_name", "attach_bone",
        "local_position", "local_rotation", "local_scale",
    )

    def __init__(self, name, kind, resource, is_dummy, parent_name, attach_bone,
                 local_position, local_rotation, local_scale):
        self.name = name                    # this entry's own _fxName
        self.kind = kind                    # "emitter" | "model"
        self.resource = resource            # _fxResourceName, raw (backslash) form
        self.is_dummy = is_dummy            # emitter only -- resource is a known dummy/anchor .prt
        self.parent_name = parent_name      # another entry's name, or "CASTER"/"TARGET"
        self.attach_bone = attach_bone      # non-empty only when parent_name is CASTER/TARGET
                                             # AND a bone was actually authored -- see
                                             # resolveAttach()'s own "only an actor has one" note;
                                             # a bone name on a locator-attached entry is stale
                                             # authoring data and deliberately dropped here too.
        self.local_position = local_position    # (x, y, z), SWTOR units, relative to parent_name
        self.local_rotation = local_rotation    # (x, y, z) degrees, ALREADY converted into
                                                 # Blender's default 'XYZ' Euler convention -- see
                                                 # _yaw_pitch_roll_to_xyz_euler_degrees(). Simplified
                                                 # relative to the reference renderer's own
                                                 # "boneComp" case (an authored-but-unresolved bone
                                                 # name tips the node into the bone's own axes
                                                 # instead of this converted value) -- that only
                                                 # matters once ops/import_fxspec.py is actually
                                                 # trying to resolve a bone against a selected
                                                 # armature at Blender-build time, which this
                                                 # parse-time module has no visibility into.
        self.local_scale = local_scale          # (x, y, z)


def _parse_vector3(text, default=(0.0, 0.0, 0.0)):
    # type: (str, tuple) -> tuple
    """
    Parses SWTOR's "(x,y,z)" or bare "x,y,z" vector text (both forms
    appear in real files -- e.g. _fxScale is authored as ".7,.7,.7"
    with no parens in czk_item_planter.fxspec, while _fxAttachPosition
    is always parenthesized). Returns `default` for empty/unparsable
    text rather than raising -- a great many fields legitimately have
    no authored value at all.
    """
    if not text:
        return default
    cleaned = text.strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    parts = [p.strip() for p in cleaned.split(",")]
    if len(parts) != 3:
        return default
    try:
        return tuple(float(p) for p in parts)
    except ValueError:
        return default


def _resource_is_dummy(resource):
    # type: (str) -> bool
    """
    True if `resource` (raw _fxResourceName, possibly with a
    \\art\\... path prefix) names one of DUMMY_EMITTER_RESOURCE_NAMES.
    Matched on the filename only, case-insensitive -- real dummy
    references are seen bare (e.g. "fxtemplate_dummy_point.prt") while
    real particle references carry a full path, but nothing guarantees
    a dummy reference is never path-qualified, so this doesn't rely on
    the bare-vs-path distinction to decide.
    """
    if not resource:
        return False
    filename = re.split(r"[\\/]", resource.strip())[-1].lower()
    return filename in DUMMY_EMITTER_RESOURCE_NAMES


def _yaw_pitch_roll_to_xyz_euler_degrees(deg):
    # type: (tuple) -> tuple
    """
    Converts an authored (_fxRotation/_fxAttachRotation) degree triple
    from SWTOR's own yaw/pitch/roll convention into the equivalent
    triple in Blender's default 'XYZ' Euler rotation_mode.

    Ported from fxspec-render.js's fxrYprEuler()/fxrComposeEuler():
    the authored triple is (x=pitch, y=yaw, z=roll), composed by the
    game as Ry(yaw) * Rx(pitch) * Rz(roll) -- roll first, then pitch,
    then yaw. Every transform downstream of that one boundary in the
    reference renderer instead uses Rz * Ry * Rx (pitch first, then
    yaw, then roll), which is exactly what Blender's intrinsic 'XYZ'
    Euler mode means (rotate about local X, then the once-rotated
    local Y, then the twice-rotated local Z) -- so once converted into
    that convention here, ops/import_fxspec.py can assign the result
    straight to rotation_euler with the default rotation_mode, no
    further conversion needed (unlike types/area.py's own 'ZXY' case,
    which is a different composition entirely).

    The two conventions only disagree when a roll is authored alongside
    a nonzero pitch or yaw (confirmed against all four real sample
    files: every nonzero rotation seen has either roll == 0, or both
    pitch == 0 and yaw == 0 -- so the cheap shortcut below already
    covers every case observed so far, but the general composition is
    still implemented rather than left as a silent gap for whatever
    file eventually combines all three).
    """
    x, y, z = (deg[0] * _DEG2RAD, deg[1] * _DEG2RAD, deg[2] * _DEG2RAD)
    if z == 0.0 or (x == 0.0 and y == 0.0):
        return (deg[0], deg[1], deg[2])

    def _mat3(r):
        cx, sx = _math.cos(r[0]), _math.sin(r[0])
        cy, sy = _math.cos(r[1]), _math.sin(r[1])
        cz, sz = _math.cos(r[2]), _math.sin(r[2])
        return (
            cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx,
            sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx,
            -sy, cy * sx, cy * cx,
        )

    a = _mat3((x, y, 0.0))
    b = _mat3((0.0, 0.0, z))
    c = [0.0] * 9
    for i in range(3):
        for j in range(3):
            c[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j]

    out_x = _math.atan2(c[7], c[8])
    out_y = _math.asin(max(-1.0, min(1.0, -c[6])))
    out_z = _math.atan2(c[3], c[0])
    return (_math.degrees(out_x), _math.degrees(out_y), _math.degrees(out_z))


def _read_attach_entries(root, list_field, kind):
    # type: (FxNode, str, str) -> List[FxAttachEntry]
    """
    Reads every <e> under `list_field` (either "_fxEmitterList" or
    "_fxModelList") into an FxAttachEntry with its RAW attach fields
    still in place (parent_name may be empty/DEFAULT/NOTHING, not yet
    resolved to a real parent) -- resolve_attach_graph() does the
    actual chain-following pass afterwards, once every entry in the
    file is known by name.
    """
    entries = []
    list_node = root.get(list_field)
    if list_node is None:
        return entries

    for e in list_node.entries():
        name = e.get_text("_fxName")
        if not name:
            # Every real sample has a name on every entry; an unnamed
            # entry can't participate in the attach graph (nothing can
            # reference it, and it can't report where it itself attaches
            # to anything meaningful downstream), so skip it rather than
            # inventing a synthetic key.
            continue

        resource = e.get_text("_fxResourceName")
        is_dummy = kind == "emitter" and _resource_is_dummy(resource)

        start_loc = (e.get_text("_fxStartLoc", "DEFAULT") or "DEFAULT").strip().upper()
        attach_to_raw = (e.get_text("_fxAttachTo") or "").strip().upper()
        attached = attach_to_raw not in ("", "NOTHING")

        if attached:
            parent_name = attach_to_raw
            local_position = _parse_vector3(e.get_text("_fxAttachPosition"))
            local_rotation = _yaw_pitch_roll_to_xyz_euler_degrees(
                _parse_vector3(e.get_text("_fxAttachRotation"))
            )
            # Only an actor (CASTER/TARGET) has bones to name -- per
            # resolveAttach()'s own comment, a bone authored on an entry
            # attached to a plain locator is stale data left over from an
            # earlier parent, and honouring it drags the element onto that
            # bone AND tips it into the bone's own axes (the reference
            # implementation's own "Victor's Trailblazer" example of what
            # goes wrong if this isn't filtered). Dropped here rather than
            # left for ops/import_fxspec.py to remember to ignore.
            attach_bone = e.get_text("_fxAttachBone").strip() if attach_to_raw in VIRTUAL_ROOTS else ""
        else:
            # Not attached to anything -- falls back to _fxStartLoc, same
            # as resolveAttach()'s own startLoc pass. DEFAULT with a bare
            # _fxPosition (rather than CASTER/TARGET) isn't handled here --
            # not seen in any of the four real samples, and Area
            # Assembler's own placeable-instance context has no notion of
            # a raw world position independent of the .fxp element's own
            # transform, so it's treated the same as "no parent named" and
            # falls through to CASTER, matching resolveAttach()'s own
            # final else-branch ("named task anchor: approximate with caster").
            parent_name = start_loc if start_loc in VIRTUAL_ROOTS else "CASTER"
            local_position = _parse_vector3(e.get_text("_fxStartLocOffset"))
            local_rotation = _yaw_pitch_roll_to_xyz_euler_degrees(
                _parse_vector3(e.get_text("_fxRotation"))
            )
            attach_bone = ""

        local_scale = _parse_vector3(e.get_text("_fxScale"), default=(1.0, 1.0, 1.0))

        entries.append(FxAttachEntry(
            name=name, kind=kind, resource=resource, is_dummy=is_dummy,
            parent_name=parent_name, attach_bone=attach_bone,
            local_position=local_position, local_rotation=local_rotation,
            local_scale=local_scale,
        ))
    return entries


def resolve_attach_graph(root):
    # type: (FxNode) -> Dict[str, FxAttachEntry]
    """
    Reads _fxEmitterList (dummy/anchor entries only -- real particle
    emitters are parsed for completeness but filtered out of the
    returned dict, see below) and _fxModelList together into one name-
    keyed graph, per the "models and dummy emitters can't be resolved
    separately" finding -- a model's own parent_name may point at a
    dummy emitter's name, which only this combined pass can resolve.

    Returns a dict from UPPERCASED entry name -> FxAttachEntry. Real
    (non-dummy) emitter entries ARE included here even though
    ops/import_fxspec.py won't build them into anything visible yet --
    a model or dummy CAN legally name a real emitter as its attach
    target (unusual, not seen in the four real samples, but nothing in
    the format rules it out), and dropping real emitters from the graph
    entirely would silently break that chain instead of just producing
    an emitter-shaped node with nothing built for it.

    Does NOT resolve cycles or missing names into a final parent chain
    -- callers walk .parent_name themselves (see
    resolve_final_parent() below) so the graph-building pass here stays
    a simple, side-effect-free read of the file.
    """
    graph = {}
    for entry in _read_attach_entries(root, "_fxEmitterList", "emitter"):
        graph[entry.name.upper()] = entry
    for entry in _read_attach_entries(root, "_fxModelList", "model"):
        graph[entry.name.upper()] = entry
    return graph


def resolve_final_parent(graph, entry_name, max_depth=64):
    # type: (Dict[str, FxAttachEntry], str, int) -> str
    """
    Walks `graph` from `entry_name` up through .parent_name references
    until it reaches "CASTER", "TARGET", or a name missing from the
    graph entirely (a dangling/unresolvable reference -- falls back to
    "CASTER", matching resolveAttach()'s own dangling-locator fallback
    to StartLoc, which itself ultimately falls back to CASTER).

    This is ONLY for reporting/diagnostics (e.g. warning about a chain
    that couldn't be resolved, or deciding whether an entry ultimately
    roots at CASTER vs TARGET for CASTER/TARGET-mode placement) --
    ops/import_fxspec.py should still parent each Blender object to its
    own IMMEDIATE parent_name (one level), not to this fully-walked
    root, so the natural parenting chain in Blender reproduces the same
    local-offset composition the real client does.

    A cycle (an entry that eventually names itself) also falls back to
    "CASTER" once `max_depth` is exceeded, rather than looping forever
    -- matches resolveAttach()'s own cycle guard (a `stack` of visited
    names), just implemented as a depth cap instead of a visited-set,
    since we only need the terminal root here, not the exact point of
    the cycle.
    """
    current = entry_name.upper()
    for _ in range(max_depth):
        if current in VIRTUAL_ROOTS:
            return current
        entry = graph.get(current)
        if entry is None:
            return "CASTER"
        current = entry.parent_name.upper()
    return "CASTER"
