"""Import .mdl models (and single .ply / .vol files) into Blender."""

import os
import uuid

import bpy
from mathutils import Matrix, Vector

from . import mesh_io, utils
from .formats import mdl as mdlfmt
from .formats import ply as plyfmt
from .formats import resources, sdl
from .formats import vol as volfmt


def addon_prefs(context):
    pkg = __package__
    addon = context.preferences.addons.get(pkg)
    return addon.preferences if addon else None


def resource_index(context, path):
    prefs = addon_prefs(context)
    extra = []
    if prefs:
        extra = [p for p in prefs.resource_roots.split(";") if p.strip()]
    return resources.ResourceIndex(resources.guess_roots(path, extra))


def nodes_to_text(nodes):
    return sdl.write(nodes).strip() if nodes else ""


def matrix_to_list(m):
    return [m[r][c] for r in range(4) for c in range(4)]


def list_to_matrix(v):
    return Matrix([v[0:4], v[4:8], v[8:12], v[12:16]])


def store_rest(obj):
    obj.gem.rest = matrix_to_list(obj.matrix_basis)
    obj.gem.has_rest = True


def set_game_matrix(obj, m):
    """Place obj by a GEM matrix and remember the exact numbers: Blender objects
    cannot hold shear, so an untouched object writes the original matrix back."""
    obj.matrix_basis = utils.gem_to_matrix(m)
    obj.gem.src_matrix = tuple(m)
    obj.gem.src_basis = matrix_to_list(obj.matrix_basis)
    obj.gem.has_src = True


def game_matrix(obj, basis):
    """GEM matrix for obj whose local Blender matrix is `basis`."""
    g = obj.gem
    if g.has_src:
        src = list_to_matrix(g.src_basis)
        if all(abs(basis[r][c] - src[r][c]) < 1e-5 for r in range(4) for c in range(4)):
            return tuple(g.src_matrix)
    return utils.matrix_to_gem(basis)


def resolve_model_file(name, mdl_path, res, exts=("",)):
    """Files named in a .mdl are relative to it, or to the resource root when they start with '/'."""
    name = resources.norm(name)
    if name.startswith("/"):
        return res.find(name, exts)
    base = os.path.dirname(mdl_path)
    for ext in exts:
        p = os.path.join(base, *(name + ext).split("/"))
        if os.path.isfile(p):
            return p
    return res.resolve(name, mdl_path, exts)


def _new_collection(name, parent):
    c = bpy.data.collections.new(name)
    parent.children.link(c)
    return c


