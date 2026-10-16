# GemWerk for Blender

Blender 4.2+ add-on (tested on 4.2 LTS and 5.2 LTS) to import, edit and export Men of War (GEM Engine) models.

Supported: .mdl (skeleton, bone parameters, LODs, sub-meshes, sequences), .ply meshes (EPLY/BPLY with
materials, UVs, normals, vertex colors, skinning, bump), .vol collision polyhedra with armor sides,
Box/Cylinder/Sphere volumes, obstacles and areas, .anm animations (v4 and v6), .mtl materials.

## Install

```bash
python build_addon.py
```

This makes `dist/gemwerk_io-0.1.0.zip`. In Blender open Edit > Preferences > Get Extensions,
then Install from Disk in the menu at the top right. 

Extra resource folders can be added in the add-on preferences, separated by `;`. The `resource` folders
of the model's mod and of the game are found automatically. .pak archives are not read, so unpack
what you need (for example shared textures from texture.pak).

## Use

- File > Import > GEM Model (.mdl). The model goes into its own collection: a root empty, bones
  (mesh objects or empties), volumes and obstacles in sub-collections, LODs in a hidden sub-collection.
  All sequences are loaded.
- The GEM tab in the 3D view sidebar (N):
  - GEM Model: sequences (enable, rest pose, load/save .anm, animate selected bones).
  - GEM Object: bone type, Parameters, Limits, Speed, Visibility, Color, VolumeView flags, volume properties.
  - Tools to parent bones, attach a mesh as a sub-mesh or LOD, make volumes, obstacles and areas.
- Material properties have a GEM panel with the .mtl name and mesh flags.
- File > Export > GEM Model (.mdl) writes the .mdl and the meshes. Meshes that were not changed
  are copied as they are, changed ones are rebuilt.
- Skinned meshes get an armature that follows the bone objects. Weights are vertex groups named
  after the bones, export keeps two bones per vertex.

## Limitations

- The game is left-handed, Y is mirrored on import and back on export. Z is up, 1 unit is about 5 cm.
- .anm replaces the local bone matrix. Export samples scene frames and writes v4 if a bone is scaled
  (v6 has rotation only).
- Vertex animation (the MESH section of .anm) is not supported, same as in SOEdit.
- Blender objects cannot hold sheared matrices. Untouched bones and volumes keep the matrix from the file.

## Tests

```bash
python -I tests/roundtrip_formats.py "<game>/mods"
blender -b --factory-startup --python-exit-code 1 -P tests/blender_roundtrip.py -- <out_dir> model.mdl
blender -b --factory-startup --python-exit-code 1 -P tests/blender_operators.py -- <out_dir> [model.mdl]
```

`roundtrip_formats.py` reads and rewrites all files in a folder and compares them. `blender_roundtrip.py`
imports, exports and compares a model inside Blender. `blender_operators.py` builds a model from scratch
with the operators. `blender_ui.py` draws the panels and saves a screenshot.
