"""GEM .vol collision polyhedra (EVLM): VERT positions, INDX triangles, SIDE codes."""

import struct

SIDES = {0: "none", 1: "front", 2: "top", 3: "left", 4: "right", 5: "rear", 6: "bottom"}


class VolError(Exception):
    pass


class Vol:
    def __init__(self):
        self.verts = []      # list of (x,y,z)
        self.indices = []    # flat triangle list
        self.sides = []      # one byte per triangle
        self.order = ["VERT", "INDX", "SIDE"]

    def triangles(self):
        ix = self.indices
        return [(ix[i], ix[i + 1], ix[i + 2]) for i in range(0, len(ix) - 2, 3)]


def read(path):
    with open(path, "rb") as f:
        return parse(f.read())


def parse(data):
    if data[:4] != b"EVLM":
        raise VolError("not a GEM volume (signature %r)" % data[:4])
    vol = Vol()
    vol.order = []
    pos = 4
    while pos + 4 <= len(data):
        tag = data[pos:pos + 4].decode("latin1")
        pos += 4
        n, = struct.unpack_from("<i", data, pos)
        pos += 4
        vol.order.append(tag)
        if tag == "VERT":
            v = struct.unpack_from("<%df" % (3 * n), data, pos)
            vol.verts = [v[i:i + 3] for i in range(0, 3 * n, 3)]
            pos += 12 * n
        elif tag == "INDX":
            vol.indices = list(struct.unpack_from("<%dH" % n, data, pos))
            pos += 2 * n
        elif tag == "SIDE":
            vol.sides = list(data[pos:pos + n])
            pos += n
        else:
            raise VolError("unknown section %r" % tag)
    if pos != len(data):
        raise VolError("truncated file")
    return vol


def serialize(vol):
    out = bytearray(b"EVLM")
    for tag in vol.order or ["VERT", "INDX", "SIDE"]:
        if tag == "VERT":
            out += b"VERT" + struct.pack("<i", len(vol.verts))
            for v in vol.verts:
                out += struct.pack("<3f", *v)
        elif tag == "INDX":
            out += b"INDX" + struct.pack("<i", len(vol.indices))
            out += struct.pack("<%dH" % len(vol.indices), *vol.indices)
        elif tag == "SIDE":
            out += b"SIDE" + struct.pack("<i", len(vol.sides)) + bytes(vol.sides)
    return bytes(out)


def write(path, vol):
    with open(path, "wb") as f:
        f.write(serialize(vol))
