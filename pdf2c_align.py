# -*- coding: utf-8 -*-
import cv2, numpy as np, os
OUT = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
img = cv2.imread(os.path.join(OUT, "2c_parter.png"))
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
# wnetrze = beżowe wypelnienie
mask = cv2.inRange(hsv, np.array([8,25,150]), np.array([35,120,245]))
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((21,21),np.uint8))
cnts,_ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
ix,iy,iw,ih = cv2.boundingRect(cnts[0])
print("interior_bbox", ix,iy,iw,ih, "ratio", round(float(ih)/iw,3))
_, dark = cv2.threshold(gray, 95, 255, cv2.THRESH_BINARY_INV)
# tylko WEWNATRZ wnetrza (odetnij ramke zewn): margines 8px
m=8
sub = dark[iy+m:iy+ih-m, ix+m:ix+iw-m]
sh,sw = sub.shape
# pionowe sciany wewn: kolumna z duzym pokryciem
colsum = sub.sum(axis=0)/255.0
def runs(arr, thr, minlen):
    out=[]; i=0
    while i<len(arr):
        if arr[i]>thr:
            j=i
            while j<len(arr) and arr[j]>thr: j+=1
            if j-i>=minlen: out.append(((i+j)//2, j-i, round(arr[i:j].max(),0)))
            i=j
        else: i+=1
    return out
vt = runs(colsum, sh*0.25, 2)
print("vertical_walls (px_x_in_sub, width, maxcov):", [(ix+m+c, w, int(mx)) for c,w,mx in vt])
rowsum = sub.sum(axis=1)/255.0
hz = runs(rowsum, sw*0.22, 2)
print("horizontal_walls (px_y_in_sub, height, maxcov):", [(iy+m+c, w, int(mx)) for c,w,mx in hz])
print("interior_right_px", ix+iw, "interior_bottom_px", iy+ih)
