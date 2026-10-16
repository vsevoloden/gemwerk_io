"""GEM data stored on Blender objects and materials (obj.gem, material.gem)."""

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)

from .formats import ply as plyfmt

ROLES = [
    ("NONE", "None", "Not part of a GEM model"),
    ("MODEL", "Model", "Root of an imported .mdl"),
    ("BONE", "Bone", "Skeleton bone; a mesh bone carries its main VolumeView"),
    ("VIEW", "Extra mesh", "Additional VolumeView of a bone: sub-mesh or LOD"),
    ("VOLUME", "Volume", "Collision volume"),
    ("SHAPE", "Obstacle/Area", "2D obstacle or area"),
]

BONE_KINDS = [
    ("NONE", "Fixed", "Plain bone"),
    ("revolute", "Revolute", "Rotating joint"),
    ("prizmatic", "Prizmatic", "Sliding joint"),
    ("socket", "Socket", "Ball joint"),
]

VOLUME_SHAPES = [
    ("box", "Box", ""),
    ("cylinder", "Cylinder", ""),
    ("sphere", "Sphere", ""),
    ("polyhedron", "Polyhedron", "Mesh stored in a .vol file"),
]

SHAPE_TAGS = [("Obstacle", "Obstacle", ""), ("Area", "Area", "")]
SHAPE_KINDS = [("Obb2", "Rectangle", ""), ("Circle2", "Circle", ""), ("Polygon2", "Polygon", "")]


class GemSequence(bpy.types.PropertyGroup):
    # name: built-in
    file: StringProperty(name="File", description="Animation file, empty = <name>.anm")
    use_speed: BoolProperty(name="Speed")
    speed: FloatProperty(name="Speed", default=1.0)
    use_smooth: BoolProperty(name="Smooth")
    smooth: FloatProperty(name="Smooth", default=0.0)
    resume: BoolProperty(name="Resume")
    autostart: BoolProperty(name="Autostart")
    store: BoolProperty(name="Store")
    extra: StringProperty(name="Extra", description="Other blocks, kept as written in the .mdl")


