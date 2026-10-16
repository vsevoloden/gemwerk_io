"""GEM .anm skeletal animation (EANM)

Two encodings exist:
  v4 (0x00040000)  FRMN/BONE/MATR/VISI chunks with full 4x3 matrices
  v6 (0x00060000)  FRM2 frames with packed keys: position + quaternion xyz (w <= 0)

Matrices are 12 floats in GEM row order: three axis rows then the translation row
(a point is transformed as p' = p.x*row0 + p.y*row1 + p.z*row2 + row3).
An animation key replaces the bone's local matrix from the .mdl.
"""

import math
import struct

ENCODING = "cp1251"

B_POSITION = 0x01
B_ORIENTATION = 0x02
B_LEFT_HANDED = 0x04
B_VISIBLE_ON = 0x08
B_VISIBLE_OFF = 0x10
B_MESH = 0x20

VERSIONS = (0x00030000, 0x00040000, 0x00050000, 0x00060000, 0x00060001)
IDENTITY3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


class AnmError(Exception):
    pass


class Key:
    __slots__ = ("pos", "rot", "quat", "left_handed", "visible")

    def __init__(self):
        self.pos = None          # (x,y,z)
        self.rot = None          # 9 floats, rows
        self.quat = None         # v6 only: stored (x,y,z) for exact rewrite
        self.left_handed = False
        self.visible = None      # None / 0 / 1


class Anm:
    def __init__(self):
        self.version = 0x00060000
        self.frame_count = 1
        self.bones = []          # names
        self.keys = {}           # frame -> {bone index: Key}

    def evaluate(self):
        """Dense per-bone tracks

        Returns {bone index: [(matrix12, visible) or None per frame]}; None means the
        bone is not driven at that frame (it keeps its .mdl transform).
        """
        tracks = {}
        for b in range(len(self.bones)):
            pos = rot = vis = None
            seen = False
            out = []
            for f in range(self.frame_count):
                k = self.keys.get(f, {}).get(b)
                if k is not None:
                    seen = True
                    if k.pos is not None:
                        pos = k.pos
                    if k.rot is not None:
                        rot = k.rot
                    elif f == 0:
                        rot = IDENTITY3
                    if k.visible is not None:
                        vis = k.visible
                if not seen:
                    out.append(None)
                    continue
                r = rot or IDENTITY3
                p = pos or (0.0, 0.0, 0.0)
                out.append((tuple(r) + tuple(p), 1 if vis is None else vis))
            if seen:
                tracks[b] = out
        return tracks


# Quaternion helpers (same formulas as CAnimBone::Process)

def quat_to_rows(qx, qy, qz):
    w = -math.sqrt(abs(1.0 - (qx * qx + qy * qy + qz * qz)))
    r2 = w * w + qx * qx + qy * qy + qz * qz
    s = 2.0 / r2 if r2 else 0.0
    xs, ys, zs = qx * s, qy * s, qz * s
    xx, xy, xz = qx * xs, qx * ys, qx * zs
    xw, yy, yz = w * xs, qy * ys, qy * zs
    yw, zz, zw = w * ys, qz * zs, w * zs
    return (1 - (yy + zz), xy - zw, xz + yw,
            xy + zw, 1 - (xx + zz), yz - xw,
            xz - yw, yz + xw, 1 - (xx + yy))


def rows_to_quat(r):
    """Inverse of quat_to_rows for an orthonormal right-handed 3x3; returns (x,y,z) with w <= 0."""
    m00, m01, m02, m10, m11, m12, m20, m21, m22 = r
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    if w > 0:
        x, y, z = -x, -y, -z
    return (x, y, z)


def det3(r):
    return (r[0] * (r[4] * r[8] - r[5] * r[7]) - r[1] * (r[3] * r[8] - r[5] * r[6])
            + r[2] * (r[3] * r[7] - r[4] * r[6]))


def _flip_row2(r):
    return tuple(r[:6]) + (-r[6], -r[7], -r[8])


# Reading

