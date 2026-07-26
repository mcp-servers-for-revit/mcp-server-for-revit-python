# -*- coding: utf-8 -*-
import fitz, numpy as np, cv2, os
PDF = r"D:\OneDrive - JD Daniel Cabanek\CLAUDE\2C.pdf"
OUT = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
d = fitz.open(PDF); p = d[0]
pix = p.get_pixmap(matrix=fitz.Matrix(1, 1))
img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR) if pix.n == 4 else cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
H, W = img.shape[:2]
print("full", W, H)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
_, dark = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)
cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
boxes = []
for c in cnts:
    x, y, w, h = cv2.boundingRect(c)
    if w*h > 8000 and h > w and h > 0.18*H and y > 0.20*H and y < 0.92*H:
        boxes.append((x, y, w, h))
boxes.sort(key=lambda b: b[0])
print("plan_boxes", boxes)
if boxes:
    x, y, w, h = boxes[0]
    m = int(0.08*h)
    x0=max(0,x-m); y0=max(0,y-m); x1=min(W,x+w+m); y1=min(H,y+h+m)
    crop = img[y0:y1, x0:x1]
    cv2.imwrite(os.path.join(OUT, "2c_parter.png"), crop)
    print("parter crop", crop.shape[1], crop.shape[0], "at", x0, y0)
