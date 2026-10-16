"""Semantic model of a GEM .mdl file (skeleton, volumes, obstacles, areas).

Everything the editor does not understand is kept as raw sdl.Node blocks and
written back unchanged, so a model survives import/export without losing data.
"""

from . import sdl
from .sdl import Atom, Node, quoted

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
BONE_KINDS = ("revolute", "prizmatic", "socket")
VIEW_FLAGS = ("NoCastShadows", "DecalTarget", "NoGroupMesh", "NoGetShadows", "Ground")


def _rows_eq(a, b, eps=1e-7):
    return all(abs(x - y) <= eps for x, y in zip(a, b))


def apply_transform_node(matrix, node):
    """Apply a Position / Orientation / Matrix34 block to a 12-float matrix."""
    m = list(matrix)
    v = node.floats()
    if node.is_("matrix34"):
        v = (v + [0.0] * 12)[:12]
        m = v
    elif node.is_("orientation"):
        v = (v + [0.0] * 9)[:9]
        m[0:9] = v
    elif node.is_("position"):
        v = (v + [0.0] * 3)[:3]
        m[9:12] = v
    return tuple(m)


def transform_nodes(matrix):
    """Smallest block(s) describing the matrix, like CBone::WriteMdl."""
    m = tuple(matrix)
    has_pos = not _rows_eq(m[9:12], (0.0, 0.0, 0.0))
    has_rot = not _rows_eq(m[0:9], IDENTITY[0:9])
    if has_rot and has_pos:
        return [Node("Matrix34").add(*[float(x) for x in m])]
    if has_rot:
        return [Node("Orientation").add(*[float(x) for x in m[0:9]])]
    if has_pos:
        return [Node("Position").add(*[float(x) for x in m[9:12]])]
    return []


def _name_of(node):
    for a in node.args:
        if isinstance(a, Atom) and a.quoted:
            return str(a)
    a = node.args
    return str(a[0]) if a else ""



class VolumeView:
    def __init__(self, file="", nodes=None):
        self.file = file
        self.nodes = nodes or []      # inner blocks: {NoCastShadows} {Layer 1} ...

    @classmethod
    def from_node(cls, node):
        return cls(node.first_arg(""), list(node.children))

    def flag(self, name):
        return any(n.is_(name) for n in self.nodes)

    def layer(self):
        for n in self.nodes:
            if n.is_("layer"):
                return n.first_arg("")
        return None

    def to_node(self):
        n = Node("VolumeView").add(quoted(self.file))
        for c in self.nodes:
            n.add(c)
        return n

    def summary(self):
        return (self.file.lower(), tuple(sdl._inline(n) for n in self.nodes))


