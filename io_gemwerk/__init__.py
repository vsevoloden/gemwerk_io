"""GemWerk for Blender: import, edit and export Men of War (GEM engine) models.

Read and write .mdl skeletons, .ply meshes with materials and skinning, 
.vol collision volumes, obstacles/areas and .anm animations.
"""

bl_info = {
    "name": "GemWerk (Men of War models)",
    "author": "vsevoloden; based on SOEdit by Brian A. Kay and contributors",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "File > Import/Export > GEM; 3D View > Sidebar > GEM",
    "description": "Import/export GEM engine .mdl, .ply, .vol and .anm files",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    for _m in ("formats.sdl", "formats.ply", "formats.vol", "formats.anm", "formats.mdl",
               "formats.mtl", "formats.resources", "utils", "props", "mesh_io", "import_mdl",
               "anim_io", "export_mdl", "operators", "ui", "translations"):
        _mod = __import__(__name__ + "." + _m, fromlist=["_"])
        importlib.reload(_mod)

import bpy  # noqa: E402
from bpy.props import StringProperty  # noqa: E402

from . import operators, props, translations, ui, utils  # noqa: E402


class GemPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    resource_roots: StringProperty(
        name="Resource folders",
        description=("Extra folders searched for textures and meshes, separated by ';' "
                     "(a game or mod folder, or its 'resource' subfolder). The model's own mod "
                     "and the game folder next to 'mods' are found automatically"),
        default="",
    )

    def draw(self, context):
        self.layout.prop(self, "resource_roots")
        self.layout.label(text=".pak archives are not read: extract the files you need first.",
                          icon="INFO")


def register():
    bpy.utils.register_class(GemPreferences)
    props.register()
    operators.register()
    ui.register()
    utils._init_interp()
    try:
        bpy.app.translations.register(__package__, translations.translations_dict())
    except ValueError:
        pass


def unregister():
    try:
        bpy.app.translations.unregister(__package__)
    except (ValueError, RuntimeError):
        pass
    ui.unregister()
    operators.unregister()
    props.unregister()
    bpy.utils.unregister_class(GemPreferences)
