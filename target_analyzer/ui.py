"""Tkinter user interface for the target analyser."""
from __future__ import annotations

import tkinter as tk
from datetime import date
from math import ceil, exp, floor, hypot, log, pi
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageTk

try:  # HEIC / HEIF (iPhone photos) support is optional
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:  # pragma: no cover
    HEIF_OK = False

from .db import Database, GroupRecord
from .detection import Group, Impact, assign_groups, detect_impacts, find_disc, find_targets
from .drawstyle import (ELEMENTS, GROUP_COLOUR, IMPACT_SIZE_RANGE, LINE_STYLES, WIDTH_RANGE,
                        DrawStyle)
from .images import resolve_image_path, source_name_of, store_image, stored_factor
from .stats import GroupStats, cluster_points, from_unit, group_stats, summarize_groups, to_unit

UNITS = ("cm", "inch")
DEFAULT_DISC_MM = 200.0  # black disc of a 25 m precision pistol target
DEFAULT_CLUSTER_MM = 50.0  # "Auto-group by distance": shots closer than this belong together
IMAGE_EXTS = "*.jpg *.jpeg *.jpe *.png *.heic *.heif *.bmp *.tif *.tiff *.webp"
# group palette: no red (the selected group) and no yellow (the impacts, by default)
GROUP_COLOURS = ("#20a0ff", "#30d030", "#e040e0", "#00d0d0", "#8080ff", "#a0ff40",
                 "#40e0a0", "#c080ff", "#80c0ff", "#ff70ff")
STYLE_SETTING = "draw_style"  # settings key of the display style (JSON)
# background colour of each tab name (full colour when selected, a light tint otherwise)
TAB_COLOURS = {"Analyse": "#2f6db3", "Records & averages": "#2e8b57", "Parameters": "#c96f1e"}
TAB_TINT = 0.7  # share of white mixed into the colour of an unselected tab


