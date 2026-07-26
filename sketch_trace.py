# -*- coding: utf-8 -*-
"""
Rysuje ściany modelu (2 lica każdej) na podkładzie szkicu 2c_parter.png.
Wynik: sketch_traced.png — lewo oryginał, prawo narysowane ściany.
  - Zielony     = ściany zewnętrzne  (ciemna=lico zew, jasna=lico wew)
  - Niebieski   = ściany wewnętrzne  (ciemna=lico 1,  jasna=lico 2)
  - Pomarańcz   = meble/wyposażenie (auto-detekcja białych obszarów)
"""
import cv2, numpy as np, os

OUT = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
src = cv2.imread(os.path.join(OUT, "2c_parter.png"))
H, W = src.shape[:2]
hsv  = cv2.cvtColor(src, cv2.COLOR_BGR2HSV)

# --- skala: model→piksel w 2c_parter.png ---
# Podkład przesunięty o (-0.06,-0.16) w Revicie =>
# piksel = (157 + (mx+0.06)/s,  1437 - (my+0.16)/s)
s    = 5.32 / 592.0     # m/px = 0.008986
OX, OY = 0.06, 0.16

def m2px(mx, my):
    return (int(round(157 + (mx + OX) / s)),
            int(round(1437 - (my + OY) / s)))

# --- definicja ścian modelu (x0,y0,x1,y1, grubość[m], zewnętrzna?) ---
walls = [
    (-0.24, -0.24,  5.56, -0.24, 0.48, True ),  # południe zew
    ( 5.56, -0.24,  5.56,  8.94, 0.48, True ),  # wschód zew
    ( 5.56,  8.94, -0.24,  8.94, 0.48, True ),  # północ zew
    (-0.24,  8.94, -0.24, -0.24, 0.48, True ),  # zachód zew
    ( 2.61, -0.24,  2.61,  2.61, 0.12, False),  # dzielnik kuchni/hol
    (-0.24,  1.66,  1.51,  1.66, 0.12, False),  # wiatr/WC dno
    (-0.24,  2.85,  1.90,  2.85, 0.12, False),  # schowek dno
    (-0.24,  5.12,  1.51,  5.12, 0.12, False),  # schowek top
    ( 1.51,  1.66,  1.51,  2.85, 0.12, False),  # WC wschód
    ( 1.90,  2.85,  1.90,  3.55, 0.12, False),  # schowek A
    ( 1.51,  3.55,  1.90,  3.55, 0.12, False),  # schowek B
    ( 1.51,  3.55,  1.51,  5.12, 0.12, False),  # schowek C
    ( 2.61,  2.01,  3.21,  2.01, 0.12, False),  # ścianka lodówki
]

# --- kolory ---
EXT_FACE1 = (0, 130, 0)       # zielony ciemny  = lico zewnętrzne
EXT_FACE2 = (30, 210, 30)     # zielony jasny   = lico wewnętrzne
EXT_FILL  = (140, 220, 140)   # wypełnienie zew
INT_FACE1 = (200, 30,  30)    # niebieski ciemny = lico 1 (BGR!)
INT_FACE2 = (255, 110, 80)    # niebieski jasny  = lico 2
INT_FILL  = (240, 190, 190)   # wypełnienie wew

# --- narysuj na kopii szkicu ---
tr = src.copy()

for (x0, y0, x1, y1, th, is_ext) in walls:
    p0 = m2px(x0, y0)
    p1 = m2px(x1, y1)
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    length = np.sqrt(dx * dx + dy * dy)
    if length < 1:
        continue
    # prostopadły wektor jednostkowy (lico)
    nx = -dy / length
    ny =  dx / length
    half = (th / 2.0) / s   # połowa grubości w pikselach

    f1 = EXT_FACE1 if is_ext else INT_FACE1
    f2 = EXT_FACE2 if is_ext else INT_FACE2
    fc = EXT_FILL  if is_ext else INT_FILL

    # wierzchołki prostokąta ściany
    A = (int(p0[0] + half * nx), int(p0[1] + half * ny))
    B = (int(p1[0] + half * nx), int(p1[1] + half * ny))
    C = (int(p1[0] - half * nx), int(p1[1] - half * ny))
    D = (int(p0[0] - half * nx), int(p0[1] - half * ny))

    # wypełnienie (semi-transparent)
    overlay = tr.copy()
    cv2.fillPoly(overlay, [np.array([A, B, C, D])], fc)
    cv2.addWeighted(overlay, 0.50, tr, 0.50, 0, tr)

    # linie lica (grubość 2px)
    cv2.line(tr, A, B, f1, 2)   # lico 1
    cv2.line(tr, D, C, f2, 2)   # lico 2

# --- meble: białe obszary wewnątrz wnętrza ---
beige = cv2.inRange(hsv, np.array([8, 25, 150]), np.array([35, 120, 245]))
beige = cv2.morphologyEx(beige, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
cnts_b, _ = cv2.findContours(beige, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
inner_cnt  = sorted(cnts_b, key=cv2.contourArea, reverse=True)[0]
inner_fill = np.zeros((H, W), np.uint8)
cv2.drawContours(inner_fill, [inner_cnt], -1, 255, -1)

white    = cv2.inRange(hsv, np.array([0, 0, 208]), np.array([180, 42, 255]))
furn_raw = cv2.bitwise_and(white, white, mask=inner_fill)
furn_raw = cv2.morphologyEx(furn_raw, cv2.MORPH_OPEN,  np.ones((5, 5),  np.uint8))
furn_raw = cv2.morphologyEx(furn_raw, cv2.MORPH_CLOSE, np.ones((12, 12), np.uint8))
cnts_f, _ = cv2.findContours(furn_raw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
furn_cnts = []
for c in cnts_f:
    a = cv2.contourArea(c)
    if a < 300 or a > 90000:
        continue
    x, y, ww, hh = cv2.boundingRect(c)
    if max(ww, hh) / max(1.0, min(ww, hh)) < 8.0:
        furn_cnts.append(c)

cv2.drawContours(tr, furn_cnts, -1, (0, 130, 255), 2)

# --- legenda ---
def leg(img, y, c1, c2, txt):
    x0 = 10
    cv2.line(img, (x0, y),    (x0+50, y),    c1, 3)
    cv2.line(img, (x0, y+12), (x0+50, y+12), c2, 3)
    cv2.putText(img, txt, (x0+58, y+10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (30,30,30), 1, cv2.LINE_AA)

leg(tr, H-130, EXT_FACE1, EXT_FACE2, "Sciana ZEW: lico zew / lico wew")
leg(tr, H-105, INT_FACE1, INT_FACE2, "Sciana WEW: lico 1 / lico 2")
cv2.rectangle(tr, (10, H-80), (60, H-68), (190, 225, 255), -1)
cv2.rectangle(tr, (10, H-80), (60, H-68), (0, 130, 255), 2)
cv2.putText(tr, "Meble/wyposazenie", (66, H-70),
            cv2.FONT_HERSHEY_SIMPLEX, 0.52, (30,30,30), 1, cv2.LINE_AA)

# --- side-by-side 1600px ---
sep   = np.full((H, 18, 3), 140, dtype=np.uint8)
combo = np.hstack([src, sep, tr])
sc    = 1600.0 / combo.shape[1]
out   = cv2.resize(combo, (int(combo.shape[1]*sc), int(combo.shape[0]*sc)),
                   interpolation=cv2.INTER_AREA)
cv2.imwrite(os.path.join(OUT, "sketch_traced.png"), out)
print("saved", out.shape[1], out.shape[0])