class Importer:
    def __init__(self, context, path, merge=True, show_volumes=True, import_animations=True,
                 report=None):
        self.context = context
        self.path = path
        self.merge = merge
        self.show_volumes = show_volumes
        self.import_animations = import_animations
        self.report = report or (lambda *a: None)
        self.res = resource_index(context, path)
        self.ply_cache = {}
        self.bones = {}          # lower-case game name -> object
        self.skins = []          # (view object, Ply)
        self.warnings = []

    def warn(self, msg):
        self.warnings.append(msg)

    # helpers
    def load_ply(self, file):
        p = resolve_model_file(file, self.path, self.res)
        if not p:
            self.warn("PLY not found: %s" % file)
            return None, None
        key = os.path.normcase(p)
        if key not in self.ply_cache:
            try:
                self.ply_cache[key] = plyfmt.read(p)
            except (OSError, plyfmt.PlyError, ValueError) as e:
                self.warn("%s: %s" % (file, e))
                self.ply_cache[key] = None
        return self.ply_cache[key], p

    def mesh_from_ply(self, ply, path, name):
        mats = [mesh_io.make_material(m, path, self.res, ply.bply) for m in ply.meshes]
        me, info = mesh_io.ply_to_mesh(ply, name, mats, merge=self.merge)
        return me, info

    def apply_view_props(self, obj, view, file, path, ply, lod_level):
        g = obj.gem
        g.has_view = True
        g.ply_file = view.file
        g.lod_level = lod_level
        g.source_file = path or ""
        g.ply_signature = ply.signature if ply else "EPLY"
        extra = []
        for n in view.nodes:
            low = n.name.lower()
            if low == "nocastshadows":
                g.no_cast_shadows = True
            elif low == "decaltarget":
                g.decal_target = True
            elif low == "nogroupmesh":
                g.no_group_mesh = True
            elif low == "nogetshadows":
                g.no_get_shadows = True
            elif low == "ground":
                g.ground = True
            elif low == "layer":
                g.layer = n.first_arg("")
            else:
                extra.append(n)
        g.view_extra = nodes_to_text(extra)

    # model
    def run(self):
        model = mdlfmt.Model.read(self.path)
        name = os.path.splitext(os.path.basename(self.path))[0]
        scene_coll = self.context.scene.collection
        self.coll = _new_collection(name, scene_coll)
        self.coll_vol = _new_collection(name + " volumes", self.coll)
        self.coll_shape = _new_collection(name + " obstacles", self.coll)
        self.coll_lod = _new_collection(name + " LODs", self.coll)

        root = bpy.data.objects.new(name, None)
        root.empty_display_type = "ARROWS"
        root.empty_display_size = 5.0
        self.coll.objects.link(root)
        g = root.gem
        g.role = "MODEL"
        g.game_name = name
        g.mdl_path = self.path
        g.header = "\n".join(model.header)
        g.has_skeleton = model.has_skeleton
        g.skeleton_extra = nodes_to_text(model.skeleton_extra)
        g.animation_extra = nodes_to_text(model.animation_extra)
        g.extra = nodes_to_text(model.extra)
        root["gem_uid"] = uuid.uuid4().hex
        for s in model.sequences:
            item = g.sequences.add()
            item.name = s.name
            item.file = s.file or ""
            item.use_speed = s.speed is not None
            item.speed = s.speed if s.speed is not None else 1.0
            item.use_smooth = s.smooth is not None
            item.smooth = s.smooth if s.smooth is not None else 0.0
            item.resume = "resume" in s.flags
            item.autostart = "autostart" in s.flags
            item.store = "store" in s.flags
            item.extra = nodes_to_text(s.extra)
        self.root = root

        for i, b in enumerate(model.bones):
            self.build_bone(b, root, i)

        if self.skins:
            self.build_armature()

        self.coll_vol.hide_render = True
        for i, v in enumerate(model.volumes):
            self.build_volume(v, i)
        for i, s in enumerate(model.shapes):
            self.build_shape(s, i)
        for lc in self.context.view_layer.layer_collection.children:
            if lc.collection == self.coll:
                for sub in lc.children:
                    if sub.collection in (self.coll_lod,):
                        sub.hide_viewport = True
                    if sub.collection == self.coll_vol and not self.show_volumes:
                        sub.hide_viewport = True

        if self.import_animations and model.sequences:
            from . import anim_io
            for s in model.sequences:
                p = resolve_model_file(s.anm_file(), self.path, self.res)
                if not p:
                    self.warn("Animation not found: %s" % s.anm_file())
                    continue
                try:
                    anim_io.import_anm(self.context, root, p, s.name, activate=False)
                except Exception as e:  # keep importing the rest
                    self.warn("%s: %s" % (s.anm_file(), e))
        return root

    def build_bone(self, b, parent, order):
        views = [(v, -1) for v in b.views]
        if b.lods is not None:
            views += [(v, i) for i, v in enumerate(b.lods)]
        main = None
        loaded = []
        for v, lod in views:
            ply, path = self.load_ply(v.file)
            loaded.append((v, lod, ply, path))
        for item in loaded:
            v, lod, ply, path = item
            if ply is not None and ply.skin is None:
                main = item
                break

        if main is not None:
            v, lod, ply, path = main
            me, info = self.mesh_from_ply(ply, path, b.name)
            obj = bpy.data.objects.new(b.name, me)
        else:
            obj = bpy.data.objects.new(b.name, None)
            obj.empty_display_type = "PLAIN_AXES"
            obj.empty_display_size = 1.0
        self.coll.objects.link(obj)
        g = obj.gem
        g.role = "BONE"
        g.game_name = b.name
        g.order = order
        g.kind = b.kind or "NONE"
        g.use_parameters = b.parameters is not None
        g.parameters = b.parameters or ""
        g.use_limits = b.limits is not None
        if b.limits is not None:
            g.limits_count = min(2, len(b.limits))
            g.limits = (list(b.limits) + [0.0, 0.0])[:2]
        g.use_speed = b.speed is not None
        g.speed = b.speed if b.speed is not None else 1.0
        g.speed2 = b.speed2
        g.use_visibility = b.visibility is not None
        g.visibility = b.visibility if b.visibility is not None else 1
        g.terminator = b.terminator
        g.use_color = b.color is not None
        g.color = b.color or "0xffffffff"
        g.lod_off = b.lod_off
        g.lod_extra = nodes_to_text(b.lod_extra)
        g.extra = nodes_to_text(b.extra)
        utils.set_parent(obj, parent)
        set_game_matrix(obj, b.matrix)
        store_rest(obj)
        self.bones.setdefault(b.name.lower(), obj)

        if main is not None:
            v, lod, ply, path = main
            self.apply_view_props(obj, v, v.file, path, ply, lod)
            obj.gem.mesh_hash = mesh_io.mesh_hash(obj)

        view_order = 0
        for item in loaded:
            if item is main:
                continue
            v, lod, ply, path = item
            view_order += 1
            if ply is None:
                # keep the reference so it is written back
                vo = bpy.data.objects.new("%s [%s]" % (b.name, os.path.basename(v.file)), None)
                vo.empty_display_size = 0.1
                self.coll.objects.link(vo)
                utils.set_parent(vo, obj)
            else:
                me, info = self.mesh_from_ply(ply, path, os.path.splitext(os.path.basename(v.file))[0])
                vo = bpy.data.objects.new(me.name, me)
                (self.coll_lod if lod > 0 else self.coll).objects.link(vo)
                if ply.skin is not None:
                    vo.gem.skinned = True
                    self.skins.append((vo, ply, info, obj))
                else:
                    utils.set_parent(vo, obj)
            vo.gem.role = "VIEW"
            vo.gem.owner = obj
            vo.gem.order = view_order
            self.apply_view_props(vo, v, v.file, path, ply, lod)
            if vo.type == "MESH":
                vo.gem.mesh_hash = mesh_io.mesh_hash(vo)

        for i, c in enumerate(b.children):
            self.build_bone(c, obj, i)
        return obj

    # skinned meshes
    def build_armature(self):
        """Armature whose bones follow the bone objects (Child Of), so skinned
        meshes deform when the bone objects are animated."""
        ctx = self.context
        self.context.view_layer.update()
        arm_data = bpy.data.armatures.new(self.root.name + " skin")
        arm = bpy.data.objects.new(self.root.name + " skin", arm_data)
        arm.show_in_front = True
        arm.gem.role = "NONE"
        self.coll.objects.link(arm)
        names = []
        for vo, ply, info, owner in self.skins:
            for n in ply.skin:
                if n not in names:
                    names.append(n)

        prev_active = ctx.view_layer.objects.active
        for o in ctx.view_layer.objects:
            o.select_set(False)
        ctx.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        size = 1.0
        for n in names:
            eb = arm_data.edit_bones.new(n)
            src = self.bones.get(n.lower())
            m = src.matrix_world.copy() if src else Matrix.Identity(4)
            loc, rot, _ = m.decompose()
            eb.head = loc
            eb.tail = loc + rot.to_matrix() @ Vector((0.0, size, 0.0))
            eb.align_roll(rot.to_matrix() @ Vector((0.0, 0.0, 1.0)))
        bpy.ops.object.mode_set(mode="POSE")
        for n in names:
            src = self.bones.get(n.lower())
            pb = arm.pose.bones.get(n)
            if src is None or pb is None:
                continue
            c = pb.constraints.new("CHILD_OF")
            c.target = src
            c.inverse_matrix = src.matrix_world.inverted_safe()
        bpy.ops.object.mode_set(mode="OBJECT")
        arm.select_set(False)
        ctx.view_layer.objects.active = prev_active
        arm.hide_set(True)

        for vo, ply, info, owner in self.skins:
            vo.matrix_world = owner.matrix_world.copy()
            vo.gem.bind = matrix_to_list(owner.matrix_world)
            if info.get("weights"):
                mesh_io.apply_weights(vo, info["weights"], ply.skin)
            vo.gem.mesh_hash = mesh_io.mesh_hash(vo)
            mod = vo.modifiers.new("Skin", "ARMATURE")
            mod.object = arm
        self.root["gem_armature"] = arm.name

    # volumes and obstacles
    def build_volume(self, v, order):
        name = v.name or "volume"
        if v.shape == "polyhedron":
            p = resolve_model_file(v.file, self.path, self.res)
            me = None
            if p:
                try:
                    me = mesh_io.vol_to_mesh(volfmt.read(p), name)
                except (OSError, volfmt.VolError, ValueError) as e:
                    self.warn("%s: %s" % (v.file, e))
            else:
                self.warn("VOL not found: %s" % v.file)
            if me is None:
                me = bpy.data.meshes.new(name)
        elif v.shape == "cylinder":
            s = list(v.size) + [1.0, 1.0]
            me = mesh_io.cylinder_mesh(name, s[0], s[1])
        elif v.shape == "sphere":
            s = list(v.size) + [1.0]
            me = mesh_io.sphere_mesh(name, s[0])
        else:
            s = (list(v.size) + [1.0, 1.0, 1.0])[:3]
            me = mesh_io.box_mesh(name, s)
        me.materials.append(mesh_io.volume_material())
        obj = bpy.data.objects.new(name, me)
        obj.display_type = "WIRE"
        obj.show_in_front = False
        obj.hide_render = True
        self.coll_vol.objects.link(obj)
        g = obj.gem
        g.role = "VOLUME"
        g.game_name = v.name
        g.order = order
        g.vol_shape = v.shape
        g.vol_file = v.file
        g.vol_size = (list(v.size) + [0.0, 0.0, 0.0])[:3]
        g.use_component = v.component is not None
        g.component = v.component or ""
        g.use_tags = v.tags is not None
        g.tags = v.tags or ""
        g.use_density = v.density is not None
        g.density = v.density or ""
        g.thickness = nodes_to_text([v.thickness]) if v.thickness is not None else ""
        g.extra = nodes_to_text(v.extra)
        g.source_file = (resolve_model_file(v.file, self.path, self.res) or "") if v.shape == "polyhedron" else ""
        parent = self.bones.get((v.bone or "").lower()) if v.bone else None
        if v.bone and parent is None:
            self.warn("Volume %s: bone %s not found" % (v.name, v.bone))
            obj["gem_bone"] = v.bone
        utils.set_parent(obj, parent or self.root)
        set_game_matrix(obj, v.matrix)
        g.mesh_hash = mesh_io.mesh_hash(obj)

    def build_shape(self, s, order):
        me = mesh_io.shape_mesh(s.name or s.tag, s)
        obj = bpy.data.objects.new(s.name or s.tag, me)
        obj.display_type = "WIRE"
        obj.hide_render = True
        self.coll_shape.objects.link(obj)
        g = obj.gem
        g.role = "SHAPE"
        g.game_name = s.name
        g.order = order
        g.shape_tag = "Area" if s.tag.lower() == "area" else "Obstacle"
        g.shape_kind = s.kind
        g.use_tags = s.tags is not None
        g.tags = s.tags or ""
        g.rotate = s.rotate
        g.extra = nodes_to_text(s.extra)
        g.shape_extra = nodes_to_text(s.shape_extra)
        utils.set_parent(obj, self.root)


