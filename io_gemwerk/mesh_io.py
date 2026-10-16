"""PLY / VOL <-> Blender mesh data."""

import hashlib
import os

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import utils
from .formats import mtl as mtlfmt
from .formats import ply as plyfmt
from .formats import resources
from .formats import vol as volfmt

VOL_SIDE_ATTR = "gem_side"


# Materials

def _load_image(path):
    for img in bpy.data.images:
        if img.filepath and os.path.normcase(bpy.path.abspath(img.filepath)) == os.path.normcase(path):
            return img
    try:
        return bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        return None


def make_material(section, ply_path, res, bply=False):
    """Blender material for one MESH section, reusing materials with the same file name."""
    name = section.textures[0] if section.textures else "no_material"
    key = (name.lower(), section.flags, section.fvf)
    for m in bpy.data.materials:
        g = m.gem
        if g.initialized and (g.mtl_name.lower(), g.flags, g.fvf) == key and m.get("gem_dir") == os.path.dirname(ply_path).lower():
            return m
    mat = bpy.data.materials.new(os.path.splitext(os.path.basename(name))[0] or "gem")
    g = mat.gem
    g.initialized = True
    g.mtl_name = name
    g.flags = section.flags
    g.fvf = section.fvf
    spec = section.specular or 0
    g.specular = spec - (1 << 32) if spec >= (1 << 31) else spec   # IntProperty is signed
    g.texture2 = section.textures[1] if len(section.textures) > 1 else ""
    mat["gem_dir"] = os.path.dirname(ply_path).lower()

    diffuse = bump = None
    blend = "none"
    exts = (("",) + resources.TEXTURE_EXTS + (".mtl",)) if "." not in os.path.basename(name) else ("",)
    found = res.resolve(name, ply_path, exts) if name else None
    if found and found.lower().endswith(".mtl"):
        try:
            m = mtlfmt.read(found)
        except (OSError, ValueError):
            m = None
        if m:
            blend = m.blend
            if m.get("diffuse"):
                diffuse = res.resolve(m.get("diffuse"), found, resources.TEXTURE_EXTS + ("",))
            if m.get("bump"):
                bump = res.resolve(m.get("bump"), found, resources.TEXTURE_EXTS + ("",))
        mat["gem_mtl_path"] = found
    elif found:
        diffuse = found
    _build_nodes(mat, diffuse, bump, blend, section.flags)
    return mat


def _build_nodes(mat, diffuse, bump, blend, flags):
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return
    bsdf.inputs["Roughness"].default_value = 0.8
    if diffuse:
        img = _load_image(diffuse)
        if img:
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            tex.location = (-400, 300)
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if blend in ("test", "blend"):
                nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
                if hasattr(mat, "blend_method"):
                    try:
                        mat.blend_method = "CLIP" if blend == "test" else "BLEND"
                    except TypeError:
                        pass
                if hasattr(mat, "surface_render_method") and blend == "blend":
                    mat.surface_render_method = "BLENDED"
    if bump:
        img = _load_image(bump)
        if img:
            img.colorspace_settings.name = "Non-Color"
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            tex.location = (-700, -200)
            nm = nt.nodes.new("ShaderNodeNormalMap")
            nm.location = (-350, -200)
            nt.links.new(tex.outputs["Color"], nm.inputs["Color"])
            nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    mat.use_backface_culling = not (flags & plyfmt.FLAG_TWO_SIDED)


def volume_material():
    mat = bpy.data.materials.get("GEM Volume")
    if mat is None:
        mat = bpy.data.materials.new("GEM Volume")
        mat.diffuse_color = (1.0, 0.8, 0.0, 0.35)
    return mat


# PLY -> Blender

def _section_of_faces(ply, nfaces):
    sec = np.zeros(nfaces, dtype=np.int32)
    for i, m in enumerate(ply.meshes):
        a = max(0, m.first_face)
        b = min(nfaces, m.first_face + m.face_count)
        if b > a:
            sec[a:b] = i
    return sec


