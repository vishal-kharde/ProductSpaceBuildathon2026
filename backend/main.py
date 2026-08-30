import logging
import hmac
import os
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from config import settings
from parser import extract_text
from models import ScenarioRequest, ScenarioResponse
from ai import analyze, scenario, red_team
from db import init_db, save_run, latest_run
from templates import TEMPLATES
from safety import sanitize_json
from auth import issue_token, require_auth

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log=logging.getLogger("bidlens.api")
app=FastAPI(title="BidLens Procurement Intelligence", version="1.5.0")
origins=[x.strip() for x in settings.cors_origins.split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

class LoginRequest(BaseModel):
    username: str
    password: str

@app.on_event("startup")
def startup():
    init_db()
    log.info("BidLens started; models=%s gemini_key_count=%s max_workers=%s", settings.model_priority(), len(settings.api_keys()), settings.ai_max_workers)

@app.get("/")
def root():
    return {
        "service": "BidLens",
        "status": "ok"
    }

@app.get("/api/health")
def health():
    return {"ok": True, "models": settings.model_priority(), "gemini_key_count": len(settings.api_keys()), "max_workers": settings.ai_max_workers}

@app.post("/api/auth/login")
def login(req: LoginRequest):
    valid_user = hmac.compare_digest(req.username, settings.bidlens_username)
    valid_pass = hmac.compare_digest(req.password, settings.bidlens_password)
    if not (valid_user and valid_pass):
        log.warning("Rejected login for username=%s", req.username[:32])
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"ok": True, "token": issue_token(req.username), "expires_in": 12*60*60}

@app.get("/api/latest")
def latest(_: str = Depends(require_auth)):
    payload=latest_run() or {"result":None}
    return JSONResponse(content=sanitize_json(payload))

@app.get("/api/templates")
def templates(_: str = Depends(require_auth)):
    return {"templates":TEMPLATES}

DATA_DIR = Path(__file__).resolve().parent / "data"

@app.get("/api/resources")
def resources(_: str = Depends(require_auth)):
    items = []
    if DATA_DIR.exists():
        for folder in sorted(p for p in DATA_DIR.iterdir() if p.is_dir() and not p.name.startswith("__")):
            files = []
            for p in sorted(folder.iterdir()):
                if p.is_file() and p.name.lower() not in {"requirements.txt", "readme.txt"}:
                    files.append({"name": p.name, "size": p.stat().st_size, "download": f"/api/resources/{folder.name}/{p.name}"})
            if files:
                readme = folder / "README.txt"
                description = readme.read_text(errors="ignore").strip() if readme.exists() else "Sample vendor proposals for testing."
                items.append({"id": folder.name, "name": folder.name.replace("_", " ").title(), "description": description, "files": files})
    return {"resources": items, "download_all": "/api/resources/download-all"}

@app.get("/api/resources/download-all")
def download_all_resources(_: str = Depends(require_auth)):
    bio = BytesIO()
    with ZipFile(bio, "w", ZIP_DEFLATED) as zf:
        for p in DATA_DIR.rglob("*"):
            if p.is_file() and p.name.lower() not in {"requirements.txt", "readme.txt"} and not p.name.startswith("."):
                zf.write(p, p.relative_to(DATA_DIR))
    bio.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(bio, media_type="application/zip", headers={"Content-Disposition":"attachment; filename=bidlens-sample-proposals.zip"})

@app.get("/api/resources/{folder}/{filename}")
def download_resource(folder: str, filename: str, _: str = Depends(require_auth)):
    if any(x in folder for x in ("..", "/", "\\")) or any(x in filename for x in ("..", "/", "\\")):
        raise HTTPException(400, "Invalid resource path")
    if filename.lower() in {"requirements.txt", "readme.txt"}:
        raise HTTPException(404, "Resource not available")
    path = DATA_DIR / folder / filename
    if not path.is_file() or not path.resolve().is_relative_to(DATA_DIR.resolve()):
        raise HTTPException(404, "Sample file not found")
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=path.name)

@app.post("/api/analyze")
async def analyze_proposals(requirements: str | None = Form(None), files: list[UploadFile] = File(...), _: str = Depends(require_auth)):
    requirements_text = (requirements or "").strip()
    if not requirements_text:
        raise HTTPException(400, "Requirements are required. Choose a procurement template or enter your custom requirements.")
    proposals=[]
    ignored_requirements_files=[]
    for f in files:
        try:
            data=await f.read()
            if not data: raise ValueError("empty file")
            filename=(f.filename or "").strip()
            basename=filename.lower().rsplit("/",1)[-1].rsplit("\\",1)[-1]
            if basename in {"requirements.txt","requirements.md","rfp.txt","rfp.md","requirements.csv"}:
                ignored_requirements_files.append(filename)
                log.info("Ignoring requirements-like upload=%s; buyer ask comes from prompt/template", filename)
                continue
            text=extract_text(filename or "proposal.txt",data)
            if not text.strip(): raise ValueError("no extractable text")
            log.info("Loaded proposal file=%s bytes=%s chars=%s",filename,len(data),len(text))
            proposals.append((filename or "Vendor",text))
        except Exception as e:
            log.exception("Input file read failed file=%s",f.filename)
            raise HTTPException(400,f"Could not read {f.filename}: {e}")
    if len(proposals)<2:
        raise HTTPException(400,"Upload at least two vendor proposals. requirements.txt is not required.")
    try:
        result=analyze(requirements_text,proposals)
        safe=sanitize_json(result.model_dump())
        run_id=save_run(requirements_text,safe)
        return JSONResponse(content={"run_id":run_id,"result":safe,"ignored_requirements_files":ignored_requirements_files})
    except Exception as e:
        log.exception("Analysis failed requirements_chars=%s proposal_count=%s", len(requirements_text), len(proposals))
        detail = str(e).strip() or "Unknown analysis error"
        raise HTTPException(500, f"Analysis failed: {detail[:500]}")

@app.post("/api/red-team")
async def red_team_analysis(payload: dict, _: str = Depends(require_auth)):
    result_data=payload.get("result") if isinstance(payload,dict) else None
    if not isinstance(result_data,dict):
        raise HTTPException(400,"A completed analysis result is required.")
    try:
        return JSONResponse(content=sanitize_json(red_team(result_data).model_dump()))
    except Exception as e:
        log.exception("Red-team analysis failed")
        raise HTTPException(500, f"Red-team analysis failed: {str(e)[:300]}")

@app.post("/api/reset")
def reset_db(_: str = Depends(require_auth)):
    from db import reset_db as _reset
    _reset(); return {"ok":True,"message":"BidLens analysis history cleared."}

@app.post("/api/scenario",response_model=ScenarioResponse)
def run_scenario(req:ScenarioRequest, _: str = Depends(require_auth)):
    latest=latest_run()
    if not latest or not latest.get("result"): raise HTTPException(404,"Run an analysis first")
    try:
        return scenario(__import__('types').SimpleNamespace(**latest["result"]),req.volume_units_per_year,req.horizon_years,req.renewal_increase_override)
    except Exception as e:
        log.exception("Scenario failed")
        raise HTTPException(500,f"Scenario failed: {e}")
