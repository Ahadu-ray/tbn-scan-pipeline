"""Stage 9 (export): your finished low-poly -> web-ready GLB (meshopt/Draco + KTX2).

Input, first match wins:
  Lowpoly/<dish>_low.glb    you exported it yourself (e.g. from Blender or Substance)
  Lowpoly/<dish>_low.blend  exported here through Blender; Substance texture exports in
                            Lowpoly/textures/ are wired up if the mesh has no textured material
Output: Export/<dish>.glb, plus Export/<dish>_raw.glb (uncompressed, for checking).
"""
from __future__ import annotations

import shutil

from .common import PKG_ROOT, Dish, log, run, tool_cmd


def export(dish: Dish, cfg: dict) -> dict:
    xc = cfg["export"]
    dish.export.mkdir(exist_ok=True)
    raw = dish.export / f"{dish.name}_raw.glb"
    low_glb = dish.lowpoly / f"{dish.name}_low.glb"
    low_blend = dish.lowpoly / f"{dish.name}_low.blend"
    if low_glb.exists():
        shutil.copy2(low_glb, raw)
    elif low_blend.exists():
        cmd = [*tool_cmd(cfg, "blender"), "-b", str(low_blend), "-P", str(PKG_ROOT / "blender" / "export_glb.py"),
               "--", "--out", str(raw)]
        tex = dish.lowpoly / "textures"
        if tex.is_dir():
            cmd += ["--textures", str(tex)]
        run(cmd, log_file=dish.export / "blender_export.log")
    else:
        raise FileNotFoundError(f"Put your finished model at {low_blend.name} or {low_glb.name} in {dish.lowpoly}")

    out = dish.export / f"{dish.name}.glb"
    cmd = [*tool_cmd(cfg, "gltf_transform"), "optimize", str(raw), str(out),
           "--compress", str(xc["compress"]) if xc["compress"] != "none" else "false",
           "--texture-compress", str(xc["texture_compress"]) if xc["texture_compress"] != "none" else "false",
           "--texture-size", str(xc["texture_size"]),
           "--simplify", "true" if xc.get("simplify") else "false"]
    run(cmd, log_file=dish.export / "gltf_transform.log")
    kb_raw, kb_out = raw.stat().st_size / 1024, out.stat().st_size / 1024
    log.info("[%s] exported %s (%.0f KB, raw %.0f KB)", dish.name, out.name, kb_out, kb_raw)
    if kb_out > 5 * 1024:
        log.warning("[%s] GLB is over 5 MB; consider texture_size 1024 or fewer faces for mobile AR", dish.name)
    return {"glb": str(out.relative_to(dish.dir)), "kb": round(kb_out)}
