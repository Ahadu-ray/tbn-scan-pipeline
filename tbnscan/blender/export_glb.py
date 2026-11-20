"""Blender export step (run inside Blender):

    blender -b <Lowpoly/dish_low.blend> -P export_glb.py -- --out Export/dish_raw.glb [--textures Lowpoly/textures]

Exports all visible mesh objects as one GLB. If --textures is given and a mesh has no
image-textured material, a glTF-friendly Principled material is built from Substance
Painter exports found there (names containing BaseColor, Normal, and either
OcclusionRoughnessMetallic/ORM or separate Roughness / Metallic / AmbientOcclusion).
"""
import argparse
import sys
from pathlib import Path

import bpy


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--textures", default="")
    return p.parse_args(argv)


def find_tex(folder: Path, *keys):
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff"):
            low = f.stem.lower()
            if any(k.lower() in low for k in keys):
                return f
    return None


def has_image_texture(obj) -> bool:
    for slot in obj.material_slots:
        m = slot.material
        if m and m.use_nodes and any(n.type == "TEX_IMAGE" and n.image for n in m.node_tree.nodes):
            return True
    return False


def build_material(name: str, folder: Path):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")

    def img(path, non_color=False):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = bpy.data.images.load(str(path))
        if non_color:
            n.image.colorspace_settings.name = "Non-Color"
        return n

    base = find_tex(folder, "basecolor", "base_color", "albedo", "diffuse")
    if base:
        nt.links.new(img(base).outputs["Color"], bsdf.inputs["Base Color"])
    nrm = find_tex(folder, "normal")
    if nrm:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(img(nrm, True).outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    orm = find_tex(folder, "occlusionroughnessmetallic", "_orm")
    if orm:
        # glTF exporter recognises Separate Color: G -> roughness, B -> metallic (R = AO via glTF settings group)
        n = img(orm, True)
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(n.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
        nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
    else:
        r = find_tex(folder, "roughness")
        if r:
            nt.links.new(img(r, True).outputs["Color"], bsdf.inputs["Roughness"])
        mtl = find_tex(folder, "metallic", "metalness")
        if mtl:
            nt.links.new(img(mtl, True).outputs["Color"], bsdf.inputs["Metallic"])
    return mat


def main():
    a = parse_args()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.visible_get()]
    if not meshes:
        raise RuntimeError("No visible mesh objects to export")
    tex_dir = Path(a.textures) if a.textures else None
    if tex_dir and tex_dir.is_dir():
        for o in meshes:
            if not has_image_texture(o):
                mat = build_material(f"{o.name}_mat", tex_dir)
                o.data.materials.clear()
                o.data.materials.append(mat)
                print(f"TBN wired Substance textures from {tex_dir} onto {o.name}")
    for o in bpy.context.scene.objects:
        o.select_set(o in meshes)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(Path(a.out).resolve()),
        export_format="GLB",
        use_selection=True,
        export_apply=True,          # apply modifiers (e.g. a final weighted-normal)
        export_yup=True,            # glTF / WebXR convention
        export_image_format="AUTO",
    )


if __name__ == "__main__":
    main()
