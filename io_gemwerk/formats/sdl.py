"""Reader/writer for the GEM brace syntax used by .mdl, .mtl and .def files.

    {Name arg "quoted arg" {Child ...} arg}

Comments start with ';' and run to the end of the line. Comments that precede
the first token are kept as the file header (author/date lines), the rest are dropped
"""

ENCODING = "cp1251"


class Atom(str):
    """A bare or quoted word. `quoted` tells the writer to put quotes back."""

    quoted = False

    def __new__(cls, value, quoted=False):
        obj = super().__new__(cls, value)
        obj.quoted = quoted
        return obj


class Node:
    __slots__ = ("name", "items")

    def __init__(self, name, items=None):
        self.name = name
        self.items = items if items is not None else []

    # queries
    @property
    def args(self):
        return [i for i in self.items if not isinstance(i, Node)]

    @property
    def children(self):
        return [i for i in self.items if isinstance(i, Node)]

    def is_(self, *names):
        low = self.name.lower()
        return any(low == n.lower() for n in names)

    def find(self, name):
        for c in self.children:
            if c.is_(name):
                return c
        return None

    def find_all(self, name):
        return [c for c in self.children if c.is_(name)]

    def floats(self):
        out = []
        for a in self.args:
            try:
                out.append(float(a))
            except ValueError:
                out.append(0.0)
        return out

    def first_arg(self, default=None):
        a = self.args
        return str(a[0]) if a else default

    # building
    def add(self, *items):
        for i in items:
            if isinstance(i, Node):
                self.items.append(i)
            elif isinstance(i, Atom):
                self.items.append(i)
            else:
                self.items.append(Atom(fmt(i) if isinstance(i, float) else str(i)))
        return self

    def __repr__(self):
        return "Node(%r, %r)" % (self.name, self.items)


def quoted(s):
    return Atom(s, True)


def fmt(v):
    """Number formatting in the style of SOEdit/3ds Max exporters (%g), with float32 precision."""
    if isinstance(v, float):
        if abs(v) < 5e-7:
            v = 0.0
        s = "%.7g" % v
        if s == "-0":
            s = "0"
        return s
    return str(v)


# Parsing

def tokenize(text):
    """Yield ('{', None), ('}', None), ('atom', Atom). Returns header comments
    via the `header` list attribute of the generator wrapper."""
    tokens = []
    header = []
    seen_token = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == ";":
            j = text.find("\n", i)
            if j < 0:
                j = n
            if not seen_token:
                header.append(text[i:j].rstrip("\r"))
            i = j + 1
        elif c == "{" or c == "}":
            tokens.append((c, None))
            seen_token = True
            i += 1
        elif c == '"':
            j = text.find('"', i + 1)
            if j < 0:
                j = n
            tokens.append(("atom", Atom(text[i + 1:j], True)))
            seen_token = True
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}";=':
                j += 1
            if j > i:
                tokens.append(("atom", Atom(text[i:j])))
                seen_token = True
            i = j + 1 if (j < n and text[j] == "=") else j
    return tokens, header


def parse(text):
    """Parse text into (list of top-level Nodes, header comment lines)."""
    tokens, header = tokenize(text)
    root = Node("<root>")
    stack = [root]
    expect_name = False
    for kind, val in tokens:
        if kind == "{":
            node = Node("")
            stack[-1].items.append(node)
            stack.append(node)
            expect_name = True
        elif kind == "}":
            if len(stack) > 1:
                stack.pop()
            expect_name = False
        else:
            if expect_name:
                stack[-1].name = str(val)
                expect_name = False
            else:
                stack[-1].items.append(val)
    return root.children, header


def read_file(path):
    with open(path, "rb") as f:
        data = f.read()
    return parse(data.decode(ENCODING, errors="replace"))


# Writing

# Blocks that always get one child per line
MULTILINE = {
    "skeleton", "bone", "animation", "volume", "obstacle", "area", "lodview",
    "obb2", "circle2", "polygon2", "thickness", "game_entity", "material",
}
# Blocks whose numbers are written as rows of three
MATRIX_ROWS = {"orientation", "matrix34"}


def _atom_text(a):
    if isinstance(a, Atom) and a.quoted:
        return '"%s"' % a
    s = str(a)
    if s == "" or any(ch in s for ch in ' \t{};"'):
        return '"%s"' % s
    return s


def _inline(node):
    parts = [node.name] + [
        _atom_text(i) if not isinstance(i, Node) else _inline(i) for i in node.items
    ]
    return "{" + " ".join(p for p in parts if p != "") + "}"


def _can_inline(node):
    low = node.name.lower()
    if (low in MULTILINE and node.children) or low in MATRIX_ROWS:
        return False
    for c in node.children:
        if c.children or c.name.lower() in MULTILINE:
            return False
    return len(_inline(node)) <= 120


def write_node(node, out, depth=0):
    ind = "\t" * depth
    low = node.name.lower()
    if low in MATRIX_ROWS and not node.children:
        nums = [_atom_text(a) for a in node.args]
        out.append("%s{%s" % (ind, node.name))
        for r in range(0, len(nums), 3):
            out.append("%s\t%s" % (ind, "\t".join(nums[r:r + 3])))
        out.append("%s}" % ind)
        return
    if _can_inline(node):
        out.append(ind + _inline(node))
        return
    head = [node.name]
    rest = []
    # Arguments that precede the first child stay on the header line
    for i, it in enumerate(node.items):
        if isinstance(it, Node):
            rest = node.items[i:]
            break
        head.append(_atom_text(it))
    out.append("%s{%s" % (ind, " ".join(head)))
    for it in rest:
        if isinstance(it, Node):
            write_node(it, out, depth + 1)
        else:
            out.append("%s\t%s" % (ind, _atom_text(it)))
    out.append("%s}" % ind)


def write(nodes, header=None):
    out = []
    for h in header or []:
        out.append(h if h.startswith(";") else ";" + h)
    for n in nodes:
        write_node(n, out, 0)
    return "\n".join(out) + "\n"


def write_file(path, nodes, header=None):
    text = write(nodes, header).replace("\n", "\r\n")
    with open(path, "wb") as f:
        f.write(text.encode(ENCODING, errors="replace"))
