"""How impacts and groups are drawn on the photo: colour, line style, width.

Pure Python (no Tk): the UI turns a `LineStyle` into canvas options, the
database stores the whole `DrawStyle` as JSON in its settings table.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

# Tk canvas dash patterns. The string forms scale with the line width and are the
# ones Tk supports on every platform (numeric lists are approximated on Windows).
LINE_STYLES = {"solid": "", "dashed": "-", "dotted": ".", "dash-dot": "-."}
GROUP_COLOUR = "group"  # colour value meaning "the colour of the group (palette)"
WIDTH_RANGE = (1, 12)
IMPACT_SIZE_RANGE = (3, 40)

# (key, label shown in the Parameters tab)
ELEMENTS = (
    ("impact", "Impact circle"),
    ("impact_manual", "Impact added by hand"),
    ("group_circle", "Group: target circle"),
    ("group_box", "Group: box and centre cross"),
    ("selected_circle", "Selected group: target circle"),
    ("selected_box", "Selected group: box and centre cross"),
)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class LineStyle:
    colour: str = "#ffff00"  # "#rrggbb" or GROUP_COLOUR
    style: str = "solid"     # a key of LINE_STYLES
    width: int = 2           # screen pixels

    def dash(self) -> str:
        return LINE_STYLES.get(self.style, "")


def default_lines() -> dict[str, LineStyle]:
    return {
        "impact": LineStyle("#ffff00", "solid", 2),
        "impact_manual": LineStyle("#ffff00", "dashed", 2),
        "group_circle": LineStyle(GROUP_COLOUR, "dashed", 1),
        "group_box": LineStyle(GROUP_COLOUR, "dashed", 1),
        "selected_circle": LineStyle("#ff0000", "solid", 4),
        "selected_box": LineStyle("#ff0000", "solid", 2),
    }


@dataclass
class DrawStyle:
    lines: dict[str, LineStyle] = field(default_factory=default_lines)
    impact_size: int = 10  # radius of an impact circle, screen pixels at "fit" zoom
    scale_with_zoom: bool = True  # True: the radius grows with the zoom (same size on the photo)

    def impact_radius(self, zoom: float) -> float:
        """Radius in screen pixels at the given zoom (1 = photo fitted to the canvas)."""
        return self.impact_size * zoom if self.scale_with_zoom else float(self.impact_size)

    def to_json(self) -> str:
        return json.dumps({"lines": {k: asdict(v) for k, v in self.lines.items()},
                           "impact_size": self.impact_size, "scale_with_zoom": self.scale_with_zoom})

    @classmethod
    def from_json(cls, text: str | None) -> "DrawStyle":
        """Parse a stored style; anything missing or invalid falls back to the default,
        so a settings row from another version never breaks the display."""
        out = cls()
        if not text:
            return out
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return out
        if not isinstance(data, dict):
            return out
        lines = data.get("lines") if isinstance(data.get("lines"), dict) else {}
        for key, default in out.lines.items():
            item = lines.get(key)
            if not isinstance(item, dict):
                continue
            colour = item.get("colour")
            if isinstance(colour, str) and (colour == GROUP_COLOUR or _HEX.match(colour)):
                default.colour = colour
            if item.get("style") in LINE_STYLES:
                default.style = item["style"]
            width = item.get("width")
            if isinstance(width, int) and WIDTH_RANGE[0] <= width <= WIDTH_RANGE[1]:
                default.width = width
        size = data.get("impact_size")
        if isinstance(size, int) and IMPACT_SIZE_RANGE[0] <= size <= IMPACT_SIZE_RANGE[1]:
            out.impact_size = size
        if isinstance(data.get("scale_with_zoom"), bool):
            out.scale_with_zoom = data["scale_with_zoom"]
        return out