def import_mdl(context, path, **kw):
    imp = Importer(context, path, **kw)
    root = imp.run()
    return root, imp.warnings


def import_ply(context, path, merge=True):
    """A single .ply as a bone mesh (not attached to any model)."""
    res = resource_index(context, path)
    ply = plyfmt.read(path)
    name = os.path.splitext(os.path.basename(path))[0]
    mats = [mesh_io.make_material(m, path, res, ply.bply) for m in ply.meshes]
    me, info = mesh_io.ply_to_mesh(ply, name, mats, merge=merge)
    obj = bpy.data.objects.new(name, me)
    context.collection.objects.link(obj)
    g = obj.gem
    g.role = "BONE"
    g.game_name = name
    g.has_view = True
    g.ply_file = os.path.basename(path)
    g.source_file = path
    g.ply_signature = ply.signature
    if info.get("weights"):
        mesh_io.apply_weights(obj, info["weights"], ply.skin)
        g.skinned = True
    g.mesh_hash = mesh_io.mesh_hash(obj)
    return obj


def import_vol(context, path):
    name = os.path.splitext(os.path.basename(path))[0]
    me = mesh_io.vol_to_mesh(volfmt.read(path), name)
    me.materials.append(mesh_io.volume_material())
    obj = bpy.data.objects.new(name, me)
    obj.display_type = "WIRE"
    context.collection.objects.link(obj)
    g = obj.gem
    g.role = "VOLUME"
    g.game_name = name
    g.vol_shape = "polyhedron"
    g.vol_file = os.path.basename(path)
    g.source_file = path
    g.mesh_hash = mesh_io.mesh_hash(obj)
    return obj
