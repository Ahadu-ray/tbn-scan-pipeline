"""Stage 5 (blender): run the Blender batch scripts headless."""
from __future__ import annotations

import json
from pathlib import Path

from .common import PKG_ROOT, Dish, log, run, tool_cmd
from .reconstruct import raw_mesh_path

SCRIPTS = PKG_ROOT / "blender"


def blender(dish: Dish, cfg: dict) -> dict:
    bc = cfg["blender"]
    mesh = raw_mesh_path(dish)
    if not mesh:
        raise FileNotFoundError(f"No raw mesh in {dish.scan}; run reconstruct (or drop a mesh there).")
    report = dish.highpoly / "blender_report.json"
    cmd = [
        *tool_cmd(cfg, "blender"), "-b", "--factory-startup", "-P", str(SCRIPTS / "process_scan.py"), "--",
        "--input", str(mesh),
        "--name", dish.name,
        "--plate-cm", str(bc["plate_diameter_cm"]),
        "--auto-orient", str(int(bc["auto_orient"])),
        "--flip", str(int(bc.get("flip", False))),
        "--high-max-faces", str(bc["highpoly_max_faces"]),
        "--proxy-faces", str(bc["proxy_faces"]),
        "--merge-mm", str(bc["merge_by_distance_mm"]),
        "--out-high", str(dish.highpoly / f"{dish.name}_high.blend"),
        "--out-proxy", str(dish.lowpoly / f"{dish.name}_proxy.blend"),
        "--report", str(report),
    ]
    dish.highpoly.mkdir(exist_ok=True)
    run(cmd, log_file=dish.highpoly / "blender.log")
    rep = json.loads(report.read_text())
    log.info("[%s] high-poly saved: %s faces, %s cm", dish.name, rep.get("faces_high"), rep.get("dimensions_cm"))
    if "warning" in rep:
        log.warning("[%s] %s", dish.name, rep["warning"])
    return {"faces_high": rep.get("faces_high"), "dimensions_cm": rep.get("dimensions_cm")}
