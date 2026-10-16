"""Import -> export -> re-import inside Blender (background mode).

    blender -b --factory-startup --python-exit-code 1 -P tests/blender_roundtrip.py -- OUT_DIR model.mdl [model2.mdl ...]

Checks per model:
  * the re-exported .mdl describes the same model (bones, matrices, views, volumes...)
  * unedited meshes are copied byte for byte
  * rebuilt meshes keep their triangles' world positions
  * the animations re-exported from Blender match the original .anm tracks
  * the skinned mesh deforms with its bones
"""

import os
import shutil
import sys
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import io_gemwerk  # noqa: E402
from io_gemwerk import anim_io, export_mdl, import_mdl, mesh_io  # noqa: E402
from io_gemwerk.formats import anm, mdl, ply  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def clear_scene():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.armatures, bpy.data.materials,
                 bpy.data.actions, bpy.data.images, bpy.data.collections):
        for item in list(coll):
            coll.remove(item)


def model_files(model):
    files = set()
    for b, _ in model.walk_bones():
        for v in b.views + (b.lods or []):
            files.add(v.file.lower())
    return files


def tri_world_points(path):
    p = ply.read(path)
    pos = p.vertices()["pos"]
    return sorted(tuple(round(c, 3) for c in pos[i]) for t in p.triangles() for i in t if i < len(pos))


