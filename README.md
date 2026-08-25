# doc2pdf

> Disclaimer: this tool has been vibe coded using GitHub Copilot in Agent Mode.

Scan a document photo and export it as a perspective-corrected DIN A4 PDF.
Load or drop an image, click the four document corners, and export.

## Features

- Drag-and-drop image loading (requires `tkinterdnd2`, see below)
- Click four corners to define the document boundary, or one-click **Auto-detect** (edge detection)
- Drag corners to fine-tune the selection
- Live A4 perspective-corrected preview
- Export to PDF fitted to DIN A4 — JPEG-compressed pages at selectable 150/200/300 dpi

Requires Python 3.10+.

## Installation

### Ubuntu 24.04

Install all dependencies via apt — no pip needed for the core app:

```bash
sudo apt install python3-tk python3-pil python3-pil.imagetk python3-opencv python3-reportlab
```

**Optional: drag-and-drop support**

`tkinterdnd2` is not in the Ubuntu package archive, so it requires a virtual environment:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install tkinterdnd2
# then run the app through the venv:
.venv/bin/python doc2pdf_gui.py
```

The `--system-site-packages` flag makes the venv reuse the apt-installed packages above.
Without `tkinterdnd2` the app still works fully — just use the **Open Image** button instead.

### Other Linux / macOS / Windows

```bash
pip install pillow opencv-python reportlab tkinterdnd2
```

## Usage

```bash
python3 doc2pdf_gui.py
```

1. Drop an image onto the canvas, or click **Open Image**
2. Click **Auto-detect**, or click the four corners of your document in order: **TL → TR → BR → BL**
3. Drag any corner to adjust
4. Click **Export PDF** and choose a save location

## Notes

- Supported image formats: PNG, JPEG, BMP, TIFF, WebP
- The PDF is generated at full DIN A4 size (210 × 297 mm)
