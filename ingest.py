"""
ingest.py — turn an uploaded scanned PDF manual into a machine's data:
  <machine_dir>/knowledge_base.json
  <machine_dir>/figures_manifest.json
  <machine_dir>/figures/*.jpg
Runs in a background thread. Uses pdftoppm (poppler) + tesseract (pytesseract).
"""
import os, re, json, glob, math, subprocess, tempfile, shutil, time
from pathlib import Path

def _clean(t):
    t = re.sub(r'[ \t]+', ' ', t)
    lines = [l.strip() for l in t.split('\n')]
    t = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', t).strip()

def _osd_rot(img):
    import pytesseract
    try:
        o = pytesseract.image_to_osd(img)
        return int([l for l in o.split('\n') if 'Rotate' in l][0].split(':')[1])
    except Exception:
        return 0

def _ink_profile(img):
    g = img.convert('L').resize((300, max(1, int(300 * img.height / img.width))))
    w, h = g.size; px = g.load()
    return [sum(1 for x in range(w) if px[x, y] < 110) / w for y in range(h)], h

def _band(rows, h):
    best = (0, 0, 0); s = None
    for y, r in enumerate(rows):
        if r > 0.20:
            if s is None: s = y
        elif s is not None:
            if y - s > best[0]: best = (y - s, s, y)
            s = None
    if s is not None and h - s > best[0]: best = (h - s, s, h)
    return best

def ingest_pdf(pdf_path, machine_dir, update):
    """update(status_dict) is called to persist progress/status."""
    from PIL import Image, ImageOps
    import pytesseract
    machine_dir = Path(machine_dir)
    figdir = machine_dir / "figures"; figdir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ocr_"))
    try:
        update({"status": "processing", "stage": "rendering pages", "progress": 0})
        subprocess.run(["pdftoppm", "-jpeg", "-r", "150", "-gray", str(pdf_path), str(tmp / "pg")],
                       check=True, timeout=1800)
        pages = sorted(glob.glob(str(tmp / "pg-*.jpg")))
        total = len(pages)
        if total == 0:
            raise RuntimeError("No pages rendered from PDF (is it a valid PDF?).")

        chunks, figures = [], []
        for i, p in enumerate(pages):
            n = int(re.search(r'pg-(\d+)', p).group(1))
            img = Image.open(p)
            rot = _osd_rot(img)
            up = img.rotate(-rot, expand=True) if rot else img
            # OCR text
            txt = _clean(pytesseract.image_to_string(up))
            if len(txt) >= 20:
                chunks.append({"chunk_id": f"p{n}", "page": n, "section": "",
                               "section_title": "", "text": txt, "char_len": len(txt)})
            # figure detection
            rows, h = _ink_profile(up)
            L, y0, y1 = _band(rows, h)
            if L / h >= 0.12:
                cap = ""
                m = re.search(r'(Fig[^\n]{0,50})', txt)
                if m: cap = m.group(1).strip()
                else:
                    first = next((l for l in txt.split('\n') if len(l) > 6), "")
                    cap = (first[:60] if first else f"Figure on page {n}")
                thumb = ImageOps.autocontrast(up.convert('L'), cutoff=1)
                thumb.thumbnail((900, 1300))
                fname = f"fig_p{n:03d}.jpg"
                thumb.convert('RGB').save(figdir / fname, quality=60, optimize=True)
                figures.append({"figure_id": f"fig_p{n}", "page": n, "caption": cap,
                                "image_key": f"figures/{fname}"})
            if i % 5 == 0 or i == total - 1:
                update({"status": "processing", "stage": f"reading pages {i+1}/{total}",
                        "progress": round(100 * (i + 1) / total)})

        (machine_dir / "knowledge_base.json").write_text(
            json.dumps({"chunk_count": len(chunks), "chunks": chunks}, ensure_ascii=False), encoding="utf-8")
        (machine_dir / "figures_manifest.json").write_text(
            json.dumps({"figure_count": len(figures), "figures": figures}, ensure_ascii=False), encoding="utf-8")
        update({"status": "ready", "available": True, "stage": "done", "progress": 100,
                "pages": total, "chunks": len(chunks), "figures": len(figures)})
    except Exception as e:
        update({"status": "failed", "available": False, "error": str(e)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
