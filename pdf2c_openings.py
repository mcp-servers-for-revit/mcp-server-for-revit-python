# -*- coding: utf-8 -*-
import cv2, numpy as np, json, os
OUT = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
img = cv2.imread(os.path.join(OUT, "2c_parter.png"))
H, W = img.shape[:2]
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
_, dark = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
# grube sciany: erozja usuwa cienkie linie (taras, okna-symbole)
thick = cv2.erode(dark, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
cnts, _ = cv2.findContours(thick, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
bx, by, bw, bh = cv2.boundingRect(cnts[0])
print("building_bbox_px", bx, by, bw, bh)
twall = 18  # przyblizona grubosc sciany zewn w px (do skanu pasma)

def scan(orient):
    res = []
    if orient in ("top", "bottom"):
        yc = by + (twall//2 if orient == "top" else bh - twall//2)
        band = dark[max(0,yc-twall//2):yc+twall//2, bx:bx+bw]
        col = band.sum(axis=0) / 255.0
        thr = band.shape[0]*0.45
        run = None
        for i, v in enumerate(col):
            wall = v > thr
            if (not wall) and run is None: run = i
            if wall and run is not None:
                if i-run > 12: res.append((bx+run, bx+i))
                run = None
        if run is not None and bw-run > 12: res.append((bx+run, bx+bw))
    else:
        xc = bx + (twall//2 if orient == "left" else bw - twall//2)
        band = dark[by:by+bh, max(0,xc-twall//2):xc+twall//2]
        col = band.sum(axis=1) / 255.0
        thr = band.shape[1]*0.45
        run = None
        for i, v in enumerate(col):
            wall = v > thr
            if (not wall) and run is None: run = i
            if wall and run is not None:
                if i-run > 12: res.append((by+run, by+i))
                run = None
        if run is not None and bh-run > 12: res.append((by+run, by+bh))
    return res

op = {o: scan(o) for o in ("top", "bottom", "left", "right")}
for o in op: print(o, op[o])
dbg = img.copy()
cv2.rectangle(dbg, (bx, by), (bx+bw, by+bh), (0, 200, 0), 2)
for o, lst in op.items():
    for a, b in lst:
        if o in ("top", "bottom"):
            y = by if o == "top" else by+bh
            cv2.rectangle(dbg, (a, y-12), (b, y+12), (0, 0, 255), 3)
        else:
            x = bx if o == "left" else bx+bw
            cv2.rectangle(dbg, (x-12, a), (x+12, b), (255, 0, 0), 3)
cv2.imwrite(os.path.join(OUT, "2c_openings_dbg.png"), dbg)
open(os.path.join(OUT, "2c_openings.json"), "w").write(json.dumps({"bbox": [bx,by,bw,bh], "op": op}))
print("saved 2c_openings_dbg.png")
