"""GEM .ply meshes (EPLY / BPLY)

File = signature + tagged sections (BNDS, SKIN, MESH/SUBM, BAS2, VERT, INDX/IND4,
ADJA, SHDW, MROR). Sections are kept in their original order so an unmodified
file is written back byte-for-byte.
"""

import struct

ENCODING = "cp1251"

# Mesh flags
FLAG_TWO_SIDED = 0x01
FLAG_USE_ALPHA = 0x02
FLAG_LIGHT = 0x04
FLAG_PLCR = 0x08
FLAG_SKINNED = 0x10
FLAG_SHADOW = 0x20
FLAG_MIRRORED = 0x40
FLAG_BLENDTEX = 0x80
FLAG_BUMP = 0x100
FLAG_SPECULAR = 0x200
FLAG_MATERIAL = 0x400
FLAG_SUBSKIN = 0x800
FLAG_TWOTEX = 0x1000
FLAG_USINGVD = 0x2000
FLAG_LIGHTMAP = 0x4000

MESH_FLAG_NAMES = [
    (FLAG_TWO_SIDED, "TWO_SIDED"), (FLAG_USE_ALPHA, "USE_ALPHA"), (FLAG_LIGHT, "LIGHT"),
    (FLAG_PLCR, "PLCR"), (FLAG_SKINNED, "SKINNED"), (FLAG_SHADOW, "SHADOW"),
    (FLAG_MIRRORED, "MIRRORED"), (FLAG_BLENDTEX, "BLENDTEX"), (FLAG_BUMP, "BUMP"),
    (FLAG_SPECULAR, "SPECULAR"), (FLAG_MATERIAL, "MATERIAL"), (FLAG_SUBSKIN, "SUBSKIN"),
    (FLAG_TWOTEX, "TWOTEX"), (FLAG_USINGVD, "USINGVD"), (FLAG_LIGHTMAP, "LIGHTMAP"),
]

# Direct3D FVF bits
FVF_POSITION_MASK = 0x400E
FVF_XYZ = 0x002
FVF_XYZRHW = 0x004
FVF_XYZB1 = 0x006
FVF_XYZB5 = 0x00E
FVF_XYZW = 0x4002
FVF_NORMAL = 0x010
FVF_PSIZE = 0x020
FVF_DIFFUSE = 0x040
FVF_SPECULAR = 0x080
FVF_TEXCOUNT_MASK = 0xF00
FVF_TEXCOUNT_SHIFT = 8
FVF_LASTBETA_UBYTE4 = 0x1000
FVF_LASTBETA_D3DCOLOR = 0x8000

# Formats the exporter writes for new meshes
FVF_STATIC = FVF_XYZ | FVF_NORMAL | 0x100                      # 0x112, 32 bytes
FVF_SKINNED = 0x008 | FVF_NORMAL | 0x100 | FVF_LASTBETA_UBYTE4  # 0x1118, 40 bytes


class PlyError(Exception):
    pass


class MeshSection:
    """One MESH (or SUBM) record: a run of faces drawn with one material."""

    def __init__(self):
        self.tag = "MESH"
        self.fvf = FVF_STATIC
        self.first_face = 0
        self.face_count = 0
        self.flags = FLAG_MATERIAL | FLAG_LIGHT
        self.specular = None          # uint32 RGBA when FLAG_SPECULAR
        self.textures = []            # material / texture names (1 or 2)
        self.empty_name = False       # a zero-length name was stored
        self.subskin = None           # list of SKIN indices when FLAG_SUBSKIN
        self.subm_head = b""          # SUBM: 14 unknown bytes
        self.subm_tail = b""          # SUBM: 3 unknown dwords after face range

    def __repr__(self):
        return "MeshSection(%s faces %d+%d fvf=%#x flags=%#x %r)" % (
            self.tag, self.first_face, self.face_count, self.fvf, self.flags, self.textures)


