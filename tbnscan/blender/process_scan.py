"""Blender batch step (run inside Blender, not plain Python):

    blender -b -P process_scan.py -- --input raw.obj --plate-cm 28 \
        --out-high Highpoly/dish_high.blend --out-proxy Lowpoly/dish_proxy.blend --report r.json

1. import the raw scan (obj / ply / fbx / glb) and join it into one object
2. auto-orient: PCA plane fit so the plate lies flat, food pointing +Z
3. scale to real size using the plate's longest side (metres, 1 BU = 1 m)
4. origin at the plate bottom centre, plate bottom on Z = 0
5. merge by distance, cap face count, save the high-poly reference .blend
6. decimate further and save a proxy .blend as the starting point for retopo
"""
import argparse
import json
import sys
from pathlib import Path

import bpy  # noqa: E402  (bpy first so the pip "bpy" module works too)
import bmesh
import bpy
import numpy as np
from mathutils import Matrix


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--name", default="dish")
    p.add_argument("--plate-cm", type=float, required=True)
    p.add_argument("--auto-orient", type=int, default=1)
    p.add_argument("--flip", type=int, default=0, help="force-flip upside down result")
    p.add_argument("--high-max-faces", type=int, default=1_000_000)
    p.add_argument("--proxy-faces", type=int, default=60_000)
    p.add_argument("--merge-mm", type=float, default=0.05)
    p.add_argument("--out-high", required=True)
    p.add_argument("--out-proxy", required=True)
    p.add_argument("--report", required=True)
    return p.parse_args(argv)


def import_mesh(path: Path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = path.suffix.lower()
    if ext == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=str(path))
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise ValueError(f"Unsupported mesh format: {ext}")
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("No mesh objects imported")
    for o in bpy.context.scene.objects:
        o.select_set(o in meshes)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    # remove empties / parents left by importers, bake transforms into the mesh
    obj.parent = None
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in list(bpy.context.scene.objects):
        if o is not obj:
            bpy.data.objects.remove(o, do_unlink=True)
    return obj


def verts_np(obj) -> np.ndarray:
    me = obj.data
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def orient_matrix(v: np.ndarray, flip: bool) -> Matrix:
    """Rotation that maps the plate plane to XY (normal -> +Z) and the plate's long axis to X."""
    c = v.mean(axis=0)
    cov = np.cov((v - c).T)
    w, vecs = np.linalg.eigh(cov)          # ascending eigenvalues
    n = vecs[:, 0]                          # least variance = plate normal
    x = vecs[:, 2]                          # most variance = long axis of plate
    # Food side: most vertices sit near the plate plane, the food lump makes a tail
    # on one side, so the distribution is skewed towards the food.
    t = (v - c) @ n
    skew = float(np.mean(t ** 3)) / (float(np.std(t)) ** 3 + 1e-12)
    if skew < 0:
        n = -n
    if flip:
        n = -n
    y = np.cross(n, x)
    x = np.cross(y, n)
    R = np.stack([x, y, n])                 # rows = new axes
    m = Matrix(R.tolist()).to_4x4()
    return m, skew


def tri_count(obj) -> int:
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def decimate_to(obj, target_faces: int):
    """Decimate to roughly target_faces triangles; returns the triangle count."""
    faces = tri_count(obj)
    if faces <= target_faces:
        return faces
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = target_faces / faces
    mod.use_collapse_triangulate = True
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return tri_count(obj)


def main():
    a = parse_args()
    obj = import_mesh(Path(a.input))
    obj.name = obj.data.name = f"{a.name}_high"
    report = {"input": a.input, "faces_raw": tri_count(obj)}

    # 2. orient
    if a.auto_orient:
        m, skew = orient_matrix(verts_np(obj), bool(a.flip))
        obj.data.transform(m)
        report["orient_skew"] = round(skew, 3)
    elif a.flip:
        obj.data.transform(Matrix.Rotation(np.pi, 4, "X"))

    # 3. scale: plate's longest horizontal side = plate_cm (robust percentiles ignore stray points)
    v = verts_np(obj)
    lo, hi = np.percentile(v, 0.02, axis=0), np.percentile(v, 99.98, axis=0)
    extent = float(max(hi[0] - lo[0], hi[1] - lo[1]))
    scale = (a.plate_cm / 100.0) / extent
    # 4. origin: plate bottom centre at (0,0,0)
    cx, cy, zmin = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]
    obj.data.transform(Matrix.Translation((-cx, -cy, -zmin)))
    obj.data.transform(Matrix.Scale(scale, 4))
    obj.location = (0, 0, 0)
    report["scale_factor"] = scale

    # 5. clean + cap
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=a.merge_mm / 1000.0)
    bm.to_mesh(obj.data)
    bm.free()
    report["faces_high"] = decimate_to(obj, a.high_max_faces)
    obj.data.update()
    bpy.context.view_layer.update()
    v = verts_np(obj)
    d = v.max(axis=0) - v.min(axis=0)
    report["dimensions_cm"] = [round(float(x) * 100, 1) for x in d]
    report["z_min_cm"] = round(float(v[:, 2].min()) * 100, 2)
    if d[2] > max(d[0], d[1]):
        report["warning"] = "taller than wide: check orientation (set flip / auto_orient in dish.yaml)"

    Path(a.out_high).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.file.make_paths_relative()
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(a.out_high).resolve()), relative_remap=True)

    # 6. proxy for retopo (keeps the scan's material so you can see it)
    obj.name = obj.data.name = f"{a.name}_proxy"
    report["faces_proxy"] = decimate_to(obj, a.proxy_faces)
    Path(a.out_proxy).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(a.out_proxy).resolve()), relative_remap=True)

    Path(a.report).write_text(json.dumps(report, indent=2))
    print("TBN_REPORT", json.dumps(report))


if __name__ == "__main__":
    main()
