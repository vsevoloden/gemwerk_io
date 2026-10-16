"""Import/export operators and editing tools."""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Vector

from . import anim_io, export_mdl, import_mdl, mesh_io, utils


def _report_warnings(op, warnings, limit=12):
    for w in warnings[:limit]:
        op.report({"WARNING"}, w)
    if len(warnings) > limit:
        op.report({"WARNING"}, "... and %d more warnings (see console)" % (len(warnings) - limit))
    for w in warnings:
        print("GEM:", w)


def active_root(context):
    root = utils.find_model_root(context.active_object) if context.active_object else None
    if root is None:
        roots = [o for o in context.scene.objects if o.gem.role == "MODEL"]
        if len(roots) == 1:
            root = roots[0]
    return root


# Import

class GEM_OT_import_mdl(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.gem_mdl"
    bl_label = "Import GEM Model"
    bl_description = "Import a Men of War / GEM model (.mdl) with meshes, volumes and animations"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".mdl"
    filter_glob: StringProperty(default="*.mdl", options={"HIDDEN"})
    merge: BoolProperty(name="Merge seam vertices", default=True,
                        description="Weld the split vertices of the game mesh so it can be edited")
    show_volumes: BoolProperty(name="Show volumes", default=True)
    import_animations: BoolProperty(name="Import animations", default=True,
                                    description="Load the .anm of every sequence listed in the model")

    def execute(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            root, warnings = import_mdl.import_mdl(
                context, self.filepath, merge=self.merge, show_volumes=self.show_volumes,
                import_animations=self.import_animations)
        except (OSError, ValueError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _report_warnings(self, warnings)
        for o in context.view_layer.objects:
            o.select_set(False)
        if root.name in context.view_layer.objects:
            context.view_layer.objects.active = root
            root.select_set(True)
        return {"FINISHED"}


class GEM_OT_import_ply(bpy.types.Operator, ImportHelper):
    bl_idname = "import_mesh.gem_ply"
    bl_label = "Import GEM Mesh"
    bl_description = "Import GEM .ply meshes (not the Stanford PLY format)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".ply"
    filter_glob: StringProperty(default="*.ply", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    merge: BoolProperty(name="Merge seam vertices", default=True)

    def execute(self, context):
        paths = [os.path.join(self.directory, f.name) for f in self.files if f.name] or [self.filepath]
        for p in paths:
            try:
                import_mdl.import_ply(context, p, merge=self.merge)
            except Exception as e:
                self.report({"ERROR"}, "%s: %s" % (os.path.basename(p), e))
        return {"FINISHED"}


class GEM_OT_import_vol(bpy.types.Operator, ImportHelper):
    bl_idname = "import_mesh.gem_vol"
    bl_label = "Import GEM Volume"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".vol"
    filter_glob: StringProperty(default="*.vol", options={"HIDDEN"})

    def execute(self, context):
        try:
            obj = import_mdl.import_vol(context, self.filepath)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        parent = context.active_object
        if parent is not None and parent.gem.role in ("BONE", "MODEL"):
            utils.set_parent(obj, parent)
        return {"FINISHED"}


class GEM_OT_import_anm(bpy.types.Operator, ImportHelper):
    bl_idname = "import_anim.gem_anm"
    bl_label = "Import GEM Animation"
    bl_description = "Load an .anm onto the active GEM model as a sequence"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".anm"
    filter_glob: StringProperty(default="*.anm", options={"HIDDEN"})
    sequence: StringProperty(name="Sequence", description="Sequence name, empty = file name")

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        try:
            missing = anim_io.import_anm(context, root, self.filepath, self.sequence or None)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if missing:
            self.report({"WARNING"}, "Bones not in model: " + ", ".join(missing[:10]))
        seq = self.sequence or os.path.splitext(os.path.basename(self.filepath))[0]
        idx = root.gem.sequences.find(seq)
        if idx >= 0:
            root.gem.sequence_index = idx
        return {"FINISHED"}


# Export

class GEM_OT_export_mdl(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.gem_mdl"
    bl_label = "Export GEM Model"
    bl_description = "Write the active GEM model: .mdl, changed .ply/.vol files and missing .mtl files"
    bl_options = {"REGISTER", "PRESET"}

    filename_ext = ".mdl"
    filter_glob: StringProperty(default="*.mdl", options={"HIDDEN"})
    apply_modifiers: BoolProperty(name="Apply modifiers", default=True)
    write_mtl: BoolProperty(name="Create missing .mtl", default=True,
                            description="Write a simple material file for materials that have none")
    force_meshes: BoolProperty(name="Rebuild all meshes", default=False,
                               description="Rebuild every .ply even when the mesh was not edited "
                                           "(unedited meshes are normally copied byte for byte)")

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def invoke(self, context, event):
        root = active_root(context)
        if root and root.gem.mdl_path:
            self.filepath = root.gem.mdl_path
        return super().invoke(context, event)

    def execute(self, context):
        root = active_root(context)
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            ex = export_mdl.export_mdl(context, root, self.filepath, apply_modifiers=self.apply_modifiers,
                                       write_mtl=self.write_mtl, force_meshes=self.force_meshes,
                                       report=self.report)
        except (OSError, ValueError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _report_warnings(self, ex.warnings)
        self.report({"INFO"}, "Saved %s: %d files written, %d copied" % (
            os.path.basename(self.filepath), len(ex.written) + 1, len(ex.copied)))
        return {"FINISHED"}


class GEM_OT_export_anm(bpy.types.Operator, ExportHelper):
    bl_idname = "export_anim.gem_anm"
    bl_label = "Export GEM Animation"
    bl_description = "Sample the bone objects of the active model over the scene frame range into an .anm"
    bl_options = {"REGISTER"}

    filename_ext = ".anm"
    filter_glob: StringProperty(default="*.anm", options={"HIDDEN"})
    version: EnumProperty(name="Format", items=[
        ("6", "v6 (game default)", "Position + quaternion; matrices with scale fall back to v4"),
        ("4", "v4 (matrices)", "Full matrices"),
    ], default="6")
    only_animated: BoolProperty(name="Only animated bones", default=True)

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def invoke(self, context, event):
        root = active_root(context)
        seq = root.get("gem_active_seq") if root else None
        if root and seq and root.gem.mdl_path:
            item = root.gem.sequences.get(seq)
            fname = (item.file if item and item.file else seq + ".anm")
            self.filepath = os.path.join(os.path.dirname(root.gem.mdl_path), fname)
        return super().invoke(context, event)

    def execute(self, context):
        root = active_root(context)
        try:
            v = anim_io.export_anm(context, root, self.filepath, int(self.version),
                                   only_animated=self.only_animated)
        except (OSError, ValueError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if v != int(self.version):
            self.report({"WARNING"}, "Bones are scaled: written as v4 matrices")
        return {"FINISHED"}


class GEM_OT_export_ply(bpy.types.Operator, ExportHelper):
    bl_idname = "export_mesh.gem_ply"
    bl_label = "Export GEM Mesh"
    bl_description = "Write the active mesh as a GEM .ply (object space)"
    filename_ext = ".ply"
    filter_glob: StringProperty(default="*.ply", options={"HIDDEN"})
    apply_modifiers: BoolProperty(name="Apply modifiers", default=True)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        try:
            export_mdl.export_ply(context, context.active_object, self.filepath, self.apply_modifiers)
        except (OSError, ValueError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


# Sequences

class GEM_OT_sequence_activate(bpy.types.Operator):
    bl_idname = "gem.sequence_activate"
    bl_label = "Play Sequence"
    bl_description = "Put this sequence's animation on the bones (empty = rest pose)"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    def execute(self, context):
        root = active_root(context)
        if root is None:
            return {"CANCELLED"}
        anim_io.activate_sequence(context, root, self.name)
        return {"FINISHED"}


class GEM_OT_sequence_add(bpy.types.Operator):
    bl_idname = "gem.sequence_add"
    bl_label = "Add Sequence"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = active_root(context)
        item = root.gem.sequences.add()
        item.name = "sequence%d" % len(root.gem.sequences)
        root.gem.sequence_index = len(root.gem.sequences) - 1
        return {"FINISHED"}


class GEM_OT_sequence_remove(bpy.types.Operator):
    bl_idname = "gem.sequence_remove"
    bl_label = "Remove Sequence"
    bl_description = "Remove the sequence from the model and delete its actions"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = active_root(context)
        g = root.gem
        if not (0 <= g.sequence_index < len(g.sequences)):
            return {"CANCELLED"}
        name = g.sequences[g.sequence_index].name
        if root.get("gem_active_seq") == name:
            anim_io.activate_sequence(context, root, "")
        for act in anim_io.sequence_actions(root, name).values():
            bpy.data.actions.remove(act)
        g.sequences.remove(g.sequence_index)
        g.sequence_index = max(0, g.sequence_index - 1)
        return {"FINISHED"}


class GEM_OT_sequence_new_action(bpy.types.Operator):
    bl_idname = "gem.sequence_keyframe"
    bl_label = "Animate Selected Bones"
    bl_description = ("Give the selected bones an action for the active sequence and key their "
                      "current transform, so they can be animated in Blender")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = active_root(context)
        g = root.gem
        if not (0 <= g.sequence_index < len(g.sequences)):
            self.report({"ERROR"}, "Select a sequence first")
            return {"CANCELLED"}
        seq = g.sequences[g.sequence_index].name
        if root.get("gem_active_seq") != seq:
            anim_io.activate_sequence(context, root, seq)
        uid = anim_io.model_uid(root)
        acts = anim_io.sequence_actions(root, seq)
        n = 0
        for o in context.selected_objects:
            if o.gem.role != "BONE" or utils.find_model_root(o) != root:
                continue
            name = utils.gem_name(o)
            act = acts.get(name)
            if act is None:
                act = bpy.data.actions.new("%s|%s" % (seq, name))
                act.use_fake_user = True
                act["gem_seq"], act["gem_model"], act["gem_bone"] = seq, uid, name
            if not o.gem.has_rest:
                import_mdl.store_rest(o)
            o.rotation_mode = "QUATERNION"
            utils.action_fcurves(o, act)
            o.keyframe_insert("location", group="Transform")
            o.keyframe_insert("rotation_quaternion", group="Transform")
            n += 1
        self.report({"INFO"}, "%d bones keyed" % n)
        return {"FINISHED"}


class GEM_OT_sequence_load(bpy.types.Operator, ImportHelper):
    bl_idname = "gem.sequence_load"
    bl_label = "Load .anm"
    bl_description = "Load an animation file into the selected sequence"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".anm"
    filter_glob: StringProperty(default="*.anm", options={"HIDDEN"})

    def invoke(self, context, event):
        root = active_root(context)
        g = root.gem
        if 0 <= g.sequence_index < len(g.sequences) and g.mdl_path:
            s = g.sequences[g.sequence_index]
            self.filepath = os.path.join(os.path.dirname(g.mdl_path), s.file or s.name + ".anm")
        return super().invoke(context, event)

    def execute(self, context):
        root = active_root(context)
        g = root.gem
        seq = g.sequences[g.sequence_index].name if 0 <= g.sequence_index < len(g.sequences) else None
        try:
            missing = anim_io.import_anm(context, root, self.filepath, seq)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if missing:
            self.report({"WARNING"}, "Bones not in model: " + ", ".join(missing[:10]))
        return {"FINISHED"}


# Editing

class GEM_OT_new_model(bpy.types.Operator):
    bl_idname = "gem.new_model"
    bl_label = "New GEM Model"
    bl_description = "Create an empty model root; parent bones (objects) to it"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = bpy.data.objects.new("model", None)
        root.empty_display_type = "ARROWS"
        root.gem.role = "MODEL"
        context.collection.objects.link(root)
        import uuid
        root["gem_uid"] = uuid.uuid4().hex
        basis = bpy.data.objects.new("basis", None)
        basis.gem.role = "BONE"
        basis.gem.game_name = "basis"
        context.collection.objects.link(basis)
        utils.set_parent(basis, root)
        for o in context.view_layer.objects:
            o.select_set(False)
        context.view_layer.objects.active = root
        root.select_set(True)
        return {"FINISHED"}


def _parent_keep_world(obj, parent):
    world = obj.matrix_world.copy()
    utils.set_parent(obj, parent)
    obj.matrix_basis = parent.matrix_world.inverted_safe() @ world


class GEM_OT_make_bone(bpy.types.Operator):
    bl_idname = "gem.make_bone"
    bl_label = "Make Bones"
    bl_description = ("Turn the selected objects into bones of the active bone/model "
                      "(meshes become the bone's VolumeView)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        a = context.active_object
        return a is not None and a.gem.role in ("BONE", "MODEL")

    def execute(self, context):
        parent = context.active_object
        for o in context.selected_objects:
            if o is parent:
                continue
            g = o.gem
            g.role = "BONE"
            g.game_name = o.name
            if o.type == "MESH":
                g.has_view = True
                g.ply_file = g.ply_file or (o.name + ".ply")
            _parent_keep_world(o, parent)
            import_mdl.store_rest(o)
        return {"FINISHED"}


class GEM_OT_make_view(bpy.types.Operator):
    bl_idname = "gem.make_view"
    bl_label = "Attach as Extra Mesh"
    bl_description = "Attach the selected meshes to the active bone as sub-meshes or LOD levels"
    bl_options = {"REGISTER", "UNDO"}

    lod_level: IntProperty(name="LOD level", default=-1, min=-1,
                           description="-1 = sub-mesh, 0 = full detail, 1.. = lower detail")

    @classmethod
    def poll(cls, context):
        a = context.active_object
        return a is not None and a.gem.role == "BONE"

    def execute(self, context):
        bone = context.active_object
        for o in context.selected_objects:
            if o is bone or o.type != "MESH":
                continue
            g = o.gem
            g.role = "VIEW"
            g.owner = bone
            g.has_view = True
            g.lod_level = self.lod_level
            g.ply_file = g.ply_file or (o.name + ".ply")
            _parent_keep_world(o, bone)
        return {"FINISHED"}


class GEM_OT_add_volume(bpy.types.Operator):
    bl_idname = "gem.add_volume"
    bl_label = "Add Volume"
    bl_description = "Add a collision volume to the active bone, sized to its mesh"
    bl_options = {"REGISTER", "UNDO"}

    shape: EnumProperty(items=[("box", "Box", ""), ("cylinder", "Cylinder", ""), ("sphere", "Sphere", "")])

    @classmethod
    def poll(cls, context):
        a = context.active_object
        return a is not None and a.gem.role in ("BONE", "MODEL")

    def execute(self, context):
        bone = context.active_object
        size = Vector((1.0, 1.0, 1.0))
        center = Vector()
        if bone.type == "MESH" and bone.data.vertices:
            vs = [v.co for v in bone.data.vertices]
            mn = Vector((min(p.x for p in vs), min(p.y for p in vs), min(p.z for p in vs)))
            mx = Vector((max(p.x for p in vs), max(p.y for p in vs), max(p.z for p in vs)))
            size = mx - mn
            center = (mx + mn) * 0.5
        name = utils.gem_name(bone)
        if self.shape == "cylinder":
            me = mesh_io.cylinder_mesh(name, max(size.x, size.y) * 0.5, size.z)
        elif self.shape == "sphere":
            me = mesh_io.sphere_mesh(name, max(size) * 0.5)
        else:
            me = mesh_io.box_mesh(name, size)
        me.materials.append(mesh_io.volume_material())
        obj = bpy.data.objects.new(name, me)
        obj.display_type = "WIRE"
        obj.hide_render = True
        context.collection.objects.link(obj)
        obj.gem.role = "VOLUME"
        obj.gem.game_name = name
        obj.gem.vol_shape = self.shape
        utils.set_parent(obj, bone)
        obj.location = center
        return {"FINISHED"}


class GEM_OT_make_volume(bpy.types.Operator):
    bl_idname = "gem.make_volume"
    bl_label = "Make Polyhedron Volumes"
    bl_description = "Use the selected meshes as polyhedron collision volumes (.vol) of the active bone"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        a = context.active_object
        return a is not None and a.gem.role in ("BONE", "MODEL")

    def execute(self, context):
        bone = context.active_object
        for o in context.selected_objects:
            if o is bone or o.type != "MESH":
                continue
            g = o.gem
            g.role = "VOLUME"
            g.vol_shape = "polyhedron"
            g.game_name = o.name
            g.vol_file = g.vol_file or (o.name + ".vol")
            g.mesh_hash = ""
            o.display_type = "WIRE"
            _parent_keep_world(o, bone)
        return {"FINISHED"}


class GEM_OT_add_shape(bpy.types.Operator):
    bl_idname = "gem.add_shape"
    bl_label = "Add Obstacle/Area"
    bl_options = {"REGISTER", "UNDO"}

    tag: EnumProperty(items=[("Obstacle", "Obstacle", ""), ("Area", "Area", "")])

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        from .formats import mdl as mdlfmt
        s = mdlfmt.Shape2D(self.tag, self.tag.lower())
        s.extent = (10.0, 10.0)
        me = mesh_io.shape_mesh(s.name, s)
        obj = bpy.data.objects.new(s.name, me)
        obj.display_type = "WIRE"
        context.collection.objects.link(obj)
        obj.gem.role = "SHAPE"
        obj.gem.shape_tag = self.tag
        obj.gem.game_name = s.name
        utils.set_parent(obj, root)
        return {"FINISHED"}


class GEM_OT_set_rest(bpy.types.Operator):
    bl_idname = "gem.set_rest"
    bl_label = "Set Rest Transform"
    bl_description = "Use the current transform of the selected bones as their .mdl transform"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for o in context.selected_objects:
            if o.gem.role == "BONE":
                import_mdl.store_rest(o)
        return {"FINISHED"}


class GEM_OT_rest_pose(bpy.types.Operator):
    bl_idname = "gem.rest_pose"
    bl_label = "Rest Pose"
    bl_description = "Remove the active sequence from the bones and show the .mdl pose"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = active_root(context)
        if root:
            anim_io.activate_sequence(context, root, "")
        return {"FINISHED"}


classes = (
    GEM_OT_import_mdl, GEM_OT_import_ply, GEM_OT_import_vol, GEM_OT_import_anm,
    GEM_OT_export_mdl, GEM_OT_export_anm, GEM_OT_export_ply,
    GEM_OT_sequence_activate, GEM_OT_sequence_add, GEM_OT_sequence_remove, GEM_OT_sequence_new_action,
    GEM_OT_sequence_load, GEM_OT_new_model, GEM_OT_make_bone, GEM_OT_make_view, GEM_OT_add_volume,
    GEM_OT_make_volume, GEM_OT_add_shape, GEM_OT_set_rest, GEM_OT_rest_pose,
)


def menu_import(self, context):
    self.layout.operator(GEM_OT_import_mdl.bl_idname, text="GEM Model (.mdl)")
    self.layout.operator(GEM_OT_import_ply.bl_idname, text="GEM Mesh (.ply)")
    self.layout.operator(GEM_OT_import_vol.bl_idname, text="GEM Volume (.vol)")
    self.layout.operator(GEM_OT_import_anm.bl_idname, text="GEM Animation (.anm)")


def menu_export(self, context):
    self.layout.operator(GEM_OT_export_mdl.bl_idname, text="GEM Model (.mdl)")
    self.layout.operator(GEM_OT_export_ply.bl_idname, text="GEM Mesh (.ply)")
    self.layout.operator(GEM_OT_export_anm.bl_idname, text="GEM Animation (.anm)")


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
