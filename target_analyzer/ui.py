"""Tkinter user interface for the target analyser."""
from __future__ import annotations

import tkinter as tk
from datetime import date
from math import hypot
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

from .db import Database
from .detection import Impact, detect_impacts, find_disc
from .images import resolve_image_path, store_image
from math import pi

from .stats import from_unit, group_stats, to_unit

UNITS = ("cm", "inch")
DEFAULT_DISC_MM = 200.0  # black disc of a 25 m precision pistol target
IMAGE_EXTS = "*.jpg *.jpeg *.jpe *.png *.heic *.heif *.bmp *.tif *.tiff *.webp"


def load_image(path: str) -> np.ndarray | None:
    """Read any common photo format (incl. HEIC) as a BGR array, honouring EXIF rotation."""
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    except Exception:
        return None


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
        self.geometry("1400x900")
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
        self.disc = None
        self.stats = None

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
        self.canvas = tk.Canvas(root, bg="#333", cursor="crosshair")
        self.canvas.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(root, padding=6, width=300)
        right.pack(side="right", fill="y")

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
        ttk.Button(left, text="Clear all impacts", command=self.clear_impacts).pack(fill="x", pady=1)
        self.lbl_mode = ttk.Label(left, text="", foreground="blue", wraplength=170)
        self.lbl_mode.pack(anchor="w", pady=4)

        # --- right panel: results + save
        ttk.Label(right, text="Group size", font=("", 11, "bold")).pack(anchor="w")
        urow = ttk.Frame(right)
        urow.pack(fill="x")
        ttk.Label(urow, text="Display unit:").pack(side="left")
        self.var_unit = tk.StringVar(value="cm")
        cb = ttk.Combobox(urow, textvariable=self.var_unit, values=UNITS, width=6, state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.update_stats())
        self.lbl_results = {}
        res = ttk.Frame(right)
        res.pack(fill="x", pady=4)
        for r, (key, txt) in enumerate((("n", "Shots"), ("width", "Width"), ("height", "Height"),
                                        ("diameter", "Diameter (extreme spread)"))):
            ttk.Label(res, text=txt + ":").grid(row=r, column=0, sticky="w")
            lbl = ttk.Label(res, text="-", font=("", 10, "bold"))
            lbl.grid(row=r, column=1, sticky="w", padx=6)
            self.lbl_results[key] = lbl
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
            e = ttk.Entry(form, width=24)
            e.insert(0, default)
            e.grid(row=r, column=1, pady=1, sticky="ew")
            self.form[key] = e
        ttk.Button(right, text="Save to database", command=self.save_record).pack(fill="x", pady=6)
        self.lbl_status = ttk.Label(right, text="", wraplength=280, foreground="green")
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

        cols = ("id", "shot_date", "weapon", "distance", "ammo", "n_shots", "width", "height", "diameter", "notes")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="extended")
        widths = (40, 90, 110, 70, 100, 50, 80, 80, 90, 300)
        for c, w in zip(cols, widths):
            self.tree.heading(c, text=c.replace("_", " "))
            self.tree.column(c, width=w, anchor="center" if c != "notes" else "w")
        self.tree.pack(fill="both", expand=True, padx=6)
        self.lbl_avg = ttk.Label(root, text="Select records and press 'Average of selection'.",
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
        self.disc = None
        self.stats = None
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

        if self.disc is not None:
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
        for i, imp in enumerate(self.impacts, 1):
            x, y = self._to_view(imp.x, imp.y)
            colour = "#ff8800" if imp.manual else "#ff2020"
            self.canvas.create_oval(x - r, y - r, x + r, y + r, outline=colour, width=2)
            self.canvas.create_text(x, y - r - 7, text=str(i), fill=colour, font=("", 9, "bold"))

        if self.stats is not None and len(self.impacts) > 1:
            xs = [imp.x for imp in self.impacts]
            ys = [imp.y for imp in self.impacts]
            x1, y1 = self._to_view(min(xs), min(ys))
            x2, y2 = self._to_view(max(xs), max(ys))
            self.canvas.create_rectangle(x1 - r, y1 - r, x2 + r, y2 + r, outline="cyan", width=1, dash=(6, 3))
            cx, cy = self._to_view(self.stats.center_x_px, self.stats.center_y_px)
            self.canvas.create_line(cx - 8, cy, cx + 8, cy, fill="cyan", width=2)
            self.canvas.create_line(cx, cy - 8, cx, cy + 8, fill="cyan", width=2)

    # ----------------------------------------------------------------- modes
    def set_mode(self, mode: str | None):
        self.mode = mode
        hints = {
            "scale": "Click 2 points on the image whose real distance you know.",
            "add": "Click on the image to add an impact. (Pick another tool to stop.)",
            "remove": "Click on an impact to remove it.",
        }
        self.lbl_mode.config(text=hints.get(mode, ""))
        if mode == "scale":
            self.scale_points.clear()
            self.redraw()

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
                self.scale_points = self.scale_points  # keep the yellow line visible
                self.redraw()
        elif self.mode == "add":
            self.impacts.append(Impact(x, y, 1, 0.0, True))
            self.update_stats()
            self.redraw()
        elif self.mode == "remove":
            if self.impacts:
                i, imp = min(enumerate(self.impacts), key=lambda t: hypot(t[1].x - x, t[1].y - y))
                if hypot(imp.x - x, imp.y - y) * self.view_scale < 25:
                    self.impacts.pop(i)
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
        n_multi = len({i.group_id for i in self.impacts if i.group_id >= 0})
        msg = f"Detected {len(self.impacts)} impacts"
        if n_multi:
            msg += f" ({n_multi} torn region(s) split by estimated count; check and correct)"
        if result.disc is None:
            msg += ". No black circle found."
        if not self.px_per_mm:
            msg += " Tip: set the scale first so single holes and torn regions are told apart by size."
        self.lbl_status.config(text=msg)
        self.update_stats()
        self.redraw()

    def clear_impacts(self):
        self.impacts = []
        self.update_stats()
        self.redraw()

    def update_stats(self):
        unit = self.var_unit.get()
        self.lbl_results["n"].config(text=str(len(self.impacts)))
        pts = [(i.x, i.y) for i in self.impacts]
        self.stats = group_stats(pts, self.px_per_mm) if self.px_per_mm else None
        for key in ("width", "height", "diameter"):
            if self.stats is None:
                self.lbl_results[key].config(text="- (set scale)" if pts else "-")
            else:
                self.lbl_results[key].config(
                    text=f"{to_unit(getattr(self.stats, key + '_mm'), unit):.2f} {unit}")

    # ----------------------------------------------------------- persistence
    def save_record(self):
        if self.stats is None:
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
        sid = self.db.save_session(
            self.stats, self.px_per_mm * k, self.var_unit.get(),
            [(i.x * k, i.y * k, i.manual) for i in self.impacts],
            image_path=rel_path, **f)
        self.lbl_status.config(text=f"Saved record #{sid} ({self.stats.n} shots), image copy {rel_path}.")

    def _refresh_records(self):
        weapons = self.db.weapons()
        self.cb_filter["values"] = [""] + weapons
        self.tree.delete(*self.tree.get_children())
        unit = self.var_unit.get()
        for r in self.db.list_sessions(self.var_filter.get() or None):
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["id"], r["shot_date"], r["weapon"], r["distance"], r["ammo"], r["n_shots"],
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
        lines = [f"{agg['count']} record(s), mean ± sample std-dev (σ, n-1):",
                 f"  {'':<14} {'cm':^20}  {'inch':^20}"]
        for key, txt in (("width_mm", "Width"), ("height_mm", "Height"),
                         ("diameter_mm", "Diameter")):
            m, s = agg[key]
            cols = [f"{to_unit(m, u):7.2f} ± {to_unit(s, u):5.2f} {u:<4}" for u in UNITS]
            lines.append(f"  {txt:<14} " + "  ".join(cols))
        p90 = agg["diameter_p90_mm"]
        cols = [f"{to_unit(p90, u):7.2f}          {u:<4}" for u in UNITS]
        lines.append(f"  {'Diameter P90':<14} " + "  ".join(cols)
                     + "   (90 % of the records are at or below)")
        m, s = agg["n_shots"]
        lines.append(f"  {'Shots':<14} {m:7.1f} ± {s:5.1f}")
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
        self.impacts = [Impact(r["x_px"], r["y_px"], 1, 0.0, bool(r["manual"])) for r in self.db.get_impacts(ids[0])]
        for k, e in self.form.items():
            e.delete(0, "end")
            e.insert(0, s[k] or "")
        self.update_stats()
        self.redraw()
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
