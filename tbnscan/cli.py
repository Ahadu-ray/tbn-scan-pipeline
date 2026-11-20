"""Command line entry point.

    python -m tbnscan init   "D:/Scans/ABC Restaurant" --dishes tibs shiro doro_wat
    python -m tbnscan add    "D:/Scans/ABC Restaurant" kitfo --plate-cm 26
    python -m tbnscan run    "D:/Scans/ABC Restaurant"                 # every dish, every automatic stage
    python -m tbnscan run    "D:/Scans/ABC Restaurant" --dish tibs --from mask --to blender
    python -m tbnscan mark   "D:/Scans/ABC Restaurant" tibs cleanup retopo bake
    python -m tbnscan status "D:/Scans/ABC Restaurant"
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path


from . import blender_stage, export, frames, masking, reconstruct
from .common import (DISH_SUBDIRS, MANUAL_STAGES, SHARED_SUBDIRS, STAGES, Dish, list_dishes, log,
                     run_hook, setup_logging)

STAGE_FUNCS = {
    "extract": frames.extract,
    "select": frames.select,
    "mask": masking.mask,
    "reconstruct": reconstruct.reconstruct,
    "blender": blender_stage.blender,
    "export": export.export,
}
HOOK_AFTER = {"reconstruct": "after_reconstruct", "blender": "after_blender", "export": "after_export"}

RESTAURANT_TEMPLATE = """\
# Restaurant-wide overrides of config.default.yaml (same keys).
tools:
  # blender: "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe"
  # realityscan: "C:/Program Files/Epic Games/RealityScan_2.0/RealityScan.exe"
blender:
  plate_diameter_cm: 28   # this restaurant's usual plate / mesob tray
"""

DISH_TEMPLATE = """\
# Per-dish overrides. Most important: the real size of the serving piece in this scan.
profile: {profile}            # standard | detailed
blender:
  plate_diameter_cm: {plate}  # longest side of the plate/tray, in cm
  # flip: true                # set if the model comes out upside down
  # auto_orient: false        # set for tall items (glasses, bottles)
"""


def create_dish(root: Path, name: str, plate_cm: float | None, profile: str) -> Dish:
    dish = Dish(root, name)
    for sub in DISH_SUBDIRS:
        (dish.dir / sub).mkdir(parents=True, exist_ok=True)
    dish.video_dir.mkdir(exist_ok=True)
    (dish.lowpoly / "textures").mkdir(exist_ok=True)
    yml = dish.dir / "dish.yaml"
    if not yml.exists():
        plate = plate_cm if plate_cm else "28"
        yml.write_text(DISH_TEMPLATE.format(plate=plate, profile=profile), encoding="utf-8")
    return dish


def cmd_init(a):
    root = Path(a.restaurant)
    for sub in SHARED_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "Dishes").mkdir(exist_ok=True)
    ry = root / "restaurant.yaml"
    if not ry.exists():
        ry.write_text(RESTAURANT_TEMPLATE, encoding="utf-8")
    for d in a.dishes or []:
        create_dish(root, d, None, "standard")
    print(f"Restaurant ready at {root} ({len(a.dishes or [])} dishes).")
    print("Drop each dish's videos into Dishes/<dish>/Photos/video/ as low.mp4, mid.mp4, top.mp4")


def cmd_add(a):
    dish = create_dish(Path(a.restaurant), a.dish, a.plate_cm, a.profile)
    print(f"Added {dish.dir}")


def stage_range(a) -> list[str]:
    i0 = STAGES.index(a.from_stage) if a.from_stage else 0
    i1 = STAGES.index(a.to_stage) if a.to_stage else len(STAGES) - 1
    return STAGES[i0:i1 + 1]


def run_dish(dish: Dish, stages: list[str], force: bool) -> bool:
    cfg = dish.config()
    for stage in stages:
        if stage in MANUAL_STAGES:
            if not dish.is_done(stage):
                log.info("[%s] waiting on manual step '%s' (run `mark ... %s %s` when done)",
                         dish.name, stage, dish.name, stage)
                return True
            continue
        if dish.is_done(stage) and not force:
            log.info("[%s] %s already done, skipping (use --force to redo)", dish.name, stage)
            continue
        log.info("[%s] -> %s", dish.name, stage)
        try:
            info = STAGE_FUNCS[stage](dish, cfg) or {}
            dish.mark(stage, "done", **info)
            if stage in HOOK_AFTER:
                run_hook(cfg, HOOK_AFTER[stage], dish)
        except Exception as e:
            dish.mark(stage, "failed", error=str(e))
            log.error("[%s] %s failed: %s", dish.name, stage, e)
            log.debug(traceback.format_exc())
            return False
    return True


def cmd_run(a):
    root = Path(a.restaurant)
    dishes = [Dish(root, d) for d in a.dish] if a.dish else list_dishes(root)
    if not dishes:
        sys.exit(f"No dishes under {root / 'Dishes'}")
    stages = stage_range(a)
    failed = [d.name for d in dishes if not run_dish(d, stages, a.force)]
    if failed:
        log.error("Failed dishes: %s", ", ".join(failed))
        sys.exit(1)


def cmd_mark(a):
    dish = Dish(Path(a.restaurant), a.dish)
    for s in a.stages:
        if s not in STAGES:
            sys.exit(f"Unknown stage {s!r}. Stages: {', '.join(STAGES)}")
        dish.mark(s, "pending" if a.undo else "done")
        print(f"{dish.name}: {s} {'reset' if a.undo else 'done'}")


def cmd_status(a):
    dishes = list_dishes(Path(a.restaurant))
    sym = {"done": "x", "failed": "!", "pending": "."}
    w = max([len(d.name) for d in dishes] + [4])
    print("dish".ljust(w), " ".join(s[:5].ljust(5) for s in STAGES))
    for d in dishes:
        st = d.state()["stages"]
        print(d.name.ljust(w), " ".join(sym.get(st.get(s, {}).get("status"), ".").ljust(5) for s in STAGES))
    print("\nx = done, ! = failed, . = to do.  Manual stages: " + ", ".join(sorted(MANUAL_STAGES)))


def main(argv=None):
    p = argparse.ArgumentParser(prog="tbnscan", description="TBN food scan pipeline")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a restaurant folder")
    s.add_argument("restaurant"); s.add_argument("--dishes", nargs="*")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add", help="add a dish")
    s.add_argument("restaurant"); s.add_argument("dish")
    s.add_argument("--plate-cm", type=float); s.add_argument("--profile", default="standard",
                                                             choices=["standard", "detailed"])
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("run", help="run automatic stages")
    s.add_argument("restaurant"); s.add_argument("--dish", nargs="*")
    s.add_argument("--from", dest="from_stage", choices=STAGES)
    s.add_argument("--to", dest="to_stage", choices=STAGES)
    s.add_argument("--force", action="store_true", help="redo stages already marked done")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("mark", help="mark manual stages done (or --undo)")
    s.add_argument("restaurant"); s.add_argument("dish"); s.add_argument("stages", nargs="+")
    s.add_argument("--undo", action="store_true")
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("status", help="show progress per dish")
    s.add_argument("restaurant")
    s.set_defaults(func=cmd_status)

    a = p.parse_args(argv)
    setup_logging(a.verbose)
    a.func(a)


if __name__ == "__main__":
    main()
