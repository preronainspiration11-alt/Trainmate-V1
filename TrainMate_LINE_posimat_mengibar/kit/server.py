"""
TrainMate backend — STAGE 2 (self-service uploads, multi-machine)
- Everything from before: login, English/Bengali/Hindi, clean SVG diagrams for Posimat, voice
- NEW: admins upload a PDF manual -> it OCRs in the background -> becomes a new machine section
- Uploaded machines + their figures live on a PERSISTENT DISK (DATA_DIR), so they survive redeploys

Env vars (Render):
  ANTHROPIC_API_KEY, SECRET_KEY, TRAINMATE_USERS="a:pw,b:pw"
  TRAINMATE_ADMINS="a"          (who may upload; default = first user)
  DATA_DIR=/var/data            (mount your Render persistent disk here)
  MODEL=claude-sonnet-5, COOKIE_SECURE=1
"""
import os, re, json, math, time, hmac, base64, hashlib, tempfile, threading, shutil
from pathlib import Path
from collections import Counter

from fastapi import FastAPI, UploadFile, File, Form, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import anthropic

import ingest

BASE = Path(__file__).parent
BUILTIN_DIR = BASE / "machines"                           # ships in the image (Posimat, Mengibar, ...)
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE / "var_data")))   # persistent disk
MACHINES_DIR = DATA_DIR / "machines"
UPLOADS_DIR = DATA_DIR / "uploads"
MACHINES_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

MODEL         = os.environ.get("MODEL", "claude-sonnet-5")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"
client = anthropic.Anthropic()

# ---------------- auth ----------------
SECRET = os.environ.get("SECRET_KEY") or base64.b64encode(os.urandom(24)).decode()
def _load_users():
    users = {}
    for pair in os.environ.get("TRAINMATE_USERS", "").split(","):
        pair = pair.strip()
        if ":" in pair:
            u, p = pair.split(":", 1); users[u.strip()] = p
    return users
USERS = _load_users()
_admins_env = [a.strip() for a in os.environ.get("TRAINMATE_ADMINS", "").split(",") if a.strip()]
ADMINS = set(_admins_env) if _admins_env else (set([next(iter(USERS))]) if USERS else set())

