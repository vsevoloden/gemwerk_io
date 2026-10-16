"""The model's own mod first, then the folders from the add-on preferences, 
then the game. Only unpacked folders are searched; .pak archives 
have to be extracted by the user.

Paths inside the game are relative to a `resource` folder:
    "/texture/common/x"  -> <resource>/texture/common/x
    "$/model/x"          -> <resource>/texture/common/model/x
    "x"                  -> next to the file that references it
"""

import os

TEXTURE_EXTS = (".dds", ".tga", ".png", ".bmp", ".jpg", ".jpeg", ".tif", ".tiff", ".gif")


def norm(p):
    return p.replace("\\", "/")


def find_resource_root(path):
    """The nearest ancestor folder named 'resource', or None."""
    p = os.path.abspath(path)
    while True:
        parent = os.path.dirname(p)
        if os.path.basename(p).lower() == "resource":
            return p
        if parent == p:
            return None
        p = parent


def guess_roots(model_path, extra_roots=()):
    """Resource roots to search for a model: its own mod, user roots, then the game."""
    roots = []

    def add(r):
        if r and os.path.isdir(r):
            r = os.path.normcase(os.path.abspath(r))
            if r not in roots:
                roots.append(r)

    own = find_resource_root(model_path) if model_path else None
    add(own)
    for r in extra_roots:
        r = r.strip()
        if not r:
            continue
        if os.path.basename(r.rstrip("/\\")).lower() != "resource" and os.path.isdir(os.path.join(r, "resource")):
            r = os.path.join(r, "resource")
        add(r)
    # <game>/mods/<mod>/resource -> <game>/resource
    if own:
        mods = os.path.dirname(os.path.dirname(own))
        if os.path.basename(mods).lower() == "mods":
            add(os.path.join(os.path.dirname(mods), "resource"))
    return roots


class ResourceIndex:
    def __init__(self, roots):
        self.roots = list(roots)

    def find(self, rel, exts=("",)):
        """Find `rel` (relative to a resource root) trying each extension."""
        rel = norm(rel).lstrip("/")
        for ext in exts:
            cand = rel + ext
            for root in self.roots:
                p = os.path.join(root, *cand.split("/"))
                if os.path.isfile(p):
                    return p
        return None

    def relative(self, path):
        """Path of a real file relative to its resource root, or None."""
        ap = os.path.normcase(os.path.abspath(path))
        for root in self.roots:
            if ap.startswith(root + os.sep):
                return norm(os.path.relpath(ap, root))
        root = find_resource_root(path)
        if root:
            return norm(os.path.relpath(os.path.abspath(path), root))
        return None

    def resolve(self, name, near_file, exts=("",)):
        """Resolve a name used inside a game file (texture or material reference)."""
        name = norm(name)
        if name.startswith("$"):
            return self.find("texture/common/" + name.lstrip("$").lstrip("/"), exts)
        if name.startswith("/"):
            return self.find(name, exts)
        base = os.path.dirname(near_file)
        for ext in exts:
            p = os.path.join(base, *(name + ext).split("/"))
            if os.path.isfile(p):
                return p
        # Same relative folder in another mod / the game
        rel_dir = self.relative(os.path.join(base, "x"))
        if rel_dir:
            rel_dir = os.path.dirname(rel_dir)
            return self.find((rel_dir + "/" if rel_dir else "") + name, exts)
        return None
