"""Operator-level smoke test: build a model from scratch, then import/export through bpy.ops.

    blender -b --factory-startup --python-exit-code 1 -P tests/blender_operators.py -- OUT_DIR [model.mdl]

Uses the installed extension when it is enabled (see build_addon.py), otherwise
registers the add-on from this checkout.
"""

import os
import sys

import addon_utils
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[0]
src_model = argv[1] if len(argv) > 1 else None
os.makedirs(out, exist_ok=True)

name = next((m.__name__ for m in addon_utils.modules() if m.__name__.endswith("gemwerk_io")), None)
if name:
    addon_utils.enable(name, default_set=True)
    print("enabled installed extension", name)
else:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import io_gemwerk
    io_gemwerk.register()
    print("registered from checkout")

pkg = sys.modules[[k for k in sys.modules if k.endswith("gemwerk_io") or k == "io_gemwerk"][0]]
formats = __import__(pkg.__name__ + ".formats.mdl", fromlist=["x"])
plyfmt = __import__(pkg.__name__ + ".formats.ply", fromlist=["x"])
anmfmt = __import__(pkg.__name__ + ".formats.anm", fromlist=["x"])
volfmt = __import__(pkg.__name__ + ".formats.vol", fromlist=["x"])

fails = []


def check(c, msg):
    if not c:
        fails.append(msg)
        print("FAIL:", msg)


ctx = bpy.context
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)

# a model from scratch
bpy.ops.gem.new_model()
root = ctx.active_object
check(root.gem.role == "MODEL", "new_model did not create a root")
basis = next(o for o in root.children if o.gem.role == "BONE")

bpy.ops.mesh.primitive_cube_add(size=4.0, location=(0, 0, 2))
body = ctx.active_object
body.name = "body"
mat = bpy.data.materials.new("hull")
body.data.materials.append(mat)
bpy.ops.mesh.primitive_cylinder_add(radius=1.0, depth=3.0, location=(0, 0, 5))
turret = ctx.active_object
turret.name = "turret"
turret.data.materials.append(mat)

# body -> bone of basis
for o in ctx.view_layer.objects:
    o.select_set(False)
body.select_set(True)
basis.select_set(True)
ctx.view_layer.objects.active = basis
bpy.ops.gem.make_bone()
check(body.gem.role == "BONE" and body.parent == basis, "make_bone failed")

# turret -> revolute bone of body, with limits
for o in ctx.view_layer.objects:
    o.select_set(False)
turret.select_set(True)
body.select_set(True)
ctx.view_layer.objects.active = body
bpy.ops.gem.make_bone()
turret.gem.kind = "revolute"
turret.gem.use_limits = True
turret.gem.limits = (-90.0, 90.0)
turret.gem.use_parameters = True
turret.gem.parameters = "ID=turret;"

ctx.view_layer.objects.active = body
for o in ctx.view_layer.objects:
    o.select_set(False)
body.select_set(True)
bpy.ops.gem.add_volume(shape="box")
bpy.ops.gem.add_shape(tag="Obstacle")

# animation: rotate the turret in a new sequence
ctx.view_layer.objects.active = root
bpy.ops.gem.sequence_add()
root.gem.sequences[0].name = "rotate"
for o in ctx.view_layer.objects:
    o.select_set(False)
turret.select_set(True)
ctx.view_layer.objects.active = turret
ctx.scene.frame_set(0)
bpy.ops.gem.sequence_keyframe()
ctx.scene.frame_set(20)
turret.rotation_mode = "QUATERNION"
from mathutils import Quaternion  # noqa: E402
turret.rotation_quaternion = Quaternion((0, 0, 1), 1.0)
turret.keyframe_insert("rotation_quaternion")
ctx.scene.frame_start, ctx.scene.frame_end = 0, 20

mdl_path = os.path.join(out, "scratch", "scratch.mdl")
res = bpy.ops.export_scene.gem_mdl(filepath=mdl_path)
check(res == {"FINISHED"}, "export_scene.gem_mdl: %s" % res)
anm_path = os.path.join(out, "scratch", "rotate.anm")
res = bpy.ops.export_anim.gem_anm(filepath=anm_path)
check(res == {"FINISHED"}, "export_anim.gem_anm: %s" % res)

m = formats.Model.read(mdl_path)
names = [b.name for b, _ in m.walk_bones()]
print(open(mdl_path, encoding="cp1251").read())
check(names == ["basis", "body", "turret"], "bones %s" % names)
t = [b for b, _ in m.walk_bones() if b.name == "turret"][0]
check(t.kind == "revolute" and t.limits == [-90.0, 90.0] and t.parameters == "ID=turret;", "turret props")
check(abs(t.matrix[11] - 3.0) < 1e-4, "turret position z %s" % (t.matrix[9:12],))
check(len(m.volumes) == 1 and m.volumes[0].shape == "box" and m.volumes[0].bone == "body", "volume")
check(abs(m.volumes[0].size[0] - 4.0) < 1e-4, "volume size %s" % m.volumes[0].size)
check(len(m.shapes) == 1 and m.shapes[0].tag == "Obstacle", "obstacle")
check([s.name for s in m.sequences] == ["rotate"], "sequences")
p = plyfmt.read(os.path.join(out, "scratch", "body.ply"))
check(p.vert_count == 24 and len(p.indices) == 36, "body.ply %d verts %d idx" % (p.vert_count, len(p.indices)))
check(p.meshes[0].textures == ["hull.mtl"], "material name %s" % p.meshes[0].textures)
check(os.path.isfile(os.path.join(out, "scratch", "hull.mtl")), "hull.mtl not written")
a = anmfmt.read(anm_path)
check(a.bones == ["turret"] and a.frame_count == 21, "anm bones %s frames %d" % (a.bones, a.frame_count))
tr = a.evaluate()[0]
# rotation of 1 rad about Z, mirrored into GEM space: angle flips sign
r = tr[20][0]
check(abs(r[0] - 0.5403) < 1e-3 and abs(abs(r[1]) - 0.8415) < 1e-3, "anm rotation %s" % (r[:9],))

# import operators on a real model
if src_model:
    res = bpy.ops.import_scene.gem_mdl(filepath=src_model)
    check(res == {"FINISHED"}, "import_scene.gem_mdl %s" % res)
    r2 = ctx.active_object
    check(r2 is not None and r2.gem.role == "MODEL", "import did not select the root")
    if r2.gem.sequences:
        bpy.ops.gem.sequence_activate(name=r2.gem.sequences[0].name)
        bpy.ops.gem.rest_pose()
    res = bpy.ops.export_scene.gem_mdl(filepath=os.path.join(out, "op_export", os.path.basename(src_model)))
    check(res == {"FINISHED"}, "export after import %s" % res)

print("Blender", bpy.app.version_string, "operators:", "OK" if not fails else fails)
sys.exit(1 if fails else 0)
