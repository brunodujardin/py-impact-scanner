"""Archive copies of the analysed photos next to the database.

Every saved record gets its own JPEG in ``<db folder>/images/``, downscaled so the
longest side is at most MAX_SIDE pixels. The record stores the path relative to the
database folder, so the folder can be moved or backed up as a whole.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

MAX_SIDE = 2500          # longest side of the stored copy, in pixels
JPEG_QUALITY = 80        # 0-100; 80 keeps holes and ring edges crisp at a fraction of the size
IMAGES_DIR = "images"


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")
    return stem[:40] or "target"


def store_image(image_bgr: np.ndarray, db_path: str | Path, source_name: str = "") -> tuple[str, float]:
    """Write a downscaled JPEG copy of the image next to the database.

    Returns (relative_path, factor): the path relative to the database folder,
    and the factor by which pixel coordinates measured on the original image must
    be multiplied to match the stored copy (1.0 when no downscaling was needed).
    """
    folder = Path(db_path).resolve().parent / IMAGES_DIR
    folder.mkdir(parents=True, exist_ok=True)

    h, w = image_bgr.shape[:2]
    factor = min(1.0, MAX_SIDE / max(h, w))
    if factor < 1.0:
        image_bgr = cv2.resize(image_bgr, (round(w * factor), round(h * factor)),
                               interpolation=cv2.INTER_AREA)

    base = f"{datetime.now():%Y%m%d_%H%M%S}_{_safe_stem(source_name)}"
    target = folder / f"{base}.jpg"
    n = 1
    while target.exists():
        n += 1
        target = folder / f"{base}_{n}.jpg"

    ok, buf = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise OSError("JPEG encoding failed")
    target.write_bytes(buf.tobytes())  # imencode + write_bytes: safe with non-ASCII paths
    return f"{IMAGES_DIR}/{target.name}", factor


def resolve_image_path(stored: str, db_path: str | Path) -> Path | None:
    """Absolute path of a stored image, or None if it does not exist.

    Relative paths are taken from the database folder; absolute paths (records from
    older versions that kept a link to the original photo) are used as they are.
    """
    if not stored:
        return None
    p = Path(stored)
    if not p.is_absolute():
        p = Path(db_path).resolve().parent / p
    return p if p.exists() else None


def delete_stored_image(stored: str, db_path: str | Path) -> bool:
    """Delete a stored copy. Only files inside <db folder>/images/ are ever removed;
    absolute paths (links to original photos from older records) are left alone.
    Returns True if a file was deleted."""
    if not stored or Path(stored).is_absolute():
        return False
    folder = (Path(db_path).resolve().parent / IMAGES_DIR).resolve()
    target = (Path(db_path).resolve().parent / stored).resolve()
    if folder not in target.parents or not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False
