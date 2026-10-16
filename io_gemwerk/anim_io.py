"""Animations: .anm files <-> per-bone Blender actions.

Each sequence becomes one action per animated bone object, tagged with
action["gem_seq"] / ["gem_model"] / ["gem_bone"]. Switching the active sequence
assigns those actions to the bone objects; bones without a track go back to
their .mdl (rest) transform.
"""

import os

import bpy


from . import utils
from .formats import anm as anmfmt


def bone_objects(root):
    """All BONE objects under root, depth first in file order."""
    out = []

    def walk(o):
        for c in utils.iter_children(o):
            if c.gem.role == "BONE":
                out.append(c)
                walk(c)
    walk(root)
    return out


def _rest(obj):
    from .import_mdl import list_to_matrix
    if obj.gem.has_rest:
        return list_to_matrix(obj.gem.rest)
    return obj.matrix_basis.copy()


def model_uid(root):
    uid = root.get("gem_uid")
    if not uid:
        import uuid
        uid = root["gem_uid"] = uuid.uuid4().hex
    return uid


def sequence_actions(root, seq):
    uid = model_uid(root)
    return {a.get("gem_bone"): a for a in bpy.data.actions
            if a.get("gem_model") == uid and a.get("gem_seq") == seq}


def import_anm(context, root, path, seq=None, activate=True):
    a = anmfmt.read(path)
    seq = seq or os.path.splitext(os.path.basename(path))[0]
    uid = model_uid(root)
    tracks = a.evaluate()
    bones = {utils.gem_name(o).lower(): o for o in bone_objects(root)}

    # Replace actions of an earlier import of the same sequence
    for act in list(sequence_actions(root, seq).values()):
        bpy.data.actions.remove(act)

    missing = []
    frames = list(range(a.frame_count))
    for bi, track in tracks.items():
        name = a.bones[bi]
        obj = bones.get(name.lower())
        if obj is None:
            missing.append(name)
            continue
        rest = _rest(obj)
        locs, rots, scales, vis = [], [], [], []
        prev_q = None
        for sample in track:
            if sample is None:
                m, v = rest, 1
            else:
                m, v = utils.gem_to_matrix(sample[0]), sample[1]
            loc, q, sc = m.decompose()   # mirrored matrices get a negative scale
            if prev_q is not None and prev_q.dot(q) < 0:
                q.negate()
            prev_q = q
            locs.append(loc)
            rots.append(q)
            scales.append(sc)
            vis.append(v)

        action = bpy.data.actions.new("%s|%s" % (seq, name))
        action.use_fake_user = True
        action["gem_seq"] = seq
        action["gem_model"] = uid
        action["gem_bone"] = utils.gem_name(obj)
        obj.rotation_mode = "QUATERNION"
        fcs = utils.action_fcurves(obj, action)
        for i in range(3):
            utils.add_fcurve(fcs, "location", i, frames, [p[i] for p in locs], group="Transform")
        for i in range(4):
            utils.add_fcurve(fcs, "rotation_quaternion", i, frames, [q[i] for q in rots], group="Transform")
        if any(abs(s[i] - 1.0) > 1e-5 for s in scales for i in range(3)):
            for i in range(3):
                utils.add_fcurve(fcs, "scale", i, frames, [s[i] for s in scales], group="Transform")
        if any(v == 0 for v in vis):
            hidden = [0.0 if v else 1.0 for v in vis]
            utils.add_fcurve(fcs, "hide_viewport", 0, frames, hidden, "CONSTANT", group="Visibility")
            utils.add_fcurve(fcs, "hide_render", 0, frames, hidden, "CONSTANT", group="Visibility")
        if not activate:
            obj.animation_data.action = None

    seqs = root.gem.sequences
    if seq not in seqs:
        item = seqs.add()
        item.name = seq
        base = os.path.basename(path)
        mdl_dir = os.path.dirname(root.gem.mdl_path) if root.gem.mdl_path else ""
        if base.lower() != (seq + ".anm").lower() or (mdl_dir and os.path.normcase(os.path.dirname(path)) != os.path.normcase(mdl_dir)):
            item.file = base
    root["gem_frames_" + seq] = a.frame_count
    if activate:
        activate_sequence(context, root, seq)
    return missing


def activate_sequence(context, root, seq):
    acts = sequence_actions(root, seq) if seq else {}
    for obj in bone_objects(root):
        act = acts.get(utils.gem_name(obj))
        ad = obj.animation_data
        if act is not None:
            obj.rotation_mode = "QUATERNION"
            utils.action_fcurves(obj, act)
        else:
            if ad is not None and ad.action is not None:
                ad.action = None
            if obj.gem.has_rest:
                obj.matrix_basis = _rest(obj)
            obj.hide_viewport = False
            obj.hide_render = False
    root["gem_active_seq"] = seq or ""
    n = root.get("gem_frames_" + seq) if seq else None
    if n:
        context.scene.frame_start = 0
        context.scene.frame_end = max(0, int(n) - 1)
        context.scene.frame_set(0)


def export_anm(context, root, path, version=6, frame_start=None, frame_end=None,
               only_animated=True):
    scene = context.scene
    fs = scene.frame_start if frame_start is None else frame_start
    fe = scene.frame_end if frame_end is None else frame_end
    objs = bone_objects(root)
    if only_animated:
        objs = [o for o in objs if o.animation_data and o.animation_data.action]
    if not objs:
        raise ValueError("No animated bones under %s" % root.name)
    names = [utils.gem_name(o) for o in objs]
    tracks = {i: [] for i in range(len(objs))}
    cur = scene.frame_current
    try:
        for f in range(fs, fe + 1):
            scene.frame_set(f)
            for i, o in enumerate(objs):
                m = utils.matrix_to_gem(utils.local_matrix(o))
                tracks[i].append((m, 0 if o.hide_render else 1))
    finally:
        scene.frame_set(cur)
    a = anmfmt.from_tracks(names, fe - fs + 1, tracks)
    if version == 6 and any(anmfmt.has_scale(s[0][0:9]) for t in tracks.values() for s in t):
        version = 4   # v6 stores rotation only
    anmfmt.write(path, a, version)
    return version
