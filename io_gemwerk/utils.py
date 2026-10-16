"""Coordinate conversion and Blender version helpers.

GEM is left-handed (SOEdit draws everything with glScalef(1,-1,1)), Blender is
right-handed. Data is mirrored on Y when it enters Blender and mirrored back on
export: points p' = S p, matrices M' = S M S with S = diag(1,-1,1). The mirror
also turns the game's clockwise triangles into Blender's counter-clockwise ones,
so triangle index order is kept as is.
"""

import bpy
from mathutils import Matrix, Vector

S = Matrix.Diagonal((1.0, -1.0, 1.0, 1.0))


def gem_to_matrix(m):
    """12 floats in GEM row order -> Blender 4x4 matrix in Blender space."""
    g = Matrix((
        (m[0], m[3], m[6], m[9]),
        (m[1], m[4], m[7], m[10]),
        (m[2], m[5], m[8], m[11]),
        (0.0, 0.0, 0.0, 1.0),
    ))
    return S @ g @ S


def matrix_to_gem(mat):
    """Blender 4x4 matrix (Blender space) -> 12 floats in GEM row order."""
    g = S @ mat @ S
    out = []
    for row in range(3):           # GEM row r = Blender column r
        out += [g[0][row], g[1][row], g[2][row]]
    out += [g[0][3], g[1][3], g[2][3]]
    return tuple(_clean(v) for v in out)


def _clean(v):
    if abs(v) < 1e-6:
        return 0.0
    for t in (1.0, -1.0):
        if abs(v - t) < 1e-6:
            return t
    return float(v)


def gem_point(p):
    return (p[0], -p[1], p[2])


def point_to_gem(p):
    return (p[0], -p[1], p[2])


def gem_name(obj):
    """Name of the bone/volume/shape in the game file.

    The stored game name wins while the object keeps it (Blender may add .001
    suffixes or cut long names); once the user renames the object, its name wins.
    """
    g = getattr(obj, "gem", None)
    gn = g.game_name if g is not None else ""
    if gn:
        base = obj.name
        if len(base) > 4 and base[-4] == "." and base[-3:].isdigit():
            base = base[:-4]
        if base == gn or gn.startswith(base) and len(obj.name) >= 60:
            return gn
    return obj.name


def find_model_root(obj):
    while obj is not None:
        if obj.gem.role == "MODEL":
            return obj
        if obj.gem.role == "VIEW" and obj.gem.owner is not None and obj.parent is None:
            obj = obj.gem.owner
            continue
        obj = obj.parent
    return None


def iter_children(obj):
    """Children of obj in the order they had in the file (gem.order), then by name."""
    kids = list(obj.children)
    kids.sort(key=lambda o: (o.gem.order, o.name))
    return kids


def link_to(coll, obj):
    if obj.name not in coll.objects:
        coll.objects.link(obj)


# Animation: F-Curves on 4.2 (legacy actions) and 4.4+ (slotted actions)

def action_fcurves(obj, action):
    """Assign action to obj and return the F-Curve collection to fill."""
    ad = obj.animation_data or obj.animation_data_create()
    ad.action = action
    if hasattr(action, "layers"):
        slot = getattr(ad, "action_slot", None)
        if slot is None:
            slot = action.slots[0] if len(action.slots) else action.slots.new(id_type="OBJECT", name=obj.name)
            ad.action_slot = slot
        layer = action.layers[0] if len(action.layers) else action.layers.new("Layer")
        strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type="KEYFRAME")
        return strip.channelbag(slot, ensure=True).fcurves
    return action.fcurves


def all_fcurves(obj):
    ad = obj.animation_data
    if not ad or not ad.action:
        return []
    action = ad.action
    if hasattr(action, "layers"):
        slot = getattr(ad, "action_slot", None)
        out = []
        for layer in action.layers:
            for strip in layer.strips:
                if slot is None:
                    continue
                cb = strip.channelbag(slot)
                if cb:
                    out += list(cb.fcurves)
        return out
    return list(action.fcurves)


def add_fcurve(fcurves, path, index, frames, values, interpolation="LINEAR", group=None):
    fc = fcurves.find(path, index=index)
    if fc is None:
        if group is None:
            fc = fcurves.new(path, index=index)
        elif "group_name" in (fcurves.new.__doc__ or ""):     # 4.4+ channelbag API
            fc = fcurves.new(path, index=index, group_name=group)
        else:
            fc = fcurves.new(path, index=index, action_group=group)
    kp = fc.keyframe_points
    kp.add(len(frames))
    co = []
    for f, v in zip(frames, values):
        co += (f, v)
    kp.foreach_set("co", co)
    kp.foreach_set("interpolation", [_INTERP[interpolation]] * len(frames))
    fc.update()
    return fc


_INTERP = {}


def _init_interp():
    enum = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items
    for i, item in enumerate(enum):
        _INTERP[item.identifier] = item.value


def set_parent(child, parent):
    """Parent without an inverse matrix so matrix_basis is the local transform."""
    child.parent = parent
    child.matrix_parent_inverse = Matrix.Identity(4)


def local_matrix(obj):
    """obj transform relative to its parent object (independent of parent inverse)."""
    if obj.parent is None:
        return obj.matrix_world.copy()
    return obj.parent.matrix_world.inverted_safe() @ obj.matrix_world


def vec(v):
    return Vector(v)
