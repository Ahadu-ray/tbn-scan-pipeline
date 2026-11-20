"""Stage 4 (reconstruct): selected frames + masks -> textured high-poly mesh in Scan/.

RealityScan (default) is driven through its command line in headless mode.
Meshroom is the free fallback; it gets the frames with the background blacked out.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import cv2

from .common import Dish, log, run, tool_cmd


def raw_mesh_path(dish: Dish) -> Path | None:
    """The mesh the reconstruct stage produced (first match), used by the Blender stage."""
    for pat in (f"{dish.name}_raw.obj", f"{dish.name}_raw.*", "*.obj", "*.ply", "*.fbx", "*.glb"):
        hits = sorted(dish.scan.glob(pat)) + sorted(dish.scan.glob(f"meshroom/**/{pat}"))
        hits = [h for h in hits if h.suffix.lower() in {".obj", ".ply", ".fbx", ".glb", ".gltf"}]
        if hits:
            return hits[0]
    return None


def _realityscan(dish: Dish, cfg: dict) -> Path:
    rc = cfg["reconstruct"]
    exe = tool_cmd(cfg, "realityscan")
    out_mesh = dish.scan / f"{dish.name}_raw.{rc['export_format']}"
    project = dish.scan / f"{dish.name}.rsproj"
    cmd = [
        *exe, "-headless",
        "-newScene",
        "-addFolder", str(dish.selected_dir),   # masks named <image>.mask.png are picked up as mask layers
        "-align",
        "-selectMaximalComponent",
        "-setReconstructionRegionAuto",
        "-calculateHighModel" if rc["quality"] == "high" else "-calculateNormalModel",
        "-calculateTexture",
        "-save", str(project),
        "-exportSelectedModel", str(out_mesh),
        *[str(a) for a in rc.get("extra_args", [])],
        "-quit",
    ]
    run(cmd, log_file=dish.scan / "realityscan.log")
    if not out_mesh.exists():
        raise RuntimeError(f"RealityScan finished but {out_mesh.name} is missing; see Scan/realityscan.log")
    return out_mesh


def _meshroom(dish: Dish, cfg: dict) -> Path:
    mc = cfg["mask"]
    inp = dish.scan / "meshroom_input"
    if inp.exists():
        shutil.rmtree(inp)
    inp.mkdir(parents=True)
    for f in sorted(dish.selected_dir.glob("*.jpg")):
        img = cv2.imread(str(f))
        m = cv2.imread(str(f.parent / mc["mask_name"].format(stem=f.stem, name=f.name)), cv2.IMREAD_GRAYSCALE)
        if m is not None:
            img[m < 128] = 0                     # black background: no features on the static backdrop
        cv2.imwrite(str(inp / f.name), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    out = dish.scan / "meshroom"
    run([*tool_cmd(cfg, "meshroom"), "--input", str(inp), "--output", str(out)],
        log_file=dish.scan / "meshroom.log")
    mesh = next(iter(sorted(out.glob("*.obj"))), None)
    if not mesh:
        raise RuntimeError("Meshroom finished but no .obj was found; see Scan/meshroom.log")
    return mesh


def reconstruct(dish: Dish, cfg: dict) -> dict:
    if not any(dish.selected_dir.glob("*.jpg")):
        raise FileNotFoundError(f"No frames in {dish.selected_dir}; run select/mask first.")
    dish.scan.mkdir(exist_ok=True)
    engine = cfg["reconstruct"]["engine"]
    log.info("[%s] reconstructing with %s (this takes a while)", dish.name, engine)
    mesh = _realityscan(dish, cfg) if engine == "realityscan" else _meshroom(dish, cfg)
    log.info("[%s] raw mesh: %s", dish.name, mesh)
    return {"mesh": str(mesh.relative_to(dish.dir))}
