"""Stage 1 (extract) and stage 2 (select): video -> frames -> sharpest, evenly spaced frames."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .common import Dish, log, run, tool_cmd


# ----------------------------------------------------------------------------- extract
def find_ring_videos(dish: Dish, cfg: dict) -> dict[str, Path]:
    vids = {}
    for ring in cfg["capture"]["rings"]:
        for ext in cfg["capture"]["video_exts"]:
            p = dish.video_dir / f"{ring}{ext}"
            if p.exists():
                vids[ring] = p
                break
    # Detailed profile: optional second pass, e.g. low_2.mp4, mid_2.mp4 ...
    for p in sorted(dish.video_dir.glob("*_2.*")) if dish.video_dir.exists() else []:
        if p.suffix in cfg["capture"]["video_exts"]:
            vids[p.stem] = p
    return vids


def video_duration(ffmpeg_cmd: list[str], video: Path) -> float | None:
    ffprobe = ffmpeg_cmd[0].replace("ffmpeg", "ffprobe")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
            capture_output=True, text=True, check=True,
        ).stdout
        return float(json.loads(out)["format"]["duration"])
    except Exception:  # ffprobe missing or odd container: just don't trim the end
        return None


def extract(dish: Dish, cfg: dict) -> dict:
    ec = cfg["extract"]
    ffmpeg = tool_cmd(cfg, "ffmpeg")
    vids = find_ring_videos(dish, cfg)
    if not vids:
        raise FileNotFoundError(
            f"No videos in {dish.video_dir}. Expected e.g. low.mp4, mid.mp4, top.mp4 "
            f"(rings: {cfg['capture']['rings']})."
        )
    counts = {}
    for ring, video in vids.items():
        out = dish.frames_dir / ring
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        dur = video_duration(ffmpeg, video)
        cmd = [*ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(ec["trim_start_s"]), "-i", str(video)]
        if dur:
            cmd += ["-t", f"{max(0.1, dur - ec['trim_start_s'] - ec['trim_end_s']):.3f}"]
        # yuvj420p keeps full-range SDR JPEGs; record SDR (not HDR/Dolby Vision) on the phones.
        cmd += ["-vf", f"fps={ec['fps']}", "-pix_fmt", "yuvj420p", "-q:v", str(ec["jpeg_quality"]),
                str(out / f"{ring}_%04d.jpg")]
        run(cmd)
        counts[ring] = len(list(out.glob("*.jpg")))
        log.info("[%s] extracted %d frames from %s", dish.name, counts[ring], video.name)
    return {"frames": counts}


# ----------------------------------------------------------------------------- select
def sharpness(path: Path, crop: float, width: int) -> float:
    """Variance of the Laplacian on the central crop: higher = sharper."""
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, w = img.shape
    ch, cw = int(h * crop), int(w * crop)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    img = img[y0:y0 + ch, x0:x0 + cw]
    if cw > width:
        img = cv2.resize(img, (width, int(ch * width / cw)), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def pick_frames(scores: list[float], target: int, min_rel: float) -> list[int]:
    """Split the rotation into `target` equal time bins and keep the sharpest frame of each bin.

    Even spacing matters more than raw sharpness: photogrammetry needs consistent overlap
    around the dish. Bins whose best frame is still blurry (< min_rel x median) are dropped.
    """
    n = len(scores)
    if n == 0:
        return []
    target = min(target, n)
    med = float(np.median(scores)) or 1.0
    edges = np.linspace(0, n, target + 1).astype(int)
    keep = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        i = a + int(np.argmax(scores[a:b]))
        if scores[i] >= min_rel * med:
            keep.append(i)
    return keep


def select(dish: Dish, cfg: dict) -> dict:
    sc = cfg["select"]
    target = sc["per_ring"].get(cfg.get("profile", "standard"), 50)
    if dish.selected_dir.exists():
        shutil.rmtree(dish.selected_dir)
    dish.selected_dir.mkdir(parents=True)
    report, kept_total = {}, 0
    rings = sorted(p for p in dish.frames_dir.iterdir() if p.is_dir()) if dish.frames_dir.exists() else []
    if not rings:
        raise FileNotFoundError(f"No extracted frames in {dish.frames_dir}; run the extract stage first.")
    for ring_dir in rings:
        frames = sorted(ring_dir.glob("*.jpg"))
        scores = [sharpness(f, sc["center_crop"], sc["analysis_width"]) for f in frames]
        keep = pick_frames(scores, target, sc["min_rel_sharpness"])
        for i in keep:
            shutil.copy2(frames[i], dish.selected_dir / frames[i].name)
        kept_total += len(keep)
        report[ring_dir.name] = {
            "frames": len(frames), "kept": len(keep),
            "median_sharpness": round(float(np.median(scores)), 1) if scores else 0,
        }
        log.info("[%s] ring %s: kept %d of %d frames", dish.name, ring_dir.name, len(keep), len(frames))
        gap = max((b - a for a, b in zip(keep[:-1], keep[1:])), default=0)
        bin_len = len(frames) / max(1, min(target, len(frames)))
        if gap > 4 * bin_len:
            log.warning("[%s] ring %s has a blurry stretch (%d frames, ~%d%% of the turn) with no usable frames: "
                        "reshoot or add a second pass", dish.name, ring_dir.name, gap,
                        round(100 * gap / len(frames)))
        if len(keep) < 0.7 * min(target, len(frames)):
            log.warning("[%s] ring %s has many blurry frames: check shutter speed / turntable speed",
                        dish.name, ring_dir.name)
    (dish.photos / "selection_report.json").write_text(json.dumps(report, indent=2))
    return {"kept": kept_total}