def read(path):
    with open(path, "rb") as f:
        return parse(f.read())


def parse(data):
    if data[:4] != b"EANM":
        raise AnmError("not a GEM animation")
    anm = Anm()
    anm.version, = struct.unpack_from("<I", data, 4)
    if anm.version not in VERSIONS:
        raise AnmError("unsupported animation version %#x" % anm.version)
    pos = 8
    if data[pos:pos + 4] != b"FRMS":
        raise AnmError("FRMS section missing")
    anm.frame_count, = struct.unpack_from("<i", data, pos + 4)
    pos += 8
    if data[pos:pos + 4] != b"BMAP":
        raise AnmError("BMAP section missing")
    n, = struct.unpack_from("<i", data, pos + 4)
    pos += 8
    for _ in range(n):
        ln, = struct.unpack_from("<i", data, pos)
        anm.bones.append(data[pos + 4:pos + 4 + ln].decode(ENCODING, errors="replace"))
        pos += 4 + ln
    frame = 0
    key = None
    end = len(data)
    while pos + 4 <= end:
        tag = data[pos:pos + 4]
        pos += 4
        if tag == b"FRMN":
            frame, = struct.unpack_from("<i", data, pos)
            pos += 4
        elif tag == b"BONE":
            b, = struct.unpack_from("<i", data, pos)
            pos += 4
            key = Key()
            anm.keys.setdefault(frame, {})[b] = key
        elif tag == b"MATR":
            m = struct.unpack_from("<12f", data, pos)
            pos += 48
            key.rot = m[0:9]
            key.pos = m[9:12]
        elif tag == b"VISI":
            v, = struct.unpack_from("<i", data, pos)
            pos += 4
            key.visible = 1 if v else 0
        elif tag == b"FRM2":
            frame, nchunks = struct.unpack_from("<HB", data, pos)
            pos += 3
            fk = anm.keys.setdefault(frame, {})
            for _ in range(nchunks):
                b, flags = struct.unpack_from("<BH", data, pos)
                pos += 3
                if flags & B_MESH or flags > 63:
                    raise AnmError("animation contains vertex (mesh) keys - not supported")
                k = Key()
                if flags & B_POSITION:
                    k.pos = struct.unpack_from("<3f", data, pos)
                    pos += 12
                if flags & B_ORIENTATION:
                    q = struct.unpack_from("<3f", data, pos)
                    pos += 12
                    k.quat = q
                    k.rot = quat_to_rows(*q)
                    if flags & B_LEFT_HANDED:
                        k.left_handed = True
                        k.rot = _flip_row2(k.rot)
                if flags & B_VISIBLE_ON:
                    k.visible = 1
                elif flags & B_VISIBLE_OFF:
                    k.visible = 0
                fk[b] = k
        elif tag == b"MESH":
            raise AnmError("animation contains vertex (mesh) keys - not supported")
        else:
            raise AnmError("unknown tag %r at %#x" % (tag, pos - 4))
    return anm


# Writing

def _f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


def from_tracks(bone_names, frame_count, tracks):
    """Build an Anm from dense tracks {bone index: [(matrix12, visible) per frame]}."""
    anm = Anm()
    anm.bones = list(bone_names)
    anm.frame_count = frame_count
    for b, track in tracks.items():
        for f, sample in enumerate(track):
            if sample is None:
                continue
            m, vis = sample
            k = Key()
            k.rot = tuple(m[0:9])
            k.pos = tuple(m[9:12])
            k.visible = vis
            anm.keys.setdefault(f, {})[b] = k
    return anm


def has_scale(rot, eps=1e-3):
    for i in range(3):
        row = rot[3 * i:3 * i + 3]
        if abs(math.sqrt(sum(c * c for c in row)) - 1.0) > eps:
            return True
    return False


