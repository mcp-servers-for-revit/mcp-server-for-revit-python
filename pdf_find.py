# -*- coding: utf-8 -*-
import fitz, os
PDF = r"D:\OneDrive - JD Daniel Cabanek\NORMA\Pn-rysunek-budowany-oznaczenia.pdf"
OUT = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
d = fitz.open(PDF)
print("pages", d.page_count)
for i in range(d.page_count):
    t = d[i].get_text().lower()
    keys = [k for k in ("schod","bieg","stopni","spocznik","podstopnic") if k in t]
    print(i+1, keys, t[:70].replace("\n"," "))