class Bone:
    def __init__(self, name=""):
        self.name = name
        self.kind = ""                 # revolute / prizmatic / socket
        self.matrix = IDENTITY
        self.parameters = None
        self.limits = None             # list of floats ([] = empty block)
        self.speed = None
        self.speed2 = False
        self.visibility = None
        self.terminator = False
        self.color = None              # raw token, e.g. 0xffaabbcc
        self.views = []                # VolumeView list (first = main mesh, rest = sub-meshes)
        self.lods = None               # VolumeView list inside {LODView}
        self.lod_off = False
        self.lod_extra = []
        self.extra = []                # unknown blocks kept verbatim
        self.children = []

    @classmethod
    def from_node(cls, node):
        b = cls()
        for a in node.args:
            if isinstance(a, Atom) and a.quoted:
                b.name = str(a)
            elif str(a).lower() in BONE_KINDS:
                b.kind = str(a).lower()
            elif not b.name:
                b.name = str(a)
        for c in node.children:
            low = c.name.lower()
            if low in ("position", "orientation", "matrix34"):
                b.matrix = apply_transform_node(b.matrix, c)
            elif low == "parameters":
                b.parameters = c.first_arg("")
            elif low == "limits":
                b.limits = c.floats()
            elif low in ("speed", "speed2"):
                f = c.floats()
                b.speed = f[0] if f else 0.0
                b.speed2 = low == "speed2"
            elif low == "visibility":
                f = c.floats()
                b.visibility = int(f[0]) if f else 0
            elif low == "terminator":
                b.terminator = True
            elif low == "color":
                b.color = c.first_arg("0")
            elif low in BONE_KINDS:
                b.kind = low
            elif low == "volumeview":
                b.views.append(VolumeView.from_node(c))
            elif low == "lodview":
                b.lods = b.lods or []
                for lc in c.children:
                    if lc.is_("volumeview"):
                        b.lods.append(VolumeView.from_node(lc))
                    elif lc.is_("off"):
                        b.lod_off = True
                    else:
                        b.lod_extra.append(lc)
            elif low == "bone":
                b.children.append(Bone.from_node(c))
            elif low == "##bone":
                continue
            else:
                b.extra.append(c)
        return b

    def to_node(self):
        n = Node("bone")
        if self.kind:
            n.add(Atom(self.kind))
        n.add(quoted(self.name))
        if self.parameters is not None:
            n.add(Node("Parameters").add(quoted(self.parameters)))
        if self.limits is not None:
            n.add(Node("Limits").add(*[float(x) for x in self.limits]))
        if self.speed is not None:
            n.add(Node("Speed2" if self.speed2 else "Speed").add(float(self.speed)))
        if self.terminator:
            n.add(Node("Terminator"))
        if self.color is not None:
            n.add(Node("Color").add(Atom(self.color)))
        for t in transform_nodes(self.matrix):
            n.add(t)
        if self.visibility is not None:
            n.add(Node("Visibility").add(int(self.visibility)))
        for e in self.extra:
            n.add(e)
        for v in self.views:
            n.add(v.to_node())
        if self.lods is not None:
            lod = Node("LODView")
            for v in self.lods:
                lod.add(v.to_node())
            for e in self.lod_extra:
                lod.add(e)
            if self.lod_off:
                lod.add(Node("OFF"))
            n.add(lod)
        for c in self.children:
            n.add(c.to_node())
        return n

    def walk(self, parent=None):
        yield self, parent
        for c in self.children:
            yield from c.walk(self)

    def summary(self):
        r = lambda v: tuple(float(x) for x in v)  # noqa: E731
        return (
            self.name, self.kind, r(self.matrix), self.parameters,
            r(self.limits) if self.limits is not None else None,
            float(self.speed) if self.speed is not None else None, self.speed2,
            self.visibility, self.terminator, self.color,
            tuple(v.summary() for v in self.views),
            tuple(v.summary() for v in self.lods) if self.lods is not None else None,
            self.lod_off, tuple(sdl._inline(e) for e in self.lod_extra),
            tuple(sdl._inline(e) for e in self.extra),
            tuple(c.summary() for c in self.children),
        )


class Sequence:
    FLAGS = ("resume", "autostart", "store")

    def __init__(self, name=""):
        self.name = name
        self.file = None
        self.speed = None
        self.smooth = None
        self.flags = set()
        self.extra = []

    @classmethod
    def from_node(cls, node):
        s = cls(_name_of(node))
        for c in node.children:
            low = c.name.lower()
            if low == "file":
                s.file = c.first_arg("")
            elif low == "speed":
                f = c.floats()
                s.speed = f[0] if f else None
            elif low == "smooth":
                f = c.floats()
                s.smooth = f[0] if f else None
            elif low in cls.FLAGS:
                s.flags.add(low)
            else:
                s.extra.append(c)
        return s

    def anm_file(self):
        return self.file or (self.name + ".anm")

    def to_node(self):
        n = Node("sequence").add(quoted(self.name))
        if self.file:
            n.add(Node("File").add(quoted(self.file)))
        if self.speed is not None:
            n.add(Node("Speed").add(float(self.speed)))
        if self.smooth is not None:
            n.add(Node("Smooth").add(float(self.smooth)))
        for f in self.FLAGS:
            if f in self.flags:
                n.add(Node(f))
        for e in self.extra:
            n.add(e)
        return n

    def summary(self):
        return (self.name, self.file, self.speed, self.smooth, tuple(sorted(self.flags)),
                tuple(sdl._inline(e) for e in self.extra))


