"""Export a GEM model from Blender: .mdl plus its .ply / .vol / .mtl files."""

import os
import shutil

import bpy
from mathutils import Matrix, Vector

from . import mesh_io, utils
from .formats import mdl as mdlfmt
from .formats import mtl as mtlfmt
from .formats import ply as plyfmt
from .formats import resources, sdl
from .formats import vol as volfmt
from .import_mdl import game_matrix, list_to_matrix, matrix_to_list


def text_to_nodes(text):
    if not text or not text.strip():
        return []
    nodes, _ = sdl.parse(text)
    return nodes


class Exporter:
    def __init__(self, context, root, path, apply_modifiers=True, write_mtl=True,
                 force_meshes=False, report=None):
        self.context = context
        self.root = root
        self.path = path
        self.dir = os.path.dirname(os.path.abspath(path))
        self.apply_modifiers = apply_modifiers
        self.write_mtl = write_mtl
        self.force_meshes = force_meshes
        self.report = report
        self.depsgraph = context.evaluated_depsgraph_get()
        self.written = []
        self.copied = []
        self.warnings = []
        self.done_files = {}

    def warn(self, msg):
        self.warnings.append(msg)

    # paths
    def dest_for(self, name):
        """Where a file referenced from the .mdl lives on disk."""
        name = resources.norm(name)
        if name.startswith("/"):
            root = resources.find_resource_root(self.path)
            if root:
                return os.path.join(root, *name.lstrip("/").split("/"))
            return os.path.join(self.dir, os.path.basename(name))
        return os.path.join(self.dir, *name.split("/"))

    def _same(self, a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    def _unchanged(self, obj):
        g = obj.gem
        return (not self.force_meshes and g.mesh_hash and g.source_file
                and os.path.isfile(g.source_file) and mesh_io.mesh_hash(obj) == g.mesh_hash
                and not (self.apply_modifiers and any(m.show_viewport for m in obj.modifiers
                                                      if m.type != "ARMATURE")))

    def _claim(self, dest, obj):
        key = os.path.normcase(os.path.abspath(dest))
        other = self.done_files.get(key)
        if other is not None and other != obj.name:
            return False
        self.done_files[key] = obj.name
        return True

    # meshes
    def write_view(self, obj, owner):
        """Write the .ply of a VolumeView object; returns the name for the .mdl."""
        g = obj.gem
        name = g.ply_file or (utils.gem_name(owner) + ".ply")
        if not name.lower().endswith(".ply"):
            name += ".ply"
        dest = self.dest_for(name)
        if not self._claim(dest, obj):
            return name           # shared file already written
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if obj.type != "MESH":
            if not os.path.isfile(dest):
                self.warn("%s: no mesh and %s does not exist" % (obj.name, name))
            return name
        if self._unchanged(obj):
            if not self._same(g.source_file, dest):
                shutil.copyfile(g.source_file, dest)
                self.copied.append(dest)
            return name
        if g.skinned:
            bind = list_to_matrix(g.bind) if any(g.bind) else obj.matrix_world
            matrix = bind.inverted_safe() @ obj.matrix_world
        elif obj is owner:
            matrix = None
        else:
            matrix = utils.local_matrix(obj)
            if obj.parent is not owner:
                matrix = owner.matrix_world.inverted_safe() @ obj.matrix_world
        ply = mesh_io.mesh_to_ply(obj, matrix, skinned=g.skinned, signature=g.ply_signature or "EPLY",
                                  depsgraph=self.depsgraph, apply_modifiers=self.apply_modifiers,
                                  report=self.report)
        plyfmt.write(dest, ply)
        self.written.append(dest)
        if self.write_mtl:
            self.write_materials(obj, dest)
        g.source_file = dest
        g.mesh_hash = mesh_io.mesh_hash(obj)
        return name

    def write_materials(self, obj, ply_dest):
        """Create .mtl files that don't exist yet next to the mesh."""
        for slot in obj.material_slots:
            m = slot.material
            if m is None:
                continue
            name = m.gem.mtl_name or (m.name + ".mtl")
            if not name.lower().endswith(".mtl") or name.startswith("$") or name.startswith("/"):
                continue
            dest = os.path.join(os.path.dirname(ply_dest), *resources.norm(name).split("/"))
            if os.path.exists(dest):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            src = m.get("gem_mtl_path")
            if src and os.path.isfile(src):
                shutil.copyfile(src, dest)
                self.copied.append(dest)
                continue
            tex = None
            if m.use_nodes and m.node_tree:
                for n in m.node_tree.nodes:
                    if n.type == "TEX_IMAGE" and n.image is not None:
                        tex = os.path.splitext(os.path.basename(bpy.path.abspath(n.image.filepath) or n.image.name))[0]
                        break
            blend = "test" if getattr(m, "blend_method", "OPAQUE") == "CLIP" else "none"
            mtlfmt.write(dest, mtlfmt.simple(tex or os.path.splitext(name)[0], blend))
            self.written.append(dest)
            if tex is None:
                self.warn("%s: no texture found, %s references %s" % (m.name, name, os.path.splitext(name)[0]))

    def view_from(self, obj, owner):
        g = obj.gem
        nodes = []
        for flag, prop in (("NoCastShadows", "no_cast_shadows"), ("DecalTarget", "decal_target"),
                           ("NoGroupMesh", "no_group_mesh"), ("NoGetShadows", "no_get_shadows"),
                           ("Ground", "ground")):
            if getattr(g, prop):
                nodes.append(sdl.Node(flag))
        if g.layer:
            nodes.append(sdl.Node("Layer").add(sdl.Atom(g.layer)))
        nodes += text_to_nodes(g.view_extra)
        return mdlfmt.VolumeView(self.write_view(obj, owner), nodes)

    # bones
    def bone_matrix(self, obj):
        animated = obj.animation_data is not None and obj.animation_data.action is not None
        if animated and obj.gem.has_rest:
            return game_matrix(obj, list_to_matrix(obj.gem.rest))
        m = utils.local_matrix(obj)
        if not animated:
            obj.gem.rest = matrix_to_list(obj.matrix_basis)
            obj.gem.has_rest = True
        return game_matrix(obj, m)

    def build_bone(self, obj):
        g = obj.gem
        b = mdlfmt.Bone(utils.gem_name(obj))
        b.kind = "" if g.kind == "NONE" else g.kind
        b.matrix = self.bone_matrix(obj)
        if g.use_parameters:
            b.parameters = g.parameters
        if g.use_limits:
            b.limits = list(g.limits)[:g.limits_count]
        if g.use_speed:
            b.speed = g.speed
            b.speed2 = g.speed2
        if g.use_visibility:
            b.visibility = g.visibility
        b.terminator = g.terminator
        if g.use_color:
            b.color = g.color
        b.extra = text_to_nodes(g.extra)
        b.lod_off = g.lod_off
        b.lod_extra = text_to_nodes(g.lod_extra)

        views = []
        if g.has_view and obj.type == "MESH":
            views.append((g.lod_level, -1, obj))
        for v in self.extra_views.get(obj.as_pointer(), []):
            views.append((v.gem.lod_level, v.gem.order, v))
        regular = [x for x in views if x[0] < 0]
        lods = sorted([x for x in views if x[0] >= 0], key=lambda x: (x[0], x[1]))
        regular.sort(key=lambda x: x[1])
        b.views = [self.view_from(v, obj) for _, _, v in regular]
        if lods:
            b.lods = [self.view_from(v, obj) for _, _, v in lods]
        for c in utils.iter_children(obj):
            if c.gem.role == "BONE":
                b.children.append(self.build_bone(c))
        return b

    # volumes / shapes
    def build_volume(self, obj):
        g = obj.gem
        v = mdlfmt.Volume(utils.gem_name(obj))
        v.shape = g.vol_shape
        parent = obj.parent
        if parent is not None and parent.gem.role == "BONE":
            v.bone = utils.gem_name(parent)
            local = utils.local_matrix(obj)
        else:
            v.bone = obj.get("gem_bone")
            local = self.root.matrix_world.inverted_safe() @ obj.matrix_world
        loc, rot, scale = local.decompose()
        # Keep mirroring in the matrix (some volumes have mirrored axes), sizes stay positive
        signs = Vector([-1.0 if s < 0 else 1.0 for s in scale])
        scale = Vector([abs(s) for s in scale])
        rigid = Matrix.Translation(loc) @ rot.to_matrix().to_4x4() @ Matrix.Diagonal(signs).to_4x4()
        if v.shape == "polyhedron":
            v.file = g.vol_file or (v.name + ".vol")
            if not v.file.lower().endswith(".vol"):
                v.file += ".vol"
            dest = self.dest_for(v.file)
            if self._claim(dest, obj):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                unchanged = self._unchanged(obj) and all(abs(s - 1.0) < 1e-6 for s in scale)
                if unchanged:
                    if not self._same(g.source_file, dest):
                        shutil.copyfile(g.source_file, dest)
                        self.copied.append(dest)
                    v.matrix = game_matrix(obj, local)
                else:
                    volfmt.write(dest, mesh_io.mesh_to_vol(obj, Matrix.Diagonal(scale).to_4x4()))
                    self.written.append(dest)
                    g.source_file = dest
                    g.mesh_hash = mesh_io.mesh_hash(obj)
                    v.matrix = game_matrix(obj, rigid)
            else:
                v.matrix = game_matrix(obj, local)
        elif self._untouched_primitive(obj, local):
            n = {"box": 3, "cylinder": 2, "sphere": 1}[v.shape]
            v.size = list(g.vol_size)[:n]
            v.matrix = tuple(g.src_matrix)
        else:
            vs = [Vector(x.co) for x in obj.data.vertices] if obj.type == "MESH" else []
            if vs:
                mn = Vector((min(p.x for p in vs), min(p.y for p in vs), min(p.z for p in vs)))
                mx = Vector((max(p.x for p in vs), max(p.y for p in vs), max(p.z for p in vs)))
            else:
                mn, mx = Vector((-0.5, -0.5, -0.5)), Vector((0.5, 0.5, 0.5))
            d = Vector((abs(scale[i]) * (mx[i] - mn[i]) for i in range(3)))
            c = Vector(((mx[i] + mn[i]) * 0.5 * scale[i] for i in range(3)))
            if v.shape == "cylinder":
                v.size = [max(d.x, d.y) * 0.5, d.z]
            elif v.shape == "sphere":
                v.size = [max(d) * 0.5]
            else:
                v.size = [d.x, d.y, d.z]
            v.matrix = game_matrix(obj, rigid @ Matrix.Translation(c))
        if g.use_component:
            v.component = g.component
        if g.use_tags:
            v.tags = g.tags
        if g.use_density:
            v.density = g.density
        th = text_to_nodes(g.thickness)
        v.thickness = th[0] if th else None
        v.extra = text_to_nodes(g.extra)
        return v

    def _untouched_primitive(self, obj, local):
        """Box/cylinder/sphere not edited since import: keep size and matrix as in the file
        (some files scale the volume matrix instead of the size)."""
        g = obj.gem
        if not (g.has_src and g.mesh_hash) or mesh_io.mesh_hash(obj) != g.mesh_hash:
            return False
        return game_matrix(obj, local) == tuple(g.src_matrix)

    def build_shape(self, obj):
        g = obj.gem
        s = mdlfmt.Shape2D(g.shape_tag, utils.gem_name(obj))
        s.kind = g.shape_kind
        m = self.root.matrix_world.inverted_safe() @ obj.matrix_world
        pts = [m @ v.co for v in obj.data.vertices] if obj.type == "MESH" else []
        pts = [(p.x, -p.y) for p in pts]      # back to GEM (mirror Y)
        if obj.type == "MESH" and obj.data.polygons:
            ring = [obj.data.vertices[i] for i in obj.data.polygons[0].vertices]
            pts = [((m @ v.co).x, -(m @ v.co).y) for v in ring]
        if pts:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            s.center = (cx, cy)
            if s.kind == "Circle2":
                s.radius = max(((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5 for p in pts)
            elif s.kind == "Polygon2":
                s.vertices = pts
            else:
                ax = Vector((pts[0][0] - pts[1][0], pts[0][1] - pts[1][1])) if len(pts) > 1 else Vector((1, 0))
                if ax.length < 1e-9:
                    ax = Vector((1.0, 0.0))
                ax.normalize()
                ay = Vector((-ax.y, ax.x))
                ex = max(abs((p[0] - cx) * ax.x + (p[1] - cy) * ax.y) for p in pts)
                ey = max(abs((p[0] - cx) * ay.x + (p[1] - cy) * ay.y) for p in pts)
                s.axis = (ax.x, ax.y)
                s.extent = (ex, ey)
        if g.use_tags:
            s.tags = g.tags
        s.rotate = g.rotate
        s.extra = text_to_nodes(g.extra)
        s.shape_extra = text_to_nodes(g.shape_extra)
        return s

    # whole model
    def collect(self):
        """Objects belonging to the model: everything under root plus skinned meshes."""
        under = []

        def walk(o):
            for c in o.children:
                under.append(c)
                walk(c)
        walk(self.root)
        bones = set(o.as_pointer() for o in under if o.gem.role == "BONE")
        self.extra_views = {}
        candidates = set(under) | set(o for o in bpy.data.objects
                                      if o.gem.role == "VIEW" and o.gem.owner is not None)
        for o in candidates:
            if o.gem.role == "VIEW" and o.gem.owner is not None and o.gem.owner.as_pointer() in bones:
                self.extra_views.setdefault(o.gem.owner.as_pointer(), []).append(o)
            elif o.gem.role == "VIEW" and o.parent is not None and o.parent.as_pointer() in bones:
                self.extra_views.setdefault(o.parent.as_pointer(), []).append(o)
        return under

    def run(self):
        under = self.collect()
        g = self.root.gem
        model = mdlfmt.Model()
        model.header = [ln for ln in g.header.split("\n") if ln.strip()]
        model.has_skeleton = True
        model.skeleton_extra = text_to_nodes(g.skeleton_extra)
        model.animation_extra = text_to_nodes(g.animation_extra)
        model.extra = text_to_nodes(g.extra)
        for item in g.sequences:
            s = mdlfmt.Sequence(item.name)
            s.file = item.file or None
            s.speed = item.speed if item.use_speed else None
            s.smooth = item.smooth if item.use_smooth else None
            for f in mdlfmt.Sequence.FLAGS:
                if getattr(item, f):
                    s.flags.add(f)
            s.extra = text_to_nodes(item.extra)
            model.sequences.append(s)
        for c in utils.iter_children(self.root):
            if c.gem.role == "BONE":
                model.bones.append(self.build_bone(c))
        vols = sorted([o for o in under if o.gem.role == "VOLUME"], key=lambda o: (o.gem.order, o.name))
        model.volumes = [self.build_volume(o) for o in vols]
        shapes = sorted([o for o in under if o.gem.role == "SHAPE"], key=lambda o: (o.gem.order, o.name))
        model.shapes = [self.build_shape(o) for o in shapes]
        os.makedirs(self.dir, exist_ok=True)
        model.write(self.path)
        g.mdl_path = self.path
        return model


def export_mdl(context, root, path, **kw):
    ex = Exporter(context, root, path, **kw)
    ex.run()
    return ex


def export_ply(context, obj, path, apply_modifiers=True):
    """Export one mesh object as a standalone .ply (object space)."""
    dg = context.evaluated_depsgraph_get()
    g = obj.gem
    ply = mesh_io.mesh_to_ply(obj, None, skinned=g.skinned, signature=g.ply_signature or "EPLY",
                              depsgraph=dg, apply_modifiers=apply_modifiers)
    plyfmt.write(path, ply)
    return ply
