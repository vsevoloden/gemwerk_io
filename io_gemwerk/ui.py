"""Sidebar panels (3D View > N > GEM) and the material panel."""

import bpy

from . import utils
from .operators import active_root


class GEM_UL_sequences(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        root = data.id_data
        active = root.get("gem_active_seq") == item.name
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="PLAY" if active else "ACTION")
        op = row.operator("gem.sequence_activate", text="", icon="RESTRICT_VIEW_OFF" if active else "HIDE_OFF",
                          emboss=False)
        op.name = item.name


class GEM_PT_model(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEM"
    bl_label = "GEM Model"

    def draw(self, context):
        layout = self.layout
        root = active_root(context)
        row = layout.row(align=True)
        row.operator("import_scene.gem_mdl", text="Import", icon="IMPORT")
        row.operator("export_scene.gem_mdl", text="Export", icon="EXPORT")
        if root is None:
            layout.operator("gem.new_model", icon="ADD")
            layout.label(text="Select an object of a GEM model")
            return
        g = root.gem
        layout.label(text=root.name, icon="OUTLINER_OB_EMPTY")
        if g.mdl_path:
            layout.label(text=bpy.path.basename(g.mdl_path), icon="FILE")

        box = layout.box()
        box.label(text="Animation sequences", icon="ACTION")
        row = box.row()
        row.template_list("GEM_UL_sequences", "", g, "sequences", g, "sequence_index", rows=4)
        col = row.column(align=True)
        col.operator("gem.sequence_add", text="", icon="ADD")
        col.operator("gem.sequence_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("gem.rest_pose", text="", icon="ARMATURE_DATA")
        if 0 <= g.sequence_index < len(g.sequences):
            s = g.sequences[g.sequence_index]
            col = box.column(align=True)
            col.prop(s, "file")
            row = col.row(align=True)
            row.prop(s, "use_speed", text="")
            sub = row.row()
            sub.active = s.use_speed
            sub.prop(s, "speed")
            row = col.row(align=True)
            row.prop(s, "use_smooth", text="")
            sub = row.row()
            sub.active = s.use_smooth
            sub.prop(s, "smooth")
            row = col.row(align=True)
            row.prop(s, "resume", toggle=True)
            row.prop(s, "autostart", toggle=True)
            row.prop(s, "store", toggle=True)
            row = box.row(align=True)
            row.operator("gem.sequence_load", icon="FILE_FOLDER")
            row.operator("export_anim.gem_anm", text="Save .anm", icon="EXPORT")
            box.operator("gem.sequence_keyframe", icon="KEY_HLT")

        col = layout.column(align=True)
        col.label(text="Add")
        row = col.row(align=True)
        row.operator("gem.add_shape", text="Obstacle").tag = "Obstacle"
        row.operator("gem.add_shape", text="Area").tag = "Area"


class GEM_PT_object(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEM"
    bl_label = "GEM Object"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        g = obj.gem
        layout.prop(g, "role")
        if g.role in ("BONE", "VOLUME", "SHAPE", "VIEW"):
            row = layout.row()
            row.label(text="Game name: " + utils.gem_name(obj))
        if g.role == "BONE":
            self.draw_bone(layout, obj)
        elif g.role == "VIEW":
            layout.prop(g, "owner")
            self.draw_view(layout, obj)
        elif g.role == "VOLUME":
            self.draw_volume(layout, obj)
        elif g.role == "SHAPE":
            col = layout.column()
            col.prop(g, "shape_tag")
            col.prop(g, "shape_kind")
            self._opt(col, g, "use_tags", "tags")
            col.prop(g, "rotate")
            col.prop(g, "extra")
        elif g.role == "MODEL":
            layout.prop(g, "mdl_path")
            layout.prop(g, "header")
            layout.prop(g, "extra")
        elif g.role == "NONE":
            layout.label(text="Use the tools below to add it to a model")

        if g.role in ("BONE", "MODEL"):
            box = layout.box()
            box.label(text="Selected objects -> active", icon="LINKED")
            col = box.column(align=True)
            col.operator("gem.make_bone", icon="BONE_DATA")
            if g.role == "BONE":
                col.operator("gem.make_view", text="Attach as sub-mesh").lod_level = -1
                col.operator("gem.make_view", text="Attach as LOD 1").lod_level = 1
            col.operator("gem.make_volume", icon="MESH_ICOSPHERE")
            row = box.row(align=True)
            row.operator("gem.add_volume", text="Box").shape = "box"
            row.operator("gem.add_volume", text="Cylinder").shape = "cylinder"
            row.operator("gem.add_volume", text="Sphere").shape = "sphere"

    @staticmethod
    def _opt(layout, g, use, prop, **kw):
        row = layout.row(align=True)
        row.prop(g, use, text="")
        sub = row.row()
        sub.active = getattr(g, use)
        sub.prop(g, prop, **kw)

    def draw_bone(self, layout, obj):
        g = obj.gem
        col = layout.column()
        col.prop(g, "kind")
        self._opt(col, g, "use_parameters", "parameters")
        if g.kind != "NONE":
            row = col.row(align=True)
            row.prop(g, "use_limits", text="")
            sub = row.row(align=True)
            sub.active = g.use_limits
            sub.prop(g, "limits_count", text="")
            if g.limits_count >= 1:
                sub.prop(g, "limits", index=0, text="Min" if g.limits_count == 2 else "Limit")
            if g.limits_count == 2:
                sub.prop(g, "limits", index=1, text="Max")
            row = col.row(align=True)
            row.prop(g, "use_speed", text="")
            sub = row.row(align=True)
            sub.active = g.use_speed
            sub.prop(g, "speed")
            sub.prop(g, "speed2", toggle=True)
        self._opt(col, g, "use_visibility", "visibility")
        self._opt(col, g, "use_color", "color")
        col.prop(g, "terminator")
        col.prop(g, "extra")
        col.operator("gem.set_rest", icon="PINNED")
        if g.has_view or obj.type == "MESH":
            box = layout.box()
            box.prop(g, "has_view", text="Mesh is a VolumeView")
            if g.has_view:
                self.draw_view(box, obj)
        box = layout.box()
        box.label(text="LODView")
        box.prop(g, "lod_off")

    def draw_view(self, layout, obj):
        g = obj.gem
        col = layout.column()
        col.prop(g, "ply_file")
        col.prop(g, "lod_level")
        flow = col.grid_flow(columns=2, align=True)
        for p in ("no_cast_shadows", "decal_target", "no_group_mesh", "no_get_shadows", "ground"):
            flow.prop(g, p, toggle=True)
        col.prop(g, "layer")
        col.prop(g, "view_extra")
        if g.skinned:
            col.label(text="Skinned mesh (weights = vertex groups)", icon="MOD_ARMATURE")

    def draw_volume(self, layout, obj):
        g = obj.gem
        col = layout.column()
        col.prop(g, "vol_shape")
        if g.vol_shape == "polyhedron":
            col.prop(g, "vol_file")
        parent = obj.parent
        col.label(text="Bone: " + (utils.gem_name(parent) if parent and parent.gem.role == "BONE" else "-"))
        self._opt(col, g, "use_component", "component")
        self._opt(col, g, "use_tags", "tags")
        self._opt(col, g, "use_density", "density")
        col.prop(g, "thickness")
        col.prop(g, "extra")


class GEM_PT_material(bpy.types.Panel):
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"
    bl_label = "GEM"

    @classmethod
    def poll(cls, context):
        return context.material is not None

    def draw(self, context):
        g = context.material.gem
        col = self.layout.column()
        col.prop(g, "mtl_name")
        col.prop(g, "texture2")
        flow = col.grid_flow(columns=2, align=True)
        for p in ("two_sided", "player_color", "bump", "light", "shadow"):
            flow.prop(g, p, toggle=True)
        row = col.row(align=True)
        row.prop(g, "flags")
        row.prop(g, "fvf")


classes = (GEM_UL_sequences, GEM_PT_model, GEM_PT_object, GEM_PT_material)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
