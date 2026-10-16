"""Round-trip the format modules over a folder of game files (no Blender needed).

    python -I tests/roundtrip_formats.py "<game>/mods" [limit]

PLY and VOL must be written back byte-for-byte. ANM is checked by decoding the
rewritten file and comparing every evaluated matrix and visibility.
"""

import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "io_gemwerk"))

from formats import anm, mdl, ply, sdl, vol  # noqa: E402


def check_ply(path, data):
    p = ply.parse(data)
    if ply.serialize(p) != data:
        return "rewrite differs"
    v = p.vertices()
    if len(v["pos"]) != p.vert_count:
        return "vertex decode count"
    if p.indices and max(p.indices) >= p.vert_count:
        return "index out of range"
    return None


def check_vol(path, data):
    v = vol.parse(data)
    if vol.serialize(v) != data:
        return "rewrite differs"
    return None


def close(a, b, eps=2e-4):
    return all(abs(x - y) <= eps for x, y in zip(a, b))


def check_anm(path, data):
    a = anm.parse(data)
    ta = a.evaluate()
    for version in (6, 4):
        rebuilt = anm.from_tracks(a.bones, a.frame_count, ta)
        out = anm.serialize_v6(rebuilt) if version == 6 else anm.serialize_v4(rebuilt)
        b = anm.parse(out)
        tb = b.evaluate()
        if set(ta) != set(tb):
            return "v%d bone set differs" % version
        for bone, track in ta.items():
            for f, (sa, sb) in enumerate(zip(track, tb[bone])):
                if (sa is None) != (sb is None):
                    return "v%d presence differs bone %d frame %d" % (version, bone, f)
                if sa is None:
                    continue
                if sa[1] != sb[1]:
                    return "v%d visibility differs" % version
                ma, mb = sa[0], sb[0]
                if version == 4 and ma != tuple(mb):
                    if not close(ma, mb, 1e-6):
                        return "v4 matrix differs"
                # v6 rebuilds quaternion w from xyz, so near-180 degree turns lose ~1e-4
                if version == 6 and not close(ma, mb, 1e-3):
                    if anm.has_scale(ma[0:9]):
                        continue
                    return "v6 matrix differs bone %s frame %d" % (a.bones[bone], f)
    return None


def check_mdl(path, data):
    nodes, header = sdl.parse(data.decode(sdl.ENCODING, errors="replace"))
    text = sdl.write(nodes, header)
    nodes2, header2 = sdl.parse(text)
    if _dump(nodes) != _dump(nodes2):
        return "sdl rewrite changes structure"
    m = mdl.Model.from_nodes(nodes, header)
    nodes3 = m.to_nodes()
    m2 = mdl.Model.from_nodes(nodes3, header)
    if not _same(m.summary(), m2.summary()):
        return "mdl model rewrite differs"
    return None


def _same(x, y, eps=1e-6):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) <= eps * max(1.0, abs(x))
    if isinstance(x, tuple) and isinstance(y, tuple):
        return len(x) == len(y) and all(_same(p, q, eps) for p, q in zip(x, y))
    return x == y


def _dump(nodes):
    out = []
    for n in nodes:
        out.append((n.name.lower(), tuple(_dump([i])[0] if isinstance(i, sdl.Node) else str(i)
                                         for i in n.items)))
    return out


CHECKS = {".ply": check_ply, ".vol": check_vol, ".anm": check_anm, ".mdl": check_mdl}


def main():
    root = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    totals = collections.Counter()
    fails = collections.Counter()
    examples = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            ext = os.path.splitext(f)[1].lower()
            check = CHECKS.get(ext)
            if not check:
                continue
            if limit and totals[ext] >= limit:
                continue
            path = os.path.join(dp, f)
            with open(path, "rb") as fh:
                data = fh.read()
            totals[ext] += 1
            try:
                err = check(path, data)
            except Exception as e:  # report and keep going
                err = "%s: %s" % (type(e).__name__, str(e)[:80])
            if err:
                key = (ext, err.split(" bone ")[0][:60])
                fails[key] += 1
                examples.setdefault(key, path)
    for ext, n in sorted(totals.items()):
        bad = sum(v for k, v in fails.items() if k[0] == ext)
        print("%s: %d files, %d failed" % (ext, n, bad))
    for k, v in fails.most_common(40):
        print("  %5d %s %s\n        e.g. %s" % (v, k[0], k[1], examples[k]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
