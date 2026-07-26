# -*- coding: utf-8 -*-
import cv2, os
OUT = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
img = cv2.imread(os.path.join(OUT, "2c_parter.png"))
# aktualne sciany modelu (m)
walls = [
 [-0.24,-0.24,5.56,-0.24,0.48],[5.56,-0.24,5.56,8.94,0.48],[5.56,8.94,-0.24,8.94,0.48],[-0.24,8.94,-0.24,-0.24,0.48],
 [2.61,-0.24,2.61,2.61,0.12],[-0.24,1.66,1.51,1.66,0.12],[-0.24,2.85,1.90,2.85,0.12],
 [-0.24,5.12,1.51,5.12,0.12],[2.61,2.01,3.21,2.01,0.12],
 [1.51,1.66,1.51,2.85,0.12],[1.90,2.85,1.90,3.55,0.12],[1.51,3.55,1.90,3.55,0.12],[1.51,3.55,1.51,5.12,0.12]]
s = 5.32/592.0
# podklad przesuniety w revicie o (-0.06,-0.16) => wzgledem podkladu sciany +0.06 x, +0.16 y
OX, OY = 0.06, 0.16
def topx(mx, my): return (int(157+(mx+OX)/s), int(1437-(my+OY)/s))
# sciany tworzace obrys schowka (uskok) - wyroznione na zolto
schowek={(-0.24,2.85,1.90,2.85),(1.90,2.85,1.90,3.55),(1.51,3.55,1.90,3.55),(1.51,3.55,1.51,5.12),(-0.24,5.12,1.51,5.12),(1.51,1.66,1.51,2.85)}
for w in walls:
    x0,y0,x1,y1,th = w
    fridge = (abs(y0-2.01)<0.01 and abs(y1-2.01)<0.01)
    if (x0,y0,x1,y1) in schowek: color=(0,255,255); t=6   # zolty - schowek
    elif fridge: color=(0,0,255); t=4
    elif th==0.48: color=(0,200,0); t=4
    else: color=(255,0,255); t=4
    cv2.line(img, topx(x0,y0), topx(x1,y1), color, t)
cv2.imwrite(os.path.join(OUT, "2c_overlay.png"), img)
# zoomy
o=cv2.imread(os.path.join(OUT,"2c_overlay.png"))
cl=o[440:1050, 130:430]; cl=cv2.resize(cl,(cl.shape[1]*2,cl.shape[0]*2),interpolation=cv2.INTER_NEAREST); cv2.imwrite(os.path.join(OUT,"ov_cluster.png"),cl)
ki=o[1080:1460,420:680]; ki=cv2.resize(ki,(ki.shape[1]*2,ki.shape[0]*2),interpolation=cv2.INTER_NEAREST); cv2.imwrite(os.path.join(OUT,"ov_kitchen2.png"),ki)
print("saved", o.shape[1], o.shape[0])
