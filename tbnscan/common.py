"""Shared helpers: config merging, folder layout, per-dish state, logging, tool calls."""
from __future__ import annotations

import copy
import json
import logging
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

PKG_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = PKG_ROOT.parent / "config.default.yaml"

DISH_SUBDIRS = ["Photos", "Scan", "Highpoly", "Lowpoly", "Export"]
SHARED_SUBDIRS = ["Shared_Assets/Plates", "Shared_Assets/Materials", "Shared_Assets/Injera"]

# Order matters. Manual stages are done by you in Blender / Substance and marked with `tbn mark`.
STAGES = ["extract", "select", "mask", "reconstruct", "blender", "cleanup", "retopo", "bake", "export"]
MANUAL_STAGES = {"cleanup", "retopo", "bake"}

log = logging.getLogger("tbn")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def deep_merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if v is None and isinstance(out.get(k), dict):
            continue  # empty section in a yaml file (only comments): keep defaults
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Dish:
    restaurant_dir: Path
    name: str

    @property
    def dir(self) -> Path:
        return self.restaurant_dir / "Dishes" / self.name

    # --- layout -------------------------------------------------------------
    @property
    def photos(self) -> Path: return self.dir / "Photos"
    @property
    def video_dir(self) -> Path: return self.photos / "video"
    @property
    def frames_dir(self) -> Path: return self.photos / "frames"      # all extracted frames, per ring
    @property
    def selected_dir(self) -> Path: return self.photos / "selected"  # sharp frames + masks, flat (engine input)
    @property
    def scan(self) -> Path: return self.dir / "Scan"
    @property
    def highpoly(self) -> Path: return self.dir / "Highpoly"
    @property
    def lowpoly(self) -> Path: return self.dir / "Lowpoly"
    @property
    def export(self) -> Path: return self.dir / "Export"
    @property
    def state_file(self) -> Path: return self.dir / "pipeline_state.json"

    # --- config -------------------------------------------------------------
    def config(self) -> dict:
        cfg = load_yaml(DEFAULT_CONFIG)
        cfg = deep_merge(cfg, load_yaml(self.restaurant_dir / "restaurant.yaml"))
        cfg = deep_merge(cfg, load_yaml(self.dir / "dish.yaml"))
        return cfg

    # --- state --------------------------------------------------------------
    def state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"stages": {}}

    def mark(self, stage: str, status: str = "done", **info) -> None:
        st = self.state()
        st["stages"][stage] = {"status": status, "time": time.strftime("%Y-%m-%d %H:%M:%S"), **info}
        self.state_file.write_text(json.dumps(st, indent=2), encoding="utf-8")

    def is_done(self, stage: str) -> bool:
        return self.state()["stages"].get(stage, {}).get("status") == "done"


def list_dishes(restaurant_dir: Path) -> list[Dish]:
    d = restaurant_dir / "Dishes"
    if not d.exists():
        return []
    return [Dish(restaurant_dir, p.name) for p in sorted(d.iterdir()) if p.is_dir() and not p.name.startswith(".")]


def tool_cmd(cfg: dict, name: str) -> list[str]:
    """Return a tool command as an argv list (config value may contain spaces/args)."""
    raw = cfg["tools"][name]
    if Path(raw).exists():          # a plain path, possibly with spaces
        return [raw]
    parts = shlex.split(raw, posix=(sys.platform != "win32"))
    if not shutil.which(parts[0]) and not Path(parts[0]).exists():
        raise FileNotFoundError(f"Tool '{name}' not found: {raw!r}. Set tools.{name} in restaurant.yaml.")
    return parts


def run(cmd: list[str], cwd: Path | None = None, log_file: Path | None = None) -> None:
    log.debug("RUN %s", " ".join(map(str, cmd)))
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "w", encoding="utf-8") as lf:
            proc = subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=lf, stderr=subprocess.STDOUT)
    else:
        proc = subprocess.run([str(c) for c in cmd], cwd=cwd)
    if proc.returncode != 0:
        hint = f" (see {log_file})" if log_file else ""
        raise RuntimeError(f"Command failed with code {proc.returncode}{hint}: {cmd[0]}")


def run_hook(cfg: dict, name: str, dish: Dish) -> None:
    cmd = (cfg.get("hooks") or {}).get(name)
    if not cmd:
        return
    cmd = cmd.format(dish_dir=dish.dir, dish=dish.name, restaurant_dir=dish.restaurant_dir)
    log.info("[%s] hook %s: %s", dish.name, name, cmd)
    subprocess.run(cmd, shell=True, check=True)