class VertexLayout:
    """Byte layout of one vertex, mirroring the rules in CPly::CPly."""

    def __init__(self, fvf, flags, vsize, bply=False):
        self.vsize = vsize
        off = 12
        self.weights = 0
        self.has_indices = False
        self.extra_w = False
        ptype = fvf & FVF_POSITION_MASK
        if FVF_XYZB1 <= (fvf & FVF_XYZB5) <= FVF_XYZB5 and ptype not in (FVF_XYZW,):
            betas = ((fvf & FVF_XYZB5) - FVF_XYZB1) // 2 + 1
            if fvf & (FVF_LASTBETA_UBYTE4 | FVF_LASTBETA_D3DCOLOR):
                self.weights = betas - 1
                self.has_indices = True
            else:
                self.weights = betas
        elif ptype in (FVF_XYZRHW, FVF_XYZW):
            self.extra_w = True
        fmt = "3f"
        if self.extra_w:
            fmt += "I"
            off += 4
        self.weights_offset = off
        if self.weights:
            fmt += "%df" % self.weights
            off += 4 * self.weights
        self.indices_offset = off
        if self.has_indices:
            fmt += "4B"
            off += 4
        self.normal_offset = off if fvf & FVF_NORMAL else None
        if fvf & FVF_NORMAL:
            fmt += "3f"
            off += 12
        self.has_psize = bool(fvf & FVF_PSIZE)
        if self.has_psize:
            fmt += "I"
            off += 4
        self.diffuse_offset = off if fvf & FVF_DIFFUSE else None
        if fvf & FVF_DIFFUSE:
            fmt += "I"
            off += 4
        self.has_specular = bool(fvf & FVF_SPECULAR)
        if self.has_specular:
            fmt += "I"
            off += 4
        ntex = (fvf & FVF_TEXCOUNT_MASK) >> FVF_TEXCOUNT_SHIFT
        if flags & FLAG_TWOTEX:
            ntex *= 2
        ntex = max(0, min(ntex, (vsize - off) // 8))
        self.uv_count = ntex
        self.uv_offset = off
        if ntex:
            fmt += "%df" % (2 * ntex)
            off += 8 * ntex
        self.bump_offset = None
        if flags & FLAG_BUMP and vsize - off >= 16:
            self.bump_offset = off
            fmt += "4f"
            off += 16
        self.extra = vsize - off
        if self.extra < 0:
            raise PlyError("vertex size %d is smaller than FVF %#x needs (%d)" % (vsize, fvf, off))
        if self.extra:
            fmt += "%ds" % self.extra
        self.struct = struct.Struct("<" + fmt)

    def unpack(self, raw, count):
        """Return dict of per-vertex lists decoded from raw VERT bytes."""
        out = {"pos": [], "normal": [], "uv": [[] for _ in range(self.uv_count)],
               "weights": [], "indices": [], "diffuse": [], "tangent": []}
        for t in self.struct.iter_unpack(raw[:count * self.vsize]):
            i = 3
            out["pos"].append(t[0:3])
            if self.extra_w:
                i += 1
            if self.weights:
                out["weights"].append(t[i:i + self.weights])
                i += self.weights
            if self.has_indices:
                out["indices"].append(t[i:i + 4])
                i += 4
            if self.normal_offset is not None:
                out["normal"].append(t[i:i + 3])
                i += 3
            if self.has_psize:
                i += 1
            if self.diffuse_offset is not None:
                out["diffuse"].append(t[i])
                i += 1
            if self.has_specular:
                i += 1
            for u in range(self.uv_count):
                out["uv"][u].append(t[i:i + 2])
                i += 2
            if self.bump_offset is not None:
                out["tangent"].append(t[i:i + 4])
                i += 4
        return out


class Ply:
    def __init__(self):
        self.signature = "EPLY"
        self.bbox = None              # ((minx,miny,minz),(maxx,maxy,maxz))
        self.skin = None              # list of bone names
        self.meshes = []
        self.basis = None             # BAS2: 12 floats
        self.vert_count = 0
        self.vsize = 32
        self.vflags = 7
        self.vert_raw = b""
        self.index_tag = "INDX"
        self.indices = []             # flat triangle list
        self.adja = None              # raw bytes (count*6)
        self.shdw = None              # raw bytes
        self.mirror = False
        self.order = []               # section tags in file order

    # derived
    @property
    def bply(self):
        return self.signature == "BPLY"

    def union_fvf_flags(self):
        """FVF/flags used for the shared vertex buffer (CPly: GlFvf/GlFlags)."""
        gfvf = 0
        gflags = 0
        for m in self.meshes:
            if gfvf:
                wo = (gfvf | m.fvf) & (0xFFFF ^ FVF_TEXCOUNT_MASK)
                old_t = gfvf & FVF_TEXCOUNT_MASK
                new_t = m.fvf & FVF_TEXCOUNT_MASK
                if old_t > new_t:
                    gfvf = wo | old_t
                else:
                    gfvf = wo | new_t
                    gflags |= m.flags
            else:
                gfvf, gflags = m.fvf, m.flags
        bump = any(m.flags & FLAG_BUMP for m in self.meshes)
        if bump:
            gflags |= FLAG_BUMP
        return gfvf, gflags

    def layout(self):
        fvf, flags = self.union_fvf_flags()
        return VertexLayout(fvf, flags, self.vsize, self.bply)

    def vertices(self):
        return self.layout().unpack(self.vert_raw, self.vert_count)

    def triangles(self):
        ix = self.indices
        return [(ix[i], ix[i + 1], ix[i + 2]) for i in range(0, len(ix) - 2, 3)]



def _read_name(data, pos):
    n = data[pos]
    pos += 1
    if n == 0:
        return "", pos + 1, True
    return data[pos:pos + n].decode(ENCODING, errors="replace"), pos + n, False


def read(path):
    with open(path, "rb") as f:
        return parse(f.read())


def parse(data):
    ply = Ply()
    sig = data[:4]
    if sig not in (b"EPLY", b"BPLY"):
        raise PlyError("not a GEM mesh (signature %r)" % sig)
    ply.signature = sig.decode()
    pos = 4
    end = len(data)
    u32 = struct.Struct("<I")
    while pos + 4 <= end:
        tag = data[pos:pos + 4].decode("latin1")
        pos += 4
        ply.order.append(tag)
        if tag == "BNDS":
            v = struct.unpack_from("<6f", data, pos)
            ply.bbox = (v[0:3], v[3:6])
            pos += 24
        elif tag == "SKIN":
            n, = struct.unpack_from("<i", data, pos)
            pos += 4
            ply.skin = []
            for _ in range(n):
                ln = data[pos]
                ply.skin.append(data[pos + 1:pos + 1 + ln].decode(ENCODING, errors="replace"))
                pos += 1 + ln
        elif tag == "MESH" or tag == "SUBM":
            m = MeshSection()
            m.tag = tag
            if tag == "MESH":
                m.fvf, m.first_face, m.face_count, m.flags = struct.unpack_from("<4I", data, pos)
                pos += 16
                if m.flags & FLAG_SPECULAR:
                    m.specular, = u32.unpack_from(data, pos)
                    pos += 4
                ntex = 2 if (ply.bply and not (m.flags & FLAG_MATERIAL)) else 1
            else:
                m.subm_head = data[pos:pos + 14]
                pos += 14
                m.first_face, m.face_count = struct.unpack_from("<2I", data, pos)
                pos += 8
                m.subm_tail = data[pos:pos + 12]
                pos += 12
                m.fvf = 0x152
                m.flags = 0x1004
                ntex = 1
            for _ in range(ntex):
                name, pos, empty = _read_name(data, pos)
                if empty:
                    m.empty_name = True
                    break
                m.textures.append(name)
            if m.flags & FLAG_SUBSKIN and tag == "MESH":
                c = data[pos]
                m.subskin = list(data[pos + 1:pos + 1 + c])
                pos += 1 + c
            ply.meshes.append(m)
        elif tag == "BAS2":
            ply.basis = struct.unpack_from("<12f", data, pos)
            pos += 48
        elif tag == "VERT":
            ply.vert_count, ply.vsize, ply.vflags = struct.unpack_from("<iHH", data, pos)
            pos += 8
            size = ply.vert_count * ply.vsize
            ply.vert_raw = data[pos:pos + size]
            pos += size
        elif tag == "INDX":
            n, = struct.unpack_from("<i", data, pos)
            pos += 4
            ply.index_tag = "INDX"
            ply.indices = list(struct.unpack_from("<%dH" % n, data, pos))
            pos += 2 * n
        elif tag == "IND4":
            n, = struct.unpack_from("<i", data, pos)
            pos += 4
            ply.index_tag = "IND4"
            ply.indices = list(struct.unpack_from("<%dI" % n, data, pos))
            pos += 4 * n
        elif tag == "ADJA":
            n, = struct.unpack_from("<i", data, pos)
            pos += 4
            ply.adja = data[pos:pos + 6 * n]
            pos += 6 * n
        elif tag == "SHDW":
            n, = struct.unpack_from("<i", data, pos)
            pos += 4
            ply.shdw = data[pos:pos + n]
            pos += n
        elif tag == "MROR":
            ply.mirror = True
        else:
            raise PlyError("unknown section %r at offset %#x" % (tag, pos - 4))
    if pos != end:
        raise PlyError("truncated file")
    return ply


def _write_name(out, name, empty):
    if empty or not name:
        out += b"\x00\x00"
        return
    b = name.encode(ENCODING, errors="replace")[:255]
    out += bytes([len(b)]) + b


def serialize(ply):
    out = bytearray(ply.signature.encode())
    order = list(ply.order) if ply.order else default_order(ply)
    # Keep sections that were added/removed since reading in sync
    mesh_i = 0
    for tag in order:
        if tag == "BNDS" and ply.bbox:
            out += b"BNDS" + struct.pack("<6f", *ply.bbox[0], *ply.bbox[1])
        elif tag == "SKIN" and ply.skin is not None:
            out += b"SKIN" + struct.pack("<i", len(ply.skin))
            for n in ply.skin:
                b = n.encode(ENCODING, errors="replace")[:255]
                out += bytes([len(b)]) + b
        elif tag in ("MESH", "SUBM"):
            if mesh_i >= len(ply.meshes):
                continue
            m = ply.meshes[mesh_i]
            mesh_i += 1
            _write_mesh(out, ply, m)
            # Meshes appended after reading go right after the last stored one
            if mesh_i == sum(1 for t in order if t in ("MESH", "SUBM")):
                while mesh_i < len(ply.meshes):
                    _write_mesh(out, ply, ply.meshes[mesh_i])
                    mesh_i += 1
        elif tag == "BAS2" and ply.basis:
            out += b"BAS2" + struct.pack("<12f", *ply.basis)
        elif tag == "VERT":
            out += b"VERT" + struct.pack("<iHH", ply.vert_count, ply.vsize, ply.vflags)
            out += ply.vert_raw
        elif tag in ("INDX", "IND4"):
            if ply.index_tag == "IND4":
                out += b"IND4" + struct.pack("<i", len(ply.indices))
                out += struct.pack("<%dI" % len(ply.indices), *ply.indices)
            else:
                out += b"INDX" + struct.pack("<i", len(ply.indices))
                out += struct.pack("<%dH" % len(ply.indices), *ply.indices)
        elif tag == "ADJA" and ply.adja is not None:
            out += b"ADJA" + struct.pack("<i", len(ply.adja) // 6) + ply.adja
        elif tag == "SHDW" and ply.shdw is not None:
            out += b"SHDW" + struct.pack("<i", len(ply.shdw)) + ply.shdw
        elif tag == "MROR" and ply.mirror:
            out += b"MROR"
    return bytes(out)


def _write_mesh(out, ply, m):
    if m.tag == "SUBM":
        out += b"SUBM" + m.subm_head + struct.pack("<2I", m.first_face, m.face_count) + m.subm_tail
        _write_name(out, m.textures[0] if m.textures else "", m.empty_name)
        return
    out += b"MESH" + struct.pack("<4I", m.fvf, m.first_face, m.face_count, m.flags)
    if m.flags & FLAG_SPECULAR:
        out += struct.pack("<I", m.specular or 0)
    ntex = 2 if (ply.bply and not (m.flags & FLAG_MATERIAL)) else 1
    for i in range(ntex):
        name = m.textures[i] if i < len(m.textures) else ""
        empty = not name
        _write_name(out, name, empty)
        if empty:
            break
    if m.flags & FLAG_SUBSKIN:
        sub = (m.subskin or [])[:255]
        out += bytes([len(sub)]) + bytes(sub)


def default_order(ply):
    order = ["BNDS"]
    if ply.skin is not None:
        order.append("SKIN")
    order += ["MESH"] * len(ply.meshes)
    if ply.basis:
        order.append("BAS2")
    order += ["VERT", ply.index_tag]
    if ply.adja is not None:
        order.append("ADJA")
    if ply.shdw is not None:
        order.append("SHDW")
    if ply.mirror:
        order.append("MROR")
    return order


def write(path, ply):
    data = serialize(ply)
    with open(path, "wb") as f:
        f.write(data)


# Building a new mesh

def build(meshes, positions, normals, uvs, triangles, skin=None, weights=None,
          tangents=None, diffuse=None, signature="EPLY"):
    """Assemble a Ply from flat vertex data.

    meshes     list of MeshSection (first_face/face_count already set)
    positions  list of (x,y,z); normals likewise
    uvs        list of UV layers, each a per-vertex list of (u,v)
    triangles  list of (a,b,c) ordered so each MeshSection's range is contiguous
    skin       list of bone names (skinned mesh) or None
    weights    per-vertex (w0, (i0, i1, 0, 0)) for skinned meshes (local subskin indices)
    tangents   per-vertex (x,y,z,w) when a mesh uses FLAG_BUMP
    diffuse    per-vertex uint32 colours when FVF_DIFFUSE is used
    """
    ply = Ply()
    ply.signature = signature
    ply.meshes = meshes
    ply.skin = skin
    ply.vflags = 7
    fvf, flags = ply.union_fvf_flags() if meshes else (FVF_STATIC, 0)
    lay = VertexLayout(fvf, flags, 1 << 12)
    vsize = 12 + 4 * lay.weights + (4 if lay.has_indices else 0)
    vsize += 12 if lay.normal_offset is not None else 0
    vsize += 4 if lay.diffuse_offset is not None else 0
    ntex = (fvf & FVF_TEXCOUNT_MASK) >> FVF_TEXCOUNT_SHIFT
    if flags & FLAG_TWOTEX:
        ntex *= 2
    vsize += 8 * ntex
    if flags & FLAG_BUMP:
        vsize += 16
    lay = VertexLayout(fvf, flags, vsize)
    ply.vsize = vsize
    ply.vert_count = len(positions)
    out = bytearray()
    st = lay.struct
    for i, p in enumerate(positions):
        vals = [p[0], p[1], p[2]]
        if lay.extra_w:
            vals.append(0)
        if lay.weights:
            w = weights[i][0] if weights else (1.0,)
            w = list(w) + [0.0] * lay.weights
            vals += w[:lay.weights]
        if lay.has_indices:
            vals += list(weights[i][1]) if weights else [0, 0, 0, 0]
        if lay.normal_offset is not None:
            vals += list(normals[i])
        if lay.has_psize:
            vals.append(0)
        if lay.diffuse_offset is not None:
            vals.append(diffuse[i] if diffuse else 0xFFFFFFFF)
        if lay.has_specular:
            vals.append(0)
        for u in range(lay.uv_count):
            layer = uvs[min(u, len(uvs) - 1)] if uvs else None
            vals += list(layer[i]) if layer else [0.0, 0.0]
        if lay.bump_offset is not None:
            vals += list(tangents[i]) if tangents else [1.0, 0.0, 0.0, 1.0]
        if lay.extra:
            vals.append(b"\x00" * lay.extra)
        out += st.pack(*vals)
    ply.vert_raw = bytes(out)
    flat = []
    for t in triangles:
        flat += t
    ply.indices = flat
    ply.index_tag = "IND4" if len(positions) > 0xFFFF else "INDX"
    if positions:
        mn = tuple(min(p[k] for p in positions) for k in range(3))
        mx = tuple(max(p[k] for p in positions) for k in range(3))
        ply.bbox = (mn, mx)
    else:
        ply.bbox = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    ply.order = default_order(ply)
    return ply
