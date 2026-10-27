# TrainMate — Production Line (Posimat · Mengibar · Mespack)

Three machines, one hub. All answer from their manuals with clean, self-drawn
schematic diagrams (no scanned pages), in English / বাংলা / हिन्दी, with voice.

## Machines
- **Posimat MASTER-15** — bottle unscrambler / positioner (138 pages, 7 diagrams)
- **Mengibar MassFlow Filler-Capper** — filler + capper monoblock (63 pages, 6 diagrams)
- **Mespack** — horizontal pouch line + ATE-920 edge cap applicator + CIP (47 pages, 5 diagrams)

## What changed in this version
- **Added Mespack** (built from Installation + ATE-920 + CIP manuals, all under one machine).
- **Fixed the "diagram on every answer" glitch:** diagrams are now chosen deterministically
  and only appear when clearly relevant to the answer (same behaviour as Posimat). The model
  no longer inserts image markers.
- **Mengibar diagrams are now clean SVG schematics** (filler turret, capper, sorter, rejection,
  filling head, line overview) instead of scanned manual photos — matching Posimat/Mespack.

## Deploy (same repo + Render service)
1. Replace your repo's app folder with this `kit` (keep your Render env vars).
   - Replace `server.py`, `client.html`, and the whole `machines/` folder.
2. Commit & push → Render redeploys. Check `/health` → `machines` should be 3.
3. Sign in → **Machines** → Posimat, Mengibar and Mespack all show Available.

## Adding more machines
Send me the manual(s) and I'll ingest them the same curated way (clean diagrams, model/name,
a new `machines/<id>/` folder). The admin **Upload** button also still works for quick adds
(those use the manual's own figures and page citations).
