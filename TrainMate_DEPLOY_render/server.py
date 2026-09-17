"""
TrainMate backend — DEPLOY build (Render-ready)
- Serves the web app AND the API from one URL
- Per-user login (cookie session); protects /ask and /transcribe
- Answers in English / Bengali / Hindi, clean SVG diagrams, optional voice

Required environment variables on the host:
    ANTHROPIC_API_KEY   your Anthropic key (secret)
    SECRET_KEY          any long random string (signs login cookies)
    TRAINMATE_USERS     "alice:pass1,bob:pass2,carol:pass3"   (your people)
Optional:
    MODEL               default "claude-sonnet-5"
    COOKIE_SECURE       "1" on real HTTPS host (default), "0" for local http testing
    APP_ORIGIN          extra allowed origin(s) if you host the page elsewhere
"""
import os, re, json, math, time, hmac, base64, hashlib, tempfile
from pathlib import Path
from collections import Counter

from fastapi import FastAPI, UploadFile, File, Form, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import anthropic

BASE = Path(__file__).parent
DATA = BASE / "data"
KB   = json.loads((DATA / "knowledge_base.json").read_text(encoding="utf-8"))
FIGS = json.loads((DATA / "figures_manifest.json").read_text(encoding="utf-8"))["figures"]
CHUNKS = KB["chunks"]