def serialize_v6(anm):
    """Write FRM2 keys; channels are stored only when they change, like the 3ds Max exporter."""
    if len(anm.bones) > 255:
        raise AnmError("v6 animation supports at most 255 bones")
    if anm.frame_count > 0xFFFF:
        raise AnmError("v6 animation supports at most 65535 frames")
    out = bytearray(b"EANM")
    out += struct.pack("<I", 0x00060000)
    out += b"FRMS" + struct.pack("<i", anm.frame_count)
    out += b"BMAP" + struct.pack("<i", len(anm.bones))
    for name in anm.bones:
        b = name.encode(ENCODING, errors="replace")
        out += struct.pack("<i", len(b)) + b
    last = {}
    for f in sorted(anm.keys):
        chunks = bytearray()
        count = 0
        for b in sorted(anm.keys[f]):
            k = anm.keys[f][b]
            prev = last.get(b, (None, None, None))
            flags = 0
            payload = bytearray()
            pos_t = tuple(_f32(v) for v in k.pos) if k.pos is not None else None
            if pos_t is not None and pos_t != prev[0]:
                flags |= B_POSITION
                payload += struct.pack("<3f", *pos_t)
            q = None
            if k.quat is not None and k.rot is not None:
                q = tuple(_f32(v) for v in k.quat)
                lh = k.left_handed
            elif k.rot is not None:
                r = k.rot
                lh = det3(r) < 0
                if lh:
                    r = _flip_row2(r)
                q = tuple(_f32(v) for v in rows_to_quat(_orthonormalize(r)))
            if q is not None and (q, lh) != prev[1]:
                flags |= B_ORIENTATION | (B_LEFT_HANDED if lh else 0)
                payload += struct.pack("<3f", *q)
            if k.visible is not None and k.visible != prev[2]:
                flags |= B_VISIBLE_ON if k.visible else B_VISIBLE_OFF
            last[b] = (pos_t if pos_t is not None else prev[0],
                       (q, lh) if q is not None else prev[1],
                       k.visible if k.visible is not None else prev[2])
            if flags:
                chunks += struct.pack("<BH", b, flags) + payload
                count += 1
        if count:
            out += b"FRM2" + struct.pack("<HB", f, count) + chunks
    return bytes(out)


def serialize_v4(anm):
    """SOEdit-style matrices: full first frame, then only changed bones."""
    out = bytearray(b"EANM")
    out += struct.pack("<I", 0x00040000)
    out += b"FRMS" + struct.pack("<i", anm.frame_count)
    out += b"BMAP" + struct.pack("<i", len(anm.bones))
    for name in anm.bones:
        b = name.encode(ENCODING, errors="replace")
        out += struct.pack("<i", len(b)) + b
    last = {}
    for f in sorted(anm.keys):
        wrote_frame = False
        for b in sorted(anm.keys[f]):
            k = anm.keys[f][b]
            m = tuple(_f32(v) for v in (tuple(k.rot or IDENTITY3) + tuple(k.pos or (0, 0, 0))))
            vis = 1 if k.visible is None else k.visible
            prev = last.get(b)
            mtx = prev is None or prev[0] != m
            vchg = prev is None or prev[1] != vis
            last[b] = (m, vis)
            if not (mtx or vchg):
                continue
            if not wrote_frame:
                out += b"FRMN" + struct.pack("<i", f)
                wrote_frame = True
            out += b"BONE" + struct.pack("<i", b)
            if mtx:
                out += b"MATR" + struct.pack("<12f", *m)
            if vchg:
                out += b"VISI" + struct.pack("<i", vis)
    return bytes(out)


def _orthonormalize(r):
    """Remove scale from a rotation matrix (rows) before quaternion conversion."""
    rows = [list(r[0:3]), list(r[3:6]), list(r[6:9])]
    for row in rows:
        n = math.sqrt(sum(c * c for c in row)) or 1.0
        for i in range(3):
            row[i] /= n
    return tuple(rows[0] + rows[1] + rows[2])


def write(path, anm, version=6):
    data = serialize_v6(anm) if version == 6 else serialize_v4(anm)
    with open(path, "wb") as f:
        f.write(data)
