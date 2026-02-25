"""
doc2pdf_gui.py  –  Document to DIN A4 PDF converter
Drop or open an image, click the four document corners, export to PDF.

Drag-and-drop requires tkinterdnd2:
    pip install tkinterdnd2
Without it the app still works fine via the "Open Image" button.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import cv2
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
import os
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
    PREV_W   = 360
    PREV_H   = int(360 * 297 / 210)   # exact A4 ratio ≈ 509

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
        # ── Left sidebar ──────────────────────────────────────────────────────
        sidebar = tk.Frame(self.root, bg=C["sidebar"], width=220)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        # Thin right border on sidebar
        tk.Frame(sidebar, bg=C["sidebar_border"], width=1).pack(
            side=tk.RIGHT, fill=tk.Y)

        inner = tk.Frame(sidebar, bg=C["sidebar"], padx=18, pady=18)
        inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # App title
        tk.Label(inner, text="doc2pdf", bg=C["sidebar"],
                 fg=C["accent"], font=("Helvetica", 20, "bold")).pack(anchor="w")
        tk.Label(inner, text="Document scanner", bg=C["sidebar"],
                 fg=C["sidebar_muted"], font=("Helvetica", 9)).pack(anchor="w", pady=(0, 24))

        # ── Open button ───────────────────────────────────────────────────────
        self.open_btn = ttk.Button(
            inner, text="Open Image…", style="Ghost.TButton",
            command=self.load_image)
        self.open_btn.pack(fill=tk.X, pady=(0, 8))

        # ── Export button ─────────────────────────────────────────────────────
        self.save_btn = ttk.Button(
            inner, text="Export PDF", style="Primary.TButton",
            command=self.generate_pdf, state=tk.DISABLED)
        self.save_btn.pack(fill=tk.X, pady=(0, 16))

        # Divider
        tk.Frame(inner, bg=C["sidebar_border"], height=1).pack(
            fill=tk.X, pady=(0, 16))

        # ── Steps guide ───────────────────────────────────────────────────────
        tk.Label(inner, text="HOW TO USE", bg=C["sidebar"],
                 fg=C["sidebar_muted"], font=("Helvetica", 8, "bold")).pack(anchor="w")

        steps = [
            ("1", "Drop or open an image"),
            ("2", "Click the 4 document\ncorners (TL → TR → BR → BL)"),
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

        # Divider
        tk.Frame(inner, bg=C["sidebar_border"], height=1).pack(
            fill=tk.X, pady=(20, 12))

        # ── Corner indicator strip ─────────────────────────────────────────────
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

        # ── Reset link ────────────────────────────────────────────────────────
        self.reset_btn = ttk.Button(
            inner, text="Reset corners", style="Danger.TButton",
            command=self._reset_points)
        self.reset_btn.pack(fill=tk.X, pady=(16, 0))

        # ── Main area ─────────────────────────────────────────────────────────
        main = tk.Frame(self.root, bg=C["bg"])
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Source image card ─────────────────────────────────────────────────
        left_card = tk.Frame(main, bg=C["surface"],
                             highlightbackground=C["border"],
                             highlightthickness=1)
        left_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                       padx=(16, 8), pady=16)

        # Card header
        card_header = tk.Frame(left_card, bg=C["surface2"], height=34)
        card_header.pack(fill=tk.X)
        card_header.pack_propagate(False)
        tk.Label(card_header, text="SOURCE IMAGE",
                 bg=C["surface2"], fg=C["text_muted"],
                 font=("Helvetica", 8, "bold")).place(x=12, rely=0.5, anchor="w")

        # Canvas inside card
        self.canvas = tk.Canvas(
            left_card,
            width=self.CANVAS_W, height=self.CANVAS_H,
            bg=C["canvas_bg"], highlightthickness=0,
            cursor="crosshair"
        )
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=1, pady=(34, 1))

        self._draw_dropzone()

        self.canvas.bind("<ButtonPress-1>",   self.on_canvas_press)
        self.canvas.bind("<B1-Motion>",       self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        # ── Preview card ──────────────────────────────────────────────────────
        right_card = tk.Frame(main, bg=C["surface"],
                              highlightbackground=C["border"],
                              highlightthickness=1)
        right_card.pack(side=tk.RIGHT, fill=tk.Y,
                        padx=(0, 16), pady=16)

        # Preview card header
        prev_header = tk.Frame(right_card, bg=C["surface2"], height=34)
        prev_header.pack(fill=tk.X)
        prev_header.pack_propagate(False)
        tk.Label(prev_header, text="A4 PREVIEW",
                 bg=C["surface2"], fg=C["text_muted"],
                 font=("Helvetica", 8, "bold")).place(x=12, rely=0.5, anchor="w")

        self.preview_canvas = tk.Canvas(
            right_card,
            width=self.PREV_W, height=self.PREV_H,
            bg=C["surface"], highlightthickness=0
        )
        self.preview_canvas.pack(padx=1, pady=(34, 1))
        self._draw_preview_placeholder()

        # ── Status bar ────────────────────────────────────────────────────────
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

        # DnD badge in status (shown when DnD unavailable)
        if not _DND_AVAILABLE:
            tk.Label(status_bar, text="⚠  install tkinterdnd2 for drag-and-drop",
                     bg=C["status_bg"], fg=C["yellow"],
                     font=("Helvetica", 9)).pack(side=tk.RIGHT, padx=10)

    # ── Drop zone ──────────────────────────────────────────────────────────────

    def _draw_dropzone(self):
        """Draw the dashed drop-zone placeholder on the source canvas."""
        self.canvas.delete("dropzone")
        w, h = self.CANVAS_W, self.CANVAS_H
        pad = 32
        # Dashed rounded rectangle (approximated with canvas dash)
        self.canvas.create_rectangle(
            pad, pad, w - pad, h - pad,
            outline=C["dropzone_icon"], width=2,
            dash=(8, 6), tags="dropzone"
        )
        # Cloud / upload icon (drawn with lines)
        cx, cy = w // 2, h // 2 - 24
        # Arrow up
        self.canvas.create_line(cx, cy + 20, cx, cy - 10,
                                fill=C["dropzone_icon"], width=3,
                                arrow=tk.LAST, arrowshape=(12, 14, 5),
                                tags="dropzone")
        # Horizontal tray
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
        w, h = self.PREV_W, self.PREV_H
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
        if not self._dnd_loaded and self._handle_drop_data(event.data):
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
        self.image_path = path
        self.img_full = Image.open(path)
        # Keep a display-sized copy for the canvas; preserve orignal for export
        self.img = self.img_full.copy()
        self.img.thumbnail((self.CANVAS_W, self.CANVAS_H), Image.LANCZOS)
        # Scale factor to map canvas coords → full-res coords
        self._scale = self.img_full.width / self.img.width
        self.tk_img = ImageTk.PhotoImage(self.img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
        self.points = []
        self._refresh_corner_indicators()
        self.save_btn.config(state=tk.DISABLED)
        self.preview_canvas.delete("all")
        self._draw_preview_placeholder()
        self._set_status(
            f"Opened  ·  {os.path.basename(path)}"
            f"  ({self.img_full.width}\u00d7{self.img_full.height})"
            f"  ·  Click the 4 document corners"
        )

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
        self.points.append((event.x, event.y))
        self._refresh_corner_indicators()
        self._redraw_overlay()

        if len(self.points) == 4:
            self.save_btn.config(state=tk.NORMAL)
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
        self.points[self.dragging_point] = (event.x, event.y)
        self._redraw_overlay()
        self.update_preview()

    def on_release(self, event):
        if self.dragging_point is not None:
            self.dragging_point = None
            self.canvas.config(cursor="crosshair")

    def _reset_points(self):
        self.points = []
        self._refresh_corner_indicators()
        self._redraw_overlay()
        self.save_btn.config(state=tk.DISABLED)
        self.preview_canvas.delete("all")
        self._draw_preview_placeholder()
        if self.img:
            self._set_status("Corners reset  ·  Click the 4 document corners")

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
        img_cv = cv2.cvtColor(np.array(self.img), cv2.COLOR_RGB2BGR)
        pts_src = np.array(self.points, dtype="float32")
        pw, ph = self.PREV_W, self.PREV_H
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

    # A4 at 300 dpi: 210mm * 300/25.4 ≈ 2480px,  297mm * 300/25.4 ≈ 3508px
    A4_DPI    = 300
    A4_W_PX   = int(round(210 * A4_DPI / 25.4))   # 2480
    A4_H_PX   = int(round(297 * A4_DPI / 25.4))   # 3508

    def generate_pdf(self):
        if len(self.points) != 4 or self.img_full is None:
            messagebox.showerror("Error", "Please select 4 corners first.")
            return

        # Scale canvas (thumbnail) coords up to full-resolution image coords
        s = self._scale
        pts_src = np.array(
            [(x * s, y * s) for x, y in self.points], dtype="float32"
        )

        # Warp full-resolution image to 300 dpi A4
        img_cv = cv2.cvtColor(np.array(self.img_full), cv2.COLOR_RGB2BGR)
        ow, oh = self.A4_W_PX, self.A4_H_PX
        pts_dst = np.array(
            [[0, 0], [ow - 1, 0], [ow - 1, oh - 1], [0, oh - 1]],
            dtype="float32"
        )
        M = cv2.getPerspectiveTransform(pts_src, pts_dst)
        warped = cv2.warpPerspective(img_cv, M, (ow, oh),
                                     flags=cv2.INTER_LANCZOS4)

        pdf_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")]
        )
        if not pdf_path:
            return

        # Write warped image into PDF at A4 page size
        # reportlab A4 is in points (1 pt = 1/72 in); image fills the page exactly
        tmp = "_doc2pdf_tmp.png"
        cv2.imwrite(tmp, warped, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        c = pdf_canvas.Canvas(pdf_path, pagesize=A4)
        c.drawImage(tmp, 0, 0, width=A4[0], height=A4[1],
                    preserveAspectRatio=False)
        c.showPage()
        c.save()
        os.remove(tmp)

        self._set_status(
            f"Exported  ·  {os.path.basename(pdf_path)}"
            f"  ({self.A4_W_PX}\u00d7{self.A4_H_PX}px @ {self.A4_DPI} dpi)"
        )
        messagebox.showinfo("PDF exported", f"Saved to:\n{pdf_path}")

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
