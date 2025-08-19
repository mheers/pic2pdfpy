import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import cv2
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import os

class Doc2PDFApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Document to DIN A4 PDF")
        self.image_path = None
        self.img = None
        self.tk_img = None
        self.points = []
        self.canvas_img = None
        self.setup_ui()

    def setup_ui(self):
        self.frame = tk.Frame(self.root)
        self.frame.pack()
        self.load_btn = tk.Button(self.frame, text="Load Image", command=self.load_image)
        self.load_btn.pack(side=tk.LEFT)
        self.save_btn = tk.Button(self.frame, text="Generate PDF", command=self.generate_pdf, state=tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT)
        self.tip_label = tk.Label(self.root, text="Tip: Click the document corners in this order: Top-Left, Top-Right, Bottom-Right, Bottom-Left.", fg="blue")
        self.tip_label.pack()
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self.main_frame, width=800, height=600, bg='gray')
        self.canvas.pack(side=tk.LEFT)
        self.canvas.bind("<Button-1>", self.on_click)
        self.preview_label = tk.Label(self.main_frame, text="Preview:")
        self.preview_label.pack(side=tk.TOP, padx=10)
        # DIN A4 aspect ratio: 210mm x 297mm, scale to fit nicely in the UI
        self.a4_preview_width = 420  # px
        self.a4_preview_height = 594  # px (A4 ratio)
        self.preview_canvas = tk.Canvas(self.main_frame, width=self.a4_preview_width, height=self.a4_preview_height, bg='white')
        self.preview_canvas.pack(side=tk.RIGHT, padx=10)
        self.dragging_point = None
        self.canvas.bind('<ButtonPress-1>', self.on_click)
        self.canvas.bind('<B1-Motion>', self.on_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_release)

    def load_image(self):
        filetypes = [("Image files", "*.png *.jpg *.jpeg")]
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            self.image_path = path
            self.img = Image.open(path)
            self.img.thumbnail((800, 600))
            self.tk_img = ImageTk.PhotoImage(self.img)
            self.canvas.delete("all")
            self.canvas_img = self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
            self.points = []
            self.save_btn.config(state=tk.DISABLED)
            self.preview_canvas.delete("all")

    def on_click(self, event):
        if self.img is None:
            return
        if len(self.points) < 4:
            self.points.append((event.x, event.y))
            self.redraw_points()
            if len(self.points) == 4:
                self.save_btn.config(state=tk.NORMAL)
                self.update_preview()
        else:
            # Check if click is near a point to start dragging
            for i, pt in enumerate(self.points):
                if abs(event.x - pt[0]) < 15 and abs(event.y - pt[1]) < 15:
                    self.dragging_point = i
                    break
            else:
                self.dragging_point = None

    def on_drag(self, event):
        if self.img is None or self.dragging_point is None:
            return
        # Move the selected point
        self.points[self.dragging_point] = (event.x, event.y)
        self.redraw_points()
        self.update_preview()

    def on_release(self, event):
        self.dragging_point = None

    def update_preview(self):
        # Perspective transform for preview
        if len(self.points) != 4 or self.img is None:
            self.preview_canvas.delete("all")
            return
        img_cv = cv2.cvtColor(np.array(self.img), cv2.COLOR_RGB2BGR)
        pts_src = np.array(self.points, dtype='float32')
        preview_width, preview_height = self.a4_preview_width, self.a4_preview_height
        pts_dst = np.array([
            [0, 0],
            [preview_width-1, 0],
            [preview_width-1, preview_height-1],
            [0, preview_height-1]
        ], dtype='float32')
        M = cv2.getPerspectiveTransform(pts_src, pts_dst)
        warped = cv2.warpPerspective(img_cv, M, (preview_width, preview_height))
        warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
        preview_img = Image.fromarray(warped_rgb)
        preview_tk = ImageTk.PhotoImage(preview_img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, anchor=tk.NW, image=preview_tk)
        self.preview_canvas.image = preview_tk  # Keep reference

    def redraw_points(self):
        self.canvas.delete("points")
        for i, pt in enumerate(self.points):
            self.canvas.create_oval(pt[0]-10, pt[1]-10, pt[0]+10, pt[1]+10, fill='red', tags="points")
            self.canvas.create_text(pt[0], pt[1], text=str(i+1), fill='white', font=('Arial', 12, 'bold'), tags="points")

    def generate_pdf(self):
        if len(self.points) != 4 or self.img is None:
            messagebox.showerror("Error", "Please select 4 points on the document.")
            return
        # Perspective transform
        img_cv = cv2.cvtColor(np.array(self.img), cv2.COLOR_RGB2BGR)
        pts_src = np.array(self.points, dtype='float32')
        width, height = A4  # points (1/72 inch)
        # Convert A4 size from points to pixels (assuming 72 dpi)
        a4_width_px = int(width)
        a4_height_px = int(height)
        pts_dst = np.array([
            [0, 0],
            [a4_width_px-1, 0],
            [a4_width_px-1, a4_height_px-1],
            [0, a4_height_px-1]
        ], dtype='float32')
        M = cv2.getPerspectiveTransform(pts_src, pts_dst)
        warped = cv2.warpPerspective(img_cv, M, (a4_width_px, a4_height_px))
        # Save as temp image
        temp_img_path = "temp_a4.png"
        cv2.imwrite(temp_img_path, warped)
        # Generate PDF
        pdf_path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")])
        if pdf_path:
            c = canvas.Canvas(pdf_path, pagesize=A4)
            c.drawImage(temp_img_path, 0, 0, width=A4[0], height=A4[1])
            c.showPage()
            c.save()
            os.remove(temp_img_path)
            messagebox.showinfo("Success", f"PDF saved to {pdf_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = Doc2PDFApp(root)
    root.mainloop()
