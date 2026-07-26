# -*- coding: utf-8 -*-
import fitz, os
PDF = r"D:\OneDrive - JD Daniel Cabanek\CLAUDE\1A.pdf"
OUT = r"C:\Users\dcaba\AppData\Roaming\pyRevit\Extensions\mcp-server-for-revit-python.extension"
d = fitz.open(PDF)
print("pages", d.page_count)
for i in range(d.page_count):
    p = d[i]
    r = p.rect
    txt = p.get_text()
    imgs = p.get_images()
    drw = p.get_drawings()
    print("page", i+1, "size_pt", round(r.width,1), round(r.height,1),
          "text_len", len(txt), "images", len(imgs), "vector_paths", len(drw))
    # render at 300 DPI
    pix = p.get_pixmap(dpi=300)
    fn = os.path.join(OUT, "pdf_1A_p%d.png" % (i+1))
    pix.save(fn)
    print("  saved", fn, pix.width, "x", pix.height)