def make_token(user, days=7):
    exp = int(time.time()) + days * 86400
    payload = f"{user}|{exp}"
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()
def read_token(tok):
    try:
        raw = base64.urlsafe_b64decode(tok.encode()).decode()
        user, exp, sig = raw.split("|")
        good = hmac.new(SECRET.encode(), f"{user}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, sig): return None
        if int(exp) < time.time(): return None
        return user
    except Exception:
        return None
def current_user(request: Request):
    tok = request.cookies.get("trainmate_session")
    user = read_token(tok) if tok else None
    if not user: raise HTTPException(status_code=401, detail="login required")
    return user
def require_admin(user: str = Depends(current_user)):
    if user not in ADMINS: raise HTTPException(status_code=403, detail="admin only")
    return user

# ---------------- machine registry ----------------
def slugify(s):
    s = re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-')
    return s or ("m" + str(int(time.time())))

def _meta_fields(m):
    return {k: m.get(k) for k in ("id","name","model","brand","available","status","stage","progress","error","desc","builtin")}

def builtin_machines():
    out=[]
    if BUILTIN_DIR.exists():
        for d in sorted(BUILTIN_DIR.glob("*")):
            mf=d/"meta.json"
            if d.is_dir() and mf.exists():
                try:
                    m=json.loads(mf.read_text(encoding="utf-8")); m["builtin"]=True
                    m.setdefault("available",True); m.setdefault("status","ready")
                    out.append(m)
                except Exception: pass
    return out

# machines offered in the hub but not yet loaded (upload to enable)
PLACEHOLDERS = [
    {"id": "starpac", "name": "Starpac", "model": "", "available": False, "status": "empty",
     "desc": "Upload its manual to enable."},
    {"id": "superpack", "name": "Superpack", "model": "", "available": False, "status": "empty",
     "desc": "Upload its manual to enable."},
]

def _builtin_dir(mid):
    d = BUILTIN_DIR / mid
    return d if (d / "knowledge_base.json").exists() else None
def machine_dir(mid):
    return MACHINES_DIR / mid           # disk (uploaded) machines
def _data_dir(mid):
    return _builtin_dir(mid) or machine_dir(mid)

def read_meta(mid):
    bd = _builtin_dir(mid)
    if bd and (bd/"meta.json").exists():
        try:
            m=json.loads((bd/"meta.json").read_text(encoding="utf-8")); m["builtin"]=True; return m
        except Exception: return None
    f = machine_dir(mid) / "meta.json"
    if f.exists():
        try: return json.loads(f.read_text(encoding="utf-8"))
        except Exception: return None
    return None
def write_meta(mid, meta):
    machine_dir(mid).mkdir(parents=True, exist_ok=True)
    (machine_dir(mid) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
def update_meta(mid, patch):
    meta = read_meta(mid) or {"id": mid}
    meta.update(patch); write_meta(mid, meta); return meta

def list_machines():
    out=[]; seen=set()
    for m in builtin_machines():
        out.append(_meta_fields(m)); seen.add(m["id"])
    for d in sorted(MACHINES_DIR.glob("*")):
        if d.is_dir():
            m=read_meta(d.name)
            if m and m["id"] not in seen:
                out.append(_meta_fields(m)); seen.add(m["id"])
    for p in PLACEHOLDERS:
        if p["id"] not in seen: out.append(p)
    return out

# On startup, any machine stuck "processing" (killed by a restart) -> failed
for d in MACHINES_DIR.glob("*"):
    m = read_meta(d.name)
    if m and m.get("status") == "processing":
        update_meta(d.name, {"status": "failed", "available": False,
                             "error": "Processing was interrupted (server restart). Re-upload."})

# ---------------- load a machine's data (cached) ----------------
_cache = {}
def load_machine_data(mid):
    if mid in _cache: return _cache[mid]
    base = _data_dir(mid)
    kb = json.loads((base / "knowledge_base.json").read_text(encoding="utf-8"))
    figs = json.loads((base / "figures_manifest.json").read_text(encoding="utf-8"))["figures"]
    chunks = kb["chunks"]
    full = "\n\n".join(
        f"[chunk {c['chunk_id']} | " + (f"\u00a7{c['section']} {c.get('section_title','')} | " if c.get('section') else "")
        + f"p.{c['page']}]\n{c['text']}" for c in chunks)
    data = {"chunks": chunks, "figs": figs, "full": full,
            "fig_by_id": {f["figure_id"]: f for f in figs},
            "has_triggers": any("triggers" in f for f in figs)}
    _cache[mid] = data
    return data
def invalidate(mid): _cache.pop(mid, None)

# ---------------- prompt ----------------
def rules_for(name, model):
    return (
    f"You are TrainMate, a training assistant for the {name}" + (f" ({model})" if model else "") + " machine. "
    "Operators and technicians ask you questions instead of reading the manual.\n\n"
    "RULES\n"
    "- Answer ONLY from the manual excerpts provided (OCR text; interpret scan noise sensibly). If not covered, "
    "say so and point to the nearest page — never invent specs, codes, torque values or part numbers.\n"
    "- Be concise and practical. Lead with the direct answer, then detail. Use short bullets ('- ').\n"
    "- Cite pages as you go, e.g. (p.30). Use `code style` for exact button/screen names and codes.\n"
    "- SAFETY: for power, air, moving parts, guards or maintenance, put the key safety step FIRST, prefixed with "
    "\u26a0 (apply LOTO — lock out power and air).\n"
    "- DIAGRAMS: if a figure in AVAILABLE FIGURES clearly helps, add a line [FIGURE: id] using an id from that list. "
    "Only if it genuinely helps; most answers need none.\n"
    "- Only discuss this machine.")
def language_directive(language):
    lang = (language or "English").strip()
    if lang.lower() in ("english", "en", ""): return "Respond in clear English."
    return (f"Respond ENTIRELY in {lang}. Keep on-machine labels, alarm text, codes (e.g. T8, FC1), machine/format "
            f"names and page citations in original English, adding a short {lang} gloss in parentheses the first time. "
            "Keep the \u26a0 prefix on safety lines.")
def figure_menu(figs):
    if not figs: return "(none)"
    return "\n".join(f"- {f['figure_id']} : {f.get('caption','figure')} (p.{f['page']})" for f in figs)

def pick_figure_trigger(question, answer, figs):
    text = (question + " " + answer).lower()
    cites = set(re.findall(r"\u00a7\s*([0-9]+(?:\.[0-9]+)*)", answer))
    best, bs = None, 0
    for f in figs:
        if "triggers" not in f: continue
        s = sum(1 for kw in f["triggers"] if kw in text)
        sec = f.get("section", "")
        if sec and any(c == sec or c.startswith(sec + ".") or sec.startswith(c + ".") for c in cites): s += 3
        if s > bs: bs, best = s, f
    return best if best and bs >= 3 else None

# ---------------- API models ----------------
class AskReq(BaseModel):
    question: str
    history: list = []
    language: str = "English"
    machine: str = "posimat"
class LoginReq(BaseModel):
    username: str
    password: str

app = FastAPI(title="TrainMate")
if os.environ.get("APP_ORIGIN"):
    app.add_middleware(CORSMiddleware, allow_origins=os.environ["APP_ORIGIN"].split(","),
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def home(): return FileResponse(str(BASE / "client.html"))

@app.get("/me")
def me(request: Request):
    tok = request.cookies.get("trainmate_session")
    user = read_token(tok) if tok else None
    return {"user": user, "is_admin": (user in ADMINS) if user else False}

@app.post("/login")
def login(req: LoginReq, response: Response):
    expected = USERS.get(req.username)
    if not expected or not hmac.compare_digest(expected, req.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    response.set_cookie("trainmate_session", make_token(req.username), httponly=True,
                        secure=COOKIE_SECURE, samesite="lax", max_age=7*86400, path="/")
    return {"ok": True, "user": req.username, "is_admin": req.username in ADMINS}

@app.post("/logout")
def logout(response: Response):
    response.delete_cookie("trainmate_session", path="/"); return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "machines": len(list_machines()),
            "users_configured": len(USERS), "admins": sorted(ADMINS), "data_dir": str(DATA_DIR)}

@app.get("/machines")
def machines(user: str = Depends(current_user)):
    return {"machines": list_machines(), "is_admin": user in ADMINS}

# ---- serve a machine's figure image (svg for posimat, jpg for uploads) ----
@app.get("/fig/{mid}/{name}")
def fig(mid: str, name: str, user: str = Depends(current_user)):
    safe = Path(name).name
    d = _data_dir(mid) / "figures"
    path = d / safe
    if not path.exists(): raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(path))

@app.post("/ask")
def ask(req: AskReq, user: str = Depends(current_user)):
    mid = req.machine or "posimat"
    meta = read_meta(mid) or {}
    if not meta.get("available"):
        return {"answer": "That machine isn't ready yet — its manual is still being prepared.", "figures": []}
    try:
        data = load_machine_data(mid)
    except Exception as e:
        return {"answer": "", "figures": [], "error": f"Machine data not loaded: {e}"}
    name = meta.get("name", mid.title()); model = meta.get("model", "") or ""
    system = [
        {"type": "text", "text": rules_for(name, model) + "\n\nLANGUAGE:\n" + language_directive(req.language)
                                 + "\n\nAVAILABLE FIGURES:\n" + figure_menu(data["figs"])},
        {"type": "text", "text": "MANUAL EXCERPTS:\n" + data["full"], "cache_control": {"type": "ephemeral"}},
    ]
    messages = (req.history or []) + [{"role": "user", "content": req.question}]
    try:
        resp = client.messages.create(model=MODEL, max_tokens=2048, system=system, messages=messages)
    except Exception as e:
        return {"answer": "", "figures": [], "error": str(e)}
    text = "".join(b.text for b in resp.content if b.type == "text")

    figures = []
    def _pull(m):
        f = data["fig_by_id"].get(m.group(1).strip())
        if f and len(figures) < 2:
            figures.append({"figure_id": f["figure_id"], "caption": f.get("caption", ""),
                            "url": f"/fig/{mid}/{Path(f['image_key']).name}",
                            "section": f.get("section", f"p.{f['page']}")})
        return ""
    answer = re.sub(r"\[FIGURE:\s*([a-zA-Z0-9_]+)\s*\]", _pull, text)
    answer = re.sub(r"\[FIGURE:[^\]]*\]?", "", answer).strip()
    if not figures and data["has_triggers"]:
        f = pick_figure_trigger(req.question, answer, data["figs"])
        if f: figures.append({"figure_id": f["figure_id"], "caption": f.get("caption", ""),
                              "url": f"/fig/{mid}/{Path(f['image_key']).name}",
                              "section": f.get("section", "")})
    return {"answer": answer, "figures": figures}

# ---------------- admin: upload a manual ----------------
@app.post("/admin/upload")
async def upload(name: str = Form(...), model: str = Form(""), brand: str = Form(""),
                 file: UploadFile = File(...), user: str = Depends(require_admin)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    mid = slugify(brand or name)
    # avoid clobbering an existing machine id
    if (read_meta(mid) or {}).get("status") in ("processing", "ready"):
        mid = f"{mid}-{int(time.time())}"
    mdir = machine_dir(mid); (mdir / "figures").mkdir(parents=True, exist_ok=True)
    pdf_path = UPLOADS_DIR / f"{mid}.pdf"
    with open(pdf_path, "wb") as out:
        shutil.copyfileobj(file.file, out)
    write_meta(mid, {"id": mid, "name": name, "model": model, "brand": brand or name,
                     "available": False, "status": "processing", "stage": "queued", "progress": 0,
                     "uploaded_by": user, "created": int(time.time()),
                     "desc": f"{name}{(' ' + model) if model else ''} — uploaded manual."})
    def _run():
        def upd(patch):
            invalidate(mid); update_meta(mid, patch)
        ingest.ingest_pdf(str(pdf_path), str(mdir), upd)
        invalidate(mid)
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "id": mid, "status": "processing"}

@app.get("/admin/status/{mid}")
def status(mid: str, user: str = Depends(require_admin)):
    return read_meta(mid) or {"error": "unknown machine"}

@app.post("/admin/delete/{mid}")
def delete(mid: str, user: str = Depends(require_admin)):
    if _builtin_dir(mid): raise HTTPException(status_code=400, detail="cannot delete a built-in machine")
    invalidate(mid)
    shutil.rmtree(machine_dir(mid), ignore_errors=True)
    try: (UPLOADS_DIR / f"{mid}.pdf").unlink()
    except OSError: pass
    return {"ok": True}
