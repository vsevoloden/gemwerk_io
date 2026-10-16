"""Pack the add-on into an installable zip.

    python build_addon.py            -> dist/gemwerk_io-<version>.zip

Install in Blender 4.2+: Edit > Preferences > Get Extensions > (v menu) Install from Disk.
"""

import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "io_gemwerk")


def main():
    with open(os.path.join(SRC, "blender_manifest.toml"), encoding="utf-8") as f:
        version = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M).group(1)
    dist = os.path.join(HERE, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, "gemwerk_io-%s.zip" % version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dn, fn in os.walk(SRC):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for f in sorted(fn):
                if f.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(dp, f)
                z.write(full, os.path.relpath(full, SRC).replace(os.sep, "/"))
    print(out)


if __name__ == "__main__":
    main()
