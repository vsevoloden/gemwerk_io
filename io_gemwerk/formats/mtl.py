"""GEM .mtl material files:

    {material bump
        {diffuse "t-72a" {MipMap 1}}
        {bump "t-72a_bp"}
        {specular "t-72a_sp"}
        {blend test}
        {alpharef 155}
    }
"""

from . import sdl
from .sdl import Node, quoted

TEXTURE_SLOTS = ("diffuse", "bump", "specular", "mask", "lightmap", "height", "envmap",
                 "simple", "diffuse1", "bumpVolume")


class Material:
    def __init__(self):
        self.kind = "simple"
        self.textures = {}      # slot -> name
        self.blend = "none"     # none / test / blend / add / sub
        self.nodes = []         # all blocks, for writing back

    def get(self, slot):
        return self.textures.get(slot.lower())


def parse_text(text):
    nodes, _ = sdl.parse(text)
    for n in nodes:
        if n.is_("material"):
            m = Material()
            m.kind = (n.first_arg("simple") or "simple").lower()
            m.nodes = n.children
            for c in n.children:
                low = c.name.lower()
                if low in (s.lower() for s in TEXTURE_SLOTS):
                    m.textures[low] = c.first_arg("")
                elif low == "blend":
                    m.blend = (c.first_arg("none") or "none").lower()
            return m
    return None


def read(path):
    with open(path, "rb") as f:
        return parse_text(f.read().decode(sdl.ENCODING, errors="replace"))


def simple(diffuse, blend="none", bump=None, specular=None):
    kind = "bump" if bump else "simple"
    n = Node("material").add(sdl.Atom(kind))
    n.add(Node("diffuse").add(quoted(diffuse)))
    if bump:
        n.add(Node("bump").add(quoted(bump)))
    if specular:
        n.add(Node("specular").add(quoted(specular)))
    n.add(Node("blend").add(sdl.Atom(blend)))
    return n


def write(path, node):
    sdl.write_file(path, [node])
