"""Archive copies of the analysed photos next to the database.

Every saved record gets its own JPEG in ``<db folder>/images/``, downscaled so the
longest side is at most MAX_SIDE pixels. The record stores the path relative to the
database folder, so the folder can be moved or backed up as a whole.

File names are ``<YYYYmmdd_HHMMSS>_r<record id>_<sanitised source stem>.jpg``: the
record id makes the name unique within a database even when the same photo (or two
photos with the same name) is saved several times in the same second, and the file
is created exclusively, so an existing file is never overwritten (a ``_2``, ``_3``,
... suffix is added if the name is taken anyway, e.g. after restoring an old
database next to a newer images folder).
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


_STORED_NAME = re.compile(r"^\d{8}_\d{6}(?:_r\d+)?_(.+)\.[A-Za-z]+$")
_CLASH_SUFFIX = re.compile(r"^(.+)_(\d+)(\.[A-Za-z]+)$")


def stored_factor(image_bgr: np.ndarray) -> float:
    """Factor (<= 1) from original pixels to the pixels of the stored copy."""
    h, w = image_bgr.shape[:2]
    return min(1.0, MAX_SIDE / max(h, w))


def source_name_of(stored: str, siblings: set[str] | frozenset[str] = frozenset()) -> str:
    """Best guess of the original file name for records saved before the name was
    kept: the file name of an absolute path, or the stem embedded in a stored copy
    name (without the timestamp and record id).

    A trailing "_<n>" is removed only when the same path without it is among
    siblings (the other stored paths): only then was it a clash suffix, otherwise it
    belongs to the photo's name (IMG_5862).
    """
    if not stored:
        return ""
    p = Path(stored)
    if p.is_absolute():
        return p.name
    name = p.name
    m = _CLASH_SUFFIX.match(name)
    if m and str(p.with_name(m.group(1) + m.group(3))).replace("\\", "/") in siblings:
        name = m.group(1) + m.group(3)
    m = _STORED_NAME.match(name)
    if not m:
        return name
    stem = m.group(1)
    if _STORED_NAME.match(stem + ".jpg"):
        # a record re-saved after "Load record" by an older version took the stored
        # copy's name as its source: unwrap it as if it were that copy's path
        return source_name_of(str(p.with_name(stem + ".jpg")).replace("\\", "/"), siblings)
    return stem


def store_image(image_bgr: np.ndarray, db_path: str | Path, source_name: str = "",
                record_id: int | None = None) -> tuple[str, float]:
    """Write a downscaled JPEG copy of the image next to the database.

    Returns (relative_path, factor): the path relative to the database folder,
    and the factor by which pixel coordinates measured on the original image must
    be multiplied to match the stored copy (1.0 when no downscaling was needed).
    record_id goes into the file name (see the module docstring).
    """
    folder = Path(db_path).resolve().parent / IMAGES_DIR
    folder.mkdir(parents=True, exist_ok=True)

    h, w = image_bgr.shape[:2]
    factor = stored_factor(image_bgr)
    if factor < 1.0:
        image_bgr = cv2.resize(image_bgr, (round(w * factor), round(h * factor)),
                               interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise OSError("JPEG encoding failed")

    rid = f"_r{record_id}" if record_id is not None else ""
    base = f"{datetime.now():%Y%m%d_%H%M%S}{rid}_{_safe_stem(source_name)}"
    n = 1
    while True:
        target = folder / (f"{base}.jpg" if n == 1 else f"{base}_{n}.jpg")
        try:
            # "x" = exclusive create: fails instead of overwriting, no check-then-write race.
            # Python's open() handles non-ASCII paths (cv2.imwrite does not, hence imencode).
            with open(target, "xb") as fh:
                fh.write(buf.tobytes())
            break
        except FileExistsError:
            n += 1
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