MODEL         = os.environ.get("MODEL", "claude-sonnet-5")
USE_RETRIEVAL = os.environ.get("USE_RETRIEVAL", "0") == "1"
TOP_K         = int(os.environ.get("TOP_K", "8"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"

# Machines shown in the hub. Only "posimat" has data loaded today; others are placeholders
# until Stage 2 (self-service upload). Add entries here as you ingest more manuals.
MACHINES = [
    {"id": "posimat",   "name": "Posimat",   "model": "MASTER-15", "available": True,
     "desc": "Bottle unscrambler / positioner — full manual loaded."},
    {"id": "mespack",   "name": "Mespack",   "model": "",          "available": False,
     "desc": "Filling / packaging machine — knowledge base being prepared."},
    {"id": "superpack", "name": "Superpack", "model": "",          "available": False,
     "desc": "Knowledge base being prepared."},
    {"id": "starpac",   "name": "Starpac",   "model": "",          "available": False,
     "desc": "Knowledge base being prepared."},
    {"id": "mengibar",  "name": "Mengibar",  "model": "",          "available": False,
     "desc": "Knowledge base being prepared."},
]
AVAILABLE = {m["id"] for m in MACHINES if m["available"]}

client = anthropic.Anthropic()

# ------------------------------------------------------------------ auth
SECRET = os.environ.get("SECRET_KEY") or base64.b64encode(os.urandom(24)).decode()
if not os.environ.get("SECRET_KEY"):
    print("WARNING: SECRET_KEY not set — using a random one (logins reset on restart).")

def _load_users():
    raw = os.environ.get("TRAINMATE_USERS", "")
    users = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            u, p = pair.split(":", 1); users[u.strip()] = p
    return users
USERS = _load_users()

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
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return user

# ------------------------------------------------------------------ retriever + prompt
def _tok(s): return re.findall(r"[a-z0-9]{2,}", s.lower())
_df = Counter()
for c in CHUNKS:
    for w in set(_tok(c["text"])): _df[w] += 1
_N = len(CHUNKS); _idf = {w: math.log(1 + _N/(1+df)) for w, df in _df.items()}
def _vec(t):
    tf = Counter(t); v = {w:(1+math.log(n))*_idf[w] for w,n in tf.items() if w in _idf}
    nrm = math.sqrt(sum(x*x for x in v.values())) or 1.0
    return {w:x/nrm for w,x in v.items()}
_cv = [(_vec(_tok(c["text"])), c) for c in CHUNKS]
def retrieve(q, k=TOP_K):
    qv=_vec(_tok(q)); sc=[]
    for cv,c in _cv:
        s=sum(qv[w]*cv.get(w,0.0) for w in qv)
        if s>0: sc.append((s,c))
    sc.sort(key=lambda x:-x[0]); return [c for _,c in sc[:k]]
def chunk_block(c): return f"[chunk {c['chunk_id']} | \u00a7{c['section']} {c['section_title']} | p.{c['page']}]\n{c['text']}"
FULL_MANUAL = "\n\n".join(chunk_block(c) for c in CHUNKS)

RULES = (
"You are TrainMate, a training assistant for the POSIMAT MASTER-15 bottle unscrambler "
"(document OC12737.3.01). Operators and technicians ask questions instead of reading the manual.\n\n"
"RULES\n"
"- Answer ONLY from the manual excerpts provided. If not covered, say so and point to the nearest section — "
"never invent specs, alarm codes, torque values or part numbers.\n"
"- Be concise and practical. Lead with the direct answer, then detail. Use short bullets ('- ').\n"
"- Cite the manual, e.g. (\u00a73.1.1, p.30). Use `code style` for button/screen names, timers (T8), alarm text.\n"
"- SAFETY: for power, air, moving parts, guards or maintenance, put the key safety step FIRST, prefixed with "
"\u26a0 (apply LOTO — lock out power and air).\n"
"- Do NOT write figure tags; the app adds the right diagram automatically.\n"
"- Only discuss this machine."
)
def language_directive(language):
    lang=(language or "English").strip()
    if lang.lower() in ("english","en",""): return "Respond in clear English."
    return (f"Respond ENTIRELY in {lang}. Keep machine labels (Start, Stop, Reverse, Reset, AUTO/MAN, "
            "DOORS BLOCKAGE, INFORMATION, ALARMS), alarm text, timer codes (T8), photocell codes (FC1, FC10), "
            f"machine/format names and all \u00a7/page citations in original English, adding a short {lang} gloss "
            "in parentheses the first time. Keep the \u26a0 prefix on safety lines. Numbers/units stay as-is.")

def pick_figure(question, answer):
    text=(question+" "+answer).lower()
    cites=set(re.findall(r"\u00a7\s*([0-9]+(?:\.[0-9]+)*)", answer))
    best,bs=None,0
    for f in FIGS:
        s=sum(1 for kw in f["triggers"] if kw in text); sec=f["section"]
        if any(c==sec or c.startswith(sec+".") or sec.startswith(c+".") for c in cites): s+=3
        if s>bs: bs,best=s,f
    if best and bs>=3:
        return {"figure_id":best["figure_id"],"caption":best["caption"],
                "url":"/figures/"+Path(best["image_key"]).name,"section":best["section"]}
    return None

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
app.mount("/figures", StaticFiles(directory=str(DATA / "figures")), name="figures")

# ---- pages & auth endpoints ----
@app.get("/")
def home():
    return FileResponse(str(BASE / "client.html"))

@app.get("/me")
def me(request: Request):
    tok = request.cookies.get("trainmate_session")
    user = read_token(tok) if tok else None
    return {"user": user}

@app.post("/login")
def login(req: LoginReq, response: Response):
    expected = USERS.get(req.username)
    if not expected or not hmac.compare_digest(expected, req.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = make_token(req.username)
    response.set_cookie("trainmate_session", token, httponly=True, secure=COOKIE_SECURE,
                        samesite="lax", max_age=7*86400, path="/")
    return {"ok": True, "user": req.username}

@app.post("/logout")
def logout(response: Response):
    response.delete_cookie("trainmate_session", path="/")
    return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "chunks": len(CHUNKS), "figures": len(FIGS),
            "figure_type": "svg", "users_configured": len(USERS), "transcription": _asr_status()}

@app.get("/machines")
def machines(user: str = Depends(current_user)):
    return {"machines": MACHINES}

# ---- protected API ----
@app.post("/ask")
def ask(req: AskReq, user: str = Depends(current_user)):
    if req.machine and req.machine not in AVAILABLE:
        return {"answer": "That machine isn't loaded yet — its manual is being prepared. "
                          "Posimat MASTER-15 is available now.", "figures": []}
    context = "\n\n".join(chunk_block(c) for c in retrieve(req.question)) if USE_RETRIEVAL else FULL_MANUAL
    system = [
        {"type":"text","text":RULES+"\n\nLANGUAGE:\n"+language_directive(req.language)},
        {"type":"text","text":"MANUAL EXCERPTS:\n"+context,"cache_control":{"type":"ephemeral"}},
    ]
    messages = (req.history or []) + [{"role":"user","content":req.question}]
    try:
        resp = client.messages.create(model=MODEL, max_tokens=2048, system=system, messages=messages)
    except Exception as e:
        return {"answer":"", "figures":[], "error":str(e)}
    answer = "".join(b.text for b in resp.content if b.type=="text")
    answer = re.sub(r"\[FIGURE:[^\]]*\]?", "", answer).strip()
    fig = pick_figure(req.question, answer)
    return {"answer": answer, "figures": ([fig] if fig else [])}

# ---- voice (optional) ----
_WHISPER=None
def _asr_status():
    try:
        import faster_whisper  # noqa
        return "local-whisper"
    except Exception:
        return "openai-whisper" if os.environ.get("OPENAI_API_KEY") else "not-configured"
def _lang_code(language): return {"bengali":"bn","hindi":"hi","english":"en"}.get((language or "").strip().lower())

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form(""), user: str = Depends(current_user)):
    audio = await file.read(); code=_lang_code(language)
    try:
        import faster_whisper
        global _WHISPER
        if _WHISPER is None:
            _WHISPER = faster_whisper.WhisperModel(os.environ.get("WHISPER_MODEL","small"), device="cpu", compute_type="int8")
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp: tmp.write(audio); path=tmp.name
        segs,_=_WHISPER.transcribe(path, language=code)
        txt="".join(s.text for s in segs).strip()
        try: os.remove(path)
        except OSError: pass
        return {"text":txt}
    except ImportError: pass
    except Exception as e: return {"text":"", "error":f"Local transcription failed: {e}"}
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            with tempfile.NamedTemporaryFile(suffix="_"+(file.filename or "audio.webm"), delete=False) as tmp: tmp.write(audio); path=tmp.name
            with open(path,"rb") as fh:
                kw={"model":"whisper-1","file":fh}
                if code: kw["language"]=code
                r=OpenAI().audio.transcriptions.create(**kw)
            try: os.remove(path)
            except OSError: pass
            return {"text":(r.text or "").strip()}
        except Exception as e: return {"text":"", "error":f"OpenAI transcription failed: {e}"}
    return {"text":"", "error":"Transcription engine not installed. Use the mic button."}
