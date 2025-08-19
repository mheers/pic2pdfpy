# Document to DIN A4 PDF GUI

> Disclaimer: this tool has been vibe coded using GitHub Copilot in Agent Mode.

This Python program lets you load a photo (PNG/JPEG) of a document, click on the edges of the document in the image, and generate a DIN A4 PDF based on those edges.

## Features
- Load PNG or JPEG images
- Click four points to mark the document edges
- Perspective correction to DIN A4 size
- Save the result as a PDF

## Installation

Install the required dependencies using pip:

```bash
pip install pillow opencv-python reportlab
```

## Usage

Run the program:

```bash
python3 doc2pdf_gui.py
```

Follow the GUI prompts to load an image, click the four corners of your document, and generate a PDF.

## Notes
- The PDF will be generated with the document fitted to DIN A4 size.
- Make sure you have Python 3 installed.
