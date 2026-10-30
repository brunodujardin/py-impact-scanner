r"""Run detection on the sample targets and write annotated images for review.

Usage: .venv\Scripts\python tests\check_samples.py [out_dir]
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
    print(f"{p.name}: disc={'yes' if res.disc else 'no'} blobs={len(res.blobs)} impacts={len(res.impacts)} "
          f"single_hole_area={res.single_hole_area:.0f}px2 | W={st.width_mm/10:.1f}cm H={st.height_mm/10:.1f}cm "
          f"D={st.diameter_mm/10:.1f}cm")
    vis = img.copy()
    if res.disc:
        cv2.circle(vis, (int(res.disc.cx), int(res.disc.cy)), int(res.disc.r), (0, 255, 0), 2)
    cv2.drawContours(vis, res.blobs, -1, (255, 0, 0), 2)
    for k, imp in enumerate(res.impacts, 1):
        cv2.circle(vis, (int(imp.x), int(imp.y)), 14, (0, 0, 255), 3)
        cv2.putText(vis, str(k), (int(imp.x) + 16, int(imp.y) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    cv2.imwrite(str(out / f"{p.stem}_detected.jpg"), vis)
print("annotated images written to", out)
