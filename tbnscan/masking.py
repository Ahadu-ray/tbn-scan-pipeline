"""Stage 3 (mask): cut the dish + plate out of every selected frame.

With a turntable the background stays still while the dish turns, so the
background MUST be masked or alignment locks onto the static backdrop.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .common import Dish, log


def _rembg_session(model: str):
    try:
        from rembg import new_session
    except ImportError as e:
        raise ImportError("Masking needs rembg: pip install 'rembg[cpu]' (or 'rembg[gpu]').") from e
    return new_session(model)


def _matte_rembg(session, img_bgr: np.ndarray) -> np.ndarray:
    from PIL import Image
    from rembg import remove

    pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    m = remove(pil, session=session, only_mask=True)
    return np.array(m.convert("L"))


def clean_mask(matte: np.ndarray, threshold: int, dilate_px: int) -> np.ndarray:
    """Binarise, keep the largest blob (the dish), fill holes, grow a little."""
    m = (matte >= threshold).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = (labels == largest).astype(np.uint8)
    # fill interior holes (dark sauces sometimes get read as background)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    m = np.zeros_like(m)
    cv2.drawContours(m, contours, -1, 1, thickness=cv2.FILLED)
    if dilate_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1, 2 * dilate_px + 1))
        m = cv2.dilate(m, k)
    return m * 255


def mask(dish: Dish, cfg: dict) -> dict:
    mc = cfg["mask"]
    if mc["backend"] != "rembg":
        raise ValueError(f"Unknown mask backend {mc['backend']!r} (only 'rembg' is implemented).")
    frames = sorted(dish.selected_dir.glob("*.jpg"))
    if not frames:
        raise FileNotFoundError(f"No selected frames in {dish.selected_dir}; run the select stage first.")
    session = _rembg_session(mc["model"])
    cut_dir = dish.photos / "cutouts"
    if mc.get("write_cutouts"):
        cut_dir.mkdir(exist_ok=True)
    empty = 0
    for i, f in enumerate(frames, 1):
        img = cv2.imread(str(f))
        m = clean_mask(_matte_rembg(session, img), mc["threshold"], mc["dilate_px"])
        if m.mean() < 255 * 0.01:
            empty += 1
            log.warning("[%s] mask for %s is almost empty", dish.name, f.name)
        cv2.imwrite(str(f.parent / mc["mask_name"].format(stem=f.stem, name=f.name)), m)
        if mc.get("write_cutouts"):
            rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            rgba[:, :, 3] = m
            cv2.imwrite(str(cut_dir / f"{f.stem}.png"), rgba)
        if i % 20 == 0:
            log.info("[%s] masked %d/%d", dish.name, i, len(frames))
    log.info("[%s] masked %d frames (%d nearly empty)", dish.name, len(frames), empty)
    return {"masked": len(frames), "empty": empty}