def tint(colour: str, amount: float) -> str:
    """Mix a #rrggbb colour with white (amount 0 = colour, 1 = white)."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(c + (255 - c) * amount) for c in (r, g, b))
CLICK_TOLERANCE_PX = 25  # screen pixels for the Remove / Assign tools (at least the circle radius)
ZOOM_STEP = 1.25         # zoom factor per mouse-wheel notch or +/- press
ZOOM_MAX = 40.0          # maximum zoom, relative to "fit to window"


def load_image(path: str) -> np.ndarray | None:
    """Read any common photo format (incl. HEIC) as a BGR array, honouring EXIF rotation."""
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def group_colour(index: int) -> str:
    return GROUP_COLOURS[index % len(GROUP_COLOURS)]


class ScaleDialog(simpledialog.Dialog):
    """Ask the real-world length of the measured segment."""

    def __init__(self, parent, pixels: float, default_unit: str):
        self.pixels = pixels
        self.default_unit = default_unit
        self.result = None
        super().__init__(parent, "Set scale")

    def body(self, master):
        ttk.Label(master, text=f"Measured segment: {self.pixels:.1f} px").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(master, text="Real length:").grid(row=1, column=0, sticky="w")
        self.value = ttk.Entry(master, width=10)
        self.value.grid(row=1, column=1, padx=4)
        self.unit = tk.StringVar(value=self.default_unit)
        for i, u in enumerate(UNITS):
            ttk.Radiobutton(master, text=u, variable=self.unit, value=u).grid(
                row=2, column=i + 1, sticky="w")
        return self.value

    def validate(self):
        try:
            v = float(self.value.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Scale", "Enter a number.", parent=self)
            return False
        if v <= 0:
            messagebox.showerror("Scale", "Length must be positive.", parent=self)
            return False
        self._value = v
        return True

    def apply(self):
        self.result = (self._value, self.unit.get())


class App(tk.Tk):
    def __init__(self, db_path: Path):
        super().__init__()
        self.title("Target impact analyser")
        self.geometry("1500x920")
        self.db = Database(db_path)
        self.style = DrawStyle.from_json(self.db.get_setting(STYLE_SETTING))

        # analysis state
        self.image_path: str | None = None
        self.source_name = ""  # original file name of the photo (kept through Load record)
        self.image_bgr = None
        self.pil_image: Image.Image | None = None
        self.tk_image = None
        self.view_scale = 1.0       # screen px per image px (= fit scale * zoom)
        self.view_off = (0.0, 0.0)  # screen position of image pixel (0, 0)
        self.zoom = 1.0             # 1 = whole image fitted to the canvas
        self._view_size: tuple[int, int] | None = None  # canvas size used for view_off
        self._pan_anchor: tuple[float, float, float, float] | None = None
        self.mode: str | None = None
        self.scale_points: list[tuple[float, float]] = []
        self.scale_line_px: float | None = None
        self.px_per_mm: float | None = None
        self.impacts: list[Impact] = []
        self.groups: list[Group] = []
        self.cur_group: int | None = None
        self.disc = None
        self.group_stats: dict[int, GroupStats] = {}  # group index -> stats (needs the scale)
        self.summary: dict = {}

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI
    def _build(self):
        # ttk notebook tabs cannot be coloured one by one (and the Windows theme ignores
        # tab colours), so the notebook's own tab row is hidden and replaced by a bar of
        # coloured labels that select the pages
        style = ttk.Style(self)
        style.layout("Coloured.TNotebook.Tab", [])
        style.configure("Coloured.TNotebook", borderwidth=0, tabmargins=0)
        self.tab_bar = tk.Frame(self)
        self.tab_bar.pack(fill="x", padx=4, pady=(4, 0))
        self.tab_strip = tk.Frame(self, height=4)  # colour of the selected tab, joins it to the page
        self.tab_strip.pack(fill="x", padx=4)
        self.nb = ttk.Notebook(self, style="Coloured.TNotebook")
        self.nb.pack(fill="both", expand=True)
        self.tab_analyze = ttk.Frame(self.nb)
        self.tab_records = ttk.Frame(self.nb)
        self.tab_params = ttk.Frame(self.nb)
        self.tab_labels: dict[str, tk.Label] = {}
        for frame, text in ((self.tab_analyze, "Analyse"), (self.tab_records, "Records & averages"),
                            (self.tab_params, "Parameters")):
            self.nb.add(frame, text=text)
            lbl = tk.Label(self.tab_bar, text=text, padx=16, pady=5, cursor="hand2", font=("", 10))
            lbl.pack(side="left", padx=(0, 3))
            lbl.bind("<Button-1>", lambda e, f=frame: self.nb.select(f))
            self.tab_labels[str(frame)] = lbl
        self._build_analyze()
        self._build_records()
        self._build_params()
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._update_tab_bar()

    def _update_tab_bar(self):
        current = self.nb.select()
        for name, lbl in self.tab_labels.items():
            colour = TAB_COLOURS[lbl.cget("text")]
            if name == current:
                lbl.config(bg=colour, fg="white", font=("", 10, "bold"))
                self.tab_strip.config(bg=colour)
            else:
                lbl.config(bg=tint(colour, TAB_TINT), fg=colour, font=("", 10))

    def _on_tab_changed(self, _event=None):
        self._update_tab_bar()
        self._refresh_records()

    def _build_analyze(self):
        root = self.tab_analyze
        left = ttk.Frame(root, padding=6)
        left.pack(side="left", fill="y")
        right = ttk.Frame(root, padding=6, width=360)
        right.pack(side="right", fill="y")
        centre = ttk.Frame(root)
        centre.pack(side="left", fill="both", expand=True)
        zbar = ttk.Frame(centre, padding=(4, 2))
        zbar.pack(fill="x")
        ttk.Button(zbar, text="−", width=3, command=lambda: self.zoom_at(1 / ZOOM_STEP)).pack(side="left")
        ttk.Button(zbar, text="+", width=3, command=lambda: self.zoom_at(ZOOM_STEP)).pack(side="left", padx=2)
        # the slider works on log(zoom) so every notch is the same relative step
        self.var_zoom = tk.DoubleVar(value=0.0)
        self.zoom_slider = ttk.Scale(zbar, from_=0.0, to=log(ZOOM_MAX), orient="horizontal",
                                     length=180, variable=self.var_zoom, command=self._on_zoom_slider)
        self.zoom_slider.pack(side="left", padx=4)
        ttk.Button(zbar, text="Fit", width=5, command=self.zoom_fit).pack(side="left")
        ttk.Button(zbar, text="Zoom to group", command=self.zoom_to_group).pack(side="left", padx=2)
        self.lbl_zoom = ttk.Label(zbar, text="Zoom: fit", width=12)
        self.lbl_zoom.pack(side="left", padx=6)
        ttk.Label(zbar, foreground="#666",
                  text="Ctrl+click: in · Ctrl+right-click: out · right-drag: pan").pack(side="left")
        self.canvas = tk.Canvas(centre, bg="#333", cursor="crosshair", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # --- left toolbar
        ttk.Button(left, text="1. Open image…", command=self.open_image).pack(fill="x", pady=2)
        ttk.Separator(left).pack(fill="x", pady=6)
        ttk.Label(left, text="2. Scale").pack(anchor="w")
        ttk.Button(left, text="Measure 2 points", command=lambda: self.set_mode("scale")).pack(fill="x", pady=2)
        ttk.Button(left, text="Auto: from outer black circle", command=self.auto_scale).pack(fill="x", pady=2)
        self.lbl_scale = ttk.Label(left, text="scale: not set", foreground="red")
        self.lbl_scale.pack(anchor="w")
        ttk.Separator(left).pack(fill="x", pady=6)

        ttk.Label(left, text="3. Detection").pack(anchor="w")
        grid = ttk.Frame(left)
        grid.pack(fill="x")
        self.var_paper = tk.IntVar(value=84)
        self.var_sat = tk.IntVar(value=95)
        self.var_kernel = tk.IntVar(value=11)
        self.var_minarea = tk.IntVar(value=150)
        self.var_hole = tk.DoubleVar(value=4.0)
        self.var_shots = tk.StringVar(value="")
        for r, (txt, var) in enumerate((("Hole max bright. (% paper)", self.var_paper),
                                        ("Colour saturation (0-255)", self.var_sat),
                                        ("Open kernel (px)", self.var_kernel),
                                        ("Min area (px²)", self.var_minarea),
                                        ("Hole core Ø (mm)", self.var_hole),
                                        ("Shots fired (opt.)", self.var_shots))):
            ttk.Label(grid, text=txt).grid(row=r, column=0, sticky="w")
            ttk.Entry(grid, textvariable=var, width=7).grid(row=r, column=1, padx=3, pady=1)
        ttk.Button(left, text="Analyse impacts", command=self.analyze).pack(fill="x", pady=4)
        ttk.Label(left, text="Correct manually:").pack(anchor="w")
        ttk.Button(left, text="Add impact (click)", command=lambda: self.set_mode("add")).pack(fill="x", pady=1)
        ttk.Button(left, text="Remove impact (click)", command=lambda: self.set_mode("remove")).pack(fill="x", pady=1)
        ttk.Button(left, text="Move impact to selected group (click)",
                   command=lambda: self.set_mode("assign")).pack(fill="x", pady=1)
        ttk.Button(left, text="Clear all impacts", command=self.clear_impacts).pack(fill="x", pady=1)
        self.lbl_mode = ttk.Label(left, text="", foreground="blue", wraplength=190)
        self.lbl_mode.pack(anchor="w", pady=4)

        # --- right panel: groups, results, save
        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="Groups", font=("", 11, "bold")).pack(side="left")
        ttk.Label(head, text="Display unit:").pack(side="left", padx=(16, 0))
        self.var_unit = tk.StringVar(value="cm")
        cb = ttk.Combobox(head, textvariable=self.var_unit, values=UNITS, width=6, state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.update_stats())

        brow = ttk.Frame(right)
        brow.pack(fill="x", pady=(4, 0))
        ttk.Button(brow, text="New", width=7, command=self.new_group).pack(side="left", padx=1)
        ttk.Button(brow, text="Rename", width=8, command=self.rename_group).pack(side="left", padx=1)
        ttk.Button(brow, text="Delete", width=7, command=self.delete_group).pack(side="left", padx=1)
        brow2 = ttk.Frame(right)
        brow2.pack(fill="x", pady=(2, 4))
        ttk.Button(brow2, text="Auto-group: target circles", command=self.auto_group_circles).pack(side="left", padx=1)
        ttk.Button(brow2, text="Auto-group: distance", command=self.auto_group_distance).pack(side="left", padx=1)

        cols = ("label", "n", "width", "height", "diameter")
        self.gtree = ttk.Treeview(right, columns=cols, show="headings", selectmode="browse", height=9)
        for c, txt, w in zip(cols, ("Group", "Shots", "Width", "Height", "Diameter"), (70, 45, 70, 70, 75)):
            self.gtree.heading(c, text=txt)
            self.gtree.column(c, width=w, anchor="center")
        self.gtree.pack(fill="x")
        self.gtree.bind("<<TreeviewSelect>>", self._on_group_select)
        self.gtree.bind("<Double-1>", lambda e: self.zoom_to_group())
        self.gtree.tag_configure("empty", foreground="#888")

        ttk.Label(right, text="Selected group", font=("", 10, "bold")).pack(anchor="w", pady=(6, 0))
        self.lbl_results = {}
        res = ttk.Frame(right)
        res.pack(fill="x", pady=2)
        for r, (key, txt) in enumerate((("n", "Shots"), ("width", "Width"), ("height", "Height"),
                                        ("diameter", "Diameter (extreme spread)"))):
            ttk.Label(res, text=txt + ":").grid(row=r, column=0, sticky="w")
            lbl = ttk.Label(res, text="-", font=("", 10, "bold"))
            lbl.grid(row=r, column=1, sticky="w", padx=6)
            self.lbl_results[key] = lbl

        ttk.Label(right, text="This image (all groups with shots)", font=("", 10, "bold")).pack(anchor="w", pady=(6, 0))
        self.lbl_summary = {}
        summ = ttk.Frame(right)
        summ.pack(fill="x", pady=2)
        for r, (key, txt) in enumerate((("groups", "Groups / shots"), ("width", "Width mean ± σ"),
                                        ("height", "Height mean ± σ"), ("diameter", "Diameter mean ± σ"),
                                        ("p90", "Diameter P90"))):
            ttk.Label(summ, text=txt + ":").grid(row=r, column=0, sticky="w")
            lbl = ttk.Label(summ, text="-", font=("", 10, "bold"))
            lbl.grid(row=r, column=1, sticky="w", padx=6)
            self.lbl_summary[key] = lbl
        ttk.Separator(right).pack(fill="x", pady=6)

        ttk.Label(right, text="Save record", font=("", 11, "bold")).pack(anchor="w")
        form = ttk.Frame(right)
        form.pack(fill="x")
        self.form = {}
        for r, (key, txt, default) in enumerate((("shot_date", "Date", date.today().isoformat()),
                                                 ("weapon", "Weapon", ""),
                                                 ("distance", "Distance", ""),
                                                 ("ammo", "Ammo", ""),
                                                 ("notes", "Notes", ""))):
            ttk.Label(form, text=txt).grid(row=r, column=0, sticky="w")
            e = ttk.Entry(form, width=28)
            e.insert(0, default)
            e.grid(row=r, column=1, pady=1, sticky="ew")
            self.form[key] = e
        ttk.Button(right, text="Save to database", command=self.save_record).pack(fill="x", pady=6)
        self.lbl_status = ttk.Label(right, text="", wraplength=340, foreground="green")
        self.lbl_status.pack(anchor="w")

        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._pan_end)
        for b in (2, 3):  # middle or right button: pan whatever tool is active
            self.canvas.bind(f"<Button-{b}>", self._pan_start)
            self.canvas.bind(f"<B{b}-Motion>", self._pan_move)
            self.canvas.bind(f"<ButtonRelease-{b}>", self._pan_end)
        # Ctrl+click zooms at the cursor whatever tool is active (more specific than
        # <Button-1>, so Tk does not also run on_click / start a pan)
        self.canvas.bind("<Control-Button-1>", lambda e: self._ctrl_zoom(e, ZOOM_STEP ** 2))
        self.canvas.bind("<Control-Shift-Button-1>", lambda e: self._ctrl_zoom(e, ZOOM_STEP ** -2))
        self.canvas.bind("<Control-Button-3>", lambda e: self._ctrl_zoom(e, ZOOM_STEP ** -2))
        self.canvas.bind("<MouseWheel>", self._on_wheel)  # Windows, macOS
        self.canvas.bind("<Button-4>", lambda e: self.zoom_at(ZOOM_STEP, e.x, e.y))  # X11
        self.canvas.bind("<Button-5>", lambda e: self.zoom_at(1 / ZOOM_STEP, e.x, e.y))
        for key in ("<plus>", "<equal>", "<KP_Add>"):
            self.canvas.bind(key, lambda e: self.zoom_at(ZOOM_STEP))
        for key in ("<minus>", "<KP_Subtract>"):
            self.canvas.bind(key, lambda e: self.zoom_at(1 / ZOOM_STEP))
        for key in ("<Key-0>", "<KP_0>", "<Home>"):
            self.canvas.bind(key, lambda e: self.zoom_fit())

    def _build_records(self):
        root = self.tab_records
        top = ttk.Frame(root, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Weapon filter:").pack(side="left")
        self.var_filter = tk.StringVar(value="")
        self.cb_filter = ttk.Combobox(top, textvariable=self.var_filter, width=18)
        self.cb_filter.pack(side="left", padx=4)
        self.cb_filter.bind("<<ComboboxSelected>>", lambda e: self._refresh_records())
        ttk.Button(top, text="Refresh", command=self._refresh_records).pack(side="left", padx=4)
        ttk.Button(top, text="Average of selection", command=self.compute_average).pack(side="left", padx=4)
        ttk.Button(top, text="Average of all listed", command=lambda: self.compute_average(all_rows=True)).pack(side="left", padx=4)
        ttk.Button(top, text="Load record", command=self.load_record).pack(side="left", padx=4)
        ttk.Button(top, text="Delete selected", command=self.delete_records).pack(side="left", padx=4)

        cols = ("id", "shot_date", "source_name", "weapon", "distance", "ammo", "n_groups", "n_shots",
                "width", "height", "diameter", "notes")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="extended")
        heads = ("id", "date", "photo", "weapon", "distance", "ammo", "groups", "shots",
                 "mean width", "mean height", "mean diameter", "notes")
        widths = (40, 90, 150, 110, 70, 100, 55, 50, 90, 90, 100, 240)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w" if c in ("notes", "source_name") else "center")
        self.tree.pack(fill="both", expand=True, padx=6)
        self.lbl_avg = ttk.Label(root, text="Select records and press 'Average of selection'. "
                                            "Averages are taken over the groups of the selected records.",
                                 font=("Consolas", 10), justify="left", padding=6)
        self.lbl_avg.pack(fill="x")

    # --------------------------------------------------------------- image
    def open_image(self):
        path = filedialog.askopenfilename(
            title="Open target photo",
            filetypes=[("Images", IMAGE_EXTS), ("JPEG", "*.jpg *.jpeg *.jpe"), ("PNG", "*.png"),
                       ("HEIC / HEIF", "*.heic *.heif"), ("All files", "*.*")])
        if not path:
            return
        self._load_image(path)

    def _load_image(self, path: str):
        img = load_image(path)
        if img is None:
            needs_heif = path.lower().endswith((".heic", ".heif")) and not HEIF_OK
            hint = "\nHEIC support needs the pillow-heif package (run setup.ps1)." if needs_heif else ""
            messagebox.showerror("Open image", f"Cannot read {path}{hint}")
            return
        self.image_path = path
        self.source_name = Path(path).name
        self.image_bgr = img
        self.pil_image = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        self.zoom = 1.0
        self._view_size = None
        self.scale_points.clear()
        self.scale_line_px = None
        self.px_per_mm = None
        self.impacts = []
        self.groups = []
        self.cur_group = None
        self.disc = None
        self.set_mode(None)
        self.lbl_scale.config(text="scale: not set", foreground="red")
        self.lbl_status.config(text=f"Loaded {Path(path).name} ({img.shape[1]}x{img.shape[0]} px)")
        self.update_stats()
        self.redraw()

    # ---------------------------------------------------------- coordinates
    def _to_view(self, x, y):
        return x * self.view_scale + self.view_off[0], y * self.view_scale + self.view_off[1]

    def _to_image(self, vx, vy):
        return (vx - self.view_off[0]) / self.view_scale, (vy - self.view_off[1]) / self.view_scale

    # ------------------------------------------------------------ zoom / pan
    def _canvas_wh(self) -> tuple[int, int]:
        return max(self.canvas.winfo_width(), 10), max(self.canvas.winfo_height(), 10)

    def _clamp_view(self):
        """Centre the image on an axis where it is smaller than the canvas; elsewhere
        keep the canvas covered (no panning past the image edge)."""
        cw, ch = self._canvas_wh()
        iw, ih = self.pil_image.size

        def axis(off: float, shown: float, size: int) -> float:
            if shown <= size:
                return (size - shown) / 2
            return min(0.0, max(size - shown, off))

        ox, oy = self.view_off
        self.view_off = (axis(ox, iw * self.view_scale, cw), axis(oy, ih * self.view_scale, ch))

    def _update_view(self):
        """Recompute view_scale / view_off for the current canvas size and zoom.
        After a window resize the image point at the canvas centre stays there."""
        cw, ch = self._canvas_wh()
        iw, ih = self.pil_image.size
        scale = min(cw / iw, ch / ih) * self.zoom
        if self._view_size is None:
            self.view_off = (0.0, 0.0)
        elif self._view_size != (cw, ch):
            px, py = self._to_image(self._view_size[0] / 2, self._view_size[1] / 2)
            self.view_off = (cw / 2 - px * scale, ch / 2 - py * scale)
        self.view_scale = scale
        self._view_size = (cw, ch)
        self._clamp_view()

    def zoom_at(self, factor: float, vx: float | None = None, vy: float | None = None):
        """Multiply the zoom by factor, keeping the image point under (vx, vy)
        (canvas coordinates, default: canvas centre) in place."""
        if self.pil_image is None:
            return
        self._update_view()
        cw, ch = self._canvas_wh()
        if vx is None or vy is None:
            vx, vy = cw / 2, ch / 2
        zoom = min(ZOOM_MAX, max(1.0, self.zoom * factor))
        if zoom == self.zoom:
            return
        px, py = self._to_image(vx, vy)
        self.view_scale *= zoom / self.zoom
        self.zoom = zoom
        self.view_off = (vx - px * self.view_scale, vy - py * self.view_scale)
        self._clamp_view()
        self.redraw()

    def zoom_fit(self):
        if self.pil_image is None:
            return
        self.zoom = 1.0
        self.redraw()

    def zoom_to_box(self, x0: float, y0: float, x1: float, y1: float):
        """Zoom so that the image rectangle fills the canvas, centred."""
        if self.pil_image is None:
            return
        cw, ch = self._canvas_wh()
        iw, ih = self.pil_image.size
        fit = min(cw / iw, ch / ih)
        want = min(cw / max(x1 - x0, 1.0), ch / max(y1 - y0, 1.0))
        self.zoom = min(ZOOM_MAX, max(1.0, want / fit))
        self.view_scale = fit * self.zoom
        self.view_off = (cw / 2 - (x0 + x1) / 2 * self.view_scale, ch / 2 - (y0 + y1) / 2 * self.view_scale)
        self._view_size = (cw, ch)
        self._clamp_view()
        self.redraw()

    def zoom_to_group(self):
        """Zoom on the selected group: its target circle, or the box of its shots."""
        gi = self.cur_group
        if self.pil_image is None or gi is None or gi >= len(self.groups):
            return
        g = self.groups[gi]
        if g.cx is not None:
            m = g.r * 1.1
            self.zoom_to_box(g.cx - m, g.cy - m, g.cx + m, g.cy + m)
            return
        pts = [(imp.x, imp.y) for imp in self.impacts if imp.group == gi]
        if not pts:
            return
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        m = 0.25 * max(max(xs) - min(xs), max(ys) - min(ys)) + (10 * self.px_per_mm if self.px_per_mm else 50)
        self.zoom_to_box(min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)

    def _ctrl_zoom(self, event, factor: float):
        self.canvas.focus_set()
        self._pan_anchor = None
        self.zoom_at(factor, event.x, event.y)
        return "break"

    def _on_zoom_slider(self, value):
        """Slider moved by the user: zoom about the canvas centre."""
        if self.pil_image is None:
            self.var_zoom.set(0.0)
            return
        target = exp(float(value))
        if abs(target / self.zoom - 1.0) > 1e-3:
            self.zoom_at(target / self.zoom)

    def _on_wheel(self, event):
        if event.delta:
            self.zoom_at(ZOOM_STEP if event.delta > 0 else 1 / ZOOM_STEP, event.x, event.y)

    def _pan_start(self, event):
        self.canvas.focus_set()  # so the +/-/0 keys reach the canvas
        if self.pil_image is None:
            return
        self._pan_anchor = (event.x, event.y, *self.view_off)
        self.canvas.config(cursor="fleur")

    def _pan_move(self, event):
        if self._pan_anchor is None or self.pil_image is None:
            return
        x, y, ox, oy = self._pan_anchor
        before = self.view_off
        self.view_off = (ox + event.x - x, oy + event.y - y)
        self._clamp_view()
        if self.view_off != before:
            self.redraw()

    def _pan_end(self, _event=None):
        if self._pan_anchor is not None:
            self._pan_anchor = None
            self.canvas.config(cursor="crosshair")

    def redraw(self):
        self.canvas.delete("all")
        if self.pil_image is None:
            self.lbl_zoom.config(text="Zoom: -")
            return
        self._update_view()
        cw, ch = self._canvas_wh()
        iw, ih = self.pil_image.size
        s = self.view_scale
        ox, oy = self.view_off
        # resample only the visible part of the photo: cost stays bounded at any zoom
        x0, y0 = max(0, floor(-ox / s)), max(0, floor(-oy / s))
        x1, y1 = min(iw, ceil((cw - ox) / s)), min(ih, ceil((ch - oy) / s))
        if x1 > x0 and y1 > y0:
            sx, sy = round(ox + x0 * s), round(oy + y0 * s)
            w, h = max(1, round(ox + x1 * s) - sx), max(1, round(oy + y1 * s) - sy)
            resample = Image.NEAREST if s >= 4 else Image.BILINEAR  # crisp pixels when far in
            self.tk_image = ImageTk.PhotoImage(
                self.pil_image.resize((w, h), resample, box=(x0, y0, x1, y1)))
            self.canvas.create_image(sx, sy, anchor="nw", image=self.tk_image)
        self.lbl_zoom.config(text="Zoom: fit" if self.zoom == 1.0 else f"Zoom: ×{self.zoom:.1f}")
        self.var_zoom.set(log(self.zoom))  # setting the variable does not fire the slider command

        if self.disc is not None and not any(g.cx is not None for g in self.groups):
            x, y = self._to_view(self.disc.cx, self.disc.cy)
            r = self.disc.r * self.view_scale
            self.canvas.create_oval(x - r, y - r, x + r, y + r, outline="#00ff00", width=1, dash=(4, 4))

        for p in self.scale_points:
            x, y = self._to_view(*p)
            self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, outline="yellow", width=2)
        if len(self.scale_points) == 2:
            (x1, y1), (x2, y2) = (self._to_view(*p) for p in self.scale_points)
            self.canvas.create_line(x1, y1, x2, y2, fill="yellow", width=2)

        members = self._members()
        order = [gi for gi in range(len(self.groups)) if gi != self.cur_group]
        if self.cur_group is not None and self.cur_group < len(self.groups):
            order.append(self.cur_group)  # the selected group is drawn last, on top
        for gi in order:
            g = self.groups[gi]
            circle = None
            if g.cx is not None:  # printed target circle the group belongs to
                x, y = self._to_view(g.cx, g.cy)
                circle = (x, y, g.r * self.view_scale)
            pts = [(*self._to_view(self.impacts[i].x, self.impacts[i].y), self.impacts[i].manual)
                   for i in members.get(gi, [])]
            self._draw_group_overlay(self.canvas, gi, g.label, circle, pts, gi == self.cur_group,
                                     self.style.impact_radius(self.zoom))

    def _line(self, key: str, gi: int) -> tuple[str, int, dict]:
        """Colour, width and extra canvas options (dash) of a drawing element."""
        ls = self.style.lines[key]
        colour = group_colour(gi) if ls.colour == GROUP_COLOUR else ls.colour
        dash = ls.dash()
        return colour, ls.width, ({"dash": dash} if dash else {})

    def _draw_group_overlay(self, cv: tk.Canvas, gi: int, label: str,
                            circle: tuple[float, float, float] | None,
                            pts: list[tuple[float, float, bool]], selected: bool, r: float):
        """Draw one group on a canvas, in canvas coordinates: target circle and label,
        impacts (radius r) numbered within the group, box around the impacts and centre
        cross. Used for the photo and for the preview of the Parameters tab."""
        col, w, kw = self._line("selected_circle" if selected else "group_circle", gi)
        bcol, bw, bkw = self._line("selected_box" if selected else "group_box", gi)
        font = ("", 11 if selected else 10, "bold")
        if circle is not None:
            x, y, rr = circle
            cv.create_oval(x - rr, y - rr, x + rr, y + rr, outline=col, width=w, **kw)
            cv.create_text(x - rr + 4, y - rr + 4, text=label, fill=col, anchor="nw", font=font)
        if len(pts) > 1:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            cv.create_rectangle(min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r,
                                outline=bcol, width=bw, **bkw)
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            cv.create_line(cx - 8, cy, cx + 8, cy, fill=bcol, width=max(2, bw))
            cv.create_line(cx, cy - 8, cx, cy + 8, fill=bcol, width=max(2, bw))
            if circle is None:
                cv.create_text(min(xs) - r, min(ys) - r - 4, text=label, fill=bcol, anchor="sw", font=font)
        elif len(pts) == 1 and circle is None:
            cv.create_text(pts[0][0] + r + 2, pts[0][1], text=label, fill=col, anchor="w", font=font)
        for k, (x, y, manual) in enumerate(pts, 1):
            icol, iw, ikw = self._line("impact_manual" if manual else "impact", gi)
            cv.create_oval(x - r, y - r, x + r, y + r, outline=icol, width=iw, **ikw)
            cv.create_text(x, y - r - 7, text=str(k), fill=icol, font=("", 9, "bold"))

    # ------------------------------------------------------------ parameters
    def _build_params(self):
        root = self.tab_params
        frm = ttk.Frame(root, padding=12)
        frm.pack(side="left", fill="y", anchor="n")
        ttk.Label(frm, text="Display of impacts and groups", font=("", 11, "bold")).grid(
            row=0, column=0, columnspan=5, sticky="w", pady=(0, 8))
        for c, txt in enumerate(("Element", "Colour", "Group colour", "Line style", "Width (px)")):
            ttk.Label(frm, text=txt, font=("", 9, "bold")).grid(row=1, column=c, sticky="w", padx=4)
        self.style_widgets: dict[str, dict] = {}
        for row, (key, text) in enumerate(ELEMENTS, start=2):
            ls = self.style.lines[key]
            ttk.Label(frm, text=text).grid(row=row, column=0, sticky="w", padx=4, pady=3)
            swatch = tk.Button(frm, width=7, relief="groove", command=lambda k=key: self._pick_colour(k))
            swatch.grid(row=row, column=1, padx=4)
            v_group = tk.BooleanVar(value=ls.colour == GROUP_COLOUR)
            ttk.Checkbutton(frm, variable=v_group, command=self._style_from_widgets).grid(row=row, column=2)
            v_style = tk.StringVar(value=ls.style)
            cb = ttk.Combobox(frm, textvariable=v_style, values=list(LINE_STYLES), state="readonly", width=10)
            cb.grid(row=row, column=3, padx=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self._style_from_widgets())
            v_width = tk.StringVar(value=str(ls.width))
            sp = ttk.Spinbox(frm, from_=WIDTH_RANGE[0], to=WIDTH_RANGE[1], textvariable=v_width, width=5,
                             command=self._style_from_widgets)
            sp.grid(row=row, column=4, padx=4)
            sp.bind("<KeyRelease>", lambda e: self._style_from_widgets())
            self.style_widgets[key] = {
                "swatch": swatch, "group": v_group, "style": v_style, "width": v_width,
                # colour kept for when "Group colour" is unticked again
                "custom": ls.colour if ls.colour != GROUP_COLOUR else "#00c0ff",
            }
        self._swatch_bg = swatch.cget("bg")
        row = len(ELEMENTS) + 2
        ttk.Label(frm, text="Impact circle radius (px)").grid(row=row, column=0, sticky="w", padx=4, pady=(10, 3))
        self.var_impact_size = tk.StringVar(value=str(self.style.impact_size))
        sp = ttk.Spinbox(frm, from_=IMPACT_SIZE_RANGE[0], to=IMPACT_SIZE_RANGE[1],
                         textvariable=self.var_impact_size, width=5, command=self._style_from_widgets)
        sp.grid(row=row, column=4, padx=4, pady=(10, 3))
        sp.bind("<KeyRelease>", lambda e: self._style_from_widgets())
        self.var_scale_zoom = tk.BooleanVar(value=self.style.scale_with_zoom)
        ttk.Checkbutton(frm, text="Impact circles grow with the zoom (same size on the photo)",
                        variable=self.var_scale_zoom, command=self._style_from_widgets).grid(
            row=row + 1, column=0, columnspan=5, sticky="w", padx=4, pady=3)
        ttk.Button(frm, text="Reset to defaults", command=self._reset_style).grid(
            row=row + 2, column=0, sticky="w", padx=4, pady=12)
        ttk.Label(frm, foreground="#666", wraplength=520, justify="left",
                  text="Changes apply at once to the photo and are saved in the database. "
                       "'Group colour' uses each group's own colour from the palette instead of "
                       "the chosen one. The radius is in screen pixels with the whole photo shown "
                       "(zoom 'fit'); line widths always stay in screen pixels.").grid(
            row=row + 3, column=0, columnspan=5, sticky="w", padx=4)
        for key in self.style_widgets:
            self._update_swatch(key)

        side = ttk.Frame(root, padding=12)
        side.pack(side="left", fill="both", expand=True, anchor="n")
        ttk.Label(side, text="Preview", font=("", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.preview = tk.Canvas(side, width=520, height=330, bg="#d8d2c4", highlightthickness=1,
                                 highlightbackground="#999")
        self.preview.pack(anchor="w")
        self._draw_preview()

    def _update_swatch(self, key: str):
        w = self.style_widgets[key]
        if w["group"].get():
            w["swatch"].config(bg=self._swatch_bg, activebackground=self._swatch_bg, text="group",
                               state="disabled")
        else:
            w["swatch"].config(bg=w["custom"], activebackground=w["custom"], text="", state="normal")

    def _pick_colour(self, key: str):
        w = self.style_widgets[key]
        _, colour = colorchooser.askcolor(color=w["custom"], parent=self,
                                          title=dict(ELEMENTS)[key] + ": colour")
        if colour:
            w["custom"] = colour.lower()
            w["group"].set(False)
            self._style_from_widgets()

    def _style_from_widgets(self):
        """Read the Parameters tab into self.style, save it, redraw."""
        for key, w in self.style_widgets.items():
            ls = self.style.lines[key]
            ls.colour = GROUP_COLOUR if w["group"].get() else w["custom"]
            if w["style"].get() in LINE_STYLES:
                ls.style = w["style"].get()
            try:  # ignore a half-typed value; keep the previous one
                ls.width = min(WIDTH_RANGE[1], max(WIDTH_RANGE[0], int(w["width"].get())))
            except ValueError:
                pass
            self._update_swatch(key)
        try:
            self.style.impact_size = min(IMPACT_SIZE_RANGE[1], max(IMPACT_SIZE_RANGE[0],
                                                                   int(self.var_impact_size.get())))
        except ValueError:
            pass
        self.style.scale_with_zoom = bool(self.var_scale_zoom.get())
        self.db.set_setting(STYLE_SETTING, self.style.to_json())
        self.redraw()
        self._draw_preview()

    def _reset_style(self):
        self.style = DrawStyle()
        for key, w in self.style_widgets.items():
            ls = self.style.lines[key]
            w["group"].set(ls.colour == GROUP_COLOUR)
            if ls.colour != GROUP_COLOUR:
                w["custom"] = ls.colour
            w["style"].set(ls.style)
            w["width"].set(str(ls.width))
        self.var_impact_size.set(str(self.style.impact_size))
        self.var_scale_zoom.set(self.style.scale_with_zoom)
        self._style_from_widgets()

    def _draw_preview(self):
        """Two sample groups on a paper-coloured background: one normal, one selected."""
        cv = self.preview
        cv.delete("all")
        for cx, gi, label, selected, shots in (
                (135, 0, "G1", False, ((-30, -20, False), (10, -35, False), (25, 15, False), (-12, 30, True))),
                (385, 1, "G2", True, ((-25, -30, False), (20, -10, False), (-5, 30, True)))):
            cy = 160
            for rr in (110, 85, 60):  # a few printed rings for context
                cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline="#333", width=12 if rr == 110 else 1)
            pts = [(cx + dx, cy + dy, manual) for dx, dy, manual in shots]
            self._draw_group_overlay(cv, gi, label, (cx, cy, 118), pts, selected, self.style.impact_size)
        cv.create_text(10, 322, anchor="sw", fill="#444",
                       text="Left: a group. Right: the selected group. Impact 4 (left) and 3 (right) "
                            "were added by hand.")

    # ---------------------------------------------------------------- groups
    def _members(self) -> dict[int, list[int]]:
        """Group index -> indexes of its impacts (in self.impacts order)."""
        out: dict[int, list[int]] = {}
        for i, imp in enumerate(self.impacts):
            out.setdefault(imp.group, []).append(i)
        return out

    def _ensure_group(self) -> int:
        """Return the selected group index, creating "G1" when there is none."""
        if not self.groups:
            self.groups = [Group("G1")]
            self.cur_group = 0
            self.update_stats()
        if self.cur_group is None or self.cur_group >= len(self.groups):
            self.cur_group = 0
        return self.cur_group

    def _unique_label(self, base: str) -> str:
        labels = {g.label for g in self.groups}
        if base not in labels:
            return base
        n = 2
        while f"{base} ({n})" in labels:
            n += 1
        return f"{base} ({n})"

    def _select_group(self, index: int | None):
        self.cur_group = index
        if index is not None and self.gtree.exists(str(index)):
            self.gtree.selection_set(str(index))
            self.gtree.see(str(index))
        else:
            self.gtree.selection_remove(*self.gtree.selection())
        self._show_selected_stats()
        self.redraw()

    def _on_group_select(self, _event=None):
        sel = self.gtree.selection()
        if sel:
            idx = int(sel[0])
            if idx != self.cur_group:
                self.cur_group = idx
                self._show_selected_stats()
                self.redraw()

    def new_group(self):
        label = simpledialog.askstring("New group", "Group name:",
                                       initialvalue=self._unique_label(f"G{len(self.groups) + 1}"), parent=self)
        if not label:
            return
        self.groups.append(Group(self._unique_label(label.strip())))
        self.update_stats()
        self._select_group(len(self.groups) - 1)
        self.set_mode("add")

    def rename_group(self):
        if self.cur_group is None or self.cur_group >= len(self.groups):
            messagebox.showinfo("Rename group", "Select a group first.")
            return
        g = self.groups[self.cur_group]
        label = simpledialog.askstring("Rename group", "Group name:", initialvalue=g.label, parent=self)
        if not label or label.strip() == g.label:
            return
        g.label = self._unique_label(label.strip())
        self.update_stats()
        self._select_group(self.cur_group)

    def delete_group(self):
        if self.cur_group is None or self.cur_group >= len(self.groups):
            messagebox.showinfo("Delete group", "Select a group first.")
            return
        gi = self.cur_group
        n = sum(1 for imp in self.impacts if imp.group == gi)
        msg = f"Delete group '{self.groups[gi].label}'"
        msg += f" and its {n} impact(s)?" if n else "?"
        msg += "\n(Use 'Move impact to selected group' first to keep shots.)" if n else ""
        if not messagebox.askyesno("Delete group", msg):
            return
        self.impacts = [imp for imp in self.impacts if imp.group != gi]
        for imp in self.impacts:
            if imp.group > gi:
                imp.group -= 1
        del self.groups[gi]
        self.update_stats()
        self._select_group(min(gi, len(self.groups) - 1) if self.groups else None)

    def auto_group_circles(self):
        """One group per printed target circle; every impact goes to the nearest."""
        if self.image_bgr is None:
            messagebox.showinfo("Auto-group", "Open an image first.")
            return
        targets = find_targets(cv2.cvtColor(self.image_bgr, cv2.COLOR_BGR2GRAY))
        if len(targets) < 2:
            messagebox.showinfo("Auto-group", f"{len(targets)} target circle(s) found; need at least 2 to "
                                "group by target. Use 'Auto-group: distance' or create groups by hand.")
            return
        self.groups = assign_groups(self.impacts, targets)
        self.update_stats()
        self._select_group(0)
        self.lbl_status.config(text=f"{len(self.groups)} target circles found; impacts assigned to the nearest.")

    def auto_group_distance(self):
        """Cluster the impacts: shots closer than a threshold form one group."""
        if not self.impacts:
            messagebox.showinfo("Auto-group", "Detect or add impacts first.")
            return
        unit = self.var_unit.get()
        if self.px_per_mm:
            v = simpledialog.askfloat("Auto-group by distance",
                                      f"Shots closer than this belong to the same group ({unit}):",
                                      initialvalue=round(to_unit(DEFAULT_CLUSTER_MM, unit), 2),
                                      minvalue=0.001, parent=self)
            if v is None:
                return
            max_px = from_unit(v, unit) * self.px_per_mm
        else:
            v = simpledialog.askfloat("Auto-group by distance",
                                      "Shots closer than this belong to the same group (pixels):",
                                      initialvalue=150, minvalue=1, parent=self)
            if v is None:
                return
            max_px = v
        labels = cluster_points([(imp.x, imp.y) for imp in self.impacts], max_px)
        for imp, lab in zip(self.impacts, labels):
            imp.group = lab
        self.groups = [Group(f"G{i + 1}") for i in range(max(labels) + 1)]
        self.update_stats()
        self._select_group(0)
        self.lbl_status.config(text=f"{len(self.groups)} group(s) formed by distance.")

    # ----------------------------------------------------------------- modes
    def set_mode(self, mode: str | None):
        if mode in ("add", "assign"):
            self._ensure_group()
        self.mode = mode
        label = self.groups[self.cur_group].label if self.cur_group is not None and self.groups else "?"
        hints = {
            "scale": "Click 2 points on the image whose real distance you know.",
            "add": f"Click on the image to add an impact to group '{label}'. (Pick another tool to stop.)",
            "remove": "Click on an impact to remove it.",
            "assign": f"Click on an impact to move it to group '{label}'. Select another group in the list to change the destination.",
        }
        self.lbl_mode.config(text=hints.get(mode, ""))
        if mode == "scale":
            self.scale_points.clear()
            self.redraw()

    def _nearest_impact(self, x: float, y: float) -> int | None:
        if not self.impacts:
            return None
        i, imp = min(enumerate(self.impacts), key=lambda t: hypot(t[1].x - x, t[1].y - y))
        tolerance = max(CLICK_TOLERANCE_PX, self.style.impact_radius(self.zoom))  # click inside the circle
        return i if hypot(imp.x - x, imp.y - y) * self.view_scale < tolerance else None

    def on_click(self, event):
        self.canvas.focus_set()  # so the +/-/0 keys reach the canvas
        self._pan_anchor = None
        if self.pil_image is None:
            return
        if self.mode is None:  # no tool active: left-drag pans
            self._pan_start(event)
            return
        x, y = self._to_image(event.x, event.y)
        iw, ih = self.pil_image.size
        if not (0 <= x < iw and 0 <= y < ih):
            return
        if self.mode == "scale":
            self.scale_points.append((x, y))
            self.redraw()
            if len(self.scale_points) == 2:
                (x1, y1), (x2, y2) = self.scale_points
                px = hypot(x2 - x1, y2 - y1)
                dlg = ScaleDialog(self, px, self.var_unit.get())
                if dlg.result:
                    value, unit = dlg.result
                    self._set_scale(px / from_unit(value, unit), f"{value:g} {unit} = {px:.0f} px")
                    self.var_unit.set(unit)
                self.set_mode(None)
                self.redraw()
        elif self.mode == "add":
            gi = self._ensure_group()
            self.impacts.append(Impact(x, y, 1, 0.0, True, -1, gi))
            self.update_stats()
            self.redraw()
        elif self.mode == "remove":
            i = self._nearest_impact(x, y)
            if i is not None:
                self.impacts.pop(i)
                self.update_stats()
                self.redraw()
        elif self.mode == "assign":
            i = self._nearest_impact(x, y)
            if i is not None:
                self.impacts[i].group = self._ensure_group()
                self.update_stats()
                self.redraw()

    def _set_scale(self, px_per_mm: float, how: str):
        self.px_per_mm = px_per_mm
        self.lbl_scale.config(text=f"scale: {px_per_mm:.3f} px/mm\n({how})", foreground="green")
        self.update_stats()

    def auto_scale(self):
        if self.image_bgr is None:
            messagebox.showinfo("Scale", "Open an image first.")
            return
        disc = find_disc(cv2.cvtColor(self.image_bgr, cv2.COLOR_BGR2GRAY))
        if disc is None:
            messagebox.showwarning("Scale", "No black circle found; use the 2-point tool.")
            return
        unit = self.var_unit.get()
        default = to_unit(DEFAULT_DISC_MM, unit)
        v = simpledialog.askfloat("Auto scale", f"Outer diameter of the black circle ({unit}):",
                                  initialvalue=round(default, 2), minvalue=0.001, parent=self)
        if v is None:
            return
        self.disc = disc
        self._set_scale(2 * disc.r / from_unit(v, unit), f"disc {v:g} {unit} = {2 * disc.r:.0f} px")
        self.redraw()

    # ------------------------------------------------------------- analysis
    def analyze(self):
        if self.image_bgr is None:
            messagebox.showinfo("Analyse", "Open an image first.")
            return
        shots = self.var_shots.get().strip()
        shots_fired = int(shots) if shots.isdigit() else None
        hint = None
        if self.px_per_mm:
            try:
                hint = pi * (self.var_hole.get() * self.px_per_mm / 2) ** 2
            except tk.TclError:
                hint = None
        try:
            result = detect_impacts(self.image_bgr, paper_ratio=self.var_paper.get() / 100.0,
                                    sat_thresh=self.var_sat.get(), open_kernel=self.var_kernel.get(),
                                    min_area=self.var_minarea.get(), shots_fired=shots_fired,
                                    hole_area_hint=hint)
        except Exception as exc:  # parameters out of range, etc.
            messagebox.showerror("Analyse", str(exc))
            return
        self.disc = result.disc
        self.impacts = result.impacts
        self.groups = result.groups
        n_multi = len({i.blob_id for i in self.impacts if i.blob_id >= 0})
        msg = f"Detected {len(self.impacts)} impacts"
        if len(result.targets) >= 2:
            msg += f" on {len(result.targets)} target diagrams ({len(self.groups)} groups)"
        if n_multi:
            msg += f"; {n_multi} torn region(s) split by estimated count; check and correct"
        if result.disc is None:
            msg += ". No black circle found."
        if not self.px_per_mm:
            msg += " Tip: set the scale first so single holes and torn regions are told apart by size."
        self.lbl_status.config(text=msg)
        self.update_stats()
        self._select_group(0 if self.groups else None)

    def clear_impacts(self):
        self.impacts = []
        self.update_stats()
        self.redraw()

    def update_stats(self):
        """Recompute per-group and image-level statistics and refresh the panel."""
        unit = self.var_unit.get()
        members = self._members()
        self.group_stats = {}
        if self.px_per_mm:
            for gi in range(len(self.groups)):
                pts = [(self.impacts[i].x, self.impacts[i].y) for i in members.get(gi, [])]
                st = group_stats(pts, self.px_per_mm)
                if st is not None:
                    self.group_stats[gi] = st
        self.summary = summarize_groups(list(self.group_stats.values()))

        selected = self.gtree.selection()
        self.gtree.delete(*self.gtree.get_children())
        for gi, g in enumerate(self.groups):
            n = len(members.get(gi, []))
            st = self.group_stats.get(gi)
            fmt = (lambda v: f"{to_unit(v, unit):.2f}") if st else (lambda v: "-")
            self.gtree.insert("", "end", iid=str(gi), tags=("empty",) if n == 0 else (),
                              values=(g.label, n, fmt(st.width_mm) if st else "-",
                                      fmt(st.height_mm) if st else "-", fmt(st.diameter_mm) if st else "-"))
        if selected and self.gtree.exists(selected[0]):
            self.gtree.selection_set(selected[0])
        self._show_selected_stats()

        s = self.summary
        if s["count"]:
            self.lbl_summary["groups"].config(text=f"{s['count']} / {s['total_shots']}")
            for key in ("width", "height", "diameter"):
                m, sd = s[key + "_mm"]
                self.lbl_summary[key].config(text=f"{to_unit(m, unit):.2f} ± {to_unit(sd, unit):.2f} {unit}")
            self.lbl_summary["p90"].config(text=f"{to_unit(s['diameter_p90_mm'], unit):.2f} {unit}")
        else:
            n_groups = len(self.groups)
            self.lbl_summary["groups"].config(text=f"{n_groups} / {len(self.impacts)}" if n_groups else "-")
            for key in ("width", "height", "diameter", "p90"):
                self.lbl_summary[key].config(text="- (set scale)" if self.impacts and not self.px_per_mm else "-")

    def _show_selected_stats(self):
        unit = self.var_unit.get()
        gi = self.cur_group
        if gi is None or gi >= len(self.groups):
            for key in self.lbl_results:
                self.lbl_results[key].config(text="-")
            return
        n = sum(1 for imp in self.impacts if imp.group == gi)
        self.lbl_results["n"].config(text=f"{n}  ({self.groups[gi].label})")
        st = self.group_stats.get(gi)
        for key in ("width", "height", "diameter"):
            if st is None:
                self.lbl_results[key].config(text="- (set scale)" if n and not self.px_per_mm else "-")
            else:
                self.lbl_results[key].config(text=f"{to_unit(getattr(st, key + '_mm'), unit):.2f} {unit}")

    # ----------------------------------------------------------- persistence
    def save_record(self):
        if not self.px_per_mm or not self.group_stats:
            messagebox.showinfo("Save", "Set the scale and detect at least one impact first.")
            return
        f = {k: e.get().strip() for k, e in self.form.items()}
        earlier = self.db.sessions_with_source(self.source_name)
        if earlier and not messagebox.askyesno(
                "Save", f"Record(s) {', '.join('#' + str(i) for i in earlier)} were already saved "
                        f"from a photo named '{self.source_name}'.\n\nSave another record? It gets "
                        "its own number and its own photo copy, nothing is overwritten."):
            return
        # archive a downscaled copy next to the database; coordinates and scale
        # are stored in the copy's pixels so "Load record" lines up exactly
        k = stored_factor(self.image_bgr) if self.image_bgr is not None else 1.0
        image_bgr = self.image_bgr
        store = None
        if image_bgr is not None:
            def store(sid: int) -> str:  # called by the db with the new record id
                return store_image(image_bgr, self.db.path, self.source_name, sid)[0]
        members = self._members()
        records = []
        for gi, g in enumerate(self.groups):
            st = self.group_stats.get(gi)
            if st is None:
                continue  # empty group: nothing to measure, not stored
            pts = [(self.impacts[i].x * k, self.impacts[i].y * k, self.impacts[i].manual) for i in members[gi]]
            records.append(GroupRecord(g.label, st, pts,
                                       g.cx * k if g.cx is not None else None,
                                       g.cy * k if g.cy is not None else None,
                                       g.r * k if g.r is not None else None))
        try:
            sid = self.db.save_session(records, self.px_per_mm * k, self.var_unit.get(),
                                       source_name=self.source_name, store=store, **f)
        except OSError as exc:
            messagebox.showerror("Save", f"Could not store the image copy; nothing was saved:\n{exc}")
            return
        rel_path = self.db.get_session(sid)["image_path"] or "none"
        skipped = len(self.groups) - len(records)
        msg = f"Saved record #{sid}: {len(records)} group(s), {self.summary['total_shots']} shots, image copy {rel_path}."
        if skipped:
            msg += f" {skipped} empty group(s) not stored."
        self.lbl_status.config(text=msg)

    def _refresh_records(self):
        weapons = self.db.weapons()
        self.cb_filter["values"] = [""] + weapons
        self.tree.delete(*self.tree.get_children())
        unit = self.var_unit.get()
        for r in self.db.list_sessions(self.var_filter.get() or None):
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["id"], r["shot_date"], r["source_name"] or "", r["weapon"], r["distance"], r["ammo"],
                r["n_groups"], r["n_shots"],
                f"{to_unit(r['width_mm'], unit):.2f} {unit}", f"{to_unit(r['height_mm'], unit):.2f} {unit}",
                f"{to_unit(r['diameter_mm'], unit):.2f} {unit}", r["notes"]))

    def _selected_ids(self, all_rows=False) -> list[int]:
        items = self.tree.get_children() if all_rows else self.tree.selection()
        return [int(i) for i in items]

    def compute_average(self, all_rows=False):
        ids = self._selected_ids(all_rows)
        if not ids:
            self.lbl_avg.config(text="No records selected.")
            return
        agg = self.db.aggregate(ids)
        lines = [f"{agg['sessions']} record(s), {agg['count']} group(s), {agg['total_shots']} shots. "
                 f"Mean ± sample std-dev (σ, n-1) over the groups:",
                 f"  {'':<14} {'cm':^20}  {'inch':^20}"]
        for key, txt in (("width_mm", "Width"), ("height_mm", "Height"),
                         ("diameter_mm", "Diameter")):
            m, s = agg[key]
            cols = [f"{to_unit(m, u):7.2f} ± {to_unit(s, u):5.2f} {u:<4}" for u in UNITS]
            lines.append(f"  {txt:<14} " + "  ".join(cols))
        p90 = agg["diameter_p90_mm"]
        cols = [f"{to_unit(p90, u):7.2f}          {u:<4}" for u in UNITS]
        lines.append(f"  {'Diameter P90':<14} " + "  ".join(cols)
                     + "   (90 % of the groups are at or below)")
        m, s = agg["n_shots"]
        lines.append(f"  {'Shots / group':<14} {m:7.1f} ± {s:5.1f}")
        self.lbl_avg.config(text="\n".join(lines))

    def load_record(self):
        ids = self._selected_ids()
        if len(ids) != 1:
            messagebox.showinfo("Load", "Select exactly one record.")
            return
        s = self.db.get_session(ids[0])
        img_path = resolve_image_path(s["image_path"], self.db.path)
        if img_path is not None:
            self._load_image(str(img_path))
        else:
            messagebox.showwarning("Load", "Image file not found; showing data only.")
            self.image_bgr = None
            self.pil_image = None
        # keep the original photo name, not the name of the stored copy
        self.source_name = s["source_name"] or source_name_of(s["image_path"] or "")
        self.var_unit.set(s["unit"])
        self._set_scale(s["px_per_mm"], f"from record #{s['id']}")
        rows = self.db.get_groups(ids[0])
        self.groups = [Group(r["label"], r["cx_px"], r["cy_px"], r["r_px"]) for r in rows]
        index_of = {r["id"]: i for i, r in enumerate(rows)}
        if not self.groups:
            self.groups = [Group("G1")]
        self.impacts = [Impact(r["x_px"], r["y_px"], 1, 0.0, bool(r["manual"]), -1,
                               index_of.get(r["group_id"], 0)) for r in self.db.get_impacts(ids[0])]
        for k, e in self.form.items():
            e.delete(0, "end")
            e.insert(0, s[k] or "")
        self.update_stats()
        self._select_group(0)
        self.nb.select(self.tab_analyze)

    def delete_records(self):
        ids = self._selected_ids()
        if not ids:
            return
        if not messagebox.askyesno("Delete", f"Delete {len(ids)} record(s)? This cannot be undone."):
            return
        removed = sum(1 for sid in ids if self.db.delete_session(sid))
        self._refresh_records()
        self.lbl_avg.config(text=f"Deleted {len(ids)} record(s) and {removed} stored image(s).")

    def _on_close(self):
        self.db.close()
        self.destroy()