class Volume:
    def __init__(self, name=""):
        self.name = name
        self.shape = "box"            # box / cylinder / sphere / polyhedron
        self.size = []                # box: 3 floats, cylinder: radius length, sphere: radius
        self.file = ""                # polyhedron .vol
        self.bone = None
        self.matrix = IDENTITY
        self.component = None
        self.tags = None
        self.density = None           # raw text
        self.thickness = None         # raw Node
        self.extra = []

    @classmethod
    def from_node(cls, node):
        v = cls(_name_of(node))
        for c in node.children:
            low = c.name.lower()
            if low in ("box", "cylinder", "sphere"):
                v.shape = low
                v.size = c.floats()
            elif low == "polyhedron":
                v.shape = "polyhedron"
                v.file = c.first_arg("")
            elif low in ("position", "orientation", "matrix34"):
                v.matrix = apply_transform_node(v.matrix, c)
            elif low == "bone":
                v.bone = c.first_arg("")
            elif low == "component":
                v.component = c.first_arg("")
            elif low == "tags":
                v.tags = c.first_arg("")
            elif low == "density":
                v.density = c.first_arg("")
            elif low == "thickness":
                v.thickness = c
            else:
                v.extra.append(c)
        return v

    def to_node(self):
        n = Node("Volume").add(quoted(self.name))
        if self.shape == "polyhedron":
            n.add(Node("Polyhedron").add(quoted(self.file)))
        else:
            n.add(Node(self.shape.capitalize()).add(*[float(x) for x in self.size]))
        if self.bone is not None:
            n.add(Node("Bone").add(quoted(self.bone)))
        if self.component is not None:
            n.add(Node("Component").add(quoted(self.component)))
        if self.tags is not None:
            n.add(Node("Tags").add(quoted(self.tags)))
        if self.thickness is not None:
            n.add(self.thickness)
        for t in transform_nodes(self.matrix):
            n.add(t)
        if self.density is not None:
            n.add(Node("Density").add(quoted(self.density)))
        for e in self.extra:
            n.add(e)
        return n

    def summary(self):
        r = lambda v: tuple(float(x) for x in v)  # noqa: E731
        return (self.name, self.shape, r(self.size), self.file.lower(), self.bone, r(self.matrix),
                self.component, self.tags, self.density,
                sdl._inline(self.thickness) if self.thickness else None,
                tuple(sdl._inline(e) for e in self.extra))


