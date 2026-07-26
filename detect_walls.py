# -*- coding: utf-8 -*-
import cv2, numpy as np, json, os, sys

IN = r"D:\OneDrive - JD Daniel Cabanek\AI REVIT\parter_cropped.jpg"
OUTDIR = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"

img = cv2.imread(IN)
if img is None:
    print("BLAD: nie wczytano obrazu", IN); sys.exit(1)
H, W = img.shape[:2]
print("img_size_WxH", W, H)

# odetnij stopke (pasek kontaktowy) ~ dolne 12%
plan = img[0:int(H*0.88), :]
gray = cv2.cvtColor(plan, cv2.COLOR_BGR2GRAY)

# maska ciemnych pikseli = sciany (i ciemne kontury)
_, dark = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)

# obrys budynku = najwiekszy kontur zewnetrzny
cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
bx, by, bw, bh = cv2.boundingRect(cnts[0])
print("building_bbox_xywh", bx, by, bw, bh, "ratio_h/w", round(float(bh)/bw, 3))

# sciany poziome/pionowe przez morfologie
hk = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25))
horiz = cv2.morphologyEx(dark, cv2.MORPH_OPEN, hk)
vert = cv2.morphologyEx(dark, cv2.MORPH_OPEN, vk)

def seg_bboxes(mask, kind, minlen=15):
    c, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for cc in c:
        x, y, w, h = cv2.boundingRect(cc)
        if kind == "H" and w < minlen: continue
        if kind == "V" and h < minlen: continue
        out.append([int(x), int(y), int(w), int(h)])
    return out

Hsegs = seg_bboxes(horiz, "H")
Vsegs = seg_bboxes(vert, "V")
print("H_walls", len(Hsegs))
print("V_walls", len(Vsegs))

dbg = plan.copy()
for x, y, w, h in Hsegs: cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 0, 255), 1)
for x, y, w, h in Vsegs: cv2.rectangle(dbg, (x, y), (x + w, y + h), (255, 0, 0), 1)
cv2.rectangle(dbg, (bx, by), (bx + bw, by + bh), (0, 200, 0), 2)
cv2.imwrite(os.path.join(OUTDIR, "debug_walls.png"), dbg)
cv2.imwrite(os.path.join(OUTDIR, "debug_dark.png"), dark)

data = {"img": [W, H], "building_bbox": [bx, by, bw, bh], "H": Hsegs, "V": Vsegs}
open(os.path.join(OUTDIR, "walls.json"), "w").write(json.dumps(data))
print("OK zapisano debug_walls.png, debug_dark.png, walls.json")
