r"""Run detection on the sample targets and write annotated images for review.

Usage: .venv\Scripts\python tests\check_samples.py [out_dir]

Prints, per sample, the number of blobs, impacts, detected target circles and
groups, then the group sizes (scale assumed from the largest circle = 200 mm, so
the cm values are only meaningful for the solid-disc targets). The annotated
image shows the blobs (blue), the impacts (red, numbered) and each target circle
with its group label and shot count (green).
"""
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from target_analyzer.detection import detect_impacts  # noqa: E402
from target_analyzer.stats import group_stats  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out"
out.mkdir(parents=True, exist_ok=True)
for p in sorted(q for q in (Path(__file__).parent.parent / "samples").glob("*") if q.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")):
    img = cv2.imread(str(p))
    res = detect_impacts(img)
    px_per_mm = 2 * res.disc.r / 200.0 if res.disc else 1.0  # 200 mm black disc
    st = group_stats([(i.x, i.y) for i in res.impacts], px_per_mm)
    counts = [sum(1 for i in res.impacts if i.group == g) for g in range(len(res.groups))]
    print(f"{p.name}: disc={'yes' if res.disc else 'no'} targets={len(res.targets)} groups={len(res.groups)} "
          f"blobs={len(res.blobs)} impacts={len(res.impacts)} single_hole_area={res.single_hole_area:.0f}px2 | "
          f"W={st.width_mm/10:.1f}cm H={st.height_mm/10:.1f}cm D={st.diameter_mm/10:.1f}cm | shots/group={counts}")
    vis = img.copy()
    for g, grp in zip(res.groups, counts):
        if g.cx is not None:
            cv2.circle(vis, (int(g.cx), int(g.cy)), int(g.r), (0, 255, 0), 2)
            cv2.putText(vis, f"{g.label}:{grp}", (int(g.cx - g.r), int(g.cy - g.r) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 0), 2)
    cv2.drawContours(vis, res.blobs, -1, (255, 0, 0), 2)
    for k, imp in enumerate(res.impacts, 1):
        cv2.circle(vis, (int(imp.x), int(imp.y)), 14, (0, 0, 255), 3)
        cv2.putText(vis, str(k), (int(imp.x) + 16, int(imp.y) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    cv2.imwrite(str(out / f"{p.stem}_detected.jpg"), vis)
print("annotated images written to", out)