def ply_to_mesh(ply, name, materials, merge=True):
    """Build a bpy mesh. Returns (mesh, info dict)."""
    v = ply.vertices()
    nverts = len(v["pos"])
    pos = np.array(v["pos"], dtype=np.float64).reshape(-1, 3)
    pos[:, 1] *= -1.0
    tris = np.array(ply.indices[:len(ply.indices) // 3 * 3], dtype=np.int64).reshape(-1, 3)
    face_sec = _section_of_faces(ply, len(tris))
    if len(tris):
        ok = (tris < nverts).all(axis=1)
        tris, face_sec = tris[ok], face_sec[ok]

    # Skin weights per original vertex: list of (bone name, weight)
    skin_w = None
    if ply.skin is not None and (v["weights"] or v["indices"]):
        vert_sec = np.full(nverts, -1, dtype=np.int64)
        for t, s in zip(tris, face_sec):
            for i in t:
                if vert_sec[i] < 0:
                    vert_sec[i] = s
        skin_w = []
        for i in range(nverts):
            ws = list(v["weights"][i]) if v["weights"] else []
            ws.append(max(0.0, 1.0 - sum(ws)))
            idx = v["indices"][i] if v["indices"] else (0, 0, 0, 0)
            sec = ply.meshes[vert_sec[i]] if vert_sec[i] >= 0 and ply.meshes else None
            sub = sec.subskin if sec is not None and sec.subskin else None
            pairs = {}
            for k, w in enumerate(ws):
                if w <= 1e-6 or k >= 4:
                    continue
                gi = sub[idx[k]] if sub and idx[k] < len(sub) else idx[k]
                if gi < len(ply.skin):
                    nm = ply.skin[gi]
                    pairs[nm] = pairs.get(nm, 0.0) + w
            skin_w.append(tuple(sorted(pairs.items())))

    # Weld split vertices (seams) so the mesh is editable
    if merge and nverts:
        keys = {}
        remap = np.empty(nverts, dtype=np.int64)
        for i in range(nverts):
            k = (pos[i, 0], pos[i, 1], pos[i, 2], skin_w[i] if skin_w else None)
            j = keys.get(k)
            if j is None:
                j = keys[k] = len(keys)
            remap[i] = j
        nweld = len(keys)
        wpos = np.zeros((nweld, 3))
        wpos[remap] = pos
    else:
        remap = np.arange(nverts)
        wpos = pos
        nweld = nverts

    faces = remap[tris] if len(tris) else tris
    good = []
    seen = set()
    for fi, f in enumerate(faces):
        a, b, c = int(f[0]), int(f[1]), int(f[2])
        if a == b or b == c or a == c:
            continue
        key = tuple(sorted((a, b, c)))
        if key in seen:
            continue
        seen.add(key)
        good.append(fi)
    good = np.array(good, dtype=np.int64)
    faces = faces[good] if len(good) else np.zeros((0, 3), dtype=np.int64)
    orig_corners = tris[good].reshape(-1) if len(good) else np.zeros(0, dtype=np.int64)
    face_sec = face_sec[good] if len(good) else face_sec[:0]

    me = bpy.data.meshes.new(name)
    me.vertices.add(nweld)
    me.vertices.foreach_set("co", wpos.astype(np.float32).ravel())
    nf = len(faces)
    me.loops.add(nf * 3)
    me.loops.foreach_set("vertex_index", faces.astype(np.int32).ravel())
    me.polygons.add(nf)
    me.polygons.foreach_set("loop_start", np.arange(0, nf * 3, 3, dtype=np.int32))

    for m in materials:
        me.materials.append(m)
    if nf:
        me.polygons.foreach_set("material_index", face_sec.astype(np.int32))

    # UVs (GEM v goes down, Blender up)
    for u in range(len(v["uv"])):
        uvl = me.uv_layers.new(name="UVMap" if u == 0 else "UVMap%d" % (u + 1))
        uv = np.array(v["uv"][u], dtype=np.float32).reshape(-1, 2)
        if len(orig_corners):
            c = uv[orig_corners].copy()
            c[:, 1] = 1.0 - c[:, 1]
            uvl.data.foreach_set("uv", c.ravel())

    # Vertex colours (D3DCOLOR = ARGB)
    if v["diffuse"] and any(d != 0xFFFFFFFF for d in v["diffuse"]):
        d = np.array(v["diffuse"], dtype=np.uint32)
        rgba = np.stack([(d >> 16) & 255, (d >> 8) & 255, d & 255, d >> 24], axis=1).astype(np.float32) / 255.0
        attr = me.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
        if len(orig_corners):
            attr.data.foreach_set("color", rgba[orig_corners].ravel())

    me.update()
    me.validate(clean_customdata=False)

    # Custom normals from the file
    if v["normal"] and len(orig_corners) == len(me.loops):
        n = np.array(v["normal"], dtype=np.float64).reshape(-1, 3)
        n[:, 1] *= -1.0
        ln = n[orig_corners]
        lens = np.linalg.norm(ln, axis=1)
        lens[lens == 0] = 1.0
        ln = (ln / lens[:, None]).astype(np.float32)
        try:
            if hasattr(me, "use_auto_smooth"):
                me.use_auto_smooth = True
            me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), dtype=bool))
            me.normals_split_custom_set([tuple(x) for x in ln])
        except (RuntimeError, TypeError, AttributeError):
            pass

    info = {"weights": None}
    if skin_w is not None:
        welded = [None] * nweld
        for i in range(nverts):
            if welded[remap[i]] is None:
                welded[remap[i]] = skin_w[i]
        info["weights"] = welded
    return me, info