class Shape2D:
    """Obstacle / Area: a 2D rectangle (Obb2), circle (Circle2) or polygon (Polygon2)."""

    def __init__(self, tag="Obstacle", name=""):
        self.tag = tag
        self.name = name
        self.kind = "Obb2"
        self.center = (0.0, 0.0)
        self.extent = (1.0, 1.0)
        self.axis = (1.0, 0.0)
        self.radius = 1.0
        self.vertices = []
        self.tags = None
        self.rotate = False
        self.extra = []
        self.shape_extra = []

    @classmethod
    def from_node(cls, node):
        s = cls(node.name, _name_of(node))
        for c in node.children:
            low = c.name.lower()
            if low in ("obb2", "circle2", "polygon2"):
                s.kind = {"obb2": "Obb2", "circle2": "Circle2", "polygon2": "Polygon2"}[low]
                for p in c.children:
                    pl = p.name.lower()
                    f = p.floats()
                    if pl == "center":
                        s.center = tuple((f + [0, 0])[:2])
                    elif pl == "extent":
                        s.extent = tuple((f + [0, 0])[:2])
                    elif pl == "axis":
                        s.axis = tuple((f + [1, 0])[:2])
                    elif pl == "radius":
                        s.radius = f[0] if f else 0.0
                    elif pl == "vertex":
                        s.vertices.append(tuple((f + [0, 0])[:2]))
                    else:
                        s.shape_extra.append(p)
            elif low == "tags":
                s.tags = c.first_arg("")
            elif low == "rotate":
                s.rotate = True
            else:
                s.extra.append(c)
        return s

    def to_node(self):
        n = Node(self.tag).add(quoted(self.name))
        sh = Node(self.kind)
        if self.kind == "Circle2":
            sh.add(Node("Radius").add(float(self.radius)))
        if self.kind == "Polygon2":
            for v in self.vertices:
                sh.add(Node("Vertex").add(float(v[0]), float(v[1])))
        else:
            sh.add(Node("Center").add(float(self.center[0]), float(self.center[1])))
        if self.kind == "Obb2":
            sh.add(Node("Extent").add(float(self.extent[0]), float(self.extent[1])))
            sh.add(Node("Axis").add(float(self.axis[0]), float(self.axis[1])))
        for e in self.shape_extra:
            sh.add(e)
        n.add(sh)
        if self.tags is not None:
            n.add(Node("Tags").add(quoted(self.tags)))
        if self.rotate:
            n.add(Node("Rotate"))
        for e in self.extra:
            n.add(e)
        return n

    def summary(self):
        r = lambda v: tuple(float(x) for x in v)  # noqa: E731
        return (self.tag.lower(), self.name, self.kind, r(self.center), r(self.extent), r(self.axis),
                float(self.radius), tuple(r(v) for v in self.vertices), self.tags, self.rotate,
                tuple(sdl._inline(e) for e in self.extra + self.shape_extra))


class Model:
    def __init__(self):
        self.header = []
        self.bones = []               # root bones of the skeleton
        self.sequences = []
        self.has_skeleton = True
        self.skeleton_extra = []
        self.animation_extra = []
        self.volumes = []
        self.shapes = []              # obstacles and areas
        self.extra = []               # unknown top-level blocks

    @classmethod
    def from_nodes(cls, nodes, header=None):
        m = cls()
        m.header = list(header or [])
        m.has_skeleton = False
        for n in nodes:
            low = n.name.lower()
            if low == "skeleton":
                m.has_skeleton = True
                for c in n.children:
                    cl = c.name.lower()
                    if cl == "bone":
                        m.bones.append(Bone.from_node(c))
                    elif cl == "animation":
                        for s in c.children:
                            if s.is_("sequence"):
                                m.sequences.append(Sequence.from_node(s))
                            else:
                                m.animation_extra.append(s)
                    elif cl == "##bone":
                        continue
                    else:
                        m.skeleton_extra.append(c)
            elif low == "volume":
                m.volumes.append(Volume.from_node(n))
            elif low in ("obstacle", "area"):
                m.shapes.append(Shape2D.from_node(n))
            else:
                m.extra.append(n)
        return m

    @classmethod
    def read(cls, path):
        nodes, header = sdl.read_file(path)
        return cls.from_nodes(nodes, header)

    def to_nodes(self):
        out = []
        if self.has_skeleton or self.bones or self.sequences:
            sk = Node("Skeleton")
            if self.sequences or self.animation_extra:
                an = Node("animation")
                for s in self.sequences:
                    an.add(s.to_node())
                for e in self.animation_extra:
                    an.add(e)
                sk.add(an)
            for e in self.skeleton_extra:
                sk.add(e)
            for b in self.bones:
                sk.add(b.to_node())
            out.append(sk)
        for s in self.shapes:
            out.append(s.to_node())
        for v in self.volumes:
            out.append(v.to_node())
        out += self.extra
        return out

    def write(self, path):
        sdl.write_file(path, self.to_nodes(), self.header)

    def walk_bones(self):
        for b in self.bones:
            yield from b.walk(None)

    def summary(self):
        return (
            tuple(b.summary() for b in self.bones),
            tuple(s.summary() for s in self.sequences),
            tuple(sdl._inline(e) for e in self.skeleton_extra + self.animation_extra),
            tuple(v.summary() for v in self.volumes),
            tuple(s.summary() for s in self.shapes),
            tuple(sdl._inline(e) for e in self.extra),
        )
