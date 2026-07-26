# -*- coding: utf-8 -*-
import fitz, os
PDF = r"D:\OneDrive - JD Daniel Cabanek\CLAUDE\2C.pdf"
d = fitz.open(PDF)
print("pages", d.page_count)
for i in range(d.page_count):
    p = d[i]
    t = p.get_text()
    low = t.lower()
    keys = [k for k in ("parter","rzut","piętro","poddasze","przyziem","kondygnac") if k in low]
    snip = " ".join(t.split())[:80]
    print(i+1, "sz", int(p.rect.width), int(p.rect.height), "img", len(p.get_images()), "vec", len(p.get_drawings()), "|", keys, "|", snip)
