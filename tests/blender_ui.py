"""Draw the GEM panels in a real Blender window and save a screenshot.

    blender --factory-startup -P tests/blender_ui.py -- OUT.png model.mdl
"""

import os
import sys
import traceback

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
shot, model = argv[0], argv[1]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import io_gemwerk  # noqa: E402

errors = []


def step1():
    try:
        io_gemwerk.register()
        # The sidebar tab can't be picked from Python: show the panels in the default tab
        from io_gemwerk import ui
        for cls in (ui.GEM_PT_model, ui.GEM_PT_object):
            bpy.utils.unregister_class(cls)
            cls.bl_category = "Item"
            bpy.utils.register_class(cls)
        bpy.ops.import_scene.gem_mdl(filepath=model)
        root = bpy.context.view_layer.objects.active
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.show_region_ui = True
                with bpy.context.temp_override(area=area, region=[r for r in area.regions if r.type == "WINDOW"][0]):
                    bpy.ops.view3d.view_all()
        bone = next(o for o in bpy.data.objects if o.gem.role == "BONE" and o.gem.kind != "NONE")
        bpy.context.view_layer.objects.active = bone
        bone.select_set(True)
        if root.gem.sequences:
            bpy.ops.gem.sequence_activate(name=root.gem.sequences[0].name)
    except Exception:
        errors.append(traceback.format_exc())
    return None


def step2():
    try:
        bpy.ops.screen.screenshot(filepath=shot)
    except Exception:
        errors.append(traceback.format_exc())
    for e in errors:
        print(e)
    print("UI test:", "OK" if not errors else "FAILED")
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(step1, first_interval=1.0)
bpy.app.timers.register(step2, first_interval=6.0)
