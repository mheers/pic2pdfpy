#!/usr/bin/env python3
"""
doc2pdf_gui.py  –  Document to DIN A4 PDF converter
Drop or open an image, click the four document corners, export to PDF.

Requires Python 3.10+.

Drag-and-drop requires tkinterdnd2:
    pip install tkinterdnd2
Without it the app still works fine via the "Open Image" button.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageOps, ImageTk
import cv2
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
import os
import tempfile
import threading
from urllib.request import url2pathname
from urllib.parse import urlparse

# ── Try to load optional DnD support ──────────────────────────────────────────
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

# ── Palette ───────────────────────────────────────────────────────────────────
C = {
    # Sidebar / shell
    "sidebar":        "#1E1E2E",
    "sidebar_border": "#313244",
    "sidebar_text":   "#CDD6F4",
    "sidebar_muted":  "#6C7086",
    "sidebar_hover":  "#313244",

    # Main area
    "bg":             "#181825",
    "surface":        "#1E1E2E",
    "surface2":       "#27273A",
    "border":         "#45475A",

    # Canvas / drop zone
    "canvas_bg":      "#11111B",
    "dropzone_idle":  "#1E1E2E",
    "dropzone_hover": "#2A2A3E",
    "dropzone_text":  "#585B70",
    "dropzone_icon":  "#45475A",

    # Accent
    "accent":         "#89B4FA",   # Catppuccin blue
    "accent_dim":     "#4A6FA5",
    "accent_hover":   "#B4D0FF",
    "green":          "#A6E3A1",
    "red":            "#F38BA8",
    "yellow":         "#F9E2AF",
    "peach":          "#FAB387",

    # Text
    "text":           "#CDD6F4",
    "text_muted":     "#6C7086",
    "text_subtle":    "#9399B2",

    # Status bar
    "status_bg":      "#11111B",
    "status_text":    "#6C7086",
}

# Corner marker colours (per-point)
POINT_COLORS = [C["accent"], C["green"], C["peach"], C["red"]]
CORNER_LABELS = ["TL", "TR", "BR", "BL"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hex_blend(hex1: str, hex2: str, t: float) -> str:
    """Linear-interpolate between two hex colours."""
    r1, g1, b1 = int(hex1[1:3], 16), int(hex1[3:5], 16), int(hex1[5:7], 16)
    r2, g2, b2 = int(hex2[1:3], 16), int(hex2[3:5], 16), int(hex2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── App ───────────────────────────────────────────────────────────────────────

class Doc2PDFApp:

    CANVAS_W = 760
    CANVAS_H = 570
    PREV_MIN_W = 220   # minimum preview panel width
    DETECT_MAX_DIM = 900   # longest side fed to the corner detector
    DPI_CHOICES = (150, 200, 300)
    JPEG_QUALITY = 90

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("doc2pdf")
        self.root.configure(bg=C["bg"])
        self.root.minsize(1180, 680)
        self.root.geometry("1240x760")

        # State
        self.image_path:    str | None = None
        self.img:           Image.Image | None = None   # display thumbnail
        self.img_full:      Image.Image | None = None   # original full-res
        self._scale:        float = 1.0                 # full_res / thumbnail
        self.tk_img:        ImageTk.PhotoImage | None = None
        self.points:        list[tuple[int, int]] = []
        self.dragging_point: int | None = None
        self._drop_active   = False
        self._dnd_loaded    = False
        self._src_resize_job: str | None = None
        self._prev_resize_job: str | None = None
        self._exporting     = False

        self._build_styles()
        self._build_ui()
        self._bind_dnd()

    # ── Styles ────────────────────────────────────────────────────────────────

    def _build_styles(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")

        # Frames
        s.configure("App.TFrame",     background=C["bg"])
        s.configure("Sidebar.TFrame", background=C["sidebar"])
        s.configure("Card.TFrame",    background=C["surface"])

        # Labels
        s.configure("TLabel",
                    background=C["bg"], foreground=C["text"],
                    font=("Helvetica", 11))
        s.configure("Sidebar.TLabel",
                    background=C["sidebar"], foreground=C["sidebar_text"],
                    font=("Helvetica", 11))
        s.configure("SidebarMuted.TLabel",
                    background=C["sidebar"], foreground=C["sidebar_muted"],
                    font=("Helvetica", 9))
        s.configure("SidebarTitle.TLabel",
                    background=C["sidebar"], foreground=C["sidebar_text"],
                    font=("Helvetica", 13, "bold"))
        s.configure("CardTitle.TLabel",
                    background=C["surface"], foreground=C["text_subtle"],
                    font=("Helvetica", 8, "bold"))
        s.configure("Status.TLabel",
                    background=C["status_bg"], foreground=C["status_text"],
                    font=("Helvetica", 9), padding=(10, 3))

        # Primary button (accent filled)
        s.configure("Primary.TButton",
                    font=("Helvetica", 11, "bold"),
                    foreground=C["bg"], background=C["accent"],
                    borderwidth=0, relief="flat", padding=(0, 10))
        s.map("Primary.TButton",
              background=[("active", C["accent_hover"]),
                          ("disabled", C["sidebar_border"])],
              foreground=[("disabled", C["sidebar_muted"])])

        # Ghost button (outline-ish)
        s.configure("Ghost.TButton",
                    font=("Helvetica", 10),
                    foreground=C["sidebar_text"], background=C["sidebar_border"],
                    borderwidth=0, relief="flat", padding=(0, 8))
        s.map("Ghost.TButton",
              background=[("active", C["sidebar_hover"])],
              foreground=[("active", C["accent"])])

        # Danger ghost
        s.configure("Danger.TButton",
                    font=("Helvetica", 10),
                    foreground=C["red"], background=C["sidebar_border"],
                    borderwidth=0, relief="flat", padding=(0, 8))
        s.map("Danger.TButton",
              background=[("active", "#3D2030")],
              foreground=[("active", C["red"])])

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Status bar (packed first so it stays at bottom) ───────────────────
        status_bar = tk.Frame(self.root, bg=C["status_bg"], height=26)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        status_bar.pack_propagate(False)
        tk.Frame(status_bar, bg=C["sidebar_border"], height=1).pack(
            side=tk.TOP, fill=tk.X)
        self._status_var = tk.StringVar(
            value="Ready  ·  Drop an image onto the canvas or click Open Image")
        tk.Label(status_bar, textvariable=self._status_var,
                 bg=C["status_bg"], fg=C["status_text"],
                 font=("Helvetica", 9), anchor="w").pack(
                 side=tk.LEFT, padx=10)
        if not _DND_AVAILABLE:
            tk.Label(status_bar, text="⚠  install tkinterdnd2 for drag-and-drop",
                     bg=C["status_bg"], fg=C["yellow"],
                     font=("Helvetica", 9)).pack(side=tk.RIGHT, padx=10)

        # ── Left sidebar ──────────────────────────────────────────────────────
        sidebar = tk.Frame(self.root, bg=C["sidebar"], width=220)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        tk.Frame(sidebar, bg=C["sidebar_border"], width=1).pack(
            side=tk.RIGHT, fill=tk.Y)

        inner = tk.Frame(sidebar, bg=C["sidebar"], padx=18, pady=18)
        inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(inner, text="doc2pdf", bg=C["sidebar"],
                 fg=C["accent"], font=("Helvetica", 20, "bold")).pack(anchor="w")
        tk.Label(inner, text="Document scanner", bg=C["sidebar"],
                 fg=C["sidebar_muted"], font=("Helvetica", 9)).pack(anchor="w", pady=(0, 24))

        self.open_btn = ttk.Button(
            inner, text="Open Image…", style="Ghost.TButton",
            command=self.load_image)
        self.open_btn.pack(fill=tk.X, pady=(0, 8))

        self.detect_btn = ttk.Button(
            inner, text="Auto-detect corners", style="Ghost.TButton",
            command=self.autodetect_corners, state=tk.DISABLED)
        self.detect_btn.pack(fill=tk.X, pady=(0, 8))

        self.save_btn = ttk.Button(
            inner, text="Export PDF…", style="Primary.TButton",
            command=self.generate_pdf, state=tk.DISABLED)
        self.save_btn.pack(fill=tk.X, pady=(0, 4))

        # Quick-save: same path as input, .pdf extension
        self.quicksave_btn = ttk.Button(
            inner, text="Save as .pdf (same path)", style="Ghost.TButton",
            command=self.quicksave_pdf, state=tk.DISABLED)
        self.quicksave_btn.pack(fill=tk.X, pady=(0, 4))

        tk.Label(inner, text="PDF EXPORT", bg=C["sidebar"],
                 fg=C["sidebar_muted"],
                 font=("Helvetica", 8, "bold")).pack(anchor="w", pady=(16, 4))
        dpi_row = tk.Frame(inner, bg=C["sidebar"])
        dpi_row.pack(fill=tk.X)
        tk.Label(dpi_row, text="Resolution", bg=C["sidebar"],
                 fg=C["sidebar_text"],
                 font=("Helvetica", 9)).pack(side=tk.LEFT)
        self.dpi_var = tk.StringVar(value="300")
        ttk.Combobox(dpi_row, textvariable=self.dpi_var,
                     values=[str(d) for d in self.DPI_CHOICES],
                     state="readonly", width=5,
                     font=("Helvetica", 9)).pack(side=tk.RIGHT)

        tk.Frame(inner, bg=C["sidebar_border"], height=1).pack(
            fill=tk.X, pady=(0, 16))

        tk.Label(inner, text="HOW TO USE", bg=C["sidebar"],
                 fg=C["sidebar_muted"], font=("Helvetica", 8, "bold")).pack(anchor="w")

        steps = [
            ("1", "Drop or open an image"),
            ("2", "Auto-detect or click the\n4 corners (TL→TR→BR→BL)"),
            ("3", "Drag corners to fine-tune"),
            ("4", "Export PDF"),
        ]
        for num, desc in steps:
            row = tk.Frame(inner, bg=C["sidebar"])
            row.pack(fill=tk.X, pady=(8, 0))
            tk.Label(row, text=num, bg=C["accent_dim"], fg=C["bg"],
                     font=("Helvetica", 9, "bold"),
                     width=2, anchor="center").pack(side=tk.LEFT, padx=(0, 8))
            tk.Label(row, text=desc, bg=C["sidebar"], fg=C["sidebar_text"],
                     font=("Helvetica", 9), justify=tk.LEFT,
                     wraplength=148).pack(side=tk.LEFT, anchor="nw")

        tk.Frame(inner, bg=C["sidebar_border"], height=1).pack(
            fill=tk.X, pady=(20, 12))

        tk.Label(inner, text="CORNERS", bg=C["sidebar"],
                 fg=C["sidebar_muted"], font=("Helvetica", 8, "bold")).pack(
                 anchor="w", pady=(0, 6))

        self._corner_frames = []
        self._corner_labels = []
        corner_row = tk.Frame(inner, bg=C["sidebar"])
        corner_row.pack(fill=tk.X)
        for i, label in enumerate(CORNER_LABELS):
            col = tk.Frame(corner_row, bg=C["sidebar_border"],
                           width=40, height=40)
            col.pack(side=tk.LEFT, padx=(0, 6))
            col.pack_propagate(False)
            lbl = tk.Label(col, text=label, bg=C["sidebar_border"],
                           fg=C["sidebar_muted"],
                           font=("Helvetica", 9, "bold"))
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            self._corner_frames.append(col)
            self._corner_labels.append(lbl)

        self.reset_btn = ttk.Button(
            inner, text="Reset corners", style="Danger.TButton",
            command=self._reset_points)
        self.reset_btn.pack(fill=tk.X, pady=(16, 0))

        # ── Main area with resizable panes ────────────────────────────────────
        main = tk.Frame(self.root, bg=C["bg"])
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._paned = tk.PanedWindow(
            main, orient=tk.HORIZONTAL,
            bg=C["bg"], sashwidth=6, sashrelief="flat",
            sashpad=0, opaqueresize=True
        )
        self._paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ── Source image card ─────────────────────────────────────────────────
        left_card = tk.Frame(self._paned, bg=C["surface"],
                             highlightbackground=C["border"],
                             highlightthickness=1)

        card_header = tk.Frame(left_card, bg=C["surface2"], height=34)
        card_header.pack(fill=tk.X)
        card_header.pack_propagate(False)
        tk.Label(card_header, text="SOURCE IMAGE",
                 bg=C["surface2"], fg=C["text_muted"],
                 font=("Helvetica", 8, "bold")).place(x=12, rely=0.5, anchor="w")

        self.canvas = tk.Canvas(
            left_card,
            bg=C["canvas_bg"], highlightthickness=0,
            cursor="crosshair"
        )
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))

        self._paned.add(left_card, stretch="always", minsize=300)

        # ── Preview card ──────────────────────────────────────────────────────
        right_card = tk.Frame(self._paned, bg=C["surface"],
                              highlightbackground=C["border"],
                              highlightthickness=1)

        prev_header = tk.Frame(right_card, bg=C["surface2"], height=34)
        prev_header.pack(fill=tk.X)
        prev_header.pack_propagate(False)
        tk.Label(prev_header, text="A4 PREVIEW",
                 bg=C["surface2"], fg=C["text_muted"],
                 font=("Helvetica", 8, "bold")).place(x=12, rely=0.5, anchor="w")

        self.preview_canvas = tk.Canvas(
            right_card,
            bg=C["surface"], highlightthickness=0
        )
        self.preview_canvas.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))

        self._paned.add(right_card, stretch="always", minsize=self.PREV_MIN_W)

        # Bind resize events so canvases update their content
        self.canvas.bind("<Configure>", self._on_source_resize)
        self.preview_canvas.bind("<Configure>", self._on_preview_resize)

        self._draw_dropzone()
        self._draw_preview_placeholder()

        self.canvas.bind("<ButtonPress-1>",   self.on_canvas_press)
        self.canvas.bind("<B1-Motion>",       self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

    # ── Drop zone ──────────────────────────────────────────────────────────────

    def _draw_dropzone(self):
        """Draw the dashed drop-zone placeholder on the source canvas."""
        self.canvas.delete("dropzone")
        w = self.canvas.winfo_width() or self.CANVAS_W
        h = self.canvas.winfo_height() or self.CANVAS_H
        pad = 32
        self.canvas.create_rectangle(
            pad, pad, w - pad, h - pad,
            outline=C["dropzone_icon"], width=2,
            dash=(8, 6), tags="dropzone"
        )
        cx, cy = w // 2, h // 2 - 24
        self.canvas.create_line(cx, cy + 20, cx, cy - 10,
                                fill=C["dropzone_icon"], width=3,
                                arrow=tk.LAST, arrowshape=(12, 14, 5),
                                tags="dropzone")
        self.canvas.create_line(cx - 16, cy + 20, cx + 16, cy + 20,
                                fill=C["dropzone_icon"], width=3,
                                tags="dropzone")
        dz_color = C["dropzone_text"] if not self._drop_active else C["accent"]
        self.canvas.create_text(
            w // 2, h // 2 + 16,
            text="Drop image here",
            fill=dz_color,
            font=("Helvetica", 14), tags="dropzone"
        )
        self.canvas.create_text(
            w // 2, h // 2 + 40,
            text="or click  Open Image",
            fill=C["sidebar_muted"],
            font=("Helvetica", 10), tags="dropzone"
        )

    def _draw_preview_placeholder(self):
        """Draw a subtle A4-sheet placeholder in the preview pane."""
        self.preview_canvas.delete("ph")
        w = self.preview_canvas.winfo_width() or self.PREV_MIN_W
        h = self.preview_canvas.winfo_height() or int(self.PREV_MIN_W * 297 / 210)
        pad = 24
        self.preview_canvas.create_rectangle(
            pad, pad, w - pad, h - pad,
            outline=C["border"], width=1,
            dash=(6, 5), tags="ph"
        )
        self.preview_canvas.create_text(
            w // 2, h // 2,
            text="Preview appears\nafter 4 corners",
            fill=C["text_muted"],
            font=("Helvetica", 10), justify=tk.CENTER, tags="ph"
        )

    # ── Resize handlers ───────────────────────────────────────────────────────

    def _on_source_resize(self, event):
        if self._src_resize_job is not None:
            self.root.after_cancel(self._src_resize_job)
        self._src_resize_job = self.root.after(80, self._apply_source_resize)

    def _apply_source_resize(self):
        self._src_resize_job = None
        if self.img is None:
            self._draw_dropzone()
        else:
            self._redisplay_image()

    def _on_preview_resize(self, event):
        if self._prev_resize_job is not None:
            self.root.after_cancel(self._prev_resize_job)
        self._prev_resize_job = self.root.after(120,
                                                self._apply_preview_resize)

    def _apply_preview_resize(self):
        self._prev_resize_job = None
        if len(self.points) == 4 and self.img is not None:
            self.update_preview()
        else:
            self._draw_preview_placeholder()

    # ── Corner indicator helpers ──────────────────────────────────────────────

    def _refresh_corner_indicators(self):
        for i, (frm, lbl) in enumerate(
                zip(self._corner_frames, self._corner_labels)):
            if i < len(self.points):
                color = POINT_COLORS[i]
                frm.config(bg=color)
                lbl.config(bg=color, fg=C["bg"])
            else:
                frm.config(bg=C["sidebar_border"])
                lbl.config(bg=C["sidebar_border"], fg=C["sidebar_muted"])

    # ── DnD binding ──────────────────────────────────────────────────────────

    def _bind_dnd(self):
        if not _DND_AVAILABLE:
            return
        # Register common file drop types
        self.canvas.drop_target_register(DND_FILES, "text/uri-list")
        self.canvas.dnd_bind("<<DropEnter>>",    self._on_drop_enter)
        self.canvas.dnd_bind("<<DropLeave>>",    self._on_drop_leave)
        self.canvas.dnd_bind("<<DropPosition>>", self._on_drop_position)
        self.canvas.dnd_bind("<<Drop>>",         self._on_drop)
        self.canvas.dnd_bind("<<Drop:text/uri-list>>", self._on_drop)

    def _on_drop_enter(self, event):
        self._drop_active = True
        self._dnd_loaded = False
        if self.img is None:
            self._draw_dropzone()
        self.canvas.config(highlightbackground=C["accent"],
                           highlightthickness=2)
        return "copy"

    def _on_drop_position(self, event):
        # <<DropPosition>> is the most reliable event carrying file data on
        # Ubuntu/Nautilus.  Load once per drag session (guard with _dnd_loaded).
        if not self._dnd_loaded:
            if self._handle_drop_data(event.data):
                self._dnd_loaded = True
        return "copy"

    def _on_drop_leave(self, event):
        self._drop_active = False
        self._dnd_loaded = False
        if self.img is None:
            self._draw_dropzone()
        self.canvas.config(highlightthickness=0)
        return "copy"

    def _on_drop(self, event):
        self._drop_active = False
        self.canvas.config(highlightthickness=0)
        # Try to load from <<Drop>> data too; _handle_drop_data is a no-op if
        # the path was already loaded via _on_drop_position.
        if not self._dnd_loaded:
            if self._handle_drop_data(event.data):
                self._dnd_loaded = True
        return "copy"

    def _handle_drop_data(self, data: str | None) -> bool:
        for path in self._iter_drop_paths(data):
            if os.path.isfile(path):
                self._load_path(path)
                return True
        return False

    def _iter_drop_paths(self, data: str | None):
        """Yield local file paths from a DnD data string or Tcl list."""
        if not data:
            return
        items: list[str]
        try:
            items = list(self.root.tk.splitlist(data))
        except tk.TclError:
            items = [str(data)]
        candidates: list[str] = []
        for item in items:
            if not item:
                continue
            text = item.strip()
            if not text:
                continue
            if "\n" in text or "\r" in text:
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        candidates.append(line)
            else:
                candidates.append(text)
        for entry in candidates:
            if entry.startswith("#"):
                continue
            if entry.startswith("file:"):
                parsed = urlparse(entry)
                yield url2pathname(parsed.path)
                continue
            yield entry

    # ── Image loading ─────────────────────────────────────────────────────────

    def load_image(self):
        filetypes = [("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff *.webp")]
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            self._load_path(path)

    def _load_path(self, path: str):
        try:
            img = Image.open(path)
            transposed = ImageOps.exif_transpose(img)
            if transposed is not None:
                img = transposed
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.load()
        except Exception as exc:
            messagebox.showerror(
                "Could not open image",
                f"{os.path.basename(path)}\n\n{exc}",
            )
            return
        self.image_path = path
        self.img_full = img
        self.points = []
        self.dragging_point = None
        self._refresh_corner_indicators()
        self.save_btn.config(state=tk.DISABLED)
        self.quicksave_btn.config(state=tk.DISABLED)
        self.detect_btn.config(state=tk.NORMAL)
        self.preview_canvas.delete("all")
        self._draw_preview_placeholder()
        self._redisplay_image()
        self._set_status(
            f"Opened  ·  {os.path.basename(path)}"
            f"  ({img.width}\u00d7{img.height})"
            f"  ·  Click the 4 document corners or use Auto-detect"
        )

    def _redisplay_image(self):
        """Fit img_full into the current canvas size and redraw."""
        if self.img_full is None:
            return
        cw = self.canvas.winfo_width() or self.CANVAS_W
        ch = self.canvas.winfo_height() or self.CANVAS_H
        self.img = self.img_full.copy()
        self.img.thumbnail((cw, ch), Image.LANCZOS)
        self._scale = self.img_full.width / self.img.width
        self.tk_img = ImageTk.PhotoImage(self.img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
        # Redraw overlay points if any
        if self.points:
            self._redraw_overlay()

    # ── Canvas events ─────────────────────────────────────────────────────────

    def on_canvas_press(self, event):
        if self.img is None:
            # Act like "open" when clicking empty drop zone
            self.load_image()
            return

        if len(self.points) == 4:
            # Try to grab an existing point
            for i, pt in enumerate(self.points):
                if abs(event.x - pt[0]) < 18 and abs(event.y - pt[1]) < 18:
                    self.dragging_point = i
                    self.canvas.config(cursor="fleur")
                    return
            self.dragging_point = None
            return

        # Add new point
        self.points.append(self._clamp_to_img(event.x, event.y))
        self._refresh_corner_indicators()
        self._redraw_overlay()

        if len(self.points) == 4:
            self.points = self._order_quads(self.points)
            self._refresh_corner_indicators()
            self._redraw_overlay()
            self.save_btn.config(state=tk.NORMAL)
            self.quicksave_btn.config(state=tk.NORMAL)
            self.update_preview()
            self._set_status(
                "All 4 corners set  ·  Drag to fine-tune  ·  Click Export PDF when ready")
        else:
            next_names = ["Top-Left", "Top-Right", "Bottom-Right", "Bottom-Left"]
            self._set_status(
                f"Corner {len(self.points)} of 4 set"
                f"  ·  Next: {next_names[len(self.points)]}")

    def on_drag(self, event):
        if self.dragging_point is None:
            return
        self.points[self.dragging_point] = self._clamp_to_img(event.x, event.y)
        self._redraw_overlay()
        self.update_preview()

    def on_release(self, event):
        if self.dragging_point is not None:
            self.dragging_point = None
            self.canvas.config(cursor="crosshair")

    def _reset_points(self):
        self.points = []
        self.dragging_point = None
        self._refresh_corner_indicators()
        self._redraw_overlay()
        self.save_btn.config(state=tk.DISABLED)
        self.quicksave_btn.config(state=tk.DISABLED)
        self.preview_canvas.delete("all")
        self._draw_preview_placeholder()
        if self.img:
            self._set_status("Corners reset  ·  Click the 4 document corners")

    # ── Geometry / auto-detection ─────────────────────────────────────────────

    def _clamp_to_img(self, x: float, y: float) -> tuple[int, int]:
        """Keep a canvas coordinate inside the displayed thumbnail."""
        if self.img is None:
            return int(x), int(y)
        xi = max(0, min(round(x), self.img.width))
        yi = max(0, min(round(y), self.img.height))
        return xi, yi

    @staticmethod
    def _order_quads(pts) -> list[tuple[int, int]]:
        """Sort 4 points into TL, TR, BR, BL order."""
        arr = np.asarray(pts, dtype="float32")
        s = arr.sum(axis=1)
        d = arr[:, 1] - arr[:, 0]
        return [
            tuple(np.round(arr[np.argmin(s)]).astype(int)),
            tuple(np.round(arr[np.argmin(d)]).astype(int)),
            tuple(np.round(arr[np.argmax(s)]).astype(int)),
            tuple(np.round(arr[np.argmax(d)]).astype(int)),
        ]

    def autodetect_corners(self):
        if self.img_full is None:
            messagebox.showinfo("No image", "Open an image first.")
            return
        pts = self._detect_document_quad()
        if pts is None:
            messagebox.showwarning(
                "No document detected",
                "Could not find four document corners automatically.\n"
                "Please click the corners manually.",
            )
            return
        self.points = pts
        self.dragging_point = None
        self._refresh_corner_indicators()
        self._redraw_overlay()
        self.save_btn.config(state=tk.NORMAL)
        self.quicksave_btn.config(state=tk.NORMAL)
        self.update_preview()
        self._set_status(
            "Auto-detected corners"
            "  ·  Drag to fine-tune  ·  Click Export PDF when ready")

    def _detect_document_quad(self):
        """Locate the dominant quadrilateral (document) in the image.

        Works on a downscaled copy; returns corner points in canvas
        thumbnail coordinates ordered TL/TR/BR/BL, or None when no
        plausible document is found.
        """
        if self.img_full is None or self._scale <= 0:
            return None
        full = np.array(self.img_full)
        h, w = full.shape[:2]
        dscale = min(1.0, self.DETECT_MAX_DIM / max(h, w))
        if dscale < 1.0:
            small = cv2.resize(full, (round(w * dscale), round(h * dscale)),
                               interpolation=cv2.INTER_AREA)
        else:
            small = full
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        img_area = float(small.shape[0] * small.shape[1])

        def finish(q):
            qo = self._order_quads(q.tolist())
            f = 1.0 / (dscale * self._scale)
            return [(round(x * f), round(y * f)) for x, y in qo]

        k3 = np.ones((3, 3), np.uint8)

        # Strategy 1: Canny edges (auto thresholds from the median, then
        # classic fixed ones).  Only a gentle morphological close – heavy
        # dilation merges unrelated edges into one giant blob whose
        # bounding box is just the image bounds.
        med = float(np.median(gray))
        for lo, hi in ((int(max(0, 0.66 * med)), int(min(255, 1.33 * med))),
                       (75, 200)):
            e = cv2.Canny(gray, lo, hi)
            e = cv2.morphologyEx(e, cv2.MORPH_CLOSE, k3, iterations=1)
            cnts, _ = cv2.findContours(e, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            q = self._best_quad(cnts, img_area, gray)
            if q is not None:
                return finish(q)

        # Strategy 2/3: bright-region segmentation (page is usually the
        # brightest large region), adaptive then Otsu.
        _, otsu = cv2.threshold(gray, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        k7 = np.ones((7, 7), np.uint8)
        for thr in (cv2.adaptiveThreshold(
                        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY, 35, 10),
                    otsu):
            t = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, k7, iterations=2)
            t = cv2.morphologyEx(t, cv2.MORPH_OPEN, k7)
            cnts, _ = cv2.findContours(t, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            # Segmentation finds *any* bright blob, so demand much
            # stronger evidence: a real page is far brighter than its
            # immediate surround (measured ~170 vs <=18 for background).
            q = self._best_quad(cnts, img_area, gray, min_contrast=35.0)
            if q is None and cnts:
                c = max(cnts, key=cv2.contourArea)
                if cv2.contourArea(c) >= img_area * 0.15:
                    q = self._quad_from_hull(c)
                    if q is not None and not (
                            self._quad_valid(q, img_area)
                            and self._quad_contrast_ok(q, gray, 35.0)):
                        q = None
            if q is not None:
                return finish(q)

        return None

    @staticmethod
    def _best_quad(contours, img_area: float, gray: np.ndarray,
                   min_frac: float = 0.06, min_contrast: float = 8.0):
        """Largest *valid* convex 4-point approximation among contours."""
        best = None
        best_area = 0.0
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
            area = cv2.contourArea(c)
            if area < img_area * min_frac or area <= best_area:
                continue
            peri = cv2.arcLength(c, True)
            for eps in (0.02, 0.03, 0.04, 0.05):
                ap = cv2.approxPolyDP(c, eps * peri, True)
                if len(ap) != 4 or not cv2.isContourConvex(ap):
                    continue
                quad = ap.reshape(4, 2).astype("float32")
                if not Doc2PDFApp._quad_valid(quad, img_area):
                    continue
                if not Doc2PDFApp._quad_contrast_ok(quad, gray,
                                                    min_contrast):
                    continue
                best = quad
                best_area = area
                break
        return best

    @staticmethod
    def _quad_from_hull(contour):
        """Approximate a contour's convex hull down to exactly 4 points."""
        hull = cv2.convexHull(contour)
        peri = cv2.arcLength(hull, True)
        for eps in (0.02, 0.03, 0.04, 0.05, 0.07, 0.09):
            ap = cv2.approxPolyDP(hull, eps * peri, True)
            if len(ap) == 4 and cv2.isContourConvex(ap):
                return ap.reshape(4, 2).astype("float32")
        return None

    @staticmethod
    def _quad_valid(quad: np.ndarray, img_area: float) -> bool:
        """Reject degenerate suggestions: ~full-frame quads (= image
        bounds, i.e. detection failure) and thin slivers."""
        x = quad[:, 0].astype("float64")
        y = quad[:, 1].astype("float64")
        area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        if not img_area * 0.06 <= area <= img_area * 0.97:
            return False
        sides = np.hypot(*(np.roll(quad, -1, axis=0) - quad).T)
        if sides.min() < (img_area ** 0.5) * 0.05:
            return False
        return True

    @staticmethod
    def _quad_contrast_ok(quad: np.ndarray, gray: np.ndarray,
                          min_delta: float = 8.0) -> bool:
        """A document page is typically brighter than what immediately
        surrounds it; candidates whose interior isn't brighter than a
        thin band just outside their own edges are almost always
        background texture."""
        m = np.zeros(gray.shape, np.uint8)
        cv2.fillPoly(m, [np.round(quad).astype(np.int32)], 255)
        if not m.any() or m.all():
            return False
        ring = cv2.dilate(m, np.ones((19, 19), np.uint8))
        band = (ring == 255) & (m == 0)
        if not band.any():
            return False
        inside = float(gray[m == 255].mean())
        outside = float(gray[band].mean())
        return inside - outside >= min_delta

    # ── Overlay drawing ──────────────────────────────────────────────────────

    def _redraw_overlay(self):
        self.canvas.delete("overlay")

        if len(self.points) < 2:
            self._draw_single_points()
            return

        # Draw filled polygon if 4 points
        if len(self.points) == 4:
            flat = [coord for pt in self.points for coord in pt]
            self.canvas.create_polygon(
                *flat,
                fill=_hex_blend(C["accent"], "#000000", 0.75),
                outline="",
                stipple="gray25",
                tags="overlay"
            )
            # Connecting edge lines
            pts = self.points + [self.points[0]]
            for a, b in zip(pts, pts[1:]):
                self.canvas.create_line(
                    a[0], a[1], b[0], b[1],
                    fill=C["accent"], width=1,
                    dash=(5, 4), tags="overlay"
                )
        else:
            # Partial connecting lines
            for a, b in zip(self.points, self.points[1:]):
                self.canvas.create_line(
                    a[0], a[1], b[0], b[1],
                    fill=C["accent_dim"], width=1,
                    dash=(4, 4), tags="overlay"
                )

        self._draw_single_points()

    def _draw_single_points(self):
        R = 11
        for i, pt in enumerate(self.points):
            color = POINT_COLORS[i]
            x, y = pt
            # White ring (contrast halo)
            self.canvas.create_oval(
                x - R - 2, y - R - 2, x + R + 2, y + R + 2,
                fill="white", outline="white", tags="overlay"
            )
            # Coloured dot
            self.canvas.create_oval(
                x - R, y - R, x + R, y + R,
                fill=color, outline=color, tags="overlay"
            )
            # Label
            self.canvas.create_text(
                x, y, text=CORNER_LABELS[i],
                fill=C["bg"], font=("Helvetica", 8, "bold"),
                tags="overlay"
            )

    # ── Preview ───────────────────────────────────────────────────────────────

    def update_preview(self):
        if len(self.points) != 4 or self.img is None:
            return
        # Fit preview into the actual canvas area, maintaining A4 ratio
        cw = self.preview_canvas.winfo_width() or self.PREV_MIN_W
        ch = self.preview_canvas.winfo_height() or int(self.PREV_MIN_W * 297 / 210)
        a4_ratio = 297 / 210
        if ch / cw > a4_ratio:
            pw = cw - 2
            ph = int(pw * a4_ratio)
        else:
            ph = ch - 2
            pw = int(ph / a4_ratio)
        pw = max(pw, 10)
        ph = max(ph, 10)
        img_cv = cv2.cvtColor(np.array(self.img), cv2.COLOR_RGB2BGR)
        pts_src = np.array(self.points, dtype="float32")
        pts_dst = np.array(
            [[0, 0], [pw - 1, 0], [pw - 1, ph - 1], [0, ph - 1]],
            dtype="float32"
        )
        M = cv2.getPerspectiveTransform(pts_src, pts_dst)
        warped = cv2.warpPerspective(img_cv, M, (pw, ph))
        warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
        pimg = Image.fromarray(warped_rgb)
        ptk = ImageTk.PhotoImage(pimg)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, anchor=tk.NW, image=ptk)
        self.preview_canvas.image = ptk  # keep reference

    # ── PDF export ────────────────────────────────────────────────────────────

    @staticmethod
    def _a4_px(dpi: int) -> tuple[int, int]:
        """A4 page dimensions in pixels at the given dpi."""
        return (int(round(210 * dpi / 25.4)), int(round(297 * dpi / 25.4)))

    @staticmethod
    def _warp_to_a4(img: Image.Image, pts_src: np.ndarray,
                    out_w: int, out_h: int) -> np.ndarray:
        """Perspective-warp img so pts_src fills an out_w×out_h page (BGR)."""
        img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        pts_dst = np.array(
            [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
            dtype="float32"
        )
        M = cv2.getPerspectiveTransform(pts_src, pts_dst)
        return cv2.warpPerspective(img_cv, M, (out_w, out_h),
                                   flags=cv2.INTER_LANCZOS4)

    def _write_pdf(self, warped: np.ndarray, pdf_path: str):
        """Write a warped BGR array to a PDF file at A4 size.

        The page is embedded as JPEG so reportlab passes the compressed
        DCT stream through verbatim; a lossless PNG embed would be many
        times larger.
        """
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                tmp = tf.name
            if not cv2.imwrite(tmp, warped,
                               [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY]):
                raise RuntimeError("Failed to encode the page as JPEG.")
            c = pdf_canvas.Canvas(pdf_path, pagesize=A4)
            c.drawImage(tmp, 0, 0, width=A4[0], height=A4[1],
                        preserveAspectRatio=False)
            c.showPage()
            c.save()
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)

    def _start_export(self, pdf_path: str, dpi: int):
        """Snapshot the selection and run the export in a background thread."""
        ow, oh = self._a4_px(dpi)
        s = self._scale
        pts_src = np.array([(x * s, y * s) for x, y in self.points],
                           dtype="float32")
        img_ref = self.img_full
        self._exporting = True
        self.detect_btn.config(state=tk.DISABLED)
        self.save_btn.config(state=tk.DISABLED)
        self.quicksave_btn.config(state=tk.DISABLED)
        self._set_status(f"Exporting…  ({ow}\u00d7{oh}px @ {dpi} dpi)")
        threading.Thread(
            target=self._export_worker,
            args=(img_ref, pts_src, ow, oh, dpi, pdf_path),
            daemon=True,
        ).start()

    def _export_worker(self, img, pts_src, ow, oh, dpi, pdf_path):
        try:
            warped = self._warp_to_a4(img, pts_src, ow, oh)
            self._write_pdf(warped, pdf_path)
            self.root.after(0, self._export_finished,
                            pdf_path, ow, oh, dpi, None)
        except Exception as exc:
            self.root.after(0, self._export_finished,
                            pdf_path, ow, oh, dpi, str(exc))

    def _export_finished(self, pdf_path, ow, oh, dpi, err):
        # Widget updates must happen on the main thread (via root.after).
        self._exporting = False
        if not self.root.winfo_exists():
            return
        can_export = len(self.points) == 4 and self.img_full is not None
        self.save_btn.config(
            state=tk.NORMAL if can_export else tk.DISABLED)
        self.quicksave_btn.config(
            state=tk.NORMAL if can_export else tk.DISABLED)
        self.detect_btn.config(
            state=tk.NORMAL if self.img_full is not None else tk.DISABLED)
        if err:
            self._set_status("Export failed")
            messagebox.showerror("Export failed", err)
            return
        self._set_status(
            f"Exported  ·  {os.path.basename(pdf_path)}"
            f"  ({ow}\u00d7{oh}px @ {dpi} dpi)")
        messagebox.showinfo("PDF exported", f"Saved to:\n{pdf_path}")

    def generate_pdf(self):
        if self._exporting:
            return
        if len(self.points) != 4 or self.img_full is None:
            messagebox.showerror("Error", "Please select 4 corners first.")
            return
        pdf_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=self._default_pdf_name(),
        )
        if not pdf_path:
            return
        self._start_export(pdf_path, int(self.dpi_var.get()))

    def quicksave_pdf(self):
        if self._exporting:
            return
        if len(self.points) != 4 or self.img_full is None:
            messagebox.showerror("Error", "Please select 4 corners first.")
            return
        pdf_path = self._default_pdf_path()
        if os.path.exists(pdf_path):
            if not messagebox.askyesno(
                "Overwrite?",
                f"{os.path.basename(pdf_path)} already exists.\nOverwrite?"
            ):
                return
        self._start_export(pdf_path, int(self.dpi_var.get()))

    def _default_pdf_path(self) -> str:
        """Return input path with .pdf extension."""
        base = os.path.splitext(self.image_path or "")[0]
        return base + ".pdf"

    def _default_pdf_name(self) -> str:
        """Return input filename with .pdf extension (no directory)."""
        return os.path.splitext(os.path.basename(self.image_path or ""))[0] + ".pdf"

    # ── Status ────────────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        self._status_var.set(text)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if _DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    Doc2PDFApp(root)
    root.mainloop()