def run_model(src, out_dir):
    name = os.path.splitext(os.path.basename(src))[0]
    print("=" * 70, "\n", src)
    clear_scene()
    ctx = bpy.context
    root, warnings = import_mdl.import_mdl(ctx, src)
    for w in warnings:
        print("  warning:", w)
    bones = anim_io.bone_objects(root)
    orig = mdl.Model.read(src)
    n_bones = sum(1 for _ in orig.walk_bones())
    check(len(bones) == n_bones, "%s: %d bone objects, %d in file" % (name, len(bones), n_bones))

    # Normals agree with the face winding after the Y mirror
    flipped = total = 0
    for o in bpy.data.objects:
        if o.type != "MESH" or o.gem.role not in ("BONE", "VIEW") or not o.data.polygons:
            continue
        me = o.data
        cn = mesh_io._loop_normals(me)
        for poly in me.polygons[:200]:
            total += 1
            avg = sum((cn[li] for li in poly.loop_indices), start=cn[0] * 0)
            if avg.dot(poly.normal) < 0:
                flipped += 1
    check(total == 0 or flipped < 0.05 * total, "%s: %d/%d faces wound against their normals" % (name, flipped, total))

    # 1) plain export: unedited meshes are copied
    d1 = os.path.join(out_dir, name + "_copy")
    shutil.rmtree(d1, ignore_errors=True)
    ex = export_mdl.export_mdl(ctx, root, os.path.join(d1, name + ".mdl"))
    for w in ex.warnings:
        print("  export warning:", w)
    out = mdl.Model.read(os.path.join(d1, name + ".mdl"))
    check(same(orig.summary(), out.summary()), "%s: re-exported .mdl differs" % name)
    for f in model_files(orig):
        s = os.path.join(os.path.dirname(src), *f.split("/"))
        d = os.path.join(d1, *f.split("/"))
        if os.path.isfile(s):
            check(os.path.isfile(d) and open(s, "rb").read() == open(d, "rb").read(),
                  "%s: %s not copied unchanged" % (name, f))

    # 2) rebuild all meshes: same triangle geometry
    d2 = os.path.join(out_dir, name + "_rebuilt")
    shutil.rmtree(d2, ignore_errors=True)
    export_mdl.export_mdl(ctx, root, os.path.join(d2, name + ".mdl"), force_meshes=True)
    for f in sorted(model_files(orig)):
        s = os.path.join(os.path.dirname(src), *f.split("/"))
        d = os.path.join(d2, *f.split("/"))
        if not os.path.isfile(s):
            continue
        try:
            ps, pd = ply.read(s), ply.read(d)
        except Exception as e:
            check(False, "%s: %s unreadable after rebuild: %s" % (name, f, e))
            continue
        a, b = set(tri_world_points(s)), set(tri_world_points(d))
        missing = len(a - b)
        check(missing <= max(2, len(a) // 100), "%s: %s lost %d/%d positions" % (name, f, missing, len(a)))
        check(len(ps.meshes) == len(pd.meshes), "%s: %s material sections %d -> %d" % (
            name, f, len(ps.meshes), len(pd.meshes)))
        check((ps.skin is None) == (pd.skin is None), "%s: %s skin lost" % (name, f))
        sva, svb = ps.vertices(), pd.vertices()
        check(len(sva["tangent"]) == 0 or len(svb["tangent"]) > 0, "%s: %s tangents lost" % (name, f))
    out2 = mdl.Model.read(os.path.join(d2, name + ".mdl"))
    check(same(orig.summary(), out2.summary()), "%s: rebuilt .mdl differs" % name)

    # 3) animations: Blender -> .anm reproduces the original tracks
    for s in orig.sequences[:4]:
        p = import_mdl.resolve_model_file(s.anm_file(), src, import_mdl.resource_index(ctx, src))
        if not p:
            continue
        try:
            a = anm.read(p)
        except anm.AnmError as e:
            print("  skip", s.name, e)
            continue
        anim_io.activate_sequence(ctx, root, s.name)
        bpy.context.scene.frame_start = 0
        bpy.context.scene.frame_end = a.frame_count - 1
        outp = os.path.join(out_dir, "%s_%s.anm" % (name, s.name))
        anim_io.export_anm(ctx, root, outp)
        b = anm.read(outp)
        ta, tb = a.evaluate(), b.evaluate()
        names_b = {n.lower(): i for i, n in enumerate(b.bones)}
        worst = 0.0
        for bi, track in ta.items():
            j = names_b.get(a.bones[bi].lower())
            if j is None:
                if a.bones[bi].lower() in {bb.gem.game_name.lower() for bb in bones}:
                    check(False, "%s/%s: bone %s missing from export" % (name, s.name, a.bones[bi]))
                continue
            for f, (x, y) in enumerate(zip(track, tb[j])):
                if x is None or y is None:
                    continue
                worst = max(worst, max(abs(p - q) for p, q in zip(x[0], y[0])))
                check(x[1] == y[1], "%s/%s: visibility differs" % (name, s.name))
        print("  anim %-14s frames %4d bones %3d max matrix error %.2e" % (s.name, a.frame_count, len(ta), worst))
        check(worst < 2e-3, "%s/%s: animation error %.4f" % (name, s.name, worst))
    anim_io.activate_sequence(ctx, root, "")

    # 4) skinned mesh follows its bones
    skins = [o for o in bpy.data.objects if o.gem.skinned]
    for o in skins:
        dg = ctx.evaluated_depsgraph_get()
        before = [v.co.copy() for v in o.evaluated_get(dg).to_mesh().vertices]
        o.evaluated_get(dg).to_mesh_clear()
        check(max((v.co - b).length for v, b in zip(o.data.vertices, before)) < 1e-3,
              "%s: skinned mesh moves in rest pose" % name)
        # Rotate the bone with most weight: the skin must follow it
        counts = {}
        for v in o.data.vertices:
            for ge in v.groups:
                counts[ge.group] = counts.get(ge.group, 0) + 1
        if not counts:
            continue
        gname = o.vertex_groups[max(counts, key=counts.get)].name
        bone = next((b for b in bones if b.gem.game_name.lower() == gname.lower()), None)
        if bone is None:
            continue
        saved = bone.matrix_basis.copy()
        bone.rotation_mode = "XYZ"
        bone.rotation_euler.z += 0.5
        ctx.view_layer.update()
        dg = ctx.evaluated_depsgraph_get()
        after = [v.co.copy() for v in o.evaluated_get(dg).to_mesh().vertices]
        o.evaluated_get(dg).to_mesh_clear()
        bone.matrix_basis = saved
        moved = max((a - b).length for a, b in zip(after, before))
        print("  skin %s: rotating bone %s moves vertices up to %.3f" % (o.name, gname, moved))
        check(moved > 1e-3, "%s: skin does not follow bone %s" % (name, gname))
    return root


def same(a, b, eps=2e-4):
    """Compare summaries with a float tolerance; prints the differences."""
    diffs = []
    _cmp(a, b, "", eps, diffs)
    for d in diffs[:10]:
        print("   diff at", d)
    return not diffs


def _cmp(x, y, path, eps, out):
    if isinstance(x, float) and isinstance(y, float):
        if abs(x - y) > eps * max(1.0, abs(x)):
            out.append("%s: %r vs %r" % (path, x, y))
    elif isinstance(x, tuple) and isinstance(y, tuple) and len(x) == len(y):
        for i, (p, q) in enumerate(zip(x, y)):
            _cmp(p, q, path + "/%d" % i, eps, out)
    elif x != y:
        out.append("%s: %s vs %s" % (path, repr(x)[:200], repr(y)[:200]))


def _diff(x, y, path=""):
    if x == y:
        return
    if isinstance(x, tuple) and isinstance(y, tuple) and len(x) == len(y):
        for i, (p, q) in enumerate(zip(x, y)):
            _diff(p, q, path + "/%d" % i)
    else:
        print("   diff at", path, "\n     ", repr(x)[:300], "\n   vs", repr(y)[:300])


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    out_dir = argv[0]
    os.makedirs(out_dir, exist_ok=True)
    io_gemwerk.register()
    try:
        for src in argv[1:]:
            try:
                run_model(src, out_dir)
            except Exception:
                traceback.print_exc()
                failures.append("%s: exception" % src)
    finally:
        io_gemwerk.unregister()
    print("=" * 70)
    print("Blender", bpy.app.version_string, "-", "OK" if not failures else "%d failures" % len(failures))
    for f in failures[:50]:
        print("  ", f)
    sys.exit(1 if failures else 0)


main()
