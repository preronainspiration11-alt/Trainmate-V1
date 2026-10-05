# TrainMate — Production Line build (Posimat + Mengibar)

Your first full line is ready: **Posimat MASTER-15 (unscrambler)** → **Mengibar
MassFlow Filler-Capper 16MS/165/8-SC (filler/capper)**. Both are built in and answer
questions the same way, with diagrams.

## What changed from your current deploy
- Machines now live under **`machines/<id>/`** (each with `knowledge_base.json`,
  `figures_manifest.json`, `figures/`, `meta.json`). Posimat moved here; **Mengibar added**.
- The old **`data/`** folder is gone — delete it in your repo if it's still there.
- Updated: `server.py` (loads every machine under `machines/`) and `client.html`
  (machine-specific topic chips; Mengibar gets filler/capper/CIP/rejection topics).
- Everything else (login, language EN/BN/HI, voice, admin upload) is unchanged.

## Deploy (same repo + Render service)
1. In your repo, replace the contents with this `kit` (keep your env vars). In particular:
   - add the **`machines/`** folder, replace **`server.py`** and **`client.html`**,
   - **delete the old `data/` folder** if present.
2. Commit & push → Render redeploys. Check `/health` → `machines` should be ≥ 2.
3. Sign in → **Machines** → you'll now see **Posimat** and **Mengibar** both Available.

## Adding the rest of the line later
Two ways, both already supported:
- **Self-serve upload** (admin → Upload a manual): good for quick adds; figures are the
  manual's own scans and citations are by page.
- **Curated (what I did for Mengibar):** send me the manual and I'll ingest it, pick the
  clean component diagrams, set the model/name, and drop in a new `machines/<id>/` folder —
  same quality as Posimat/Mengibar. Best for the important line machines.

## Notes on Mengibar
- Model: MassFlow Filler-Capper **16MS/165/8-SC**. Covers filler turret, capper, sorter,
  universal rejection, product valve/tank, conveyors, CIP, LRFL filling head, maintenance.
- 10 component diagrams extracted from the manual's CAD renders (filler turret, capper,
  sorter, rejection, conveyors, assemblies overview, filling head, etc.).
- Cited by page (no chapter map in this manual). Answers are grounded in the manual text.
