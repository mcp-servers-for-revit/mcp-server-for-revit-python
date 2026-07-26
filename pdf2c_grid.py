# -*- coding: utf-8 -*-
import cv2, numpy as np, os
OUT = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
img = cv2.imread(os.path.join(OUT, "2c_parter.png"))
H, W = img.shape[:2]
# beżowe wypełnienie pomieszczen (BGR) -> wnetrze
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
mask = cv2.inRange(hsv, np.array([8, 25, 150]), np.array([35, 120, 245]))
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15,15), np.uint8))
cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
ix, iy, iw, ih = cv2.boundingRect(cnts[0])
print("interior_bbox_px", ix, iy, iw, ih, "ratio", round(float(ih)/iw,3))
# skala: wnetrze ~ 5.32 (szer) x 8.70 (gleb) wg naszego modelu
sx = 5.32/iw; sy = 8.70/ih
print("scale m/px sx=%.5f sy=%.5f"%(sx,sy))
dbg = img.copy()
cv2.rectangle(dbg,(ix,iy),(ix+iw,iy+ih),(0,180,0),2)
# siatka co 0.5 m, origin = dolny-lewy rog wnetrza (model: x w prawo, y w gore=front na dole)
step=0.5
mx=0.0
while mx<=5.32+0.01:
    px=int(ix+mx/sx)
    cv2.line(dbg,(px,iy),(px,iy+ih),(255,150,0),1)
    cv2.putText(dbg,"%.1f"%mx,(px-10,iy-6),cv2.FONT_HERSHEY_SIMPLEX,0.5,(255,0,0),1)
    mx+=step
my=0.0
while my<=8.70+0.01:
    py=int(iy+ih-my/sy)   # y w gore
    cv2.line(dbg,(ix,py),(ix+iw,py),(255,150,0),1)
    cv2.putText(dbg,"%.1f"%my,(ix-32,py+4),cv2.FONT_HERSHEY_SIMPLEX,0.5,(255,0,0),1)
    my+=step
cv2.imwrite(os.path.join(OUT,"2c_grid.png"), dbg)
print("saved 2c_grid.png")
