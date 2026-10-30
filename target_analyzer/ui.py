"""Tkinter user interface for the target analyser."""
from __future__ import annotations

import tkinter as tk
from datetime import date
from math import hypot, pi
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

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
from .images import resolve_image_path, store_image
from .stats import GroupStats, cluster_points, from_unit, group_stats, summarize_groups, to_unit

UNITS = ("cm", "inch")
DEFAULT_DISC_MM = 200.0  # black disc of a 25 m precision pistol target
DEFAULT_CLUSTER_MM = 50.0  # "Auto-group by distance": shots closer than this belong together
IMAGE_EXTS = "*.jpg *.jpeg *.jpe *.png *.heic *.heif *.bmp *.tif *.tiff *.webp"
GROUP_COLOURS = ("#ff2020", "#20a0ff", "#30d030", "#ffb000", "#e040e0", "#00d0d0",
                 "#ff70a0", "#a0ff40", "#ff8040", "#8080ff", "#c0a000", "#40e0a0")
CLICK_TOLERANCE_PX = 25  # screen pixels for the Remove / Assign tools


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

        # analysis state
        self.image_path: str | None = None
        self.image_bgr = None
        self.pil_image: Image.Image | None = None
        self.tk_image = None
        self.view_scale = 1.0
        self.view_off = (0, 0)
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
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.tab_analyze = ttk.Frame(self.nb)
        self.tab_records = ttk.Frame(self.nb)
        self.nb.add(self.tab_analyze, text="Analyse")
        self.nb.add(self.tab_records, text="Records & averages")
        self._build_analyze()
        self._build_records()
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._refresh_records())

    def _build_analyze(self):
        root = self.tab_analyze
        left = ttk.Frame(root, padding=6)
        left.pack(side="left", fill="y")
        right = ttk.Frame(root, padding=6, width=360)
        right.pack(side="right", fill="y")
        self.canvas = tk.Canvas(root, bg="#333", cursor="crosshair")
        self.canvas.pack(side="left", fill="both", expand=True)

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

        cols = ("id", "shot_date", "weapon", "distance", "ammo", "n_groups", "n_shots",
                "width", "height", "diameter", "notes")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="extended")
        heads = ("id", "date", "weapon", "distance", "ammo", "groups", "shots",
                 "mean width", "mean height", "mean diameter", "notes")
        widths = (40, 90, 110, 70, 100, 55, 50, 90, 90, 100, 300)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center" if c != "notes" else "w")
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
        self.image_bgr = img
        self.pil_image = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
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

    def redraw(self):
        self.canvas.delete("all")
        if self.pil_image is None:
            return
        cw, ch = max(self.canvas.winfo_width(), 10), max(self.canvas.winfo_height(), 10)
        iw, ih = self.pil_image.size
        self.view_scale = min(cw / iw, ch / ih)
        dw, dh = max(1, int(iw * self.view_scale)), max(1, int(ih * self.view_scale))
        self.view_off = ((cw - dw) // 2, (ch - dh) // 2)
        self.tk_image = ImageTk.PhotoImage(self.pil_image.resize((dw, dh), Image.BILINEAR))
        self.canvas.create_image(self.view_off, anchor="nw", image=self.tk_image)

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

        r = 10
        members = self._members()
        for gi, g in enumerate(self.groups):
            colour = group_colour(gi)
            selected = gi == self.cur_group
            idx = members.get(gi, [])
            if g.cx is not None:  # printed target circle the group belongs to
                x, y = self._to_view(g.cx, g.cy)
                rr = g.r * self.view_scale
                self.canvas.create_oval(x - rr, y - rr, x + rr, y + rr, outline=colour,
                                        width=3 if selected else 1, dash=None if selected else (4, 4))
                self.canvas.create_text(x - rr + 4, y - rr + 4, text=g.label, fill=colour, anchor="nw",
                                        font=("", 10, "bold"))
            for k, i in enumerate(idx, 1):
                imp = self.impacts[i]
                x, y = self._to_view(imp.x, imp.y)
                self.canvas.create_oval(x - r, y - r, x + r, y + r, outline=colour, width=2,
                                        dash=(3, 2) if imp.manual else None)
                self.canvas.create_text(x, y - r - 7, text=str(k), fill=colour, font=("", 9, "bold"))
            if len(idx) > 1:
                xs = [self.impacts[i].x for i in idx]
                ys = [self.impacts[i].y for i in idx]
                x1, y1 = self._to_view(min(xs), min(ys))
                x2, y2 = self._to_view(max(xs), max(ys))
                self.canvas.create_rectangle(x1 - r, y1 - r, x2 + r, y2 + r, outline=colour,
                                             width=2 if selected else 1, dash=(6, 3))
                cx, cy = self._to_view(sum(xs) / len(xs), sum(ys) / len(ys))
                self.canvas.create_line(cx - 8, cy, cx + 8, cy, fill=colour, width=2)
                self.canvas.create_line(cx, cy - 8, cx, cy + 8, fill=colour, width=2)
                if g.cx is None:
                    self.canvas.create_text(x1 - r, y1 - r - 4, text=g.label, fill=colour, anchor="sw",
                                            font=("", 10, "bold"))
            elif len(idx) == 1 and g.cx is None:
                x, y = self._to_view(self.impacts[idx[0]].x, self.impacts[idx[0]].y)
                self.canvas.create_text(x + r + 2, y, text=g.label, fill=colour, anchor="w", font=("", 10, "bold"))

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
        return i if hypot(imp.x - x, imp.y - y) * self.view_scale < CLICK_TOLERANCE_PX else None

    def on_click(self, event):
        if self.pil_image is None:
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
        # archive a downscaled copy next to the database; coordinates and scale
        # are stored in the copy's pixels so "Load record" lines up exactly
        rel_path, k = "", 1.0
        if self.image_bgr is not None:
            try:
                rel_path, k = store_image(self.image_bgr, self.db.path, self.image_path or "")
            except OSError as exc:
                messagebox.showerror("Save", f"Could not store the image copy:\n{exc}")
                return
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
        sid = self.db.save_session(records, self.px_per_mm * k, self.var_unit.get(), image_path=rel_path, **f)
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
                r["id"], r["shot_date"], r["weapon"], r["distance"], r["ammo"], r["n_groups"], r["n_shots"],
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