class GemObject(bpy.types.PropertyGroup):
    role: EnumProperty(name="Role", items=ROLES, default="NONE")
    game_name: StringProperty(name="Name in game", description="Name written to the .mdl")
    order: IntProperty(default=1 << 20, description="Position among siblings in the file")
    extra: StringProperty(name="Extra blocks", description="Unrecognised blocks, written back as is")

    # model
    mdl_path: StringProperty(name="MDL", subtype="FILE_PATH")
    header: StringProperty(name="Header comments")
    sequences: CollectionProperty(type=GemSequence)
    sequence_index: IntProperty()
    skeleton_extra: StringProperty()
    animation_extra: StringProperty()
    has_skeleton: BoolProperty(default=True)

    # bone
    kind: EnumProperty(name="Type", items=BONE_KINDS, default="NONE")
    parameters: StringProperty(name="Parameters")
    use_parameters: BoolProperty(name="Parameters")
    use_limits: BoolProperty(name="Limits")
    limits: FloatVectorProperty(name="Limits", size=2)
    limits_count: IntProperty(name="Values", min=0, max=2, default=2)
    use_speed: BoolProperty(name="Speed")
    speed: FloatProperty(name="Speed", default=1.0)
    speed2: BoolProperty(name="Speed2", description="Speed in m/s and degrees instead of 1/20 m and radians")
    use_visibility: BoolProperty(name="Visibility")
    visibility: IntProperty(name="Visibility", default=1)
    terminator: BoolProperty(name="Terminator")
    use_color: BoolProperty(name="Color")
    color: StringProperty(name="Color", default="0xffffffff")
    rest: FloatVectorProperty(size=16, description="Local matrix from the .mdl (used while animated)")
    has_rest: BoolProperty()
    src_matrix: FloatVectorProperty(size=12, description="Matrix exactly as read from the file")
    src_basis: FloatVectorProperty(size=16, description="matrix_basis right after import")
    has_src: BoolProperty()
    lod_off: BoolProperty(name="LOD OFF", description="{OFF} at the end of LODView")
    lod_extra: StringProperty()

    # mesh (VolumeView)
    has_view: BoolProperty(description="Object mesh is a VolumeView")
    owner: PointerProperty(type=bpy.types.Object, name="Bone",
                           description="Bone this extra mesh belongs to")
    ply_file: StringProperty(name="PLY file", description="Path written to VolumeView, relative to the .mdl")
    lod_level: IntProperty(name="LOD", default=-1, min=-1,
                           description="-1 = regular (sub-)mesh, 0.. = level inside LODView")
    view_extra: StringProperty(name="VolumeView blocks", description="Blocks inside VolumeView")
    no_cast_shadows: BoolProperty(name="NoCastShadows")
    decal_target: BoolProperty(name="DecalTarget")
    no_group_mesh: BoolProperty(name="NoGroupMesh")
    no_get_shadows: BoolProperty(name="NoGetShadows")
    ground: BoolProperty(name="Ground")
    layer: StringProperty(name="Layer")
    source_file: StringProperty(subtype="FILE_PATH", description="File the mesh was imported from")
    mesh_hash: StringProperty(description="Geometry hash at import; unchanged meshes are copied, not rebuilt")
    ply_signature: StringProperty(default="EPLY")
    bind: FloatVectorProperty(size=16, description="Skin: bone matrix the vertices are bound to")
    skinned: BoolProperty()

    # volume
    vol_shape: EnumProperty(name="Shape", items=VOLUME_SHAPES, default="box")
    vol_file: StringProperty(name="VOL file")
    vol_size: FloatVectorProperty(size=3, description="Box/cylinder/sphere size as read from the file")
    component: StringProperty(name="Component")
    use_component: BoolProperty(name="Component")
    tags: StringProperty(name="Tags")
    use_tags: BoolProperty(name="Tags")
    density: StringProperty(name="Density")
    use_density: BoolProperty(name="Density")
    thickness: StringProperty(name="Thickness", description="{Thickness ...} block as text")

    # obstacle / area
    shape_tag: EnumProperty(name="Kind", items=SHAPE_TAGS, default="Obstacle")
    shape_kind: EnumProperty(name="Shape", items=SHAPE_KINDS, default="Obb2")
    rotate: BoolProperty(name="Rotate")
    shape_extra: StringProperty()


def _flag_prop(bit, name, desc=""):
    def get(self):
        return bool(self.flags & bit)

    def set_(self, value):
        self.flags = (self.flags | bit) if value else (self.flags & ~bit)

    return BoolProperty(name=name, description=desc, get=get, set=set_)


class GemMaterial(bpy.types.PropertyGroup):
    mtl_name: StringProperty(name="Material file", description="Name stored in the .ply (e.g. body.mtl)")
    texture2: StringProperty(name="Second texture", description="BPLY second texture name")
    flags: IntProperty(name="Flags", default=plyfmt.FLAG_MATERIAL | plyfmt.FLAG_LIGHT)
    fvf: IntProperty(name="FVF", default=plyfmt.FVF_STATIC)
    specular: IntProperty(name="Specular colour")
    initialized: BoolProperty()
    two_sided: _flag_prop(plyfmt.FLAG_TWO_SIDED, "Two sided")
    player_color: _flag_prop(plyfmt.FLAG_PLCR, "Player colour")
    bump: _flag_prop(plyfmt.FLAG_BUMP, "Bump (tangents)")
    light: _flag_prop(plyfmt.FLAG_LIGHT, "Light")
    shadow: _flag_prop(plyfmt.FLAG_SHADOW, "Shadow volume")


classes = (GemSequence, GemObject, GemMaterial)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.gem = PointerProperty(type=GemObject)
    bpy.types.Material.gem = PointerProperty(type=GemMaterial)


def unregister():
    del bpy.types.Material.gem
    del bpy.types.Object.gem
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