def apply_weights(obj, weights, bone_order):
    groups = {}
    for name in bone_order:
        groups[name] = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
    for vi, pairs in enumerate(weights):
        for name, w in pairs or ():
            g = groups.get(name) or obj.vertex_groups.new(name=name)
            groups[name] = g
            g.add([vi], w, "REPLACE")


# Hash: detects whether a mesh was edited since import

def mesh_hash(obj):
    me = obj.data
    h = hashlib.md5()
    co = np.zeros(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    h.update(co.tobytes())
    li = np.zeros(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", li)
    h.update(li.tobytes())
    mi = np.zeros(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("material_index", mi)
    h.update(mi.tobytes())
    for uvl in me.uv_layers:
        uv = np.zeros(len(me.loops) * 2, dtype=np.float32)
        uvl.data.foreach_get("uv", uv)
        h.update(uv.tobytes())
    for m in me.materials:
        if m:
            g = m.gem
            h.update(("%s|%s|%d|%d|%d" % (g.mtl_name, g.texture2, g.flags, g.fvf, g.specular)).encode())
    for g in obj.vertex_groups:
        h.update(g.name.encode())
    if obj.vertex_groups and len(me.vertices) < 200000:
        for vtx in me.vertices:
            for ge in vtx.groups:
                h.update(np.array((ge.group, ge.weight), dtype=np.float32).tobytes())
    return h.hexdigest()


# Blender -> PLY

def _loop_normals(me):
    if hasattr(me, "corner_normals"):
        n = np.zeros(len(me.loops) * 3, dtype=np.float32)
        me.corner_normals.foreach_get("vector", n)
        return n.reshape(-1, 3)
    if hasattr(me, "calc_normals_split"):
        me.calc_normals_split()
    n = np.zeros(len(me.loops) * 3, dtype=np.float32)
    me.loops.foreach_get("normal", n)
    return n.reshape(-1, 3)


def mesh_to_ply(obj, matrix=None, skinned=False, signature="EPLY", depsgraph=None,
                apply_modifiers=True, report=None):
    """Build a Ply from obj. `matrix` maps object-space points into the bone space."""
    if apply_modifiers and depsgraph is not None and not skinned:
        ev = obj.evaluated_get(depsgraph)
        me = ev.to_mesh()
        owner = ev
    else:
        me = obj.data
        owner = None
    try:
        return _mesh_to_ply(obj, me, matrix, skinned, signature, report)
    finally:
        if owner is not None:
            owner.to_mesh_clear()


def _mesh_to_ply(obj, me, matrix, skinned, signature, report):
    me.calc_loop_triangles()
    ntri = len(me.loop_triangles)
    if ntri == 0:
        raise ValueError("%s has no faces" % obj.name)
    mat = matrix if matrix is not None else Matrix.Identity(4)
    nmat = mat.to_3x3().inverted_safe().transposed()

    co = np.zeros(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    co = (np.array(mat.to_3x3()) @ co.T).T + np.array(mat.translation)

    tri_loops = np.zeros(ntri * 3, dtype=np.int32)
    me.loop_triangles.foreach_get("loops", tri_loops)
    tri_mat = np.zeros(ntri, dtype=np.int32)
    me.loop_triangles.foreach_get("material_index", tri_mat)
    loop_vert = np.zeros(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", loop_vert)

    ln = _loop_normals(me).astype(np.float64)
    ln = (np.array(nmat) @ ln.T).T
    lens = np.linalg.norm(ln, axis=1)
    lens[lens == 0] = 1.0
    ln /= lens[:, None]

    uv_layers = []
    for uvl in list(me.uv_layers)[:2]:
        uv = np.zeros(len(me.loops) * 2, dtype=np.float32)
        uvl.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2).astype(np.float64)
        uv[:, 1] = 1.0 - uv[:, 1]
        uv_layers.append(uv)
    if not uv_layers:
        uv_layers.append(np.zeros((len(me.loops), 2)))
        if report:
            report({"WARNING"}, "%s has no UV map" % obj.name)

    # Materials -> sections
    slots = [s.material for s in obj.material_slots] or [None]
    used = sorted(set(int(i) for i in tri_mat))
    sections = []
    for mi in used:
        m = slots[mi] if mi < len(slots) else None
        sec = plyfmt.MeshSection()
        if m is not None:
            g = m.gem       # defaults for new materials: FVF_STATIC, MATERIAL|LIGHT
            sec.textures = [g.mtl_name or (m.name + ".mtl")]
            if g.texture2:
                sec.textures.append(g.texture2)
            sec.flags = g.flags
            sec.fvf = g.fvf
            sec.specular = g.specular & 0xFFFFFFFF if g.flags & plyfmt.FLAG_SPECULAR else None
        else:
            name = (m.name if m else "default") + ".mtl"
            sec.textures = [name]
        sections.append(sec)

    # Unify the vertex format across sections
    if skinned:
        fvf = plyfmt.FVF_SKINNED
        for s in sections:
            s.flags |= plyfmt.FLAG_SKINNED | plyfmt.FLAG_SUBSKIN
            s.fvf = fvf
    else:
        fvf = 0
        for s in sections:
            s.flags &= ~(plyfmt.FLAG_SKINNED | plyfmt.FLAG_SUBSKIN)
            f = s.fvf & ~(plyfmt.FVF_POSITION_MASK | plyfmt.FVF_LASTBETA_UBYTE4 | plyfmt.FVF_LASTBETA_D3DCOLOR)
            f |= plyfmt.FVF_XYZ | plyfmt.FVF_NORMAL
            if not f & plyfmt.FVF_TEXCOUNT_MASK:
                f |= 0x100
            fvf |= f
        for s in sections:
            s.fvf = fvf
    bump = any(s.flags & plyfmt.FLAG_BUMP for s in sections)

    # Vertex colours
    col = None
    if fvf & plyfmt.FVF_DIFFUSE and me.color_attributes:
        ca = me.color_attributes.active_color or me.color_attributes[0]
        c = np.zeros(len(ca.data) * 4, dtype=np.float32)
        ca.data.foreach_get("color", c)
        c = (np.clip(c.reshape(-1, 4), 0, 1) * 255 + 0.5).astype(np.uint32)
        argb = (c[:, 3] << 24) | (c[:, 0] << 16) | (c[:, 1] << 8) | c[:, 2]
        col = argb if ca.domain == "CORNER" else argb[loop_vert]

    # Skin weights per Blender vertex: top two influences
    vweights = None
    skin_names = None
    if skinned:
        gname = {g.index: g.name for g in obj.vertex_groups}
        vweights = []
        used_names = []
        for vtx in me.vertices:
            ws = sorted(((ge.weight, gname.get(ge.group)) for ge in vtx.groups
                         if ge.weight > 1e-5 and gname.get(ge.group)), reverse=True)[:2]
            if not ws:
                ws = [(1.0, None)]
            tot = sum(w for w, _ in ws) or 1.0
            ws = [(w / tot, n) for w, n in ws]
            vweights.append(ws)
            for _, n in ws:
                if n is not None and n not in used_names:
                    used_names.append(n)
        # SKIN order: vertex group order of the object (keeps the original order on re-export)
        order = [g.name for g in obj.vertex_groups if g.name in used_names]
        skin_names = order or ["skin"]

    # Build unique vertices per section
    tri_loops = tri_loops.reshape(-1, 3)
    order_tris = np.argsort(tri_mat, kind="stable")
    keymap = {}
    out_pos, out_nrm, out_uv, out_col, out_w, out_corner = [], [], [[] for _ in uv_layers], [], [], []
    triangles = []
    sec_of_mat = {mi: i for i, mi in enumerate(used)}
    sec_bones = [[] for _ in sections]
    for s in sections:
        s.face_count = 0
    first = 0
    cur_sec = -1
    for t in order_tris:
        si = sec_of_mat[int(tri_mat[t])]
        if si != cur_sec:
            if cur_sec >= 0:
                sections[cur_sec].face_count = len(triangles) - first
            cur_sec = si
            first = len(triangles)
            sections[si].first_face = first
        tri = []
        for li in tri_loops[t]:
            vi = int(loop_vert[li])
            n = ln[li]
            uvk = tuple(round(float(u[li][k]), 6) for u in uv_layers for k in (0, 1))
            k = (si, vi, round(float(n[0]), 4), round(float(n[1]), 4), round(float(n[2]), 4), uvk,
                 int(col[li]) if col is not None else 0)
            idx = keymap.get(k)
            if idx is None:
                idx = keymap[k] = len(out_pos)
                p = co[vi]
                out_pos.append((float(p[0]), -float(p[1]), float(p[2])))
                out_nrm.append((float(n[0]), -float(n[1]), float(n[2])))
                for ui, u in enumerate(uv_layers):
                    out_uv[ui].append((float(u[li][0]), float(u[li][1])))
                if col is not None:
                    out_col.append(int(col[li]))
                if vweights is not None:
                    ws = vweights[vi]
                    loc = []
                    for w, nm in ws:
                        gi = skin_names.index(nm) if nm in skin_names else 0
                        if gi not in sec_bones[si]:
                            sec_bones[si].append(gi)
                        loc.append((w, sec_bones[si].index(gi)))
                    w0 = loc[0][0]
                    i0 = loc[0][1]
                    i1 = loc[1][1] if len(loc) > 1 else 0
                    out_w.append(((w0,), (i0, i1, 0, 0)))
            tri.append(idx)
        triangles.append(tuple(tri))
    if cur_sec >= 0:
        sections[cur_sec].face_count = len(triangles) - first
    if skinned:
        for s, bones in zip(sections, sec_bones):
            if len(bones) > 255:
                raise ValueError("%s: one material uses more than 255 bones" % obj.name)
            s.subskin = bones
    if len(skin_names or []) > 255:
        raise ValueError("%s: more than 255 skin bones" % obj.name)

    tangents = _tangents(out_pos, out_nrm, out_uv[0], triangles) if bump else None
    ply = plyfmt.build(sections, out_pos, out_nrm, out_uv, triangles, skin=skin_names,
                       weights=out_w if skinned else None, tangents=tangents,
                       diffuse=out_col if col is not None else None, signature=signature)
    return ply


def _tangents(pos, nrm, uv, tris):
    """Per-vertex tangents in GEM space: T = dP/du, w = sign((N x T) . dP/dv)."""
    P = np.array(pos)
    N = np.array(nrm)
    U = np.array(uv)
    T = np.zeros_like(P)
    B = np.zeros_like(P)
    t = np.array(tris)
    e1 = P[t[:, 1]] - P[t[:, 0]]
    e2 = P[t[:, 2]] - P[t[:, 0]]
    d1 = U[t[:, 1]] - U[t[:, 0]]
    d2 = U[t[:, 2]] - U[t[:, 0]]
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    ok = np.abs(r) > 1e-12
    r[~ok] = 1.0
    ft = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
    fb = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / r[:, None]
    ft[~ok] = 0
    fb[~ok] = 0
    for k in range(3):
        np.add.at(T, t[:, k], ft)
        np.add.at(B, t[:, k], fb)
    T = T - N * np.sum(N * T, axis=1)[:, None]
    ln = np.linalg.norm(T, axis=1)
    bad = ln < 1e-9
    # Fallback tangent perpendicular to the normal
    alt = np.cross(N, np.array([0.0, 0.0, 1.0]))
    alt2 = np.cross(N, np.array([0.0, 1.0, 0.0]))
    alt[np.linalg.norm(alt, axis=1) < 1e-6] = alt2[np.linalg.norm(alt, axis=1) < 1e-6]
    T[bad] = alt[bad]
    T /= np.linalg.norm(T, axis=1)[:, None]
    w = np.sign(np.sum(np.cross(N, T) * B, axis=1))
    w[w == 0] = 1.0
    return [(float(a), float(b), float(c), float(d)) for (a, b, c), d in zip(T, w)]


# Volumes

def vol_to_mesh(vol, name):
    me = bpy.data.meshes.new(name)
    verts = [utils.gem_point(p) for p in vol.verts]
    tris = [t for t in vol.triangles() if max(t) < len(verts) and len(set(t)) == 3]
    me.from_pydata(verts, [], tris)
    if vol.sides and len(tris) == len(me.polygons):
        attr = me.attributes.new(VOL_SIDE_ATTR, "INT", "FACE")
        sides = [s for t, s in zip(vol.triangles(), vol.sides) if max(t) < len(verts) and len(set(t)) == 3]
        attr.data.foreach_set("value", sides)
    me.update()
    return me


def mesh_to_vol(obj, matrix=None):
    me = obj.data
    me.calc_loop_triangles()
    mat = matrix if matrix is not None else Matrix.Identity(4)
    vol = volfmt.Vol()
    vol.verts = [utils.point_to_gem(mat @ v.co) for v in me.vertices]
    if len(vol.verts) > 0xFFFF:
        raise ValueError("%s: volume has more than 65535 vertices" % obj.name)
    attr = me.attributes.get(VOL_SIDE_ATTR)
    for lt in me.loop_triangles:
        vol.indices += [lt.vertices[0], lt.vertices[1], lt.vertices[2]]
        side = attr.data[lt.polygon_index].value if attr is not None and attr.domain == "FACE" else 0
        vol.sides.append(max(0, min(255, side)))
    return vol


def box_mesh(name, size):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    bm.to_mesh(me)
    bm.free()
    return me


def cylinder_mesh(name, radius, length):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=radius, radius2=radius, depth=length)
    bm.to_mesh(me)
    bm.free()
    return me


def sphere_mesh(name, radius):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=radius)
    bm.to_mesh(me)
    bm.free()
    return me


def shape_mesh(name, shape):
    """Flat mesh for an obstacle / area (z = 0)."""
    import math
    me = bpy.data.meshes.new(name)
    if shape.kind == "Circle2":
        n = 24
        c = shape.center
        verts = [(c[0] + shape.radius * math.cos(2 * math.pi * i / n),
                  -(c[1] + shape.radius * math.sin(2 * math.pi * i / n)), 0.0) for i in range(n)]
    elif shape.kind == "Polygon2":
        verts = [(x, -y, 0.0) for x, y in shape.vertices]
    else:
        ax = Vector((shape.axis[0], shape.axis[1]))
        if ax.length < 1e-9:
            ax = Vector((1.0, 0.0))
        ax.normalize()
        ay = Vector((-ax.y, ax.x))
        c = Vector(shape.center)
        ex, ey = shape.extent
        pts = [c + ax * ex + ay * ey, c - ax * ex + ay * ey, c - ax * ex - ay * ey, c + ax * ex - ay * ey]
        verts = [(p.x, -p.y, 0.0) for p in pts]
    faces = [list(range(len(verts)))] if len(verts) >= 3 else []
    me.from_pydata(verts, [], faces)
    me.update()
    return me
