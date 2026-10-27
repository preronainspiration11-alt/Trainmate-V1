# Stage 2 — self-service manual upload (multi-machine)

Admins upload a PDF in the app; it OCRs in the background and becomes a new machine
section everyone can query. Uploaded data lives on a persistent disk so it survives redeploys.

## What changed vs your live app
- New files: `ingest.py` (OCR pipeline). Updated: `server.py`, `client.html`, `Dockerfile`,
  `requirements.txt`, `render.yaml`. The Dockerfile now installs **tesseract-ocr** and
  **poppler-utils**, so the first build is a bit slower and the image larger.

## Deploy on your existing Render service
1. **Add a persistent disk** (Render → your service → **Disks** → Add Disk):
   - Name: `trainmate-data`   Mount path: `/var/data`   Size: 5 GB
   (Or let `render.yaml` create it via a Blueprint.)
2. **Bump the plan** to **Standard** (Settings → Instance Type). OCR needs the extra RAM;
   Starter can run out of memory on big scans.
3. **Add env vars** (Environment): keep your existing ones, and add
   - `DATA_DIR = /var/data`
   - `TRAINMATE_ADMINS = <your-username>`  (only these users can upload; default = first user)
4. **Push the updated files** to your repo (in the same folder Render builds from) and let it
   redeploy. Check `/health` — it should show your `admins` and `data_dir`.

## Using it
- Sign in as an admin → **Machines** → a dashed **“Upload a manual”** tile appears.
- Enter the machine/brand name (+ optional model), choose the PDF, **Upload & process**.
- The new tile shows **Processing …%**; a 144-page scan takes a few minutes. When done it flips
  to **Available** for everyone and answers just like Posimat.
- Admins can **Remove** an uploaded machine (deletes its data).

## Notes / limits (by design, per our plan)
- **Diagrams for uploaded machines** are the scanned figures from that manual (auto-extracted),
  not hand-drawn schematics. Posimat keeps its clean SVGs. Ask me to hand-draw key diagrams for
  an important machine later.
- **Sections/citations**: uploaded manuals are cited by **page** (no chapter map), since we don't
  have their table of contents. Still accurate and grounded.
- **One upload at a time** is easiest on a Standard instance. Very large PDFs use more memory.
- Non-PDF or image-only weirdness may OCR poorly — that's the scan's quality, not the pipeline.

## Adding clean navigation later
This is Stage 3-ready: every uploaded machine is already its own section in the hub. If you want
per-machine categories (Safety/Production… inside a machine) we can add that on top.
